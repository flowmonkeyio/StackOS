#!/usr/bin/env bash
#
# StackOS plugin installer.
#
# Delegates to the Python installer primitives so clone-mode scripts and
# package-mode `stackos install` share one plugin/cache hydration path.

set -euo pipefail

ACTION="install"
for arg in "$@"; do
    case "${arg}" in
        --remove) ACTION="remove" ;;
        *) echo "unknown flag: ${arg}" >&2; exit 2 ;;
    esac
done

HOME_DIR="${STACKOS_HOME:-${HOME}}"
TARGET="${HOME_DIR}/.codex/plugins/stackos"
MARKETPLACE="${HOME_DIR}/.agents/plugins/marketplace.json"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_PYTHON="${STACKOS_PLUGIN_PYTHON:-${REPO_ROOT}/.venv/bin/python}"
if [[ ! -x "${PLUGIN_PYTHON}" ]]; then
    PLUGIN_PYTHON="$(command -v python3)"
fi

PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" "${PLUGIN_PYTHON}" - \
    "${HOME_DIR}" "${ACTION}" "${TARGET}" "${MARKETPLACE}" <<'PYEOF'
import sys
from pathlib import Path

from stackos import install as installer

home = Path(sys.argv[1])
action = sys.argv[2]
target = Path(sys.argv[3])
marketplace = Path(sys.argv[4])

if action == "install":
    installed_target, count = installer.copy_plugins(home=home)
    installer.register_plugin_marketplace(home=home)
    print(f"Installed {count} plugins to {installed_target}")
    print(f"Registered StackOS plugin marketplace at {marketplace}")
else:
    target, cache_root = installer.remove_plugins(home=home)
    installer.register_plugin_marketplace(home=home, remove=True)
    print(f"Removed StackOS plugin from {target}")
    print(f"Removed StackOS plugin cache from {cache_root}")
    print(f"Unregistered StackOS plugin marketplace entry at {marketplace}")
PYEOF
