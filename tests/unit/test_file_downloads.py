"""Client materialization rejects corrupt/redirected bytes without local side effects."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from stackos.mcp.bridge.downloads import materialize_download


@pytest.mark.parametrize(
    "failure", ["checksum", "size", "redirect", "denied", "foreign-url", "local-race"]
)
def test_failed_download_never_publishes_or_overwrites(tmp_path, failure):
    data = b"exact bytes"
    uri = "/api/v1/projects/1/files/artifact/2"
    destination = tmp_path / "result.bin"
    descriptor = {
        "download_uri": uri,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    if failure == "checksum":
        descriptor["sha256"] = "0" * 64
    if failure == "size":
        descriptor["bytes"] = 1
    if failure == "foreign-url":
        descriptor["download_uri"] = "https://foreign.invalid/secret"
    calls = []

    def respond(request):
        calls.append(request)
        assert str(request.url) == "https://selected.test" + uri
        if failure == "local-race":
            destination.write_bytes(b"other writer")
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://foreign.invalid/secret"})
        if failure == "denied":
            return httpx.Response(403)
        return httpx.Response(200, content=data)

    with (
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
        pytest.raises((ValueError, FileExistsError)),
    ):
        materialize_download(
            client,
            server_url="https://selected.test/mcp",
            headers={"Authorization": "Bearer fixture"},
            tool_name="artifact.download",
            arguments={"project_id": 1, "artifact_id": 2},
            descriptor=descriptor,
            destination_path=str(destination),
        )
    assert len(calls) == (0 if failure == "foreign-url" else 1)
    if failure == "local-race":
        assert destination.read_bytes() == b"other writer"
    else:
        assert not destination.exists()
    assert not list(tmp_path.glob(".stackos-download-*"))
