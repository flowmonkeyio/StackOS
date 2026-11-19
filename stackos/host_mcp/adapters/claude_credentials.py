"""Claude's native subscription storage, kept private to the account adapter."""

from __future__ import annotations

import json
import os
import pwd
import re
import subprocess
import sys
import unicodedata
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from stackos.host_mcp.accounts import _private_fd


class ClaudeCredentialError(Exception):
    """Safe native-store diagnostic; never include native payloads."""


def store_directory(env: dict[str, str]) -> tuple[Path, str | None]:
    raw = env.get("CLAUDE_SECURESTORAGE_CONFIG_DIR", env.get("CLAUDE_CONFIG_DIR"))
    home = Path(env.get("HOME") or Path.home())
    return (Path(raw).expanduser(), raw) if raw else (home / ".claude", None)


def keychain_target(env: dict[str, str]) -> tuple[str, str]:
    _, raw = store_directory(env)
    service = "Claude Code-credentials"
    if raw:
        service += "-" + sha256(unicodedata.normalize("NFC", raw).encode()).hexdigest()[:8]
    account = env.get("USER", pwd.getpwuid(os.getuid()).pw_name)
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", account):
        account = "claude-code-user"
    return service, account


def _security(args: list[str], *, payload: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["/usr/bin/security", *args],
            input=payload,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ClaudeCredentialError("Claude native Keychain is unavailable.") from None


def read_object(path: Path) -> dict:
    try:
        fd = _private_fd(path, os.O_RDONLY)
        with os.fdopen(fd, encoding="utf-8") as source:
            value = json.load(source)
        if isinstance(value, dict):
            return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        pass
    raise ClaudeCredentialError("Claude native credential file is unreadable.")


def write_object(path: Path, value: dict) -> None:
    # Validate an existing target before replacing it; reject links and foreign files.
    if path.exists() or path.is_symlink():
        fd = _private_fd(path, os.O_RDONLY)
        os.close(fd)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        staged = Path(out.name)
        try:
            json.dump(value, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def read_store(env: dict[str, str]) -> tuple[dict, str]:
    if sys.platform == "darwin":
        service, account = keychain_target(env)
        result = _security(["find-generic-password", "-a", account, "-s", service, "-w"])
        if result.returncode == 0:
            try:
                value = json.loads(result.stdout)
                if isinstance(value, dict):
                    return value, "keychain"
            except ValueError:
                pass
            raise ClaudeCredentialError("Claude native Keychain credentials are unreadable.")
        if result.returncode != 44:
            raise ClaudeCredentialError("Claude native Keychain is locked or unavailable.")
    directory, _ = store_directory(env)
    path = directory / ".credentials.json"
    if path.exists() or path.is_symlink() or sys.platform != "darwin":
        return read_object(path), "file"
    return {}, "keychain"


def write_store(env: dict[str, str], value: dict, backend: str) -> None:
    if backend == "file":
        directory, _ = store_directory(env)
        write_object(directory / ".credentials.json", value)
        return
    service, account = keychain_target(env)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode().hex()
    # security's interactive command keeps credentials out of process arguments.
    command = f'add-generic-password -U -a "{account}" -s "{service}" -X {encoded}\n'
    if len(command) > 4032:
        raise ClaudeCredentialError("Claude credentials exceed the native Keychain write limit.")
    result = _security(["-i"], payload=command)
    if result.returncode != 0 or result.stderr.strip():
        raise ClaudeCredentialError("Claude native Keychain credential write failed.")
