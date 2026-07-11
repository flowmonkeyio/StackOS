"""Persist one declarative plugin manifest into catalog tables."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, select

from stackos.db.models import (
    Action,
    ActionVersion,
    Capability,
    Plugin,
    PluginSource,
    Provider,
    Resource,
)
from stackos.plugins.manifest import PluginManifest
from stackos.repositories.base import ConflictError


def _utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def _required_id(value: int | None) -> int:
    if value is None:
        raise RuntimeError("expected persisted row id")
    return int(value)


def sync_plugin_manifest(session: Session, manifest: PluginManifest) -> Plugin:
    now = _utcnow()
    row = session.exec(select(Plugin).where(Plugin.slug == manifest.slug)).first()
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
    session.add(row)
    session.flush()
    assert row.id is not None

    providers_by_key: dict[str, Provider] = {}
    for provider_manifest in manifest.providers:
        provider_config = dict(provider_manifest.config or {})
        if provider_manifest.auth_methods:
            provider_config["auth_methods"] = [
                method.model_dump(mode="json") for method in provider_manifest.auth_methods
            ]
        provider = session.exec(
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
                config_json=provider_config or None,
            )
        else:
            provider.name = provider_manifest.name
            provider.description = provider_manifest.description
            provider.auth_type = provider_manifest.auth_type
            provider.config_json = provider_config or None
            provider.updated_at = now
        session.add(provider)
        session.flush()
        providers_by_key[provider.key] = provider

    for capability_manifest in manifest.capabilities:
        capability = session.exec(
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
        session.add(capability)

    for resource_manifest in manifest.resources:
        resource = session.exec(
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
        session.add(resource)

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
        action = session.exec(
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
        session.add(action)
        session.flush()
        assert action.id is not None
        version = session.exec(
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
            session.add(version)
    if manifest.slug == "trackbooth":
        from stackos.actions.trackbooth import retire_removed_trackbooth_actions

        retire_removed_trackbooth_actions(
            session=session,
            plugin_id=_required_id(row.id),
            now=now,
        )
    return row
