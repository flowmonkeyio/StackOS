"""Daemon-owned dispatcher for canonical durable ActionCall jobs.

The repository persists the job, item, correlation, and admission lease before
this module invokes a connector.  The dispatcher intentionally has no
provider-specific retry or delivery policy: adapters return typed failure
metadata and each manifest supplies its minimum pacing through the sealed job.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session

from stackos.actions.connectors import ActionConnectorError, ActionConnectorRegistry
from stackos.actions.repository import ActionRepository
from stackos.actions.repository.durable_artifacts import validate_durable_action_artifact_pins
from stackos.artifacts import redact_secret_text
from stackos.db.models import ActionCall
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.repositories.secrets import PayloadSecretRepository
from stackos.secret_refs import materialize_secret_refs, redact_secret_values


class DurableActionDispatcher:
    """Dispatch due generic durable action items with one session per transition.

    ``start`` and ``stop`` are lifecycle hooks for the daemon owner.  Tests and
    host lifecycle code may call ``run_once`` directly without starting a task.
    Every connector invocation is bounded to one persisted item lease; a
    restart reconciles an old lease to unknown before this dispatcher resumes
    pending or scheduled work.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        connectors: ActionConnectorRegistry,
        *,
        asset_dir: Path | None = None,
        poll_interval_seconds: float = 0.01,
        lease_seconds: int = 90,
        job_limit: int = 20,
        max_inflight: int = 64,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if job_limit < 1:
            raise ValueError("job_limit must be positive")
        if max_inflight < 1:
            raise ValueError("max_inflight must be positive")
        self._session_factory = session_factory
        self._connectors = connectors
        self._asset_dir = asset_dir
        self._poll_interval_seconds = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._job_limit = job_limit
        self._max_inflight = max_inflight
        self._stopped = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._inflight: set[asyncio.Task[None]] = set()
        self._had_due_jobs = False

    async def start(self) -> None:
        """Reconcile restart ambiguity, then begin periodic due-item claims."""

        if self._task is not None and not self._task.done():
            return
        self._stopped.clear()
        with self._session_factory() as session:
            self._repo(session).reconcile_durable_action_jobs()
        self._task = asyncio.create_task(self._run(), name="stackos-durable-action-dispatch")

    async def stop(self) -> None:
        """Stop future claims; an interrupted provider effect is held unknown."""

        self._stopped.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._cancel_inflight()

    async def _cancel_inflight(self) -> None:
        tasks = tuple(self._inflight)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            # Cancellation before a newly-created task first runs bypasses its
            # item guard. The durable lease still needs an explicit unknown
            # transition before the daemon reports itself stopped.
            with self._session_factory() as session:
                self._repo(session).reconcile_durable_action_jobs()

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.run_once(wait_for_receipts=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failure outside an item transition can strand a persisted
                # lease. Recover it to unknown before the next scan; never
                # require a daemon restart before the operator can inspect it.
                await self._cancel_inflight()
                with suppress(Exception), self._session_factory() as session:
                    self._repo(session).reconcile_durable_action_jobs()
            try:
                interval = self._poll_interval_seconds if self._had_due_jobs else 0.5
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
            except TimeoutError:
                continue

    async def run_once(self, *, now: datetime | None = None, wait_for_receipts: bool = True) -> int:
        """Attempt at most one currently due item from each candidate job.

        The return value counts provider-attempt leases claimed, including a
        typed provider deferral or an unknown hold.  It does not count jobs that
        are not yet eligible under shared Account/destination admission.
        """

        scan_now = _as_naive_utc(now)
        with self._session_factory() as session:
            repo = ActionRepository(session, connectors=self._connectors, asset_dir=self._asset_dir)
            repo.expire_durable_action_jobs(now=scan_now)
            jobs = repo.list_dispatchable_durable_action_jobs(now=scan_now, limit=self._job_limit)
        self._had_due_jobs = bool(jobs)
        attempted = 0
        for job in jobs:
            if len(self._inflight) >= self._max_inflight:
                break
            claim_now = _transition_now(now)
            item = self._claim_one(
                job_id=job.id,
                project_id=job.project_id,
                now=claim_now,
                pacing=job.pacing_json,
            )
            if item is None:
                continue
            attempted += 1
            task = asyncio.create_task(
                self._dispatch_with_guard(
                    project_id=job.project_id,
                    job_id=job.id,
                    action_call_id=job.action_call_id,
                    action_ref=job.action_ref,
                    credential_ref=job.credential_ref,
                    item=item,
                    now=now,
                ),
                name=f"stackos-durable-item-{item.id}",
            )
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)
        if wait_for_receipts and self._inflight:
            await asyncio.gather(*tuple(self._inflight))
        return attempted

    async def _dispatch_with_guard(self, **kwargs: Any) -> None:
        try:
            await self._dispatch_item(**kwargs)
        except asyncio.CancelledError:
            self._hold_unknown(
                project_id=kwargs["project_id"],
                item_id=kwargs["item"].id,
                attempt_ref=_required_attempt_ref(kwargs["item"].attempt_ref),
                reason="dispatcher-stopped-during-provider-effect",
                now=_transition_now(kwargs["now"]),
            )
            raise
        except Exception as exc:
            self._hold_unknown(
                project_id=kwargs["project_id"],
                item_id=kwargs["item"].id,
                attempt_ref=_required_attempt_ref(kwargs["item"].attempt_ref),
                reason=_safe_error(str(exc), ()),
                now=_transition_now(kwargs["now"]),
            )

    def _claim_one(
        self,
        *,
        project_id: int,
        job_id: int,
        now: datetime | None,
        pacing: dict[str, float],
    ) -> Any | None:
        account_interval = float(pacing.get("account_interval_seconds", 1.0))
        destination_interval = float(pacing.get("destination_interval_seconds", 1.0))
        with self._session_factory() as session:
            items = self._repo(session).claim_durable_action_items(
                project_id=project_id,
                job_id=job_id,
                now=now,
                limit=1,
                lease_seconds=self._lease_seconds,
                account_interval_seconds=account_interval,
                destination_interval_seconds=destination_interval,
                overlap_account_leases=True,
            )
        return items[0] if items else None

    async def _dispatch_item(
        self,
        *,
        project_id: int,
        job_id: int,
        action_call_id: int,
        action_ref: str,
        credential_ref: str,
        item: Any,
        now: datetime | None,
    ) -> None:
        attempt_ref = _required_attempt_ref(item.attempt_ref)
        entered_connector = False
        sensitive_values: tuple[str, ...] = ()
        try:
            with self._session_factory() as session:
                repo = self._repo(session)
                validate_durable_action_artifact_pins(
                    session,
                    project_id=project_id,
                    job_id=job_id,
                    asset_dir=self._asset_dir,
                )
                call = session.get(ActionCall, action_call_id)
                if call is None or call.project_id != project_id:
                    raise NotFoundError(
                        f"action call {action_call_id} not found for project {project_id}"
                    )
                manifest, _provider_config = repo._manifest_with_provider_config(
                    action_ref=action_ref,
                    plugin_slug=None,
                    action_key=None,
                    project_id=project_id,
                    credential_ref=credential_ref,
                )
                credential = await repo._resolve_credential(
                    project_id=project_id,
                    manifest=manifest,
                    credential_ref=credential_ref,
                )
                materialized_input, sensitive_values = materialize_secret_refs(
                    item.input_json,
                    resolve=lambda secret_ref: PayloadSecretRepository(session).resolve(
                        project_id=project_id,
                        secret_ref=secret_ref,
                    ),
                )

                def progress_callback(progress_json: dict[str, Any]) -> None:
                    self._record_progress(
                        project_id=project_id,
                        item_id=item.id,
                        attempt_ref=attempt_ref,
                        progress_json=redact_secret_values(progress_json, sensitive_values),
                    )

                connector = self._connectors.get(manifest.connector_key or "")
                request = repo._connector_request(
                    project_id=project_id,
                    manifest=manifest,
                    input_json=materialized_input,
                    provider_context_json=dict(call.provider_context_json or {}),
                    credential=credential,
                    dry_run=False,
                    idempotency_key=attempt_ref,
                    progress_callback=progress_callback,
                    action_call_id=action_call_id,
                    attempt_ref=attempt_ref,
                    correlation_ref=item.correlation_ref,
                    delivery_item_id=item.id,
                )
            entered_connector = True
            result = await connector.execute(request)
            self._complete(
                project_id=project_id,
                item_id=item.id,
                attempt_ref=attempt_ref,
                result_json=redact_secret_values(result.output_json, sensitive_values),
                now=_transition_now(now),
            )
        except ActionConnectorError as exc:
            output = redact_secret_values(exc.output_json, sensitive_values)
            safe_error = _safe_error(exc.detail, sensitive_values)
            if _outcome_unknown(output):
                self._hold_unknown(
                    project_id=project_id,
                    item_id=item.id,
                    attempt_ref=attempt_ref,
                    reason=safe_error,
                    result_json=output,
                    now=_transition_now(now),
                )
                return
            retry_after = _retry_after_seconds(output)
            transition_now = _transition_now(now)
            retry_scope = "destination" if output.get("retry_scope") == "destination" else "account"
            if retry_after is not None and _proven_no_effect(output):
                self._defer(
                    project_id=project_id,
                    item_id=item.id,
                    attempt_ref=attempt_ref,
                    retry_at=transition_now + timedelta(seconds=retry_after),
                    reason=safe_error,
                    flood_wait_seconds=retry_after,
                    result_json=output,
                    retry_scope=retry_scope,
                    now=transition_now,
                )
                return
            self._fail(
                project_id=project_id,
                item_id=item.id,
                attempt_ref=attempt_ref,
                error=safe_error,
                result_json=output,
                pause_remaining_reason=(
                    "provider_account_restricted" if _account_restricted(output) else None
                ),
                cooldown_until=(
                    transition_now + timedelta(seconds=retry_after)
                    if retry_after is not None
                    else None
                ),
                cooldown_scope=retry_scope,
                now=transition_now,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if entered_connector:
                self._hold_unknown(
                    project_id=project_id,
                    item_id=item.id,
                    attempt_ref=attempt_ref,
                    reason=_safe_error(str(exc), sensitive_values),
                    now=_transition_now(now),
                )
                return
            self._fail(
                project_id=project_id,
                item_id=item.id,
                attempt_ref=attempt_ref,
                error=_safe_error(str(exc), sensitive_values),
                result_json={"provider_executed": False, "retry_safe": True},
                now=_transition_now(now),
            )

    def _record_progress(
        self,
        *,
        project_id: int,
        item_id: int,
        attempt_ref: str,
        progress_json: dict[str, Any],
    ) -> None:
        with self._session_factory() as session:
            self._repo(session).record_durable_action_item_progress(
                project_id=project_id,
                item_id=item_id,
                attempt_ref=attempt_ref,
                progress_json=progress_json,
            )

    def _complete(self, **kwargs: Any) -> None:
        with self._session_factory() as session:
            self._repo(session).complete_durable_action_item(**kwargs)

    def _defer(self, **kwargs: Any) -> None:
        with self._session_factory() as session:
            self._repo(session).defer_durable_action_item(**kwargs)

    def _fail(self, **kwargs: Any) -> None:
        with self._session_factory() as session:
            self._repo(session).fail_durable_action_item(**kwargs)

    def _hold_unknown(self, **kwargs: Any) -> None:
        with self._session_factory() as session:
            repo = self._repo(session)
            try:
                repo.hold_durable_action_item_unknown(**kwargs)
            except (ConflictError, NotFoundError):
                # A concurrent explicit operator transition already produced
                # the authoritative receipt; never overwrite it.
                return

    def _repo(self, session: Session) -> ActionRepository:
        return ActionRepository(session, connectors=self._connectors, asset_dir=self._asset_dir)


def _required_attempt_ref(value: str | None) -> str:
    if not value:
        raise RuntimeError("durable item claim did not return its persisted attempt_ref")
    return value


def _retry_after_seconds(output_json: dict[str, Any]) -> int | None:
    value = output_json.get("retry_after_seconds")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return max(1, int(value))
    return None


def _outcome_unknown(output_json: dict[str, Any]) -> bool:
    return output_json.get("outcome_unknown") is True


def _proven_no_effect(output_json: dict[str, Any]) -> bool:
    return output_json.get("provider_executed") is False and output_json.get("retry_safe") is True


def _account_restricted(output_json: dict[str, Any]) -> bool:
    return output_json.get("account_restricted") is True


def _safe_error(value: str, sensitive_values: tuple[str, ...]) -> str:
    return redact_secret_text(redact_secret_values(value, sensitive_values))


def _as_naive_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(tzinfo=None)
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _transition_now(value: datetime | None) -> datetime:
    """Use the injected test clock, otherwise capture each real transition."""

    return _as_naive_utc(value)


__all__ = ["DurableActionDispatcher"]
