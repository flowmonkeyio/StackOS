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
    input_json: dict[str, Any]
    config_json: Mapping[str, Any]
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
    "ActionConnectorRegistry",
    "ActionConnectorRequest",
    "ActionConnectorResult",
    "ActionValidationIssue",
]
