"""`scripts/install-claude.sh` mirrors `skills/` into a sandbox HOME.

Mirror of `test_install_codex_idempotent.py`; we keep the two files
separate so a regression in one runtime does not silently mask the
other.
"""

from __future__ import annotations

import filecmp
import os
import subprocess
from pathlib import Path


def _run(script: Path, home: Path) -> str:
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={**os.environ, "STACKOS_HOME": str(home)},
        check=True,
    )
    return result.stdout


def _snapshot(target: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(target)): p.read_bytes() for p in sorted(target.rglob("*")) if p.is_file()
    }


def test_install_claude_creates_target(
    sandbox_home: Path, scripts_dir: Path, repo_root: Path
) -> None:
    target = sandbox_home / ".claude" / "skills" / "stackos"
    assert not target.exists()
    output = _run(scripts_dir / "install-claude.sh", sandbox_home)
    expected = sum(1 for _ in (repo_root / "skills").rglob("SKILL.md"))
    assert f"Installed {expected} skills" in output


def test_install_claude_idempotent(sandbox_home: Path, scripts_dir: Path) -> None:
    _run(scripts_dir / "install-claude.sh", sandbox_home)
    target = sandbox_home / ".claude" / "skills" / "stackos"
    snap1 = _snapshot(target)
    _run(scripts_dir / "install-claude.sh", sandbox_home)
    snap2 = _snapshot(target)
    assert snap1 == snap2


def test_install_claude_matches_source(
    sandbox_home: Path, scripts_dir: Path, repo_root: Path
) -> None:
    _run(scripts_dir / "install-claude.sh", sandbox_home)
    target = sandbox_home / ".claude" / "skills" / "stackos"
    cmp = filecmp.dircmp(str(repo_root / "skills"), str(target))
    assert cmp.left_only == [] or cmp.left_only == [".DS_Store"]
    assert cmp.right_only == []
    assert cmp.diff_files == []
