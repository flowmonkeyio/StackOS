"""Strict publication request and deny-by-default connector boundary."""

import asyncio

import pytest
from pydantic import ValidationError

from stackos.actions.connectors import ActionConnectorError
from stackos.actions.controlled_publication import ControlledPublicationConnector, _PublicationInput


def payload():
    return dict(
        canonical_item_id="task:1",
        owner_ref="fixture:lead",
        expected_epoch=1,
        expected_revision=1,
        scope_digest="a" * 64,
        intent_ref="fixture:original",
        target_ref="owned-synthetic-report-v1",
        candidate_commit_oid="a" * 40,
        candidate_report_sha256="b" * 64,
        expected_destination_oid="0" * 40,
        review_record_id=1,
        review_content_digest="c" * 64,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_epoch", True),
        ("expected_revision", 1.0),
        ("review_record_id", "1"),
        ("target_ref", "../foreign"),
        ("candidate_commit_oid", "HEAD"),
        ("candidate_commit_oid", "a" * 64),
        ("scope_digest", "bad"),
        ("argv", ["git", "push"]),
        ("env", {"HOME": "/foreign"}),
        ("path", "/foreign"),
        ("remote", "https://example.test/repo"),
    ],
)
def test_strict_request(field, value):
    with pytest.raises(ValidationError):
        _PublicationInput.model_validate({**payload(), field: value})


def test_generic_connector_cannot_publish():
    with pytest.raises(ActionConnectorError):
        asyncio.run(ControlledPublicationConnector().execute(object()))
