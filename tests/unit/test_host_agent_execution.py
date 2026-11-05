from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from stackos.host_mcp import AgentRunResult, run_agent, service
from stackos.host_mcp.adapters import codex

SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "working project"
    root.mkdir()
    return root


def test_codex_runs_supplied_instructions_without_agent_files(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    result = run_agent("codex", project, instructions, "Deliver this ticket.", SCHEMA)

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
    assert args[:5] == ["exec", "--json", "--cd", str(project), "--approve-for-me"]
    assert "--sandbox" not in args
    assert args[-1] == "-"
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
