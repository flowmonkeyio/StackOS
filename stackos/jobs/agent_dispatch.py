"""Opt-in consumer of an explicitly admitted, finite set of canonical tickets.

This lifecycle is deliberately not registered in the installed daemon. The host
must supply the existing run token, scoped grants, reviewed profile and scope.
It never creates authority, retries an uncertain launch, accepts work, or frees
an occupied worker slot. Tracker/Run/Action records remain the only durable state.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from stackos.config import Settings
from stackos.db.models import (
    ActionCall,
    Run,
    RunKind,
    RunPlan,
    RunPlanStep,
    TrackerTicket,
    WorkItemOwnership,
)
from stackos.operations.dispatcher import OperationDispatcher
from stackos.operations.registry import build_operation_registry
from stackos.repositories.base import ConflictError
from stackos.repositories.runs import RunRepository
from stackos.repositories.tracker import TrackerRepository

ACTION = "engineering.codex.recorded-turn"
RECEIPT = "agent_dispatch"


@dataclass(frozen=True)
class AdmittedTicket:
    key: str
    scope_digest: str
    prompt: str


@dataclass(frozen=True)
class AgentDispatchScope:
    project_id: int
    run_plan_id: int
    step_id: str
    controller_run_id: int
    run_token: str = field(repr=False)
    source_revision: str
    tickets: tuple[AdmittedTicket, ...]
    operator_ref: str
    capacity: int = 1
    interval_seconds: float = 1
    stall_seconds: float = 90

    def __post_init__(self) -> None:
        if (
            not self.tickets
            or len(self.tickets) > 50
            or len({ticket.key for ticket in self.tickets}) != len(self.tickets)
            or not 1 <= self.capacity <= len(self.tickets)
            or self.interval_seconds <= 0
            or self.stall_seconds <= 0
            or not self.run_token
            or not self.operator_ref
        ):
            raise ValueError("explicit finite scope, capacity, authority and deadlines required")


class AgentDispatchLoop:
    """Run once as a background lifecycle; never needs a parent polling prompt.

    The configured ticket list is an allowlist, not a readiness queue. Each pass
    re-reads tracker readiness and existing owners. Capacity includes every owner
    in this scope, including held/completed-with-unknown-effects attempts. This
    finite pilot has zero automatic retries and no automatic failure closure.
    """

    def __init__(
        self,
        *,
        engine: Engine,
        settings: Settings,
        services: dict[str, Any],
        scope: AgentDispatchScope,
    ) -> None:
        self.engine, self.settings, self.services, self.scope = engine, settings, services, scope
        self._dispatcher = OperationDispatcher(build_operation_registry())
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._launches: dict[str, asyncio.Task[None]] = {}
        self.last_error: str | None = None

    def start(self) -> asyncio.Task[None]:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="stackos-admitted-agent-dispatch")
        return self._task

    async def stop(self) -> None:
        """Stop selection/requesters. Original background command custody survives."""
        self._stop.set()
        if self._task is not None:
            await self._task
        # These are requesters, not owned command tasks. Settle them before host
        # custody shutdown so none can enqueue new work after a clean shutdown.
        for task in self._launches.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._launches.values(), return_exceptions=True)

    async def _call(self, name: str, **arguments: Any) -> dict[str, Any]:
        arguments.setdefault("project_id", self.scope.project_id)
        arguments.update(run_token=self.scope.run_token, response_mode="raw")
        with Session(self.engine) as session:
            session.info["operation_services"] = self.services
            result = await self._dispatcher.dispatch(
                name, arguments, session=session, surface="mcp", settings=self.settings
            )
        return result.payload.get("data", result.payload)

    def _brief(self, key: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            return (
                TrackerRepository(session)
                .brief(project_id=self.scope.project_id, ticket_key=key)
                .model_dump(mode="json")
            )

    def _owner(self, item: str) -> WorkItemOwnership | None:
        with Session(self.engine) as session:
            return session.exec(
                select(WorkItemOwnership).where(
                    WorkItemOwnership.project_id == self.scope.project_id,
                    WorkItemOwnership.canonical_item_id == item,
                )
            ).one_or_none()

    async def _receipt(
        self,
        key: str,
        *,
        ticket_status: str | None = None,
        ticket_blocker_reason: str | None = None,
        **changes: Any,
    ) -> None:
        ticket = self._brief(key)["ticket"]
        context = dict(ticket.get("context_json") or {})
        context[RECEIPT] = {**context.get(RECEIPT, {}), **changes}
        patch: dict[str, Any] = {"context_json": context}
        if ticket_status is not None:
            patch["status"] = ticket_status
        if ticket_blocker_reason is not None:
            patch["blocker_reason"] = ticket_blocker_reason
        await self._call("tracker.updateTicket", ticket_key=key, patch_json=patch)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as exc:
                # Authority loss is not a reason to fall back to unscoped writes.
                # Keep the host task inspectable and stop, with custody unchanged.
                self.last_error = type(exc).__name__
                return
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self.scope.interval_seconds)

    async def _tick(self) -> None:
        with Session(self.engine) as session:
            readiness = (
                TrackerRepository(session)
                .next(
                    project_id=self.scope.project_id,
                    limit=50,
                    include_blocked=True,
                    ticket_keys=frozenset(ticket.key for ticket in self.scope.tickets),
                )
                .model_dump(mode="json")
            )
        ready = {ticket["key"] for ticket in readiness["tickets"]}
        occupied = 0
        pending: list[AdmittedTicket] = []
        reasons: dict[str, str] = {}
        for admitted in self.scope.tickets:
            brief = self._brief(admitted.key)
            ticket = brief["ticket"]
            item = f"ticket:{ticket['id']}"
            owner = self._owner(item)
            receipt = (ticket.get("context_json") or {}).get(RECEIPT, {})
            if owner is None and receipt.get("state") == "admission_rejected":
                reasons[admitted.key] = (
                    "admission rejected before acquisition; operator review required"
                )
            elif owner is not None or receipt:
                occupied += 1  # Unknown, interrupted and completed are never free capacity.
                if owner is not None and owner.owner_ref == (
                    f"dispatch:{self.scope.controller_run_id}:{item}"
                ):
                    await self._observe(admitted, item, receipt, owner)
                reasons[admitted.key] = "existing attempt retains capacity"
            elif admitted.key not in ready:
                reasons[admitted.key] = "tracker dependency, approval or terminal state"
            else:
                pending.append(admitted)
        for admitted in pending:
            if occupied >= self.scope.capacity:
                reasons[admitted.key] = "admitted capacity occupied; no slot reuse"
                continue
            # Claim synchronously before considering another ticket. Fresh owner CAS
            # and action admission, not this readiness hint, decide whether work starts.
            occupied += int(await self._launch(admitted))
            reasons[admitted.key] = "launch intent persisted; admission pending"
        with Session(self.engine) as session:
            plan = session.get(RunPlan, self.scope.run_plan_id)
            metadata = dict(plan.metadata_json or {}) if plan is not None else {}
        snapshot = {"reasons": reasons, "occupied": occupied, "capacity": self.scope.capacity}
        if metadata.get(RECEIPT) != snapshot:
            metadata[RECEIPT] = snapshot
            await self._call(
                "runPlan.update", run_plan_id=self.scope.run_plan_id, metadata_json=metadata
            )

    async def _launch(self, admitted: AdmittedTicket) -> bool:
        brief = self._brief(admitted.key)
        item = f"ticket:{brief['ticket']['id']}"
        owner_ref = f"dispatch:{self.scope.controller_run_id}:{item}"
        try:
            acquired = await self._call(
                "run.ownerAcquire",
                canonical_item_id=item,
                owner_ref=owner_ref,
                scope_digest=admitted.scope_digest,
            )
        except Exception as exc:
            # A denied item must not stop a separately admitted item. Record the
            # rejection via the same scoped control plane, never unscoped fallback.
            await self._receipt(admitted.key, state="admission_rejected", error=type(exc).__name__)
            return False
        if not acquired["acquired"]:
            return True  # existing owner is never a second launch instruction
        owner = acquired["ownership"]
        intent = f"dispatch:{self.scope.controller_run_id}:{item}:{owner['epoch']}"
        await self._receipt(
            admitted.key,
            ticket_status="in-progress",
            state="launch_intent",
            intent_id=intent,
            started_at=datetime.now(UTC).isoformat(),
            owner_ref=owner_ref,
            epoch=owner["epoch"],
            original_controller_run_id=self.scope.controller_run_id,
            automatic_retries=0,
        )
        # Backend audit creation is not a public run.start call under a forged
        # system token. Re-read its real controller and acquired ownership fence.
        # Process admission still independently revalidates all of this later.
        with Session(self.engine) as session:
            plan = session.get(RunPlan, self.scope.run_plan_id)
            controller = session.get(Run, self.scope.controller_run_id)
            step = session.exec(
                select(RunPlanStep).where(
                    RunPlanStep.run_plan_id == self.scope.run_plan_id,
                    RunPlanStep.step_id == self.scope.step_id,
                )
            ).one_or_none()
            repo = RunRepository(session)
            current = repo.owner_get(project_id=self.scope.project_id, canonical_item_id=item)
            if (
                plan is None
                or controller is None
                or step is None
                or plan.project_id != self.scope.project_id
                or plan.run_id != self.scope.controller_run_id
                or plan.status != "started"
                or controller.status != "running"
                or controller.project_id != self.scope.project_id
                or step.status != "running"
                or controller.client_session_id != self.scope.run_token
                or current.owner_ref != owner_ref
                or current.epoch != owner["epoch"]
                or current.revision != owner["revision"]
                or current.scope_digest != admitted.scope_digest
            ):
                raise ConflictError(
                    "controller or original acquisition changed before attempt audit"
                )
            repo.owner_authority(
                project_id=self.scope.project_id,
                canonical_item_id=item,
                ownership=current,
                require_budget=True,
            )
            attempt_id = repo.start(
                project_id=self.scope.project_id,
                kind=RunKind.SKILL_RUN,
                parent_run_id=self.scope.controller_run_id,
                metadata_json={
                    "ownership_binding": {
                        "canonical_item_id": item,
                        "owner_ref": owner_ref,
                        "epoch": owner["epoch"],
                        "scope_digest": admitted.scope_digest,
                    }
                },
            ).data.id
        await self._receipt(admitted.key, attempt_run_id=attempt_id)
        await self._annotate(admitted, item, attempt_id, "running", intent)
        owner = self._owner(item)
        assert owner is not None
        payload = {
            "prompt": admitted.prompt,
            "canonical_item_id": item,
            "owner_ref": owner_ref,
            "epoch": owner.epoch,
            "revision": owner.revision,
            "scope_digest": admitted.scope_digest,
            "source_revision": self.scope.source_revision,
            "attempt_run_id": attempt_id,
        }

        async def execute() -> None:
            try:
                await self._call(
                    "action.execute", action_ref=ACTION, input_json=payload, idempotency_key=intent
                )
            except Exception as exc:
                await self._receipt(admitted.key, execution_error=type(exc).__name__)

        self._launches[admitted.key] = asyncio.create_task(execute(), name=intent)
        return True

    async def _annotate(
        self, admitted: AdmittedTicket, item: str, attempt_id: int, state: str, evidence: str
    ) -> None:
        owner = self._owner(item)
        assert owner is not None
        await self._call(
            "run.ownerRecordAttempt",
            canonical_item_id=item,
            owner_ref=owner.owner_ref,
            scope_digest=admitted.scope_digest,
            expected_epoch=owner.epoch,
            expected_revision=owner.revision,
            attempt_run_id=attempt_id,
            observation=state,
            evidence_ref=evidence,
        )

    async def _observe(
        self,
        admitted: AdmittedTicket,
        item: str,
        receipt: dict[str, Any],
        owner: WorkItemOwnership,
    ) -> None:
        if receipt.get("state") in {"escalated", "review_ready", "admission_rejected"}:
            return
        with Session(self.engine) as session:
            calls = list(
                session.exec(
                    select(ActionCall).where(
                        ActionCall.project_id == self.scope.project_id,
                        ActionCall.idempotency_key == receipt.get("intent_id", ""),
                    )
                )
            )
        if len(calls) == 1:
            call = calls[0]
            if receipt.get("action_call_id") != call.id:
                await self._receipt(admitted.key, action_call_id=call.id)
            if call.status == "success":
                await self._annotate(
                    admitted,
                    item,
                    receipt["attempt_run_id"],
                    "review_ready",
                    f"actionCall:{call.id}",
                )
                await self._receipt(
                    admitted.key,
                    state="review_ready",
                    review_required=True,
                    capacity_reusable=False,
                    action_call_id=call.id,
                )
                return
            if call.status != "running":
                await self._escalate(admitted, item, receipt, "terminal action requires diagnosis")
                return
        started = receipt.get("started_at")
        origin = (
            datetime.fromisoformat(started) if started else owner.created_at.replace(tzinfo=UTC)
        )
        age = (datetime.now(UTC) - origin).total_seconds()
        if age >= self.scope.stall_seconds:
            await self._escalate(admitted, item, receipt, "stalled or interrupted original attempt")

    async def _escalate(
        self, admitted: AdmittedTicket, item: str, receipt: dict[str, Any], reason: str
    ) -> None:
        # Deterministic key makes a crash between creating and linking the incident
        # recoverable. It is a canonical unfinished ticket, never an accepted review.
        rca_key = f"dispatch-rca-{self.scope.controller_run_id}-{item.split(':')[1]}"
        with Session(self.engine) as session:
            existing = session.exec(
                select(TrackerTicket).where(
                    TrackerTicket.project_id == self.scope.project_id,
                    TrackerTicket.key == rca_key,
                )
            ).first()
        if existing is None:
            await self._call(
                "tracker.createTicket",
                run_plan_id=self.scope.run_plan_id,
                step_id=self.scope.step_id,
                key=rca_key,
                title=f"Diagnose and prevent {admitted.key} execution failure",
                goal="Reconcile original effects and custody; review prevention before closure.",
                definition_of_done_json=[
                    "Explain failure and test escape with original action/attempt evidence.",
                    "Reconcile unknown effects without retrying or releasing uncertain capacity.",
                    "Add prevention evidence and independent review before closing original work.",
                ],
                context_json={
                    "original_item": item,
                    "original_attempt": receipt,
                    "operator_ref": self.scope.operator_ref,
                    "reason": reason,
                    "notification_delivery": "unconfirmed",
                },
            )
        await self._call(
            "tracker.updateTicket",
            ticket_key=admitted.key,
            patch_json={"add_dependency_keys": [rca_key]},
        )
        if receipt.get("attempt_run_id"):
            await self._annotate(
                admitted, item, receipt["attempt_run_id"], "suspected_stall", f"tracker:{rca_key}"
            )
        await self._receipt(
            admitted.key,
            ticket_blocker_reason=f"Execution held; diagnosis and independent review: {rca_key}",
            state="escalated",
            rca_ticket_key=rca_key,
            operator_ref=self.scope.operator_ref,
            reason=reason,
            operator_action="Reconcile original attempt and review prevention; do not retry.",
            notification_delivery="unconfirmed",
            capacity_reusable=False,
            outcome_unknown=True,
        )
