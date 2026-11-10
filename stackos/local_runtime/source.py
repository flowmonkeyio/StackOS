"""Read current local source/tool identity for the native Codex caller.

This does no preparation, launch, registration or server mutation. The local
runner must call it immediately before invocation with its locally selected MCP
reference and the controller's optional Git candidate evidence. Authorization
still belongs to the connection/worker; this result is an observation, not a grant.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from stackos.host_mcp.adapters import codex
from stackos.host_mcp.bridge import is_stackos_bridge_command
from stackos.host_mcp.discovery import subprocess_env_for_cli
from stackos.host_mcp.result import AgentSandbox
from stackos.host_mcp.service import agent_selection_error
from stackos.host_mcp.toml_config import read_mcp_config_ref
from stackos.local_runtime.connection import LocalBinding, LocalConnectionStore
from stackos.repositories.base import ValidationError
from stackos.task_delivery import _baseline, _candidate_commit, _git


def check_codex_source(
    store: LocalConnectionStore,
    binding: LocalBinding,
    *,
    candidate: dict[str, str] | None = None,
    mcp_config_ref: dict[str, str] | None = None,
    sandbox: AgentSandbox = "workspace-write",
    model: str | None = None,
    provider: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Reuse canonical folder, candidate, native selection and task-route checks.

    No cached readiness survives a call. Non-Git/unborn folders retain the
    existing optional-candidate contract; supplied evidence must identify clean
    current HEAD and its ancestor. Local paths/tool details stay on this computer.
    """
    root = store.require_folder(binding, Path(binding.root))
    error = agent_selection_error(
        "codex", sandbox=sandbox, model=model, provider=provider, profile=profile
    )
    if error:
        raise ValueError(error)
    if candidate is not None and not isinstance(candidate, dict):
        raise ValueError("invalid local Git candidate evidence")
    try:
        observed = _baseline(str(root))
        common_dir = None
        if observed or any((p / ".git").exists() for p in (root, *root.parents)):
            if Path(_git(str(root), "rev-parse", "--show-toplevel")).resolve() != root:
                raise ValueError("bound folder differs from the Git repository root")
            common_dir = _git(str(root), "rev-parse", "--path-format=absolute", "--git-common-dir")
        if candidate:
            if not set(candidate) <= {"base_commit_sha", "commit_sha"} or any(
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{7,64}", value)
                for value in candidate.values()
            ):
                raise ValueError("invalid local Git candidate evidence")
            sha = _candidate_commit(
                str(root),
                candidate.get("commit_sha") or candidate.get("base_commit_sha"),
                candidate.get("base_commit_sha"),
            )
            observed = {
                key: _git(str(root), "rev-parse", "--verify", f"{value}^{{commit}}")
                for key, value in candidate.items()
            }
            if "commit_sha" in candidate:
                observed["commit_sha"] = sha
    except ValidationError as error:
        raise ValueError(str(error)) from None

    binary = codex._select_codex_bin()
    if binary is None:
        raise ValueError("No usable Codex CLI was found by host discovery.")
    reference, overrides = (
        codex.task_mcp_overrides(root, mcp_config_ref, codex_bin=binary)
        if mcp_config_ref is not None
        else (None, [])
    )
    result = codex._run_codex(binary, [*overrides, "mcp", "list", "--json"], cwd=root)
    try:
        rows = json.loads(result.stdout)
        if result.returncode or not isinstance(rows, list):
            raise ValueError
        servers = {row["name"]: row for row in rows}
        if len(servers) != len(rows):
            raise ValueError
        if reference is not None and not read_mcp_config_ref(reference)[1].keys() <= servers.keys():
            raise ValueError
        bridge = _local_tool(servers["stackos"], root, binary)
        command = [bridge["command"], *bridge.get("args", [])]
        if not is_stackos_bridge_command(command, runtime="codex"):
            raise ValueError
        # Duplicate selectors are ambiguous; never guess which root a CLI uses.
        if command.count("--workspace-root") != 1:
            raise ValueError
        selected_root = command[command.index("--workspace-root") + 1]
        if (
            not Path(selected_root).is_absolute()
            or Path(selected_root).resolve(strict=True) != root
        ):
            raise ValueError
        if "serena" in servers:
            serena = _local_tool(servers["serena"], root, binary)
            selected_root = serena.get("env", {}).get("SERENA_PROJECT", "")
            if (
                not Path(selected_root).is_absolute()
                or Path(selected_root).resolve(strict=True) != root
            ):
                raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OSError, RuntimeError):
        # Native configuration may include local credentials; do not echo it.
        raise ValueError(
            "Native MCP tool/root identity is missing or differs from the bound folder"
        ) from None

    # Probe subprocesses can take time. Recheck local identities after discovery.
    store.require_folder(binding, root)
    try:
        if _baseline(str(root)) != (
            {"base_commit_sha": observed.get("commit_sha") or observed["base_commit_sha"]}
            if observed
            else {}
        ):
            raise ValueError("local source changed during native discovery")
        if candidate:
            _candidate_commit(str(root), sha, candidate.get("base_commit_sha"))
        if (
            common_dir is not None
            and _git(str(root), "rev-parse", "--path-format=absolute", "--git-common-dir")
            != common_dir
        ):
            raise ValueError("local repository changed during native discovery")
    except ValidationError as error:
        raise ValueError(str(error)) from None
    if reference is not None:
        # Check the same frozen bytes again, without inventing a second route owner.
        read_mcp_config_ref(reference)
    return {
        "project_dir": str(root),
        "candidate": observed,
        "git_common_dir": common_dir,
        "native_executable": binary,
        "mcp_servers": sorted(servers),
        **({"mcp_config_ref": reference} if reference is not None else {}),
    }


def _local_tool(row: dict[str, Any], root: Path, binary: str) -> dict[str, Any]:
    tool = row["transport"]
    if row.get("enabled") is False or tool["type"] != "stdio":
        raise ValueError
    command = tool["command"]
    if not isinstance(command, str) or not shutil.which(
        command, path=subprocess_env_for_cli(binary).get("PATH")
    ):
        raise ValueError
    if Path(tool.get("cwd") or str(root)).resolve(strict=True) != root:
        raise ValueError
    return tool
