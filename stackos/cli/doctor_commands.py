"""Doctor diagnostics and install-health checks."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from stackos import __milestone__, __version__
from stackos.config import Settings, get_settings
from stackos.install import _codex_mcp_line_is_bridge as _install_codex_mcp_line_is_bridge

from .app import _exit, app
from .constants import _MCP_SERVER_NAME
from .daemon_commands import _launchd_plist_path, _tcp_can_connect
from .paths import _doctor_home


def _file_mode_or_none(path: Path) -> int | None:
    """Return the file's permission bits or None if it does not exist."""
    if not path.exists():
        return None
    return stat.S_IMODE(path.stat().st_mode)


def _check_credentials_decrypt(
    settings: Settings,
    db_present: bool,
) -> tuple[bool, list[dict[str, object]]]:
    """Return ``(all_ok, [issues])`` for every integration_credentials row.

    Each issue is ``{credential_id, kind, project_id, error}``. Returns
    ``(True, [])`` when there are zero rows or when the DB does not
    exist yet — those are not failures, just a fresh install.
    """
    if not db_present:
        return True, []
    try:
        from sqlmodel import Session, select

        from stackos.crypto.aes_gcm import (
            CryptoError,
            configure_seed_path,
        )
        from stackos.db.connection import make_engine
        from stackos.db.models import IntegrationCredential
        from stackos.repositories.projects import IntegrationCredentialRepository

        configure_seed_path(settings.seed_path)
        engine = make_engine(settings.db_path)
        issues: list[dict[str, object]] = []
        try:
            with Session(engine) as session:
                rows = session.exec(select(IntegrationCredential)).all()
                repo = IntegrationCredentialRepository(session)
                for row in rows:
                    if row.id is None:
                        continue
                    try:
                        repo.get_decrypted(row.id)
                    except CryptoError as exc:
                        issues.append(
                            {
                                "credential_id": row.id,
                                "kind": row.kind,
                                "project_id": row.project_id,
                                "error": str(exc.detail),
                            }
                        )
        finally:
            engine.dispose()
    except Exception as exc:  # pragma: no cover — defensive
        return False, [{"error": f"doctor probe failed: {exc}"}]
    return len(issues) == 0, issues


def _check_alembic_at_head(settings: Settings, db_present: bool) -> tuple[bool, str | None]:
    """Confirm ``alembic_version`` row matches the expected head.

    Returns ``(ok, version_or_None)``. A missing DB (fresh install) is
    treated as a non-failure — ``code=4`` only fires when the DB exists
    AND the version table is missing or stale.
    """
    if not db_present:
        return True, None
    try:
        from alembic.script import ScriptDirectory

        from stackos.db.migrate import alembic_config, current_alembic_version

        current = current_alembic_version(settings)
        script = ScriptDirectory.from_config(alembic_config(settings))
        head = script.get_current_head()
        return current == head, current
    except Exception:  # pragma: no cover — defensive
        return False, None


def _check_scheduler_jobs(settings: Settings) -> tuple[bool, int]:
    """Lightweight liveness check for APScheduler.

    The daemon owns the scheduler instance, so the doctor falls back to checking
    that the daemon is up. When it is, we trust the lifespan registered the
    expected operations jobs.
    """
    daemon_up = _tcp_can_connect(settings.host, settings.port)
    return daemon_up, 4 if daemon_up else 0


def _check_browser_runtime() -> tuple[bool, dict[str, object]]:
    """Return Camoufox package/browser-binary readiness."""
    if importlib.util.find_spec("camoufox") is None:
        return False, {
            "package_installed": False,
            "browser_downloaded": False,
            "browser_path_present": False,
            "repair": "install/sync StackOS Python dependencies, then run `stackos install`",
        }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "camoufox", "path"],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except Exception as exc:
        return False, {
            "package_installed": True,
            "browser_downloaded": False,
            "browser_path_present": False,
            "error": str(exc),
            "repair": "run `python3 -m camoufox fetch` or `stackos install`",
        }
    path = result.stdout.strip()
    ok = result.returncode == 0 and bool(path)
    return ok, {
        "package_installed": True,
        "browser_downloaded": ok,
        "browser_path_present": bool(path),
        "stderr": result.stderr.strip() or None,
        "repair": None if ok else "run `python3 -m camoufox fetch` or `stackos install`",
    }


def _count_traversable_named(
    root: object, filename: str, *, exclude_dirs: frozenset[str] = frozenset()
) -> int:
    """Count files named ``filename`` under an importlib.resources Traversable."""
    count = 0
    try:
        children = list(root.iterdir())  # type: ignore[attr-defined]
    except Exception:
        return 0
    for child in children:
        name = getattr(child, "name", "")
        if name in {".DS_Store", "__pycache__"}:
            continue
        try:
            if child.is_dir():  # type: ignore[attr-defined]
                if name in exclude_dirs:
                    continue
                count += _count_traversable_named(child, filename, exclude_dirs=exclude_dirs)
            elif name == filename:
                count += 1
        except Exception:
            continue
    return count


def _count_expected_plugins(source: object) -> int:
    if isinstance(source, Path):
        return sum(1 for p in source.rglob("plugin.json") if p.parent.name == ".codex-plugin")

    count = 0

    def walk(node: object, *, in_codex_plugin: bool = False) -> None:
        nonlocal count
        try:
            children = list(node.iterdir())  # type: ignore[attr-defined]
        except Exception:
            return
        for child in children:
            name = getattr(child, "name", "")
            try:
                if child.is_dir():  # type: ignore[attr-defined]
                    walk(child, in_codex_plugin=name == ".codex-plugin")
                elif name == "plugin.json" and in_codex_plugin:
                    count += 1
            except Exception:
                continue

    walk(source)
    return count


def _expected_asset_count(kind: Literal["skills", "plugins"]) -> int | None:
    """Return source asset count for doctor install checks."""
    try:
        from stackos import install as installer

        source = installer._resolve_source(kind)  # type: ignore[attr-defined]
    except Exception:
        return None
    if kind == "plugins":
        return _count_expected_plugins(source)
    filename = "SKILL.md"
    if isinstance(source, Path):
        return sum(1 for _ in source.rglob(filename))
    return _count_traversable_named(source, filename, exclude_dirs=frozenset())


def _installed_asset_count(home: Path, runtime: Literal["codex", "claude"], kind: str) -> int:
    """Count installed skills for one runtime target."""
    filename = "SKILL.md"
    target = home / f".{runtime}" / kind / "stackos"
    if not target.is_dir():
        return 0
    return sum(1 for _ in target.rglob(filename))


def _installed_plugin_count(home: Path) -> int:
    target = home / ".codex" / "plugins" / "stackos" / ".codex-plugin" / "plugin.json"
    return 1 if target.is_file() else 0


def _plugin_marketplace_has_stackos(home: Path) -> bool:
    target = home / ".agents" / "plugins" / "marketplace.json"
    if not target.exists():
        return False
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return False
    plugins = payload.get("plugins") if isinstance(payload, dict) else None
    if not isinstance(plugins, list):
        return False
    return any(
        isinstance(p, dict)
        and p.get("name") == "stackos"
        and (p.get("source") or {}).get("path") == "./.codex/plugins/stackos"
        for p in plugins
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stackos_plugin_skill_source() -> dict[str, object]:
    rel_parts = ("stackos", "skills", "stackos", "SKILL.md")
    rel_path = "/".join(rel_parts)
    try:
        from stackos import install as installer

        source = installer._resolve_source("plugins")  # type: ignore[attr-defined]
        if isinstance(source, Path):
            path = source.joinpath(*rel_parts)
            if not path.is_file():
                return {"available": False, "relpath": rel_path, "error": "missing source skill"}
            return {
                "available": True,
                "relpath": rel_path,
                "path": str(path),
                "sha256": _sha256(path.read_bytes()),
            }

        node: Any = source
        for part in rel_parts:
            node = node.joinpath(part)
        if not node.is_file():
            return {"available": False, "relpath": rel_path, "error": "missing bundled skill"}
        return {
            "available": True,
            "relpath": rel_path,
            "path": rel_path,
            "sha256": _sha256(node.read_bytes()),
        }
    except Exception as exc:
        return {"available": False, "relpath": rel_path, "error": str(exc)}


def _installed_skill_info(path: Path, expected_sha256: str | None) -> dict[str, object]:
    if not path.is_file():
        return {"path": str(path), "exists": False, "ok": False}
    digest = _sha256(path.read_bytes())
    return {
        "path": str(path),
        "exists": True,
        "sha256": digest,
        "ok": expected_sha256 is not None and digest == expected_sha256,
    }


def _check_stackos_plugin_skill_sync(home: Path) -> tuple[bool, dict[str, object]]:
    """Compare managed StackOS plugin skill copies with the canonical source."""
    source = _stackos_plugin_skill_source()
    expected = source.get("sha256") if source.get("available") else None
    expected_hash = expected if isinstance(expected, str) else None
    installed = _installed_skill_info(
        home / ".codex" / "plugins" / "stackos" / "skills" / "stackos" / "SKILL.md",
        expected_hash,
    )

    cache_root = home / ".codex" / "plugins" / "cache" / "local-stackos" / "stackos"
    caches: list[dict[str, object]] = []
    if cache_root.is_dir():
        for version_dir in sorted(cache_root.iterdir()):
            if not (version_dir / ".codex-plugin" / "plugin.json").is_file():
                continue
            caches.append(
                _installed_skill_info(
                    version_dir / "skills" / "stackos" / "SKILL.md",
                    expected_hash,
                )
            )

    ok = (
        bool(source.get("available"))
        and bool(installed.get("ok"))
        and all(bool(row.get("ok")) for row in caches)
    )
    return ok, {
        "source": source,
        "installed": installed,
        "cache_count": len(caches),
        "caches": caches,
        "repair": "run `stackos install` or `make install` to refresh managed plugin assets",
    }


def _check_installed_assets(home: Path) -> tuple[dict[str, bool], dict[str, object]]:
    """Return install mirror checks for plugin-first assets."""
    expected_skills = _expected_asset_count("skills")
    expected_plugins = _expected_asset_count("plugins")
    skill_current, skill_info = _check_stackos_plugin_skill_sync(home)
    checks: dict[str, bool] = {}
    details: dict[str, object] = {
        "expected_skills": expected_skills,
        "expected_plugins": expected_plugins,
    }
    for runtime in ("codex", "claude"):
        skills_count = _installed_asset_count(home, runtime, "skills")
        skills_installed = expected_skills is not None and skills_count == expected_skills
        details[f"{runtime}_skills_count"] = skills_count
        details[f"{runtime}_skills_installed"] = skills_installed
        checks[f"{runtime}_skills_installed"] = skills_installed
    plugins_count = _installed_plugin_count(home)
    checks["plugins_installed"] = expected_plugins is not None and plugins_count == expected_plugins
    checks["plugin_marketplace_registered"] = _plugin_marketplace_has_stackos(home)
    checks["stackos_plugin_skill_current"] = skill_current
    details["plugins_count"] = plugins_count
    details["stackos_plugin_skill"] = skill_info
    return checks, details


def _codex_mcp_line_is_bridge(line: str) -> bool:
    return _install_codex_mcp_line_is_bridge(line)


def _check_codex_mcp_registered() -> tuple[bool, dict[str, object]]:
    """Best-effort read-only check for Codex MCP registration."""
    codex = shutil.which("codex")
    if codex is None:
        return False, {"available": False}
    try:
        result = subprocess.run(
            [codex, "mcp", "list"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except Exception as exc:
        return False, {"available": True, "error": str(exc)}
    stackos_lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith(_MCP_SERVER_NAME)
    ]
    bridge_lines = [line for line in stackos_lines if _codex_mcp_line_is_bridge(line)]
    ok = bool(bridge_lines)
    return ok, {
        "available": True,
        "returncode": result.returncode,
        "expected_transport": "stdio",
        "expected_command": "python -m stackos mcp-bridge",
        "expected_server": _MCP_SERVER_NAME,
        "entries": stackos_lines,
        "bridge_entries": bridge_lines,
        "status": "current" if ok else ("stale" if stackos_lines else "missing"),
        "repair": (
            "run `stackos install --mcp-only` or `bash scripts/register-mcp-codex.sh --force`"
        ),
    }


def _check_claude_mcp_registered(home: Path) -> tuple[bool, dict[str, object]]:
    """Read the Claude MCP JSON target and look for the StackOS entry."""
    target = Path(os.environ.get("STACKOS_MCP_TARGET") or home / ".claude" / "mcp.json")
    if not target.exists():
        return False, {"target": str(target), "exists": False}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        servers = payload.get("mcpServers", {})
        row = servers.get(_MCP_SERVER_NAME) if isinstance(servers, dict) else None
    except Exception as exc:
        return False, {"target": str(target), "exists": True, "error": str(exc)}
    ok = (
        isinstance(row, dict)
        and row.get("transport") == "stdio"
        and isinstance(row.get("command"), str)
        and row.get("args") == ["-m", "stackos", "mcp-bridge"]
        and "url" not in row
        and "headers" not in row
    )
    return ok, {
        "target": str(target),
        "exists": True,
        "expected_server": _MCP_SERVER_NAME,
        "expected_transport": "stdio",
        "expected_args": ["-m", "stackos", "mcp-bridge"],
        "has_url": isinstance(row, dict) and "url" in row,
        "has_headers": isinstance(row, dict) and "headers" in row,
    }


def _check_launchd_plist(home: Path) -> tuple[bool, dict[str, object]]:
    """Optional launchd plist presence check; launchd itself is not required."""
    target = _launchd_plist_path(home)
    return target.exists(), {"target": str(target), "exists": target.exists()}


@app.command()
def doctor(
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON")] = False,
) -> None:
    """Diagnose the local StackOS install.

    Exit codes:
      0 all green
      1 daemon down
      4 alembic head mismatch
      7 auth token missing or wrong mode
      8 seed file missing or wrong mode
      9 installed StackOS plugin/skill assets missing or stale

    The DB-not-yet-created case is a *warning*, not a failure — first install
    has no DB until `make migrate` runs.
    """
    settings = get_settings()

    daemon_up = _tcp_can_connect(settings.host, settings.port)

    seed_mode = _file_mode_or_none(settings.seed_path)
    seed_ok = seed_mode == 0o600

    token_mode = _file_mode_or_none(settings.token_path)
    token_ok = token_mode == 0o600

    db_present = settings.db_path.exists()

    # Walk every integration_credentials row and confirm it decrypts cleanly.
    # A failure here usually means the seed file was rotated outside the CLI or
    # restored from a backup that doesn't match the DB's credentials. Surface it
    # as an issue list rather than crashing doctor.
    credentials_ok, credential_issues = _check_credentials_decrypt(settings, db_present)

    # Alembic-head and scheduler-job probes.
    alembic_ok, alembic_version = _check_alembic_at_head(settings, db_present)
    scheduler_ok, scheduler_job_count = _check_scheduler_jobs(settings)
    browser_ok, browser_info = _check_browser_runtime()
    home = _doctor_home()
    install_checks, install_info = _check_installed_assets(home)
    codex_mcp_ok, codex_mcp_info = _check_codex_mcp_registered()
    claude_mcp_ok, claude_mcp_info = _check_claude_mcp_registered(home)
    launchd_ok, launchd_info = _check_launchd_plist(home)

    checks = {
        "daemon_up": daemon_up,
        "seed_file_present": seed_mode is not None,
        "seed_file_mode_0600": seed_ok,
        "auth_token_present": token_mode is not None,
        "auth_token_mode_0600": token_ok,
        "db_file_present": db_present,
        "credentials_decrypt": credentials_ok,
        "alembic_at_head": alembic_ok,
        "scheduler_jobs_healthy": scheduler_ok,
        "browser_runtime_ready": browser_ok,
        "codex_mcp_registered": codex_mcp_ok,
        "claude_mcp_registered": claude_mcp_ok,
        "launchd_plist_present": launchd_ok,
        **install_checks,
    }
    info = {
        "host": settings.host,
        "port": settings.port,
        "data_dir": str(settings.data_dir),
        "state_dir": str(settings.state_dir),
        "seed_mode": oct(seed_mode) if seed_mode is not None else None,
        "token_mode": oct(token_mode) if token_mode is not None else None,
        "version": __version__,
        "milestone": __milestone__,
        "credential_issues": credential_issues,
        "alembic_version": alembic_version,
        "scheduler_job_count": scheduler_job_count,
        "browser_runtime": browser_info,
        "home_dir": str(home),
        "install_checks": install_info,
        "codex_mcp": codex_mcp_info,
        "claude_mcp": claude_mcp_info,
        "launchd": launchd_info,
    }

    # Compute exit code with the highest-priority failure winning so a single
    # `doctor` invocation gives the operator the most actionable signal.
    # Credential decrypt failures imply seed mismatch (operator restored a
    # DB without the seed, or seed was overwritten); we surface that as
    # the same exit-code 8 as a missing seed since the remediation is
    # identical (find the right seed or rotate).
    if seed_mode is None or not seed_ok or not credentials_ok:
        code = 8
    elif token_mode is None or not token_ok:
        code = 7
    elif not alembic_ok:
        code = 4
    elif not all(install_checks.values()) or not browser_ok:
        code = 9
    elif not daemon_up:
        code = 1
    else:
        code = 0

    if json_output:
        typer.echo(json.dumps({"ok": code == 0, "code": code, "checks": checks, "info": info}))
    else:
        status = "OK" if code == 0 else "ISSUES"
        typer.echo(f"stackos doctor: {status} (exit code {code})")
        for name, val in checks.items():
            mark = "  ok " if val else " FAIL"
            typer.echo(f"  [{mark}] {name}: {val}")
        if not db_present:
            typer.echo("  note: DB not yet created — run `make migrate` after first install.")
        if credential_issues:
            typer.echo(
                f"  note: {len(credential_issues)} credential row(s) failed to decrypt — "
                "see --json output for details."
            )
        if not all(install_checks.values()):
            skill_info = install_info.get("stackos_plugin_skill")
            repair = (
                skill_info.get("repair")
                if isinstance(skill_info, dict) and isinstance(skill_info.get("repair"), str)
                else "run `stackos install` or `make install` to refresh managed assets"
            )
            typer.echo(f"  note: installed StackOS plugin or skill assets are stale — {repair}.")
        if not browser_ok:
            repair = browser_info.get("repair")
            typer.echo(f"  note: browser runtime is not ready — {repair}.")

    _exit(code)
