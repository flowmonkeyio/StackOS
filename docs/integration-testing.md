# StackOS Integration Testing

Use the built-in mock provider before live vendor credentials exist.

The mock provider is intentionally fake at the vendor edge, but real inside
StackOS:

```text
REST / CLI / MCP
-> operation registry
-> direct-action policy or run-plan grant check
-> action schema and connector validation
-> daemon-side credential resolution
-> connector execution
-> redacted action-call and credential-usage audit
```

## Built-In Mock Provider

- Plugin: `utils`
- Provider: `mock-provider`
- Auth method: `api_key`
- Action: `utils.mock.echo`
- Connector: `mock-provider`

Store any non-empty fake API key through the normal auth setup route, then pass
only the returned `credential_ref` to `action.run` for one explicit call or
`action.execute` inside a granted run-plan step.

Example action payload:

```json
{
  "project_id": 1,
  "run_token": "run-plan-token",
  "action_ref": "utils.mock.echo",
  "credential_ref": "cred_...",
  "input_json": {
    "message": "hello",
    "echo": {
      "campaign": "local-test"
    },
    "cost_cents": 7
  }
}
```

Supported `scenario` values:

- `success`
- `partial_success`
- `provider_error`
- `invalid_credentials`
- `rate_limit`
- `timeout`

Failure scenarios still go through the connector path and should produce
redacted failed action-call audit rows. Provider HTTP failures use
`ActionConnectorError`, so the operation error envelope and audit
`response_json` preserve safe fields such as `provider_status_code`,
`provider_error.code`, `provider_error.message`, and retry metadata. They are
useful for validating agent and operator behavior around retries, rate limits,
credential failure, and provider errors without live API accounts.

## Entrypoint Parity

The same mock-provider run is proven through all three callable entrypoints:

| Entrypoint | Test | What it proves |
| --- | --- | --- |
| REST | `tests/integration/test_routes/test_operations_routes.py::test_operation_rest_mock_provider_vertical_slice` | Generic REST operation calls enter the shared registry and produce linked, redacted action-call audit. |
| CLI | `tests/integration/test_routes/test_cli_mock_provider.py::test_cli_mock_provider_vertical_slice_uses_shared_operation_registry` | CLI aliases call the same REST operation adapter and do not bypass grants, auth, redaction, or audit. |
| MCP | `tests/integration/test_mcp/test_mcp_actions.py::test_action_execute_mock_provider_vertical_slice_through_mcp` | MCP tools generated from the operation specs hit the same action registry and run-plan boundary. |

All three checks assert the same invariants:

- `provider_key` and `connector_key` are both `mock-provider`.
- The action call is linked to the run, run plan, and active step.
- The caller passes only an opaque `credential_ref`.
- Secret-like values returned by the fake connector are redacted from output,
  metadata, and audit rows.
- The action cost and structured output are recorded through the normal
  action-call ledger.

## Telegram Local Slice

Telegram communication setup has an account-owned TDLib slice because it spans
credential setup, explicit agent session control, local user authorization,
communication-profile setup, native update ingress, agent requests, run plans,
and provider actions:

```bash
uv run pytest \
  tests/unit/test_telegram_tdlib_sessions.py \
  tests/unit/test_telegram_tdlib_service.py \
  tests/integration/test_repositories/test_telegram_account_lifecycle.py \
  tests/integration/test_repositories/test_telegram_account_session_operations.py \
  tests/integration/test_repositories/test_telegram_native_authorization.py \
  tests/integration/test_routes/test_auth_provider_routes.py \
  tests/integration/test_mcp/test_mcp_auth.py \
  tests/integration/test_mcp/test_mcp_communications.py \
  tests/integration/test_repositories/test_telegram_actions.py \
  -q
```

This does not require a live Telegram account. It uses fake Account material and
a fake TDLib runtime at the provider edge while preserving the real StackOS
credential boundary, project attachment check, one-session lifecycle, durable
desired-connected state, authorization generation fence, native update
normalization, action grants, and redacted action/resource audit. It proves that
only an explicit connect restores a session after daemon boot and that explicit
disconnect stops ingress, preserves saved user authorization, and holds later
delivery. It does not prove a real
Telegram login, proxy, destination rights, or provider send; those require the
operator-owned live verification gate.

## Slack Local Slice

Slack communication setup has mocked provider coverage for the Web API action
edge and signed HTTP ingress:

```bash
uv run pytest \
  tests/integration/test_repositories/test_slack_bot_actions.py \
  tests/integration/test_routes/test_slack_ingress_routes.py \
  -q
```

This does not require a live Slack workspace. It uses fake bot token/signing
secret values and mocked Slack Web API responses, while still exercising the
real StackOS credential boundary, communication-profile binding, raw-body HMAC
verification, Block Kit interaction state, native message reaction actions,
message delete actions, generic agent requests, and redacted action-call/resource
audit.

## Retained Consumers And Lifecycle Fixtures

For a migration change, inspect affected retained readers and current-head
expectations as well as the migration itself. Tests that initialize or upgrade
to head must reconcile their expected revision with the migration owner;
historical upgrade/downgrade cases retain their deliberately pinned revisions
and data assertions. Update
only expectations invalidated by the change, preserving released migration
fixes and unrelated historical coverage.

For a shared UI producer or component change, trace its actual emitted payload
and event contract through affected retained callers, including screens outside
the edited path. Exercise the applicable selection, empty/error and recovery
interaction at the consumer; a hand-built simplified payload or a component
test alone cannot prove that wiring. Use the existing UI checks and selected
browser proof where rendering or interaction changes, with the independent
guards in `ui/src/design/DESIGN.md`.

Lifecycle source tests use disposable HOME, data/state directories and explicit
host/port settings before invoking the real caller. A sandbox HOME alone does
not isolate a probe that still targets the installed daemon. For an absent
source, reserve a non-listening port for the fixture lifetime; occupied-source
cases need their own owned endpoint and explicit expected refusal or handoff.
Use the real emitted upgrade identity, preparation/continuation result and
checkpoint refs where the claim crosses those owners. Mock only the declared
external/native edge, retain the full consumed contract and state its limits;
do not disable the guard or return a shortened success object to hide a failure.
The existing install-script sandbox and upgrade-caller regressions own these
checks; preserve them rather than adding another fixture framework.

These are source/fixture safeguards. They do not execute or establish the
deferred real installed-upgrade M2 proof, app restart, signing or notarization.
Those remain with their existing explicitly authorized lifecycle/release gate.

## Select proof for a StackOS change

During test design or planning, pick the row that matches the changed behavior. Put the selected case and its expected observation in the existing ticket. Delivery updates that selection if the final diff reaches another caller. Choose the named cases within each linked suite.

### Setup and contracts

| When changing | Decisive observation | Maintained checks |
| --- | --- | --- |
| Setup ownership, reporting or cancellation | Only the original owner can recover a writer token on replay. A malformed source read or committed write response preserves evidence. Optional roles remain optional. Uncertain ownership prevents cleanup; cancelling before the first report retains the possibility of edits. | [Start/report replay](../tests/integration/test_mcp/test_mcp_project_setup_replay.py), [damaged source and optional roles](../tests/integration/test_repositories/test_project_setup.py), [uncertain ownership](../tests/integration/test_repositories/test_managed_setup.py), [external cancel before report](../tests/integration/test_routes/test_project_setup_operations.py). |
| Host choice, preference inheritance or readiness | Start with a custom-model-only host or omit the host with saved settings. Compare the inherited host before proposal overrides. A process exiting zero with a failed outcome earns no readiness. A fresh reviewer needs no preseeded task; after evidence rejection the recovery reviewer remains available. | [Launch dialog](../ui/src/views/project-setup/ProjectSetupLaunchDialog.spec.ts), [managed activation](../tests/integration/test_repositories/test_managed_setup_activation.py), [logical failure](../tests/integration/test_repositories/test_project_setup_activation.py). |
| Preset text, host capabilities or local guidance | Follow YAML through the local role and routed docs, resolved instructions, packaged source and MCP response. Check inherited obligations, scoped amendments, contradictory instructions and required compact handoffs as well as version identity. Record installed or loaded consumption only when observed there. | [Agent](../tests/unit/test_agent_presets.py), [skill/package](../tests/unit/test_skill_presets.py), [local contracts](../tests/unit/test_project_guidance_contract.py), [MCP preset](../tests/integration/test_mcp/test_mcp_agent_presets.py) and [MCP host status](../tests/integration/test_mcp/test_mcp_host_status.py). |
| Defaults, source adoption or workspace binding | Compare legacy and mapped settings, omitted versus explicitly null fields, and partial role adoption. Check the actual role selected and the original bound project after a conflicting request. | [Role fallback and partial adoption](../tests/integration/test_repositories/test_task_delivery_sources.py), [registered partial handoff](../tests/integration/test_routes/test_task_delivery_routes.py), [workspace-bound MCP flow](./release-signoff.md#agent-flow-matrix). |
| CFOBooth connector inventory or auth | Hold a catalog result across Account detach/reattach. Direct and granted callers need a fresh authorized sync before inventory is usable. Compare an explicit empty auth catalog ref with omission and an unavailable package. | [Held result and caller scopes](../tests/integration/test_repositories/test_cfobooth_execution.py), [catalog reference and package controls](../tests/integration/test_repositories/test_connector_package_boundary.py). |

### UI reads and interactions

Finance changes select the existing [page](../ui/src/views/FinanceView.spec.ts),
[filters](../ui/src/components/finance/FinanceFilters.spec.ts) and
[record panel](../ui/src/components/finance/FinanceRecordPanel.spec.ts) cases:
correct a rejected query without losing edit authority; hold same/changed-query
responses; change a period while another editor contains an unapplied draft;
preserve exact money, reviewed-save identity and document cleanup across record
scope changes. Shared chip changes also select the actual
[Board consumer](../ui/src/views/task-tracker/TrackerBoardFilterBar.spec.ts).
Readable filter-choice projection changes select
[the complete current-source matrix](../tests/unit/test_finance_management.py)
and [REST output preservation](../tests/integration/test_routes/test_finance_management_routes.py),
including zero matches, pagination, supersession and missing/conflicting labels.
Overview copy or empty-state changes also select the existing
[reporting-readiness consumer](../ui/src/views/finance/reportingReadiness.spec.ts).

| When changing | Decisive observation | Maintained checks |
| --- | --- | --- |
| Pending reads, writes or scope | Release an old read after mutation, project A after B, or an old detail after newer usage. A 503 can precede denial, including a coded 422 `missing_scope`; a later confirmation cannot restore denied state. A failed readback after a successful write keeps valid siblings visible and reports the refresh error. | [Store ordering and readback](../ui/src/stores/plugins.spec.ts), [scope denial](../ui/src/stores/plugins.scope.spec.ts), [agent scope](../ui/src/views/agents/useProjectAgents.spec.ts), [tracker detail/metrics](../ui/src/views/TaskTrackerView.spec.ts). |
| Shared controls | Focus a chart point, then leave with the pointer. Open the body-portaled select inside the Board filter. Compare saved capacity with an unsaved or failed draft. Compare UTC text and tooltip across the Los Angeles day boundary. Fail source-open, then save the retained editor. | [Chart](../ui/src/views/home/AgentActivityChart.spec.ts), [portaled select](../ui/src/components/ui/UiPopover.spec.ts), [Board popover](../ui/src/views/task-tracker/TrackerBoardFilterBar.spec.ts), [capacity](../ui/src/views/home/DeliveryCapacityControl.spec.ts), [Patterns dates](../ui/src/views/PatternsView.spec.ts), [Agents editor](../ui/src/views/AgentsView.spec.ts). |
| Demand, visibility or animation | A hidden consumer does no heartbeat work. Revealing it shows fresh data. A quiet cold or warm mount admits one baseline read. A content save requests the changed data. Changed live geometry animates once. Enabling reduced motion or a reconnecting feed cancels animation and suppresses new motion. Unmounting the canvas cancels animation and leaves no scheduled frame. | [Work visibility](../ui/src/components/domain/WorkTimeVisibility.spec.ts), [Home reads](../ui/src/views/home/useHomeReads.spec.ts), [activity row refresh](../ui/src/views/home/ActiveTaskRow.refresh.spec.ts), [canvas motion and cancellation](../ui/src/composables/useUsageCanvas.spec.ts), [Graph and content-save demand](../ui/src/views/TaskTrackerView.spec.ts). |
| Notifications, loading or retained Home layout | Measure the keyboard-accessible final notification action and the movement of Home landmarks before/after a delayed response in the actual shell. | [Browser recipes and viewport sizes](./browser-automation.md#measure-stackos-shell-layout). |

### State, history and lifetime

| When changing | Decisive observation | Maintained checks |
| --- | --- | --- |
| Time, usage or stream history | Follow one invocation from producer to retained receipt, projection and real stream update. Interleave finalization with a live read, keep unknown child history partial, and conserve sparse/out-of-order usage. Select interrupted, deferred-resume or reporting-correction inputs when those writers change. Check bulk Board arrivals, archive scope and event-ID regrowth after purge at the affected reader. | [Final handoff](../tests/integration/test_repositories/test_delivery_trends.py), [child coverage](../tests/integration/test_repositories/test_work_duration.py), [usage ordering](../tests/integration/test_repositories/test_retained_usage.py), [sparse intervals](../ui/src/lib/liveUsage.spec.ts), [Board batch/SSE](../tests/integration/test_routes/test_tracker_board_routes.py), [archive/removal feed](../tests/integration/test_routes/test_tracker_ui_projection_routes.py), [global stream watermark](../tests/integration/test_routes/test_timeline_stream_purge.py). |
| Native sessions, review recovery or upgrade callers | Announce session A before competing terminal B. A returned error with a live child cannot prove quiescence. Resume retained review once with its repair allowance; keep deferred/skipped work distinct. A partial finish returns original upgrade/checkpoint refs and explicit retry settles held work without replay. | [Native delivery](../tests/integration/test_repositories/test_task_delivery.py) (`test_terminal_session_mismatch_preserves_announced_identity_for_recovery`, `test_review_replacement_real_rejection_uses_retained_repair_allowance`, `test_report_correction_resumes_once_without_replaying_accepted_work`), [live child after error](../tests/integration/test_repositories/test_managed_upgrade.py), [phase/usage continuation](../tests/integration/test_repositories/test_upgrade_continuation.py), [CLI and REST callers](../tests/integration/test_routes/test_upgrade_callers.py). These fixtures establish source behavior. |
| Exceptional-path sanitization | Exercise cancel, supersede/reopen and native error writes. Track synthetic secret and path markers through persisted receipts, public summaries, visible text and titles. Check colon-delimited and quoted Windows paths with spaces while retaining useful repair context. | [Native delivery](../tests/integration/test_repositories/test_task_delivery.py) (`test_unvalidated_result_receipt_privacy_preserves_lifetime_evidence`), [reader agreement](../tests/integration/test_repositories/test_tracker_task_inspection.py), [UI text/title](../ui/src/components/domain/TaskDeliveryStop.spec.ts). |
| Patterns migration or record identity | Interrupt DDL, falsely stamp an empty schema, and upgrade from either historical branch head. Assert usable schema and retained data. Delete/recreate the same key and try saving or deleting with the old etag. Keep historical revision expectations pinned. | [Pattern creation](../tests/integration/test_repositories/test_patterns.py), [bundle recovery](../tests/integration/test_repositories/test_pattern_bundles.py), [branch heads](../tests/integration/test_patterns_setup_merge_migration.py), [replacement record via adapters](../tests/integration/test_mcp/test_mcp_bridge_agent_path.py). |
| Destructive cleanup or custody | Follow a foreign producer and shared historical file through both deletion orders, the admission fence and a failed purge. Ownership and exclusion survive rollback, and cold plan loading rechecks admission. | [Shared/legacy custody](../tests/integration/test_project_cleanup_recovery.py), [producer fencing](../tests/integration/test_project_cleanup_lock.py), [rollback/cold catalog](../tests/integration/test_project_cleanup_repair.py). |
| Tracker mutation or workflow gates | Preview and apply the same typed invalid/valid edit; preview stays read-only, a rolled-back write restores the in-session revision, and concurrent allocations stay distinct. For gate changes, complete, requeue and recover a mixed group with a nested required child and a separately excluded child. | [Preview/apply and gate declaration](../tests/integration/test_repositories/test_tracker.py), [delivery mutations](../tests/integration/test_repositories/test_task_delivery.py) (`test_parallel_mutations_allocate_distinct_tracker_revisions`, `test_excluded_parent_does_not_hide_nested_required_work_or_complete_group`, `test_recovery_clears_reactivated_exclusion_without_losing_decision_history`). |

## Rule

Do not bypass StackOS internals during local integration testing. Mock only the
external vendor edge. Auth refs, credential storage, run-plan grants, action
validation, connector lookup, redaction, and audit must stay enabled.
