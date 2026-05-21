"""WordPress and Ghost wrapper tests against their public REST/Admin APIs."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
from pytest_httpx import HTTPXMock

from content_stack.integrations.ghost import GhostIntegration
from content_stack.integrations.wordpress import WordPressIntegration


def test_wordpress_test_credentials_uses_application_password_basic_auth(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://wp.example/wp-json/wp/v2/users/me?context=edit",
        json={"id": 7, "name": "Editor", "roles": ["editor"]},
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            integ = WordPressIntegration(
                payload=json.dumps(
                    {"username": "editor", "application_password": "app pass"}
                ).encode("utf-8"),
                project_id=project_id,
                http=client,
                site_url="https://wp.example",
            )
            return await integ.test_credentials()

    out = asyncio.run(go())
    req = httpx_mock.get_requests()[0]
    assert out == {
        "ok": True,
        "vendor": "wordpress",
        "user_id": 7,
        "name": "Editor",
        "roles": ["editor"],
    }
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"editor:app pass").decode(
        "ascii"
    )


def test_ghost_test_credentials_builds_admin_jwt(
    httpx_mock: HTTPXMock,
    project_id: int,
) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://ghost.example/ghost/api/admin/users/?limit=1&include=roles",
        json={
            "users": [
                {
                    "id": "u1",
                    "name": "Editor",
                    "roles": [{"name": "Editor"}],
                }
            ]
        },
    )

    async def go() -> object:
        async with httpx.AsyncClient() as client:
            integ = GhostIntegration(
                payload=b"keyid:00112233445566778899aabbccddeeff",
                project_id=project_id,
                http=client,
                site_url="https://ghost.example",
                api_version="v5.0",
            )
            return await integ.test_credentials()

    out = asyncio.run(go())
    req = httpx_mock.get_requests()[0]
    assert out["ok"] is True
    assert out["vendor"] == "ghost"
    assert req.headers["authorization"].startswith("Ghost ")
    assert req.headers["accept-version"] == "v5.0"
    token = req.headers["authorization"].removeprefix("Ghost ")
    assert len(token.split(".")) == 3
