"""Explicit host-side file writes for the existing download operations."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DOWNLOAD_TOOLS = {
    "artifact.download": ("artifact", "artifact_id"),
    "actionCall.download": ("action-output", "action_call_id"),
}


def materialize_download(
    client: Any,
    *,
    server_url: str,
    headers: dict[str, str],
    tool_name: str,
    arguments: dict[str, Any],
    descriptor: dict[str, Any],
    destination_path: str,
) -> dict[str, Any]:
    """Fetch only the selected server's exact owned-file route; never follow redirects."""
    destination = Path(destination_path)
    if not destination.is_absolute() or not destination.parent.is_dir():
        raise ValueError(
            "destination_path must be an absolute new file in an existing local directory"
        )
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination_path already exists; choose a new local file")
    source, field = DOWNLOAD_TOOLS[tool_name]
    project_id, file_id = arguments.get("project_id"), arguments.get(field)
    if any(type(value) is not int or value <= 0 for value in (project_id, file_id)):
        raise ValueError("download requires exact project and stored file identifiers")
    expected_uri = f"/api/v1/projects/{project_id}/files/{source}/{file_id}"
    checksum, size = descriptor.get("sha256"), descriptor.get("bytes")
    if (
        descriptor.get("download_uri") != expected_uri
        or not isinstance(checksum, str)
        or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
        or type(size) is not int
        or size < 0
    ):
        raise ValueError("server did not return a valid download descriptor")
    origin = urlsplit(server_url)
    if (
        origin.scheme not in {"http", "https"}
        or not origin.netloc
        or origin.username
        or origin.password
    ):
        raise ValueError("a selected StackOS server origin is required")
    url = f"{origin.scheme}://{origin.netloc}{expected_uri}"
    fd, temporary = tempfile.mkstemp(prefix=".stackos-download-", dir=destination.parent)
    try:
        digest = hashlib.sha256()
        count = 0
        with os.fdopen(fd, "wb") as output:
            with client.stream("GET", url, headers=headers, follow_redirects=False) as response:
                if response.status_code != 200:
                    raise ValueError(f"authorized download failed (HTTP {response.status_code})")
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    if count > size:
                        raise ValueError("download size does not match its descriptor")
                    output.write(chunk)
                    digest.update(chunk)
            if count != size or digest.hexdigest() != checksum:
                raise ValueError("download checksum or size does not match its descriptor")
            output.flush()
            os.fsync(output.fileno())
        # Atomic no-clobber publication: even a file created during the fetch wins.
        os.link(temporary, destination)
    finally:
        os.unlink(temporary)
    return {**descriptor, "local_path": str(destination)}
