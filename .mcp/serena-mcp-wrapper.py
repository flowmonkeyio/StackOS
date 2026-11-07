#!/usr/bin/env python3
"""Deprecated shim: the Serena MCP wrapper is now shared across all repos.

Behaviour lives in ~/.serena/bin/serena-mcp-wrapper.py and is configured
per repo via SERENA_* env vars in .codex/config.toml. This file is kept so
stale references keep working; do not add logic here, and do not fork the
canonical wrapper again.
"""

from pathlib import Path
import os
import sys

CANONICAL = Path.home() / ".serena/bin/serena-mcp-wrapper.py"
if not CANONICAL.is_file():
    raise SystemExit(f"canonical Serena wrapper is missing: {CANONICAL}")

# Preserve repo context for callers that relied on this path being repo-local.
os.environ.setdefault("SERENA_PROJECT", str(Path(__file__).resolve().parents[1]))

os.execv(sys.executable, [sys.executable, str(CANONICAL), *sys.argv[1:]])
