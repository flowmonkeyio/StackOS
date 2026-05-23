"""Shared fixtures for distribution/install tests.

Each test runs in a sandbox HOME so the install scripts target a tmp
directory rather than the operator's real `~/.codex` or `~/.claude`.
The bash scripts honour `STACKOS_HOME` exactly to make the
sandboxing surgical.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"


@pytest.fixture
def sandbox_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a sandboxed `${HOME}` with a fake auth token in place.

    The fixture pre-creates `~/.local/state/stackos/auth.token`
    (mode 0600) so the registration scripts can read it without booting
    the real daemon. Tests that need the file absent should ``unlink``
    it inside the test body.
    """
    home = tmp_path / "home"
    home.mkdir()
    state_dir = home / ".local" / "state" / "stackos"
    state_dir.mkdir(parents=True)
    token_path = state_dir / "auth.token"
    token_path.write_text("test-token-deadbeef\n", encoding="utf-8")
    os.chmod(token_path, 0o600)

    monkeypatch.setenv("STACKOS_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the StackOS repo for source-of-truth checks."""
    return REPO_ROOT


@pytest.fixture
def scripts_dir() -> Path:
    """Absolute path to the scripts directory under test."""
    return SCRIPTS_DIR
