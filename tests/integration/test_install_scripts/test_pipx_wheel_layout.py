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
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


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


def test_wheel_includes_finance_plugin_assets(built_wheel: Path) -> None:
    """The external finance workflow package must survive a pipx install."""
    names = _wheel_names(built_wheel)
    finance_root = "stackos/_assets/plugins/finance/"
    expected = {
        "plugin.yaml",
        "agent-presets/finance.yaml",
        "skill-presets/finance.yaml",
        "templates/finance-workspace/FINANCE.md",
        "templates/finance-workspace/finance.json",
        "templates/finance-workspace/schemas/finance-v1.schema.json",
        "workflows/receipt-intake.yaml",
        "workflows/bookkeeping-close.yaml",
        "workflows/payment-request.yaml",
        "workflows/payment-request-followups.yaml",
        "workflows/cashflow-management.yaml",
        "workflows/tax-estimates.yaml",
        "workflows/management-review.yaml",
        "scripts/finance_workspace.py",
        "scripts/finance_records.py",
        "scripts/finance_views.py",
        "scripts/finance_management.py",
        "schemas/management-v1.schema.json",
        "schemas/management-result-v1.schema.json",
        "references/approval-matrix.md",
        "references/backend-contract.md",
        "references/imap-host-handoff-contract.md",
        "references/local-workspace-contract.md",
        "references/workflow-handoffs.md",
    }

    assert {
        name.removeprefix(finance_root) for name in names if name.startswith(finance_root)
    } >= expected


def test_wheel_assets_path_namespace_is_under_stackos(built_wheel: Path) -> None:
    """Assets are namespaced under the package so `importlib.resources` resolves them."""
    names = _wheel_names(built_wheel)
    # All `_assets/...` entries live inside `stackos/`.
    stray = [n for n in names if "_assets/" in n and not n.startswith("stackos/_assets/")]
    assert stray == [], f"stray _assets entries outside stackos/: {stray}"


def test_wheel_management_origin_read_query_and_guarded_edit(
    built_wheel: Path, tmp_path: Path
) -> None:
    from tests.unit.test_finance_management_package import run_packaged_smoke

    package = tmp_path / "installed"
    with zipfile.ZipFile(built_wheel) as wheel:
        wheel.extractall(package)
    run_packaged_smoke(package, tmp_path)


def test_wheel_no_duplicate_entries(built_wheel: Path) -> None:
    """Hatchling warns about dupes via `force-include`; the wheel must be clean."""
    names = _wheel_names(built_wheel)
    assert len(names) == len(set(names)), "wheel contains duplicate zip entries"


def test_wheel_bounds_supported_mcp_major(built_wheel: Path) -> None:
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
    assert mcp_requirements == ["mcp<3,>=2.0"]


def test_wheel_keeps_s3_host_assets_and_uses_separate_connector_package(
    built_wheel: Path,
) -> None:
    names = _wheel_names(built_wheel)

    assert "stackos/integrations/s3.py" not in names
    assert not any(name.startswith("stackos_connectors/") for name in names)
    assert "stackos/plugins/builtin_utils_s3.py" in names
    assert "stackos/ui_dist/images/integrations/s3.png" in names

    with zipfile.ZipFile(built_wheel) as wheel:
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = wheel.read(metadata_name).decode("utf-8")
    for dependency in ("boto3", "botocore", "s3transfer"):
        assert f"Requires-Dist: {dependency}" in metadata
    requirements = Parser().parsestr(metadata).get_all("Requires-Dist", [])

    repo_root = Path(__file__).resolve().parents[3]
    source = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    source_connectors = [
        requirement
        for value in source["project"]["dependencies"]
        if canonicalize_name((requirement := Requirement(value)).name) == "stackos-connectors"
    ]
    assert len(source_connectors) == 1, "source must declare one connector dependency"
    source_connector = source_connectors[0]
    assert source_connector.url is None
    assert source_connector.marker is None
    assert not source_connector.extras
    specifiers = list(source_connector.specifier)
    assert len(specifiers) == 1
    assert specifiers[0].operator == "==" and "*" not in specifiers[0].version, (
        "source connector dependency must pin one exact version"
    )
    connector_requirements = [
        requirement
        for value in requirements
        if canonicalize_name((requirement := Requirement(value)).name) == "stackos-connectors"
    ]
    assert connector_requirements == source_connectors
    assert all("file:" not in value and " @ " not in value for value in requirements)
