"""Compatibility import surface for StackOS SQLModel table declarations.

Importing this module imports every semantic model module so
``SQLModel.metadata`` remains complete for Alembic autogenerate, app startup,
and existing repository/test imports. The table classes themselves live in
``stackos.db.model_*`` modules grouped by domain.

Persistence rules:

- Enums are stored as TEXT (``native_enum=False``) so SQLite gets the
  canonical string value, not a numeric ordinal.
- Foreign keys stay declared at the SQLModel ``Field`` level, with explicit
  SQLAlchemy columns only where ON DELETE behavior differs from RESTRICT.
- Composite and unique indexes remain on the model classes so metadata
  introspection matches the on-disk database after autogenerate checks.
"""

from __future__ import annotations

from stackos.db.model_auth import (
    AuthProvider,
    Credential,
    CredentialAccount,
    CredentialRefreshEvent,
    CredentialScope,
    CredentialUsageEvent,
    IntegrationBudget,
    IntegrationCredential,
    OAuthState,
)
from stackos.db.model_context import (
    ContextIndexEntry,
    ContextSnapshot,
    Decision,
    Experiment,
    ExperimentObservation,
    ExperimentVariant,
    Learning,
    MetricSnapshot,
)
from stackos.db.model_core import (
    Action,
    ActionCall,
    ActionVersion,
    Capability,
    Plugin,
    Project,
    ProjectPlugin,
    Provider,
)
from stackos.db.model_enums import (
    ACTION_CALL_STATUS_TRANSITIONS,
    AGENT_REQUEST_STATUS_TRANSITIONS,
    APPROVAL_REQUEST_STATUS_TRANSITIONS,
    RUN_PLAN_STATUS_TRANSITIONS,
    RUN_PLAN_STEP_STATUS_TRANSITIONS,
    RUN_STATUS_TRANSITIONS,
    TRACKER_ITEM_STATUS_TRANSITIONS,
    ActionCallStatus,
    AgentRequestAttentionStatus,
    AgentRequestStatus,
    ApprovalRequestStatus,
    PluginSource,
    RunKind,
    RunPlanStatus,
    RunPlanStepStatus,
    RunStatus,
    RunStepStatus,
    TrackerItemStatus,
    TrackerLinkKind,
    TrackerSourceKind,
    TrackerTicketKind,
)
from stackos.db.model_resources import (
    Artifact,
    ProjectEvent,
    Resource,
    ResourceRecord,
)
from stackos.db.model_runtime import (
    AgentRequest,
    AgentSession,
    IdempotencyKey,
    Run,
    RunStep,
    RunStepCall,
    ScheduledJob,
    WorkspaceBinding,
)
from stackos.db.model_tracker import (
    TaskTracker,
    TaskTrackerLane,
    TaskTrackerPriority,
    TrackerRevision,
    TrackerTask,
    TrackerTicket,
    TrackerTicketDependency,
    TrackerTicketLink,
    TrackerTicketReference,
    TrackerTombstone,
)
from stackos.db.model_workflows import (
    ApprovalRequest,
    ProjectWorkflowTemplate,
    RunPlan,
    RunPlanStep,
    WorkflowTemplate,
    WorkflowTemplateVersion,
)

__all__ = [
    "ACTION_CALL_STATUS_TRANSITIONS",
    "AGENT_REQUEST_STATUS_TRANSITIONS",
    "APPROVAL_REQUEST_STATUS_TRANSITIONS",
    "RUN_PLAN_STATUS_TRANSITIONS",
    "RUN_PLAN_STEP_STATUS_TRANSITIONS",
    "RUN_STATUS_TRANSITIONS",
    "TRACKER_ITEM_STATUS_TRANSITIONS",
    "Action",
    "ActionCall",
    "ActionCallStatus",
    "ActionVersion",
    "AgentRequest",
    "AgentRequestAttentionStatus",
    "AgentRequestStatus",
    "AgentSession",
    "ApprovalRequest",
    "ApprovalRequestStatus",
    "Artifact",
    "AuthProvider",
    "Capability",
    "ContextIndexEntry",
    "ContextSnapshot",
    "Credential",
    "CredentialAccount",
    "CredentialRefreshEvent",
    "CredentialScope",
    "CredentialUsageEvent",
    "Decision",
    "Experiment",
    "ExperimentObservation",
    "ExperimentVariant",
    "IdempotencyKey",
    "IntegrationBudget",
    "IntegrationCredential",
    "Learning",
    "MetricSnapshot",
    "OAuthState",
    "Plugin",
    "PluginSource",
    "Project",
    "ProjectEvent",
    "ProjectPlugin",
    "ProjectWorkflowTemplate",
    "Provider",
    "Resource",
    "ResourceRecord",
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
    "TaskTracker",
    "TaskTrackerLane",
    "TaskTrackerPriority",
    "TrackerItemStatus",
    "TrackerLinkKind",
    "TrackerRevision",
    "TrackerSourceKind",
    "TrackerTask",
    "TrackerTicket",
    "TrackerTicketDependency",
    "TrackerTicketKind",
    "TrackerTicketLink",
    "TrackerTicketReference",
    "TrackerTombstone",
    "WorkflowTemplate",
    "WorkflowTemplateVersion",
    "WorkspaceBinding",
]
