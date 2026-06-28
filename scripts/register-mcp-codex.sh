#!/usr/bin/env bash
#
# Register or remove StackOS in Codex's MCP registry.
#
# This is intentionally a thin wrapper around the shared Python host lifecycle
# service used by install, repair, doctor, desktop repair, and uninstall.

set -euo pipefail

ACTION="register"
FORCE=""
for arg in "$@"; do
    case "${arg}" in
        --remove) ACTION="remove" ;;
        --force) FORCE="--force" ;;
        *) echo "unknown flag: ${arg}" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="${BASH_SOURCE[0]%/*}"
if [[ "${SCRIPT_DIR}" == "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="."
fi
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${STACKOS_INSTALL_PYTHON:-${REPO_ROOT}/.venv/bin/python}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    PYTHON_BIN="$(command -v python3)"
fi
exec "${PYTHON_BIN}" -m stackos.host_mcp codex "${ACTION}" ${FORCE}
