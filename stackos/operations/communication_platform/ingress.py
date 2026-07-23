"""Ingress endpoint operations and provider route synchronization."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlparse

import httpx
from sqlmodel import Session, col, select

from stackos.actions import ActionRepository
from stackos.artifacts import redact_secret_text
from stackos.communications import communication_profile_record_by_key, merged_provider_profile
from stackos.db.models import Credential, ResourceRecord
from stackos.mcp.context import MCPContext
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ResourceRepository

from .constants import (
    _DEFAULT_DRIVER_CONFIG,
    _DEFAULT_INGRESS_KEY,
    _DEFAULT_LOCAL_BASE_URL,
    _LOCAL_TUNNEL_PROVIDERS,
)
from .schemas import (
    IngressEndpointConfigureInput,
    IngressEndpointOut,
    IngressEndpointRefreshInput,
    IngressEndpointRoutesInput,
    IngressEndpointRoutesOut,
    IngressEndpointStatusInput,
    IngressEndpointStatusOut,
    IngressEndpointSyncInput,
    IngressEndpointSyncOut,
    IngressRouteNextActionOut,
    IngressRouteOut,
)
from .utils import (
    _communication_profile_ref,
    _record_by_resource_external_id,
    _require_project,
    _resource_records,
    _utcnow_iso,
    _validate_no_setup_secrets,
    _validate_profile_key,
)


async def ingress_endpoint_configure(
    inp: IngressEndpointConfigureInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[IngressEndpointOut]:
    _require_project(ctx.session, inp.project_id)
    _validate_profile_key(inp.key)
    public_base_url = _normalize_public_base_url(inp.public_base_url)
    local_base_url = _normalize_base_url(inp.local_base_url, require_https=False)
    driver_config = _normalize_driver_config(inp.driver, inp.driver_config)
    _validate_no_setup_secrets(
        "ingressEndpoint.configure",
        {
            "driver_config": driver_config,
            "metadata_json": inp.metadata_json,
        },
    )
    data_json = {
        "key": inp.key.strip(),
        "endpoint_ref": _ingress_endpoint_ref(inp.key),
        "driver": inp.driver,
        "enabled": inp.enabled,
        "status": "running" if public_base_url and inp.enabled else "configured",
        "public_base_url": public_base_url,
        "local_base_url": local_base_url,
        "driver_config": driver_config,
        "metadata_json": inp.metadata_json,
        "updated_at": _utcnow_iso(),
    }
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="ingress-endpoint",
        external_id=_ingress_endpoint_ref(inp.key),
        title=f"Ingress endpoint {inp.key.strip()}",
        data_json=data_json,
        provenance_json={"source": "ingressEndpoint.configure"},
    )
    return WriteEnvelope(
        data=_ingress_endpoint_out(env.data.id, env.data.project_id, env.data.data_json or {}),
        run_id=ctx.run_id,
        project_id=env.project_id,
    )


async def ingress_endpoint_refresh(
    inp: IngressEndpointRefreshInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[IngressEndpointSyncOut]:
    _require_project(ctx.session, inp.project_id)
    endpoint = _require_ingress_endpoint(ctx.session, project_id=inp.project_id, key=inp.key)
    data = dict(endpoint.data_json or {})
    driver = str(data.get("driver") or "public-url")
    driver_config = _normalize_driver_config(
        driver,
        {
            **dict(data.get("driver_config") or {}),
            **inp.driver_config,
        },
    )
    _validate_no_setup_secrets(
        "ingressEndpoint.refresh",
        {
            "driver_config": driver_config,
        },
    )
    public_base_url = _normalize_public_base_url(inp.public_base_url)
    diagnostics: dict[str, Any] = {}
    if public_base_url is None:
        public_base_url, diagnostics = await _discover_public_base_url(
            driver=driver,
            driver_config=driver_config,
        )
    data["public_base_url"] = public_base_url
    data["driver_config"] = driver_config
    data["status"] = "running" if public_base_url else "failed"
    data["last_refreshed_at"] = _utcnow_iso()
    data["metadata_json"] = {
        **dict(data.get("metadata_json") or {}),
        "last_refresh": diagnostics,
    }
    env = ResourceRepository(ctx.session).upsert_record(
        project_id=inp.project_id,
        plugin_slug="communications",
        resource_key="ingress-endpoint",
        external_id=_ingress_endpoint_ref(inp.key),
        title=f"Ingress endpoint {inp.key.strip()}",
        data_json=data,
        provenance_json={"source": "ingressEndpoint.refresh"},
    )
    endpoint_out = _ingress_endpoint_out(env.data.id, env.data.project_id, env.data.data_json or {})
    if inp.sync_profiles:
        sync_out = await _sync_ingress_endpoint(
            ctx,
            endpoint_out,
            apply_provider_webhooks=inp.apply_provider_webhooks,
            dry_run_provider_webhooks=inp.dry_run_provider_webhooks,
        )
    else:
        sync_out = IngressEndpointSyncOut(
            endpoint=endpoint_out,
            routes=_ingress_routes(ctx.session, endpoint=endpoint_out),
            provider_results=[],
            updated_profile_refs=[],
        )
    return WriteEnvelope(data=sync_out, run_id=ctx.run_id, project_id=env.project_id)


async def ingress_endpoint_routes(
    inp: IngressEndpointRoutesInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> IngressEndpointRoutesOut:
    _require_project(ctx.session, inp.project_id)
    endpoint = _require_ingress_endpoint(ctx.session, project_id=inp.project_id, key=inp.key)
    endpoint_out = _ingress_endpoint_out(endpoint.id, endpoint.project_id, endpoint.data_json or {})
    return IngressEndpointRoutesOut(
        endpoint=endpoint_out,
        routes=_ingress_routes(ctx.session, endpoint=endpoint_out),
    )


async def ingress_endpoint_sync(
    inp: IngressEndpointSyncInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> WriteEnvelope[IngressEndpointSyncOut]:
    _require_project(ctx.session, inp.project_id)
    endpoint = _require_ingress_endpoint(ctx.session, project_id=inp.project_id, key=inp.key)
    endpoint_out = _ingress_endpoint_out(endpoint.id, endpoint.project_id, endpoint.data_json or {})
    sync_out = await _sync_ingress_endpoint(
        ctx,
        endpoint_out,
        apply_provider_webhooks=inp.apply_provider_webhooks,
        dry_run_provider_webhooks=inp.dry_run_provider_webhooks,
    )
    return WriteEnvelope(data=sync_out, run_id=ctx.run_id, project_id=inp.project_id)


async def ingress_endpoint_status(
    inp: IngressEndpointStatusInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> IngressEndpointStatusOut:
    _require_project(ctx.session, inp.project_id)
    endpoint = _record_by_resource_external_id(
        ctx.session,
        project_id=inp.project_id,
        resource_key="ingress-endpoint",
        external_id=_ingress_endpoint_ref(inp.key),
    )
    if endpoint is None:
        return IngressEndpointStatusOut(
            configured=False,
            ready=False,
            notes=["No ingress endpoint is configured for this project."],
        )
    endpoint_out = _ingress_endpoint_out(endpoint.id, endpoint.project_id, endpoint.data_json or {})
    routes = _ingress_routes(ctx.session, endpoint=endpoint_out)
    ready = bool(endpoint_out.enabled and endpoint_out.public_base_url and routes)
    notes = []
    if not endpoint_out.public_base_url:
        notes.append("Set or refresh public_base_url before syncing provider webhooks.")
    if not routes:
        notes.append("No enabled provider profiles currently expose ingress routes.")
    return IngressEndpointStatusOut(
        endpoint=endpoint_out,
        routes=routes,
        configured=True,
        ready=ready,
        notes=notes,
    )


def _normalize_public_base_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return _normalize_base_url(value, require_https=True)


def _normalize_base_url(value: str, *, require_https: bool) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationError("base URL must include http(s) scheme and host")
    if require_https and parsed.scheme != "https":
        raise ValidationError("public ingress base URL must use https")
    if parsed.query or parsed.fragment:
        raise ValidationError("base URL must not include query or fragment")
    return raw


def _normalize_driver_config(driver: str, value: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        **_DEFAULT_DRIVER_CONFIG.get(driver, {}),
        **dict(value or {}),
    }
    if driver == "local-tunnel":
        provider = str(normalized.get("provider") or "").strip().lower()
        if provider not in _LOCAL_TUNNEL_PROVIDERS:
            raise ValidationError(f"unsupported local tunnel provider {provider!r}")
        provider_defaults = _LOCAL_TUNNEL_PROVIDERS[provider]
        normalized = {
            **provider_defaults,
            **normalized,
            "provider": provider,
        }
        discovery_url = normalized.get("discovery_url")
        if isinstance(discovery_url, str) and discovery_url.strip():
            normalized["discovery_url"] = _normalize_base_url(
                discovery_url,
                require_https=False,
            )
        static_host = normalized.get("static_host")
        if isinstance(static_host, str) and static_host.strip():
            normalized["static_host"] = static_host.strip().lower()
        fields = normalized.get("response_url_fields")
        if isinstance(fields, tuple):
            normalized["response_url_fields"] = list(fields)
    elif driver == "public-url":
        normalized = {}
    else:
        raise ValidationError(f"unsupported ingress driver {driver!r}")
    return normalized


async def _discover_public_base_url(
    *,
    driver: str,
    driver_config: dict[str, Any],
) -> tuple[str | None, dict[str, Any]]:
    if driver == "public-url":
        return None, {
            "driver": driver,
            "status": "failed",
            "error": "public_base_url is required for public-url ingress endpoints",
        }
    if driver != "local-tunnel":
        return None, {"driver": driver, "status": "failed", "error": "unsupported ingress driver"}
    provider = str(driver_config.get("provider") or "").strip().lower()
    if provider not in _LOCAL_TUNNEL_PROVIDERS:
        return None, {
            "driver": driver,
            "provider": provider,
            "status": "failed",
            "error": "unsupported local tunnel provider",
        }
    api_url = str(driver_config.get("discovery_url") or "")
    static_host = str(driver_config.get("static_host") or "") or None
    diagnostics: dict[str, Any] = {
        "driver": driver,
        "provider": provider,
        "source": "driver_discovery",
        "discovery_url": api_url,
    }
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(api_url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        diagnostics.update({"status": "failed", "error": str(exc)})
        return None, diagnostics
    items = []
    if isinstance(payload, dict):
        if isinstance(payload.get("endpoints"), list):
            items = payload["endpoints"]
            diagnostics["resource"] = "endpoints"
        elif isinstance(payload.get("tunnels"), list):
            items = payload["tunnels"]
            diagnostics["resource"] = "tunnels"
    if not isinstance(items, list) or not items:
        diagnostics.update({"status": "failed", "error": "local tunnel API returned no endpoints"})
        return None, diagnostics
    candidates: list[str] = []
    response_url_fields = [
        str(item)
        for item in driver_config.get("response_url_fields", [])
        if isinstance(item, str) and item
    ]
    if not response_url_fields:
        response_url_fields = ["url", "public_url"]
    for item in items:
        if not isinstance(item, dict):
            continue
        public_url = next(
            (item.get(field) for field in response_url_fields if isinstance(item.get(field), str)),
            None,
        )
        if isinstance(public_url, str) and public_url.startswith("https://"):
            candidates.append(public_url.rstrip("/"))
    diagnostics["candidate_count"] = len(candidates)
    if static_host:
        for candidate in candidates:
            if (urlparse(candidate).hostname or "").lower() == static_host.lower():
                diagnostics.update(
                    {
                        "status": "ok",
                        "selected": candidate,
                        "matched_static_host": True,
                    }
                )
                return candidate, diagnostics
    if candidates:
        diagnostics.update({"status": "ok", "selected": candidates[0]})
        return candidates[0], diagnostics
    diagnostics.update({"status": "failed", "error": "no https local tunnel endpoints are active"})
    return None, diagnostics


def _require_ingress_endpoint(
    session: Session,
    *,
    project_id: int,
    key: str,
) -> ResourceRecord:
    _validate_profile_key(key)
    record = _record_by_resource_external_id(
        session,
        project_id=project_id,
        resource_key="ingress-endpoint",
        external_id=_ingress_endpoint_ref(key),
    )
    if record is None:
        raise ValidationError("ingress endpoint is not configured")
    return record


def _ingress_endpoint_ref(key: str) -> str:
    return f"ingress-endpoint:{key.strip()}"


def _ingress_endpoint_out(
    record_id: int | None,
    project_id: int,
    data: dict[str, Any],
) -> IngressEndpointOut:
    key = str(data.get("key") or _DEFAULT_INGRESS_KEY)
    return IngressEndpointOut(
        record_id=int(record_id or 0),
        project_id=project_id,
        endpoint_ref=str(data.get("endpoint_ref") or _ingress_endpoint_ref(key)),
        key=key,
        driver=str(data.get("driver") or "public-url"),
        enabled=bool(data.get("enabled", True)),
        status=str(data.get("status") or "configured"),
        public_base_url=(
            data.get("public_base_url") if isinstance(data.get("public_base_url"), str) else None
        ),
        local_base_url=str(data.get("local_base_url") or _DEFAULT_LOCAL_BASE_URL),
        driver_config=dict(data.get("driver_config") or {}),
        last_refreshed_at=(
            data.get("last_refreshed_at")
            if isinstance(data.get("last_refreshed_at"), str)
            else None
        ),
        last_synced_at=(
            data.get("last_synced_at") if isinstance(data.get("last_synced_at"), str) else None
        ),
        metadata_json=dict(data.get("metadata_json") or {}),
    )


def _ingress_routes(session: Session, *, endpoint: IngressEndpointOut) -> list[IngressRouteOut]:
    routes: list[IngressRouteOut] = []
    seen: set[tuple[str, str]] = set()
    for record in _resource_records(
        session,
        project_id=endpoint.project_id,
        resource_key="communication-profile",
    ):
        data = dict(record.data_json or {})
        profile_key = str(data.get("key") or "")
        if not profile_key:
            continue
        profile_ref = str(data.get("profile_ref") or _communication_profile_ref(profile_key))
        facets = data.get("provider_facets")
        if not isinstance(facets, dict):
            continue
        if isinstance(facets.get("slack-bot"), dict):
            routes.append(
                _route_out(
                    endpoint=endpoint,
                    provider_key="slack-bot",
                    profile_key=profile_key,
                    profile_ref=profile_ref,
                    profile_resource_key="communication-profile",
                    remote_status="manual_provider_update_required",
                )
            )
            seen.add(("slack-bot", profile_key))
        if isinstance(facets.get("telegram-bot"), dict):
            key = ("telegram-bot", profile_key)
            if key in seen:
                continue
            routes.append(
                _route_out(
                    endpoint=endpoint,
                    provider_key="telegram-bot",
                    profile_key=profile_key,
                    profile_ref=profile_ref,
                    profile_resource_key="communication-profile",
                    remote_status="provider_webhook_not_checked",
                )
            )
            seen.add(key)
    hubspot_credentials = session.exec(
        select(Credential).where(
            col(Credential.project_id) == endpoint.project_id,
            col(Credential.provider_key) == "hubspot",
            col(Credential.revoked_at).is_(None),
        )
    ).all()
    for credential in sorted(hubspot_credentials, key=lambda item: item.profile_key):
        config = dict(credential.config_json or {})
        if config.get("webhook_enabled") is not True:
            continue
        routes.append(
            _route_out(
                endpoint=endpoint,
                provider_key="hubspot",
                profile_key=credential.profile_key,
                profile_ref=credential.credential_ref,
                profile_resource_key="credential",
                remote_status="manual_provider_update_required",
            )
        )
    return routes


def _route_out(
    *,
    endpoint: IngressEndpointOut,
    provider_key: str,
    profile_key: str,
    profile_ref: str,
    profile_resource_key: str,
    remote_status: str,
) -> IngressRouteOut:
    path = _provider_ingress_path(
        project_id=endpoint.project_id,
        provider_key=provider_key,
        profile_key=profile_key,
    )
    ingress_url = _join_base_path(endpoint.public_base_url, path)
    next_action = _route_next_action(
        provider_key=provider_key,
        profile_key=profile_key,
        remote_status=remote_status,
        ingress_url=ingress_url,
    )
    return IngressRouteOut(
        provider_key=provider_key,
        profile_key=profile_key,
        profile_ref=profile_ref,
        profile_resource_key=profile_resource_key,
        ingress_path=path,
        ingress_url=ingress_url,
        local_url=_join_base_path(endpoint.local_base_url, path),
        remote_status=remote_status,
        notes=_route_notes(endpoint=endpoint, provider_key=provider_key),
        action_required=next_action is not None,
        next_action=next_action,
    )


def _route_notes(*, endpoint: IngressEndpointOut, provider_key: str) -> list[str]:
    notes: list[str] = []
    if not endpoint.public_base_url:
        notes.append("public_base_url is not configured")
    if provider_key == "slack-bot":
        notes.append("Slack Events API and Interactivity URLs must be set in the Slack app.")
    if provider_key == "telegram-bot":
        notes.append("Telegram setWebhook can be applied by ingressEndpoint.sync.")
    if provider_key == "hubspot":
        notes.append(
            "HubSpot webhook Target URL and any custom workflow action URL must be set "
            "in the HubSpot app configuration."
        )
    return notes


def _route_next_action(
    *,
    provider_key: str,
    profile_key: str,
    remote_status: str,
    ingress_url: str | None,
) -> IngressRouteNextActionOut | None:
    if remote_status != "manual_provider_update_required":
        return None
    if provider_key == "slack-bot":
        return IngressRouteNextActionOut(
            kind="manual-provider-update",
            label="Copy webhook URL",
            title=f"Update Slack webhook for {profile_key}",
            instructions=(
                "Copy this URL into the Slack app Event Subscriptions Request URL and "
                "Interactivity Request URL fields."
            ),
            url=ingress_url,
            provider_fields=[
                "Event Subscriptions Request URL",
                "Interactivity Request URL",
            ],
        )
    if provider_key == "hubspot":
        return IngressRouteNextActionOut(
            kind="manual-provider-update",
            label="Copy webhook URL",
            title=f"Update HubSpot ingress for {profile_key}",
            instructions=(
                "Copy this URL into the HubSpot app Webhooks Target URL. Use the same "
                "URL as actionUrl only for explicitly allowlisted custom workflow actions."
            ),
            url=ingress_url,
            provider_fields=[
                "Webhooks Target URL",
                "Custom workflow action actionUrl",
            ],
        )
    return IngressRouteNextActionOut(
        kind="manual-provider-update",
        label="Copy webhook URL",
        title=f"Update {provider_key} webhook for {profile_key}",
        instructions="Copy this URL into the provider webhook configuration.",
        url=ingress_url,
        provider_fields=["Webhook URL"],
    )


def _provider_ingress_path(*, project_id: int, provider_key: str, profile_key: str) -> str:
    encoded = quote(profile_key, safe="")
    if provider_key == "slack-bot":
        return f"/api/v1/ingress/slack/{project_id}/{encoded}"
    if provider_key == "telegram-bot":
        return f"/api/v1/ingress/telegram/{project_id}/{encoded}"
    if provider_key == "hubspot":
        return f"/api/v1/ingress/hubspot/{project_id}/{encoded}"
    raise ValidationError(f"provider {provider_key!r} does not support webhook ingress")


def _join_base_path(base_url: str | None, path: str) -> str | None:
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}{path}"


async def _sync_ingress_endpoint(
    ctx: MCPContext,
    endpoint: IngressEndpointOut,
    *,
    apply_provider_webhooks: bool,
    dry_run_provider_webhooks: bool,
) -> IngressEndpointSyncOut:
    routes = _ingress_routes(ctx.session, endpoint=endpoint)
    updated_profile_refs: list[str] = []
    provider_results: list[dict[str, Any]] = []
    resources = ResourceRepository(ctx.session)
    for route in routes:
        if not route.ingress_url:
            provider_results.append(
                {
                    "provider_key": route.provider_key,
                    "profile_key": route.profile_key,
                    "status": "skipped",
                    "reason": "public_base_url_missing",
                }
            )
            continue
        if route.profile_resource_key == "communication-profile":
            updated = _sync_communication_profile_route(
                ctx.session,
                resources=resources,
                endpoint=endpoint,
                route=route,
            )
            if updated:
                updated_profile_refs.append(route.profile_ref)
        if route.provider_key == "telegram-bot":
            provider_results.append(
                await _maybe_apply_telegram_webhook(
                    ctx,
                    project_id=endpoint.project_id,
                    route=route,
                    apply_provider_webhooks=apply_provider_webhooks,
                    dry_run_provider_webhooks=dry_run_provider_webhooks,
                )
            )
        elif route.provider_key in {"slack-bot", "hubspot"}:
            next_action = (
                route.next_action.model_dump(mode="json") if route.next_action is not None else {}
            )
            provider_results.append(
                {
                    "provider_key": route.provider_key,
                    "profile_key": route.profile_key,
                    "status": "manual_provider_update_required",
                    "request_url": route.ingress_url,
                    "next_action": next_action or None,
                    "notes": [next_action.get("instructions")]
                    if isinstance(next_action.get("instructions"), str)
                    else [],
                }
            )
    _mark_ingress_endpoint_synced(ctx.session, endpoint=endpoint, route_count=len(routes))
    updated_endpoint = _require_ingress_endpoint(
        ctx.session,
        project_id=endpoint.project_id,
        key=endpoint.key,
    )
    updated_endpoint_out = _ingress_endpoint_out(
        updated_endpoint.id,
        updated_endpoint.project_id,
        updated_endpoint.data_json or {},
    )
    return IngressEndpointSyncOut(
        endpoint=updated_endpoint_out,
        routes=_ingress_routes(ctx.session, endpoint=updated_endpoint_out),
        provider_results=provider_results,
        updated_profile_refs=sorted(set(updated_profile_refs)),
    )


def _sync_communication_profile_route(
    session: Session,
    *,
    resources: ResourceRepository,
    endpoint: IngressEndpointOut,
    route: IngressRouteOut,
) -> bool:
    record = _record_by_resource_external_id(
        session,
        project_id=endpoint.project_id,
        resource_key="communication-profile",
        external_id=route.profile_ref,
    )
    if record is None:
        return False
    data = dict(record.data_json or {})
    facets = dict(data.get("provider_facets") or {})
    facet = dict(facets.get(route.provider_key) or {})
    facet.update(
        {
            "ingress_path": route.ingress_path,
            "ingress_url": route.ingress_url,
            "ingress_public_base_url": endpoint.public_base_url,
            "ingress_driver": endpoint.driver,
            "ingress_endpoint_ref": endpoint.endpoint_ref,
        }
    )
    if route.provider_key == "telegram-bot":
        host = urlparse(route.ingress_url or "").hostname
        allowed_hosts = {str(item) for item in facet.get("allowed_webhook_hosts") or []}
        if host:
            allowed_hosts.add(host.lower())
        refs = dict(facet.get("refs") or {})
        refs["ingress_url"] = str(route.ingress_url)
        refs["ingress_endpoint_ref"] = endpoint.endpoint_ref
        facet.update(
            {
                "ingress_mode": "webhook",
                "webhook_base_url": endpoint.public_base_url,
                "allowed_webhook_hosts": sorted(allowed_hosts),
                "refs": refs,
                "webhook_policy": {
                    **dict(facet.get("webhook_policy") or {}),
                    "driver": endpoint.driver,
                    "endpoint_ref": endpoint.endpoint_ref,
                    "allowed_hosts": sorted(allowed_hosts),
                },
            }
        )
    facets[route.provider_key] = facet
    data["provider_facets"] = facets
    data["metadata_json"] = {
        **dict(data.get("metadata_json") or {}),
        "last_ingress_sync_at": _utcnow_iso(),
    }
    resources.upsert_record(
        project_id=endpoint.project_id,
        plugin_slug="communications",
        resource_key="communication-profile",
        external_id=route.profile_ref,
        title=str(
            dict(data.get("identity") or {}).get("display_name")
            or data.get("key")
            or route.profile_key
        ),
        data_json=data,
        provenance_json={"source": "ingressEndpoint.sync"},
    )
    return True


async def _maybe_apply_telegram_webhook(
    ctx: MCPContext,
    *,
    project_id: int,
    route: IngressRouteOut,
    apply_provider_webhooks: bool,
    dry_run_provider_webhooks: bool,
) -> dict[str, Any]:
    if not apply_provider_webhooks:
        return {
            "provider_key": "telegram-bot",
            "profile_key": route.profile_key,
            "status": "profile_updated",
            "remote_status": "not_applied",
            "webhook_url": route.ingress_url,
        }
    credential_ref = _credential_ref_for_profile(
        ctx.session,
        project_id=project_id,
        provider_key="telegram-bot",
        profile_key=_telegram_auth_profile_key(
            ctx.session,
            project_id=project_id,
            profile_key=route.profile_key,
        ),
    )
    if credential_ref is None:
        return {
            "provider_key": "telegram-bot",
            "profile_key": route.profile_key,
            "status": "missing_credential",
            "webhook_url": route.ingress_url,
        }
    try:
        env = await ActionRepository(ctx.session).execute(
            project_id=project_id,
            action_ref="communications.telegram-bot.webhook.set",
            input_json={
                "profile_key": route.profile_key,
                "webhook_url": route.ingress_url,
            },
            credential_ref=credential_ref,
            dry_run=dry_run_provider_webhooks,
            metadata_json={"source": "ingressEndpoint.sync"},
        )
    except Exception as exc:
        return {
            "provider_key": "telegram-bot",
            "profile_key": route.profile_key,
            "status": "failed",
            "webhook_url": route.ingress_url,
            "error": redact_secret_text(str(exc)),
        }
    return {
        "provider_key": "telegram-bot",
        "profile_key": route.profile_key,
        "status": (
            "remote_webhook_dry_run" if dry_run_provider_webhooks else "remote_webhook_updated"
        ),
        "webhook_url": route.ingress_url,
        "action_call_id": env.data.action_call.id,
    }


def _telegram_auth_profile_key(session: Session, *, project_id: int, profile_key: str) -> str:
    record = communication_profile_record_by_key(
        session,
        project_id=project_id,
        key=profile_key,
    )
    data = (
        merged_provider_profile(dict(record.data_json or {}), "telegram-bot")
        if record is not None
        else {}
    )
    return str(data.get("auth_profile_key") or "default")


def _credential_ref_for_profile(
    session: Session,
    *,
    project_id: int,
    provider_key: str,
    profile_key: str,
) -> str | None:
    row = session.exec(
        select(Credential).where(
            col(Credential.project_id) == project_id,
            col(Credential.provider_key) == provider_key,
            col(Credential.profile_key) == profile_key,
            col(Credential.revoked_at).is_(None),
        )
    ).first()
    return row.credential_ref if row is not None else None


def _mark_ingress_endpoint_synced(
    session: Session,
    *,
    endpoint: IngressEndpointOut,
    route_count: int,
) -> None:
    record = _record_by_resource_external_id(
        session,
        project_id=endpoint.project_id,
        resource_key="ingress-endpoint",
        external_id=endpoint.endpoint_ref,
    )
    if record is None:
        return
    data = dict(record.data_json or {})
    data["status"] = "running" if endpoint.public_base_url else data.get("status") or "configured"
    data["last_synced_at"] = _utcnow_iso()
    data["metadata_json"] = {
        **dict(data.get("metadata_json") or {}),
        "last_sync": {"route_count": route_count},
    }
    ResourceRepository(session).upsert_record(
        project_id=endpoint.project_id,
        plugin_slug="communications",
        resource_key="ingress-endpoint",
        external_id=endpoint.endpoint_ref,
        title=f"Ingress endpoint {endpoint.key}",
        data_json=data,
        provenance_json={"source": "ingressEndpoint.sync"},
    )
