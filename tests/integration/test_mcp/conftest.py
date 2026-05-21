"""Fixtures for MCP integration tests.

The ``mcp_client`` fixture wraps a ``TestClient`` with a small helper
that posts JSON-RPC bodies to ``/mcp`` and returns parsed responses.
This deliberately uses raw HTTP rather than the SDK's ``ClientSession``
because the M3 deliverable is the wire shape — we want to lock the
JSON-RPC envelope explicitly so SDK upgrades can't silently drift the
contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from content_stack.config import Settings
from content_stack.server import create_app

# ---------------------------------------------------------------------------
# Wire helper.
# ---------------------------------------------------------------------------


@dataclass
class MCPClient:
    """Thin JSON-RPC wrapper over the FastAPI ``TestClient``.

    Exposes ``call_tool``, ``list_tools``, ``initialize``, and ``raw``.
    Auto-initialises on first use so tests don't have to.
    """

    test_client: TestClient
    auth_token: str
    _initialized: bool = False
    _request_counter: int = 0

    def _next_id(self) -> str:
        self._request_counter += 1
        return f"req-{self._request_counter}"

    def _headers(self, *, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.auth_token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    def initialize(self) -> dict[str, Any]:
        """Run the ``initialize`` JSON-RPC handshake; idempotent."""
        if self._initialized:
            return {}
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest-mcp-client", "version": "0.1"},
            },
        }
        r = self.test_client.post("/mcp", json=body, headers=self._headers())
        r.raise_for_status()
        self._initialized = True
        return r.json()

    def list_tools(self) -> list[dict[str, Any]]:
        """Call ``tools/list`` and return the tool list."""
        self.initialize()
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        }
        r = self.test_client.post("/mcp", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()["result"]["tools"]

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call ``tools/call`` for a single tool; returns the parsed result."""
        self.initialize()
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        r = self.test_client.post("/mcp", json=body, headers=self._headers())
        r.raise_for_status()
        return r.json()

    def call_tool_structured(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call a tool and unwrap its ``structuredContent`` field.

        The SDK packs structured returns under
        ``result.structuredContent``; the unstructured ``content`` array
        carries the JSON dump as text. Tests almost always want the
        structured form, so this helper short-circuits.
        """
        envelope = self.call_tool(name, arguments)
        result = envelope.get("result")
        if result is None:
            return envelope  # error response — let caller assert
        return result.get("structuredContent") or result

    def call_tool_error(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a tool expected to fail; returns the parsed error envelope.

        Tools surface errors via the SDK's ``CallToolResult.isError=True``
        path. Our dispatcher writes the error JSON into both ``content``
        and ``structuredContent``; this helper unpacks the structured
        form so tests can assert on ``code`` / ``message`` / ``data``.
        """
        envelope = self.call_tool(name, arguments)
        if "error" in envelope:
            return envelope["error"]
        result = envelope.get("result", {})
        if result.get("isError"):
            return result.get("structuredContent") or {}
        return envelope


# ---------------------------------------------------------------------------
# Pytest fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_settings(tmp_path: Path) -> Settings:
    """Settings pointed at a tmp dir; fresh DB per test."""
    return Settings(
        host="127.0.0.1",
        port=5180,
        data_dir=tmp_path / "data",
        state_dir=tmp_path / "state",
    )


@pytest.fixture
def mcp_app(mcp_settings: Settings) -> Any:
    """Build a FastAPI app with MCP mounted."""
    return create_app(mcp_settings)


@pytest.fixture
def mcp_token(mcp_settings: Settings) -> str:
    """Read the bearer token written by ``create_app`` startup."""
    return mcp_settings.token_path.read_text(encoding="utf-8").strip()


@pytest.fixture
def mcp_client(mcp_app: Any, mcp_token: str) -> Iterator[MCPClient]:
    """JSON-RPC wrapper over a TestClient bound to the MCP app."""
    with TestClient(mcp_app, base_url="http://127.0.0.1:5180") as test_client:
        yield MCPClient(test_client=test_client, auth_token=mcp_token)


@pytest.fixture
def seeded_project(mcp_client: MCPClient) -> dict[str, Any]:
    """Create a fresh project and return its envelope.

    Used by tests that need a bound ``project_id`` without re-implementing
    the MCP-side boilerplate.
    """
    payload = mcp_client.call_tool_structured(
        "project.create",
        {
            "slug": "test-project",
            "name": "Test Project",
            "domain": "test.example",
            "locale": "en-US",
        },
    )
    return payload
