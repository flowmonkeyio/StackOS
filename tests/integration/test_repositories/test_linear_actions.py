from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.db.models import (
    Credential,
    CredentialAccount,
    CredentialScope,
    ProjectCredential,
)
from stackos.repositories.base import ConflictError, NotFoundError
from stackos.repositories.plugins import PluginRepository
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository
from tests.integration.account_test_support import seed_test_account

READ_ACTIONS = {
    "viewer.get",
    "teams.list",
    "teams.get",
    "users.list",
    "users.get",
    "workflow_states.list",
    "workflow_states.get",
    "projects.list",
    "projects.get",
    "cycles.list",
    "cycles.get",
    "issue_labels.list",
    "issue_labels.get",
    "issues.list",
    "issues.get",
    "issues.search",
    "comments.list",
    "comments.get",
    "issue_relations.list",
    "issue_relations.get",
}
WRITE_ACTIONS = {
    "issues.create",
    "issues.update",
    "issues.archive",
    "issues.unarchive",
    "issues.labels.add",
    "issues.labels.remove",
    "comments.create",
    "comments.update",
    "comments.resolve",
    "comments.unresolve",
    "comments.delete",
    "issue_relations.create",
    "issue_relations.update",
    "issue_relations.delete",
}


def _linear_credential(
    session: Session,
    *,
    project_id: int,
    account_id: str = "linear-org-1",
    scopes: tuple[str, ...] = ("read", "write"),
    auth_method_key: str = "oauth2_authorization_code",
    secret_payload: bytes | None = None,
) -> str:
    if secret_payload is None:
        secret_payload = json.dumps({"access_token": "linear-secret"}).encode()
    config_json: dict[str, object] = {"auth_method_key": auth_method_key}
    if auth_method_key == "oauth2_authorization_code":
        config_json["scope_status"] = "known"
    seeded = seed_test_account(
        session,
        project_id=project_id,
        provider_key="linear",
        display_name="Linear - Default",
        secret_payload=secret_payload,
        config_json=config_json,
    )
    credential = session.exec(
        select(Credential).where(Credential.integration_credential_id == seeded.data.id)
    ).one()
    credential_ref = credential.credential_ref
    credential.auth_type = "api-key" if auth_method_key == "personal_api_key" else "oauth"
    credential.auth_method_key = auth_method_key
    if auth_method_key == "oauth2_authorization_code":
        credential.config_json = {**(credential.config_json or {}), "scope_status": "known"}
    session.add(credential)
    assert credential.id is not None
    session.add(
        CredentialAccount(
            credential_id=credential.id,
            provider_account_id=account_id,
            display_name="Example Linear",
        )
    )
    if auth_method_key == "oauth2_authorization_code":
        for scope in scopes:
            session.add(CredentialScope(credential_id=credential.id, scope=scope))
    session.commit()
    return credential_ref


def _safe_ref(
    session: Session,
    *,
    credential_ref: str,
    object_type: str,
    provider_id: str,
) -> str:
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    attachment = session.exec(
        select(ProjectCredential).where(
            ProjectCredential.credential_id == credential.id,
        )
    ).one()
    return ProviderObjectReferenceRepository(
        session,
        project_id=attachment.project_id,
    ).upsert(
        credential=credential,
        object_type=f"linear.{object_type}",
        provider_object_id=provider_id,
        display_name=provider_id,
    )


def test_linear_catalog_exposes_exact_fixed_action_set(
    session: Session,
    project_id: int,
) -> None:
    actions = PluginRepository(session).list_actions(
        plugin_slug="linear",
        project_id=project_id,
    )

    assert {action.key for action in actions} == READ_ACTIONS | WRITE_ACTIONS
    assert len(actions) == 34
    for action in actions:
        assert action.provider_key == "linear"
        assert action.config_json["connector"] == "linear"
        assert action.config_json["operation"] == "graphql.fixed"
        linear = action.config_json["linear"]
        assert linear["document"].startswith("graphql/")
        assert linear["document"].endswith(".graphql")
        assert linear["scope"] in {"read", "write"}


def test_linear_personal_api_key_actions_use_saved_method_without_local_scope_gate(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _linear_credential(
        session,
        project_id=project_id,
        scopes=(),
        auth_method_key="personal_api_key",
        secret_payload=b"linear-personal-key-sentinel",
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "viewer": {
                    "id": "user-personal",
                    "name": "Personal Operator",
                    "organization": {"id": "org-personal", "name": "Personal Workspace"},
                }
            }
        },
    )

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="linear.viewer.get",
            input_json={},
            credential_ref=credential_ref,
        )
    )

    request = httpx_mock.get_requests()[0]
    rendered = json.dumps(result.data.model_dump(mode="json"))
    assert request.headers["Authorization"] == "linear-personal-key-sentinel"
    assert request.headers["Authorization"] != "Bearer linear-personal-key-sentinel"
    assert "linear-personal-key-sentinel" not in rendered


def test_linear_team_list_uses_bounded_pagination_and_mints_safe_refs(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _linear_credential(session, project_id=project_id)
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "teams": {
                    "nodes": [
                        {
                            "id": "team-provider-1",
                            "name": "Engineering",
                            "key": "ENG",
                        }
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        },
    )

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="linear.teams.list",
            input_json={
                "first": 25,
                "after": "cursor-1",
                "include_archived": True,
                "order_by": "updatedAt",
            },
            credential_ref=credential_ref,
        )
    )

    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body["variables"] == {
        "first": 25,
        "after": "cursor-1",
        "includeArchived": True,
        "orderBy": "updatedAt",
    }
    node = result.data.output_json["data"]["teams"]["nodes"][0]
    assert node["team_ref"].startswith("provider-object:")
    assert "id" not in node
    assert "team-provider-1" not in json.dumps(result.data.output_json)


def test_linear_issue_filters_compile_only_from_typed_account_refs(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _linear_credential(session, project_id=project_id)
    team_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="team",
        provider_id="team-1",
    )
    user_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="user",
        provider_id="user-1",
    )
    state_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="workflow-state",
        provider_id="state-1",
    )
    label_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="issue-label",
        provider_id="label-1",
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "issues": {
                    "nodes": [
                        {
                            "id": "issue-1",
                            "identifier": "ENG-1",
                            "title": "Ship Linear",
                            "team": {"id": "team-1", "name": "Engineering", "key": "ENG"},
                            "state": {"id": "state-1", "name": "In Progress", "type": "started"},
                        }
                    ],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        },
    )

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="linear.issues.list",
            input_json={
                "team_ref": team_ref,
                "assignee_ref": user_ref,
                "workflow_state_ref": state_ref,
                "issue_label_ref": label_ref,
                "created_after": "2026-07-01T00:00:00Z",
                "updated_before": "2026-07-31T23:59:59Z",
                "first": 50,
            },
            credential_ref=credential_ref,
        )
    )

    variables = json.loads(httpx_mock.get_requests()[0].content)["variables"]
    assert variables == {
        "filter": {
            "team": {"id": {"eq": "team-1"}},
            "assignee": {"id": {"eq": "user-1"}},
            "state": {"id": {"eq": "state-1"}},
            "labels": {"id": {"eq": "label-1"}},
            "createdAt": {"gte": "2026-07-01T00:00:00Z"},
            "updatedAt": {"lte": "2026-07-31T23:59:59Z"},
        },
        "first": 50,
    }
    issue = result.data.output_json["data"]["issues"]["nodes"][0]
    assert issue["issue_ref"].startswith("provider-object:")
    assert issue["team"]["team_ref"].startswith("provider-object:")
    assert issue["state"]["workflow_state_ref"].startswith("provider-object:")
    assert '"id"' not in json.dumps(result.data.output_json)


def test_linear_comment_and_relation_lists_are_issue_scoped(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _linear_credential(session, project_id=project_id)
    issue_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="issue",
        provider_id="issue-1",
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "comments": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "issue": {
                    "id": "issue-1",
                    "identifier": "ENG-1",
                    "relations": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    },
                }
            }
        },
    )

    repo = ActionRepository(session)
    asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="linear.comments.list",
            input_json={"issue_ref": issue_ref},
            credential_ref=credential_ref,
        )
    )
    asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="linear.issue_relations.list",
            input_json={"issue_ref": issue_ref},
            credential_ref=credential_ref,
        )
    )

    comment_body = json.loads(httpx_mock.get_requests()[0].content)
    relation_body = json.loads(httpx_mock.get_requests()[1].content)
    assert comment_body["variables"] == {"issueId": "issue-1", "first": 50}
    assert "comments(" in comment_body["query"]
    assert relation_body["variables"] == {"issueId": "issue-1", "first": 50}
    assert "issue(id: $issueId)" in relation_body["query"]
    assert "issueRelations(" not in relation_body["query"]


def test_linear_wrong_type_ref_fails_before_provider_dispatch(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _linear_credential(session, project_id=project_id)
    team_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="team",
        provider_id="team-1",
    )

    with pytest.raises(ConflictError) as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="linear.issues.get",
                input_json={"issue_ref": team_ref},
                credential_ref=credential_ref,
            )
        )

    assert "object type" in exc.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_linear_issue_create_and_update_resolve_refs_and_preserve_patch_semantics(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _linear_credential(session, project_id=project_id)
    team_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="team",
        provider_id="team-1",
    )
    issue_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="issue",
        provider_id="issue-1",
    )
    user_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="user",
        provider_id="user-1",
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "issueCreate": {
                    "success": True,
                    "issue": {
                        "id": "issue-created",
                        "identifier": "ENG-2",
                        "title": "Created",
                        "team": {"id": "team-1", "name": "Engineering", "key": "ENG"},
                    },
                }
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "issueUpdate": {
                    "success": True,
                    "issue": {
                        "id": "issue-1",
                        "identifier": "ENG-1",
                        "title": "Updated",
                        "assignee": None,
                    },
                }
            }
        },
    )

    repo = ActionRepository(session)
    created = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="linear.issues.create",
            input_json={
                "team_ref": team_ref,
                "title": "Created",
                "assignee_ref": user_ref,
                "priority": 2,
            },
            credential_ref=credential_ref,
        )
    )
    updated = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="linear.issues.update",
            input_json={
                "issue_ref": issue_ref,
                "assignee_ref": None,
                "description": None,
            },
            credential_ref=credential_ref,
        )
    )

    create_variables = json.loads(httpx_mock.get_requests()[0].content)["variables"]
    update_variables = json.loads(httpx_mock.get_requests()[1].content)["variables"]
    assert create_variables == {
        "input": {
            "teamId": "team-1",
            "title": "Created",
            "assigneeId": "user-1",
            "priority": 2,
        }
    }
    assert update_variables == {
        "id": "issue-1",
        "input": {"assigneeId": None, "description": None},
    }
    assert "title" not in update_variables["input"]
    assert created.data.output_json["data"]["issueCreate"]["issue"]["issue_ref"].startswith(
        "provider-object:"
    )
    assert updated.data.output_json["data"]["issueUpdate"]["issue"]["issue_ref"].startswith(
        "provider-object:"
    )


def test_linear_comment_and_relation_mutations_use_only_safe_refs(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _linear_credential(session, project_id=project_id)
    issue_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="issue",
        provider_id="issue-1",
    )
    related_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="issue",
        provider_id="issue-2",
    )
    comment_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="comment",
        provider_id="comment-1",
    )
    relation_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="issue-relation",
        provider_id="relation-1",
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "commentCreate": {
                    "success": True,
                    "comment": {
                        "id": "comment-created",
                        "body": "Hello",
                        "issue": {"id": "issue-1", "identifier": "ENG-1"},
                    },
                }
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "issueRelationCreate": {
                    "success": True,
                    "issueRelation": {
                        "id": "relation-created",
                        "type": "blocks",
                        "issue": {"id": "issue-1", "identifier": "ENG-1"},
                        "relatedIssue": {"id": "issue-2", "identifier": "ENG-2"},
                    },
                }
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "commentUpdate": {
                    "success": True,
                    "comment": {"id": "comment-1", "body": "Updated"},
                }
            }
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "issueRelationUpdate": {
                    "success": True,
                    "issueRelation": {"id": "relation-1", "type": "related"},
                }
            }
        },
    )

    repo = ActionRepository(session)
    asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="linear.comments.create",
            input_json={"issue_ref": issue_ref, "body": "Hello"},
            credential_ref=credential_ref,
        )
    )
    asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="linear.issue_relations.create",
            input_json={
                "issue_ref": issue_ref,
                "related_issue_ref": related_ref,
                "relation_type": "blocks",
            },
            credential_ref=credential_ref,
        )
    )
    asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="linear.comments.update",
            input_json={"comment_ref": comment_ref, "body": "Updated"},
            credential_ref=credential_ref,
        )
    )
    asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="linear.issue_relations.update",
            input_json={"relation_ref": relation_ref, "relation_type": "related"},
            credential_ref=credential_ref,
        )
    )

    requests = [json.loads(request.content)["variables"] for request in httpx_mock.get_requests()]
    assert requests == [
        {"input": {"issueId": "issue-1", "body": "Hello"}},
        {
            "input": {
                "issueId": "issue-1",
                "relatedIssueId": "issue-2",
                "type": "blocks",
            }
        },
        {"id": "comment-1", "input": {"body": "Updated"}},
        {"id": "relation-1", "input": {"type": "related"}},
    ]


@pytest.mark.parametrize(
    ("action_ref", "input_json"),
    [
        ("linear.issues.get", {"id": "issue-1"}),
        ("linear.issues.list", {"filter": {"team": {"id": {"eq": "team-1"}}}}),
        ("linear.issues.search", {"term": "ship", "sort": "priority"}),
        (
            "linear.issues.create",
            {"team_id": "team-1", "title": "Raw provider id"},
        ),
        (
            "linear.comments.create",
            {"issue_ref": "provider-object:fake", "body": "x", "create_as_user": "Ada"},
        ),
        (
            "linear.issue_relations.update",
            {"relation_ref": "provider-object:fake", "relation_type": "unknown"},
        ),
    ],
)
def test_linear_action_schemas_reject_raw_provider_and_internal_inputs(
    session: Session,
    project_id: int,
    action_ref: str,
    input_json: dict[str, object],
) -> None:
    credential_ref = _linear_credential(session, project_id=project_id)

    validation = ActionRepository(session).validate(
        project_id=project_id,
        action_ref=action_ref,
        input_json=input_json,
        credential_ref=credential_ref,
    )

    assert validation.valid is False


@pytest.mark.parametrize(
    ("scopes", "action_ref", "needs_issue_ref"),
    [
        (("write",), "linear.viewer.get", False),
        (("read",), "linear.issues.update", True),
    ],
)
def test_linear_direct_actions_fail_closed_on_missing_exact_scope(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    scopes: tuple[str, ...],
    action_ref: str,
    needs_issue_ref: bool,
) -> None:
    credential_ref = _linear_credential(
        session,
        project_id=project_id,
        scopes=scopes,
    )
    input_json: dict[str, object] = {}
    if needs_issue_ref:
        input_json = {
            "issue_ref": _safe_ref(
                session,
                credential_ref=credential_ref,
                object_type="issue",
                provider_id="issue-1",
            ),
            "title": "Denied",
        }
    repo = ActionRepository(session)

    validation = repo.validate(
        project_id=project_id,
        action_ref=action_ref,
        input_json=input_json,
        credential_ref=credential_ref,
    )

    # Generic validation confirms shape and credential identity. Exact granted
    # scope is enforced when daemon-held material resolves for execution.
    assert validation.valid is True
    with pytest.raises(ConflictError) as exc:
        asyncio.run(
            repo.execute(
                project_id=project_id,
                action_ref=action_ref,
                input_json=input_json,
                credential_ref=credential_ref,
            )
        )
    assert exc.value.data["missing_scopes"]
    assert httpx_mock.get_requests() == []


def test_linear_generic_idempotency_replays_without_second_mutation(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _linear_credential(session, project_id=project_id)
    issue_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="issue",
        provider_id="issue-1",
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.linear.app/graphql",
        json={
            "data": {
                "issueArchive": {
                    "success": True,
                    "entity": {
                        "id": "issue-1",
                        "identifier": "ENG-1",
                        "archivedAt": "2026-07-23T00:00:00Z",
                    },
                }
            }
        },
    )
    repo = ActionRepository(session)

    first = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="linear.issues.archive",
            input_json={"issue_ref": issue_ref},
            credential_ref=credential_ref,
            idempotency_key="archive-eng-1",
        )
    )
    second = asyncio.run(
        repo.execute(
            project_id=project_id,
            action_ref="linear.issues.archive",
            input_json={"issue_ref": issue_ref},
            credential_ref=credential_ref,
            idempotency_key="archive-eng-1",
        )
    )

    assert first.data.replayed is False
    assert second.data.replayed is True
    assert len(httpx_mock.get_requests()) == 1


def test_linear_write_timeout_surfaces_outcome_unknown_without_retry(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _linear_credential(session, project_id=project_id)
    issue_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="issue",
        provider_id="issue-1",
    )
    httpx_mock.add_exception(httpx.ReadTimeout("ambiguous write timeout"))

    with pytest.raises(ConflictError) as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="linear.issues.update",
                input_json={"issue_ref": issue_ref, "title": "May have updated"},
                credential_ref=credential_ref,
            )
        )

    assert len(httpx_mock.get_requests()) == 1
    assert exc.value.data["provider_error"]["reason_code"] == "transport_failure"
    assert exc.value.data["provider_error"]["outcome_unknown"] is True


def test_linear_removed_and_raw_graphql_actions_are_absent(
    session: Session,
    project_id: int,
) -> None:
    repo = ActionRepository(session)

    for action_ref in (
        "linear.issueDelete",
        "linear.issues.delete",
        "linear.graphql",
        "linear.raw_graphql",
    ):
        with pytest.raises(NotFoundError):
            repo.validate(
                project_id=project_id,
                action_ref=action_ref,
                input_json={},
            )
