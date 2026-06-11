"""Action manifest lookup and availability description."""

# mypy: disable-error-code=attr-defined

from __future__ import annotations

from typing import Any

from sqlmodel import col, select

from stackos.action_availability import build_action_availability, build_action_exposure
from stackos.actions.manifest import ExecutableActionManifest, parse_action_manifest
from stackos.db.models import Action, ExecutionContext, Plugin, Project, ProjectPlugin, Provider
from stackos.generated_inventory import (
    generated_action_public_key,
    generated_action_visible_for_project,
)
from stackos.provider_setup import build_provider_setup
from stackos.repositories.base import ConflictError, NotFoundError, ValidationError
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
            project_id=project_id,
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
        exposure = build_action_exposure(
            availability,
            project_id=project_id,
            plugin_slug=manifest.plugin_slug,
            provider_key=manifest.provider_key,
            requires_credential=manifest.requires_credential,
            allows_credential=manifest.allows_credential,
        )
        registered = availability.connector_registered
        return ActionDescribeOut(
            manifest=manifest,
            connector_registered=registered,
            execution_available=availability.executable,
            agent_execute_available=availability.executable,
            availability=availability,
            exposure=exposure,
            provider_setup=build_provider_setup(
                project_id=project_id,
                provider_key=manifest.provider_key,
                provider_config_json=provider_config_json,
            ),
            execution_context=_action_execution_context_guidance(
                project_id=project_id,
                manifest=manifest,
            ),
        )

    def _manifest(
        self,
        *,
        action_ref: str | None,
        plugin_slug: str | None,
        action_key: str | None,
        project_id: int | None = None,
        context_ref: str | None = None,
        credential_ref: str | None = None,
    ) -> ExecutableActionManifest:
        manifest, _provider_config_json = self._manifest_with_provider_config(
            action_ref=action_ref,
            plugin_slug=plugin_slug,
            action_key=action_key,
            project_id=project_id,
            context_ref=context_ref,
            credential_ref=credential_ref,
        )
        return manifest

    def _manifest_with_provider_config(
        self,
        *,
        action_ref: str | None,
        plugin_slug: str | None,
        action_key: str | None,
        project_id: int | None,
        context_ref: str | None = None,
        credential_ref: str | None = None,
    ) -> tuple[ExecutableActionManifest, dict[str, Any] | None]:
        PluginRepository(self._s).sync_builtin_plugins()
        resolved_plugin, resolved_action = self._resolve_action_key(
            action_ref=action_ref,
            plugin_slug=plugin_slug,
            action_key=action_key,
        )
        credential_hint = self._action_lookup_credential_hint(
            project_id=project_id,
            context_ref=context_ref,
            credential_ref=credential_ref,
        )
        stmt = (
            select(Action, Plugin, Provider)
            .join(Plugin, col(Action.plugin_id) == col(Plugin.id))
            .outerjoin(Provider, col(Action.provider_id) == col(Provider.id))
            .where(col(Plugin.slug) == resolved_plugin, col(Action.key) == resolved_action)
        )
        row = self._s.exec(stmt).first()
        if row is not None:
            action, _plugin, _provider = row
            public_action_key = generated_action_public_key(action.config_json)
            if public_action_key is not None and public_action_key != resolved_action:
                row = None
        if row is None:
            row = self._generated_public_action_row(
                plugin_slug=resolved_plugin,
                action_key=resolved_action,
                project_id=project_id,
                credential_ref=credential_hint,
            )
        if row is None:
            raise NotFoundError(
                f"action {resolved_plugin}.{resolved_action!r} not found",
                data={"plugin_slug": resolved_plugin, "action_key": resolved_action},
            )
        action, plugin, provider = row
        public_action_key = generated_action_public_key(action.config_json)
        if public_action_key is not None and public_action_key != resolved_action:
            raise NotFoundError(
                f"action {resolved_plugin}.{resolved_action!r} not found",
                data={"plugin_slug": resolved_plugin, "action_key": resolved_action},
            )
        if not generated_action_visible_for_project(
            config_json=action.config_json,
            project_id=project_id,
            action_key=action.key,
        ):
            raise NotFoundError(
                f"action {resolved_plugin}.{resolved_action!r} not found",
                data={"plugin_slug": resolved_plugin, "action_key": resolved_action},
            )
        return (
            parse_action_manifest(action=action, plugin=plugin, provider=provider),
            provider.config_json if provider is not None else None,
        )

    def _generated_public_action_row(
        self,
        *,
        plugin_slug: str,
        action_key: str,
        project_id: int | None,
        credential_ref: str | None = None,
    ) -> tuple[Action, Plugin, Provider | None] | None:
        if project_id is None:
            return None
        stmt = (
            select(Action, Plugin, Provider)
            .join(Plugin, col(Action.plugin_id) == col(Plugin.id))
            .outerjoin(Provider, col(Action.provider_id) == col(Provider.id))
            .where(col(Plugin.slug) == plugin_slug)
        )
        candidates: list[tuple[Action, Plugin, Provider | None]] = []
        for candidate in self._s.exec(stmt).all():
            action, _plugin, _provider = candidate
            if generated_action_public_key(action.config_json) != action_key:
                continue
            if not generated_action_visible_for_project(
                config_json=action.config_json,
                project_id=project_id,
                action_key=action.key,
            ):
                continue
            candidates.append(candidate)
        if not candidates:
            return None
        if credential_ref:
            credential_candidates = [
                candidate
                for candidate in candidates
                if isinstance(candidate[0].config_json, dict)
                and candidate[0].config_json.get("inventory_credential_ref") == credential_ref
            ]
            if credential_candidates:
                candidates = credential_candidates
        if len(candidates) > 1:
            candidates.sort(key=lambda row: row[0].updated_at, reverse=True)
            latest_updated_at = candidates[0][0].updated_at
            latest = [row for row in candidates if row[0].updated_at == latest_updated_at]
            if len(latest) > 1:
                raise ConflictError(
                    "generated action ref is ambiguous for this project",
                    data={
                        "action_key": action_key,
                        "project_id": project_id,
                        "candidate_keys": [row[0].key for row in latest[:8]],
                    },
                )
        return candidates[0]

    def _action_lookup_credential_hint(
        self,
        *,
        project_id: int | None,
        context_ref: str | None,
        credential_ref: str | None,
    ) -> str | None:
        if credential_ref:
            return credential_ref
        if project_id is None or not context_ref:
            return None
        row = self._s.exec(
            select(ExecutionContext).where(
                col(ExecutionContext.project_id) == project_id,
                col(ExecutionContext.context_ref) == context_ref,
            )
        ).first()
        if row is None or not isinstance(row.credential_ref, str) or not row.credential_ref:
            return None
        return row.credential_ref

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


def _action_execution_context_guidance(
    *,
    project_id: int | None,
    manifest: ExecutableActionManifest,
) -> dict[str, Any]:
    discover_args: dict[str, Any] = {"action_ref": manifest.action_ref}
    create_args: dict[str, Any] = {
        "name": f"{manifest.action_ref} context",
        "plugin_slug": manifest.plugin_slug,
        "action_ref": manifest.action_ref,
    }
    if project_id is not None:
        discover_args["project_id"] = project_id
        create_args["project_id"] = project_id
    if manifest.provider_key is not None:
        discover_args["provider_key"] = manifest.provider_key
        create_args["provider_key"] = manifest.provider_key
    return {
        "purpose": (
            "Reuse context_ref when repeated action calls share credential, provider scope, "
            "output policy, request budget, or artifact namespace."
        ),
        "decision_rule": (
            "If provider/account scope or credential selection repeats across calls, stop "
            "passing it inline and resolve or create an execution context."
        ),
        "use_when": [
            "several calls use the same provider/account/credential",
            "provider_context_json should not be repeated in every call",
            "outputs should use a shared artifact namespace or file-backed policy",
            "request budget limits should be visible to future agents",
        ],
        "discover": {
            "operation": "executionContext.discover",
            "arguments": discover_args,
        },
        "create": {
            "operation": "executionContext.create",
            "arguments": create_args,
            "notes": [
                "add credential_ref only when selecting a stored credential reference",
                "add provider_context_json only for provider/account scope, never endpoint payload",
                "add task_key, ticket_key, run_plan_id, or run_id to make future discovery scoped",
            ],
        },
        "payload_boundary": {
            "input_json": "endpoint payload for this one action call",
            "provider_context_json": "reusable provider/account scope, not endpoint payload",
            "context_ref": "preferred carrier for repeated credential/provider context",
        },
        "pass_context_ref_to": [
            "action.validate",
            "action.run",
            "action.execute",
        ],
        "provider_context_schema_source": (
            "action.provider_context_schema_json" if manifest.provider_context_schema_json else None
        ),
    }
