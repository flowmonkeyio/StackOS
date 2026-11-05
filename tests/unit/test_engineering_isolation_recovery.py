"""Focused isolation, recovery, and model-resolution behavior tests."""

from pathlib import Path

import pytest

# These tests exercise boundaries shared by independent workflow stages. They
# intentionally test durable semantics rather than restating implementation text.
from stackos.cli.engineering_delivery import module as delivery


def _workspace(tmp_path: Path, role: str, *, admitted: dict[str, str] | None = None):
    import hashlib
    import json

    controlled_workspace = delivery._workspace

    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = tmp_path / "repo"
    view = tmp_path / "view"
    scratch = tmp_path / "scratch"
    repo.mkdir()
    view.mkdir()
    scratch.mkdir()
    files = []
    for path, content in (admitted or {}).items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(0o640)
        view_target = view / path
        view_target.parent.mkdir(parents=True, exist_ok=True)
        view_target.write_text(content, encoding="utf-8")
        files.append(
            {
                "path": path,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "mode": 0o640,
                "selection": "changed-deliverable",
            }
        )
    manifest = {
        "selection_contract": "controller-source-outcome-v1",
        "files": files,
        "deleted": [],
        "changed_paths": sorted(admitted or {}),
    }
    encoded = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    (tmp_path / "outcome-manifest.json").write_bytes(encoded)
    broker = controlled_workspace.Workspace(
        repo, view, scratch, role, hashlib.sha256(encoded).hexdigest()
    )
    return controlled_workspace, broker, repo, view


def test_workspace_edit_role_and_compare_and_swap_are_enforced(tmp_path: Path) -> None:
    _, writer, repo, _ = _workspace(
        tmp_path / "writer", "executor", admitted={"src/app.py": "old\n"}
    )
    with pytest.raises(ValueError, match="role stage"):
        writer.dispatch(
            "workspace_edit", {"path": "tests/test_app.py", "expected_sha256": None, "content": "x"}
        )
    with pytest.raises(ValueError, match="changed or missing"):
        writer.dispatch(
            "workspace_edit",
            {"path": "src/app.py", "expected_sha256": "0" * 64, "content": "new\n"},
        )
    assert (repo / "src/app.py").read_text() == "old\n"
    writer.close()

    _, test_writer, repo, view = _workspace(tmp_path / "tests", "test_writer")
    prior_umask = __import__("os").umask(0o077)
    try:
        result = test_writer.dispatch(
            "workspace_edit",
            {"path": "tests/test_app.py", "expected_sha256": None, "content": "assert True\n"},
        )
    finally:
        __import__("os").umask(prior_umask)
    assert (repo / "tests/test_app.py").read_text() == "assert True\n"
    assert (repo / "tests/test_app.py").stat().st_mode & 0o777 == 0o600
    assert (view / "tests/test_app.py").stat().st_mode & 0o777 == 0o600
    assert result["after_sha256"]
    test_writer.close()


def test_reviewer_tool_contract_keeps_required_schema_and_resume_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import importlib

    scripts = Path(delivery.__file__).parent
    monkeypatch.syspath_prepend(str(scripts))
    reviewer_isolation = importlib.import_module("reviewer_isolation")

    _, workspace_broker, _, _ = _workspace(tmp_path / "reviewer-contract", "executor")
    worker_tools = reviewer_isolation.tools(worker=True) + workspace_broker.specs()
    workspace_broker.close()
    names = {tool["name"] for tool in worker_tools}
    assert {"workspace_list", "workspace_read", "workspace_edit"} <= names
    edit = next(tool for tool in worker_tools if tool["name"] == "workspace_edit")
    assert edit["type"] == "function"
    assert set(edit["inputSchema"]["required"]) == {"path", "expected_sha256", "content"}
    assert reviewer_isolation.nonnull({"nested": {"optional": None, "required": False}}) == {
        "nested": {"required": False}
    }
    source = Path(reviewer_isolation.__file__).read_text(encoding="utf-8")
    assert 'server.call("thread/resume", params)' in source
    assert 'params.update({"threadId": resume_id, "excludeTurns": True})' in source
    assert "thread_id != resume_id" in source


def test_recovery_allowance_survives_same_failure_and_agent_replacement() -> None:
    recovery = delivery._recovery
    episode = {"id": "episode-1", "evidence_refs": ["failure-1"]}
    ledger = object.__new__(recovery.Ledger)
    ledger.value = {"events": []}
    ledger.allowance = recovery.Ledger.allowance.__get__(ledger)
    ledger.append = lambda kind, **facts: ledger.value["events"].append({"kind": kind, **facts})
    assert ledger.allowance(episode) == "retry"
    recovery.Ledger.consume(ledger, episode, "retry", "same_thread")
    assert ledger.allowance(episode) == "final"
    recovery.Ledger.consume(ledger, episode, "final", "replacement_agent")
    assert ledger.allowance(episode) == "stop"
    with pytest.raises(ValueError, match="exhausted"):
        recovery.Ledger.consume(ledger, episode, "final", "another_replacement")


def test_authority_and_effect_gates_hold_denial_and_unknown_custody() -> None:
    recovery = delivery._recovery
    denied = {"host_refusal": True, "refusal_state": "no_refusal_reported"}
    unknown = {"refusal_state": "unknown", "events": [{"type": "host.error"}]}
    assert recovery.authority_disposition(denied) == "denied"
    assert recovery.authority_disposition(unknown) == "unknown"
    assert (
        recovery.role_effect_disposition("executor", {"events": [{"type": "turn.completed"}]})[
            "state"
        ]
        == "unknown"
    )
    assert not recovery.handoff_effects_safe(
        {"observations": [{"exit_code": 0, "stdout": "unchanged"}]}
    )
    assert not recovery.handoff_effects_safe(
        {
            "observations": [
                {
                    "exit_code": 0,
                    "group_cleared": True,
                    "confinement": {"mode": "read_only", "verified": True, "network": False},
                    "refusal_state": "no_refusal_reported",
                    "timed_out": False,
                }
            ],
            "outcome_review": denied,
        }
    )


def test_model_precedence_is_task_then_project_then_builtin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        delivery,
        "_call",
        lambda operation, arguments: (
            calls.append((operation, arguments))
            or {
                "extension": {
                    "enabled": True,
                    "selected_context_json": {
                        "agent_models": {
                            "planner": "project-planner",
                            "executor": "project-executor",
                        }
                    },
                }
            }
        ),
    )
    models = delivery._models(
        9,
        {
            "executor": "task-executor",
            "reviewer": None,
            "planner": None,
            "test_writer": "task-tests",
        },
    )
    assert models == {
        "planner": "project-planner",
        "executor": "task-executor",
        "reviewer": delivery.DEFAULT_MODELS["reviewer"],
        "test_writer": "task-tests",
    }
    assert calls[0][0] == "workflowExtension.get"


def _settled_designer_receipt(
    *, answer: object, thread: str = "designer-thread"
) -> dict[str, object]:
    digest = "a" * 64
    return {
        "role": "designer",
        "status": "complete",
        "answer": answer,
        "requested_model": "gpt-6-astra",
        "thread_id": thread,
        "refusal_state": "no_refusal_reported",
        "exit_code": 0,
        "timed_out": False,
        "group_cleared": True,
        "group_leaked": False,
        "outcome_manifest": {"sha256": digest},
        "events": [
            {
                "type": "model.resolved",
                "requested_model": "gpt-6-astra",
                "resolved_model": "gpt-6-astra",
                "thread_id": thread,
            },
            {
                "type": "scope.admitted",
                "scope": "controlled_workspace",
                "role": "designer",
                "network": False,
                "actual_repository_command_access": False,
                "mutation_owner": "synchronous_main_process_broker",
                "custody": "outer_existing_execute",
                "manifest_sha256": digest,
                "requested_model": "gpt-6-astra",
                "resolved_model": "gpt-6-astra",
                "thread_id": thread,
            },
            {
                "type": "effects.reconciled",
                "manifest_sha256": digest,
                "scope": "controlled_workspace",
                "broker_closed": True,
                "mutation_owner": "synchronous_main_process_broker",
                "thread_id": thread,
            },
        ],
    }


def test_designer_response_retry_requires_same_model_thread_and_clear_custody() -> None:
    recovery = delivery._recovery
    schema = delivery._schema("designer")
    malformed = _settled_designer_receipt(answer={"decision": "maybe"})
    malformed["effect_disposition"] = recovery.role_effect_disposition("designer", malformed)
    assert recovery.role_effects_safe(malformed)
    assert recovery.authority_disposition(malformed) == "clear"
    with pytest.raises(recovery.ValidationError):
        recovery.validate(malformed["answer"], schema)

    stop = _settled_designer_receipt(
        answer={
            "decision": "stop",
            "authority": "clear",
            "classification": "execution",
            "cause": "Stop",
            "plan": "",
            "rationale": "Stop this run after classifying the failure.",
            "evidence_refs": ["f" * 32],
            "comparisons": [],
        }
    )
    recovery.validate(stop["answer"], schema)

    rerouted = _settled_designer_receipt(answer={"decision": "maybe"})
    rerouted["requested_model"] = "other-model"
    rerouted["effect_disposition"] = recovery.role_effect_disposition("designer", rerouted)
    wrong_thread = _settled_designer_receipt(
        answer={"decision": "maybe"}, thread="replacement-thread"
    )
    wrong_thread["effect_disposition"] = recovery.role_effect_disposition("designer", wrong_thread)
    denied = _settled_designer_receipt(answer={"decision": "maybe"})
    denied["host_refusal"] = True
    denied["effect_disposition"] = recovery.role_effect_disposition("designer", denied)
    unknown = _settled_designer_receipt(answer={"decision": "maybe"})
    unknown["refusal_state"] = "unknown"
    unknown["effect_disposition"] = recovery.role_effect_disposition("designer", unknown)

    assert not recovery.role_effects_safe(rerouted)
    assert recovery.role_effects_safe(wrong_thread)
    assert wrong_thread["thread_id"] != malformed["thread_id"]
    assert recovery.authority_disposition(denied) == "denied"
    assert recovery.authority_disposition(unknown) == "unknown"
    assert not recovery.role_effects_safe(denied)
    # Effect settlement and authority are independent gates; unknown authority holds triage.
    assert recovery.role_effects_safe(unknown)


def test_protocol_rpc_preserves_explicit_null_params(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    monkeypatch.syspath_prepend(str(Path(delivery.__file__).parent))
    adapter = importlib.import_module("reviewer_isolation")
    server = object.__new__(adapter.Server)
    server.sequence = 0
    sent = []
    server.send = sent.append
    server.receive = lambda: {"id": 1, "result": {"requirements": []}}

    result = server.call("configRequirements/read", None)

    assert sent == [{"id": 1, "method": "configRequirements/read", "params": None}]
    assert result == {"requirements": []}


def test_failure_episode_requires_grounded_ids_without_mutating_on_validation() -> None:
    recovery = delivery._recovery
    ledger = object.__new__(recovery.Ledger)
    ledger.value = {
        "events": [
            {"kind": "failure", "failure_id": "failure-current"},
            {"kind": "failure", "failure_id": "failure-prior"},
        ],
        "episodes": [
            {
                "id": "episode-prior",
                "evidence_refs": ["failure-prior"],
                "resolved": False,
            }
        ],
    }
    before = __import__("copy").deepcopy(ledger.value)
    decision = {
        "cause": "The same missing dependency failed again.",
        "classification": "execution",
        "decision": "try_again",
        "authority": "clear",
        "plan": "",
        "rationale": "The failure evidence identifies the same dependency.",
        "evidence_refs": ["failure-current"],
        "comparisons": [
            {
                "episode_id": "episode-prior",
                "relation": "same",
                "rationale": "Both attempts name the same dependency.",
                "evidence_refs": ["failure-current", "failure-prior"],
            }
        ],
    }

    assert ledger.episode(decision, "failure-current", validate_only=True) is None
    assert ledger.value == before
    for bad_refs in (
        ["failure-current.event"],
        ["failure-current", "invented-failure"],
        ["failure-current", "episode-prior"],
    ):
        invalid = {**decision, "evidence_refs": bad_refs}
        with pytest.raises(ValueError, match="grounded identity"):
            ledger.episode(invalid, "failure-current", validate_only=True)
        assert ledger.value == before


def test_packaged_command_runtime_grant_requires_exact_interpreter_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    monkeypatch.syspath_prepend(str(Path(delivery.__file__).parent))
    adapter = importlib.import_module("reviewer_isolation")
    runtime = Path("/Applications/StackOS.app/Contents/Resources/stackos/.venv")
    binary = runtime / "bin" / "python"
    roots = (runtime / "bin", runtime / "lib", runtime / "pyvenv.cfg")

    monkeypatch.setattr(adapter.sys, "prefix", str(runtime))
    monkeypatch.setattr(adapter.sys, "base_prefix", str(runtime))
    monkeypatch.setattr(adapter.sys, "executable", str(binary))
    reads, path = adapter.command_runtime()
    assert reads == {str(root): "read" for root in roots}
    assert path == f"{runtime / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin"

    repo_venv = Path(delivery.__file__).resolve().parents[2] / ".venv"
    for prefix in ("/usr/local/opt/python@3.14", str(repo_venv)):
        monkeypatch.setattr(adapter.sys, "prefix", prefix)
        reads, path = adapter.command_runtime()
        assert reads == {}
        assert path == "/usr/bin:/bin:/usr/sbin:/sbin"

def _provenance_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for path, content in {
        "AGENTS.md": "ordinary repository guidance\n",
        "docs/agent-operating-model.md": "ordinary routed guidance\n",
        "docs/plans/evidence/uifirst-001/independent-expectations.md": "private evidence\n",
        "src/module.py": "VALUE = 1\n",
        "tests/test_module.py": "def test_value(): pass\n",
    }.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    delivery._git(repo, "init", "-q")
    delivery._git(repo, "config", "user.name", "Provenance Test")
    delivery._git(repo, "config", "user.email", "provenance@example.invalid")
    delivery._git(repo, "add", "AGENTS.md", "docs", "src", "tests")
    delivery._git(repo, "commit", "-qm", "baseline")
    (repo / "src/module.py").write_text("VALUE = 2\n", encoding="utf-8")
    return repo


def test_worker_snapshots_admit_ordinary_guidance_and_exclude_unchanged_evidence(
    tmp_path: Path,
) -> None:
    import hashlib

    repo = _provenance_repo(tmp_path)
    protected = "docs/plans/evidence/uifirst-001/independent-expectations.md"
    guidance = ("AGENTS.md", "docs/agent-operating-model.md")

    for role in ("planner", "executor", "designer", "test_writer"):
        state = tmp_path / role
        state.mkdir()
        manifest = delivery._outcome_snapshot(repo, state / "view", worker=True)
        broker = delivery._workspace.Workspace(
            repo, state / "view", state / "scratch", role, manifest["sha256"]
        )
        try:
            admitted = {
                row["path"]: row
                for row in broker.dispatch("workspace_list", {})["files"]
            }
            assert protected not in admitted
            for path in guidance:
                result = broker.dispatch("workspace_read", {"path": path})
                assert admitted[path]["selection"] == "project-guidance-data"
                assert result["sha256"] == hashlib.sha256(
                    (repo / path).read_bytes()
                ).hexdigest()
        finally:
            broker.close()

    reviewer_state = tmp_path / "reviewer"
    reviewer_state.mkdir()
    reviewer_manifest = delivery._outcome_snapshot(repo, reviewer_state / "view")
    reviewer = delivery._workspace.Workspace(
        repo,
        reviewer_state / "view",
        reviewer_state / "scratch",
        "reviewer",
        reviewer_manifest["sha256"],
    )
    try:
        reviewer_paths = {
            row["path"] for row in reviewer.dispatch("workspace_list", {})["files"]
        }
        assert protected not in reviewer_paths
        assert not set(guidance) & reviewer_paths
        assert "src/module.py" in reviewer_paths
    finally:
        reviewer.close()


def test_changed_protected_evidence_fails_closed(tmp_path: Path) -> None:
    repo = _provenance_repo(tmp_path)
    protected = repo / "docs/plans/evidence/uifirst-001/independent-expectations.md"
    protected.write_text("changed protected evidence\n", encoding="utf-8")

    with pytest.raises(
        ValueError, match="changed path has forbidden runtime/report/credential provenance"
    ):
        delivery._outcome_snapshot(repo, tmp_path / "snapshot", worker=True)


def test_forged_manifest_labels_cannot_bypass_private_or_role_path_admission(
    tmp_path: Path,
) -> None:
    import hashlib
    import json

    controlled = delivery._workspace

    def forged_broker(root: Path, role: str, path: str, selection: str):
        repo = root / "repo"
        view = root / "view"
        scratch = root / "scratch"
        repo.mkdir(parents=True)
        view.mkdir()
        scratch.mkdir()
        content = "synthetic content\n"
        for base in (repo, view):
            target = base / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        manifest = {
            "selection_contract": "controller-source-outcome-v1",
            "files": [
                {
                    "path": path,
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "mode": 0o600,
                    "selection": selection,
                }
            ],
            "deleted": [],
            "changed_paths": [path],
        }
        encoded = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        (root / "outcome-manifest.json").write_bytes(encoded)
        return controlled.Workspace(
            repo, view, scratch, role, hashlib.sha256(encoded).hexdigest()
        )

    with pytest.raises(
        ValueError, match="changed path has forbidden runtime/report/credential provenance"
    ):
        forged_broker(
            tmp_path / "private",
            "planner",
            "docs/plans/evidence/uifirst-001/independent-expectations.md",
            "project-guidance-data",
        )

    with pytest.raises(ValueError, match="invalid manifest file metadata"):
        forged_broker(
            tmp_path / "forged-guidance",
            "executor",
            "tests/test_module.py",
            "changed-guidance-data",
        )

    broker = forged_broker(
        tmp_path / "role", "executor", "tests/test_module.py", "changed-deliverable"
    )
    try:
        with pytest.raises(ValueError, match="outside this role stage"):
            broker.dispatch(
                "workspace_edit",
                {
                    "path": "tests/test_module.py",
                    "expected_sha256": hashlib.sha256(b"synthetic content\n").hexdigest(),
                    "content": "def test_forged(): pass\n",
                },
            )
    finally:
        broker.close()
