"""Unit tests for `content_stack.db.models` enum and state-machine surface."""

from __future__ import annotations

from content_stack.db.models import (
    APPROVAL_REQUEST_STATUS_TRANSITIONS,
    ARTICLE_STATUS_TRANSITIONS,
    RUN_PLAN_STATUS_TRANSITIONS,
    RUN_PLAN_STEP_STATUS_TRANSITIONS,
    ApprovalRequestStatus,
    ArticleAssetKind,
    ArticlePublishStatus,
    ArticleStatus,
    ClusterType,
    CompliancePosition,
    ComplianceRuleKind,
    EeatCategory,
    EeatTier,
    EeatVerdict,
    InternalLinkStatus,
    ProcedureRunStepStatus,
    PublishTargetKind,
    RedirectKind,
    RunKind,
    RunPlanStatus,
    RunPlanStepStatus,
    RunStatus,
    RunStepStatus,
    TopicIntent,
    TopicSource,
    TopicStatus,
)


def _values(enum_cls: type) -> set[str]:
    return {m.value for m in enum_cls}


def test_topic_status_values() -> None:
    assert _values(TopicStatus) == {"queued", "approved", "drafting", "published", "rejected"}


def test_topic_intent_values() -> None:
    assert _values(TopicIntent) == {
        "informational",
        "commercial",
        "transactional",
        "navigational",
        "mixed",
    }


def test_topic_source_values() -> None:
    assert _values(TopicSource) == {
        "manual",
        "dataforseo",
        "ahrefs",
        "reddit",
        "paa",
        "competitor-sitemap",
        "gsc-opportunity",
        "refresh-detector",
    }


def test_article_status_values() -> None:
    assert _values(ArticleStatus) == {
        "briefing",
        "outlined",
        "drafted",
        "edited",
        "eeat_passed",
        "published",
        "refresh_due",
        "aborted-publish",
    }


def test_run_status_values() -> None:
    assert _values(RunStatus) == {"running", "success", "failed", "aborted"}


def test_run_kind_values() -> None:
    # PLAN.md L391 declares the run-kind enum.
    expected = {
        "procedure",
        "skill-run",
        "gsc-pull",
        "drift-check",
        "refresh-detector",
        "eeat-audit",
        "eeat-gate",
        "publish-push",
        "manual-edit",
        "crawl-error-watch",
        "humanize-pass",
        "bulk-launch",
        "interlink-suggest",
        "scheduled-job",
        "maintenance",
    }
    actual = _values(RunKind)
    assert actual == expected
    assert len(actual) == 15


def test_internal_link_status_values() -> None:
    assert _values(InternalLinkStatus) == {"suggested", "applied", "dismissed", "broken"}


def test_compliance_kind_values() -> None:
    assert _values(ComplianceRuleKind) == {
        "responsible-gambling",
        "affiliate-disclosure",
        "jurisdiction",
        "age-gate",
        "privacy",
        "terms",
        "custom",
    }


def test_compliance_position_values() -> None:
    assert _values(CompliancePosition) == {
        "header",
        "after-intro",
        "footer",
        "every-section",
        "sidebar",
        "hidden-meta",
    }


def test_cluster_type_values() -> None:
    assert _values(ClusterType) == {"pillar", "spoke", "hub", "comparison", "resource"}


def test_article_asset_kind_values() -> None:
    assert _values(ArticleAssetKind) == {
        "hero",
        "inline",
        "thumbnail",
        "og",
        "twitter",
        "infographic",
        "screenshot",
        "gallery",
    }


def test_article_publish_status_values() -> None:
    assert _values(ArticlePublishStatus) == {"pending", "published", "failed", "reverted"}


def test_eeat_tier_values() -> None:
    assert _values(EeatTier) == {"core", "recommended", "project"}


def test_eeat_category_values() -> None:
    # 8 dimensions per the canonical CORE-EEAT rubric (PLAN.md L444).
    assert _values(EeatCategory) == {"C", "O", "R", "E", "Exp", "Ept", "A", "T"}


def test_eeat_verdict_values() -> None:
    assert _values(EeatVerdict) == {"pass", "partial", "fail"}


def test_procedure_run_step_status_values() -> None:
    assert _values(ProcedureRunStepStatus) == {
        "pending",
        "running",
        "success",
        "failed",
        "skipped",
    }


def test_run_step_status_values() -> None:
    assert _values(RunStepStatus) == {"pending", "running", "success", "failed", "skipped"}


def test_run_plan_status_values() -> None:
    assert _values(RunPlanStatus) == {"draft", "started", "completed", "failed", "aborted"}


def test_run_plan_step_status_values() -> None:
    assert _values(RunPlanStepStatus) == {
        "pending",
        "running",
        "success",
        "failed",
        "skipped",
        "blocked",
    }


def test_approval_request_status_values() -> None:
    assert _values(ApprovalRequestStatus) == {"pending", "approved", "rejected", "cancelled"}


def test_redirect_kind_values() -> None:
    assert _values(RedirectKind) == {"301", "302"}


def test_publish_target_kind_values() -> None:
    assert _values(PublishTargetKind) == {
        "nuxt-content",
        "wordpress",
        "ghost",
        "hugo",
        "astro",
        "custom-webhook",
    }


def test_status_machine_legal_transitions_keys_cover_all_statuses() -> None:
    # Every member of ArticleStatus should appear as a key — even terminal
    # states, which map to an empty set. This defends against silently
    # forgetting to declare a transition row when a new state is added.
    assert set(ARTICLE_STATUS_TRANSITIONS.keys()) == set(ArticleStatus)


def test_status_machine_legal_transitions_only_reference_defined_statuses() -> None:
    for src, dests in ARTICLE_STATUS_TRANSITIONS.items():
        assert isinstance(src, ArticleStatus)
        for d in dests:
            assert isinstance(d, ArticleStatus)
    for src, dests in RUN_PLAN_STATUS_TRANSITIONS.items():
        assert isinstance(src, RunPlanStatus)
        for d in dests:
            assert isinstance(d, RunPlanStatus)
    for src, dests in RUN_PLAN_STEP_STATUS_TRANSITIONS.items():
        assert isinstance(src, RunPlanStepStatus)
        for d in dests:
            assert isinstance(d, RunPlanStepStatus)
    for src, dests in APPROVAL_REQUEST_STATUS_TRANSITIONS.items():
        assert isinstance(src, ApprovalRequestStatus)
        for d in dests:
            assert isinstance(d, ApprovalRequestStatus)


def test_status_machine_terminal_states_are_terminal() -> None:
    # `aborted-publish` is terminal; the only forward path from `published`
    # is the refresh loop into `refresh_due`.
    assert ARTICLE_STATUS_TRANSITIONS[ArticleStatus.ABORTED_PUBLISH] == frozenset()
    assert ARTICLE_STATUS_TRANSITIONS[ArticleStatus.PUBLISHED] == frozenset(
        {ArticleStatus.REFRESH_DUE}
    )


def test_status_machine_publish_only_via_eeat_passed() -> None:
    # No transition into PUBLISHED from anywhere except EEAT_PASSED.
    sources_to_published = {
        src for src, dests in ARTICLE_STATUS_TRANSITIONS.items() if ArticleStatus.PUBLISHED in dests
    }
    assert sources_to_published == {ArticleStatus.EEAT_PASSED, ArticleStatus.REFRESH_DUE}
