"""Private supplied-key receipt contract; no live host or native observations."""

from __future__ import annotations

import builtins
import ctypes
import hashlib
import hmac
import importlib
import io
import json
import os
import socket
import sqlite3
import subprocess
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from tempfile import gettempdir
from types import SimpleNamespace

import pytest

BOOT = "00000000-0000-0000-0000-000000000001"
KEY = b"K" * 32


def golden_payload():
    """An independent literal wire fixture, including every required field."""
    binding = {
        "project_id": 1,
        "canonical_item_id": "task:1",
        "owner_ref": "fixture:lead",
        "epoch": 1,
        "reservation_revision": 2,
        "scope_digest": "a" * 64,
        "controller_run_id": 1,
        "run_plan_id": 1,
        "run_plan_step_id": 1,
        "attempt_run_id": 2,
        "intent_ref": "fixture:one",
        "manifest_digest": "b" * 64,
        "host_ref": "fixture:host",
        "boot_uuid": BOOT,
    }

    def process(pid, parent, micros):
        return {
            "status": "known",
            "identity": {
                "boot_uuid": BOOT,
                "pid": pid,
                "parent_pid": parent,
                "pgid": pid,
                "sid": pid,
                "start_seconds": 1,
                "start_microseconds": micros,
                "api_version": "darwin-proc-bsdinfo-v1",
                "runtime_abi": "Darwin/arm64/64",
            },
            "issues": [],
        }

    fact = {
        "kind": "armed",
        "origin": "host-shim",
        "sequence": 0,
        "clock": {
            "status": "known",
            "boot_uuid": BOOT,
            "continuous_ns": 1000,
            "awake_ns": 900,
            "api_version": "darwin-mach-clock-v1",
            "runtime_abi": "Darwin/arm64/64",
            "issues": [],
        },
        "shim": process(111, 100, 1),
        "leader": process(112, 111, 2),
        "deadline_continuous_ns": 2000,
        "cancel_intent_digest": "c" * 64,
        "barrier_nonce": "d" * 64,
    }
    return {"binding": binding, "fact": fact}


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def golden_values(receipt, payload):
    clock = importlib.import_module("stackos.controlled_process_clock")
    identity = importlib.import_module("stackos.controlled_process_identity")
    fact_fields = dict(payload["fact"])
    clock_fields = dict(fact_fields["clock"])
    clock_fields["issues"] = ()
    fact_fields["clock"] = clock._ClockObservation(**clock_fields)
    for name in ("shim", "leader"):
        fields = fact_fields[name]
        fact_fields[name] = identity._ProcessObservation(
            status=fields["status"],
            identity=identity._ProcessIdentity(**fields["identity"]),
            issues=(),
        )
    return receipt._Binding(**payload["binding"]), receipt._ArmFact(**fact_fields)


def test_canonical_receipt_round_trip():
    # Import inside the test: the red result must identify the absent producer.
    receipt = importlib.import_module("stackos.controlled_process_receipt")
    payload = golden_payload()
    binding, fact = golden_values(receipt, payload)
    payload_bytes = canonical(payload)
    message = (
        b"StackOS\x00controlled-arm-receipt\x00v1\x00"
        + len(payload_bytes).to_bytes(4, "big", signed=False)
        + payload_bytes
    )
    expected_mac = hmac.digest(KEY, message, "sha256").hex()
    expected_wire = canonical(
        {
            "schema": "stackos.controlled-arm-receipt.v1",
            "payload": payload,
            "mac": expected_mac,
        }
    )

    raw = receipt._seal_receipt(binding, fact, KEY)
    assert type(raw) is bytes
    assert raw == expected_wire
    assert json.loads(raw)["payload"] == payload
    assert json.loads(raw)["mac"] == expected_mac
    assert receipt._seal_receipt(binding, fact, KEY) == raw

    authenticated = receipt._authenticate_receipt(raw, binding, KEY)
    assert type(authenticated) is receipt._KeyAuthenticatedReceipt
    assert authenticated.binding == binding
    assert authenticated.fact == fact
    assert authenticated.raw == expected_wire
    assert authenticated.digest == hashlib.sha256(expected_wire).hexdigest()


@pytest.fixture(autouse=True)
def forbid_effects(monkeypatch, tmp_path):
    """Allow only disposable receipt fixtures; never start a host/native consumer."""
    allowed_dirs = set()

    def forbidden(*args, **kwargs):
        pytest.fail("receipt attempted a forbidden effect")

    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in (
        "kill",
        "killpg",
        "fork",
        "posix_spawn",
        "posix_spawnp",
        "system",
        "popen",
        "getpgid",
        "getsid",
    ):
        monkeypatch.setattr(os, name, forbidden, raising=False)
    monkeypatch.setattr(ctypes, "CDLL", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    original_import = builtins.__import__

    def safe_import(name, *args, **kwargs):
        fromlist = kwargs.get("fromlist", args[2] if len(args) > 2 else ())
        names = [name, *(name + "." + item for item in (fromlist or ()))]
        if any(
            item.startswith(("stackos.config", "stackos.crypto", "stackos.db")) for item in names
        ):
            forbidden()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", safe_import)
    original_import_module = importlib.import_module

    def safe_import_module(name, *args, **kwargs):
        if name.startswith(("stackos.config", "stackos.crypto", "stackos.db")):
            forbidden()
        return original_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", safe_import_module)

    def protect_open(original):
        def safe(file, mode="r", *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                path = Path(os.fsdecode(file)).absolute()
                if path.name in {"seed.bin", "auth.token", ".env"}:
                    forbidden()
                if any(flag in mode for flag in "wax+") and not path.is_relative_to(tmp_path):
                    forbidden()
            return original(file, mode, *args, **kwargs)

        return safe

    monkeypatch.setattr(builtins, "open", protect_open(builtins.open))
    monkeypatch.setattr(io, "open", protect_open(io.open))
    original_open = os.open

    def safe_os_open(path, flags, *args, **kwargs):
        if isinstance(path, (str, bytes, os.PathLike)):
            value = Path(os.fsdecode(path))
            if value.name in {"seed.bin", "auth.token", ".env"}:
                forbidden()
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                if kwargs.get("dir_fd") is not None:
                    if kwargs["dir_fd"] not in allowed_dirs:
                        forbidden()
                elif not value.absolute().is_relative_to(tmp_path):
                    forbidden()
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", safe_os_open)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {safe_os_open})
    return allowed_dirs


@pytest.fixture
def receipt():
    return importlib.import_module("stackos.controlled_process_receipt")


@pytest.fixture
def sample(receipt):
    return golden_values(receipt, golden_payload())


@pytest.fixture
def directory(tmp_path, forbid_effects):
    target_device = os.stat(gettempdir()).st_dev
    assert tmp_path.stat().st_dev == target_device
    folder = tmp_path / "receipts"
    folder.mkdir(mode=0o700)
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    forbid_effects.add(fd)
    try:
        assert os.fstat(fd).st_dev == folder.stat().st_dev == target_device
        yield folder, fd
    finally:
        forbid_effects.discard(fd)
        os.close(fd)


def held(receipt, result, code):
    assert type(result) is receipt._ReceiptHold
    assert result.code == code
    return result


def filename(payload=None):
    return (
        hashlib.sha256(canonical((payload or golden_payload())["binding"])).hexdigest() + ".receipt"
    )


def signed(
    payload, *, domain=b"StackOS\x00controlled-arm-receipt\x00v1\x00", length_delta=0, key=KEY
):
    data = canonical(payload)
    tag = hmac.digest(
        key, domain + (len(data) + length_delta).to_bytes(4, "big") + data, "sha256"
    ).hex()
    return canonical(
        {"schema": "stackos.controlled-arm-receipt.v1", "payload": payload, "mac": tag}
    )


def existing_file(receipt, sample, directory, raw=None):
    folder, _fd = directory
    binding, fact = sample
    path = folder / filename()
    path.write_bytes(receipt._seal_receipt(binding, fact, KEY) if raw is None else raw)
    path.chmod(0o600)
    return path


def patch_open(monkeypatch, wrapper):
    monkeypatch.setattr(os, "open", wrapper)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {wrapper})


def test_create_read_and_identical_replay(receipt, sample, directory, monkeypatch):
    folder, fd = directory
    binding, fact = sample
    slot = receipt._allocate_receipt_slot(fd, binding)
    assert type(slot) is receipt._AllocatedSlot
    sync = os.fsync
    synced = []

    def observe_sync(owned):
        synced.append(owned)
        return sync(owned)

    monkeypatch.setattr(os, "fsync", observe_sync)
    try:
        first = receipt._fill_allocated_receipt_slot(
            fd, slot, binding, KEY, receipt._seal_receipt(binding, fact, KEY)
        )
    finally:
        assert slot._close_once() is None
    assert type(first) is receipt._WriteReceiptResult and first.state == "created"
    assert len(synced) == 2 and synced[1] == fd and synced[0] != fd
    path = folder / filename()
    before = path.stat()
    raw = path.read_bytes()
    read = receipt._read_receipt(fd, binding, KEY)
    assert read == first.receipt
    replay = receipt._write_receipt(fd, binding, fact, KEY)
    assert replay.state == "replayed" and replay.receipt == first.receipt
    after = path.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_ctime_ns, after.st_size) == (
        before.st_ino,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_size,
    )
    assert path.read_bytes() == raw and len(synced) == 2
    assert first.receipt.digest == hashlib.sha256(raw).hexdigest()
    assert os.fstat(fd).st_ino == folder.stat().st_ino
    with pytest.raises(FrozenInstanceError):
        first.state = "replayed"


@pytest.mark.parametrize("change", ["domain", "length", "key", "tag", "tamper"])
def test_authentication_rejects_wrong_message_or_key(receipt, sample, change):
    binding, _fact = sample
    payload = golden_payload()
    if change == "domain":
        raw = signed(payload, domain=b"other\x00")
    elif change == "length":
        raw = signed(payload, length_delta=1)
    elif change == "key":
        raw = signed(payload, key=b"W" * 32)
    else:
        wire = json.loads(signed(payload))
        if change == "tag":
            wire["mac"] = "0" * 64
        else:
            wire["payload"]["fact"]["deadline_continuous_ns"] += 1
        raw = canonical(wire)
    held(receipt, receipt._authenticate_receipt(raw, binding, KEY), "authentication_failed")


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", 2),
        ("canonical_item_id", "ticket:2"),
        ("owner_ref", "fixture:other"),
        ("epoch", 2),
        ("reservation_revision", 3),
        ("scope_digest", "e" * 64),
        ("controller_run_id", 2),
        ("run_plan_id", 2),
        ("run_plan_step_id", 2),
        ("attempt_run_id", 3),
        ("intent_ref", "fixture:two"),
        ("manifest_digest", "e" * 64),
        ("host_ref", "fixture:other"),
        ("boot_uuid", "00000000-0000-0000-0000-000000000002"),
    ],
)
def test_each_expected_binding_field_must_match(receipt, sample, field, value):
    binding, fact = sample
    raw = receipt._seal_receipt(binding, fact, KEY)
    held(
        receipt,
        receipt._authenticate_receipt(raw, replace(binding, **{field: value}), KEY),
        "binding_mismatch",
    )


@pytest.mark.parametrize("key", [None, "K" * 32, b"", b"K" * 31, b"K" * 33, bytearray(KEY)])
def test_invalid_keys_are_rejected_before_io(receipt, sample, directory, key):
    folder, fd = directory
    binding, fact = sample
    for result in (
        receipt._seal_receipt(binding, fact, key),
        receipt._authenticate_receipt(b"{}", binding, key),
        receipt._write_receipt(fd, binding, fact, key),
        receipt._read_receipt(fd, binding, key),
    ):
        held(receipt, result, "invalid_key")
    assert list(folder.iterdir()) == []


@pytest.mark.parametrize(
    "field",
    [
        "project_id",
        "epoch",
        "reservation_revision",
        "controller_run_id",
        "run_plan_id",
        "run_plan_step_id",
        "attempt_run_id",
    ],
)
@pytest.mark.parametrize("value", [None, False, 1.0, 0, -1, 2**63, "huge"])
def test_binding_integer_types_and_bounds(receipt, sample, directory, field, value):
    binding, fact = sample
    folder, fd = directory
    if value == "huge":
        value = 1 << 100_000
    held(
        receipt,
        receipt._write_receipt(fd, replace(binding, **{field: value}), fact, KEY),
        "invalid_schema",
    )
    assert list(folder.iterdir()) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("canonical_item_id", "task:0"),
        ("canonical_item_id", "task:01"),
        ("canonical_item_id", "task:9223372036854775808"),
        ("canonical_item_id", "task:1/../2"),
        ("owner_ref", " x"),
        ("owner_ref", "x" * 201),
        ("owner_ref", "é"),
        ("intent_ref", "x" * 129),
        ("host_ref", "x\n"),
        ("host_ref", "x" * 129),
        ("scope_digest", "A" * 64),
        ("manifest_digest", "b" * 63),
        ("boot_uuid", "00000000-0000-0000-0000-000000000000"),
        ("boot_uuid", None),
    ],
)
def test_binding_string_contract(receipt, sample, field, value):
    binding, fact = sample
    held(
        receipt,
        receipt._seal_receipt(replace(binding, **{field: value}), fact, KEY),
        "invalid_schema",
    )


@pytest.mark.parametrize("part", ["clock", "shim", "leader"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("boot_uuid", "00000000-0000-0000-0000-000000000002"),
        ("api_version", "other-api"),
        ("runtime_abi", "Darwin/x86_64/64"),
    ],
)
def test_each_observation_basis_must_match(receipt, sample, part, field, value):
    binding, fact = sample
    observation = getattr(fact, part)
    changed = (
        replace(observation, **{field: value})
        if part == "clock"
        else replace(observation, identity=replace(observation.identity, **{field: value}))
    )
    held(
        receipt,
        receipt._seal_receipt(binding, replace(fact, **{part: changed}), KEY),
        "invalid_schema",
    )


@pytest.mark.parametrize("part", ["clock", "shim", "leader"])
@pytest.mark.parametrize(
    "changes", [{"status": "unknown"}, {"issues": ("denied",)}, {"issues": []}]
)
def test_unknown_or_nonexact_observation_is_not_a_fact(receipt, sample, part, changes):
    binding, fact = sample
    changed = replace(getattr(fact, part), **changes)
    held(
        receipt,
        receipt._seal_receipt(binding, replace(fact, **{part: changed}), KEY),
        "invalid_schema",
    )


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("pid", [0, 2**31]),
        ("parent_pid", [-1, 2**31]),
        ("pgid", [0, 2**31]),
        ("sid", [0, 2**31]),
        ("start_seconds", [0, 2**63]),
        ("start_microseconds", [-1, 1_000_000]),
    ],
)
@pytest.mark.parametrize("part", ["shim", "leader"])
def test_process_integer_bounds(receipt, sample, field, invalid, part):
    binding, fact = sample
    for value in [*invalid, True, None, 1.0]:
        observation = getattr(fact, part)
        changed = replace(observation, identity=replace(observation.identity, **{field: value}))
        held(
            receipt,
            receipt._seal_receipt(binding, replace(fact, **{part: changed}), KEY),
            "invalid_schema",
        )


def test_fact_limits_and_exact_dataclass_types(receipt, sample):
    binding, fact = sample
    for fields in (
        {"kind": "claimed"},
        {"origin": "worker"},
        {"sequence": False},
        {"sequence": None},
        {"sequence": 0.0},
        {"sequence": 1},
        {"deadline_continuous_ns": None},
        {"deadline_continuous_ns": True},
        {"deadline_continuous_ns": 2000.0},
        {"deadline_continuous_ns": 1000},
        {"deadline_continuous_ns": 2**63},
        {"barrier_nonce": "0" * 64},
        {"cancel_intent_digest": "c" * 63},
    ):
        held(
            receipt, receipt._seal_receipt(binding, replace(fact, **fields), KEY), "invalid_schema"
        )
    for field in ("continuous_ns", "awake_ns"):
        for value in (None, False, 1.0, -1, 2**63):
            changed = replace(fact.clock, **{field: value})
            held(
                receipt,
                receipt._seal_receipt(binding, replace(fact, clock=changed), KEY),
                "invalid_schema",
            )
    for field in ("pid", "pgid"):
        leader = replace(
            fact.leader,
            identity=replace(fact.leader.identity, **{field: getattr(fact.shim.identity, field)}),
        )
        held(
            receipt,
            receipt._seal_receipt(binding, replace(fact, leader=leader), KEY),
            "invalid_schema",
        )

    class BindingSubclass(receipt._Binding):
        pass

    class FactSubclass(receipt._ArmFact):
        pass

    held(
        receipt,
        receipt._seal_receipt(BindingSubclass(**golden_payload()["binding"]), fact, KEY),
        "invalid_schema",
    )
    held(receipt, receipt._seal_receipt(binding, FactSubclass(**vars(fact)), KEY), "invalid_schema")


def test_valid_boundary_values_and_nonatomic_clocks(receipt, sample):
    binding, fact = sample
    large = replace(
        binding, project_id=2**63 - 1, owner_ref="x" * 200, intent_ref="x" * 128, host_ref="x" * 128
    )
    zero = replace(
        fact,
        clock=replace(fact.clock, continuous_ns=0, awake_ns=2**63 - 1),
        deadline_continuous_ns=1,
    )
    raw = receipt._seal_receipt(large, zero, KEY)
    assert type(receipt._authenticate_receipt(raw, large, KEY)) is receipt._KeyAuthenticatedReceipt


@pytest.mark.parametrize("part", ["clock", "shim", "leader", "shim-identity", "leader-identity"])
def test_s1_subclasses_are_not_accepted_as_exact_types(receipt, sample, part):
    binding, fact = sample
    name = part.split("-", 1)[0]
    observation = getattr(fact, name)
    value = observation.identity if part.endswith("-identity") else observation
    child = type("ReceiptFixtureSubclass", (type(value),), {})(**vars(value))
    changed = replace(observation, identity=child) if part.endswith("-identity") else child
    held(
        receipt,
        receipt._seal_receipt(binding, replace(fact, **{name: changed}), KEY),
        "invalid_schema",
    )


def test_bytes_subclass_is_not_a_key_and_mac_uses_constant_time_comparison(
    receipt, sample, monkeypatch
):
    class KeySubclass(bytes):
        pass

    held(receipt, receipt._seal_receipt(*sample, KeySubclass(KEY)), "invalid_key")
    original = hmac.compare_digest
    comparisons = []

    def compare(left, right):
        comparisons.append((left, right))
        return original(left, right)

    monkeypatch.setattr(hmac, "compare_digest", compare)
    raw = receipt._seal_receipt(*sample, KEY)
    assert (
        type(receipt._authenticate_receipt(raw, sample[0], KEY)) is receipt._KeyAuthenticatedReceipt
    )
    assert len(comparisons) == 1 and comparisons[0][0] == comparisons[0][1]


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b" " * 16385,
        b"\xef\xbb\xbf{}",
        "{}",
        bytearray(b"{}"),
        b"[" * 9 + b"0" + b"]" * 9,
        b"[" * 1000 + b"0" + b"]" * 1000,
        b'{"x":' + b"9" * 5000 + b"}",
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1.0}',
        b'{"x":1e0}',
        b'{"x":1,"x":2}',
        b'{"x":"unterminated}',
    ],
    ids=[
        "empty",
        "overflow",
        "bom",
        "str",
        "bytearray",
        "depth9",
        "deep",
        "long-int",
        "nan",
        "infinity",
        "float",
        "exponent",
        "duplicate",
        "unclosed",
    ],
)
def test_bounded_json_rejection(receipt, sample, raw):
    held(receipt, receipt._authenticate_receipt(raw, sample[0], KEY), "invalid_json")


@pytest.mark.parametrize(
    "part", ["envelope", "payload", "binding", "fact", "clock", "process", "identity"]
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_exact_nested_field_sets(receipt, sample, part, change):
    wire = json.loads(signed(golden_payload()))
    targets = {
        "envelope": wire,
        "payload": wire["payload"],
        "binding": wire["payload"]["binding"],
        "fact": wire["payload"]["fact"],
        "clock": wire["payload"]["fact"]["clock"],
        "process": wire["payload"]["fact"]["shim"],
        "identity": wire["payload"]["fact"]["shim"]["identity"],
    }
    target = targets[part]
    if change == "missing":
        target.pop(next(iter(target)))
    else:
        target["extra"] = 1
    held(receipt, receipt._authenticate_receipt(canonical(wire), sample[0], KEY), "invalid_schema")


@pytest.mark.parametrize("change", ["space", "order", "escape", "negative-zero"])
def test_canonical_bytes_are_required(receipt, sample, change):
    raw = signed(golden_payload())
    if change == "space":
        raw += b"\n"
    elif change == "order":
        wire = json.loads(raw)
        raw = json.dumps(
            {key: wire[key] for key in ("schema", "payload", "mac")}, separators=(",", ":")
        ).encode()
    elif change == "escape":
        raw = raw.replace(b"fixture:lead", b"\\u0066ixture:lead")
    else:
        raw = raw.replace(b'"sequence":0', b'"sequence":-0')
    held(receipt, receipt._authenticate_receipt(raw, sample[0], KEY), "noncanonical")


@pytest.mark.parametrize("value", [None, False, "é", "\ud800"])
def test_json_null_bool_unicode_and_surrogate_fields_reject(receipt, sample, value):
    payload = golden_payload()
    payload["binding"]["owner_ref"] = value
    held(receipt, receipt._authenticate_receipt(signed(payload), sample[0], KEY), "invalid_schema")


def test_nested_duplicate_and_nonascii_wire_reject(receipt, sample):
    raw = signed(golden_payload())
    duplicate = raw.replace(b'"project_id":1', b'"project_id":1,"project_id":1')
    held(receipt, receipt._authenticate_receipt(duplicate, sample[0], KEY), "invalid_json")
    held(
        receipt,
        receipt._authenticate_receipt(raw.replace(b"fixture:lead", "é".encode()), sample[0], KEY),
        "invalid_json",
    )


def test_existing_different_valid_fact_conflicts_without_repair(receipt, sample, directory):
    path = existing_file(receipt, sample, directory)
    before, raw = path.stat(), path.read_bytes()
    binding, fact = sample
    held(
        receipt,
        receipt._write_receipt(
            directory[1], binding, replace(fact, deadline_continuous_ns=2001), KEY
        ),
        "conflict",
    )
    assert path.read_bytes() == raw and path.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize(
    "raw,code", [(b"{", "invalid_json"), (b"", "unsafe_entry"), (b"x" * 16385, "unsafe_entry")]
)
def test_invalid_existing_artifact_is_retained(receipt, sample, directory, raw, code):
    path = existing_file(receipt, sample, directory, raw)
    held(receipt, receipt._write_receipt(directory[1], *sample, KEY), code)
    assert path.read_bytes() == raw


def test_filesystem_support_is_required(receipt, sample, directory, monkeypatch):
    monkeypatch.setattr(receipt, "fcntl", None)
    held(receipt, receipt._read_receipt(directory[1], sample[0], KEY), "unsupported_fs")
    held(receipt, receipt._write_receipt(directory[1], *sample, KEY), "unsupported_fs")


@pytest.mark.parametrize("bad", [True, -1, None, "0", 2**31 - 1, 1 << 1000])
def test_invalid_directory_descriptors_reject(receipt, sample, bad):
    held(receipt, receipt._read_receipt(bad, sample[0], KEY), "unsafe_directory")


@pytest.mark.parametrize("change", ["mode", "inheritable", "uid", "access", "not-directory"])
def test_directory_contract(receipt, sample, directory, monkeypatch, change):
    folder, fd = directory
    if change == "mode":
        folder.chmod(0o755)
    elif change == "inheritable":
        os.set_inheritable(fd, True)
    elif change == "uid":
        monkeypatch.setattr(os, "geteuid", lambda: folder.stat().st_uid + 1)
    elif change == "access":
        original = receipt.fcntl.fcntl
        monkeypatch.setattr(
            receipt.fcntl,
            "fcntl",
            lambda item, op: (
                os.O_WRONLY if item == fd and op == receipt.fcntl.F_GETFL else original(item, op)
            ),
        )
    else:
        path = existing_file(receipt, sample, directory)
        owned = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            held(receipt, receipt._read_receipt(owned, sample[0], KEY), "unsafe_directory")
        finally:
            os.close(owned)
        return
    held(receipt, receipt._read_receipt(fd, sample[0], KEY), "unsafe_directory")
    assert os.fstat(fd).st_ino == folder.stat().st_ino


@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink", "directory", "mode"])
def test_unsafe_entries_reject_before_open(receipt, sample, directory, monkeypatch, kind):
    folder, fd = directory
    path = folder / filename()
    if kind == "symlink":
        path.symlink_to(folder / "missing")
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "directory":
        path.mkdir(mode=0o700)
    else:
        existing_file(receipt, sample, directory)
        if kind == "hardlink":
            os.link(path, folder / "alias")
        else:
            path.chmod(0o644)
    original = os.open

    def never_open(name, *args, **kwargs):
        assert name != filename()
        return original(name, *args, **kwargs)

    patch_open(monkeypatch, never_open)
    held(receipt, receipt._read_receipt(fd, sample[0], KEY), "unsafe_entry")


@pytest.mark.parametrize("field,value", [("st_uid", -1), ("st_nlink", 2)])
def test_file_owner_and_link_metadata_reject(receipt, sample, directory, monkeypatch, field, value):
    existing_file(receipt, sample, directory)
    entry = receipt._entry

    def changed(*args):
        info = entry(*args)
        return SimpleNamespace(
            **{
                name: getattr(info, name)
                for name in (
                    "st_dev",
                    "st_ino",
                    "st_uid",
                    "st_gid",
                    "st_mode",
                    "st_nlink",
                    "st_size",
                    "st_mtime_ns",
                    "st_ctime_ns",
                )
            }
            | {field: value}
        )

    monkeypatch.setattr(receipt, "_entry", changed)
    held(receipt, receipt._read_receipt(directory[1], sample[0], KEY), "unsafe_entry")


@pytest.mark.parametrize(
    "change", ["replace-before-open", "replace-after-open", "same-size", "directory"]
)
def test_entry_and_directory_drift_reject(receipt, sample, directory, monkeypatch, change):
    path = existing_file(receipt, sample, directory)
    raw = path.read_bytes()
    if change == "replace-before-open":
        original_open = os.open

        def race_open(name, *args, **kwargs):
            path.rename(path.with_name("previous"))
            path.write_bytes(raw)
            path.chmod(0o600)
            return original_open(name, *args, **kwargs)

        patch_open(monkeypatch, race_open)
    else:
        original_read = os.read
        changed = False

        def race_read(fd, size):
            nonlocal changed
            data = original_read(fd, size)
            if not changed:
                changed = True
                if change == "replace-after-open":
                    path.rename(path.with_name("previous"))
                    path.write_bytes(raw)
                    path.chmod(0o600)
                elif change == "same-size":
                    stamp = path.stat()
                    path.write_bytes(raw[:-1] + b" ")
                    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
                else:
                    directory[0].chmod(0o755)
            return data

        monkeypatch.setattr(os, "read", race_read)
    held(receipt, receipt._read_receipt(directory[1], sample[0], KEY), "entry_changed")


@pytest.mark.parametrize("access", ["inheritable", "wrong-access"])
@pytest.mark.parametrize("operation", ["read", "write"])
def test_opened_file_flags_are_verified(receipt, sample, directory, monkeypatch, access, operation):
    if operation == "read":
        existing_file(receipt, sample, directory)
    original = os.open

    def unsafe_open(name, flags, *args, **kwargs):
        if access == "wrong-access":
            flags = (flags & ~os.O_ACCMODE) | os.O_RDWR
        fd = original(name, flags, *args, **kwargs)
        if access == "inheritable":
            os.set_inheritable(fd, True)
        return fd

    patch_open(monkeypatch, unsafe_open)
    result = (
        receipt._read_receipt(directory[1], sample[0], KEY)
        if operation == "read"
        else receipt._write_receipt(directory[1], *sample, KEY)
    )
    held(receipt, result, "unsafe_entry")


def test_short_io_and_eintr_make_bounded_progress(receipt, sample, directory, monkeypatch):
    original_read, original_write = os.read, os.write
    reads, writes = 0, 0

    def short_read(fd, size):
        nonlocal reads
        reads += 1
        if reads == 1:
            raise InterruptedError()
        return original_read(fd, min(size, 7))

    def short_write(fd, data):
        nonlocal writes
        writes += 1
        if writes == 1:
            raise InterruptedError()
        return original_write(fd, data[:11])

    monkeypatch.setattr(os, "read", short_read)
    monkeypatch.setattr(os, "write", short_write)
    slot = receipt._allocate_receipt_slot(directory[1], sample[0])
    assert type(slot) is receipt._AllocatedSlot
    try:
        result = receipt._fill_allocated_receipt_slot(
            directory[1], slot, sample[0], KEY, receipt._seal_receipt(*sample, KEY)
        )
    finally:
        assert slot._close_once() is None
    assert result.state == "created" and reads > 2 and writes > 2


@pytest.mark.parametrize("operation", ["read", "write"])
def test_eintr_call_budget_terminates(receipt, sample, directory, monkeypatch, operation):
    if operation == "read":
        existing_file(receipt, sample, directory)
    calls = 0

    def interrupt(*args):
        nonlocal calls
        calls += 1
        raise InterruptedError()

    monkeypatch.setattr(os, operation, interrupt)
    result = (
        receipt._read_receipt(directory[1], sample[0], KEY)
        if operation == "read"
        else receipt._write_receipt(directory[1], *sample, KEY)
    )
    held(receipt, result, "io_limit")
    limit = 16_401 if operation == "read" else len(receipt._seal_receipt(*sample, KEY)) + 16
    assert calls == limit


@pytest.mark.parametrize("written", [0, -1, True, None, 1_000_000])
def test_invalid_write_progress_retains_file(receipt, sample, directory, monkeypatch, written):
    monkeypatch.setattr(os, "write", lambda *args: written)
    held(receipt, receipt._write_receipt(directory[1], *sample, KEY), "io_failure")
    assert (directory[0] / filename()).exists()


def test_partial_write_failure_is_never_repaired(receipt, sample, directory, monkeypatch):
    original = os.write
    calls = 0

    def partial(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(fd, data[:9])
        raise OSError("disposable fault")

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", partial)
        held(receipt, receipt._write_receipt(directory[1], *sample, KEY), "io_failure")
    path = directory[0] / filename()
    raw, stamp = path.read_bytes(), path.stat().st_mtime_ns
    assert len(raw) == 9
    held(receipt, receipt._write_receipt(directory[1], *sample, KEY), "invalid_json")
    assert path.read_bytes() == raw and path.stat().st_mtime_ns == stamp


@pytest.mark.parametrize("fault", ["error", "early-eof", "overflow", "bad-result"])
def test_read_failures_are_holds(receipt, sample, directory, monkeypatch, fault):
    existing_file(receipt, sample, directory)

    def faulty(*args):
        if fault == "error":
            raise OSError("disposable fault")
        return {"early-eof": b"", "overflow": b"x" * 4096, "bad-result": None}[fault]

    monkeypatch.setattr(os, "read", faulty)
    code = {
        "error": "io_failure",
        "early-eof": "entry_changed",
        "overflow": "invalid_json",
        "bad-result": "io_failure",
    }[fault]
    held(receipt, receipt._read_receipt(directory[1], sample[0], KEY), code)


@pytest.mark.parametrize("target", ["file", "directory"])
def test_fsync_failure_retains_hold_and_bytes(receipt, sample, directory, monkeypatch, target):
    original = os.fsync
    with monkeypatch.context() as patch:

        def fail(fd):
            if (fd == directory[1]) == (target == "directory"):
                raise OSError("disposable sync fault")
            return original(fd)

        patch.setattr(os, "fsync", fail)
        failure = held(receipt, receipt._write_receipt(directory[1], *sample, KEY), "io_failure")
    raw = (directory[0] / filename()).read_bytes()
    authenticated = receipt._read_receipt(directory[1], sample[0], KEY)
    assert authenticated.raw == raw
    replay = receipt._write_receipt(directory[1], *sample, KEY)
    assert replay.state == "replayed" and replay.receipt == authenticated
    assert failure.code == "io_failure"  # Later byte authentication clears no prior failure.


def test_readback_failure_is_not_created(receipt, sample, directory, monkeypatch):
    monkeypatch.setattr(receipt, "_read_checked", lambda *args: receipt._ReceiptHold("io_failure"))
    held(receipt, receipt._write_receipt(directory[1], *sample, KEY), "io_failure")
    assert (directory[0] / filename()).is_file()


@pytest.mark.parametrize("operation", ["read", "write"])
def test_close_failure_closes_owned_fd_once_never_borrowed(
    receipt, sample, directory, monkeypatch, operation
):
    if operation == "read":
        existing_file(receipt, sample, directory)
    original_close = os.close
    closed = []

    def close_once(fd):
        assert fd != directory[1]
        closed.append(fd)
        original_close(fd)
        raise OSError("disposable close fault")

    with monkeypatch.context() as patch:
        patch.setattr(os, "close", close_once)
        result = (
            receipt._read_receipt(directory[1], sample[0], KEY)
            if operation == "read"
            else receipt._write_receipt(directory[1], *sample, KEY)
        )
        held(receipt, result, "io_failure")
    assert len(closed) == len(set(closed))
    assert os.fstat(directory[1]).st_ino == directory[0].stat().st_ino


def test_absence_and_open_failures_are_closed(receipt, sample, directory, monkeypatch):
    held(receipt, receipt._read_receipt(directory[1], sample[0], KEY), "not_found")

    def denied(*args, **kwargs):
        raise PermissionError("disposable open fault")

    patch_open(monkeypatch, denied)
    held(receipt, receipt._write_receipt(directory[1], *sample, KEY), "io_failure")
    assert list(directory[0].iterdir()) == []


def test_source_provenance_and_lazy_observation_imports(receipt):
    root = Path(__file__).resolve().parents[2]
    expected = {
        "controlled_process_clock": (
            "1f026c7da808692d69f8724e86fda1c6ac6bda49b6d886276df20c49c7e82e77"
        ),
        "controlled_process_identity": (
            "b2799ea7fdcf6e9a94b86265181a87181e64d82c9e73a2e773bfb9a5f563343d"
        ),
    }
    assert Path(receipt.__file__).resolve() == root / "stackos/controlled_process_receipt.py"
    for name, expected_hash in expected.items():
        module = importlib.import_module("stackos." + name)
        path = root / "stackos" / (name + ".py")
        assert Path(module.__file__).resolve() == path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
        importlib.reload(module)  # CDLL is forbidden; importing must remain lazy.


# Slot component fixtures use synthetic bytes and real disposable descriptors.
# They do not stand in for canonical live admission or a trusted host wrapper.
@pytest.fixture
def allocated(receipt, sample, directory):
    slot = receipt._allocate_receipt_slot(directory[1], sample[0])
    assert type(slot) is receipt._AllocatedSlot
    try:
        yield slot
    finally:
        slot._close_once()


def fill_allocated(receipt, sample, directory, slot):
    return receipt._fill_allocated_receipt_slot(
        directory[1], slot, sample[0], KEY, receipt._seal_receipt(*sample, KEY)
    )


def changed_stat(info, index):
    names = (
        "st_dev",
        "st_ino",
        "st_uid",
        "st_gid",
        "st_mode",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    data = {name: getattr(info, name) for name in names}
    data[names[index]] += 1
    return SimpleNamespace(**data)


def test_slot_own_write_uses_distinct_empty_and_written_fingerprints(
    receipt, sample, directory, allocated
):
    frozen = allocated.fempty
    assert frozen[6] == 0 and allocated.offset == 0
    result = fill_allocated(receipt, sample, directory, allocated)
    assert type(result) is receipt._WriteReceiptResult and result.state == "created"
    assert allocated.fempty == frozen
    assert receipt._file_fingerprint(os.fstat(allocated.fd)) != frozen
    assert receipt._directory(directory[1]) == allocated.dalloc
    assert allocated.consume_state == "CONSUMED" and allocated.fill_state == "VERIFIED"
    held(receipt, fill_allocated(receipt, sample, directory, allocated), "conflict")
    assert allocated._close_once() is None and allocated.close_state == "CLOSED"
    assert allocated._close_once() is None
    assert os.fstat(directory[1]).st_ino == directory[0].stat().st_ino
    with pytest.raises(FrozenInstanceError):
        allocated.dalloc = (0,) * 6


@pytest.mark.parametrize(
    "kind", ["empty", "partial", "valid", "wrong-binding", "symlink", "fifo", "hardlink"]
)
def test_slot_allocation_refuses_existing_entry(receipt, sample, directory, kind):
    folder, fd = directory
    path = folder / filename()
    if kind == "symlink":
        path.symlink_to(folder / "missing")
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    elif kind == "hardlink":
        target = folder / "other"
        target.write_bytes(b"fixture")
        os.link(target, path)
    else:
        raw = b"" if kind == "empty" else b"partial"
        if kind == "valid":
            raw = receipt._seal_receipt(*sample, KEY)
        elif kind == "wrong-binding":
            raw = receipt._seal_receipt(replace(sample[0], epoch=2), sample[1], KEY)
        path.write_bytes(raw)
        path.chmod(0o600)
    before = path.lstat()
    held(receipt, receipt._allocate_receipt_slot(fd, sample[0]), "conflict")
    after = path.lstat()
    assert (before.st_ino, before.st_mode, before.st_size, before.st_nlink) == (
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_nlink,
    )


@pytest.mark.parametrize("kind", ["valid", "empty", "partial", "strict-create-drift"])
def test_slot_legacy_read_replay_and_strict_write_compatibility(
    receipt, sample, directory, monkeypatch, kind
):
    if kind == "valid":
        slot = receipt._allocate_receipt_slot(directory[1], sample[0])
        try:
            result = fill_allocated(receipt, sample, directory, slot)
        finally:
            slot._close_once()
        assert receipt._read_receipt(directory[1], sample[0], KEY) == result.receipt
        replay = receipt._write_receipt(directory[1], *sample, KEY)
        assert replay.state == "replayed" and replay.receipt == result.receipt
    elif kind in {"empty", "partial"}:
        path = existing_file(receipt, sample, directory, b"" if kind == "empty" else b"partial")
        before = path.read_bytes()
        held(receipt, receipt._allocate_receipt_slot(directory[1], sample[0]), "conflict")
        code = "unsafe_entry" if kind == "empty" else "invalid_json"
        held(receipt, receipt._write_receipt(directory[1], *sample, KEY), code)
        assert path.read_bytes() == before
    else:
        original = receipt._directory
        before = original(directory[1])
        calls = 0

        def drift(fd):
            nonlocal calls
            calls += 1
            return before if calls == 1 else (*before[:5], before[5] + 1)

        monkeypatch.setattr(receipt, "_directory", drift)
        held(receipt, receipt._write_receipt(directory[1], *sample, KEY), "entry_changed")
        assert calls > 1 and (directory[0] / filename()).is_file()


@pytest.mark.parametrize("index", range(6))
@pytest.mark.parametrize(
    "stage", ["waiting", "claim", "body-prewrite", "write-sync", "readback", "final"]
)
def test_slot_frozen_directory_rejects_drift(
    receipt, sample, directory, allocated, monkeypatch, index, stage
):
    original_dir = receipt._directory
    original_write, original_read, original_sync = os.write, os.read, os.fsync
    original_checked = receipt._read_checked
    frozen = allocated.dalloc
    active = stage in {"waiting", "claim"}
    calls = 0
    writes = 0

    def directory_view(fd):
        nonlocal calls
        calls += 1
        if active or (stage == "body-prewrite" and calls >= 2):
            changed = list(allocated.dalloc)
            changed[index] += 1
            return tuple(changed)
        return original_dir(fd)

    def write(fd, raw):
        nonlocal writes
        writes += 1
        return original_write(fd, raw)

    def sync(fd):
        nonlocal active
        result = original_sync(fd)
        if stage == "write-sync":
            active = True
        return result

    def read(fd, size):
        nonlocal active
        result = original_read(fd, size)
        if stage == "readback":
            active = True
        return result

    def checked(*args):
        nonlocal active
        result = original_checked(*args)
        if stage == "final":
            active = True
        return result

    monkeypatch.setattr(receipt, "_directory", directory_view)
    monkeypatch.setattr(receipt, "_read_checked", checked)
    monkeypatch.setattr(os, "write", write)
    monkeypatch.setattr(os, "read", read)
    monkeypatch.setattr(os, "fsync", sync)
    if stage == "claim":
        held(
            receipt, receipt._check_allocated_receipt_slot(directory[1], allocated), "entry_changed"
        )
        assert allocated.consume_state == "FRESH"
    held(receipt, fill_allocated(receipt, sample, directory, allocated), "entry_changed")
    assert allocated.dalloc == frozen
    assert allocated.fill_state == "HOLD"
    if stage in {"waiting", "claim", "body-prewrite"}:
        assert writes == 0
    else:
        assert writes > 0


@pytest.mark.parametrize("index", range(9))
def test_slot_frozen_empty_rejected_at_actual_body(
    receipt, sample, directory, allocated, monkeypatch, index
):
    original_body, original_stat = receipt._write_opened, os.fstat
    in_body = False
    writes = []

    def body(*args, **kwargs):
        nonlocal in_body
        assert kwargs["expected_empty_fingerprint"] == allocated.fempty
        in_body = True
        return original_body(*args, **kwargs)

    def info(fd):
        observed = original_stat(fd)
        return changed_stat(observed, index) if in_body and fd == allocated.fd else observed

    monkeypatch.setattr(receipt, "_write_opened", body)
    monkeypatch.setattr(os, "fstat", info)
    monkeypatch.setattr(os, "write", lambda *args: writes.append(args))
    held(receipt, fill_allocated(receipt, sample, directory, allocated), "entry_changed")
    assert (
        writes == []
        and allocated.fempty[index] == receipt._file_fingerprint(original_stat(allocated.fd))[index]
    )


def test_slot_wrapper_to_body_drift_never_writes(
    receipt, sample, directory, allocated, monkeypatch
):
    original = receipt._write_opened
    writes = []

    def changed(*args, **kwargs):
        monkeypatch.setattr(
            receipt, "_directory", lambda fd: (*allocated.dalloc[:5], allocated.dalloc[5] + 1)
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(receipt, "_write_opened", changed)
    monkeypatch.setattr(os, "write", lambda *args: writes.append(args))
    held(receipt, fill_allocated(receipt, sample, directory, allocated), "entry_changed")
    assert writes == [] and os.fstat(allocated.fd).st_size == 0


@pytest.mark.parametrize(
    "fault", ["append", "offset", "shared-offset", "inherit", "access", "closed", "rebound"]
)
def test_slot_fd_status_and_shared_offset_drift(
    receipt, sample, directory, allocated, monkeypatch, fault
):
    if fault == "append":
        receipt.fcntl.fcntl(
            allocated.fd, receipt.fcntl.F_SETFL, allocated.status_flags | os.O_APPEND
        )
    elif fault in {"offset", "shared-offset"}:
        alias = os.dup(allocated.fd) if fault == "shared-offset" else allocated.fd
        try:
            os.lseek(alias, 1, os.SEEK_SET)
        finally:
            if alias != allocated.fd:
                os.close(alias)
    elif fault == "inherit":
        os.set_inheritable(allocated.fd, True)
    elif fault == "access":
        original = receipt._safe_fd
        monkeypatch.setattr(
            receipt,
            "_safe_fd",
            lambda fd, access: False if fd == allocated.fd else original(fd, access),
        )
    else:
        original = os.fstat

        def changed(fd):
            if fd == allocated.fd and fault == "closed":
                raise OSError("fixture closed observation")
            observed = original(fd)
            return changed_stat(observed, 1) if fd == allocated.fd else observed

        monkeypatch.setattr(os, "fstat", changed)
    result = fill_allocated(receipt, sample, directory, allocated)
    held(receipt, result, "io_failure" if fault == "closed" else "entry_changed")
    assert allocated.consume_state == "CONSUMED"


@pytest.mark.parametrize(
    "fault", ["write", "partial", "read", "file-sync", "directory-sync", "close"]
)
def test_slot_io_and_close_failures_hold(receipt, sample, directory, allocated, monkeypatch, fault):
    original_write, original_read, original_sync, original_close = (
        os.write,
        os.read,
        os.fsync,
        os.close,
    )
    writes, closes = 0, 0
    with monkeypatch.context() as patch:

        def write(fd, raw):
            nonlocal writes
            writes += 1
            if fault == "partial" and writes == 1:
                return original_write(fd, raw[:9])
            if fault in {"write", "partial"}:
                raise OSError("fixture write failure")
            return original_write(fd, raw)

        def read(fd, size):
            if fault == "read":
                raise OSError("fixture read failure")
            return original_read(fd, size)

        def sync(fd):
            if (fault == "file-sync" and fd == allocated.fd) or (
                fault == "directory-sync" and fd == directory[1]
            ):
                raise OSError("fixture sync failure")
            return original_sync(fd)

        def close(fd):
            nonlocal closes
            if fd != allocated.fd:
                return original_close(fd)
            closes += 1
            assert allocated.close_state == "ATTEMPTED"
            original_close(fd)
            if fault == "close":
                raise InterruptedError()

        patch.setattr(os, "write", write)
        patch.setattr(os, "read", read)
        patch.setattr(os, "fsync", sync)
        patch.setattr(os, "close", close)
        result = fill_allocated(receipt, sample, directory, allocated)
        if fault == "close":
            assert result.state == "created"
            held(receipt, allocated._close_once(), "io_failure")
            held(receipt, allocated._close_once(), "io_failure")
            assert allocated.close_state == "FAILED"
        else:
            held(receipt, result, "io_failure")
            assert allocated._close_once() is None
        assert closes == 1
    assert (directory[0] / filename()).exists()
    assert os.fstat(directory[1]).st_ino == directory[0].stat().st_ino


def test_slot_lost_context_never_reopens_empty_file(
    receipt, sample, directory, allocated, monkeypatch
):
    assert allocated._close_once() is None
    original_open = os.open
    opens = []

    def observe(*args, **kwargs):
        opens.append(args[1])
        return original_open(*args, **kwargs)

    patch_open(monkeypatch, observe)
    held(receipt, fill_allocated(receipt, sample, directory, allocated), "conflict")
    assert opens == []
    held(receipt, receipt._allocate_receipt_slot(directory[1], sample[0]), "conflict")
    assert len(opens) == 1 and opens[0] & os.O_EXCL
    assert (directory[0] / filename()).read_bytes() == b""


def test_slot_imports_and_effect_guards_remain_private(receipt, allocated, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("consume performed I/O")

    with monkeypatch.context() as patch:
        for name in ("open", "fstat", "read", "write", "fsync", "close"):
            patch.setattr(os, name, forbidden)
        assert allocated._consume_once() is None
        held(receipt, allocated._consume_once(), "conflict")
        held(receipt, allocated._close_once(), "conflict")
    object.__setattr__(allocated, "fill_state", "UNKNOWN")
    assert allocated._close_once() is None


def test_slot_unexpected_fill_and_close_keep_failure_history(
    receipt, sample, directory, allocated, monkeypatch
):
    def fail(*args, **kwargs):
        raise RuntimeError("fixture unexpected body")

    with monkeypatch.context() as patch:
        patch.setattr(receipt, "_write_opened", fail)
        with pytest.raises(RuntimeError, match="fixture unexpected body"):
            fill_allocated(receipt, sample, directory, allocated)
    assert allocated.fill_state == "UNKNOWN"
    original_close = os.close

    def close(fd):
        original_close(fd)
        raise RuntimeError("fixture unexpected close")

    with monkeypatch.context() as patch:
        patch.setattr(os, "close", close)
        with pytest.raises(RuntimeError, match="fixture unexpected close"):
            allocated._close_once()
        assert allocated.close_state == "UNKNOWN"
        held(receipt, allocated._close_once(), "io_failure")

    # A cooperating owner retains a primary fill exception if close also fails.
    binding = replace(sample[0], intent_ref="fixture:second-close")
    second = receipt._allocate_receipt_slot(directory[1], binding)
    assert type(second) is receipt._AllocatedSlot
    primary = None
    with monkeypatch.context() as patch:
        patch.setattr(receipt, "_write_opened", fail)
        patch.setattr(os, "close", close)
        with pytest.raises(RuntimeError, match="fixture unexpected body"):
            try:
                receipt._fill_allocated_receipt_slot(
                    directory[1],
                    second,
                    binding,
                    KEY,
                    receipt._seal_receipt(binding, sample[1], KEY),
                )
            except BaseException as error:
                primary = error
                raise
            finally:
                try:
                    second._close_once()
                except BaseException:
                    if primary is None:
                        raise
    assert second.fill_state == "UNKNOWN" and second.close_state == "UNKNOWN"
