#!/usr/bin/env bash
#
# StackOS skill installer for the Claude Code runtime.
#
# Mirrors `skills/` into `${HOME}/.claude/skills/stackos/` with
# `rsync -a --delete`. Honours `STACKOS_HOME` so tests can target a
# sandbox HOME.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="${STACKOS_HOME:-${HOME}}"
TARGET="${HOME_DIR}/.claude/skills/stackos"

mkdir -p "${TARGET}"
rsync -a --delete \
    --exclude '.DS_Store' \
    --exclude '__pycache__' \
    "${REPO_ROOT}/skills/" "${TARGET}/"

count=$(find "${TARGET}" -name SKILL.md -type f | wc -l | tr -d ' ')
echo "Installed ${count} skills to ${TARGET}"
