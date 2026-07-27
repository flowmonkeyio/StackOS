from __future__ import annotations

from pathlib import Path

from stackos.actions import DEFAULT_ACTION_CONNECTORS

ROOT = Path(__file__).resolve().parents[2]
QUALITY_DOC = ROOT / "docs" / "integration-contracts" / "connector-quality.md"
CURRENT_CONNECTORS_DOC = ROOT / "docs" / "integration-contracts" / "current-connectors.md"
S3_DOC = ROOT / "docs" / "integration-contracts" / "s3.md"


def test_every_registered_connector_has_quality_gate_row() -> None:
    text = QUALITY_DOC.read_text(encoding="utf-8")

    for heading in (
        "Validation",
        "Errors",
        "Pagination/status",
        "Rate limits/budget",
        "Contract docs",
        "Current signoff",
    ):
        assert heading in text

    for connector_key in DEFAULT_ACTION_CONNECTORS.list_keys():
        assert f"| `{connector_key}` |" in text


def test_quality_gate_points_agents_to_release_signoff() -> None:
    text = QUALITY_DOC.read_text(encoding="utf-8")

    assert "../release-signoff.md" in text
    assert "Agents are the primary users" in text


def test_s3_contract_and_inventory_match_executable_surface() -> None:
    quality = QUALITY_DOC.read_text(encoding="utf-8")
    current = CURRENT_CONNECTORS_DOC.read_text(encoding="utf-8")
    contract = S3_DOC.read_text(encoding="utf-8")

    assert "| `aws-s3` |" in quality
    assert "Executable with locked-SDK model" in quality
    assert "| Amazon S3 |" in current
    assert "| `aws-s3` | `utils.s3.directory.list`" in current
    assert "disposable live-bucket smoke remains release evidence" in current
    assert "not yet part of the current executable surface" not in current
    for phrase in (
        "general-purpose buckets only",
        "One StackOS Account binds one bucket",
        "AWS IAM and bucket policy",
        "explicit daemon-held credentials",
        "directory markers",
        "historical versions",
        "Prefix rename is not exposed",
        "generic S3-compatible",
    ):
        assert phrase in contract
    for retired_phrase in (
        "expected_bucket_owner",
        "ExpectedBucketOwner",
        "ExpectedSourceBucketOwner",
        "owner binding",
    ):
        assert retired_phrase not in contract
