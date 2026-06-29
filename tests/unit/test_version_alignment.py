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
    package_version = pyproject["project"]["version"]

    assert package_version == stackos.__version__
    assert package_version == desktop_package["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", package_version)
