#!/usr/bin/env bash
#
# content-stack skill installer for the Claude Code runtime.
#
# Mirrors `skills/` into `${HOME}/.claude/skills/content-stack/` with
# `rsync -a --delete`. Honours `CONTENT_STACK_HOME` so tests can target a
# sandbox HOME.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="${CONTENT_STACK_HOME:-${HOME}}"
TARGET="${HOME_DIR}/.claude/skills/content-stack"

mkdir -p "${TARGET}"
rsync -a --delete \
    --exclude '.DS_Store' \
    --exclude '__pycache__' \
    "${REPO_ROOT}/skills/" "${TARGET}/"

count=$(find "${TARGET}" -name SKILL.md -type f | wc -l | tr -d ' ')
echo "Installed ${count} skills to ${TARGET}"
