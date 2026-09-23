"""UI generated API types must match the source FastAPI OpenAPI methods."""

from __future__ import annotations

import json
import re
import runpy
from pathlib import Path

import pytest

from stackos.actions.repository.durable import DurableActionItemOut, DurableActionJobOut
from stackos.config import Settings
from stackos.operations.actions.schemas import (
    ActionCallDurableItemsOut,
    ActionCallResumeInput,
    ActionCallRetryInput,
)
from stackos.server import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
API_TS = REPO_ROOT / "ui" / "src" / "api.ts"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _source_methods(tmp_path: Path) -> dict[str, set[str]]:
    settings = Settings(
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "state",
    )
    openapi = create_app(settings).openapi()
    return {
        path: {method for method in spec if method in HTTP_METHODS}
        for path, spec in openapi["paths"].items()
        if path.startswith("/api/v1")
    }


def _generated_methods() -> dict[str, set[str]]:
    text = API_TS.read_text(encoding="utf-8")
    matches = re.findall(r'"(/api/v1[^"]+)": \{([\s\S]*?)\n    \};', text)
    return {
        path: {method for method in HTTP_METHODS if re.search(rf"\n        {method}:", body)}
        for path, body in matches
    }


def test_generated_ui_api_methods_match_source_openapi(tmp_path: Path) -> None:
    """Fail when backend routes changed but ``ui/src/api.ts`` was not regenerated."""
    assert _generated_methods() == _source_methods(tmp_path)


def test_ui_generation_includes_registry_owned_durable_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dynamic operation calls still provide canonical generated UI contracts."""
    target = tmp_path / "openapi.json"
    monkeypatch.setenv("STACKOS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STACKOS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("sys.argv", ["write-openapi.py", str(target)])
    writer = runpy.run_path(str(REPO_ROOT / "scripts" / "write-openapi.py"))
    assert writer["main"]() == 0
    schemas = json.loads(target.read_text())["components"]["schemas"]
    generated = API_TS.read_text()
    for model in (
        ActionCallDurableItemsOut,
        DurableActionJobOut,
        DurableActionItemOut,
        ActionCallResumeInput,
        ActionCallRetryInput,
    ):
        assert schemas[model.__name__]["properties"].keys() == model.model_fields.keys()
        assert f"Schema{model.__name__}" in generated
    for reference in re.findall(r'"\$ref": "#/components/schemas/([^\"]+)"', target.read_text()):
        assert reference in schemas
