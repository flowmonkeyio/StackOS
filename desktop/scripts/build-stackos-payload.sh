#!/usr/bin/env bash
#
# Build a platform-local Python payload for the Electron app.
#
# The generated payload is intentionally ignored by git. electron-builder copies
# desktop/payload/stackos into the app resources directory, where the desktop
# shell resolves resources/stackos/bin/stackos first.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DESKTOP_DIR}/.." && pwd)"
BUILD_DIR="${DESKTOP_DIR}/payload-build"
PAYLOAD_DIR="${DESKTOP_DIR}/payload/stackos"
UV_BIN="${UV:-uv}"
PYTHON_VERSION="${STACKOS_DESKTOP_PYTHON:-3.12}"

rm -rf "${BUILD_DIR}" "${PAYLOAD_DIR}"
mkdir -p "${BUILD_DIR}/dist" "${PAYLOAD_DIR}/bin"

"${UV_BIN}" build --wheel --out-dir "${BUILD_DIR}/dist" "${REPO_ROOT}"

wheel_count="$(find "${BUILD_DIR}/dist" -name 'stackos-*.whl' | wc -l | tr -d ' ')"
if [[ "${wheel_count}" != "1" ]]; then
  echo "expected exactly one stackos wheel, found ${wheel_count}" >&2
  find "${BUILD_DIR}/dist" -maxdepth 1 -type f -print >&2
  exit 1
fi

WHEEL_PATH="$(find "${BUILD_DIR}/dist" -name 'stackos-*.whl' -print -quit)"

"${UV_BIN}" venv "${PAYLOAD_DIR}/.venv" --python "${PYTHON_VERSION}"
"${UV_BIN}" pip install --python "${PAYLOAD_DIR}/.venv/bin/python" "${WHEEL_PATH}"

cat > "${PAYLOAD_DIR}/bin/stackos" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ROOT_DIR}/.venv/bin/python" -m stackos "$@"
WRAPPER
chmod +x "${PAYLOAD_DIR}/bin/stackos"

if [[ "${STACKOS_DESKTOP_INSTALL_PLAYWRIGHT:-0}" == "1" ]]; then
  "${PAYLOAD_DIR}/.venv/bin/python" -m playwright install chromium
fi

printf 'stackos desktop payload built at %s\n' "${PAYLOAD_DIR}"
