"""StackOS plugin/catalog repository."""

from __future__ import annotations

import weakref
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, col, select

from stackos.action_availability import ActionAvailabilityOut, ActionExposureOut
from stackos.db.models import (
    Action,
    Capability,
    Plugin,
    PluginSource,
    Project,
    ProjectPlugin,
    Provider,
    Resource,
)
from stackos.generated_inventory import (
    generated_action_public_key,
    generated_action_visible_for_project,
)
from stackos.plugins.manifest import (
    PluginManifest,
    get_builtin_plugin_manifest_snapshot,
    plugin_sort_key,
)
from stackos.repositories.base import ConflictError, Envelope, NotFoundError
from stackos.repositories.plugin_manifest_sync import sync_plugin_manifest
from stackos.repositories.resources import ResourceOut


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _required_id(value: int | None) -> int:
    if value is None:
        raise RuntimeError("expected persisted row id")
    return int(value)


def _group_by_plugin_slug(rows: Sequence[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.plugin_slug, []).append(row)
    return grouped


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
    exposure: ActionExposureOut


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

    _builtin_sync_generations: ClassVar[weakref.WeakKeyDictionary[Any, str]] = (
        weakref.WeakKeyDictionary()
    )

    def __init__(self, session: Session) -> None:
        self._s = session
        self._builtin_plugins_generation: str | None = None
        self._disabled_plugin_ids_cache: dict[int | None, set[int]] = {}
        self._connector_keys_cache: set[str] | None = None

    def sync_builtin_plugins(self) -> None:
        """Idempotently upsert built-in plugin manifests into catalog tables."""
        snapshot = get_builtin_plugin_manifest_snapshot()
        engine = self._s.get_bind()
        if (
            self._builtin_plugins_generation == snapshot.generation
            or self._builtin_sync_generations.get(engine) == snapshot.generation
        ):
            self._builtin_plugins_generation = snapshot.generation
            return
        for manifest in snapshot.manifests:
            self._sync_manifest(manifest)
        self._s.commit()
        self._builtin_plugins_generation = snapshot.generation
        self._builtin_sync_generations[engine] = snapshot.generation

    def _sync_manifest(self, manifest: PluginManifest) -> None:
        sync_plugin_manifest(self._s, manifest)

    def list_plugins(
        self,
        *,
        project_id: int | None = None,
        compact: bool = False,
    ) -> list[PluginOut]:
        self.sync_builtin_plugins()
        enabled_by_plugin = self._enabled_map(project_id)
        rows = list(self._s.exec(select(Plugin)).all())
        rows = sorted(rows, key=lambda row: plugin_sort_key(row.slug, row.manifest_json))
        return [
            self._plugin_out(
                row,
                enabled_by_plugin.get(_required_id(row.id)),
                compact=compact,
            )
            for row in rows
        ]

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
        self._disabled_plugin_ids_cache.clear()
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
        self._disabled_plugin_ids_cache.clear()
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
        rows = list(self._s.exec(stmt.order_by(col(Capability.key).asc())).all())
        rows = self._filter_project_enabled(rows, project_id=project_id)
        rows.sort(key=lambda row: (*plugin_sort_key(row[1].slug, row[1].manifest_json), row[0].key))
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
        rows = list(self._s.exec(stmt.order_by(col(Provider.key).asc())).all())
        rows = self._filter_project_enabled(rows, project_id=project_id)
        rows.sort(key=lambda row: (*plugin_sort_key(row[1].slug, row[1].manifest_json), row[0].key))
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
        rows = cast(
            list[tuple[Action, Plugin, Provider | None]],
            list(self._s.exec(stmt.order_by(col(Action.key).asc())).all()),
        )
        rows = self._filter_project_enabled(rows, project_id=project_id)
        rows = [
            row
            for row in rows
            if generated_action_visible_for_project(
                config_json=row[0].config_json,
                project_id=project_id,
                action_key=row[0].key,
            )
        ]
        rows = _dedupe_generated_public_action_rows(rows)
        rows.sort(key=lambda row: (*plugin_sort_key(row[1].slug, row[1].manifest_json), row[0].key))
        availability_context = self._action_availability_context(project_id)
        connector_keys = self._connector_keys()
        return [
            self._action_out(
                action,
                plugin,
                provider,
                project_id=project_id,
                availability_context=availability_context,
                connector_keys=connector_keys,
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
        rows = list(self._s.exec(stmt.order_by(col(Resource.key).asc())).all())
        rows = self._filter_project_enabled(rows, project_id=project_id)
        rows.sort(key=lambda row: (*plugin_sort_key(row[1].slug, row[1].manifest_json), row[0].key))
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
        capabilities_by_plugin = _group_by_plugin_slug(
            self.list_capabilities(plugin_slug=plugin_slug, project_id=project_id)
        )
        providers_by_plugin = _group_by_plugin_slug(
            self.list_providers(plugin_slug=plugin_slug, project_id=project_id)
        )
        actions_by_plugin = _group_by_plugin_slug(
            self.list_actions(plugin_slug=plugin_slug, project_id=project_id)
        )
        resources_by_plugin = _group_by_plugin_slug(
            self.list_resources(plugin_slug=plugin_slug, project_id=project_id)
        )
        catalogs = [
            PluginCatalogOut(
                plugin=plugin,
                capabilities=capabilities_by_plugin.get(plugin.slug, []),
                providers=providers_by_plugin.get(plugin.slug, []),
                actions=actions_by_plugin.get(plugin.slug, []),
                resources=resources_by_plugin.get(plugin.slug, []),
            )
            for plugin in plugins
        ]
        return CatalogOut(plugins=catalogs)

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
        cached = self._disabled_plugin_ids_cache.get(project_id)
        if cached is not None:
            return cached
        disabled_ids = {
            row.plugin_id
            for row in self._s.exec(
                select(ProjectPlugin).where(
                    col(ProjectPlugin.project_id) == project_id,
                    col(ProjectPlugin.enabled).is_(False),
                )
            ).all()
        }
        self._disabled_plugin_ids_cache[project_id] = disabled_ids
        return disabled_ids

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

    def _action_availability_context(self, project_id: int | None) -> Any:
        from stackos.action_availability import build_action_availability_context

        return build_action_availability_context(self._s, project_id=project_id)

    def _connector_keys(self) -> set[str]:
        if self._connector_keys_cache is None:
            from stackos.actions.connectors import DEFAULT_ACTION_CONNECTORS

            self._connector_keys_cache = set(DEFAULT_ACTION_CONNECTORS.list_keys())
        return self._connector_keys_cache

    def _plugin_out(
        self,
        row: Plugin,
        enabled_for_project: bool | None,
        *,
        compact: bool = False,
    ) -> PluginOut:
        out = PluginOut.model_validate(row)
        if compact:
            # The global app shell only needs ordering and contributed nav.
            # Avoid serializing action, provider, workflow, and resource
            # manifests on every project navigation.
            out.manifest_json = {
                key: row.manifest_json[key]
                for key in ("display_order", "ui")
                if key in row.manifest_json
            }
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
        availability_context: Any | None = None,
        connector_keys: set[str] | None = None,
    ) -> ActionOut:
        from stackos.action_availability import build_action_availability, build_action_exposure
        from stackos.actions.manifest import parse_action_manifest

        assert row.id is not None and row.plugin_id is not None
        manifest = parse_action_manifest(action=row, plugin=plugin, provider=provider)
        availability = build_action_availability(
            self._s,
            manifest=manifest,
            connector_keys=connector_keys if connector_keys is not None else self._connector_keys(),
            project_id=project_id,
            provider_config_json=provider.config_json if provider is not None else None,
            context=availability_context,
        )
        exposure = build_action_exposure(
            availability,
            project_id=project_id,
            plugin_slug=plugin.slug,
            provider_key=provider.key if provider is not None else None,
            requires_credential=manifest.requires_credential,
            allows_credential=manifest.allows_credential,
        )
        return ActionOut(
            id=row.id,
            plugin_id=row.plugin_id,
            plugin_slug=plugin.slug,
            provider_id=row.provider_id,
            provider_key=provider.key if provider is not None else None,
            action_ref=manifest.action_ref,
            key=manifest.action_key,
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
            exposure=exposure,
        )


def _dedupe_generated_public_action_rows(
    rows: list[tuple[Action, Plugin, Provider | None]],
) -> list[tuple[Action, Plugin, Provider | None]]:
    latest_by_public_ref: dict[tuple[str, str], tuple[Action, Plugin, Provider | None]] = {}
    for row in rows:
        action, plugin, _provider = row
        public_action_key = generated_action_public_key(action.config_json)
        if public_action_key is None:
            continue
        key = (plugin.slug, public_action_key)
        current = latest_by_public_ref.get(key)
        if current is None or _action_row_recency_key(action) > _action_row_recency_key(current[0]):
            latest_by_public_ref[key] = row

    deduped: list[tuple[Action, Plugin, Provider | None]] = []
    emitted_public_refs: set[tuple[str, str]] = set()
    for row in rows:
        action, plugin, _provider = row
        public_action_key = generated_action_public_key(action.config_json)
        if public_action_key is None:
            deduped.append(row)
            continue
        key = (plugin.slug, public_action_key)
        if key in emitted_public_refs:
            continue
        if latest_by_public_ref.get(key) == row:
            deduped.append(row)
            emitted_public_refs.add(key)
    return deduped


def _action_row_recency_key(action: Action) -> tuple[Any, Any, int]:
    return action.updated_at, action.created_at, int(action.id or 0)


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
