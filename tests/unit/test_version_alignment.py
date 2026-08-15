from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import stackos

ROOT = Path(__file__).resolve().parents[2]


def test_release_version_sources_stay_aligned() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    desktop_package = json.loads((ROOT / "desktop" / "package.json").read_text())
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text())
    package_version = pyproject["project"]["version"]
    editable_stackos = [
        package
        for package in uv_lock["package"]
        if package["name"] == "stackos"
        and package.get("source", {}).get("editable") == "."
    ]

    assert len(editable_stackos) == 1
    assert package_version == stackos.__version__
    assert package_version == desktop_package["version"]
    assert package_version == editable_stackos[0]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", package_version)
