"""Local setup, migration, install, and maintenance commands."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal

import typer

from stackos.config import get_settings

from .app import app
from .daemon_commands import _install_launchd_autostart
from .doctor_commands import doctor
from .paths import _doctor_home


def _stub(milestone: str, name: str) -> None:
    """Print a clear placeholder and exit 0 for reserved CLIs."""
    typer.echo(f"`stackos {name}` not yet implemented ({milestone}).")


@app.command()
def init(
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing config")] = False,
) -> None:
    """Initialize XDG dirs, seed, and bearer token.

    Idempotent: re-running on a populated state dir is a no-op (the
    seed and token are read but not regenerated). ``--force`` is
    accepted by the CLI shape; it is rejected here as too dangerous to wire
    blindly. Operators wanting to rotate seed
    should call ``stackos rotate-seed`` and ``rotate-token``
    explicitly so the side-effects (re-encryption, MCP re-registration)
    cannot be skipped accidentally.
    """
    if force:
        typer.echo(
            "error: --force on `init` is intentionally not implemented. "
            "Use `stackos rotate-seed --reencrypt` or `rotate-token --yes`.",
            err=True,
        )
        raise typer.Exit(code=2)

    from stackos.auth import ensure_token
    from stackos.crypto.seed import ensure_seed_file

    settings = get_settings()
    settings.ensure_dirs()
    ensure_seed_file(settings.seed_path)
    ensure_token(settings.token_path)
    typer.echo(f"init: state dir at {settings.state_dir}; seed + auth.token present (mode 0600).")


@app.command()
def migrate() -> None:
    """Run alembic migrations forward to head."""
    from stackos.db.migrate import upgrade_to_head

    settings = get_settings()
    result = upgrade_to_head(settings)
    if result.stamped_existing_schema:
        typer.echo("migrate: stamped existing create_all schema at alembic head.")
    typer.echo(f"migrate: alembic upgraded to head ({settings.db_path}).")


@app.command()
def install(
    skills_only: Annotated[
        bool,
        typer.Option("--skills-only", help="Only mirror the StackOS skill into runtimes."),
    ] = False,
    mcp_only: Annotated[
        bool,
        typer.Option("--mcp-only", help="Only register the MCP server."),
    ] = False,
    plugins_only: Annotated[
        bool,
        typer.Option("--plugins-only", help="Only mirror plugins and register marketplace."),
    ] = False,
    launchd: Annotated[
        bool,
        typer.Option("--launchd", help="Also install the launchd plist (macOS)."),
    ] = False,
    skip_doctor: Annotated[
        bool,
        typer.Option("--skip-doctor", help="Skip the post-install doctor check."),
    ] = False,
) -> None:
    """End-user one-liner install — clone-mode or pipx-mode.

    Auto-detects whether the package was installed from a checked-out repo or
    via pipx. Clone mode reads repo assets; package mode resolves bundled
    assets via ``importlib.resources``. The two paths land at the same end
    state.

    Re-running is idempotent: plugins are the default Codex runtime surface,
    Codex and Claude skill mirrors are hydrated from the same canonical
    StackOS skill, and MCP registration upserts existing entries.
    """
    from stackos import install as installer

    selectors = [skills_only, mcp_only, plugins_only]
    if sum(1 for s in selectors if s) > 1:
        typer.echo(
            "error: --skills-only, --mcp-only, and --plugins-only are mutually exclusive.",
            err=True,
        )
        raise typer.Exit(code=2)
    do_skills = skills_only or not (mcp_only or plugins_only)
    do_mcp = mcp_only or not (skills_only or plugins_only)
    do_plugins = plugins_only or not (skills_only or mcp_only)

    mode = installer.detect_mode()
    typer.echo(f"==> Install mode: {mode}")

    settings = get_settings()
    settings.ensure_dirs()
    from stackos.auth import ensure_token
    from stackos.crypto.seed import ensure_seed_file

    ensure_seed_file(settings.seed_path)
    ensure_token(settings.token_path)
    typer.echo(f"==> Bootstrap state ready: {settings.state_dir}")

    if not (skills_only or mcp_only or plugins_only):
        from stackos.db.migrate import upgrade_to_head

        result = upgrade_to_head(settings)
        if result.stamped_existing_schema:
            typer.echo("==> Database schema stamped at alembic head")
        typer.echo(f"==> Database schema ready: {settings.db_path}")
        browser_ok, browser_message = installer.ensure_playwright_browser()
        typer.echo(f"==> Browser runtime: {browser_message}")
        if not browser_ok and "not importable" not in browser_message:
            raise typer.Exit(code=1)

    home = Path.home()

    runtimes: tuple[Literal["codex", "claude"], ...] = ("codex", "claude")
    if do_skills:
        for runtime in runtimes:
            target, count = installer.copy_skills(runtime, home=home)
            typer.echo(f"==> Installed {count} skills -> {target}")

    if do_plugins:
        target, count = installer.copy_plugins(home=home)
        typer.echo(f"==> Installed {count} plugins -> {target}")
        msg = installer.register_plugin_marketplace(home=home)
        typer.echo(f"==> {msg}")

    if do_mcp:
        # Codex first; it prints its own skip-line if not on PATH.
        msg = installer.register_mcp_codex(home=home, port=settings.port)
        typer.echo(f"==> {msg}")
        msg = installer.register_mcp_claude(home=home, port=settings.port)
        typer.echo(f"==> {msg}")

    if launchd:
        ok, message = _install_launchd_autostart(
            settings,
            home=_doctor_home(),
            force=False,
            host=settings.host,
            port=settings.port,
            log_level=settings.log_level,
        )
        if not ok:
            typer.echo(f"==> launchd autostart failed: {message}", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"==> {message}")

    if not skip_doctor:
        typer.echo("==> Running doctor")
        # Re-enter the doctor command in-process so the exit code
        # propagates. We delegate to the underlying typer command via
        # the same module to avoid duplicating the logic.
        try:
            doctor(json_output=False)
        except typer.Exit as exc:
            # A fresh install usually happens before the daemon is started.
            # Keep `doctor` strict when called directly, but let install finish
            # once state, assets, and MCP registration are in place.
            if exc.exit_code == 1:
                typer.echo(
                    "==> Doctor: daemon is not running yet. Start it with "
                    "`stackos start` or `make serve`, then open "
                    f"http://{settings.host}:{settings.port}/."
                )
            elif exc.exit_code not in (0, None):
                raise
    typer.echo("==> install complete")


@app.command(name="rotate-seed")
def rotate_seed(
    reencrypt: Annotated[
        bool,
        typer.Option(
            "--reencrypt",
            help="Required — re-encrypt every integration_credentials row under a fresh seed.",
        ),
    ] = False,
) -> None:
    """Rotate the integration-credentials seed.

    Writes a fresh 32-byte seed, re-encrypts every credential row in a single
    SQLite transaction, and keeps the old seed at ``seed.bin.bak`` for one
    daemon boot. ``--reencrypt`` is mandatory because rotating without
    re-encrypting would orphan every existing credential.
    """
    if not reencrypt:
        typer.echo(
            "error: rotate-seed requires --reencrypt (rotating without re-encryption "
            "would orphan every credential row).",
            err=True,
        )
        raise typer.Exit(code=2)

    from sqlmodel import Session

    from stackos.crypto.aes_gcm import configure_seed_path
    from stackos.crypto.seed import (
        abort_staged_seed_rotation,
        commit_staged_seed_rotation,
        reencrypt_rows_for_seed_rotation,
        stage_seed_rotation,
    )
    from stackos.db.connection import make_engine
    from stackos.db.models import IntegrationCredential

    settings = get_settings()
    settings.ensure_dirs()
    configure_seed_path(settings.seed_path)
    engine = make_engine(settings.db_path)
    db_committed = False
    try:
        with Session(engine) as session:
            from sqlmodel import select

            rows = list(session.exec(select(IntegrationCredential)).all())
            row_dicts = [
                {
                    "id": r.id,
                    "project_id": r.project_id,
                    "kind": r.kind,
                    "encrypted_payload": r.encrypted_payload,
                    "nonce": r.nonce,
                }
                for r in rows
            ]
            new_seed, rotated = reencrypt_rows_for_seed_rotation(settings.seed_path, rows=row_dicts)
            stage_seed_rotation(settings.seed_path, new_seed)
            id_to_row = {r.id: r for r in rows}
            for rotated_row in rotated:
                row = id_to_row[rotated_row["id"]]
                row.encrypted_payload = rotated_row["encrypted_payload"]
                row.nonce = rotated_row["nonce"]
                session.add(row)
            session.commit()
            db_committed = True
        commit_staged_seed_rotation(settings.seed_path)
        # Drop any cached key from the old seed so subsequent calls in
        # this process re-derive from the fresh seed file.
        configure_seed_path(settings.seed_path)
        typer.echo(f"rotate-seed: rotated {len(rows)} row(s); old seed → seed.bin.bak")
    except Exception:
        if not db_committed:
            abort_staged_seed_rotation(settings.seed_path)
        raise
    finally:
        engine.dispose()


@app.command(name="rotate-token")
def rotate_token(
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            help="Required — rotating without confirmation changes the daemon bearer token.",
        ),
    ] = False,
) -> None:
    """Rotate the daemon bearer token and refresh bridge MCP configs.

    Writes a fresh 32 bytes to ``auth.token`` (mode 0600), then re-registers
    Codex + Claude Code so both clients keep using the local stdio bridge. The
    token itself is not stored in agent MCP configs. A daemon that is already
    running keeps accepting the token it loaded at startup until it is restarted.
    """
    if not yes:
        typer.echo(
            "error: rotate-token requires --yes (rotating changes the daemon bearer token).",
            err=True,
        )
        raise typer.Exit(code=2)

    import secrets

    from stackos import install as installer

    settings = get_settings()
    settings.ensure_dirs()
    new_token = secrets.token_hex(32)
    token_path = settings.token_path
    # Write under temp + rename for atomicity, mode 0600 enforced.
    fd = os.open(
        str(token_path) + ".new",
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new_token)
        f.write("\n")
    os.replace(str(token_path) + ".new", token_path)

    # Re-register to keep agent MCP clients on the stdio bridge path.
    msg = installer.register_mcp_codex(port=settings.port, force=True)
    typer.echo(msg)
    msg = installer.register_mcp_claude(port=settings.port)
    typer.echo(msg)
    typer.echo("rotate-token: token rotated; MCP configs updated.")
    typer.echo("rotate-token: run `stackos restart` so the daemon loads the new token.")


@app.command()
def backup() -> None:
    """Reserved backup command placeholder."""
    _stub("backup/restore jobs", "backup")


@app.command()
def restore(
    file: Annotated[Path, typer.Argument(help="Path to a .db backup")],
) -> None:
    """Reserved restore command placeholder."""
    _ = file
    _stub("backup/restore jobs", "restore")


# Re-export Settings on the module so tests can `from stackos.cli import Settings`
# (handy for end-to-end smoke tests that want to assert on env-derived paths).
