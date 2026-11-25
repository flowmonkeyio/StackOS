"""Local archive identities for automatic delivery, without artifact storage."""

from __future__ import annotations

import os
import re
import tarfile
from copy import deepcopy
from hashlib import sha256
from pathlib import Path, PurePosixPath

from stackos.repositories.base import ValidationError


def _local_path(directory: Path, value: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValidationError("Archive candidate paths must be nonempty strings")
    path = Path(value)
    path = path if path.is_absolute() else directory / path
    if not path.is_relative_to(directory) or ".." in path.parts:
        raise ValidationError("Archive candidate paths must remain in the delivery directory")
    for parent in (path, *path.parents):
        if parent == directory:
            break
        if parent.is_symlink():
            raise ValidationError("Archive candidate paths cannot traverse symbolic links")
    return path


def _archive(directory: Path, value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"path", "sha256", "root", "exclude"}:
        raise ValidationError("Archive identity requires path, sha256, root and exclude")
    if not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
        raise ValidationError("Archive identity requires a lowercase SHA256")
    archive, root = (_local_path(directory, value[key]) for key in ("path", "root"))
    if not archive.is_file() or not root.is_dir():
        raise ValidationError("Archive candidate archive or live root is unavailable")
    excludes = value["exclude"]
    if (
        not isinstance(excludes, list)
        or any(
            not isinstance(item, str)
            or not item
            or PurePosixPath(item).is_absolute()
            or any(part in {"", ".", ".."} for part in item.split("/"))
            for item in excludes
        )
        or len(set(excludes)) != len(excludes)
    ):
        raise ValidationError("Archive exclusions must be distinct relative directory paths")

    def excluded(name):
        return any(name == item or name.startswith(item + "/") for item in excludes)

    content = archive.read_bytes()
    if sha256(content).hexdigest() != value["sha256"]:
        raise ValidationError("Archive candidate SHA256 mismatch")
    members = {}
    try:
        # Read the same bytes that were hashed, never a reopened mutable path.
        from io import BytesIO

        with tarfile.open(fileobj=BytesIO(content), mode="r:*") as bundle:
            seen = set()
            for entry in bundle:
                name = entry.name.removeprefix("./").rstrip("/")
                if not name or name == ".":
                    if entry.isdir():
                        continue
                    raise ValidationError("Unsafe archive candidate member")
                if (
                    name in seen
                    or PurePosixPath(name).is_absolute()
                    or any(part in {"", ".", ".."} for part in name.split("/"))
                    or excluded(name)
                    or not (entry.isfile() or entry.isdir())
                ):
                    raise ValidationError("Unsafe, excluded or duplicate archive candidate member")
                seen.add(name)
                if entry.isfile():
                    stream = bundle.extractfile(entry)
                    assert stream is not None
                    members[name] = sha256(stream.read()).hexdigest()
    except (tarfile.TarError, OSError) as error:
        raise ValidationError(f"Cannot read archive candidate: {error}") from None
    if not members:
        raise ValidationError("Archive candidate cannot be empty")
    actual = {}

    def walk_error(error: OSError) -> None:
        raise ValidationError("Cannot inspect complete live archive candidate inventory") from error

    for current, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        current_path = Path(current)
        dirs[:] = [
            name
            for name in dirs
            if not excluded((current_path / name).relative_to(root).as_posix())
        ]
        for name in [*dirs, *files]:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if excluded(relative):
                if not path.is_dir():
                    raise ValidationError("Archive exclusions must identify directories")
                continue
            if path.is_symlink() or (not path.is_dir() and not path.is_file()):
                raise ValidationError("Live archive candidate contains a link or special file")
            if path.is_file():
                actual[relative] = sha256(path.read_bytes()).hexdigest()
    if actual != members:
        raise ValidationError("Archive candidate differs from live source/build contents")
    return deepcopy(value)


def archive_candidate(directory: str, candidate: dict) -> dict:
    """Verify both complete declared live inventories against their sealed archives."""
    if (
        not isinstance(candidate, dict)
        or set(candidate) != {"kind", "source", "build"}
        or candidate.get("kind") != "archives"
    ):
        raise ValidationError("Archive candidate requires kind=archives, source and build")
    root = Path(directory).resolve()
    return {
        "kind": "archives",
        **{key: _archive(root, candidate[key]) for key in ("source", "build")},
    }


def archive_layout(candidate: dict) -> dict:
    """The admitted live scope cannot shrink between repair attempts."""
    return {
        key: {field: candidate[key][field] for field in ("root", "exclude")}
        for key in ("source", "build")
    }


def archive_source_digest(directory: str, candidate: dict) -> str:
    """Compare source bytes, so repacking an unchanged source cannot buy a repair."""
    import json
    from io import BytesIO

    source = candidate["source"]
    content = _local_path(Path(directory).resolve(), source["path"]).read_bytes()
    if sha256(content).hexdigest() != source["sha256"]:
        raise ValidationError("Prior source archive changed; cannot validate repair")
    with tarfile.open(fileobj=BytesIO(content), mode="r:*") as bundle:
        inventory = {}
        for member in bundle:
            if member.isfile():
                stream = bundle.extractfile(member)
                if stream is None:
                    raise ValidationError("Source archive member has no readable contents")
                inventory[member.name.removeprefix("./")] = sha256(stream.read()).hexdigest()
    return sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
