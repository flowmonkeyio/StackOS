#!/usr/bin/env bash
#
# content-stack skill installer for the Codex CLI runtime.
#
# Mirrors `skills/` from this repository into
# `${HOME}/.codex/skills/content-stack/` using `rsync -a --delete` so
# retired skills disappear and re-running the script lands at the same
# end state every time.
#
# Honours `CONTENT_STACK_HOME` for tests so the sandbox HOME fixture
# can redirect the install target without monkey-patching `${HOME}`.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="${CONTENT_STACK_HOME:-${HOME}}"
TARGET="${HOME_DIR}/.codex/skills/content-stack"

mkdir -p "${TARGET}"
rsync -a --delete \
    --exclude '.DS_Store' \
    --exclude '__pycache__' \
    "${REPO_ROOT}/skills/" "${TARGET}/"

count=$(find "${TARGET}" -name SKILL.md -type f | wc -l | tr -d ' ')
echo "Installed ${count} skills to ${TARGET}"
