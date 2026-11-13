"""Per audit P-G4: the wheel must bundle skills and plugins.

A pipx-mode install has no checked-out repo on disk, so
`stackos install` resolves assets via `importlib.resources` from
``stackos/_assets/skills/`` and ``stackos/_assets/plugins/``.
We verify the wheel produced by `python -m build` contains those paths
with the same skill counts as the canonical StackOS skill source.
"""

from __future__ import annotations

import os
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
    """Build with the locked dev backend, without provisioning another environment."""
    repo_root = Path(__file__).resolve().parents[3]
    dist_dir = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(dist_dir)],
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


@pytest.mark.parametrize("layout", ["wheel", "desktop"])
def test_packaged_pattern_startup_refresh(built_wheel: Path, tmp_path: Path, layout: str) -> None:
    """Run the real lifespan with package resolution, never the live install.

    Desktop's payload builder installs this same wheel into site-packages.
    These fixture layouts exercise that installed owner without building or
    replacing the native app, launchd, browser runtime or system installation.
    """
    import json

    from tests.integration.test_repositories.test_pattern_bundles import bundle, entry

    package = tmp_path / (
        "installed"
        if layout == "wheel"
        else "StackOS.app/Contents/Resources/stackos/.venv/lib/python3.13/site-packages"
    )
    with zipfile.ZipFile(built_wheel) as wheel:
        wheel.extractall(package)
    asset = package / "stackos/_assets/patterns/bundle.json"
    source = Path(__file__).resolve().parents[3] / "stackos/db/bundled_patterns.json"
    assert asset.read_bytes() == source.read_bytes()
    reviewed = json.loads(source.read_text())
    assert reviewed["entries"], "The production bundle contains reviewed recommendations"
    # Replace only a disposable package's trusted asset, not a runtime selector.
    script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from stackos.config import Settings
from stackos.install import bundled_patterns_source, detect_mode
from stackos.repositories.patterns import PatternRepository
from stackos.db.model_patterns import Pattern, PatternDeletion
from stackos.server import create_app
root, phase = Path(sys.argv[2]), sys.argv[3]
settings = Settings(_env_file=None, data_dir=root/'data', state_dir=root/'state')
assert detect_mode() == 'pipx'
assert str(bundled_patterns_source()).startswith(sys.argv[1])
with TestClient(create_app(settings), base_url='http://127.0.0.1:5199') as api:
    headers = {'Authorization': 'Bearer ' + settings.token_path.read_text().strip()}
    def call(name, arguments):
        response = api.post('/api/v1/operations/pattern.'+name+'/call',
                            json={'arguments': arguments}, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()
    if phase == 'reviewed':
        expected = json.loads(bundled_patterns_source().read_text())
        page = call('list', {'source':'stackos', 'limit':100})
        assert page['catalog_total'] == len(expected['entries'])
        assert page['total_estimate'] == len(expected['entries'])
        assert all('body' not in row for row in page['items'])
        for item in expected['entries']:
            detail = call('get', {'key':item['key']})
            for field in ['key', 'title', 'summary', 'body']:
                assert detail[field] == item[field]
            assert detail['tags'] == sorted(set(tag.strip() for tag in item['tags']))
            assert detail['source_label'] == 'StackOS'
            assert detail['source'] == 'stackos'
    elif phase == 'empty':
        assert call('list', {})['catalog_total'] == 0
    elif phase == 'v1':
        edited = call('get', {'key': 'edited'})
        call('upsert', {k: edited[k] for k in ['key','title','summary','tags']} |
             {'body':'Local bytes \\n', 'expected_etag': edited['etag']})
        deleted = call('get', {'key':'deleted'})
        call('delete', {'pattern_id':deleted['id'], 'expected_etag':deleted['etag']})
        call('upsert', {'key':'user','title':'User','summary':'','tags':[], 'body':'Keep'})
    else:
        assert call('get', {'key':'shipped'})['body'] == 'New package'
        assert call('get', {'key':'edited'})['body'] == 'Local bytes \\n'
        assert call('get', {'key':'edited'})['source_label'] == 'StackOS · Customized'
        assert call('get', {'key':'new'})['source_label'] == 'StackOS'
        assert call('get', {'key':'removed'})['source_label'] == 'StackOS'
        assert call('get', {'key':'user'})['source_label'] == 'Custom'
        assert call('list', {})['catalog_total'] == 5
        page = call('list', {'query':'Local','tags':['freeform'],'source':'customized'})
        assert page['items'][0]['key'] == 'edited'
    with Session(api.app.state.engine) as session:
        records = session.exec(select(Pattern).order_by(Pattern.id))
        rows = [r.model_dump(mode='json') for r in records]
        markers = [r.model_dump() for r in session.exec(select(PatternDeletion))]
    print('PATTERN_RESULT=' + json.dumps({'rows': rows, 'markers': markers}, sort_keys=True))
"""

    def run(phase):
        env = dict(os.environ)
        env.pop("PYTHONHOME", None)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(package),
                str(tmp_path / ("reviewed-runtime" if phase == "reviewed" else "runtime")),
                phase,
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(
            next(
                line.removeprefix("PATTERN_RESULT=")
                for line in result.stdout.splitlines()
                if line.startswith("PATTERN_RESULT=")
            )
        )

    shipped = run("reviewed")
    for row, item in zip(shipped["rows"], reviewed["entries"], strict=True):
        assert row["bundled_id"] == item["bundled_id"]
        assert row["bundled_version"] == reviewed["version"]
        assert row["body"] == item["body"]
    assert run("reviewed") == shipped
    asset.write_text(bundle())
    run("empty")
    asset.write_text(bundle(entry(), entry("edited"), entry("deleted"), entry("removed")))
    v1 = run("v1")
    asset.write_text(
        bundle(
            entry(body="New package"),
            entry("edited", body="Do not overwrite"),
            entry("deleted"),
            entry("new"),
            version="v2",
        )
    )
    v2 = run("v2")
    for key in ("edited", "user", "removed"):
        assert next(r for r in v1["rows"] if r["key"] == key) == next(
            r for r in v2["rows"] if r["key"] == key
        )
    assert v2["markers"] == [{"bundled_id": "deleted"}]
    assert run("repair") == v2
    asset.write_text('{"version":"bad","entries":[{}]}')
    assert run("malformed") == v2


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


@pytest.mark.parametrize(
    ("package", "supported", "migration"),
    [
        ("anthropic", "0.100.0", "0.101.0"),
        ("cryptography", "48.0.1", "49.0.0"),
        ("sqlmodel", "0.0.44", "0.0.45"),
    ],
)
def test_wheel_excludes_deferred_dependency_migrations(
    built_wheel: Path, package: str, supported: str, migration: str
) -> None:
    """Pipx installs have no uv.lock, so the wheel must preserve reviewed branches."""
    with zipfile.ZipFile(built_wheel) as wheel:
        metadata_name = next(
            name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = Parser().parsestr(wheel.read(metadata_name).decode("utf-8"))
    requirements = [
        requirement
        for value in metadata.get_all("Requires-Dist", [])
        if canonicalize_name((requirement := Requirement(value)).name) == package
    ]
    assert len(requirements) == 1
    assert supported in requirements[0].specifier
    assert migration not in requirements[0].specifier


def test_graphql_dev_requirement_stays_on_reviewed_api_branch() -> None:
    """GraphQL-core minor releases may change API contracts used by provider tests."""
    repo_root = Path(__file__).resolve().parents[3]
    source = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [
        requirement
        for value in source["dependency-groups"]["dev"]
        if canonicalize_name((requirement := Requirement(value)).name) == "graphql-core"
    ]
    assert len(requirements) == 1
    assert "3.2.13" in requirements[0].specifier
    assert "3.3.0" not in requirements[0].specifier


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
