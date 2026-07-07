#!/usr/bin/env bash
#
# Build a standalone, platform-local Python payload for the Electron app.
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
PYTHON_VERSION_MAJOR_MINOR=""
INSTALL_PLAYWRIGHT="${STACKOS_DESKTOP_INSTALL_PLAYWRIGHT:-1}"

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
find "${PAYLOAD_DIR}/.venv" -path "*/stackos-*.dist-info/direct_url.json" -delete

thin_payload_to_arm64() {
  if ! command -v file >/dev/null 2>&1 || ! command -v lipo >/dev/null 2>&1; then
    return
  fi
  while IFS= read -r -d "" candidate; do
    if ! file "${candidate}" | grep -q "Mach-O"; then
      continue
    fi
    lipo_info="$(lipo -info "${candidate}" 2>/dev/null || true)"
    if [[ "${lipo_info}" != *"x86_64"* ]]; then
      continue
    fi
    if [[ "${lipo_info}" != *"arm64"* ]]; then
      echo "cannot build arm64 package with x86_64-only binary: ${candidate}" >&2
      exit 1
    fi
    tmp_path="${candidate}.arm64"
    lipo "${candidate}" -remove x86_64 -output "${tmp_path}"
    mv "${tmp_path}" "${candidate}"
  done < <(find "${PAYLOAD_DIR}" -type f -print0)
}

normalize_payload_metadata_paths() {
  rm -rf "${PAYLOAD_DIR}/ms-playwright/.links"
  find "${PAYLOAD_DIR}/.venv/lib" \
    \( -name "*Config.sh" -o -path "*/config-*/Makefile" \) \
    -type f -delete
  PAYLOAD_DIR_ENV="${PAYLOAD_DIR}" \
  REPO_ROOT_ENV="${REPO_ROOT}" \
  BUILD_DIR_ENV="${BUILD_DIR}" \
  HOME_ENV="${HOME}" \
    "${PYTHON_LINK}" - <<'PY'
import os
import re
from pathlib import Path

root = Path(os.environ["PAYLOAD_DIR_ENV"])
markers = (
    os.environ["REPO_ROOT_ENV"],
    os.environ["BUILD_DIR_ENV"],
    "desktop/payload/stackos",
    "~/.local/share/uv/python",
    f"{os.environ['HOME_ENV']}/.local/share/uv",
    "/private/var/folders",
)

for candidate in root.rglob("*"):
    if not candidate.is_file():
        continue
    try:
        text = candidate.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    if not any(marker in text for marker in markers):
        continue
    text = text.replace(os.environ["REPO_ROOT_ENV"], "__STACKOS_REPO__")
    text = text.replace(os.environ["BUILD_DIR_ENV"], "__STACKOS_PAYLOAD_BUILD__")
    text = text.replace("desktop/payload/stackos", "__STACKOS_PAYLOAD__")
    text = text.replace(f"{os.environ['HOME_ENV']}/.local/share/uv", "__STACKOS_UV_CACHE__")
    text = re.sub(r"~/.local/share/uv/python/[^\s\"',)]*", "__STACKOS_PACKAGED_PYTHON__", text)
    text = re.sub(r"/private/var/folders/[^\s\"',)]*", "__STACKOS_BUILD_TMP__", text)
    candidate.write_text(text, encoding="utf-8")
PY
}

PYTHON_LINK="${PAYLOAD_DIR}/.venv/bin/python"
PYTHON_VERSION_MAJOR_MINOR="$("${PYTHON_LINK}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PYTHON_BASE="$("${PYTHON_LINK}" -c 'import os, sys; print(os.path.realpath(sys.base_prefix))')"
PYTHON_LIB_ROOT="${PYTHON_BASE}/lib"
PYTHON_STDLIB_ROOT="${PYTHON_LIB_ROOT}/python${PYTHON_VERSION_MAJOR_MINOR}"
if [[ ! -d "${PYTHON_STDLIB_ROOT}/encodings" ]]; then
  echo "base Python stdlib is missing encodings: ${PYTHON_STDLIB_ROOT}" >&2
  exit 1
fi
if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync is required to build the standalone desktop Python payload" >&2
  exit 1
fi
mkdir -p "${PAYLOAD_DIR}/.venv/lib"
rsync -a \
  --exclude "python${PYTHON_VERSION_MAJOR_MINOR}/site-packages/***" \
  --exclude "__pycache__/" \
  --exclude "*.pyc" \
  "${PYTHON_LIB_ROOT}/" \
  "${PAYLOAD_DIR}/.venv/lib/"

if [[ -L "${PYTHON_LINK}" ]]; then
  PYTHON_TARGET="$(readlink "${PYTHON_LINK}")"
  if [[ "${PYTHON_TARGET}" == "~/"* ]]; then
    PYTHON_TARGET="${HOME}/${PYTHON_TARGET#~/}"
  elif [[ "${PYTHON_TARGET}" != /* ]]; then
    PYTHON_TARGET="$(cd "$(dirname "${PYTHON_LINK}")/$(dirname "${PYTHON_TARGET}")" && pwd)/$(basename "${PYTHON_TARGET}")"
  fi
  if [[ ! -x "${PYTHON_TARGET}" ]]; then
    echo "venv python target is not executable: ${PYTHON_TARGET}" >&2
    exit 1
  fi
  PYTHON_ROOT="$(cd "$(dirname "${PYTHON_TARGET}")/.." && pwd -P)"
  PYTHON_DYLIB="$(find -L "${PYTHON_ROOT}/lib" -maxdepth 1 -name 'libpython*.dylib' -print -quit)"
  if [[ -z "${PYTHON_DYLIB}" || ! -f "${PYTHON_DYLIB}" ]]; then
    echo "venv python dylib is missing under ${PYTHON_ROOT}/lib" >&2
    exit 1
  fi
  cp "${PYTHON_DYLIB}" "${PAYLOAD_DIR}/.venv/lib/"
  rm "${PYTHON_LINK}"
  cp "${PYTHON_TARGET}" "${PYTHON_LINK}"
  chmod 755 "${PYTHON_LINK}"
fi

PYTHON_DYLIB_COUNT="$(find "${PAYLOAD_DIR}/.venv/lib" -maxdepth 1 -name 'libpython*.dylib' | wc -l | tr -d ' ')"
if [[ "${PYTHON_DYLIB_COUNT}" != "1" ]]; then
  echo "expected exactly one bundled libpython dylib, found ${PYTHON_DYLIB_COUNT}" >&2
  find "${PAYLOAD_DIR}/.venv/lib" -maxdepth 1 -name 'libpython*.dylib' -print >&2
  exit 1
fi
BUNDLED_PYTHON_DYLIB="$(find "${PAYLOAD_DIR}/.venv/lib" -maxdepth 1 -name 'libpython*.dylib' -print -quit)"
if command -v install_name_tool >/dev/null 2>&1; then
  install_name_tool -id "@rpath/$(basename "${BUNDLED_PYTHON_DYLIB}")" "${BUNDLED_PYTHON_DYLIB}"
fi

cat > "${PAYLOAD_DIR}/.venv/pyvenv.cfg" <<CFG
home = .
implementation = CPython
uv = standalone
version_info = $("${PYTHON_LINK}" -c 'import sys; print(".".join(str(part) for part in sys.version_info[:3]))')
include-system-site-packages = false
CFG

if [[ "${INSTALL_PLAYWRIGHT}" == "1" ]]; then
  PLAYWRIGHT_BROWSERS_PATH="${PAYLOAD_DIR}/ms-playwright" \
  PYTHONHOME="${PAYLOAD_DIR}/.venv" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
    "${PAYLOAD_DIR}/.venv/bin/python" -B -m playwright install chromium
fi

normalize_payload_metadata_paths
thin_payload_to_arm64

codesign --remove-signature "${PYTHON_LINK}" 2>/dev/null || true
codesign --remove-signature "${BUNDLED_PYTHON_DYLIB}" 2>/dev/null || true
codesign --force --sign - "${BUNDLED_PYTHON_DYLIB}"
codesign --force --sign - "${PYTHON_LINK}"

PYTHONHOME="${PAYLOAD_DIR}/.venv" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  "${PYTHON_LINK}" -B -c 'import encodings, importlib.metadata, sqlite3, ssl, sys; assert sys.prefix == sys.exec_prefix'

PACKAGE_VERSION="$(PYTHONHOME="${PAYLOAD_DIR}/.venv" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 "${PYTHON_LINK}" -B -c 'from importlib.metadata import version; print(version("stackos"))')"
find "${PAYLOAD_DIR}/.venv/bin" -mindepth 1 \
  ! -name "python" \
  ! -name "python3" \
  ! -name "python${PYTHON_VERSION_MAJOR_MINOR}" \
  -exec rm -f {} +
BUILD_ID="${STACKOS_DESKTOP_BUILD_ID:-$(date -u '+%Y%m%dT%H%M%SZ')}"
BUILT_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
cat > "${PAYLOAD_DIR}/build-info.json" <<INFO
{
  "name": "stackos",
  "version": "${PACKAGE_VERSION}",
  "buildId": "${BUILD_ID}",
  "builtAt": "${BUILT_AT}"
}
INFO

cat > "${PAYLOAD_DIR}/bin/stackos" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export STACKOS_PACKAGED_CLI="${ROOT_DIR}/bin/stackos"
export PYTHONHOME="${ROOT_DIR}/.venv"
export PLAYWRIGHT_BROWSERS_PATH="${ROOT_DIR}/ms-playwright"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1
unset PYTHONPATH
unset VIRTUAL_ENV
unset __PYVENV_LAUNCHER__
exec "${ROOT_DIR}/.venv/bin/python" -B -m stackos "$@"
WRAPPER
chmod +x "${PAYLOAD_DIR}/bin/stackos"

find "${PAYLOAD_DIR}/.venv" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "${PAYLOAD_DIR}/.venv" -name "*.pyc" -delete
if find "${PAYLOAD_DIR}/.venv" -name "direct_url.json" -print -quit | grep -q .; then
  echo "payload must not contain wheel direct_url.json build-path metadata" >&2
  exit 1
fi
if grep -R -I -F \
  -e "${REPO_ROOT}" \
  -e "${BUILD_DIR}" \
  -e "desktop/payload/stackos" \
  -e "~/.local/share/uv/python" \
  -e "${HOME}/.local/share/uv" \
  -e "/private/var/folders" \
  "${PAYLOAD_DIR}" >/dev/null; then
  echo "payload contains build-machine paths" >&2
  exit 1
fi

printf 'stackos desktop payload %s (%s) built at %s\n' "${PACKAGE_VERSION}" "${BUILD_ID}" "${PAYLOAD_DIR}"
