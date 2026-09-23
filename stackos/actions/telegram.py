"""Telegram bot/user messaging through the daemon's managed TDLib session.

Typed methods follow the pinned native API; no Bot API or generic raw-method tool.
https://github.com/tdlib/td/blob/d1085f9cebc5a62379991ae1652673954f229c1f/td/generate/scheme/td_api.tl
"""

from __future__ import annotations

import math
import mimetypes
import re
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import ValidationError as ModelValidationError
from sqlmodel import Session, col, select

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.telegram_payloads import (
    formatted_text,
    message_content,
    reply_markup,
    send_options,
    topic,
)
from stackos.actions.telegram_schema import (
    TELEGRAM_ACTION_MODELS,
    TelegramAlbumSend,
    TelegramBroadcastItem,
    TelegramBroadcastMessage,
    TelegramChatList,
    TelegramChatResolve,
    TelegramFileDownload,
    TelegramMessageBroadcast,
    TelegramMessageEdit,
    TelegramMessageForward,
    TelegramMessageHistory,
    TelegramMessageSend,
    TelegramProfileInput,
    parse_telegram_file_ref,
    telegram_file_ref,
)
from stackos.actions.telegram_storage import store_sent_messages
from stackos.auth_providers import ResolvedCredential
from stackos.auth_providers.repository import AuthRepository
from stackos.communications import (
    communication_profile_record_by_key,
    communication_record_by_external_id,
    communication_surface_binding_external_id,
)
from stackos.communications.target_policy import target_policy_allowed
from stackos.db.models import Artifact
from stackos.integrations.telegram_tdlib.native import (
    TelegramTdlibClosedError,
    TelegramTdlibNativeError,
    TelegramTdlibRequestError,
    safe_error_metadata,
)
from stackos.integrations.telegram_tdlib.service import TelegramTdlibServiceError
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactRepository

_CHAT_REF = re.compile(r"telegram-chat:(-?[1-9][0-9]*)\Z")
# TDLib uses Bot API dialog IDs. Secret chats occupy this disjoint negative range.
# https://core.telegram.org/api/bots/ids
_SECRET_CHAT_MIN = -2_002_147_483_648
_SECRET_CHAT_MAX = -1_997_852_516_353
_CHAT_SEND_PERMISSIONS = (
    "can_send_basic_messages",
    "can_send_audios",
    "can_send_documents",
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
    "can_send_polls",
    "can_send_other_messages",
)
_SEND_OPERATIONS = {"message.send", "album.send", "message.forward", "message.broadcast"}
_LIVE_READ_OPERATIONS = {
    "identity.get",
    "chat.list",
    "chat.resolve",
    "chat.inspect",
    "chat.sender.list",
    "message.history",
    "message.get",
    "file.download",
}
_MUTATIONS = _SEND_OPERATIONS | {
    "message.edit",
    "message.delete",
    "message.react",
    "poll.stop",
    "callback.answer",
}


class TelegramRuntime(Protocol):
    def files_directory(self, account_ref: str) -> Path: ...

    async def request(
        self, account_ref: str, request: dict[str, Any], *, correlation_id: str | None = None
    ) -> dict[str, Any]: ...

    async def wait_message(
        self, account_ref: str, chat_id: int, temporary_message_id: int, *, timeout_seconds: float
    ) -> dict[str, Any]: ...


class _AccountClient:
    def __init__(self, runtime: TelegramRuntime, account_ref: str) -> None:
        self.runtime, self.account_ref = runtime, account_ref

    async def _request(self, request: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return await self.runtime.request(self.account_ref, request, **kwargs)


class TelegramActionConnector:
    key = "telegram"

    def __init__(self, runtime: TelegramRuntime | None = None) -> None:
        self.runtime = runtime

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        model = TELEGRAM_ACTION_MODELS.get(request.operation)
        if model is None:
            return [ActionValidationIssue(path="operation", message="Unsupported Telegram action")]
        try:
            payload = model.model_validate(request.input_json)
        except ModelValidationError as exc:
            return [
                ActionValidationIssue(
                    path="input_json." + ".".join(str(part) for part in error["loc"]),
                    message=error["msg"],
                )
                for error in exc.errors(include_input=False, include_url=False)
            ]
        issue = self._actor_capability_issue(request, payload)
        return [issue] if issue is not None else []

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def prepare_delivery(self, request: ActionConnectorRequest) -> list[dict[str, Any]]:
        """Seal authorized destinations before any Telegram send or recipient lookup."""
        if request.operation not in _MUTATIONS:
            raise ValidationError("Only Telegram mutations use durable delivery")
        payload = TELEGRAM_ACTION_MODELS[request.operation].model_validate(request.input_json)
        self._profile(request, cast(TelegramProfileInput, payload))
        self._actor_capabilities(request, payload)
        self._assert_project_artifacts(request, payload)
        self._assert_native_file_ownership(request, payload)
        client = self._client(request)
        if isinstance(payload, TelegramMessageBroadcast):
            return await self._prepare_broadcast(client, request, payload)
        surface_ref = getattr(payload, "surface_ref", None)
        if surface_ref:
            chat_id = _chat_id(surface_ref)
            inspected = await self._inspect(
                client, chat_id, require_write=request.operation in _SEND_OPERATIONS
            )
            if request.operation in _SEND_OPERATIONS:
                self._assert_content_permissions(inspected, payload)
                await self._sender_for_send(
                    client, inspected["chat"], getattr(payload, "sender_ref", None), mutate=False
                )
            self._surface(request, cast(TelegramProfileInput, payload).profile_ref, surface_ref)
        if isinstance(payload, TelegramMessageSend):
            await message_content(client, payload.content, asset_dir=request.asset_dir)
        elif isinstance(payload, TelegramAlbumSend):
            for content in payload.contents:
                await message_content(client, content, asset_dir=request.asset_dir)
        destination_ref = surface_ref
        if destination_ref is None:
            callback_query_id = getattr(payload, "callback_query_id", None)
            if not isinstance(callback_query_id, str):
                raise ValidationError("Telegram mutation requires a destination or callback query")
            destination_ref = f"telegram-callback:{callback_query_id}"
        return [
            {
                "destination_ref": destination_ref,
                "input_json": payload.model_dump(mode="json"),
            }
        ]

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation in _MUTATIONS and (
            request.action_call_id is None
            or request.attempt_ref is None
            or getattr(request, "delivery_item_id", None) is None
        ):
            raise ValidationError("Telegram mutations require a durable delivery lease")
        item = (
            TelegramBroadcastItem.model_validate(request.input_json)
            if request.operation == "message.broadcast"
            else None
        )
        if item is not None:
            try:
                self._authorize_broadcast(
                    request, item.target_ref, item.message, [item.recipient_ref]
                )
            except ValidationError as exc:
                raise ActionConnectorError(
                    str(exc),
                    output_json={
                        "status": "recipient_policy_rejected",
                        "recipient_ref": item.recipient_ref,
                        "provider_executed": False,
                        "retry_safe": False,
                    },
                ) from exc
        payload = (
            item.message
            if item is not None
            else TELEGRAM_ACTION_MODELS[request.operation].model_validate(request.input_json)
        )
        self._profile(request, cast(TelegramProfileInput, payload))
        self._actor_capabilities(request, payload)
        self._assert_project_artifacts(request, payload)
        self._assert_native_file_ownership(request, payload)
        if request.operation in _LIVE_READ_OPERATIONS:
            self._require_connected_for_read(request)
        client = self._client(request)
        try:
            broadcast_private_chat = None
            if item is not None:
                if item.recipient_ref.startswith("telegram-user:"):
                    user_id = int(item.recipient_ref.removeprefix("telegram-user:"))
                    try:
                        broadcast_private_chat = await client._request(
                            {"@type": "createPrivateChat", "user_id": user_id, "force": False}
                        )
                    except TelegramTdlibRequestError:
                        raise
                    except (TelegramTdlibServiceError, TelegramTdlibNativeError) as exc:
                        raise ActionConnectorError(
                            "Telegram did not confirm the recipient chat; no message was submitted",
                            output_json={
                                "status": "recipient_resolution_unknown",
                                "provider_executed": False,
                                "message_submitted": False,
                                "retry_safe": True,
                            },
                        ) from exc
                    chat_id = broadcast_private_chat.get("id")
                    if not isinstance(chat_id, int) or isinstance(chat_id, bool) or chat_id == 0:
                        raise ValidationError(
                            "Telegram did not return a private chat for recipient"
                        )
                    if chat_id != user_id:
                        raise ValidationError(
                            "Telegram returned a private chat with an unexpected dialog ID"
                        )
                    surface_ref = f"telegram-chat:{chat_id}"
                else:
                    surface_ref = _canonical_broadcast_destination(item.recipient_ref)
                payload = TelegramMessageSend.model_validate(
                    {**item.message.model_dump(mode="json"), "surface_ref": surface_ref}
                )
                request = replace(
                    request, operation="message.send", input_json=payload.model_dump(mode="json")
                )
            output = await self._execute(
                client,
                request,
                payload,
                broadcast_recipient_ref=item.recipient_ref if item is not None else None,
                broadcast_private_chat=broadcast_private_chat,
            )
        except ValidationError as exc:
            if item is None:
                raise
            raise ActionConnectorError(
                str(exc),
                output_json={
                    "status": "recipient_rejected",
                    "recipient_ref": item.recipient_ref,
                    "provider_executed": False,
                    "retry_safe": False,
                    "reason": str(exc),
                    "repair_context": exc.data,
                },
            ) from exc
        except ActionConnectorError as exc:
            if item is not None:
                exc.output_json.setdefault("recipient_ref", item.recipient_ref)
            raise
        except (TelegramTdlibServiceError, TelegramTdlibClosedError) as exc:
            if request.operation in _LIVE_READ_OPERATIONS and (
                isinstance(exc, TelegramTdlibClosedError)
                or str(exc)
                in {"TDLib session is not configured", "TDLib session generation is stale"}
            ):
                raise self._not_connected_error(request) from exc
            raise
        except TelegramTdlibRequestError as exc:
            retry_after = getattr(exc, "retry_after_seconds", None)
            error_name = getattr(exc, "error_name", None)
            raise ActionConnectorError(
                str(exc),
                provider_status_code=exc.code,
                output_json={
                    "status": "failed",
                    **({"recipient_ref": item.recipient_ref} if item is not None else {}),
                    "provider_status_code": exc.code,
                    "provider_error": error_name or "TDLIB_REQUEST_REJECTED",
                    "retry_after_seconds": retry_after,
                    "retry_scope": _retry_scope(error_name),
                    "provider_executed": False,
                    "retry_safe": retry_after is not None,
                    "account_restricted": error_name == "PEER_FLOOD",
                },
            ) from exc
        except TelegramTdlibNativeError as exc:
            if (
                request.operation in _LIVE_READ_OPERATIONS
                and str(exc) == "TDLib request timed out awaiting a response."
            ):
                raise ActionConnectorError(
                    "Telegram read timed out before TDLib returned a result",
                    output_json={
                        "status": "retryable_timeout",
                        "next_action": request.action_ref,
                        "retry_safe": True,
                        "provider_result_known": False,
                    },
                ) from exc
            raise
        if item is not None:
            output["recipient_ref"] = item.recipient_ref
        return ActionConnectorResult(output_json=output, metadata_json={"transport": "tdlib"})

    def _require_connected_for_read(self, request: ActionConnectorRequest) -> None:
        account = self._credential(request).credential
        if (
            account.status == "disconnected"
            or (account.config_json or {}).get("telegram_desired_connected") is False
        ):
            raise self._not_connected_error(request)

    @staticmethod
    def _not_connected_error(request: ActionConnectorRequest) -> ActionConnectorError:
        return ActionConnectorError(
            "Telegram Account session is not connected",
            output_json={
                "status": "not_connected",
                "credential_ref": request.credential.credential_ref if request.credential else None,
                "next_action": "account.session.connect",
                "provider_executed": False,
                "retry_safe": True,
            },
        )

    def _authorize_broadcast(
        self, request: ActionConnectorRequest, target_ref: str, payload: Any, recipients: list[str]
    ) -> None:
        target_ref = "communication-target:" + target_ref.removeprefix("communication-target:")
        session = self._session(request)
        record = communication_record_by_external_id(
            session,
            project_id=request.project_id,
            resource_key="communication-target",
            external_id=target_ref,
        )
        data = dict(record.data_json or {}) if record else {}
        policy = dict(data.get("send_policy") or {})
        metadata = dict(data.get("metadata_json") or {})
        profile_ref = "communication-profile:" + payload.profile_ref.removeprefix(
            "communication-profile:"
        )
        allowed_profiles = policy.get("allowed_profile_refs")
        allowed_actions = metadata.get("allowed_action_refs")
        configured_action_ref = data.get("action_ref")
        if (
            not data.get("enabled", False)
            or data.get("provider_key") != "telegram"
            or policy.get("destination_mode") != "recipient-list"
            or data.get("profile_ref") not in {None, profile_ref}
            or not isinstance(allowed_profiles, list)
            or profile_ref not in allowed_profiles
        ):
            raise ValidationError(
                "Broadcast requires an enabled Telegram recipient-list target with an "
                "explicit profile allowlist"
            )
        if (
            configured_action_ref != "communications.telegram.message.broadcast"
            and metadata.get("action_mode") != "auto"
            and (
                not isinstance(allowed_actions, list)
                or "communications.telegram.message.broadcast" not in allowed_actions
            )
        ):
            raise ValidationError(
                "Broadcast target does not authorize the Telegram message.broadcast action variant"
            )
        source_id = payload.source_agent_request_id
        source = (
            AgentRequestRepository(session).get(
                project_id=request.project_id,
                request_id=source_id,
            )
            if source_id
            else None
        )
        source_data = dict(source.metadata_json or {}) if source else {}
        allowed, reason = target_policy_allowed(
            policy,
            target_ref=target_ref,
            profile_ref=profile_ref,
            source_surface_ref=source_data.get("source_surface_ref")
            or source_data.get("surface_ref"),
            invoker_ref=source_data.get("invoker_ref"),
        )
        if not allowed:
            raise ValidationError(f"Broadcast target policy rejected delivery: {reason}")
        allowed_recipients = policy.get("allowed_recipient_refs")
        if allowed_recipients is not None and (
            not isinstance(allowed_recipients, list)
            or not set(recipients) <= set(allowed_recipients)
        ):
            raise ValidationError(
                "Broadcast recipient is outside the target's allowed recipient refs"
            )

    async def _prepare_broadcast(
        self,
        client: _AccountClient,
        request: ActionConnectorRequest,
        payload: TelegramMessageBroadcast,
    ) -> list[dict[str, Any]]:
        self._authorize_broadcast(request, payload.target_ref, payload, payload.recipients)
        destinations = [_canonical_broadcast_destination(ref) for ref in payload.recipients]
        if len(set(destinations)) != len(destinations):
            raise ValidationError("Different recipient refs identify the same Telegram chat")
        await message_content(client, payload.content, asset_dir=request.asset_dir)
        items = []
        message = TelegramBroadcastMessage.model_validate(
            payload.model_dump(exclude={"target_ref", "recipients"})
        )
        for recipient, destination_ref in zip(payload.recipients, destinations, strict=True):
            item = TelegramBroadcastItem(
                target_ref=payload.target_ref, recipient_ref=recipient, message=message
            )
            items.append(
                {"destination_ref": destination_ref, "input_json": item.model_dump(mode="json")}
            )
        return items

    def _client(self, request: ActionConnectorRequest) -> _AccountClient:
        if self.runtime is None:
            raise ValidationError(
                "Managed Telegram runtime is not available",
                data={
                    "provider_key": "telegram",
                    "next_action": "Repair the TDLib runtime and reconnect the Account.",
                },
            )
        credential = self._credential(request)
        return _AccountClient(self.runtime, credential.credential_ref)

    @staticmethod
    def _session(request: ActionConnectorRequest) -> Session:
        if request.session is None:
            raise ValidationError("Telegram actions require a project-scoped session")
        return cast(Session, request.session)

    @staticmethod
    def _credential(request: ActionConnectorRequest) -> ResolvedCredential:
        if request.credential is None:
            raise ValidationError("Telegram requires a connected Account")
        return request.credential

    def _profile(
        self, request: ActionConnectorRequest, payload: TelegramProfileInput
    ) -> dict[str, Any]:
        session = self._session(request)
        credential = self._credential(request)
        key = payload.profile_ref.removeprefix("communication-profile:")
        record = communication_profile_record_by_key(
            session, project_id=request.project_id, key=key
        )
        data = dict(record.data_json or {}) if record else {}
        facet = (data.get("provider_facets") or {}).get("telegram") or {}
        if not data or data.get("enabled") is False or facet.get("enabled") is False:
            raise ValidationError("Telegram communication profile is missing or disabled")
        if facet.get("credential_ref") != credential.credential_ref:
            raise ValidationError(
                "Telegram communication profile does not match the selected Account"
            )
        return data

    def _surface(self, request: ActionConnectorRequest, profile_ref: str, surface_ref: str) -> None:
        profile_ref = "communication-profile:" + profile_ref.removeprefix("communication-profile:")
        record = communication_record_by_external_id(
            self._session(request),
            project_id=request.project_id,
            resource_key="communication-channel",
            external_id=communication_surface_binding_external_id(
                provider_key="telegram", profile_ref=profile_ref, surface_ref=surface_ref
            ),
        )
        data = dict(record.data_json or {}) if record else {}
        if (
            data.get("surface_binding_state") not in {None, "ready"}
            or data.get("send_enabled") is False
        ):
            raise ValidationError("Telegram surface is disabled or requires binding repair")

    @staticmethod
    def _is_bot_account(request: ActionConnectorRequest) -> bool | None:
        if request.credential is not None:
            return request.credential.credential.auth_method_key == "tdlib-bot-token"
        if request.credential_ref is None or request.session is None:
            return None
        account = AuthRepository(cast(Session, request.session)).require_attached_account(
            project_id=request.project_id,
            credential_ref=request.credential_ref,
            provider_key="telegram",
            require_connected=False,
        )
        return account.auth_method_key == "tdlib-bot-token"

    def _actor_capability_issue(
        self, request: ActionConnectorRequest, payload: Any
    ) -> ActionValidationIssue | None:
        is_bot = self._is_bot_account(request)
        if is_bot is None:
            return None
        if is_bot and request.operation in {"chat.list", "message.history"}:
            return ActionValidationIssue(
                path="operation",
                message=(
                    "Telegram chat list and history pagination require a user Account; "
                    "bots can read selected retained updates and resolve known chats or messages"
                ),
            )
        options = getattr(payload, "options", None)
        if not is_bot and request.operation == "callback.answer":
            return ActionValidationIssue(
                path="operation", message="This Telegram action is supported only by bot Accounts"
            )
        if not is_bot and isinstance(payload, TelegramMessageEdit) and payload.kind == "buttons":
            return ActionValidationIssue(
                path="input_json.kind",
                message="Editing Telegram message buttons is supported only by bot Accounts",
            )
        if not is_bot and getattr(payload, "buttons", None):
            return ActionValidationIssue(
                path="input_json.buttons",
                message="Telegram inline buttons are supported only by bot Accounts",
            )
        if not is_bot and options is not None and options.protect_content:
            return ActionValidationIssue(
                path="input_json.options.protect_content",
                message="Telegram protected content is supported only by bot Accounts",
            )
        for content in getattr(payload, "contents", [getattr(payload, "content", None)]):
            if (
                content is not None
                and content.kind == "poll"
                and not is_bot
                and (content.is_closed or len(content.question) > 255)
            ):
                return ActionValidationIssue(
                    path="input_json.content",
                    message="This Telegram poll option is supported only by bot Accounts",
                )
        return None

    def _actor_capabilities(self, request: ActionConnectorRequest, payload: Any) -> None:
        is_bot = self._is_bot_account(request)
        if is_bot is None:
            raise ValidationError("Telegram Account kind could not be determined")
        issue = self._actor_capability_issue(request, payload)
        if issue is not None:
            repair = (
                {
                    "account_kind": "bot",
                    "supported_account_kinds": ["user"],
                    "next_action": "Use retained Telegram updates or resolve a known chat/message",
                    "provider_executed": False,
                }
                if is_bot and request.operation in {"chat.list", "message.history"}
                else {}
            )
            raise ValidationError(issue.message, data={"path": issue.path, **repair})

    def _assert_project_artifacts(self, request: ActionConnectorRequest, payload: Any) -> None:
        """Require every local media ref to be an active artifact of this project."""

        session = self._session(request)
        contents = [getattr(payload, "content", None), *(getattr(payload, "contents", []) or [])]
        for content in contents:
            if content is None:
                continue
            for field in ("file", "thumbnail", "cover"):
                file = getattr(content, field, None)
                artifact_ref = getattr(file, "artifact_ref", None) if file is not None else None
                if artifact_ref is None:
                    continue
                artifact = session.exec(
                    select(Artifact).where(
                        col(Artifact.project_id) == request.project_id,
                        col(Artifact.uri) == artifact_ref,
                    )
                ).first()
                if artifact is None or artifact.status not in {"draft", "approved"}:
                    raise ValidationError(
                        "Telegram upload artifact is not an active artifact in this project",
                        data={"artifact_ref": artifact_ref},
                    )

    def _assert_native_file_ownership(self, request: ActionConnectorRequest, payload: Any) -> None:
        """Reject an ``inputFileId`` that belongs to a different TDLib Account."""

        expected_account_ref = self._credential(request).credential_ref
        file_refs: list[str] = []
        if isinstance(payload, TelegramFileDownload):
            file_refs.append(payload.file_ref)
        contents = [getattr(payload, "content", None), *(getattr(payload, "contents", []) or [])]
        for content in contents:
            if content is None:
                continue
            for field in ("file", "thumbnail", "cover"):
                file = getattr(content, field, None)
                file_ref = getattr(file, "file_ref", None) if file is not None else None
                if file_ref is not None:
                    file_refs.append(file_ref)
        for file_ref in file_refs:
            account_ref, _file_id = parse_telegram_file_ref(file_ref)
            if account_ref != expected_account_ref:
                raise ValidationError(
                    "Telegram native file belongs to a different Account",
                    data={"file_ref": file_ref, "credential_ref": expected_account_ref},
                )

    async def _inspect(
        self, client: _AccountClient, chat_id: int, *, require_write: bool = False
    ) -> dict[str, Any]:
        chat = await client._request({"@type": "getChat", "chat_id": chat_id})
        chat_type = (chat.get("type") or {}).get("@type")
        if chat_type == "chatTypeSecret":
            raise ValidationError("Secret chats are not enabled for StackOS Telegram Accounts")
        if chat_type == "chatTypePrivate":
            can_post = (chat.get("permissions") or {}).get("can_send_basic_messages", True)
            if require_write and not can_post:
                raise ValidationError("Telegram private chat does not allow messages")
            return {
                "chat": _safe_native(chat),
                "membership": None,
                "is_member": None,
                "can_post_messages": can_post,
            }
        if chat_type == "chatTypeBasicGroup":
            group_id_key, group_method = "basic_group_id", "getBasicGroup"
        elif chat_type == "chatTypeSupergroup":
            group_id_key, group_method = "supergroup_id", "getSupergroup"
        else:
            raise ValidationError("Telegram chat type does not provide current Account membership")
        group_id = (chat.get("type") or {}).get(group_id_key)
        if not isinstance(group_id, int) or isinstance(group_id, bool) or group_id <= 0:
            raise ValidationError("Telegram chat type does not provide current Account membership")
        group = await client._request({"@type": group_method, group_id_key: group_id})
        status = group.get("status") or {}
        status_type = status.get("@type")
        if not isinstance(status_type, str):
            raise ValidationError("Telegram did not return current Account membership status")
        is_channel = bool((chat.get("type") or {}).get("is_channel"))
        joined = status_type in {
            "chatMemberStatusAdministrator",
            "chatMemberStatusMember",
        }
        if status_type in {"chatMemberStatusCreator", "chatMemberStatusRestricted"}:
            joined = status.get("is_member") is True
        rights = dict(status.get("rights") or {})
        permissions = dict(
            (
                status.get("permissions")
                if status_type == "chatMemberStatusRestricted"
                else chat.get("permissions")
            )
            or {}
        )
        can_post = joined and (
            status_type == "chatMemberStatusCreator"
            or (
                rights.get("can_post_messages") is True
                if is_channel
                else status_type == "chatMemberStatusAdministrator"
                or any(permissions.get(key) is True for key in _CHAT_SEND_PERMISSIONS)
            )
        )
        if require_write and (not joined or (is_channel and not can_post)):
            raise ValidationError(
                "Telegram Account cannot send to this chat",
                data={
                    "surface_ref": f"telegram-chat:{chat_id}",
                    "is_member": joined,
                    "can_post_messages": can_post,
                },
            )
        return {
            "chat": _safe_native(chat),
            "membership": {"status": _safe_native(status)},
            "is_member": joined,
            "can_post_messages": can_post,
        }

    @staticmethod
    def _send_permissions(inspected: dict[str, Any]) -> dict[str, bool]:
        chat = inspected["chat"]
        chat_type = chat.get("type") or {}
        if chat_type.get("@type") not in {"chatTypeBasicGroup", "chatTypeSupergroup"}:
            return {}
        if chat_type.get("is_channel") is True:
            return {}
        status = (inspected.get("membership") or {}).get("status") or {}
        status_type = status.get("@type")
        if status_type in {"chatMemberStatusAdministrator", "chatMemberStatusCreator"}:
            return {key: inspected["is_member"] is True for key in _CHAT_SEND_PERMISSIONS}
        source = (
            status.get("permissions")
            if status_type == "chatMemberStatusRestricted"
            else chat.get("permissions")
        ) or {}
        return {
            key: inspected["is_member"] is True and source.get(key) is True
            for key in _CHAT_SEND_PERMISSIONS
        }

    @staticmethod
    def _assert_content_permissions(inspected: dict[str, Any], payload: Any) -> None:
        chat = inspected["chat"]
        chat_type = chat.get("type") or {}
        if chat_type.get("@type") not in {"chatTypeBasicGroup", "chatTypeSupergroup"}:
            return
        if chat_type.get("is_channel") is True:
            # Channel administrator rights were checked by _inspect.
            return
        permissions = TelegramActionConnector._send_permissions(inspected)
        permission_for_kind = {
            "text": "can_send_basic_messages",
            "location": "can_send_basic_messages",
            "venue": "can_send_basic_messages",
            "contact": "can_send_basic_messages",
            "audio": "can_send_audios",
            "document": "can_send_documents",
            "photo": "can_send_photos",
            "video": "can_send_videos",
            "video_note": "can_send_video_notes",
            "voice": "can_send_voice_notes",
            "poll": "can_send_polls",
            "animation": "can_send_other_messages",
            "sticker": "can_send_other_messages",
            "dice": "can_send_other_messages",
        }
        contents = getattr(payload, "contents", None)
        if contents is None:
            contents = [getattr(payload, "content", None)]
        for content in contents:
            if content is None:
                continue
            required = permission_for_kind.get(content.kind)
            if required is None:
                raise ValidationError("Telegram content type has no mapped chat permission")
            if permissions.get(required) is not True:
                raise ValidationError(
                    f"Telegram chat permission {required} is required for {content.kind}",
                    data={
                        "surface_ref": f"telegram-chat:{chat['id']}",
                        "permission": required,
                        "content_kind": content.kind,
                    },
                )

    async def _sender_options(
        self, client: _AccountClient, chat: dict[str, Any], *, limit: int | None = 100
    ) -> dict[str, Any]:
        chat_id = chat["id"]
        selected_ref = _sender_ref(chat.get("message_sender_id"))
        chat_type = (chat.get("type") or {}).get("@type")
        if chat_type == "chatTypePrivate":
            me = await client._request({"@type": "getMe"})
            account_ref = _sender_ref({"@type": "messageSenderUser", "user_id": me.get("id")})
            if account_ref is None:
                raise ValidationError("Telegram did not return the Account sender")
            choices = [{"sender_ref": account_ref, "needs_premium": False}]
            selected_ref = account_ref
        else:
            result = await client._request(
                {"@type": "getChatAvailableMessageSenders", "chat_id": chat_id}
            )
            native_choices = result.get("senders")
            if not isinstance(native_choices, list):
                raise ValidationError("Telegram did not return available chat senders")
            choices = []
            for native_choice in native_choices:
                if not isinstance(native_choice, dict):
                    raise ValidationError("Telegram returned an invalid chat sender option")
                sender_ref = _sender_ref(native_choice.get("sender"))
                needs_premium = native_choice.get("needs_premium")
                if sender_ref is None or not isinstance(needs_premium, bool):
                    raise ValidationError("Telegram returned an invalid chat sender option")
                choices.append({"sender_ref": sender_ref, "needs_premium": needs_premium})
        return {
            "surface_ref": f"telegram-chat:{chat_id}",
            "selected_sender_ref": selected_ref,
            "available_senders": choices[:limit] if limit is not None else choices,
            "available_sender_count": len(choices),
            "available_senders_truncated": limit is not None and len(choices) > limit,
        }

    async def _sender_for_send(
        self,
        client: _AccountClient,
        chat: dict[str, Any],
        requested_ref: str | None,
        *,
        mutate: bool,
        sender_options: dict[str, Any] | None = None,
    ) -> tuple[str | None, bool]:
        chat_id = chat["id"]
        chat_type = chat.get("type") or {}
        native_type = chat_type.get("@type")
        if requested_ref is not None:
            _native_sender(requested_ref)
        if native_type == "chatTypePrivate" and requested_ref is None:
            return None, False
        options = sender_options or await self._sender_options(client, chat, limit=None)
        choices = options["available_senders"]
        if requested_ref is not None:
            choice = next((item for item in choices if item["sender_ref"] == requested_ref), None)
            if choice is None:
                raise ValidationError(
                    "Requested Telegram sender is unavailable in this destination chat",
                    data={
                        "sender_ref": requested_ref,
                        "surface_ref": f"telegram-chat:{chat_id}",
                        "next_action": "Call chat.sender.list and choose one available sender_ref.",
                    },
                )
        else:
            eligible = [item for item in choices if item["needs_premium"] is False]
            if len(eligible) != 1:
                raise ValidationError(
                    "Choose a Telegram sender for this chat before sending",
                    data={
                        "surface_ref": f"telegram-chat:{chat_id}",
                        "available_sender_count": len(choices),
                        "next_action": "Call chat.sender.list and pass sender_ref explicitly.",
                    },
                )
            choice = eligible[0]
        if choice["needs_premium"]:
            raise ValidationError(
                "Telegram Premium is required for the requested sender",
                data={
                    "sender_ref": choice["sender_ref"],
                    "surface_ref": f"telegram-chat:{chat_id}",
                },
            )
        chosen_ref = choice["sender_ref"]
        if not mutate or options["selected_sender_ref"] == chosen_ref:
            return chosen_ref, False
        try:
            await client._request(
                {
                    "@type": "setChatMessageSender",
                    "chat_id": chat_id,
                    "message_sender_id": _native_sender(chosen_ref),
                }
            )
            refreshed = await client._request({"@type": "getChat", "chat_id": chat_id})
        except (TelegramTdlibServiceError, TelegramTdlibNativeError) as exc:
            raise ActionConnectorError(
                "Telegram sender selection could not be verified; no message was submitted",
                output_json={
                    "status": "sender_selection_outcome_unknown",
                    "sender_ref": chosen_ref,
                    "surface_ref": f"telegram-chat:{chat_id}",
                    "sender_selection_changed": True,
                    "message_submitted": False,
                    "provider_executed": True,
                    "retry_safe": False,
                },
            ) from exc
        if _sender_ref(refreshed.get("message_sender_id")) != chosen_ref:
            raise ActionConnectorError(
                "Telegram sender selection changed but could not be verified; no message was sent",
                output_json={
                    "status": "sender_selection_unverified",
                    "sender_ref": chosen_ref,
                    "surface_ref": f"telegram-chat:{chat_id}",
                    "sender_selection_changed": True,
                    "provider_executed": True,
                    "retry_safe": False,
                },
            )
        return chosen_ref, True

    async def _execute(
        self,
        client: _AccountClient,
        request: ActionConnectorRequest,
        payload: Any,
        *,
        broadcast_recipient_ref: str | None = None,
        broadcast_private_chat: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation = request.operation
        if operation == "identity.get":
            user = await client._request({"@type": "getMe"})
            return {
                "provider": "telegram",
                "user_id": user["id"],
                "first_name": user.get("first_name"),
                "last_name": user.get("last_name"),
                "usernames": user.get("usernames"),
                "account_kind": "bot"
                if self._credential(request).credential.auth_method_key == "tdlib-bot-token"
                else "user",
            }
        if operation == "chat.list":
            assert isinstance(payload, TelegramChatList)
            return await self._list_chats(client, payload)
        if operation == "chat.resolve":
            assert isinstance(payload, TelegramChatResolve)
            if payload.username is not None:
                chat = await client._request(
                    {"@type": "searchPublicChat", "username": payload.username.lstrip("@")}
                )
            elif payload.user_id is not None:
                await client._request({"@type": "getUser", "user_id": payload.user_id})
                chat = await client._request(
                    {"@type": "createPrivateChat", "user_id": payload.user_id, "force": False}
                )
            else:
                chat = await client._request({"@type": "getChat", "chat_id": payload.chat_id})
            return {
                "surface_ref": f"telegram-chat:{chat['id']}",
                "chat": _chat_summary(
                    chat, account_can_page_history=not self._is_bot_account(request)
                ),
            }
        if operation == "callback.answer":
            result = await client._request(
                {"@type": "answerCallbackQuery", **payload.model_dump(exclude={"profile_ref"})}
            )
            return {"status": "acknowledged", "result": _safe_native(result)}
        if operation == "file.download":
            _account_ref, file_id = parse_telegram_file_ref(payload.file_ref)
            return await self._download(client, request, file_id)
        chat_id = _chat_id(payload.surface_ref)
        if operation == "chat.inspect":
            inspected = await self._inspect(client, chat_id)
            member = inspected["membership"] or {}
            status = member.get("status") or {}
            rights = status.get("rights") or {}
            return {
                "surface_ref": payload.surface_ref,
                "chat": _chat_summary(
                    inspected["chat"], account_can_page_history=not self._is_bot_account(request)
                ),
                "membership_status": status.get("@type"),
                "is_member": inspected["is_member"],
                "can_post_messages": inspected["can_post_messages"],
                "send_permissions": self._send_permissions(inspected),
                "admin_rights": {
                    key: rights[key]
                    for key in sorted(rights)
                    if key.startswith("can_") and isinstance(rights[key], bool)
                },
            }
        if operation == "chat.sender.list":
            inspected = await self._inspect(client, chat_id)
            return await self._sender_options(client, inspected["chat"])
        if operation == "message.history":
            assert isinstance(payload, TelegramMessageHistory)
            await self._assert_readable_chat(client, chat_id)
            return await self._history(client, chat_id, payload)
        if operation == "message.get":
            await self._assert_readable_chat(client, chat_id)
            result = await client._request(
                {"@type": "getMessage", "chat_id": chat_id, "message_id": payload.message_id}
            )
            return {
                "message_ref": f"telegram-message:{chat_id}:{payload.message_id}",
                "message": _message_summary(
                    result, chat_id, client.account_ref, include_content=True
                ),
            }
        self._surface(request, payload.profile_ref, payload.surface_ref)
        sender_chat = None
        sender_options = None
        requested_sender_ref = getattr(payload, "sender_ref", None)
        if broadcast_recipient_ref is not None:
            # The recipient already has a durable lease. Telegram's send result
            # decides membership and content eligibility for this item.
            if broadcast_private_chat is not None:
                if (broadcast_private_chat.get("type") or {}).get("@type") != "chatTypePrivate":
                    raise ValidationError("Telegram did not return a private chat for recipient")
                if payload.sender_ref is not None:
                    sender_chat = _safe_native(broadcast_private_chat)
            elif chat_id < 0:
                # Non-private chats can expose several sender identities. Pick
                # exactly one eligible sender before submitting a post, without
                # using membership/content inspection as an acceptance gate.
                placeholder = {"id": chat_id, "type": {"@type": "chatTypeSupergroup"}}
                sender_options = await self._sender_options(client, placeholder, limit=None)
                requested_sender_ref, _ = await self._sender_for_send(
                    client,
                    placeholder,
                    requested_sender_ref,
                    mutate=False,
                    sender_options=sender_options,
                )
                sender_chat = await client._request({"@type": "getChat", "chat_id": chat_id})
                if (sender_chat.get("type") or {}).get("@type") == "chatTypeSecret":
                    raise ValidationError(
                        "Secret chats are not enabled for StackOS Telegram Accounts"
                    )
                sender_options["selected_sender_ref"] = _sender_ref(
                    sender_chat.get("message_sender_id")
                )
            elif payload.sender_ref is not None:
                # Explicit sender selection requires the destination chat, but
                # we do not inspect membership or permissions as a send gate.
                sender_chat = await client._request({"@type": "getChat", "chat_id": chat_id})
                if (sender_chat.get("type") or {}).get("@type") == "chatTypeSecret":
                    raise ValidationError(
                        "Secret chats are not enabled for StackOS Telegram Accounts"
                    )
        else:
            inspected = await self._inspect(
                client, chat_id, require_write=operation in _SEND_OPERATIONS
            )
            if operation in _SEND_OPERATIONS:
                self._assert_content_permissions(inspected, payload)
                sender_chat = inspected["chat"]
        query = await self._mutation_query(client, request, payload, chat_id)
        sender_ref = None
        sender_selection_changed = False
        if operation in _SEND_OPERATIONS and sender_chat is not None:
            sender_ref, sender_selection_changed = await self._sender_for_send(
                client,
                sender_chat,
                requested_sender_ref,
                mutate=True,
                sender_options=sender_options,
            )
        correlation = getattr(request, "correlation_ref", None)
        try:
            result = await client._request(query, correlation_id=correlation)
        except TelegramTdlibRequestError as exc:
            if not sender_selection_changed:
                raise
            raise ActionConnectorError(
                "Telegram sender was selected, but the message request failed",
                provider_status_code=exc.code,
                output_json={
                    "status": "sender_selected_send_failed",
                    "surface_ref": payload.surface_ref,
                    "sender_ref": sender_ref,
                    "sender_selection_changed": True,
                    "provider_executed": True,
                    "retry_safe": False,
                    "provider_error": exc.error_name or "TDLIB_REQUEST_REJECTED",
                    "provider_status_code": exc.code,
                    "retry_after_seconds": exc.retry_after_seconds,
                    "retry_scope": _retry_scope(exc.error_name),
                    "account_restricted": exc.error_name == "PEER_FLOOD",
                },
            ) from exc
        except (TelegramTdlibServiceError, TelegramTdlibNativeError) as exc:
            raise ActionConnectorError(
                "Telegram did not confirm whether the message was submitted",
                output_json={
                    "status": "send_outcome_unknown",
                    "surface_ref": payload.surface_ref,
                    "sender_ref": sender_ref,
                    "sender_selection_changed": sender_selection_changed,
                    "provider_executed": True,
                    "provider_result_known": False,
                    "retry_safe": False,
                },
            ) from exc
        if operation in _SEND_OPERATIONS:
            try:
                return await self._send_receipt(
                    client,
                    request,
                    chat_id,
                    result,
                    expected_sender_ref=sender_ref,
                    sender_selection_changed=sender_selection_changed,
                )
            except (TelegramTdlibServiceError, TelegramTdlibNativeError, TimeoutError) as exc:
                raise ActionConnectorError(
                    "Telegram accepted the message, but its final receipt is unknown",
                    output_json={
                        "status": "send_receipt_unknown",
                        "surface_ref": payload.surface_ref,
                        "sender_ref": sender_ref,
                        "sender_selection_changed": sender_selection_changed,
                        "provider_executed": True,
                        "provider_result_known": False,
                        "retry_safe": False,
                    },
                ) from exc
        return {
            "status": "completed",
            "surface_ref": payload.surface_ref,
            "result": _safe_native(result),
        }

    @staticmethod
    async def _assert_readable_chat(client: _AccountClient, chat_id: int) -> None:
        chat = await client._request({"@type": "getChat", "chat_id": chat_id})
        if (chat.get("type") or {}).get("@type") == "chatTypeSecret":
            raise ValidationError("Secret chats are not enabled for StackOS Telegram Accounts")

    async def _list_chats(
        self, client: _AccountClient, payload: TelegramChatList
    ) -> dict[str, Any]:
        # TDLib getChats has no offset: it returns a beginning-of-list snapshot.
        # loadChats extends TDLib's loaded list; the cursor slices that snapshot.
        # https://github.com/tdlib/td/blob/d1085f9cebc5a62379991ae1652673954f229c1f/td/generate/scheme/td_api.tl
        offset = int(payload.cursor.split(":", 1)[1]) if payload.cursor else 0
        requested = offset + payload.limit + 1
        if requested > 5000:
            requested = 5000
        chat_list = {"@type": "chatListMain" if payload.chat_list == "main" else "chatListArchive"}
        result: dict[str, Any] = {}
        end_reached = False
        for _ in range(6):
            result = await client._request(
                {"@type": "getChats", "chat_list": chat_list, "limit": requested}
            )
            chat_ids = result.get("chat_ids") or []
            if len(chat_ids) >= requested:
                break
            try:
                await client._request(
                    {
                        "@type": "loadChats",
                        "chat_list": chat_list,
                        "limit": min(500, requested - len(chat_ids)),
                    }
                )
            except TelegramTdlibRequestError as exc:
                if exc.code != 404:
                    raise
                end_reached = True
                break
        chat_ids = result.get("chat_ids") or []
        if len(chat_ids) <= offset and not end_reached:
            raise ActionConnectorError(
                "Telegram is still loading this chat page; retry the same cursor",
                output_json={
                    "status": "retry",
                    "chat_list": payload.chat_list,
                    "cursor": payload.cursor,
                    "retry_safe": True,
                },
            )
        page_ids = chat_ids[offset : offset + payload.limit]
        chats: list[dict[str, Any]] = []
        for chat_id in page_ids:
            chat = await client._request({"@type": "getChat", "chat_id": chat_id})
            chats.append(_chat_summary(chat))
        next_offset = offset + len(page_ids)
        scan_limit_reached = next_offset >= 5000
        next_cursor = (
            f"{payload.chat_list}:{next_offset}"
            if page_ids
            and not scan_limit_reached
            and (next_offset < len(chat_ids) or not end_reached)
            else None
        )
        return {
            "chat_list": payload.chat_list,
            "chats": chats,
            "next_cursor": next_cursor,
            "total_count": result.get("total_count"),
            "end_reached": end_reached and next_cursor is None,
            "scan_limit_reached": scan_limit_reached,
            "ordering": "current_tdlib_chat_list_snapshot",
        }

    async def _history(
        self, client: _AccountClient, chat_id: int, payload: TelegramMessageHistory
    ) -> dict[str, Any]:
        # getChatHistory anchors inclusively and may return a short page before
        # the end. Re-request the anchor, then drop it for an exclusive cursor.
        # https://github.com/tdlib/td/blob/d1085f9cebc5a62379991ae1652673954f229c1f/td/generate/scheme/td_api.tl
        anchor = payload.before_message_id
        result = await client._request(
            {
                "@type": "getChatHistory",
                "chat_id": chat_id,
                "from_message_id": anchor or 0,
                "offset": 0,
                "limit": payload.limit + (1 if anchor else 0),
                "only_local": False,
            }
        )
        messages = _history_messages(result, before_message_id=anchor, limit=payload.limit)
        probe_count = 0
        if anchor is not None and anchor > 1 and not messages:
            # A short native page can contain only the inclusive anchor while
            # older messages still exist. Seek just below it before reporting
            # that no older message was returned.
            for _ in range(2):
                probe_count += 1
                probe = await client._request(
                    {
                        "@type": "getChatHistory",
                        "chat_id": chat_id,
                        "from_message_id": anchor - 1,
                        "offset": 0,
                        "limit": payload.limit,
                        "only_local": False,
                    }
                )
                messages = _history_messages(probe, before_message_id=anchor, limit=payload.limit)
                if messages:
                    break
        pagination_inconclusive = not messages and (
            anchor is not None or (result.get("total_count") or 0) > 0
        )
        return {
            "surface_ref": f"telegram-chat:{chat_id}",
            "messages": [
                _message_summary(
                    message,
                    chat_id,
                    client.account_ref,
                    include_content=payload.include_content,
                )
                for message in messages
            ],
            "next_before_message_id": messages[-1]["id"] if messages else None,
            "probe_count": probe_count,
            "pagination_inconclusive": pagination_inconclusive,
            "next_action": (
                "No older message was returned after bounded TDLib probes; stop this traversal "
                "and retry later if older history is needed."
                if pagination_inconclusive and probe_count
                else "TDLib returned no messages; retry later if history is expected."
                if pagination_inconclusive
                else None
            ),
            "total_count": result.get("total_count"),
            "ordering": "newest_first",
        }

    async def _mutation_query(
        self, client: _AccountClient, request: ActionConnectorRequest, payload: Any, chat_id: int
    ) -> dict[str, Any]:
        operation = request.operation
        if operation in _SEND_OPERATIONS:
            sending_id = getattr(request, "delivery_item_id", None)
            if not isinstance(sending_id, int) or not 0 < sending_id <= 2**31 - 1:
                raise ValidationError(
                    "Telegram delivery sequence exceeds the native sending_id range"
                )
            query = {
                "chat_id": chat_id,
                "topic_id": topic(payload.topic),
                "options": send_options(payload.options, sending_id=sending_id),
            }
            if isinstance(payload, TelegramMessageForward):
                return {
                    "@type": "forwardMessages",
                    **query,
                    "from_chat_id": _chat_id(payload.from_surface_ref),
                    "message_ids": payload.message_ids,
                    "send_copy": payload.send_copy,
                    "remove_caption": payload.remove_caption,
                }
            query["reply_to"] = (
                {"@type": "inputMessageReplyToMessage", "message_id": payload.reply_to_message_id}
                if payload.reply_to_message_id
                else None
            )
            if isinstance(payload, TelegramAlbumSend):
                return {
                    "@type": "sendMessageAlbum",
                    **query,
                    "input_message_contents": [
                        await message_content(client, content, asset_dir=request.asset_dir)
                        for content in payload.contents
                    ],
                }
            return {
                "@type": "sendMessage",
                **query,
                "reply_markup": reply_markup(payload.buttons),
                "input_message_content": await message_content(
                    client, payload.content, asset_dir=request.asset_dir
                ),
            }
        base: dict[str, Any] = {"chat_id": chat_id}
        if operation == "message.delete":
            return {
                "@type": "deleteMessages",
                **base,
                "message_ids": payload.message_ids,
                "revoke": payload.revoke,
            }
        base["message_id"] = payload.message_id
        if operation == "message.react":
            return {
                "@type": "setMessageReactions",
                **base,
                "reaction_types": [
                    {"@type": "reactionTypeEmoji", "emoji": emoji} for emoji in payload.emojis
                ]
                + [
                    {"@type": "reactionTypeCustomEmoji", "custom_emoji_id": str(value)}
                    for value in payload.custom_emoji_ids
                ],
                "is_big": payload.is_big,
            }
        if operation == "poll.stop":
            return {"@type": "stopPoll", **base}
        assert isinstance(payload, TelegramMessageEdit)
        markup = reply_markup(payload.buttons or [])
        if payload.buttons is None and payload.kind != "buttons":
            prior = await client._request({"@type": "getMessage", **base})
            markup = prior.get("reply_markup")
        base["reply_markup"] = markup
        if payload.kind in {"text", "media"}:
            assert payload.content is not None
            return {
                "@type": "editMessageText" if payload.kind == "text" else "editMessageMedia",
                **base,
                "input_message_content": await message_content(
                    client, payload.content, asset_dir=request.asset_dir
                ),
            }
        if payload.kind == "caption":
            return {
                "@type": "editMessageCaption",
                **base,
                "caption": await formatted_text(client, payload.caption or "", payload.format),
                "show_caption_above_media": payload.show_caption_above_media,
            }
        if payload.kind == "buttons":
            return {"@type": "editMessageReplyMarkup", **base}
        location = None
        if payload.location is not None:
            content = await message_content(client, payload.location, asset_dir=request.asset_dir)
            if content["@type"] != "inputMessageLiveLocation":
                raise ValidationError("Live location edits require a nonzero live_period")
            location = content["location"]
        return {"@type": "editMessageLiveLocation", **base, "location": location}

    async def _send_receipt(
        self,
        client: _AccountClient,
        request: ActionConnectorRequest,
        chat_id: int,
        result: dict[str, Any],
        *,
        expected_sender_ref: str | None = None,
        sender_selection_changed: bool = False,
    ) -> dict[str, Any]:
        messages = result.get("messages", []) if result.get("@type") == "messages" else [result]
        temporary_ids = [
            message["id"] for message in messages if message and message.get("sending_state")
        ]
        progress: dict[str, Any] = {
            "phase": "provider_accepted",
            "chat_id": chat_id,
            "temporary_message_ids": temporary_ids,
            "confirmed_message_refs": [],
            "provider_receipts": {},
        }

        def report_progress() -> None:
            if request.progress_callback:
                request.progress_callback(progress)

        report_progress()
        final_messages: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for message in messages:
            if message is None:
                failures.append({"reason": "message_not_forwardable"})
                continue
            temporary_message_id: int | None = None
            if message.get("sending_state"):
                temporary_message_id = message["id"]
                update = await client.runtime.wait_message(
                    client.account_ref, chat_id, message["id"], timeout_seconds=60.0
                )
                if update.get("@type") == "updateMessageSendFailed":
                    failed_state = (update.get("message") or {}).get("sending_state") or {}
                    error = update.get("error") or failed_state.get("error") or {}
                    error_code = error.get("code")
                    error_code = (
                        error_code
                        if isinstance(error_code, int) and not isinstance(error_code, bool)
                        else None
                    )
                    error_name, parsed_retry_after = safe_error_metadata(error.get("message"))
                    state_retry_after = failed_state.get("retry_after")
                    retry_after: int | float | None
                    if (
                        isinstance(state_retry_after, (int, float))
                        and not isinstance(state_retry_after, bool)
                        and math.isfinite(state_retry_after)
                        and 0 < state_retry_after <= 7 * 24 * 60 * 60
                    ):
                        retry_after = max(parsed_retry_after or 0, state_retry_after)
                    else:
                        retry_after = parsed_retry_after
                    failure = {
                        "temporary_message_id": message["id"],
                        "error_code": error_code,
                        "provider_error": error_name,
                        "retry_after_seconds": retry_after,
                        "tdlib_can_retry": failed_state.get("can_retry") is True,
                        "tdlib_need_another_sender": (
                            failed_state.get("need_another_sender") is True
                        ),
                    }
                    failures.append(failure)
                    progress["provider_receipts"][str(temporary_message_id)] = {
                        "status": "failed",
                        "error_code": error_code,
                        "provider_error": error_name,
                        "retry_after_seconds": retry_after,
                        "tdlib_can_retry": failure["tdlib_can_retry"],
                    }
                    report_progress()
                    continue
                message = update["message"]
            final_messages.append(message)
            message_ref = f"telegram-message:{chat_id}:{message['id']}"
            actual_sender_ref = _sender_ref(message.get("sender_id"))
            if expected_sender_ref is not None and actual_sender_ref != expected_sender_ref:
                failures.append(
                    {
                        "reason": "unexpected_sender",
                        "message_ref": message_ref,
                        "expected_sender_ref": expected_sender_ref,
                        "actual_sender_ref": actual_sender_ref,
                    }
                )
            progress["confirmed_message_refs"].append(message_ref)
            if temporary_message_id is not None:
                progress["provider_receipts"][str(temporary_message_id)] = {
                    "status": "sent",
                    "message_ref": message_ref,
                }
            store_sent_messages(request, [message])
            report_progress()
        refs = [f"telegram-message:{chat_id}:{message['id']}" for message in final_messages]
        output: dict[str, Any] = {
            "status": "partial" if failures and refs else "failed" if failures else "sent",
            "surface_ref": f"telegram-chat:{chat_id}",
            "sender_ref": expected_sender_ref,
            "sender_selection_changed": sender_selection_changed,
            "message_refs": refs,
            "message_ref": refs[0] if len(refs) == 1 else None,
            "temporary_message_ids": temporary_ids,
            "failures": failures,
            "messages": [
                _safe_native_with_file_refs(message, client.account_ref)
                for message in final_messages
            ],
            "provider_executed": True,
            "retry_safe": False,
        }
        if failures:
            provider_failures = [failure for failure in failures if "error_code" in failure]
            first_code = next(
                (
                    failure["error_code"]
                    for failure in provider_failures
                    if failure["error_code"] is not None
                ),
                None,
            )
            first_error = next(
                (
                    failure["provider_error"]
                    for failure in provider_failures
                    if failure["provider_error"] is not None
                ),
                None,
            )
            output.update(
                {
                    "provider_status_code": first_code,
                    "provider_error": first_error,
                    "retry_after_seconds": max(
                        (failure["retry_after_seconds"] or 0 for failure in provider_failures),
                        default=0,
                    )
                    or None,
                    "retry_scope": _retry_scope(first_error),
                    "tdlib_can_retry": any(
                        failure["tdlib_can_retry"] for failure in provider_failures
                    ),
                    "account_restricted": any(
                        failure["provider_error"] == "PEER_FLOOD" for failure in provider_failures
                    ),
                }
            )
            raise ActionConnectorError(
                "Telegram could not complete every message", output_json=output
            )
        return output

    async def _download(
        self, client: _AccountClient, request: ActionConnectorRequest, file_id: int
    ) -> dict[str, Any]:
        # Native download completion is explicit; publishing a retained artifact is
        # handled at the existing generated media owner, never by returning a DB path.
        try:
            file = await client._request(
                {
                    "@type": "downloadFile",
                    "file_id": file_id,
                    "priority": 16,
                    "offset": 0,
                    "limit": 0,
                    "synchronous": True,
                }
            )
        except TelegramTdlibNativeError as exc:
            if str(exc) != "TDLib request timed out awaiting a response.":
                raise
            raise ActionConnectorError(
                "Telegram file download timed out before TDLib returned a result",
                output_json={
                    "status": "retryable_timeout",
                    "file_ref": telegram_file_ref(
                        credential_ref=client.account_ref, file_id=file_id
                    ),
                    "next_action": request.action_ref,
                    "retry_safe": True,
                    "provider_result_known": False,
                },
            ) from exc
        if not (file.get("local") or {}).get("is_downloading_completed"):
            raise ActionConnectorError("Telegram file download did not complete")
        if request.asset_dir is None or request.session is None:
            raise ValidationError("Telegram file downloads require generated asset storage")
        session = self._session(request)
        source = Path(str((file.get("local") or {}).get("path") or "")).resolve()
        owned_root = client.runtime.files_directory(client.account_ref).resolve()
        if not source.is_relative_to(owned_root) or not source.is_file():
            raise ActionConnectorError("Telegram returned a file outside this Account's storage")
        suffix = source.suffix if re.fullmatch(r"\.[a-zA-Z0-9]{1,10}", source.suffix) else ".bin"
        relative = Path("telegram") / str(request.project_id) / (uuid.uuid4().hex + suffix)
        destination = request.asset_dir.resolve() / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.resolve().is_relative_to(request.asset_dir.resolve()):
            raise ValidationError("Telegram artifact storage path is outside generated assets")
        shutil.copyfile(source, destination)
        uri = "/generated-assets/" + relative.as_posix()
        artifact = (
            ArtifactRepository(session)
            .create(
                project_id=request.project_id,
                plugin_slug="communications",
                kind="file",
                uri=uri,
                name=destination.name,
                mime_type=mimetypes.guess_type(destination.name)[0] or "application/octet-stream",
                size_bytes=destination.stat().st_size,
                metadata_json={
                    "provider_key": "telegram",
                    "file_ref": telegram_file_ref(
                        credential_ref=client.account_ref, file_id=file_id
                    ),
                },
                provenance_json={
                    "source": "telegram.file.download",
                    "action_ref": request.action_ref,
                },
            )
            .data
        )
        return {
            "status": "downloaded",
            "file_ref": telegram_file_ref(credential_ref=client.account_ref, file_id=file_id),
            "file": _safe_downloaded_file(file),
            "artifact_ref": uri,
            "artifact_id": artifact.id,
            "artifact_refs": [uri],
        }


def _retry_scope(error_name: str | None) -> str | None:
    if error_name == "SLOWMODE_WAIT":
        return "destination"
    if error_name == "FLOOD_WAIT":
        return "account"
    return None


def _canonical_broadcast_destination(recipient_ref: str) -> str:
    # TDLib dialog IDs for private users equal the user ID. Canonicalize the
    # durable admission key without any provider lookup, so user/chat aliases
    # share destination pacing. https://core.telegram.org/api/bots/ids
    if recipient_ref.startswith("telegram-user:"):
        user_id = int(recipient_ref.removeprefix("telegram-user:"))
        return f"telegram-chat:{user_id}"
    chat_id = _chat_id(recipient_ref)
    if _SECRET_CHAT_MIN <= chat_id <= _SECRET_CHAT_MAX:
        raise ValidationError("Secret chats are not enabled for StackOS Telegram Accounts")
    return recipient_ref


def _chat_id(surface_ref: str) -> int:
    match = _CHAT_REF.fullmatch(surface_ref)
    if match is None or abs(int(match[1])) >= 2**53:
        raise ValidationError("Use an Account-known TDLib telegram-chat:<native-chat-id> surface")
    return int(match[1])


def _sender_ref(sender: Any) -> str | None:
    if not isinstance(sender, dict):
        return None
    kind = sender.get("@type")
    if kind == "messageSenderUser":
        value = sender.get("user_id")
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2**53:
            return f"telegram-user:{value}"
    if kind == "messageSenderChat":
        value = sender.get("chat_id")
        if isinstance(value, int) and not isinstance(value, bool) and 0 < abs(value) < 2**53:
            return f"telegram-chat:{value}"
    return None


def _native_sender(sender_ref: str) -> dict[str, Any]:
    if sender_ref.startswith("telegram-user:"):
        value = sender_ref.removeprefix("telegram-user:")
        if value.isdigit() and 0 < int(value) < 2**53:
            return {"@type": "messageSenderUser", "user_id": int(value)}
    if sender_ref.startswith("telegram-chat:"):
        value = sender_ref.removeprefix("telegram-chat:")
        if value.lstrip("-").isdigit() and 0 < abs(int(value)) < 2**53:
            return {"@type": "messageSenderChat", "chat_id": int(value)}
    raise ValidationError("Use an available Telegram sender_ref from chat.sender.list")


def _chat_summary(chat: dict[str, Any], *, account_can_page_history: bool = True) -> dict[str, Any]:
    chat_id = chat["id"]
    chat_type = chat.get("type") or {}
    native_type = chat_type.get("@type")
    kind = {
        "chatTypePrivate": "private",
        "chatTypeBasicGroup": "group",
        "chatTypeSecret": "secret",
    }.get(
        native_type if isinstance(native_type, str) else "",
        "channel" if chat_type.get("is_channel") else "supergroup",
    )
    last = chat.get("last_message") or {}
    last_id = last.get("id")
    title = str(chat.get("title") or "")
    return {
        "surface_ref": f"telegram-chat:{chat_id}",
        "title": title[:256],
        "title_truncated": len(title) > 256,
        "kind": kind,
        "history_supported": account_can_page_history and kind != "secret",
        "unread_count": chat.get("unread_count") or 0,
        "is_marked_as_unread": bool(chat.get("is_marked_as_unread")),
        "last_message_ref": (
            f"telegram-message:{chat_id}:{last_id}"
            if isinstance(last_id, int) and last_id > 0
            else None
        ),
        "last_message_date": last.get("date"),
    }


def _history_messages(
    result: dict[str, Any], *, before_message_id: int | None, limit: int
) -> list[dict[str, Any]]:
    return [
        message
        for message in result.get("messages") or []
        if isinstance(message, dict)
        and isinstance(message.get("id"), int)
        and message["id"] > 0
        and (before_message_id is None or message["id"] < before_message_id)
    ][:limit]


def _message_summary(
    message: dict[str, Any],
    chat_id: int,
    credential_ref: str,
    *,
    include_content: bool,
) -> dict[str, Any]:
    message_id = message["id"]
    content = message.get("content") or {}
    sender = message.get("sender_id") or {}
    sender_type = sender.get("@type")
    sender_id = (
        sender.get("user_id") if sender_type == "messageSenderUser" else sender.get("chat_id")
    )
    sender_ref = (
        f"telegram-user:{sender_id}"
        if sender_type == "messageSenderUser" and isinstance(sender_id, int) and sender_id > 0
        else f"telegram-chat:{sender_id}"
        if sender_type == "messageSenderChat" and isinstance(sender_id, int) and sender_id != 0
        else None
    )
    formatted = content.get("text") or content.get("caption") or {}
    text = formatted.get("text") if isinstance(formatted, dict) else formatted
    text = text if isinstance(text, str) else ""
    reply_to = message.get("reply_to") or {}
    reply_id = reply_to.get("message_id")
    result: dict[str, Any] = {
        "message_ref": f"telegram-message:{chat_id}:{message_id}",
        "message_id": message_id,
        "date": message.get("date"),
        "edit_date": message.get("edit_date") or None,
        "sender_ref": sender_ref,
        "is_outgoing": bool(message.get("is_outgoing")),
        "is_channel_post": bool(message.get("is_channel_post")),
        "content_type": content.get("@type"),
        "text_preview": text[:160],
        "text_truncated": len(text) > (8192 if include_content else 160),
        "file_refs": _content_file_refs(content, credential_ref),
        "reply_to_message_ref": (
            f"telegram-message:{chat_id}:{reply_id}"
            if isinstance(reply_id, int) and reply_id > 0
            else None
        ),
    }
    if include_content:
        result["text"] = text[:8192]
    return result


def _content_file_refs(content: dict[str, Any], credential_ref: str) -> list[str]:
    """Return only account-qualified file refs, never TDLib paths or file metadata."""

    found: list[str] = []

    def visit(value: Any) -> None:
        if len(found) >= 4:
            return
        if isinstance(value, dict):
            if _is_embedded_photo_size(value):
                return
            if value.get("@type") == "file":
                file_id = value.get("id")
                if (
                    isinstance(file_id, int)
                    and not isinstance(file_id, bool)
                    and 0 < file_id < 2**31
                ):
                    ref = telegram_file_ref(credential_ref=credential_ref, file_id=file_id)
                    if ref not in found:
                        found.append(ref)
                return
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in reversed(value):
                visit(nested)

    visit(content)
    return found


def _is_embedded_photo_size(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("@type") == "photoSize"
        and value.get("type") in {"i", "j"}
    )


def _safe_native(value: Any) -> Any:
    if isinstance(value, list):
        return [_safe_native(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _safe_native(item)
            for key, item in value.items()
            if key not in {"@extra", "path", "downloaded_prefix_size"}
        }
    return value


def _safe_downloaded_file(value: dict[str, Any]) -> dict[str, Any]:
    """Keep safe download facts while withholding an unqualified native file ID."""

    safe = _safe_native(value)
    if not isinstance(safe, dict):  # pragma: no cover - TDLib file response contract
        return {}
    safe.pop("id", None)
    return safe


def _safe_native_with_file_refs(value: Any, credential_ref: str) -> Any:
    """Return safe native output with reusable files bound to their Account."""

    safe = _safe_native(value)

    def qualify(current: Any) -> Any:
        if isinstance(current, list):
            return [qualify(item) for item in current if not _is_embedded_photo_size(item)]
        if not isinstance(current, dict):
            return current
        result = {key: qualify(item) for key, item in current.items()}
        file_id = result.get("id")
        if (
            result.get("@type") == "file"
            and isinstance(file_id, int)
            and not isinstance(file_id, bool)
            and file_id > 0
        ):
            result.pop("id")
            result["file_ref"] = telegram_file_ref(credential_ref=credential_ref, file_id=file_id)
        return result

    return qualify(safe)
