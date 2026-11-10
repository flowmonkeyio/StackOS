"""Private native-client custody of the selected StackOS session, never Supabase tokens."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr

from stackos.config import Settings
from stackos.local_runtime.connection import LocalConnectionStore, check_connection, server_origin


class NativeSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_url: str
    environment_id: str
    user_id: str
    cookie: SecretStr
    csrf_token: SecretStr


class ServerSession:
    def __init__(self, settings: Settings):
        if settings.process_role not in {"runtime", "server"} or not settings.server_url:
            raise ValueError("select an online environment file first")
        self.settings = settings
        self.origin = server_origin(settings.server_url)
        self.path = settings.state_dir / "client-session.json"

    def load(self) -> NativeSession:
        LocalConnectionStore(self.settings.state_dir)._directory()
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd) as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                ):
                    raise ValueError("client session must be private and owned by this user")
                saved = NativeSession.model_validate_json(stream.read())
        except FileNotFoundError:
            raise ValueError("sign in with `stackos login` for the selected environment") from None
        except ValueError:
            raise ValueError("invalid private client session; sign in explicitly") from None
        if (saved.server_url, saved.environment_id) != (self.origin, self.settings.environment_id):
            raise ValueError("client session belongs to another server or environment")
        return saved

    def save(self, client: httpx.Client, identity: dict) -> None:
        if identity.get("environment_id") != self.settings.environment_id or not identity.get(
            "user_id"
        ):
            raise ValueError("signed-in environment differs from the selected environment")
        # Keep only the opaque StackOS session cookie issued by this response.
        from stackos.auth import session_cookie_name

        name = session_cookie_name(self.settings)
        value = client.cookies.get(name)
        if not value or not identity.get("csrf_token"):
            raise ValueError("selected server did not issue a StackOS session")
        saved = NativeSession(
            server_url=self.origin,
            environment_id=identity["environment_id"],
            user_id=identity["user_id"],
            cookie=SecretStr(f"{name}={value}"),
            csrf_token=SecretStr(identity["csrf_token"]),
        )
        # Reuse the connection store's private-directory boundary.
        LocalConnectionStore(self.settings.state_dir)._directory()
        fd, temporary = tempfile.mkstemp(prefix=".client-session-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                payload = saved.model_dump(mode="json")
                payload["cookie"] = saved.cookie.get_secret_value()
                payload["csrf_token"] = saved.csrf_token.get_secret_value()
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def headers(
        self, *, require_connection: bool = False, folder: Path | None = None
    ) -> dict[str, str]:
        saved = self.load()
        headers = {
            "Origin": self.origin,
            "Cookie": saved.cookie.get_secret_value(),
            "X-CSRF-Token": saved.csrf_token.get_secret_value(),
        }
        with httpx.Client(headers=headers, timeout=15, follow_redirects=False) as client:
            response = client.get(f"{self.origin}/api/v1/auth/session")
            if response.status_code != 200:
                raise ValueError("server session expired or unavailable; sign in explicitly")
            identity = response.json()
            if identity.get("status") != "authenticated":
                raise ValueError("complete account recovery and sign in explicitly")
            if any(
                identity.get(key) != getattr(saved, key) for key in ("environment_id", "user_id")
            ):
                raise ValueError("selected server session identity changed")
            if require_connection:
                store = LocalConnectionStore(self.settings.state_dir)
                binding = store.load()
                if binding is None:
                    raise ValueError(
                        "connect this computer and existing folder with `stackos connect`"
                    )
                if any(
                    getattr(binding, key) != getattr(saved, key)
                    for key in ("server_url", "environment_id", "user_id")
                ):
                    raise ValueError("computer binding differs from the signed-in identity")
                store.require_folder(binding, folder or Path.cwd())
                check_connection(client, binding)
        return headers
