"""Per-skill MCP tool-grant matrix per audit B-10 / PLAN.md L692-L718.

The matrix is the load-bearing seam for the security model: every MCP
tool call resolves to a skill name (via the ``run_token`` ↔
``runs.client_session_id`` lookup), then this module checks the tool
against the skill's allow-list.

The matrix keeps two different agent pathways explicit:

- ``__system__`` — the agent-owned setup and product-operation grant used
  before a run token exists. The browser UI is read-only, so these calls
  must remain available to the operator agent through MCP.
- ``__test__`` — a reserved full-grant sentinel for tests that bind it
  explicitly. Unmatched tokens never resolve to it.
- ``_test_*`` skills (e.g. ``_test_keyword_discovery``,
  ``_test_editor``) — narrow grants used by the verification tests in
  ``tests/integration/test_mcp/test_mcp_tool_grants.py``.

Real skill grants stay narrower and continue to own vendor calls,
high-cost research, and procedure-scoped work.
"""

from __future__ import annotations

from sqlmodel import Session, select

from content_stack.db.models import Run
from content_stack.mcp.errors import ToolNotGrantedError

# ---------------------------------------------------------------------------
# Sentinel skill names.
# ---------------------------------------------------------------------------


SYSTEM_SKILL = "__system__"
TEST_SKILL = "__test__"
INVALID_SKILL = "__invalid__"


# ---------------------------------------------------------------------------
# Tool-grant matrix.
# ---------------------------------------------------------------------------


# Test skills used by ``tests/integration/test_mcp/test_mcp_tool_grants.py``.
# Each entry mimics the shape M7 will produce when real SKILL.md files
# land — narrow allow-lists per skill role. Naming convention: ``_test_``
# prefix so production skill names cannot accidentally collide.
_SYSTEM_TOOLS: frozenset[str] = frozenset(
    {
        # Bootstrapping and operator-owned actions. The browser UI is an
        # observer; product-state operations that are not vendor research calls
        # must be available to the agent through the MCP toolbox before a
        # procedure run exists. Costly/vendor calls remain per-skill grants.
        "article.bulkCreate",
        "article.create",
        "article.createVersion",
        "article.get",
        "article.list",
        "article.listDueForRefresh",
        "article.listPublishes",
        "article.listVersions",
        "article.markAbortedPublish",
        "article.markDrafted",
        "article.markEeatPassed",
        "article.markPublished",
        "article.markRefreshDue",
        "article.refreshDue",
        "article.setBrief",
        "article.setDraft",
        "article.setEdited",
        "article.setOutline",
        "artifact.get",
        "artifact.query",
        "asset.create",
        "asset.list",
        "asset.remove",
        "asset.update",
        "author.create",
        "author.delete",
        "author.get",
        "author.list",
        "author.update",
        "budget.list",
        "budget.queryProject",
        "budget.set",
        "budget.update",
        "cluster.create",
        "cluster.get",
        "cluster.list",
        "compliance.add",
        "compliance.list",
        "compliance.remove",
        "compliance.update",
        "cost.queryAll",
        "cost.queryProject",
        "catalog.describe",
        "catalog.list",
        "capability.describe",
        "capability.list",
        "drift.diff",
        "drift.get",
        "drift.list",
        "drift.snapshot",
        "eeat.bulkRecord",
        "eeat.bulkSet",
        "eeat.getReport",
        "eeat.list",
        "eeat.listEvaluations",
        "eeat.record",
        "eeat.score",
        "eeat.toggle",
        "gsc.bulkIngest",
        "gsc.listDaily",
        "gsc.queryArticle",
        "gsc.queryProject",
        "gsc.rollup",
        "gscOauth.get",
        "gscOauth.start",
        "integration.list",
        "integration.remove",
        "integration.set",
        "integration.test",
        "integration.testGsc",
        "interlink.apply",
        "interlink.bulkApply",
        "interlink.dismiss",
        "interlink.list",
        "interlink.repair",
        "interlink.suggest",
        "meta.enums",
        "plugin.list",
        "project.create",
        "project.activate",
        "project.delete",
        "project.get",
        "project.getActive",
        "project.list",
        "project.setActive",
        "project.update",
        "publish.preview",
        "publish.recordExternal",
        "publish.recordPublish",
        "publish.setCanonical",
        "procedure.claimStep",
        "procedure.currentStep",
        "procedure.executeProgrammaticStep",
        "procedure.fork",
        "procedure.list",
        "procedure.recordStep",
        "procedure.resume",
        "procedure.run",
        "procedure.status",
        "redirect.create",
        "redirect.list",
        "redirect.lookup",
        "run.abort",
        "run.children",
        "run.cost",
        "run.finish",
        "run.fork",
        "run.get",
        "run.heartbeat",
        "run.insertStep",
        "run.list",
        "run.listStepCalls",
        "run.listSteps",
        "run.recordStepCall",
        "run.resume",
        "run.start",
        "provider.describe",
        "provider.list",
        "resource.get",
        "resource.query",
        "schedule.list",
        "schedule.remove",
        "schedule.set",
        "schedule.toggle",
        "schema.get",
        "schema.list",
        "schema.set",
        "schema.validate",
        "sitemap.fetch",
        "source.add",
        "source.list",
        "source.update",
        "target.add",
        "target.list",
        "target.remove",
        "target.setPrimary",
        "target.update",
        "topic.approve",
        "topic.assignCluster",
        "topic.bulkCreate",
        "topic.bulkUpdateStatus",
        "topic.create",
        "topic.get",
        "topic.list",
        "topic.reject",
        "voice.get",
        "voice.listVariants",
        "voice.set",
        "voice.setActive",
        "workspace.connect",
        "workspace.listBindings",
        "workspace.resolve",
        "workspace.startSession",
        "workspace.updateProfile",
    }
)

_TEST_KEYWORD_DISCOVERY: frozenset[str] = frozenset(
    {
        "topic.create",
        "topic.bulkCreate",
        "topic.list",
        "cluster.create",
        "cluster.list",
        "meta.enums",
    }
)

_TEST_EDITOR: frozenset[str] = frozenset(
    {
        "article.get",
        "voice.get",
        "article.setEdited",
        "compliance.list",
        "meta.enums",
    }
)

_TEST_EEAT_GATE: frozenset[str] = frozenset(
    {
        "article.get",
        "eeat.list",
        "eeat.score",
        "eeat.getReport",
        "compliance.list",
        "article.markEeatPassed",
        "meta.enums",
    }
)

_TEST_PUBLISHER: frozenset[str] = frozenset(
    {
        "article.get",
        "schema.get",
        "target.list",
        "publish.preview",
        "article.markPublished",
        "meta.enums",
    }
)


# ---------------------------------------------------------------------------
# M6.A real skill grants.
# ---------------------------------------------------------------------------
#
# Per PLAN.md L692-L718 the tool-grant matrix is the load-bearing seam
# for the security model — every skill declares the smallest set of MCP
# tools it needs, and ``check_grant`` rejects anything else. Grants for
# the five M6.A research-phase skills are below; the corresponding
# ``allowed_tools`` lists in each ``SKILL.md`` frontmatter must mirror
# this set verbatim (a startup smoke check enforces the parity).
#
# The shared ``_run_lifecycle`` set carries the four read-write tools
# every skill needs to participate in the runs audit trail; we factor
# it out so a future skill author doesn't accidentally drop one.


_RUN_LIFECYCLE: frozenset[str] = frozenset(
    {
        "run.start",
        "run.heartbeat",
        "run.finish",
        "run.recordStepCall",
        "procedure.currentStep",
        "procedure.recordStep",
    }
)


_SKILL_KEYWORD_DISCOVERY: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "cluster.list",
    "topic.bulkCreate",
    "topic.list",
    "integration.test",
    "integration.testGsc",
    "cost.queryProject",
    "dataforseo.serp",
    "dataforseo.keywordVolume",
    "dataforseo.keywordsForSite",
    "dataforseo.domainIntersection",
    "dataforseo.paa",
    "reddit.searchSubreddit",
    "reddit.topQuestions",
    "googlePaa.extract",
}


_SKILL_SERP_ANALYZER: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "article.get",
    "source.add",
    "source.list",
    "integration.test",
    "dataforseo.serp",
    "firecrawl.scrape",
    "jina.read",
}


_SKILL_TOPICAL_CLUSTER: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "cluster.create",
    "cluster.list",
    "cluster.get",
    "topic.list",
    "topic.assignCluster",
    "topic.bulkUpdateStatus",
}


_SKILL_CONTENT_BRIEF: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "cluster.get",
    "voice.get",
    "compliance.list",
    "eeat.list",
    "article.get",
    "article.create",
    "article.setBrief",
    "source.add",
    "source.list",
    "integration.test",
    "dataforseo.serp",
    "dataforseo.keywordVolume",
    "firecrawl.scrape",
    "jina.read",
}


_SKILL_COMPETITOR_SITEMAP: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "cluster.list",
    "cluster.create",
    "topic.bulkCreate",
    "topic.list",
    "integration.test",
    "sitemap.fetch",
    "ahrefs.keywordsForSite",
}


# ---------------------------------------------------------------------------
# M6.B real skill grants (content-production phase).
# ---------------------------------------------------------------------------
#
# Per PLAN.md L848-L854 the seven content-production skills (#6-#12) build
# the article from outline through humanizer pass. Each declares the
# narrowest set of tools it touches; the EEAT gate carries the unique
# eeat.bulkRecord + eeat.score + article.markEeatPassed grant so no other
# skill can mark a verdict.


_SKILL_OUTLINE: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "voice.get",
    "eeat.list",
    "article.get",
    "article.setOutline",
    "source.list",
}


_SKILL_DRAFT_INTRO: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "voice.get",
    "compliance.list",
    "eeat.list",
    "article.get",
    "article.setDraft",
    "source.list",
    "source.update",
}


_SKILL_DRAFT_BODY: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "voice.get",
    "compliance.list",
    "eeat.list",
    "article.get",
    "article.setDraft",
    "source.list",
    "source.update",
}


_SKILL_DRAFT_CONCLUSION: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "voice.get",
    "compliance.list",
    "eeat.list",
    "article.get",
    "article.setDraft",
    "article.markDrafted",
    "source.list",
    "source.update",
}


_SKILL_EDITOR: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "voice.get",
    "compliance.list",
    "eeat.list",
    "article.get",
    "article.setEdited",
    "source.list",
}


_SKILL_EEAT_GATE: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "voice.get",
    "eeat.list",
    "eeat.score",
    "eeat.bulkRecord",
    "article.get",
    "article.markEeatPassed",
    "article.markAbortedPublish",
    "compliance.list",
}


_SKILL_HUMANIZER: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "voice.get",
    "article.get",
    "article.setEdited",
}


# ---------------------------------------------------------------------------
# M6.C real skill grants (assets + publishing phase).
# ---------------------------------------------------------------------------
#
# The assets-and-publishing skills turn the EEAT-passed article into
# published artifacts. The image generator is the only skill that calls
# into the OpenAI Images integration (the wrapper handles cost recording
# and generated-asset persistence; the skill consults cost.queryProject
# pre-emptively). Procedure 4 authors agent-publish as the default
# targetless handoff and only swaps to a concrete target publisher when the
# primary target kind is wired.


_SKILL_IMAGE_GENERATOR: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "voice.get",
    "compliance.list",
    "article.get",
    "asset.create",
    "asset.list",
    "asset.update",
    "cost.queryProject",
    "integration.test",
    "openaiImages.generate",
}


_SKILL_ALT_TEXT_AUDITOR: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "article.get",
    "asset.list",
    "asset.update",
}


_SKILL_INTERLINKER: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "cluster.list",
    "topic.list",
    "article.list",
    "article.get",
    "interlink.suggest",
    "interlink.list",
    "interlink.repair",
}


_SKILL_SCHEMA_EMITTER: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "voice.get",
    "article.get",
    "asset.list",
    "author.get",
    "schema.set",
    "schema.get",
    "schema.list",
    "schema.validate",
}


_SKILL_AGENT_PUBLISH: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "article.get",
    "asset.list",
    "source.list",
    "schema.get",
    "schema.list",
    "publish.recordExternal",
}


# Publish skills share the same state-write grant set: they consume the
# same artefacts (article + assets + sources + schema_emits), call the
# target-backed publish writes, and may call article.markPublished. The
# runner maps only concrete target kinds with production-grade tool support.
# Deferred specs such as WordPress/Ghost must add real hidden media/post
# toolkit operations before the runner maps targets to them.
_SKILL_PUBLISH_BASE: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "voice.get",
    "compliance.list",
    "article.get",
    "article.markPublished",
    "asset.list",
    "source.list",
    "schema.get",
    "target.list",
    "publish.preview",
    "publish.recordPublish",
    "publish.setCanonical",
    "integration.test",
}


_SKILL_NUXT_CONTENT_PUBLISH: frozenset[str] = _SKILL_PUBLISH_BASE
_SKILL_WORDPRESS_PUBLISH: frozenset[str] = _SKILL_PUBLISH_BASE
_SKILL_GHOST_PUBLISH: frozenset[str] = _SKILL_PUBLISH_BASE


# ---------------------------------------------------------------------------
# M6.D real skill grants (ongoing-operations phase).
# ---------------------------------------------------------------------------
#
# Per PLAN.md L862-L866 the five ongoing-operations skills (#20-#24) carry
# the project from "first published" into the steady-state pipeline:
# weekly GSC opportunity finding, weekly drift / crawl-error watch,
# weekly-or-on-demand refresh detection, and the per-article content
# refresh that re-runs the production chain. The opportunity-finder, the
# drift-watch, and the crawl-error-watch are the GSC trio (procedure 6's
# weekly-gsc-review pipeline); refresh-detector is procedure 7's seed,
# and content-refresher composes the editor + humanizer + interlinker +
# schema-emitter + publish skills around an `article.createVersion`
# transaction.
#
# Skill #24 (content-refresher) inherits the publish-base grant set
# because it must drive the full re-publish chain. The drift-watch grant
# carries the four `drift.*` tools even though `drift.diff` is a
# MilestoneDeferralError until the comparison engine lands — the skill
# prose handles the deferral by falling through to capture-only mode and
# emitting a warning to the operator.


_SKILL_GSC_OPPORTUNITY_FINDER: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "cluster.list",
    "topic.list",
    "topic.bulkCreate",
    "article.list",
    "gsc.queryProject",
    "gsc.queryArticle",
    "integration.test",
    "integration.testGsc",
}


_SKILL_DRIFT_WATCH: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "article.list",
    "article.get",
    "drift.snapshot",
    "drift.list",
    "drift.get",
    "drift.diff",
    "integration.test",
    "firecrawl.scrape",
    "gsc.pagespeed",
}


_SKILL_CRAWL_ERROR_WATCH: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "article.list",
    "topic.list",
    "topic.bulkCreate",
    "gsc.inspectUrl",
    "integration.test",
    "integration.testGsc",
}


_SKILL_REFRESH_DETECTOR: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "article.list",
    "article.markRefreshDue",
    "gsc.queryArticle",
    "drift.list",
    "drift.get",
}


# Content-refresher composes the publish chain; it inherits the publish
# base set (article.markPublished + publish.recordPublish + the schema /
# target / asset reads) plus the version-management writes (createVersion
# / listVersions), the voice / compliance / EEAT reads the editor and
# the humanizer pull, and `interlink.repair` for stale-link cleanup on
# the refreshed body. The grant intentionally does NOT include
# `interlink.suggest` (the suggester runs as a separate dispatch when the
# refresh body needs new links) or `schema.validate` (the schema-emitter
# the refresher dispatches owns validation; the refresher only reads /
# rewrites schema_emits via schema.set when chaining the emitter).
_SKILL_CONTENT_REFRESHER: frozenset[str] = _RUN_LIFECYCLE | {
    "meta.enums",
    "project.get",
    "voice.get",
    "compliance.list",
    "eeat.list",
    "article.get",
    "article.list",
    "article.createVersion",
    "article.listVersions",
    "interlink.repair",
    "schema.list",
    "schema.set",
    "target.list",
    "publish.preview",
    "publish.recordPublish",
    "redirect.create",
}


# Procedure slugs are the grant bound to a run token between claimed
# skill steps. They can inspect/claim the next step and execute
# deterministic ``_programmatic/*`` helpers, but prose/content writes
# still require the concrete skill grant bound by ``procedure.claimStep``.
_PROCEDURE_CONTROL: frozenset[str] = frozenset(
    {
        "procedure.currentStep",
        "procedure.claimStep",
        "procedure.executeProgrammaticStep",
        "procedure.status",
        "run.get",
        "run.heartbeat",
        "run.listSteps",
    }
)


_PROGRAMMATIC_CONTROL: frozenset[str] = frozenset(
    {
        "procedure.currentStep",
        "procedure.executeProgrammaticStep",
        "procedure.status",
        "run.get",
        "run.heartbeat",
        "run.listSteps",
    }
)


# The matrix proper. ``__system__`` is broad enough for agent-owned
# product-state operation, but it is still explicit and excludes vendor
# research tools that must remain bound to concrete skills.
SKILL_TOOL_GRANTS: dict[str, frozenset[str]] = {
    SYSTEM_SKILL: _SYSTEM_TOOLS,
    TEST_SKILL: frozenset(),  # full grant; sentinel-checked in check_grant
    "_test_keyword_discovery": _TEST_KEYWORD_DISCOVERY,
    "_test_editor": _TEST_EDITOR,
    "_test_eeat_gate": _TEST_EEAT_GATE,
    "_test_publisher": _TEST_PUBLISHER,
    # Agent-led procedure controllers (bound between claimed skill steps).
    "01-bootstrap-project": _PROCEDURE_CONTROL,
    "02-one-site-shortcut": _PROCEDURE_CONTROL,
    "03-keyword-to-topic-queue": _PROCEDURE_CONTROL,
    "04-topic-to-published": _PROCEDURE_CONTROL,
    "05-bulk-content-launch": _PROCEDURE_CONTROL,
    "06-weekly-gsc-review": _PROCEDURE_CONTROL,
    "07-monthly-humanize-pass": _PROCEDURE_CONTROL,
    "08-add-new-site": _PROCEDURE_CONTROL,
    # Deterministic programmatic helpers. The current agent explicitly
    # invokes these through ``procedure.executeProgrammaticStep``; no
    # hidden writer or vendor session is spawned.
    "_programmatic/bootstrap-verify": _PROGRAMMATIC_CONTROL,
    "_programmatic/bulk-cost-estimator": _PROGRAMMATIC_CONTROL,
    "_programmatic/bulk-final-summary": _PROGRAMMATIC_CONTROL,
    "_programmatic/compliance-seed": _PROGRAMMATIC_CONTROL,
    "_programmatic/eeat-seed-verify": _PROGRAMMATIC_CONTROL,
    "_programmatic/gsc-pull": _PROGRAMMATIC_CONTROL,
    "_programmatic/integration-creds-prompt": _PROGRAMMATIC_CONTROL,
    "_programmatic/project-create": _PROGRAMMATIC_CONTROL,
    "_programmatic/publish-target-prompt": _PROGRAMMATIC_CONTROL,
    "_programmatic/run-child-procedure": _PROGRAMMATIC_CONTROL,
    "_programmatic/select-refresh-candidates": _PROGRAMMATIC_CONTROL,
    "_programmatic/spawn-procedure-4-batch": _PROGRAMMATIC_CONTROL,
    "_programmatic/topic-approval-pause": _PROGRAMMATIC_CONTROL,
    "_programmatic/voice-profile-prompt": _PROGRAMMATIC_CONTROL,
    "_programmatic/wait-for-children": _PROGRAMMATIC_CONTROL,
    "_programmatic/weekly-summary": _PROGRAMMATIC_CONTROL,
    # M6.A skills (research phase).
    "01-research/keyword-discovery": _SKILL_KEYWORD_DISCOVERY,
    "01-research/serp-analyzer": _SKILL_SERP_ANALYZER,
    "01-research/topical-cluster": _SKILL_TOPICAL_CLUSTER,
    "01-research/content-brief": _SKILL_CONTENT_BRIEF,
    "01-research/competitor-sitemap-shortcut": _SKILL_COMPETITOR_SITEMAP,
    # M6.B skills (content-production phase).
    "02-content/outline": _SKILL_OUTLINE,
    "02-content/draft-intro": _SKILL_DRAFT_INTRO,
    "02-content/draft-body": _SKILL_DRAFT_BODY,
    "02-content/draft-conclusion": _SKILL_DRAFT_CONCLUSION,
    "02-content/editor": _SKILL_EDITOR,
    "02-content/eeat-gate": _SKILL_EEAT_GATE,
    "02-content/humanizer": _SKILL_HUMANIZER,
    # M6.C skills (assets + publishing phase).
    "03-assets/image-generator": _SKILL_IMAGE_GENERATOR,
    "03-assets/alt-text-auditor": _SKILL_ALT_TEXT_AUDITOR,
    "04-publishing/interlinker": _SKILL_INTERLINKER,
    "04-publishing/schema-emitter": _SKILL_SCHEMA_EMITTER,
    "04-publishing/agent-publish": _SKILL_AGENT_PUBLISH,
    "04-publishing/nuxt-content-publish": _SKILL_NUXT_CONTENT_PUBLISH,
    "04-publishing/wordpress-publish": _SKILL_WORDPRESS_PUBLISH,
    "04-publishing/ghost-publish": _SKILL_GHOST_PUBLISH,
    # M6.D skills (ongoing-operations phase).
    "05-ongoing/gsc-opportunity-finder": _SKILL_GSC_OPPORTUNITY_FINDER,
    "05-ongoing/drift-watch": _SKILL_DRIFT_WATCH,
    "05-ongoing/crawl-error-watch": _SKILL_CRAWL_ERROR_WATCH,
    "05-ongoing/refresh-detector": _SKILL_REFRESH_DETECTOR,
    "05-ongoing/content-refresher": _SKILL_CONTENT_REFRESHER,
}


# ---------------------------------------------------------------------------
# Public helpers.
# ---------------------------------------------------------------------------


def is_full_grant(skill_name: str) -> bool:
    """Return ``True`` iff ``skill_name`` carries an unrestricted grant.

    Only the explicit test sentinel bypasses the whitelist. System MCP
    calls use their explicit product-operation grant in ``SKILL_TOOL_GRANTS``.
    """
    return skill_name == TEST_SKILL


def resolve_run_token(
    token: str | None,
    session: Session,
) -> tuple[Run | None, str]:
    """Resolve a request's ``run_token`` to its calling skill.

    Lookup contract:

    - ``token=None`` → ``(None, "__system__")``. Direct MCP calls without
      a run token use the explicit agent setup/product-operation allow-list.
    - ``token`` matches a row's ``runs.client_session_id`` →
      ``(run, skill_name)`` where ``skill_name`` comes from
      ``runs.metadata_json.skill_name`` if set, else falls back to the
      run's ``procedure_slug``.
    - Token does not match any row → ``(None, "__invalid__")``.
    """
    if token is None or token == "":
        return None, SYSTEM_SKILL
    row = session.exec(select(Run).where(Run.client_session_id == token)).first()
    if row is None:
        return None, INVALID_SKILL
    metadata = row.metadata_json or {}
    skill_name = metadata.get("skill_name") or row.procedure_slug or INVALID_SKILL
    return row, skill_name


def check_grant(tool_name: str, skill_name: str) -> None:
    """Raise ``ToolNotGrantedError`` if the skill cannot call the tool.

    The ``__test__`` sentinel remains a full-grant fixture escape hatch,
    but normal system/bootstrap calls use an explicit allow-list.
    """
    if is_full_grant(skill_name):
        return
    allowed = SKILL_TOOL_GRANTS.get(skill_name, frozenset())
    if tool_name in allowed:
        return
    raise ToolNotGrantedError(
        f"skill {skill_name!r} is not granted tool {tool_name!r}",
        data={
            "tool": tool_name,
            "skill": skill_name,
            "allowed": sorted(allowed),
        },
    )


__all__ = [
    "INVALID_SKILL",
    "SKILL_TOOL_GRANTS",
    "SYSTEM_SKILL",
    "TEST_SKILL",
    "check_grant",
    "is_full_grant",
    "resolve_run_token",
]
