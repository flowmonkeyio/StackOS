"""Shared bounded native transport and quota meter normalization."""

from __future__ import annotations

import json
import math
import os
import selectors
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import TypeGuard

from .agent_execution import ManagedProcessControl
from .discovery import subprocess_env_for_cli

_OutputHandler = Callable[[bytes], tuple[bytes, bool]]


def run_capture(
    binary: str,
    args: Sequence[str],
    *,
    stdin: str | None = None,
    timeout: float = 12.0,
    max_output_bytes: int = 1_048_576,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Bound both output streams and retire only our process group; never log output."""
    return _capture(
        binary, args, (stdin or "").encode(), timeout, max_output_bytes, env=env, cwd=cwd
    )


def _capture(
    binary: str,
    args: Sequence[str],
    stdin: bytes,
    timeout: float,
    max_output_bytes: int,
    on_output: _OutputHandler | None = None,
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str] | None:
    # The existing host process owner confirms group retirement only on POSIX.
    if os.name != "posix" or timeout <= 0 or max_output_bytes <= 0:
        return None
    control = ManagedProcessControl()
    process = None
    output = {"stdout": bytearray(), "stderr": bytearray()}
    outgoing = bytearray(stdin)
    complete = False
    retired = False
    deadline = monotonic() + timeout
    with tempfile.TemporaryDirectory(prefix="stackos-subscription-") as capture_dir:
        try:
            process = control.spawn(
                [binary, *args],
                cwd=cwd or capture_dir,
                env=subprocess_env_for_cli(binary) if env is None else env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            assert process.stdin is not None
            assert process.stdout is not None and process.stderr is not None
            os.set_blocking(process.stdin.fileno(), False)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                if outgoing:
                    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                elif on_output is None:
                    process.stdin.close()
                while selector.get_map():
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        return None
                    for key, _ in selector.select(remaining):
                        if key.data == "stdin":
                            count = os.write(key.fd, outgoing[:16_384])
                            del outgoing[:count]
                            if not outgoing:
                                selector.unregister(process.stdin)
                                if on_output is None:
                                    process.stdin.close()
                            continue
                        chunk = os.read(key.fd, 16_384)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output[key.data].extend(chunk)
                        if sum(map(len, output.values())) > max_output_bytes:
                            return None
                        if key.data == "stdout" and on_output is not None:
                            reply, complete = on_output(chunk)
                            if reply:
                                if not outgoing:
                                    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                                outgoing.extend(reply)
                            if complete:
                                break
                    if complete:
                        break
            if on_output is not None and not complete:
                return None
            if not complete:
                process.wait(timeout=max(0, deadline - monotonic()))
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
            return None
        finally:
            if process is not None:
                retired = control.stop(timeout=0.2) == "stopped"
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
    if process is None or not retired:
        return None
    return subprocess.CompletedProcess(
        process.args,
        process.returncode,
        bytes(output["stdout"]).decode("utf-8", errors="replace"),
        bytes(output["stderr"]).decode("utf-8", errors="replace"),
    )


def read_rpc(
    binary: str,
    args: Sequence[str],
    calls: list[tuple[str, dict | None]],
    *,
    env: Mapping[str, str] | None = None,
) -> list[dict]:
    """Sequence native reads after initialize, ignoring notifications and private fields."""
    responses: list[dict] = []
    pending = bytearray()

    def request(index: int) -> bytes:
        method, params = calls[index]
        message = {"jsonrpc": "2.0", "id": index + 1, "method": method}
        if params is not None:
            message["params"] = params
        return (json.dumps(message) + "\n").encode()

    def receive(chunk: bytes) -> tuple[bytes, bool]:
        pending.extend(chunk)
        reply = b""
        while b"\n" in pending:
            line, _, rest = pending.partition(b"\n")
            pending[:] = rest
            try:
                message = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if not isinstance(message, dict) or type(message.get("id")) is not int:
                continue
            if message["id"] != len(responses) + 1 or "method" in message:
                continue
            responses.append(message)
            if len(responses) == len(calls):
                return reply, True
            if len(responses) == 1:
                if "result" not in message:
                    return b"", True
                if args[0] == "app-server":
                    reply += b'{"jsonrpc":"2.0","method":"initialized"}\n'
            reply += request(len(responses))
        return reply, False

    result = _capture(binary, args, request(0), 15.0, 1_048_576, receive, env=env)
    return responses if result is not None and len(responses) == len(calls) else []


def rpc_result(responses: list[dict], index: int) -> dict:
    result = responses[index].get("result") if len(responses) > index else None
    return result if isinstance(result, dict) else {}


def _percent(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 100
        and math.isfinite(value)
    )


def quota_meter(scope: str, used: object, reset: object) -> dict | None:
    if not _percent(used):
        return None
    resets_at = None
    if reset is not None:
        try:
            if isinstance(reset, (int, float)) and not isinstance(reset, bool):
                when = datetime.fromtimestamp(reset, UTC)
            elif isinstance(reset, str):
                when = datetime.fromisoformat(reset)
                if when.tzinfo is None:
                    return None
            else:
                return None
            if when <= datetime.now(UTC):
                return None
            resets_at = when.astimezone(UTC).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    return {"scope": scope, "remaining_percent": 100 - used, "resets_at": resets_at}
