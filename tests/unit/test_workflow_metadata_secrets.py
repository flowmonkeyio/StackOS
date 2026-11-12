"""Operator metadata never exempts its contents from workflow secret hygiene."""

import pytest

from stackos.workflows.run_plan_schema import validate_run_plan_obj
from stackos.workflows.template_schema import validate_workflow_template_obj


@pytest.fixture(params=["template", "run-plan", "run-plan-step"])
def validate_metadata(request):
    def validate(metadata):
        data = {"key": "metadata-proof", "steps": [{"id": "proof", "title": "Proof"}]}
        if request.param == "template":
            data["name"] = "Metadata proof"
            validator = validate_workflow_template_obj
        else:
            data["title"] = "Metadata proof"
            validator = validate_run_plan_obj
        target = data["steps"][0] if request.param == "run-plan-step" else data
        target["metadata"] = metadata
        return validator(data)

    return validate, request.param == "run-plan-step"


def test_operator_authorization_instruction_is_preserved(validate_metadata):
    validate, step = validate_metadata
    metadata = {"operator_authorization": "Proceed with the reviewed implementation and checks."}
    result = validate(metadata)
    assert result.valid, result.errors
    parsed = getattr(result, "template", None) or result.plan
    assert (parsed.steps[0] if step else parsed).metadata_json == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {"Authorization": "synthetic-material"},
        {"authentication": "synthetic-material"},
        {"credential": "synthetic-material"},
        {"bearer": "synthetic-material"},
        {"password": "synthetic-material"},
        {"private-key": "synthetic-material"},
        {"api_key": "synthetic-material"},
        {"operator_authorization_password": "synthetic-material"},
        {"Operator_Authorization": "synthetic-material"},
        {"operator-authorization": "synthetic-material"},
        {"provider_authorization": "synthetic-material"},
        {"operator_authorization": [{"credential": "synthetic-material"}]},
        {"operator_authorization": "Authorization: Bearer synthetic-material"},
        {"operator_authorization": "password=synthetic-material"},
        {
            "operator_authorization": "-----BEGIN PRIVATE KEY-----synthetic-material"
            "-----END PRIVATE KEY-----"
        },
        {"ordinary": {"nested": [{"api_key": "synthetic-material"}]}},
        {"ordinary": "Bearer synthetic-material"},
    ],
)
def test_metadata_secret_errors_remain_structured_and_safe(validate_metadata, metadata):
    validate, _ = validate_metadata
    result = validate(metadata)
    assert not result.valid
    assert result.errors and all(issue.code and issue.path for issue in result.errors)
    assert "synthetic-material" not in result.model_dump_json()


def test_run_plan_opaque_references_keep_existing_paths():
    from stackos.workflows.run_plan_schema import find_run_plan_secret_paths

    assert (
        find_run_plan_secret_paths(
            {
                "credential_ref": "cred_synthetic",
                "auth_ref": "declared-auth",
                "secret_ref": "secret_" + "a" * 32,
                "password": {"$secret_ref": "secret_" + "b" * 32},
            }
        )
        == []
    )
    assert find_run_plan_secret_paths({"secret_ref": "invalid-reference"}) == ["$.secret_ref"]
    assert find_run_plan_secret_paths({"password": {"$secret_ref": "invalid-reference"}})


def test_operator_instruction_name_is_not_an_action_payload_exemption():
    from stackos.workflows.run_plan_schema import find_run_plan_secret_paths

    assert find_run_plan_secret_paths(
        {"action_payloads": {"operator_authorization": "synthetic-material"}}
    ) == ["$.action_payloads.operator_authorization"]
