#!/usr/bin/env bash
#
# Register `stackos` with the Codex CLI as an MCP server.
#
# Idempotent: if Codex already lists a `stackos` server we treat the
# script as a no-op. The registered server is the local stdio bridge; the
# bearer token stays inside the bridge process.
#
# `--remove` unregisters the server (used by `make uninstall`).
# `--force` re-registers even if already present (used after rotation).

set -euo pipefail

ACTION="register"
for arg in "$@"; do
    case "${arg}" in
        --remove) ACTION="remove" ;;
        --force) ACTION="force" ;;
        *) echo "unknown flag: ${arg}" >&2; exit 2 ;;
    esac
done

if ! command -v codex >/dev/null 2>&1; then
    echo "Codex CLI not on PATH — skipping MCP registration."
    echo "  Install Codex CLI then re-run \`bash scripts/register-mcp-codex.sh\`."
    exit 0
fi

HOME_DIR="${STACKOS_HOME:-${HOME}}"
TOKEN_PATH="${HOME_DIR}/.local/state/stackos/auth.token"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRIDGE_PYTHON="${STACKOS_BRIDGE_PYTHON:-${REPO_ROOT}/.venv/bin/python}"
MCP_NAME="${STACKOS_MCP_NAME:-stackos}"
if [[ ! -x "${BRIDGE_PYTHON}" ]]; then
    BRIDGE_PYTHON="$(command -v python3)"
fi

already_registered() {
    codex mcp list 2>/dev/null | grep -q "^${1}[[:space:]]"
}

if [[ "${ACTION}" == "remove" ]]; then
    if already_registered "${MCP_NAME}"; then
        codex mcp remove "${MCP_NAME}"
        echo "Unregistered MCP '${MCP_NAME}' from Codex CLI"
    else
        echo "MCP '${MCP_NAME}' not registered with Codex CLI; nothing to remove"
    fi
    exit 0
fi

if [[ "${ACTION}" == "register" ]] && already_registered "${MCP_NAME}"; then
    echo "MCP '${MCP_NAME}' already registered with Codex CLI"
    exit 0
fi

if [[ ! -f "${TOKEN_PATH}" ]]; then
    echo "auth token missing at ${TOKEN_PATH} — run \`make install\` or \`stackos init\` first." >&2
    exit 1
fi
# `--force` removes-then-adds so the registration refreshes after rotation.
if [[ "${ACTION}" == "force" ]] && already_registered "${MCP_NAME}"; then
    codex mcp remove "${MCP_NAME}"
fi

codex mcp add "${MCP_NAME}" \
    -- "${BRIDGE_PYTHON}" -m stackos mcp-bridge

echo "Registered MCP '${MCP_NAME}' with Codex CLI via mcp-bridge"
