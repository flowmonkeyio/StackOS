"""`scripts/install-launchd.sh` delegates launchd setup to the CLI.

We can't actually `launchctl bootstrap` in the test runner, so we shim `launchctl`
with a stub on PATH that records every invocation. The plist itself
must:
  - use the Python module daemon entrypoint,
  - include the operator home and daemon log path,
  - be idempotent on re-run with identical content,
  - honor `--force` when the plist differs from the generated one,
  - boot out and remove the plist on `--uninstall`.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def launchctl_stub(tmp_path: Path) -> Path:
    """Install a fake `launchctl` that records but never errors."""
    bin_dir = tmp_path / "stubs"
    bin_dir.mkdir()
    log = tmp_path / "launchctl.log"
    script = bin_dir / "launchctl"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        'case "$1" in\n'
        "  print) exit 1 ;;  # always report 'not loaded' so bootstrap path runs\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return bin_dir


def _run(
    scripts_dir: Path,
    home: Path,
    launchctl_dir: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(scripts_dir / "install-launchd.sh"), *args],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "STACKOS_HOME": str(home),
            "PATH": f"{launchctl_dir}{os.pathsep}{os.environ['PATH']}",
        },
    )


def test_writes_plist_with_substitutions(
    sandbox_home: Path, scripts_dir: Path, launchctl_stub: Path, repo_root: Path
) -> None:
    _ = repo_root
    result = _run(scripts_dir, sandbox_home, launchctl_stub)
    assert result.returncode == 0, result.stderr
    plist = sandbox_home / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    assert plist.is_file()
    content = plist.read_text(encoding="utf-8")
    # The CLI owns the plist and runs the package module directly.
    assert "<string>-m</string>" in content
    assert "<string>stackos</string>" in content
    assert "<string>serve</string>" in content
    assert str(sandbox_home) in content
    assert "auth.token" not in content
    assert "seed.bin" not in content
    assert "Authorization" not in content
    assert "Bearer" not in content
    assert "STACKOS_TOKEN" not in content
    # Label is the stable identifier we'll boot out later.
    assert "<string>com.stackos.daemon</string>" in content


def test_idempotent_no_op(sandbox_home: Path, scripts_dir: Path, launchctl_stub: Path) -> None:
    _run(scripts_dir, sandbox_home, launchctl_stub)
    plist = sandbox_home / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    first = plist.read_bytes()

    second = _run(scripts_dir, sandbox_home, launchctl_stub)
    assert second.returncode == 0
    assert "already current" in second.stdout
    assert plist.read_bytes() == first


def test_force_overwrites_with_bak(
    sandbox_home: Path, scripts_dir: Path, launchctl_stub: Path
) -> None:
    plist_dir = sandbox_home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist = plist_dir / "com.stackos.daemon.plist"
    plist.write_text("<plist><dict>obsolete</dict></plist>\n", encoding="utf-8")

    result = _run(scripts_dir, sandbox_home, launchctl_stub, "--force")
    assert result.returncode == 0, result.stderr
    bak = plist.with_suffix(plist.suffix + ".bak")
    assert bak.is_file()
    assert bak.read_text(encoding="utf-8") == "<plist><dict>obsolete</dict></plist>\n"
    # New plist no longer matches the obsolete content.
    assert "obsolete" not in plist.read_text(encoding="utf-8")


def test_diff_without_force_aborts_in_non_tty(
    sandbox_home: Path, scripts_dir: Path, launchctl_stub: Path
) -> None:
    plist_dir = sandbox_home / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist = plist_dir / "com.stackos.daemon.plist"
    plist.write_text("<plist><dict>different</dict></plist>\n", encoding="utf-8")

    result = _run(scripts_dir, sandbox_home, launchctl_stub)
    assert result.returncode != 0
    assert "differs" in result.stderr
    # Plist remains untouched.
    assert "different" in plist.read_text(encoding="utf-8")


def test_uninstall_removes_plist(
    sandbox_home: Path, scripts_dir: Path, launchctl_stub: Path
) -> None:
    _run(scripts_dir, sandbox_home, launchctl_stub)
    plist = sandbox_home / "Library" / "LaunchAgents" / "com.stackos.daemon.plist"
    assert plist.is_file()

    result = _run(scripts_dir, sandbox_home, launchctl_stub, "--uninstall")
    assert result.returncode == 0, result.stderr
    assert not plist.exists()


def test_uninstall_when_absent(sandbox_home: Path, scripts_dir: Path, launchctl_stub: Path) -> None:
    result = _run(scripts_dir, sandbox_home, launchctl_stub, "--uninstall")
    assert result.returncode == 0
    assert "nothing to do" in result.stdout
