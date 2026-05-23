#!/usr/bin/env bash
# Regenerate ui/src/api.ts from the source OpenAPI spec.
#
# Builds the FastAPI app in-process and dumps its OpenAPI document to a
# temporary file, then runs openapi-typescript against that file. This avoids
# blessing a stale long-running local daemon when backend source has changed.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OPENAPI_JSON="$(mktemp)"
GEN_STATE="$(mktemp -d)"
GEN_DATA="$(mktemp -d)"

cleanup() {
    rm -f "$OPENAPI_JSON"
    rm -rf "$GEN_STATE" "$GEN_DATA"
}
trap cleanup EXIT

echo "[gen-types] dumping source OpenAPI"
STACKOS_STATE_DIR="$GEN_STATE" \
    STACKOS_DATA_DIR="$GEN_DATA" \
    uv run python scripts/write-openapi.py "$OPENAPI_JSON"

echo "[gen-types] regenerating ui/src/api.ts"
BEFORE_LINES=$(wc -l < ui/src/api.ts || echo 0)
pnpm --dir ui exec openapi-typescript "$OPENAPI_JSON" -o src/api.ts --enum --root-types
AFTER_LINES=$(wc -l < ui/src/api.ts)
echo "[gen-types] ui/src/api.ts: $BEFORE_LINES -> $AFTER_LINES lines"

if git diff --quiet -- ui/src/api.ts; then
    echo "[gen-types] no changes — types already in sync."
else
    echo "[gen-types] ui/src/api.ts updated; review and commit."
fi
