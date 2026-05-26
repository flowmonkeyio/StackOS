"""Canonical action repository facade."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from stackos.actions.connectors import DEFAULT_ACTION_CONNECTORS, ActionConnectorRegistry

from .audit import ActionAuditMixin
from .catalog import ActionCatalogMixin
from .execution import ActionExecutionMixin
from .run_scope import ActionRunScopeMixin
from .validation import ActionValidationMixin


class ActionRepository(
    ActionCatalogMixin,
    ActionValidationMixin,
    ActionExecutionMixin,
    ActionAuditMixin,
    ActionRunScopeMixin,
):
    """Internal action manifest/executor service.

    The repository executes explicit action payloads only. It never chooses a
    provider, edits campaign strategy, decides workflow order, or returns
    sensitive material to callers.
    """

    def __init__(
        self,
        session: Session,
        *,
        connectors: ActionConnectorRegistry | None = None,
        asset_dir: Path | None = None,
    ) -> None:
        self._s = session
        self._connectors = connectors or DEFAULT_ACTION_CONNECTORS
        self._asset_dir = asset_dir
