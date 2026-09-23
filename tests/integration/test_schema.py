"""Integration tests for the clean StackOS schema."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from stackos.browser.runtime import BROWSER_PROFILE_DIRNAME, browser_profile_dir
from stackos.communication_surface_bindings import communication_surface_binding_external_id

EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "action_calls",
        "action_delivery_admissions",
        "action_versions",
        "actions",
        "agent_requests",
        "agent_sessions",
        "approval_requests",
        "artifacts",
        "auth_providers",
        "browser_action_receipts",
        "browser_profiles",
        "browser_sessions",
        "capabilities",
        "context_index_entries",
        "context_snapshots",
        "credential_accounts",
        "credential_refresh_events",
        "credential_scopes",
        "credential_usage_events",
        "credentials",
        "decisions",
        "durable_action_artifacts",
        "durable_action_attempts",
        "durable_action_items",
        "durable_action_jobs",
        "execution_context_artifacts",
        "execution_context_links",
        "execution_contexts",
        "experiment_observations",
        "experiment_variants",
        "experiments",
        "idempotency_keys",
        "integration_budgets",
        "integration_credentials",
        "learnings",
        "metric_snapshots",
        "oauth_states",
        "payload_secrets",
        "plugins",
        "project_events",
        "project_plugins",
        "project_workflow_templates",
        "projects",
        "provider_object_references",
        "project_credentials",
        "providers",
        "resource_records",
        "resources",
        "run_plan_steps",
        "run_plans",
        "run_step_calls",
        "run_steps",
        "runs",
        "scheduled_jobs",
        "task_tracker_lanes",
        "task_tracker_priorities",
        "task_trackers",
        "telegram_application",
        "tracker_revisions",
        "tracker_tasks",
        "tracker_ticket_dependencies",
        "tracker_ticket_links",
        "tracker_ticket_references",
        "tracker_tickets",
        "tracker_tombstones",
        "workflow_template_versions",
        "workflow_template_extensions",
        "workflow_templates",
        "workspace_bindings",
    }
)

LEGACY_TABLES: frozenset[str] = frozenset(
    {
        "article_assets",
        "article_publishes",
        "article_versions",
        "articles",
        "authors",
        "clusters",
        "compliance_rules",
        "drift_baselines",
        "eeat_criteria",
        "eeat_evaluations",
        "gsc_metrics",
        "gsc_metrics_daily",
        "internal_links",
        "publish_targets",
        "redirects",
        "research_sources",
        "schema_emits",
        "topics",
        "voice_profiles",
    }
)

REJECTED_FINANCE_TABLES: frozenset[str] = frozenset(
    {
        "finance_books",
        "finance_journal_entries",
        "finance_journal_lines",
        "finance_period_events",
        "finance_periods",
    }
)


@pytest.fixture
def isolated_alembic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    data_dir = tmp_path / "data"
    state_dir = tmp_path / "state"
    monkeypatch.setenv("STACKOS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("STACKOS_STATE_DIR", str(state_dir))
    yield data_dir / "stackos.db"


def _run_alembic(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=cwd or repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


def _insert_legacy_project(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT INTO projects
        (id, slug, name, domain, locale, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'en-US', 1, ?, ?)
        """,
        (
            project_id,
            f"legacy-project-{project_id}",
            f"Legacy Project {project_id}",
            f"legacy-{project_id}.example.test",
            now,
            now,
        ),
    )


def _insert_legacy_account(
    conn: sqlite3.Connection,
    *,
    integration_id: int,
    credential_id: int,
    project_id: int,
    credential_ref: str,
    ciphertext: bytes,
    nonce: bytes,
    label: str,
    now: str,
) -> None:
    config_json = json.dumps(
        {
            "auth_method_key": "api_key",
            "profile_key": "default",
            "label": label,
        }
    )
    conn.execute(
        """
        INSERT INTO integration_credentials
        (id, project_id, kind, profile_key, encrypted_payload, nonce,
         config_json, created_at, updated_at)
        VALUES (?, ?, 'openrouter', 'default', ?, ?, ?, ?, ?)
        """,
        (integration_id, project_id, ciphertext, nonce, config_json, now, now),
    )
    conn.execute(
        """
        INSERT INTO credentials
        (id, project_id, integration_credential_id, credential_ref, provider_key,
         auth_type, auth_method_key, profile_key, status, config_json,
         created_at, updated_at)
        VALUES (?, ?, ?, ?, 'openrouter', 'api-key', 'api_key', 'default',
                'connected', ?, ?, ?)
        """,
        (credential_id, project_id, integration_id, credential_ref, config_json, now, now),
    )


def _list_tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE 'alembic_%'"
        )
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def _table_columns(db_path: Path, table_name: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def test_global_account_schema_has_explicit_project_attachments(
    isolated_alembic: Path,
) -> None:
    _run_alembic(["upgrade", "head"])

    credential_columns = _table_columns(isolated_alembic, "credentials")
    integration_columns = _table_columns(isolated_alembic, "integration_credentials")
    attachment_columns = _table_columns(isolated_alembic, "project_credentials")

    assert "display_name" in credential_columns
    assert "project_id" not in credential_columns
    assert "profile_key" not in credential_columns
    assert integration_columns == {
        "id",
        "encrypted_payload",
        "nonce",
        "created_at",
        "updated_at",
    }
    assert attachment_columns == {
        "id",
        "project_id",
        "credential_id",
        "attached_at",
        "attached_by",
    }


def test_global_account_migration_preserves_and_reencrypts_legacy_credentials(
    isolated_alembic: Path,
) -> None:
    from stackos.crypto.aes_gcm import decrypt_account, encrypt
    from stackos.crypto.seed import ensure_seed_file

    _run_alembic(["upgrade", "0025_visible_chromium_profiles"])
    seed_path = isolated_alembic.parent.parent / "state" / "seed.bin"
    seed = ensure_seed_file(seed_path)
    ciphertext, nonce = encrypt(
        b'{"api_key":"legacy-secret"}',
        project_id=1,
        kind="openrouter",
        seed=seed,
    )
    now = "2026-07-24 00:00:00"

    conn = sqlite3.connect(isolated_alembic)
    try:
        conn.execute(
            """
            INSERT INTO projects
            (id, slug, name, domain, locale, is_active, created_at, updated_at)
            VALUES
            (1, 'legacy-project', 'Legacy Project', 'example.com', 'en-US', 1, ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO integration_credentials
            (id, project_id, kind, profile_key, encrypted_payload, nonce,
            config_json, created_at, updated_at)
            VALUES
            (1, 1, 'openrouter', 'default', ?, ?,
             '{"auth_method_key":"api_key","profile_key":"default","label":"Openrouter - Default"}',
             ?, ?)
            """,
            (ciphertext, nonce, now, now),
        )
        conn.execute(
            """
            INSERT INTO credentials
            (id, project_id, integration_credential_id, credential_ref, provider_key,
             auth_type, auth_method_key, profile_key, status, config_json,
             created_at, updated_at)
            VALUES
            (1, 1, 1, 'cred_legacy', 'openrouter',
             'api-key', 'api_key', 'default', 'connected',
             '{"auth_method_key":"api_key","profile_key":"default","label":"Openrouter - Default"}',
             ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO credentials
            (id, project_id, integration_credential_id, credential_ref, provider_key,
             auth_type, auth_method_key, profile_key, status, revoked_at, config_json,
             created_at, updated_at)
            VALUES
            (2, 1, NULL, 'cred_revoked', 'openrouter',
             'api-key', 'api_key', 'retired', 'revoked', ?,
             '{"auth_method_key":"api_key","label":"Retired"}', ?, ?)
            """,
            (now, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "head"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        account = conn.execute(
            """
            SELECT credential_ref, provider_key, display_name, display_name_key,
                   integration_credential_id, config_json
            FROM credentials WHERE id = 1
            """
        ).fetchone()
        assert account is not None
        assert account[:4] == (
            "cred_legacy",
            "openrouter",
            "Openrouter - Default",
            "openrouter - default",
        )
        assert account[4] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM credentials WHERE credential_ref = 'cred_revoked'"
        ).fetchone() == (0,)
        assert "profile_key" not in json.loads(account[5])
        assert conn.execute(
            "SELECT project_id, credential_id FROM project_credentials"
        ).fetchall() == [(1, 1)]
        encrypted = conn.execute(
            "SELECT encrypted_payload, nonce FROM integration_credentials WHERE id = 1"
        ).fetchone()
        assert encrypted is not None
        assert (
            decrypt_account(
                encrypted[0],
                nonce=encrypted[1],
                credential_ref="cred_legacy",
                provider_key="openrouter",
                seed=seed,
            )
            == b'{"api_key":"legacy-secret"}'
        )
    finally:
        conn.close()


def test_global_account_migration_preserves_credential_foreign_key_children(
    isolated_alembic: Path,
) -> None:
    from stackos.crypto.aes_gcm import decrypt_account, encrypt
    from stackos.crypto.seed import ensure_seed_file

    _run_alembic(["upgrade", "0025_visible_chromium_profiles"])
    seed = ensure_seed_file(isolated_alembic.parent.parent / "state" / "seed.bin")
    legacy_secret = b'{"api_key":"legacy-child-secret"}'
    ciphertext, nonce = encrypt(
        legacy_secret,
        project_id=1,
        kind="openrouter",
        seed=seed,
    )
    now = "2026-07-24 00:00:00"

    conn = sqlite3.connect(isolated_alembic)
    try:
        _insert_legacy_project(conn, project_id=1, now=now)
        _insert_legacy_account(
            conn,
            integration_id=1,
            credential_id=1,
            project_id=1,
            credential_ref="cred_children",
            ciphertext=ciphertext,
            nonce=nonce,
            label="Children",
            now=now,
        )
        conn.execute(
            """
            INSERT INTO credential_scopes (id, credential_id, scope, created_at)
            VALUES (11, 1, 'read:models', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO credential_accounts
            (id, credential_id, provider_account_id, display_name, metadata_json,
             created_at, updated_at)
            VALUES (12, 1, 'provider-account', 'Provider Account', '{"tier":"pro"}', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO provider_object_references
            (id, project_id, credential_id, provider_key, provider_account_id, object_type,
             provider_object_id, safe_ref, display_name, metadata_json, stale_at,
             created_at, updated_at)
            VALUES (13, 1, 1, 'openrouter', 'provider-account', 'model', 'model-1',
                    'safe_model_1', 'Model One', '{"source":"legacy"}', NULL, ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO action_calls
            (id, project_id, credential_id, action_key, plugin_slug, provider_key, operation,
             status, dry_run, idempotency_key, credential_ref, cost_cents, created_at)
            VALUES (14, 1, 1, 'openrouter.models.list', 'test-plugin', 'openrouter', 'list',
                    'success', 0, 'legacy-child-action', 'cred_children', 0, ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO credential_usage_events
            (id, credential_id, project_id, provider_key, operation, status,
             metadata_json, created_at)
            VALUES (15, 1, 1, 'openrouter', 'list', 'success', '{"source":"legacy"}', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO credential_refresh_events
            (id, credential_id, project_id, provider_key, status, metadata_json, created_at)
            VALUES (16, 1, 1, 'openrouter', 'success', '{"source":"legacy"}', ?)
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO oauth_states
            (id, project_id, provider_key, credential_id, integration_credential_id, state,
             redirect_uri, expires_at, consumed_at, created_at)
            VALUES (17, 1, 'openrouter', 1, 1, 'legacy-child-state',
                    'https://example.test/callback', NULL, NULL, ?)
            """,
            (now,),
        )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "head"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        assert conn.execute(
            "SELECT id, credential_id, scope FROM credential_scopes"
        ).fetchall() == [(11, 1, "read:models")]
        assert conn.execute(
            "SELECT id, credential_id, provider_account_id FROM credential_accounts"
        ).fetchall() == [(12, 1, "provider-account")]
        assert conn.execute(
            """
            SELECT id, project_id, credential_id, safe_ref
            FROM provider_object_references
            """
        ).fetchall() == [(13, 1, 1, "safe_model_1")]
        assert conn.execute(
            "SELECT id, credential_id, credential_ref FROM action_calls"
        ).fetchall() == [(14, 1, "cred_children")]
        assert conn.execute("SELECT id, credential_id FROM credential_usage_events").fetchall() == [
            (15, 1)
        ]
        assert conn.execute(
            "SELECT id, credential_id FROM credential_refresh_events"
        ).fetchall() == [(16, 1)]
        oauth_state = conn.execute(
            """
            SELECT id, attach_project_id, credential_id, integration_credential_id, consumed_at
            FROM oauth_states
            """
        ).fetchone()
        assert oauth_state is not None
        assert oauth_state[:4] == (17, 1, 1, 1)
        assert oauth_state[4] is not None
        encrypted = conn.execute(
            "SELECT encrypted_payload, nonce FROM integration_credentials WHERE id = 1"
        ).fetchone()
        assert encrypted is not None
        assert (
            decrypt_account(
                encrypted[0],
                nonce=encrypted[1],
                credential_ref="cred_children",
                provider_key="openrouter",
                seed=seed,
            )
            == legacy_secret
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_global_account_backing_repair_is_a_healthy_noop(
    isolated_alembic: Path,
) -> None:
    from stackos.crypto.aes_gcm import encrypt
    from stackos.crypto.seed import ensure_seed_file

    _run_alembic(["upgrade", "0025_visible_chromium_profiles"])
    seed = ensure_seed_file(isolated_alembic.parent.parent / "state" / "seed.bin")
    ciphertext, nonce = encrypt(
        b'{"api_key":"healthy"}',
        project_id=1,
        kind="openrouter",
        seed=seed,
    )
    now = "2026-07-24 00:00:00"
    conn = sqlite3.connect(isolated_alembic)
    try:
        _insert_legacy_project(conn, project_id=1, now=now)
        _insert_legacy_account(
            conn,
            integration_id=1,
            credential_id=1,
            project_id=1,
            credential_ref="cred_healthy",
            ciphertext=ciphertext,
            nonce=nonce,
            label="Healthy",
            now=now,
        )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "0026_global_reusable_accounts"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        before = conn.execute(
            """
            SELECT integration_credential_id, status, updated_at
            FROM credentials WHERE id = 1
            """
        ).fetchone()
        assert before is not None
    finally:
        conn.close()

    _run_alembic(["upgrade", "head"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        assert (
            conn.execute(
                """
            SELECT integration_credential_id, status, updated_at
            FROM credentials WHERE id = 1
            """
            ).fetchone()
            == before
        )
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0032_shared_telegram_application",
        )
    finally:
        conn.close()


def test_telegram_application_migration_preserves_matching_sessions_and_flags_conflicts(
    isolated_alembic: Path,
) -> None:
    from stackos.crypto.aes_gcm import decrypt, decrypt_account, encrypt_account
    from stackos.crypto.seed import ensure_seed_file

    _run_alembic(["upgrade", "0031_retire_telegram_bot_api"])
    seed = ensure_seed_file(isolated_alembic.parent.parent / "state" / "seed.bin")
    native_database = isolated_alembic.parent / "telegram-tdlib" / "cred_telegram_2" / "database"
    native_database.mkdir(parents=True)
    (native_database / "tdlib-state").write_bytes(b"saved-native-session")
    now = "2026-09-22 00:00:00"
    conn = sqlite3.connect(isolated_alembic)
    try:
        for account_id, app_id, app_hash in (
            (1, 12345, "shared-app-hash"),
            (2, 12345, "shared-app-hash"),
            (3, 67890, "different-app-hash"),
        ):
            ref = f"cred_telegram_{account_id}"
            payload = json.dumps(
                {
                    "api_hash": app_hash,
                    "bot_token": f"{account_id}:test-token",
                    "_tdlib_database_encryption_key": f"saved-key-{account_id}",
                }
            ).encode()
            ciphertext, nonce = encrypt_account(
                payload, credential_ref=ref, provider_key="telegram", seed=seed
            )
            conn.execute(
                """
                INSERT INTO integration_credentials
                (id, encrypted_payload, nonce, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (account_id, ciphertext, nonce, now, now),
            )
            config = {
                "auth_method_key": "tdlib-bot-token",
                "api_id": app_id,
                "telegram_desired_connected": account_id >= 2,
                "telegram_auth": {
                    "generation": 1,
                    "state": "disconnected",
                    "updated_at": "2026-09-22T00:00:00",
                },
            }
            conn.execute(
                """
                INSERT INTO credentials
                (id, integration_credential_id, credential_ref, provider_key,
                 display_name, display_name_key, auth_type, auth_method_key,
                 status, config_json, created_at, updated_at)
                VALUES (?, ?, ?, 'telegram', ?, ?, 'tdlib-bot-token',
                        'tdlib-bot-token', 'disconnected', ?, ?, ?)
                """,
                (
                    account_id,
                    account_id,
                    ref,
                    ref,
                    ref,
                    json.dumps(config),
                    now,
                    now,
                ),
            )
            if account_id == 2:
                conn.execute(
                    "UPDATE credentials SET auth_type = 'tdlib-user-session', "
                    "auth_method_key = 'tdlib-user-session' WHERE id = 2"
                )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "head"])
    conn = sqlite3.connect(isolated_alembic)
    try:
        app_ciphertext, app_nonce = conn.execute(
            "SELECT encrypted_payload, nonce FROM telegram_application WHERE id = 1"
        ).fetchone()
        application = json.loads(
            decrypt(
                app_ciphertext,
                nonce=app_nonce,
                project_id=None,
                kind="telegram-application",
                seed=seed,
            )
        )
        assert application == {"api_id": 12345, "api_hash": "shared-app-hash"}
        for account_id in (1, 2):
            ref = f"cred_telegram_{account_id}"
            config_raw, ciphertext, nonce = conn.execute(
                """
                SELECT c.config_json, i.encrypted_payload, i.nonce
                FROM credentials c JOIN integration_credentials i
                ON i.id = c.integration_credential_id WHERE c.id = ?
                """,
                (account_id,),
            ).fetchone()
            config = json.loads(config_raw)
            payload = json.loads(
                decrypt_account(
                    ciphertext,
                    nonce=nonce,
                    credential_ref=ref,
                    provider_key="telegram",
                    seed=seed,
                )
            )
            assert "api_id" not in config
            assert "api_hash" not in payload
            assert payload["_tdlib_database_encryption_key"] == f"saved-key-{account_id}"
            if account_id == 2:
                assert config["telegram_desired_connected"] is True
        assert (native_database / "tdlib-state").read_bytes() == b"saved-native-session"
        config_raw, ciphertext, nonce = conn.execute(
            """
            SELECT c.config_json, i.encrypted_payload, i.nonce
            FROM credentials c JOIN integration_credentials i
            ON i.id = c.integration_credential_id WHERE c.id = 3
            """
        ).fetchone()
        config = json.loads(config_raw)
        payload = json.loads(
            decrypt_account(
                ciphertext,
                nonce=nonce,
                credential_ref="cred_telegram_3",
                provider_key="telegram",
                seed=seed,
            )
        )
        assert config["telegram_application_conflict"] is True
        assert config["telegram_desired_connected"] is False
        assert config["telegram_auth"]["state"] == "repair-required"
        assert payload["api_hash"] == "different-app-hash"
    finally:
        conn.close()
    from sqlmodel import Session

    from stackos.auth_providers import AuthRepository
    from stackos.db.connection import make_engine

    engine = make_engine(isolated_alembic)
    try:
        with Session(engine) as session:
            account = AuthRepository(session).get_account(credential_ref="cred_telegram_3")
            assert account.status == "repair-required"
            assert account.setup_required is True
    finally:
        engine.dispose()


def test_global_account_backing_repair_restores_only_the_missing_backing_link(
    isolated_alembic: Path,
) -> None:
    from stackos.crypto.aes_gcm import decrypt_account, encrypt
    from stackos.crypto.seed import ensure_seed_file

    _run_alembic(["upgrade", "0025_visible_chromium_profiles"])
    seed = ensure_seed_file(isolated_alembic.parent.parent / "state" / "seed.bin")
    legacy_secret = b'{"api_key":"repair-me"}'
    ciphertext, nonce = encrypt(
        legacy_secret,
        project_id=1,
        kind="openrouter",
        seed=seed,
    )
    now = "2026-07-24 00:00:00"
    conn = sqlite3.connect(isolated_alembic)
    try:
        _insert_legacy_project(conn, project_id=1, now=now)
        _insert_legacy_account(
            conn,
            integration_id=1,
            credential_id=1,
            project_id=1,
            credential_ref="cred_repair",
            ciphertext=ciphertext,
            nonce=nonce,
            label="Repair",
            now=now,
        )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "0026_global_reusable_accounts"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        conn.execute(
            """
            INSERT INTO action_calls
            (id, project_id, credential_id, action_key, plugin_slug, provider_key, operation,
             status, dry_run, idempotency_key, credential_ref, cost_cents, created_at)
            VALUES (21, 1, NULL, 'openrouter.models.list', 'test-plugin', 'openrouter', 'list',
                    'success', 0, 'null-audit-link', 'cred_repair', 0, ?)
            """,
            (now,),
        )
        conn.executemany(
            """
            INSERT INTO action_calls
            (id, project_id, credential_id, action_key, plugin_slug, provider_key, operation,
             status, dry_run, idempotency_key, credential_ref, cost_cents, created_at)
            VALUES (?, 1, NULL, 'test.action', 'test-plugin', ?, 'test',
                    'success', 0, ?, ?, 0, ?)
            """,
            [
                (22, "different-provider", "unknown-audit-link", "cred_unknown", now),
                (23, "different-provider", "mismatch-audit-link", "cred_repair", now),
            ],
        )
        before = conn.execute("SELECT status, updated_at FROM credentials WHERE id = 1").fetchone()
        conn.execute("UPDATE credentials SET integration_credential_id = NULL WHERE id = 1")
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "head"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        assert conn.execute(
            """
            SELECT integration_credential_id, status, updated_at
            FROM credentials WHERE id = 1
            """
        ).fetchone() == (1, *before)
        assert conn.execute(
            "SELECT credential_id, credential_ref FROM action_calls WHERE id = 21"
        ).fetchone() == (1, "cred_repair")
        unmatched = conn.execute(
            """
            SELECT id, credential_id, metadata_json
            FROM action_calls WHERE id IN (22, 23) ORDER BY id
            """
        ).fetchall()
        assert [row[:2] for row in unmatched] == [(22, None), (23, None)]
        assert all(
            json.loads(row[2])["credential_identity_status"]
            == "removed-before-account-tombstone-retention"
            for row in unmatched
        )
        encrypted = conn.execute(
            "SELECT encrypted_payload, nonce FROM integration_credentials WHERE id = 1"
        ).fetchone()
        assert encrypted is not None
        assert (
            decrypt_account(
                encrypted[0],
                nonce=encrypted[1],
                credential_ref="cred_repair",
                provider_key="openrouter",
                seed=seed,
            )
            == legacy_secret
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_communication_binding_cleanup_removes_aliases_without_inventing_an_account(
    isolated_alembic: Path,
) -> None:
    _run_alembic(["upgrade", "0027_repair_global_account_backings"])
    now = "2026-07-25 00:00:00"
    profile = {
        "key": "support",
        "profile_ref": "communication-profile:support",
        "enabled": True,
        "identity": {"display_name": "Support"},
        "provider_facets": {
            "slack-bot": {
                "auth_profile_key": "default",
                "team_id": "T123",
                "ingress_url": "https://stale.example/ingress",
                "manual_ingress_confirmation": {
                    "ingress_url": "https://stale.example/ingress",
                    "source": "legacy-local-state",
                },
            }
        },
        "access_policy": {"allowed_user_refs": ["slack-user:U123"]},
    }
    conn = sqlite3.connect(isolated_alembic)
    try:
        _insert_legacy_project(conn, project_id=1, now=now)
        conn.execute(
            """
            INSERT INTO plugins
            (id, slug, name, version, description, source, manifest_json, created_at, updated_at)
            VALUES (1, 'communications', 'Communications', '1.0.0', '', 'builtin', '{}', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO resources
            (id, plugin_id, key, name, description, schema_json, created_at, updated_at)
            VALUES (1, 1, 'communication-profile', 'Profile', '', '{}', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO resource_records
            (id, project_id, resource_id, external_id, title, data_json,
             provenance_json, created_at, updated_at)
            VALUES (1, 1, 1, 'communication-profile:support', 'Support', ?, '{}', ?, ?)
            """,
            (json.dumps(profile), now, now),
        )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "head"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        cleaned = json.loads(
            conn.execute("SELECT data_json FROM resource_records WHERE id = 1").fetchone()[0]
        )
        facet = cleaned["provider_facets"]["slack-bot"]
        assert "auth_profile_key" not in facet
        assert "credential_ref" not in facet
        assert facet["ingress_enabled"] is True
        assert "ingress_url" not in facet
        assert "manual_ingress_confirmation" not in facet
        assert facet["team_id"] == "T123"
        assert cleaned["access_policy"] == {"allowed_user_refs": ["slack-user:U123"]}
    finally:
        conn.close()


@pytest.mark.parametrize("canonical_last", [False, True])
def test_communication_surface_binding_migration_scopes_legacy_surfaces_to_one_profile(
    isolated_alembic: Path,
    canonical_last: bool,
) -> None:
    _run_alembic(["upgrade", "0028_cleanup_communication_account_bindings"])
    now = "2026-09-22 00:00:00"
    support_profile = {
        "key": "support",
        "profile_ref": "communication-profile:support",
        "provider_facets": {"slack-bot": {"credential_ref": "cred_support"}},
    }
    sales_profile = {
        "key": "sales",
        "profile_ref": "communication-profile:sales",
        "provider_facets": {"slack-bot": {"credential_ref": "cred_sales"}},
    }
    generic_surface = {
        "provider_key": "slack-bot",
        "credential_ref": "cred_support",
        "surface_ref": "slack-channel:C123",
        "channel_ref": "slack-channel:C123",
        "kind": "slack-channel",
    }
    slack_surface = {
        "provider_key": "slack-bot",
        "profile_key": "support",
        "credential_ref": "cred_support",
        "surface_ref": "slack-channel:C456",
        "channel_ref": "slack-channel:C456",
        "kind": "slack-channel",
    }
    ambiguous_surface = {
        "provider_key": "slack-bot",
        "surface_ref": "slack-channel:C999",
        "channel_ref": "slack-channel:C999",
        "kind": "slack-channel",
    }
    direct_conflict_surface = {
        "provider_key": "slack-bot",
        "profile_key": "support",
        "credential_ref": "cred_support",
        "surface_ref": "slack-channel:C777",
        "channel_ref": "slack-channel:C777",
        "kind": "slack-channel",
        "audience": "internal",
    }
    generic_conflict_surface = {
        "provider_key": "slack-bot",
        "credential_ref": "cred_support",
        "surface_ref": "slack-channel:C777",
        "channel_ref": "slack-channel:C777",
        "kind": "slack-channel",
        "audience": "customer",
    }
    conn = sqlite3.connect(isolated_alembic)
    try:
        _insert_legacy_project(conn, project_id=1, now=now)
        conn.execute(
            """
            INSERT INTO plugins
            (id, slug, name, version, description, source, manifest_json, created_at, updated_at)
            VALUES (1, 'communications', 'Communications', '1.0.0', '', 'builtin', '{}', ?, ?)
            """,
            (now, now),
        )
        conn.executemany(
            """
            INSERT INTO resources
            (id, plugin_id, key, name, description, schema_json, created_at, updated_at)
            VALUES (?, 1, ?, ?, '', '{}', ?, ?)
            """,
            [
                (1, "communication-profile", "Profile", now, now),
                (2, "communication-channel", "Channel", now, now),
            ],
        )
        conn.executemany(
            """
            INSERT INTO resource_records
            (id, project_id, resource_id, external_id, title, data_json,
             provenance_json, created_at, updated_at)
            VALUES (?, 1, ?, ?, ?, ?, '{}', ?, ?)
            """,
            [
                (
                    1,
                    1,
                    "communication-profile:support",
                    "Support",
                    json.dumps(support_profile),
                    now,
                    now,
                ),
                (
                    2,
                    1,
                    "communication-profile:sales",
                    "Sales",
                    json.dumps(sales_profile),
                    now,
                    now,
                ),
                (
                    3,
                    2,
                    "communication-surface:slack-channel:C123",
                    "Support",
                    json.dumps(generic_surface),
                    now,
                    now,
                ),
                (
                    4,
                    2,
                    "slack-channel:support:C456",
                    "Support",
                    json.dumps(slack_surface),
                    now,
                    now,
                ),
                (
                    5,
                    2,
                    "communication-surface:slack-channel:C999",
                    "Ambiguous",
                    json.dumps(ambiguous_surface),
                    now,
                    now,
                ),
                (
                    6,
                    2,
                    "slack-channel:support:C777",
                    "Conflicting direct",
                    json.dumps(direct_conflict_surface),
                    now,
                    now,
                ),
                (
                    7,
                    2,
                    "communication-surface:slack-channel:C777",
                    "Conflicting generic",
                    json.dumps(generic_conflict_surface),
                    now,
                    now,
                ),
            ],
        )
        if canonical_last:
            # A pre-existing canonical record can follow a legacy conflicting
            # record in id order; its later visit must not erase the repair flag.
            conn.execute(
                "UPDATE resource_records SET external_id = ? WHERE id = 7",
                (
                    communication_surface_binding_external_id(
                        provider_key="slack-bot",
                        profile_ref="communication-profile:support",
                        surface_ref="slack-channel:C777",
                    ),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "head"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        migrated = conn.execute(
            "SELECT external_id, data_json FROM resource_records WHERE id IN (3, 4) ORDER BY id"
        ).fetchall()
        assert [row[0] for row in migrated] == [
            communication_surface_binding_external_id(
                provider_key="slack-bot",
                profile_ref="communication-profile:support",
                surface_ref="slack-channel:C123",
            ),
            communication_surface_binding_external_id(
                provider_key="slack-bot",
                profile_ref="communication-profile:support",
                surface_ref="slack-channel:C456",
            ),
        ]
        assert all(
            json.loads(row[1])["profile_ref"] == "communication-profile:support" for row in migrated
        )

        external_id, data_json = conn.execute(
            "SELECT external_id, data_json FROM resource_records WHERE id = 5"
        ).fetchone()
        assert external_id == "communication-surface:slack-channel:C999"
        assert json.loads(data_json)["surface_binding_state"] == "repair-required"
        assert json.loads(data_json)["surface_binding_issue"] == "ambiguous_profile_binding"

        conflict_rows = conn.execute(
            "SELECT external_id, data_json FROM resource_records WHERE id IN (6, 7) ORDER BY id"
        ).fetchall()
        canonical_index = 1 if canonical_last else 0
        assert conflict_rows[canonical_index][0] == communication_surface_binding_external_id(
            provider_key="slack-bot",
            profile_ref="communication-profile:support",
            surface_ref="slack-channel:C777",
        )
        assert conflict_rows[1 - canonical_index][0] == (
            "slack-channel:support:C777"
            if canonical_last
            else "communication-surface:slack-channel:C777"
        )
        assert all(
            json.loads(row[1])["surface_binding_state"] == "repair-required"
            for row in conflict_rows
        )
        assert all(
            json.loads(row[1])["surface_binding_issue"] == "conflicting_duplicate_binding_metadata"
            for row in conflict_rows
        )
    finally:
        conn.close()


@pytest.mark.parametrize("failure_mode", ["zero-match", "ambiguous-match"])
def test_global_account_backing_repair_fails_closed_without_partial_writes(
    isolated_alembic: Path,
    failure_mode: str,
) -> None:
    from stackos.crypto.aes_gcm import encrypt
    from stackos.crypto.seed import ensure_seed_file

    _run_alembic(["upgrade", "0025_visible_chromium_profiles"])
    seed = ensure_seed_file(isolated_alembic.parent.parent / "state" / "seed.bin")
    first_ciphertext, first_nonce = encrypt(
        b'{"api_key":"uniquely-valid"}',
        project_id=1,
        kind="openrouter",
        seed=seed,
    )
    second_ciphertext, second_nonce = encrypt(
        b'{"api_key":"fail-closed"}',
        project_id=2,
        kind="openrouter",
        seed=seed,
    )
    now = "2026-07-24 00:00:00"
    conn = sqlite3.connect(isolated_alembic)
    try:
        _insert_legacy_project(conn, project_id=1, now=now)
        _insert_legacy_project(conn, project_id=2, now=now)
        _insert_legacy_account(
            conn,
            integration_id=1,
            credential_id=1,
            project_id=1,
            credential_ref="cred_uniquely_valid",
            ciphertext=first_ciphertext,
            nonce=first_nonce,
            label="Uniquely Valid",
            now=now,
        )
        _insert_legacy_account(
            conn,
            integration_id=2,
            credential_id=2,
            project_id=2,
            credential_ref="cred_fail_closed",
            ciphertext=second_ciphertext,
            nonce=second_nonce,
            label="Fail Closed",
            now=now,
        )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "0026_global_reusable_accounts"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        if failure_mode == "zero-match":
            conn.execute("DELETE FROM integration_credentials WHERE id = 2")
        else:
            encrypted = conn.execute(
                "SELECT encrypted_payload, nonce, created_at, updated_at "
                "FROM integration_credentials WHERE id = 2"
            ).fetchone()
            assert encrypted is not None
            conn.execute(
                """
                INSERT INTO integration_credentials
                (id, encrypted_payload, nonce, created_at, updated_at)
                VALUES (3, ?, ?, ?, ?)
                """,
                encrypted,
            )
        conn.execute("UPDATE credentials SET integration_credential_id = NULL WHERE id IN (1, 2)")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(subprocess.CalledProcessError) as error:
        _run_alembic(["upgrade", "head"])
    assert "restore the pre-0026 backup or contact support" in error.value.stderr
    assert "No changes were applied." in error.value.stderr

    conn = sqlite3.connect(isolated_alembic)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0026_global_reusable_accounts",
        )
        assert conn.execute(
            """
            SELECT id, integration_credential_id, status
            FROM credentials
            WHERE id IN (1, 2)
            ORDER BY id
            """
        ).fetchall() == [(1, None, "connected"), (2, None, "connected")]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


@pytest.mark.parametrize("failure_mode", ["missing-seed", "corrupt-ciphertext"])
def test_global_account_migration_preflight_failure_leaves_0025_untouched(
    isolated_alembic: Path,
    failure_mode: str,
) -> None:
    from stackos.crypto.seed import ensure_seed_file

    _run_alembic(["upgrade", "0025_visible_chromium_profiles"])
    seed_path = isolated_alembic.parent.parent / "state" / "seed.bin"
    if failure_mode == "corrupt-ciphertext":
        ensure_seed_file(seed_path)
    else:
        assert not seed_path.exists()
    now = "2026-07-24 00:00:00"
    ciphertext = b"not-a-valid-aes-gcm-payload"
    nonce = b"0" * 12

    conn = sqlite3.connect(isolated_alembic)
    try:
        _insert_legacy_project(conn, project_id=1, now=now)
        _insert_legacy_account(
            conn,
            integration_id=1,
            credential_id=1,
            project_id=1,
            credential_ref="cred_preflight",
            ciphertext=ciphertext,
            nonce=nonce,
            label="Preflight",
            now=now,
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(subprocess.CalledProcessError):
        _run_alembic(["upgrade", "head"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0025_visible_chromium_profiles",
        )
        assert "display_name" not in _table_columns(isolated_alembic, "credentials")
        assert "project_id" in _table_columns(isolated_alembic, "integration_credentials")
        assert conn.execute("SELECT credential_ref, project_id FROM credentials").fetchall() == [
            ("cred_preflight", 1)
        ]
        assert conn.execute(
            "SELECT encrypted_payload, nonce FROM integration_credentials"
        ).fetchone() == (ciphertext, nonce)
        assert "project_credentials" not in _list_tables(isolated_alembic)
    finally:
        conn.close()


def test_global_account_migration_rejects_active_orphan_without_mutation(
    isolated_alembic: Path,
) -> None:
    _run_alembic(["upgrade", "0025_visible_chromium_profiles"])
    now = "2026-07-24 00:00:00"
    conn = sqlite3.connect(isolated_alembic)
    try:
        _insert_legacy_project(conn, project_id=1, now=now)
        conn.execute(
            """
            INSERT INTO credentials
            (id, project_id, integration_credential_id, credential_ref, provider_key,
             auth_type, auth_method_key, profile_key, status, config_json,
             created_at, updated_at)
            VALUES
            (1, 1, NULL, 'cred_active_orphan', 'openrouter',
             'api-key', 'api_key', 'default', 'connected',
             '{"auth_method_key":"api_key"}', ?, ?)
            """,
            (now, now),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(subprocess.CalledProcessError):
        _run_alembic(["upgrade", "head"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0025_visible_chromium_profiles",
        )
        assert conn.execute("SELECT credential_ref, status FROM credentials").fetchall() == [
            ("cred_active_orphan", "connected")
        ]
        assert "display_name" not in _table_columns(isolated_alembic, "credentials")
        assert "project_credentials" not in _list_tables(isolated_alembic)
    finally:
        conn.close()


def test_global_account_migration_resolves_name_collisions_and_preserves_attachments(
    isolated_alembic: Path,
) -> None:
    from stackos.crypto.aes_gcm import encrypt
    from stackos.crypto.seed import ensure_seed_file

    _run_alembic(["upgrade", "0025_visible_chromium_profiles"])
    seed_path = isolated_alembic.parent.parent / "state" / "seed.bin"
    seed = ensure_seed_file(seed_path)
    now = "2026-07-24 00:00:00"
    conn = sqlite3.connect(isolated_alembic)
    try:
        for project_id in (1, 2):
            _insert_legacy_project(conn, project_id=project_id, now=now)
            ciphertext, nonce = encrypt(
                f'{{"api_key":"legacy-{project_id}"}}'.encode(),
                project_id=project_id,
                kind="openrouter",
                seed=seed,
            )
            _insert_legacy_account(
                conn,
                integration_id=project_id,
                credential_id=project_id,
                project_id=project_id,
                credential_ref=f"cred_collision_{project_id}",
                ciphertext=ciphertext,
                nonce=nonce,
                label="Shared",
                now=now,
            )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "head"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        assert conn.execute(
            "SELECT credential_ref, display_name, display_name_key FROM credentials ORDER BY id"
        ).fetchall() == [
            ("cred_collision_1", "Shared", "shared"),
            ("cred_collision_2", "Shared (2)", "shared (2)"),
        ]
        assert conn.execute(
            "SELECT project_id, credential_id FROM project_credentials ORDER BY project_id"
        ).fetchall() == [(1, 1), (2, 2)]
    finally:
        conn.close()


def test_global_account_migration_refuses_data_collapsing_downgrade(
    isolated_alembic: Path,
) -> None:
    from stackos.crypto.aes_gcm import encrypt
    from stackos.crypto.seed import ensure_seed_file

    _run_alembic(["upgrade", "0025_visible_chromium_profiles"])
    seed = ensure_seed_file(isolated_alembic.parent.parent / "state" / "seed.bin")
    ciphertext, nonce = encrypt(
        b'{"api_key":"legacy"}',
        project_id=1,
        kind="openrouter",
        seed=seed,
    )
    now = "2026-07-24 00:00:00"
    conn = sqlite3.connect(isolated_alembic)
    try:
        _insert_legacy_project(conn, project_id=1, now=now)
        _insert_legacy_account(
            conn,
            integration_id=1,
            credential_id=1,
            project_id=1,
            credential_ref="cred_no_downgrade",
            ciphertext=ciphertext,
            nonce=nonce,
            label="No Downgrade",
            now=now,
        )
        conn.commit()
    finally:
        conn.close()
    _run_alembic(["upgrade", "head"])

    with pytest.raises(subprocess.CalledProcessError):
        _run_alembic(["downgrade", "0025_visible_chromium_profiles"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0026_global_reusable_accounts",
        )
        assert conn.execute("SELECT credential_ref, display_name FROM credentials").fetchall() == [
            ("cred_no_downgrade", "No Downgrade")
        ]
        assert conn.execute(
            "SELECT project_id, credential_id FROM project_credentials"
        ).fetchall() == [(1, 1)]
    finally:
        conn.close()


def test_telegram_cutover_retires_active_bindings_preserves_history(isolated_alembic: Path) -> None:
    from sqlmodel import Session

    from stackos.db.connection import make_engine
    from stackos.db.models import (
        Action,
        ActionCall,
        Credential,
        Plugin,
        Project,
        ProjectCredential,
        Provider,
        Resource,
        ResourceRecord,
    )

    _run_alembic(["upgrade", "0030_durable_action_runtime"])
    engine = make_engine(isolated_alembic)
    with Session(engine) as session:
        session.add(
            Project(id=1, slug="cutover", name="Cutover", domain="example.test", locale="en-US")
        )
        session.add(Plugin(id=1, slug="communications", name="Communications", source="builtin"))
        session.commit()
        session.add(Provider(id=1, plugin_id=1, key="telegram-bot", name="Old Telegram"))
        session.add(Provider(id=2, plugin_id=1, key="slack-bot", name="Slack"))
        session.add(
            Credential(
                id=1,
                provider_key="telegram-bot",
                credential_ref="cred_old_bot",
                display_name="Old bot",
                display_name_key="old bot",
            )
        )
        session.commit()
        session.add(ProjectCredential(project_id=1, credential_id=1))
        session.add(
            Action(
                id=1, plugin_id=1, provider_id=1, key="telegram-bot.message.send", name="Old send"
            )
        )
        session.commit()
        session.add(
            ActionCall(
                id=1,
                project_id=1,
                action_id=1,
                plugin_slug="communications",
                action_key="telegram-bot.message.send",
                provider_key="telegram-bot",
                connector_key="telegram-bot",
                operation="message.send",
                status="success",
                request_json={"text": "historic"},
                response_json={"message_ref": "telegram-message:1:2"},
            )
        )
        for index, key in enumerate(
            [
                "communication-profile",
                "communication-target",
                "communication-message",
                "communication-channel",
            ],
            1,
        ):
            session.add(Resource(id=index, plugin_id=1, key=key, name=key))
        session.commit()
        records = [
            (
                1,
                "mixed",
                {
                    "enabled": True,
                    "provider_facets": {
                        "telegram-bot": {
                            "credential_ref": "cred_old_bot",
                            "webhook_base_url": "https://old.test",
                        },
                        "slack-bot": {"credential_ref": "cred_slack"},
                    },
                },
            ),
            (
                1,
                "old-only",
                {
                    "enabled": True,
                    "provider_facets": {"telegram-bot": {"credential_ref": "cred_old_bot"}},
                },
            ),
            (
                2,
                "destination",
                {
                    "enabled": True,
                    "provider_key": "telegram-bot",
                    "send_policy": {"mode": "explicit-target"},
                },
            ),
            (
                4,
                "old-channel",
                {
                    "provider_key": "telegram-bot",
                    "surface_ref": "telegram-chat:-100123",
                    "profile_ref": "communication-profile:old-only",
                    "kind": "channel",
                    "ingest_enabled": True,
                    "send_enabled": True,
                },
            ),
            (3, "historic", {"provider_key": "telegram-bot", "text_preview": "Historic receipt"}),
        ]
        for resource_id, external_id, data in records:
            session.add(
                ResourceRecord(
                    project_id=1, resource_id=resource_id, external_id=external_id, data_json=data
                )
            )
        session.commit()
    engine.dispose()
    _run_alembic(["upgrade", "head"])
    _run_alembic(["upgrade", "head"])
    with sqlite3.connect(isolated_alembic) as conn:
        assert conn.execute("SELECT status FROM credentials WHERE id=1").fetchone() == ("revoked",)
        assert conn.execute("SELECT COUNT(*) FROM project_credentials").fetchone() == (0,)
        assert conn.execute("SELECT key FROM providers").fetchall() == [("slack-bot",)]
        assert conn.execute("SELECT COUNT(*) FROM actions").fetchone() == (0,)
        assert conn.execute(
            "SELECT action_key, status FROM action_calls WHERE id=1"
        ).fetchone() == ("telegram-bot.message.send", "success")
        data = {
            key: json.loads(raw)
            for key, raw in conn.execute("SELECT external_id, data_json FROM resource_records")
        }
        assert data["mixed"]["enabled"] is True
        assert data["mixed"]["provider_facets"] == {"slack-bot": {"credential_ref": "cred_slack"}}
        assert data["old-only"]["enabled"] is False
        assert data["destination"]["send_policy"]["mode"] == "deny"
        from stackos.operations.communication_platform.utils import _communication_surface_out

        surface = _communication_surface_out(None, 1, data["old-channel"])
        assert surface.binding_state == "repair-required"
        assert surface.binding_issues == [{"code": "telegram_bot_api_retired"}]
        assert surface.send_enabled is False
        assert surface.ingest_enabled is False
        assert data["historic"] == records[-1][2]


def test_alembic_upgrade_creates_expected_stackos_tables(isolated_alembic: Path) -> None:
    _run_alembic(["upgrade", "head"])
    tables = _list_tables(isolated_alembic)

    assert not (tables & REJECTED_FINANCE_TABLES)
    assert tables == EXPECTED_TABLES, (
        f"Missing: {EXPECTED_TABLES - tables}; Extra: {tables - EXPECTED_TABLES}"
    )
    assert not (tables & LEGACY_TABLES)


def test_alembic_downgrade_then_upgrade_idempotent(isolated_alembic: Path) -> None:
    _run_alembic(["upgrade", "head"])
    _run_alembic(["downgrade", "base"])
    assert _list_tables(isolated_alembic) == set()

    _run_alembic(["upgrade", "head"])
    assert _list_tables(isolated_alembic) == EXPECTED_TABLES


def test_workflow_extension_migration_recovers_partial_table(
    isolated_alembic: Path,
) -> None:
    _run_alembic(["upgrade", "0016_tracker_completion_evidence"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        conn.execute(
            """
            CREATE TABLE workflow_template_extensions (
                id INTEGER NOT NULL,
                project_id INTEGER NOT NULL,
                workflow_key VARCHAR(160) NOT NULL,
                enabled BOOLEAN NOT NULL,
                input_defaults_json JSON,
                selected_context_json JSON,
                required_input_keys_json JSON,
                guardrails_json JSON,
                step_overrides_json JSON,
                metadata_json JSON,
                created_by VARCHAR(200),
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (id),
                UNIQUE (project_id, workflow_key),
                FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "head"])

    columns = _table_columns(isolated_alembic, "workflow_template_extensions")
    assert "template_overrides_json" in columns


def test_browser_replacement_preserves_historic_rows_and_profile_path(
    isolated_alembic: Path,
    tmp_path: Path,
) -> None:
    _run_alembic(["upgrade", "0024_provider_object_references"])
    external_cookie = tmp_path / "main-account-cookie-sentinel"
    external_cookie.write_text("do-not-touch", encoding="utf-8")
    legacy_profile_dir = browser_profile_dir(tmp_path / "data", project_id=1, profile_key="stable")
    legacy_profile_dir.mkdir(parents=True)
    profile_sentinel = legacy_profile_dir / "synthetic-cookie-sentinel"
    profile_sentinel.write_text("preserve-profile", encoding="utf-8")

    conn = sqlite3.connect(isolated_alembic)
    try:
        now = "2026-07-24 00:00:00"
        conn.execute(
            """
            INSERT INTO projects (id, slug, name, domain, locale, is_active, created_at, updated_at)
            VALUES (1, 'browser-migration', 'Browser Migration', 'example.com', 'en-US', 1, ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO browser_profiles
            (id, project_id, profile_key, name, provider, status, profile_ref,
             launch_options_json, created_at, updated_at)
            VALUES (1, 1, 'stable', 'Stable', 'playwright', 'ready',
                    'browser-profile:project-1:stable', ?, ?, ?)
            """,
            ('{"locale":"en-US","args":["--user-data-dir=/private/main"]}', now, now),
        )
        conn.execute(
            """
            INSERT INTO browser_sessions
            (id, project_id, profile_id, session_ref, provider, status, headless,
             started_at, updated_at)
            VALUES (1, 1, 1, 'browser-session:project-1:stable:historic',
                    'playwright', 'stopped', 1, ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO browser_action_receipts
            (id, project_id, profile_id, session_id, session_ref, page_ref,
             operation, method, side_effect_class, status, input_summary_json,
             result_json, created_at, completed_at)
            VALUES (1, 1, 1, 1, 'browser-session:project-1:stable:historic',
                    'browser-session:project-1:stable:historic:page-1',
                    'browser.page.call', 'goto', 'navigation', 'ok',
                    '{"url":"https://historic.example.test"}',
                    '{"status":"ok"}', ?, ?)
            """,
            (now, now),
        )
        conn.commit()
    finally:
        conn.close()

    _run_alembic(["upgrade", "head"])
    _run_alembic(["upgrade", "head"])

    conn = sqlite3.connect(isolated_alembic)
    try:
        options = conn.execute(
            "SELECT launch_options_json FROM browser_profiles WHERE id = 1"
        ).fetchone()
        historic = conn.execute("SELECT headless FROM browser_sessions WHERE id = 1").fetchone()
        receipt = conn.execute(
            "SELECT operation, method, status FROM browser_action_receipts WHERE id = 1"
        ).fetchone()
    finally:
        conn.close()
    assert options is not None
    assert json.loads(options[0]) == {"locale": "en-US"}
    assert historic == (1,)
    assert receipt == ("browser.page.call", "goto", "ok")
    assert BROWSER_PROFILE_DIRNAME == "playwright-chromium"
    assert profile_sentinel.read_text(encoding="utf-8") == "preserve-profile"
    assert external_cookie.read_text(encoding="utf-8") == "do-not-touch"
