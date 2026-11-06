"""Opt-in, bounded public output ownership; no credential discovery or source rewriting.

Only original escape sequences are interpreted, once per representation. Nested
JSON is admitted at its explicit decode boundary, never by recursive unescaping.
Malformed escapes hold conservatively. Computed/encrypted/compressed data and
downstream host-generated representations are outside this contract.
"""

from __future__ import annotations

import json
import math
import os
import stat
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_BYTES = 20_000_000
MAX_NODES = 100_000
MAX_DEPTH = 64
PRIVATE_LIMIT = 4096


class OutputRejected(ValueError):
    """Deliberately value-free error; callers must not print an unchecked fallback."""


def _unescape(text: str) -> str:
    result = []
    i = 0
    escapes = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
    while i < len(text):
        char = text[i]
        i += 1
        if char != "\\":
            result.append(char)
            continue
        if i == len(text):
            raise OutputRejected("incomplete escape representation")
        char = text[i]
        i += 1
        if char in "\r\n\u2028\u2029":
            if char == "\r" and text[i:i + 1] == "\n":
                i += 1
            continue
        if char in "xu":
            width = 2 if char == "x" else 4
            if char == "u" and text[i:i + 1] == "{":
                end = text.find("}", i + 1, i + 8)
                if end < 0:
                    raise OutputRejected("malformed Unicode escape")
                digits = text[i + 1:end]
                i = end + 1
            else:
                digits = text[i:i + width]
                i += width
                if len(digits) != width:
                    raise OutputRejected("malformed numeric escape")
            if not digits or any(c not in "0123456789abcdefABCDEF" for c in digits):
                raise OutputRejected("malformed numeric escape")
            number = int(digits, 16)
            if number > 0x10FFFF:
                raise OutputRejected("invalid Unicode escape")
            result.append(chr(number))
        elif char in "01234567":
            digits = char
            maximum = 3 if char in "0123" else 2
            while len(digits) < maximum and i < len(text) and text[i] in "01234567":
                digits += text[i]
                i += 1
            result.append(chr(int(digits, 8)))
        else:
            # Includes escaped backslash and JavaScript identity escapes.
            result.append(escapes.get(char, char))
    decoded = "".join(result)
    # Pair surrogate code units without interpreting any new backslashes.
    try:
        return decoded.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
    except UnicodeError:
        raise OutputRejected("unpaired Unicode surrogate") from None


@dataclass(frozen=True, repr=False)
class OutputPolicy:
    values: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self):
        values = tuple(self.values)
        if any(type(v) is not str or not v for v in values):
            raise OutputRejected("invalid protected-value policy")
        object.__setattr__(self, "values", values)
        if len(json.dumps(values).encode()) > PRIVATE_LIMIT:
            raise OutputRejected("protected-value policy exceeds private channel limit")

    def text(self, text: str, *, limit: int = MAX_BYTES) -> None:
        if type(text) is not str:
            raise OutputRejected("text required")
        try:
            size = len(text.encode("utf-8"))
        except UnicodeError:
            raise OutputRejected("invalid Unicode text") from None
        if size > min(limit, MAX_BYTES):
            raise OutputRejected("output exceeds bounded size")
        if not self.values:
            return
        decoded = _unescape(text)
        if any(value in text or value in decoded for value in self.values):
            raise OutputRejected("protected content withheld")

    def require(self, value: Any) -> None:
        nodes = 0
        size = 0

        def walk(item, depth):
            nonlocal nodes, size
            nodes += 1
            if nodes > MAX_NODES or depth > MAX_DEPTH:
                raise OutputRejected("structured input exceeds bounds")
            if type(item) is str:
                self.text(item)
                size += len(item.encode())
            elif item is None or type(item) in (bool, int, float):
                if type(item) is float and not math.isfinite(item):
                    raise OutputRejected("nonfinite number")
                self.text(json.dumps(item, allow_nan=False))
                size += 32
            elif type(item) is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise OutputRejected("string keys required")
                    walk(key, depth + 1)
                    walk(child, depth + 1)
            elif type(item) in (list, tuple):
                for child in item:
                    walk(child, depth + 1)
            else:
                raise OutputRejected("unsupported structured value")
            if size > MAX_BYTES:
                raise OutputRejected("structured input exceeds bounds")

        walk(value, 0)

    def retain_text(self, text: str) -> str:
        try:
            self.text(text)
            return text
        except OutputRejected:
            # Never decode/rewrite executable input. Retention alone may omit it.
            self.text("[redacted]")
            return "[redacted]"

    def encoded_text(
        self, text: str, *, prefix: str = "", suffix: str = "", limit=MAX_BYTES
    ) -> bytes:
        self.text(text, limit=limit)
        final = prefix + text + suffix
        self.text(final, limit=limit)
        return final.encode("utf-8")

    def encoded_json(self, value: Any, *, suffix: str = "", limit=MAX_BYTES, **format) -> bytes:
        self.require(value)
        text = json.dumps(value, allow_nan=False, **format)
        return self.encoded_text(text, suffix=suffix, limit=limit)

    def decode_json(self, data: bytes | str, *, limit=MAX_BYTES) -> Any:
        try:
            text = data.decode("utf-8") if type(data) is bytes else data
            self.text(text, limit=limit)
            value = json.loads(text)
            self.require(value)
            return value
        except (UnicodeError, RecursionError, json.JSONDecodeError):
            raise OutputRejected("invalid bounded JSON input") from None


def write_all(stream, data: bytes) -> None:
    """Physical partial writes consume slices of the same admitted payload."""
    offset = 0
    while offset < len(data):
        written = stream.write(data[offset:])
        if type(written) is not int or not 0 < written <= len(data) - offset:
            raise OutputRejected("output write incomplete")
        offset += written


class CheckedStream:
    """Single-owner stream: admit full cumulative framing before each first write.

    A failure poisons the stream; never append diagnostics after uncertain writes.
    Other writers to the same descriptor violate this adapter's ownership contract.
    """

    def __init__(self, policy: OutputPolicy, *, limit=MAX_BYTES):
        self.policy, self.limit = policy, limit
        self.history = b""
        self.failed = False
        self.lock = threading.Lock()

    def write(self, stream, data: bytes) -> None:
        with self.lock:
            if self.failed:
                raise OutputRejected("output stream is closed after failure")
            try:
                combined = self.history + data
                self.policy.text(combined.decode("utf-8"), limit=self.limit)
                write_all(stream, data)
                stream.flush()
                self.history = combined
            except BaseException:
                self.failed = True
                raise


class CheckedConsole:
    """Fixed stdout/stderr destinations, coalescing physical aliases.

    Writes use pinned descriptor duplicates, never a mutable Python wrapper.
    One canonical descriptor/history owns each destination, including separate
    handles to the same regular file. External writers/seeks are not authorized.
    """

    def __init__(self, policy: OutputPolicy, *streams):
        self.policy = policy
        self.lock = threading.Lock()
        self.closed = False
        self.routes = []
        self.destinations = {}
        try:
            for stream in streams:
                descriptor = stream.fileno()
                identity = self._identity(descriptor)
                if os.isatty(descriptor) and any(
                    os.isatty(existing) and previous != identity
                    for _stream, existing, previous in self.routes
                ):
                    # /dev/tty and its concrete tty path can alias despite
                    # different device numbers; portable identity is unresolved.
                    raise OutputRejected("console terminal aliasing is unresolved")
                if identity not in self.destinations:
                    duplicate = os.dup(descriptor)
                    try:
                        if self._identity(duplicate) != identity:
                            raise OutputRejected("console destination changed during admission")
                        sink = os.fdopen(duplicate, "wb", buffering=0)
                    except BaseException:
                        os.close(duplicate)
                        raise
                    self.destinations[identity] = (CheckedStream(policy), sink)
                self.routes.append((stream, descriptor, identity))
        except Exception:
            self.close()
            raise OutputRejected("console destination ownership is unavailable") from None

    @staticmethod
    def _identity(descriptor):
        if type(descriptor) is not int or descriptor < 0:
            raise OutputRejected("inspectable console descriptor required")
        info = os.fstat(descriptor)
        kind = stat.S_IFMT(info.st_mode)
        if stat.S_ISCHR(info.st_mode):
            if not os.isatty(descriptor) and info.st_rdev != os.stat(os.devnull).st_rdev:
                raise OutputRejected("unresolved console character device")
            # tty device aliases can have different filesystem inode identities.
            return (kind, info.st_rdev)
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISFIFO(info.st_mode)
                or stat.S_ISSOCK(info.st_mode)) or info.st_ino == 0:
            raise OutputRejected("unresolved console destination")
        return (kind, info.st_dev, info.st_ino)

    def write(self, stream, data: bytes) -> None:
        with self.lock:
            if self.closed:
                raise OutputRejected("console owner is closed")
            try:
                selected = None
                for original, descriptor, identity in self.routes:
                    if original.fileno() != descriptor or self._identity(descriptor) != identity:
                        raise OutputRejected("console descriptor was rebound")
                    if original is stream:
                        selected = identity
                if selected is None:
                    raise OutputRejected("console wrapper was rebound or is uninspectable")
                owner, sink = self.destinations[selected]
                owner.write(sink, data)
            except BaseException:
                self.closed = True
                raise

    def close(self) -> None:
        with self.lock:
            self.closed = True
            for _owner, sink in self.destinations.values():
                # Teardown must not trigger an unchecked interpreter diagnostic,
                # and one failed close must not strand the other duplicate.
                with suppress(OSError):
                    sink.close()


def atomic_write(path: Path, data: bytes, *, policy: OutputPolicy) -> None:
    """Own final admission before creating a file; hashes use these same bytes."""
    policy.text(data.decode("utf-8"))
    fd, temporary = tempfile.mkstemp(prefix=".checked-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            write_all(stream, data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def private_policy_pipe(policy: OutputPolicy) -> int:
    """Anonymous bounded privileged channel, never public argv/env/files."""
    data = json.dumps(policy.values).encode()
    read, write = os.pipe()
    try:
        if len(data) > os.fpathconf(write, "PC_PIPE_BUF"):
            raise OutputRejected("private policy exceeds atomic pipe capacity")
        if os.write(write, data) != len(data):
            raise OutputRejected("private policy transfer incomplete")
    except BaseException:
        os.close(read)
        raise
    finally:
        os.close(write)
    return read


def receive_private_policy(fd: int) -> OutputPolicy:
    try:
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(PRIVATE_LIMIT + 1)
        if len(data) > PRIVATE_LIMIT:
            raise OutputRejected("private policy exceeds bound")
        values = json.loads(data)
        if type(values) is not list:
            raise OutputRejected("invalid private policy")
        return OutputPolicy(tuple(values))
    except (UnicodeError, json.JSONDecodeError):
        raise OutputRejected("invalid private policy") from None
