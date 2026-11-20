"""Exercise dependency preparation through the real Make targets."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


@pytest.fixture
def ui_commands(tmp_path, monkeypatch):
    (tmp_path / "ui").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pnpm = bin_dir / "pnpm"
    pnpm.write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$UI_COMMAND_LOG"\n'
        'if [ "$1" = install ]; then\n'
        '  case " $* " in *" --silent "*) exit 37;; esac\n'
        '  echo "Lockfile is up to date"\n'
        '  if [ "${UI_INSTALL_EXIT:-0}" -ne 0 ]; then\n'
        '    echo "ERR_PNPM_FETCH_403: registry denied package download" >&2\n'
        '    exit "$UI_INSTALL_EXIT"\n'
        "  fi\n"
        "fi\n",
        encoding="utf-8",
    )
    pnpm.chmod(0o755)
    log = tmp_path / "commands.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("UI_COMMAND_LOG", str(log))
    monkeypatch.delenv("UI_INSTALL_EXIT", raising=False)
    return tmp_path, log


def run_make(root, *targets):
    return subprocess.run(
        ["make", "-j1", "-f", str(MAKEFILE), *targets],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_ui_dependency_failure_preserves_diagnostics_and_stops_consumers(ui_commands, monkeypatch):
    root, log = ui_commands
    monkeypatch.setenv("UI_INSTALL_EXIT", "37")

    result = run_make(root, "test-ui-unit", "test-ui-e2e", "build-ui")

    assert result.returncode != 0
    assert "Lockfile is up to date" in result.stdout
    assert "ERR_PNPM_FETCH_403: registry denied package download" in result.stderr
    assert "exit 37" in result.stderr
    commands = log.read_text().splitlines()
    assert len(commands) == 1  # No retry or consuming check after failed setup.
    assert commands[0].startswith("install --frozen-lockfile")
    assert "--silent" not in commands[0]


def test_ui_targets_share_one_successful_locked_preparation(ui_commands):
    root, log = ui_commands

    result = run_make(root, "test-ui-unit", "test-ui-e2e", "build-ui")

    assert result.returncode == 0, result.stderr
    commands = log.read_text().splitlines()
    assert len(commands) == 4
    assert commands[0].startswith("install --frozen-lockfile")
    assert commands[1:] == ["test", "e2e", "build"]
    assert "Lockfile is up to date" in result.stdout


def test_ui_preparation_without_ui_is_explicit(tmp_path):
    result = run_make(tmp_path, "ui-deps")

    assert result.returncode == 0
    assert "ui/ not available in this checkout" in result.stdout
