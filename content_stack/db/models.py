"""SQLModel declarations for the 30 content-stack tables.

Single-file layout per PLAN.md L137 / L202. Each table maps 1:1 to a
section in PLAN.md "Database schema (30 tables — full scope)". Status
enums are declared next to the table they serialise to; column-level
docstrings explain non-obvious *why* (per CLAUDE.md style guidance).

Persistence rules:

- Enums are stored as TEXT (`native_enum=False` so SQLite gets the
  string verbatim, not a numeric ordinal). The `values_callable`
  hook ensures we ship the canonical hyphenated PLAN.md spelling
  (e.g. ``aborted-publish``) and not the Python identifier.
- Foreign keys are declared at the SQLModel `Field(foreign_key=...)`
  level so SQLModel/SQLAlchemy emit them; ON DELETE behaviour is
  attached via an explicit `sa.Column(ForeignKey(..., ondelete=...))`
  where it differs from the default RESTRICT.
- Composite / partial / unique indexes that SQLModel cannot express
  declaratively (notably the partial-unique constraints from PLAN.md
  L479 / L485 + audit B-08 / B-09 / M-20) are issued by the M1
  initial-schema Alembic migration. They are *also* declared here in
  the per-table ``__table_args__`` for the indexes that SQLAlchemy
  *can* express, so introspection of `SQLModel.metadata` matches the
  on-disk database after an autogenerate diff.

State-machine invariants are enforced in the M1.B repository layer.
This module exports `ARTICLE_STATUS_TRANSITIONS` so tests can defend
the legal-transition map without duplicating it.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    LargeBinary,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel

# ---------------------------------------------------------------------------
# Enum helper
# ---------------------------------------------------------------------------


def _enum_column(
    enum_cls: type[enum.Enum],
    *,
    nullable: bool = False,
    name: str | None = None,
) -> Column[Any]:
    """Build a portable TEXT-backed Enum column.

    SQLite has no native ENUM type; using ``native_enum=False`` forces a
    CHECK-constrained TEXT column, which matches PLAN.md L383
    ("string columns, validated by pydantic"). ``values_callable``
    returns the canonical hyphenated value string (PLAN.md spellings
    like ``aborted-publish`` cannot be Python identifiers, so we keep
    the Python member ``ABORTED_PUBLISH`` while persisting the value).
    """
    return Column(
        SAEnum(
            enum_cls,
            native_enum=False,
            length=64,
            values_callable=lambda cls: [m.value for m in cls],
            name=name or f"ck_{enum_cls.__name__.lower()}",
        ),
        nullable=nullable,
    )


def _utcnow() -> datetime:
    """Naive UTC default for ``created_at`` / ``updated_at`` columns.

    SQLite stores datetimes as ISO-8601 text; we keep the value naive but
    explicitly UTC for consistency. Tests rely on a callable default so
    rows inserted in the same transaction don't share a frozen timestamp.
    """
    return datetime.now(tz=UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Enums (one block per table, ordered alphabetically by table name)
# ---------------------------------------------------------------------------


class ArticleAssetKind(enum.StrEnum):
    """Persists to ``article_assets.kind`` per PLAN.md L396."""

    HERO = "hero"
    INLINE = "inline"
    THUMBNAIL = "thumbnail"
    OG = "og"
    TWITTER = "twitter"
    INFOGRAPHIC = "infographic"
    SCREENSHOT = "screenshot"
    GALLERY = "gallery"


class ArticlePublishStatus(enum.StrEnum):
    """Persists to ``article_publishes.status`` per PLAN.md L397."""

    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"
    REVERTED = "reverted"


class ArticleStatus(enum.StrEnum):
    """Persists to ``articles.status`` per PLAN.md L388.

    Note ``aborted-publish`` (hyphen) is the canonical wire spelling —
    the Python member name is ``ABORTED_PUBLISH``.
    """

    BRIEFING = "briefing"
    OUTLINED = "outlined"
    DRAFTED = "drafted"
    EDITED = "edited"
    EEAT_PASSED = "eeat_passed"
    PUBLISHED = "published"
    REFRESH_DUE = "refresh_due"
    ABORTED_PUBLISH = "aborted-publish"


# Legal transitions used by the repository layer (M1.B). Defined here so
# unit tests can lock the map down without duplicating it.
ARTICLE_STATUS_TRANSITIONS: dict[ArticleStatus, frozenset[ArticleStatus]] = {
    ArticleStatus.BRIEFING: frozenset({ArticleStatus.OUTLINED, ArticleStatus.ABORTED_PUBLISH}),
    ArticleStatus.OUTLINED: frozenset({ArticleStatus.DRAFTED, ArticleStatus.ABORTED_PUBLISH}),
    ArticleStatus.DRAFTED: frozenset({ArticleStatus.EDITED, ArticleStatus.ABORTED_PUBLISH}),
    ArticleStatus.EDITED: frozenset(
        {
            ArticleStatus.EEAT_PASSED,
            ArticleStatus.DRAFTED,  # editor → re-draft loop on FIX verdict
            ArticleStatus.ABORTED_PUBLISH,
        }
    ),
    ArticleStatus.EEAT_PASSED: frozenset(
        {ArticleStatus.PUBLISHED, ArticleStatus.EDITED, ArticleStatus.ABORTED_PUBLISH}
    ),
    ArticleStatus.PUBLISHED: frozenset({ArticleStatus.REFRESH_DUE}),
    ArticleStatus.REFRESH_DUE: frozenset({ArticleStatus.EDITED, ArticleStatus.PUBLISHED}),
    ArticleStatus.ABORTED_PUBLISH: frozenset(),  # terminal
}


class ClusterType(enum.StrEnum):
    """Persists to ``clusters.type`` per PLAN.md L395."""

    PILLAR = "pillar"
    SPOKE = "spoke"
    HUB = "hub"
    COMPARISON = "comparison"
    RESOURCE = "resource"


class ComplianceRuleKind(enum.StrEnum):
    """Persists to ``compliance_rules.kind`` per PLAN.md L393."""

    RESPONSIBLE_GAMBLING = "responsible-gambling"
    AFFILIATE_DISCLOSURE = "affiliate-disclosure"
    JURISDICTION = "jurisdiction"
    AGE_GATE = "age-gate"
    PRIVACY = "privacy"
    TERMS = "terms"
    CUSTOM = "custom"


class CompliancePosition(enum.StrEnum):
    """Persists to ``compliance_rules.position`` per PLAN.md L394."""

    HEADER = "header"
    AFTER_INTRO = "after-intro"
    FOOTER = "footer"
    EVERY_SECTION = "every-section"
    SIDEBAR = "sidebar"
    HIDDEN_META = "hidden-meta"


class EeatCategory(enum.StrEnum):
    """Persists to ``eeat_criteria.category``.

    PLAN.md L351 narrates "E/E/A/T" but L444 enumerates the rubric using
    8 dimensions ``C, O, R, E, Exp, Ept, A, T`` and the canonical 80-item
    benchmark is structured as 8 x 10. We persist all 8 - the EEAT gate
    ("refuses to score if any dimension has 0 active items", PLAN.md
    L1620) cannot work with the collapsed 4-letter taxonomy. Surfaced
    as a deliberate tightening of the schema in the M1 implementation
    report.
    """

    C = "C"  # Contextual Clarity
    O = "O"  # Organization  # noqa: E741 — single-letter dimension code
    R = "R"  # Referenceability
    E = "E"  # Exclusivity
    EXP = "Exp"  # Experience
    EPT = "Ept"  # Expertise
    A = "A"  # Authority
    T = "T"  # Trust


class EeatTier(enum.StrEnum):
    """Persists to ``eeat_criteria.tier`` per PLAN.md L402 (D7 lock)."""

    CORE = "core"
    RECOMMENDED = "recommended"
    PROJECT = "project"


class EeatVerdict(enum.StrEnum):
    """Persists to ``eeat_evaluations.verdict`` per PLAN.md L401."""

    PASS = "pass"
    PARTIAL = "partial"
    FAIL = "fail"


class InternalLinkStatus(enum.StrEnum):
    """Persists to ``internal_links.status`` per PLAN.md L392."""

    SUGGESTED = "suggested"
    APPLIED = "applied"
    DISMISSED = "dismissed"
    BROKEN = "broken"


class ProcedureRunStepStatus(enum.StrEnum):
    """Persists to ``procedure_run_steps.status`` per PLAN.md L399."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class PublishTargetKind(enum.StrEnum):
    """Persists to ``publish_targets.kind`` per PLAN.md L398."""

    NUXT_CONTENT = "nuxt-content"
    WORDPRESS = "wordpress"
    GHOST = "ghost"
    HUGO = "hugo"
    ASTRO = "astro"
    CUSTOM_WEBHOOK = "custom-webhook"


class RedirectKind(enum.StrEnum):
    """Persists to ``redirects.kind`` per PLAN.md L404."""

    R301 = "301"
    R302 = "302"


class RunKind(enum.StrEnum):
    """Persists to ``runs.kind`` per PLAN.md L391 (16 values)."""

    PROCEDURE = "procedure"
    SKILL_RUN = "skill-run"
    GSC_PULL = "gsc-pull"
    DRIFT_CHECK = "drift-check"
    REFRESH_DETECTOR = "refresh-detector"
    EEAT_AUDIT = "eeat-audit"
    EEAT_GATE = "eeat-gate"
    PUBLISH_PUSH = "publish-push"
    MANUAL_EDIT = "manual-edit"
    CRAWL_ERROR_WATCH = "crawl-error-watch"
    HUMANIZE_PASS = "humanize-pass"
    BULK_LAUNCH = "bulk-launch"
    INTERLINK_SUGGEST = "interlink-suggest"
    SCHEDULED_JOB = "scheduled-job"
    MAINTENANCE = "maintenance"


class RunStatus(enum.StrEnum):
    """Persists to ``runs.status`` per PLAN.md L390."""

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    ABORTED = "aborted"


class RunStepStatus(enum.StrEnum):
    """Persists to ``run_steps.status`` per PLAN.md L400."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunPlanStatus(enum.StrEnum):
    """Persists to ``run_plans.status`` for StackOS run-plan lifecycle."""

    DRAFT = "draft"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class RunPlanStepStatus(enum.StrEnum):
    """Persists to ``run_plan_steps.status`` for agent-owned run plans."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ApprovalRequestStatus(enum.StrEnum):
    """Persists to ``approval_requests.status`` for explicit gates."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class PluginSource(enum.StrEnum):
    """Persists to ``plugins.source`` for StackOS catalog ownership."""

    BUILTIN = "builtin"
    REPO = "repo"
    PROJECT = "project"
    USER = "user"


class TopicIntent(enum.StrEnum):
    """Persists to ``topics.intent`` per PLAN.md L387."""

    INFORMATIONAL = "informational"
    COMMERCIAL = "commercial"
    TRANSACTIONAL = "transactional"
    NAVIGATIONAL = "navigational"
    MIXED = "mixed"


class TopicSource(enum.StrEnum):
    """Persists to ``topics.source`` per PLAN.md L386."""

    MANUAL = "manual"
    DATAFORSEO = "dataforseo"
    AHREFS = "ahrefs"
    REDDIT = "reddit"
    PAA = "paa"
    COMPETITOR_SITEMAP = "competitor-sitemap"
    GSC_OPPORTUNITY = "gsc-opportunity"
    REFRESH_DETECTOR = "refresh-detector"


class TopicStatus(enum.StrEnum):
    """Persists to ``topics.status`` per PLAN.md L385."""

    QUEUED = "queued"
    APPROVED = "approved"
    DRAFTING = "drafting"
    PUBLISHED = "published"
    REJECTED = "rejected"


# Topic state-machine. M1.B repository layer enforces transitions via
# ``validate_transition``; the legal moves are derived from PLAN.md L385 +
# procedure 4 (queued/approved are pre-draft; drafting kicks in once an
# Article row is created; published mirrors the Article terminal; rejected
# terminates without drafting).
TOPIC_STATUS_TRANSITIONS: dict[TopicStatus, frozenset[TopicStatus]] = {
    TopicStatus.QUEUED: frozenset({TopicStatus.APPROVED, TopicStatus.REJECTED}),
    TopicStatus.APPROVED: frozenset(
        {TopicStatus.DRAFTING, TopicStatus.REJECTED, TopicStatus.QUEUED}
    ),
    TopicStatus.DRAFTING: frozenset({TopicStatus.PUBLISHED, TopicStatus.REJECTED}),
    # Terminal — refresh creates a new Article version, not a new topic.
    TopicStatus.PUBLISHED: frozenset(),
    # Un-reject path for the UI escape hatch.
    TopicStatus.REJECTED: frozenset({TopicStatus.QUEUED}),
}


# Internal-link state-machine. PLAN.md L392 lists the statuses; the
# transitions encode the per-status flows from the interlinker skill (#15)
# and the article-unpublish-repair (audit M-05).
INTERNAL_LINK_STATUS_TRANSITIONS: dict[InternalLinkStatus, frozenset[InternalLinkStatus]] = {
    InternalLinkStatus.SUGGESTED: frozenset(
        {InternalLinkStatus.APPLIED, InternalLinkStatus.DISMISSED}
    ),
    InternalLinkStatus.APPLIED: frozenset(
        {InternalLinkStatus.BROKEN, InternalLinkStatus.DISMISSED}
    ),
    InternalLinkStatus.BROKEN: frozenset(
        {InternalLinkStatus.APPLIED, InternalLinkStatus.DISMISSED}
    ),
    InternalLinkStatus.DISMISSED: frozenset(),  # terminal
}


# Run lifecycle. PLAN.md L390. ``running`` is the only entry; the three
# terminal states are mutually exclusive and not re-enterable (a new run row
# is created instead).
RUN_STATUS_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.RUNNING: frozenset({RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.ABORTED}),
    RunStatus.SUCCESS: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.ABORTED: frozenset(),
}


# Run plans are sidecar execution state. They do not replace legacy procedure
# rows; they link to a normal ``runs`` audit row only once started.
RUN_PLAN_STATUS_TRANSITIONS: dict[RunPlanStatus, frozenset[RunPlanStatus]] = {
    RunPlanStatus.DRAFT: frozenset({RunPlanStatus.STARTED, RunPlanStatus.ABORTED}),
    RunPlanStatus.STARTED: frozenset(
        {RunPlanStatus.COMPLETED, RunPlanStatus.FAILED, RunPlanStatus.ABORTED}
    ),
    RunPlanStatus.COMPLETED: frozenset(),
    RunPlanStatus.FAILED: frozenset(),
    RunPlanStatus.ABORTED: frozenset(),
}


RUN_PLAN_STEP_STATUS_TRANSITIONS: dict[
    RunPlanStepStatus, frozenset[RunPlanStepStatus]
] = {
    RunPlanStepStatus.PENDING: frozenset(
        {
            RunPlanStepStatus.RUNNING,
            RunPlanStepStatus.SKIPPED,
            RunPlanStepStatus.BLOCKED,
        }
    ),
    RunPlanStepStatus.BLOCKED: frozenset(
        {RunPlanStepStatus.PENDING, RunPlanStepStatus.SKIPPED}
    ),
    RunPlanStepStatus.RUNNING: frozenset(
        {
            RunPlanStepStatus.SUCCESS,
            RunPlanStepStatus.FAILED,
            RunPlanStepStatus.SKIPPED,
        }
    ),
    RunPlanStepStatus.SUCCESS: frozenset(),
    RunPlanStepStatus.FAILED: frozenset(),
    RunPlanStepStatus.SKIPPED: frozenset(),
}


APPROVAL_REQUEST_STATUS_TRANSITIONS: dict[
    ApprovalRequestStatus, frozenset[ApprovalRequestStatus]
] = {
    ApprovalRequestStatus.PENDING: frozenset(
        {
            ApprovalRequestStatus.APPROVED,
            ApprovalRequestStatus.REJECTED,
            ApprovalRequestStatus.CANCELLED,
        }
    ),
    ApprovalRequestStatus.APPROVED: frozenset(),
    ApprovalRequestStatus.REJECTED: frozenset(),
    ApprovalRequestStatus.CANCELLED: frozenset(),
}


# ---------------------------------------------------------------------------
# Tables — order chosen so referenced parents are declared before children.
# ---------------------------------------------------------------------------


class Project(SQLModel, table=True):
    """Site registrations (PLAN.md L347).

    ``slug`` is globally unique (no ``project_id`` prefix). ``locale`` is
    singular per D3; multi-locale = separate row.
    """

    __tablename__ = "projects"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(max_length=80, unique=True, index=True)
    name: str = Field(max_length=200)
    domain: str = Field(max_length=255)
    niche: str | None = Field(default=None, max_length=200)
    locale: str = Field(max_length=16)
    is_active: bool = Field(default=False)
    schedule_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Plugin(SQLModel, table=True):
    """Installed StackOS plugin manifest metadata.

    Plugin rows are catalog state only. They describe capabilities, providers,
    actions, resources, and UI contributions; domain execution remains in
    plugin-owned manifests/connectors and grant-gated tools.
    """

    __tablename__ = "plugins"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(max_length=120, unique=True, index=True)
    name: str = Field(max_length=200)
    version: str = Field(default="0.1.0", max_length=40)
    description: str = Field(default="")
    source: PluginSource = Field(sa_column=_enum_column(PluginSource))
    manifest_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ProjectPlugin(SQLModel, table=True):
    """Project-level plugin enablement state."""

    __tablename__ = "project_plugins"
    __table_args__ = (
        UniqueConstraint("project_id", "plugin_id", name="uq_project_plugins_project_plugin"),
        Index("ix_project_plugins_project", "project_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    plugin_id: int = Field(
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    enabled: bool = Field(default=True)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    enabled_at: datetime | None = Field(default_factory=_utcnow)
    disabled_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Capability(SQLModel, table=True):
    """Capability contributed by a plugin, such as SEO publishing or images."""

    __tablename__ = "capabilities"
    __table_args__ = (
        UniqueConstraint("plugin_id", "key", name="uq_capabilities_plugin_key"),
        Index("ix_capabilities_plugin", "plugin_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    plugin_id: int = Field(
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    description: str = Field(default="")
    kind: str = Field(default="domain", max_length=80)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Provider(SQLModel, table=True):
    """External or internal provider declared by a plugin."""

    __tablename__ = "providers"
    __table_args__ = (
        UniqueConstraint("plugin_id", "key", name="uq_providers_plugin_key"),
        Index("ix_providers_plugin", "plugin_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    plugin_id: int = Field(
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    description: str = Field(default="")
    auth_type: str = Field(default="none", max_length=80)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Action(SQLModel, table=True):
    """Generic action declared by a plugin/provider.

    D02 stores schema/catalog metadata only. Execution lands in later action
    deliverables and must remain grant-gated.
    """

    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("plugin_id", "key", name="uq_actions_plugin_key"),
        Index("ix_actions_plugin", "plugin_id"),
        Index("ix_actions_provider", "provider_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    plugin_id: int = Field(
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    provider_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    description: str = Field(default="")
    capability_key: str | None = Field(default=None, max_length=160)
    risk_level: str = Field(default="read", max_length=40)
    input_schema_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    output_schema_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ActionVersion(SQLModel, table=True):
    """Versioned action manifest snapshots."""

    __tablename__ = "action_versions"
    __table_args__ = (
        UniqueConstraint("action_id", "version", name="uq_action_versions_action_version"),
        Index("ix_action_versions_action", "action_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    action_id: int = Field(
        sa_column=Column(
            ForeignKey("actions.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    version: str = Field(max_length=40)
    manifest_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class WorkflowTemplate(SQLModel, table=True):
    """Reusable workflow template catalog/storage row.

    Templates are inert configuration and instruction contracts. They do not
    execute actions or decide provider payloads; agents turn them into concrete
    run plans later.
    """

    __tablename__ = "workflow_templates"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "key",
            "source",
            name="uq_workflow_templates_project_key_source",
        ),
        Index("ix_workflow_templates_key", "key"),
        Index("ix_workflow_templates_project", "project_id"),
        Index("ix_workflow_templates_plugin", "plugin_id"),
        Index("ix_workflow_templates_source", "source"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    plugin_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    description: str = Field(default="")
    source: str = Field(max_length=40)
    origin_path: str | None = Field(default=None, max_length=1000)
    status: str = Field(default="active", max_length=40)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class WorkflowTemplateVersion(SQLModel, table=True):
    """Immutable version snapshot for a workflow template."""

    __tablename__ = "workflow_template_versions"
    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "version",
            name="uq_workflow_template_versions_template_version",
        ),
        Index("ix_workflow_template_versions_template", "template_id"),
        Index("ix_workflow_template_versions_checksum", "checksum"),
    )

    id: int | None = Field(default=None, primary_key=True)
    template_id: int = Field(
        sa_column=Column(
            ForeignKey("workflow_templates.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    version: str = Field(max_length=40)
    spec_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    checksum: str = Field(max_length=64)
    created_by: str | None = Field(default=None, max_length=200)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ProjectWorkflowTemplate(SQLModel, table=True):
    """Project-level enablement/current pointer for stored templates."""

    __tablename__ = "project_workflow_templates"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "template_id",
            name="uq_project_workflow_templates_project_template",
        ),
        Index("ix_project_workflow_templates_project", "project_id"),
        Index("ix_project_workflow_templates_template", "template_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    template_id: int = Field(
        sa_column=Column(
            ForeignKey("workflow_templates.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    active_version_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("workflow_template_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    enabled: bool = Field(default=True)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class RunPlan(SQLModel, table=True):
    """Concrete agent-authored execution plan derived from a workflow template.

    Run plans are StackOS sidecars: they freeze setup, inputs, steps, approvals,
    context references, and audit links without deleting or replacing procedure
    rows. The linked ``runs`` row is opened only when the plan starts.
    """

    __tablename__ = "run_plans"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_plans_run"),
        Index("ix_run_plans_project_status", "project_id", "status"),
        Index("ix_run_plans_run", "run_id"),
        Index("ix_run_plans_template", "template_id", "template_version_id"),
        Index("ix_run_plans_context_snapshot", "context_snapshot_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    template_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("workflow_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    template_version_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("workflow_template_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    context_snapshot_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("context_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    key: str = Field(max_length=160)
    title: str = Field(max_length=300)
    goal: str = Field(default="")
    status: RunPlanStatus = Field(sa_column=_enum_column(RunPlanStatus))
    template_key: str | None = Field(default=None, max_length=160)
    template_version: str | None = Field(default=None, max_length=40)
    template_source: str | None = Field(default=None, max_length=40)
    template_origin_path: str | None = Field(default=None, max_length=1000)
    template_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    inputs_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    selected_context_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    context_filters_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    grant_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    budget_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    policy_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    output_contract_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_by: str | None = Field(default=None, max_length=120)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)


class RunPlanStep(SQLModel, table=True):
    """Concrete step state for a run plan.

    Step rows store the action/resource/context references and caller-supplied
    payload snapshots. StackOS only validates and records them; the agent owns
    the decisions behind the plan.
    """

    __tablename__ = "run_plan_steps"
    __table_args__ = (
        UniqueConstraint("run_plan_id", "step_id", name="uq_run_plan_steps_plan_step"),
        Index("ix_run_plan_steps_plan_position", "run_plan_id", "position"),
        Index("ix_run_plan_steps_plan_status", "run_plan_id", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    run_plan_id: int = Field(
        sa_column=Column(
            ForeignKey("run_plans.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    step_id: str = Field(max_length=160)
    title: str = Field(max_length=300)
    purpose: str = Field(default="")
    position: int = Field(nullable=False)
    status: RunPlanStepStatus = Field(sa_column=_enum_column(RunPlanStepStatus))
    depends_on_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    input_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    context_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    action_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    resource_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    policy_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    approval_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    output_refs_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    instructions_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    success_criteria_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    action_payloads_json: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    expected_outputs_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    result_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = Field(default=None)
    claimed_by: str | None = Field(default=None, max_length=120)
    claimed_at: datetime | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ApprovalRequest(SQLModel, table=True):
    """Explicit approval gate state for a run plan or one of its steps."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("run_plan_id", "approval_key", name="uq_approval_requests_plan_key"),
        Index("ix_approval_requests_project_status", "project_id", "status"),
        Index("ix_approval_requests_plan", "run_plan_id"),
        Index("ix_approval_requests_step", "run_plan_step_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_plan_id: int = Field(
        sa_column=Column(
            ForeignKey("run_plans.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_plan_step_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("run_plan_steps.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    approval_key: str = Field(max_length=160)
    title: str = Field(max_length=300)
    description: str = Field(default="")
    required_when: str = Field(default="always", max_length=160)
    approver: str | None = Field(default=None, max_length=200)
    status: ApprovalRequestStatus = Field(sa_column=_enum_column(ApprovalRequestStatus))
    requested_by: str | None = Field(default=None, max_length=120)
    decided_by: str | None = Field(default=None, max_length=120)
    requested_at: datetime = Field(default_factory=_utcnow, nullable=False)
    decided_at: datetime | None = Field(default=None)
    decision_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Resource(SQLModel, table=True):
    """Resource type declared by a StackOS plugin.

    Resources are static schemas/catalog metadata. They do not decide workflow
    behavior; agents and humans decide what to write, then StackOS validates,
    stores, filters, and retrieves the records.
    """

    __tablename__ = "resources"
    __table_args__ = (
        UniqueConstraint("plugin_id", "key", name="uq_resources_plugin_key"),
        Index("ix_resources_plugin", "plugin_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    plugin_id: int = Field(
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    description: str = Field(default="")
    schema_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    ui_schema_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ResourceRecord(SQLModel, table=True):
    """Project-scoped record for a plugin-defined resource type."""

    __tablename__ = "resource_records"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "resource_id",
            "external_id",
            name="uq_resource_records_project_resource_external",
        ),
        Index("ix_resource_records_project_resource", "project_id", "resource_id"),
        Index("ix_resource_records_resource", "resource_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    resource_id: int = Field(
        sa_column=Column(
            ForeignKey("resources.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    external_id: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=300)
    data_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    provenance_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Artifact(SQLModel, table=True):
    """Generic artifact storage reference.

    Artifacts point at files, generated media, screenshots, exports, or other
    blobs produced by tools. The daemon stores references and sanitized
    metadata only; provider auth and secret material stay outside agent reach.
    """

    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_project", "project_id"),
        Index("ix_artifacts_resource_record", "resource_record_id"),
        Index("ix_artifacts_plugin", "plugin_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    plugin_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    resource_record_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("resource_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    kind: str = Field(max_length=80)
    uri: str = Field(max_length=2048)
    name: str | None = Field(default=None, max_length=300)
    mime_type: str | None = Field(default=None, max_length=160)
    size_bytes: int | None = Field(default=None)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    provenance_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class AuthProvider(SQLModel, table=True):
    """Auth provider metadata derived from plugin provider declarations."""

    __tablename__ = "auth_providers"
    __table_args__ = (
        UniqueConstraint("plugin_id", "key", name="uq_auth_providers_plugin_key"),
        Index("ix_auth_providers_plugin", "plugin_id"),
        Index("ix_auth_providers_key", "key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    plugin_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("plugins.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    key: str = Field(max_length=160)
    name: str = Field(max_length=200)
    description: str = Field(default="")
    auth_type: str = Field(default="none", max_length=80)
    scopes_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Credential(SQLModel, table=True):
    """Opaque credential reference over encrypted integration credential rows."""

    __tablename__ = "credentials"
    __table_args__ = (
        UniqueConstraint("credential_ref", name="uq_credentials_ref"),
        UniqueConstraint(
            "integration_credential_id",
            name="uq_credentials_integration_credential",
        ),
        Index("ix_credentials_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    auth_provider_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("auth_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    integration_credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("integration_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    credential_ref: str = Field(max_length=120)
    provider_key: str = Field(max_length=160)
    auth_type: str = Field(default="none", max_length=80)
    status: str = Field(default="connected", max_length=40)
    expires_at: datetime | None = Field(default=None)
    last_tested_at: datetime | None = Field(default=None)
    revoked_at: datetime | None = Field(default=None)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialScope(SQLModel, table=True):
    """Scope granted to an opaque credential reference."""

    __tablename__ = "credential_scopes"
    __table_args__ = (
        UniqueConstraint("credential_id", "scope", name="uq_credential_scopes_scope"),
        Index("ix_credential_scopes_credential", "credential_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int = Field(
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    scope: str = Field(max_length=200)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialAccount(SQLModel, table=True):
    """Provider account metadata linked to a credential reference."""

    __tablename__ = "credential_accounts"
    __table_args__ = (
        Index("ix_credential_accounts_credential", "credential_id"),
        Index("ix_credential_accounts_provider_account", "provider_account_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int = Field(
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    provider_account_id: str | None = Field(default=None, max_length=300)
    display_name: str | None = Field(default=None, max_length=300)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class OAuthState(SQLModel, table=True):
    """OAuth state nonce for local human setup flows."""

    __tablename__ = "oauth_states"
    __table_args__ = (
        UniqueConstraint("state", name="uq_oauth_states_state"),
        Index("ix_oauth_states_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    provider_key: str = Field(max_length=160)
    credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    integration_credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("integration_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    state: str = Field(max_length=200)
    redirect_uri: str | None = Field(default=None, max_length=2048)
    expires_at: datetime | None = Field(default=None)
    consumed_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialUsageEvent(SQLModel, table=True):
    """Redacted audit event for credential use or health probes."""

    __tablename__ = "credential_usage_events"
    __table_args__ = (
        Index("ix_credential_usage_events_credential", "credential_id"),
        Index("ix_credential_usage_events_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    project_id: int | None = Field(default=None)
    provider_key: str = Field(max_length=160)
    operation: str = Field(max_length=120)
    status: str = Field(max_length=40)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class CredentialRefreshEvent(SQLModel, table=True):
    """Redacted audit event for credential refresh attempts."""

    __tablename__ = "credential_refresh_events"
    __table_args__ = (
        Index("ix_credential_refresh_events_credential", "credential_id"),
        Index("ix_credential_refresh_events_project_provider", "project_id", "provider_key"),
    )

    id: int | None = Field(default=None, primary_key=True)
    credential_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    project_id: int | None = Field(default=None)
    provider_key: str = Field(max_length=160)
    status: str = Field(max_length=40)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ProjectEvent(SQLModel, table=True):
    """Append-only project memory timeline event."""

    __tablename__ = "project_events"
    __table_args__ = (
        Index("ix_project_events_project_occurred", "project_id", "occurred_at"),
        Index("ix_project_events_source", "source_type", "source_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    source_type: str = Field(max_length=80)
    source_id: int | None = Field(default=None)
    event_type: str = Field(max_length=120)
    title: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None)
    tags_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    occurred_at: datetime = Field(default_factory=_utcnow, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ContextIndexEntry(SQLModel, table=True):
    """Compact, searchable pointer into project memory."""

    __tablename__ = "context_index_entries"
    __table_args__ = (
        Index("ix_context_index_project_source", "project_id", "source_type", "source_id"),
        Index("ix_context_index_project_occurred", "project_id", "occurred_at"),
        Index("ix_context_index_domain_status", "domain", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    source_type: str = Field(max_length=80)
    source_id: int | None = Field(default=None)
    title: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None)
    domain: str | None = Field(default=None, max_length=120)
    provider_key: str | None = Field(default=None, max_length=160)
    status: str | None = Field(default=None, max_length=80)
    tags_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    occurred_at: datetime = Field(default_factory=_utcnow, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ContextSnapshot(SQLModel, table=True):
    """Immutable record of context loaded for a run or operator review."""

    __tablename__ = "context_snapshots"
    __table_args__ = (
        Index("ix_context_snapshots_project", "project_id"),
        Index("ix_context_snapshots_run", "run_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    name: str | None = Field(default=None, max_length=300)
    query_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    selected_sources_json: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSON),
    )
    summary_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Learning(SQLModel, table=True):
    """Project-level learning candidate or accepted learning."""

    __tablename__ = "learnings"
    __table_args__ = (
        Index("ix_learnings_project_status", "project_id", "status", "review_state"),
        Index("ix_learnings_domain", "domain"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    source_snapshot_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("context_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    supersedes_learning_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("learnings.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    statement: str
    domain: str | None = Field(default=None, max_length=120)
    confidence: str = Field(default="unknown", max_length=40)
    status: str = Field(default="active", max_length=40)
    review_state: str = Field(default="proposed", max_length=40)
    created_by: str | None = Field(default=None, max_length=120)
    tags_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    applies_to_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    evidence_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Experiment(SQLModel, table=True):
    """Project-level experiment that may span multiple runs/providers."""

    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_experiments_project_key"),
        Index("ix_experiments_project_status", "project_id", "status"),
        Index("ix_experiments_domain", "domain"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str | None = Field(default=None, max_length=160)
    name: str | None = Field(default=None, max_length=300)
    domain: str | None = Field(default=None, max_length=120)
    hypothesis: str
    status: str = Field(default="planned", max_length=60)
    linked_template_ids_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    linked_run_ids_json: list[int] | None = Field(default=None, sa_column=Column(JSON))
    metric_targets_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    decision_policy_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ExperimentVariant(SQLModel, table=True):
    """Experiment arm metadata supplied by an agent or human."""

    __tablename__ = "experiment_variants"
    __table_args__ = (
        UniqueConstraint("experiment_id", "key", name="uq_experiment_variants_key"),
        Index("ix_experiment_variants_experiment", "experiment_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    experiment_id: int = Field(
        sa_column=Column(
            ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    key: str = Field(max_length=160)
    name: str | None = Field(default=None, max_length=300)
    resources_json: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ExperimentObservation(SQLModel, table=True):
    """Observed experiment data supplied by a tool, agent, or human."""

    __tablename__ = "experiment_observations"
    __table_args__ = (
        Index("ix_experiment_observations_experiment", "experiment_id", "observed_at"),
        Index("ix_experiment_observations_project", "project_id", "observed_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    experiment_id: int = Field(
        sa_column=Column(
            ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    variant_key: str | None = Field(default=None, max_length=160)
    metrics_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    summary: str | None = Field(default=None)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    observed_at: datetime = Field(default_factory=_utcnow, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Decision(SQLModel, table=True):
    """Explicit decision record supplied by an agent or human."""

    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_project", "project_id", "created_at"),
        Index("ix_decisions_experiment", "experiment_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    experiment_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("experiments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    title: str | None = Field(default=None, max_length=300)
    decision: str
    rationale: str | None = Field(default=None)
    status: str = Field(default="recorded", max_length=60)
    decided_by: str | None = Field(default=None, max_length=120)
    tags_json: list[str] | None = Field(default=None, sa_column=Column(JSON))
    evidence_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class MetricSnapshot(SQLModel, table=True):
    """Point-in-time metric value linked to a project/source."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        Index("ix_metric_snapshots_project_metric", "project_id", "metric_key", "captured_at"),
        Index("ix_metric_snapshots_source", "source_type", "source_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    source_type: str | None = Field(default=None, max_length=80)
    source_id: int | None = Field(default=None)
    metric_key: str = Field(max_length=160)
    metric_value: float | None = Field(default=None)
    dimensions_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    captured_at: datetime = Field(default_factory=_utcnow, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class VoiceProfile(SQLModel, table=True):
    """Voice/tone variant per project (PLAN.md L348).

    The articles snapshot ``voice_id`` at brief time and copy the resolved
    text into ``articles.brief_json``; later edits to the row do not
    retroactively rewrite drafts.
    """

    __tablename__ = "voice_profiles"
    __table_args__ = (Index("ix_voice_profiles_project", "project_id"),)

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    name: str = Field(max_length=120)
    voice_md: str = Field(default="")
    is_default: bool = Field(default=False)
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Author(SQLModel, table=True):
    """Per-article author attribution (PLAN.md L349).

    Required for E-E-A-T `Experience`/`Expertise` dimensions. Self-describing
    JSON columns drive ``schema.org/Person`` JSON-LD emission.
    """

    __tablename__ = "authors"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_authors_project_slug"),
        Index("ix_authors_project", "project_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    name: str = Field(max_length=200)
    slug: str = Field(max_length=80)
    bio_md: str | None = Field(default=None)
    headshot_url: str | None = Field(default=None, max_length=2048)
    role: str | None = Field(default=None, max_length=120)
    credentials_md: str | None = Field(default=None)
    social_links_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    schema_person_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ComplianceRule(SQLModel, table=True):
    """RG / affiliate / jurisdiction / age-gate rules (PLAN.md L350).

    ``params_json`` carries structured fields for built-in kinds; ``validator``
    names a registered Python callable (built-ins for predefined kinds; required
    for ``custom``).
    """

    __tablename__ = "compliance_rules"
    __table_args__ = (Index("ix_compliance_rules_project", "project_id"),)

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    kind: ComplianceRuleKind = Field(sa_column=_enum_column(ComplianceRuleKind))
    title: str = Field(max_length=200)
    body_md: str = Field(default="")
    jurisdictions: str | None = Field(default=None, max_length=500)
    position: CompliancePosition = Field(sa_column=_enum_column(CompliancePosition))
    params_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    validator: str | None = Field(default=None, max_length=120)
    is_active: bool = Field(default=True)


class EeatCriterion(SQLModel, table=True):
    """Per-project quality gate items (PLAN.md L351, D7 lock).

    Rows with ``tier=core`` are non-deactivatable / non-non-required (repository
    invariant + 422). T04, C01, R10 are seeded as ``tier='core'``.
    """

    __tablename__ = "eeat_criteria"
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_eeat_criteria_project_code"),
        Index("ix_eeat_criteria_project", "project_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    code: str = Field(max_length=16)
    category: EeatCategory = Field(sa_column=_enum_column(EeatCategory))
    description: str = Field(default="")
    text: str = Field(default="")
    weight: int = Field(default=10)
    required: bool = Field(default=False)
    active: bool = Field(default=True)
    tier: EeatTier = Field(sa_column=_enum_column(EeatTier))
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Cluster(SQLModel, table=True):
    """Topical map (PLAN.md L352).

    Self-referential ``parent_id`` permits arbitrary nesting (pillar→hub→spoke).
    """

    __tablename__ = "clusters"
    __table_args__ = (Index("ix_clusters_project", "project_id"),)

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    name: str = Field(max_length=200)
    type: ClusterType = Field(sa_column=_enum_column(ClusterType))
    parent_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("clusters.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Topic(SQLModel, table=True):
    """Topic queue with provenance (PLAN.md L353).

    ``priority`` 0-100 (NULL=50, higher=sooner); tiebreaker
    ``(priority DESC, created_at ASC, id ASC)`` is enforced by
    ``idx_topics_queue`` (PLAN.md L471).
    """

    __tablename__ = "topics"
    __table_args__ = (
        # Hot-path queue index per PLAN.md L471; the trailing ASC on
        # created_at is the SQLite default.
        Index(
            "idx_topics_queue",
            "project_id",
            "status",
            "priority",
            "created_at",
        ),
        CheckConstraint(
            "(priority IS NULL) OR (priority BETWEEN 0 AND 100)",
            name="ck_topics_priority_range",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    cluster_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("clusters.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    title: str = Field(max_length=300)
    primary_kw: str = Field(default="", max_length=300)
    secondary_kws: list[str] | None = Field(default=None, sa_column=Column(JSON))
    intent: TopicIntent = Field(sa_column=_enum_column(TopicIntent))
    status: TopicStatus = Field(sa_column=_enum_column(TopicStatus))
    priority: int | None = Field(default=None)
    source: TopicSource = Field(sa_column=_enum_column(TopicSource))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class PublishTarget(SQLModel, table=True):
    """Per-project publish destinations (PLAN.md L366).

    ``uq_publish_targets_primary`` enforces at most one primary at the DB
    layer; the repository keeps exactly one primary when a project has any
    target rows (PLAN.md L485 + audit B-08).
    """

    __tablename__ = "publish_targets"
    __table_args__ = (Index("ix_publish_targets_project", "project_id"),)

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    kind: PublishTargetKind = Field(sa_column=_enum_column(PublishTargetKind))
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    is_primary: bool = Field(default=False)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class WorkspaceBinding(SQLModel, table=True):
    """Daemon-owned mapping from an external repo/workspace to a project.

    Plugin-provided MCP bridges run from arbitrary site repositories. They send
    repo fingerprints and framework hints to the singleton daemon; this table
    is the durable, non-invasive binding back to ``projects``. No required
    ``.env`` / ``.mcp.json`` / repo-local content-stack file is needed.
    """

    __tablename__ = "workspace_bindings"
    __table_args__ = (
        Index("ix_workspace_bindings_project", "project_id"),
        Index("ix_workspace_bindings_git_remote", "git_remote_url"),
        UniqueConstraint("repo_fingerprint", name="uq_workspace_bindings_fingerprint"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    repo_fingerprint: str = Field(max_length=128)
    git_remote_url: str | None = Field(default=None, max_length=500)
    normalized_repo_name: str | None = Field(default=None, max_length=200)
    last_known_root: str | None = Field(default=None, max_length=1000)
    framework: str | None = Field(default=None, max_length=120)
    content_model_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)
    last_seen_at: datetime | None = Field(default=None)


class AgentSession(SQLModel, table=True):
    """Ephemeral-ish record for a plugin MCP bridge connected to the daemon."""

    __tablename__ = "agent_sessions"
    __table_args__ = (
        Index("ix_agent_sessions_project", "project_id"),
        Index("ix_agent_sessions_fingerprint", "repo_fingerprint"),
        Index("ix_agent_sessions_last_seen", "last_seen_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    workspace_binding_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("workspace_bindings.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    runtime: str = Field(default="unknown", max_length=40)
    cwd: str | None = Field(default=None, max_length=1000)
    repo_fingerprint: str | None = Field(default=None, max_length=128)
    git_remote_url: str | None = Field(default=None, max_length=500)
    thread_id: str | None = Field(default=None, max_length=160)
    client_session_id: str | None = Field(default=None, max_length=160)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    last_seen_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Run(SQLModel, table=True):
    """Top-level pipeline audit (PLAN.md L374).

    ``parent_run_id`` enables ``run.children`` / cascade abort; ``heartbeat_at``
    is the daemon-restart-orphan signal.
    """

    __tablename__ = "runs"
    __table_args__ = (
        # Primary look-ups per PLAN.md L472-L474.
        Index("idx_runs_project_started", "project_id", "started_at"),
        Index("idx_runs_parent", "parent_run_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    kind: RunKind = Field(sa_column=_enum_column(RunKind))
    parent_run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    procedure_slug: str | None = Field(default=None, max_length=120)
    client_session_id: str | None = Field(default=None, max_length=120)
    started_at: datetime = Field(default_factory=_utcnow, nullable=False)
    ended_at: datetime | None = Field(default=None)
    status: RunStatus = Field(sa_column=_enum_column(RunStatus))
    error: str | None = Field(default=None)
    heartbeat_at: datetime | None = Field(default=None)
    last_step: str | None = Field(default=None, max_length=120)
    last_step_at: datetime | None = Field(default=None)
    metadata_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class Article(SQLModel, table=True):
    """Content lifecycle row, current version only (PLAN.md L354, D6).

    ``slug`` is unique per project (``uq_article_slug``, PLAN.md L484) and
    immutable post-publish (M1.B repository invariant; documented here per
    PLAN.md L354 "CHECK + repository invariant"; we keep it as a repository
    invariant only because SQLite CHECK can't reference the row's own
    *prior* state without a trigger, which we add in M1.B alongside the
    state-machine guard).

    Publish gate: ``status='published'`` requires ``eeat_criteria_version_used
    IS NOT NULL`` AND a successful ``eeat-gate`` run with verdict ``SHIP``.
    Enforced in repository layer at M1.B.
    """

    __tablename__ = "articles"
    __table_args__ = (
        # Per PLAN.md L468 / L470. ``uq_article_slug`` ships in the migration
        # because SQLModel can't express the partial-immutable invariant.
        UniqueConstraint("project_id", "slug", name="uq_article_slug"),
        Index("idx_articles_status_project", "project_id", "status"),
        Index(
            "idx_articles_refresh_eval",
            "project_id",
            "status",
            "last_evaluated_for_refresh_at",
        ),
        Index("idx_articles_canonical_target", "canonical_target_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    topic_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("topics.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    author_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("authors.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    reviewer_author_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("authors.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    canonical_target_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("publish_targets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    owner_run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    slug: str = Field(max_length=80)
    title: str = Field(max_length=300)
    status: ArticleStatus = Field(sa_column=_enum_column(ArticleStatus))
    brief_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    outline_md: str | None = Field(default=None)
    draft_md: str | None = Field(default=None)
    edited_md: str | None = Field(default=None)
    voice_id_used: int | None = Field(default=None)
    eeat_criteria_version_used: int | None = Field(default=None)
    last_refreshed_at: datetime | None = Field(default=None)
    last_evaluated_for_refresh_at: datetime | None = Field(default=None)
    last_link_audit_at: datetime | None = Field(default=None)
    version: int = Field(default=1)
    current_step: str | None = Field(default=None, max_length=120)
    last_completed_step: str | None = Field(default=None, max_length=120)
    step_started_at: datetime | None = Field(default=None)
    step_etag: str | None = Field(default=None, max_length=36)
    lock_token: str | None = Field(default=None, max_length=36)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ArticleVersion(SQLModel, table=True):
    """Historical bodies (PLAN.md L355, D6).

    ``article.createVersion`` MCP copies the live row here BEFORE mutating;
    refresh runs append a new row.
    """

    __tablename__ = "article_versions"
    __table_args__ = (
        UniqueConstraint("article_id", "version", name="uq_article_versions_article_version"),
        Index("ix_article_versions_article", "article_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    article_id: int = Field(
        sa_column=Column(
            ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    version: int = Field(nullable=False)
    brief_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    outline_md: str | None = Field(default=None)
    draft_md: str | None = Field(default=None)
    edited_md: str | None = Field(default=None)
    frontmatter_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    published_url: str | None = Field(default=None, max_length=2048)
    published_at: datetime | None = Field(default=None)
    voice_id_used: int | None = Field(default=None)
    eeat_criteria_version_used: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    refreshed_at: datetime | None = Field(default=None)
    refresh_reason: str | None = Field(default=None, max_length=200)


class ArticleAsset(SQLModel, table=True):
    """Hero + inline images (PLAN.md L356)."""

    __tablename__ = "article_assets"
    __table_args__ = (Index("ix_article_assets_article", "article_id"),)

    id: int | None = Field(default=None, primary_key=True)
    article_id: int = Field(
        sa_column=Column(
            ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    kind: ArticleAssetKind = Field(sa_column=_enum_column(ArticleAssetKind))
    prompt: str | None = Field(default=None)
    url: str = Field(max_length=2048)
    alt_text: str | None = Field(default=None, max_length=500)
    width: int | None = Field(default=None)
    height: int | None = Field(default=None)
    position: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ArticlePublish(SQLModel, table=True):
    """Per-target publish records (PLAN.md L357).

    Target-backed publishes are unique on
    ``(article_id, target_id, version_published)``. Targetless rows are the
    operator-agent/manual publish escape hatch and are unique on
    ``(article_id, version_published)`` where ``target_id IS NULL``.
    ``id`` is a synthetic surrogate for FastAPI / repository ergonomics.
    """

    __tablename__ = "article_publishes"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "target_id",
            "version_published",
            name="uq_article_publishes_pk",
        ),
        Index(
            "uq_article_publishes_external",
            "article_id",
            "version_published",
            unique=True,
            sqlite_where=text("target_id IS NULL"),
        ),
        Index("ix_article_publishes_article", "article_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    article_id: int = Field(
        sa_column=Column(
            ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    target_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("publish_targets.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    version_published: int = Field(nullable=False)
    published_url: str | None = Field(default=None, max_length=2048)
    frontmatter_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    published_at: datetime | None = Field(default=None)
    status: ArticlePublishStatus = Field(sa_column=_enum_column(ArticlePublishStatus))
    error: str | None = Field(default=None)


class InternalLink(SQLModel, table=True):
    """Interlink graph (PLAN.md L358).

    Partial-unique index excluding ``status='dismissed'`` lives in the
    migration (audit B-09); the per-direction indexes are declarative.
    """

    __tablename__ = "internal_links"
    __table_args__ = (
        Index("idx_internal_links_from", "from_article_id"),
        Index("idx_internal_links_to", "to_article_id"),
        Index("ix_internal_links_project", "project_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    from_article_id: int = Field(
        sa_column=Column(
            ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    to_article_id: int = Field(
        sa_column=Column(
            ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    anchor_text: str = Field(max_length=300)
    position: int | None = Field(default=None)
    status: InternalLinkStatus = Field(sa_column=_enum_column(InternalLinkStatus))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ResearchSource(SQLModel, table=True):
    """Citation tracking (PLAN.md L360)."""

    __tablename__ = "research_sources"
    __table_args__ = (Index("ix_research_sources_article", "article_id"),)

    id: int | None = Field(default=None, primary_key=True)
    article_id: int = Field(
        sa_column=Column(
            ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    url: str = Field(max_length=2048)
    title: str | None = Field(default=None, max_length=500)
    snippet: str | None = Field(default=None)
    fetched_at: datetime = Field(default_factory=_utcnow, nullable=False)
    used: bool = Field(default=False)


class SchemaEmit(SQLModel, table=True):
    """JSON-LD blobs per article (PLAN.md L361).

    ``is_primary`` exactly-one-per-article is enforced in the repository
    layer at M1.B (no DB-level partial unique because SchemaEmit is also
    used for the per-project *templates* seeded with ``article_id IS NULL``;
    those templates never have ``is_primary=true``).
    """

    __tablename__ = "schema_emits"
    __table_args__ = (Index("ix_schema_emits_article", "article_id"),)

    id: int | None = Field(default=None, primary_key=True)
    article_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    type: str = Field(max_length=120)
    # `schema_json` is the canonical PLAN.md L361 column name. SQLModel /
    # pydantic's parent class exposes a `schema_json` classmethod, which
    # mypy flags as an override. The runtime works correctly (with a benign
    # UserWarning silenced in pyproject.toml's pytest filter); the
    # `type: ignore` here is the narrowest fix that does not require
    # renaming a column the spec freezes.
    schema_json: dict[str, Any] | None = Field(  # type: ignore[assignment]
        default=None, sa_column=Column(JSON)
    )
    position: int | None = Field(default=None)
    is_primary: bool = Field(default=False)
    version_published: int | None = Field(default=None)
    validated_at: datetime | None = Field(default=None)


class DriftBaseline(SQLModel, table=True):
    """Drift detection baseline (PLAN.md L362)."""

    __tablename__ = "drift_baselines"
    __table_args__ = (Index("ix_drift_baselines_article", "article_id"),)

    id: int | None = Field(default=None, primary_key=True)
    article_id: int = Field(
        sa_column=Column(
            ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    baseline_md: str = Field(default="")
    baseline_at: datetime = Field(default_factory=_utcnow, nullable=False)
    current_score: float | None = Field(default=None)


class GscMetric(SQLModel, table=True):
    """Search Console snapshots, raw, retained 90 days (PLAN.md L363)."""

    __tablename__ = "gsc_metrics"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "article_id",
            "captured_at",
            "dimensions_hash",
            name="uq_gsc_metrics_dedup",
        ),
        Index("idx_gsc_metrics_article_time", "article_id", "captured_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    article_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    captured_at: datetime = Field(default_factory=_utcnow, nullable=False)
    query: str | None = Field(default=None, max_length=500)
    query_normalized: str | None = Field(default=None, max_length=500)
    page: str | None = Field(default=None, max_length=2048)
    country: str | None = Field(default=None, max_length=8)
    device: str | None = Field(default=None, max_length=32)
    dimensions_hash: str = Field(max_length=64)
    impressions: int = Field(default=0)
    clicks: int = Field(default=0)
    ctr: float = Field(default=0.0)
    avg_position: float = Field(default=0.0)


class GscMetricDaily(SQLModel, table=True):
    """Aggregated GSC reads (PLAN.md L364)."""

    __tablename__ = "gsc_metrics_daily"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "article_id",
            "day",
            name="uq_gsc_metrics_daily",
        ),
        Index(
            "idx_gsc_metrics_daily_lookup",
            "project_id",
            "article_id",
            "day",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    article_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    day: datetime = Field(nullable=False)
    impressions_sum: int = Field(default=0)
    clicks_sum: int = Field(default=0)
    ctr_avg: float = Field(default=0.0)
    avg_position_avg: float = Field(default=0.0)
    queries_count: int = Field(default=0)


class EeatEvaluation(SQLModel, table=True):
    """Per-criterion EEAT result grain (PLAN.md L365)."""

    __tablename__ = "eeat_evaluations"
    __table_args__ = (Index("idx_eeat_evals_article", "article_id", "run_id"),)

    id: int | None = Field(default=None, primary_key=True)
    article_id: int = Field(
        sa_column=Column(
            ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    criterion_id: int = Field(
        sa_column=Column(
            ForeignKey("eeat_criteria.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_id: int = Field(
        sa_column=Column(
            ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    verdict: EeatVerdict = Field(sa_column=_enum_column(EeatVerdict))
    notes: str | None = Field(default=None)
    evaluated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class Redirect(SQLModel, table=True):
    """301/302 records (PLAN.md L359)."""

    __tablename__ = "redirects"
    __table_args__ = (Index("ix_redirects_project", "project_id"),)

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    from_url: str = Field(max_length=2048)
    to_article_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("articles.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    kind: RedirectKind = Field(sa_column=_enum_column(RedirectKind))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class IntegrationCredential(SQLModel, table=True):
    """API keys per integration (PLAN.md L367).

    ``project_id`` is nullable for global credentials. ``encrypted_payload`` +
    ``nonce`` are AES-256-GCM ciphertext; AAD is composed at the repository
    layer (M5).
    """

    __tablename__ = "integration_credentials"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "kind",
            name="uq_integration_credentials_project_kind",
        ),
        Index("ix_integration_credentials_project", "project_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    kind: str = Field(max_length=120)
    encrypted_payload: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    nonce: bytes = Field(sa_column=Column(LargeBinary(12), nullable=False))
    expires_at: datetime | None = Field(default=None)
    last_refreshed_at: datetime | None = Field(default=None)
    config_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class IntegrationBudget(SQLModel, table=True):
    """Pre-emptive cost cap + rate limit (PLAN.md L368)."""

    __tablename__ = "integration_budgets"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "kind",
            name="uq_integration_budgets_project_kind",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    kind: str = Field(max_length=120)
    monthly_budget_usd: float = Field(default=50.0)
    alert_threshold_pct: int = Field(default=80)
    current_month_spend: float = Field(default=0.0)
    current_month_calls: int = Field(default=0)
    qps: float = Field(default=1.0)
    last_reset: datetime = Field(default_factory=_utcnow, nullable=False)
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ProcedureRunStep(SQLModel, table=True):
    """One row per procedure step (PLAN.md L375)."""

    __tablename__ = "procedure_run_steps"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "step_index",
            name="uq_procedure_run_steps_run_step",
        ),
        Index("ix_procedure_run_steps_run", "run_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(
        sa_column=Column(
            ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    step_index: int = Field(nullable=False)
    step_id: str = Field(max_length=120)
    status: ProcedureRunStepStatus = Field(sa_column=_enum_column(ProcedureRunStepStatus))
    started_at: datetime | None = Field(default=None)
    ended_at: datetime | None = Field(default=None)
    output_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = Field(default=None)


class RunStep(SQLModel, table=True):
    """Per-skill audit grain (PLAN.md L376).

    ``cost_cents`` is the cost-of-truth (PLAN.md L376); ``runs.metadata_json.cost``
    is denormalised for fast UI display.
    """

    __tablename__ = "run_steps"
    __table_args__ = (Index("idx_run_steps_run", "run_id", "step_index"),)

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(
        sa_column=Column(
            ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    step_index: int = Field(nullable=False)
    skill_name: str = Field(max_length=120)
    started_at: datetime | None = Field(default=None)
    ended_at: datetime | None = Field(default=None)
    status: RunStepStatus = Field(sa_column=_enum_column(RunStepStatus))
    input_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    output_snapshot_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = Field(default=None)
    cost_cents: int = Field(default=0)
    integration_calls_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


class RunStepCall(SQLModel, table=True):
    """Per-MCP-call audit grain inside a skill step (PLAN.md L377)."""

    __tablename__ = "run_step_calls"
    __table_args__ = (Index("idx_run_step_calls_step", "run_step_id"),)

    id: int | None = Field(default=None, primary_key=True)
    run_step_id: int = Field(
        sa_column=Column(
            ForeignKey("run_steps.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    mcp_tool: str = Field(max_length=120)
    request_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    response_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    duration_ms: int | None = Field(default=None)
    error: str | None = Field(default=None)
    cost_cents: int = Field(default=0)


class IdempotencyKey(SQLModel, table=True):
    """Mutating-tool dedup (PLAN.md L378).

    UNIQUE ``(project_id, tool_name, idempotency_key)``; replays within the
    24 h window short-circuit to ``response_json``.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "tool_name",
            "idempotency_key",
            name="uq_idempotency",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    tool_name: str = Field(max_length=120)
    idempotency_key: str = Field(max_length=120)
    run_id: int | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    response_json: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow, nullable=False)


class ScheduledJob(SQLModel, table=True):
    """Per-project schedules (PLAN.md L379)."""

    __tablename__ = "scheduled_jobs"
    __table_args__ = (Index("ix_scheduled_jobs_project", "project_id"),)

    id: int | None = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    kind: str = Field(max_length=120)
    cron_expr: str = Field(max_length=120)
    next_run_at: datetime | None = Field(default=None)
    last_run_at: datetime | None = Field(default=None)
    last_run_status: str | None = Field(default=None, max_length=32)
    enabled: bool = Field(default=True)


# ---------------------------------------------------------------------------
# Public re-exports — used by tests + future repository layer.
# ---------------------------------------------------------------------------


__all__ = [
    "APPROVAL_REQUEST_STATUS_TRANSITIONS",
    "ARTICLE_STATUS_TRANSITIONS",
    "INTERNAL_LINK_STATUS_TRANSITIONS",
    "RUN_PLAN_STATUS_TRANSITIONS",
    "RUN_PLAN_STEP_STATUS_TRANSITIONS",
    "RUN_STATUS_TRANSITIONS",
    "TOPIC_STATUS_TRANSITIONS",
    "AgentSession",
    "ApprovalRequest",
    "ApprovalRequestStatus",
    "Article",
    "ArticleAsset",
    "ArticleAssetKind",
    "ArticlePublish",
    "ArticlePublishStatus",
    "ArticleStatus",
    "ArticleVersion",
    "Author",
    "Cluster",
    "ClusterType",
    "CompliancePosition",
    "ComplianceRule",
    "ComplianceRuleKind",
    "DriftBaseline",
    "EeatCategory",
    "EeatCriterion",
    "EeatEvaluation",
    "EeatTier",
    "EeatVerdict",
    "GscMetric",
    "GscMetricDaily",
    "IdempotencyKey",
    "IntegrationBudget",
    "IntegrationCredential",
    "InternalLink",
    "InternalLinkStatus",
    "ProcedureRunStep",
    "ProcedureRunStepStatus",
    "Project",
    "PublishTarget",
    "PublishTargetKind",
    "Redirect",
    "RedirectKind",
    "ResearchSource",
    "Run",
    "RunKind",
    "RunPlan",
    "RunPlanStatus",
    "RunPlanStep",
    "RunPlanStepStatus",
    "RunStatus",
    "RunStep",
    "RunStepCall",
    "RunStepStatus",
    "ScheduledJob",
    "SchemaEmit",
    "Topic",
    "TopicIntent",
    "TopicSource",
    "TopicStatus",
    "VoiceProfile",
    "WorkspaceBinding",
]
