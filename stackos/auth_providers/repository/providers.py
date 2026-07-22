"""Provider metadata and setup-flow behavior for auth repositories."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from typing import Any

from sqlmodel import col, select

from stackos.artifacts import redact_secrets
from stackos.config import Settings
from stackos.db.models import AuthProvider, Plugin, Project, ProjectPlugin, Provider
from stackos.provider_setup import sanitize_provider_setup_config
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError

from .schema import AuthFieldOut, AuthMethodOut, AuthProviderOut
from .utils import utcnow


class ProviderMetadataMixin:
    """Sync provider auth metadata and expose sanitized setup models."""

    # mypy cannot see the repository facade's session attribute on mixins.

    def sync_providers(self) -> None:
        """Mirror plugin provider auth metadata into auth_providers."""
        from stackos.repositories.plugins import PluginRepository

        PluginRepository(self._s).sync_builtin_plugins()
        rows = self._s.exec(
            select(Provider, Plugin)
            .join(Plugin, col(Provider.plugin_id) == col(Plugin.id))
            .order_by(col(Plugin.slug).asc(), col(Provider.key).asc())
        ).all()
        existing = {
            (row.plugin_id, row.key): row for row in self._s.exec(select(AuthProvider)).all()
        }
        now = utcnow()
        changed = False
        for provider, plugin in rows:
            row = existing.get((plugin.id, provider.key))
            scopes = None
            if provider.config_json and isinstance(provider.config_json.get("scopes"), list):
                scopes = [str(scope) for scope in provider.config_json["scopes"]]
            safe_config = self._safe_provider_config(provider.config_json)
            if row is None:
                row = AuthProvider(
                    plugin_id=plugin.id,
                    key=provider.key,
                    name=provider.name,
                    description=provider.description,
                    auth_type=provider.auth_type,
                    scopes_json=scopes,
                    config_json=safe_config,
                )
                changed = True
            else:
                values = {
                    "name": provider.name,
                    "description": provider.description,
                    "auth_type": provider.auth_type,
                    "scopes_json": scopes,
                    "config_json": safe_config,
                }
                if any(getattr(row, field) != value for field, value in values.items()):
                    for field, value in values.items():
                        setattr(row, field, value)
                    row.updated_at = now
                    changed = True
            self._s.add(row)
        if changed:
            self._s.commit()

    def list_providers(self, *, provider_key: str | None = None) -> list[AuthProviderOut]:
        self.sync_providers()
        stmt = select(AuthProvider, Plugin).outerjoin(
            Plugin, col(AuthProvider.plugin_id) == col(Plugin.id)
        )
        if provider_key is not None:
            stmt = stmt.where(col(AuthProvider.key) == provider_key)
        rows = self._s.exec(stmt.order_by(col(AuthProvider.key).asc())).all()
        return [self._provider_out(provider, plugin) for provider, plugin in rows]

    def _require_provider_enabled_for_project(self, *, project_id: int, provider: Any) -> None:
        """Reject setup for providers whose owning plugin is disabled in this project."""

        if provider.plugin_id is None:
            return
        project_plugin = self._s.exec(
            select(ProjectPlugin).where(
                ProjectPlugin.project_id == project_id,
                ProjectPlugin.plugin_id == provider.plugin_id,
            )
        ).first()
        if project_plugin is not None and project_plugin.enabled is False:
            plugin = self._s.get(Plugin, provider.plugin_id)
            raise ConflictError(
                "provider plugin is disabled for project",
                data={
                    "project_id": project_id,
                    "provider_key": provider.key,
                    "plugin_slug": plugin.slug if plugin is not None else None,
                    "next_action": "Enable the plugin before connecting this provider.",
                },
            )

    def _local_setup_url(self, *, settings: Settings, project_id: int, provider_key: str) -> str:
        return (
            f"http://{settings.host}:{settings.port}"
            f"/projects/{project_id}/connections?provider_key={provider_key}"
        )

    def _get_provider(
        self,
        provider_key: str,
        *,
        required: bool = True,
        sync: bool = True,
    ) -> AuthProvider | None:
        if sync:
            self.sync_providers()
        row = self._s.exec(
            select(AuthProvider).where(col(AuthProvider.key) == provider_key)
        ).first()
        if row is None and required:
            raise NotFoundError(f"auth provider {provider_key!r} not found")
        return row

    def _auth_methods(self, provider: AuthProvider) -> list[AuthMethodOut]:
        raw_methods = (provider.config_json or {}).get("auth_methods")
        methods: list[AuthMethodOut] = []
        if isinstance(raw_methods, list):
            for raw_method in raw_methods:
                if not isinstance(raw_method, dict):
                    continue
                fields = [
                    AuthFieldOut(**raw_field)
                    for raw_field in raw_method.get("fields", [])
                    if isinstance(raw_field, dict)
                ]
                methods.append(
                    AuthMethodOut(
                        key=str(raw_method.get("key") or provider.auth_type or "default"),
                        label=str(raw_method.get("label") or provider.name),
                        auth_type=str(raw_method.get("auth_type") or provider.auth_type),
                        description=str(raw_method.get("description") or ""),
                        interactive=bool(raw_method.get("interactive")),
                        payload_format=str(raw_method.get("payload_format") or "json"),
                        payload_field=(
                            str(raw_method["payload_field"])
                            if raw_method.get("payload_field") is not None
                            else None
                        ),
                        fields=fields,
                        config=(
                            dict(raw_method["config"])
                            if isinstance(raw_method.get("config"), dict)
                            else None
                        ),
                    )
                )
        if methods:
            return methods
        if provider.auth_type in {"none", "local"}:
            return [
                AuthMethodOut(
                    key=provider.auth_type,
                    label=provider.auth_type.replace("-", " ").title(),
                    auth_type=provider.auth_type,
                    payload_format="none",
                )
            ]
        raise ValidationError(
            f"auth provider {provider.key!r} does not declare auth_methods",
            data={"provider_key": provider.key},
        )

    def _get_auth_method(
        self,
        provider: AuthProvider,
        auth_method_key: str | Any | None,
        *,
        required: bool = True,
    ) -> AuthMethodOut | None:
        methods = self._auth_methods(provider)
        if auth_method_key is None:
            return methods[0] if methods else None
        auth_method_key = str(auth_method_key)
        for method in methods:
            if method.key == auth_method_key:
                return method
        if required:
            raise ValidationError(
                f"auth method {auth_method_key!r} not found for provider {provider.key!r}",
                data={"provider_key": provider.key, "auth_method_key": auth_method_key},
            )
        return None

    def _provider_out(self, row: AuthProvider, plugin: Plugin | None) -> AuthProviderOut:
        assert row.id is not None
        return AuthProviderOut(
            id=row.id,
            plugin_id=row.plugin_id,
            plugin_slug=plugin.slug if plugin is not None else None,
            key=row.key,
            name=row.name,
            description=row.description,
            auth_type=row.auth_type,
            auth_methods=self._auth_methods(row),
            scopes=list(row.scopes_json or []),
            config_json=self._provider_public_config(row.config_json),
        )

    def _safe_provider_config(self, config_json: dict[str, Any] | None) -> dict[str, Any] | None:
        if config_json is None:
            return None
        visible_config = {key: value for key, value in config_json.items() if key != "auth_methods"}
        raw_setup = visible_config.pop("setup", None)
        safe = redact_secrets(visible_config)
        safe_setup = sanitize_provider_setup_config(raw_setup)
        if safe_setup:
            safe["setup"] = safe_setup
        if "auth_methods" in config_json:
            safe["auth_methods"] = config_json["auth_methods"]
        return safe or None

    def _provider_public_config(self, config_json: dict[str, Any] | None) -> dict[str, Any] | None:
        if config_json is None:
            return None
        public = dict(config_json)
        public.pop("auth_methods", None)
        return public or None

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")
