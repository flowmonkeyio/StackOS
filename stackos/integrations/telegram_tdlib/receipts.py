"""Reconcile delayed native send receipts without replaying an uncertain effect."""

from typing import Any

from sqlmodel import Session

from stackos.actions.connectors import ActionConnectorRequest
from stackos.actions.repository import ActionRepository
from stackos.actions.repository.durable import DurableActionItemOut
from stackos.actions.telegram_storage import store_sent_messages
from stackos.auth_providers import ResolvedCredential
from stackos.db.models import ActionCall, Credential, DurableActionJob, IntegrationCredential


def reconcile_telegram_receipt(
    session: Session,
    *,
    credential_ref: str,
    update: dict[str, Any],
) -> None:
    message = update.get("message") or {}
    old_id, chat_id = update.get("old_message_id"), message.get("chat_id")
    if (
        not isinstance(old_id, int)
        or isinstance(old_id, bool)
        or not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
    ):
        return
    kind = update.get("@type")
    if kind not in {"updateMessageSendSucceeded", "updateMessageSendFailed"}:
        return
    succeeded = kind == "updateMessageSendSucceeded"
    final_id = message.get("id")
    if succeeded and (not isinstance(final_id, int) or isinstance(final_id, bool) or final_id <= 0):
        return
    receipt = (
        {"status": "sent", "message_ref": f"telegram-message:{chat_id}:{final_id}"}
        if succeeded
        else {
            "status": "failed",
            "error_code": (update.get("error") or {}).get("code"),
        }
    )
    repo = ActionRepository(session)
    for item in repo.list_unknown_held_durable_action_items(credential_ref=credential_ref):
        progress = item.progress_json or {}
        temporary_ids = progress.get("temporary_message_ids") or []
        if (
            progress.get("chat_id") != chat_id
            or old_id not in temporary_ids
            or item.attempt_ref is None
        ):
            continue
        updated = repo.record_durable_action_item_progress(
            project_id=item.project_id,
            item_id=item.id,
            attempt_ref=item.attempt_ref,
            progress_json={"provider_receipts": {str(old_id): receipt}},
        )
        if succeeded:
            _store_outbound_history(
                session,
                item=updated,
                credential_ref=credential_ref,
                message=message,
            )
        receipts = (updated.progress_json or {}).get("provider_receipts") or {}
        if not all(str(value) in receipts for value in temporary_ids):
            continue
        refs = list((updated.progress_json or {}).get("confirmed_message_refs") or [])
        failures = []
        for value in temporary_ids:
            current = receipts[str(value)]
            if current.get("status") == "sent":
                if current["message_ref"] not in refs:
                    refs.append(current["message_ref"])
            else:
                failures.append(
                    {"temporary_message_id": value, "error_code": current.get("error_code")}
                )
        repo.reconcile_durable_action_item(
            project_id=item.project_id,
            item_id=item.id,
            attempt_ref=item.attempt_ref,
            success=not failures,
            result_json={
                "status": "partial" if refs and failures else "failed" if failures else "sent",
                "surface_ref": f"telegram-chat:{chat_id}",
                "message_refs": refs,
                "message_ref": refs[0] if len(refs) == 1 else None,
                "temporary_message_ids": temporary_ids,
                "failures": failures,
                "reconciled_from_native_receipt": True,
                "retry_safe": False,
            },
        )


def _store_outbound_history(
    session: Session,
    *,
    item: DurableActionItemOut,
    credential_ref: str,
    message: dict[str, Any],
) -> None:
    """Reuse the final-receipt storage writer for a late durable send receipt.

    Receipt processing is the one path where a TDLib final message arrives after
    the normal connector has deliberately held its attempt as unknown.  The
    stored external id remains the same provider/profile/chat/message tuple, so
    duplicate native receipts update the same resource instead of inventing a
    second outbound history row.
    """
    input_json = _message_input(item.input_json)
    if input_json is None:
        return
    job = session.get(DurableActionJob, item.job_id)
    if job is None:  # pragma: no cover - durable item foreign-key invariant
        raise RuntimeError(f"durable action item {item.id} has no job")
    action_call = session.get(ActionCall, job.action_call_id)
    if action_call is None:  # pragma: no cover - durable job foreign-key invariant
        raise RuntimeError(f"durable action job {job.id} has no action call")
    credential = session.get(Credential, action_call.credential_id)
    if credential is None or credential.credential_ref != credential_ref:
        raise RuntimeError("durable Telegram receipt credential does not match its action call")
    integration_id = credential.integration_credential_id
    integration = session.get(IntegrationCredential, integration_id) if integration_id else None
    if integration is None:  # pragma: no cover - connected credential invariant
        raise RuntimeError("durable Telegram receipt has no integration credential")
    store_sent_messages(
        ActionConnectorRequest(
            project_id=item.project_id,
            plugin_slug=action_call.plugin_slug,
            action_key=action_call.action_key,
            action_ref=job.action_ref,
            provider_key="telegram",
            operation="message.send",
            input_json=input_json,
            config_json=dict(credential.config_json or {}),
            credential=ResolvedCredential(
                credential=credential,
                integration=integration,
                secret_payload=b"",
                config_json=dict(credential.config_json or {}),
            ),
            session=session,
            action_call_id=action_call.id,
            attempt_ref=item.attempt_ref,
            correlation_ref=item.correlation_ref,
            delivery_item_id=item.id,
        ),
        [message],
    )


def _message_input(input_json: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize the sealed broadcast item to the send payload stored by its connector."""
    payload: Any = input_json
    nested = input_json.get("message")
    if isinstance(nested, dict) and isinstance(nested.get("profile_ref"), str):
        payload = nested
    if not isinstance(payload, dict) or not isinstance(payload.get("profile_ref"), str):
        return None
    return dict(payload)
