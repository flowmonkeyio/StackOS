#!/usr/bin/env bash
#
# Clone-mode wrapper for the CLI-owned launchd autostart installer.

set -euo pipefail

ACTION="install"
FORCE=0
for arg in "$@"; do
    case "${arg}" in
        --force) FORCE=1 ;;
        --uninstall) ACTION="uninstall" ;;
        *) echo "unknown flag: ${arg}" >&2; exit 2 ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_ARGS=(-m stackos autostart "${ACTION}")
if [[ "${ACTION}" == "install" && "${FORCE}" -eq 1 ]]; then
    CLI_ARGS+=(--force)
fi

if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    exec "${REPO_ROOT}/.venv/bin/python" "${CLI_ARGS[@]}"
fi

exec "${UV:-uv}" run --directory "${REPO_ROOT}" python "${CLI_ARGS[@]}"
