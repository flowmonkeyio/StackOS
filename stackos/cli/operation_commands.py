"""Operation-backed CLI command groups."""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer

from .api_client import (
    _api_request,
    _echo_json,
    _load_operation_arguments,
    _merge_common_arguments,
    _split_csv,
)
from .app import actions_app, agent_requests_app, ops_app, run_plans_app, tracker_app


def _operation_call(operation_name: str, arguments: dict[str, Any]) -> Any:
    return _api_request(
        "POST",
        f"/api/v1/operations/{operation_name}/call",
        body={"arguments": arguments},
    )


def _require_run_token(run_token: str | None, command_name: str) -> str:
    if run_token is None or not run_token.strip():
        raise typer.BadParameter(f"--run-token is required for {command_name}")
    return run_token.strip()


@ops_app.command(name="list")
def ops_list(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the full machine-readable operation list."),
    ] = False,
) -> None:
    """List daemon-registered operations."""
    payload = _api_request("GET", "/api/v1/operations")
    if json_output:
        _echo_json(payload)
        return
    for item in payload.get("items", []):
        surfaces = [
            name
            for name, surface in item.get("surfaces", {}).items()
            if isinstance(surface, dict) and surface.get("enabled")
        ]
        typer.echo(f"{item['name']}\t{','.join(surfaces)}\t{item['summary']}")


@ops_app.command(name="describe")
def ops_describe(
    operation_name: Annotated[str, typer.Argument(help="Operation name, e.g. action.describe")],
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the full schema and agent guidance."),
    ] = False,
) -> None:
    """Describe one operation with schemas, examples, and agent guidance."""
    payload = _api_request("GET", f"/api/v1/operations/{operation_name}")
    if json_output:
        _echo_json(payload)
        return
    typer.echo(f"{payload['name']}: {payload['summary']}")
    typer.echo(f"purpose: {payload['purpose']}")
    if payload.get("prerequisites"):
        typer.echo("prerequisites:")
        for item in payload["prerequisites"]:
            typer.echo(f"  - {item}")
    if payload.get("examples"):
        typer.echo("examples:")
        for item in payload["examples"]:
            typer.echo(f"  - {item['title']}")
            typer.echo(f"    {json.dumps(item['arguments'], sort_keys=True)}")


@ops_app.command(name="call")
def ops_call(
    operation_name: Annotated[str, typer.Argument(help="Operation name, e.g. action.validate")],
    input_path: Annotated[
        str | None,
        typer.Option(
            "--input",
            "-i",
            help="JSON file containing operation arguments, or '-' for stdin.",
        ),
    ] = None,
    project_id: Annotated[
        int | None,
        typer.Option("--project", help="Merge project_id into operation arguments."),
    ] = None,
    run_token: Annotated[
        str | None,
        typer.Option("--run-token", help="Merge run_token into operation arguments."),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option("--idempotency-key", help="Merge idempotency_key into operation arguments."),
    ] = None,
    response_mode: Annotated[
        str | None,
        typer.Option(
            "--response-mode",
            help="compact, raw, or ack response shaping for operations that allow it.",
        ),
    ] = None,
) -> None:
    """Call one operation through the daemon's generic REST adapter."""
    loaded = _load_operation_arguments(input_path)
    if response_mode is not None:
        loaded["response_mode"] = response_mode
    arguments = _merge_common_arguments(
        loaded,
        project_id=project_id,
        run_token=run_token,
        idempotency_key=idempotency_key,
    )
    payload = _operation_call(operation_name, arguments)
    _echo_json(payload)


@actions_app.command(name="list")
def actions_list(
    query: Annotated[
        str | None,
        typer.Option(
            "--query",
            "-q",
            help="Search action refs, names, descriptions, paths, and schema fields.",
        ),
    ] = None,
    project_id: Annotated[int | None, typer.Option("--project", help="Project id.")] = None,
    plugin_slug: Annotated[
        str | None,
        typer.Option("--plugin", help="Plugin slug filter."),
    ] = None,
    provider_key: Annotated[
        str | None,
        typer.Option("--provider", help="Provider key filter."),
    ] = None,
    capability_key: Annotated[
        str | None,
        typer.Option("--capability", help="Capability key filter."),
    ] = None,
    executable: Annotated[
        bool | None,
        typer.Option("--executable/--any-executable", help="Filter by current executability."),
    ] = None,
    include_unavailable_integrations: Annotated[
        bool,
        typer.Option(
            "--include-unavailable-integrations",
            help="Include hidden/disconnected/non-executable external provider actions.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit the full machine-readable action list."),
    ] = False,
) -> None:
    """List or search project-aware action contracts."""
    payload = _operation_call(
        "action.list",
        _merge_common_arguments(
            {
                "plugin_slug": plugin_slug,
                "provider_key": provider_key,
                "capability_key": capability_key,
                "query": query,
                "executable": executable,
                "include_unavailable_integrations": include_unavailable_integrations,
            },
            project_id=project_id,
        ),
    )
    if json_output:
        _echo_json(payload)
        return
    for item in payload.get("items", []):
        availability = item.get("availability_status", "unknown")
        action_ref = item.get("action_ref", "")
        risk = item.get("risk_level", "")
        name = item.get("name", "")
        typer.echo(f"{action_ref}\t{availability}\t{risk}\t{name}")


@actions_app.command(name="describe")
def actions_describe(
    action_ref: Annotated[
        str | None,
        typer.Argument(help="Action ref, e.g. utils.sitemap.fetch."),
    ] = None,
    project_id: Annotated[int | None, typer.Option("--project", help="Project id.")] = None,
    plugin_slug: Annotated[
        str | None,
        typer.Option("--plugin", help="Plugin slug when not passing action_ref."),
    ] = None,
    action_key: Annotated[
        str | None,
        typer.Option("--action-key", help="Action key when paired with --plugin."),
    ] = None,
) -> None:
    """Describe one action manifest and safe availability state."""
    arguments = _merge_common_arguments(
        {
            "action_ref": action_ref,
            "plugin_slug": plugin_slug,
            "action_key": action_key,
        },
        project_id=project_id,
    )
    _echo_json(_operation_call("action.describe", arguments))


@actions_app.command(name="validate")
def actions_validate(
    action_ref: Annotated[
        str | None,
        typer.Argument(help="Action ref, e.g. utils.sitemap.fetch."),
    ] = None,
    input_path: Annotated[
        str | None,
        typer.Option("--input", "-i", help="JSON action input payload, or '-' for stdin."),
    ] = None,
    provider_context_path: Annotated[
        str | None,
        typer.Option(
            "--provider-context",
            help="JSON provider execution context payload, or '-' for stdin.",
        ),
    ] = None,
    context_ref: Annotated[
        str | None,
        typer.Option("--context-ref", help="Reusable execution context ref."),
    ] = None,
    project_id: Annotated[int | None, typer.Option("--project", help="Project id.")] = None,
    plugin_slug: Annotated[
        str | None,
        typer.Option("--plugin", help="Plugin slug when not passing action_ref."),
    ] = None,
    action_key: Annotated[
        str | None,
        typer.Option("--action-key", help="Action key when paired with --plugin."),
    ] = None,
    credential_ref: Annotated[
        str | None,
        typer.Option("--credential-ref", help="Opaque credential ref; never a secret value."),
    ] = None,
) -> None:
    """Validate a concrete action input without execution."""
    arguments = _merge_common_arguments(
        {
            "action_ref": action_ref,
            "plugin_slug": plugin_slug,
            "action_key": action_key,
            "credential_ref": credential_ref,
            "input_json": _load_operation_arguments(input_path) if input_path else None,
            "context_ref": context_ref,
            "provider_context_json": _load_operation_arguments(provider_context_path)
            if provider_context_path
            else None,
        },
        project_id=project_id,
    )
    _echo_json(_operation_call("action.validate", arguments))


@actions_app.command(name="execute")
def actions_execute(
    action_ref: Annotated[
        str | None,
        typer.Argument(help="Action ref, e.g. utils.sitemap.fetch."),
    ] = None,
    input_path: Annotated[
        str | None,
        typer.Option("--input", "-i", help="JSON action input payload, or '-' for stdin."),
    ] = None,
    provider_context_path: Annotated[
        str | None,
        typer.Option(
            "--provider-context",
            help="JSON provider execution context payload, or '-' for stdin.",
        ),
    ] = None,
    context_ref: Annotated[
        str | None,
        typer.Option("--context-ref", help="Reusable execution context ref."),
    ] = None,
    project_id: Annotated[int | None, typer.Option("--project", help="Project id.")] = None,
    run_token: Annotated[
        str | None,
        typer.Option("--run-token", help="Run token returned by run-plans start."),
    ] = None,
    plugin_slug: Annotated[
        str | None,
        typer.Option("--plugin", help="Plugin slug when not passing action_ref."),
    ] = None,
    action_key: Annotated[
        str | None,
        typer.Option("--action-key", help="Action key when paired with --plugin."),
    ] = None,
    credential_ref: Annotated[
        str | None,
        typer.Option("--credential-ref", help="Opaque credential ref; never a secret value."),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option("--idempotency-key", help="24h dedupe token for the execution call."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Ask the connector to dry-run."),
    ] = False,
) -> None:
    """Execute an action inside the currently claimed run-plan step."""
    run_token = _require_run_token(run_token, "actions execute")
    arguments = _merge_common_arguments(
        {
            "action_ref": action_ref,
            "plugin_slug": plugin_slug,
            "action_key": action_key,
            "credential_ref": credential_ref,
            "input_json": _load_operation_arguments(input_path) if input_path else None,
            "context_ref": context_ref,
            "provider_context_json": _load_operation_arguments(provider_context_path)
            if provider_context_path
            else None,
            "dry_run": dry_run,
        },
        project_id=project_id,
        run_token=run_token,
        idempotency_key=idempotency_key,
    )
    _echo_json(_operation_call("action.execute", arguments))


@actions_app.command(name="run")
def actions_run(
    action_ref: Annotated[
        str | None,
        typer.Argument(help="Action ref, e.g. communications.telegram-bot.message.send."),
    ] = None,
    input_path: Annotated[
        str | None,
        typer.Option("--input", "-i", help="JSON action input payload, or '-' for stdin."),
    ] = None,
    provider_context_path: Annotated[
        str | None,
        typer.Option(
            "--provider-context",
            help="JSON provider execution context payload, or '-' for stdin.",
        ),
    ] = None,
    context_ref: Annotated[
        str | None,
        typer.Option("--context-ref", help="Reusable execution context ref."),
    ] = None,
    project_id: Annotated[int | None, typer.Option("--project", help="Project id.")] = None,
    plugin_slug: Annotated[
        str | None,
        typer.Option("--plugin", help="Plugin slug when not passing action_ref."),
    ] = None,
    action_key: Annotated[
        str | None,
        typer.Option("--action-key", help="Action key when paired with --plugin."),
    ] = None,
    credential_ref: Annotated[
        str | None,
        typer.Option("--credential-ref", help="Opaque credential ref; never a secret value."),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option("--idempotency-key", help="24h dedupe token for the direct action."),
    ] = None,
    intent_id: Annotated[
        str | None,
        typer.Option(
            "--intent-id",
            help="Stable caller intent id used to derive retry-safe writes.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Ask the connector to dry-run."),
    ] = False,
    confirm_direct: Annotated[
        bool,
        typer.Option("--confirm-direct", help="Required for non-read direct actions."),
    ] = False,
    intent_summary: Annotated[
        str | None,
        typer.Option("--intent-summary", help="Why this direct action is being run."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            help=(
                "Deprecated no-op; use response-mode to choose compact file metadata "
                "or raw audit shape."
            ),
        ),
    ] = False,
) -> None:
    """Run one explicit action directly without creating a workflow run plan."""
    arguments = _merge_common_arguments(
        {
            "action_ref": action_ref,
            "plugin_slug": plugin_slug,
            "action_key": action_key,
            "credential_ref": credential_ref,
            "input_json": _load_operation_arguments(input_path) if input_path else None,
            "context_ref": context_ref,
            "provider_context_json": _load_operation_arguments(provider_context_path)
            if provider_context_path
            else None,
            "dry_run": dry_run,
            "confirm_direct": confirm_direct,
            "intent_summary": intent_summary,
            "intent_id": intent_id,
            "verbose": verbose,
        },
        project_id=project_id,
        idempotency_key=idempotency_key,
    )
    _echo_json(_operation_call("action.run", arguments))


@run_plans_app.command(name="validate")
def run_plans_validate(
    input_path: Annotated[
        str | None,
        typer.Option("--input", "-i", help="JSON run-plan spec, or '-' for stdin."),
    ] = None,
    template_key: Annotated[
        str | None,
        typer.Option("--template-key", help="Template key to validate as a run plan."),
    ] = None,
    workflow_key: Annotated[
        str | None,
        typer.Option("--workflow-key", help="Workflow key to validate as a run plan."),
    ] = None,
    project_id: Annotated[int | None, typer.Option("--project", help="Project id.")] = None,
    repo_root: Annotated[str | None, typer.Option("--repo-root", help="Repository root.")] = None,
    plugin_slug: Annotated[str | None, typer.Option("--plugin", help="Plugin slug filter.")] = None,
    source: Annotated[str | None, typer.Option("--source", help="Template source filter.")] = None,
) -> None:
    """Validate a run plan without saving it."""
    arguments = _merge_common_arguments(
        {
            "run_plan_json": _load_operation_arguments(input_path) if input_path else None,
            "template_key": template_key,
            "workflow_key": workflow_key,
            "repo_root": repo_root,
            "plugin_slug": plugin_slug,
            "source": source,
        },
        project_id=project_id,
    )
    _echo_json(_operation_call("runPlan.validate", arguments))


@run_plans_app.command(name="create")
def run_plans_create(
    input_path: Annotated[
        str | None,
        typer.Option("--input", "-i", help="JSON run-plan spec, or '-' for stdin."),
    ] = None,
    template_key: Annotated[
        str | None,
        typer.Option("--template-key", help="Template key to instantiate."),
    ] = None,
    workflow_key: Annotated[
        str | None,
        typer.Option("--workflow-key", help="Workflow key to instantiate."),
    ] = None,
    project_id: Annotated[int | None, typer.Option("--project", help="Project id.")] = None,
    key: Annotated[str | None, typer.Option("--key", help="Override run-plan key.")] = None,
    title: Annotated[str | None, typer.Option("--title", help="Override run-plan title.")] = None,
    created_by: Annotated[str | None, typer.Option("--created-by", help="Creator label.")] = None,
    repo_root: Annotated[str | None, typer.Option("--repo-root", help="Repository root.")] = None,
    plugin_slug: Annotated[str | None, typer.Option("--plugin", help="Plugin slug filter.")] = None,
    source: Annotated[str | None, typer.Option("--source", help="Template source filter.")] = None,
) -> None:
    """Create a draft run plan from JSON or a template."""
    arguments = _merge_common_arguments(
        {
            "run_plan_json": _load_operation_arguments(input_path) if input_path else None,
            "template_key": template_key,
            "workflow_key": workflow_key,
            "repo_root": repo_root,
            "plugin_slug": plugin_slug,
            "source": source,
            "key": key,
            "title": title,
            "created_by": created_by,
        },
        project_id=project_id,
    )
    _echo_json(_operation_call("runPlan.create", arguments))


@run_plans_app.command(name="start")
def run_plans_start(
    run_plan_id: Annotated[int, typer.Argument(help="Run plan id.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    idempotency_key: Annotated[
        str | None,
        typer.Option("--idempotency-key", help="24h dedupe token for start."),
    ] = None,
) -> None:
    """Start a draft run plan and return the run token."""
    arguments = _merge_common_arguments(
        {"run_plan_id": run_plan_id},
        project_id=project_id,
        idempotency_key=idempotency_key,
    )
    _echo_json(_operation_call("runPlan.start", arguments))


@run_plans_app.command(name="get")
def run_plans_get(
    run_plan_id: Annotated[int, typer.Argument(help="Run plan id.")],
    run_token: Annotated[
        str | None,
        typer.Option("--run-token", help="Optional run token for scoped reads."),
    ] = None,
) -> None:
    """Fetch one run plan."""
    arguments = _merge_common_arguments({"run_plan_id": run_plan_id}, run_token=run_token)
    _echo_json(_operation_call("runPlan.get", arguments))


@run_plans_app.command(name="list")
def run_plans_list(
    project_id: Annotated[int | None, typer.Option("--project", help="Project id.")] = None,
    status: Annotated[str | None, typer.Option("--status", help="Run-plan status.")] = None,
    template_key: Annotated[
        str | None,
        typer.Option("--template-key", help="Filter by template key."),
    ] = None,
    workflow_key: Annotated[
        str | None,
        typer.Option("--workflow-key", help="Filter by workflow key."),
    ] = None,
    limit: Annotated[int | None, typer.Option("--limit", help="Page size.")] = None,
    after_id: Annotated[int | None, typer.Option("--after-id", help="Cursor id.")] = None,
    run_token: Annotated[
        str | None,
        typer.Option("--run-token", help="Optional run token for scoped reads."),
    ] = None,
) -> None:
    """List run plans with optional filters."""
    arguments = _merge_common_arguments(
        {
            "status": status,
            "template_key": template_key,
            "workflow_key": workflow_key,
            "limit": limit,
            "after_id": after_id,
        },
        project_id=project_id,
        run_token=run_token,
    )
    _echo_json(_operation_call("runPlan.list", arguments))


@run_plans_app.command(name="approve")
def run_plans_approve(
    run_plan_id: Annotated[int, typer.Argument(help="Run plan id.")],
    approval_key: Annotated[str, typer.Option("--approval-key", help="Approval gate key.")],
    status: Annotated[
        str,
        typer.Option("--status", help="Approval status: approved, rejected, or cancelled."),
    ] = "approved",
    decided_by: Annotated[
        str | None,
        typer.Option("--decided-by", help="Approver/operator label."),
    ] = None,
    decision_path: Annotated[
        str | None,
        typer.Option("--decision", help="JSON decision payload, or '-' for stdin."),
    ] = None,
) -> None:
    """Record an approval-gate decision through the local admin surface."""
    arguments = {
        "run_plan_id": run_plan_id,
        "approval_key": approval_key,
        "approval_status": status,
        "decided_by": decided_by,
        "decision_json": _load_operation_arguments(decision_path) if decision_path else None,
    }
    _echo_json(_operation_call("runPlan.update", arguments))


@run_plans_app.command(name="abort")
def run_plans_abort(
    run_plan_id: Annotated[int, typer.Argument(help="Run plan id.")],
    project_id: Annotated[int | None, typer.Option("--project", help="Project id.")] = None,
    reason: Annotated[str | None, typer.Option("--reason", help="Abort reason.")] = None,
    actor: Annotated[str | None, typer.Option("--actor", help="Operator/agent label.")] = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option("--idempotency-key", help="24h dedupe token for abort."),
    ] = None,
) -> None:
    """Abort a draft or started run plan and retire its tracker mirror."""
    arguments = _merge_common_arguments(
        {
            "run_plan_id": run_plan_id,
            "reason": reason,
            "actor": actor,
        },
        project_id=project_id,
        idempotency_key=idempotency_key,
    )
    _echo_json(_operation_call("runPlan.abort", arguments))


@run_plans_app.command(name="recover")
def run_plans_recover(
    run_plan_id: Annotated[int, typer.Argument(help="Run plan id.")],
    step_id: Annotated[str, typer.Option("--step-id", help="Step id to restore.")],
    project_id: Annotated[int | None, typer.Option("--project", help="Project id.")] = None,
    step_status: Annotated[
        str,
        typer.Option("--step-status", help="Recovered step status: blocked or pending."),
    ] = "blocked",
    reason: Annotated[
        str | None,
        typer.Option("--reason", help="System recovery reason."),
    ] = None,
    actor: Annotated[str | None, typer.Option("--actor", help="Operator/agent label.")] = None,
    result_path: Annotated[
        str | None,
        typer.Option("--result", help="JSON recovery result payload, or '-' for stdin."),
    ] = None,
    error: Annotated[str | None, typer.Option("--error", help="Recoverable blocker text.")] = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option("--idempotency-key", help="24h dedupe token for recovery."),
    ] = None,
) -> None:
    """Recover a system-failed run plan into a live blocked or pending step."""
    arguments = _merge_common_arguments(
        {
            "run_plan_id": run_plan_id,
            "step_id": step_id,
            "step_status": step_status,
            "reason": reason,
            "actor": actor,
            "result_json": _load_operation_arguments(result_path) if result_path else None,
            "error": error,
        },
        project_id=project_id,
        idempotency_key=idempotency_key,
    )
    _echo_json(_operation_call("runPlan.recover", arguments))


@run_plans_app.command(name="claim-step")
def run_plans_claim_step(
    run_plan_id: Annotated[int, typer.Argument(help="Run plan id.")],
    step_id: Annotated[str | None, typer.Option("--step-id", help="Step id to claim.")] = None,
    run_token: Annotated[str, typer.Option("--run-token", help="Run token from start.")] = "",
    claimed_by: Annotated[str | None, typer.Option("--claimed-by", help="Claimer label.")] = None,
) -> None:
    """Claim an eligible step and activate its tool grants."""
    run_token = _require_run_token(run_token, "run-plans claim-step")
    arguments = _merge_common_arguments(
        {
            "run_plan_id": run_plan_id,
            "step_id": step_id,
            "claimed_by": claimed_by,
        },
        run_token=run_token,
    )
    _echo_json(_operation_call("runPlan.claimStep", arguments))


@run_plans_app.command(name="record-step")
def run_plans_record_step(
    run_plan_id: Annotated[int, typer.Argument(help="Run plan id.")],
    step_id: Annotated[str, typer.Option("--step-id", help="Step id to record.")],
    status: Annotated[
        str,
        typer.Option("--status", help="success, failed, skipped, or blocked."),
    ],
    result_path: Annotated[
        str | None,
        typer.Option("--result", help="JSON result payload, or '-' for stdin."),
    ] = None,
    error: Annotated[str | None, typer.Option("--error", help="Terminal error text.")] = None,
    run_token: Annotated[str, typer.Option("--run-token", help="Run token from start.")] = "",
) -> None:
    """Record the terminal result for a running step."""
    run_token = _require_run_token(run_token, "run-plans record-step")
    arguments = _merge_common_arguments(
        {
            "run_plan_id": run_plan_id,
            "step_id": step_id,
            "status": status,
            "result_json": _load_operation_arguments(result_path) if result_path else None,
            "error": error,
        },
        run_token=run_token,
    )
    _echo_json(_operation_call("runPlan.recordStep", arguments))


@agent_requests_app.command(name="list")
def agent_requests_list(
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    statuses: Annotated[
        str | None,
        typer.Option("--statuses", help="Comma-separated request statuses."),
    ] = None,
    attention_status: Annotated[
        str | None,
        typer.Option("--attention-status", help="unread, read, or archived."),
    ] = None,
    claimed_by: Annotated[str | None, typer.Option("--claimed-by", help="Claimer label.")] = None,
    claimable: Annotated[
        bool,
        typer.Option("--claimable", help="Return new requests and expired claims only."),
    ] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Page size.")] = None,
    after_id: Annotated[int | None, typer.Option("--after-id", help="Cursor id.")] = None,
) -> None:
    """List generic agent request queue items."""
    arguments = _merge_common_arguments(
        {
            "statuses": _split_csv(statuses),
            "attention_status": attention_status,
            "claimed_by": claimed_by,
            "claimable": claimable,
            "limit": limit,
            "after_id": after_id,
        },
        project_id=project_id,
    )
    _echo_json(_operation_call("agentRequest.list", arguments))


@agent_requests_app.command(name="get")
def agent_requests_get(
    request_id: Annotated[int, typer.Argument(help="Agent request id.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
) -> None:
    """Fetch one generic agent request."""
    arguments = _merge_common_arguments({"request_id": request_id}, project_id=project_id)
    _echo_json(_operation_call("agentRequest.get", arguments))


@agent_requests_app.command(name="create")
def agent_requests_create(
    request_key: Annotated[str, typer.Argument(help="Stable project-scoped request key.")],
    title: Annotated[str, typer.Argument(help="Request title.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    run_token: Annotated[
        str,
        typer.Option("--run-token", help="Run token whose active step grants agentRequest.create."),
    ],
    body_preview: Annotated[
        str, typer.Option("--body-preview", help="Sanitized body preview.")
    ] = "",
    source_provider: Annotated[
        str | None,
        typer.Option("--source-provider", help="Source provider key."),
    ] = None,
    source_kind: Annotated[
        str | None, typer.Option("--source-kind", help="Source event kind.")
    ] = None,
    source_resource_key: Annotated[
        str | None,
        typer.Option("--source-resource-key", help="Source resource key."),
    ] = None,
    source_resource_record_id: Annotated[
        int | None,
        typer.Option("--source-resource-record-id", help="Source resource record id."),
    ] = None,
    source_message_ref: Annotated[
        str | None,
        typer.Option("--source-message-ref", help="Provider-safe source message reference."),
    ] = None,
    priority: Annotated[int, typer.Option("--priority", help="Higher values are more urgent.")] = 0,
    metadata_path: Annotated[
        str | None,
        typer.Option("--metadata", help="JSON metadata object, or '-' for stdin."),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        typer.Option("--idempotency-key", help="24h dedupe token for create."),
    ] = None,
) -> None:
    """Create a request from a granted ingestion/run-plan step."""
    arguments = _merge_common_arguments(
        {
            "request_key": request_key,
            "title": title,
            "body_preview": body_preview,
            "source_provider": source_provider,
            "source_kind": source_kind,
            "source_resource_key": source_resource_key,
            "source_resource_record_id": source_resource_record_id,
            "source_message_ref": source_message_ref,
            "priority": priority,
            "metadata_json": _load_operation_arguments(metadata_path) if metadata_path else None,
        },
        project_id=project_id,
        run_token=run_token,
        idempotency_key=idempotency_key,
    )
    _echo_json(_operation_call("agentRequest.create", arguments))


@agent_requests_app.command(name="claim")
def agent_requests_claim(
    request_id: Annotated[int, typer.Argument(help="Agent request id.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    claimed_by: Annotated[str, typer.Option("--claimed-by", help="Stable claimer label.")],
    idempotency_key: Annotated[
        str,
        typer.Option("--idempotency-key", help="Required replay key for retry-safe claims."),
    ],
    lease_seconds: Annotated[
        int, typer.Option("--lease-seconds", help="Claim lease seconds.")
    ] = 600,
) -> None:
    """Claim one request and return a one-time claim token."""
    arguments = _merge_common_arguments(
        {
            "request_id": request_id,
            "claimed_by": claimed_by,
            "lease_seconds": lease_seconds,
        },
        project_id=project_id,
        idempotency_key=idempotency_key,
    )
    _echo_json(_operation_call("agentRequest.claim", arguments))


@agent_requests_app.command(name="release")
def agent_requests_release(
    request_id: Annotated[int, typer.Argument(help="Agent request id.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    claim_token: Annotated[str, typer.Option("--claim-token", help="Token returned by claim.")],
) -> None:
    """Release a claimed request."""
    arguments = _merge_common_arguments(
        {"request_id": request_id, "claim_token": claim_token},
        project_id=project_id,
    )
    _echo_json(_operation_call("agentRequest.release", arguments))


@agent_requests_app.command(name="link-run-plan")
def agent_requests_link_run_plan(
    request_id: Annotated[int, typer.Argument(help="Agent request id.")],
    run_plan_id: Annotated[int, typer.Argument(help="Run plan id.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    claim_token: Annotated[str, typer.Option("--claim-token", help="Token returned by claim.")],
) -> None:
    """Attach a claimed request to a run plan."""
    arguments = _merge_common_arguments(
        {
            "request_id": request_id,
            "run_plan_id": run_plan_id,
            "claim_token": claim_token,
        },
        project_id=project_id,
    )
    _echo_json(_operation_call("agentRequest.linkRunPlan", arguments))


@agent_requests_app.command(name="prepare-run-plan")
def agent_requests_prepare_run_plan(
    request_id: Annotated[int, typer.Argument(help="Agent request id.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    claimed_by: Annotated[str, typer.Option("--claimed-by", help="Stable claimer label.")],
    idempotency_key: Annotated[
        str,
        typer.Option("--idempotency-key", help="Required replay key for retry-safe prepare."),
    ],
    input_path: Annotated[
        str | None,
        typer.Option("--input", "-i", help="JSON run-plan spec, or '-' for stdin."),
    ] = None,
    template_key: Annotated[
        str | None,
        typer.Option("--template-key", help="Template key to instantiate."),
    ] = None,
    lease_seconds: Annotated[
        int, typer.Option("--lease-seconds", help="Claim lease seconds.")
    ] = 86_400,
    created_by: Annotated[str | None, typer.Option("--created-by", help="Creator label.")] = None,
    repo_root: Annotated[str | None, typer.Option("--repo-root", help="Repository root.")] = None,
    plugin_slug: Annotated[str | None, typer.Option("--plugin", help="Plugin slug filter.")] = None,
    source: Annotated[str | None, typer.Option("--source", help="Template source filter.")] = None,
    metadata_path: Annotated[
        str | None,
        typer.Option("--metadata", help="JSON metadata object, or '-' for stdin."),
    ] = None,
) -> None:
    """Claim a request, create a supplied run plan, and link both."""
    arguments = _merge_common_arguments(
        {
            "request_id": request_id,
            "claimed_by": claimed_by,
            "lease_seconds": lease_seconds,
            "run_plan_json": _load_operation_arguments(input_path) if input_path else None,
            "template_key": template_key,
            "repo_root": repo_root,
            "plugin_slug": plugin_slug,
            "source": source,
            "created_by": created_by,
            "metadata_json": _load_operation_arguments(metadata_path) if metadata_path else None,
        },
        project_id=project_id,
        idempotency_key=idempotency_key,
    )
    _echo_json(_operation_call("agentRequest.prepareRunPlan", arguments))


@agent_requests_app.command(name="complete")
def agent_requests_complete(
    request_id: Annotated[int, typer.Argument(help="Agent request id.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    claim_token: Annotated[str, typer.Option("--claim-token", help="Token returned by claim.")],
    status: Annotated[str, typer.Option("--status", help="resolved or failed.")] = "resolved",
    metadata_path: Annotated[
        str | None,
        typer.Option("--metadata", help="JSON metadata object, or '-' for stdin."),
    ] = None,
) -> None:
    """Resolve or fail a claimed request."""
    arguments = _merge_common_arguments(
        {
            "request_id": request_id,
            "claim_token": claim_token,
            "status": status,
            "metadata_json": _load_operation_arguments(metadata_path) if metadata_path else None,
        },
        project_id=project_id,
    )
    _echo_json(_operation_call("agentRequest.complete", arguments))


@agent_requests_app.command(name="ignore")
def agent_requests_ignore(
    request_id: Annotated[int, typer.Argument(help="Agent request id.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    ignored_by: Annotated[str, typer.Option("--ignored-by", help="Stable actor label.")],
    claim_token: Annotated[
        str | None,
        typer.Option("--claim-token", help="Required when the request is claimed."),
    ] = None,
    metadata_path: Annotated[
        str | None,
        typer.Option("--metadata", help="JSON metadata object, or '-' for stdin."),
    ] = None,
) -> None:
    """Archive a request without creating work."""
    arguments = _merge_common_arguments(
        {
            "request_id": request_id,
            "ignored_by": ignored_by,
            "claim_token": claim_token,
            "metadata_json": _load_operation_arguments(metadata_path) if metadata_path else None,
        },
        project_id=project_id,
    )
    _echo_json(_operation_call("agentRequest.ignore", arguments))


@tracker_app.command(name="status")
def tracker_status(
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
) -> None:
    """Show compact tracker counts and revision."""
    _echo_json(_operation_call("tracker.status", {"project_id": project_id}))


@tracker_app.command(name="get")
def tracker_get(
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    workflow_key: Annotated[
        str | None,
        typer.Option("--workflow-key", help="Filter by template/run-plan key."),
    ] = None,
    run_plan_id: Annotated[int | None, typer.Option("--run-plan-id", help="Run plan id.")] = None,
    assignee: Annotated[str | None, typer.Option("--assignee", help="Assignee filter.")] = None,
    include_graph: Annotated[
        bool,
        typer.Option("--include-graph/--no-graph", help="Include graph projection."),
    ] = True,
) -> None:
    """Fetch tasks, tickets, dependencies, links, and graph projection."""
    _echo_json(
        _operation_call(
            "tracker.get",
            {
                "project_id": project_id,
                "workflow_key": workflow_key,
                "run_plan_id": run_plan_id,
                "assignee": assignee,
                "include_graph": include_graph,
            },
        )
    )


@tracker_app.command(name="next")
def tracker_next(
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    limit: Annotated[int, typer.Option("--limit", help="Max tickets.")] = 5,
    assignee: Annotated[str | None, typer.Option("--assignee", help="Assignee filter.")] = None,
) -> None:
    """List ready tracker tickets."""
    _echo_json(
        _operation_call(
            "tracker.next",
            {"project_id": project_id, "limit": limit, "assignee": assignee},
        )
    )


@tracker_app.command(name="brief")
def tracker_brief(
    ticket_key: Annotated[str, typer.Argument(help="Tracker ticket key.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
) -> None:
    """Fetch one ticket's bounded execution context."""
    _echo_json(
        _operation_call("tracker.brief", {"project_id": project_id, "ticket_key": ticket_key})
    )


@tracker_app.command(name="verify")
def tracker_verify(
    ticket_key: Annotated[str, typer.Argument(help="Tracker ticket key.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
) -> None:
    """Check whether one ticket is verification-ready."""
    _echo_json(
        _operation_call("tracker.verify", {"project_id": project_id, "ticket_key": ticket_key})
    )


@tracker_app.command(name="pick")
def tracker_pick(
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    assignee: Annotated[str, typer.Option("--assignee", help="Assignee label.")],
    ticket_key: Annotated[
        str | None,
        typer.Option("--ticket-key", help="Explicit ticket key; defaults to next ready."),
    ] = None,
) -> None:
    """Claim the next ready ticket or an explicit ticket."""
    _echo_json(
        _operation_call(
            "tracker.pick",
            {"project_id": project_id, "assignee": assignee, "ticket_key": ticket_key},
        )
    )


@tracker_app.command(name="create-task")
def tracker_create_task(
    key: Annotated[str, typer.Argument(help="Stable task key.")],
    title: Annotated[str, typer.Argument(help="Task title.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    goal: Annotated[str, typer.Option("--goal", help="Task goal.")] = "",
    owner: Annotated[str | None, typer.Option("--owner", help="Owner label.")] = None,
    priority_key: Annotated[str, typer.Option("--priority", help="Priority key.")] = "p2",
    lane_key: Annotated[str, typer.Option("--lane", help="Lane key.")] = "implementation",
    created_by: Annotated[str | None, typer.Option("--created-by", help="Creator label.")] = None,
) -> None:
    """Create a tracker task."""
    _echo_json(
        _operation_call(
            "tracker.createTask",
            {
                "project_id": project_id,
                "key": key,
                "title": title,
                "goal": goal,
                "owner": owner,
                "priority_key": priority_key,
                "lane_key": lane_key,
                "created_by": created_by,
            },
        )
    )


@tracker_app.command(name="create-ticket")
def tracker_create_ticket(
    task_key: Annotated[str, typer.Argument(help="Parent task key.")],
    key: Annotated[str, typer.Argument(help="Stable ticket key.")],
    title: Annotated[str, typer.Argument(help="Ticket title.")],
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    goal: Annotated[str, typer.Option("--goal", help="Ticket goal.")] = "",
    assignee: Annotated[str | None, typer.Option("--assignee", help="Assignee label.")] = None,
    priority_key: Annotated[str, typer.Option("--priority", help="Priority key.")] = "p2",
    lane_key: Annotated[str, typer.Option("--lane", help="Lane key.")] = "implementation",
    dependencies: Annotated[
        str | None,
        typer.Option("--dependencies", help="Comma-separated dependency ticket keys."),
    ] = None,
    created_by: Annotated[str | None, typer.Option("--created-by", help="Creator label.")] = None,
) -> None:
    """Create a tracker ticket under a task."""
    _echo_json(
        _operation_call(
            "tracker.createTicket",
            {
                "project_id": project_id,
                "task_key": task_key,
                "key": key,
                "title": title,
                "goal": goal,
                "assignee": assignee,
                "priority_key": priority_key,
                "lane_key": lane_key,
                "dependency_keys": _split_csv(dependencies),
                "created_by": created_by,
            },
        )
    )


@tracker_app.command(name="patch")
def tracker_patch(
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    input_path: Annotated[
        str,
        typer.Option("--input", "-i", help="Tracker patch JSON object, or '-' for stdin."),
    ],
    actor: Annotated[str | None, typer.Option("--actor", help="Actor label.")] = None,
) -> None:
    """Apply a small multi-entity tracker patch."""
    _echo_json(
        _operation_call(
            "tracker.patch",
            {
                "project_id": project_id,
                "patch_json": _load_operation_arguments(input_path),
                "actor": actor,
            },
        )
    )


@tracker_app.command(name="reject-task")
def tracker_reject_task(
    project_id: Annotated[int, typer.Option("--project", help="Project id.")],
    reason: Annotated[str, typer.Option("--reason", help="Operator rejection reason.")],
    task_key: Annotated[
        str | None,
        typer.Option("--task", help="Tracker task key to reject."),
    ] = None,
    run_plan_id: Annotated[
        int | None,
        typer.Option("--run-plan", help="Run-plan id whose tracker mirror should be rejected."),
    ] = None,
    actor: Annotated[str | None, typer.Option("--actor", help="Actor label.")] = None,
) -> None:
    """Reject a tracker task or run-plan mirror and cascade child tickets."""
    _echo_json(
        _operation_call(
            "tracker.rejectTask",
            {
                "project_id": project_id,
                "task_key": task_key,
                "run_plan_id": run_plan_id,
                "reason": reason,
                "actor": actor,
            },
        )
    )
