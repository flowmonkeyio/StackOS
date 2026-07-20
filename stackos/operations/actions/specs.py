"""Operation registry specifications for actions."""

from __future__ import annotations

from stackos.actions import ActionDescribeOut, ActionExecutionOut, ActionValidationOut
from stackos.mcp.contract import WriteEnvelope
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)

from .discovery import action_describe, action_list, action_validate
from .execution import action_call_get, action_execute, action_run
from .schemas import (
    ACTION_CALL_POLL_RESPONSE_POLICY,
    ACTION_FILE_OUTPUT_RESPONSE_POLICY,
    ActionCallGetInput,
    ActionCallGetOut,
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
            ),
            returns=(
                "A WriteEnvelope containing the public ActionExecutionOut.",
                "A redacted audit row linked to run_id, run_plan_id, and run_plan_step_id.",
                "For MCP and REST external provider calls, compact file path, schema_ref, "
                "schema_operation, and metadata for the sanitized request+response envelope.",
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
            ),
            returns=(
                "A redacted action-call audit id linked to the project.",
                "MCP and REST calls return compact output metadata with file path, "
                "schema_ref, schema_operation, checksum, and summaries. CLI calls return "
                "the raw redacted operation payload by default.",
            ),
            examples=(
                OperationExample(
                    title="Send one Telegram message directly",
                    arguments={
                        "action_ref": "communications.telegram-bot.message.send",
                        "confirm_direct": True,
                        "intent_summary": "User asked to send one status message.",
                        "idempotency_key": "telegram-send-status-1",
                        "input_json": {
                            "profile_key": "support",
                            "chat_ref": "telegram-chat:123",
                            "text": "Done.",
                        },
                    },
                ),
            ),
            grant_policy="direct-action-policy",
            response_policy=ACTION_FILE_OUTPUT_RESPONSE_POLICY,
        ),
    ]


__all__ = ["operation_specs"]
