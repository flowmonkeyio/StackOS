"""Runtime configuration — XDG paths, daemon port, log level.

Centralised so every subsystem (server, CLI, doctor, migrations) reads the
same source of truth. Loopback-only enforcement happens in `BaseSettings`
validation rather than the CLI alone, so a misconfigured `.env` is also
rejected.
"""

from __future__ import annotations

import ipaddress
import os
import re
import tomllib
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


def _default_data_dir() -> Path:
    """Return the XDG-style data directory.

    `~/.local/share/stackos/` matches the spec and the gitignore
    expectation that DB files live outside the repo.
    """
    return Path.home() / ".local" / "share" / "stackos"


def _default_state_dir() -> Path:
    """Return the XDG-style state directory.

    `~/.local/state/stackos/` is where transient or rotateable state
    lives — seed, auth token, PID, logs.
    """
    return Path.home() / ".local" / "state" / "stackos"


class _StateSettings(BaseSettings):
    """Resolve the log location even when unrelated runtime settings are invalid."""

    model_config = SettingsConfigDict(
        env_prefix="STACKOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    state_dir: Path = _default_state_dir()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        selected = os.environ.get("STACKOS_ENV_FILE")
        if selected is None:
            return init_settings, env_settings, dotenv_settings, file_secret_settings
        path = Path(selected)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("STACKOS_ENV_FILE must select an existing absolute settings file")
        if path.stat().st_mode & 0o077:
            raise ValueError("STACKOS_ENV_FILE must be private (mode 0600)")
        # An explicit file is a complete selection, not an overlay on an
        # installed service environment, cwd .env, or a host secret directory.
        field_names = {f"STACKOS_{name.upper()}" for name in settings_cls.model_fields}
        if field_names & os.environ.keys():
            raise ValueError("selected settings file cannot be mixed with STACKOS field overrides")
        source = DotEnvSettingsSource(settings_cls, env_file=path)
        if "process_role" in settings_cls.model_fields and source().get("process_role") not in {
            "server",
            "runtime",
        }:
            raise ValueError("selected settings file must explicitly select server or runtime")
        return init_settings, source


def get_state_dir() -> Path:
    """Use the same env/dotenv path policy for startup failure logging."""
    if os.environ.get("STACKOS_ENV_FILE") is not None:
        return Settings().state_dir
    return _StateSettings().state_dir


class Settings(_StateSettings):
    """Top-level settings, env-prefixed with `STACKOS_`."""

    # Network
    host: str = "127.0.0.1"
    port: int = 5180
    oauth_callback_base_url: str | None = None
    oauth_state_ttl_seconds: int = 600
    bridge_startup_timeout_seconds: float = Field(default=15.0, gt=0, le=300, allow_inf_nan=False)

    # Logging
    log_level: str = "INFO"

    # Paths (overridable via env for tests / non-default installs)
    data_dir: Path = _default_data_dir()

    # Explicit online targets. Standalone is the existing local installation;
    # selecting either new role never falls back to that installation or SQLite.
    process_role: Literal["standalone", "server", "runtime"] = "standalone"
    environment_id: str | None = None
    server_url: str | None = None
    supabase_workdir: Path | None = None
    supabase_url: str | None = None
    database_url: SecretStr | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_service_role_key: SecretStr | None = None

    @model_validator(mode="after")
    def _validate_environment(self) -> Self:
        target_fields = (
            "environment_id",
            "server_url",
            "supabase_workdir",
            "supabase_url",
            "database_url",
            "supabase_anon_key",
            "supabase_service_role_key",
        )
        if self.process_role == "standalone":
            if any(getattr(self, name) is not None for name in target_fields):
                raise ValueError(
                    "online targets require an explicit server or runtime process_role"
                )
            return self
        required = {"environment_id", "server_url", "host", "port", "data_dir", "state_dir"}
        if self.process_role == "server":
            required.update(target_fields)
        if not required <= self.model_fields_set or any(
            getattr(self, name) is None for name in required
        ):
            raise ValueError(
                "selected role requires explicit environment, server, paths and targets"
            )
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", self.environment_id or ""):
            raise ValueError("environment_id must be a local Supabase project identifier")
        if self.host != "127.0.0.1" or not 1024 <= self.port <= 65535 or self.port == 5180:
            raise ValueError("isolated roles require 127.0.0.1 and an explicit non-installed port")
        # Only the local-first topology is supported here. Hosted setup has its
        # own delivery; credentials and arbitrary origins must not enter this path.
        server = urlsplit(self.server_url or "")
        if (
            server.scheme != "http"
            or server.hostname != "127.0.0.1"
            or server.username is not None
            or server.password is not None
            or server.path
            or server.query
            or server.fragment
            or server.port is None
            or server.port == 5180
            or not 1024 <= server.port <= 65535
        ):
            raise ValueError("server_url must be an explicit isolated loopback HTTP origin")
        for path in (self.data_dir, self.state_dir):
            resolved = path.resolve()
            installed_paths = (_default_data_dir().resolve(), _default_state_dir().resolve())
            if not path.is_absolute() or any(
                resolved == installed
                or installed in resolved.parents
                or resolved in installed.parents
                for installed in installed_paths
            ):
                raise ValueError("isolated roles require absolute non-installed data/state paths")
        data, state = self.data_dir.resolve(), self.state_dir.resolve()
        if data == state or data in state.parents or state in data.parents:
            raise ValueError("isolated data and state paths must be separate")
        if self.process_role == "runtime":
            if any(getattr(self, name) is not None for name in target_fields[2:]):
                raise ValueError("runtime must not receive server database or Supabase settings")
            if self.port == server.port:
                raise ValueError("runtime listener must be separate from its server target")
            return self
        self._validate_local_supabase()
        return self

    def _validate_local_supabase(self) -> None:
        """Check the selected native CLI config, never discover another instance."""
        assert self.supabase_workdir is not None
        assert self.database_url is not None
        if not self.supabase_workdir.is_absolute():
            raise ValueError("supabase_workdir must be absolute")
        root = self.supabase_workdir.resolve()
        if any(root not in path.resolve().parents for path in (self.data_dir, self.state_dir)):
            raise ValueError("server data/state must belong to the selected Supabase workdir")
        try:
            config_path = root / "supabase/config.toml"
            if root.stat().st_mode & 0o077 or config_path.stat().st_mode & 0o077:
                raise ValueError("selected Supabase workdir/config must be private")
            config = tomllib.loads(config_path.read_text())
            if config["project_id"] != self.environment_id:
                raise ValueError("Supabase project_id does not match environment_id")
            api, db, auth = config["api"], config["db"], config["auth"]
            signing_key = root / "supabase/signing_keys.json"
            if (
                auth.get("signing_keys_path") != "./signing_keys.json"
                or not signing_key.is_file()
                or signing_key.stat().st_mode & 0o077
                or root not in signing_key.resolve().parents
            ):
                raise ValueError("local Auth requires its own private signing_keys.json")
            if api.get("enabled") is not True or auth.get("enabled") is not True:
                raise ValueError("selected Supabase API and Auth must be enabled")
            if auth.get("email", {}).get("enable_confirmations") is not True:
                raise ValueError("selected Auth must require email confirmation")
            for template, kind in (("confirmation", "signup"), ("recovery", "recovery")):
                relative = f"./supabase/templates/{template}.html"
                template_path = root / relative
                if (
                    auth["email"]["template"][template]["content_path"] != relative
                    or root not in template_path.resolve().parents
                    or template_path.stat().st_mode & 0o077
                    or template_path.read_text()
                    != (
                        '<p><a href="{{ .SiteURL }}/api/v1/auth/callback?token_hash='
                        "{{ .TokenHash }}&amp;type=" + kind + '">Confirm your StackOS email</a></p>'
                    )
                ):
                    raise ValueError("selected Auth email templates must use the StackOS callback")
            ports = [self.port, api["port"], db["port"], db["shadow_port"]]
            for section in ("studio", "inbucket", "analytics", "edge_runtime"):
                service = config.get(section, {})
                if service.get("enabled", True):
                    ports.append(service["inspector_port" if section == "edge_runtime" else "port"])
                    ports.extend(
                        service[key] for key in ("smtp_port", "pop3_port") if key in service
                    )
            if db.get("pooler", {}).get("enabled", False):
                ports.append(db["pooler"]["port"])
            if any(type(port) is not int or not 1024 <= port <= 65535 for port in ports):
                raise ValueError("all enabled local ports must be explicit unprivileged ports")
            if len(set(ports)) != len(ports):
                raise ValueError("enabled local ports must be distinct")
            if (
                self.server_url != f"http://127.0.0.1:{self.port}"
                or auth["site_url"] != self.server_url
            ):
                raise ValueError("server_url and Auth site_url must match the selected server")
            expected_api = f"http://127.0.0.1:{api['port']}"
            if self.supabase_url != expected_api or auth["jwt_issuer"] != f"{expected_api}/auth/v1":
                raise ValueError("Supabase URL/issuer must match the selected CLI API port")
            database = urlsplit(self.database_url.get_secret_value())
            if (
                database.scheme not in {"postgresql", "postgresql+psycopg"}
                or database.hostname != "127.0.0.1"
                or database.port != db["port"]
                or database.username != "postgres"
                or not database.password
                or database.path != "/postgres"
                or database.query
                or database.fragment
            ):
                raise ValueError(
                    "database_url must select the explicit local Postgres port/database"
                )
            for name, config_key in (
                ("supabase_anon_key", "anon_key"),
                ("supabase_service_role_key", "service_role_key"),
            ):
                secret = getattr(self, name)
                if (
                    not secret
                    or not secret.get_secret_value()
                    or secret.get_secret_value() != auth[config_key]
                ):
                    raise ValueError("Supabase keys must match the selected private CLI config")
        except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError("selected local Supabase config is missing or incomplete") from exc

    @field_validator("host")
    @classmethod
    def _reject_non_loopback(cls, v: str) -> str:
        """Reject any host that is not a loopback address.

        We bind by name *or* IP, so accept the canonical loopback names plus
        anything that parses as an IPv4/IPv6 loopback. Cross-machine binding
        is a single-user-product class-of-bug we explicitly close at parse
        time.
        """
        if v in _LOOPBACK_HOSTS:
            return v
        try:
            addr = ipaddress.ip_address(v)
        except ValueError as exc:
            raise ValueError(
                f"host {v!r} is not a loopback address (use 127.0.0.1, ::1, or localhost)"
            ) from exc
        if not addr.is_loopback:
            raise ValueError(f"host {v!r} is not a loopback address — refusing to bind")
        return v

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, v: str) -> str:
        """Normalise log level to upper-case and validate against logging stdlib."""
        upper = v.upper()
        if upper not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}:
            raise ValueError(f"invalid log_level {v!r}")
        return upper

    @field_validator("oauth_callback_base_url")
    @classmethod
    def _validate_oauth_callback_base_url(cls, value: str | None) -> str | None:
        """Accept one origin-only callback base controlled by the operator.

        Production callbacks must be HTTPS. Plain HTTP remains available only
        for loopback development, matching provider callback exemptions.
        """

        if value is None or not value.strip():
            return None
        parsed = urlsplit(value.strip())
        if (
            not parsed.scheme
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("oauth_callback_base_url must be an origin without path or query")
        try:
            is_loopback = (
                parsed.hostname in _LOOPBACK_HOSTS
                or ipaddress.ip_address(parsed.hostname).is_loopback
            )
        except ValueError:
            is_loopback = parsed.hostname in _LOOPBACK_HOSTS
        if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
            raise ValueError(
                "oauth_callback_base_url must use HTTPS, except for a loopback HTTP origin"
            )
        return f"{parsed.scheme}://{parsed.netloc}"

    @field_validator("oauth_state_ttl_seconds")
    @classmethod
    def _validate_oauth_state_ttl(cls, value: int) -> int:
        if not 60 <= value <= 1800:
            raise ValueError("oauth_state_ttl_seconds must be between 60 and 1800")
        return value

    # ---- Derived paths --------------------------------------------------

    @property
    def db_path(self) -> Path:
        """SQLite DB file (created on first migrate)."""
        if self.process_role != "standalone":
            raise ValueError(
                "selected server/runtime role cannot use the standalone SQLite database"
            )
        return self.data_dir / "stackos.db"

    @property
    def seed_path(self) -> Path:
        """32-byte HKDF seed for integration-credential encryption (M5)."""
        return self.state_dir / "seed.bin"

    @property
    def token_path(self) -> Path:
        """Per-install bearer token used by REST + MCP middleware."""
        return self.state_dir / "auth.token"

    @property
    def log_path(self) -> Path:
        """Daemon log file — rotated by RotatingFileHandler at 10 MB x 5."""
        return self.state_dir / "daemon.log"

    @property
    def bridge_log_path(self) -> Path:
        """Automatic sanitized bridge diagnostics, independent of daemon availability."""
        return self.state_dir / "mcp-bridge.log"

    @property
    def generated_assets_dir(self) -> Path:
        """Public local files generated by daemon-side asset tools."""
        return self.data_dir / "generated-assets"

    @property
    def pid_path(self) -> Path:
        """PID file written by the daemon at startup (M9 launchd integration)."""
        return self.state_dir / "daemon.pid"

    @property
    def oauth_callback_base(self) -> str:
        if self.oauth_callback_base_url is not None:
            return self.oauth_callback_base_url
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"http://{host}:{self.port}"

    @property
    def oauth_callback_uri(self) -> str:
        return f"{self.oauth_callback_base}/api/v1/auth/oauth/callback"

    @property
    def oauth_callback_host(self) -> str:
        parsed = urlsplit(self.oauth_callback_base)
        return parsed.hostname or self.host

    # ---- Side-effecting helpers ----------------------------------------

    def ensure_dirs(self) -> None:
        """Create data + state directories with mode 0700.

        We use `os.makedirs` + explicit chmod rather than `Path.mkdir(mode=...)`
        because the latter is filtered through umask and can land at 0750 or
        wider on a default-umask system. Re-running adjusts the mode of an
        existing dir, which is what we want.
        """
        for d in (self.data_dir, self.state_dir, self.generated_assets_dir):
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)


def get_settings() -> Settings:
    """Build a Settings instance — kept callable so tests can patch env first."""
    return Settings()
