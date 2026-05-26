"""Action manifest lookup and availability description."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from typing import Any

from sqlmodel import col, select

from stackos.action_availability import build_action_availability
from stackos.actions.manifest import ExecutableActionManifest, parse_action_manifest
from stackos.db.models import Action, Plugin, Project, ProjectPlugin, Provider
from stackos.repositories.base import NotFoundError, ValidationError
from stackos.repositories.plugins import PluginRepository

from .schema import ActionDescribeOut


class ActionCatalogMixin:
    """Read action manifest metadata and project availability."""

    def describe(
        self,
        *,
        action_ref: str | None = None,
        plugin_slug: str | None = None,
        action_key: str | None = None,
        project_id: int | None = None,
    ) -> ActionDescribeOut:
        manifest, provider_config_json = self._manifest_with_provider_config(
            action_ref=action_ref,
            plugin_slug=plugin_slug,
            action_key=action_key,
        )
        if project_id is not None:
            self._require_project(project_id)
        connector_keys = set(self._connectors.list_keys())
        availability = build_action_availability(
            self._s,
            manifest=manifest,
            connector_keys=connector_keys,
            project_id=project_id,
            provider_config_json=provider_config_json,
            plugin_disabled=self._plugin_disabled_for_project(
                project_id=project_id,
                plugin_slug=manifest.plugin_slug,
            ),
        )
        registered = availability.connector_registered
        return ActionDescribeOut(
            manifest=manifest,
            connector_registered=registered,
            execution_available=availability.executable,
            agent_execute_available=availability.executable,
            availability=availability,
        )

    def _manifest(
        self,
        *,
        action_ref: str | None,
        plugin_slug: str | None,
        action_key: str | None,
    ) -> ExecutableActionManifest:
        manifest, _provider_config_json = self._manifest_with_provider_config(
            action_ref=action_ref,
            plugin_slug=plugin_slug,
            action_key=action_key,
        )
        return manifest

    def _manifest_with_provider_config(
        self,
        *,
        action_ref: str | None,
        plugin_slug: str | None,
        action_key: str | None,
    ) -> tuple[ExecutableActionManifest, dict[str, Any] | None]:
        PluginRepository(self._s).sync_builtin_plugins()
        resolved_plugin, resolved_action = self._resolve_action_key(
            action_ref=action_ref,
            plugin_slug=plugin_slug,
            action_key=action_key,
        )
        stmt = (
            select(Action, Plugin, Provider)
            .join(Plugin, col(Action.plugin_id) == col(Plugin.id))
            .outerjoin(Provider, col(Action.provider_id) == col(Provider.id))
            .where(col(Plugin.slug) == resolved_plugin, col(Action.key) == resolved_action)
        )
        row = self._s.exec(stmt).first()
        if row is None:
            raise NotFoundError(
                f"action {resolved_plugin}.{resolved_action!r} not found",
                data={"plugin_slug": resolved_plugin, "action_key": resolved_action},
            )
        action, plugin, provider = row
        return (
            parse_action_manifest(action=action, plugin=plugin, provider=provider),
            provider.config_json if provider is not None else None,
        )

    def _resolve_action_key(
        self,
        *,
        action_ref: str | None,
        plugin_slug: str | None,
        action_key: str | None,
    ) -> tuple[str, str]:
        if action_ref is not None:
            if "." not in action_ref:
                raise ValidationError("action_ref must be '<plugin>.<action_key>'")
            resolved_plugin, resolved_action = action_ref.split(".", 1)
            if not resolved_plugin or not resolved_action:
                raise ValidationError("action_ref must be '<plugin>.<action_key>'")
            return resolved_plugin, resolved_action
        if plugin_slug is None or action_key is None:
            raise ValidationError("action_ref or plugin_slug/action_key is required")
        return plugin_slug, action_key

    def _plugin_disabled_for_project(self, *, project_id: int | None, plugin_slug: str) -> bool:
        if project_id is None:
            return False
        row = self._s.exec(
            select(ProjectPlugin, Plugin)
            .join(Plugin, col(ProjectPlugin.plugin_id) == col(Plugin.id))
            .where(ProjectPlugin.project_id == project_id, col(Plugin.slug) == plugin_slug)
        ).first()
        if row is None:
            return False
        project_plugin, _plugin = row
        return project_plugin.enabled is False

    def _require_project(self, project_id: int) -> None:
        if self._s.get(Project, project_id) is None:
            raise NotFoundError(f"project {project_id} not found")
