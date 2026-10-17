"""Private supplied-key byte integrity and logical create-once receipt I/O.

Authentication here establishes neither key custody nor host origin, freshness,
armed state, permission, process control, durability history or certification.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import re
import stat
from contextlib import suppress
from dataclasses import dataclass, field, fields
from typing import Any, Literal, cast
from uuid import UUID

from stackos import controlled_process_clock as clock
from stackos import controlled_process_identity as identity

try:
    import fcntl
except ImportError:  # Unsupported hosts still import the codec.
    fcntl = None  # type: ignore[assignment]

_SCHEMA = "stackos.controlled-arm-receipt.v1"
_DOMAIN = b"StackOS\x00controlled-arm-receipt\x00v1\x00"
_MAX_BYTES = 16_384
_MAX_INT = 2**63 - 1
_MAX_PID = 2**31 - 1
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]*", re.ASCII)
_ITEM = re.compile(r"(?:task|ticket):([1-9][0-9]{0,18})", re.ASCII)
_HEX = re.compile(r"[0-9a-f]{64}", re.ASCII)
type _Table = dict[str, Any]
type _Fingerprint = tuple[int, ...]
type D6 = tuple[int, int, int, int, int, int]
type F9 = tuple[int, int, int, int, int, int, int, int, int]


@dataclass(frozen=True)
class _Binding:
    project_id: int
    canonical_item_id: str
    owner_ref: str
    epoch: int
    reservation_revision: int
    scope_digest: str
    controller_run_id: int
    run_plan_id: int
    run_plan_step_id: int
    attempt_run_id: int
    intent_ref: str
    manifest_digest: str
    host_ref: str
    boot_uuid: str


@dataclass(frozen=True)
class _ArmFact:
    kind: Literal["armed"]
    origin: Literal["host-shim"]
    sequence: int
    clock: clock._ClockObservation
    shim: identity._ProcessObservation
    leader: identity._ProcessObservation
    deadline_continuous_ns: int
    cancel_intent_digest: str
    barrier_nonce: str


@dataclass(frozen=True)
class _ReceiptHold:
    code: str


@dataclass(frozen=True)
class _KeyAuthenticatedReceipt:
    binding: _Binding
    fact: _ArmFact
    raw: bytes
    digest: str


@dataclass(frozen=True)
class _WriteReceiptResult:
    state: Literal["created", "replayed"]
    receipt: _KeyAuthenticatedReceipt


@dataclass(frozen=True)
class _AllocatedSlot:
    """One cooperating caller's owned FD, never an execution capability."""

    fd: int
    binding: _Binding
    binding_digest: str
    name: str
    dalloc: D6
    fempty: F9
    status_flags: int
    offset: int
    consume_state: Literal["FRESH", "CONSUMED"] = field(default="FRESH", init=False)
    fill_state: Literal["NOT_RUN", "IN_PROGRESS", "VERIFIED", "HOLD", "UNKNOWN"] = field(
        default="NOT_RUN", init=False
    )
    fill_hold: _ReceiptHold | None = field(default=None, init=False)
    close_state: Literal["OPEN", "ATTEMPTED", "CLOSED", "FAILED", "UNKNOWN"] = field(
        default="OPEN", init=False
    )
    close_hold: _ReceiptHold | None = field(default=None, init=False)

    def _consume_once(self) -> _ReceiptHold | None:
        if not _valid_slot(self):
            return _ReceiptHold("unsafe_entry")
        if (
            self.consume_state != "FRESH"
            or self.fill_state != "NOT_RUN"
            or self.fill_hold is not None
            or self.close_state != "OPEN"
            or self.close_hold is not None
        ):
            return _ReceiptHold("conflict")
        object.__setattr__(self, "consume_state", "CONSUMED")
        object.__setattr__(self, "fill_state", "IN_PROGRESS")
        return None

    def _close_once(self) -> _ReceiptHold | None:
        if self.close_state == "CLOSED":
            return None
        if self.close_state != "OPEN":
            return self.close_hold or _ReceiptHold("io_failure")
        if self.fill_state == "IN_PROGRESS":
            return _ReceiptHold("conflict")
        # The original integer is spent BEFORE close, including EINTR/unknown.
        object.__setattr__(self, "close_state", "ATTEMPTED")
        try:
            os.close(self.fd)
        except OSError:
            hold = _ReceiptHold("io_failure")
            object.__setattr__(self, "close_state", "FAILED")
            object.__setattr__(self, "close_hold", hold)
            return hold
        except BaseException:
            object.__setattr__(self, "close_state", "UNKNOWN")
            object.__setattr__(self, "close_hold", _ReceiptHold("io_failure"))
            raise
        object.__setattr__(self, "close_state", "CLOSED")
        return None


type _ReadResult = tuple[_KeyAuthenticatedReceipt, _Fingerprint] | _ReceiptHold


def _integer(value: object, minimum: int = 1, maximum: int = _MAX_INT) -> bool:
    return type(value) is int and value.bit_length() <= 63 and minimum <= value <= maximum


def _token(value: object, maximum: int) -> bool:
    return type(value) is str and 1 <= len(value) <= maximum and bool(_TOKEN.fullmatch(value))


def _hex(value: object, *, nonzero: bool = False) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and bool(_HEX.fullmatch(value))
        and (not nonzero or value != "0" * 64)
    )


def _uuid(value: object) -> bool:
    if type(value) is not str or len(value) != 36:
        return False
    try:
        parsed = UUID(value)
        return bool(parsed.int) and str(parsed) == value
    except (ValueError, AttributeError, TypeError):
        return False


def _valid_binding(value: object) -> bool:
    if type(value) is not _Binding:
        return False
    item = (
        _ITEM.fullmatch(value.canonical_item_id) if type(value.canonical_item_id) is str else None
    )
    return (
        all(
            _integer(getattr(value, name))
            for name in (
                "project_id",
                "epoch",
                "reservation_revision",
                "controller_run_id",
                "run_plan_id",
                "run_plan_step_id",
                "attempt_run_id",
            )
        )
        and item is not None
        and int(item[1]) <= _MAX_INT
        and _token(value.owner_ref, 200)
        and _token(value.intent_ref, 128)
        and _token(value.host_ref, 128)
        and _hex(value.scope_digest)
        and _hex(value.manifest_digest)
        and _uuid(value.boot_uuid)
    )


def _valid_process(value: object) -> bool:
    if (
        type(value) is not identity._ProcessObservation
        or type(value.identity) is not identity._ProcessIdentity
    ):
        return False
    process = value.identity
    return (
        type(value.status) is str
        and value.status == "known"
        and type(value.issues) is tuple
        and value.issues == ()
        and type(process.api_version) is str
        and type(process.runtime_abi) is str
        and _uuid(process.boot_uuid)
        and identity._valid_identity(process)
        and _integer(process.pid, maximum=_MAX_PID)
        and _integer(process.parent_pid, 0, _MAX_PID)
        and _integer(process.pgid, maximum=_MAX_PID)
        and _integer(process.sid, maximum=_MAX_PID)
        and _integer(process.start_seconds)
        and _integer(process.start_microseconds, 0, 999_999)
    )


def _valid_fact(value: object, binding: _Binding) -> bool:
    if type(value) is not _ArmFact or type(value.clock) is not clock._ClockObservation:
        return False
    observation = value.clock
    if not (
        type(value.kind) is str
        and value.kind == "armed"
        and type(value.origin) is str
        and value.origin == "host-shim"
        and type(value.sequence) is int
        and value.sequence == 0
        and type(observation.status) is str
        and observation.status == "known"
        and type(observation.api_version) is str
        and type(observation.runtime_abi) is str
        and type(observation.issues) is tuple
        and observation.issues == ()
        and _uuid(observation.boot_uuid)
        and clock._valid_clock(observation)
        and _integer(observation.continuous_ns, 0)
        and _integer(observation.awake_ns, 0)
        and _valid_process(value.shim)
        and _valid_process(value.leader)
        and _integer(value.deadline_continuous_ns, 0)
        and _hex(value.cancel_intent_digest)
        and _hex(value.barrier_nonce, nonzero=True)
    ):
        return False
    shim = value.shim.identity
    leader = value.leader.identity
    assert shim is not None and leader is not None and observation.continuous_ns is not None
    return (
        binding.boot_uuid == observation.boot_uuid == shim.boot_uuid == leader.boot_uuid
        and observation.runtime_abi == shim.runtime_abi == leader.runtime_abi
        and shim.pid != leader.pid
        and shim.pgid != leader.pgid
        and value.deadline_continuous_ns > observation.continuous_ns
    )


def _table(value: object) -> _Table:
    # Callers have already required the exact declared dataclass type.
    return {field.name: getattr(value, field.name) for field in fields(cast(Any, value))}


def _payload(binding: _Binding, fact: _ArmFact) -> _Table:
    fact_table = _table(fact)
    fact_table["clock"] = _table(fact.clock) | {"issues": []}
    for name in ("shim", "leader"):
        process = getattr(fact, name)
        fact_table[name] = {
            "status": process.status,
            "identity": _table(process.identity),
            "issues": [],
        }
    return {"binding": _table(binding), "fact": fact_table}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def _mac(payload: _Table, key32: bytes) -> str:
    raw = _canonical(payload)
    return hmac.digest(
        key32, _DOMAIN + len(raw).to_bytes(4, "big", signed=False) + raw, "sha256"
    ).hex()


def _valid_key(key32: object) -> bool:
    return type(key32) is bytes and len(key32) == 32


def _seal_receipt(binding: _Binding, fact: _ArmFact, key32: bytes) -> bytes | _ReceiptHold:
    if not _valid_key(key32):
        return _ReceiptHold("invalid_key")
    if not _valid_binding(binding) or not _valid_fact(fact, binding):
        return _ReceiptHold("invalid_schema")
    payload = _payload(binding, fact)
    return _canonical({"schema": _SCHEMA, "payload": payload, "mac": _mac(payload, key32)})


def _exact_table(value: object, names: set[str]) -> bool:
    return type(value) is dict and value.keys() == names


def _decode_payload(value: object) -> tuple[_Binding, _ArmFact] | None:
    if not _exact_table(value, {"binding", "fact"}):
        return None
    payload = cast(_Table, value)
    binding_data, fact_data = payload["binding"], payload["fact"]
    if not _exact_table(
        binding_data, {field.name for field in fields(_Binding)}
    ) or not _exact_table(fact_data, {field.name for field in fields(_ArmFact)}):
        return None
    binding = _Binding(**binding_data)
    if not _valid_binding(binding):
        return None
    if not _exact_table(
        fact_data["clock"], {field.name for field in fields(clock._ClockObservation)}
    ):
        return None
    clock_data = fact_data["clock"]
    if type(clock_data["issues"]) is not list or clock_data["issues"] != []:
        return None
    fact_fields = dict(fact_data)
    fact_fields["clock"] = clock._ClockObservation(**(clock_data | {"issues": ()}))
    for name in ("shim", "leader"):
        process = fact_data[name]
        if not _exact_table(process, {"status", "identity", "issues"}):
            return None
        if (
            type(process["issues"]) is not list
            or process["issues"] != []
            or not _exact_table(
                process["identity"], {field.name for field in fields(identity._ProcessIdentity)}
            )
        ):
            return None
        fact_fields[name] = identity._ProcessObservation(
            process["status"], identity._ProcessIdentity(**process["identity"]), ()
        )
    fact = _ArmFact(**fact_fields)
    return (binding, fact) if _valid_fact(fact, binding) else None


def _scan(raw: bytes) -> str:
    text = raw.decode("ascii")
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 8:
                raise ValueError("depth")
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("depth")
    return text


def _pairs(pairs: list[tuple[str, Any]]) -> _Table:
    result: _Table = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _parse_int(text: str) -> int:
    if len(text) > 20:
        raise ValueError("integer_length")
    return int(text)


def _reject_number(_text: str) -> Any:
    raise ValueError("number")


def _authenticate_receipt(
    raw: bytes, expected_binding: _Binding, key32: bytes
) -> _KeyAuthenticatedReceipt | _ReceiptHold:
    if not _valid_key(key32):
        return _ReceiptHold("invalid_key")
    if not _valid_binding(expected_binding):
        return _ReceiptHold("invalid_schema")
    if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_BYTES:
        return _ReceiptHold("invalid_json")
    try:
        envelope = json.loads(
            _scan(raw),
            object_pairs_hook=_pairs,
            parse_int=_parse_int,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
        if not _exact_table(envelope, {"schema", "payload", "mac"}):
            return _ReceiptHold("invalid_schema")
        if (
            type(envelope["schema"]) is not str
            or envelope["schema"] != _SCHEMA
            or not _hex(envelope["mac"])
        ):
            return _ReceiptHold("invalid_schema")
        decoded = _decode_payload(envelope["payload"])
        if decoded is None:
            return _ReceiptHold("invalid_schema")
        if raw != _canonical(envelope):
            return _ReceiptHold("noncanonical")
        if not hmac.compare_digest(envelope["mac"], _mac(envelope["payload"], key32)):
            return _ReceiptHold("authentication_failed")
        binding, fact = decoded
        if binding != expected_binding:
            return _ReceiptHold("binding_mismatch")
        return _KeyAuthenticatedReceipt(binding, fact, raw, hashlib.sha256(raw).hexdigest())
    except (UnicodeError, ValueError, TypeError, OverflowError, RecursionError):
        return _ReceiptHold("invalid_json")


def _fs_supported() -> bool:
    return (
        os.name == "posix"
        and fcntl is not None
        and all(hasattr(fcntl, name) for name in ("fcntl", "F_GETFL"))
        and all(
            hasattr(os, name)
            for name in (
                "O_NOFOLLOW",
                "O_NONBLOCK",
                "O_CLOEXEC",
                "O_ACCMODE",
                "geteuid",
                "fstat",
                "fsync",
                "get_inheritable",
            )
        )
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
    )


def _directory_fingerprint(info: os.stat_result) -> D6:
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode, info.st_nlink)


def _file_fingerprint(info: os.stat_result) -> F9:
    return (*_directory_fingerprint(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory(dirfd: int) -> D6 | _ReceiptHold:
    if type(dirfd) is not int or dirfd < 0 or fcntl is None:
        return _ReceiptHold("unsafe_directory")
    try:
        info = os.fstat(dirfd)
        flags = fcntl.fcntl(dirfd, fcntl.F_GETFL)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or os.get_inheritable(dirfd)
            or flags & os.O_ACCMODE != os.O_RDONLY
        ):
            return _ReceiptHold("unsafe_directory")
        return _directory_fingerprint(info)
    except (OSError, OverflowError, ValueError):
        return _ReceiptHold("unsafe_directory")


def _same_directory(dirfd: int, before: _Fingerprint) -> bool:
    current = _directory(dirfd)
    return not isinstance(current, _ReceiptHold) and current == before


def _safe_file(info: os.stat_result, *, new: bool = False) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and stat.S_IMODE(info.st_mode) == 0o600
        and info.st_nlink == 1
        and (info.st_size == 0 if new else 1 <= info.st_size <= _MAX_BYTES)
    )


def _safe_fd(fd: int, access: int) -> bool:
    assert fcntl is not None
    return not os.get_inheritable(fd) and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == access


def _name(binding: _Binding) -> str:
    return hashlib.sha256(_canonical(_table(binding))).hexdigest() + ".receipt"


def _entry(dirfd: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=dirfd, follow_symlinks=False)


def _read_opened(
    fd: int,
    dirfd: int,
    name: str,
    before: _Fingerprint,
    entry: os.stat_result,
    binding: _Binding,
    key32: bytes,
) -> _ReadResult:
    initial = os.fstat(fd)
    fingerprint = _file_fingerprint(initial)
    if not _safe_file(initial) or not _safe_fd(fd, os.O_RDONLY):
        return _ReceiptHold("unsafe_entry")
    if fingerprint != _file_fingerprint(entry):
        return _ReceiptHold("entry_changed")
    data = bytearray()
    calls = 0
    while True:
        calls += 1
        if calls > 16_401:
            return _ReceiptHold("io_limit")
        try:
            chunk = os.read(fd, 4096)
        except InterruptedError:
            continue
        if type(chunk) is not bytes or len(chunk) > 4096:
            return _ReceiptHold("io_failure")
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > _MAX_BYTES:
            return _ReceiptHold("invalid_json")
    if (
        len(data) != initial.st_size
        or _file_fingerprint(os.fstat(fd)) != fingerprint
        or _file_fingerprint(_entry(dirfd, name)) != fingerprint
        or not _same_directory(dirfd, before)
    ):
        return _ReceiptHold("entry_changed")
    receipt = _authenticate_receipt(bytes(data), binding, key32)
    return receipt if isinstance(receipt, _ReceiptHold) else (receipt, fingerprint)


def _read_checked(dirfd: int, binding: _Binding, key32: bytes, before: _Fingerprint) -> _ReadResult:
    name = _name(binding)
    try:
        entry = _entry(dirfd, name)
    except FileNotFoundError:
        return _ReceiptHold("not_found")
    except OSError:
        return _ReceiptHold("io_failure")
    if not _safe_file(entry):
        return _ReceiptHold("unsafe_entry")
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dirfd)
    except OSError as error:
        return _ReceiptHold(
            "entry_changed" if error.errno in {errno.ENOENT, errno.ELOOP} else "io_failure"
        )
    close_failed = False
    result: _ReadResult = _ReceiptHold("io_failure")
    try:
        try:
            result = _read_opened(fd, dirfd, name, before, entry, binding, key32)
        except FileNotFoundError:
            result = _ReceiptHold("entry_changed")
        except OSError:
            result = _ReceiptHold("io_failure")
    finally:
        try:
            os.close(fd)
        except OSError:
            close_failed = True
    return _ReceiptHold("io_failure") if close_failed else result


def _read_receipt(
    dirfd: int, expected_binding: _Binding, key32: bytes
) -> _KeyAuthenticatedReceipt | _ReceiptHold:
    if not _valid_key(key32):
        return _ReceiptHold("invalid_key")
    if not _valid_binding(expected_binding):
        return _ReceiptHold("invalid_schema")
    if not _fs_supported():
        return _ReceiptHold("unsupported_fs")
    before = _directory(dirfd)
    if isinstance(before, _ReceiptHold):
        return before
    result = _read_checked(dirfd, expected_binding, key32, before)
    return result if isinstance(result, _ReceiptHold) else result[0]


def _write_opened(
    fd: int,
    dirfd: int,
    name: str,
    before: _Fingerprint,
    binding: _Binding,
    key32: bytes,
    raw: bytes,
    *,
    expected_empty_fingerprint: _Fingerprint | None = None,
) -> _WriteReceiptResult | _ReceiptHold:
    initial = os.fstat(fd)
    if (
        expected_empty_fingerprint is not None
        and _file_fingerprint(initial) != expected_empty_fingerprint
    ):
        return _ReceiptHold("entry_changed")
    if not _safe_file(initial, new=True) or not _safe_fd(fd, os.O_WRONLY):
        return _ReceiptHold("unsafe_entry")
    if _file_fingerprint(_entry(dirfd, name)) != _file_fingerprint(initial):
        return _ReceiptHold("entry_changed")
    if expected_empty_fingerprint is not None and not _same_directory(dirfd, before):
        return _ReceiptHold("entry_changed")
    offset, calls = 0, 0
    while offset < len(raw):
        calls += 1
        if calls > len(raw) + 16:
            return _ReceiptHold("io_limit")
        chunk = raw[offset : offset + 4096]
        try:
            written = os.write(fd, chunk)
        except InterruptedError:
            continue
        if type(written) is not int or not 0 < written <= len(chunk):
            return _ReceiptHold("io_failure")
        offset += written
    os.fsync(fd)
    os.fsync(dirfd)
    readback = _read_checked(dirfd, binding, key32, before)
    if isinstance(readback, _ReceiptHold):
        return readback
    receipt, fingerprint = readback
    if (
        receipt.raw != raw
        or os.fstat(fd).st_size != len(raw)
        or _file_fingerprint(os.fstat(fd)) != fingerprint
        or _file_fingerprint(_entry(dirfd, name)) != fingerprint
        or not _same_directory(dirfd, before)
    ):
        return _ReceiptHold("entry_changed")
    return _WriteReceiptResult("created", receipt)


def _write_receipt(
    dirfd: int, binding: _Binding, fact: _ArmFact, key32: bytes
) -> _WriteReceiptResult | _ReceiptHold:
    raw = _seal_receipt(binding, fact, key32)
    if isinstance(raw, _ReceiptHold):
        return raw
    if not _fs_supported():
        return _ReceiptHold("unsupported_fs")
    before = _directory(dirfd)
    if isinstance(before, _ReceiptHold):
        return before
    name = _name(binding)
    try:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            0o600,
            dir_fd=dirfd,
        )
    except FileExistsError:
        existing = _read_checked(dirfd, binding, key32, before)
        if isinstance(existing, _ReceiptHold):
            return existing
        receipt = existing[0]
        return (
            _WriteReceiptResult("replayed", receipt)
            if receipt.raw == raw
            else _ReceiptHold("conflict")
        )
    except OSError:
        return _ReceiptHold("io_failure")
    close_failed = False
    result: _WriteReceiptResult | _ReceiptHold = _ReceiptHold("io_failure")
    try:
        try:
            result = _write_opened(fd, dirfd, name, before, binding, key32, raw)
        except FileNotFoundError:
            result = _ReceiptHold("entry_changed")
        except OSError:
            result = _ReceiptHold("io_failure")
    finally:
        try:
            os.close(fd)
        except OSError:
            close_failed = True
    return _ReceiptHold("io_failure") if close_failed else result


def _stat_vector(value: object, size: int) -> bool:
    return type(value) is tuple and len(value) == size and all(type(part) is int for part in value)


def _valid_slot(slot: _AllocatedSlot) -> bool:
    return (
        type(slot) is _AllocatedSlot
        and type(slot.fd) is int
        and slot.fd >= 0
        and _valid_binding(slot.binding)
        and type(slot.name) is str
        and slot.name == _name(slot.binding)
        and _hex(slot.binding_digest)
        and slot.binding_digest == slot.name[:-8]
        and _stat_vector(slot.dalloc, 6)
        and _stat_vector(slot.fempty, 9)
        and stat.S_ISDIR(slot.dalloc[4])
        and stat.S_IMODE(slot.dalloc[4]) == 0o700
        and stat.S_ISREG(slot.fempty[4])
        and stat.S_IMODE(slot.fempty[4]) == 0o600
        and slot.fempty[5] == 1
        and slot.fempty[6] == 0
        and slot.dalloc[2] == slot.fempty[2]
        and type(slot.status_flags) is int
        and slot.status_flags >= 0
        and slot.status_flags & os.O_ACCMODE == os.O_WRONLY
        and not slot.status_flags & os.O_APPEND
        and type(slot.offset) is int
        and slot.offset == 0
        and type(slot.consume_state) is str
        and slot.consume_state in {"FRESH", "CONSUMED"}
        and type(slot.fill_state) is str
        and slot.fill_state in {"NOT_RUN", "IN_PROGRESS", "VERIFIED", "HOLD", "UNKNOWN"}
        and (slot.fill_hold is None or type(slot.fill_hold) is _ReceiptHold)
        and type(slot.close_state) is str
        and slot.close_state in {"OPEN", "ATTEMPTED", "CLOSED", "FAILED", "UNKNOWN"}
        and (slot.close_hold is None or type(slot.close_hold) is _ReceiptHold)
    )


def _allocate_receipt_slot(dirfd: int, binding: _Binding) -> _AllocatedSlot | _ReceiptHold:
    """Allocate before Dalloc; never adopt any EEXIST entry or issue authority."""
    if not _valid_binding(binding):
        return _ReceiptHold("invalid_schema")
    if not _fs_supported() or not all(
        hasattr(os, name) for name in ("lseek", "SEEK_CUR", "O_APPEND")
    ):
        return _ReceiptHold("unsupported_fs")
    before = _directory(dirfd)
    if isinstance(before, _ReceiptHold):
        return before
    name = _name(binding)
    try:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            0o600,
            dir_fd=dirfd,
        )
    except FileExistsError:
        return _ReceiptHold("conflict")
    except OSError:
        return _ReceiptHold("io_failure")
    result: _AllocatedSlot | _ReceiptHold = _ReceiptHold("io_failure")
    try:
        initial = os.fstat(fd)
        if not _safe_file(initial, new=True) or not _safe_fd(fd, os.O_WRONLY):
            result = _ReceiptHold("unsafe_entry")
        elif _file_fingerprint(_entry(dirfd, name)) != _file_fingerprint(initial):
            result = _ReceiptHold("entry_changed")
        else:
            os.fsync(fd)
            os.fsync(dirfd)
            final = os.fstat(fd)
            dalloc = _directory(dirfd)
            assert fcntl is not None
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            offset = os.lseek(fd, 0, os.SEEK_CUR)
            if (
                isinstance(dalloc, _ReceiptHold)
                or dalloc[:5] != before[:5]
                or not _safe_file(final, new=True)
                or not _safe_fd(fd, os.O_WRONLY)
                or flags & os.O_APPEND
                or offset != 0
                or _file_fingerprint(_entry(dirfd, name)) != _file_fingerprint(final)
            ):
                result = _ReceiptHold("entry_changed")
            else:
                result = _AllocatedSlot(
                    fd,
                    binding,
                    name[:-8],
                    name,
                    dalloc,
                    _file_fingerprint(final),
                    flags,
                    offset,
                )
    except FileNotFoundError:
        result = _ReceiptHold("entry_changed")
    except OSError:
        result = _ReceiptHold("io_failure")
    except BaseException:
        # No context is returned; still attempt the owned close once, preserving
        # the original exception if closing also raises.
        with suppress(BaseException):
            os.close(fd)
        raise
    if isinstance(result, _ReceiptHold):
        try:
            os.close(fd)
        except OSError:
            return _ReceiptHold("io_failure")
    return result


def _check_allocated_receipt_slot(dirfd: int, slot: _AllocatedSlot) -> _ReceiptHold | None:
    """Sample frozen facts for pre-claim/pre-fill; never grant or consume."""
    if type(slot) is not _AllocatedSlot or not _valid_slot(slot):
        return _ReceiptHold("unsafe_entry")
    if slot.close_state != "OPEN":
        return _ReceiptHold("conflict")
    if not _fs_supported() or not all(
        hasattr(os, name) for name in ("lseek", "SEEK_CUR", "O_APPEND")
    ):
        return _ReceiptHold("unsupported_fs")
    try:
        assert fcntl is not None
        current = os.fstat(slot.fd)
        if (
            not _same_directory(dirfd, slot.dalloc)
            or _file_fingerprint(current) != slot.fempty
            or _file_fingerprint(_entry(dirfd, slot.name)) != slot.fempty
            or not _safe_file(current, new=True)
            or not _safe_fd(slot.fd, os.O_WRONLY)
            or fcntl.fcntl(slot.fd, fcntl.F_GETFL) != slot.status_flags
            or os.lseek(slot.fd, 0, os.SEEK_CUR) != slot.offset
        ):
            return _ReceiptHold("entry_changed")
    except FileNotFoundError:
        return _ReceiptHold("entry_changed")
    except OSError:
        return _ReceiptHold("io_failure")
    except (ValueError, OverflowError):
        return _ReceiptHold("unsafe_entry")
    return None


def _fill_allocated_receipt_slot(
    dirfd: int, slot: _AllocatedSlot, binding: _Binding, key32: bytes, raw: bytes
) -> _WriteReceiptResult | _ReceiptHold:
    """Borrow once; the owner closes in finally before canonical commit."""
    if type(slot) is not _AllocatedSlot:
        return _ReceiptHold("unsafe_entry")
    consumed = slot._consume_once()
    if consumed is not None:
        return consumed
    try:
        authenticated = _authenticate_receipt(raw, binding, key32)
        result: _WriteReceiptResult | _ReceiptHold
        if isinstance(authenticated, _ReceiptHold):
            result = authenticated
        elif binding != slot.binding:
            result = _ReceiptHold("binding_mismatch")
        elif not _fs_supported() or not all(
            hasattr(os, name) for name in ("lseek", "SEEK_CUR", "O_APPEND")
        ):
            result = _ReceiptHold("unsupported_fs")
        else:
            checked = _check_allocated_receipt_slot(dirfd, slot)
            if checked is not None:
                result = checked
            else:
                result = _write_opened(
                    slot.fd,
                    dirfd,
                    slot.name,
                    slot.dalloc,
                    binding,
                    key32,
                    raw,
                    expected_empty_fingerprint=slot.fempty,
                )
    except FileNotFoundError:
        result = _ReceiptHold("entry_changed")
    except OSError:
        result = _ReceiptHold("io_failure")
    except BaseException:
        object.__setattr__(slot, "fill_state", "UNKNOWN")
        raise
    if isinstance(result, _ReceiptHold):
        object.__setattr__(slot, "fill_state", "HOLD")
        object.__setattr__(slot, "fill_hold", result)
    else:
        object.__setattr__(slot, "fill_state", "VERIFIED")
    return result
