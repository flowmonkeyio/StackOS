"""Finite synthetic report producer. Standalone stdlib entrypoint under -I -S."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

INPUT_SHA256 = "37b752df834a883cf0ca9f83f1ab6df96ead1fcfd91d420b65c85f2201121d7f"
REPORT_SHA256 = "ed21d51f672f7c0110a6c256dadf5f7067d282d4fb0ae3a7c4fa37d8fddd760c"
INPUT = Path(__file__).absolute().parents[1] / "fixtures/controlled-status-input-v1.json"


def directory_fd(path: Path) -> int:
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise ValueError("noncanonical directory")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            previous, fd = fd, child
            os.close(previous)
        result, fd = fd, -1
        return result
    finally:
        if fd != -1:
            os.close(fd)


def read_regular(directory: int, name: str, limit: int) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("nonregular input/output")
        raw = os.read(fd, limit + 1)
        if len(raw) != limit:
            raise ValueError("unexpected byte count")
        return raw
    finally:
        os.close(fd)


def strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def report_bytes(raw: bytes) -> bytes:
    value = json.loads(raw, object_pairs_hook=strict_pairs)
    if (
        type(value) is not dict
        or value.keys() != {"schema_version", "records"}
        or value["schema_version"] != "stackos.synthetic-status-input.v1"
        or type(value["records"]) is not list
        or len(value["records"]) != 3
    ):
        raise ValueError("invalid input schema")
    statuses: dict[str, str] = {}
    for record in value["records"]:
        if (
            type(record) is not dict
            or record.keys() != {"id", "status"}
            or type(record["id"]) is not str
            or record["id"] not in {"synthetic-1", "synthetic-2", "synthetic-3"}
            or record["id"] in statuses
            or type(record["status"]) is not str
            or record["status"] not in {"blocked", "complete", "ready"}
        ):
            raise ValueError("invalid record")
        statuses[record["id"]] = record["status"]
    return (
        json.dumps(
            dict(
                schema_version="stackos.synthetic-status-report.v1",
                total=3,
                by_status={
                    key: list(statuses.values()).count(key)
                    for key in ("blocked", "complete", "ready")
                },
                blocked_ids=sorted(key for key, status in statuses.items() if status == "blocked"),
                input_sha256=hashlib.sha256(raw).hexdigest(),
            ),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def produce(directory: Path) -> None:
    if (
        directory.parent.name != "controlled-reports"
        or len(directory.name) != 64
        or any(c not in "0123456789abcdef" for c in directory.name)
    ):
        raise ValueError("invalid owned workspace")
    source = directory_fd(INPUT.parent)
    try:
        raw = read_regular(source, INPUT.name, 186)
    finally:
        os.close(source)
    if hashlib.sha256(raw).hexdigest() != INPUT_SHA256:
        raise ValueError("input drift")
    output = report_bytes(raw)
    if len(output) != 227 or hashlib.sha256(output).hexdigest() != REPORT_SHA256:
        raise ValueError("report drift")
    owned = directory_fd(directory)
    try:
        info = os.fstat(owned)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("unsafe workspace")
        fd = os.open(
            "report.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=owned
        )
        try:
            if os.write(fd, output) != len(output):
                raise OSError("partial report write")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(owned)
        if read_regular(owned, "report.json", 227) != output:
            raise ValueError("report readback differs")
    finally:
        os.close(owned)


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("exact workspace required")
        produce(Path(sys.argv[1]))
    except Exception:
        # No paths, raw payloads or exception text in captured process output.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
