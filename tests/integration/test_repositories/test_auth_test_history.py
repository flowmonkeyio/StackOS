"""Account verification evidence survives inventory reload without granting access."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, select

from stackos.auth_providers import AuthRepository
from stackos.db.models import Credential


def test_account_inventory_keeps_latest_test_separate_from_connected_state(
    session: Session, project_id: int
) -> None:
    repo = AuthRepository(session)
    account = repo.store_credential(
        provider_key="firecrawl",
        display_name="Diagnostic history fixture",
        fields={"api_key": "fc-test-fixture"},
        attach_project_id=project_id,
    ).data
    assert repo.get_account(credential_ref=account.credential_ref).last_test is None
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == account.credential_ref)
    ).one()
    result = {
        "credential_ref": account.credential_ref,
        "provider_key": "firecrawl",
        "ok": False,
        "status": "failed",
        "summary": "Permission denied for the probe.",
        "checked_at": datetime.now(UTC).isoformat(),
        "retryable": False,
        "next_action": "Review the provider key permissions and test again.",
        "metadata": {"provider_status_code": 403, "api_key": "must-not-leak"},
    }
    repo.record_usage_event(
        credential=credential,
        provider_key="firecrawl",
        operation="account.test",
        status="failed",
        metadata_json={"ok": False, "metadata": result["metadata"], "result": result},
        project_id=project_id,
    )
    # A later action event is not evidence that the account probe passed.
    repo.record_usage_event(
        credential=credential,
        provider_key="firecrawl",
        operation="action.run",
        status="success",
        metadata_json={},
        project_id=project_id,
    )
    session.commit()
    for scope in (None, project_id):
        loaded = repo.status(project_id=scope, provider_key="firecrawl").accounts[0]
        assert loaded.status == "connected"
        assert loaded.setup_required is False
        assert loaded.last_test is not None
        assert loaded.last_test.ok is False
        assert loaded.last_test.summary == result["summary"]
        assert loaded.last_test.next_action == result["next_action"]
        assert loaded.last_test.retryable is False
        assert "must-not-leak" not in loaded.model_dump_json()
    result.update(ok=True, status="ok", summary="Authenticated", next_action=None)
    repo.record_usage_event(
        credential=credential,
        provider_key="firecrawl",
        operation="account.test",
        status="ok",
        metadata_json={"ok": True, "result": result},
    )
    session.commit()
    latest = repo.get_account(credential_ref=account.credential_ref)
    assert latest.last_test is not None
    assert latest.last_test.ok is True


def test_legacy_test_event_preserves_failure_without_inventing_diagnostics(
    session: Session, project_id: int
) -> None:
    repo = AuthRepository(session)
    account = repo.store_credential(
        provider_key="firecrawl",
        display_name="Legacy diagnostic fixture",
        fields={"api_key": "fc-legacy-fixture"},
        attach_project_id=project_id,
    ).data
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == account.credential_ref)
    ).one()
    repo.record_usage_event(
        credential=credential,
        provider_key="firecrawl",
        operation="account.test",
        status="failed",
        metadata_json={"ok": False, "metadata": {"stage": "test"}},
    )
    session.commit()
    loaded = repo.get_account(credential_ref=account.credential_ref)
    assert loaded.last_test is not None
    assert loaded.last_test.ok is False
    assert loaded.last_test.metadata == {"stage": "test"}
    assert loaded.last_test.next_action == "Test the Account again for current diagnostics."
    assert "permission" not in loaded.last_test.summary.lower()
