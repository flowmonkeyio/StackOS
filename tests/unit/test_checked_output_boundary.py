"""Synthetic canary tests for the opt-in final-output adapters."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

import stackos.artifacts.checked_output as checked_output
from stackos.artifacts.checked_output import (
    CheckedConsole,
    CheckedStream,
    OutputPolicy,
    OutputRejected,
    atomic_write,
    private_policy_pipe,
    receive_private_policy,
)
from stackos.cli import api_client
from stackos.cli.engineering_delivery import module as delivery

CANARY = "test-canary-7f2a"


def test_candidate_module_provenance_is_explicit() -> None:
    candidate = Path(__file__).resolve().parents[2]
    assert Path(checked_output.__file__).resolve().is_relative_to(candidate)
    assert Path(api_client.__file__).resolve().is_relative_to(candidate)
    assert Path(delivery.__file__).resolve().is_relative_to(candidate)


@pytest.mark.parametrize(
    "rendered",
    [
        CANARY,
        r"test-canary-\x37f2a",
        r"test-canary-\u0037f2a",
        r"test-canary-\u{37}f2a",
        r"test-canary-\067f2a",
    ],
)
def test_decoded_and_mixed_escape_canaries_are_rejected(rendered: str) -> None:
    policy = OutputPolicy((CANARY,))
    with pytest.raises(OutputRejected):
        policy.text(rendered)


def test_escaped_backslash_is_interpreted_once() -> None:
    policy = OutputPolicy(("A",))
    policy.text(r"\\u0041")
    with pytest.raises(OutputRejected):
        policy.text(r"\u0041")


@pytest.mark.parametrize(
    ("protected", "rendered"),
    [
        ("q", r"\q"),  # JavaScript identity escape
        ("ab", "a\\\nb"),  # line continuation
        ("😀", r"\uD83D\uDE00"),  # UTF-16 surrogate pair
    ],
)
def test_identity_escape_continuation_and_surrogate_decoding(
    protected: str, rendered: str
) -> None:
    with pytest.raises(OutputRejected):
        OutputPolicy((protected,)).text(rendered)


def test_malformed_escape_and_explicit_text_limit_hold() -> None:
    policy = OutputPolicy((CANARY,))
    with pytest.raises(OutputRejected):
        policy.text("bad escape \\u12")
    with pytest.raises(OutputRejected):
        policy.text("six bytes", limit=5)


@pytest.mark.parametrize(
    "record",
    [
        {"event": {"payload": json.dumps({"value": CANARY})}},
        {"argv": ["tool", "--flag=test-canary-7f2a"]},
    ],
)
def test_structured_decoded_arguments_and_nested_json_are_rejected(record) -> None:
    policy = OutputPolicy((CANARY,))
    with pytest.raises(OutputRejected):
        policy.require(record)
    if "event" in record:
        with pytest.raises(OutputRejected):
            policy.decode_json(record["event"]["payload"])


def test_encoded_json_field_framing_is_checked_before_sink_write() -> None:
    # This canary is genuinely adjacent in encoded JSON, across the field separator.
    policy = OutputPolicy(('", "right": "',))
    record = {"left": "safe", "right": "value"}
    policy.require(record)  # decoded fields individually do not contain the framing
    sink = io.BytesIO()
    with pytest.raises(OutputRejected):
        CheckedStream(policy).write(sink, policy.encoded_json(record))
    assert sink.getvalue() == b""


def test_final_prefix_suffix_and_cumulative_stream_are_admitted_before_write() -> None:
    policy = OutputPolicy((CANARY,))
    stream = CheckedStream(policy)
    sink = io.BytesIO()
    first = "prefix test-canary-"
    stream.write(sink, policy.encoded_text(first))
    before = sink.getvalue()
    with pytest.raises(OutputRejected):
        stream.write(sink, policy.encoded_text("7f2a", suffix="\n"))
    assert sink.getvalue() == before
    with pytest.raises(OutputRejected):
        policy.encoded_text("7f2a", prefix="event=test-canary-", suffix="\n")


class ShortWriter:
    def __init__(self):
        self.data = bytearray()
        self.calls = 0

    def write(self, data: bytes) -> int:
        self.calls += 1
        size = min(3, len(data))
        self.data.extend(data[:size])
        return size

    def flush(self):
        pass


def test_partial_writes_preserve_admitted_bytes_and_failure_poisons_stream() -> None:
    policy = OutputPolicy((CANARY,))
    stream = CheckedStream(policy)
    sink = ShortWriter()
    payload = policy.encoded_text("ordinary output\n")
    stream.write(sink, payload)
    assert bytes(sink.data) == payload
    assert sink.calls > 1

    class Broken(ShortWriter):
        def write(self, data):
            self.calls += 1
            return 0

    failed = CheckedStream(policy)
    broken = Broken()
    with pytest.raises(OutputRejected):
        failed.write(broken, b"benign")
    assert broken.calls == 1
    with pytest.raises(OutputRejected):
        failed.write(broken, b"again")
    assert broken.calls == 1


def test_real_pipe_successive_records_rejects_before_canary_write() -> None:
    policy = OutputPolicy((CANARY,))
    read_fd, write_fd = os.pipe()
    owner = CheckedStream(policy)
    try:
        with os.fdopen(write_fd, "wb", buffering=0) as writer:
            owner.write(writer, policy.encoded_text("prefix test-canary-"))
            with pytest.raises(OutputRejected):
                owner.write(writer, policy.encoded_text("7f2a", suffix="\n"))
        assert os.read(read_fd, 128) == b"prefix test-canary-"
    finally:
        os.close(read_fd)


def test_console_coalesces_two_handles_to_same_real_file(tmp_path: Path) -> None:
    path = tmp_path / "console.bin"
    first = path.open("wb", buffering=0)
    second = path.open("ab", buffering=0)
    policy = OutputPolicy((CANARY,))
    owner = CheckedConsole(policy, first, second)
    try:
        owner.write(first, b"prefix test-canary-")
        with pytest.raises(OutputRejected):
            owner.write(second, b"7f2a")
    finally:
        owner.close()
        first.close()
        second.close()
    assert path.read_bytes() == b"prefix test-canary-"


def test_stdout_stderr_dup_pipe_shares_framing_history_and_poison() -> None:
    policy = OutputPolicy((CANARY,))
    read_fd, write_fd = os.pipe()
    duplicate_fd = os.dup(write_fd)
    stdout = os.fdopen(write_fd, "wb", buffering=0)
    stderr = os.fdopen(duplicate_fd, "wb", buffering=0)
    owner = CheckedConsole(policy, stdout, stderr)
    try:
        owner.write(stdout, b"test-canary-")
        with pytest.raises(OutputRejected):
            owner.write(stderr, b"7f2a")
        with pytest.raises(OutputRejected):
            owner.write(stdout, b"later")
    finally:
        owner.close()
        stdout.close()
        stderr.close()
    assert os.read(read_fd, 128) == b"test-canary-"
    os.close(read_fd)


def test_atomic_manifest_bytes_and_digest_match_exact_replacement(tmp_path: Path) -> None:
    policy = OutputPolicy((CANARY,))
    path = tmp_path / "manifest.json"
    encoded = policy.encoded_json({"files": ["one.py"], "revision": "abc"}, sort_keys=True)
    atomic_write(path, encoded, policy=policy)
    assert path.read_bytes() == encoded
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(encoded).digest()

    rejected = tmp_path / "rejected.json"
    with pytest.raises(OutputRejected):
        atomic_write(rejected, json.dumps({"value": CANARY}).encode(), policy=policy)
    assert not rejected.exists()


def test_private_policy_pipe_closes_after_receive_and_keeps_value_out_of_repr() -> None:
    policy = OutputPolicy((CANARY,))
    fd = private_policy_pipe(policy)
    received = receive_private_policy(fd)
    with pytest.raises(OSError):
        os.fstat(fd)
    assert received.values == (CANARY,)
    assert CANARY not in repr(received)


@pytest.mark.parametrize("mode", ["worker", "reviewer", "observe"])
def test_helper_modes_close_real_policy_fd_before_local_admission_stops(
    mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = Path(__file__).resolve().parents[2]
    scripts = candidate / "plugins/engineering/scripts"
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    host = tmp_path / "host"
    host.mkdir()
    parent_home = tmp_path / "synthetic-parent-home"
    parent_home.mkdir()
    auth_home = parent_home / ".codex"
    auth_home.mkdir()
    (auth_home / "auth.json").write_text("{}", encoding="utf-8")
    manifest = {
        "version": 1, "selection_contract": "controller-source-outcome-v1",
        "files": [], "deleted": [], "changed_paths": [], "excluded_paths": [],
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    (tmp_path / "outcome-manifest.json").write_bytes(manifest_bytes)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    helper_args = {
        "worker": ["--worker", "executor", "/synthetic/bin", str(repo), str(schema),
                   "model", "5", str(host), "", str(snapshot), manifest_digest],
        "reviewer": ["/synthetic/bin", str(snapshot), str(schema), "model", "5",
                     str(host), "", manifest_digest],
        "observe": ["--observe", "/synthetic/bin", str(snapshot), "5", str(host)],
    }[mode]
    transport_value = "synthetic-private-policy"
    monkeypatch.setattr(delivery, "_OUTPUT_POLICY", None)
    monkeypatch.setattr(delivery, "_ENVIRONMENT", None)
    monkeypatch.setattr(delivery, "_CONSOLE", None)
    monkeypatch.setenv("FAKE_API_TOKEN", transport_value)
    monkeypatch.setenv("PYTHONPATH", "/must/be-scrubbed")
    monkeypatch.setenv("HOME", str(parent_home))
    code = "\n".join(
        [
            "import importlib.util, pathlib, sys, os",
            f"candidate=pathlib.Path({str(candidate)!r})",
            f"sys.path.insert(0, {str(scripts)!r})",
            "sys.path.insert(0, str(candidate))",
            "import stackos.artifacts.checked_output as core",
            "assert pathlib.Path(core.__file__).resolve().is_relative_to(candidate)",
            "spec=importlib.util.spec_from_file_location('isolated_review_helper', "
            "candidate/'plugins/engineering/scripts/reviewer_isolation.py')",
            "mod=importlib.util.module_from_spec(spec)",
            "sys.modules[spec.name]=mod",
            "spec.loader.exec_module(mod)",
            "assert pathlib.Path(mod.__file__).resolve().is_relative_to(candidate)",
            "class SafeStop(Exception): pass",
            "mod.command_runtime=lambda: ({}, '/synthetic/path')",
            "def safe_stop(binary, *, output_policy=None):",
            "    import json, os",
            "    closed=True",
            "    try: os.fstat(policy_fd)",
            "    except OSError: pass",
            "    else: closed=False",
            "    proof={'closed':closed,'helper':str(pathlib.Path(mod.__file__).resolve()),",
            "           'core':str(pathlib.Path(core.__file__).resolve()),",
            "           'pythonpath_absent':'PYTHONPATH' not in os.environ,",
            "           'policy_env_absent':'FAKE_API_TOKEN' not in os.environ,",
            "           'policy_present':bool(mod.POLICY.values)}",
            "    print(json.dumps(proof, sort_keys=True))",
            "    raise SafeStop()",
            "mod.native_binary=safe_stop",
            "policy_fd=int(sys.argv[-1])",
            "fd=policy_fd",
            "args=sys.argv[1:-2]",
            "sys.argv=['helper', *args, '--policy-fd', str(fd)]",
            "try:",
            "    mod.main()",
            "except Exception as exc:",
            "    print('stopped-before-host')",
            "    try:",
            "        os.fstat(fd)",
            "    except OSError:",
            "        print('closed:'+type(exc).__name__)",
            "    else:",
            "        raise AssertionError('private policy fd remained open')",
            "else:",
            "    raise AssertionError('invalid local fixture unexpectedly passed')",
        ]
    )
    stdout, stderr = io.BytesIO(), io.BytesIO()
    result = delivery._execute(
        [sys.executable, "-c", code, *helper_args], tmp_path, 10, stdout, stderr,
        prompt="{}",
    )
    assert result["exit_code"] == 0, stderr.getvalue().decode(errors="replace")
    lines = stdout.getvalue().decode().splitlines()
    proof = next(json.loads(line) for line in lines if line.startswith("{"))
    assert proof["closed"] and proof["policy_present"]
    assert proof["pythonpath_absent"] and proof["policy_env_absent"]
    assert Path(proof["helper"]).is_relative_to(candidate)
    assert Path(proof["core"]).is_relative_to(candidate)
    assert transport_value.encode() not in stdout.getvalue() + stderr.getvalue()


def test_api_adapter_keeps_default_request_behavior_and_checks_opted_in_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            return b'{"ok":true}'

    class Request:
        def __init__(self, url, data=None, method=None, headers=None):
            captured.append((url, data, method, headers))

    monkeypatch.setattr(
        api_client,
        "_read_daemon_token",
        lambda _settings, **_kwargs: "synthetic-token",
    )
    monkeypatch.setattr("urllib.request.Request", Request)
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Response())
    settings = type("SettingsStub", (), {"host": "127.0.0.1", "port": 5180})()

    assert api_client._api_request(
        "POST", "/ops", body={"ok": True}, settings=settings
    ) == {"ok": True}
    assert captured[-1][1] == b'{"ok": true}'  # established default API encoding

    policy = OutputPolicy((CANARY,))
    assert api_client._api_request(
        "POST", "/ops", body={"safe": "value"}, settings=settings, output_policy=policy
    ) == {"ok": True}
    assert captured[-1][1] == policy.encoded_json({"safe": "value"})
    before = len(captured)
    with pytest.raises(OutputRejected):
        api_client._api_request(
            "POST", "/ops", body={"value": CANARY}, settings=settings, output_policy=policy
        )
    assert len(captured) == before  # no Request/send on decoded-body rejection


def _reviewer_module():
    candidate = Path(__file__).resolve().parents[2]
    scripts = candidate / "plugins/engineering/scripts"
    path = scripts / "reviewer_isolation.py"
    spec = importlib.util.spec_from_file_location("reviewer_isolation_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(scripts))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(scripts))
    return module


def test_reviewer_send_checks_json_spanning_canary_before_stdin_write(monkeypatch):
    reviewer = _reviewer_module()
    policy = OutputPolicy(('", "right": "',))
    monkeypatch.setattr(reviewer, "POLICY", policy)
    server = reviewer.Server.__new__(reviewer.Server)
    server.process = SimpleNamespace(stdin=io.BytesIO())
    server.input_stream = CheckedStream(policy)

    reviewer.Server.send(server, {"left": "benign"})
    benign = server.process.stdin.getvalue()
    assert benign.endswith(b"\n")
    assert json.loads(benign) == {"left": "benign"}
    with pytest.raises(OutputRejected):
        reviewer.Server.send(server, {"left": "x", "right": "y"})
    assert server.process.stdin.getvalue() == benign


def test_outcome_command_admits_decoded_argv_before_dispatch_and_result_before_disclosure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reviewer = _reviewer_module()
    policy = OutputPolicy((CANARY,))
    monkeypatch.setattr(reviewer, "POLICY", policy)
    monkeypatch.setattr(reviewer, "command_runtime", lambda: ("/synthetic/python", "/safe/path"))

    class ServerStub:
        def __init__(self, result):
            self.result = result
            self.calls = 0

        def call(self, *_args, **_kwargs):
            self.calls += 1
            return self.result

    unsafe = ServerStub({"exitCode": 0, "stdout": "safe", "stderr": ""})
    with pytest.raises(OutputRejected):
        reviewer.outcome_command(
            unsafe, ["tool", f"--value={CANARY}"], tmp_path, 9e99
        )
    assert unsafe.calls == 0

    response = {"exitCode": 0, "stdout": CANARY, "stderr": ""}
    benign_argv = ServerStub(response)
    result = reviewer.outcome_command(benign_argv, ["tool", "safe"], tmp_path, 9e99)
    assert benign_argv.calls == 1
    with pytest.raises(OutputRejected):
        policy.encoded_json(result)  # exact consumer handoff is withheld


@pytest.mark.parametrize("failure_kind", ["http", "connection", "oversize"])
def test_api_failure_and_capture_boundaries_do_not_print_canaries(
    monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    canary = "api-synthetic-canary"
    policy = OutputPolicy((canary,))
    diagnostic = io.BytesIO()
    monkeypatch.setattr(sys, "stderr", SimpleNamespace(buffer=diagnostic))
    monkeypatch.setattr(api_client, "_read_daemon_token", lambda *_a, **_k: "synthetic-token")
    monkeypatch.setattr("urllib.request.Request", lambda *a, **k: SimpleNamespace())
    if failure_kind == "http":
        body = json.dumps({"detail": canary}).encode()
        error = HTTPError("http://127.0.0.1", 400, "bad", {}, io.BytesIO(body))

        def raise_http(*_a, **_k):
            raise error

        monkeypatch.setattr("urllib.request.urlopen", raise_http)
    elif failure_kind == "connection":
        def raise_connection(*_a, **_k):
            raise URLError(canary)

        monkeypatch.setattr("urllib.request.urlopen", raise_connection)
    else:
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_a): return False
            def read(self, size): return b"x" * size

        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: Response())
    settings = SimpleNamespace(host="127.0.0.1", port=5180)
    checked = CheckedStream(policy)
    with pytest.raises((OutputRejected, api_client.typer.Exit)):
        api_client._api_request(
            "GET", "/health", settings=settings, output_policy=policy,
            diagnostic_stream=checked,
        )
    assert canary.encode() not in diagnostic.getvalue()
