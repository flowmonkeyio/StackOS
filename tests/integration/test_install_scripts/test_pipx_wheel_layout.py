"""Per audit P-G4: the wheel must bundle skills and plugins.

A pipx-mode install has no checked-out repo on disk, so
`stackos install` resolves assets via `importlib.resources` from
``stackos/_assets/skills/`` and ``stackos/_assets/plugins/``.
We verify the wheel produced by `python -m build` contains those paths
with the same skill counts as the canonical StackOS skill source.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel into a tmp dist dir; return the .whl path."""
    repo_root = Path(__file__).resolve().parents[3]
    dist_dir = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist_dir)],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"`python -m build` failed: {result.stderr}\n{result.stdout}")
    wheels = list(dist_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel, got {wheels}"
    yield wheels[0]
    shutil.rmtree(str(dist_dir), ignore_errors=True)


def _wheel_names(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as z:
        return z.namelist()


def test_wheel_includes_assets_skills(built_wheel: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    source = repo_root / "plugins" / "stackos" / "skills"
    expected = sum(1 for _ in source.rglob("SKILL.md"))
    names = _wheel_names(built_wheel)
    bundled = [
        n for n in names if n.startswith("stackos/_assets/skills/") and n.endswith("/SKILL.md")
    ]
    assert len(bundled) == expected, (
        f"wheel has {len(bundled)} SKILL.md files; source has {expected}"
    )
    assert "stackos/_assets/skills/stackos/SKILL.md" in names
    assert "stackos/_assets/skills/stackos-sdlc-delivery-orchestrator/SKILL.md" not in names


def test_wheel_includes_stackos_plugin(built_wheel: Path) -> None:
    names = _wheel_names(built_wheel)

    assert "stackos/_assets/plugins/stackos/.codex-plugin/plugin.json" in names
    assert "stackos/_assets/plugins/stackos/.claude-plugin/plugin.json" in names
    assert "stackos/_assets/plugins/stackos/.mcp.json" in names
    assert "stackos/_assets/plugins/engineering/skill-presets/sdlc.yaml" in names


def test_wheel_assets_path_namespace_is_under_stackos(built_wheel: Path) -> None:
    """Assets are namespaced under the package so `importlib.resources` resolves them."""
    names = _wheel_names(built_wheel)
    # All `_assets/...` entries live inside `stackos/`.
    stray = [n for n in names if "_assets/" in n and not n.startswith("stackos/_assets/")]
    assert stray == [], f"stray _assets entries outside stackos/: {stray}"


def test_wheel_no_duplicate_entries(built_wheel: Path) -> None:
    """Hatchling warns about dupes via `force-include`; the wheel must be clean."""
    names = _wheel_names(built_wheel)
    assert len(names) == len(set(names)), "wheel contains duplicate zip entries"


def test_wheel_excludes_unsupported_mcp_major(built_wheel: Path) -> None:
    """Wheel installs must not resolve against an untested MCP major."""
    with zipfile.ZipFile(built_wheel) as wheel:
        metadata_name = next(
            name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = Parser().parsestr(wheel.read(metadata_name).decode("utf-8"))

    mcp_requirements = [
        requirement
        for requirement in metadata.get_all("Requires-Dist", [])
        if requirement.split(";", 1)[0].strip().startswith("mcp")
    ]
    assert mcp_requirements == ["mcp<2,>=1.0"]


def test_wheel_includes_s3_runtime_ui_asset_and_sdk_requirements(
    built_wheel: Path,
) -> None:
    names = _wheel_names(built_wheel)

    assert "stackos/actions/s3.py" in names
    assert "stackos/integrations/s3.py" in names
    assert "stackos/plugins/builtin_utils_s3.py" in names
    assert "stackos/ui_dist/images/integrations/s3.png" in names

    with zipfile.ZipFile(built_wheel) as wheel:
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = wheel.read(metadata_name).decode("utf-8")
    for dependency in ("boto3", "botocore", "s3transfer"):
        assert f"Requires-Dist: {dependency}" in metadata
