"""Compatibility surface for the StackOS Typer CLI package."""

from __future__ import annotations

# ruff: noqa: F401
import shutil
import subprocess

import typer

from stackos.config import Settings

from .api_client import (
    _api_request,
    _echo_json,
    _load_operation_arguments,
    _merge_common_arguments,
    _read_daemon_token,
    _split_csv,
)
from .app import (
    _exit,
    actions_app,
    agent_requests_app,
    app,
    autostart_app,
    ops_app,
    run_plans_app,
    tracker_app,
)
from .constants import _LAUNCHD_LABEL, _LOOPBACK_HOSTS, _MCP_SERVER_NAME
from .daemon_commands import (
    _autostart_bridge_daemon,
    _command_looks_like_daemon,
    _daemon_args,
    _daemon_health_ok,
    _discover_daemon_processes,
    _git_output,
    _install_launchd_autostart,
    _is_loopback_host,
    _launchctl,
    _launchd_bootout,
    _launchd_bootstrap,
    _launchd_domain,
    _launchd_loaded,
    _launchd_plist_content,
    _launchd_plist_path,
    _launchd_service,
    _listener_pids,
    _mcp_bridge_workspace_hints,
    _pid_command,
    _pid_is_running,
    _read_pid_file,
    _remove_pid_file,
    _spawn_detached_daemon,
    _tcp_can_connect,
    _terminate_daemon_processes,
    _uninstall_launchd_autostart,
    _wait_for_daemon,
    _wait_for_pids_to_exit,
    _write_pid_file,
    autostart_install,
    autostart_status,
    autostart_uninstall,
    mcp_bridge,
    restart,
    serve,
    start,
    stop,
)
from .doctor_commands import (
    _check_alembic_at_head,
    _check_claude_mcp_registered,
    _check_codex_mcp_registered,
    _check_credentials_decrypt,
    _check_installed_assets,
    _check_launchd_plist,
    _check_scheduler_jobs,
    _codex_mcp_line_is_bridge,
    _count_expected_plugins,
    _count_traversable_named,
    _doctor_home,
    _expected_asset_count,
    _file_mode_or_none,
    _installed_asset_count,
    _installed_plugin_count,
    _plugin_marketplace_has_stackos,
    doctor,
    mcp_host_status,
)
from .local_commands import (
    backup,
    init,
    install,
    migrate,
    restore,
    rotate_seed,
    rotate_token,
    uninstall,
)
from .operation_commands import _operation_call

# Import command modules for their Typer decorators. The imported symbols above
# keep legacy private tests and smoke scripts working during the package split.

__all__ = ["Settings", "app"]
