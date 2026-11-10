"""Interactive connection setup only; native work is owned by existing hosts."""

import argparse
import getpass
from pathlib import Path

import httpx

from stackos.local_runtime.connection import (
    LocalConnectionStore,
    connect,
    disconnect,
    server_origin,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Connect an owned project to this computer")
    parser.add_argument("command", choices=["connect", "disconnect"])
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--folder", type=Path)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    origin = server_origin(args.server_url)
    store = LocalConnectionStore(args.state_dir)
    if args.command == "connect" and args.folder is None:
        parser.error("connect requires --folder naming an existing local folder")
    with httpx.Client(
        base_url=origin, headers={"Origin": origin}, follow_redirects=False
    ) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": args.email,
                "password": getpass.getpass("StackOS password: "),
            },
        )
        if response.status_code != 200:
            parser.exit(1, "Sign-in failed. Verify this account on the selected server.\n")
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        try:
            if args.command == "connect":
                binding = connect(
                    client,
                    store,
                    server_url=origin,
                    environment_id=args.environment_id,
                    project_id=args.project_id,
                    folder=args.folder,
                )
                print(f"Connected project {binding.project_id} to {binding.root}")
            else:
                saved_binding = store.load()
                if saved_binding is not None and (
                    saved_binding.server_url,
                    saved_binding.environment_id,
                    saved_binding.project_id,
                ) != (origin, args.environment_id, args.project_id):
                    raise ValueError(
                        "selected server/environment/project differs from local binding"
                    )
                disconnect(client, store)
                print("Computer authorization revoked and local binding removed")
        except (ValueError, OSError, httpx.HTTPError):
            parser.exit(
                1,
                "Connection setup refused. Check identity, folder and private state directory.\n",
            )
        finally:
            client.post("/api/v1/auth/logout")


if __name__ == "__main__":
    main()
