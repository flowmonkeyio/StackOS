"""Delivery lookup coverage for profile-scoped communication surfaces."""

from __future__ import annotations

import pytest
from sqlmodel import Session

from stackos.communication_surface_bindings import communication_surface_binding_external_id
from stackos.operations.communication_delivery.resolution import _surface_data
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ResourceRepository


def test_surface_lookup_uses_one_profile_scoped_binding_without_legacy_fallback(
    session: Session,
    project_id: int,
) -> None:
    resources = ResourceRepository(session)
    surface_ref = "slack-channel:C123"
    resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-channel",
        external_id=f"communication-surface:{surface_ref}",
        title="Legacy support",
        data_json={"marker": "legacy"},
    )
    resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-channel",
        external_id=communication_surface_binding_external_id(
            provider_key="slack-bot",
            profile_ref="communication-profile:support",
            surface_ref=surface_ref,
        ),
        title="Support",
        data_json={"marker": "canonical"},
    )

    assert _surface_data(
        session,
        project_id=project_id,
        provider_key="slack-bot",
        profile_ref="communication-profile:support",
        surface_ref=surface_ref,
    ) == {"marker": "canonical"}
    assert (
        _surface_data(
            session,
            project_id=project_id,
            provider_key="slack-bot",
            profile_ref="communication-profile:other",
            surface_ref=surface_ref,
        )
        == {}
    )

    resources.upsert_record(
        project_id=project_id,
        plugin_slug="communications",
        resource_key="communication-channel",
        external_id=communication_surface_binding_external_id(
            provider_key="slack-bot",
            profile_ref="communication-profile:repair",
            surface_ref="slack-channel:CREPAIR",
        ),
        title="Repair required",
        data_json={
            "surface_binding_state": "repair-required",
            "surface_binding_issue": "conflicting_duplicate_binding_metadata",
        },
    )

    with pytest.raises(ValidationError, match="surface binding requires repair"):
        _surface_data(
            session,
            project_id=project_id,
            provider_key="slack-bot",
            profile_ref="communication-profile:repair",
            surface_ref="slack-channel:CREPAIR",
        )
