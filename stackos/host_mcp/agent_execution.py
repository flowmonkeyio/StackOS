"""Shared native process and structured-result boundary; adapters own CLI syntax."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from stackos.host_mcp.discovery import subprocess_env_for_cli
from stackos.host_mcp.result import AgentRunResult, validate_agent_result


def json_object(text: str) -> dict[str, Any]:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def structured_prompt(prompt: str, schema: dict[str, Any]) -> str:
    return (
        prompt
        + "\nReturn only a JSON object matching this schema: "
        + json.dumps(schema)
        + "\nIf native permissions deny required work, report that failure in the result and stop."
    )


def execute_json_agent(
    command: list[str],
    project_dir: Path,
    prompt: str,
    schema: dict[str, Any],
    host: str,
    decode: Callable[[str], AgentRunResult],
) -> AgentRunResult:
    try:
        process = subprocess.run(
            command,
            input=prompt,
            cwd=project_dir,
            env=subprocess_env_for_cli(command[0]),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return AgentRunResult(None, error=f"Could not launch {host}: {exc}")
    try:
        native = decode(process.stdout)
    except (ValueError, KeyError, TypeError) as exc:
        detail = process.stderr.strip() or process.stdout.strip() or str(exc)
        return AgentRunResult(process.returncode, error=f"Invalid {host} result: {detail}")
    result = AgentRunResult(
        process.returncode,
        thread_id=native.thread_id,
        result=native.result,
        error=native.error
        or (
            process.stderr.strip() or process.stdout.strip() or f"{host} exited unsuccessfully."
            if process.returncode
            else None
        ),
    )
    return validate_agent_result(result, schema, host)
