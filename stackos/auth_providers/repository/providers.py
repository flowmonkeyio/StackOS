"""Provider metadata and setup-flow behavior for auth repositories."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from typing import Any

from sqlmodel import col, select

from stackos.artifacts import redact_secrets
from stackos.config import Settings
from stackos.db.models import AuthProvider, Plugin, Project, Provider
from stackos.repositories.base import Envelope, NotFoundError, ValidationError

from .schema import AuthFieldOut, AuthMethodOut, AuthProviderOut, AuthStartOut
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
        now = utcnow()
        for provider, plugin in rows:
            row = self._s.exec(
                select(AuthProvider).where(
                    AuthProvider.plugin_id == plugin.id,
                    AuthProvider.key == provider.key,
                )
            ).first()
            scopes = None
            if provider.config_json and isinstance(provider.config_json.get("scopes"), list):
                scopes = [str(scope) for scope in provider.config_json["scopes"]]
            if row is None:
                row = AuthProvider(
                    plugin_id=plugin.id,
                    key=provider.key,
                    name=provider.name,
                    description=provider.description,
                    auth_type=provider.auth_type,
                    scopes_json=scopes,
                    config_json=self._safe_provider_config(provider.config_json),
                )
            else:
                row.name = provider.name
                row.description = provider.description
                row.auth_type = provider.auth_type
                row.scopes_json = scopes
                row.config_json = self._safe_provider_config(provider.config_json)
                row.updated_at = now
            self._s.add(row)
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

    def start(
        self,
        *,
        project_id: int,
        provider_key: str,
        settings: Settings,
        auth_method_key: str | None = None,
        redirect_uri: str | None = None,
    ) -> Envelope[AuthStartOut]:
        self._require_project(project_id)
        provider = self._get_provider(provider_key)
        assert provider is not None
        method = self._get_auth_method(provider, auth_method_key)
        assert method is not None
        _ = redirect_uri
        setup_url = self._local_setup_url(
            settings=settings,
            project_id=project_id,
            provider_key=provider_key,
        )
        setup_required = provider.auth_type not in {"none", "local"}
        status = "not-required"
        if setup_required and method.interactive:
            status = "requires-oauth"
        elif setup_required:
            status = "requires-local-credential"
        return Envelope(
            data=AuthStartOut(
                project_id=project_id,
                provider_key=provider.key,
                auth_type=method.auth_type,
                auth_method_key=method.key,
                status=status,
                setup_url=setup_url if setup_required else None,
                authorization_url=self._authorization_url(method),
            ),
            project_id=project_id,
        )

    def _local_setup_url(self, *, settings: Settings, project_id: int, provider_key: str) -> str:
        return (
            f"http://{settings.host}:{settings.port}"
            f"/projects/{project_id}/connections?provider_key={provider_key}"
        )

    def _get_provider(self, provider_key: str, *, required: bool = True) -> AuthProvider | None:
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

    def _authorization_url(self, method: AuthMethodOut) -> str | None:
        config = method.config or {}
        value = config.get("authorization_url") or config.get("authorize_url")
        return str(value) if value is not None and str(value).strip() else None

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
        safe = redact_secrets(visible_config)
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
