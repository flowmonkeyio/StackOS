from __future__ import annotations

from pathlib import Path

from stackos.actions import DEFAULT_ACTION_CONNECTORS

ROOT = Path(__file__).resolve().parents[2]
QUALITY_DOC = ROOT / "docs" / "integration-contracts" / "connector-quality.md"


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
