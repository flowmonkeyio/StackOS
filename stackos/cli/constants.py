"""Shared constants for the StackOS CLI adapter."""

from __future__ import annotations

_LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})
_DESKTOP_BUNDLE_IDENTIFIER = "io.stackos.desktop"
_LAUNCHD_LABEL = "com.stackos.daemon"
_MCP_SERVER_NAME = "stackos"

__all__ = [
    "_DESKTOP_BUNDLE_IDENTIFIER",
    "_LAUNCHD_LABEL",
    "_LOOPBACK_HOSTS",
    "_MCP_SERVER_NAME",
]
