"""Native-shaped Claude commands for the registered foreground integration test."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path
from uuid import uuid4

PERSON = "fef10000-1111-4111-8111-00000000000"
ORG = "fef10000-1111-4111-8111-000000000010"


def native(args: list[str]) -> int:
    profile = Path(os.environ["CLAUDE_CONFIG_DIR"])
    metadata = profile / ".claude.json"
    info = json.loads(metadata.read_text()) if metadata.exists() else {}
    oauth = info.get("oauthAccount", {})
    email = oauth.get("emailAddress")
    if args[:2] == ["auth", "status"]:
        print(
            json.dumps(
                {
                    "loggedIn": bool(email),
                    "authMethod": "claude.ai" if email else "none",
                    "apiProvider": "firstParty",
                    "configDirectory": str(profile),
                    "projectsDirectory": str(profile / "projects"),
                    "email": email,
                    "orgId": ORG,
                }
            )
        )
        return 0 if email else 1
    if args[:2] == ["auth", "login"]:
        assert args[2:] == ["--claudeai"]
        info["oauthAccount"] = {
            "accountUuid": PERSON + "2",
            "organizationUuid": ORG,
            "emailAddress": "b@example.invalid",
        }
        metadata.write_text(json.dumps(info))
        return 0
    if args == ["agents", "--json"]:
        rows = [json.loads(file.read_text()) for file in (profile / "sessions").glob("*.json")]
        print(json.dumps(rows))
        return 0
    if args == ["--safe-mode", "--ax-screen-reader", "--strict-mcp-config"]:
        print(f"Permission Required: Accessing workspace:\n{Path.cwd()}")
        print("Quick safety check: Is this a project you created or one you trust?")
        print("y. Yes, I trust this folder\nn. No, exit\nEnter y/n:", flush=True)
        if input() != "y":
            return 1
        info.setdefault("projects", {}).setdefault(str(Path.cwd()), {})[
            "hasTrustDialogAccepted"
        ] = True
        metadata.write_text(json.dumps(info))
        # Wait for retirement without forwarding or consuming a conversation turn.
        input()
        return 0

    selector, target = args[:2]
    transcript = (
        Path(target)
        if selector == "--resume"
        else profile / "projects" / "work" / f"{target}.jsonl"
    )
    session_id = transcript.stem
    cwd = Path.cwd()
    plugin = Path(args[args.index("--plugin-dir") + 1])
    hooks = json.loads((plugin / "hooks" / "hooks.json").read_text())["hooks"]
    shared = Path(os.environ["STACKOS_DATA_DIR"])
    typo = (shared / "wrong-b").exists() and email == "b@example.invalid"
    wait = (shared / "hold-b").exists() and email == "b@example.invalid"
    process_id = os.getpid()
    sessions = profile / "sessions"
    sessions.mkdir(exist_ok=True)
    state = sessions / f"{process_id}.json"
    prompt_id = None

    def event(name: str) -> int:
        command = shlex.split(hooks[name][0]["hooks"][0]["command"])
        return subprocess.run(
            command,
            input=json.dumps(
                {
                    "hook_event_name": name,
                    "session_id": session_id,
                    "prompt_id": prompt_id,
                    "cwd": str(cwd),
                    "transcript_path": str(transcript),
                    "reason": "prompt_input_exit",
                    "source": "resume" if selector == "--resume" else "startup",
                }
            ),
            text=True,
            check=False,
        ).returncode

    tty.setraw(0)
    assert event("SessionStart") == 0
    state.write_text(
        json.dumps(
            {
                "pid": process_id,
                "sessionId": "wrong" if typo else session_id,
                "kind": "interactive",
                "cwd": str(cwd),
                "startedAt": time.time_ns() // 1_000_000,
                "status": "busy" if wait else "idle",
            }
        )
    )
    print(
        "native "
        + json.dumps(
            {"email": email, "session_id": session_id, "tty": os.ttyname(0), "args": args[4:]}
        ),
        flush=True,
    )
    try:
        # This process has no voluntary input gate. The kernel stops this raw
        # terminal read when the supervisor keeps the group in the background.
        command = ""
        while True:
            char = os.read(0, 1).decode()
            if char not in ("\r", "\n"):
                command += char
                continue
            if command == "exit":
                return event("SessionEnd")
            prompt_id = str(uuid4())
            if event("UserPromptSubmit") == 0:
                if command == "pending":
                    print("tool is pending", flush=True)
                    command = ""
                    continue
                transcript.parent.mkdir(parents=True, exist_ok=True)
                with transcript.open("a") as stream:
                    print(
                        json.dumps(
                            {
                                "type": "user",
                                "sessionId": session_id,
                                "cwd": str(cwd),
                                "message": command,
                                "principal": email,
                            }
                        ),
                        file=stream,
                    )
                assert event("Stop") == 0
                print("effect " + command, flush=True)
            command = ""
    finally:
        state.unlink(missing_ok=True)


if __name__ == "__main__":
    mode, *args = sys.argv[1:]
    if mode == "native":
        sys.exit(native(args))
    from stackos.cli import app

    before = termios.tcgetattr(0)
    result = app(args=args, standalone_mode=False)
    print(f"shell modes restored {termios.tcgetattr(0) == before}", flush=True)
    print(f"shell returned {result or 0}", flush=True)
    print("shell received " + input(), flush=True)
    sys.exit(result or 0)
