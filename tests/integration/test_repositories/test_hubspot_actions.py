from __future__ import annotations

import asyncio
import hashlib
import json
import re

import pytest
from pytest_httpx import HTTPXMock
from sqlmodel import Session, select

from stackos.actions import ActionRepository
from stackos.auth_providers import AuthRepository
from stackos.db.models import (
    ActionCall,
    Credential,
    CredentialAccount,
    CredentialScope,
)
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.projects import IntegrationCredentialRepository
from stackos.repositories.provider_refs import ProviderObjectReferenceRepository
from stackos.repositories.resources import ArtifactRepository, ResourceRepository


def _hubspot_credential(
    session: Session,
    *,
    project_id: int,
    scopes: set[str],
    transactional_email_entitlement_confirmed: bool | None = None,
) -> str:
    IntegrationCredentialRepository(session).set(
        project_id=project_id,
        kind="hubspot",
        secret_payload=json.dumps({"access_token": "hubspot-secret"}).encode(),
        config_json=(
            {
                "transactional_email_entitlement_confirmed": (
                    transactional_email_entitlement_confirmed
                )
            }
            if transactional_email_entitlement_confirmed is not None
            else None
        ),
    )
    credential_ref = (
        AuthRepository(session)
        .status(project_id=project_id, provider_key="hubspot")
        .connections[0]
        .credential_ref
    )
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    credential.auth_type = "oauth"
    credential.auth_method_key = "oauth2_authorization_code"
    credential.config_json = {**(credential.config_json or {}), "scope_status": "known"}
    if transactional_email_entitlement_confirmed is not None:
        credential.config_json["transactional_email_entitlement_confirmed"] = (
            transactional_email_entitlement_confirmed
        )
    session.add(credential)
    assert credential.id is not None
    session.add(
        CredentialAccount(
            credential_id=credential.id,
            provider_account_id="1234567",
            metadata_json={"hub_id": 1234567},
        )
    )
    for scope in sorted(scopes):
        session.add(CredentialScope(credential_id=credential.id, scope=scope))
    session.commit()
    return credential_ref


def _safe_ref(
    session: Session,
    *,
    credential_ref: str,
    object_type: str,
    provider_object_id: str,
    metadata_json: dict[str, object] | None = None,
) -> str:
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    return ProviderObjectReferenceRepository(session).upsert(
        credential=credential,
        object_type=object_type,
        provider_object_id=provider_object_id,
        display_name=provider_object_id,
        metadata_json=metadata_json,
    )


def _transactional_send_input(
    *,
    contact_ref: str,
    email_ref: str,
    send_id: str = "transactional-order-101",
) -> dict[str, object]:
    return {
        "contact_ref": contact_ref,
        "email_ref": email_ref,
        "send_id": send_id,
        "custom_properties": {"order_number": "SO-101", "item_count": 2},
        "entitlement_confirmed": True,
        "transactional_use_confirmed": True,
        "consent_or_relationship_confirmed": True,
        "legal_basis": "contract",
        "legal_basis_explanation": "Order status requested by the customer.",
        "marketing_contact_state": "non-marketing",
        "communication_target_ref": "communication-target:customer-order",
        "profile_ref": "communication-profile:order-mailer",
    }


def test_hubspot_properties_normalize_standard_custom_and_lifecycle_metadata(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.schemas.contacts.read"},
    )
    provider_body = {
        "results": [
            {
                "name": "lifecyclestage",
                "label": "Lifecycle Stage",
                "description": "Customer lifecycle stage",
                "groupName": "contactinformation",
                "type": "enumeration",
                "fieldType": "select",
                "hubspotDefined": True,
                "hasUniqueValue": False,
                "hidden": False,
                "options": [
                    {
                        "label": "Lead",
                        "value": "lead",
                        "displayOrder": 10,
                        "hidden": False,
                    }
                ],
            },
            {
                "name": "customer_tier",
                "label": "Customer tier",
                "groupName": "contactinformation",
                "type": "enumeration",
                "fieldType": "select",
                "hubspotDefined": False,
                "hasUniqueValue": False,
                "hidden": False,
                "options": [
                    {
                        "label": "Gold",
                        "value": "gold_internal",
                        "displayOrder": 0,
                        "hidden": False,
                    }
                ],
            },
        ]
    }
    for _ in range(2):
        httpx_mock.add_response(
            method="GET",
            url="https://api.hubapi.com/crm/properties/2026-03/contacts",
            json=provider_body,
            headers={"x-hubspot-correlation-id": "corr-properties"},
        )

    outputs = []
    for _ in range(2):
        outputs.append(
            asyncio.run(
                ActionRepository(session).execute(
                    project_id=project_id,
                    action_ref="gtm.hubspot.crm.contacts.properties.list",
                    input_json={},
                    credential_ref=credential_ref,
                )
            ).data
        )

    first = outputs[0].output_json
    first_property = first["results"][0]
    custom_property = first["results"][1]
    rendered = json.dumps(first)
    assert first_property["property_ref"].startswith("provider-object:")
    assert first_property["group_ref"].startswith("provider-object:")
    assert first_property["options"][0]["option_ref"].startswith("provider-object:")
    assert first_property["is_custom"] is False
    assert custom_property["is_custom"] is True
    assert first["lifecycle_stages"] == [
        {
            "stage_ref": first_property["options"][0]["option_ref"],
            "label": "Lead",
            "display_order": 10,
            "hidden": False,
        }
    ]
    assert first["request_id"] == "corr-properties"
    assert outputs[1].output_json["results"][0]["property_ref"] == (first_property["property_ref"])
    assert (
        outputs[1].output_json["results"][0]["options"][0]["option_ref"]
        == (first_property["options"][0]["option_ref"])
    )
    for provider_identifier in (
        "lifecyclestage",
        "customer_tier",
        "contactinformation",
        "gold_internal",
    ):
        assert provider_identifier not in rendered
    assert "hubspot-secret" not in rendered


def test_hubspot_owners_normalize_teams_and_paging(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.owners.read"},
    )
    httpx_mock.add_response(
        method="GET",
        url=("https://api.hubapi.com/crm/owners/2026-03?limit=2&after=next-owner&archived=false"),
        json={
            "results": [
                {
                    "id": "41629779",
                    "userId": 9586504,
                    "email": "owner@example.com",
                    "firstName": "Ada",
                    "lastName": "Owner",
                    "type": "PERSON",
                    "archived": False,
                    "teams": [{"id": "368389", "name": "Sales", "primary": True}],
                }
            ],
            "paging": {"next": {"after": "owner-cursor"}},
        },
    )

    output = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.hubspot.crm.owners.list",
            input_json={"limit": 2, "after": "next-owner", "archived": False},
            credential_ref=credential_ref,
        )
    ).data.output_json

    owner = output["results"][0]
    assert owner == {
        "owner_ref": owner["owner_ref"],
        "email": "owner@example.com",
        "first_name": "Ada",
        "last_name": "Owner",
        "type": "PERSON",
        "archived": False,
        "teams": [
            {
                "team_ref": owner["teams"][0]["team_ref"],
                "name": "Sales",
                "primary": True,
            }
        ],
    }
    assert owner["owner_ref"].startswith("provider-object:")
    assert owner["teams"][0]["team_ref"].startswith("provider-object:")
    assert output["paging"] == {"after": "owner-cursor"}
    assert "41629779" not in json.dumps(output)
    assert "368389" not in json.dumps(output)
    assert "9586504" not in json.dumps(output)


def test_hubspot_pipeline_and_association_labels_use_typed_safe_refs(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={
            "crm.objects.deals.read",
            "crm.objects.contacts.read",
            "crm.objects.companies.read",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/crm/pipelines/2026-03/deals",
        json={
            "results": [
                {
                    "id": "default",
                    "label": "Sales Pipeline",
                    "displayOrder": 0,
                    "archived": False,
                    "stages": [
                        {
                            "id": "appointmentscheduled",
                            "label": "Appointment scheduled",
                            "displayOrder": 0,
                            "archived": False,
                            "metadata": {"probability": "0.2"},
                        }
                    ],
                }
            ]
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=("https://api.hubapi.com/crm/associations/2026-03/contacts/companies/labels"),
        json={"results": [{"category": "HUBSPOT_DEFINED", "typeId": 1, "label": "Primary"}]},
    )

    repository = ActionRepository(session)
    pipeline_output = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.crm.deals.pipelines.list",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data.output_json
    labels_output = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.crm.contact_company.labels.list",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data.output_json

    pipeline = pipeline_output["results"][0]
    label = labels_output["results"][0]
    assert pipeline["pipeline_ref"].startswith("provider-object:")
    assert pipeline["stages"][0]["stage_ref"].startswith("provider-object:")
    assert label["association_label_ref"].startswith("provider-object:")
    assert label["label"] == "Primary"
    assert label["category"] == "HUBSPOT_DEFINED"
    rendered = json.dumps({"pipelines": pipeline_output, "labels": labels_output})
    assert "appointmentscheduled" not in rendered
    assert '"typeId"' not in rendered


@pytest.mark.parametrize(
    (
        "labels_action_ref",
        "labels_url",
        "dissociate_action_ref",
        "from_type",
        "to_type",
        "required_scopes",
    ),
    [
        (
            "gtm.hubspot.crm.contact_company.labels.list",
            "https://api.hubapi.com/crm/associations/2026-03/contacts/companies/labels",
            "gtm.hubspot.crm.contact_company.dissociate",
            "contact",
            "company",
            {
                "crm.objects.contacts.read",
                "crm.objects.companies.read",
                "crm.objects.contacts.write",
                "crm.objects.companies.write",
            },
        ),
        (
            "gtm.hubspot.crm.contact_deal.labels.list",
            "https://api.hubapi.com/crm/associations/2026-03/contacts/deals/labels",
            "gtm.hubspot.crm.contact_deal.dissociate",
            "contact",
            "deal",
            {
                "crm.objects.contacts.read",
                "crm.objects.deals.read",
                "crm.objects.contacts.write",
                "crm.objects.deals.write",
            },
        ),
        (
            "gtm.hubspot.crm.company_deal.labels.list",
            "https://api.hubapi.com/crm/associations/2026-03/companies/deals/labels",
            "gtm.hubspot.crm.company_deal.dissociate",
            "company",
            "deal",
            {
                "crm.objects.companies.read",
                "crm.objects.deals.read",
                "crm.objects.companies.write",
                "crm.objects.deals.write",
            },
        ),
    ],
)
def test_hubspot_unlabeled_association_cannot_use_label_specific_dissociate(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    labels_action_ref: str,
    labels_url: str,
    dissociate_action_ref: str,
    from_type: str,
    to_type: str,
    required_scopes: set[str],
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes=required_scopes,
    )
    from_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type=from_type,
        provider_object_id=f"{from_type}-123",
    )
    to_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type=to_type,
        provider_object_id=f"{to_type}-456",
    )
    session.commit()
    httpx_mock.add_response(
        method="GET",
        url=labels_url,
        json={
            "results": [
                {
                    "category": "HUBSPOT_DEFINED",
                    "typeId": 279,
                    "label": None,
                }
            ]
        },
    )
    repository = ActionRepository(session)
    unlabeled_ref = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref=labels_action_ref,
            input_json={},
            credential_ref=credential_ref,
        )
    ).data.output_json["results"][0]["association_label_ref"]

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref=dissociate_action_ref,
                input_json={
                    "from_ref": from_ref,
                    "to_ref": to_ref,
                    "association_label_ref": unlabeled_ref,
                },
                credential_ref=credential_ref,
                idempotency_key=f"{from_type}-{to_type}-unlabeled-remove",
            )
        )

    assert "unlabeled and legacy ambiguous refs are rejected" in exc.value.data["error"]
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert requests[0].method == "GET"


def test_hubspot_legacy_association_ref_without_label_kind_cannot_dissociate(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={
            "crm.objects.contacts.write",
            "crm.objects.companies.write",
        },
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="contact-legacy",
    )
    company_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="company",
        provider_object_id="company-legacy",
    )
    ambiguous_label_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="association-label:contact-company",
        provider_object_id="1",
        metadata_json={"category": "HUBSPOT_DEFINED"},
    )
    session.commit()

    with pytest.raises(ConflictError, match="action connector failed") as exc:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.crm.contact_company.dissociate",
                input_json={
                    "from_ref": contact_ref,
                    "to_ref": company_ref,
                    "association_label_ref": ambiguous_label_ref,
                },
                credential_ref=credential_ref,
                idempotency_key="legacy-ambiguous-label-remove",
            )
        )

    assert "legacy ambiguous refs are rejected" in exc.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_hubspot_metadata_provider_error_preserves_repair_evidence_without_secrets(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.schemas.companies.read"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/crm/properties/2026-03/companies",
        status_code=429,
        headers={"Retry-After": "7", "x-hubspot-correlation-id": "corr-rate-limit"},
        json={
            "status": "error",
            "category": "RATE_LIMITS",
            "message": "Too many requests; token hubspot-secret must be redacted",
            "correlationId": "corr-rate-limit",
        },
    )

    with pytest.raises(ConflictError) as excinfo:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.crm.companies.properties.list",
                input_json={},
                credential_ref=credential_ref,
            )
        )

    data = excinfo.value.data
    assert data["provider_status_code"] == 429
    assert data["provider_error"]["category"] == "RATE_LIMITS"
    call = session.exec(select(ActionCall).where(ActionCall.id == data["action_call_id"])).one()
    assert call.metadata_json is not None
    assert call.metadata_json["request_id"] == "corr-rate-limit"
    assert call.metadata_json["retry_after"] == "7"
    rendered = json.dumps(call.response_json)
    assert "hubspot-secret" not in rendered
    assert "[redacted]" in rendered


def test_hubspot_contact_search_resolves_property_refs_and_normalizes_records(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.read"},
    )
    email_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact-property",
        provider_object_id="email",
    )
    created_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact-property",
        provider_object_id="createdate",
    )
    session.commit()
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/objects/2026-03/contacts/search",
        headers={"x-hubspot-correlation-id": "corr-search"},
        json={
            "total": 1,
            "results": [
                {
                    "id": "contact-123",
                    "properties": {
                        "email": "ada@example.com",
                        "createdate": "2026-07-01T00:00:00Z",
                        "hs_object_id": "contact-123",
                    },
                    "createdAt": "2026-07-01T00:00:00Z",
                    "updatedAt": "2026-07-02T00:00:00Z",
                    "archived": False,
                    "url": "https://app.hubspot.com/contacts/123/contact-123",
                }
            ],
            "paging": {"next": {"after": "20", "link": "signed-or-provider-url"}},
        },
    )

    output = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.hubspot.crm.contacts.search",
            input_json={
                "filter_groups": [
                    {
                        "filters": [
                            {
                                "property_ref": email_ref,
                                "operator": "EQ",
                                "value": "ada@example.com",
                            }
                        ]
                    }
                ],
                "sort": {"property_ref": created_ref, "direction": "DESCENDING"},
                "property_refs": [email_ref, created_ref],
                "limit": 20,
            },
            credential_ref=credential_ref,
        )
    ).data.output_json

    request = httpx_mock.get_requests()[0]
    assert json.loads(request.content) == {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "email",
                        "operator": "EQ",
                        "value": "ada@example.com",
                    }
                ]
            }
        ],
        "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
        "properties": ["email", "createdate"],
        "limit": 20,
    }
    record = output["results"][0]
    assert record["record_ref"].startswith("provider-object:")
    assert record["properties"] == [
        {"property_ref": email_ref, "value": "ada@example.com"},
        {"property_ref": created_ref, "value": "2026-07-01T00:00:00Z"},
    ]
    assert output["total"] == 1
    assert output["paging"] == {"after": "20"}
    assert output["request_id"] == "corr-search"
    rendered = json.dumps(output)
    assert "contact-123" not in rendered
    assert "hs_object_id" not in rendered
    assert "app.hubspot.com" not in rendered


def test_hubspot_batch_upsert_normalizes_partial_results_and_replays_idempotently(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.companies.write"},
    )
    external_id_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="company-property",
        provider_object_id="external_company_id",
        metadata_json={"has_unique_value": True},
    )
    name_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="company-property",
        provider_object_id="name",
    )
    session.commit()
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/objects/2026-03/companies/batch/upsert",
        status_code=207,
        headers={"x-hubspot-correlation-id": "corr-batch"},
        json={
            "status": "COMPLETE",
            "numErrors": 1,
            "results": [
                {
                    "id": "company-123",
                    "new": True,
                    "objectWriteTraceId": "row-1",
                    "properties": {"domain": "example.com", "name": "Example"},
                }
            ],
            "errors": [
                {
                    "category": "VALIDATION_ERROR",
                    "message": "duplicate unique property value",
                    "context": {"objectWriteTraceId": ["row-2"], "ids": ["raw-id"]},
                }
            ],
            "startedAt": "2026-07-22T00:00:00Z",
            "completedAt": "2026-07-22T00:00:01Z",
        },
    )
    repository = ActionRepository(session)
    kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.crm.companies.batch_upsert",
        "input_json": {
            "id_property_ref": external_id_ref,
            "inputs": [
                {
                    "properties": [
                        {
                            "property_ref": external_id_ref,
                            "value": "company-ext-123",
                        },
                        {"property_ref": name_ref, "value": "Example"},
                    ],
                    "trace_id": "row-1",
                }
            ],
        },
        "credential_ref": credential_ref,
        "idempotency_key": "hubspot-company-example-v1",
    }

    first = asyncio.run(repository.execute(**kwargs)).data
    replay = asyncio.run(repository.execute(**kwargs)).data

    assert len(httpx_mock.get_requests()) == 1
    assert json.loads(httpx_mock.get_requests()[0].content) == {
        "inputs": [
            {
                "id": "company-ext-123",
                "idProperty": "external_company_id",
                "properties": {
                    "external_company_id": "company-ext-123",
                    "name": "Example",
                },
                "objectWriteTraceId": "row-1",
            }
        ]
    }
    assert first.output_json == replay.output_json
    assert first.output_json["status"] == "partial"
    assert first.output_json["success_count"] == 1
    assert first.output_json["failure_count"] == 1
    assert first.output_json["results"][0]["record_ref"].startswith("provider-object:")
    assert first.output_json["failures"] == [
        {
            "category": "VALIDATION_ERROR",
            "message": "duplicate unique property value",
            "trace_ids": ["row-2"],
        }
    ]
    rendered = json.dumps(first.output_json)
    assert "company-123" not in rendered
    assert "raw-id" not in rendered


def test_hubspot_lead_metadata_enables_safe_search_when_sales_scope_is_granted(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.leads.read"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/crm/properties/2026-03/leads",
        json={
            "results": [
                {
                    "name": "hs_lead_name",
                    "label": "Lead name",
                    "groupName": "lead_information",
                    "type": "string",
                    "fieldType": "text",
                    "hubspotDefined": True,
                    "options": [],
                }
            ]
        },
    )
    repository = ActionRepository(session)
    properties = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.crm.leads.properties.list",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data.output_json
    lead_name_ref = properties["results"][0]["property_ref"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/objects/2026-03/leads/search",
        json={
            "total": 1,
            "results": [
                {
                    "id": "lead-123",
                    "properties": {"hs_lead_name": "Qualified lead"},
                    "archived": False,
                }
            ],
        },
    )

    search = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.crm.leads.search",
            input_json={"property_refs": [lead_name_ref], "limit": 10},
            credential_ref=credential_ref,
        )
    ).data.output_json

    assert search["results"][0]["record_ref"].startswith("provider-object:")
    assert search["results"][0]["properties"] == [
        {"property_ref": lead_name_ref, "value": "Qualified lead"}
    ]
    assert "lead-123" not in json.dumps(search)


def test_hubspot_deal_list_returns_property_history_without_raw_ids(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.deals.read"},
    )
    amount_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="deal-property",
        provider_object_id="amount",
    )
    session.commit()
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.hubapi.com/crm/objects/2026-03/deals"
            "?limit=1&properties=amount&propertiesWithHistory=amount"
        ),
        json={
            "results": [
                {
                    "id": "deal-123",
                    "properties": {"amount": "2500"},
                    "propertiesWithHistory": {
                        "amount": [
                            {
                                "value": "1000",
                                "timestamp": "2026-07-01T00:00:00Z",
                                "sourceType": "CRM_UI",
                                "sourceId": "raw-user-id",
                            }
                        ]
                    },
                    "archived": False,
                }
            ]
        },
    )

    output = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.hubspot.crm.deals.list",
            input_json={
                "property_refs": [amount_ref],
                "property_history_refs": [amount_ref],
                "limit": 1,
            },
            credential_ref=credential_ref,
        )
    ).data.output_json

    record = output["results"][0]
    assert record["properties"] == [{"property_ref": amount_ref, "value": "2500"}]
    assert record["property_history"] == [
        {
            "property_ref": amount_ref,
            "values": [
                {
                    "value": "1000",
                    "timestamp": "2026-07-01T00:00:00Z",
                    "source_type": "CRM_UI",
                }
            ],
        }
    ]
    rendered = json.dumps(output)
    assert "deal-123" not in rendered
    assert "raw-user-id" not in rendered


def test_hubspot_object_specific_scope_denial_stops_before_provider_call(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.read"},
    )

    with pytest.raises(ConflictError, match="missing required scopes"):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.crm.deals.search",
                input_json={"limit": 1},
                credential_ref=credential_ref,
            )
        )

    assert httpx_mock.get_requests() == []


def test_hubspot_typed_association_create_remove_and_idempotent_replay(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={
            "crm.objects.contacts.write",
            "crm.objects.companies.write",
            "crm.objects.deals.write",
        },
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="contact-123",
    )
    company_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="company",
        provider_object_id="company-456",
    )
    deal_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="deal",
        provider_object_id="deal-789",
    )
    primary_label_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="association-label:contact-company",
        provider_object_id="1",
        metadata_json={"category": "HUBSPOT_DEFINED", "is_unlabeled": False},
    )
    custom_label_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="association-label:contact-deal",
        provider_object_id="36",
        metadata_json={"category": "USER_DEFINED", "is_unlabeled": False},
    )
    session.commit()
    httpx_mock.add_response(
        method="PUT",
        url=(
            "https://api.hubapi.com/crm/objects/2026-03/contact/contact-123/"
            "associations/company/company-456"
        ),
        json={
            "fromObjectId": "contact-123",
            "toObjectId": "company-456",
            "labels": ["Primary"],
        },
    )
    repository = ActionRepository(session)
    create_kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.crm.contact_company.associate",
        "input_json": {
            "from_ref": contact_ref,
            "to_ref": company_ref,
            "association_label_ref": primary_label_ref,
        },
        "credential_ref": credential_ref,
        "idempotency_key": "contact-company-primary-v1",
    }

    created = asyncio.run(repository.execute(**create_kwargs)).data
    replay = asyncio.run(repository.execute(**create_kwargs)).data
    httpx_mock.add_response(
        method="POST",
        url=("https://api.hubapi.com/crm/associations/2026-03/contacts/deals/batch/labels/archive"),
        status_code=204,
    )
    removed = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.crm.contact_deal.dissociate",
            input_json={
                "from_ref": contact_ref,
                "to_ref": deal_ref,
                "association_label_ref": custom_label_ref,
            },
            credential_ref=credential_ref,
            idempotency_key="contact-deal-label-remove-v1",
        )
    ).data

    assert created.output_json == replay.output_json
    assert len(httpx_mock.get_requests()) == 2
    create_request, remove_request = httpx_mock.get_requests()
    assert json.loads(create_request.content) == [
        {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 1}
    ]
    assert json.loads(remove_request.content) == {
        "inputs": [
            {
                "types": [{"associationCategory": "USER_DEFINED", "associationTypeId": 36}],
                "from": {"id": "contact-123"},
                "to": {"id": "deal-789"},
            }
        ]
    }
    assert created.output_json["relationship_state"] == "associated"
    assert removed.output_json["relationship_state"] == "label_removed"
    rendered = json.dumps({"created": created.output_json, "removed": removed.output_json})
    for raw_id in ("contact-123", "company-456", "deal-789"):
        assert raw_id not in rendered


def test_hubspot_association_rejects_wrong_direction_and_stale_record_refs(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.write", "crm.objects.deals.write"},
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="contact-123",
    )
    deal_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="deal",
        provider_object_id="deal-789",
    )
    wrong_label_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="association-label:contact-company",
        provider_object_id="1",
        metadata_json={"category": "HUBSPOT_DEFINED"},
    )
    session.commit()
    repository = ActionRepository(session)

    with pytest.raises(ConflictError, match="action connector failed") as wrong_direction:
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="gtm.hubspot.crm.contact_deal.associate",
                input_json={
                    "from_ref": contact_ref,
                    "to_ref": deal_ref,
                    "association_label_ref": wrong_label_ref,
                },
                credential_ref=credential_ref,
            )
        )
    assert "object type" in wrong_direction.value.data["error"]

    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    ProviderObjectReferenceRepository(session).mark_stale(
        credential=credential,
        safe_ref=contact_ref,
        expected_object_type="contact",
    )
    session.commit()
    valid_label_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="association-label:contact-deal",
        provider_object_id="4",
        metadata_json={"category": "HUBSPOT_DEFINED"},
    )
    session.commit()

    with pytest.raises(ConflictError, match="action connector failed") as stale:
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="gtm.hubspot.crm.contact_deal.associate",
                input_json={
                    "from_ref": contact_ref,
                    "to_ref": deal_ref,
                    "association_label_ref": valid_label_ref,
                },
                credential_ref=credential_ref,
            )
        )
    assert "stale" in stale.value.data["error"]

    assert httpx_mock.get_requests() == []


def test_hubspot_note_create_resolves_all_safe_refs_and_replays_idempotently(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.write"},
    )
    owner_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="owner",
        provider_object_id="owner-101",
    )
    associations = [
        (
            "contact",
            "contact-201",
            202,
        ),
        (
            "company",
            "company-301",
            190,
        ),
        (
            "deal",
            "deal-401",
            214,
        ),
        (
            "lead",
            "lead-501",
            855,
        ),
    ]
    association_refs = [
        _safe_ref(
            session,
            credential_ref=credential_ref,
            object_type=object_type,
            provider_object_id=provider_id,
        )
        for object_type, provider_id, _type_id in associations
    ]
    session.commit()
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/objects/2026-03/notes",
        headers={"x-hubspot-correlation-id": "corr-note"},
        json={
            "id": "note-901",
            "createdAt": "2026-07-22T21:00:00Z",
            "updatedAt": "2026-07-22T21:00:00Z",
            "properties": {"hs_object_id": "note-901"},
        },
    )
    repository = ActionRepository(session)
    kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.crm.notes.create",
        "input_json": {
            "timestamp": "2026-07-22T20:59:00Z",
            "body": "Discovery summary",
            "owner_ref": owner_ref,
            "associations": association_refs,
        },
        "credential_ref": credential_ref,
        "idempotency_key": "hubspot-note-discovery-v1",
    }

    created = asyncio.run(repository.execute(**kwargs)).data
    replay = asyncio.run(repository.execute(**kwargs)).data

    assert len(httpx_mock.get_requests()) == 1
    assert json.loads(httpx_mock.get_requests()[0].content) == {
        "properties": {
            "hs_timestamp": "2026-07-22T20:59:00Z",
            "hs_note_body": "Discovery summary",
            "hubspot_owner_id": "owner-101",
        },
        "associations": [
            {
                "to": {"id": provider_id},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": type_id,
                    }
                ],
            }
            for _object_type, provider_id, type_id in associations
        ],
    }
    assert created.output_json == replay.output_json
    assert created.output_json == {
        "provider": "hubspot",
        "operation": "crm.notes.create",
        "status_code": 200,
        "activity_ref": created.output_json["activity_ref"],
        "activity_type": "note",
        "associations": [
            {"record_ref": safe_ref, "object_type": object_type}
            for safe_ref, (object_type, _provider_id, _type_id) in zip(
                association_refs,
                associations,
                strict=True,
            )
        ],
        "owner_ref": owner_ref,
        "created_at": "2026-07-22T21:00:00Z",
        "updated_at": "2026-07-22T21:00:00Z",
        "request_id": "corr-note",
    }
    assert created.output_json["activity_ref"].startswith("provider-object:")
    rendered = json.dumps(created.output_json)
    for raw_id in (
        "owner-101",
        "contact-201",
        "company-301",
        "deal-401",
        "lead-501",
        "note-901",
    ):
        assert raw_id not in rendered


@pytest.mark.parametrize(
    (
        "action_ref",
        "activity_type",
        "association_type",
        "association_provider_id",
        "association_type_id",
        "input_json",
        "expected_properties",
    ),
    [
        (
            "gtm.hubspot.crm.tasks.create",
            "task",
            "company",
            "company-301",
            192,
            {
                "due_at": "2026-07-23T17:00:00Z",
                "title": "Send proposal",
                "body": "Include revised pricing",
                "status": "NOT_STARTED",
                "priority": "HIGH",
            },
            {
                "hs_timestamp": "2026-07-23T17:00:00Z",
                "hs_task_subject": "Send proposal",
                "hs_task_body": "Include revised pricing",
                "hs_task_status": "NOT_STARTED",
                "hs_task_priority": "HIGH",
            },
        ),
        (
            "gtm.hubspot.crm.calls.create",
            "call",
            "deal",
            "deal-401",
            206,
            {
                "timestamp": 1784754000000,
                "title": "Discovery call",
                "body": "Confirmed buying process",
                "direction": "OUTBOUND",
                "status": "COMPLETED",
                "duration_ms": 420000,
            },
            {
                "hs_timestamp": "1784754000000",
                "hs_call_direction": "OUTBOUND",
                "hs_call_title": "Discovery call",
                "hs_call_body": "Confirmed buying process",
                "hs_call_status": "COMPLETED",
                "hs_call_duration": "420000",
            },
        ),
        (
            "gtm.hubspot.crm.meetings.create",
            "meeting",
            "lead",
            "lead-501",
            601,
            {
                "timestamp": "2026-07-24T17:00:00Z",
                "title": "Solution review",
                "body": "Review implementation plan",
                "start_time": "2026-07-24T17:00:00Z",
                "end_time": "2026-07-24T17:45:00Z",
                "outcome": "SCHEDULED",
            },
            {
                "hs_timestamp": "2026-07-24T17:00:00Z",
                "hs_meeting_title": "Solution review",
                "hs_meeting_body": "Review implementation plan",
                "hs_meeting_start_time": "2026-07-24T17:00:00Z",
                "hs_meeting_end_time": "2026-07-24T17:45:00Z",
                "hs_meeting_outcome": "SCHEDULED",
            },
        ),
    ],
)
def test_hubspot_sales_activities_render_only_approved_fields(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    action_ref: str,
    activity_type: str,
    association_type: str,
    association_provider_id: str,
    association_type_id: int,
    input_json: dict[str, object],
    expected_properties: dict[str, str],
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.write"},
    )
    owner_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="owner",
        provider_object_id="owner-101",
    )
    association_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type=association_type,
        provider_object_id=association_provider_id,
    )
    session.commit()
    plural = f"{activity_type}s"
    httpx_mock.add_response(
        method="POST",
        url=f"https://api.hubapi.com/crm/objects/2026-03/{plural}",
        json={"id": f"{activity_type}-901"},
    )

    output = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref=action_ref,
            input_json={
                **input_json,
                "owner_ref": owner_ref,
                "associations": [association_ref],
            },
            credential_ref=credential_ref,
            idempotency_key=f"hubspot-{activity_type}-v1",
        )
    ).data.output_json

    assert json.loads(httpx_mock.get_requests()[0].content) == {
        "properties": {**expected_properties, "hubspot_owner_id": "owner-101"},
        "associations": [
            {
                "to": {"id": association_provider_id},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": association_type_id,
                    }
                ],
            }
        ],
    }
    assert output["activity_type"] == activity_type
    assert output["activity_ref"].startswith("provider-object:")
    assert output["owner_ref"] == owner_ref
    assert output["associations"] == [
        {"record_ref": association_ref, "object_type": association_type}
    ]
    rendered = json.dumps(output)
    assert association_provider_id not in rendered
    assert "owner-101" not in rendered
    assert f"{activity_type}-901" not in rendered


def test_hubspot_activity_scope_and_ref_failures_stop_before_provider_call(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    read_only_credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.read"},
    )
    with pytest.raises(ConflictError, match="missing required scopes"):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.crm.notes.create",
                input_json={
                    "timestamp": "2026-07-22T21:00:00Z",
                    "body": "Should not be sent",
                },
                credential_ref=read_only_credential_ref,
            )
        )

    credential = session.exec(
        select(Credential).where(Credential.credential_ref == read_only_credential_ref)
    ).one()
    assert credential.id is not None
    session.add(
        CredentialScope(
            credential_id=credential.id,
            scope="crm.objects.contacts.write",
        )
    )
    session.commit()
    write_credential_ref = read_only_credential_ref
    contact_ref = _safe_ref(
        session,
        credential_ref=write_credential_ref,
        object_type="contact",
        provider_object_id="contact-201",
    )
    owner_ref = _safe_ref(
        session,
        credential_ref=write_credential_ref,
        object_type="owner",
        provider_object_id="owner-101",
    )
    session.commit()
    repository = ActionRepository(session)
    with pytest.raises(ConflictError, match="action connector failed") as wrong_owner:
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="gtm.hubspot.crm.notes.create",
                input_json={
                    "timestamp": "2026-07-22T21:00:00Z",
                    "body": "Should not be sent",
                    "owner_ref": contact_ref,
                },
                credential_ref=write_credential_ref,
            )
        )
    assert "object type" in wrong_owner.value.data["error"]

    ProviderObjectReferenceRepository(session).mark_stale(
        credential=credential,
        safe_ref=contact_ref,
        expected_object_type="contact",
    )
    session.commit()
    with pytest.raises(ConflictError, match="action connector failed") as stale:
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="gtm.hubspot.crm.notes.create",
                input_json={
                    "timestamp": "2026-07-22T21:00:00Z",
                    "body": "Should not be sent",
                    "owner_ref": owner_ref,
                    "associations": [contact_ref],
                },
                credential_ref=write_credential_ref,
            )
        )
    assert "stale" in stale.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_hubspot_product_metadata_enables_safe_search_and_paging(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.products.read", "crm.objects.products.write"},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/crm/properties/2026-03/products",
        json={
            "results": [
                {
                    "name": "hs_sku",
                    "label": "SKU",
                    "groupName": "productinformation",
                    "type": "string",
                    "fieldType": "text",
                    "hubspotDefined": True,
                    "hasUniqueValue": True,
                    "options": [],
                },
                {
                    "name": "name",
                    "label": "Name",
                    "groupName": "productinformation",
                    "type": "string",
                    "fieldType": "text",
                    "hubspotDefined": True,
                    "hasUniqueValue": False,
                    "options": [],
                },
            ]
        },
    )
    repository = ActionRepository(session)
    properties = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.sales.products.properties.list",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data.output_json["results"]
    sku_ref = properties[0]["property_ref"]
    name_ref = properties[1]["property_ref"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/objects/2026-03/products/search",
        headers={"x-hubspot-correlation-id": "corr-product-search"},
        json={
            "total": 1,
            "results": [
                {
                    "id": "product-901",
                    "properties": {"hs_sku": "SKU-101", "name": "Support Plan"},
                    "archived": False,
                }
            ],
            "paging": {"next": {"after": "product-page-2"}},
        },
    )

    output = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.sales.products.search",
            input_json={
                "filter_groups": [
                    {
                        "filters": [
                            {
                                "property_ref": sku_ref,
                                "operator": "EQ",
                                "value": "SKU-101",
                            }
                        ]
                    }
                ],
                "property_refs": [sku_ref, name_ref],
                "limit": 20,
            },
            credential_ref=credential_ref,
        )
    ).data.output_json

    assert json.loads(httpx_mock.get_requests()[1].content) == {
        "filterGroups": [
            {
                "filters": [
                    {
                        "propertyName": "hs_sku",
                        "operator": "EQ",
                        "value": "SKU-101",
                    }
                ]
            }
        ],
        "properties": ["hs_sku", "name"],
        "limit": 20,
    }
    assert output["results"][0]["record_ref"].startswith("provider-object:")
    assert output["results"][0]["properties"] == [
        {"property_ref": sku_ref, "value": "SKU-101"},
        {"property_ref": name_ref, "value": "Support Plan"},
    ]
    assert output["paging"] == {"after": "product-page-2"}
    assert output["request_id"] == "corr-product-search"
    assert "product-901" not in json.dumps(output)


def test_hubspot_product_property_metadata_requires_provider_documented_write_scope(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.products.read"},
    )

    with pytest.raises(ConflictError, match="missing required scopes"):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.sales.products.properties.list",
                input_json={},
                credential_ref=credential_ref,
            )
        )

    assert httpx_mock.get_requests() == []


def test_hubspot_product_upsert_requires_verified_unique_property_and_replays_partial(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.products.write"},
    )
    unverified_name_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="product-property",
        provider_object_id="name",
        metadata_json={"has_unique_value": False},
    )
    sku_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="product-property",
        provider_object_id="hs_sku",
        metadata_json={"has_unique_value": True},
    )
    session.commit()
    repository = ActionRepository(session)
    with pytest.raises(ConflictError, match="action connector failed") as unverified:
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="gtm.hubspot.sales.products.batch_upsert",
                input_json={
                    "id_property_ref": unverified_name_ref,
                    "inputs": [
                        {"properties": [{"property_ref": unverified_name_ref, "value": "Plan"}]}
                    ],
                },
                credential_ref=credential_ref,
            )
        )
    assert "provider-verified as unique" in unverified.value.data["error"]
    assert httpx_mock.get_requests() == []

    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/objects/2026-03/products/batch/upsert",
        status_code=207,
        headers={"x-hubspot-correlation-id": "corr-product-upsert"},
        json={
            "status": "COMPLETE",
            "results": [
                {
                    "id": "product-901",
                    "new": True,
                    "objectWriteTraceId": "sku-101",
                    "properties": {"hs_sku": "SKU-101", "name": "Support Plan"},
                }
            ],
            "errors": [
                {
                    "category": "VALIDATION_ERROR",
                    "message": "one row was rejected",
                    "context": {"objectWriteTraceId": ["sku-102"]},
                }
            ],
        },
    )
    kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.sales.products.batch_upsert",
        "input_json": {
            "id_property_ref": sku_ref,
            "inputs": [
                {
                    "properties": [
                        {"property_ref": sku_ref, "value": "SKU-101"},
                        {"property_ref": unverified_name_ref, "value": "Support Plan"},
                    ],
                    "trace_id": "sku-101",
                }
            ],
        },
        "credential_ref": credential_ref,
        "idempotency_key": "hubspot-product-sku-101-v1",
    }
    created = asyncio.run(repository.execute(**kwargs)).data
    replay = asyncio.run(repository.execute(**kwargs)).data

    assert len(httpx_mock.get_requests()) == 1
    assert created.output_json == replay.output_json
    assert created.output_json["status"] == "partial"
    assert created.output_json["success_count"] == 1
    assert created.output_json["failure_count"] == 1
    assert created.output_json["results"][0]["record_ref"].startswith("provider-object:")
    assert created.output_json["request_id"] == "corr-product-upsert"
    assert "product-901" not in json.dumps(created.output_json)
    calls = session.exec(
        select(ActionCall).where(ActionCall.operation == "sales.products.batch_upsert")
    ).all()
    assert any(call.status == "success" for call in calls)


def test_hubspot_line_item_upsert_resolves_product_ref_and_validates_price(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.line_items.write"},
    )
    external_id_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="line-item-property",
        provider_object_id="external_line_item_id",
        metadata_json={"has_unique_value": True},
    )
    product_property_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="line-item-property",
        provider_object_id="hs_product_id",
    )
    price_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="line-item-property",
        provider_object_id="price",
    )
    product_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="product",
        provider_object_id="product-901",
    )
    session.commit()
    repository = ActionRepository(session)

    for unsafe_value, expected_error in (
        ("product-901", "typed provider-object ref"),
        (product_ref, "non-negative number"),
    ):
        price = "10.00" if unsafe_value == "product-901" else "-0.01"
        with pytest.raises(ConflictError, match="action connector failed") as rejected:
            asyncio.run(
                repository.execute(
                    project_id=project_id,
                    action_ref="gtm.hubspot.sales.line_items.batch_upsert",
                    input_json={
                        "id_property_ref": external_id_ref,
                        "inputs": [
                            {
                                "properties": [
                                    {
                                        "property_ref": external_id_ref,
                                        "value": "line-ext-101",
                                    },
                                    {
                                        "property_ref": product_property_ref,
                                        "value": unsafe_value,
                                    },
                                    {"property_ref": price_ref, "value": price},
                                ]
                            }
                        ],
                    },
                    credential_ref=credential_ref,
                )
            )
        assert expected_error in rejected.value.data["error"]
    assert httpx_mock.get_requests() == []

    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/objects/2026-03/line_items/batch/upsert",
        json={
            "status": "COMPLETE",
            "results": [
                {
                    "id": "line-item-701",
                    "properties": {
                        "external_line_item_id": "line-ext-101",
                        "hs_product_id": "product-901",
                        "price": "10.00",
                    },
                }
            ],
        },
    )
    output = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.sales.line_items.batch_upsert",
            input_json={
                "id_property_ref": external_id_ref,
                "inputs": [
                    {
                        "properties": [
                            {
                                "property_ref": external_id_ref,
                                "value": "line-ext-101",
                            },
                            {
                                "property_ref": product_property_ref,
                                "value": product_ref,
                            },
                            {"property_ref": price_ref, "value": "10.00"},
                        ]
                    }
                ],
            },
            credential_ref=credential_ref,
            idempotency_key="hubspot-line-item-101-v1",
        )
    ).data.output_json

    request_body = json.loads(httpx_mock.get_requests()[0].content)
    assert request_body["inputs"][0]["properties"]["hs_product_id"] == "product-901"
    product_value = next(
        item
        for item in output["results"][0]["properties"]
        if item["property_ref"] == product_property_ref
    )
    assert product_value == {"property_ref": product_property_ref, "value_ref": product_ref}
    rendered = json.dumps(output)
    assert "product-901" not in rendered
    assert "line-item-701" not in rendered


def test_hubspot_line_item_deal_association_uses_typed_refs_and_safe_replay(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.line_items.write", "crm.objects.deals.write"},
    )
    line_item_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="line-item",
        provider_object_id="line-item-701",
    )
    deal_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="deal",
        provider_object_id="deal-401",
    )
    session.commit()
    httpx_mock.add_response(
        method="PUT",
        url=(
            "https://api.hubapi.com/crm/objects/2026-03/line_items/line-item-701/"
            "associations/default/deals/deal-401"
        ),
        json={"status": "COMPLETE", "results": [{"from": {"id": "raw"}}]},
    )
    repository = ActionRepository(session)
    kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.sales.line_items.associate_deal",
        "input_json": {"line_item_ref": line_item_ref, "deal_ref": deal_ref},
        "credential_ref": credential_ref,
        "idempotency_key": "hubspot-line-deal-attach-v1",
    }
    attached = asyncio.run(repository.execute(**kwargs)).data
    replay = asyncio.run(repository.execute(**kwargs)).data
    httpx_mock.add_response(
        method="DELETE",
        url=(
            "https://api.hubapi.com/crm/objects/2026-03/line_items/line-item-701/"
            "associations/deals/deal-401"
        ),
        status_code=204,
    )
    detached = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.sales.line_items.dissociate_deal",
            input_json={"line_item_ref": line_item_ref, "deal_ref": deal_ref},
            credential_ref=credential_ref,
            idempotency_key="hubspot-line-deal-detach-v1",
        )
    ).data

    assert len(httpx_mock.get_requests()) == 2
    assert attached.output_json == replay.output_json
    assert attached.output_json["relationship_state"] == "associated"
    assert detached.output_json["relationship_state"] == "dissociated"
    rendered = json.dumps({"attached": attached.output_json, "detached": detached.output_json})
    assert "line-item-701" not in rendered
    assert "deal-401" not in rendered
    assert "raw" not in rendered


def test_hubspot_quotes_and_goal_targets_use_current_object_contracts(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.quotes.read", "crm.objects.goals.read"},
    )
    for object_type, properties in (
        ("quotes", [("hs_title", "Title")]),
        (
            "goal_targets",
            [("hs_goal_name", "Goal name"), ("hubspot_owner_id", "Owner")],
        ),
    ):
        httpx_mock.add_response(
            method="GET",
            url=f"https://api.hubapi.com/crm/properties/2026-03/{object_type}",
            json={
                "results": [
                    {
                        "name": name,
                        "label": label,
                        "groupName": "sales_information",
                        "type": "string",
                        "fieldType": "text",
                        "hubspotDefined": True,
                        "hasUniqueValue": False,
                        "options": [],
                    }
                    for name, label in properties
                ]
            },
        )
    repository = ActionRepository(session)
    quote_property = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.sales.quotes.properties.list",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data.output_json["results"][0]["property_ref"]
    goal_properties = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.sales.goal_targets.properties.list",
            input_json={},
            credential_ref=credential_ref,
        )
    ).data.output_json["results"]
    goal_name_ref = goal_properties[0]["property_ref"]
    goal_owner_ref = goal_properties[1]["property_ref"]
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/objects/2026-03/quotes/search",
        json={
            "total": 1,
            "results": [{"id": "quote-801", "properties": {"hs_title": "Annual plan"}}],
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.hubapi.com/crm/objects/2026-03/goal_targets"
            "?limit=2&properties=hs_goal_name%2Chubspot_owner_id"
        ),
        json={
            "results": [
                {
                    "id": "goal-target-601",
                    "properties": {
                        "hs_goal_name": "Annual revenue",
                        "hubspot_owner_id": "owner-101",
                    },
                    "archived": False,
                }
            ],
            "paging": {"next": {"after": "goal-page-2"}},
        },
    )

    quote_output = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.sales.quotes.search",
            input_json={"property_refs": [quote_property], "limit": 1},
            credential_ref=credential_ref,
        )
    ).data.output_json
    goal_output = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.sales.goal_targets.list",
            input_json={
                "property_refs": [goal_name_ref, goal_owner_ref],
                "limit": 2,
            },
            credential_ref=credential_ref,
        )
    ).data.output_json

    assert quote_output["results"][0]["record_ref"].startswith("provider-object:")
    assert goal_output["results"][0]["record_ref"].startswith("provider-object:")
    owner_value = next(
        item
        for item in goal_output["results"][0]["properties"]
        if item["property_ref"] == goal_owner_ref
    )
    assert owner_value["value_ref"].startswith("provider-object:")
    assert goal_output["paging"] == {"after": "goal-page-2"}
    rendered = json.dumps({"quotes": quote_output, "goals": goal_output})
    for raw_id in ("quote-801", "goal-target-601", "owner-101"):
        assert raw_id not in rendered


def test_hubspot_forms_and_submissions_use_safe_refs_and_sensitive_paging(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"forms"},
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.hubapi.com/marketing/v3/forms"
            "?limit=2&after=form-page&archived=false&formTypes=hubspot"
        ),
        json={
            "results": [
                {
                    "id": "form-101",
                    "name": "Lead capture",
                    "formType": "hubspot",
                    "archived": False,
                    "createdAt": "2026-07-01T10:00:00Z",
                    "updatedAt": "2026-07-20T10:00:00Z",
                    "legalConsentOptions": {"type": "legitimateInterest"},
                    "fieldGroups": [
                        {
                            "fields": [
                                {
                                    "name": "internal_email_field",
                                    "label": "Email",
                                    "fieldType": "text",
                                    "objectTypeId": "0-1",
                                    "required": True,
                                    "hidden": False,
                                }
                            ]
                        }
                    ],
                }
            ],
            "paging": {"next": {"after": "form-page-2"}},
        },
    )
    repository = ActionRepository(session)
    forms_result = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.forms.list",
            input_json={
                "form_types": ["hubspot"],
                "limit": 2,
                "after": "form-page",
                "archived": False,
            },
            credential_ref=credential_ref,
        )
    ).data
    form = forms_result.output_json["results"][0]
    form_ref = form["form_ref"]
    field_ref = form["fields"][0]["field_ref"]

    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.hubapi.com/form-integrations/v1/submissions/forms/form-101"
            "?limit=2&after=submission-page"
        ),
        headers={"x-hubspot-correlation-id": "corr-form-submissions"},
        json={
            "results": [
                {
                    "conversionId": "conversion-301",
                    "submittedAt": 1784714400000,
                    "pageUrl": ("https://example.com/signup?email=private%40example.com#details"),
                    "values": [
                        {
                            "name": "internal_email_field",
                            "value": "customer@example.com",
                        }
                    ],
                }
            ],
            "paging": {"next": {"after": "submission-page-2"}},
        },
    )
    submissions_result = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.forms.submissions.list",
            input_json={
                "form_ref": form_ref,
                "limit": 2,
                "after": "submission-page",
            },
            credential_ref=credential_ref,
        )
    ).data
    output = submissions_result.output_json
    submission = output["results"][0]

    assert form_ref.startswith("provider-object:")
    assert field_ref.startswith("provider-object:")
    assert form["fields"][0]["object_type"] == "contact"
    assert forms_result.output_json["paging"] == {"after": "form-page-2"}
    assert output["form_ref"] == form_ref
    assert submission["submission_ref"].startswith("provider-object:")
    assert submission["values"] == [{"field_ref": field_ref, "value": "customer@example.com"}]
    assert submission["page_url"] == "https://example.com/signup"
    assert output["paging"] == {"after": "submission-page-2"}
    assert output["request_id"] == "corr-form-submissions"
    assert submissions_result.metadata_json["sensitive_data"] is True
    rendered = json.dumps({"forms": forms_result.output_json, "submissions": output})
    for raw_identifier in (
        "form-101",
        "conversion-301",
        "internal_email_field",
        "private%40example.com",
    ):
        assert raw_identifier not in rendered


def test_hubspot_contact_segments_memberships_and_mutations_are_safe_and_replayable(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.lists.read", "crm.lists.write"},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/lists/2026-03/search",
        headers={"x-hubspot-correlation-id": "corr-segment-search"},
        json={
            "lists": [
                {
                    "listId": "segment-101",
                    "name": "Qualified leads",
                    "objectTypeId": "0-1",
                    "processingType": "MANUAL",
                    "processingStatus": "COMPLETE",
                    "additionalProperties": {"hs_list_size": "2"},
                    "createdAt": "2026-07-01T10:00:00Z",
                    "updatedAt": "2026-07-20T10:00:00Z",
                }
            ],
            "hasMore": True,
            "offset": 20,
            "total": 1,
        },
    )
    repository = ActionRepository(session)
    segment_output = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.segments.list",
            input_json={
                "query": "Qualified",
                "processing_types": ["MANUAL"],
                "offset": 0,
                "count": 20,
            },
            credential_ref=credential_ref,
        )
    ).data.output_json
    segment = segment_output["results"][0]
    segment_ref = segment["segment_ref"]

    assert json.loads(httpx_mock.get_requests()[0].content) == {
        "objectTypeId": "0-1",
        "query": "Qualified",
        "processingTypes": ["MANUAL"],
        "offset": 0,
        "count": 20,
    }
    assert segment["object_type"] == "contact"
    assert segment["processing_type"] == "MANUAL"
    assert segment["size"] == 2
    assert segment_output["paging"] == {"offset": 20}
    assert segment_output["total"] == 1
    assert segment_output["request_id"] == "corr-segment-search"

    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.hubapi.com/crm/lists/2026-03/segment-101/memberships"
            "?limit=1&after=membership-page"
        ),
        json={
            "results": [
                {
                    "recordId": "contact-201",
                    "membershipTimestamp": "2026-07-21T10:00:00Z",
                }
            ],
            "paging": {"next": {"after": "membership-page-2"}},
            "total": 2,
        },
    )
    membership_output = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.segments.memberships.list",
            input_json={
                "segment_ref": segment_ref,
                "limit": 1,
                "after": "membership-page",
            },
            credential_ref=credential_ref,
        )
    ).data.output_json
    first_contact_ref = membership_output["results"][0]["contact_ref"]
    second_contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="contact-202",
    )
    session.commit()

    assert membership_output["segment_ref"] == segment_ref
    assert membership_output["paging"] == {"after": "membership-page-2"}
    assert membership_output["total"] == 2
    assert first_contact_ref.startswith("provider-object:")

    httpx_mock.add_response(
        method="PUT",
        url=("https://api.hubapi.com/crm/lists/2026-03/segment-101/memberships/add"),
        headers={"x-hubspot-correlation-id": "corr-segment-add"},
        status_code=207,
        json={
            "recordsIdsAdded": ["contact-201"],
            "recordIdsMissing": ["contact-202"],
        },
    )
    add_kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.marketing.segments.memberships.add",
        "input_json": {
            "segment_ref": segment_ref,
            "contact_refs": [first_contact_ref, second_contact_ref],
        },
        "credential_ref": credential_ref,
        "idempotency_key": "hubspot-segment-add-v1",
    }
    added = asyncio.run(repository.execute(**add_kwargs)).data
    replay = asyncio.run(repository.execute(**add_kwargs)).data

    assert json.loads(httpx_mock.get_requests()[2].content) == [
        "contact-201",
        "contact-202",
    ]
    assert added.output_json == replay.output_json
    assert added.output_json["status"] == "partial"
    assert added.output_json["added_contact_refs"] == [first_contact_ref]
    assert added.output_json["missing_contact_refs"] == [second_contact_ref]
    assert added.output_json["request_id"] == "corr-segment-add"
    assert added.metadata_json["partial_failure"] is True

    httpx_mock.add_response(
        method="PUT",
        url=("https://api.hubapi.com/crm/lists/2026-03/segment-101/memberships/remove"),
        json={"recordIdsRemoved": ["contact-201"], "recordIdsMissing": []},
    )
    removed = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.segments.memberships.remove",
            input_json={
                "segment_ref": segment_ref,
                "contact_refs": [first_contact_ref],
            },
            credential_ref=credential_ref,
            idempotency_key="hubspot-segment-remove-v1",
        )
    ).data.output_json
    assert removed["status"] == "success"
    assert removed["removed_contact_refs"] == [first_contact_ref]
    assert len(httpx_mock.get_requests()) == 4
    rendered = json.dumps(
        {
            "segments": segment_output,
            "memberships": membership_output,
            "added": added.output_json,
            "removed": removed,
        }
    )
    for raw_identifier in ("segment-101", "contact-201", "contact-202"):
        assert raw_identifier not in rendered


def test_hubspot_segment_scope_type_and_stale_ref_failures_stop_before_http(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.lists.read"},
    )
    repository = ActionRepository(session)
    placeholder_segment_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="segment",
        provider_object_id="segment-placeholder",
        metadata_json={
            "object_type_id": "0-1",
            "processing_type": "MANUAL",
        },
    )
    placeholder_contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="contact-placeholder",
    )
    session.commit()
    with pytest.raises(ConflictError, match="missing required scopes"):
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.segments.memberships.add",
                input_json={
                    "segment_ref": placeholder_segment_ref,
                    "contact_refs": [placeholder_contact_ref],
                },
                credential_ref=credential_ref,
            )
        )

    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    assert credential.id is not None
    session.add(CredentialScope(credential_id=credential.id, scope="crm.lists.write"))
    session.commit()
    dynamic_segment_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="segment",
        provider_object_id="segment-dynamic",
        metadata_json={
            "object_type_id": "0-1",
            "processing_type": "DYNAMIC",
        },
    )
    company_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="company",
        provider_object_id="company-201",
    )
    session.commit()

    with pytest.raises(ConflictError, match="action connector failed") as dynamic:
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.segments.memberships.add",
                input_json={
                    "segment_ref": dynamic_segment_ref,
                    "contact_refs": [placeholder_contact_ref],
                },
                credential_ref=credential_ref,
            )
        )
    assert "MANUAL or SNAPSHOT" in dynamic.value.data["error"]

    with pytest.raises(ConflictError, match="action connector failed") as wrong_type:
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.segments.memberships.add",
                input_json={
                    "segment_ref": placeholder_segment_ref,
                    "contact_refs": [company_ref],
                },
                credential_ref=credential_ref,
            )
        )
    assert "object type" in wrong_type.value.data["error"]

    ProviderObjectReferenceRepository(session).mark_stale(
        credential=credential,
        safe_ref=placeholder_contact_ref,
        expected_object_type="contact",
    )
    session.commit()
    with pytest.raises(ConflictError, match="action connector failed") as stale:
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.segments.memberships.remove",
                input_json={
                    "segment_ref": placeholder_segment_ref,
                    "contact_refs": [placeholder_contact_ref],
                },
                credential_ref=credential_ref,
            )
        )
    assert "stale" in stale.value.data["error"]
    assert httpx_mock.get_requests() == []


def test_hubspot_campaign_lifecycle_metrics_and_writes_use_bounded_safe_contracts(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"marketing.campaigns.read", "marketing.campaigns.write"},
    )
    campaign_response = {
        "id": "campaign-101",
        "properties": {
            "hs_name": "Customer launch",
            "hs_start_date": "2026-08-01",
            "hs_end_date": "2026-08-31",
            "hs_notes": "Launch notes",
            "hs_audience": "Existing customers",
            "hs_currency_code": "USD",
            "hs_campaign_status": "planned",
            "hs_utm": "customer-launch",
            "hs_owner": "owner-101",
            "hs_budget_items_sum_amount": "5000.00",
        },
        "businessUnits": [{"id": 42}],
        "createdAt": "2026-07-20T10:00:00Z",
        "updatedAt": "2026-07-21T10:00:00Z",
    }
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://api\.hubapi\.com/marketing/campaigns/2026-03\?.+$"),
        headers={"x-hubspot-correlation-id": "corr-campaign-list"},
        json={
            "results": [campaign_response],
            "total": 1,
            "paging": {"next": {"after": "campaign-page-2"}},
        },
    )
    repository = ActionRepository(session)
    listed = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.campaigns.list",
            input_json={"name": "Customer", "limit": 10},
            credential_ref=credential_ref,
        )
    ).data.output_json
    campaign = listed["results"][0]
    campaign_ref = campaign["campaign_ref"]

    list_request = httpx_mock.get_requests()[0]
    assert list_request.url.params["name"] == "Customer"
    assert list_request.url.params["limit"] == "10"
    assert "hs_goal" not in list_request.url.params["properties"]
    assert "hs_campaign_status" in list_request.url.params["properties"]
    assert campaign["status"] == "planned"
    assert campaign["owner_ref"].startswith("provider-object:")
    assert campaign["brand_refs"][0].startswith("provider-object:")
    assert listed["paging"] == {"after": "campaign-page-2"}
    assert listed["request_id"] == "corr-campaign-list"

    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://api\.hubapi\.com/marketing/campaigns/2026-03/campaign-101\?.+$"),
        json={
            **campaign_response,
            "assets": {
                "EMAIL": {
                    "results": [
                        {
                            "id": "email-201",
                            "name": "Launch email",
                            "metrics": {
                                "delivered": 50,
                                "opens": 25,
                                "campaignId": 999,
                            },
                        }
                    ]
                }
            },
        },
    )
    detail = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.campaigns.get",
            input_json={
                "campaign_ref": campaign_ref,
                "start_date": "2026-08-01",
                "end_date": "2026-08-31",
            },
            credential_ref=credential_ref,
        )
    ).data.output_json
    detail_request = httpx_mock.get_requests()[1]
    assert detail_request.url.params["startDate"] == "2026-08-01"
    assert detail_request.url.params["endDate"] == "2026-08-31"
    asset = detail["results"][0]["assets"][0]
    assert asset["asset_ref"].startswith("provider-object:")
    assert asset["asset_type"] == "email"
    assert asset["metrics"] == {"delivered": 50, "opens": 25}

    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/marketing/campaigns/2026-03",
        status_code=201,
        json={
            **campaign_response,
            "id": "campaign-102",
            "properties": {
                **campaign_response["properties"],
                "hs_name": "Expansion campaign",
                "hs_campaign_status": "in_progress",
            },
        },
    )
    create_kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.marketing.campaigns.create",
        "input_json": {
            "name": "Expansion campaign",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
            "currency_code": "usd",
            "status": "in_progress",
        },
        "credential_ref": credential_ref,
        "idempotency_key": "hubspot-campaign-create-v1",
    }
    created = asyncio.run(repository.execute(**create_kwargs)).data
    replay = asyncio.run(repository.execute(**create_kwargs)).data
    create_body = json.loads(httpx_mock.get_requests()[2].content)
    assert create_body == {
        "properties": {
            "hs_name": "Expansion campaign",
            "hs_start_date": "2026-09-01",
            "hs_end_date": "2026-09-30",
            "hs_currency_code": "USD",
            "hs_campaign_status": "in_progress",
        }
    }
    assert created.output_json == replay.output_json

    httpx_mock.add_response(
        method="PATCH",
        url="https://api.hubapi.com/marketing/campaigns/2026-03/campaign-101",
        json={
            **campaign_response,
            "properties": {
                **campaign_response["properties"],
                "hs_campaign_status": "active",
            },
        },
    )
    updated = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.campaigns.update",
            input_json={"campaign_ref": campaign_ref, "status": "active"},
            credential_ref=credential_ref,
            idempotency_key="hubspot-campaign-update-v1",
        )
    ).data.output_json
    assert json.loads(httpx_mock.get_requests()[3].content) == {
        "properties": {"hs_campaign_status": "active"}
    }
    assert updated["results"][0]["campaign_ref"] == campaign_ref
    assert updated["results"][0]["status"] == "active"
    assert len(httpx_mock.get_requests()) == 4
    rendered = json.dumps(
        {"listed": listed, "detail": detail, "created": created.output_json, "updated": updated}
    )
    for raw_identifier in (
        "campaign-101",
        "campaign-102",
        "owner-101",
        "email-201",
        '"id": 42',
    ):
        assert raw_identifier not in rendered


def test_hubspot_marketing_email_assets_are_draft_only_and_omit_recipients(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"marketing-email"},
    )
    campaign_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="campaign",
        provider_object_id="campaign-101",
    )
    brand_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="brand",
        provider_object_id="42",
    )
    session.commit()
    email_response = {
        "id": "email-201",
        "name": "Customer newsletter",
        "subject": "Product update",
        "type": "BATCH_EMAIL",
        "subcategory": "batch",
        "archived": False,
        "isPublished": False,
        "isTransactional": False,
        "state": "DRAFT",
        "campaign": "campaign-101",
        "campaignName": "Customer launch",
        "businessUnitId": "42",
        "folderIdV2": 71,
        "content": {"templatePath": "@hubspot/email/dnd/welcome.html"},
        "subscriptionDetails": {
            "subscriptionId": "301",
            "subscriptionName": "Customer updates",
        },
        "to": {
            "contactIds": {"include": ["contact-raw-1"]},
            "contactIlsLists": {"include": ["segment-raw-1"]},
        },
        "stats": {
            "counters": {"delivered": 20, "opened": 10, "emailId": 201},
            "ratios": {"openRate": 0.5},
        },
        "createdAt": "2026-07-20T10:00:00Z",
        "updatedAt": "2026-07-21T10:00:00Z",
    }
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://api\.hubapi\.com/marketing/emails/2026-03\?.+$"),
        json={
            "results": [email_response],
            "total": 1,
            "paging": {"next": {"after": "email-page-2"}},
        },
    )
    repository = ActionRepository(session)
    listed = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.emails.list",
            input_json={
                "limit": 10,
                "archived": False,
                "is_published": False,
                "include_stats": True,
                "email_type": "BATCH_EMAIL",
            },
            credential_ref=credential_ref,
        )
    ).data.output_json
    email = listed["results"][0]
    email_ref = email["email_ref"]
    template_ref = email["template_ref"]

    request_params = httpx_mock.get_requests()[0].url.params
    assert request_params["includeStats"] == "true"
    assert request_params["isPublished"] == "false"
    assert email["lifecycle_state"] == "draft"
    assert email["campaign_ref"] == campaign_ref
    assert email["brand_ref"] == brand_ref
    assert email["template_ref"].startswith("provider-object:")
    assert email["folder_ref"].startswith("provider-object:")
    assert email["subscription_type_ref"].startswith("provider-object:")
    assert email["recipient_configuration_present"] is True
    assert email["statistics"] == {
        "counters": {"delivered": 20, "opened": 10},
        "ratios": {"openRate": 0.5},
    }
    assert listed["paging"] == {"after": "email-page-2"}

    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/marketing/emails/2026-03/email-201",
        json=email_response,
    )
    detail = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.emails.get",
            input_json={"email_ref": email_ref},
            credential_ref=credential_ref,
        )
    ).data.output_json
    assert detail["results"][0]["email_ref"] == email_ref

    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/marketing/emails/2026-03",
        status_code=201,
        json={**email_response, "id": "email-202", "name": "Draft two"},
    )
    create_kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.marketing.emails.create",
        "input_json": {
            "name": "Draft two",
            "subject": "Another update",
            "template_ref": template_ref,
            "campaign_ref": campaign_ref,
            "brand_ref": brand_ref,
        },
        "credential_ref": credential_ref,
        "idempotency_key": "hubspot-email-create-v1",
    }
    created = asyncio.run(repository.execute(**create_kwargs)).data
    replay = asyncio.run(repository.execute(**create_kwargs)).data
    assert created.output_json == replay.output_json
    assert json.loads(httpx_mock.get_requests()[2].content) == {
        "name": "Draft two",
        "subject": "Another update",
        "templatePath": "@hubspot/email/dnd/welcome.html",
        "campaign": "campaign-101",
        "businessUnitId": 42,
    }

    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/marketing/emails/2026-03/email-201",
        json=email_response,
    )
    httpx_mock.add_response(
        method="PATCH",
        url="https://api.hubapi.com/marketing/emails/2026-03/email-201",
        json={**email_response, "subject": "Updated product news"},
    )
    update_kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.marketing.emails.update",
        "input_json": {"email_ref": email_ref, "subject": "Updated product news"},
        "credential_ref": credential_ref,
        "idempotency_key": "hubspot-email-update-v1",
    }
    updated = asyncio.run(repository.execute(**update_kwargs)).data
    update_replay = asyncio.run(repository.execute(**update_kwargs)).data
    assert updated.output_json == update_replay.output_json
    assert json.loads(httpx_mock.get_requests()[4].content) == {"subject": "Updated product news"}
    assert len(httpx_mock.get_requests()) == 5
    rendered = json.dumps(
        {
            "listed": listed,
            "detail": detail,
            "created": created.output_json,
            "updated": updated.output_json,
        }
    )
    for raw_identifier in (
        "email-201",
        "email-202",
        "campaign-101",
        "contact-raw-1",
        "segment-raw-1",
        '"emailId"',
    ):
        assert raw_identifier not in rendered


def test_hubspot_marketing_email_update_rejects_published_state_before_patch(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"marketing-email"},
    )
    email_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="marketing-email",
        provider_object_id="email-published",
        metadata_json={"is_published": False},
    )
    session.commit()
    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/marketing/emails/2026-03/email-published",
        json={
            "id": "email-published",
            "name": "Already sent",
            "isPublished": True,
            "state": "PUBLISHED",
        },
    )

    with pytest.raises(ConflictError, match="action connector failed") as published:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.emails.update",
                input_json={"email_ref": email_ref, "subject": "Must not change"},
                credential_ref=credential_ref,
                idempotency_key="hubspot-email-published-reject-v1",
            )
        )
    assert "provider-verified draft" in published.value.data["error"]
    assert len(httpx_mock.get_requests()) == 1


def test_hubspot_marketing_email_update_rejects_scheduled_state_before_patch(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"marketing-email"},
    )
    email_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="marketing-email",
        provider_object_id="email-scheduled",
        metadata_json={"is_published": False},
    )
    session.commit()
    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/marketing/emails/2026-03/email-scheduled",
        json={
            "id": "email-scheduled",
            "name": "Scheduled send",
            "isPublished": False,
            "state": "SCHEDULED",
            "publishDate": "2026-08-01T17:00:00Z",
        },
    )

    with pytest.raises(ConflictError, match="action connector failed") as scheduled:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.emails.update",
                input_json={"email_ref": email_ref, "subject": "Must not change"},
                credential_ref=credential_ref,
                idempotency_key="hubspot-email-scheduled-reject-v1",
            )
        )

    assert "provider-verified draft" in scheduled.value.data["error"]
    assert len(httpx_mock.get_requests()) == 1
    assert httpx_mock.get_requests()[0].method == "GET"


def test_hubspot_subscription_preferences_resolve_contact_email_and_audit_consent(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={
            "crm.objects.contacts.read",
            "subscriptions-definition-read",
            "subscriptions-status-read",
            "subscriptions-status-write",
        },
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="contact-201",
    )
    session.commit()
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.hubapi.com/communication-preferences/2026-03/definitions"
            "?includeTranslations=false"
        ),
        json={
            "status": "COMPLETE",
            "results": [
                {
                    "businessUnitId": 42,
                    "id": "301",
                    "name": "Customer updates",
                    "description": "Product and service updates",
                    "purpose": "Marketing",
                    "communicationMethod": "Email",
                    "isActive": True,
                    "isDefault": False,
                    "isInternal": False,
                }
            ],
        },
    )
    repository = ActionRepository(session)
    definitions = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.subscription_types.list",
            input_json={"include_translations": False},
            credential_ref=credential_ref,
        )
    ).data.output_json
    subscription_ref = definitions["results"][0]["subscription_type_ref"]

    for _ in range(2):
        httpx_mock.add_response(
            method="GET",
            url=(
                "https://api.hubapi.com/crm/objects/2026-03/contacts/contact-201"
                "?properties=email&archived=false"
            ),
            json={
                "id": "contact-201",
                "properties": {"email": "customer@example.com"},
                "archived": False,
            },
        )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.hubapi.com/communication-preferences/2026-03/statuses/"
            "customer%40example.com?channel=EMAIL"
        ),
        headers={"x-hubspot-correlation-id": "corr-preference-read"},
        json={
            "status": "SUCCESS",
            "results": [
                {
                    "businessUnitId": 42,
                    "channel": "EMAIL",
                    "subscriberIdString": "customer@example.com",
                    "subscriptionId": 301,
                    "status": "UNSUBSCRIBED",
                    "source": "Public status API",
                    "legalBasis": "CONSENT_WITH_NOTICE",
                    "legalBasisExplanation": "Customer preference center choice",
                    "timestamp": "2026-07-20T10:00:00Z",
                }
            ],
        },
    )
    read_result = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.contact_preferences.get",
            input_json={"contact_ref": contact_ref},
            credential_ref=credential_ref,
        )
    ).data
    preference = read_result.output_json["results"][0]
    assert preference["contact_ref"] == contact_ref
    assert preference["subscription_type_ref"] == subscription_ref
    assert preference["status"] == "UNSUBSCRIBED"
    assert preference["legal_basis"] == "CONSENT_WITH_NOTICE"
    assert read_result.metadata_json["sensitive_data"] is True
    assert read_result.metadata_json["consent_audit"] is True

    httpx_mock.add_response(
        method="POST",
        url=(
            "https://api.hubapi.com/communication-preferences/2026-03/statuses/"
            "customer%40example.com"
        ),
        headers={"x-hubspot-correlation-id": "corr-preference-write"},
        json={
            "businessUnitId": 42,
            "channel": "EMAIL",
            "subscriberIdString": "customer@example.com",
            "subscriptionId": 301,
            "status": "SUBSCRIBED",
            "source": "Public status API",
            "legalBasis": "CONSENT_WITH_NOTICE",
            "legalBasisExplanation": "Customer opted in on the preference center",
            "setStatusSuccessReason": "RESUBSCRIBE_OCCURRED",
            "timestamp": "2026-07-22T10:00:00Z",
        },
    )
    update_kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.marketing.contact_preferences.update",
        "input_json": {
            "contact_ref": contact_ref,
            "subscription_type_ref": subscription_ref,
            "status": "SUBSCRIBED",
            "legal_basis": "CONSENT_WITH_NOTICE",
            "legal_basis_explanation": ("Customer opted in on the preference center"),
            "legal_change_confirmed": True,
            "consent_obtained": True,
        },
        "credential_ref": credential_ref,
        "idempotency_key": "hubspot-preference-subscribe-v1",
    }
    updated = asyncio.run(repository.execute(**update_kwargs)).data
    replay = asyncio.run(repository.execute(**update_kwargs)).data
    assert updated.output_json == replay.output_json
    assert json.loads(httpx_mock.get_requests()[4].content) == {
        "subscriptionId": 301,
        "statusState": "SUBSCRIBED",
        "legalBasis": "CONSENT_WITH_NOTICE",
        "legalBasisExplanation": "Customer opted in on the preference center",
        "channel": "EMAIL",
    }
    assert updated.output_json["results"][0]["status"] == "SUBSCRIBED"
    assert updated.output_json["request_id"] == "corr-preference-write"
    assert updated.metadata_json["consent_audit"] is True
    assert len(httpx_mock.get_requests()) == 5
    audit_calls = session.exec(
        select(ActionCall).where(ActionCall.operation == "marketing.contact_preferences.update")
    ).all()
    assert len(audit_calls) == 1
    assert audit_calls[0].status == "success"
    assert audit_calls[0].request_json["legal_basis"] == "CONSENT_WITH_NOTICE"
    assert "customer@example.com" not in json.dumps(audit_calls[0].response_json)
    rendered = json.dumps(
        {
            "definitions": definitions,
            "read": read_result.output_json,
            "updated": updated.output_json,
        }
    )
    for raw_identifier in (
        "customer@example.com",
        "contact-201",
        '"subscriptionId": 301',
        '"businessUnitId": 42',
    ):
        assert raw_identifier not in rendered


def test_hubspot_preference_consent_and_scope_denials_stop_before_http(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.read", "subscriptions-status-read"},
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="contact-201",
    )
    subscription_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="subscription-type",
        provider_object_id="301",
    )
    session.commit()
    repository = ActionRepository(session)
    base_input = {
        "contact_ref": contact_ref,
        "subscription_type_ref": subscription_ref,
        "status": "SUBSCRIBED",
        "legal_basis": "CONSENT_WITH_NOTICE",
        "legal_basis_explanation": "Customer consent record",
        "legal_change_confirmed": True,
    }
    with pytest.raises(ValidationError, match="action payload is invalid"):
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.contact_preferences.update",
                input_json=base_input,
                credential_ref=credential_ref,
            )
        )
    assert httpx_mock.get_requests() == []

    with pytest.raises(ConflictError, match="missing required scopes"):
        asyncio.run(
            repository.execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.contact_preferences.update",
                input_json={**base_input, "consent_obtained": True},
                credential_ref=credential_ref,
            )
        )
    assert httpx_mock.get_requests() == []


def test_hubspot_preference_provider_error_redacts_resolved_contact_email(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.read", "subscriptions-status-read"},
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="contact-201",
    )
    session.commit()
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.hubapi.com/crm/objects/2026-03/contacts/contact-201"
            "?properties=email&archived=false"
        ),
        json={
            "id": "contact-201",
            "properties": {"email": "customer@example.com"},
            "archived": False,
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=(
            "https://api.hubapi.com/communication-preferences/2026-03/statuses/"
            "customer%40example.com?channel=EMAIL"
        ),
        status_code=403,
        headers={"x-hubspot-correlation-id": "corr-preference-denied"},
        json={
            "status": "error",
            "category": "MISSING_SCOPES",
            "message": "Cannot read preferences for customer@example.com",
        },
    )

    with pytest.raises(ConflictError, match="action connector failed") as denied:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.contact_preferences.get",
                input_json={"contact_ref": contact_ref},
                credential_ref=credential_ref,
            )
        )

    call = session.exec(
        select(ActionCall).where(ActionCall.id == denied.value.data["action_call_id"])
    ).one()
    rendered_error = json.dumps(denied.value.data)
    rendered_audit = json.dumps(call.model_dump(mode="json"))
    assert "customer@example.com" not in rendered_error
    assert "customer@example.com" not in rendered_audit
    assert "[redacted]" in rendered_error
    assert call.metadata_json["request_id"] == "corr-preference-denied"


def test_hubspot_marketing_entitlement_error_preserves_safe_repair_evidence(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"marketing.campaigns.read"},
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://api\.hubapi\.com/marketing/campaigns/2026-03\?.+$"),
        status_code=403,
        headers={"x-hubspot-correlation-id": "corr-marketing-entitlement"},
        json={
            "status": "error",
            "category": "MISSING_ENTITLEMENT",
            "message": "Marketing Hub Professional or Enterprise is required",
            "correlationId": "corr-marketing-entitlement",
        },
    )

    with pytest.raises(ConflictError, match="action connector failed") as denied:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.campaigns.list",
                input_json={},
                credential_ref=credential_ref,
            )
        )

    assert denied.value.data["provider_status_code"] == 403
    assert denied.value.data["provider_error"]["category"] == "MISSING_ENTITLEMENT"
    call = session.exec(
        select(ActionCall).where(ActionCall.id == denied.value.data["action_call_id"])
    ).one()
    assert call.metadata_json["request_id"] == "corr-marketing-entitlement"
    assert "hubspot-secret" not in json.dumps(call.response_json)


def test_hubspot_marketing_and_behavioral_events_use_safe_refs_and_deduplicate(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={
            "crm.objects.marketing_events.read",
            "crm.objects.marketing_events.write",
            "behavioral_events.event_definitions.read_write",
            "analytics.behavioral_events.send",
        },
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="contact-201",
    )
    session.commit()
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://api\.hubapi\.com/marketing/marketing-events/2026-03\?.+$"),
        headers={"x-hubspot-correlation-id": "corr-marketing-events"},
        json={
            "results": [
                {
                    "objectId": "marketing-event-101",
                    "eventName": "Customer summit",
                    "eventOrganizer": "StackOS",
                    "eventType": "CONFERENCE",
                    "eventUrl": "https://events.example.test/summit?token=secret#agenda",
                    "externalEventId": "external-event-101",
                    "appInfo": {"id": "app-77", "name": "StackOS"},
                    "customProperties": [
                        {"name": "event_region", "value": "west"},
                    ],
                    "createdAt": "2026-07-20T10:00:00Z",
                    "updatedAt": "2026-07-21T10:00:00Z",
                }
            ],
            "paging": {"next": {"after": "event-page-2"}},
        },
    )
    repository = ActionRepository(session)
    listed = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.events.list",
            input_json={"after": "event-page-1", "limit": 25},
            credential_ref=credential_ref,
        )
    ).data.output_json
    marketing_event = listed["results"][0]
    assert marketing_event["event_ref"].startswith("provider-object:")
    assert marketing_event["app_ref"].startswith("provider-object:")
    assert marketing_event["custom_property_names"] == ["event_region"]
    assert marketing_event["event_url"] == "https://events.example.test/summit"
    assert "participation" not in marketing_event
    assert listed["paging"] == {"after": "event-page-2"}
    assert listed["request_id"] == "corr-marketing-events"
    list_params = httpx_mock.get_requests()[0].url.params
    assert list_params["after"] == "event-page-1"
    assert list_params["limit"] == "25"

    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/marketing/marketing-events/2026-03/events/upsert",
        headers={"x-hubspot-correlation-id": "corr-marketing-event-upsert"},
        json={
            "status": "COMPLETE",
            "results": [
                {
                    "objectId": "marketing-event-102",
                    "eventName": "Customer workshop",
                    "eventOrganizer": "StackOS",
                    "eventType": "WORKSHOP",
                    "startDateTime": "2026-08-01T17:00:00Z",
                    "endDateTime": "2026-08-01T18:00:00Z",
                    "customProperties": [
                        {"name": "event_region", "value": "west"},
                    ],
                }
            ],
            "errors": [],
            "numErrors": 0,
            "startedAt": "2026-07-22T20:00:00Z",
            "completedAt": "2026-07-22T20:00:01Z",
        },
    )
    upsert_kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.marketing.events.upsert",
        "input_json": {
            "external_account_key": "stackos-events",
            "external_event_key": "customer-workshop-2026",
            "name": "Customer workshop",
            "organizer": "StackOS",
            "event_type": "WORKSHOP",
            "start_at": "2026-08-01T17:00:00Z",
            "end_at": "2026-08-01T18:00:00Z",
            "event_url": "https://events.example.test/workshop",
            "custom_properties": {"event_region": "west"},
        },
        "credential_ref": credential_ref,
        "idempotency_key": "hubspot-marketing-event-upsert-v1",
    }
    created = asyncio.run(repository.execute(**upsert_kwargs)).data
    replay = asyncio.run(repository.execute(**upsert_kwargs)).data
    assert created.output_json == replay.output_json
    assert created.output_json["results"][0]["event_ref"].startswith("provider-object:")
    assert created.output_json["status"] == "success"
    assert json.loads(httpx_mock.get_requests()[1].content) == {
        "inputs": [
            {
                "externalAccountId": "stackos-events",
                "externalEventId": "customer-workshop-2026",
                "eventName": "Customer workshop",
                "eventOrganizer": "StackOS",
                "eventType": "WORKSHOP",
                "startDateTime": "2026-08-01T17:00:00Z",
                "endDateTime": "2026-08-01T18:00:00Z",
                "eventUrl": "https://events.example.test/workshop",
                "customProperties": [{"name": "event_region", "value": "west"}],
            }
        ]
    }

    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://api\.hubapi\.com/events/2026-03/event-definitions\?.+$"),
        headers={"x-hubspot-correlation-id": "corr-event-definitions"},
        json={
            "results": [
                {
                    "id": "definition-301",
                    "fullyQualifiedName": "pe1234567_customer_login",
                    "name": "customer_login",
                    "labels": {"singular": "Customer login", "plural": "Customer logins"},
                    "description": "A customer logged in.",
                    "primaryObject": "CONTACT",
                    "objectTypeId": "0-1",
                    "archived": False,
                    "properties": [
                        {
                            "name": "login_method",
                            "label": "Login method",
                            "type": "string",
                            "fieldType": "text",
                            "description": "Authentication mechanism.",
                        }
                    ],
                }
            ],
            "total": 1,
            "paging": {"next": {"after": "definition-page-2"}},
        },
    )
    definitions = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.behavioral_events.definitions.list",
            input_json={
                "search_string": "login",
                "after": "definition-page-1",
                "limit": 20,
                "include_properties": True,
            },
            credential_ref=credential_ref,
        )
    ).data.output_json
    definition = definitions["results"][0]
    definition_ref = definition["definition_ref"]
    property_ref = definition["properties"][0]["property_ref"]
    assert definition_ref.startswith("provider-object:")
    assert property_ref.startswith("provider-object:")
    assert definition["target_object_type"] == "contact"
    assert definitions["total"] == 1
    definition_params = httpx_mock.get_requests()[2].url.params
    assert definition_params["searchString"] == "login"
    assert definition_params["includeProperties"] == "true"

    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/events/2026-03/send",
        status_code=204,
        headers={"x-hubspot-correlation-id": "corr-behavioral-event"},
    )
    send_kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.marketing.behavioral_events.send",
        "input_json": {
            "definition_ref": definition_ref,
            "contact_ref": contact_ref,
            "occurrence_key": "login-session-2026-07-22-001",
            "occurred_at": "2026-07-22T19:45:00Z",
            "property_values": [
                {"property_ref": property_ref, "value": "passwordless"},
            ],
            "tracking_authority_confirmed": True,
            "legal_basis": "contract",
            "legal_basis_explanation": "Customer security activity required for service.",
        },
        "credential_ref": credential_ref,
        "idempotency_key": "hubspot-behavioral-event-send-v1",
    }
    sent = asyncio.run(repository.execute(**send_kwargs)).data
    send_replay = asyncio.run(repository.execute(**send_kwargs)).data
    assert sent.output_json == send_replay.output_json
    assert sent.output_json["status"] == "accepted"
    assert sent.output_json["occurrence_ref"].startswith("provider-object:")
    assert sent.output_json["definition_ref"] == definition_ref
    assert sent.output_json["contact_ref"] == contact_ref
    assert sent.output_json["request_id"] == "corr-behavioral-event"
    send_body = json.loads(httpx_mock.get_requests()[3].content)
    assert send_body["eventName"] == "pe1234567_customer_login"
    assert send_body["objectId"] == "contact-201"
    assert send_body["occurredAt"] == "2026-07-22T19:45:00Z"
    assert send_body["properties"] == {"login_method": "passwordless"}
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        send_body["uuid"],
    )
    assert len(httpx_mock.get_requests()) == 4
    rendered = json.dumps(
        {
            "listed": listed,
            "created": created.output_json,
            "definitions": definitions,
            "sent": sent.output_json,
        }
    )
    for raw_identifier in (
        "marketing-event-101",
        "marketing-event-102",
        "external-event-101",
        "app-77",
        "definition-301",
        "pe1234567_customer_login",
        "contact-201",
    ):
        assert raw_identifier not in rendered


def test_hubspot_behavioral_event_validation_and_scope_denials_stop_before_http(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"behavioral_events.event_definitions.read_write"},
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="contact-201",
    )
    definition_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="behavioral-event-definition",
        provider_object_id="pe1234567_customer_login",
        metadata_json={"target_object_type": "contact"},
    )
    property_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="behavioral-event-property",
        provider_object_id=json.dumps(
            ["pe1234567_customer_login", "login_method"],
            separators=(",", ":"),
        ),
        metadata_json={"definition_ref": definition_ref},
    )
    session.commit()
    base_input = {
        "definition_ref": definition_ref,
        "contact_ref": contact_ref,
        "occurrence_key": "login-session-001",
        "occurred_at": "2026-07-22T19:45:00Z",
        "property_values": [{"property_ref": property_ref, "value": "passwordless"}],
        "tracking_authority_confirmed": True,
        "legal_basis": "contract",
        "legal_basis_explanation": "Customer security activity required for service.",
    }
    with pytest.raises(ValidationError, match="action payload is invalid"):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.behavioral_events.send",
                input_json={
                    **base_input,
                    "occurred_at": "not-a-timestamp",
                    "tracking_authority_confirmed": False,
                },
                credential_ref=credential_ref,
            )
        )
    with pytest.raises(ConflictError, match="missing required scopes"):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.behavioral_events.send",
                input_json=base_input,
                credential_ref=credential_ref,
            )
        )
    assert httpx_mock.get_requests() == []


def test_hubspot_behavioral_event_provider_errors_redact_internal_identities(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"analytics.behavioral_events.send"},
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="contact-201",
    )
    definition_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="behavioral-event-definition",
        provider_object_id="pe1234567_customer_login",
        metadata_json={"target_object_type": "contact"},
    )
    session.commit()
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/events/2026-03/send",
        status_code=403,
        headers={"x-hubspot-correlation-id": "corr-event-denied"},
        json={
            "status": "error",
            "category": "MISSING_ENTITLEMENT",
            "message": (
                "Cannot send pe1234567_customer_login for contact-201 without an eligible hub"
            ),
        },
    )
    with pytest.raises(ConflictError, match="action connector failed") as denied:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.marketing.behavioral_events.send",
                input_json={
                    "definition_ref": definition_ref,
                    "contact_ref": contact_ref,
                    "occurrence_key": "login-session-002",
                    "occurred_at": "2026-07-22T19:45:00Z",
                    "property_values": [],
                    "tracking_authority_confirmed": True,
                    "legal_basis": "contract",
                    "legal_basis_explanation": ("Customer security activity required for service."),
                },
                credential_ref=credential_ref,
                idempotency_key="hubspot-behavioral-event-provider-denial-v1",
            )
        )
    call = session.exec(
        select(ActionCall).where(ActionCall.id == denied.value.data["action_call_id"])
    ).one()
    rendered = json.dumps(
        {
            "error": denied.value.data,
            "response": call.response_json,
            "metadata": call.metadata_json,
        }
    )
    assert "pe1234567_customer_login" not in rendered
    assert "contact-201" not in rendered
    assert "[redacted]" in rendered
    assert call.metadata_json["request_id"] == "corr-event-denied"


def test_hubspot_transactional_send_uses_safe_refs_and_replays_without_second_send(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={
            "crm.objects.contacts.read",
            "marketing-email",
            "transactional-email",
        },
        transactional_email_entitlement_confirmed=True,
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="501",
    )
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"^https://api\.hubapi\.com/marketing/emails/2026-03\?.+$"),
        json={
            "results": [
                {
                    "id": "701",
                    "name": "Order status template",
                    "isPublished": False,
                    "isTransactional": True,
                    "state": "DRAFT",
                }
            ],
            "total": 1,
        },
    )
    repository = ActionRepository(session)
    email_ref = asyncio.run(
        repository.execute(
            project_id=project_id,
            action_ref="gtm.hubspot.marketing.emails.list",
            input_json={"limit": 1, "is_published": False},
            credential_ref=credential_ref,
        )
    ).data.output_json["results"][0]["email_ref"]
    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/crm/objects/2026-03/contacts/501?properties=email&archived=false",
        json={
            "id": "501",
            "properties": {"email": "buyer@example.test"},
            "archived": False,
        },
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/marketing/transactional/2026-03/single-email/send",
        headers={"x-hubspot-correlation-id": "corr-transactional-1"},
        json={
            "status": "PENDING",
            "statusId": "status-9001",
            "eventId": {
                "id": "event-8001",
                "created": "2026-07-22T23:00:00Z",
            },
            "requestedAt": "2026-07-22T23:00:00Z",
            "message": "Queued buyer@example.test as status-9001",
        },
    )
    kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.transactional.single_email.send",
        "input_json": _transactional_send_input(
            contact_ref=contact_ref,
            email_ref=email_ref,
        ),
        "credential_ref": credential_ref,
        "idempotency_key": "transactional-order-101",
    }

    first = asyncio.run(repository.execute(**kwargs)).data
    replay = asyncio.run(repository.execute(**kwargs)).data

    request = httpx_mock.get_request(
        method="POST",
        url="https://api.hubapi.com/marketing/transactional/2026-03/single-email/send",
    )
    assert request is not None
    assert json.loads(request.content) == {
        "emailId": 701,
        "message": {
            "to": "buyer@example.test",
            "sendId": "transactional-order-101",
        },
        "contactProperties": {},
        "customProperties": {"order_number": "SO-101", "item_count": 2},
    }
    assert len(httpx_mock.get_requests()) == 3
    assert replay.replayed is True
    assert replay.output_json == first.output_json
    output = first.output_json
    assert output["status"] == "pending"
    assert output["provider_status"] == "PENDING"
    assert output["message_ref"].startswith("provider-object:")
    assert output["event_ref"].startswith("provider-object:")
    assert output["contact_ref"] == contact_ref
    assert output["email_ref"] == email_ref
    assert output["contact_properties_updated"] is False
    assert output["marketing_contact_state_changed"] is False
    assert output["request_id"] == "corr-transactional-1"
    credential = session.exec(
        select(Credential).where(Credential.credential_ref == credential_ref)
    ).one()
    event = ProviderObjectReferenceRepository(session).resolve(
        credential=credential,
        safe_ref=output["event_ref"],
        expected_object_type="transactional-email-event",
    )
    assert event.provider_object_id == "event-8001"
    assert event.metadata_json == {"created": "2026-07-22T23:00:00Z"}
    rendered = json.dumps(first.model_dump(mode="json"))
    for sensitive in (
        "hubspot-secret",
        "buyer@example.test",
        "status-9001",
        "event-8001",
        '"501"',
        '"701"',
    ):
        assert sensitive not in rendered
    messages = ResourceRepository(session).query_records(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-message",
        limit=10,
    )
    stored = next(item for item in messages.items if item.external_id == output["message_ref"])
    assert stored.data_json["surface_ref"] == contact_ref
    assert stored.data_json["email_ref"] == email_ref
    assert stored.data_json["transport_status"] == "pending"
    assert stored.data_json["marketing_contact_state"] == "non-marketing"
    assert stored.data_json["marketing_contact_state_changed"] is False
    assert stored.data_json["template_property_keys"] == ["item_count", "order_number"]
    assert "buyer@example.test" not in json.dumps(stored.data_json)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transactional_use_confirmed", False),
        ("consent_or_relationship_confirmed", False),
        ("marketing_contact_state", "unknown"),
    ],
)
def test_hubspot_transactional_send_policy_inputs_fail_before_provider_call(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    field: str,
    value: object,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.read", "transactional-email"},
        transactional_email_entitlement_confirmed=True,
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="502",
    )
    email_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="marketing-email",
        provider_object_id="702",
        metadata_json={"is_transactional": True},
    )
    input_json = _transactional_send_input(
        contact_ref=contact_ref,
        email_ref=email_ref,
    )
    input_json[field] = value

    with pytest.raises(ValidationError, match="action payload is invalid"):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.transactional.single_email.send",
                input_json=input_json,
                credential_ref=credential_ref,
                idempotency_key=f"denied-{field}",
            )
        )

    assert httpx_mock.get_requests() == []


def test_hubspot_transactional_send_preserves_incomplete_async_acceptance_state(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.read", "transactional-email"},
        transactional_email_entitlement_confirmed=True,
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="505",
    )
    email_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="marketing-email",
        provider_object_id="705",
        metadata_json={"is_transactional": True},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/crm/objects/2026-03/contacts/505?properties=email&archived=false",
        json={"id": "505", "properties": {"email": "partial@example.test"}},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/marketing/transactional/2026-03/single-email/send",
        json={"status": "PROCESSING", "message": "Accepted for asynchronous processing"},
    )

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.hubspot.transactional.single_email.send",
            input_json=_transactional_send_input(
                contact_ref=contact_ref,
                email_ref=email_ref,
                send_id="partial-acceptance",
            ),
            credential_ref=credential_ref,
            idempotency_key="partial-acceptance",
        )
    ).data

    assert result.output_json["status"] == "processing"
    assert result.output_json["message_ref"].startswith("hubspot-transactional-message:")
    assert result.output_json["response_complete"] is False
    assert result.metadata_json["provider_response_incomplete"] is True
    assert "partial@example.test" not in json.dumps(result.model_dump(mode="json"))


def test_hubspot_transactional_send_unconfirmed_entitlement_fails_before_provider_call(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    denied_credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.read", "transactional-email"},
        transactional_email_entitlement_confirmed=False,
    )
    denied_contact_ref = _safe_ref(
        session,
        credential_ref=denied_credential_ref,
        object_type="contact",
        provider_object_id="503",
    )
    denied_email_ref = _safe_ref(
        session,
        credential_ref=denied_credential_ref,
        object_type="marketing-email",
        provider_object_id="703",
        metadata_json={"is_transactional": True},
    )
    with pytest.raises(ConflictError, match="action connector failed") as entitlement_denied:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.transactional.single_email.send",
                input_json=_transactional_send_input(
                    contact_ref=denied_contact_ref,
                    email_ref=denied_email_ref,
                    send_id="entitlement-denied",
                ),
                credential_ref=denied_credential_ref,
                idempotency_key="entitlement-denied",
            )
        )
    denied_call = session.get(ActionCall, entitlement_denied.value.data["action_call_id"])
    assert denied_call is not None
    assert denied_call.response_json["reason"] == ("transactional_email_entitlement_not_confirmed")
    assert denied_call.metadata_json["provider_called"] is False
    assert httpx_mock.get_requests() == []


def test_hubspot_transactional_send_requires_capability_scoped_oauth_grants(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"transactional-email"},
        transactional_email_entitlement_confirmed=True,
    )
    contact_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact",
        provider_object_id="506",
    )
    email_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="marketing-email",
        provider_object_id="706",
        metadata_json={"is_transactional": True},
    )

    with pytest.raises(ConflictError, match="missing required scopes") as denied:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.transactional.single_email.send",
                input_json=_transactional_send_input(
                    contact_ref=contact_ref,
                    email_ref=email_ref,
                    send_id="missing-contact-read",
                ),
                credential_ref=credential_ref,
                idempotency_key="missing-contact-read",
            )
        )

    assert denied.value.data["missing_scopes"] == ["crm.objects.contacts.read"]
    assert httpx_mock.get_requests() == []


def test_hubspot_transactional_send_provider_error_redacts_recipient_and_provider_ids(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    allowed_credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.objects.contacts.read", "transactional-email"},
        transactional_email_entitlement_confirmed=True,
    )
    allowed_contact_ref = _safe_ref(
        session,
        credential_ref=allowed_credential_ref,
        object_type="contact",
        provider_object_id="504",
    )
    allowed_email_ref = _safe_ref(
        session,
        credential_ref=allowed_credential_ref,
        object_type="marketing-email",
        provider_object_id="704",
        metadata_json={"is_transactional": True},
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/crm/objects/2026-03/contacts/504?properties=email&archived=false",
        json={"id": "504", "properties": {"email": "private@example.test"}},
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/marketing/transactional/2026-03/single-email/send",
        status_code=403,
        headers={"x-hubspot-correlation-id": "corr-transactional-denied"},
        json={
            "status": "error",
            "category": "MISSING_ENTITLEMENT",
            "message": ("Cannot send 704 to private@example.test for contact 504 without add-on"),
        },
    )
    with pytest.raises(ConflictError, match="action connector failed") as provider_denied:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.transactional.single_email.send",
                input_json=_transactional_send_input(
                    contact_ref=allowed_contact_ref,
                    email_ref=allowed_email_ref,
                    send_id="provider-denied",
                ),
                credential_ref=allowed_credential_ref,
                idempotency_key="provider-denied",
            )
        )
    provider_call = session.get(ActionCall, provider_denied.value.data["action_call_id"])
    assert provider_call is not None
    rendered = json.dumps(
        {
            "error": provider_denied.value.data,
            "response": provider_call.response_json,
            "metadata": provider_call.metadata_json,
        }
    )
    for sensitive in (
        "hubspot-secret",
        "private@example.test",
        '"504"',
        '"704"',
    ):
        assert sensitive not in rendered
    assert provider_call.metadata_json["request_id"] == "corr-transactional-denied"


def test_hubspot_bulk_export_create_uses_verified_refs_and_replays_without_second_job(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.export"},
    )
    email_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact-property",
        provider_object_id="email",
    )
    firstname_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact-property",
        provider_object_id="firstname",
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/exports/2026-03/export/async",
        status_code=202,
        headers={"x-hubspot-correlation-id": "corr-export-create"},
        json={
            "id": "9001",
            "links": {"status": "https://api.hubapi.com/private/tasks/9001"},
        },
    )
    kwargs = {
        "project_id": project_id,
        "action_ref": "gtm.hubspot.bulk.exports.create",
        "input_json": {
            "export_name": "Customer export",
            "object_type": "contacts",
            "property_refs": [email_ref, firstname_ref],
            "associated_object_types": ["companies"],
            "format": "CSV",
            "language": "EN",
            "include_internal_property_names": True,
            "include_internal_property_values": False,
            "include_primary_display_properties": True,
            "include_labeled_associations": True,
            "override_association_limit": False,
            "export_authorized": True,
        },
        "credential_ref": credential_ref,
        "idempotency_key": "customer-export-2026-07-22",
    }

    first = asyncio.run(ActionRepository(session).execute(**kwargs)).data
    replay = asyncio.run(ActionRepository(session).execute(**kwargs)).data

    request = httpx_mock.get_request(
        method="POST",
        url="https://api.hubapi.com/crm/exports/2026-03/export/async",
    )
    assert request is not None
    assert json.loads(request.content) == {
        "exportType": "VIEW",
        "exportName": "Customer export",
        "format": "CSV",
        "language": "EN",
        "objectType": "CONTACT",
        "objectProperties": ["email", "firstname"],
        "associatedObjectType": ["COMPANY"],
        "includePrimaryDisplayPropertyForAssociatedObjects": True,
        "includeLabeledAssociations": True,
        "exportInternalValuesOptions": ["NAMES"],
        "overrideAssociatedObjectsPerDefinitionPerRowLimit": False,
    }
    assert len(httpx_mock.get_requests()) == 1
    assert replay.replayed is True
    assert replay.output_json == first.output_json
    output = first.output_json
    assert output["status"] == "accepted"
    assert output["job_ref"].startswith("provider-object:")
    assert output["object_type"] == "contacts"
    assert output["property_refs"] == [email_ref, firstname_ref]
    assert output["associated_object_types"] == ["companies"]
    assert output["request_id"] == "corr-export-create"
    rendered = json.dumps(first.model_dump(mode="json"))
    assert "9001" not in rendered
    assert "private/tasks" not in rendered


def test_hubspot_bulk_export_status_returns_truthful_safe_state(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.export"},
    )
    email_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact-property",
        provider_object_id="email",
    )
    job_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="bulk-export-job",
        provider_object_id="9002",
        metadata_json={
            "object_type": "contacts",
            "property_refs": [email_ref],
            "provider_property_names": ["email"],
            "associated_object_types": [],
            "format": "CSV",
            "export_name": "Status export",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.hubapi.com/crm/exports/2026-03/export/9002",
        headers={"x-hubspot-correlation-id": "corr-export-status"},
        json={
            "id": "9002",
            "exportName": "Status export",
            "exportType": "VIEW",
            "objectType": "0-1",
            "objectProperties": ["email"],
            "createdAt": "2026-07-22T20:00:00Z",
            "updatedAt": "2026-07-22T20:01:00Z",
            "exportState": "DONE",
            "recordCount": 42,
        },
    )

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.hubspot.bulk.exports.status",
            input_json={"job_ref": job_ref},
            credential_ref=credential_ref,
            idempotency_key="status-export-9002",
        )
    ).data

    assert result.output_json == {
        "provider": "hubspot",
        "operation": "bulk.exports.status",
        "status_code": 200,
        "status": "complete",
        "provider_status": "DONE",
        "job_ref": job_ref,
        "object_type": "contacts",
        "property_refs": [email_ref],
        "associated_object_types": [],
        "format": "CSV",
        "export_name": "Status export",
        "record_count": 42,
        "created_at": "2026-07-22T20:00:00Z",
        "updated_at": "2026-07-22T20:01:00Z",
        "request_id": "corr-export-status",
    }
    assert "9002" not in json.dumps(result.model_dump(mode="json"))


def test_hubspot_bulk_export_result_preserves_pending_and_partial_failure_state(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.export"},
    )
    job_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="bulk-export-job",
        provider_object_id="9003",
        metadata_json={
            "object_type": "companies",
            "property_refs": [],
            "provider_property_names": [],
            "associated_object_types": [],
            "format": "XLSX",
            "export_name": "Pending export",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=("https://api.hubapi.com/crm/exports/2026-03/export/async/tasks/9003/status"),
        headers={"x-hubspot-correlation-id": "corr-export-result-pending"},
        json={
            "status": "PROCESSING",
            "requestedAt": "2026-07-22T20:00:00Z",
            "startedAt": "2026-07-22T20:00:05Z",
            "numErrors": 2,
            "errors": [
                {
                    "category": "VALIDATION_ERROR",
                    "message": "private property internal_secret failed for portal 1234567",
                    "errors": [{"code": "INVALID_PROPERTY", "message": "internal_secret"}],
                }
            ],
        },
    )

    result = asyncio.run(
        ActionRepository(session).execute(
            project_id=project_id,
            action_ref="gtm.hubspot.bulk.exports.result",
            input_json={"job_ref": job_ref, "max_bytes": 10_000_000},
            credential_ref=credential_ref,
            idempotency_key="result-export-9003-pending",
        )
    ).data

    output = result.output_json
    assert output["status"] == "processing"
    assert output["provider_status"] == "PROCESSING"
    assert output["job_ref"] == job_ref
    assert output["result_available"] is False
    assert output["error_count"] == 2
    assert output["error_summary"] == [
        {"category": "VALIDATION_ERROR", "codes": ["INVALID_PROPERTY"]}
    ]
    assert output["requested_at"] == "2026-07-22T20:00:00Z"
    assert output["started_at"] == "2026-07-22T20:00:05Z"
    rendered = json.dumps(result.model_dump(mode="json"))
    assert "internal_secret" not in rendered
    assert "1234567" not in rendered
    assert len(httpx_mock.get_requests()) == 1


def test_hubspot_bulk_export_result_downloads_to_managed_artifact_without_signed_url(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    tmp_path,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.export"},
    )
    job_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="bulk-export-job",
        provider_object_id="9004",
        metadata_json={
            "object_type": "contacts",
            "property_refs": [],
            "provider_property_names": [],
            "associated_object_types": ["companies"],
            "format": "CSV",
            "export_name": "Completed export",
        },
    )
    signed_url = "https://exports.hubspotusercontent.example/download/private-token-9004"
    httpx_mock.add_response(
        method="GET",
        url=("https://api.hubapi.com/crm/exports/2026-03/export/async/tasks/9004/status"),
        headers={"x-hubspot-correlation-id": "corr-export-result-complete"},
        json={
            "status": "COMPLETE",
            "requestedAt": "2026-07-22T20:00:00Z",
            "startedAt": "2026-07-22T20:00:05Z",
            "completedAt": "2026-07-22T20:01:00Z",
            "numErrors": 0,
            "errors": [],
            "result": signed_url,
        },
    )
    file_content = b"email,firstname\nbuyer@example.test,Ada\n"
    httpx_mock.add_response(
        method="GET",
        url=signed_url,
        headers={
            "content-type": "text/csv",
            "content-disposition": 'attachment; filename="completed-export.csv"',
        },
        content=file_content,
    )

    result = asyncio.run(
        ActionRepository(session, asset_dir=tmp_path).execute(
            project_id=project_id,
            action_ref="gtm.hubspot.bulk.exports.result",
            input_json={"job_ref": job_ref, "max_bytes": 10_000_000},
            credential_ref=credential_ref,
            idempotency_key="result-export-9004-complete",
        )
    ).data

    output = result.output_json
    assert output["status"] == "complete"
    assert output["provider_status"] == "COMPLETE"
    assert output["result_available"] is True
    assert output["job_ref"] == job_ref
    assert output["artifact_ref"].startswith("/generated-assets/hubspot-exports/")
    assert isinstance(output["artifact_id"], int)
    assert output["filename"] == "completed-export.csv"
    assert output["mime_type"] == "text/csv"
    assert output["size_bytes"] == len(file_content)
    assert output["sha256"] == hashlib.sha256(file_content).hexdigest()
    artifact_path = tmp_path / output["artifact_ref"].removeprefix("/generated-assets/")
    assert artifact_path.read_bytes() == file_content
    artifacts = ArtifactRepository(session).query(
        project_id=project_id,
        plugin_slug="gtm",
        kind="hubspot-export",
    )
    assert [item.id for item in artifacts.items] == [output["artifact_id"]]
    assert artifacts.items[0].uri == output["artifact_ref"]
    rendered = json.dumps(result.model_dump(mode="json"))
    assert signed_url not in rendered
    assert "private-token-9004" not in rendered
    assert "buyer@example.test" not in rendered


def test_hubspot_bulk_export_requires_export_scope_before_provider_call(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(session, project_id=project_id, scopes=set())
    email_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact-property",
        provider_object_id="email",
    )

    with pytest.raises(ConflictError, match="missing required scopes") as denied:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.bulk.exports.create",
                input_json={
                    "export_name": "Denied export",
                    "object_type": "contacts",
                    "property_refs": [email_ref],
                    "associated_object_types": [],
                    "format": "CSV",
                    "language": "EN",
                    "include_internal_property_names": False,
                    "include_internal_property_values": False,
                    "include_primary_display_properties": False,
                    "include_labeled_associations": False,
                    "override_association_limit": False,
                    "export_authorized": True,
                },
                credential_ref=credential_ref,
                idempotency_key="denied-export",
            )
        )

    assert denied.value.data["missing_scopes"] == ["crm.export"]
    assert httpx_mock.get_requests() == []


def test_hubspot_bulk_export_rate_limit_preserves_retry_context_and_redacts_selection(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.export"},
    )
    email_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="contact-property",
        provider_object_id="email",
    )
    httpx_mock.add_response(
        method="POST",
        url="https://api.hubapi.com/crm/exports/2026-03/export/async",
        status_code=429,
        headers={
            "x-hubspot-correlation-id": "corr-export-rate-limit",
            "retry-after": "60",
        },
        json={
            "status": "error",
            "category": "RATE_LIMITS",
            "message": "Customer export containing email exceeded the rolling limit",
        },
    )

    with pytest.raises(ConflictError, match="action connector failed") as denied:
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.bulk.exports.create",
                input_json={
                    "export_name": "Customer export",
                    "object_type": "contacts",
                    "property_refs": [email_ref],
                    "format": "CSV",
                    "export_authorized": True,
                },
                credential_ref=credential_ref,
                idempotency_key="rate-limited-export",
            )
        )

    call = session.get(ActionCall, denied.value.data["action_call_id"])
    assert call is not None
    assert call.metadata_json["request_id"] == "corr-export-rate-limit"
    assert call.metadata_json["retry_after"] == "60"
    rendered = json.dumps(
        {
            "error": denied.value.data,
            "response": call.response_json,
            "metadata": call.metadata_json,
        }
    )
    assert "Customer export" not in rendered
    assert "containing email" not in rendered
    assert "[redacted]" in rendered


def test_hubspot_bulk_export_result_rejects_private_download_url_before_request(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    tmp_path,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.export"},
    )
    job_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="bulk-export-job",
        provider_object_id="9005",
        metadata_json={
            "object_type": "contacts",
            "property_refs": [],
            "provider_property_names": [],
            "associated_object_types": [],
            "format": "CSV",
            "export_name": "Unsafe URL export",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=("https://api.hubapi.com/crm/exports/2026-03/export/async/tasks/9005/status"),
        json={
            "status": "COMPLETE",
            "numErrors": 0,
            "result": "https://127.0.0.1/private-export.csv",
        },
    )

    with pytest.raises(ConflictError, match="action connector failed"):
        asyncio.run(
            ActionRepository(session, asset_dir=tmp_path).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.bulk.exports.result",
                input_json={"job_ref": job_ref},
                credential_ref=credential_ref,
                idempotency_key="unsafe-export-url",
            )
        )

    assert len(httpx_mock.get_requests()) == 1
    assert (
        ArtifactRepository(session)
        .query(
            project_id=project_id,
            kind="hubspot-export",
        )
        .items
        == []
    )


def test_hubspot_bulk_export_result_enforces_download_byte_ceiling(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
    tmp_path,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.export"},
    )
    job_ref = _safe_ref(
        session,
        credential_ref=credential_ref,
        object_type="bulk-export-job",
        provider_object_id="9006",
        metadata_json={
            "object_type": "contacts",
            "property_refs": [],
            "provider_property_names": [],
            "associated_object_types": [],
            "format": "CSV",
            "export_name": "Oversized export",
        },
    )
    signed_url = "https://exports.hubspotusercontent.example/oversized-export"
    httpx_mock.add_response(
        method="GET",
        url=("https://api.hubapi.com/crm/exports/2026-03/export/async/tasks/9006/status"),
        json={"status": "COMPLETE", "numErrors": 0, "result": signed_url},
    )
    httpx_mock.add_response(
        method="GET",
        url=signed_url,
        content=b"0123456789",
    )

    with pytest.raises(ConflictError, match="action connector failed") as blocked:
        asyncio.run(
            ActionRepository(session, asset_dir=tmp_path).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.bulk.exports.result",
                input_json={"job_ref": job_ref, "max_bytes": 5},
                credential_ref=credential_ref,
                idempotency_key="oversized-export",
            )
        )

    call = session.get(ActionCall, blocked.value.data["action_call_id"])
    assert call is not None
    assert call.response_json["status"] == "download_failed"
    assert call.response_json["job_ref"] == job_ref
    assert call.response_json["retryable"] is False
    assert "narrower" in call.response_json["next_action"]
    assert signed_url not in json.dumps(call.response_json)
    assert (
        ArtifactRepository(session)
        .query(
            project_id=project_id,
            kind="hubspot-export",
        )
        .items
        == []
    )


def test_hubspot_bulk_import_remains_deferred_without_connector_call(
    session: Session,
    project_id: int,
    httpx_mock: HTTPXMock,
) -> None:
    credential_ref = _hubspot_credential(
        session,
        project_id=project_id,
        scopes={"crm.import"},
    )

    with pytest.raises((ConflictError, ValidationError)):
        asyncio.run(
            ActionRepository(session).execute(
                project_id=project_id,
                action_ref="gtm.hubspot.bulk.imports.create",
                input_json={"artifact_ref": "/generated-assets/imports/customers.csv"},
                credential_ref=credential_ref,
                idempotency_key="deferred-import",
            )
        )

    assert httpx_mock.get_requests() == []
