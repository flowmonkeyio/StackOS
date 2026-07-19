"""Connector adapter contract for StackOS generic actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from stackos.auth_providers import ResolvedCredential
from stackos.repositories.base import NotFoundError


class ActionValidationIssue(BaseModel):
    """Machine-readable validation issue for an action payload."""

    path: str
    message: str
    code: str = "validation_error"


@dataclass(frozen=True)
class ActionConnectorRequest:
    """In-process request sent to a connector adapter.

    ``credential`` may contain decrypted secret material. It is deliberately kept as a
    dataclass field instead of JSON so it cannot accidentally become an agent
    response through Pydantic serialization.
    """

    project_id: int
    plugin_slug: str
    action_key: str
    action_ref: str
    provider_key: str | None
    operation: str
    input_json: dict[str, Any] = field(repr=False)
    config_json: Mapping[str, Any]
    provider_context_json: dict[str, Any] = field(default_factory=dict)
    credential: ResolvedCredential | None = field(default=None, repr=False)
    asset_dir: Path | None = None
    session: Any | None = field(default=None, repr=False)
    dry_run: bool = False


class ActionConnectorResult(BaseModel):
    """Connector return value before StackOS redaction/audit wrapping."""

    model_config = ConfigDict(extra="forbid")

    output_json: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] | None = None
    cost_cents: int = 0


class ActionConnectorError(Exception):
    """Connector failure with structured, redaction-ready provider output.

    Connectors should raise this when a provider returned a real response that
    agents can use for diagnosis, such as HTTP 429/403/500 bodies. The executor
    records ``output_json`` on the failed action call and surfaces the same
    redacted provider fields in the operation error envelope.
    """

    def __init__(
        self,
        detail: str,
        *,
        provider_status_code: int | None = None,
        provider_error: Any | None = None,
        output_json: dict[str, Any] | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.provider_status_code = provider_status_code
        self.provider_error = provider_error
        self.output_json = output_json or _provider_failure_output(
            provider_status_code=provider_status_code,
            provider_error=provider_error,
        )
        self.metadata_json = metadata_json or {}


def _provider_failure_output(
    *,
    provider_status_code: int | None,
    provider_error: Any | None,
) -> dict[str, Any]:
    output: dict[str, Any] = {"status": "failed"}
    if provider_status_code is not None:
        output["provider_status_code"] = provider_status_code
    if provider_error is not None:
        output["provider_error"] = provider_error
    return output


class ActionConnector(Protocol):
    """Small, decision-free adapter API used by the generic executor."""

    key: str

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        """Return connector-specific payload issues without making provider calls."""
        ...

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        """Return the best pre-call cost estimate in cents."""
        ...

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        """Perform the requested operation with the already-resolved credential."""
        ...


class ActionConnectorRegistry:
    """In-memory connector registry owned by the daemon process."""

    def __init__(self) -> None:
        self._connectors: dict[str, ActionConnector] = {}

    def register(self, connector: ActionConnector) -> None:
        if not connector.key:
            raise ValueError("connector key is required")
        self._connectors[connector.key] = connector

    def get(self, key: str) -> ActionConnector:
        connector = self._connectors.get(key)
        if connector is None:
            raise NotFoundError(f"action connector {key!r} is not registered")
        return connector

    def list_keys(self) -> list[str]:
        return sorted(self._connectors)


DEFAULT_ACTION_CONNECTORS = ActionConnectorRegistry()


__all__ = [
    "DEFAULT_ACTION_CONNECTORS",
    "ActionConnector",
    "ActionConnectorError",
    "ActionConnectorRegistry",
    "ActionConnectorRequest",
    "ActionConnectorResult",
    "ActionValidationIssue",
]
