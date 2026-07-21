"""CLI shim for shell wrappers around host MCP lifecycle."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stackos.host_mcp import inspect_host, register_host, remove_host
from stackos.host_mcp.service import supported_host_keys


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage StackOS host MCP registration.")
    parser.add_argument(
        "host",
        choices=supported_host_keys(),
    )
    parser.add_argument("action", choices=["inspect", "register", "remove"])
    parser.add_argument("--home", type=Path, default=None)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-registration when supported.",
    )
    args = parser.parse_args()

    if args.action == "inspect":
        result = inspect_host(args.host, home=args.home)
    elif args.action == "remove":
        result = remove_host(args.host, home=args.home)
    else:
        result = register_host(args.host, home=args.home, force=args.force)
    output = sys.stderr if result.blocking or not result.ok else sys.stdout
    print(result.message, file=output)
    if result.repair and not result.ok:
        print(result.repair, file=output)
    return 1 if result.blocking or not result.ok else 0


if __name__ == "__main__":
    raise SystemExit(main())
