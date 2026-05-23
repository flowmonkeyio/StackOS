"""UI generated API types must match the source FastAPI OpenAPI methods."""

from __future__ import annotations

import re
from pathlib import Path

from stackos.config import Settings
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
