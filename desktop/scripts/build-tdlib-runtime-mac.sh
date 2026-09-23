#!/usr/bin/env bash
#
# Stage the exact TDLib JSON ABI that the macOS StackOS payload owns. The
# resulting runtime is a payload/data-dir asset, never a Python-wheel asset and
# never a library discovered from Homebrew, DYLD paths, or the system.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PAYLOAD_DIR="${STACKOS_DESKTOP_PAYLOAD_DIR:-${DESKTOP_DIR}/payload/stackos}"
RUNTIME_ROOT="${TDLIB_RUNTIME_ROOT:-${PAYLOAD_DIR}/telegram-tdlib-runtime}"
BUILD_PARENT="${TDLIB_BUILD_PARENT:-${DESKTOP_DIR}/payload-build/telegram-tdlib}"
TDLIB_LIBRARY_PATH="${TDLIB_LIBRARY_PATH:-}"
CMAKE_BIN="${CMAKE:-cmake}"
PYTHON_BIN="${TDLIB_PYTHON:-python3}"
OPENSSL_ROOT="${TDLIB_OPENSSL_ROOT:-}"
TDLIB_SOURCE_URL="https://github.com/tdlib/td.git"
TDLIB_SOURCE_COMMIT="d1085f9cebc5a62379991ae1652673954f229c1f"
TDLIB_SOURCE_ARCHIVE_SHA256="5f5ebdb7f658b1a71ba6cb4f823017ad6649352427ce2a274cebc4b0dd2216e9"
TDLIB_VERSION="1.8.67"
TDLIB_EXPECTED_LIBRARY_SHA256="c397db26e7596eaf4a37ffb6d9400f2a34d764a26f388e4f4b9bfd41b070d5af"
TDLIB_BUILD_MANIFEST_VERSION="1"

if [[ -z "${RUNTIME_ROOT}" || "${RUNTIME_ROOT}" == "/" ]]; then
  echo "TDLIB_RUNTIME_ROOT must name one managed runtime directory." >&2
  exit 1
fi
if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Set TDLIB_PYTHON to the Python used for the native smoke." >&2
  exit 1
fi
for command in lipo otool shasum; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "${command} is required to stage the managed TDLib runtime." >&2
    exit 1
  fi
done

STAGE_ROOT="${RUNTIME_ROOT}.staging.$$"
cleanup() {
  rm -rf "${STAGE_ROOT}"
  if [[ -n "${WORK_ROOT:-}" ]]; then
    rm -rf "${WORK_ROOT}"
  fi
}
trap cleanup EXIT
rm -rf "${STAGE_ROOT}"
mkdir -p "${STAGE_ROOT}/lib"

if [[ -n "${TDLIB_LIBRARY_PATH}" ]]; then
  if [[ ! -f "${TDLIB_LIBRARY_PATH}" || -L "${TDLIB_LIBRARY_PATH}" ]]; then
    echo "TDLIB_LIBRARY_PATH must identify one regular, explicitly supplied library." >&2
    exit 1
  fi
  TDJSON_BUILT_PATH="${TDLIB_LIBRARY_PATH}"
  BUILD_MODE="verified-prebuilt"
else
  if ! command -v "${CMAKE_BIN}" >/dev/null 2>&1; then
    echo "CMake is required; set CMAKE to an explicit CMake executable." >&2
    exit 1
  fi
  if ! command -v git >/dev/null 2>&1; then
    echo "git is required to build the pinned TDLib source; alternatively set TDLIB_LIBRARY_PATH." >&2
    exit 1
  fi
  if [[ -z "${OPENSSL_ROOT}" || ! -f "${OPENSSL_ROOT}/lib/libcrypto.a" || ! -f "${OPENSSL_ROOT}/lib/libssl.a" ]]; then
    echo "Set TDLIB_OPENSSL_ROOT to an OpenSSL root with static libcrypto.a and libssl.a, or set TDLIB_LIBRARY_PATH to a verified proof artifact." >&2
    exit 1
  fi
  mkdir -p "${BUILD_PARENT}"
  WORK_ROOT="$(mktemp -d "${BUILD_PARENT}/build.XXXXXX")"
  SOURCE_DIR="${WORK_ROOT}/source"
  CMAKE_BUILD_DIR="${WORK_ROOT}/cmake-build"
  git clone --filter=blob:none --no-checkout "${TDLIB_SOURCE_URL}" "${SOURCE_DIR}"
  git -C "${SOURCE_DIR}" checkout --detach "${TDLIB_SOURCE_COMMIT}"
  if [[ "$(git -C "${SOURCE_DIR}" rev-parse HEAD)" != "${TDLIB_SOURCE_COMMIT}" ]]; then
    echo "TDLib checkout did not resolve the pinned source commit." >&2
    exit 1
  fi
  SOURCE_ARCHIVE_SHA256="$(git -C "${SOURCE_DIR}" archive --format=tar "${TDLIB_SOURCE_COMMIT}" | shasum -a 256 | awk '{print $1}')"
  if [[ "${SOURCE_ARCHIVE_SHA256}" != "${TDLIB_SOURCE_ARCHIVE_SHA256}" ]]; then
    echo "TDLib source archive did not match the pinned source digest." >&2
    exit 1
  fi
  "${CMAKE_BIN}" \
    -S "${SOURCE_DIR}" \
    -B "${CMAKE_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DOPENSSL_ROOT_DIR="${OPENSSL_ROOT}" \
    -DOPENSSL_USE_STATIC_LIBS=TRUE \
    -DTD_ENABLE_JNI=OFF
  "${CMAKE_BIN}" --build "${CMAKE_BUILD_DIR}" --target tdjson --parallel 4
  TDJSON_BUILT_PATH="$(find "${CMAKE_BUILD_DIR}" -maxdepth 3 -type f -name 'libtdjson.*.dylib' -print -quit)"
  if [[ -z "${TDJSON_BUILT_PATH}" ]]; then
    echo "TDLib tdjson build did not produce a shared library." >&2
    exit 1
  fi
  BUILD_MODE="pinned-source"
fi

if [[ "$(lipo -archs "${TDJSON_BUILT_PATH}")" != "arm64" ]]; then
  echo "TDLib library must be arm64-only." >&2
  exit 1
fi
while IFS= read -r dependency; do
  dependency="${dependency#"${dependency%%[![:space:]]*}"}"
  dependency="${dependency%% (*}"
  if [[ "${dependency}" == "@rpath/libtdjson.${TDLIB_VERSION}.dylib" ]]; then
    continue
  fi
  if [[ "${dependency}" != /usr/lib/* && "${dependency}" != /System/Library/* ]]; then
    echo "TDLib library has a non-system runtime dependency: ${dependency}" >&2
    exit 1
  fi
done < <(otool -L "${TDJSON_BUILT_PATH}" | tail -n +2)

cp "${TDJSON_BUILT_PATH}" "${STAGE_ROOT}/lib/libtdjson.dylib"
TDJSON_SHA256="$(shasum -a 256 "${STAGE_ROOT}/lib/libtdjson.dylib" | awk '{print $1}')"
if [[ "${TDJSON_SHA256}" != "${TDLIB_EXPECTED_LIBRARY_SHA256}" ]]; then
  echo "TDLib library did not match the pinned proof artifact digest." >&2
  exit 1
fi
cat > "${STAGE_ROOT}/manifest.json" <<JSON
{
  "runtime": "telegram-tdlib",
  "source": {
    "repository": "${TDLIB_SOURCE_URL}",
    "commit": "${TDLIB_SOURCE_COMMIT}",
    "archive_sha256": "${TDLIB_SOURCE_ARCHIVE_SHA256}"
  },
  "tdlib_version": "${TDLIB_VERSION}",
  "platform": {
    "os": "darwin",
    "arch": "arm64"
  },
  "build": {
    "manifest_version": ${TDLIB_BUILD_MANIFEST_VERSION},
    "mode": "${BUILD_MODE}",
    "dependency_policy": "system-only",
    "source_archive_sha256": "${TDLIB_SOURCE_ARCHIVE_SHA256}"
  },
  "library": {
    "path": "lib/libtdjson.dylib",
    "sha256": "${TDJSON_SHA256}"
  }
}
JSON

"${PYTHON_BIN}" - "${STAGE_ROOT}/lib/libtdjson.dylib" "${TDLIB_VERSION}" <<'PY'
import ctypes
import json
import sys
import time

library_path, expected_version = sys.argv[1:]
library = ctypes.CDLL(library_path, mode=ctypes.RTLD_LOCAL)
create = library.td_json_client_create
create.argtypes = []
create.restype = ctypes.c_void_p
send = library.td_json_client_send
send.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
send.restype = None
receive = library.td_json_client_receive
receive.argtypes = [ctypes.c_void_p, ctypes.c_double]
receive.restype = ctypes.c_void_p
execute = library.td_json_client_execute
execute.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
execute.restype = ctypes.c_void_p
destroy = library.td_json_client_destroy
destroy.argtypes = [ctypes.c_void_p]
destroy.restype = None

def execute_request(payload):
    pointer = execute(None, json.dumps(payload, separators=(",", ":")).encode())
    if not pointer:
        raise SystemExit("TDLib service request returned no response")
    return json.loads(ctypes.string_at(pointer).decode())

version = execute_request({"@type": "getOption", "name": "version"})
if version != {"@type": "optionValueString", "value": expected_version}:
    raise SystemExit("TDLib version did not match the pinned runtime manifest")
if execute_request({"@type": "setLogStream", "log_stream": {"@type": "logStreamEmpty"}}).get("@type") != "ok":
    raise SystemExit("TDLib log stream could not be disabled")

client = create()
if not client:
    raise SystemExit("TDLib client creation failed")
try:
    send(client, b'{"@type":"close","@extra":"stackos-native-smoke"}')
    deadline = time.monotonic() + 10
    closed = False
    while time.monotonic() < deadline:
        pointer = receive(client, 0.25)
        if not pointer:
            continue
        response = json.loads(ctypes.string_at(pointer).decode())
        state = response.get("authorization_state", {})
        if state.get("@type") == "authorizationStateClosed":
            closed = True
            break
    if not closed:
        raise SystemExit("TDLib client did not emit authorizationStateClosed")
finally:
    destroy(client)
print("TDLib native JSON ABI smoke passed")
PY

rm -rf "${RUNTIME_ROOT}"
mv "${STAGE_ROOT}" "${RUNTIME_ROOT}"
trap - EXIT
if [[ -n "${WORK_ROOT:-}" ]]; then
  rm -rf "${WORK_ROOT}"
fi
printf 'staged pinned TDLib %s at %s\n' "${TDLIB_VERSION}" "${RUNTIME_ROOT}"
