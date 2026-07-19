"""StackOS action operation registrations."""

from __future__ import annotations

from .discovery import action_describe, action_list, action_validate
from .execution import (
    _approval_ref_for_action as _approval_ref_for_action,
)
from .execution import (
    _compact_action_output as _compact_action_output,
)
from .execution import (
    action_execute,
    action_run,
)
from .schemas import (
    ActionDescribeInput,
    ActionExecuteInput,
    ActionListInput,
    ActionListItemOut,
    ActionListOut,
    ActionRunInput,
    ActionRunOut,
    ActionValidateInput,
)
from .specs import operation_specs

__all__ = [
    "ActionDescribeInput",
    "ActionExecuteInput",
    "ActionListInput",
    "ActionListItemOut",
    "ActionListOut",
    "ActionRunInput",
    "ActionRunOut",
    "ActionValidateInput",
    "action_describe",
    "action_execute",
    "action_list",
    "action_run",
    "action_validate",
    "operation_specs",
]
