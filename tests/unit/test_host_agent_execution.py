from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from stackos.host_mcp import AgentRunResult, run_agent, service
from stackos.host_mcp.adapters import claude_code, codex, gemini_cli, grok, hermes

SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

NATIVE_CASES = [
    (
        "claude-code",
        "claude-sonnet-5-5",
        None,
        {
            "type": "result",
            "is_error": False,
            "session_id": "native-thread",
            "structured_output": {"summary": "delivered"},
        },
    ),
    (
        "gemini-cli",
        "gemini-3.1-pro-preview",
        None,
        {"response": '{"summary":"delivered"}', "stats": {}, "session_id": "native-thread"},
    ),
    (
        "hermes",
        "provider/model-id",
        "openrouter",
        {
            "type": "result",
            "session_id": "native-thread",
            "exit_code": 0,
            "text": '{"summary":"delivered"}',
        },
    ),
    (
        "grok",
        "grok-build",
        None,
        {"text": '{"summary":"delivered"}', "sessionId": "native-thread", "stopReason": "end_turn"},
    ),
]


def fake_native_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_code.claude_mcp, "resolve_claude_bin", lambda: "/fake/claude")
    # Native version probing is exercised separately by usage-contract fixtures.
    monkeypatch.setattr(claude_code, "_native_version", lambda *_: "2.1.277 (Claude Code)")
    monkeypatch.setattr(gemini_cli, "resolve_gemini_bin", lambda: "/fake/gemini")
    monkeypatch.setattr(hermes, "resolve_hermes_bin", lambda: "/fake/hermes")
    monkeypatch.setattr(grok, "resolve_grok_bin", lambda _home: "/fake/grok")


def test_codex_native_usage_is_separate_from_agent_output(project, monkeypatch):
    """Native exec's cumulative counters are separate from agent-authored output."""
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")

    def execute(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text('{"summary":"delivered"}')
        events = [
            {"type": "thread.started", "thread_id": "native-thread"},
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 80,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 20,
                },
            },
        ]
        return subprocess.CompletedProcess(command, 0, "\n".join(map(json.dumps, events)), "")

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent("codex", project, "Instructions", "Work", SCHEMA)
    assert result.ok
    assert result.usage["basis"] == "session"
    assert result.usage["snapshot"] == {"input": 100, "output": 50, "total": 150}


@pytest.mark.parametrize(("host", "model", "provider", "envelope"), NATIVE_CASES)
@pytest.mark.parametrize("returned_id", ["native-thread", None, "wrong-thread"])
def test_native_exact_resume(project, monkeypatch, host, model, provider, envelope, returned_id):
    fake_native_discovery(monkeypatch)
    envelope = dict(envelope)
    envelope["sessionId" if host == "grok" else "session_id"] = returned_id
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        assert command[command.index("--resume") + 1] == "native-thread"
        assert command[command.index("--model") + 1] == model
        assert kwargs["cwd"] == project
        assert not any(flag in command for flag in ("--latest", "--last", "--fork"))
        if host == "hermes":
            assert command[command.index("--provider") + 1] == provider
            assert command[command.index("--profile") + 1] == "work"
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent(
        host,
        project,
        "Instructions",
        "Repair",
        SCHEMA,
        model=model,
        provider=provider,
        profile="work" if host == "hermes" else None,
        resume_thread_id="native-thread",
    )
    assert result.ok is (returned_id == "native-thread")
    assert result.thread_id == returned_id
    if not result.ok:
        assert "session" in result.error.lower()
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("host", "mode"),
    [
        ("codex", "workspace-write"),
        ("claude-code", "workspace-write"),
        ("grok", "workspace-write"),
        ("gemini-cli", "normal"),
        ("hermes", "normal"),
    ],
)
def test_review_mode_permits_executable_specs(host, mode):
    assert service.agent_review_sandbox(host) == mode


@pytest.mark.parametrize("thread_id", [None, "wrong-thread", "saved-thread"])
@pytest.mark.parametrize("native_error", [None, "native resume denied"])
def test_codex_resume_identity_and_error_reach_caller(
    project, monkeypatch, thread_id, native_error
):
    calls = []

    def runner(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["resume_thread_id"] == "saved-thread"
        assert kwargs["model"] == "gpt-6.1-sol"
        assert kwargs["provider"] == "custom"
        return AgentRunResult(
            1 if native_error else 0,
            thread_id=thread_id,
            result={"summary": "done"},
            error=native_error,
        )

    definition = replace(service._definitions()[0], run_agent=runner)
    monkeypatch.setattr(service, "_definitions", lambda: (definition,))
    result = run_agent(
        "codex",
        project,
        "Instructions",
        "Repair",
        SCHEMA,
        model="gpt-6.1-sol",
        provider="custom",
        resume_thread_id="saved-thread",
    )
    assert result.ok is (thread_id == "saved-thread" and native_error is None)
    assert result.thread_id == thread_id
    if native_error:
        assert native_error in result.error
    assert len(calls) == 1


@pytest.mark.parametrize("resume_thread_id", ["", " ", " session", "--last", "latest", "last"])
def test_invalid_resume_id_never_launches(project, monkeypatch, resume_thread_id):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("must not launch"))
    result = run_agent(
        "codex",
        project,
        "Instructions",
        "Repair",
        SCHEMA,
        resume_thread_id=resume_thread_id,
    )
    assert not result.ok
    assert "session ID" in result.error


@pytest.mark.parametrize("exit_code", [0, 1])
def test_gemini_error_preserves_session_id(project, monkeypatch, exit_code):
    fake_native_discovery(monkeypatch)
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            exit_code,
            json.dumps(
                {
                    "session_id": "native-thread",
                    "error": {"message": "permission denied"},
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent(
        "gemini-cli",
        project,
        "Instructions",
        "Repair",
        SCHEMA,
        resume_thread_id="native-thread",
    )
    assert not result.ok
    assert result.thread_id == "native-thread"
    assert "permission denied" in result.error
    assert result.exit_code == exit_code
    assert len(calls) == 1


@pytest.mark.parametrize(("host", "model", "provider", "envelope"), NATIVE_CASES)
def test_native_command_and_structured_result(
    project, monkeypatch, host, model, provider, envelope
):
    fake_native_discovery(monkeypatch)
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        assert kwargs["cwd"] == project
        assert "--resume" not in command
        assert command[command.index("--model") + 1] == model
        assert "Project instructions" in " ".join(command) + kwargs.get("input", "")
        assert "Ticket packet" in " ".join(command) + kwargs.get("input", "")
        assert not any(arg in command for arg in ("--yolo", "--always-approve", "--bare"))
        if host == "claude-code":
            assert command[command.index("--permission-mode") + 1] == "auto"
            assert "--tools" not in command
            assert "--disallowedTools" not in command
            assert json.loads(command[command.index("--json-schema") + 1]) == SCHEMA
        elif host == "gemini-cli":
            assert command[command.index("--approval-mode") + 1] == "auto_edit"
        elif host == "hermes":
            assert command[command.index("--provider") + 1] == provider
            assert command[command.index("--profile") + 1] == "work"
            assert command[command.index("--query-file") + 1] == "-"
            assert "--oneshot" in command
        elif host == "grok":
            assert "--no-auto-update" in command
            assert command[command.index("--sandbox") + 1] == "workspace"
            assert command[command.index("--permission-mode") + 1] == "auto"
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent(
        host,
        project,
        "Project instructions",
        "Ticket packet",
        SCHEMA,
        model=model,
        provider=provider,
        profile="work" if host == "hermes" else None,
    )
    assert result.ok, result.error
    assert result.result == {"summary": "delivered"}
    assert result.thread_id == "native-thread"
    assert len(calls) == 1
    assert not (project / ".codex").exists()


@pytest.mark.parametrize("host", ["gemini-cli", "hermes", "claude-desktop"])
def test_unsupported_native_read_only_does_not_launch(project, monkeypatch, host):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("must not launch"))
    result = run_agent(host, project, "Review", "packet", SCHEMA, sandbox="read-only")
    assert not result.ok
    assert "support" in result.error.lower()


@pytest.mark.parametrize("host", ["gemini-cli", "hermes"])
@pytest.mark.parametrize("valid", [True, False])
def test_normal_review_uses_native_permissions_and_review_schema(project, monkeypatch, host, valid):
    from stackos import task_delivery

    fake_native_discovery(monkeypatch)
    calls = []
    value = {"status": "success", "summary": "Reviewed", "outputs": "{}", "followups": []}
    if not valid:
        value["status"] = "complete"

    def execute(command, **kwargs):
        calls.append(command)
        prompt = " ".join(command) + kwargs.get("input", "")
        assert "Do not change files or commits" in prompt
        assert "exact committed candidate" in prompt
        assert all(
            flag not in command
            for flag in ("--sandbox", "--approval-mode", "--yolo", "--always-approve")
        )
        if host == "gemini-cli":
            assert command[command.index("--output-format") + 1] == "json"
            envelope = {"response": json.dumps(value)}
        else:
            assert "--oneshot" in command
            assert command[command.index("--format") + 1] == "stream-json"
            envelope = {"type": "result", "exit_code": 0, "text": json.dumps(value)}
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")

    monkeypatch.setattr(subprocess, "run", execute)
    mode = service.agent_review_sandbox(host)
    assert mode == "normal"
    result = run_agent(
        host,
        project,
        "Independent review. Do not change files or commits.",
        "Review the exact committed candidate",
        task_delivery.RESULT_SCHEMA,
        sandbox=mode,
        model="provider/model-id" if host == "hermes" else "gemini-3.1-pro-preview",
        provider="openrouter" if host == "hermes" else None,
    )
    assert result.ok is valid
    assert len(calls) == 1
    if valid:
        assert result.result == value
    else:
        assert result.error


@pytest.mark.parametrize("host", ["claude-code", "grok"])
def test_native_review_controls(project, monkeypatch, host):
    fake_native_discovery(monkeypatch)

    def execute(command, **kwargs):
        assert command[command.index("--permission-mode") + 1] == "dontAsk"
        if host == "claude-code":
            assert command[command.index("--tools") + 1] == "Read,Glob,Grep"
            assert command[command.index("--disallowedTools") + 1] == "mcp__*"
            envelope = NATIVE_CASES[0][3]
        else:
            assert command[command.index("--sandbox") + 1] == "read-only"
            envelope = NATIVE_CASES[3][3]
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")

    monkeypatch.setattr(subprocess, "run", execute)
    assert run_agent(host, project, "Review", "packet", SCHEMA, sandbox="read-only").ok


@pytest.mark.parametrize(
    ("host", "payload"),
    [
        ("claude-code", {"is_error": True, "result": "Account has no model access"}),
        ("gemini-cli", {"error": {"message": "Account has no model access"}}),
        ("hermes", {"type": "result", "exit_code": 1, "error": "Account has no model access"}),
        ("grok", {"error": "Account has no model access"}),
    ],
)
@pytest.mark.parametrize("exit_code", [0, 1])
def test_native_stdout_failure_stops_without_retry(project, monkeypatch, host, payload, exit_code):
    fake_native_discovery(monkeypatch)
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, exit_code, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent(
        host,
        project,
        "Instructions",
        "packet",
        SCHEMA,
        model="provider/model-id" if host == "hermes" else None,
        provider="openrouter" if host == "hermes" else None,
    )
    assert not result.ok
    assert "no model access" in result.error
    assert result.exit_code == exit_code
    assert len(calls) == 1


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "working project"
    root.mkdir()
    return root


@pytest.mark.parametrize("resume_thread_id", [None, "thread-test"])
def test_codex_runs_supplied_instructions_without_agent_files(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume_thread_id
) -> None:
    cli = tmp_path / "codex"
    receipt = tmp_path / "receipt.json"
    cli.write_text(
        f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if args == ["mcp", "list"]:
    sys.exit(0)
Path({str(receipt)!r}).write_text(json.dumps({{
    "args": args, "cwd": os.getcwd(), "prompt": sys.stdin.read(),
    "path_first": os.environ["PATH"].split(os.pathsep)[0],
    "schema": json.loads(Path(args[args.index("--output-schema") + 1]).read_text()),
}}))
Path(args[args.index("-o") + 1]).write_text('{{"summary": "delivered"}}')
print('{{"type": "thread.started", "thread_id": "thread-test"}}')
print('{{"type": "turn.completed"}}')
""",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    monkeypatch.setattr(codex, "resolve_codex_bins", lambda: [str(cli)])

    instructions = 'Use the project contract. Quote: "yes"\nWork carefully.'
    result = run_agent(
        "codex",
        project,
        instructions,
        "Deliver this ticket.",
        SCHEMA,
        resume_thread_id=resume_thread_id,
    )

    assert result.ok
    assert result.exit_code == 0
    assert result.thread_id == "thread-test"
    assert result.result == {"summary": "delivered"}
    call = json.loads(receipt.read_text(encoding="utf-8"))
    assert call["cwd"] == str(project)
    assert call["prompt"] == "Deliver this ticket."
    assert call["schema"] == SCHEMA
    assert call["path_first"] == str(cli.parent)
    args = call["args"]
    assert args[:6] == [
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--cd",
        str(project),
        "--approve-for-me",
    ]
    assert "--sandbox" not in args
    assert args[-1] == "-"
    if resume_thread_id:
        assert args[-3:] == ["resume", resume_thread_id, "-"]
        assert args.index("--cd") < args.index("resume")
        assert args.index("--approve-for-me") < args.index("resume")
    else:
        assert "resume" not in args
    override = args[args.index("-c") + 1]
    assert tomllib.loads(override) == {"developer_instructions": instructions}
    assert args.count("-c") == 1
    assert not (project / ".codex").exists()
    assert not Path(args[args.index("-o") + 1]).exists()


def test_runner_capability_uses_overview_registry(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = AgentRunResult(0, result={"summary": "reviewed"})
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return expected

    definition = replace(service._definitions()[0], run_agent=runner)
    monkeypatch.setattr(service, "_definitions", lambda: (definition,))

    assert (
        run_agent("codex", project, "delivery", "Review", SCHEMA, sandbox="read-only") is expected
    )
    assert calls == [((project, "delivery", "Review", SCHEMA), {"sandbox": "read-only"})]
    unsupported = run_agent("other-host", project, "delivery", "Review", SCHEMA)
    assert not unsupported.ok
    assert unsupported.exit_code is None
    assert "does not support" in (unsupported.error or "")


def test_empty_instructions_stop_before_discovery(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: pytest.fail("must not launch"))
    result = run_agent("codex", project, "  ", "Prompt", SCHEMA)
    assert not result.ok
    assert result.exit_code is None
    assert "instructions must not be empty" in (result.error or "")


@pytest.mark.parametrize(
    ("exit_code", "final_text", "expected_error"),
    [
        (7, None, "host failed"),
        (0, None, "Invalid Codex result"),
        (0, "not JSON", "Invalid Codex result"),
        (0, '{"unexpected": true}', "Invalid Codex result"),
    ],
)
def test_codex_failure_is_not_success_or_retried(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    final_text: str | None,
    expected_error: str,
) -> None:
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        if final_text is not None:
            Path(command[command.index("-o") + 1]).write_text(final_text, encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            exit_code,
            '{"type":"thread.started","thread_id":"failed-thread"}\n',
            "host failed",
        )

    monkeypatch.setattr(codex.subprocess, "run", execute)
    result = run_agent("codex", project, "delivery", "Prompt", SCHEMA)
    assert not result.ok
    assert result.exit_code == exit_code
    assert result.thread_id == "failed-thread"
    assert expected_error in (result.error or "")
    assert len(calls) == 1


def test_harness_settings_are_inherited_and_read_only_is_forwarded(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")

    def execute(command, **kwargs):
        assert not any(arg.startswith("model=") for arg in command)
        assert not any(arg.startswith("model_reasoning_effort=") for arg in command)
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert 'approval_policy="never"' in command
        assert "--approve-for-me" not in command
        Path(command[command.index("-o") + 1]).write_text('{"summary": "pass"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(codex.subprocess, "run", execute)
    assert run_agent("codex", project, "delivery", "Review", SCHEMA, sandbox="read-only").ok


@pytest.mark.parametrize("resume_thread_id", [None, "configured-thread"])
def test_codex_native_model_and_provider_selectors(project, monkeypatch, resume_thread_id):
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")

    def execute(command, **kwargs):
        assert command[command.index("--model") + 1] == "configured-model"
        assert 'model_provider="local-provider"' in command
        if resume_thread_id:
            assert command[-3:] == ["resume", resume_thread_id, "-"]
        Path(command[command.index("-o") + 1]).write_text('{"summary":"selected"}')
        return subprocess.CompletedProcess(
            command, 0, '{"type":"thread.started","thread_id":"configured-thread"}', ""
        )

    monkeypatch.setattr(subprocess, "run", execute)
    assert run_agent(
        "codex",
        project,
        "Instructions",
        "packet",
        SCHEMA,
        model="configured-model",
        provider="local-provider",
        resume_thread_id=resume_thread_id,
    ).ok


@pytest.mark.parametrize("event_type", ["error", "turn.failed"])
def test_codex_stdout_error_wins_over_success_file(project, monkeypatch, event_type):
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")

    def execute(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text('{"summary":"selected"}')
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"type": event_type, "error": {"message": "Model access denied"}}),
            "",
        )

    monkeypatch.setattr(subprocess, "run", execute)
    result = run_agent("codex", project, "Instructions", "packet", SCHEMA)
    assert not result.ok
    assert "Model access denied" in result.error


@pytest.mark.parametrize(
    ("host", "selection", "reason"),
    [
        ("codex", {"model": "invented-model"}, "catalog"),
        ("claude-code", {"provider": "other"}, "provider selector"),
        ("gemini-cli", {"profile": "work"}, "profile selector"),
        ("hermes", {}, "explicit provider and model"),
        ("grok", {"model": "--unsafe"}, "not a CLI option"),
    ],
)
def test_invalid_selection_never_launches(project, monkeypatch, host, selection, reason):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("must not launch"))
    result = run_agent(host, project, "Instructions", "packet", SCHEMA, **selection)
    assert not result.ok
    assert reason in result.error


@pytest.mark.parametrize(("host", "model", "provider", "envelope"), NATIVE_CASES)
@pytest.mark.parametrize("payload", ["not JSON", {"wrong": "schema"}])
def test_native_results_share_schema_validation(
    project, monkeypatch, host, model, provider, envelope, payload
):
    fake_native_discovery(monkeypatch)
    value = dict(envelope)
    field = {
        "claude-code": "structured_output",
        "gemini-cli": "response",
        "hermes": "text",
        "grok": "text",
    }[host]
    value[field] = (
        payload if host == "claude-code" or isinstance(payload, str) else json.dumps(payload)
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(command, 0, json.dumps(value), ""),
    )
    result = run_agent(
        host, project, "Instructions", "packet", SCHEMA, model=model, provider=provider
    )
    assert not result.ok
    assert "Invalid" in result.error


def test_hermes_discovered_shim_failure_is_actionable(project, monkeypatch):
    fake_native_discovery(monkeypatch)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(
            command, 127, "", "hermes: command not found"
        ),
    )
    result = run_agent(
        "hermes",
        project,
        "Instructions",
        "packet",
        SCHEMA,
        provider="openrouter",
        model="provider/model",
    )
    assert result.exit_code == 127
    assert "command not found" in result.error


def test_discovery_and_execution_use_same_capability_catalog():
    from stackos.host_mcp.result import HostMcpResult

    for definition in service._definitions():
        key = definition.policy.host_key
        info = service.normalize_result(
            HostMcpResult(
                key,
                "fixture",
                "registered_current",
                "Connected",
                True,
                True,
            ),
            definition.policy,
        ).to_info()
        capability = info["agent_execution"]
        assert info["connection_state"] == "connected"
        assert capability["credential_status"] == capability["model_access"] == "not_checked"
        assert capability["checked_on"] == "2026-11-05"
        assert capability["sources"]
        if definition.run_agent is not None:
            mode = service.agent_review_sandbox(key)
            assert capability["review_sandbox"] == mode
            assert mode == ("normal" if key in {"gemini-cli", "hermes"} else "workspace-write")
            if mode == "normal":
                assert "no enforced read-only isolation" in capability["notes"]
        for model in capability["models"]:
            assert (
                service.agent_selection_error(key, sandbox="workspace-write", model=model) is None
            )
        for sandbox in capability["sandboxes"]:
            selection = (
                {"provider": "openrouter", "model": "provider/model"} if key == "hermes" else {}
            )
            assert service.agent_selection_error(key, sandbox=sandbox, **selection) is None


@pytest.mark.parametrize("host", ["codex", "claude-code", "gemini-cli", "hermes", "grok"])
def test_plain_stdout_auth_failure_is_preserved_at_zero_exit(project, monkeypatch, host):
    fake_native_discovery(monkeypatch)
    monkeypatch.setattr(codex, "_select_codex_bin", lambda: "/fake/codex")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(
            command, 0, "No credentials for selected model", ""
        ),
    )
    selection = {"provider": "openrouter", "model": "provider/model"} if host == "hermes" else {}
    result = run_agent(host, project, "Instructions", "packet", SCHEMA, **selection)
    assert not result.ok
    assert "No credentials for selected model" in result.error
