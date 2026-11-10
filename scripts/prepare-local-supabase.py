"""Prepare one fresh, private local Supabase workdir; native CLI owns its lifecycle.

No installed settings, host registrations, live data or existing workdirs change.
Requires the Supabase CLI 2.84.2 config contract and a responsive Docker engine.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec


def _signing_key() -> str:
    """Replace the CLI's shared default ES256 key with a per-workdir key."""
    numbers = ec.generate_private_key(ec.SECP256R1()).private_numbers()

    def encode(number: int) -> str:
        return base64.urlsafe_b64encode(number.to_bytes(32)).rstrip(b"=").decode()

    return json.dumps(
        [
            {
                "kty": "EC",
                "crv": "P-256",
                "alg": "ES256",
                "use": "sig",
                "key_ops": ["sign", "verify"],
                "kid": str(uuid.uuid4()),
                "x": encode(numbers.public_numbers.x),
                "y": encode(numbers.public_numbers.y),
                "d": encode(numbers.private_value),
            }
        ]
    )


def _jwt(secret: str, role: str, environment_id: str) -> str:
    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    now = int(time.time())
    header = encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = encode(
        json.dumps(
            {
                "iss": "supabase",
                "ref": environment_id,
                "role": role,
                "iat": now,
                "exp": now + 365 * 86400,
            }
        ).encode()
    )
    body = f"{header}.{payload}"
    signature = encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{signature}"


def _write_private(path: Path, content: str) -> None:
    with open(path, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as output:
        output.write(content)


def prepare(workdir: Path, environment_id: str, ports: dict[str, int]) -> None:
    if not workdir.is_absolute() or workdir.exists():
        raise ValueError("workdir must be a fresh absolute path")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", environment_id):
        raise ValueError("invalid environment_id")
    if len(set(ports.values())) != len(ports) or any(
        not 1024 <= port <= 65535 or port == 5180 for port in ports.values()
    ):
        raise ValueError("choose distinct unprivileged ports, excluding installed port 5180")
    # Supabase namespaces Docker resources by project_id, not just --workdir.
    # Refuse reuse even when an old instance is stopped but its data remains.
    for command in (
        ["docker", "ps", "-aq", "--filter", f"label=com.supabase.cli.project={environment_id}"],
        [
            "docker",
            "volume",
            "ls",
            "-q",
            "--filter",
            f"label=com.supabase.cli.project={environment_id}",
        ],
    ):
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        if result.stdout.strip():
            raise ValueError(
                "Supabase project_id already owns containers or volumes; choose a fresh ID"
            )
    for port in ports.values():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", port))
    workdir.mkdir(mode=0o700, parents=True)
    (workdir / "supabase").mkdir(mode=0o700)
    jwt_secret = secrets.token_urlsafe(48)
    anon = _jwt(jwt_secret, "anon", environment_id)
    service = _jwt(jwt_secret, "service_role", environment_id)
    server_url = f"http://127.0.0.1:{ports['server']}"
    api_url = f"http://127.0.0.1:{ports['api']}"
    config = f'''project_id = "{environment_id}"
[api]
enabled = true
port = {ports["api"]}
schemas = []
extra_search_path = ["public", "extensions"]
max_rows = 1000
[db]
port = {ports["db"]}
shadow_port = {ports["shadow"]}
major_version = 17
[db.pooler]
enabled = false
[db.seed]
enabled = false
[studio]
enabled = false
[inbucket]
enabled = true
port = {ports["mail"]}
[auth]
enabled = true
site_url = "{server_url}"
additional_redirect_urls = []
jwt_issuer = "{api_url}/auth/v1"
signing_keys_path = "./signing_keys.json"
jwt_secret = "{jwt_secret}"
anon_key = "{anon}"
service_role_key = "{service}"
enable_signup = true
minimum_password_length = 8
[auth.email]
enable_signup = true
enable_confirmations = true
[realtime]
enabled = false
[storage]
enabled = false
[edge_runtime]
enabled = false
[analytics]
enabled = false
'''
    _write_private(workdir / "supabase/config.toml", config)
    _write_private(workdir / "supabase/signing_keys.json", _signing_key())
    common = {
        "STACKOS_ENVIRONMENT_ID": environment_id,
        "STACKOS_SERVER_URL": server_url,
        "STACKOS_HOST": "127.0.0.1",
    }
    server = {
        **common,
        "STACKOS_PROCESS_ROLE": "server",
        "STACKOS_PORT": str(ports["server"]),
        "STACKOS_DATA_DIR": str(workdir / "server-data"),
        "STACKOS_STATE_DIR": str(workdir / "server-state"),
        "STACKOS_SUPABASE_WORKDIR": str(workdir),
        "STACKOS_SUPABASE_URL": api_url,
        "STACKOS_DATABASE_URL": f"postgresql://postgres:postgres@127.0.0.1:{ports['db']}/postgres",
        "STACKOS_SUPABASE_ANON_KEY": anon,
        "STACKOS_SUPABASE_SERVICE_ROLE_KEY": service,
    }
    runtime = {
        **common,
        "STACKOS_PROCESS_ROLE": "runtime",
        "STACKOS_PORT": str(ports["runtime"]),
        "STACKOS_DATA_DIR": str(workdir / "runtime-data"),
        "STACKOS_STATE_DIR": str(workdir / "runtime-state"),
    }
    for name, values in (("server.env", server), ("runtime.env", runtime)):
        _write_private(
            workdir / name,
            "".join(f"{key}={shlex.quote(value)}\n" for key, value in values.items()),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--environment-id", required=True)
    for name in ("server", "runtime", "api", "db", "shadow", "mail"):
        parser.add_argument(f"--{name}-port", type=int, required=True)
    args = parser.parse_args()
    try:
        prepare(
            args.workdir,
            args.environment_id,
            {
                name: getattr(args, f"{name}_port")
                for name in ("server", "runtime", "api", "db", "shadow", "mail")
            },
        )
    except (ValueError, OSError, subprocess.CalledProcessError):
        parser.exit(
            1,
            "Preparation refused: use a fresh workdir/project ID, "
            "free distinct ports and responsive Docker.\n",
        )
    print(f"Prepared {args.environment_id} at {args.workdir}; no services started.")


if __name__ == "__main__":
    main()
