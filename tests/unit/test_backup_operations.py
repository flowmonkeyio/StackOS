"""Backup operation contracts keep local filesystem authority out of MCP."""

import pytest

from stackos.operations.registry import build_operation_registry
from stackos.repositories.base import NotFoundError


@pytest.mark.parametrize("name", ["backup.status", "backup.configure", "backup.create"])
def test_backup_surfaces_are_explicit_local_operator_only(name):
    registry = build_operation_registry()
    spec = registry.get(name)
    assert spec.surfaces.rest.enabled and spec.surfaces.rest.browser_safe
    assert spec.surfaces.cli.enabled
    assert spec.surfaces.rest.path == f"/api/v1/operations/{name}/call"
    assert spec.grant_policy == (
        "local-admin-backup-read" if spec.read_only else "local-admin-backup-write"
    )
    assert spec.effective_response_policy.allowed_modes == ("raw",)
    with pytest.raises(NotFoundError):
        registry.get(name, surface="mcp")
    fields = spec.input_model.model_fields
    assert "private_key" not in fields
    assert "private_key_file" not in fields
    assert "exclusion_paths" not in fields
