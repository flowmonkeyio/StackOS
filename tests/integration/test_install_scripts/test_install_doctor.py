"""Post-install doctor wrapper semantics."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


def _fake_doctor(tmp_path: Path, code: int) -> Path:
    script = tmp_path / f"doctor-{code}.sh"
    script.write_text(
        f"#!/usr/bin/env bash\necho doctor exit {code}\nexit {code}\n",
        encoding="utf-8",
    )
    script.chmod(stat.S_IRWXU)
    return script


def _run(scripts_dir: Path, fake_doctor: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(scripts_dir / "install-doctor.sh")],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CONTENT_STACK_DOCTOR_SCRIPT": str(fake_doctor),
        },
    )


def test_install_doctor_accepts_healthy_doctor(scripts_dir: Path, tmp_path: Path) -> None:
    result = _run(scripts_dir, _fake_doctor(tmp_path, 0))

    assert result.returncode == 0
    assert "doctor exit 0" in result.stdout


def test_install_doctor_tolerates_daemon_down(scripts_dir: Path, tmp_path: Path) -> None:
    result = _run(scripts_dir, _fake_doctor(tmp_path, 1))

    assert result.returncode == 0
    assert "doctor exit 1" in result.stdout
    assert "daemon is not running yet" in result.stdout


def test_install_doctor_preserves_blocking_failures(
    scripts_dir: Path,
    tmp_path: Path,
) -> None:
    result = _run(scripts_dir, _fake_doctor(tmp_path, 8))

    assert result.returncode == 8
    assert "doctor exit 8" in result.stdout
    assert "blocking setup issue" in result.stderr
