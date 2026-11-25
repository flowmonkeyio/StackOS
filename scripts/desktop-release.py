#!/usr/bin/env python3
"""Load the repository's private API-key environment and delegate desktop release.

Packaging and credential validation remain in the canonical desktop scripts.
This wrapper never prints credentials or reads the private .p8 key contents.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]
API_KEY_NAMES = ("APPLE_API_KEY", "APPLE_API_KEY_ID", "APPLE_API_ISSUER")
COMPETING_NOTARY_NAMES = (
    "APPLE_KEYCHAIN_PROFILE",
    "APPLE_KEYCHAIN",
    "APPLE_ID",
    "APPLE_APP_SPECIFIC_PASSWORD",
    "APPLE_TEAM_ID",
)
BYPASS_NAMES = (
    "STACKOS_UNSIGNED_DEV",
    "STACKOS_ALLOW_UNSIGNED_RELEASE",
    "STACKOS_SKIP_NOTARIZATION",
    "STACKOS_ALLOW_SIGNING_AUTO_DISCOVERY",
    "CSC_IDENTITY_AUTO_DISCOVERY",
)
COMMANDS = {"preflight": "release:preflight", "build": "dist:mac:release"}
API_CONFIGURATION_MESSAGE = (
    "API-key release requires APPLE_API_KEY, APPLE_API_KEY_ID and APPLE_API_ISSUER "
    "in the private .env.local or caller environment. Inspect existing release "
    "configuration before requesting new credentials."
)


def release_environment(repo_root: Path, inherited: Mapping[str, str]) -> dict[str, str]:
    """Load only Apple/signing values, preserving explicit caller values.

    Values are literal dotenv data, never evaluated as shell code. The API-key
    route is deliberate: missing inputs cannot fall back to an example profile.
    """
    env = dict(inherited)
    local_file = repo_root / ".env.local"
    if local_file.is_file():
        for name, value in dotenv_values(local_file, interpolate=False).items():
            if name.startswith(("APPLE_", "CSC_")) and value is not None:
                env.setdefault(name, value)
    if not all(env.get(name, "").strip() for name in API_KEY_NAMES):
        raise ValueError(API_CONFIGURATION_MESSAGE)
    for name in (*COMPETING_NOTARY_NAMES, *BYPASS_NAMES):
        env.pop(name, None)
    env["STACKOS_REQUIRE_SIGNING"] = "1"
    env["STACKOS_REQUIRE_UPDATE_URL"] = "1"
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=COMMANDS)
    args = parser.parse_args(argv)
    try:
        env = release_environment(REPO_ROOT, os.environ)
        return subprocess.run(
            ["pnpm", "--dir", "desktop", "run", COMMANDS[args.mode]],
            cwd=REPO_ROOT,
            env=env,
            check=False,
        ).returncode
    except ValueError:
        print(API_CONFIGURATION_MESSAGE, file=sys.stderr)
    except OSError:
        print(
            "Release could not start; check private configuration access and pnpm.", file=sys.stderr
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
