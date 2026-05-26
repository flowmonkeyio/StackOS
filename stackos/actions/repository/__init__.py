"""Compatibility surface for the action repository package."""

from __future__ import annotations

from stackos.action_availability import ActionAvailabilityOut

from .repository import ActionRepository
from .schema import (
    ActionCallAuditOut,
    ActionCallOut,
    ActionDescribeOut,
    ActionExecutionOut,
    ActionValidationOut,
)
from .utils import _redact_for_audit

__all__ = [
    "ActionAvailabilityOut",
    "ActionCallAuditOut",
    "ActionCallOut",
    "ActionDescribeOut",
    "ActionExecutionOut",
    "ActionRepository",
    "ActionValidationOut",
    "_redact_for_audit",
]
