"""StackOS protocol-neutral operation registry."""

from content_stack.operations.registry import OperationRegistry, build_operation_registry
from content_stack.operations.spec import OperationSpec

__all__ = ["OperationRegistry", "OperationSpec", "build_operation_registry"]
