"""Bind an explicit StackOS session, then replace this process with native browse."""

from __future__ import annotations

import os
import sys

import typer

from stackos.browser.runtime import native_process_env
from stackos.cli.api_client import _api_request
from stackos.repositories.base import ValidationError
from stackos.repositories.browser import BrowserRepository

_USAGE = "Usage: stackos.browser --session <full-session-ref> [native browse arguments...]"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) < 2 or args[0] != "--session":
        print(_USAGE, file=sys.stderr)
        return 2
    session_ref = args[1]
    try:
        project_id = BrowserRepository.project_id_from_session_ref(session_ref)
    except ValidationError as exc:
        print(f"{exc}\n{_USAGE}", file=sys.stderr)
        return 2

    try:
        session = _api_request(
            "POST",
            "/api/v1/operations/browser.session.status/call",
            body={
                "arguments": {
                    "project_id": project_id,
                    "session_ref": session_ref,
                    "response_mode": "raw",
                }
            },
        )
        if (
            not isinstance(session, dict)
            or session.get("session_ref") != session_ref
            or session.get("project_id") != project_id
        ):
            raise ValueError("daemon returned invalid session metadata")
        context = session.get("native_cli")
        if not isinstance(context, dict):
            raise ValueError("selected session has no native CLI context; inspect its status")
        executable, cwd, env = context.get("executable"), context.get("cwd"), context.get("env")
        if (
            not isinstance(executable, str)
            or not os.path.isabs(executable)
            or not isinstance(cwd, str)
            or not os.path.isabs(cwd)
            or not isinstance(env, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())
        ):
            raise ValueError("daemon returned invalid native session context")
        # Missing selectors let upstream fall back to unrelated local browser state.
        if (
            any(
                not os.path.isabs(env.get(key, ""))
                for key in ("BROWSE_STATE_FILE", "CHROMIUM_PROFILE")
            )
            or "BROWSE_NO_AUTOSTART" in env
        ):
            raise ValueError("daemon returned an incomplete or outdated session binding")
        native_env = native_process_env(env)
        os.chdir(cwd)
        os.execve(executable, [executable, *args[2:]], native_env)
    except typer.Exit as exc:
        return exc.exit_code
    except (OSError, ValueError) as exc:
        print(f"Cannot bind browser session: {exc}", file=sys.stderr)
        return 1
    return 0  # execve never returns on success.


if __name__ == "__main__":
    raise SystemExit(main())
