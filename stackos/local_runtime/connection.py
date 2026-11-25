"""Deliberate folder binding on this computer, using server-owned authorization."""

from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class LocalBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_url: str
    environment_id: str
    user_id: str
    project_id: int = Field(gt=0)
    connection_id: str
    credential: SecretStr
    root: str
    root_device: int
    root_inode: int


def server_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})
    ):
        raise ValueError("select an exact HTTPS server origin or isolated loopback HTTP origin")
    return value.rstrip("/")


def chosen_folder(folder: Path) -> tuple[Path, os.stat_result]:
    try:
        root = folder.expanduser().resolve(strict=True)
        info = root.stat()
    except OSError:
        raise ValueError("choose an existing local project folder") from None
    if not stat.S_ISDIR(info.st_mode) or root == Path(root.anchor):
        raise ValueError("choose an existing local project folder")
    return root, info


class LocalConnectionStore:
    """A private local config file, scoped by an explicitly selected state dir."""

    def __init__(self, state_dir: Path):
        self.path = state_dir.expanduser().absolute() / "connection.json"

    def _directory(self) -> None:
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = self.path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("runtime state directory must be private and owned by this user")

    def load(self) -> LocalBinding | None:
        self._directory()
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("runtime connection file must be private and owned by this user")
            # A malformed/incomplete record stops; it never silently reconnects.
            return LocalBinding.model_validate_json(stream.read())

    @contextmanager
    def reserve(self):
        self._directory()
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                yield stream
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            self.path.unlink()
            raise

    def require_folder(self, binding: LocalBinding, folder: Path) -> Path:
        root, info = chosen_folder(folder)
        if (str(root), info.st_dev, info.st_ino) != (
            binding.root,
            binding.root_device,
            binding.root_inode,
        ):
            raise ValueError("local folder binding changed; explicitly disconnect and choose again")
        return root

    def require_binding(self, binding: LocalBinding) -> Path:
        """Retain the exact admitted identity, including its opaque credential.

        Compare models rather than redacted serialization: two different secrets
        have the same display value. No binding field is exposed on refusal.
        """
        try:
            current = self.load()
        except (ValueError, OSError):
            raise ValueError(
                "local computer binding unavailable; reconciliation required"
            ) from None
        if current != binding:
            raise ValueError("local computer binding changed; reconciliation required")
        return self.require_folder(binding, Path(binding.root))


def _call(
    client, origin: str, name: str, arguments: dict[str, Any], credential: SecretStr | None = None
):
    headers = {"Origin": origin}
    if credential is not None:
        headers["Authorization"] = f"Bearer {credential.get_secret_value()}"
    response = client.post(
        f"{origin}/api/v1/operations/localRuntime.{name}/call",
        json={"arguments": arguments | {"response_mode": "raw"}},
        headers=headers,
        follow_redirects=False,
    )
    if response.status_code != 200:
        raise ValueError(f"computer authorization {name} refused (HTTP {response.status_code})")
    return response.json()


def check_connection(client, binding: LocalBinding) -> dict[str, Any]:
    result = _call(
        client,
        binding.server_url,
        "status",
        {
            "project_id": binding.project_id,
            "connection_id": binding.connection_id,
        },
        binding.credential,
    )
    if (
        any(
            result.get(key) != getattr(binding, key)
            for key in ("server_url", "environment_id", "user_id", "project_id", "connection_id")
        )
        or result.get("status") != "authorized"
    ):
        raise ValueError("computer authorization is stale or belongs to another identity")
    return result


def connect(
    client,
    store: LocalConnectionStore,
    *,
    server_url: str,
    environment_id: str,
    project_id: int,
    folder: Path,
) -> LocalBinding:
    """Use a signed-in StackOS client; require exact identity before any reuse."""
    origin = server_origin(server_url)
    root, info = chosen_folder(folder)
    session_response = client.get(
        f"{origin}/api/v1/auth/session", headers={"Origin": origin}, follow_redirects=False
    )
    if session_response.status_code != 200:
        raise ValueError("sign in to the selected StackOS server first")
    identity = session_response.json()
    if identity.get("environment_id") != environment_id or not identity.get("user_id"):
        raise ValueError("signed-in environment differs from the selected environment")
    binding = store.load()
    if binding is not None:
        expected = (origin, environment_id, identity["user_id"], project_id)
        actual = (binding.server_url, binding.environment_id, binding.user_id, binding.project_id)
        if actual != expected:
            raise ValueError("existing local binding belongs to another owner/environment/project")
        store.require_folder(binding, root)
        check_connection(client, binding)
        return binding
    with store.reserve() as stream:
        issued = _call(client, origin, "authorize", {"project_id": project_id})
        binding = LocalBinding(
            **{
                key: issued[key]
                for key in (
                    "server_url",
                    "environment_id",
                    "user_id",
                    "project_id",
                    "connection_id",
                    "credential",
                )
            },
            root=str(root),
            root_device=info.st_dev,
            root_inode=info.st_ino,
        )
        try:
            if (
                binding.server_url,
                binding.environment_id,
                binding.user_id,
                binding.project_id,
            ) != (origin, environment_id, identity["user_id"], project_id):
                raise ValueError("server authorization identity changed during connection")
            store.require_folder(binding, root)
            check_connection(client, binding)
            payload = binding.model_dump(mode="json")
            payload["credential"] = binding.credential.get_secret_value()
            json.dump(payload, stream)
        except BaseException:
            _call(
                client,
                origin,
                "revoke",
                {
                    "project_id": binding.project_id,
                    "connection_id": binding.connection_id,
                },
                binding.credential,
            )
            raise
    return binding


def disconnect(client, store: LocalConnectionStore) -> None:
    binding = store.load()
    if binding is None:
        return
    # The owner's session may revoke an already-revoked connection. Use it when
    # available; never erase local identity on unconfirmed network failure.
    _call(
        client,
        binding.server_url,
        "revoke",
        {
            "project_id": binding.project_id,
            "connection_id": binding.connection_id,
        },
    )
    store.path.unlink()
