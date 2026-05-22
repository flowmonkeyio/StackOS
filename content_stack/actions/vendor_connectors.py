"""Generic StackOS action connectors for existing vendor wrappers."""

from __future__ import annotations

import math
from typing import Any

import httpx

from content_stack.actions.connectors import (
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from content_stack.integrations.ahrefs import AhrefsIntegration
from content_stack.integrations.dataforseo import DataForSeoIntegration
from content_stack.integrations.firecrawl import FirecrawlIntegration
from content_stack.integrations.jina_reader import JinaReaderIntegration
from content_stack.integrations.reddit import RedditIntegration
from content_stack.repositories.base import ValidationError


def _issue(path: str, message: str, code: str = "validation_error") -> ActionValidationIssue:
    return ActionValidationIssue(path=path, message=message, code=code)


def _required_str(payload: dict[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(_issue(f"$.{key}", f"{key} is required", "required"))


def _optional_str(payload: dict[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        issues.append(_issue(f"$.{key}", f"{key} must be a string", "type_mismatch"))


def _int_range(
    payload: dict[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    minimum: int,
    maximum: int,
) -> None:
    value = payload.get(key)
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or value > maximum:
        issues.append(
            _issue(
                f"$.{key}",
                f"{key} must be an integer between {minimum} and {maximum}",
                "range",
            )
        )


def _bool_field(payload: dict[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, bool):
        issues.append(_issue(f"$.{key}", f"{key} must be a boolean", "type_mismatch"))


def _str_list(
    payload: dict[str, Any],
    key: str,
    issues: list[ActionValidationIssue],
    *,
    required: bool = False,
    length: int | None = None,
) -> None:
    value = payload.get(key)
    if value is None:
        if required:
            issues.append(_issue(f"$.{key}", f"{key} is required", "required"))
        return
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        issues.append(_issue(f"$.{key}", f"{key} must be an array of strings", "type_mismatch"))
        return
    if length is not None and len(value) != length:
        issues.append(_issue(f"$.{key}", f"{key} must contain {length} items", "length"))


def _dict_field(payload: dict[str, Any], key: str, issues: list[ActionValidationIssue]) -> None:
    value = payload.get(key)
    if value is not None and not isinstance(value, dict):
        issues.append(_issue(f"$.{key}", f"{key} must be an object", "type_mismatch"))


def _unknown_operation(request: ActionConnectorRequest) -> list[ActionValidationIssue]:
    return [_issue("$.operation", f"unsupported operation {request.operation!r}", "enum_mismatch")]


def _result(vendor: str, operation: str, result: Any, cost_usd: float) -> ActionConnectorResult:
    output = result if isinstance(result, dict) else {"data": result}
    return ActionConnectorResult(
        output_json=output,
        metadata_json={"vendor": vendor, "operation": operation},
        cost_cents=_cost_cents(cost_usd),
    )


def _cost_cents(cost_usd: float) -> int:
    if cost_usd <= 0:
        return 0
    return max(1, math.ceil(cost_usd * 100))


def _credential_payload(request: ActionConnectorRequest, *, required: bool = True) -> bytes:
    if request.credential is None:
        if required:
            target = request.provider_key or request.action_ref
            raise ValidationError(f"{target} requires a credential")
        return b""
    return request.credential.plaintext_payload


class FirecrawlActionConnector:
    """Decision-free adapter for Firecrawl utility actions."""

    key = "firecrawl"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "scrape":
                _required_str(payload, "url", issues)
                _str_list(payload, "formats", issues)
                _bool_field(payload, "only_main_content", issues)
            case "crawl":
                _required_str(payload, "url", issues)
                _int_range(payload, "max_depth", issues, minimum=1, maximum=10)
                _int_range(payload, "limit", issues, minimum=1, maximum=1000)
            case "map":
                _required_str(payload, "url", issues)
                _optional_str(payload, "search", issues)
            case "extract":
                _required_str(payload, "url", issues)
                _dict_field(payload, "schema", issues)
                _optional_str(payload, "prompt", issues)
            case _:
                issues.extend(_unknown_operation(request))
        return issues

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        operation = request.operation
        if operation == "crawl":
            limit = request.input_json.get("limit", 25)
            pages = limit if isinstance(limit, int) and not isinstance(limit, bool) else 25
            return _cost_cents(FirecrawlIntegration._ESTIMATED_COSTS["crawl"] * pages)
        cost = FirecrawlIntegration._ESTIMATED_COSTS.get(operation, 0.001)
        return _cost_cents(cost)

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        payload = request.input_json
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = FirecrawlIntegration(
                payload=_credential_payload(request),
                project_id=request.project_id,
                http=http,
            )
            match request.operation:
                case "scrape":
                    result = await client.scrape(
                        url=str(payload["url"]),
                        formats=payload.get("formats"),
                        only_main_content=bool(payload.get("only_main_content", True)),
                    )
                case "crawl":
                    result = await client.crawl(
                        url=str(payload["url"]),
                        max_depth=int(payload.get("max_depth", 2)),
                        limit=int(payload.get("limit", 25)),
                    )
                case "map":
                    result = await client.map(
                        url=str(payload["url"]),
                        search=payload.get("search"),
                    )
                case "extract":
                    result = await client.extract(
                        url=str(payload["url"]),
                        schema=payload.get("schema"),
                        prompt=payload.get("prompt"),
                    )
                case _:
                    raise ValidationError(f"unsupported Firecrawl operation {request.operation!r}")
        return _result("firecrawl", request.operation, result.data, result.cost_usd)


class JinaActionConnector:
    """Decision-free adapter for Jina Reader utility actions."""

    key = "jina"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation != "read":
            return _unknown_operation(request)
        issues: list[ActionValidationIssue] = []
        _required_str(request.input_json, "url", issues)
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        payload = request.input_json
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = JinaReaderIntegration(
                payload=_credential_payload(request, required=False),
                project_id=request.project_id,
                http=http,
            )
            result = await client.read(url=str(payload["url"]))
        return _result("jina", request.operation, result.data, result.cost_usd)


class RedditActionConnector:
    """Decision-free adapter for Reddit research utility actions."""

    key = "reddit"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "search_subreddit":
                _required_str(payload, "subreddit", issues)
                _required_str(payload, "query", issues)
                _optional_str(payload, "sort", issues)
                _int_range(payload, "limit", issues, minimum=1, maximum=100)
            case "top_questions":
                _required_str(payload, "subreddit", issues)
                _optional_str(payload, "time_filter", issues)
                _int_range(payload, "limit", issues, minimum=1, maximum=100)
            case _:
                issues.extend(_unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        payload = request.input_json
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = RedditIntegration(
                payload=_credential_payload(request),
                project_id=request.project_id,
                http=http,
            )
            match request.operation:
                case "search_subreddit":
                    result = await client.search_subreddit(
                        subreddit=str(payload["subreddit"]),
                        query=str(payload["query"]),
                        sort=str(payload.get("sort", "relevance")),
                        limit=int(payload.get("limit", 25)),
                    )
                case "top_questions":
                    result = await client.top_questions(
                        subreddit=str(payload["subreddit"]),
                        time_filter=str(payload.get("time_filter", "month")),
                        limit=int(payload.get("limit", 50)),
                    )
                case _:
                    raise ValidationError(f"unsupported Reddit operation {request.operation!r}")
        return _result("reddit", request.operation, result.data, result.cost_usd)


class DataForSeoActionConnector:
    """Decision-free adapter for DataForSEO actions."""

    key = "dataforseo"

    _OP_COSTS = DataForSeoIntegration._PRE_EMPT_COSTS

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "keyword.research" | "keyword_volume":
                _str_list(payload, "keywords", issues, required=True)
                _optional_str(payload, "location", issues)
                _optional_str(payload, "language", issues)
            case "serp.analyze" | "serp":
                _required_str(payload, "keyword", issues)
                _optional_str(payload, "location", issues)
                _optional_str(payload, "language", issues)
                _int_range(payload, "depth", issues, minimum=1, maximum=700)
            case "domain_intersection":
                _str_list(payload, "domains", issues, required=True, length=2)
                _optional_str(payload, "location", issues)
                _optional_str(payload, "language", issues)
            case "keywords_for_site":
                _required_str(payload, "target", issues)
                _optional_str(payload, "location", issues)
                _optional_str(payload, "language", issues)
            case "paa":
                _required_str(payload, "keyword", issues)
                _optional_str(payload, "location", issues)
                _optional_str(payload, "language", issues)
            case _:
                issues.extend(_unknown_operation(request))
        return issues

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        operation = self._wrapper_operation(request.operation)
        return _cost_cents(self._OP_COSTS.get(operation, 0.001))

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.credential is None:
            raise ValidationError("DataForSEO action requires a resolved credential")
        login = (request.credential.config_json or {}).get("login")
        if not isinstance(login, str) or not login:
            raise ValidationError("DataForSEO credential missing config_json.login")
        payload = request.input_json
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = DataForSeoIntegration(
                payload=request.credential.plaintext_payload,
                project_id=request.project_id,
                http=http,
                login=login,
            )
            match request.operation:
                case "keyword.research" | "keyword_volume":
                    result = await client.keyword_volume(
                        keywords=list(payload["keywords"]),
                        location=str(payload.get("location", "United States")),
                        language=str(payload.get("language", "en")),
                    )
                case "serp.analyze" | "serp":
                    result = await client.serp(
                        keyword=str(payload["keyword"]),
                        location=str(payload.get("location", "United States")),
                        language=str(payload.get("language", "en")),
                        depth=int(payload.get("depth", 100)),
                    )
                case "domain_intersection":
                    result = await client.intersection(
                        domains=list(payload["domains"]),
                        location=str(payload.get("location", "United States")),
                        language=str(payload.get("language", "en")),
                    )
                case "keywords_for_site":
                    result = await client.keywords_for_site(
                        target=str(payload["target"]),
                        location=str(payload.get("location", "United States")),
                        language=str(payload.get("language", "en")),
                    )
                case "paa":
                    result = await client.paa(
                        keyword=str(payload["keyword"]),
                        location=str(payload.get("location", "United States")),
                        language=str(payload.get("language", "en")),
                    )
                case _:
                    raise ValidationError(f"unsupported DataForSEO operation {request.operation!r}")
        return _result("dataforseo", request.operation, result.data, result.cost_usd)

    def _wrapper_operation(self, operation: str) -> str:
        return {
            "keyword.research": "keyword_volume",
            "serp.analyze": "serp",
        }.get(operation, operation)


class AhrefsActionConnector:
    """Decision-free adapter for Ahrefs SEO actions."""

    key = "ahrefs"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        payload = request.input_json
        issues: list[ActionValidationIssue] = []
        match request.operation:
            case "competitor.keywords" | "keywords_for_site":
                _required_str(payload, "target", issues)
                _optional_str(payload, "country", issues)
                _optional_str(payload, "date", issues)
                _int_range(payload, "limit", issues, minimum=1, maximum=1000)
            case "backlink.research" | "top_backlinks":
                _required_str(payload, "target", issues)
                _optional_str(payload, "mode", issues)
                _int_range(payload, "limit", issues, minimum=1, maximum=1000)
            case _:
                issues.extend(_unknown_operation(request))
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        payload = request.input_json
        async with httpx.AsyncClient(timeout=60.0) as http:
            client = AhrefsIntegration(
                payload=_credential_payload(request),
                project_id=request.project_id,
                http=http,
            )
            match request.operation:
                case "competitor.keywords" | "keywords_for_site":
                    result = await client.keywords_for_site(
                        target=str(payload["target"]),
                        country=str(payload.get("country", "us")),
                        limit=int(payload.get("limit", 100)),
                        date_=payload.get("date"),
                    )
                case "backlink.research" | "top_backlinks":
                    result = await client.top_backlinks(
                        target=str(payload["target"]),
                        mode=str(payload.get("mode", "domain")),
                        limit=int(payload.get("limit", 100)),
                    )
                case _:
                    raise ValidationError(f"unsupported Ahrefs operation {request.operation!r}")
        return _result("ahrefs", request.operation, result.data, result.cost_usd)


__all__ = [
    "AhrefsActionConnector",
    "DataForSeoActionConnector",
    "FirecrawlActionConnector",
    "JinaActionConnector",
    "RedditActionConnector",
]
