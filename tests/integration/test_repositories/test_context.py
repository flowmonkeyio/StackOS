"""Repository tests for StackOS project memory primitives."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session

from stackos.context import ContextRepository
from stackos.db.models import ActionCall, ActionCallStatus, Run, RunKind, RunStatus
from stackos.repositories.agent_requests import AgentRequestRepository
from stackos.repositories.base import ValidationError
from stackos.repositories.resources import ArtifactRepository, ResourceRepository


def test_context_query_projects_fields_limits_and_redacts(
    session: Session,
    project_id: int,
) -> None:
    repo = ContextRepository(session)
    created = repo.create_learning(
        project_id=project_id,
        statement="CPA improved after api_key=secret",
        domain="media-buying",
        confidence="medium",
        review_state="accepted",
        tags=["creative", "meta"],
        evidence_json={"access_token": "tok", "safe": "value"},
    ).data

    out = repo.query_context(
        project_id=project_id,
        sources=["learnings"],
        fields=["statement", "confidence", "evidence_json"],
        tags=["creative"],
        limit=1,
    )

    assert out.limit == 1
    assert out.items[0].id == created.id
    assert out.items[0].fields == {
        "statement": "CPA improved after api_key=[redacted]",
        "confidence": "medium",
        "evidence_json": {"access_token": "[redacted]", "safe": "value"},
    }
    assert out.items[0].provenance == {"table": "learnings", "id": created.id}


def test_context_query_rejects_unbounded_or_unknown_fields(
    session: Session,
    project_id: int,
) -> None:
    repo = ContextRepository(session)

    try:
        repo.query_context(project_id=project_id, sources=["learnings"], limit=51)
    except ValidationError as exc:
        assert exc.data["max"] == 50
    else:  # pragma: no cover
        raise AssertionError("expected limit validation")

    try:
        repo.query_context(
            project_id=project_id,
            sources=["learnings"],
            fields=["statement", "raw_payload"],
        )
    except ValidationError as exc:
        assert exc.data == {"source": "learnings", "fields": ["raw_payload"]}
    else:  # pragma: no cover
        raise AssertionError("expected field validation")


def test_context_query_projects_filtered_resource_records(
    session: Session,
    project_id: int,
) -> None:
    record = (
        ResourceRepository(session)
        .upsert_record(
            project_id=project_id,
            plugin_slug="core",
            resource_key="learning",
            external_id="seo-research-1",
            title="SEO opportunity set",
            data_json={
                "status": "active",
                "domain": "seo",
                "tags": ["seo"],
                "keywords": ["agentic workflow"],
                "api_key": "should-not-leak",
            },
        )
        .data
    )

    out = ContextRepository(session).query_context(
        project_id=project_id,
        sources=["resources"],
        fields=["resource_key", "title", "status", "data_json", "updated_at"],
        plugin_slug="core",
        resource_keys=["learning"],
        tags=["seo"],
        domain="seo",
        statuses=["active"],
        limit=10,
    )

    assert [item.id for item in out.items] == [record.id]
    assert out.items[0].fields["resource_key"] == "learning"
    assert out.items[0].fields["status"] == "active"
    assert out.items[0].fields["data_json"]["api_key"] == "[redacted]"
    assert out.items[0].provenance == {
        "table": "resource_records",
        "id": record.id,
        "plugin_slug": "core",
        "resource_key": "learning",
    }


def test_context_query_executes_every_declared_workflow_source(
    session: Session,
    project_id: int,
) -> None:
    artifact = (
        ArtifactRepository(session)
        .create(
            project_id=project_id,
            plugin_slug="branding",
            kind="brand-voice-guide",
            uri="artifact://voice-guide",
            status="approved",
            name="Current voice guide",
            metadata_json={"tags": ["voice"], "api_key": "secret"},
        )
        .data
    )
    request = (
        AgentRequestRepository(session)
        .create(
            project_id=project_id,
            request_key="context-source-test",
            title="Customer feedback",
            body_preview="The callback failed",
            source_message_ref="message:1",
            metadata_json={
                "attachments": [{"ref": "media:1", "access_token": "secret"}],
                "tags": ["support"],
            },
        )
        .data
    )
    call = ActionCall(
        project_id=project_id,
        action_key="message.send",
        plugin_slug="communications",
        operation="send",
        status=ActionCallStatus.SUCCESS,
        response_json={"message_ref": "message:2", "access_token": "secret"},
        metadata_json={"tags": ["notification"]},
    )
    completed_at = datetime.now(UTC).replace(tzinfo=None)
    run = Run(
        project_id=project_id,
        kind=RunKind.RUN_PLAN,
        status=RunStatus.SUCCESS,
        ended_at=completed_at,
    )
    session.add(call)
    session.add(run)
    session.commit()
    session.refresh(call)
    session.refresh(run)

    repo = ContextRepository(session)
    artifacts = repo.query_context(
        project_id=project_id,
        sources=["artifacts"],
        fields=["name", "kind", "status", "uri", "metadata_json", "updated_at"],
        plugin_slug="branding",
        statuses=["approved"],
        tags=["voice"],
    )
    requests = repo.query_context(
        project_id=project_id,
        sources=["agent_requests"],
        fields=[
            "title",
            "body_preview",
            "source_message_ref",
            "attachments",
            "status",
            "updated_at",
        ],
        tags=["support"],
    )
    calls = repo.query_context(
        project_id=project_id,
        sources=["action_calls"],
        fields=["action_key", "status", "response_json", "created_at"],
        plugin_slug="communications",
        statuses=["success"],
        tags=["notification"],
    )
    runs = repo.query_context(
        project_id=project_id,
        sources=["runs"],
        fields=["kind", "status", "completed_at"],
        statuses=["success"],
    )

    assert [item.id for item in artifacts.items] == [artifact.id]
    assert artifacts.items[0].fields["metadata_json"]["api_key"] == "[redacted]"
    assert [item.id for item in requests.items] == [request.id]
    assert requests.items[0].fields["attachments"][0]["access_token"] == "[redacted]"
    assert [item.id for item in calls.items] == [call.id]
    assert calls.items[0].fields["response_json"]["access_token"] == "[redacted]"
    assert [item.id for item in runs.items] == [run.id]
    assert runs.items[0].fields["completed_at"] == completed_at


def test_experiment_observations_and_decisions_are_stored_not_interpreted(
    session: Session,
    project_id: int,
) -> None:
    repo = ContextRepository(session)
    experiment = repo.create_experiment(
        project_id=project_id,
        key="creative-test",
        name="Creative Test",
        domain="media-buying",
        hypothesis="Founder-led creative reduces CPA.",
        status="running",
        metric_targets_json={"primary": "cpa", "client_secret": "secret"},
        variants=[
            {"key": "founder", "name": "Founder"},
            {"key": "demo", "name": "Demo", "metadata_json": {"api_key": "secret"}},
        ],
    ).data

    observation = repo.record_observation(
        project_id=project_id,
        experiment_id=experiment.id,
        variant_key="founder",
        metrics_json={"cpa": 42.0, "authorization": "Bearer secret"},
        summary="Early data; no winner selected.",
    ).data
    decision = repo.record_experiment_decision(
        project_id=project_id,
        experiment_id=experiment.id,
        decision="Continue collecting data; do not pick a winner.",
        experiment_status="running",
        tags=["creative"],
    ).data

    assert experiment.metric_targets_json == {"primary": "cpa", "client_secret": "[redacted]"}
    assert experiment.variants[1].metadata_json == {"api_key": "[redacted]"}
    assert observation.metrics_json == {"cpa": 42.0, "authorization": "[redacted]"}
    assert decision.decision == "Continue collecting data; do not pick a winner."

    experiments = repo.query_experiments(project_id=project_id, status="running").items
    assert [item.id for item in experiments] == [experiment.id]
    decisions = repo.query_decisions(project_id=project_id, tags=["creative"]).items
    assert [item.id for item in decisions] == [decision.id]


def test_context_query_filters_experiments_by_tags(
    session: Session,
    project_id: int,
) -> None:
    repo = ContextRepository(session)
    repo.create_experiment(
        project_id=project_id,
        hypothesis="Offer A improves CVR.",
        status="running",
        metadata_json={"tags": ["offer"]},
    )
    kept = repo.create_experiment(
        project_id=project_id,
        hypothesis="Creative A improves CTR.",
        status="running",
        metadata_json={"tags": ["creative"]},
    ).data

    out = repo.query_context(
        project_id=project_id,
        sources=["experiments"],
        fields=["hypothesis", "status"],
        tags=["creative"],
        limit=10,
    )

    assert [item.id for item in out.items] == [kept.id]


def test_tag_filter_pagination_does_not_miss_older_matches(
    session: Session,
    project_id: int,
) -> None:
    repo = ContextRepository(session)
    target = repo.create_learning(
        project_id=project_id,
        statement="Older but relevant",
        tags=["target"],
    ).data
    for idx in range(60):
        repo.create_learning(
            project_id=project_id,
            statement=f"Noise {idx}",
            tags=["noise"],
        )

    out = repo.query_learnings(project_id=project_id, tags=["target"], limit=1)
    context_out = repo.query_context(
        project_id=project_id,
        sources=["learnings"],
        tags=["target"],
        limit=1,
    )

    assert [item.id for item in out.items] == [target.id]
    assert out.total_estimate == 1
    assert [item.id for item in context_out.items] == [target.id]


def test_individual_query_context_outputs_are_projected(
    session: Session,
    project_id: int,
) -> None:
    repo = ContextRepository(session)
    learning = repo.create_learning(
        project_id=project_id,
        statement="Specific claim",
        evidence_json={"safe": "hidden unless requested"},
    ).data
    decision = repo.record_decision(
        project_id=project_id,
        decision="Pause this path",
        rationale="Sensitive rationale",
    ).data

    learning_out = repo.query_learning_context(
        project_id=project_id,
        fields=["statement"],
        limit=5,
    )
    decision_out = repo.query_decision_context(
        project_id=project_id,
        fields=["decision"],
        limit=5,
    )

    assert learning_out.items[0].id == learning.id
    assert learning_out.items[0].fields == {"statement": "Specific claim"}
    assert decision_out.items[0].id == decision.id
    assert decision_out.items[0].fields == {"decision": "Pause this path"}


def test_context_snapshot_records_timeline_event(
    session: Session,
    project_id: int,
) -> None:
    repo = ContextRepository(session)
    snapshot = repo.create_snapshot(
        project_id=project_id,
        name="Launch context",
        query_json={"sources": ["learnings"]},
        selected_sources_json=[{"source": "learning", "id": 1}],
        summary_json={"authorization": "Bearer secret"},
    ).data

    events = repo.timeline(project_id=project_id).items

    assert snapshot.summary_json == {"authorization": "[redacted]"}
    assert events[0].event_type == "context.snapshot"
    assert events[0].metadata_json == {"snapshot_id": snapshot.id}
