"""Local MCP host registration lifecycle."""

from .bridge import MCP_SERVER_NAME, resolve_bridge_command, token_preflight
from .result import AgentRunResult, HostMcpResult
from .service import (
    HostMcpAggregate,
    inspect_all,
    inspect_host,
    register_host,
    remove_all,
    remove_host,
    repair_all,
    run_agent,
)

__all__ = [
    "MCP_SERVER_NAME",
    "AgentRunResult",
    "HostMcpAggregate",
    "HostMcpResult",
    "inspect_all",
    "inspect_host",
    "register_host",
    "remove_all",
    "remove_host",
    "repair_all",
    "resolve_bridge_command",
    "run_agent",
    "token_preflight",
]
