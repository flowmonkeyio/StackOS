"""Finance management package origin, host routing and documented boundaries."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FINANCE = ROOT / "plugins" / "finance"

# Shared by relocation and actual wheel extraction; child imports no test helpers.
PACKAGED_SMOKE = r"""
import json, sys
from pathlib import Path
package, external = map(Path, sys.argv[1:3])
sys.path.insert(0, str(package))
import stackos
from stackos.plugins.builtin_finance import helpers
assert Path(stackos.__file__).is_relative_to(package)
m = helpers()
assert Path(m.__file__).is_relative_to(package / 'stackos/_assets/plugins/finance/scripts')
path = m.workspace.initialize(external)
before = path.read_bytes()
s = m.read_snapshot(path)
query = dict(start='2026-09-01', end='2026-09-30', recognition_basis='cash',
             date_basis='transaction', timezone='UTC')
assert m.query(s, query)['rows'] == []
assert path.read_bytes() == before and s['schema_version'] == 'local-json-v1'
intent = dict(request_id='package-synthetic-create',
    expected_snapshot={k:s[k] for k in ('revision','sha256')},
    action='create-bookkeeping',
    fields=dict(date='2026-09-03', amount=dict(minor='12345',currency='JPY'),
                category=dict(state='unresolved'), business_purpose='Synthetic package fixture'),
    source_refs=[], attestation='Simulated test operator observation, not business approval',
    reason='Synthetic isolated package proof')
p = m.preview(s, intent)
assert path.read_bytes() == before
receipt = m.commit(path, intent, p['preview_digest'], actor='local-user:operator')
assert receipt['reread_result'] == 'verified' and receipt['snapshot']['revision'] == 1
current = m.read_snapshot(path)
assert current['schema_version'] == 'local-json-v1'
ref = current['document']['bookkeeping'][0]['record_id']
detail = m.record_detail(current, ref)
assert detail['record']['amount'] == {'amount_minor':12345, 'currency':'JPY'}
assert m.query(current, query)['rows']
stable = path.read_bytes()
replay = m.commit(path, intent, p['preview_digest'], actor='local-user:operator')
assert replay['idempotency_replay'] and path.read_bytes() == stable
print(json.dumps({'origin':str(m.__file__), 'schema_version':current['schema_version'],
                  'revision':current['revision'], 'reread':'verified', 'retry':'replayed'}))
"""


def run_packaged_smoke(package: Path, tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-I", "-c", PACKAGED_SMOKE, str(package), str(tmp_path / "external")],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"schema_version": "local-json-v1"' in result.stdout


def test_relocated_management_helpers_without_source_checkout(tmp_path: Path) -> None:
    package = tmp_path / "installed"
    shutil.copytree(
        ROOT / "stackos",
        package / "stackos",
        ignore=shutil.ignore_patterns("__pycache__", "ui_dist", "_assets"),
    )
    shutil.copytree(FINANCE, package / "stackos/_assets/plugins/finance")
    run_packaged_smoke(package, tmp_path)


def test_all_seven_workflows_have_current_root_guidance_route() -> None:
    workflows = sorted(path.stem for path in (FINANCE / "workflows").glob("*.yaml"))
    assert len(workflows) == 7
    routes = (ROOT / "AGENTS.md").read_text()
    assert all(f"finance.{name}" in routes for name in workflows)
    assert ".codex/orchestrator/finance-department-orchestrator.md" in routes
    source = yaml.safe_load((FINANCE / "skill-presets/finance.yaml").read_text())
    preset = next(
        p for p in source["presets"] if p["key"] == "stackos.finance.department-orchestrator"
    )
    adaptation = (ROOT / ".codex/orchestrator/finance-department-orchestrator.md").read_text()
    assert f"v{preset['version']}" in adaptation
    assert "finance.management-review" in adaptation


def test_management_docs_preserve_privacy_and_actual_host_boundary() -> None:
    readme = (FINANCE / "README.md").read_text()
    assert all(label in readme for label in ("Overview", "Expenses & income", "Reports"))
    assert "Clear nonempty search text" in readme and "apply filters" in readme
    assert "source build does not update or restart" in readme
    handoff = (FINANCE / "references/workflow-handoffs.md").read_text()
    for field in (
        "external_result_ref",
        "external_result_sha256",
        "source_snapshot",
        "report_sha256",
    ):
        assert field in handoff
    assert "full published" in handoff and "independent" in handoff
    local = (FINANCE / "references/local-workspace-contract.md").read_text()
    assert "not automatically enrolled" in local and "reselection" in local
    assert "does not" in local and "master schema migration" in local
