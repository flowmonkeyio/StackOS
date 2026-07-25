"""Slack communication profile lookup and validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlmodel import Session, col, select

from stackos.actions.connectors import ActionConnectorRequest
from stackos.db.models import Plugin, Resource, ResourceRecord
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ResourceRepository


def _credential_ref(request: ActionConnectorRequest) -> str:
    if request.credential is None:
        raise ValidationError("Slack communication actions require a connected Account")
    return request.credential.credential_ref


def _communication_profile_key(request: ActionConnectorRequest) -> str:
    """Resolve the communication profile that owns outbound interaction state."""

    raw = request.input_json.get("profile_ref") or request.input_json.get("profile_key")
    if isinstance(raw, str) and raw.strip():
        text = raw.strip()
        profile_key = text.removeprefix("communication-profile:")
        _communication_profile_data(request, profile_key=profile_key)
        return profile_key
    raise ValidationError("Slack profile_ref or profile_key is required")


def _communication_profile_data(
    request: ActionConnectorRequest,
    *,
    profile_key: str,
) -> dict[str, Any]:
    session = request.session
    if not isinstance(session, Session):
        raise ValidationError("Slack profile_ref requires a database session")
    record = _resource_record_by_external_id(
        session,
        project_id=request.project_id,
        resource_key="communication-profile",
        external_id=f"communication-profile:{profile_key}",
    )
    if record is None:
        raise ValidationError(
            "Slack communication profile not found",
            data={"profile_ref": f"communication-profile:{profile_key}"},
        )
    data = dict(record.data_json or {})
    if data.get("key") != profile_key or data.get("enabled") is False:
        raise ValidationError(
            "Slack communication profile is disabled or malformed",
            data={"profile_ref": f"communication-profile:{profile_key}"},
        )
    facets = data.get("provider_facets")
    slack_facet = facets.get("slack-bot") if isinstance(facets, Mapping) else None
    if not isinstance(slack_facet, Mapping):
        raise ValidationError(
            "Slack communication profile missing slack-bot provider facet",
            data={"profile_ref": f"communication-profile:{profile_key}"},
        )
    profile_credential_ref = str(slack_facet.get("credential_ref") or "").strip()
    credential_ref = _credential_ref(request)
    if profile_credential_ref != credential_ref:
        raise ValidationError(
            "Slack communication profile does not match the selected Account",
            data={
                "profile_ref": f"communication-profile:{profile_key}",
                "profile_credential_ref": profile_credential_ref,
                "credential_ref": credential_ref,
            },
        )
    return data


def _resource_record_by_external_id(
    session: Session,
    *,
    project_id: int,
    resource_key: str,
    external_id: str,
) -> ResourceRecord | None:
    ResourceRepository(session).list_resources(
        plugin_slug="communications",
        project_id=project_id,
    )
    return session.exec(
        select(ResourceRecord)
        .join(Resource, col(ResourceRecord.resource_id) == col(Resource.id))
        .join(Plugin, col(Resource.plugin_id) == col(Plugin.id))
        .where(
            col(ResourceRecord.project_id) == project_id,
            col(ResourceRecord.external_id) == external_id,
            col(Resource.key) == resource_key,
            col(Plugin.slug) == "communications",
        )
    ).first()
