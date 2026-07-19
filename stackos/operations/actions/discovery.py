"""Read-only action discovery, description, and validation handlers."""

from __future__ import annotations

import re
from typing import Any

from stackos.action_availability import ActionAvailabilityOut
from stackos.actions import ActionDescribeOut, ActionRepository, ActionValidationOut
from stackos.mcp.context import MCPContext
from stackos.mcp.streaming import ProgressEmitter
from stackos.repositories.plugins import ActionOut, PluginRepository

from .schemas import (
    ActionDescribeInput,
    ActionListInput,
    ActionListItemOut,
    ActionListOut,
    ActionValidateInput,
)


async def action_list(
    inp: ActionListInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ActionListOut:
    project_id = inp.project_id if inp.project_id is not None else ctx.project_id
    rows = PluginRepository(ctx.session).list_actions(
        plugin_slug=inp.plugin_slug,
        project_id=project_id,
    )
    scored: list[tuple[ActionOut, float]] = []
    for row in rows:
        score = _action_query_score(row, inp.query)
        if score <= 0:
            continue
        if _matches_action_filters(
            row,
            provider_key=inp.provider_key,
            capability_key=inp.capability_key,
            executable=inp.executable,
        ):
            scored.append((row, score))
    if (inp.query or "").strip():
        scored.sort(key=lambda item: (-item[1], item[0].plugin_slug, item[0].key))
    matched = [row for row, _score in scored]
    filtered = [
        row
        for row in matched
        if inp.include_unavailable_integrations or row.exposure.visible_by_default
    ]
    return ActionListOut(
        items=[_action_list_item(row, row.availability) for row in filtered],
        count=len(filtered),
        hidden_count=len(matched) - len(filtered),
        filters={
            key: value
            for key, value in {
                "project_id": project_id,
                "plugin_slug": inp.plugin_slug,
                "provider_key": inp.provider_key,
                "capability_key": inp.capability_key,
                "query": inp.query,
                "executable": inp.executable,
                "include_unavailable_integrations": inp.include_unavailable_integrations,
            }.items()
            if value is not None
        },
    )


def _matches_action_filters(
    row: ActionOut,
    *,
    provider_key: str | None,
    capability_key: str | None,
    executable: bool | None,
) -> bool:
    if provider_key is not None and row.provider_key != provider_key:
        return False
    if capability_key is not None and row.capability_key != capability_key:
        return False
    return executable is None or row.availability.executable == executable


_SEARCH_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _action_query_score(row: ActionOut, query: str | None) -> float:
    normalized_query = _normalize_search_text(query or "")
    if not normalized_query:
        return 1.0
    haystack = _action_search_haystack(row)
    if normalized_query in haystack:
        return 100.0 + len(normalized_query) / 1000
    query_tokens = _search_tokens(normalized_query)
    if not query_tokens:
        return 1.0
    haystack_tokens = set(_search_tokens(haystack))
    if not haystack_tokens:
        return 0.0
    matched = [token for token in query_tokens if token in haystack_tokens]
    if not matched:
        return 0.0
    ratio = len(matched) / len(query_tokens)
    required_ratio = 1.0 if len(query_tokens) <= 2 else 0.5
    if ratio < required_ratio:
        return 0.0
    return ratio * 10 + len(matched) / 100


def _action_search_haystack(row: ActionOut) -> str:
    values: list[str] = [
        row.action_ref,
        row.key,
        row.name,
        row.description,
        row.operation,
        row.provider_key or "",
        row.capability_key or "",
        row.risk_level,
    ]
    values.extend(_json_search_values(row.config_json))
    values.extend(_json_search_values(row.input_schema_json))
    values.extend(_json_search_values(row.output_schema_json))
    return _normalize_search_text(" ".join(value for value in values if value))


def _json_search_values(value: Any, *, limit: int = 600) -> list[str]:
    out: list[str] = []

    def visit(item: Any) -> None:
        if len(out) >= limit:
            return
        if isinstance(item, dict):
            for key, nested in item.items():
                if isinstance(key, str):
                    out.append(key)
                visit(nested)
                if len(out) >= limit:
                    return
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
                if len(out) >= limit:
                    return
        elif isinstance(item, str):
            out.append(item)
        elif isinstance(item, int | float | bool):
            out.append(str(item))

    visit(value)
    return out


def _normalize_search_text(value: str) -> str:
    return " ".join(_SEARCH_TOKEN_RE.findall(value.replace("_", " ").replace("-", " ").lower()))


def _search_tokens(value: str) -> list[str]:
    raw_tokens = _SEARCH_TOKEN_RE.findall(value)
    tokens: list[str] = []
    for token in raw_tokens:
        variants = _search_token_variants(token)
        for variant in variants:
            if variant not in tokens:
                tokens.append(variant)
    return tokens


def _search_token_variants(token: str) -> list[str]:
    variants = [token]
    if len(token) > 3 and token.endswith("s"):
        variants.append(token[:-1])
    synonyms = {
        "metric": ["metrics"],
        "metrics": ["metric"],
        "partner": ["partners", "partnership", "partnerships"],
        "partners": ["partner", "partnership", "partnerships"],
        "partnership": ["partner", "partners", "relationships", "relationship"],
        "partnerships": ["partner", "partners", "relationships", "relationship"],
        "relation": ["relationship", "relationships"],
        "relationship": ["relation", "relationships", "partnership", "partnerships"],
        "relationships": ["relation", "relationship", "partnership", "partnerships"],
        "report": ["reporting", "reports"],
        "reports": ["report", "reporting"],
        "reporting": ["report", "reports"],
    }
    variants.extend(synonyms.get(token, []))
    return variants


def _action_list_item(row: ActionOut, availability: ActionAvailabilityOut) -> ActionListItemOut:
    return ActionListItemOut(
        action_ref=row.action_ref,
        plugin_slug=row.plugin_slug,
        key=row.key,
        name=row.name,
        description=row.description,
        provider_key=row.provider_key,
        capability_key=row.capability_key,
        risk_level=row.risk_level,
        operation=row.operation,
        connector_key=row.connector_key,
        requires_credential=row.requires_credential,
        allows_credential=row.allows_credential,
        budget_kind=row.budget_kind,
        executable=availability.executable,
        availability_status=availability.status,
        availability_reasons=list(availability.reasons),
        credential_state=availability.credential_state,
        budget_state=availability.budget_state,
        exposure=row.exposure,
    )


async def action_describe(
    inp: ActionDescribeInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ActionDescribeOut:
    project_id = inp.project_id if inp.project_id is not None else ctx.project_id
    return ActionRepository(ctx.session).describe(
        action_ref=inp.action_ref,
        plugin_slug=inp.plugin_slug,
        action_key=inp.action_key,
        project_id=project_id,
    )


async def action_validate(
    inp: ActionValidateInput,
    ctx: MCPContext,
    _emitter: ProgressEmitter,
) -> ActionValidationOut:
    project_id = inp.project_id if inp.project_id is not None else ctx.project_id
    return ActionRepository(ctx.session).validate(
        project_id=project_id,
        action_ref=inp.action_ref,
        plugin_slug=inp.plugin_slug,
        action_key=inp.action_key,
        input_json=inp.input_json,
        context_ref=inp.context_ref,
        provider_context_json=inp.provider_context_json,
        credential_ref=inp.credential_ref,
    )


__all__ = ["action_describe", "action_list", "action_validate"]
