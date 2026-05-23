#!/usr/bin/env bash
#
# Register `stackos` with Claude Code as an MCP server.
#
# Reads the target `.mcp.json` (default `${HOME}/.claude/mcp.json`,
# overridable via `STACKOS_MCP_TARGET` for per-project configs),
# upserts the `stackos` entry, and writes back atomically with a
# `.bak` backup of any pre-existing file. Never `>`-overwrites; it uses an
# atomic rename via a temp file in the same directory.
#
# `--remove` deletes the entry (used by `make uninstall`).

set -euo pipefail

ACTION="register"
for arg in "$@"; do
    case "${arg}" in
        --remove) ACTION="remove" ;;
        --force) ACTION="register" ;;  # always upserts; --force is a no-op alias
        *) echo "unknown flag: ${arg}" >&2; exit 2 ;;
    esac
done

HOME_DIR="${STACKOS_HOME:-${HOME}}"
TARGET="${STACKOS_MCP_TARGET:-${HOME_DIR}/.claude/mcp.json}"
TOKEN_PATH="${HOME_DIR}/.local/state/stackos/auth.token"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRIDGE_PYTHON="${STACKOS_BRIDGE_PYTHON:-${REPO_ROOT}/.venv/bin/python}"
MCP_NAME="${STACKOS_MCP_NAME:-stackos}"
if [[ ! -x "${BRIDGE_PYTHON}" ]]; then
    BRIDGE_PYTHON="$(command -v python3)"
fi

mkdir -p "$(dirname "${TARGET}")"

if [[ -f "${TARGET}" ]]; then
    cp "${TARGET}" "${TARGET}.bak"
fi

if [[ "${ACTION}" == "register" ]]; then
    if [[ ! -f "${TOKEN_PATH}" ]]; then
        echo "auth token missing at ${TOKEN_PATH} — run \`make install\` or \`stackos init\` first." >&2
        exit 1
    fi
fi

# Use Python (already a hard dep — the daemon is Python) for the JSON
# merge so we don't pull jq into the install floor. Atomic via tempfile
# + os.replace, which is what `rename(2)` guarantees on POSIX.
python3 - "${TARGET}" "${BRIDGE_PYTHON}" "${ACTION}" "${MCP_NAME}" <<'PYEOF'
import json
import os
import sys
import tempfile

target, bridge_python, action, server_name = sys.argv[1:5]

existing: dict[str, object] = {}
if os.path.exists(target):
    with open(target, encoding="utf-8") as f:
        text = f.read().strip()
        if text:
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError as exc:
                print(f"existing {target} is not valid JSON: {exc}", file=sys.stderr)
                sys.exit(1)
            if not isinstance(loaded, dict):
                print(f"existing {target} is not a JSON object", file=sys.stderr)
                sys.exit(1)
            existing = loaded

servers = existing.setdefault("mcpServers", {})
if not isinstance(servers, dict):
    print(f"`mcpServers` in {target} must be an object", file=sys.stderr)
    sys.exit(1)

if action == "remove":
    if server_name in servers:
        del servers[server_name]
        msg = f"Unregistered MCP '{server_name}' from {target}"
    else:
        msg = f"MCP '{server_name}' not present in {target}; nothing to remove"
else:
    servers[server_name] = {
        "transport": "stdio",
        "command": bridge_python,
        "args": ["-m", "stackos", "mcp-bridge"],
    }
    msg = f"Registered MCP '{server_name}' with Claude Code -> {target}"

target_dir = os.path.dirname(os.path.abspath(target)) or "."
fd, tmp = tempfile.mkstemp(prefix=".mcp.", dir=target_dir)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, target)
except Exception:
    if os.path.exists(tmp):
        os.unlink(tmp)
    raise

print(msg)
PYEOF
