"""StackOS protocol-neutral operation registry."""

from stackos.operations.registry import OperationRegistry, build_operation_registry
from stackos.operations.spec import OperationSpec

__all__ = ["OperationRegistry", "OperationSpec", "build_operation_registry"]
