from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from graphql import (
    NoDeprecatedCustomRule,
    build_client_schema,
    parse,
    specified_rules,
    validate,
)
from graphql.language import OperationDefinitionNode

from stackos.actions.linear import LINEAR_ACTION_SPECS

PLUGIN_ROOT = Path(__file__).parents[2] / "plugins" / "linear"
GRAPHQL_ROOT = PLUGIN_ROOT / "graphql"
SCHEMA_ROOT = PLUGIN_ROOT / "schema"
SCHEMA_FILE = SCHEMA_ROOT / "introspection-2026-07-23.json"
METADATA_FILE = SCHEMA_ROOT / "introspection-2026-07-23.metadata.json"
ROOT_POLICY_FILE = SCHEMA_ROOT / "root-policy-2026-07-23.json"

ACTION_DOCUMENTS = {
    "linear.viewer.get": ("viewer/get.graphql", "viewer"),
    "linear.teams.list": ("teams/list.graphql", "teams"),
    "linear.teams.get": ("teams/get.graphql", "team"),
    "linear.users.list": ("users/list.graphql", "users"),
    "linear.users.get": ("users/get.graphql", "user"),
    "linear.workflow_states.list": ("workflow-states/list.graphql", "workflowStates"),
    "linear.workflow_states.get": ("workflow-states/get.graphql", "workflowState"),
    "linear.projects.list": ("projects/list.graphql", "projects"),
    "linear.projects.get": ("projects/get.graphql", "project"),
    "linear.cycles.list": ("cycles/list.graphql", "cycles"),
    "linear.cycles.get": ("cycles/get.graphql", "cycle"),
    "linear.issue_labels.list": ("issue-labels/list.graphql", "issueLabels"),
    "linear.issue_labels.get": ("issue-labels/get.graphql", "issueLabel"),
    "linear.issues.list": ("issues/list.graphql", "issues"),
    "linear.issues.get": ("issues/get.graphql", "issue"),
    "linear.issues.search": ("issues/search.graphql", "searchIssues"),
    "linear.comments.list": ("comments/list.graphql", "comments"),
    "linear.comments.get": ("comments/get.graphql", "comment"),
    # Relation listing is intentionally issue-scoped. The workspace-wide
    # issueRelations root cannot filter by issue.
    "linear.issue_relations.list": ("issue-relations/list.graphql", "issue"),
    "linear.issue_relations.get": ("issue-relations/get.graphql", "issueRelation"),
    "linear.issues.create": ("issues/create.graphql", "issueCreate"),
    "linear.issues.update": ("issues/update.graphql", "issueUpdate"),
    "linear.issues.archive": ("issues/archive.graphql", "issueArchive"),
    "linear.issues.unarchive": ("issues/unarchive.graphql", "issueUnarchive"),
    "linear.issues.labels.add": ("issues/add-label.graphql", "issueAddLabel"),
    "linear.issues.labels.remove": ("issues/remove-label.graphql", "issueRemoveLabel"),
    "linear.comments.create": ("comments/create.graphql", "commentCreate"),
    "linear.comments.update": ("comments/update.graphql", "commentUpdate"),
    "linear.comments.resolve": ("comments/resolve.graphql", "commentResolve"),
    "linear.comments.unresolve": ("comments/unresolve.graphql", "commentUnresolve"),
    "linear.comments.delete": ("comments/delete.graphql", "commentDelete"),
    "linear.issue_relations.create": (
        "issue-relations/create.graphql",
        "issueRelationCreate",
    ),
    "linear.issue_relations.update": (
        "issue-relations/update.graphql",
        "issueRelationUpdate",
    ),
    "linear.issue_relations.delete": (
        "issue-relations/delete.graphql",
        "issueRelationDelete",
    ),
}

AUTH_DOCUMENTS = {
    "linear.auth.viewer_organization": ("auth/viewer-organization.graphql", "viewer"),
}


def _load_snapshot() -> dict[str, Any]:
    return json.loads(SCHEMA_FILE.read_text())


def _operation(document_text: str) -> OperationDefinitionNode:
    definitions = [
        definition
        for definition in parse(document_text).definitions
        if isinstance(definition, OperationDefinitionNode)
    ]
    assert len(definitions) == 1
    return definitions[0]


def _query_type(snapshot: dict[str, Any]) -> dict[str, Any]:
    return next(
        type_data for type_data in snapshot["__schema"]["types"] if type_data["name"] == "Query"
    )


def _validation_errors(snapshot: dict[str, Any], document_text: str) -> list[str]:
    schema = build_client_schema(snapshot)
    errors = validate(
        schema,
        parse(document_text),
        rules=(*specified_rules, NoDeprecatedCustomRule),
    )
    return [error.message for error in errors]


def test_linear_schema_snapshot_metadata_is_complete_and_hashed() -> None:
    metadata = json.loads(METADATA_FILE.read_text())
    schema_bytes = SCHEMA_FILE.read_bytes()

    assert metadata == {
        "format": "graphql-introspection-data-v1",
        "endpoint": "https://api.linear.app/graphql",
        "retrieved_at": "2026-07-23T18:35:54-07:00",
        "retrieval_timezone": "America/Los_Angeles",
        "introspection": {
            "descriptions": False,
            "directive_is_repeatable": True,
            "input_value_deprecation": True,
            "schema_description": False,
            "specified_by_url": True,
        },
        "query_root_count": 163,
        "mutation_root_count": 371,
        "sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "source": "Linear public GraphQL introspection",
        "validator": "graphql-core 3.2",
    }

    snapshot = json.loads(schema_bytes)
    assert len(_query_type(snapshot)["fields"]) == metadata["query_root_count"]
    mutation_type = next(
        type_data for type_data in snapshot["__schema"]["types"] if type_data["name"] == "Mutation"
    )
    assert len(mutation_type["fields"]) == metadata["mutation_root_count"]


def test_exactly_34_fixed_linear_action_documents_validate() -> None:
    assert len(ACTION_DOCUMENTS) == 34
    actual_paths = {
        path.relative_to(GRAPHQL_ROOT).as_posix() for path in GRAPHQL_ROOT.rglob("*.graphql")
    }
    expected_paths = {
        path for path, _root in (*ACTION_DOCUMENTS.values(), *AUTH_DOCUMENTS.values())
    }
    assert actual_paths == expected_paths

    snapshot = _load_snapshot()
    for action_ref, (relative_path, expected_root) in {
        **ACTION_DOCUMENTS,
        **AUTH_DOCUMENTS,
    }.items():
        document_text = (GRAPHQL_ROOT / relative_path).read_text()
        operation = _operation(document_text)
        assert operation.name is not None, action_ref
        assert len(operation.selection_set.selections) == 1, action_ref
        assert operation.selection_set.selections[0].name.value == expected_root, action_ref
        assert _validation_errors(snapshot, document_text) == [], action_ref


def test_unselected_and_forbidden_roots_are_absent_from_fixed_documents() -> None:
    combined = "\n".join(
        (GRAPHQL_ROOT / relative_path).read_text()
        for relative_path, _root in ACTION_DOCUMENTS.values()
    )
    snapshot = _load_snapshot()
    query_fields = {field["name"]: field for field in _query_type(snapshot)["fields"]}

    # issueSearch is current but deliberately excluded by product policy in
    # favor of searchIssues; it must not be described as schema-deprecated.
    assert query_fields["issueSearch"]["isDeprecated"] is False
    assert "issueSearch" not in combined
    assert "searchIssues" in combined
    assert "issueDelete" not in combined
    assert "permanentlyDelete" not in combined
    assert "__schema" not in combined
    assert "__type" not in combined


def test_linear_manifest_connector_and_documents_have_exact_34_action_parity() -> None:
    manifest = yaml.safe_load((PLUGIN_ROOT / "plugin.yaml").read_text())
    manifest_actions = {action["key"]: action for action in manifest["actions"]}
    expected = {
        action_ref.removeprefix("linear."): (document, root)
        for action_ref, (document, root) in ACTION_DOCUMENTS.items()
    }

    assert set(manifest_actions) == set(expected) == set(LINEAR_ACTION_SPECS)
    for action_key, (document, root) in expected.items():
        action = manifest_actions[action_key]
        config = action["config"]
        linear_config = config["linear"]
        spec = LINEAR_ACTION_SPECS[action_key]
        assert config["connector"] == "linear"
        assert config["operation"] == "graphql.fixed"
        assert linear_config == {
            "document": f"graphql/{document}",
            "root": root,
            "scope": spec.scope,
        }
        assert spec.document == linear_config["document"]
        assert spec.root == root
        assert config["required_scopes"] == [spec.scope]
        assert action["risk_level"] == spec.scope


def test_every_current_root_is_partitioned_by_the_linear_policy() -> None:
    snapshot = _load_snapshot()
    policy = json.loads(ROOT_POLICY_FILE.read_text())
    query_roots = {field["name"] for field in _query_type(snapshot)["fields"]}
    mutation_type = next(
        type_data for type_data in snapshot["__schema"]["types"] if type_data["name"] == "Mutation"
    )
    mutation_roots = {field["name"] for field in mutation_type["fields"]}

    allowed_query = set(policy["allowed_query_roots"])
    excluded_query = set(policy["excluded_query_roots"])
    allowed_mutation = set(policy["allowed_mutation_roots"])
    excluded_mutation = set(policy["excluded_mutation_roots"])

    assert policy["schema_sha256"] == json.loads(METADATA_FILE.read_text())["sha256"]
    assert policy["document_action_count"] == len(ACTION_DOCUMENTS)
    assert policy["unique_allowed_root_count"] == 33
    assert allowed_query.isdisjoint(excluded_query)
    assert allowed_mutation.isdisjoint(excluded_mutation)
    assert allowed_query | excluded_query == query_roots
    assert allowed_mutation | excluded_mutation == mutation_roots
    assert len(excluded_query) == 144
    assert len(excluded_mutation) == 357
    assert "issueSearch" in excluded_query
    assert "issueRelations" in excluded_query
    assert "issueDelete" in excluded_mutation


def test_schema_drift_is_additive_or_selected_contract_breaking() -> None:
    snapshot = _load_snapshot()
    viewer_document = (GRAPHQL_ROOT / "viewer/get.graphql").read_text()

    additive_snapshot = copy.deepcopy(snapshot)
    _query_type(additive_snapshot)["fields"].append(
        {
            "name": "linearContractCanary",
            "args": [],
            "type": {"kind": "SCALAR", "name": "String", "ofType": None},
            "isDeprecated": False,
            "deprecationReason": None,
        }
    )
    assert _validation_errors(additive_snapshot, viewer_document) == []

    removed_snapshot = copy.deepcopy(snapshot)
    removed_query = _query_type(removed_snapshot)
    removed_query["fields"] = [
        field for field in removed_query["fields"] if field["name"] != "viewer"
    ]
    assert _validation_errors(removed_snapshot, viewer_document)

    deprecated_snapshot = copy.deepcopy(snapshot)
    deprecated_viewer = next(
        field for field in _query_type(deprecated_snapshot)["fields"] if field["name"] == "viewer"
    )
    deprecated_viewer["isDeprecated"] = True
    deprecated_viewer["deprecationReason"] = "contract drift fixture"
    assert _validation_errors(deprecated_snapshot, viewer_document)
