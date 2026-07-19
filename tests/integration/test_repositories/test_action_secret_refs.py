from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlmodel import Session, select

from stackos.actions import (
    ActionConnectorError,
    ActionConnectorRegistry,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionRepository,
    ActionValidationIssue,
)
from stackos.auth_providers import AuthRepository
from stackos.db.models import (
    Action,
    ActionCall,
    PayloadSecret,
    Plugin,
    PluginSource,
    Provider,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository, ProjectRepository
from stackos.repositories.secrets import PayloadSecretRepository
from stackos.workflows.run_plan_schema import find_run_plan_secret_paths

CANARY = "transit-canary-7bb97c28dca84d70"


def _api_key_auth_methods() -> dict[str, object]:
    return {
        "auth_methods": [
            {
                "key": "api-key",
                "label": "API key",
                "fields": [
                    {
                        "key": "api_key",
                        "label": "API key",
                        "type": "secret",
                        "required": True,
                    }
                ],
            }
        ]
    }


def _seed_tenant_action(session: Session) -> None:
    plugin = Plugin(
        slug="tenant-actions",
        name="Tenant Actions",
        version="0.1.0",
        description="Multi-tenant action fixtures",
        source=PluginSource.PROJECT,
        manifest_json={},
    )
    session.add(plugin)
    session.flush()
    assert plugin.id is not None
    provider = Provider(
        plugin_id=plugin.id,
        key="tenant-provider",
        name="Tenant Provider",
        auth_type="api-key",
        config_json=_api_key_auth_methods(),
    )
    session.add(provider)
    session.flush()
    assert provider.id is not None
    session.add(
        Action(
            plugin_id=plugin.id,
            provider_id=provider.id,
            key="account.create",
            name="Create tenant account",
            description="Create one account with tenant-owned SMTP configuration.",
            capability_key=None,
            risk_level="write",
            input_schema_json={
                "type": "object",
                "additionalProperties": False,
                "required": ["account", "smtp"],
                "properties": {
                    "account": {"type": "string"},
                    "smtp": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["host", "password"],
                        "properties": {
                            "host": {"type": "string"},
                            "password": {"type": "string"},
                        },
                    },
                },
            },
            output_schema_json={"type": "object", "additionalProperties": True},
            config_json={
                "schema_version": "stackos.action.v1",
                "connector": "tenant.capture",
                "operation": "account.create",
                "requires_credential": True,
            },
        )
    )
    session.commit()


def _provider_credential_ref(session: Session, project_id: int) -> str:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="tenant-provider",
        secret_payload=b"provider-super-admin",
        config_json={"label": "Tenant provider admin"},
    )
    status = AuthRepository(session).status(
        project_id=project_id,
        provider_key="tenant-provider",
    )
    return status.connections[0].credential_ref


class _CapturingConnector:
    key = "tenant.capture"

    def __init__(self) -> None:
        self.calls = 0
        self.validated_passwords: list[object] = []
        self.executed_password: object | None = None
        self.provider_secret: bytes | None = None
        self.request_repr: str | None = None

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        password = request.input_json.get("smtp", {}).get("password")
        self.validated_passwords.append(password)
        if not isinstance(password, str) or not password:
            return [
                ActionValidationIssue(
                    path="$.smtp.password",
                    message="password is required",
                    code="required",
                )
            ]
        return []

    def estimate_cost_cents(self, request: ActionConnectorRequest) -> int:
        del request
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        self.calls += 1
        assert request.credential is not None
        self.provider_secret = request.credential.secret_payload
        self.executed_password = request.input_json["smtp"]["password"]
        self.request_repr = repr(request)
        return ActionConnectorResult(
            output_json={
                "status": "created",
                "echo": f"provider echoed {self.executed_password}",
                "nested": {"value": self.executed_password},
            },
            metadata_json={"provider_note": self.executed_password},
        )


class _FailingConnector(_CapturingConnector):
    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        self.calls += 1
        password = request.input_json["smtp"]["password"]
        raise ActionConnectorError(
            f"provider rejected {password}",
            output_json={"provider_error": f"invalid password: {password}"},
            metadata_json={"provider_debug": password},
        )


def _marker(secret_ref: str) -> dict[str, str]:
    return {"$secret_ref": secret_ref}


def _payload(secret_ref: str) -> dict[str, object]:
    return {
        "account": "tenant-123",
        "smtp": {
            "host": "smtp.example.test",
            "password": _marker(secret_ref),
        },
    }


def test_payload_secret_store_is_encrypted_project_scoped_and_opaque(
    session: Session,
    project_id: int,
) -> None:
    stored = PayloadSecretRepository(session).set(project_id=project_id, value=CANARY)

    assert stored.secret_ref.startswith("secret_")
    assert CANARY not in stored.secret_ref
    row = session.exec(select(PayloadSecret)).one()
    assert CANARY.encode() not in row.encrypted_payload
    assert (
        PayloadSecretRepository(session).resolve(
            project_id=project_id,
            secret_ref=stored.secret_ref,
        )
        == CANARY
    )

    other = (
        ProjectRepository(session)
        .create(
            slug="other-project",
            name="Other Project",
            domain="other.example.test",
            locale="en-US",
        )
        .data
    )
    assert other.id is not None
    with pytest.raises(ValidationError, match="payload secret reference is unavailable"):
        PayloadSecretRepository(session).resolve(
            project_id=other.id,
            secret_ref=stored.secret_ref,
        )


def test_action_ref_stays_symbolic_until_connector_and_redacts_dynamic_values(
    session: Session,
    project_id: int,
) -> None:
    _seed_tenant_action(session)
    credential_ref = _provider_credential_ref(session, project_id)
    stored = PayloadSecretRepository(session).set(project_id=project_id, value=CANARY)
    connector = _CapturingConnector()
    registry = ActionConnectorRegistry()
    registry.register(connector)
    repo = ActionRepository(session, connectors=registry)

    validation = repo.validate(
        project_id=project_id,
        action_ref="tenant-actions.account.create",
        input_json=_payload(stored.secret_ref),
        credential_ref=credential_ref,
    )
    assert validation.valid is True
    assert connector.validated_passwords
    assert CANARY not in json.dumps(connector.validated_passwords)

    out = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="tenant-actions.account.create",
            input_json=_payload(stored.secret_ref),
            credential_ref=credential_ref,
            idempotency_key="tenant-account-create",
        )
    ).data

    assert connector.calls == 1
    assert connector.provider_secret == b"provider-super-admin"
    assert connector.executed_password == CANARY
    assert connector.request_repr is not None
    assert CANARY not in connector.request_repr
    assert out.output_json == {
        "status": "created",
        "echo": "provider echoed [redacted]",
        "nested": {"value": "[redacted]"},
    }
    assert out.metadata_json == {"provider_note": "[redacted]"}

    call = session.exec(select(ActionCall)).one()
    assert call.request_json == _payload(stored.secret_ref)
    assert CANARY not in json.dumps(call.model_dump(mode="json"))

    replay = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="tenant-actions.account.create",
            input_json=_payload(stored.secret_ref),
            credential_ref=credential_ref,
            idempotency_key="tenant-account-create",
        )
    ).data
    assert replay.replayed is True
    assert connector.calls == 1
    assert CANARY not in json.dumps(replay.model_dump(mode="json"))


def test_missing_and_foreign_refs_share_safe_failure_before_connector(
    session: Session,
    project_id: int,
) -> None:
    _seed_tenant_action(session)
    credential_ref = _provider_credential_ref(session, project_id)
    foreign = (
        ProjectRepository(session)
        .create(
            slug="foreign-project",
            name="Foreign Project",
            domain="foreign.example.test",
            locale="en-US",
        )
        .data
    )
    assert foreign.id is not None
    foreign_ref = (
        PayloadSecretRepository(session)
        .set(
            project_id=foreign.id,
            value=CANARY,
        )
        .secret_ref
    )
    connector = _CapturingConnector()
    registry = ActionConnectorRegistry()
    registry.register(connector)
    repo = ActionRepository(session, connectors=registry)

    issue_sets: list[list[dict[str, object]]] = []
    for ref in (foreign_ref, "secret_AAAAAAAAAAAAAAAAAAAAAAAA"):
        validation = repo.validate(
            project_id=project_id,
            action_ref="tenant-actions.account.create",
            input_json=_payload(ref),
            credential_ref=credential_ref,
        )
        assert validation.valid is False
        issue_sets.append([issue.model_dump(mode="json") for issue in validation.issues])
        with pytest.raises(ValidationError, match="action payload is invalid"):
            asyncio.run(
                repo.execute(
                    project_id=project_id,
                    action_ref="tenant-actions.account.create",
                    input_json=_payload(ref),
                    credential_ref=credential_ref,
                )
            )

    assert issue_sets[0] == issue_sets[1]
    assert issue_sets[0] == [
        {
            "path": "$.smtp.password",
            "message": "payload secret reference is unavailable",
            "code": "secret_ref_unavailable",
        }
    ]
    assert connector.calls == 0
    assert CANARY not in json.dumps(issue_sets)


def test_marker_grammar_is_exact_and_raw_secret_values_remain_rejected(
    session: Session,
    project_id: int,
) -> None:
    _seed_tenant_action(session)
    credential_ref = _provider_credential_ref(session, project_id)
    stored = PayloadSecretRepository(session).set(project_id=project_id, value=CANARY)
    repo = ActionRepository(session)

    assert find_run_plan_secret_paths(_payload(stored.secret_ref)) == []

    invalid_passwords: list[object] = [
        CANARY,
        {"$secret_ref": stored.secret_ref, "extra": True},
        {"$secret_ref": 42},
        f"prefix-{stored.secret_ref}",
    ]
    for password in invalid_passwords:
        payload = _payload(stored.secret_ref)
        smtp = payload["smtp"]
        assert isinstance(smtp, dict)
        smtp["password"] = password
        with pytest.raises(ValidationError) as exc_info:
            repo.validate(
                project_id=project_id,
                action_ref="tenant-actions.account.create",
                input_json=payload,
                credential_ref=credential_ref,
            )
        assert "use secret.set and an exact $secret_ref marker" in exc_info.value.detail
        assert "credential_ref is only for provider auth" in exc_info.value.detail


def test_connector_request_repr_omits_input_json() -> None:
    request = ActionConnectorRequest(
        project_id=1,
        plugin_slug="tenant-actions",
        action_key="account.create",
        action_ref="tenant-actions.account.create",
        provider_key="tenant-provider",
        operation="account.create",
        input_json={"smtp": {"password": CANARY}},
        config_json={},
    )

    assert CANARY not in repr(request)
    assert "input_json" not in repr(request)


def test_dynamic_redaction_covers_connector_failure_and_audit(
    session: Session,
    project_id: int,
) -> None:
    _seed_tenant_action(session)
    credential_ref = _provider_credential_ref(session, project_id)
    stored = PayloadSecretRepository(session).set(project_id=project_id, value=CANARY)
    connector = _FailingConnector()
    registry = ActionConnectorRegistry()
    registry.register(connector)
    repo = ActionRepository(session, connectors=registry)

    with pytest.raises(ConflictError) as exc_info:
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref="tenant-actions.account.create",
                input_json=_payload(stored.secret_ref),
                credential_ref=credential_ref,
            )
        )

    assert connector.calls == 1
    assert CANARY not in json.dumps(exc_info.value.data)
    call = session.exec(select(ActionCall)).one()
    assert call.request_json == _payload(stored.secret_ref)
    assert call.response_json == {"provider_error": "invalid password: [redacted]"}
    assert call.metadata_json == {"provider_debug": "[redacted]"}
    assert call.error == "provider rejected [redacted]"
    assert CANARY not in json.dumps(call.model_dump(mode="json"))


def test_dynamic_redaction_covers_file_backed_action_output(
    session: Session,
    project_id: int,
    tmp_path: Path,
) -> None:
    _seed_tenant_action(session)
    credential_ref = _provider_credential_ref(session, project_id)
    stored = PayloadSecretRepository(session).set(project_id=project_id, value=CANARY)
    connector = _CapturingConnector()
    registry = ActionConnectorRegistry()
    registry.register(connector)
    repo = ActionRepository(session, connectors=registry, asset_dir=tmp_path)

    out = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="tenant-actions.account.create",
            input_json=_payload(stored.secret_ref),
            credential_ref=credential_ref,
            output_policy_json={"mode": "always_file", "path": str(tmp_path)},
            default_external_file_output=True,
        )
    ).data

    saved = json.loads(Path(out.output_json["file"]["path"]).read_text(encoding="utf-8"))
    assert saved["request"]["input_json"] == _payload(stored.secret_ref)
    assert saved["response"]["output_json"] == {
        "status": "created",
        "echo": "provider echoed [redacted]",
        "nested": {"value": "[redacted]"},
    }
    assert CANARY not in json.dumps(saved)
    assert CANARY not in json.dumps(out.model_dump(mode="json"))
