"""Operation registry specifications for actions."""

from __future__ import annotations

from stackos.actions import ActionDescribeOut, ActionExecutionOut, ActionValidationOut
from stackos.actions.repository.durable import DurableActionJobOut
from stackos.actions.repository.schema import ActionCallAuditOut
from stackos.mcp.contract import WriteEnvelope
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.repositories.base import Page

from .discovery import action_describe, action_list, action_validate
from .execution import action_call_get, action_execute, action_run
from .history import action_call_query
from .lifecycle import (
    action_call_cancel,
    action_call_items,
    action_call_pause,
    action_call_resume,
    action_call_retry,
)
from .schemas import (
    ACTION_CALL_HISTORY_RESPONSE_POLICY,
    ACTION_CALL_LIFECYCLE_RESPONSE_POLICY,
    ACTION_CALL_POLL_RESPONSE_POLICY,
    ACTION_FILE_OUTPUT_RESPONSE_POLICY,
    ActionCallControlInput,
    ActionCallDurableItemsOut,
    ActionCallGetInput,
    ActionCallGetOut,
    ActionCallItemsInput,
    ActionCallQueryInput,
    ActionCallResumeInput,
    ActionCallRetryInput,
    ActionDescribeInput,
    ActionExecuteInput,
    ActionListInput,
    ActionListOut,
    ActionRunInput,
    ActionRunOut,
    ActionValidateInput,
)


def operation_specs() -> list[OperationSpec]:
    return [
        operation_spec(
            name="actionCall.query",
            summary="Read project action history with exact date and provider filters.",
            input_model=ActionCallQueryInput,
            output_model=Page[ActionCallAuditOut],
            handler=action_call_query,
            response_policy=ACTION_CALL_HISTORY_RESPONSE_POLICY,
            purpose="Inspect filtered action history or one exact project-scoped action call.",
            grant_policy="direct-read",
            mutating=False,
            examples=(
                OperationExample(
                    title="Inspect one action", arguments={"project_id": 1, "action_call_id": 42}
                ),
            ),
            returns=(
                "Cursor-paginated canonical safe audit rows and the exact filtered total; "
                "default excludes dry runs.",
            ),
        ),
        OperationSpec(
            name="action.list",
            summary="List or search action contracts with compact availability state.",
            input_model=ActionListInput,
            output_model=ActionListOut,
            handler=action_list,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.list/call",
                ),
                cli=OperationSurface(enabled=True, command="actions list"),
            ),
            purpose=(
                "Use this when an agent needs to discover currently usable action refs without "
                "walking plugin manifests, broad catalog payloads, or disconnected provider noise."
            ),
            when_to_use=(
                (
                    "An agent knows a plugin, provider, capability, or search term "
                    "and needs candidate actions."
                ),
                "A caller needs project-aware executable/blocked state for many actions at once.",
                (
                    "A setup/admin caller needs hidden disconnected or non-executable "
                    "external-provider actions and can pass "
                    "include_unavailable_integrations=true deliberately."
                ),
            ),
            prerequisites=(
                (
                    "Pass project_id when project-specific credential, budget, and plugin "
                    "availability matters."
                ),
                (
                    "Use action.describe for the exact schema and connector details before "
                    "executing an action."
                ),
            ),
            returns=(
                (
                    "Compact action summaries with action_ref, provider/capability, risk, "
                    "operation, and availability."
                ),
                (
                    "Availability state includes executable, credential_state, budget_state, "
                    "and model-readable reasons."
                ),
                (
                    "Exposure state says whether the action is visible in normal discovery "
                    "or hidden until a provider integration is connected or the action "
                    "becomes executable."
                ),
            ),
            examples=(
                OperationExample(
                    title="Find ready sitemap actions",
                    arguments={"project_id": 1, "query": "sitemap", "executable": True},
                ),
                OperationExample(
                    title="List communication Slack bot actions",
                    arguments={"plugin_slug": "communications", "provider_key": "slack-bot"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="action.describe",
            summary=(
                "Describe one action manifest, connector availability, auth state, "
                "and budget state."
            ),
            input_model=ActionDescribeInput,
            output_model=ActionDescribeOut,
            handler=action_describe,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.describe/call",
                ),
                cli=OperationSurface(enabled=True, command="actions describe"),
            ),
            purpose=(
                "Use this before a run to inspect the exact action contract and whether "
                "the current project is configured to execute it."
            ),
            when_to_use=(
                "The agent needs schema, provider, connector, credential, or budget status.",
                "A human or script wants to check why an action is not executable yet.",
            ),
            prerequisites=(
                "Pass either action_ref or plugin_slug plus action_key.",
                "Pass project_id when project-specific availability is needed.",
            ),
            returns=(
                "Static manifest details.",
                "Connector registration and executable availability.",
                "Safe credential refs and setup reasons; never plaintext secrets.",
            ),
            examples=(
                OperationExample(
                    title="Describe OpenAI image generation",
                    arguments={"project_id": 1, "action_ref": "utils.image.generate"},
                ),
            ),
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="action.validate",
            summary="Validate one explicit action payload without executing the connector.",
            input_model=ActionValidateInput,
            output_model=ActionValidationOut,
            handler=action_validate,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.validate/call",
                ),
                cli=OperationSurface(enabled=True, command="actions validate"),
            ),
            purpose=(
                "Use this to check a concrete payload against the action schema, "
                "credential policy, and connector validator before execution."
            ),
            when_to_use=(
                "The agent has chosen an action and built a candidate input payload.",
                "A script wants a dry validation gate before creating a run plan.",
            ),
            prerequisites=(
                "Pass either action_ref or plugin_slug plus action_key.",
                "Pass input_json with the exact payload the action would receive.",
                "For a non-auth sensitive string, call secret.set first and place the returned "
                "ref only as the exact {'$secret_ref': 'secret_...'} marker.",
                "Pass context_ref when reusable execution defaults should supply credential "
                "or provider context; pass credential_ref only for a deliberate direct override.",
            ),
            returns=(
                "valid=true when schema, credential policy, and connector validation pass.",
                "Structured issues with paths and machine-readable codes when validation fails.",
            ),
            examples=(
                OperationExample(
                    title="Validate sitemap fetch payload",
                    arguments={
                        "project_id": 1,
                        "action_ref": "utils.sitemap.fetch",
                        "input_json": {"urls": ["https://example.com/sitemap.xml"]},
                        "context_ref": "ctx_provider_analysis",
                    },
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
        ),
        OperationSpec(
            name="actionCall.get",
            summary="Poll one project-scoped action call for live progress or stored outcome.",
            input_model=ActionCallGetInput,
            output_model=ActionCallGetOut,
            handler=action_call_get,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/actionCall.get/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call actionCall.get"),
            ),
            purpose=(
                "Use this after a background action is accepted to inspect process-live "
                "progress while running and the persisted terminal result afterward."
            ),
            when_to_use=(
                "action.run or action.execute returned action_call_id and poll guidance.",
                "The caller needs authoritative completion or failure state.",
            ),
            prerequisites=(
                "Pass the returned action_call_id.",
                "The current workspace must resolve to the owning project, or pass project_id.",
            ),
            returns=(
                "Sanitized live progress only while the persisted call is running.",
                "Persisted terminal output or failure details, including uncertainty flags.",
            ),
            examples=(
                OperationExample(
                    title="Poll a background action call",
                    arguments={"project_id": 1, "action_call_id": 42},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            response_policy=ACTION_CALL_POLL_RESPONSE_POLICY,
        ),
        OperationSpec(
            name="actionCall.items",
            summary="List the sealed item receipts for one durable action call.",
            input_model=ActionCallItemsInput,
            output_model=ActionCallDurableItemsOut,
            handler=action_call_items,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/actionCall.items/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call actionCall.items"),
            ),
            purpose=(
                "Inspect the immutable destination snapshot and persisted delivery receipts for "
                "one accepted durable action."
            ),
            when_to_use=(
                "A caller needs item-level progress, a deferred retry time, or an unknown receipt.",
            ),
            prerequisites=("Pass the owning action_call_id and project scope.",),
            returns=("The durable job state and ordered item-level receipt records.",),
            examples=(
                OperationExample(
                    title="Inspect durable delivery items",
                    arguments={"project_id": 1, "action_call_id": 42},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            response_policy=ACTION_CALL_LIFECYCLE_RESPONSE_POLICY,
        ),
        OperationSpec(
            name="actionCall.pause",
            summary="Pause future claims for one durable action call.",
            input_model=ActionCallControlInput,
            output_model=WriteEnvelope[DurableActionJobOut],
            handler=action_call_pause,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    browser_safe=True,
                    path="/api/v1/operations/actionCall.pause/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call actionCall.pause"),
            ),
            purpose="Stop new durable-item claims while preserving exact receipts and retry state.",
            when_to_use=("An operator needs to stop future delivery attempts temporarily.",),
            prerequisites=("Pass a non-terminal durable action_call_id.",),
            returns=("The paused durable job and its item counters.",),
            examples=(
                OperationExample(
                    title="Pause a durable delivery",
                    arguments={"project_id": 1, "action_call_id": 42},
                ),
            ),
            grant_policy="direct-action-policy",
            response_policy=ACTION_CALL_LIFECYCLE_RESPONSE_POLICY,
        ),
        OperationSpec(
            name="actionCall.resume",
            summary="Resume future claims for one paused durable action call.",
            input_model=ActionCallResumeInput,
            output_model=WriteEnvelope[DurableActionJobOut],
            handler=action_call_resume,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    browser_safe=True,
                    path="/api/v1/operations/actionCall.resume/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call actionCall.resume"),
            ),
            purpose="Resume the sealed job from its persisted next-eligible item state.",
            when_to_use=(
                "An operator has reviewed a paused durable delivery and wants it to continue.",
            ),
            prerequisites=(
                "Pass a paused durable action_call_id.",
                "Direct resumes require confirm_direct=true and a concrete intent_summary.",
                "Workflow resumes require the active original step and its matching action or "
                "communication grant.",
            ),
            returns=("The resumed durable job and its item counters.",),
            examples=(
                OperationExample(
                    title="Resume a durable delivery",
                    arguments={
                        "project_id": 1,
                        "action_call_id": 42,
                        "confirm_direct": True,
                        "intent_summary": (
                            "Operator reviewed the paused delivery and approved resuming it."
                        ),
                    },
                ),
            ),
            grant_policy="direct-action-policy",
            response_policy=ACTION_CALL_LIFECYCLE_RESPONSE_POLICY,
        ),
        OperationSpec(
            name="actionCall.cancel",
            summary=(
                "Cancel the remaining receipt-proven-no-effect items of one durable action call."
            ),
            input_model=ActionCallControlInput,
            output_model=WriteEnvelope[DurableActionJobOut],
            handler=action_call_cancel,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    browser_safe=True,
                    path="/api/v1/operations/actionCall.cancel/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call actionCall.cancel"),
            ),
            purpose="End only remaining items that can still prove no provider effect occurred.",
            when_to_use=("An operator no longer wants the safe remaining delivery subset to run.",),
            prerequisites=(
                "Pass a durable action_call_id with a pending or deferred no-effect item.",
            ),
            returns=("The updated job receipt and item counters.",),
            examples=(
                OperationExample(
                    title="Cancel the safe remaining delivery items",
                    arguments={"project_id": 1, "action_call_id": 42},
                ),
            ),
            grant_policy="direct-action-policy",
            response_policy=ACTION_CALL_LIFECYCLE_RESPONSE_POLICY,
        ),
        OperationSpec(
            name="actionCall.retry",
            summary=(
                "Retry selected durable items only after their receipts prove no provider effect."
            ),
            input_model=ActionCallRetryInput,
            output_model=WriteEnvelope[DurableActionJobOut],
            handler=action_call_retry,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    browser_safe=True,
                    path="/api/v1/operations/actionCall.retry/call",
                ),
                cli=OperationSurface(enabled=True, command="ops call actionCall.retry"),
            ),
            purpose=(
                "Requeue only explicit failed, cancelled, or deferred item receipts that prove "
                "the provider never accepted a delivery."
            ),
            when_to_use=(
                "An operator has inspected specific item receipts and wants to retry only the "
                "known no-effect subset.",
            ),
            prerequisites=(
                "Pass one or more unique item_ids from this durable action call.",
                "Direct retries require confirm_direct=true and a concrete intent_summary.",
                "Workflow retries require the active original step and its matching action or "
                "communication grant.",
            ),
            returns=("The reopened durable job and its current item counters.",),
            examples=(
                OperationExample(
                    title="Retry selected no-effect delivery items",
                    arguments={
                        "project_id": 1,
                        "action_call_id": 42,
                        "item_ids": [7, 9],
                        "confirm_direct": True,
                        "intent_summary": (
                            "Operator reviewed the no-effect receipts and approved retrying them."
                        ),
                    },
                ),
            ),
            grant_policy="direct-action-policy",
            response_policy=ACTION_CALL_LIFECYCLE_RESPONSE_POLICY,
        ),
        OperationSpec(
            name="action.execute",
            summary=(
                "Execute one action inside an explicitly granted run-plan step and return "
                "the caller-surface response shape."
            ),
            input_model=ActionExecuteInput,
            output_model=WriteEnvelope[ActionExecutionOut],
            handler=action_execute,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.execute/call",
                ),
                cli=OperationSurface(enabled=True, command="actions execute"),
            ),
            purpose=(
                "Use this only after a run plan has started and the active claimed step "
                "grants the exact action ref. StackOS resolves credentials inside the daemon."
            ),
            when_to_use=(
                "A run-plan step is currently running and names the exact action_ref.",
                "The frozen run-plan grant snapshot includes action.execute for that ref.",
            ),
            prerequisites=(
                "Pass project_id and run_token from runPlan.start.",
                "Exactly one run-plan step must be running.",
                "The requested action_ref must match the step and mcp_tool_grants refs.",
                "Pass context_ref when the active task/run has a reusable execution context; "
                "pass only opaque credential_ref values for deliberate low-level overrides.",
                "For a non-auth sensitive string, call secret.set first and place the returned "
                "ref only as the exact {'$secret_ref': 'secret_...'} marker in input_json.",
                "MCP and REST calls default external provider output to a response file; "
                "inspect the returned path before rerunning the provider call. CLI calls "
                "default to raw inline output. Explicit output_policy_json and "
                "execution-context policies override the surface default.",
                "For a foreground provider read, transient output requires response_mode=raw. "
                "Only a content-free audit receipt remains linked to the run-plan step; "
                "caller-supplied idempotency keys are rejected.",
            ),
            returns=(
                "A WriteEnvelope containing the public ActionExecutionOut.",
                "A redacted audit row linked to run_id, run_plan_id, and run_plan_step_id.",
                "For MCP and REST external provider calls, compact file path, schema_ref, "
                "schema_operation, and metadata for the sanitized request+response envelope.",
                "For a transient read, only the immediate raw response contains the "
                "bounded provider result; the linked action call retains an audit receipt.",
            ),
            examples=(
                OperationExample(
                    title="Execute no-auth sitemap fetch from a run-plan step",
                    arguments={
                        "project_id": 1,
                        "run_token": "run-plan-token",
                        "action_ref": "utils.sitemap.fetch",
                        "input_json": {"urls": ["https://example.com/sitemap.xml"]},
                    },
                ),
            ),
            grant_policy="run-plan-step-action-ref",
            response_policy=ACTION_FILE_OUTPUT_RESPONSE_POLICY,
        ),
        OperationSpec(
            name="action.run",
            summary=(
                "Run one explicit action directly with caller-surface response shaping and audit."
            ),
            input_model=ActionRunInput,
            output_model=WriteEnvelope[ActionRunOut],
            handler=action_run,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/operations/action.run/call",
                ),
                cli=OperationSurface(enabled=True, command="actions run"),
            ),
            purpose=(
                "Use this for a single explicit tool action when no multi-step workflow "
                "is needed. StackOS still validates inputs, resolves daemon-held "
                "credentials, enforces provider/profile policy, and records action audit."
            ),
            when_to_use=(
                "The user asked for one concrete action, such as sending one message.",
                "The work does not need a template, multi-step plan, artifacts, or learning loop.",
            ),
            prerequisites=(
                "The current workspace must resolve to a project, or pass project_id.",
                "Pass context_ref when the project/task has reusable execution defaults; "
                "pass only opaque credential_ref values for deliberate low-level overrides.",
                "For a non-auth sensitive string, call secret.set first and place the returned "
                "ref only as the exact {'$secret_ref': 'secret_...'} marker in input_json.",
                "For non-read actions, pass confirm_direct=true and intent_summary; "
                "pass intent_id or idempotency_key when stable retries matter. "
                "If omitted, StackOS derives a request-scoped idempotency key.",
                "MCP and REST calls default external provider output to a response file; "
                "inspect the returned path before rerunning the provider call. CLI calls "
                "default to raw inline output. Explicit output_policy_json and "
                "execution-context policies override the surface default.",
                "For an ephemeral foreground provider read, pass output_policy_json with "
                "mode=transient and response_mode=raw. The result is bounded to 256 KiB, "
                "cannot be replayed, and actionCall.get retains only an audit receipt.",
            ),
            returns=(
                "A redacted action-call audit id linked to the project.",
                "MCP and REST calls return compact output metadata with file path, "
                "schema_ref, schema_operation, checksum, and summaries. CLI calls return "
                "the raw redacted operation payload by default.",
                "A transient read returns its bounded redacted result only in the immediate "
                "raw response; the ActionCall audit contains shape and count only.",
            ),
            examples=(
                OperationExample(
                    title="Send one Telegram message directly",
                    arguments={
                        "action_ref": "communications.telegram.message.send",
                        "confirm_direct": True,
                        "intent_summary": "User asked to send one status message.",
                        "idempotency_key": "telegram-send-status-1",
                        "input_json": {
                            "profile_ref": "communication-profile:support",
                            "surface_ref": "telegram-chat:123",
                            "content": {"kind": "text", "text": "Done."},
                        },
                    },
                ),
            ),
            grant_policy="direct-action-policy",
            response_policy=ACTION_FILE_OUTPUT_RESPONSE_POLICY,
        ),
    ]


__all__ = ["operation_specs"]
