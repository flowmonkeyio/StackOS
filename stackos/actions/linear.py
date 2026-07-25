"""Curated Linear issue-work GraphQL action connector."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from stackos.actions.connectors import (
    ActionConnectorError,
    ActionConnectorRequest,
    ActionConnectorResult,
    ActionValidationIssue,
)
from stackos.actions.vendor_utils import issue, unknown_operation
from stackos.artifacts import redact_secrets
from stackos.integrations.linear import LinearIntegration
from stackos.mcp.errors import IntegrationDownError, RateLimitedError
from stackos.repositories.base import ValidationError
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository

LINEAR_OPERATION = "graphql.fixed"
LINEAR_DEFAULT_FIRST = 50


@dataclass(frozen=True)
class LinearActionSpec:
    document: str
    root: str
    scope: str
    output_type: str | None = None
    deleted_input_ref: str | None = None

    @property
    def write(self) -> bool:
        return self.scope == "write"


LINEAR_ACTION_SPECS: dict[str, LinearActionSpec] = {
    "viewer.get": LinearActionSpec("graphql/viewer/get.graphql", "viewer", "read", "user"),
    "teams.list": LinearActionSpec("graphql/teams/list.graphql", "teams", "read", "team"),
    "teams.get": LinearActionSpec("graphql/teams/get.graphql", "team", "read", "team"),
    "users.list": LinearActionSpec("graphql/users/list.graphql", "users", "read", "user"),
    "users.get": LinearActionSpec("graphql/users/get.graphql", "user", "read", "user"),
    "workflow_states.list": LinearActionSpec(
        "graphql/workflow-states/list.graphql",
        "workflowStates",
        "read",
        "workflow-state",
    ),
    "workflow_states.get": LinearActionSpec(
        "graphql/workflow-states/get.graphql",
        "workflowState",
        "read",
        "workflow-state",
    ),
    "projects.list": LinearActionSpec(
        "graphql/projects/list.graphql", "projects", "read", "project"
    ),
    "projects.get": LinearActionSpec("graphql/projects/get.graphql", "project", "read", "project"),
    "cycles.list": LinearActionSpec("graphql/cycles/list.graphql", "cycles", "read", "cycle"),
    "cycles.get": LinearActionSpec("graphql/cycles/get.graphql", "cycle", "read", "cycle"),
    "issue_labels.list": LinearActionSpec(
        "graphql/issue-labels/list.graphql",
        "issueLabels",
        "read",
        "issue-label",
    ),
    "issue_labels.get": LinearActionSpec(
        "graphql/issue-labels/get.graphql",
        "issueLabel",
        "read",
        "issue-label",
    ),
    "issues.list": LinearActionSpec("graphql/issues/list.graphql", "issues", "read", "issue"),
    "issues.get": LinearActionSpec("graphql/issues/get.graphql", "issue", "read", "issue"),
    "issues.search": LinearActionSpec(
        "graphql/issues/search.graphql", "searchIssues", "read", "issue"
    ),
    "comments.list": LinearActionSpec(
        "graphql/comments/list.graphql", "comments", "read", "comment"
    ),
    "comments.get": LinearActionSpec("graphql/comments/get.graphql", "comment", "read", "comment"),
    "issue_relations.list": LinearActionSpec(
        "graphql/issue-relations/list.graphql", "issue", "read", "issue"
    ),
    "issue_relations.get": LinearActionSpec(
        "graphql/issue-relations/get.graphql",
        "issueRelation",
        "read",
        "issue-relation",
    ),
    "issues.create": LinearActionSpec(
        "graphql/issues/create.graphql", "issueCreate", "write", "issue"
    ),
    "issues.update": LinearActionSpec(
        "graphql/issues/update.graphql", "issueUpdate", "write", "issue"
    ),
    "issues.archive": LinearActionSpec(
        "graphql/issues/archive.graphql", "issueArchive", "write", "issue"
    ),
    "issues.unarchive": LinearActionSpec(
        "graphql/issues/unarchive.graphql", "issueUnarchive", "write", "issue"
    ),
    "issues.labels.add": LinearActionSpec(
        "graphql/issues/add-label.graphql", "issueAddLabel", "write", "issue"
    ),
    "issues.labels.remove": LinearActionSpec(
        "graphql/issues/remove-label.graphql", "issueRemoveLabel", "write", "issue"
    ),
    "comments.create": LinearActionSpec(
        "graphql/comments/create.graphql", "commentCreate", "write", "comment"
    ),
    "comments.update": LinearActionSpec(
        "graphql/comments/update.graphql", "commentUpdate", "write", "comment"
    ),
    "comments.resolve": LinearActionSpec(
        "graphql/comments/resolve.graphql", "commentResolve", "write", "comment"
    ),
    "comments.unresolve": LinearActionSpec(
        "graphql/comments/unresolve.graphql", "commentUnresolve", "write", "comment"
    ),
    "comments.delete": LinearActionSpec(
        "graphql/comments/delete.graphql",
        "commentDelete",
        "write",
        deleted_input_ref="comment_ref",
    ),
    "issue_relations.create": LinearActionSpec(
        "graphql/issue-relations/create.graphql",
        "issueRelationCreate",
        "write",
        "issue-relation",
    ),
    "issue_relations.update": LinearActionSpec(
        "graphql/issue-relations/update.graphql",
        "issueRelationUpdate",
        "write",
        "issue-relation",
    ),
    "issue_relations.delete": LinearActionSpec(
        "graphql/issue-relations/delete.graphql",
        "issueRelationDelete",
        "write",
        deleted_input_ref="relation_ref",
    ),
}

_GET_REFS = {
    "teams.get": ("team_ref", "team"),
    "users.get": ("user_ref", "user"),
    "workflow_states.get": ("workflow_state_ref", "workflow-state"),
    "projects.get": ("project_ref", "project"),
    "cycles.get": ("cycle_ref", "cycle"),
    "issue_labels.get": ("issue_label_ref", "issue-label"),
    "issues.get": ("issue_ref", "issue"),
    "comments.get": ("comment_ref", "comment"),
    "issue_relations.get": ("relation_ref", "issue-relation"),
}

_LIST_ACTIONS = {
    "teams.list",
    "users.list",
    "workflow_states.list",
    "projects.list",
    "cycles.list",
    "issue_labels.list",
    "issues.list",
    "issues.search",
    "comments.list",
    "issue_relations.list",
}

_ISSUE_PATCH_FIELDS = {
    "title": "title",
    "description": "description",
    "priority": "priority",
    "estimate": "estimate",
    "due_date": "dueDate",
}

_ISSUE_PATCH_REFS = {
    "assignee_ref": ("assigneeId", "user"),
    "parent_issue_ref": ("parentId", "issue"),
    "cycle_ref": ("cycleId", "cycle"),
    "project_ref": ("projectId", "project"),
    "workflow_state_ref": ("stateId", "workflow-state"),
}

_FIELD_OBJECT_TYPES = {
    "viewer": "user",
    "teams": "team",
    "users": "user",
    "workflowStates": "workflow-state",
    "projects": "project",
    "cycles": "cycle",
    "issueLabels": "issue-label",
    "issues": "issue",
    "searchIssues": "issue",
    "comments": "comment",
    "assignee": "user",
    "creator": "user",
    "user": "user",
    "team": "team",
    "state": "workflow-state",
    "project": "project",
    "status": "project-status",
    "cycle": "cycle",
    "labels": "issue-label",
    "issueLabel": "issue-label",
    "issue": "issue",
    "relatedIssue": "issue",
    "comment": "comment",
    "issueRelation": "issue-relation",
    "relations": "issue-relation",
    "organization": "organization",
}

_REF_KEYS = {
    "user": "user_ref",
    "team": "team_ref",
    "workflow-state": "workflow_state_ref",
    "project": "project_ref",
    "project-status": "project_status_ref",
    "cycle": "cycle_ref",
    "issue-label": "issue_label_ref",
    "issue": "issue_ref",
    "comment": "comment_ref",
    "issue-relation": "relation_ref",
    "organization": "organization_ref",
}


class LinearActionConnector:
    """Decision-free adapter for the reviewed Linear fixed-document catalog."""

    key = "linear"

    def validate(self, request: ActionConnectorRequest) -> list[ActionValidationIssue]:
        if request.operation != LINEAR_OPERATION:
            return unknown_operation(request)
        issues: list[ActionValidationIssue] = []
        spec = LINEAR_ACTION_SPECS.get(request.action_key)
        if spec is None:
            issues.append(
                issue(
                    "$.action_key",
                    f"unsupported Linear action {request.action_key!r}",
                    "enum_mismatch",
                )
            )
            return issues
        config = request.config_json.get("linear")
        expected = {
            "document": spec.document,
            "root": spec.root,
            "scope": spec.scope,
        }
        if not isinstance(config, dict):
            issues.append(issue("$.config.linear", "Linear action config is required", "required"))
        else:
            for key, value in expected.items():
                if config.get(key) != value:
                    issues.append(
                        issue(
                            f"$.config.linear.{key}",
                            f"Linear {key} must match the fixed connector contract",
                            "contract_mismatch",
                        )
                    )
        if request.action_key == "issues.update":
            patch_keys = set(_ISSUE_PATCH_FIELDS) | set(_ISSUE_PATCH_REFS)
            if not any(key in request.input_json for key in patch_keys):
                issues.append(
                    issue(
                        "$",
                        "issues.update requires at least one reviewed patch field",
                        "required",
                    )
                )
        return issues

    def estimate_cost_cents(self, _request: ActionConnectorRequest) -> int:
        return 0

    async def execute(self, request: ActionConnectorRequest) -> ActionConnectorResult:
        if request.operation != LINEAR_OPERATION:
            raise ValidationError(f"unsupported Linear operation {request.operation!r}")
        spec = LINEAR_ACTION_SPECS.get(request.action_key)
        if spec is None:
            raise ValidationError(f"unsupported Linear action {request.action_key!r}")
        if request.credential is None:
            raise ValidationError("Linear action requires a resolved credential")
        if request.session is None:
            raise ValidationError("Linear action requires a repository session")

        refs = ProviderObjectReferenceRepository(
            request.session,
            project_id=request.project_id,
        )
        variables = _variables_for_action(request, refs)
        async with httpx.AsyncClient(timeout=60.0) as http:
            integration = LinearIntegration(
                payload=request.credential.secret_payload,
                project_id=request.project_id,
                http=http,
                auth_method_key=request.credential.credential.auth_method_key,
            )
            try:
                result = await integration.execute_document(
                    document_path=spec.document,
                    variables=variables or None,
                    op=f"action.{request.action_key}",
                    write=spec.write,
                )
            except (IntegrationDownError, RateLimitedError) as exc:
                raise _connector_error(
                    exc,
                    request=request,
                    spec=spec,
                ) from exc

        body = result.data
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            raise ValidationError("Linear connector received an invalid transport result")
        safe_data = _safe_output(
            body["data"],
            refs=refs,
            credential=request.credential.credential,
            object_type=None,
            entity_type=spec.output_type,
        )
        if not isinstance(safe_data, dict):
            raise ValidationError("Linear connector could not normalize provider output")
        if spec.deleted_input_ref:
            root = safe_data.get(spec.root)
            if isinstance(root, dict):
                root["deleted_ref"] = request.input_json[spec.deleted_input_ref]
        metadata = {
            "vendor": "linear",
            "operation": request.action_key,
            "schema_ref": spec.document,
            "schema_operation": spec.root,
            **(result.metadata or {}),
        }
        return ActionConnectorResult(
            output_json={
                "provider": "linear",
                "operation": request.action_key,
                "data": safe_data,
            },
            metadata_json=metadata,
        )


def _variables_for_action(
    request: ActionConnectorRequest,
    refs: ProviderObjectReferenceRepository,
) -> dict[str, Any]:
    action = request.action_key
    payload = request.input_json
    credential = request.credential
    assert credential is not None

    if action == "viewer.get":
        return {}
    if action in _GET_REFS:
        key, object_type = _GET_REFS[action]
        return {"id": _resolved_ref(payload[key], object_type, refs=refs, request=request)}
    if action in _LIST_ACTIONS:
        variables = _pagination_variables(payload)
        if action == "users.list" and "include_disabled" in payload:
            variables["includeDisabled"] = payload["include_disabled"]
        if action in {"comments.list", "issue_relations.list"}:
            variables["issueId"] = _resolved_ref(
                payload["issue_ref"],
                "issue",
                refs=refs,
                request=request,
            )
        filter_value = _filter_for_action(action, payload, refs=refs, request=request)
        if filter_value:
            variables["filter"] = filter_value
        if action == "issues.search":
            variables["term"] = payload["term"]
        return variables

    if action == "issues.create":
        issue_input = {
            "teamId": _resolved_ref(payload["team_ref"], "team", refs=refs, request=request),
            "title": payload["title"],
        }
        _copy_issue_patch(issue_input, payload, refs=refs, request=request)
        if "issue_label_refs" in payload:
            issue_input["labelIds"] = [
                _resolved_ref(value, "issue-label", refs=refs, request=request)
                for value in payload["issue_label_refs"]
            ]
        return {"input": issue_input}
    if action == "issues.update":
        update_input: dict[str, Any] = {}
        _copy_issue_patch(update_input, payload, refs=refs, request=request)
        return {
            "id": _resolved_ref(payload["issue_ref"], "issue", refs=refs, request=request),
            "input": update_input,
        }
    if action in {"issues.archive", "issues.unarchive"}:
        return {"id": _resolved_ref(payload["issue_ref"], "issue", refs=refs, request=request)}
    if action in {"issues.labels.add", "issues.labels.remove"}:
        return {
            "id": _resolved_ref(payload["issue_ref"], "issue", refs=refs, request=request),
            "labelId": _resolved_ref(
                payload["issue_label_ref"],
                "issue-label",
                refs=refs,
                request=request,
            ),
        }
    if action == "comments.create":
        comment_input = {
            "issueId": _resolved_ref(payload["issue_ref"], "issue", refs=refs, request=request),
            "body": payload["body"],
        }
        if "parent_comment_ref" in payload:
            comment_input["parentId"] = _resolved_nullable_ref(
                payload["parent_comment_ref"],
                "comment",
                refs=refs,
                request=request,
            )
        return {"input": comment_input}
    if action == "comments.update":
        return {
            "id": _resolved_ref(payload["comment_ref"], "comment", refs=refs, request=request),
            "input": {"body": payload["body"]},
        }
    if action in {"comments.resolve", "comments.unresolve", "comments.delete"}:
        return {"id": _resolved_ref(payload["comment_ref"], "comment", refs=refs, request=request)}
    if action == "issue_relations.create":
        return {
            "input": {
                "issueId": _resolved_ref(payload["issue_ref"], "issue", refs=refs, request=request),
                "relatedIssueId": _resolved_ref(
                    payload["related_issue_ref"],
                    "issue",
                    refs=refs,
                    request=request,
                ),
                "type": payload["relation_type"],
            }
        }
    if action == "issue_relations.update":
        return {
            "id": _resolved_ref(
                payload["relation_ref"],
                "issue-relation",
                refs=refs,
                request=request,
            ),
            "input": {"type": payload["relation_type"]},
        }
    if action == "issue_relations.delete":
        return {
            "id": _resolved_ref(
                payload["relation_ref"],
                "issue-relation",
                refs=refs,
                request=request,
            )
        }
    raise ValidationError(f"unsupported Linear action {action!r}")


def _pagination_variables(payload: dict[str, Any]) -> dict[str, Any]:
    variables: dict[str, Any] = {"first": payload.get("first", LINEAR_DEFAULT_FIRST)}
    for input_key, variable_key in (
        ("after", "after"),
        ("include_archived", "includeArchived"),
        ("order_by", "orderBy"),
    ):
        if input_key in payload:
            variables[variable_key] = payload[input_key]
    return variables


def _filter_for_action(
    action: str,
    payload: dict[str, Any],
    *,
    refs: ProviderObjectReferenceRepository,
    request: ActionConnectorRequest,
) -> dict[str, Any]:
    if action not in {
        "workflow_states.list",
        "projects.list",
        "cycles.list",
        "issue_labels.list",
        "issues.list",
        "issues.search",
    }:
        return {}
    filter_value: dict[str, Any] = {}
    if "team_ref" in payload:
        provider_id = _resolved_ref(payload["team_ref"], "team", refs=refs, request=request)
        key = "accessibleTeams" if action == "projects.list" else "team"
        filter_value[key] = {"id": {"eq": provider_id}}
    if action in {"issues.list", "issues.search"}:
        for input_key, provider_key, object_type in (
            ("assignee_ref", "assignee", "user"),
            ("workflow_state_ref", "state", "workflow-state"),
            ("project_ref", "project", "project"),
            ("cycle_ref", "cycle", "cycle"),
            ("issue_label_ref", "labels", "issue-label"),
        ):
            if input_key in payload:
                provider_id = _resolved_ref(
                    payload[input_key],
                    object_type,
                    refs=refs,
                    request=request,
                )
                filter_value[provider_key] = {"id": {"eq": provider_id}}
    for input_key, provider_key, operator in (
        ("created_after", "createdAt", "gte"),
        ("created_before", "createdAt", "lte"),
        ("updated_after", "updatedAt", "gte"),
        ("updated_before", "updatedAt", "lte"),
    ):
        if input_key in payload:
            comparator = filter_value.setdefault(provider_key, {})
            comparator[operator] = payload[input_key]
    return filter_value


def _copy_issue_patch(
    target: dict[str, Any],
    payload: dict[str, Any],
    *,
    refs: ProviderObjectReferenceRepository,
    request: ActionConnectorRequest,
) -> None:
    for input_key, provider_key in _ISSUE_PATCH_FIELDS.items():
        if input_key in payload:
            target[provider_key] = payload[input_key]
    for input_key, (provider_key, object_type) in _ISSUE_PATCH_REFS.items():
        if input_key in payload:
            target[provider_key] = _resolved_nullable_ref(
                payload[input_key],
                object_type,
                refs=refs,
                request=request,
            )


def _resolved_nullable_ref(
    value: Any,
    object_type: str,
    *,
    refs: ProviderObjectReferenceRepository,
    request: ActionConnectorRequest,
) -> str | None:
    if value is None:
        return None
    return _resolved_ref(value, object_type, refs=refs, request=request)


def _resolved_ref(
    value: Any,
    object_type: str,
    *,
    refs: ProviderObjectReferenceRepository,
    request: ActionConnectorRequest,
) -> str:
    if request.credential is None:
        raise ValidationError("Linear action requires a resolved credential")
    resolved = refs.resolve(
        credential=request.credential.credential,
        safe_ref=value,
        expected_object_type=f"linear.{object_type}",
    )
    return resolved.provider_object_id


def _safe_output(
    value: Any,
    *,
    refs: ProviderObjectReferenceRepository,
    credential: Any,
    object_type: str | None,
    entity_type: str | None,
) -> Any:
    if isinstance(value, list):
        return [
            _safe_output(
                item,
                refs=refs,
                credential=credential,
                object_type=object_type,
                entity_type=entity_type,
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    provider_id = value.get("id")
    if object_type and isinstance(provider_id, str) and provider_id.strip():
        ref_key = _REF_KEYS[object_type]
        normalized[ref_key] = refs.upsert(
            credential=credential,
            object_type=f"linear.{object_type}",
            provider_object_id=provider_id,
            display_name=_display_name(value),
            metadata_json=_safe_ref_metadata(value),
        )
    for key, item in value.items():
        if key in {"id", "entityId"}:
            continue
        if key == "nodes":
            child_type = object_type
        elif key == "entity":
            child_type = entity_type
        elif key == "parent":
            child_type = "comment" if object_type == "comment" else None
            if object_type == "issue":
                child_type = "issue"
        else:
            child_type = _FIELD_OBJECT_TYPES.get(key)
        normalized[key] = _safe_output(
            item,
            refs=refs,
            credential=credential,
            object_type=child_type,
            entity_type=entity_type,
        )
    return normalized


def _display_name(value: dict[str, Any]) -> str | None:
    for key in ("name", "title", "identifier", "key", "displayName"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()[:500]
    return None


def _safe_ref_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key in {"identifier", "key", "name", "title", "type"}
        and isinstance(item, (str, int, float, bool))
    }


def _connector_error(
    exc: IntegrationDownError | RateLimitedError,
    *,
    request: ActionConnectorRequest,
    spec: LinearActionSpec,
) -> ActionConnectorError:
    data = dict(exc.data) if isinstance(exc.data, dict) else {}
    status = data.get("status")
    provider_status_code = status if isinstance(status, int) else None
    provider_error: dict[str, Any] = {
        "reason_code": str(data.get("reason_code") or "provider_failure"),
        "outcome_unknown": bool(data.get("outcome_unknown")),
    }
    for key in ("partial_data", "rate_limit", "retry_after"):
        if key in data:
            provider_error[key] = data[key]
    if isinstance(data.get("provider_error"), dict):
        provider_error["details"] = data["provider_error"]
    safe_provider_error = redact_secrets(provider_error)
    return ActionConnectorError(
        "Linear action failed",
        provider_status_code=provider_status_code,
        provider_error=safe_provider_error,
        output_json={
            "status": "failed",
            "provider_status_code": provider_status_code,
            "provider_error": safe_provider_error,
        },
        metadata_json={
            "vendor": "linear",
            "operation": request.action_key,
            "schema_ref": spec.document,
            "schema_operation": spec.root,
        },
    )


__all__ = [
    "LINEAR_ACTION_SPECS",
    "LINEAR_OPERATION",
    "LinearActionConnector",
    "LinearActionSpec",
]
