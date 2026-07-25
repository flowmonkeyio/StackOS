"""CLI shim for shell wrappers around host MCP lifecycle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stackos.host_mcp import inspect_host, register_host, remove_host


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage StackOS host MCP registration.")
    parser.add_argument(
        "host",
        choices=["codex", "claude-code", "claude-desktop", "gemini-cli", "hermes"],
    )
    parser.add_argument("action", choices=["inspect", "register", "remove"])
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument(
        "--profile",
        default=None,
        help="Target an existing named Hermes profile (default: Hermes default profile).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-registration when supported.",
    )
    args = parser.parse_args()

    if args.action == "inspect":
        result = inspect_host(args.host, home=args.home, profile=args.profile)
    elif args.action == "remove":
        result = remove_host(args.host, home=args.home, profile=args.profile)
    else:
        result = register_host(
            args.host,
            home=args.home,
            force=args.force,
            profile=args.profile,
        )
    output = sys.stderr if result.blocking or not result.ok else sys.stdout
    print(result.message, file=output)
    if result.repair and not result.ok:
        print(result.repair, file=output)
    return 1 if result.blocking or not result.ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
