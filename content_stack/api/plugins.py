"""StackOS plugin/catalog REST routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, status
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session

from content_stack.api.deps import get_session
from content_stack.api.envelopes import WriteResponse, write_response
from content_stack.repositories.plugins import (
    ActionOut,
    CapabilityOut,
    CatalogOut,
    PluginCatalogOut,
    PluginOut,
    PluginRepository,
    ProjectPluginOut,
    ProviderOut,
)

router = APIRouter(prefix="/api/v1", tags=["plugins"])


class PluginEnableRequest(BaseModel):
    """Local-admin setup body for enabling a plugin on a project."""

    model_config = ConfigDict(json_schema_extra={"example": {"config_json": {}}})

    config_json: dict[str, Any] | None = None


@router.get("/plugins", response_model=list[PluginOut])
async def list_plugins(
    project_id: int | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[PluginOut]:
    """List installed plugins, optionally annotated with project enablement."""
    return PluginRepository(session).list_plugins(project_id=project_id)


@router.get("/plugins/{plugin_slug}", response_model=PluginOut)
async def get_plugin(
    plugin_slug: str,
    project_id: int | None = Query(default=None),
    session: Session = Depends(get_session),
) -> PluginOut:
    """Describe one installed plugin."""
    return PluginRepository(session).get_plugin(plugin_slug, project_id=project_id)


@router.get("/catalog", response_model=CatalogOut)
async def list_catalog(session: Session = Depends(get_session)) -> CatalogOut:
    """Return the full installed plugin catalog."""
    return PluginRepository(session).catalog()


@router.get("/catalog/{plugin_slug}", response_model=PluginCatalogOut)
async def describe_catalog(
    plugin_slug: str,
    session: Session = Depends(get_session),
) -> PluginCatalogOut:
    """Return one plugin's catalog contribution."""
    catalog = PluginRepository(session).catalog(plugin_slug=plugin_slug)
    return catalog.plugins[0]


@router.get("/capabilities", response_model=list[CapabilityOut])
async def list_capabilities(
    plugin_slug: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[CapabilityOut]:
    """List registered capabilities."""
    return PluginRepository(session).list_capabilities(plugin_slug=plugin_slug)


@router.get("/capabilities/{capability_key}", response_model=CapabilityOut)
async def describe_capability(
    capability_key: str,
    plugin_slug: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> CapabilityOut:
    """Describe one capability; pass plugin_slug if the key is ambiguous."""
    return PluginRepository(session).get_capability(key=capability_key, plugin_slug=plugin_slug)


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(
    plugin_slug: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[ProviderOut]:
    """List registered providers."""
    return PluginRepository(session).list_providers(plugin_slug=plugin_slug)


@router.get("/providers/{provider_key}", response_model=ProviderOut)
async def describe_provider(
    provider_key: str,
    plugin_slug: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> ProviderOut:
    """Describe one provider; pass plugin_slug if the key is ambiguous."""
    return PluginRepository(session).get_provider(key=provider_key, plugin_slug=plugin_slug)


@router.get("/actions", response_model=list[ActionOut])
async def list_actions(
    plugin_slug: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[ActionOut]:
    """List registered action schemas."""
    return PluginRepository(session).list_actions(plugin_slug=plugin_slug)


@router.get("/actions/{action_key}", response_model=ActionOut)
async def describe_action(
    action_key: str,
    plugin_slug: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> ActionOut:
    """Describe one action schema; pass plugin_slug if the key is ambiguous."""
    return PluginRepository(session).get_action(key=action_key, plugin_slug=plugin_slug)


@router.post(
    "/projects/{project_id}/plugins/{plugin_slug}/enable",
    status_code=status.HTTP_200_OK,
    response_model=WriteResponse[ProjectPluginOut],
)
async def enable_project_plugin(
    project_id: int,
    plugin_slug: str,
    body: PluginEnableRequest | None = Body(default=None),
    session: Session = Depends(get_session),
) -> WriteResponse[ProjectPluginOut]:
    """Local-admin setup route for enabling a plugin on a project."""
    env = PluginRepository(session).enable(
        project_id=project_id,
        plugin_slug=plugin_slug,
        config_json=body.config_json if body is not None else None,
    )
    return write_response(env)


@router.post(
    "/projects/{project_id}/plugins/{plugin_slug}/disable",
    status_code=status.HTTP_200_OK,
    response_model=WriteResponse[ProjectPluginOut],
)
async def disable_project_plugin(
    project_id: int,
    plugin_slug: str,
    session: Session = Depends(get_session),
) -> WriteResponse[ProjectPluginOut]:
    """Local-admin setup route for disabling a plugin on a project."""
    env = PluginRepository(session).disable(project_id=project_id, plugin_slug=plugin_slug)
    return write_response(env)


__all__ = ["router"]
