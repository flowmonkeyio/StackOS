"""Fetch one reviewed registry artifact, without running package scripts."""

import base64
import hashlib
import io
import json
import os
import tarfile
import tempfile
import urllib.request
from pathlib import Path

URL = "https://registry.npmjs.org/@prebuilt-tdlib/darwin-arm64/-/darwin-arm64-0.1008067.0.tgz"
INTEGRITY = (
    "sha512-dFefumR1NX2SSYf9p6fXyeLxw1+EL00Qe86ECqbtllWdP476gseScqU34hSzaR4RY"
    "sIuQZJDjaYGt1v2YiI7HA=="
)
ARCHIVE_SHA256 = "84aac79ec7b8892798744963aad4590750f3f4c21c5976503d5b47202882f89a"
LIBRARY_SHA256 = "67ea9d6f0494ca88c57b97f4d779977b764465773ef137d10099c8c75f555303"
COMMIT = "d1085f9cebc5a62379991ae1652673954f229c1f"


def verify_archive(data):
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if integrity != INTEGRITY or hashlib.sha256(data).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("TDLib registry archive checksum mismatch")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if (
            len(names) != len(set(names))
            or set(names)
            != {
                "package/LICENSE",
                "package/README.md",
                "package/package.json",
                "package/libtdjson.dylib",
            }
            or any(not member.isfile() for member in members)
        ):
            raise ValueError("Unexpected TDLib registry archive contents")
        metadata = json.load(archive.extractfile("package/package.json"))
        if (
            metadata.get("name"),
            metadata.get("version"),
            metadata.get("tdlib"),
            metadata.get("os"),
            metadata.get("cpu"),
        ) != (
            "@prebuilt-tdlib/darwin-arm64",
            "0.1008067.0",
            {"commit": COMMIT, "version": "1.8.67"},
            ["darwin"],
            ["arm64"],
        ):
            raise ValueError("Unexpected TDLib package identity")
        library = archive.extractfile("package/libtdjson.dylib").read()
        if hashlib.sha256(library).hexdigest() != LIBRARY_SHA256:
            raise ValueError("TDLib registry library checksum mismatch")
        return library


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def fetch(cache=None):
    root = (
        cache
        or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "stackos/build-inputs/tdlib-npm-0.1008067.0"
    )
    root.mkdir(parents=True, exist_ok=True)
    archive_path = root / "package.tgz"
    library_path = root / "libtdjson.dylib"
    data = None
    if archive_path.is_file() and not archive_path.is_symlink():
        try:
            data = archive_path.read_bytes()
            library = verify_archive(data)
        except (OSError, ValueError, tarfile.TarError):
            data = None
    if data is None:
        with urllib.request.urlopen(URL, timeout=60) as response:
            if response.url != URL:
                raise ValueError("Unexpected TDLib registry redirect")
            data = response.read(40 * 1024 * 1024 + 1)
        library = verify_archive(data)
        atomic_write(archive_path, data)
    # Recreate a missing/corrupt extracted file only from the verified archive.
    if (
        library_path.is_symlink()
        or not library_path.is_file()
        or library_path.read_bytes() != library
    ):
        atomic_write(library_path, library)
    return library_path.resolve()


if __name__ == "__main__":
    print(fetch())
