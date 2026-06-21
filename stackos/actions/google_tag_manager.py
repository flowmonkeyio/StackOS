"""Google Tag Manager action connector."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from stackos.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.provider_utils import connector_error_from_integration
from stackos.actions.vendor_utils import (
    bool_field,
    credential_payload,
    optional_str,
    required_str,
    result,
    unknown_operation,
)
from stackos.integrations.google_tag_manager import GoogleTagManagerIntegration
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError


class GoogleTagManagerActionConnector:
    """Decision-free adapter for read-only Google Tag Manager inventory."""

    key = "google-tag-manager"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "accounts.list":
                bool_field(payload, "include_google_tags", issues)
                optional_str(payload, "page_cursor", issues)
            case "accounts.containers.list":
                required_str(payload, "account_ref", issues)
                optional_str(payload, "page_cursor", issues)
            case "accounts.containers.snippet":
                required_str(payload, "account_ref", issues)
                required_str(payload, "container_ref", issues)
            case "accounts.containers.workspaces.list":
                required_str(payload, "account_ref", issues)
                required_str(payload, "container_ref", issues)
                optional_str(payload, "page_cursor", issues)
            case "accounts.containers.workspaces.tags.list":
                self._workspace_refs(payload, issues)
                optional_str(payload, "page_cursor", issues)
            case "accounts.containers.workspaces.triggers.list":
                self._workspace_refs(payload, issues)
                optional_str(payload, "page_cursor", issues)
            case _:
                issues.extend(unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        payload = request.input_json
        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                client = GoogleTagManagerIntegration(
                    payload=credential_payload(request),
                    project_id=request.project_id,
                    http=http,
                )
                match request.operation:
                    case "accounts.list":
                        call_result = await client.accounts_list(
                            include_google_tags=(
                                bool(payload["include_google_tags"])
                                if payload.get("include_google_tags") is not None
                                else None
                            ),
                            page_token=str(payload["page_cursor"])
                            if payload.get("page_cursor")
                            else None,
                        )
                    case "accounts.containers.list":
                        call_result = await client.containers_list(
                            account_path=self._account_path(request, str(payload["account_ref"])),
                            page_token=str(payload["page_cursor"])
                            if payload.get("page_cursor")
                            else None,
                        )
                    case "accounts.containers.snippet":
                        call_result = await client.container_snippet(
                            container_path=self._container_path(
                                request,
                                str(payload["account_ref"]),
                                str(payload["container_ref"]),
                            )
                        )
                    case "accounts.containers.workspaces.list":
                        call_result = await client.workspaces_list(
                            container_path=self._container_path(
                                request,
                                str(payload["account_ref"]),
                                str(payload["container_ref"]),
                            ),
                            page_token=str(payload["page_cursor"])
                            if payload.get("page_cursor")
                            else None,
                        )
                    case "accounts.containers.workspaces.tags.list":
                        call_result = await client.workspace_tags_list(
                            workspace_path=self._workspace_path(request, payload),
                            page_token=str(payload["page_cursor"])
                            if payload.get("page_cursor")
                            else None,
                        )
                    case "accounts.containers.workspaces.triggers.list":
                        call_result = await client.workspace_triggers_list(
                            workspace_path=self._workspace_path(request, payload),
                            page_token=str(payload["page_cursor"])
                            if payload.get("page_cursor")
                            else None,
                        )
                    case _:
                        raise ValidationError(
                            f"unsupported Google Tag Manager operation {request.operation!r}"
                        )
        except (IntegrationDownError, RateLimitedError) as exc:
            raise connector_error_from_integration(
                exc,
                provider=self.key,
                operation=request.operation,
            ) from exc
        return self._result(request.operation, call_result.data, call_result.cost_usd)

    def _result(self, operation: str, data: Any, cost_usd: float) -> ActionConnectorResult:
        output = data if isinstance(data, dict) else {"data": data}
        if isinstance(output, dict):
            output = dict(output)
            next_page = output.pop("nextPageToken", None)
            if isinstance(next_page, str) and next_page:
                output["next_page_cursor"] = next_page
        return result(self.key, operation, output, cost_usd)

    @staticmethod
    def _workspace_refs(
        payload: dict[str, Any],
        issues: list[ActionValidationIssue],
    ) -> None:
        required_str(payload, "account_ref", issues)
        required_str(payload, "container_ref", issues)
        required_str(payload, "workspace_ref", issues)

    @classmethod
    def _account_path(cls, request: ActionConnectorRequest, value: str) -> str:
        resolved = cls._resolve_ref(
            request, value, "account_refs", default_key="default_account_ref"
        )
        if resolved.startswith("accounts/"):
            parts = resolved.split("/")
            if len(parts) == 2 and parts[1]:
                return f"accounts/{quote(parts[1], safe='')}"
            raise ValidationError("google-tag-manager account_ref must be accounts/{account}")
        if "/" in resolved:
            raise ValidationError("google-tag-manager account_ref must be an account id")
        return f"accounts/{quote(resolved, safe='')}"

    @classmethod
    def _container_path(
        cls,
        request: ActionConnectorRequest,
        account_ref: str,
        container_ref: str,
    ) -> str:
        account_path = cls._account_path(request, account_ref)
        resolved = cls._resolve_ref(
            request,
            container_ref,
            "container_refs",
            default_key="default_container_ref",
        )
        if resolved.startswith("accounts/"):
            parts = resolved.split("/")
            if len(parts) == 4 and parts[0] == "accounts" and parts[2] == "containers":
                return f"accounts/{quote(parts[1], safe='')}/containers/{quote(parts[3], safe='')}"
            raise ValidationError(
                "google-tag-manager container_ref must be accounts/{account}/containers/{container}"
            )
        if resolved.startswith("containers/"):
            parts = resolved.split("/")
            if len(parts) == 2 and parts[1]:
                return f"{account_path}/containers/{quote(parts[1], safe='')}"
            raise ValidationError("google-tag-manager container_ref must be containers/{container}")
        if "/" in resolved:
            raise ValidationError("google-tag-manager container_ref must be a container id")
        return f"{account_path}/containers/{quote(resolved, safe='')}"

    @classmethod
    def _workspace_path(cls, request: ActionConnectorRequest, payload: dict[str, Any]) -> str:
        container_path = cls._container_path(
            request,
            str(payload["account_ref"]),
            str(payload["container_ref"]),
        )
        resolved = cls._resolve_ref(
            request,
            str(payload["workspace_ref"]),
            "workspace_refs",
            default_key="default_workspace_ref",
        )
        if resolved.startswith("accounts/"):
            parts = resolved.split("/")
            if (
                len(parts) == 6
                and parts[0] == "accounts"
                and parts[2] == "containers"
                and parts[4] == "workspaces"
            ):
                return (
                    f"accounts/{quote(parts[1], safe='')}/containers/"
                    f"{quote(parts[3], safe='')}/workspaces/{quote(parts[5], safe='')}"
                )
            raise ValidationError(
                "google-tag-manager workspace_ref must be "
                "accounts/{account}/containers/{container}/workspaces/{workspace}"
            )
        if resolved.startswith("workspaces/"):
            parts = resolved.split("/")
            if len(parts) == 2 and parts[1]:
                return f"{container_path}/workspaces/{quote(parts[1], safe='')}"
            raise ValidationError("google-tag-manager workspace_ref must be workspaces/{workspace}")
        if "/" in resolved:
            raise ValidationError("google-tag-manager workspace_ref must be a workspace id")
        return f"{container_path}/workspaces/{quote(resolved, safe='')}"

    @staticmethod
    def _resolve_ref(
        request: ActionConnectorRequest,
        value: str,
        map_key: str,
        *,
        default_key: str,
    ) -> str:
        config = request.credential.config_json if request.credential is not None else None
        if isinstance(config, dict):
            default_ref = config.get(default_key)
            if value in {"default", "main"} and isinstance(default_ref, str) and default_ref:
                return default_ref
            mapping = config.get(map_key)
            if isinstance(mapping, dict):
                mapped = mapping.get(value)
                if isinstance(mapped, str) and mapped:
                    return mapped
        if not value:
            raise ValidationError("google-tag-manager ref cannot be empty")
        return value


__all__ = ["GoogleTagManagerActionConnector"]
