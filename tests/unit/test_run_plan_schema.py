"""Unit tests for StackOS run-plan schema."""

from __future__ import annotations

import pytest

from stackos.plugins.manifest import BUILTIN_PLUGIN_MANIFESTS
from stackos.workflows import run_plan_schema as run_plan_schema_module
from stackos.workflows.run_plan_schema import (
    RunPlanSpec,
    find_run_plan_secret_findings,
    find_run_plan_secret_paths,
    run_plan_from_template,
    validate_run_plan_obj,
)
from stackos.workflows.template_loader import LoadedWorkflowTemplate, WorkflowTemplateSummaryOut
from stackos.workflows.template_schema import WorkflowTemplateSpec


def _plan_dict() -> dict:
    return {
        "schema_version": "stackos.run-plan.v1",
        "key": "media.launch.run",
        "title": "Launch Meta Campaign",
        "goal": "Create configured provider objects after approval.",
        "inputs": {"budget": 250},
        "grants": {"credential_ref": "cred_abc"},
        "approvals": [{"key": "launch-review", "title": "Launch review"}],
        "steps": [
            {
                "id": "create-campaign",
                "title": "Create campaign",
                "approval_refs": ["launch-review"],
                "action_refs": ["meta.campaign.create"],
                "action_payloads": {
                    "provider_object_id": "act_123",
                    "campaign": {"name": "Agent selected campaign"},
                    "credential_ref": "cred_abc",
                },
            }
        ],
    }


def test_run_plan_schema_accepts_concrete_provider_payloads() -> None:
    plan = RunPlanSpec.model_validate(_plan_dict())

    assert plan.key == "media.launch.run"
    assert plan.steps[0].action_payloads_json[0]["provider_object_id"] == "act_123"
    assert plan.grant_snapshot_json == {"credential_ref": "cred_abc"}


def test_run_plan_schema_rejects_unknown_approval_refs() -> None:
    data = _plan_dict()
    data["steps"][0]["approval_refs"] = ["missing"]

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "missing" in result.errors[0].message


def test_run_plan_schema_rejects_secrets_but_allows_credential_refs() -> None:
    data = _plan_dict()
    data["steps"][0]["action_payloads"]["api_key"] = "real-secret"

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "must not contain secrets" in result.errors[0].message
    assert "api_key" in result.errors[0].message


@pytest.mark.parametrize("key", ["page_token", "next_page_token"])
def test_generic_run_plan_inputs_treat_token_fields_as_sensitive(key: str) -> None:
    scan = find_run_plan_secret_findings({key: "synthetic-value"})
    assert [finding.kind for finding in scan.findings] == ["key_name"]


def test_run_plan_schema_rejects_unknown_step_tool_grant() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "missing", "tool": "resource.upsert"}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "unknown step" in result.errors[0].message


@pytest.mark.parametrize("field", ["prior_tokens", "cumulative_planning_exposure_tokens"])
def test_approval_accounting_names_remain_secrets_in_generic_run_plan_input(field: str) -> None:
    assert find_run_plan_secret_paths({field: 21525}) == [f"$.{field}"]
    data = _plan_dict()
    data["steps"][0]["action_payloads"][field] = 21525
    result = validate_run_plan_obj(data)
    assert result.valid is False
    assert field in result.errors[0].message


def test_secret_paths_mask_untrusted_keys_and_bound_diagnostics():
    sentinel = "KEY_SENTINEL_7f9d"
    payload = {
        "result_json": {
            "api_key": "VALUE_SENTINEL_1c2e",
            sentinel: {"password": "x"},
            "authorization: " + sentinel: "x",
            sentinel * 1000: {"token": "x"},
            "nested": [{"password": "x"} for _ in range(20)],
        }
    }
    paths = find_run_plan_secret_paths(payload)
    assert "$.result_json.api_key" in paths
    assert sentinel not in str(paths)
    assert 1 <= len(paths) <= 8
    assert all(len(path) <= 256 for path in paths)
    assert sum(map(len, paths)) <= 2048


def test_deep_secret_paths_remain_bounded_without_missing_denials():
    payload = {"api_key": "x"}
    for _ in range(60):
        payload = {"metadata": payload}
    paths = find_run_plan_secret_paths(payload)
    assert paths and all(len(path) <= 256 for path in paths)


@pytest.mark.parametrize(
    ("payload", "kinds"),
    [
        ({"credentials": 21525}, ["key_name"]),
        ({"credentials": True}, ["key_name"]),
        ({"api_key": "Bearer SYNTHETIC_CANARY"}, ["key_name", "value_pattern"]),
        ({"ordinary": {"nested": [{"note": "Bearer SYNTHETIC_CANARY"}]}}, ["value_pattern"]),
        ({"password": {"$secret_ref": "invalid-marker"}}, ["key_name", "key_name"]),
        ({"note": 'log {"authorization":"SYNTHETIC_CANARY"}'}, ["value_pattern"]),
        ({"note": "log {'authorization': 'SYNTHETIC_CANARY'}"}, ["value_pattern"]),
        ({"note": "inline authorization': SYNTHETIC_CANARY"}, ["value_pattern"]),
        ({"note": "inline 'authorization\": SYNTHETIC_CANARY"}, ["value_pattern"]),
        ({"note": "password=SYNTHETIC_CANARY"}, ["value_pattern"]),
        (
            {"note": "-----BEGIN PRIVATE KEY-----SYNTHETIC_CANARY-----END PRIVATE KEY-----"},
            ["value_pattern"],
        ),
        ({"note": "https://example.test/file?sig=SYNTHETIC_CANARY"}, ["value_pattern"]),
    ],
)
def test_secret_findings_classify_causes_without_rejected_content(payload, kinds):
    scan = find_run_plan_secret_findings(payload)
    assert scan.classification_complete is True
    assert [finding.kind for finding in scan.findings] == kinds
    assert scan.paths == find_run_plan_secret_paths(payload)
    assert "SYNTHETIC_CANARY" not in scan.model_dump_json()
    data = _plan_dict()
    data["inputs"] = payload
    assert not validate_run_plan_obj(data).valid


def test_secret_findings_keep_reference_and_workflow_metadata_exemptions():
    payload = {
        "credential_ref": "cred_synthetic",
        "secret_ref": "secret_" + "a" * 32,
        "password": {"$secret_ref": "secret_" + "b" * 32},
    }
    assert find_run_plan_secret_findings(payload).findings == []
    metadata = {"metadata": {"operator_authorization": "Proceed with the reviewed checks."}}
    assert find_run_plan_secret_findings(metadata).findings[0].kind == "key_name"
    assert find_run_plan_secret_findings(metadata, workflow_metadata=True).findings == []


def test_secret_findings_saturate_before_late_value_without_a_second_scan(monkeypatch):
    calls = []
    original = run_plan_schema_module.redact_secret_text

    def observe(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(run_plan_schema_module, "redact_secret_text", observe)
    payload = {f"credential_{index}": "ordinary prose" for index in range(8)}
    payload["nested"] = [{"note": "Bearer SYNTHETIC_CANARY"}]
    scan = find_run_plan_secret_findings(payload)
    assert len(scan.findings) == 8
    assert {finding.kind for finding in scan.findings} == {"key_name"}
    assert scan.classification_complete is False
    assert "Bearer SYNTHETIC_CANARY" not in calls
    assert len(calls) == 7  # The eighth key exhausts the shared budget before its value.
    assert "credential_" not in scan.model_dump_json()
    assert "SYNTHETIC_CANARY" not in scan.model_dump_json()


@pytest.mark.parametrize("count", [7, 8, 20])
def test_secret_findings_conservatively_mark_saturated_diagnostics_incomplete(count):
    payload = {"nested": [{"password": "ordinary prose"} for _ in range(count)]}
    scan = find_run_plan_secret_findings(payload)
    assert len(scan.findings) == min(count, 8)
    assert scan.classification_complete is (count < 8)
    assert all(len(finding.path) <= 256 for finding in scan.findings)


def test_masked_path_cannot_create_operator_metadata_exemption():
    payload = {"steps[0]": {"metadata": {"operator_authorization": "x"}}}
    assert find_run_plan_secret_paths(payload, workflow_metadata=True)


def test_run_plan_preserves_generic_authorization_prose():
    data = _plan_dict()
    data["goal"] = "After operator authorization: inspect the approved environment."
    assert validate_run_plan_obj(data).valid
    assert find_run_plan_secret_paths({"note": data["goal"]}) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"password": "x"},
        {"note": 'inline authorization: "x"'},
        {"note": "inline authorization=x"},
        {"note": "[INFO] authorization: x"},
        {"note": "Authorization: x"},
        {"note": "Bearer x"},
        {"note": "-----BEGIN PRIVATE KEY-----x-----END PRIVATE KEY-----"},
        {"note": "https://example.test/file?sig=x"},
    ],
)
def test_real_credential_syntax_still_rejects_through_run_plan_validator(payload):
    data = _plan_dict()
    data["inputs"] = payload
    assert not validate_run_plan_obj(data).valid
    assert find_run_plan_secret_paths(payload)


@pytest.mark.parametrize("tool_name", ["account.start", "connection.attach", "connection.detach"])
def test_run_plan_schema_rejects_admin_tool_grant(tool_name: str) -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "create-campaign", "tool": tool_name}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "admin/setup tool" in result.errors[0].message


@pytest.mark.parametrize("tool_name", ["workflowTemplate.save", "workflowTemplate.fork"])
def test_template_authoring_remains_setup_not_a_run_plan_grant(tool_name: str) -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "create-campaign", "tool": tool_name}],
    }
    result = validate_run_plan_obj(data)
    assert result.valid is False
    assert "not a run-plan grantable tool" in result.errors[0].message


def test_builtin_runtime_refs_follow_current_manifest_snapshot(monkeypatch) -> None:
    by_slug = {manifest.slug: manifest for manifest in BUILTIN_PLUGIN_MANIFESTS}
    current = [type("Snapshot", (), {"manifests": (by_slug["core"],)})()]
    monkeypatch.setattr(
        run_plan_schema_module,
        "get_builtin_plugin_manifest_snapshot",
        lambda: current[0],
    )

    assert run_plan_schema_module._builtin_plugin_slugs() == {"core"}

    current[0] = type("Snapshot", (), {"manifests": (by_slug["branding"],)})()

    assert run_plan_schema_module._builtin_plugin_slugs() == {"branding"}


def test_run_plan_schema_requires_action_execute_refs() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "create-campaign", "tool": "action.execute"}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "action_refs" in result.errors[0].message


@pytest.mark.parametrize("action_ref", ["utils.web.scrape", "seo.serp.analyze"])
def test_run_plan_schema_accepts_action_execute_with_refs(action_ref: str) -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [
            {
                "step_id": "create-campaign",
                "tool": "action.execute",
                "action_refs": [action_ref],
            }
        ],
    }
    data["steps"][0]["action_refs"] = [action_ref]

    result = validate_run_plan_obj(data)

    assert result.valid is True
    assert result.warnings == []


def test_run_plan_schema_requires_communication_send_targets() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "create-campaign", "tool": "communication.send"}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "targets" in result.errors[0].message


def test_run_plan_schema_accepts_communication_send_with_targets() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [
            {
                "step_id": "create-campaign",
                "tool": "communication.send",
                "targets": ["communication-target:ops-alerts"],
            }
        ],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is True


def test_run_plan_schema_warns_when_action_refs_lack_executable_grants() -> None:
    data = _plan_dict()

    result = validate_run_plan_obj(data)

    assert result.valid is True
    assert result.warnings
    assert result.warnings[0].code == "missing_action_execute_grant"
    assert "action.execute" in result.warnings[0].message


def test_run_plan_schema_builds_template_derived_grants() -> None:
    template = WorkflowTemplateSpec.model_validate(
        {
            "schema_version": "stackos.workflow-template.v1",
            "key": "communications.outbound-notification",
            "name": "Outbound notification",
            "version": "0.1.0",
            "context_requirements": [
                {
                    "id": "recent_requests",
                    "source": "agent_requests",
                    "fields": ["status", "summary"],
                }
            ],
            "action_contracts": [
                {
                    "key": "send_email",
                    "action": "smtp.email.send",
                    "risk_level": "write",
                }
            ],
            "resource_contracts": [
                {
                    "key": "delivery_summary",
                    "resource": "communication-delivery",
                }
            ],
            "outputs": [{"key": "operator_receipt"}],
            "steps": [
                {
                    "id": "notify",
                    "title": "Notify",
                    "context_refs": ["recent_requests"],
                    "action_refs": ["send_email"],
                    "resource_refs": ["delivery_summary"],
                    "output_refs": ["operator_receipt"],
                }
            ],
        }
    )
    plan = run_plan_from_template(
        LoadedWorkflowTemplate(
            summary=WorkflowTemplateSummaryOut(
                key=template.key,
                name=template.name,
                version=template.version,
                source="plugin",
                precedence=10,
                plugin_slug="communications",
            ),
            spec=template,
        )
    )

    result = validate_run_plan_obj(plan.model_dump(mode="json"))

    assert result.valid is True
    assert result.warnings == []
    assert plan.steps[0].action_refs == ["communications.smtp.email.send"]
    grants = plan.grant_snapshot_json["mcp_tool_grants"]
    grant_pairs = {
        (grant["step_id"], tool)
        for grant in grants
        for tool in ([grant["tool"]] if "tool" in grant else grant["tools"])
    }
    assert grant_pairs == {
        ("notify", "action.execute"),
        ("notify", "resource.upsert"),
        ("notify", "context.query"),
        ("notify", "artifact.create"),
        ("notify", "artifact.update"),
        ("notify", "artifact.archive"),
        ("notify", "artifact.supersede"),
    }
    action_grant = next(grant for grant in grants if grant["tool"] == "action.execute")
    resource_grant = next(grant for grant in grants if grant["tool"] == "resource.upsert")
    context_grant = next(grant for grant in grants if grant["tool"] == "context.query")
    assert action_grant["action_refs"] == ["communications.smtp.email.send"]
    assert resource_grant["resource_key"] == "communication-delivery"
    assert context_grant["sources"] == ["agent_requests"]
    assert context_grant["fields"] == ["status", "summary"]


def test_run_plan_context_requirements_keep_source_specific_field_grants() -> None:
    template = WorkflowTemplateSpec.model_validate(
        {
            "schema_version": "stackos.workflow-template.v1",
            "key": "seo.research-context",
            "name": "Research context",
            "version": "0.1.0",
            "context_requirements": [
                {
                    "id": "recent_research",
                    "source": "resources",
                    "fields": ["resource_key", "title", "data_json"],
                },
                {
                    "id": "accepted_learnings",
                    "source": "learnings",
                    "fields": ["statement", "confidence"],
                },
            ],
            "steps": [
                {
                    "id": "orient",
                    "title": "Orient",
                    "context_refs": ["recent_research", "accepted_learnings"],
                }
            ],
        }
    )
    plan = run_plan_from_template(
        LoadedWorkflowTemplate(
            summary=WorkflowTemplateSummaryOut(
                key=template.key,
                name=template.name,
                version=template.version,
                source="plugin",
                precedence=10,
                plugin_slug="seo",
            ),
            spec=template,
        )
    )

    grants = [
        grant
        for grant in plan.grant_snapshot_json["mcp_tool_grants"]
        if grant.get("tool") == "context.query"
    ]
    assert grants == [
        {
            "step_id": "orient",
            "tool": "context.query",
            "sources": ["resources"],
            "fields": ["data_json", "resource_key", "title"],
        },
        {
            "step_id": "orient",
            "tool": "context.query",
            "sources": ["learnings"],
            "fields": ["confidence", "statement"],
        },
    ]


def test_run_plan_schema_allows_explicit_artifact_grant_policy_without_output_grants() -> None:
    template = WorkflowTemplateSpec.model_validate(
        {
            "schema_version": "stackos.workflow-template.v1",
            "key": "branding.iterative-content",
            "name": "Iterative content",
            "version": "0.1.0",
            "metadata": {"artifact_grant_policy": "explicit"},
            "steps": [
                {
                    "id": "draft",
                    "title": "Draft",
                    "output_refs": ["draft_output"],
                }
            ],
            "outputs": [{"key": "draft_output", "type": "object"}],
        }
    )
    plan = run_plan_from_template(
        LoadedWorkflowTemplate(
            summary=WorkflowTemplateSummaryOut(
                key=template.key,
                name=template.name,
                version=template.version,
                source="plugin",
                precedence=10,
                plugin_slug="branding",
            ),
            spec=template,
        )
    )

    result = validate_run_plan_obj(plan.model_dump(mode="json"))

    assert result.valid is True
    assert result.warnings == []
    assert plan.grant_snapshot_json["artifact_grant_policy"] == "explicit"
    assert plan.grant_snapshot_json["mcp_tool_grants"] == []


def test_run_plan_schema_requires_communication_reply_sources() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "create-campaign", "tool": "communication.reply"}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "sources" in result.errors[0].message


def test_run_plan_schema_accepts_communication_reply_with_sources() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [
            {
                "step_id": "create-campaign",
                "tool": "communication.reply",
                "sources": ["telegram", "slack-bot"],
            }
        ],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is True


def test_run_plan_schema_requires_context_query_grant_filters() -> None:
    data = _plan_dict()
    data["grants"] = {
        "mcp_tool_grants": [{"step_id": "create-campaign", "tool": "context.query"}],
    }

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "sources and fields" in result.errors[0].message


def test_run_plan_schema_rejects_compact_context_query_grant() -> None:
    data = _plan_dict()
    data["grants"] = {"step_tools": {"create-campaign": ["context.query"]}}

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "sources and fields" in result.errors[0].message


def test_run_plan_schema_rejects_cyclic_step_dependencies() -> None:
    data = _plan_dict()
    data["steps"] = [
        {"id": "first", "title": "First", "depends_on": ["second"]},
        {"id": "second", "title": "Second", "depends_on": ["first"]},
    ]
    data["approvals"] = []

    result = validate_run_plan_obj(data)

    assert result.valid is False
    assert "cyclic step dependencies" in result.errors[0].message
