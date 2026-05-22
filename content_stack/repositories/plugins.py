"""StackOS plugin/catalog repository."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

from content_stack.action_availability import ActionAvailabilityOut
from content_stack.db.models import (
    Action,
    ActionVersion,
    Capability,
    Plugin,
    PluginSource,
    Project,
    ProjectPlugin,
    Provider,
    Resource,
)
from content_stack.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS, PluginManifest
from content_stack.repositories.base import ConflictError, Envelope, NotFoundError
from content_stack.repositories.resources import ResourceOut


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _required_id(value: int | None) -> int:
    if value is None:
        raise RuntimeError("expected persisted row id")
    return int(value)


class PluginOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str
    version: str
    description: str
    source: PluginSource
    manifest_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    enabled_for_project: bool | None = None


class ProjectPluginOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    plugin_id: int
    plugin_slug: str
    enabled: bool
    config_json: dict[str, Any] | None
    enabled_at: datetime | None
    disabled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CapabilityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plugin_id: int
    plugin_slug: str
    key: str
    name: str
    description: str
    kind: str
    config_json: dict[str, Any] | None


class ProviderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plugin_id: int
    plugin_slug: str
    key: str
    name: str
    description: str
    auth_type: str
    config_json: dict[str, Any] | None


class ActionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plugin_id: int
    plugin_slug: str
    provider_id: int | None
    provider_key: str | None
    action_ref: str
    key: str
    name: str
    description: str
    capability_key: str | None
    risk_level: str
    input_schema_json: dict[str, Any]
    output_schema_json: dict[str, Any]
    config_json: dict[str, Any] | None
    connector_key: str | None = None
    operation: str
    requires_credential: bool = False
    allows_credential: bool = False
    budget_kind: str | None = None
    enforce_budget: bool = False
    availability: ActionAvailabilityOut


class PluginCatalogOut(BaseModel):
    plugin: PluginOut
    capabilities: list[CapabilityOut]
    providers: list[ProviderOut]
    actions: list[ActionOut]
    resources: list[ResourceOut]


class CatalogOut(BaseModel):
    plugins: list[PluginCatalogOut]


class PluginRepository:
    """Repository for installed plugin manifests and project enablement."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def sync_builtin_plugins(self) -> None:
        """Idempotently upsert built-in plugin manifests into catalog tables."""
        for manifest in BUILTIN_PLUGIN_MANIFESTS:
            self._sync_manifest(manifest)
        self._s.commit()

    def list_plugins(self, *, project_id: int | None = None) -> list[PluginOut]:
        self.sync_builtin_plugins()
        enabled_by_plugin = self._enabled_map(project_id)
        rows = self._s.exec(select(Plugin).order_by(col(Plugin.slug).asc())).all()
        return [self._plugin_out(row, enabled_by_plugin.get(_required_id(row.id))) for row in rows]

    def get_plugin(self, slug: str, *, project_id: int | None = None) -> PluginOut:
        self.sync_builtin_plugins()
        row = self._get_plugin_row(slug)
        enabled = self._enabled_map(project_id).get(_required_id(row.id))
        return self._plugin_out(row, enabled)

    def enable(
        self,
        *,
        project_id: int,
        plugin_slug: str,
        config_json: dict[str, Any] | None = None,
    ) -> Envelope[ProjectPluginOut]:
        self.sync_builtin_plugins()
        self._require_project(project_id)
        plugin = self._get_plugin_row(plugin_slug)
        row = self._s.exec(
            select(ProjectPlugin).where(
                ProjectPlugin.project_id == project_id,
                ProjectPlugin.plugin_id == plugin.id,
            )
        ).first()
        now = _utcnow()
        if row is None:
            row = ProjectPlugin(
                project_id=project_id,
                plugin_id=plugin.id,
                enabled=True,
                config_json=config_json,
                enabled_at=now,
            )
        else:
            row.enabled = True
            row.config_json = config_json if config_json is not None else row.config_json
            row.enabled_at = now
            row.disabled_at = None
            row.updated_at = now
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._project_plugin_out(row, plugin), project_id=project_id)

    def disable(self, *, project_id: int, plugin_slug: str) -> Envelope[ProjectPluginOut]:
        self.sync_builtin_plugins()
        self._require_project(project_id)
        plugin = self._get_plugin_row(plugin_slug)
        row = self._s.exec(
            select(ProjectPlugin).where(
                ProjectPlugin.project_id == project_id,
                ProjectPlugin.plugin_id == plugin.id,
            )
        ).first()
        if row is None:
            raise ConflictError(
                "plugin is not enabled for project",
                data={"project_id": project_id, "plugin_slug": plugin_slug},
            )
        now = _utcnow()
        row.enabled = False
        row.disabled_at = now
        row.updated_at = now
        self._s.add(row)
        self._s.commit()
        self._s.refresh(row)
        return Envelope(data=self._project_plugin_out(row, plugin), project_id=project_id)

    def list_capabilities(
        self,
        *,
        plugin_slug: str | None = None,
        project_id: int | None = None,
    ) -> list[CapabilityOut]:
        self.sync_builtin_plugins()
        stmt = select(Capability, Plugin).join(Plugin, col(Capability.plugin_id) == col(Plugin.id))
        if plugin_slug is not None:
            stmt = stmt.where(col(Plugin.slug) == plugin_slug)
        rows = list(
            self._s.exec(stmt.order_by(col(Plugin.slug).asc(), col(Capability.key).asc())).all()
        )
        rows = self._filter_project_enabled(rows, project_id=project_id)
        return [self._capability_out(capability, plugin) for capability, plugin in rows]

    def get_capability(self, *, key: str, plugin_slug: str | None = None) -> CapabilityOut:
        rows = [c for c in self.list_capabilities(plugin_slug=plugin_slug) if c.key == key]
        if not rows:
            raise NotFoundError(f"capability {key!r} not found")
        if len(rows) > 1 and plugin_slug is None:
            raise ConflictError(
                "capability key is ambiguous; pass plugin_slug",
                data={"key": key, "plugin_slugs": sorted(r.plugin_slug for r in rows)},
            )
        return rows[0]

    def list_providers(
        self,
        *,
        plugin_slug: str | None = None,
        project_id: int | None = None,
    ) -> list[ProviderOut]:
        self.sync_builtin_plugins()
        stmt = select(Provider, Plugin).join(Plugin, col(Provider.plugin_id) == col(Plugin.id))
        if plugin_slug is not None:
            stmt = stmt.where(col(Plugin.slug) == plugin_slug)
        rows = list(
            self._s.exec(stmt.order_by(col(Plugin.slug).asc(), col(Provider.key).asc())).all()
        )
        rows = self._filter_project_enabled(rows, project_id=project_id)
        return [self._provider_out(provider, plugin) for provider, plugin in rows]

    def get_provider(self, *, key: str, plugin_slug: str | None = None) -> ProviderOut:
        rows = [p for p in self.list_providers(plugin_slug=plugin_slug) if p.key == key]
        if not rows:
            raise NotFoundError(f"provider {key!r} not found")
        if len(rows) > 1 and plugin_slug is None:
            raise ConflictError(
                "provider key is ambiguous; pass plugin_slug",
                data={"key": key, "plugin_slugs": sorted(r.plugin_slug for r in rows)},
            )
        return rows[0]

    def list_actions(
        self,
        *,
        plugin_slug: str | None = None,
        project_id: int | None = None,
    ) -> list[ActionOut]:
        self.sync_builtin_plugins()
        if project_id is not None:
            self._require_project(project_id)
        stmt = (
            select(Action, Plugin, Provider)
            .join(Plugin, col(Action.plugin_id) == col(Plugin.id))
            .outerjoin(Provider, col(Action.provider_id) == col(Provider.id))
        )
        if plugin_slug is not None:
            stmt = stmt.where(col(Plugin.slug) == plugin_slug)
        rows = list(
            self._s.exec(stmt.order_by(col(Plugin.slug).asc(), col(Action.key).asc())).all()
        )
        rows = self._filter_project_enabled(rows, project_id=project_id)
        return [
            self._action_out(
                action,
                plugin,
                provider,
                project_id=project_id,
            )
            for action, plugin, provider in rows
        ]

    def get_action(
        self,
        *,
        key: str,
        plugin_slug: str | None = None,
        project_id: int | None = None,
    ) -> ActionOut:
        rows = [
            action
            for action in self.list_actions(plugin_slug=plugin_slug, project_id=project_id)
            if action.key == key
        ]
        if not rows:
            raise NotFoundError(f"action {key!r} not found")
        if len(rows) > 1 and plugin_slug is None:
            raise ConflictError(
                "action key is ambiguous; pass plugin_slug",
                data={"key": key, "plugin_slugs": sorted(r.plugin_slug for r in rows)},
            )
        return rows[0]

    def list_resources(
        self,
        *,
        plugin_slug: str | None = None,
        project_id: int | None = None,
    ) -> list[ResourceOut]:
        self.sync_builtin_plugins()
        stmt = select(Resource, Plugin).join(Plugin, col(Resource.plugin_id) == col(Plugin.id))
        if plugin_slug is not None:
            stmt = stmt.where(col(Plugin.slug) == plugin_slug)
        rows = list(
            self._s.exec(stmt.order_by(col(Plugin.slug).asc(), col(Resource.key).asc())).all()
        )
        rows = self._filter_project_enabled(rows, project_id=project_id)
        return [self._resource_out(resource, plugin) for resource, plugin in rows]

    def catalog(
        self,
        *,
        plugin_slug: str | None = None,
        project_id: int | None = None,
    ) -> CatalogOut:
        self.sync_builtin_plugins()
        plugins = [self.get_plugin(plugin_slug)] if plugin_slug else self.list_plugins()
        disabled_plugin_ids = self._disabled_plugin_ids(project_id)
        if disabled_plugin_ids:
            plugins = [plugin for plugin in plugins if plugin.id not in disabled_plugin_ids]
        catalogs = [
            PluginCatalogOut(
                plugin=plugin,
                capabilities=self.list_capabilities(plugin_slug=plugin.slug, project_id=project_id),
                providers=self.list_providers(plugin_slug=plugin.slug, project_id=project_id),
                actions=self.list_actions(plugin_slug=plugin.slug, project_id=project_id),
                resources=self.list_resources(plugin_slug=plugin.slug, project_id=project_id),
            )
            for plugin in plugins
        ]
        return CatalogOut(plugins=catalogs)

    def _sync_manifest(self, manifest: PluginManifest) -> Plugin:
        now = _utcnow()
        row = self._s.exec(select(Plugin).where(Plugin.slug == manifest.slug)).first()
        manifest_json = manifest.model_dump(mode="json", by_alias=True)
        if row is None:
            row = Plugin(
                slug=manifest.slug,
                name=manifest.name,
                version=manifest.version,
                description=manifest.description,
                source=PluginSource(manifest.source),
                manifest_json=manifest_json,
            )
        else:
            row.name = manifest.name
            row.version = manifest.version
            row.description = manifest.description
            row.source = PluginSource(manifest.source)
            row.manifest_json = manifest_json
            row.updated_at = now
        self._s.add(row)
        self._s.flush()
        assert row.id is not None

        providers_by_key: dict[str, Provider] = {}
        for provider_manifest in manifest.providers:
            provider = self._s.exec(
                select(Provider).where(
                    Provider.plugin_id == row.id,
                    Provider.key == provider_manifest.key,
                )
            ).first()
            if provider is None:
                provider = Provider(
                    plugin_id=row.id,
                    key=provider_manifest.key,
                    name=provider_manifest.name,
                    description=provider_manifest.description,
                    auth_type=provider_manifest.auth_type,
                    config_json=provider_manifest.config,
                )
            else:
                provider.name = provider_manifest.name
                provider.description = provider_manifest.description
                provider.auth_type = provider_manifest.auth_type
                provider.config_json = provider_manifest.config
                provider.updated_at = now
            self._s.add(provider)
            self._s.flush()
            providers_by_key[provider.key] = provider

        for capability_manifest in manifest.capabilities:
            capability = self._s.exec(
                select(Capability).where(
                    Capability.plugin_id == row.id,
                    Capability.key == capability_manifest.key,
                )
            ).first()
            if capability is None:
                capability = Capability(
                    plugin_id=row.id,
                    key=capability_manifest.key,
                    name=capability_manifest.name,
                    description=capability_manifest.description,
                    kind=capability_manifest.kind,
                    config_json=capability_manifest.config,
                )
            else:
                capability.name = capability_manifest.name
                capability.description = capability_manifest.description
                capability.kind = capability_manifest.kind
                capability.config_json = capability_manifest.config
                capability.updated_at = now
            self._s.add(capability)

        for resource_manifest in manifest.resources:
            resource = self._s.exec(
                select(Resource).where(
                    Resource.plugin_id == row.id,
                    Resource.key == resource_manifest.key,
                )
            ).first()
            if resource is None:
                resource = Resource(
                    plugin_id=row.id,
                    key=resource_manifest.key,
                    name=resource_manifest.name,
                    description=resource_manifest.description,
                    schema_data=resource_manifest.schema_data,
                    ui_schema_json=resource_manifest.ui_schema,
                    config_json=resource_manifest.config,
                )
            else:
                resource.name = resource_manifest.name
                resource.description = resource_manifest.description
                resource.schema_data = resource_manifest.schema_data
                resource.ui_schema_json = resource_manifest.ui_schema
                resource.config_json = resource_manifest.config
                resource.updated_at = now
            self._s.add(resource)

        for action_manifest in manifest.actions:
            provider_id = None
            if action_manifest.provider is not None:
                provider = providers_by_key.get(action_manifest.provider)
                if provider is None:
                    raise ConflictError(
                        "action references unknown provider",
                        data={
                            "plugin_slug": manifest.slug,
                            "action": action_manifest.key,
                            "provider": action_manifest.provider,
                        },
                    )
                provider_id = provider.id
            action = self._s.exec(
                select(Action).where(Action.plugin_id == row.id, Action.key == action_manifest.key)
            ).first()
            if action is None:
                action = Action(
                    plugin_id=row.id,
                    provider_id=provider_id,
                    key=action_manifest.key,
                    name=action_manifest.name,
                    description=action_manifest.description,
                    capability_key=action_manifest.capability,
                    risk_level=action_manifest.risk_level,
                    input_schema_json=action_manifest.input_schema,
                    output_schema_json=action_manifest.output_schema,
                    config_json=action_manifest.config,
                )
            else:
                action.provider_id = provider_id
                action.name = action_manifest.name
                action.description = action_manifest.description
                action.capability_key = action_manifest.capability
                action.risk_level = action_manifest.risk_level
                action.input_schema_json = action_manifest.input_schema
                action.output_schema_json = action_manifest.output_schema
                action.config_json = action_manifest.config
                action.updated_at = now
            self._s.add(action)
            self._s.flush()
            assert action.id is not None
            version = self._s.exec(
                select(ActionVersion).where(
                    ActionVersion.action_id == action.id,
                    ActionVersion.version == manifest.version,
                )
            ).first()
            if version is None:
                version = ActionVersion(
                    action_id=action.id,
                    version=manifest.version,
                    manifest_json=action_manifest.model_dump(mode="json", by_alias=True),
                )
                self._s.add(version)
        return row

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")

    def _get_plugin_row(self, slug: str) -> Plugin:
        row = self._s.exec(select(Plugin).where(Plugin.slug == slug)).first()
        if row is None:
            raise NotFoundError(f"plugin {slug!r} not found")
        return row

    def _enabled_map(self, project_id: int | None) -> dict[int, bool | None]:
        if project_id is None:
            return {}
        rows = self._s.exec(
            select(ProjectPlugin).where(ProjectPlugin.project_id == project_id)
        ).all()
        return {row.plugin_id: row.enabled for row in rows}

    def _disabled_plugin_ids(self, project_id: int | None) -> set[int]:
        if project_id is None:
            return set()
        return {
            row.plugin_id
            for row in self._s.exec(
                select(ProjectPlugin).where(
                    col(ProjectPlugin.project_id) == project_id,
                    col(ProjectPlugin.enabled).is_(False),
                )
            ).all()
        }

    def _filter_project_enabled(
        self,
        rows: Sequence[Any],
        *,
        project_id: int | None,
    ) -> list[Any]:
        disabled_plugin_ids = self._disabled_plugin_ids(project_id)
        if not disabled_plugin_ids:
            return list(rows)
        return [row for row in rows if row[1].id not in disabled_plugin_ids]

    def _plugin_out(self, row: Plugin, enabled_for_project: bool | None) -> PluginOut:
        out = PluginOut.model_validate(row)
        out.enabled_for_project = enabled_for_project
        return out

    def _project_plugin_out(self, row: ProjectPlugin, plugin: Plugin) -> ProjectPluginOut:
        assert row.id is not None and row.plugin_id is not None
        return ProjectPluginOut(
            id=row.id,
            project_id=row.project_id,
            plugin_id=row.plugin_id,
            plugin_slug=plugin.slug,
            enabled=row.enabled,
            config_json=row.config_json,
            enabled_at=row.enabled_at,
            disabled_at=row.disabled_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _capability_out(self, row: Capability, plugin: Plugin) -> CapabilityOut:
        assert row.id is not None and row.plugin_id is not None
        return CapabilityOut(
            id=row.id,
            plugin_id=row.plugin_id,
            plugin_slug=plugin.slug,
            key=row.key,
            name=row.name,
            description=row.description,
            kind=row.kind,
            config_json=row.config_json,
        )

    def _provider_out(self, row: Provider, plugin: Plugin) -> ProviderOut:
        assert row.id is not None and row.plugin_id is not None
        return ProviderOut(
            id=row.id,
            plugin_id=row.plugin_id,
            plugin_slug=plugin.slug,
            key=row.key,
            name=row.name,
            description=row.description,
            auth_type=row.auth_type,
            config_json=row.config_json,
        )

    def _resource_out(self, row: Resource, plugin: Plugin) -> ResourceOut:
        assert row.id is not None and row.plugin_id is not None
        return ResourceOut(
            id=row.id,
            plugin_id=row.plugin_id,
            plugin_slug=plugin.slug,
            key=row.key,
            name=row.name,
            description=row.description,
            schema_data=row.schema_data,
            ui_schema_json=row.ui_schema_json,
            config_json=row.config_json,
        )

    def _action_out(
        self,
        row: Action,
        plugin: Plugin,
        provider: Provider | None,
        *,
        project_id: int | None = None,
    ) -> ActionOut:
        from content_stack.action_availability import build_action_availability
        from content_stack.actions.connectors import DEFAULT_ACTION_CONNECTORS
        from content_stack.actions.manifest import parse_action_manifest

        assert row.id is not None and row.plugin_id is not None
        manifest = parse_action_manifest(action=row, plugin=plugin, provider=provider)
        availability = build_action_availability(
            self._s,
            manifest=manifest,
            connector_keys=set(DEFAULT_ACTION_CONNECTORS.list_keys()),
            project_id=project_id,
            provider_config_json=provider.config_json if provider is not None else None,
        )
        return ActionOut(
            id=row.id,
            plugin_id=row.plugin_id,
            plugin_slug=plugin.slug,
            provider_id=row.provider_id,
            provider_key=provider.key if provider is not None else None,
            action_ref=manifest.action_ref,
            key=row.key,
            name=row.name,
            description=row.description,
            capability_key=row.capability_key,
            risk_level=row.risk_level,
            input_schema_json=row.input_schema_json,
            output_schema_json=row.output_schema_json,
            config_json=row.config_json,
            connector_key=manifest.connector_key,
            operation=manifest.operation,
            requires_credential=manifest.requires_credential,
            allows_credential=manifest.allows_credential,
            budget_kind=manifest.budget_kind,
            enforce_budget=manifest.enforce_budget,
            availability=availability,
        )


__all__ = [
    "ActionOut",
    "CapabilityOut",
    "CatalogOut",
    "PluginCatalogOut",
    "PluginOut",
    "PluginRepository",
    "ProjectPluginOut",
    "ProviderOut",
    "ResourceOut",
]
