"""Private API-key loading delegates to the existing desktop release commands."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "desktop-release.py"
SPEC = importlib.util.spec_from_file_location("desktop_release", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def write_local_env(root: Path, *, complete: bool = True) -> None:
    lines = [
        "APPLE_API_KEY='/private/synthetic AuthKey.p8'",
        "APPLE_API_KEY_ID=synthetic-key-id",
        "CSC_NAME='Local Identity (TEAM)'",
        "APPLE_KEYCHAIN_PROFILE=obsolete-profile",
        "APPLE_ID=unused@example.test",
        "APPLE_APP_SPECIFIC_PASSWORD=synthetic-password",
        "APPLE_TEAM_ID=OLDTEAM",
        "UNRELATED_TOKEN=must-not-be-exported",
        "STACKOS_UPDATE_URL=https://ignored-file.example.test/",
    ]
    if complete:
        lines.append("APPLE_API_ISSUER=synthetic-issuer")
    (root / ".env.local").write_text("\n".join(lines))


def test_private_api_loading_is_selective_and_caller_identity_wins(tmp_path: Path) -> None:
    write_local_env(tmp_path)
    inherited = {
        "PATH": "/usr/bin",
        "CSC_NAME": "Selected Identity (TEAM)",
        "STACKOS_UPDATE_URL": "https://release.example.test/StackOS/",
        **{key: "1" for key in release.BYPASS_NAMES},
        "APPLE_KEYCHAIN_PROFILE": "stale-inherited-profile",
    }
    result = release.release_environment(tmp_path, inherited)
    assert result["APPLE_API_KEY"] == "/private/synthetic AuthKey.p8"
    assert result["APPLE_API_KEY_ID"] == "synthetic-key-id"
    assert result["APPLE_API_ISSUER"] == "synthetic-issuer"
    assert result["CSC_NAME"] == inherited["CSC_NAME"]
    assert result["STACKOS_UPDATE_URL"] == inherited["STACKOS_UPDATE_URL"]
    assert result["PATH"] == inherited["PATH"]
    assert "UNRELATED_TOKEN" not in result
    assert not set(release.COMPETING_NOTARY_NAMES + release.BYPASS_NAMES) & result.keys()
    assert result["STACKOS_REQUIRE_SIGNING"] == "1"
    assert result["STACKOS_REQUIRE_UPDATE_URL"] == "1"
    assert inherited["APPLE_KEYCHAIN_PROFILE"] == "stale-inherited-profile"


def test_missing_file_uses_complete_caller_api_environment(tmp_path: Path) -> None:
    inherited = dict.fromkeys(release.API_KEY_NAMES, "synthetic-caller-value")
    assert (
        release.release_environment(tmp_path, inherited)["APPLE_API_KEY"]
        == inherited["APPLE_API_KEY"]
    )


def test_dotenv_values_are_literal_data(tmp_path: Path) -> None:
    write_local_env(tmp_path)
    with (tmp_path / ".env.local").open("a") as stream:
        stream.write("\nCSC_LINK='$(touch should-not-exist) ${UNRELATED_TOKEN}'\n")
    result = release.release_environment(tmp_path, {})
    assert result["CSC_LINK"] == "$(touch should-not-exist) ${UNRELATED_TOKEN}"
    assert not (tmp_path / "should-not-exist").exists()


@pytest.mark.parametrize("file_present", [False, True])
def test_missing_api_input_refuses_profile_fallback_without_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    file_present: bool,
) -> None:
    if file_present:
        write_local_env(tmp_path, complete=False)
    monkeypatch.setattr(release, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        release.os, "environ", {"APPLE_KEYCHAIN_PROFILE": "synthetic-secret-profile"}
    )
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **k: pytest.fail("must not dispatch"))
    assert release.main(["preflight"]) == 2
    output = capsys.readouterr()
    assert "APPLE_API_ISSUER" in output.err
    assert "synthetic" not in output.err and not output.out


@pytest.mark.parametrize(
    "mode,script", [("preflight", "release:preflight"), ("build", "dist:mac:release")]
)
def test_fixed_command_delegation_preserves_validation_flags_and_child_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    mode: str,
    script: str,
) -> None:
    write_local_env(tmp_path)
    monkeypatch.setattr(release, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        release.os,
        "environ",
        {
            "STACKOS_PREFLIGHT_VALIDATE_NOTARY": "1",
            "STACKOS_DESKTOP_BUILD_DRY_RUN": "1",
        },
    )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(release.subprocess, "run", run)
    assert release.main([mode]) == 7
    command, kwargs = calls[0]
    assert command == ["pnpm", "--dir", "desktop", "run", script]
    assert kwargs["cwd"] == tmp_path and kwargs["check"] is False
    assert "shell" not in kwargs
    assert kwargs["env"]["STACKOS_PREFLIGHT_VALIDATE_NOTARY"] == "1"
    assert kwargs["env"]["STACKOS_DESKTOP_BUILD_DRY_RUN"] == "1"
    assert capsys.readouterr() == ("", "")


def test_configuration_access_failure_does_not_print_source_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    write_local_env(tmp_path)
    monkeypatch.setattr(release, "REPO_ROOT", tmp_path)

    def fail(*args, **kwargs):
        raise PermissionError("synthetic-secret-path")

    monkeypatch.setattr(release, "dotenv_values", fail)
    assert release.main(["preflight"]) == 2
    assert "synthetic-secret-path" not in capsys.readouterr().err


def test_wrapper_process_loads_private_file_and_delegates_without_leaking(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "desktop-release.py"
    script.write_bytes(SCRIPT.read_bytes())
    write_local_env(tmp_path)
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    pnpm = executable_dir / "pnpm"
    pnpm.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "assert sys.argv[1:] == ['--dir', 'desktop', 'run', 'release:preflight']\n"
        "assert os.path.isfile('.env.local')\n"
        "assert os.environ['APPLE_API_KEY_ID'] == 'synthetic-key-id'\n"
        "assert 'UNRELATED_TOKEN' not in os.environ\n"
        "assert 'APPLE_KEYCHAIN_PROFILE' not in os.environ\n"
        "assert os.environ['STACKOS_REQUIRE_SIGNING'] == '1'\n"
        "print('synthetic delegation verified')\n"
        "sys.exit(9)\n"
    )
    pnpm.chmod(0o700)
    result = subprocess.run(
        [sys.executable, str(script), "preflight"],
        cwd=scripts,
        env={"PATH": str(executable_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 9
    assert result.stdout == "synthetic delegation verified\n" and result.stderr == ""
