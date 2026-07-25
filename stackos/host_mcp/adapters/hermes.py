"""Profile-scoped Hermes MCP and StackOS skill lifecycle adapter."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from stackos.host_mcp.bridge import (
    MCP_SERVER_NAME,
    daemon_preflight,
    is_stackos_bridge_command,
    resolve_bridge_command,
    token_preflight,
)
from stackos.host_mcp.discovery import resolve_cli_bin, subprocess_env_for_cli
from stackos.host_mcp.result import HostMcpResult, looks_secretish

HOST_KEY = "hermes"
SURFACE = "profile-config"
HERMES_BIN_ENV = "STACKOS_HERMES_BIN"
HERMES_HOME_ENV = "HERMES_HOME"
COMMON_HERMES_CLI_CANDIDATES = (
    "~/.local/bin/hermes",
    "~/bin/hermes",
    "/opt/homebrew/bin/hermes",
    "/usr/local/bin/hermes",
)
UNSAFE_TRANSPORT_FIELDS = frozenset({"url", "headers", "auth", "env"})


def inspect(
    home: Path,
    *,
    server_name: str = MCP_SERVER_NAME,
    profile: str | None = None,
) -> HostMcpResult:
    hermes_bin = resolve_hermes_bin()
    if hermes_bin is None:
        return _absent()
    if profile is not None:
        return _inspect_profile_with_bin(home, hermes_bin, _normalize_profile(profile), server_name)

    results = [
        _inspect_profile_with_bin(home, hermes_bin, candidate, server_name)
        for candidate in discover_profile_names(home)
    ]
    selected = [result for result in results if result.selected]
    if not selected:
        default = results[0] if results else _available_unregistered(None, None)
        return replace(
            default,
            message="Hermes is available. Choose a profile to connect to StackOS.",
            targets=[],
        )

    priority = {
        "registered_unsafe": 0,
        "registered_unmanaged": 1,
        "config_unreadable": 2,
        "registered_stale": 3,
        "registered_current": 4,
        "registered": 4,
    }
    representative = min(selected, key=lambda result: priority.get(result.status, 5))
    targets = [result.target for result in selected if result.target is not None]
    profile_labels = ", ".join(str(target["profile"]) for target in targets)
    if all(result.status in {"registered_current", "registered"} for result in selected):
        message = f"Hermes StackOS connection is healthy in: {profile_labels}."
    else:
        message = f"Hermes StackOS profile connections need attention in: {profile_labels}."
    return replace(representative, message=message, target=None, targets=targets)


def inspect_profile(
    home: Path,
    *,
    profile: str | None = None,
    server_name: str = MCP_SERVER_NAME,
) -> HostMcpResult:
    hermes_bin = resolve_hermes_bin()
    if hermes_bin is None:
        return _absent()
    result = _inspect_profile_with_bin(
        home,
        hermes_bin,
        _normalize_profile(profile),
        server_name,
    )
    if result.status == "config_unreadable":
        return replace(result, selected=True)
    return result


def register(
    home: Path,
    *,
    server_name: str = MCP_SERVER_NAME,
    profile: str | None = None,
) -> HostMcpResult:
    normalized_profile = _normalize_profile(profile)
    hermes_bin = resolve_hermes_bin()
    if hermes_bin is None:
        return _absent()
    if normalized_profile is not None and not _profile_home(home, normalized_profile).is_dir():
        return _profile_missing(normalized_profile)
    token_error = token_preflight(home)
    if token_error:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="token_missing",
            message=token_error,
            ok=False,
            available=True,
            blocking=True,
            repair="Run `stackos install` before connecting the selected Hermes profile.",
            target=_target(normalized_profile),
        )
    daemon_error = daemon_preflight()
    if daemon_error:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="register_failed",
            message=(
                f"StackOS must be running before Hermes can discover MCP tools. {daemon_error}"
            ),
            ok=False,
            available=True,
            blocking=True,
            repair="Start StackOS, then retry the selected Hermes profile.",
            target=_target(normalized_profile),
        )

    command = resolve_bridge_command(runtime=HOST_KEY)
    current = _inspect_profile_with_bin(
        home,
        hermes_bin,
        normalized_profile,
        server_name,
    )
    if current.status in {
        "config_unreadable",
        "registered_unmanaged",
        "registered_unsafe",
    }:
        return replace(current, selected=True, blocking=True)
    if current.selected and not current.managed:
        return current

    mcp_is_current = current.command == command
    if (
        current.status == "registered_stale"
        and not mcp_is_current
        and not _remove_profile_server(
            home,
            hermes_bin,
            normalized_profile,
            server_name,
        )
    ):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="remove_failed",
            message="Hermes could not remove the stale StackOS-owned profile entry.",
            ok=False,
            available=True,
            blocking=True,
            repair="Review the selected Hermes profile, then retry StackOS setup.",
            target=_target(normalized_profile),
            selected=True,
            managed=True,
        )

    if not mcp_is_current:
        added = _run_hermes(
            hermes_bin,
            [
                *_profile_args(normalized_profile),
                "mcp",
                "add",
                server_name,
                "--command",
                command[0],
                "--args",
                *command[1:],
            ],
            input_text="y\n",
        )
        if added.returncode != 0:
            return HostMcpResult(
                host_key=HOST_KEY,
                surface=SURFACE,
                status="register_failed",
                message="Hermes did not save the local StackOS MCP bridge.",
                ok=False,
                available=True,
                blocking=True,
                repair="Check `hermes mcp add --help`, then retry the selected profile.",
                target=_target(normalized_profile),
            )

    from stackos.install import copy_stackos_skill_to

    skill_target = _profile_home(home, normalized_profile) / "skills"
    try:
        copy_stackos_skill_to(skill_target)
    except OSError as exc:
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="register_failed",
            message=f"Hermes MCP connected, but the StackOS skill could not be installed: {exc}",
            ok=False,
            available=True,
            blocking=True,
            repair="Fix the selected Hermes profile permissions, then retry.",
            target=_target(normalized_profile),
            selected=True,
            managed=True,
        )

    verified = _inspect_profile_with_bin(
        home,
        hermes_bin,
        normalized_profile,
        server_name,
    )
    if verified.status != "registered_current":
        return verified
    verified_target = verified.target or _target(normalized_profile)
    return replace(
        verified,
        status="registered",
        message=(
            f"Connected the {verified_target['profile']} Hermes profile to StackOS "
            "and installed the canonical StackOS skill."
        ),
        target=verified_target,
    )


def repair(home: Path, *, server_name: str = MCP_SERVER_NAME) -> HostMcpResult:
    """Repair only already-selected, StackOS-managed Hermes profiles."""

    hermes_bin = resolve_hermes_bin()
    if hermes_bin is None:
        return _absent()
    for profile in discover_profile_names(home):
        current = _inspect_profile_with_bin(home, hermes_bin, profile, server_name)
        if current.selected and current.managed and current.status == "registered_stale":
            repaired = register(home, server_name=server_name, profile=profile)
            if repaired.status not in {"registered", "registered_current"}:
                return repaired
    return inspect(home, server_name=server_name)


def remove(
    home: Path,
    *,
    server_name: str = MCP_SERVER_NAME,
    profile: str | None = None,
) -> HostMcpResult:
    hermes_bin = resolve_hermes_bin()
    if hermes_bin is None:
        return _absent(message="Hermes was not detected; skipped StackOS removal.")
    profiles = (
        [_normalize_profile(profile)] if profile is not None else discover_profile_names(home)
    )
    removed_targets: list[dict[str, str]] = []
    for candidate in profiles:
        current = _inspect_profile_with_bin(home, hermes_bin, candidate, server_name)
        if profile is not None and current.status == "config_unreadable":
            return replace(current, selected=True, blocking=True)
        if not current.selected:
            continue
        if not current.managed:
            return current
        if not _remove_profile_server(home, hermes_bin, candidate, server_name):
            return HostMcpResult(
                host_key=HOST_KEY,
                surface=SURFACE,
                status="remove_failed",
                message="Hermes MCP removal failed for a selected profile.",
                ok=False,
                available=True,
                blocking=True,
                repair="Review the selected Hermes profile and retry removal.",
                target=_target(candidate),
                selected=True,
                managed=True,
            )
        try:
            shutil.rmtree(
                _profile_home(home, candidate) / "skills" / "stackos",
                ignore_errors=False,
            )
        except FileNotFoundError:
            pass
        except OSError as exc:
            return HostMcpResult(
                host_key=HOST_KEY,
                surface=SURFACE,
                status="remove_failed",
                message=(
                    "Hermes MCP was removed, but its managed StackOS skill "
                    f"could not be removed: {exc}"
                ),
                ok=False,
                available=True,
                blocking=True,
                repair="Fix the selected Hermes profile permissions and retry uninstall.",
                target=_target(candidate),
                selected=True,
                managed=True,
            )
        removed_targets.append(_target(candidate))
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="removed",
        message="Removed StackOS MCP from selected Hermes profiles.",
        ok=True,
        available=True,
        targets=removed_targets,
    )


def resolve_hermes_bin(hermes_bin: str | None = None) -> str | None:
    return resolve_cli_bin(
        "hermes",
        explicit=hermes_bin,
        env_var=HERMES_BIN_ENV,
        common_candidates=COMMON_HERMES_CLI_CANDIDATES,
    )


def discover_profile_names(home: Path) -> list[str | None]:
    profiles: list[str | None] = [None]
    profile_root = _hermes_root(home) / "profiles"
    if profile_root.is_dir():
        profiles.extend(
            path.name
            for path in sorted(profile_root.iterdir(), key=lambda item: item.name)
            if path.is_dir() and not path.name.startswith(".")
        )
    return profiles


def _inspect_profile_with_bin(
    home: Path,
    hermes_bin: str,
    profile: str | None,
    server_name: str,
) -> HostMcpResult:
    if profile is not None and not _profile_home(home, profile).is_dir():
        return _profile_missing(profile)
    command = resolve_bridge_command(runtime=HOST_KEY)
    entry, config_path, error = _read_profile_server(home, profile, server_name)
    if error:
        return _config_error(profile, config_path)
    if entry is None:
        return _available_unregistered(profile, config_path)
    target = _target(profile)
    actual_command = _entry_command(entry)
    if looks_secretish(entry) or not UNSAFE_TRANSPORT_FIELDS.isdisjoint(entry):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_unsafe",
            message="Hermes has an unsafe StackOS profile entry; StackOS left it unchanged.",
            ok=False,
            available=True,
            blocking=True,
            config_path=_path_text(config_path),
            repair="Review and remove the profile entry manually before reconnecting.",
            warnings=["unsafe Hermes MCP entry redacted"],
            target=target,
            selected=True,
            managed=False,
        )
    if actual_command == command:
        if not _profile_skill_ready(home, profile):
            return HostMcpResult(
                host_key=HOST_KEY,
                surface=SURFACE,
                status="registered_stale",
                message="Hermes MCP is connected, but the canonical StackOS skill is missing.",
                ok=False,
                available=True,
                blocking=True,
                config_path=_path_text(config_path),
                repair="Repair the selected Hermes profile to install the StackOS skill.",
                command=command,
                target=target,
                selected=True,
                managed=True,
            )
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_current",
            message=f"Hermes profile {target['profile']} is connected to StackOS.",
            ok=True,
            available=True,
            config_path=_path_text(config_path),
            command=command,
            target=target,
            selected=True,
            managed=True,
        )
    if actual_command is not None and is_stackos_bridge_command(actual_command):
        return HostMcpResult(
            host_key=HOST_KEY,
            surface=SURFACE,
            status="registered_stale",
            message="Hermes has a stale StackOS-owned profile connection.",
            ok=False,
            available=True,
            blocking=True,
            config_path=_path_text(config_path),
            repair="Repair the selected Hermes profile.",
            warnings=["stale Hermes MCP entry redacted"],
            command=actual_command,
            target=target,
            selected=True,
            managed=True,
        )
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="registered_unmanaged",
        message="Hermes has a stackos entry that StackOS does not own; it was left unchanged.",
        ok=False,
        available=True,
        blocking=True,
        config_path=_path_text(config_path),
        repair="Review the selected Hermes profile before connecting StackOS.",
        warnings=["unmanaged Hermes MCP entry redacted"],
        target=target,
        selected=True,
        managed=False,
    )


def _read_profile_server(
    home: Path,
    profile: str | None,
    server_name: str,
) -> tuple[dict[str, Any] | None, Path | None, str | None]:
    config_path = _hermes_config_path(home, profile)
    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, config_path, None
    except OSError:
        return None, config_path, "read"
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None, config_path, "parse"
    if loaded is None:
        return None, config_path, None
    if not isinstance(loaded, dict):
        return None, config_path, "root"
    servers = loaded.get("mcp_servers")
    if servers is None:
        return None, config_path, None
    if not isinstance(servers, dict):
        return None, config_path, "servers"
    entry = servers.get(server_name)
    if entry is None:
        return None, config_path, None
    if not isinstance(entry, dict):
        return None, config_path, "entry"
    return entry, config_path, None


def _hermes_config_path(home: Path, profile: str | None = None) -> Path:
    return _profile_home(home, profile) / "config.yaml"


def _run_hermes(
    hermes_bin: str,
    args: Sequence[str],
    *,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [hermes_bin, *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=subprocess_env_for_cli(hermes_bin),
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            [hermes_bin, *args],
            returncode=124,
            stdout=_text(exc.stdout),
            stderr=_text(exc.stderr) or "Hermes CLI timed out.",
        )
    except Exception as exc:
        return subprocess.CompletedProcess(
            [hermes_bin, *args],
            returncode=1,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}",
        )


def _remove_profile_server(
    home: Path,
    hermes_bin: str,
    profile: str | None,
    server_name: str,
) -> bool:
    removed = _run_hermes(
        hermes_bin,
        [*_profile_args(profile), "mcp", "remove", server_name],
        input_text="y\n",
    )
    if removed.returncode != 0:
        return False
    entry, _config_path, error = _read_profile_server(home, profile, server_name)
    return error is None and entry is None


def _entry_command(entry: dict[str, Any]) -> list[str] | None:
    command = entry.get("command")
    args = entry.get("args", [])
    enabled = entry.get("enabled", True)
    if not isinstance(command, str) or not isinstance(args, list) or enabled is False:
        return None
    if not all(isinstance(arg, str) for arg in args):
        return None
    return [command, *args]


def _profile_args(profile: str | None) -> list[str]:
    return ["-p", profile or "default"]


def _normalize_profile(profile: str | None) -> str | None:
    if profile is None:
        return None
    stripped = profile.strip()
    return None if stripped in {"", "default"} else stripped


def _hermes_root(home: Path) -> Path:
    configured = os.environ.get(HERMES_HOME_ENV)
    if not configured:
        return home / ".hermes"
    candidate = Path(configured).expanduser()
    if candidate.parent.name == "profiles":
        return candidate.parent.parent
    return candidate


def _profile_home(home: Path, profile: str | None) -> Path:
    root = _hermes_root(home)
    return root if profile is None else root / "profiles" / profile


def _profile_skill_ready(home: Path, profile: str | None) -> bool:
    return (_profile_home(home, profile) / "skills" / "stackos" / "SKILL.md").is_file()


def _target(profile: str | None) -> dict[str, str]:
    return {"kind": "profile", "profile": profile or "default"}


def _available_unregistered(
    profile: str | None,
    config_path: Path | None,
) -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="available_unregistered",
        message=f"Hermes profile {_target(profile)['profile']} is available to connect.",
        ok=False,
        available=True,
        blocking=False,
        config_path=_path_text(config_path),
        target=_target(profile),
        selected=False,
        managed=False,
    )


def _profile_missing(profile: str) -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="register_failed",
        message=f"Hermes profile {profile!r} does not exist; StackOS did not create it.",
        ok=False,
        available=True,
        blocking=True,
        repair=f"Create the Hermes profile first, then retry with profile {profile!r}.",
        target=_target(profile),
    )


def _config_error(profile: str | None, config_path: Path | None) -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="config_unreadable",
        message=f"Hermes profile {_target(profile)['profile']} could not be safely inspected.",
        ok=False,
        available=True,
        blocking=True,
        config_path=_path_text(config_path),
        repair="Fix the selected Hermes profile configuration, then retry.",
        target=_target(profile),
    )


def _absent(
    message: str = "Hermes was not detected. It is optional and was left unchanged.",
) -> HostMcpResult:
    return HostMcpResult(
        host_key=HOST_KEY,
        surface=SURFACE,
        status="absent",
        message=message,
        ok=True,
        available=False,
        advisory=True,
        repair="Install Hermes only if you want to connect a Hermes profile.",
    )


def _path_text(path: Path | None) -> str | None:
    return str(path) if path is not None else None


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)
