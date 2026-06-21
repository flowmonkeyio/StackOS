"""Daemon REST adapter helpers for CLI operation aliases."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from stackos.config import Settings, get_settings


def _read_daemon_token(settings: Settings) -> str:
    try:
        return settings.token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        typer.echo(
            f"auth token missing at {settings.token_path}; run `stackos init`.",
            err=True,
        )
        raise typer.Exit(code=7) from None


def _api_request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> Any:
    """Call the local daemon REST API and return parsed JSON."""
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    settings = settings or get_settings()
    token = _read_daemon_token(settings)
    url = f"http://{settings.host}:{settings.port}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-StackOS-Client-Surface": "cli",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        typer.echo(_format_daemon_api_error(exc.code, payload), err=True)
        raise typer.Exit(code=1) from None
    except (OSError, TimeoutError, URLError) as exc:
        typer.echo(
            f"daemon API request failed: {exc}; is StackOS running on "
            f"{settings.host}:{settings.port}?",
            err=True,
        )
        raise typer.Exit(code=1) from None
    if not raw:
        return None
    return json.loads(raw)


def _format_daemon_api_error(status_code: int, payload: Any) -> str:
    if isinstance(payload, dict):
        body = {
            key: payload[key]
            for key in ("detail", "code", "retryable", "data", "retry_after", "hint")
            if key in payload and payload[key] is not None
        }
        if body:
            return f"daemon API error {status_code}: {json.dumps(body, sort_keys=True)}"
    return f"daemon API error {status_code}: {payload}"


def _load_operation_arguments(input_path: str | None) -> dict[str, Any]:
    if input_path is None:
        return {}
    raw = sys.stdin.read() if input_path == "-" else Path(input_path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        typer.echo(f"invalid JSON input: {exc.msg}", err=True)
        raise typer.Exit(code=2) from None
    if not isinstance(payload, dict):
        typer.echo("operation input must be a JSON object", err=True)
        raise typer.Exit(code=2)
    if set(payload) == {"arguments"} and isinstance(payload["arguments"], dict):
        return dict(payload["arguments"])
    return dict(payload)


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _echo_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


def _merge_common_arguments(
    arguments: dict[str, Any],
    *,
    project_id: int | None = None,
    run_token: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    merged = {key: value for key, value in arguments.items() if value is not None}
    if project_id is not None:
        merged["project_id"] = project_id
    if run_token is not None:
        merged["run_token"] = run_token
    if idempotency_key is not None:
        merged["idempotency_key"] = idempotency_key
    return merged


__all__ = [
    "_api_request",
    "_echo_json",
    "_format_daemon_api_error",
    "_load_operation_arguments",
    "_merge_common_arguments",
    "_read_daemon_token",
    "_split_csv",
]
