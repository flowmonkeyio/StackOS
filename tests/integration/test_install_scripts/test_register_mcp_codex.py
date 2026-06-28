"""`scripts/register-mcp-codex.sh` upserts the `stackos` MCP entry.

Codex CLI may not be installed on a given machine; the script must
exit 0 with a notice in that case (audit B-24 — "skipped: codex CLI
not on PATH" branch). When a stub `codex` is on PATH, two runs must
produce the same end state.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


def _run(
    script: Path, home: Path, extra_path: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "STACKOS_HOME": str(home)}
    if extra_path is not None:
        env["PATH"] = f"{extra_path}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def codex_stub(tmp_path: Path) -> Path:
    """Install a fake `codex` binary onto a tmp PATH dir.

    The stub records every invocation to ``invocations.log`` so tests
    can assert on the exact CLI calls without mocking subprocess.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "invocations.log"
    state = tmp_path / "registered"
    script = bin_dir / "codex"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        f'STATE="{state}"\n'
        'case "$1 $2" in\n'
        + '  "mcp list") if [[ -f "$STATE" ]]; then cat "$STATE"; fi ;;\n'
        + '  "mcp add") printf "%s " "$3" "${@:5}" > "$STATE"; printf "\\n" >> "$STATE" ;;\n'
        + '  "mcp remove") rm -f "$STATE" ;;\n'
        + '  *) echo "unknown: $@" >&2; exit 2 ;;\n'
        + "esac\n",
        encoding="utf-8",
    )
    script.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return bin_dir


def test_no_codex_on_path_is_skipped(sandbox_home: Path, scripts_dir: Path, tmp_path: Path) -> None:
    # Pin PATH to a minimal set that contains `bash` but explicitly NOT `codex`.
    # We compute a parent-of-bash dir and pair it with a scratch dir.
    empty_path = tmp_path / "empty"
    empty_path.mkdir()
    bash_dir = "/bin"
    if not Path(bash_dir, "bash").exists():
        bash_dir = "/usr/bin"
    env = {
        **os.environ,
        "STACKOS_HOME": str(sandbox_home),
        "PATH": f"{empty_path}{os.pathsep}{bash_dir}",
    }
    result = subprocess.run(
        ["bash", str(scripts_dir / "register-mcp-codex.sh")],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "Codex CLI not on PATH" in result.stdout


def test_register_then_idempotent(sandbox_home: Path, scripts_dir: Path, codex_stub: Path) -> None:
    log = codex_stub.parent / "invocations.log"
    first = _run(scripts_dir / "register-mcp-codex.sh", sandbox_home, codex_stub)
    assert first.returncode == 0, first.stderr
    assert "Registered MCP" in first.stdout

    invocations_after_first = log.read_text(encoding="utf-8").splitlines()
    add_calls = [line for line in invocations_after_first if line.startswith("mcp add")]
    assert len(add_calls) == 1, "first run should call `mcp add` exactly once"
    assert "--url" not in add_calls[0]
    assert "mcp-bridge" in add_calls[0]

    second = _run(scripts_dir / "register-mcp-codex.sh", sandbox_home, codex_stub)
    assert second.returncode == 0
    assert "already registered" in second.stdout

    invocations_after_second = log.read_text(encoding="utf-8").splitlines()
    add_calls_after = [line for line in invocations_after_second if line.startswith("mcp add")]
    assert len(add_calls_after) == 1, "second run must NOT re-add"


def test_remove_flag(sandbox_home: Path, scripts_dir: Path, codex_stub: Path) -> None:
    _run(scripts_dir / "register-mcp-codex.sh", sandbox_home, codex_stub)
    result = _run(scripts_dir / "register-mcp-codex.sh --remove", sandbox_home, codex_stub)
    # Bash treats arguments literally — re-issue with explicit args:
    result = subprocess.run(
        ["bash", str(scripts_dir / "register-mcp-codex.sh"), "--remove"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "STACKOS_HOME": str(sandbox_home),
            "PATH": f"{codex_stub}{os.pathsep}{os.environ['PATH']}",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "Unregistered" in result.stdout


def test_force_reregisters(sandbox_home: Path, scripts_dir: Path, codex_stub: Path) -> None:
    log = codex_stub.parent / "invocations.log"
    _run(scripts_dir / "register-mcp-codex.sh", sandbox_home, codex_stub)
    invocations_after_first = log.read_text(encoding="utf-8").splitlines()
    add_count_first = sum(1 for line in invocations_after_first if line.startswith("mcp add"))

    result = subprocess.run(
        ["bash", str(scripts_dir / "register-mcp-codex.sh"), "--force"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "STACKOS_HOME": str(sandbox_home),
            "PATH": f"{codex_stub}{os.pathsep}{os.environ['PATH']}",
        },
    )
    assert result.returncode == 0, result.stderr
    invocations_after_force = log.read_text(encoding="utf-8").splitlines()
    add_count_after = sum(1 for line in invocations_after_force if line.startswith("mcp add"))
    assert add_count_after == add_count_first + 1


def test_stale_entry_is_repaired(sandbox_home: Path, scripts_dir: Path, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "invocations.log"
    state = tmp_path / "registered"
    state.write_text("stale\n", encoding="utf-8")
    script = bin_dir / "codex"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        f'STATE="{state}"\n'
        'case "$1 $2" in\n'
        '  "mcp list") if [[ -f "$STATE" ]]; then '
        'echo "stackos --url http://127.0.0.1:5180/mcp"; fi ;;\n'
        '  "mcp add") echo "current" > "$STATE" ;;\n'
        '  "mcp remove") rm -f "$STATE" ;;\n'
        '  *) echo "unknown: $@" >&2; exit 2 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)

    result = _run(scripts_dir / "register-mcp-codex.sh", sandbox_home, bin_dir)

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("mcp remove stackos") for line in calls)
    assert any(line.startswith("mcp add stackos") for line in calls)


def test_register_fails_without_token(
    sandbox_home: Path, scripts_dir: Path, codex_stub: Path
) -> None:
    token_path = sandbox_home / ".local" / "state" / "stackos" / "auth.token"
    token_path.unlink()

    result = subprocess.run(
        ["bash", str(scripts_dir / "register-mcp-codex.sh")],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "STACKOS_HOME": str(sandbox_home),
            "PATH": f"{codex_stub}{os.pathsep}{os.environ['PATH']}",
        },
    )
    assert result.returncode == 1
    assert "auth token missing" in result.stderr


# Silence the unused-fixture-import warning when ``shutil`` is not used directly.
_ = shutil
