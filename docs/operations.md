# StackOS Operations

StackOS operations are the protocol-neutral callable layer. An operation is
registered once, then adapters expose it through MCP, REST, and CLI when its
surface policy allows that.

Operations implement the explicit-execution boundary in the canonical
[`Product Contract`](./product-direction.md#product-contract). They expose
reusable behavior; they do not own agent strategy or workflow-specific
business decisions.

```text
OperationSpec
  -> MCP tool
  -> REST operation call
  -> CLI command
  -> UI operation catalog
```

The service, repository, connector, auth, and audit code remains the source of
behavior. Operations define the callable contract around that behavior:

- name and summary
- input and output models
- handler
- mutating/read-only classification
- grant and secret policy
- enabled surfaces
- agent guidance, prerequisites, return notes, and examples

## Shared Patterns

`pattern.list`, `pattern.get`, `pattern.upsert` and `pattern.delete` use one
installation-wide catalog, independent of projects. Discover and read it before
choosing a project. MCP writes require an established bound session or a declared
`workspace.startSession(global_session=true)` session; a global session needs no
selected project for catalog CRUD. HTTP/CLI use the existing authenticated owner
boundary, and the generic REST routes are browser-safe. Inputs have no
`project_id`. A project run token or step grant is not global mutation authority;
use a direct owner call without `run_token` for catalog writes.

The static workspace/session pointer and searchable operation descriptions make
recommendations discoverable from bound and global entry. Describe exact names
through `toolbox.describe`, then call `pattern.list` for a relevant bounded
search and `pattern.get` for selected entries only. Do not scan/dump the catalog
at startup. Follow [shared pattern recommendations](./agent-operating-model.md#shared-pattern-recommendations):
preserve intentional project choices, keep entry ID/key and revision in an existing
plan/decision when useful, and treat server-derived provenance as information,
never higher authority. Empty/no-match results are normal and grant no reason
to block unrelated work or authority to install, scaffold, edit or change setup.

`pattern.list` accepts `query`, `tags`, `source`, `limit` (1–100, default 25),
and an optional positive ID `cursor`. Text searches title, summary, individual
tags and article body. Any selected tag matches; text, tags and source are ANDed.
`source` is `all`, `stackos` (all bundled), `customized` (modified bundled), or
`custom` (user-authored). Server-derived labels are `Custom`, `StackOS`, and
`StackOS · Customized`. A customized record still matches `stackos`.
Rows are ordered by ID ascending, with `next_cursor`, `total_estimate` for the
filtered query and `catalog_total` to distinguish no matches from an empty
catalog. Reset the cursor when filters change. Lists never include bodies,
including in raw mode. `pattern.get` takes exactly one of `pattern_id` or `key`
and defaults to raw, returning one full Markdown entry. Compact get omits body.

Create through `stackos ops call pattern.upsert --input pattern.json` or
`POST /api/v1/operations/pattern.upsert/call` with this `arguments` object:

```json
{"key":"my-guidance","title":"My guidance","summary":"When it applies","body":"Markdown recommendation, examples and references with usage instructions.","tags":["freeform topic"]}
```

This example is an input contract, not shipped content. A stable-key retry with
identical content returns the existing record. A differing save must include the
current `expected_etag` from get/list. Copy this opaque token unchanged: it binds
the record identity and revision, so deleting and recreating the same key cannot
make an old draft current again. Do not derive it from `revision`. Delete takes
`pattern_id` and `expected_etag`. Missing records return 404; absent/stale edit
tokens return 409 with `current_etag`, identity and repair guidance. Re-read and reconcile the draft
before retrying. Write receipts contain summaries, refs, revision and etag, never
the body. Compact/raw are supported; ack is intentionally unavailable.

`source` and `source_label` are server-derived output, never editable tags or
write fields. Ordinary APIs reject bundled identity, shipped version/hash and
provenance fields. Core validates structure (title/key, Markdown, freeform tags,
size limits), not applicability or semantic quality. Author guidance should cover
applicability, recommendations, examples and references plus concrete usage for
any required technology. Reading an entry neither applies it to a project nor
authorizes installation or any other side effect.

On startup, after migrations, the catalog refreshes once from package-owned
`_assets/patterns/bundle.json` (the clone source is
`stackos/db/bundled_patterns.json`). Wheel packaging includes that asset;
desktop payloads install the same wheel. There is no runtime source selector or
refresh operation available to normal API callers. The shipped bundle contains
portable engineering recommendations. A later package can insert trusted IDs
and update entries whose exact normalized editable content still matches the
last-applied shipped hash.
The bundled `self-improving-automated-delivery` pattern is the shared method for
reviewing delivery runs, proposing a plain-language project improvement plan and
separating StackOS requirements. Human approval authorizes creation of the
project's engineering workflow. Reference the catalog key/revision rather than
copying the definition into project guidance; its canonical body ships from the
same bundle file.
Local customizations, user entries and prior baselines on skipped updates are
preserved. Explicit bundled deletion retains only a trusted-ID marker, so repair
or update cannot resurrect it; package removal leaves local entries intact.
Refresh reports bounded counts for collisions, customization and concurrent edits.
Malformed/missing assets log a bounded startup diagnostic and preserve the catalog.

## Local console plugin enablement

`plugin.enable` accepts explicit project setup from the authenticated local
console through its browser-safe REST operation. The existing plugin repository
owns validation and enablement; the console does not receive agent grants.
`plugin.disable` and legacy plugin write routes remain daemon-admin only.


On a selected online server, the shared dispatcher checks the verified principal
against persisted project ownership before grants, handlers or idempotent replay.
Admitted child IDs resolve their stored project; supplying another project ID
does not override it. Project list/portfolio discovery includes only the current
user's environment-owned projects. Other state operations require one owned
project scope, explicitly or through an admitted persisted child. Resolved
implicit scope is passed into the validated query input before pagination and
counting; operations that cannot carry that project scope are refused. Payload
JSON does not select authority. The finite setup boundary admits owned tracker
preparation, run-plan validation/creation/start and granted step calls, workflow
catalog/extension and preset discovery, and action discovery/validation/execution.
Nested tracker list targets, workflow provenance and plan snapshot IDs are
checked before mutation. Workflow tasks are created by `runPlan.create` through
its existing mirror owner; direct `tracker.createTask` prepares manual work.
Catalog calls cannot select a server `repo_root`;
repository overlays stay local. Global administration and unadapted operations
remain denied.

Selected-server REST/CLI clients use these existing preparation operations and
`action.run`/`action.execute` with the same owner and grant checks as MCP. Direct
actions still need their existing confirmation/intent policy; workflow actions
need the exact running step grant. Account attachment, payload-secret resolution,
private output download and computer-credential restrictions retain their owning
checks. `secret.set` remains write-only and MCP-only. Local browser tokens retain
their existing read/browser-safe surface policy; verified users never receive
local-admin authority. See [security](security.md).

## Computer connection setup

The REST-only `localRuntime.authorize`, `localRuntime.list`, `localRuntime.status`
and `localRuntime.revoke` operations use the shared registry and dispatcher.
They require a verified server owner and explicit `project_id`; status/revoke
also require `connection_id`. They accept no run token or idempotency key and
are raw-only. Authorization returns its scoped credential once, directly to
the local caller; it is never persisted in an operation replay record. Safe
status/list responses contain identity, expiry and authorization state, with
no credential. A computer bearer may only inspect/revoke that same connection.
Browser setup retains exact Origin and session CSRF enforcement.

Status/list also expose `connection_state` (`connected`, `disconnected`, or
`needs_attention`), `last_seen_at`, the retained `invocation_id` and
`last_known_state`, `needed_action`, and `unresolved_tasks`. Authorization and
transport presence are distinct. A poll/report within 30 seconds establishes
presence; reads do not. An accepted worker polls only its exact invocation while
native work runs. Lost presence retains the last-known work without killing it
or allowing a duplicate. Temporary transport errors retain the same worker and
pending report; identity, authorization and unknown-outcome errors stop it.
If a consumer cannot identify already dispatched work, status/list retain that
uncertainty and its needed action. An exact known late report can still resolve
the transport outcome; a presence poll alone cannot clear uncertainty.

After server restart, connected queued/running controllers become stopped with
`needs_attention`; ticket outcomes, sessions, candidates and repair counts stay
intact. Polling cannot clear that fence. After checking the native outcome and
confirming no work remains active, the verified owner can call the REST-only,
browser-safe, raw-only `localRuntime.reconcile` with project/connection IDs,
`task_key`, the exact `handoff_id` and nullable `invocation_id` from
`unresolved_tasks`, `confirmed_stopped=true` and a non-secret `outcome_note`.
The operation rejects active controllers, unretired retained invocations and
stale identities. It records confirmation in existing task delivery metadata
and clears only admission uncertainty. It never accepts a result, reopens a
failed ticket, starts work or sends a signal. A later handoff still requires
normal ticket/workflow readiness and current computer presence. Runtime bearers
cannot reconcile. There is no automatic process reattachment or reconstruction.

Select the computer's private runtime environment file, then use the existing CLI:

```bash
export STACKOS_ENV_FILE=/absolute/private/runtime.env
stackos login --email owner@example.test
stackos connect --project-id 1 --folder /absolute/existing/project
```

The password is prompted locally and exchanged only with the selected StackOS
server. The native session and computer credential have separate private local
custody. Repeat `connect` to validate and reuse the exact binding; `disconnect`
confirms server revocation before removing it. `logout` revokes the native
session independently. All commands retain the same selected environment.
Native host startup and invocation exchange keep their existing owners. Server
workspace IDs and paths never select local filesystem access. See
[CLI and stdio routing](setup.md#cli-and-stdio-mcp-server-routing).

`localRuntime.poll` and `localRuntime.report` are REST-only, raw-only computer
operations. They require that exact connection's bearer, project and connection
IDs. A browser login cannot exchange invocations. Run tokens, dispatcher replay
keys, MCP/CLI exposure and arbitrary command/file/proxy arguments are refused.
Poll is a write because it consumes a pending launch before returning it.

The app's `local_invocations` operation service holds one in-memory handoff per
computer. The existing host/controller supplies `ActiveInvocation` and its exact
attempt/scope, host, model and resume-session identity through `offer`; this is
an internal handoff, with no enqueue operation for clients. Poll returns the
finite host input once, without a server-selected folder or MCP config path.
The runtime service attaches one outbound consumer. Its
`OutboundWorker.exchange_once` uses the existing private folder binding and
`NativeAgentRunner`, which checks the actual source/tools and delegates to
`host_mcp.run_agent`. The worker passes the exact binding authenticated for its
poll. The runner checks that binding after discovery and again through the
host's registration and process-spawn guards, including credential and folder
identity. A changed or missing binding never selects a replacement connection.
The first connected flow supports Codex; unsupported hosts
stop without substitution. The server process refuses native spawning.
Host/model/provider/profile, schema, instructions and exact resume ID travel
unchanged. Optional candidate evidence travels as `NativeLaunch.candidate` and
is checked locally; returned commits must identify clean current HEAD descended
from the supplied base. Native session, result and measured usage return through
the existing report, separately from lifecycle acceptance. Delivery and review
session selection remains with the server controller.

Optional `STACKOS_NATIVE_MCP_CONFIG_REF` in the private runtime environment file
is a JSON object with the existing absolute `path` and `sha256` route identity.
It is local configuration, checked on each invocation, and cannot be supplied by
the server launch payload. The existing task-route parser and native host own
overrides and credential custody. No local database or workflow engine is added.
Uncertain transport/source identity stops consumption for explicit attention;
service shutdown fences new launches without claiming a running process stopped.

Explicit abort uses this same exchange: only a poll naming the exact dispatched
invocation receives `state=stop` with its attempt. The local consumer relays that
intent to the existing native process control, which fences registration/spawn
or confirms owned process-group exit. It never signals a session or searches
for processes. Connection loss alone still sends no stop. A pending, unconsumed
launch can be prevented on the server; dispatched work requires local confirmation.
Unresolved-task admission fences do not suppress a stop for the exact retained
invocation. Missing, stale or unidentified work remains unknown, and the same
fence still prevents consuming a new launch.

Reports contain `invocation_id`, `attempt`, `state=completed|unknown|stopped`, and
an `outcome` for completed work, using the existing host result fields. A stopped
report requires `stop_outcome=stopped|prevented` and may retain the native
outcome/usage. Unconfirmed termination uses `state=unknown` with
`stop_outcome=unconfirmed`. These reports are accepted only for the matching stop
intent, including after controller retirement, and cannot accept work or revive a run.
Supplied native outcomes must match the invocation's known resume/session identity
before any stop confirmation or usage is retained. Launch prevention may omit
the outcome or provide a sessionless error with no result or usage.
Offline/unconfirmed abort stays `stop_unconfirmed` with needs attention in the
existing delivery metadata; retirement or a second abort is not confirmation.
Owner reconciliation resolves retained uncertainty before another handoff.
The first terminal report signals the owning caller; identical repeats acknowledge
`replayed=true`, and conflicting reports or session identities refuse with 409.
Only the controller can release a known completed or confirmed-stopped handoff
after persisting its result and finishing the existing invocation. This transport does not advance
tracker/run state or decide whether a domain result succeeds.
Known terminal handoffs are released after their evidence is durable even when
validation rejects the candidate or the workflow occurrence was superseded.
The rejected result remains failed; releasing transport ownership does not
accept it. Unknown or live work retains its admission fence.

A lost poll response returns `needs_attention` on subsequent polls without a
second launch payload. A lost report response can resend the same retained result
without rerunning native work. Unknown work blocks admission and requires
explicit reconciliation; neither disconnect nor uncertainty sends process signals.
After transport retirement/restart, a query naming an absent invocation returns
`unknown`, and a late report refuses. There is no durable queue or automatic
restart reconstruction. The controller must retain uncertainty in its existing
attempt owner before considering further work; transport `idle` alone proves no
prior outcome.

On a selected server, `tracker.startDelivery` takes `connection_id` in place of
`project_dir`. The verified owner selects an authorized computer that is actively
polling. Optional `base_commit_sha` pins the initial local source; local candidate
validation and independent review enforce the returned exact SHA. Folder and MCP
configuration remain on that computer. Server input cannot select them.
The existing server controller freezes role settings, dispatches invocations,
persists results/usage, and alone accepts tickets and workflow steps. Separate
native sessions, three repair passes, and correction rules retain their owners.

Admission reserves that connection in the existing task snapshot. Before offering
an invocation the controller marks its outcome uncertain there; only a persisted
known result clears that marker. A disconnected computer, unresolved handoff or
uncertainty surviving restart requires manual reconciliation before a new handoff.
This fence covers every task sharing that connection, including stopped tasks.
An idle transport after restart does not authorize replay. No process is killed
or relaunched to resolve uncertainty.
## Project display names and catalog order

`project.update` changes display names through the existing project repository.
Names are trimmed, nonblank strings of at most 200 characters; duplicate names
are allowed. A same-name update is a no-op. IDs, slugs, workspace bindings and
repository folders retain their identity. Agent and browser callers can patch
only `name`; daemon-admin REST/CLI callers retain safe metadata administration.

`project.list` and the legacy project list route use stored `(order_index, id)`
order and matching ID cursors. Migration `0038_project_order` preserves the
previous ID-ascending order; newly created projects append. Start pagination
again after a concurrent catalog change. Explicit portfolio `recent`/`name`
sorts retain their own documented projection ordering.

`project.move` accepts one `project_id`, `placement` (`top`, `before`, `after`),
and an `anchor_id` for before/after. It moves that project within the complete
stored catalog, including records outside the caller's loaded page. It never
accepts a replacement list. Active projects are permitted visible targets and
anchors; missing/inactive anchors reject before mutation. Self and adjacent
no-op moves preserve all data. A canceled interaction makes no call.

Console callers explicitly supply `scope: "catalog"` to `project.update` and
`project.move` through their browser-safe generic REST operation routes.
Catalog scope requires authenticated local-admin/console REST authority and
cannot be asserted through MCP. Ordinary `scope: "project"` calls keep normal
bridge injection: a bound agent can rename/move only its own project, relative
to an active project visible in `project.list`. No bridge scope exception exists.
CLI uses the same registered operations via `stackos ops call project.move`.

For example, move the selected catalog project before an anchor:

```json
{"project_id": 2, "scope": "catalog", "placement": "before", "anchor_id": 1}
```

## Permanent project deletion

`project.delete` is the single permanent cleanup operation for MCP, REST, CLI
and the browser console. It delegates to `ProjectCleanupService`; it does not
archive the project. Supply the selected immutable ID and its current slug:

```json
{"project_id": 2, "confirmation": {"permanent": true, "project_id": 2, "project_slug": "acme"}}
```

Missing, false or mismatched confirmation rejects before cleanup. Display names
never select the target. Bound agents can delete only their injected project;
global agents must explicitly supply an existing project ID on every call.
Console calls additionally require `scope: "catalog"` through the browser-safe
generic REST operation route. Catalog scope remains REST-admin/console only.
CLI uses `stackos ops call project.delete --input <json-file>`.
The old `DELETE /api/v1/projects/{id}` route, including `hard=true`, is removed;
old unconfirmed callers cannot become destructive.

The bounded result is identical in `compact` and `raw` modes; `ack` is refused.
Its `status` is `blocked`, `incomplete`, `deleted` or `missing`. `blockers` names
active owners that must be stopped through their existing controls. Cleanup
never aborts work or kills an agent. `incomplete` retains the project, the same
`operation_id`, `retryable`, a safe `error_code`, and any
`ownership_required_artifact_ids`. Some files may already be removed; retry the
same confirmed operation rather than assuming rollback. Only `deleted` means
cleanup completed. It removes live owned StackOS data and managed files while
preserving repository folders, other projects, shared accounts/files, external
exports and existing backup archives.

Deletion handles retries by observing current state instead of storing a replay
receipt in the deleted project. A same-key retry revalidates confirmation and
returns `missing` after deletion. Global bridges retain existing-project
admission and instead reject a missing saved ID with `project_unavailable`;
they never substitute a same-named replacement. No deleted project payload,
audit history or idempotency row is recreated.

Unaudited legacy generated files can require an explicit ownership decision.
Only an administrator using REST or CLI can call
`project.reconcileFileOwnership`; it is unavailable to MCP and the console.
It delegates to `ArtifactRepository.reconcile_generated_file` and accepts only
a stopped cleanup's reported artifact gap. Review the exact identity, URI and
current content hash, then supply:

```json
{"project_id": 2, "project_slug": "acme", "artifact_id": 42, "expected_uri": "/generated-assets/openai-images/legacy.png", "expected_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", "confirm_ownership": true}
```

The managed root is daemon-derived; callers cannot supply a filesystem path.
Wrong identity, changed content, unsafe files or absent cleanup gaps reject.
Ordinary deletion confirmation and editable artifact metadata cannot grant
custody. After reconciliation, retry `project.delete` with the original ID/slug;
its operation ID is preserved, including across restart.

## Project setup receipts and reminders

`projectSetup.get({"project_id": 1})` reads shared setup status without launching
an agent or changing completion. Optional `workflow_key` selects detail while
project counts remain aggregate. The bound MCP bridge injects and fences project
scope. Directoryless projects return `ineligible` with no update count; a selected
unavailable folder returns `folder_unavailable`, retained evidence/preferences and
an actionable folder diagnostic. Existing adaptations without receipts are unknown.

Project Home sends `summary_only: true` with raw response mode. The repository
uses the same source comparison and activation checks for every workflow, but
does not construct workflow source snapshots or historical result bodies in the
response. The explicit `summary_only` marker distinguishes this complete status
list from full detail; it does not use generic compact list truncation. Setup
keeps the default full read. Opening Launch from Home loads full names, versions
and required launch preferences before submission; `detail_ref` also returns the
full default read. No new readiness, polling or cache owner is introduced.

Compact/raw modes are supported; ack is refused. Compact keeps project eligibility,
counts, permanent reminder timestamps, workflow receipt IDs/revisions, latest outcome,
partial-change flags, attempt/result refs and `detail_ref` for full evidence. Raw
includes receipt-owned source snapshots, role/obligation mappings, checked readiness,
activation status and the latest partial result. Source identity never proves
installed activation. Missing source evidence remains diagnostic uncertainty.

`projectSetup.setReminders` is a browser-safe local-admin REST/CLI operation,
unavailable over agent MCP. For example:

```json
{"project_id": 1, "dismiss_setup": true, "dismiss_updates": false}
```

Each true field permanently stamps only its own preference; omitted/false fields
preserve existing dismissals. Rename, later versions and opening setup do not reset
them. This operation changes neither setup success nor quiet update counts.
See [applied evidence](workflow-templates.md#applied-setup-evidence) for the checked
result contract.

`workspace.startSession` also returns `project_setup`: a compact projection of the
selected project's eligibility, setup/update counts, current attempt reference,
repair diagnostics and shared `workflowTemplate.authoringGuide` reference. Profile
readiness remains separate. Startup creates no setup request, native invocation,
workflow run or tracker task, and does not edit local guidance. An unselected global
session has no project summary; selected global calls still require explicit project
IDs. A summary neither changes workspace binding nor grants filesystem access.

`projectSetup.start` supports existing-session agent-MCP entry and authenticated
console REST entry with `entry_kind: "managed_ui"`. `projectSetup.report` remains
agent-MCP only; CLI cannot start setup. Existing-session entry uses
the session ID returned by startup (`id` in raw; `agent_session_id` in compact).
The bridge injects and fences `client_session_id`; it must match that session and
project. For example, through `toolbox.call`:

```json
{"tool_name":"projectSetup.start","arguments":{"session_id":123,"mode":"setup","workflow_keys":["engineering.tracked-delivery"],"idempotency_key":"setup-this-session"}}
```

New Setup and Update calls default `requested_scope` to `ready_to_use`. Select
`infrastructure_only` explicitly to finish at preparation. Stored attempts and
receipts without the field retain historical infrastructure semantics; reads do
not upgrade them. Attachment preserves the existing owner's scope, and replay
binds scope as part of the original intent. Neither scope grants additional
operation, local-admin or business-action authority.

`VerifiedSetup.activation_proof` records the selected `method_ref`, required
claims, missing claims, status and bounded checks labelled `reused`, `executed`
or `simulated`. Each check identifies project/binding, effective source-set,
saved delivery-default and local-adaptation digests plus its environment ref.
Digests use SHA256 of canonical JSON, as for source snapshots. `saved_defaults`
and `native_consumption` are minimum coverage; workflow method may require more.
Simulation cannot cover required claims. Deferred per-run inputs remain in the
separate readiness tuple.

Checks reference an exact completed task/ticket phase, native request/invocation
or workflow/step and the canonical JSON digest of its result. The receipt owner
rechecks project, observed outcome, source/default basis and completion; failed,
cancelled, live, foreign or changed evidence cannot receive positive coverage.
Native coverage requires canonical request resolution, a result validated against
its frozen output schema and a successful logical outcome, including the wrapped
managed setup report. Process exit alone is insufficient. Failed, blocked or
missing outcomes can be retained as failed checks without covering activation.
This validates reference integrity. The workflow's agent and independent review
still judge what the underlying evidence actually covers, including loaded source
and environment.
No additional proof workflow is required beyond managed setup's control run.
A ready-to-use attempt cannot report final success until every selected workflow
has current verified activation coverage
and observed loading. Missing coverage returns `complete_activation` with the
workflow and missing claims. Preparation results and other completed workflows
remain inspectable while required activation is pending or blocked.

`report.adoption_proposal` contains only the observed project/root/binding,
expected saved-delivery digest and bounded role selections with provenance.
Use the scoped `projectSetup.get.delivery_defaults_digest` for the proposal's
`expected_delivery_digest`. Refresh it before reporting; the managed owner still
rejects stale proposals. This does not grant access to the console-only
`resolve_launch_preferences` preview or authorize a defaults write.
Codex selections use exact source refs/digests; supported other hosts retain
their native inline contracts. The repository validates and retains the proposal
without writing defaults. It cannot supply trusted-local-admin context or an
arbitrary operation payload. Authorized adoption and readback remain with the
existing managed/defaults owners. Compact workflow reads include requested scope,
activation status and missing claims; raw receipts retain detailed proof.

Authenticated managed `ready_to_use` admission retains narrow adoption authority
in one protected `AgentRequest.metadata_json.project_setup.workflow_admission`.
The row links the actual `core.project-setup` run and task; it is an admission/source
record, not an execution owner. Public run/task metadata is only a locator and
cannot grant bootstrap or adoption authority. Generic request mutations and the
legacy `projectSetup.report` writer cannot operate on this tracked admission.

At accepted preparation, the setup adapter rechecks the protected admission,
folder/binding, actual native receipt, proposal sources and saved-default digest.
It composes canonical adoption/readback, pending verification bindings, setup
receipts and run-plan step acceptance in one transaction. Failure rolls them all
back before safe failure evidence is retained. Compatible role preferences,
explicit nulls and unrelated settings remain intact; queued/running snapshots
are untouched. This does not expose `tracker.configureDelivery` over MCP or CLI.
Configured delivery hosts are checked against saved role/project inheritance
before proposal overrides, independently of the setup agent's host. Matching
inline updates retain omitted role hosts so future project-host changes still
inherit; a conflicting proposal cannot silently replace that preference.

Managed native results use the normal status/summary/outputs/followups envelope.
JSON-encoded `outputs` contains `setup_report` and, for an assigned check,
`setup_activation_checks`. Preparation records selected missing `workflow_checks`.
The canonical method binds a never-started verification mirror to one missing role,
or uses normal children for multiple distinct roles and final Main aggregation.
The owner derives exact completed `task_phase` references from unchanged native
receipts, fills only matching predeclared slots and applies existing activation
validation. Generic success never establishes a missing specialist claim. There
is no completion-only native turn, successor-request chain or second proof store.

The agent chooses workflows under the shared guide and inspects unknown legacy
adaptations before deciding what to change. Admission is serialized with the
project owner. Its protected, versioned setup namespace retains selected workflows,
observed receipt revisions, progress, outcomes and questions. Generic request
create/claim/release/prepare/link/complete/ignore operations cannot own this namespace.
Unknown legacy native ownership remains a blocker until explicitly reconciled;
historical records are not backfilled with invented tracker work.

For `existing_agent`, the first start returns a private claim token for its
executing session. Do not put it in artifacts or guidance. Use a stable idempotency
key to recover the original response after an uncertain retry. A concurrent start
attaches as an observer to the existing attempt and receives no writer token.
Attach does not replace the owner, change its workflow selection or grant report
authority. There is no expired-lease takeover. Managed start instead returns real
`run_plan_id`, `run_id` and `task_key` references with no worker claim token.

Existing-agent setup replay rechecks the bound session and original normalized
request before
returning any cached response. The key binds the session, attempt, token, expected
revision and complete report; changing only `response_mode` preserves an exact
retry, including compact-to-raw recovery. Foreign or changed-intent reuse returns
structured repair context and never exposes the owner's token. A cached start is
usable only while its original attempt/claim remains active. Exact terminal-report
retries return that historical request without restoring its claim or changing a
successor; cached running progress cannot represent a finished attempt. Older cache
entries lacking original-intent evidence reject for inspection.

`projectSetup.report` is for existing-agent attempts and requires that session,
token, `request_id` and the expected
attempt revision. Its typed `report` includes a status, bounded progress message
and evidence ref, optional checked workflow results, and a concrete question only
for `needs_input`. Each workflow result carries `workflow_key`, its expected receipt
revision and the existing `SetupResult` proof contract, with the exact attempt ref.
Successful workflow writes advance only after source, role, obligation and readiness
validation. Failed/unfinished outcomes retain prior success and possible partial
edits. A report is atomic; a stale/foreign/invalid item changes neither attempt nor
receipts. Attempt success additionally requires current verified success for every
selected workflow, including a source/receipt freshness recheck at completion.

Existing-session questions carry `question_id`, `revision`, `prompt`, affected
`workflow_keys`, `answer_state: "pending"` and `response_channel: "existing_agent"`.
The agent asks and receives answers in that session, then reports subsequent work.
Home can display the question and status without promising an external conversation
link or accepting a browser answer for this entry kind. Report history retains
question/progress/result refs; sibling native execution metadata stays untouched.

Managed console entry needs only the selected project and setup/update mode;
`workflow_keys` can be omitted so the coordinator chooses from the operator goal.
Optional typed `preferences` contain only `host_key`, `model`,
`model_reasoning_effort`, `provider`, `profile`. Omitted fields inherit saved
main-agent choices on the same host; explicit null clears that field for this
launch. Changing host discards inherited host-specific choices. Changing model
discards inherited reasoning unless reasoning is also explicitly supplied.
Save these choices through local-admin `tracker.configureDelivery({"project_id":1,
"main_agent":{"host_key":"codex","model":"gpt-6.2-sol"}})`. That form merges
only `delivery.main_agent`, preserving specialist roles, unrelated settings and
already admitted snapshots. `projectSetup.get.launch_preferences` reads saved choices;
`hostMcp.status.agent_execution` supplies supported and recommended models from the
existing host catalog. Neither catalog nor recommendation proves account model access.
`hostMcp.status` defaults `include_models` to true for selection consumers.
Overview requests false: it keeps fresh connection/runtime/version checks while
skipping native model lists; any packaged model suggestions remain explicitly
unverified. Web and desktop share this demand, including desktop's CLI fallback
(`mcp-host-status --no-include-models`). This read option does not change fresh
launch validation, subscription reads, account selection or retained sessions.
When the console needs an inherited host for the launch dialog, it requests
`projectSetup.get` with `resolve_launch_preferences: true`. This local-console-only
read returns effective choices in `launch_preferences` through the same resolver
as launch, retaining explicit saved fields and invalid choices for editing.
Ordinary reads still return saved preferences without inspecting native hosts.
With no saved or explicit model/effort, native defaults are retained without
guessing their effective values; recommendation metadata is advisory. Launch
overrides do not save project defaults. The `main_agent` configure form replaces
only that preference object, so omitted/null fields there really clear saved
choices. Native provider/profile names select host configuration, never credentials.

`agent_execution.reasoning_efforts` maps explicit model IDs to verified native
effort choices. Missing model entries (including native-default and custom
models) permit only native reasoning. The launch dialog consumes that same
catalog, shows model and reasoning in its summary, and puts optional native
provider/profile selectors in Advanced options; required provider/model remain
visible. Unsupported pairs reject before native execution. Codex uses its
validated `model_reasoning_effort` config override; Claude uses `--effort`; Grok
uses `--reasoning-effort`. Antigravity model slugs carry native effort; separate
effort overrides are unverified. Hermes retains native reasoning because this
runner has no verified per-invocation override. Claude
rejects a conflicting inherited `CLAUDE_CODE_EFFORT_LEVEL`, whose native priority
would otherwise override the selection. Normal permissions and auth stay native.

`agent_execution.mcp_routing` declares the supported native configuration mechanism:
`per-call`, `workspace`, `profile`, or `unsupported`. It is separate from connection
readiness. Task admission verifies the intended root/project and freezes route
identity through the existing delivery owner; a declared mode does not prove that
native tools loaded or ran. See [task-local routing](task-tracker.md#task-local-native-tool-routing).

One managed attempt creates or attaches to one `core.project-setup` control run
and its real mirrored Work task. Explicit `infrastructure_only` composes one
`prepare` ticket; ordinary `ready_to_use` adds `activate-verify`. Both use the
canonical task-delivery queue, native session/usage receipts, cancellation and
recovery. Navigation does not cancel work. `projectSetup.get.work` projects actual
run/delivery state and available controls; setup receipts separately prove readiness.

The bootstrap Main snapshot comes from the shared authoring method and saved
Main preferences. It works before project role files exist without relaxing
ordinary source-backed Codex admission. Invalid hosts or unavailable/mismatched
folders fail before native spawn, without host/model fallback. After accepted
adoption, only never-started verification bindings may consume newly selected
sources; active and resumed sessions remain frozen. A first scheduling failure
retains truthful stopped work for explicit recovery, not a fabricated session.

Managed cancellation uses canonical `runPlan.abort`; `projectSetup.cancel` is a
bounded console adapter to the same owner. It cancels queued work before spawn and
keeps native ownership until retirement. Late results cannot restore authority.
Completed workflow receipts and evidence of possible edits remain visible; no
rollback is implied. Unconfirmed legacy native work still blocks competing
admission and cleanup. Cancelling does not launch replacement work.

For an `existing_agent` attempt, the same console operation requires the exact
`expected_revision` and strict boolean `external_work_stopped: true`. Stop the
previous setup work in the agent app before acknowledging it. StackOS atomically
cancels that record and revokes its claim under the project writer reservation;
the acknowledgement does not establish observed native retirement, stop a process,
or rebind a session. It preserves original identity, receipts, reports, questions,
answers and possible file changes. A changed revision requires a fresh decision.
Cancellation retries bind the project/request/revision/acknowledgement intent;
exact replay returns only the historical target, even after another attempt starts.
Old start/progress replays and late reports cannot restore the revoked claim.
Home keeps the cancelled record inspectable even when retained receipts are current.
**Set up again** opens a separate launch decision; cancellation never starts it.
MCP, CLI and run-step authority cannot cancel setup.

Console-only `projectSetup.answer` takes `project_id`, current `request_id`,
`expected_revision`, `question_id`, `question_revision` and a bounded `answer`.
It validates the protected run/task and pending question, records the answer once,
then uses canonical recovery and rehandoff after retirement. Existing-agent questions
stay in their original session. Stale, foreign or duplicate input rejects without
launching work. Stable idempotency keys support exact response replay; changed
intent under the same key rejects. Compact/raw retain real work refs and repair
context, never a worker token. MCP and CLI cannot invoke these console controls.

`projectSetup.continue` takes the same project/request/revision fields without an
answer. It recovers remaining stopped work in the same run/tickets, preserving
accepted preparation, receipts and exact retained sessions/settings. A pending
question requires `answer`. Confirmed never-started work can retry its first launch;
possibly started work cannot bypass missing native identity or uncertain retirement.
Source/default changes require current validation and cannot relabel frozen proof.
A completed control run is not reattached as new work: a later Setup/Update gets a
new run while preserving earlier per-workflow evidence.

These managed answer/continue controls require tracked work. Legacy managed
questions remain readable; their live owner can be explicitly cancelled, and
confirmed retired work can start a new tracked setup. The removed coordinator
does not resume. Existing-agent reporting and cancellation keep their separate
session/acknowledgement contract.

Continuation and cancellation retain successful workflow outcomes and possible
partial edits. Readiness still requires current checked reports; task completion
alone is insufficient. Browser rendering and setup journeys consume the shared
`projectSetup.get` work projection and per-workflow receipts.

Both operations support compact/raw and refuse ack. Compact retains claim/attempt
refs, revision, outcomes, question and repair context; raw also retains report history.
Source verification does not establish native loading or installed activation, and
structural setup readiness does not imply that deferred per-run inputs are supplied.
This operation family grants no business execution, provider or repository-file authority.

## Project workflow schedules

Project folder setup has one owner: `Project.primary_workspace_binding_id`.
Folder-backed bootstrap records its chosen binding; connecting task worktrees or
starting sessions does not change it. Existing projects deliberately remain
unselected after migration. Setup selects an existing same-project directory
binding from `workspace.listBindings` through the local-admin `project.update`
patch `{"primary_workspace_binding_id": <binding_id>}`. This does not rebind a
workspace or grant filesystem access. `project.get` returns that exact
`project_dir`, `project_folder_status` (`ready`, `directoryless`, `missing`,
`unavailable`, or `conflict`) and an actionable `project_folder_issue` when local
files cannot be used. No selected primary with no directory bindings means a
supported directoryless project; an unselected existing directory or a stale
primary reference needs explicit setup. Unavailable roots and cross-project
conflicts fail closed without selecting a newer worktree. Reads and retries do
not repair data or change a session binding.

`schedule.list`, `schedule.set`, `schedule.toggle`, `schedule.remove` and
`schedule.runs` share `ScheduledJobRepository` through MCP, registered REST
operations, the project schedule REST URLs and `stackos ops call`.
ID-based mutations require the matching project; a foreign job is missing
before any effect. Writes retain their existing local-admin setup boundary.
Compact responses retain identity/state and repair context; use raw to read back
the concrete goal, supplied inputs and native execution configuration.

`schedule.set` replaces configuration under a stable project `kind` key.
It accepts `name`, `goal` (instructions), five-field `cron_expr`, IANA
`timezone`, `enabled` and an optional existing workflow `template_key`.
`inputs_json` retains supplied context, including migrated workflow inputs.
The main agent resolves concrete workflow inputs, method and roles; schedule
setup does not validate business input completion or require role mappings.

In standalone mode the actual launch folder comes from project setup. Optional
`execution_json.host_key` constrains the shared main-agent preference resolver
used by setup. New occurrences inherit saved `delivery.main_agent` model,
reasoning, provider and profile choices supported by the selected host. Host
selection falls through the saved main-agent host, project delivery host, then
Codex. An explicit different schedule host clears incompatible inherited launch
choices. Delivery/reviewer instructions, source refs and model pins are never
inherited. Admitted occurrences retain their native selection after preference
changes; a later occurrence resolves current preferences. An explicit Codex
`mcp_config_ref` uses the shared
validated task-local route contract. The retained optional
`execution_json.project_dir` only fences a saved legacy folder: if it differs
from project setup, enabling fails until the operator confirms the configured
folder and saves without the old fence. It cannot select a different target.

On a selected server, `execution_json.connection_id` instead selects an owned,
authorized computer. It cannot be combined with a server-selected `project_dir`
or MCP config; the computer's private binding supplies both. The same main-agent
preferences and request lifecycle apply. The server retains the request claim
and final evidence while the shared invocation exchange transports one native
launch and its result. The local process uses the exact remote invocation ID for
attribution, without attempting to claim a request in a local database. Known
terminal work releases transport ownership after persistence; unknown work stays
fenced for inspection. No workflow is created by the scheduler.

Schedules default to disabled and incomplete inert drafts are permitted.
Supplied shape, cron/timezone, workflow reference and no-secret rules still
validate. Enabling checks name, goal, timezone, the canonical project folder,
workflow existence when selected and native host capability/readiness.
It creates no request, plan or task. Raw secrets are rejected even in drafts;
opaque credential refs and exact secret markers retain their normal rules.
Cron uses APScheduler: numeric weekdays run Monday (`0`) through Sunday (`6`);
named weekdays such as `mon` avoid ambiguity. Next-fire times are UTC.

Migration `0042_schedule_main_thread` moves existing active setup once:
goal/instructions are recovered from saved inputs, all input context and
schedule identity/history remain, and role/step dispatch settings are removed.
Missing goal/folder or an explicitly different saved folder disables the
schedule with an actionable setup issue. It never rewrites admitted or
historical request/plan/native snapshots. New occurrences use only the main
thread path; historical plans remain readable and can still prevent overlap.

Electron main holds the authenticated REST-only `desktop.availability` stream
at `/api/v1/desktop/availability`. Browser tokens, MCP and CLI cannot advertise
presence. Quit, suspend or disconnect removes future timers while admitted work
continues. Reconnection arms future instants only. The existing memory scheduler
uses exact next cron instants, one-second jitter tolerance, and generation/config
guards; edits/removal are checked again at native process spawn. No missed ticks
are replayed.

For connected schedules the runtime forwards that availability stream with its
scoped computer credential. Presence applies only to schedules selecting that
connection; admission also checks current authorization, polling and unresolved
work. The browser user session cannot advertise native presence.
The one-second jitter window gates request admission. Once admitted, native
preparation may take longer; generation, configuration, presence and project-folder
checks still fence registration and spawn.

Each eligible instant creates and claims one existing AgentRequest under
`schedule:<id>:<due-UTC-ISO8601>`, then calls shared
`host_mcp.service.run_agent` once with request ownership. Its domain-neutral
bootstrap carries project/request identity, goal, context and optional workflow
reference. It tells the main agent to read current repository/project and
effective workflow guidance and use ordinary tools and approvals. The scheduler
never creates, starts, claims or advances a workflow plan, and never calls
`startDelivery`. Explicit task delivery remains available for deliberately
selected tracked work.

The native service retains actual session, process lifetime, terminal result
and measured counters in the request, renewing only the exact owner's lease.
The controller interprets `status`, `summary` and optional `run_plan_id`.
A real returned plan is same-project validated before linking; prompt-only
results have null plan/task/run refs. Success resolves only with canonical
successful native evidence. Failed launches/crashes remain failed with
diagnostics; blocked or unobserved exits remain unresolved for explicit
inspection. Existing occurrence keys never retry. Unresolved requests prevent
overlap even with no plan, an expired lease or an interrupted last-request
pointer write; linked unfinished real plans/tasks also prevent overlap.
No fallback runner or automatic resume/replay exists.

`schedule.remove` deletes configuration and reconciles its timer, returning
`{project_id, job_id, removed}`. It does not cancel admitted work or delete
history. Schedule IDs are never reused (`0037_schedule_id_nonreuse`).
Every mutation reaches the daemon's `schedule_reconcile(project_id, job_id)`
seam after commit.

`schedule.runs` pages scheduled requests first (limit 1–200, descending
request-ID cursor `after_id`, optional `schedule_id`). Safe output retains
schedule name/ID snapshots after rename/removal, request status, actual native
identity/session, outcome/summary/error, timestamps/duration and measured usage,
plus optional real workflow/run/task links. Historical workflow-only records
retain their canonical task status, usage and recorded `work_duration` projection
(also exposed as nullable `duration_seconds`). Native rows retain their own
process duration, separately from linked task work time. Main-thread usage does not add
linked child-task totals. Missing usage remains unknown. Claim/run tokens,
prompts, launch snapshots and arbitrary request metadata are never projected.
Skipped ticks create no row; there is no second occurrence or usage ledger.

For example, save an inert prompt draft with
`stackos ops call schedule.set --input automation.json`:

```json
{
  "project_id": 1,
  "kind": "weekly-review",
  "name": "Weekly review",
  "goal": "Review project context and report useful updates.",
  "cron_expr": "0 9 * * mon",
  "timezone": "America/Los_Angeles",
  "enabled": false
}
```

Add `template_key` for a workflow hint. Normal grants and approvals apply to
any work the main agent chooses; enabled configuration alone is not evidence
of desktop availability.

## Task Board writes

Task Board writes use `tracker.placeTask`, `tracker.updateTaskPriority` and
`tracker.reorderTask` through the registered MCP/REST/CLI surfaces. Placement
accepts only Backlog/Todo for eligible nonstarted work; reorder uses the shared
projected Board column. Neither grants delivery execution authority. All retain
strict snapshots, intent-bound idempotency and raw save receipts. See
[Board membership and order writes](task-tracker.md#board-priority-and-order-writes).

## Managed upgrade preparation

`maintenance.prepareUpgrade` and `maintenance.upgradeStatus` share the registered
installation-administrator REST/CLI contract. They are disabled through MCP and
for the browser console, reject run tokens and project arguments, and do not
grant upgrade replacement or workflow execution authority. CLI uses the same
authenticated operation route:

```bash
stackos ops call maintenance.prepareUpgrade --input prepare-upgrade.json
stackos ops call maintenance.upgradeStatus
```

The input file contains the explicit transition identity and CAS revision:

```json
{"upgrade_id":"upgrade-123","source_identity":"source-build","target_identity":"target-build","expected_revision":0}
```

Preparation atomically saves one durable installation fence and references to
task-owned checkpoints, then retires exact managed process and callback owners.
`quiesced` means the selected preparation succeeded; it does not claim target
installation or continuation. Unknown native outcomes or unconfirmed retirement
return structured 409 repair context with the fence retained. Status exposes
the current revision and per-checkpoint issues. Successful retries are
idempotent; a held retry pins the current revision and original build identities.
Compact/raw preserve the complete bounded preparation receipt; ack is disabled.
See [checkpoint ownership and interface](task-tracker.md#managed-upgrade-preparation).

## Local task admission capacity

`tracker.deliveryCapacity` and `tracker.setDeliveryCapacity` use the same
registry-owned local-admin console boundary as other explicit settings controls.
They are REST/CLI-only, browser-safe operations with compact/raw readback and no
ack mode. Neither accepts workflow run-token authority. Read with `{}` for all
projects or `{"project_id": 7}` for one project. Both return `scope: "project"`,
`projects`, `supported_min: 1` and `supported_max: 32`. Each project row preserves
`project_id`, `project_name`, `project_slug`, `configured_limit`, `running_count`
and `queued_count`. Running includes admitted task callbacks through retirement,
including tracked setup tasks, plus genuine legacy setup reservations; it can
exceed a lowered limit. Waiting counts current-daemon queued tasks without a
reservation. A tracked setup admission row is not counted again as a native owner.

Save with `{"updates": [{"project_id": 7, "max_concurrent_tasks": 6}]}`. The array
accepts 1..100 distinct positive project IDs and strict integer limits 1..32.
Unknown projects or invalid input reject the entire request. Only changed rows
are written, in one transaction; drain runs only after a successful changed-row
commit. Failed commit leaves every prior limit effective and triggers no drain.
Lowering affects future admission without stopping or cancelling admitted work.

The existing capacity table owns each project's preference. Migration copies the
saved legacy limit (or default 3) to every existing project, including inactive
ones; new projects default to 3 without read-side writes. Permanent project
deletion removes only its capacity row. Ordinary restart/install/upgrade and
encrypted database backup retain the settings. No separate settings file,
global quota, ordinary MCP mutation surface or workflow authority is added.
Downgrade to the singleton schema is allowed only when all effective project
limits agree; divergent limits reject before schema or preference changes.

## Local backup administration

`backup.status`, `backup.configure` and `backup.create` share the generic
encrypted local backup service for standalone SQLite installations. Online
server/runtime calls refuse with `unsupported_backup_backend` before reading
local state or keys; there is no Postgres full-instance archive/export/restore.
They are local-admin REST/CLI operations,
with explicitly browser-safe controls on Backups (`/setup`). They
are excluded from MCP, project/workflow grants and derived agent-token
authority. Their exact registered REST paths enforce the local-admin boundary.

Configuration stores a public recovery key, explicit external folder targets,
destination and opt-in schedule. Partial configuration preserves omitted
fields. Status and creation return safe coverage, time, path, size, checksum
and failure metadata; no archive bytes, private recovery key, credential or
financial contents are returned. Status includes covered managed project IDs,
or null when coverage could not be verified. Creation includes all managed
project files and enrolled targets and
rejects unsafe destinations. Recovery private key generation and verified
restore remain host-local CLI operations. See [backup and recovery](./backups.md)
for setup, automatic project coverage and key custody.

## Agent Documentation

Agents are the primary callers for workflow execution operations. Here, agent
means the MCP/tool consumer calling StackOS, not necessarily a process with
repository filesystem access. Scripts can use the same catalog for automation
and setup, but run-plan mechanics are optimized for agents that need precise
schemas, grants, examples, and safe return notes. Callers should not guess
operation payloads. The operation registry returns an agent-readable
description for every registered operation:

```bash
stackos ops list
stackos ops describe action.execute --json
stackos ops describe action.run --json
stackos ops describe actionCall.get --json
stackos ops describe runPlan.claimStep --json
```

The same metadata is available through REST:

```http
GET /api/v1/operations
GET /api/v1/operations/action.execute
GET /api/v1/operations/action.run
GET /api/v1/operations/actionCall.get
```

Native MCP clients can read the same operation catalog without leaving the MCP
session. In the agent bridge, these calls are hidden setup tools: call them
through `toolbox.call` rather than expecting them in the first-level MCP tool
list.

```text
toolbox.call({
  "tool_name": "operation.list",
  "arguments": { "surface": "mcp", "mode": "grouped", "response_mode": "compact" }
})
toolbox.call({
  "tool_name": "operation.describe",
  "arguments": { "name": "communication.send", "surface": "mcp", "response_mode": "compact" }
})
toolbox.call({
  "tool_name": "agentPreset.resolveForWorkflow",
  "arguments": { "workflow_key": "core.project-memory-review" }
})
toolbox.call({
  "tool_name": "integration.list",
  "arguments": {}
})
```

The discovery operations are OperationSpecs too. `operation.list` includes
`operation.list` and `operation.describe`, and `operation.describe` can describe
both discovery tools with the same schemas, examples, and guidance it returns
for domain operations. Use `surface: "mcp"`, `mode: "grouped"`, and
`response_mode: "compact"` when an agent only needs operation names by category
instead of the full inventory. After that, call `toolbox.describe` with exact
`tool_names` before invoking the selected operation. In workspace-bound bridge
sessions, omit injected fields such as `project_id`; the bridge supplies the
current workspace scope and refuses cross-project calls.

Directory resolution uses the actual root or the most specific containing
directory binding. A supplied repository fingerprint cannot override that
directory, and a shared Git remote supplies project candidates only; it never
selects another worktree's binding. When an unbound directory shares a remote
with an existing project, startup waits for explicit selection even with
`auto_bootstrap=true`. Start with `workspace.startSession(auto_bootstrap=false)`,
then call `workspace.connect` with the approved existing `project_id` and
`rebind_existing=false` through that directory's bridge. Let the bridge inject
the actual root and path fingerprint. Resolve and start again to confirm its
distinct binding; the original project folder and other worktrees stay bound.
No Git repository is required for directory binding.

In a global session, first call
`workspace.startSession({"global_session":true,"project_id":42})` for the
chosen existing project, or omit the ID to discover projects first. Startup
creates no project/directory binding and never sets a default. Pass an explicit
positive existing `project_id` on every scoped call, inside `arguments` for
`toolbox.call`. Direct browser calls use their top-level `project_id`.
Run-aware discovery also needs the project before the bridge refreshes grants:

```javascript
toolbox.describe({"project_id": 42, "run_id": 7, "tool_names": ["resource.upsert"]})
toolbox.call({
  "tool_name": "runPlan.get",
  "arguments": {"project_id": 42, "run_plan_id": 3}
})
browser.profile.list({"project_id": 42})
```

Use IDs from the selected project/run, not these example numbers. A run or plan
from another project, missing project scope, or failed authority refresh cannot
reuse cached permission. Direct-read and direct-run-audit-write inner IDs remain
operation targets, so historical `run.get` and ordinary run maintenance do not
require an active workflow. An outer `toolbox.call.run_id` denotes execution
context and is validated separately. Provider `*_json` payload IDs are opaque.
For pending discovery, normal folder startup, named setup, and saved-ID adoption,
see [the setup path](agent-operating-model.md#setup-path).

For action discovery, `action.list` answers "what can I use now?" and hides
disconnected, deferred, project-local, missing-connector, and otherwise
non-executable external-provider actions by default. Pass
`include_unavailable_integrations=true` only for setup or deliberate catalog
inspection. Use `integration.list` for compact provider readiness and hidden
action counts, and `readiness.check` for one selected workflow/action.
Generated provider actions use stable public refs in `action.list`; internal
inventory storage keys and retired generated rows are audit history, not
callable options. Search indexes action metadata, input/output schema fields,
category/tags, paths, and operation ids so agents can discover actions from
business intent before fetching one detailed schema.

Each description includes:

- enabled surfaces: `mcp`, `rest`, `cli`
- grant policy, for example `run-plan-step-action-ref`
- secret policy
- response policy: default mode, allowed modes, ack safety, and raw-only reason
- JSON input schema
- JSON output schema
- prerequisites
- return notes
- examples

`secret.set` is the narrow exception for write-only action payload ingress. It
is available only through MCP/toolbox, is scoped to the selected project, and
returns one opaque `secret_ref`. It is intentionally absent from REST and CLI
and has no plaintext read/list operation. Put the ref into an action string
field only as the exact marker `{"$secret_ref":"secret_..."}`. This is
separate from `credential_ref`, which remains provider authentication.

## Public Getting-Started Guide

`guide.gettingStarted` is the agent-readable path to the public first-use
guide. It is a global, read-only operation, so an agent can call it before a
workspace is bound:

```text
toolbox.call({
  "tool_name": "guide.gettingStarted",
  "arguments": { "response_mode": "raw" }
})
```

The raw response returns the current Markdown plus two stable references:
`guide_url` is the designed page for people, and `markdown_url` is the
plain-text source for tools. Agents should answer from the fetched content and
include `guide_url` for the person. They must not turn the skill prompt or MCP
response into a second maintained guide. If the website fetch is temporarily
unavailable, the operation still returns both references and a warning;
share `guide_url` and avoid inventing replacement instructions. Compact mode
keeps the references and omits the document body.

## Response Modes

`artifact.download` and `actionCall.download` are raw-only reads of existing
project-owned files. They expose authenticated relative download URIs, bytes,
and checksums. A native MCP caller may explicitly request `destination_path`;
the bridge materializes and verifies a new local file before returning
`local_path`. Workflow calls retain active step grants. REST/CLI callers omit
that host-only argument. See [private files](resources-and-artifacts.md#private-files-and-explicit-local-downloads).

Operation responses are shaped for agent decision-making. The canonical
operation result is always the raw redacted payload. When an agent requests a
different shape, StackOS stores raw first for idempotency, then shapes the
returned payload.

- `compact`: next-call-sufficient ids, refs, counts, warnings, schema summaries,
  bounded lists/text, and action hints. Compact mode omits file/content bodies.
- `raw`/`standard`/`verbose`: full redacted daemon payload.
- `ack`: minimal success envelope for safe internal writes only.

Compact `agentPreset.describe` and `skillPreset.describe` responses use the
same adaptation projection as workflow preset resolution. The top-level
`project_adaptation` packet retains bounded adaptation instructions and source
references (`ref`, `purpose`, `required`, and conditional `when`), alongside the
preset's source/version identity. Read the same operation with
`response_mode="raw"` for the complete contract when compact text or lists are
truncated. References provide guidance; they grant no execution authority.

Errors are never compacted. Validation failures, grant denials, auth/setup
diagnostics, and provider partial failures keep structured repair context so an
agent can decide whether to retry, claim a step, reconnect a provider, or stop.
Structured validation results with `valid=false` retain their full repair context
even when compact or ack is requested. Successful safe internal writes still
use the minimal ack envelope. For example, `tracker.createTicket` shares complete
reference validation between list dry-run and apply, and protects single/list
creation with a repository savepoint: late relation failures cannot leave partial
tickets, relations, revisions, or task rollup changes for error cleanup to commit.
List updates keep their existing per-item outcomes.

External provider actions through `action.run` and `action.execute` are
compact file-backed by default for MCP/REST and raw-by-default for CLI, with
explicit `response_mode` still allowed by policy. MCP/REST compact responses
must keep the action-call id, provider/action refs, cost, status, file path,
checksum, `schema_ref`, `schema_operation`, and receipt metadata; raw provider
response data lives in the file-backed action-output envelope for MCP/REST
defaults. CLI calls default to raw inline output unless an explicit output
policy says otherwise. Agents call `schema.get` with `schema_ref` only when they
need the envelope schema.
Communication delivery operations
such as `communication.send` and `communication.reply` remain raw-only until
their own provider-safe compact contract exists.

When a background action is accepted, its response has status `running` and
includes `action_call_id`, `poll_operation=actionCall.get`, exact
`poll_arguments`, and `next_poll_after_ms`. Call the project-scoped,
read-only `actionCall.get` operation until the persisted status is terminal.
Its live progress counters exist only in the daemon process and are never
terminal evidence; stored status, output/error, and completion time are the
authority. After a daemon restart, an orphaned running call is stored as
`failed` with `outcome_unknown=true` and `retry_safe=false`.

`runPlan.recordStep` rejects `success` and `skipped` while the same project's
plan step has running action calls. Its validation error returns pending action
IDs and polling repair context; no step lifecycle state changes on rejection.
Failure and blocked recovery retain their normal semantics.

MCP tools are generated from the same operation specs, so the daemon has one
callable contract per StackOS primitive. The agent bridge advertises
`workspace.startSession`, `workspace.resolve`, `toolbox.describe`,
`toolbox.call`, and local full-control `browser.*` tools. Use
`operation.list` and `operation.describe` through the toolbox for purpose,
prerequisites, return notes, examples, and schemas. Use `toolbox.describe` for
exact hidden setup and step-granted tools before calling them.

The operation registry remains agent accessible through MCP, REST and CLI.
`GET /api/v1/operations` and `GET /api/v1/operations/{operation_name}` retain
their shared contracts. The standalone human operation-catalog page is removed;
relevant object Details may expose safe contextual metadata.

## Agent activity reads

Overview reads subscription data through the local-console REST operation
`hostMcp.subscriptions`. Its timestamped snapshot contains each supported
host's plan and quota windows with scope, percentage remaining and optional
reset time. An unavailable host reading is null. Native registration, agent
selection and Doctor do not invoke subscription checks.

Antigravity's reader runs bounded `agy --print /usage --output-format json`
(12 seconds, 1 MiB per output stream). Only an exit-zero `SUCCESS` usage command
is eligible. Its `command.data.groups[].buckets[]` supplies bucket `id`, `name`,
`window`, `remaining_fraction` and `reset_time`. The verified weekly buckets
become separate group/window meters, retaining zero/full remaining fractions
and valid reset instants. The console shows used percent as
`(1 - remaining_fraction) * 100`. Unknown windows and invalid buckets stay
unavailable; valid sibling buckets survive. No plan, credits or email is
inferred. Timeout, sign-in text, native error and malformed output leave only
that host's reading null, independently of MCP connectivity and healthy siblings.
The source fixture is a sanitized agy 1.5.13 capture; it proves the mapping,
not installed activation or native model execution.

Each host resolves its selected account before its native read and again after
all native readers finish. A changed account reference or generation discards
only that host's reading and returns its current selection. If a selected
profile cannot be resolved, that host's reading is null and its entry is omitted
from `selections`; no native default or empty selection is substituted. Healthy
siblings retain their own readings and selections. A native reader failure alone still returns the
resolved selection with a null reading.

For Home connected tools, existing `account.list` and `connection.list` accept
`summary_only=true` (also on their existing GET routes). This skips provider
setup inventory construction and returns the exact same safe Account records:
status/setup-required, auth method, expiry/revocation, last-test diagnostics,
scopes, safe identity and project attachments remain complete in compact/raw
mode. `providers` is empty and `summary_only` is explicit. Default setup APIs
retain the full provider inventory. Home display names come from the current
project store or `project.get`, not `project.portfolio` supervision.

Work summary and exact metric demand use the existing `tracker.get` and timeline
stream owners. See [UI summary/demand](./task-tracker.md#graph-projection) for
complete lightweight scope, null unrequested metrics and legacy compatibility.

Home uses `tracker.activeTasks` (`project_id`, direct-read) and
`tracker.activeTasksAll` (local-admin-read). Both are read-only registry
operations exposed through REST/CLI/MCP, with the global operation denied to
project-bound MCP callers. `ActiveTasksOut` preserves bounded task-index facts
and project/task identity in compact/raw mode, without prompts or receipt
payloads; ack is unavailable. Pass `limit` and then the returned `next_cursor`
as `after_id` for another page. See [Home active-task reads](./task-tracker.md#home-active-task-reads)
for lifecycle, scope and refresh semantics.

```json
{"arguments":{"project_id":1,"limit":100,"response_mode":"raw"}}
```

Use this payload at `/api/v1/operations/tracker.activeTasks/call`. Local admin
may call `/api/v1/operations/tracker.activeTasksAll/call` with
`{"arguments":{"limit":100}}`.

`tracker.agentStats` (`project_id`) and `tracker.agentStatsAll` (`is_active`:
true/false/null for Active/Archived/All, default true) expose one tracker-owned
projection of recorded invocation receipts. The project operation uses
`direct-read` with project scope; the global operation uses `local-admin-read`,
so project-scoped MCP callers cannot read it. Registry-generated MCP, REST and
CLI surfaces share `TrackerAgentStatsOut` and support compact/raw, without ack.

Both return scope, `as_of`, `totals` and grouped `rows`, preserving all numeric
usage and missing/classification coverage in compact responses. No private
receipt payloads are returned, and reads change no state. See
[metric semantics](./task-tracker.md#recorded-agent-statistics).

`delivery_trends` adds one shared set of period totals and daily buckets for
completed tasks/tickets, first-review acceptance, changes requested, stop
occurrences and summed task work time. Both scopes reuse this read; six widgets
must not query or aggregate receipt history individually. Existing token and
recorded agent/model grouping fields keep their meanings. Compact/raw retain
the same trend facts and coverage; ack remains unavailable.

```json
{"arguments": {"project_id": 1, "response_mode": "compact"}}
```

Use that payload at `/api/v1/operations/tracker.agentStats/call`. Local admin
may call `/api/v1/operations/tracker.agentStatsAll/call` with
`{"arguments":{"is_active":null}}` for all projects.

## Native harness account controls

The authenticated local console calls these operations at
`POST /api/v1/operations/{operation_name}/call`. They use the user-local host
registry. Requests only accept the declared identifiers and revision.

| Operation | Arguments | Result |
| --- | --- | --- |
| `hostMcp.listAccounts` | None | `hosts` keyed by switch-capable host, including hosts with zero accounts. Each row contains `accounts`, `desired`, `pending_enrollment` and `pending_cleanups`. |
| `hostMcp.adoptAccount` | `host_key` | Enrolled account after checking the existing native default sign-in in place. |
| `hostMcp.addAccount` | `host_key`, new UUID `enrollment_ref` | Provisional native sign-in receipt. Retry with the same reference to recover the original receipt without starting sign-in again. |
| `hostMcp.completeAccountEnrollment` | `host_key`, `enrollment_ref` | Current enrollment outcome. Poll while pending. Native verification can report missing email, expired credentials or a competing credential. |
| `hostMcp.cancelAccountEnrollment` | `host_key`, `enrollment_ref` | Final enrollment outcome. If `cleanup_pending` is true, retirement is unconfirmed and the same cancel call may retry it. |
| `hostMcp.selectAccount` | `host_key`, `account_ref`, `expected_revision` | Verified desired account and revision. Codex, Claude Code and Grok switches resume affected managed delivery workers in their retained conversations. On a stale revision, reload inventory before submitting a new selection. |
| `hostMcp.refreshAccount` | `host_key`, `account_ref` | Enrolled account with current identity status and the last verified display email. |

The inventory is the read response body. Writes return the corresponding
receipt under `data`. Enrollment expires after 15 minutes. A matching native
principal and workspace keeps its account reference. A healthy saved profile
is reused, and a newly verified sign-in can replace an unusable saved profile.
A successful replacement through enrollment returns `enrolled`. A failed
recheck of an adopted native default updates the saved verification status.
Inventory and refresh report that state alongside the last verified display
email. Equal emails can have distinct account references.

Only an explicit select call changes the desired account reference. Replacing
the selected account's underlying profile advances its revision so consumers
can detect stale work. Revision 0 with a null reference uses the native default.
Each native client determines the account used by its session. Native profile
references, principals and workspace identifiers stay inside host management.

`pending_cleanups` lists finalized enrollments whose native cleanup is still
unconfirmed. After reopening the console, retry cancellation with the listed
`enrollment_ref`. Native adapters retry by profile reference even when a prior
attempt already removed the provisional directory.

These operations use raw responses and the exact-path local-admin console
policy. See [native harness account custody](security.md#native-harness-account-custody).

Host definitions opt in through `HostMcpDefinition.account_adapter`. Native
launch and quota consumers can get a single internal snapshot through
`service.host_accounts(settings.data_dir).resolve(host_key)`. The snapshot
includes the desired revision, the native profile context and the expected
principal/workspace tuple. Recheck native identity at the affected consumer
and use the revision to detect a superseded request.

Codex selection persistently replaces the normal file-backed
`CODEX_HOME/auth.json`. Adoption saves the original login in the existing private
host profile store before another account can overwrite it. Enrollment opens
first-party browser sign-in in an isolated home and verifies the native principal
and workspace. Selecting another account saves the outgoing native credentials,
including native refreshes, then atomically installs the selected saved login.
The registry changes its selected reference only after activation succeeds;
a failed file replacement keeps the prior usable native login.

Normal Codex CLI and app launches retain the same `CODEX_HOME`, original
conversations, settings and native capabilities. There is no per-child credential
lease or automatic revert. Existing clients cache their login. Account selection
for Codex, Claude Code and Grok pauses affected StackOS automated delivery workers
across projects, confirms their native process has stopped, switches credentials,
then resumes the same conversation with `proceed`. Delivery/review phase, settings and repair allowance
remain unchanged, and interrupted invocation usage stays recorded. Work already
stopped or cancelled is not restarted. New managed launches for the selected host
wait for the switch to finish. A worker without a resumable session, including an
legacy request-owned setup invocation, prevents switching before credentials change.

Personal terminals and other independently started processes must be
restarted manually, then resumed in their original conversation. The console
shows switching progress immediately and confirms selection before refreshing
quotas; its success message explains the restart requirement. Avoid concurrent
native login changes during selection.
If the actual native principal differs from the outgoing saved account, selection
fails instead of saving credentials under the wrong account.

Codex adoption and switching require file mode. Keychain, automatic store
selection, ephemeral storage and competing credential overrides fail closed.

Claude Code enrollment starts first-party subscription sign-in in a private
profile with both `CLAUDE_CONFIG_DIR` and `CLAUDE_SECURESTORAGE_CONFIG_DIR` set.
Adoption captures the original subscription in the existing host profile store.
Selection saves outgoing native refreshes, then replaces only `claudeAiOauth`
in the native secure-store object and `oauthAccount` in native `.claude.json`.
Other credential entries, MCP configuration, workspace trust, settings and
transcripts remain in their original native locations. A metadata write failure
restores the prior credential object and reports failure.

On macOS, the native Keychain service is primary; a missing item may use the
native `.credentials.json` fallback. A locked or unreadable primary item stops
selection. Writes use the native `security` command's stdin protocol and reject
oversized payloads without passing secrets in arguments. Ordinary Claude runs
and resume keep native defaults and original session IDs. Existing clients may
cache credentials; managed delivery workers resume through the shared continuation
above, while external terminals require a manual restart.

## Browser Operations

For online clients, the same operations retain metadata in the server's existing
browser repository and execute native lifecycle work through the connected
computer's CLI/MCP adapter. The server reports native state as unavailable and
returns no native context; it never launches a browser. The local adapter checks
the selected login, exact computer/project/folder and runtime health before using
the existing browser runtime. Server authorization and run grants still precede
local lifecycle work. See [online browser routing](browser-automation.md#launcher-contract).

The browser operation surface owns runtime status, profile create/list, and
session start/list/status/stop. Session discovery reconciles owned gstack state
and OS process identity after daemon restart. Every session row includes a ready
`cli_argv` prefix: `["stackos.browser","--session",session_ref]`.

Agents append upstream gstack arguments and run that command in their terminal.
The standalone launcher resolves the exact session once through the existing
authenticated status operation, then replaces itself with the native process.
Native commands, output, terminal streams and exit status do not pass through
StackOS operation dispatch. There is no browser command relay or browser-method
allowlist. Upstream owns parsing and browser behavior.

Selected start/status returns `native_cli={executable,cwd,env}` independently of
observed health when its runtime and profile binding are valid. Bulk lists omit
that context. A live sibling profile owner prevents a competing handoff.

The same OperationSpec supplies lifecycle MCP, generic REST, generic CLI, and
Operations UI discovery. Selected paths are local capabilities; state contents
and tokens are not exposed. Native output may contain requested page data.
Agents preserve useful captures explicitly through generic artifacts. See
[browser sessions](browser-automation.md) for usage and profile continuity.

## Canonical ownership operations

The `run.owner*` contracts use the existing tracker identity and run/event owner.
Resolve external aliases first; mutations accept only one canonical `task:<id>`
or `ticket:<id>`. Ambiguous aliases reject. These operations store integrity and
audit; they launch no process, signal no cancellation and grant no publication.

| Operation | Contract |
| --- | --- |
| `run.ownerResolve` | Read one native identity or existing opaque external alias |
| `run.ownerGet` | Read stored fence/continuity plus current-source consistency and capture time |
| `run.ownerAcquire` | Reserve once; duplicate assignment returns `acquired=false` or conflict |
| `run.ownerCheckpoint` | CAS a reported checkpoint reference, preserving scope and counters |
| `run.ownerStop` | Add a stop without clearing ownership, counts or original scope |
| `run.ownerReassign` | Explicit replacement, or explicit `new_owner_ref=null` revocation, after backend-certified quiescence/effect disposition |
| `run.ownerConsume` | Increase counts or reconcile authoritative floors; reject negative/reset/limit/ref changes |
| `run.ownerRecordAttempt` | Annotate an existing bound child run's reported observation or terminal attempt reason |

Writes require the current controller run, exactly one running step, an explicit
tool grant with exact canonical `targets`, and the item's persisted plan/step
binding. They recheck the shared grant owner and current canonical continuity
inside the SQLite write transaction before audit/commit. System/local-admin
traffic, arbitrary owner strings and evidence references cannot bypass this
boundary. Controller tokens stay controller-held; `owner_ref` names an assignment.
See [ownership grants](./run-plans.md#canonical-ownership-grants).

Before first admission the canonical item's existing `metadata_json` holds
`ownership_continuity`: scope digest, original budget/stop/history refs, known
stopped/denied flags, cumulative attempts/failures and positive attempt/failure
limits. Existing effect/deadline refs are retained. These are corroborated inputs
from the work-item owner, not a caller's replacement policy. Unknown or changed
authority blocks the affected write. Consumption preserves immutable refs and
limits, takes the maximum stored/current counts and adds only nonnegative deltas.
Late failures remain recorded after a stop; they cannot admit another attempt.
Exhausted failures persist a stop. No operation clears stops or extends a budget.
`authority_valid` in inspection describes continuity consistency, not permission.

Observation annotations and terminal reasons stay on existing child `Run`
metadata/status. The child must match controller parent, project, canonical item,
owner, epoch and scope. Reported states are running, waiting, suspected stall,
blocked, scope drift and review ready. Resuming blocked/drift observations needs
recorded clearance, current unstopped authority, reconciled canonical counters,
and remaining failure budget. Zero-delta `ownerConsume` reconciles externally
advanced counters; exhausted failures persist a stop and cannot be cleared by an
observation. Resuming an already charged attempt does not admit another attempt.
Verified hang, host suspension and accepted completion are backend-only; their
trusted producers remain separate.
Terminal reasons map to existing run status: succeeded→success, failed→failed,
cancelled/abandoned/superseded→aborted. Supersession needs current backend evidence.
Terminal attempts cannot reactivate; succeeded never completes a tracker ticket.

Compact/raw ownership receipts retain the complete canonical fence, checkpoint,
continuity refs, request digest, actual controller `run_id` and replay status;
ack is not supported. Idempotency matches exact operation/input/run/step before
replay and revalidates current authority/fence. Acquisition replay always returns
`acquired=false`. Attempt receipts also revalidate the child's identity, ownership
binding/annotations and terminal lifecycle; heartbeat or unrelated metadata updates
alone do not invalidate replay. A changed request or stale result conflicts. If
commit precedes response/cache interruption, inspect `run.ownerGet`; an in-flight
key is not permission to retry or dispatch. No quiescence certificate setter
exists on MCP, REST or CLI. MYB-95 supplies genuine host/effect evidence; storage fixtures are
not that proof.

The generic operation catalog/REST/CLI and UI operation inspector consume these
same schemas. This source contract does not upgrade a loaded daemon, alter an
existing grant snapshot or activate the stopped evaluation.

## Generic REST Calls

The generic REST adapter is command-shaped and meant for scripts, CLI, CI, and
external automation:

```http
POST /api/v1/operations/{operation_name}/call
```

Payload:

```json
{
  "arguments": {
    "project_id": 1,
    "action_ref": "utils.sitemap.fetch",
    "input_json": {
      "urls": ["https://example.com/sitemap.xml"]
    },
    "run_token": "..."
  }
}
```

UI-friendly resource routes still exist separately, for example
`GET /api/v1/projects/{project_id}/action-calls`. The operation endpoint is the
generic callable surface; resource routes remain the ergonomic query/read model
for the UI.

## Finance local console operations

Finance registers these exact REST/CLI operations over one project-owned
`local-json-v1` master. They are disabled on MCP; agents perform finance method
through existing host/workflow contracts, not domain MCP primitives.

| Operation | Behavior | Authority/response |
| --- | --- | --- |
| `finance.status` | Prepare the enabled project's default Finance store and inspect readiness/snapshot. Never replace missing or corrupt existing records. No caller-selected root. | `local-admin-console-write`; handler-owned retry validation; compact/raw safe receipt. |
| `finance.query` / `finance.record.get` | Bounded filters, source coverage, current facts/history and undated record refs. | Current project/root/read mode; compact/raw. |
| `finance.record.preview` | Validate a sourced draft against its source snapshot without writing. | Current correction mode; compact/raw. |
| `finance.record.commit` | Guarded additive correction/create/proposal, exact preview and revision/hash, actual readback. | `local-admin-console-write`; handler-owned retry and under-lock authority recheck. |
| `finance.evidence.read` / `finance.report.render` | Registered private original or derived period/independently reviewed analysis result. | Raw-only bounded content with exact hash/state; no ack/compact. |

The generic endpoint is `/api/v1/operations/{name}/call`; CLI `ops call {name}`
uses the same authenticated REST owner. Trusted local context requires the exact
registered path, browser-safe surface, MCP-disabled spec and matching read/write
policy. Headers, caller actor/admin fields and run tokens cannot widen it.
`OperationSpec.idempotency_policy` defaults to `dispatcher`; finance writes opt
into `handler` so every retry revalidates current root authority and original
external intent. Descriptions expose that metadata. Existing dispatcher raw/ack
replay remains unchanged for its default consumers.

Private evidence/report responses are bare `FinanceContentOut` in raw mode;
other default compact console calls return the normal `data` envelope.
Exact minor-unit strings and source revision/hash survive shaping. Registry models
generate UI contracts through `scripts/write-openapi.py`, without a second schema.
Browser `localAgentChat.createMessage` is a separate bounded inbound human intent:
safe refs/query/snapshot only, no actor/outbound spoofing, provider work or model
execution. Clear private search text and apply filters before analysis; read
[the actual host and result protocol](../plugins/finance/references/workflow-handoffs.md#management-review-request-and-result).

## Automated delivery results and manual instruction adoption

`tracker.startDelivery` accepts operator-authorized `replace_review` through the
shared typed MCP, REST and CLI contract. See
[review-only replacement](task-tracker.md#explicit-review-only-replacement) for
eligibility, pinned identities, source selection, retained evidence and recovery.
Raw execution overrides reject; browser-console permissions remain unchanged.
Compact and raw receipts preserve `review_replacement` identity, candidate/snapshot
digest and inspection refs; errors retain structured repair context.

The generic `stackos.task_delivery` module owns task execution.
Its host calls register actual agent/model/start lifetime at
`stackos.host_mcp.service.run_agent`. Tracker task-index, ticket and detail reads
expose the same nullable `active_execution` projection; persisted status never
substitutes for an active call. Registration/cleanup publish
`tracker.execution.changed` through the existing tracker SSE feed without changing
ticket status. The same host-service registry owns live usage observation for
active Codex calls: one five-second collector reads appended native counters
from the exact rollout source, with a fixed prior-call baseline on resume.
No active calls means no collector timer or native reads. Other hosts retain
their existing final collection; unproven live counters remain unavailable.

The existing context timeline SSE transport also sends replaceable
`tracker-usage` frames, without durable event IDs or per-sample database writes.
Project streams retain project scope; `/api/v1/context/timeline/stream` supplies
the same local-admin transport for Overview's global scope. Reconnect sends the
current transient state; durable tracker events retain their existing cursor.
UI subscribers share the transport and refresh owner; usage-only Work updates
patch metrics and execution in place without rebuilding the page or graph.
Native exit immediately closes readers and removes live identity. Bounded measured
samples and counters remain readable through the synchronous usage-to-receipt
handoff, which persists them with the actual execution identity in the existing
invocation receipt. Subsequent registry cleanup releases that in-memory snapshot,
including when notification fails; durable receipt history remains available for
task/ticket projections. Native result and candidate acceptance are still validated
afterwards. Receipt-statistics changes publish `tracker.usage.changed` atomically
with that write, including evidence-only
updates. The stream includes affected task/ticket projections after persistence;
the UI does not treat that usage event as another page-reload request. There is
no per-sample event write, separate history collector or new retry policy.
Statistics readers copy provisional counters before loading fresh receipt
evidence, so a request overlapping the handoff cannot lose both contributions.
Recorded usage wins over an older request copy; the existing SSE refresh shares
that copy across its task/ticket metrics. No database read holds the registry
lock or extends an invocation's reader/sample lifetime.
`tracker.configureDelivery` is the narrow local-admin REST-only setup write for
`TaskTracker.config_json.delivery`; it replaces that object while preserving
unrelated tracker configuration. Send `arguments` with `project_id` and
`delivery` to `POST /api/v1/operations/tracker.configureDelivery/call`. The raw
response includes saved configuration and tracker revision; MCP, CLI, workflow
grants, compact and ack are unsupported for this setup write. Read current
defaults through `tracker.get` with `response_mode=raw`. Changes affect explicit future handoffs. Queued/running executions keep their
snapshot; an explicit rehandoff after complete/stopped execution resolves current
defaults and overrides. Historical task/run snapshots and ticket evidence retain
their settings. Migration `0035_generic_task_delivery` transfers the retired
engineering plugin defaults once and preserves existing generic values.

The existing Agents console can instead send `role_patch: {role, settings}` for
`delivery_agent`, `review_agent` or `main_agent`, with exact bound `project_dir`
and current `expected_revision`. This typed patch merges only supplied fields
at that same defaults owner: omission preserves, null clears to inheritance.
The compare-and-swap rejects concurrent stale saves; foreign project folders
reject before mutation. `preview: true` returns effective settings through the
existing specialist or main launch resolver without a write. Successful saves
return tracker configuration/revision plus `role_settings` with effective values
and scope. Codex source values retain precedence; non-Codex roles use inline
contracts. Main contracts remain setup/automation-owned. These forms retain the
same browser-safe, authenticated local-admin REST-only boundary and never
dispatch work or rewrite active/resumed snapshots.

`tracker.startDelivery` freezes project defaults, task overrides and optional
`agent_assignments` into one execution snapshot. The map identifies canonical
ticket keys and selected arbitrary role keys in `agents`; frozen step
extension `agent_assignment` supplies mirror defaults and explicit entries win.
Otherwise admission uses the unique applicable selected declared workflow role;
ambiguity rejects. Ordinary tickets retain a separate independent reviewer.

New handoffs also persist bounded
[ordinary restart/missed-launch continuation intent](task-tracker.md#ordinary-restart-and-missed-launch-continuation)
in existing metadata and phase receipts. Startup and missed queued launches reuse
the existing queue/checkpoint owner, with exact retained sessions and process
retirement checks. Historical admissions, stops/failures, denial and unknown or
surviving owners retain their recovery fences. This does not retry completed
scheduled occurrences or replace managed-upgrade finish authority.

Each role can declare `execution_requirements: {"filesystem_write": true}`.
Requirements are typed assignment needs, separate from native authority. They
survive saved/default/task round trips; false or omitted permits inspection-only
review. Admission and launch use the host's effective sandbox resolution, including
source restrictions and actual retained phase settings. An incompatible requirement
rejects before scheduling delivery with structured `execution_requirement_mismatch`
repair context containing role, phase, required/effective permissions and selected
versus retained source identities. Native settings, session history and normal
approval behavior remain unchanged; no command inference or automatic escalation follows.

Fresh Codex admission reads exact project source refs through the bounded source
owner and freezes instructions/digest/supported native options. Source model and
effort override execution fallbacks; no source field grants permissions, provider
or MCP changes. Legacy inline Codex configuration needs explicit adoption.
`tracker.configureDelivery` also accepts `project_dir`, `adopt_sources` with exact
refs/digests, and `preview` in place of `delivery`. Preview compares without writes;
explicit adoption preserves unrelated settings and verifies saved readback. See
[source adoption](task-tracker.md#automated-task-delivery).
For workflows the running `step_id` is entry context. The existing job traverses
all dependency-ready work, activates nested groups without launching them,
claims mirrors through the run-plan owner, runs accepted descendants and then
launches each remaining frozen mirror contract once. Zero-child mirrors launch
directly. Only `RunPlanRepository` records terminal mirror/plan/run state.
Bound controller context stays daemon-held; worker packets carry no raw token.

Every native result has `status` (success/repair/blocked/failed), `summary`,
`outputs` (a JSON-encoded object string), and `followups` (text objects).
This closed envelope transports arbitrary output contracts through native
harness restrictions. Malformed JSON/nonobjects reject; mirror outputs pass to
the canonical frozen-schema validation. Agents explicitly map domain conclusions
to status. Runtime has no domain verdict table. Only success accepts a gate;
unfinished non-ready work stops. After native validation, a valid success or
repair result following supported persisted correction/reopen returns to
dependency scanning without accepting the superseded occurrence. Mirror repair
without persisted progress stops; mirrors do not enter a duplicate ordinary-ticket
review loop. See the
[agent-owned correction method](task-tracker.md#agent-owned-automatic-corrections).

Ordinary tickets retain independent review and up to three scoped repairs in the same
delivery/review sessions, with persisted rejection, attempt receipts and consumed
repair allowance surviving requeue. Reviewers execute planned specs and relevant
integration checks; failures report repair, unavailable required checks blocked.
Unrelated notes stay in nonactionable followups and never enter repair prompts,
dependencies or automatic work.

Git is optional. Supplied `outputs.commit_sha` evidence retains clean-current-HEAD,
ancestry and exact review identity checks. Observation may cite unchanged HEAD;
project guidance requires separate commits for source changes/repairs.
Non-Git tasks invoke no Git commands; Codex uses `--skip-git-repo-check`.
Native/access/model/resume/schema failure, candidate mismatch or rejection after the third repair
stops the same task, with no fresh-session, host or model fallback.
See [task delivery](task-tracker.md#automated-task-delivery) for session/followup
storage and native execution modes.

Source/local guidance changes require manual adoption per project through the
local-admin `tracker.configureDelivery` configuration owner. Ordinary
MCP grants do not authorize that setup. Read the current configuration, copy its
complete `delivery` object, replace only selected role `instructions`, and save
that object; a supplied `delivery` replaces the key. Preserve names, task/role
hosts, models, provider/profile and unrelated fields, then read back exact equality
outside those instruction strings and exact content equality inside them. Keep
the approved source refs/revisions and candidate with the receipt, and verify a
subsequent new handoff prompt. No source edit refreshes saved prompts or active/
historical snapshots, and no synchronizer, watcher or cross-project rollout exists.

For this repository the manual mapping after independent candidate approval is:

| Existing saved field | Accepted local source |
| --- | --- |
| `delivery.delivery_agent.instructions` | `.codex/agents/sdlc-delivery.toml` parsed `developer_instructions` |
| `delivery.review_agent.instructions` | `.codex/agents/sdlc-delivery-reviewer.toml` parsed `developer_instructions` |

Current-project adoption is a separately authorized setup action after independent
source/local review and old-job completion. A reviewed one-time operational
helper may perform it when explicitly assigned, outside the daemon lifetime;
this is not a shipped synchronizer. Compare instruction strings with canonical
whitespace normalization and every other setting exactly. Preserve completed
tasks and frozen run/templates; an explicit rehandoff after completed/stopped
execution may refresh only current task execution settings while retaining
ticket receipts, sessions and consumed repair allowance. Never refresh a
queued/running job silently.
Other projects select and adapt their own available sources individually; host
files are optional and no other project is changed by this handoff.

## Human task content

`tracker.updateTaskContent` is an exact-path browser-safe REST/CLI operation
with MCP disabled and `local-admin-console-write` authority. Its strict input
accepts an existing project/task, `expected_updated_at`, a stable
`idempotency_key`, and `description` and/or `additional_input`. It cannot write
task title, goal, lifecycle, ownership, dependencies, run bindings or arbitrary
JSON. The handler derives the operator actor and atomically persists the
content, tracker revision and intent-bound retry receipt. Stale snapshots and
reused keys with changed intent return structured conflicts without mutation.
The raw-only response keeps the saved task, revision, input receipt and replay
status; ACK and compact modes are not available. See [task content](task-tracker.md#ui)
for storage and UI behavior. Broader tracker operations remain agent/controller
surfaces and do not become browser-safe through this exception.

`tracker.updateTaskPriority` and `tracker.reorderTask` expose bounded task Board
edits through MCP, REST (including the local browser) and CLI, with one repository
transaction owner. Strict inputs require the task's status/timestamp and an
intent-bound retry key; they cannot supply lifecycle or arbitrary patches.
See [Board priority and order writes](task-tracker.md#board-priority-and-order-writes)
for neighbor placement, ordering, conflicts and existing-stream invalidation.
Each accepted reorder commits one tracker revision and one
`tracker.tasks.changed` timeline/SSE event with structured affected task
membership and that revision. Priority edits retain `tracker.task.changed`.
The raw Board receipt's `affected_task_keys` and `rev` identify the same accepted
mutation; retries and no-ops do not emit another event.

## CLI Calls

The CLI uses the same REST operation adapter:

```bash
stackos ops call action.describe \
  --input describe-action.json
```

In a source checkout, the CLI entrypoint is available at `.venv/bin/stackos`
after dependency sync. If the shell has not activated the repo venv, use
`.venv/bin/stackos ...`, `uv run stackos ...`, or export
`PATH="$PWD/.venv/bin:$PATH"` before using the shorter `stackos ...` form.

For common cross-cutting fields, the CLI can merge flags into the input JSON:

```bash
stackos ops call action.execute \
  --project 1 \
  --run-token "$RUN_TOKEN" \
  --idempotency-key sitemap-1 \
  --response-mode raw \
  --input action-input.json
```

`--input -` reads the operation arguments from stdin.

Common operations also have aliases that still call the generic operation
endpoint:

```bash
stackos actions list --project 1 --plugin trackbooth --query "top offers" --executable
stackos actions describe utils.sitemap.fetch --project 1
stackos actions validate utils.sitemap.fetch \
  --project 1 \
  --context-ref ctx_reporting \
  --input action-input.json
stackos actions execute utils.sitemap.fetch \
  --project 1 \
  --run-token "$RUN_TOKEN" \
  --context-ref ctx_reporting \
  --input action-input.json
stackos actions run communications.telegram.message.send \
  --project 1 \
  --credential-ref cred_123 \
  --confirm-direct \
  --intent-summary "User asked to send one status message." \
  --intent-id telegram-send-status-1 \
  --input telegram-message.json

stackos run-plans validate --project 1 --template-key core.project-memory-review
stackos run-plans create --project 1 --input run-plan.json
stackos run-plans start 42 --project 1
stackos run-plans claim-step 42 --step-id fetch-sitemap --run-token "$RUN_TOKEN"
stackos run-plans record-step 42 \
  --step-id fetch-sitemap \
  --status success \
  --result step-result.json \
  --run-token "$RUN_TOKEN"

stackos agent-requests list --project 1 --claimable
stackos agent-requests prepare-run-plan 42 \
  --project 1 \
  --claimed-by codex \
  --input run-plan.json \
  --idempotency-key prepare-agent-request-42
stackos agent-requests complete 42 \
  --project 1 \
  --claim-token "$CLAIM_TOKEN" \
  --status resolved

stackos tracker next --project 1
stackos tracker brief workflow-42-review --project 1
stackos tracker pick --project 1 --assignee codex
stackos tracker patch --project 1 --input tracker-patch.json
```

Use `tracker.startDelivery` for a task-level background handoff of an independent
task whose tickets and dependencies are prepared. The `handoff.json` input file
contains the task handoff (task key, project directory, and optional delivery/review
role overrides). Project defaults use generic `TaskTracker.config_json.delivery`
through local-admin `tracker.configureDelivery`; omitted role fields inherit those
defaults and resolved roles must be complete. Task overrides merge by role once,
preserving unrelated metadata. No ticket settings or permission expansion is needed.
See [Automated Task Delivery](task-tracker.md#automated-task-delivery)
for its structure. The call returns immediately while the daemon runs ready tickets
sequentially through delivery and review. Inspect progress with `tracker.get`
using `response_mode="raw"`.

```bash
stackos ops call tracker.startDelivery --project 1 --input handoff.json --response-mode raw
```

Provider execution context stays outside endpoint payloads. Pass it directly
with `--provider-context provider-context.json`, or store it once on an
execution context and pass `--context-ref` on subsequent calls.

For Trackbooth on-behalf execution, `provider-context.json` can contain:

```json
{
  "acting_as_account": "acct_123"
}
```

StackOS validates that provider context separately from `--input`; the
Trackbooth connector maps `acting_as_account` to the `X-Acting-As-Account`
header. It is not sent as REST body/query/path data.

Examples:

```bash
stackos actions run trackbooth.reporting.aggregate \
  --project 1 \
  --credential-ref cred_trackbooth \
  --provider-context provider-context.json \
  --input reporting-aggregate.json \
  --dry-run

stackos actions run trackbooth.offers.update \
  --project 1 \
  --context-ref ctx_revtrix_reporting \
  --input offer-update.json \
  --confirm-direct \
  --intent-summary "Operator approved updating one offer." \
  --idempotency-key trackbooth-offer-update-123
```

Agent tracker reads such as `tracker.status`, `tracker.next`,
`tracker.blockers`, `tracker.brief`, `tracker.why`, `tracker.execute`,
`tracker.verify`, `tracker.history`, `tracker.changed`, and `tracker.search`
are compact by default on the agent-facing bridge and on tracker-specific
commands. For direct REST, direct daemon MCP, or generic `ops call`, pass
`response_mode: "compact"` when the caller wants the compact shape and
`response_mode: "raw"` or `"standard"` when the full diagnostic row payload is
needed.

## Registered Core Operations

The live operation registry is the source of truth. Use `operation.list` and
`operation.describe` for the current inventory, schemas, response policy, grant
policy, examples, and enabled surfaces. The static list below is a convenience
index for common core operations, not a replacement for registry discovery.

- `action.describe`
- `action.list`
- `action.validate`
- `action.run`
- `action.execute`
- `actionCall.get`
- `artifact.create`
- `artifact.read`
- `artifact.download`
- `actionCall.download`
- `artifact.update`
- `artifact.archive`
- `artifact.supersede`
- `agentPreset.list`
- `agentPreset.describe`
- `agentPreset.resolveForWorkflow`
- `skillPreset.list`
- `skillPreset.describe`
- `skillPreset.resolveForWorkflow`
- `agentRequest.list`
- `agentRequest.get`
- `agentRequest.create`
- `agentRequest.claim`
- `agentRequest.prepareRunPlan`
- `agentRequest.release`
- `agentRequest.linkRunPlan`
- `agentRequest.complete`
- `agentRequest.ignore`
- `browser.runtime.status`
- `browser.profile.create`
- `browser.profile.list`
- `browser.session.start`
- `browser.session.stop`
- `browser.session.list`
- `browser.session.status`
- `communication.reply`
- `communication.send`
- `communicationProfile.accountUsage`
- `communicationProfile.list`
- `communicationProfile.get`
- `communicationProfile.upsert`
- `communicationSurface.list`
- `communicationSurface.upsert`
- `communicationContact.list`
- `communicationContact.upsert`
- `communicationMembership.list`
- `communicationMembership.upsert`
- `communicationTarget.list`
- `communicationTarget.resolve`
- `communicationTarget.upsert`
- `communicationRoute.list`
- `communicationRoute.upsert`
- `communicationContext.query`
- `context.query`
- `context.timeline`
- `decision.query`
- `decision.record`
- `executionContext.discover`
- `executionContext.resolve`
- `executionContext.create`
- `executionContext.artifact.list`
- `executionContext.artifact.read`
- `experiment.query`
- `learning.create`
- `learning.query`
- `ingressEndpoint.configure`
- `ingressEndpoint.confirmManualUpdate`
- `ingressEndpoint.refresh`
- `ingressEndpoint.routes`
- `ingressEndpoint.sync`
- `ingressEndpoint.status`
- `localAgentChat.createMessage`
- `toolProfile.resolve`
- `readiness.check`
- `tracker.status`
- `tracker.get`
- `tracker.next`
- `tracker.blockers`
- `tracker.brief`
- `tracker.why`
- `tracker.execute`
- `tracker.startDelivery`
- `tracker.verify`
- `tracker.history`
- `tracker.changed`
- `tracker.search`
- `tracker.createTask`
- `tracker.createTicket`
- `tracker.updateTask`
- `tracker.updateTaskContent`
- `tracker.updateTaskPriority`
- `tracker.reorderTask`
- `tracker.updateTicket`
- `tracker.patch`
- `tracker.rejectTask`
- `tracker.reopen`
- `tracker.pick`
- `tracker.release`
- `tracker.linkRunPlan`
- `runPlan.validate`
- `runPlan.create`
- `runPlan.start`
- `runPlan.get`
- `runPlan.getStep`
- `runPlan.checkConsistency`
- `runPlan.list`
- `runPlan.update`
- `runPlan.recover`
- `runPlan.reopen`
- `runPlan.abort`
- `runPlan.claimStep`
- `runPlan.recordStep`
- `resource.get`
- `resource.query`
- `resource.upsert`
- `schema.get`
- `workflowExtension.list`
- `workflowExtension.get`
- `workflowExtension.delete`
- `workflowExtension.validate`
- `workflowExtension.upsert`
- `workflowTemplate.authoringGuide`
- `workflowTemplate.list`
- `workflowTemplate.describe`
- `workflowTemplate.validate`
- `workflowTemplate.save`
- `workflowTemplate.fork`

Tracker list workflows reuse existing tracker operations. Use
`tracker.createTicket` with `tickets_json` and `dry_run=true` for draft review,
call the same operation without dry-run to create the list, use `tracker.get`
filters for review, and use `tracker.updateTicket` with `updates_json` for
atomic per-ticket patches. Do not add separate list-specific tracker endpoints.
Terminal child-ticket updates can roll up a parent task's terminal status, but
ticket evidence is not copied into the task; compact tracker mutation responses
include `task_rollup` and evidence-presence flags when this matters.
Use `tracker.rejectTask` for operator-level rejection or abort: it accepts a
task key or run-plan id, marks the parent task aborted/rejected, and cascades all
child tickets to aborted with rejection evidence. For resumable parked work, use
normal tracker status updates with `deferred` instead of rejection. For
workflow-backed tasks, rejection only applies to draft/started run plans and
routes through `runPlan.abort` first. Completed or failed workflow run plans
remain canonical; create follow-up tracker work instead of overriding their
terminal lifecycle.
`tracker.linkRunPlan` is a provenance link only and does not transfer lifecycle
ownership from tracker to the run plan.

Agent preset setup reuses the same operation infrastructure. Use
`agentPreset.list` to discover generic role presets, `agentPreset.describe` to
read one role and its project-adaptation contract, and
`agentPreset.resolveForWorkflow` to resolve a workflow template into
required/recommended agents, installed `skill_requirements`, and resolved
main-agent `skill_preset_requirements`. Use `skillPreset.*` when the agent
needs skill-preset-only discovery or diagnostics.

Workflow templates are inert presets/contracts. They do not act by themselves.
The main agent should resolve preset and skill requirements, adapt generic roles
to the project, create a concrete run plan with `runPlan.create`, and work
through tracker tasks/tickets with explicit dependencies and evidence.

Workflow extensions are the project-configuration layer for templates. Use
`workflowExtension.validate` and `workflowExtension.upsert` when a base workflow
should stay reusable but a project needs stable input defaults, communication
route refs, channel/target context, guardrails, extra step guidance, or atomic
workflow-field overrides. `template_overrides_json` replaces the provided
top-level workflow keys, then StackOS validates the effective template before
saving or creating a run. Agents should put project-specific agent, installed
skill, or skill preset changes in the existing `agent_requirements` /
`skill_requirements` / `skill_preset_requirements` workflow
fields, not in a new context mechanism. `workflowTemplate.describe` returns the
attached `project_extension`, and `runPlan.create` applies enabled extension
defaults before per-run inputs. Use `workflowTemplate.fork` only when a new
workflow identity or separately reusable method is needed.
When supplied, `source` is a template-origin filter (`plugin`, `project`,
`user`, or `repo`), not audit or workflow-run provenance.

Use `readiness.check` before broad setup scans when the agent already knows a
workflow key or action ref. It answers the scoped question: is this workflow or
action executable now, and which exact credentials, budgets, connectors, or
setup items are missing? Read `structurally_ready`, `context_status`,
`required_providers_ready`, and `execution_ready` separately; structural validity
does not mean execution is ready. Unresolved required action contracts and
missing required provider setup block execution readiness and include repair
context. Optional
action-contract gaps are reported as `required_for:
optional_action_execution`; resolve them only when the operator selected that
branch. Missing credential rows include provider setup guidance from
`config.setup`, including the local connection URL and safe vendor registration,
API-key, billing, docs, confidence, and fallback fields when known.

## Execution Context Refs

Use execution contexts when several provider actions share the same project,
credential, provider scope, output policy, request budget, or artifact
namespace. The context is a StackOS object, not plugin business logic.

Agent flow:

1. Create or discover a context with `executionContext.create`,
   `executionContext.list`, or `executionContext.discover`. Link it to the
   relevant task, ticket, run plan, or run so resumed agents can find it.
2. Inspect compatibility with `executionContext.resolve` before a new action
   family or provider scope is used.
3. Pass `context_ref` to `action.validate`, `action.run`, or
   `action.execute`. StackOS applies the daemon-held `credential_ref`, validates
   and merges typed `provider_context_json`, records output/request-budget
   metadata, and keeps endpoint `input_json` separate.
4. External provider actions are file-backed by default for MCP/REST calls and
   raw inline by default for CLI calls. Use `output_policy_json.mode` for a
   deliberate override: `inline`, `file_if_large`, `always_file`, or
   `transient`. File-backed output can take a `semantic_name` and absolute
   directory `path`; StackOS generates the filename there. For a foreground
   provider read that should not retain its result, use `transient` with
   `response_mode=raw` and no replay key. The immediate response contains a
   bounded result; the audit retains a content-free receipt and `actionCall.get`
   cannot replay the result. For file-backed responses, inspect the returned
   file path before rerunning paid/provider calls. If the response envelope
   contract is needed, call `schema.get` with the returned `schema_ref`. Use
   `artifact.read` only for intentional StackOS artifacts.
5. Treat `request_budget_json` as the coordination contract for agents using
   the context. Supported fields are `max_parallel`, `max_calls`,
   `max_calls_per_run`, `window_seconds`, and `notes`. Current action-call rows
   are terminal audit records, so StackOS records these limits and exposes them
   in action metadata/UI; agents should obey them until a running-call lifecycle
   exists for hard concurrency enforcement.

Agent-facing discovery surfaces reinforce this path. `action.describe` returns
`execution_context` guidance for the selected action, and
`executionContext.discover` returns a scoped `executionContext.create` next call
when no reusable context exists yet.

Risk labels come from the action or generated catalog contract, not HTTP method
alone. Catalog-marked or semantically read-only reporting/list/export POST
actions can be read actions, while idempotent mutations remain write actions
and still require the normal confirmation/idempotency protections.

## Direct Actions Vs Workflows

`action.run` is for one explicit action when no workflow state is needed:

1. A workspace bridge supplies its bound project. Global bridge sessions and
   scripts pass an explicit `project_id`.
2. The caller must pass an explicit action ref or plugin/action pair.
3. Non-read actions require `confirm_direct=true` and `intent_summary`.
   Callers may pass `intent_id` or `idempotency_key` for stable retries; when
   absent, StackOS derives a safe key before connector execution.
4. Credentials are resolved inside the daemon; callers pass only
   `credential_ref`.
5. Provider execution context, when supported by the selected action, is passed
   through `provider_context_json` and validated against the action's
   `provider_context_schema_json`. This context is connector-owned and is not
   endpoint body/query/path payload.
6. When a reusable execution context exists, callers should pass `context_ref`
   instead of repeating credential and provider scope. Explicit
   `credential_ref` or `provider_context_json` still works as a deliberate
   override when the context allows it.
7. Direct external-provider action responses are compact file-backed by default
   for MCP/REST and raw inline by default for CLI. File-backed responses point
   to a sanitized action-output file with schema version, checksum, byte size,
   and semantic name. Request raw only when the public audit shape itself is
   required; read the returned file path only when the request/response envelope
   is needed.
8. The execution writes the same `action_calls` audit row as workflow
   execution, but without run-plan linkage.

Agent-facing MCP setup/discovery tools default to compact operation-shaped
responses when the operation policy allows it. The operation response shaper is
the single source of truth for compact/raw/ack behavior; the bridge forwards the
requested mode and protects legacy non-operation replies only. Use
`response_mode=raw` or `response_mode=standard` for the normal daemon payload,
and `response_mode=ack` only for safe internal writes. REST and UI operation
calls use each operation's response policy default, which is usually compact for
agent-facing reads and writes; callers that need the full contract must request
`response_mode=raw` explicitly.

Compact `resource.get` and `resource.query` responses retain bounded resource
and record summaries, record ids, `data_json`, timestamps, counts, and cursors.

Use `action.execute` when the action belongs to a workflow:

1. A valid `run_token` is required.
2. The run token must belong to a started run plan.
3. Exactly one run-plan step must be running.
4. The requested `project_id` must match the plan project.
5. The active step must declare the requested `action_ref`.
6. The frozen `mcp_tool_grants` snapshot must grant `action.execute` for that
   exact `action_ref`.
7. Credentials are resolved inside the daemon; callers pass only
   `credential_ref`.
8. Pass `context_ref` when a workflow/task-linked execution context supplies
   credential, provider scope, output policy, request budget, or artifact
   namespace defaults for the step.
9. The execution writes an `action_calls` audit row with redacted request,
   response, metadata, and run-plan linkage.

`runPlan.*` keeps the same boundary everywhere:

1. `runPlan.validate`, `runPlan.create`, `runPlan.start`, `runPlan.get`,
   `runPlan.getStep`, `runPlan.checkConsistency`, and `runPlan.list` are bootstrap/setup
   operations.
2. `runPlan.claimStep` and `runPlan.recordStep` require the `run_token` returned
   by `runPlan.start`.
3. `runPlan.claimStep` activates only the frozen grants for the claimed step.
4. A run plan may have only one running step. Record the running step before
   claiming another.
5. `runPlan.recordStep(blocked)` records a recoverable blocker and keeps the
   plan started so the same step can be reclaimed after repair.
6. `runPlan.recordStep(success)` enforces run-plan lifecycle, transitive step
   dependencies, and frozen required output keys and JSON schemas before
   changing state. Validation failures keep the step running and return repair
   paths. Tracker graph warnings stay visible through
   `tracker.get(include_graph=true)` and `runPlan.get.consistency_issues`, but
   do not by themselves reject a valid step result.
7. `runPlan.recordStep(success|failed|skipped)` persists a terminal step result
   and closes the plan/run when the last step finishes or a step fails.
8. `runPlan.update` records safe metadata or an explicit operator approval
   decision through the project-scoped MCP, REST, or CLI operation. The operator
   decides; an agent can record a decision already given verbally or in
   authoritative evidence using `decided_by` and `decision_json`. It does not
   need another approval ceremony. Pending/rejected gates still block their
   steps and actions, and project scope is checked before mutation.
9. `runPlan.recover` is a narrow lifecycle repair for system-recoverable
   terminal states, such as an old daemon rejecting a recoverable blocked step
   or a daemon-restart orphan aborting the canonical workflow. It restores the
   same plan/run into a live blocked or pending step instead of creating a
   duplicate replacement workflow.
10. `tracker.reopen` is the normal operator follow-up path after closeout. It
   accepts a task key, run-plan id, or linked run id and reopens the task, or
   the linked plan/run/task mirror when the target is workflow-backed. Agents
   should use it instead of patching workflow task status directly.
11. Stale-run reaping, explicit run aborts, and run-plan aborts reconcile the
   linked run, plan, unfinished steps, pending approvals, and tracker mirror
   through one lifecycle path. If old data or manual edits leave a mismatch,
   `runPlan.get.consistency_issues` and `runPlan.checkConsistency` expose the
   read-side diagnostics.

`agentRequest.*` keeps the same boundary everywhere:

1. `agentRequest.list` and `agentRequest.get` read sanitized queue state.
2. `agentRequest.claim` is a bootstrap work-queue mutation and requires
   `claimed_by` plus `idempotency_key`; the raw `claim_token` is returned only
   in the claim response or its idempotency replay.
3. `agentRequest.prepareRunPlan` is a bootstrap work-queue mutation that
   atomically claims a request, creates the caller-supplied run plan or
   template-backed run plan, links both records, and returns the claim token.
   It does not choose the plan, start execution, call a model, call providers,
   or send replies.
4. `agentRequest.release`, `agentRequest.linkRunPlan`, `agentRequest.complete`,
   and `agentRequest.ignore` require the active `claim_token`.
5. `agentRequest.create` is registered for REST/CLI/MCP parity, but it is not a
   bootstrap/system write. Normal callers need a `run_token` whose active
   run-plan step explicitly grants `agentRequest.create`.
6. Agent request operations never call Telegram, SMTP, IMAP, or any provider API.

Native request execution has an additional ownership fence: expired requests with
admitted `native_execution` evidence are excluded from claimable reads and cannot
be reclaimed, released, ignored or prepared by a replacement runner. Only the
native host service writes that metadata through `AgentRequestRepository`; callers
cannot supply it through request create/complete metadata. Completion uses the
original claim token and retained terminal evidence, including after lease expiry.
Resolving requires an actual successful native exit and identity, with no
cancellation. Crashed or interrupted work remains unresolved for explicit handling.
See [request-owned native execution](architecture.md#request-owned-native-execution)
for the internal lease, usage, liveness and final-persistence boundary.

`localAgentChat.createMessage` stores local human/agent chat messages as
communications resources and may create a generic `agent_request` for inbound
human messages. It does not run a model, choose a workflow, call a provider, or
send an external reply. Agent responses in the same local chat thread use the
same operation with `direction=outbound`, the same `thread_key`, a new
`message_key`, and `create_request=false`; the result is a stored
`communication-message`, not a new request.

`communicationProfile.*`, `communicationSurface.*`,
`communicationContact.*`, `communicationMembership.*`, `communicationTarget.*`,
and `communicationRoute.*` are setup/read
operations for provider-neutral communication state. They store identities,
surfaces, contacts, memberships, named destinations, and handoff routes.
Surfaces should carry `audience`, `intent`, `agent_guidance`, `data_scope`, and
`external_context` when a channel/DM/mailbox can contain customer or internal
operational context. These fields are static guidance for the operating agent:
they do not authorize a send, choose a workflow, or fetch live provider history.

`communication.send` and `communication.reply` are the normal delivery
operations for agents. The agent passes a named target or request id plus
message content; StackOS resolves actor/profile, target, provider action,
daemon-held credential, policy, capabilities, idempotency, and action audit.
If a requested rich feature cannot be delivered exactly, the operation rejects
with `effect: none`, failed JSON paths, supported capabilities, and repair
options. It does not silently convert buttons, attachments, private delivery,
threads, or notification behavior.
Template-backed providers use the same operation rather than a second messaging
API. Pass `content.template_ref` plus optional scalar `content.template_data`.
HubSpot accepts only that template form: the target resolves one opaque contact
ref, its policy must record transactional-purpose, recipient-eligibility,
legal-basis, and marketing-contact-state evidence, and the connection must have
the Transactional Email add-on explicitly confirmed. StackOS resolves the
contact's primary email inside the daemon, sends `contactProperties={}` so the
send cannot mutate the CRM contact, derives a fixed safe HubSpot `sendId` from
the same StackOS idempotency key, and returns provider status/event identifiers
only as safe refs. Raw text/HTML, attachments, controls, and bulk-recipient
approximation reject instead of changing semantics.
For media-bearing sends, text/caption and files stay in the provider's native
media message path: Slack resolves to file upload with `initial_comment`, and
Telegram resolves to file/photo upload or media group rather than sending an
extra text message first.
Simple one-off sends can run directly. Workflow sends can also run with a
`run_token`; in that case the active step must grant `communication.send` with
explicit `targets` such as `communication-target:ops-alerts`. Workflow replies
must grant `communication.reply` with explicit origin `sources` such as
`telegram`, `slack-bot`, or a source surface ref.

`communication.sendBatch` accepts an explicit list of up to 1,000
`telegram-user:` or `telegram-chat:` refs plus the same actor, target and content
intent as a normal send. Its target must declare
`send_policy.destination_mode=recipient-list`, explicitly allow the sending
profile, and authorize the broadcast action. Fixed channel targets cannot
authorize arbitrary DMs. A bare numeric ID is not a recipient ref. StackOS
validates ref syntax and target policy, deduplicates the list, and seals it with
the content into a durable ActionCall. It does not make per-recipient Telegram
reachability calls before accepting the job. Each leased item resolves its peer
and records Telegram's send result, including an inaccessible-peer failure.
Poll `actionCall.get`, inspect `actionCall.items`, and use
`actionCall.pause/resume/cancel/retry` for lifecycle controls. Workflow calls require
a `communication.sendBatch` grant naming the exact target.

For durable sends, replies, and batches, `delivery.due_at` sets the earliest UTC
dispatch time and `delivery.expires_at` sets a deadline for new attempts.
`delivery.account_interval_seconds` and `delivery.destination_interval_seconds`
may slow the provider's minimum pacing. These values are sealed at acceptance;
reusing an `intent_id` with changed content, recipients, or timing rejects
instead of creating another delivery. Providers without durable support reject
these options before sending.

Resume and selected-item retry require explicit direct confirmation and an
intent summary, or the original active workflow step with its matching grant.
Retry accepts only items with a proven safe, no-effect outcome. Completed,
in-flight, partial, and unknown effects cannot be replayed. Cancellation and
expiry stop remaining pending or safely deferred work and preserve existing
receipts and in-flight effects.

`communicationTarget.resolve` remains a read-only planning/debug helper. It
returns an explicit provider action ref and safe defaults; it does not send.
It evaluates send policy with the same target/default profile selection that
`communication.send` uses when `from` is omitted, and returns
`policy_profile_ref` so agents can see which actor profile was checked. Pass
`profile_ref`, `source_surface_ref`, and `invoker_ref` when the source request
has them so target policy can evaluate a specific communication profile, source
surface, and approved human/bot actor. Provider actions through
`action.run`/`action.execute` are lower-level escape hatches for provider-specific
work, not the default agent path.

`communication.send` and `communication.reply` with `dry_run=true` validate the
provider payload and write a dry-run `action_calls` audit row, but do not call
the provider connector. The response `effects` field states whether the provider
connector was called or only validation/audit happened.
If the resolved action runs in the background, the response reports
`status=running` with `poll_operation=actionCall.get`, exact `poll_arguments`,
and `next_poll_after_ms`. Acceptance is not a delivery receipt. Workflow sends
and replies link their action call to the active plan and step; that step cannot
complete while its delivery is still running. A replay reports `replayed=true`
and returns the existing action result without starting another send.
`communicationContext.query` returns bounded stored communication-message
history only. It can return outbound messages StackOS sent, inbound messages or
interactions delivered through ingress, and state changes StackOS recorded.
It does not call Slack, Telegram, email, or future providers to recover history
that never reached StackOS. Live provider history fetches and backfills must be
explicit provider actions with provider scopes, pagination, rate-limit handling,
visibility checks, and audit.
Invalid `fields` requests return both the rejected `fields` and the
`allowed_fields` set so agents can repair the query without guessing.

`ingressEndpoint.*` stores the project-level public webhook endpoint for
provider ingress. Configure stores the generic endpoint, refresh updates it
from explicit input or driver discovery, routes derives provider webhook URLs,
sync writes safe route metadata into communication profiles where applicable,
and status reports readiness. Slack and HubSpot app-level URLs remain explicit
manual provider updates. Telegram receives native TDLib updates and has no webhook route.
HubSpot routes are derived only for credentials with signed ingress explicitly
enabled. `ngrok` is only a local tunnel provider configured under
`driver_config`; production uses `driver=public-url` with a deployed HTTPS base
URL.

## Provider authentication operations

The operation registry separates global Account lifecycle from project
Connection authorization:

- `account.list` inventories reusable Accounts globally; `connection.list`
  returns only Accounts attached to one project.
- `account.test` is the normal daemon-side Account health probe. For a Telegram
  Account with saved sign-in, it reopens the encrypted TDLib database, checks
  authorization with `getMe`, and closes the temporary session; an already
  active session is reused. Agent calls require the Account to be attached to
  the current project; the global Accounts Test button is local-admin setup.
  It does not set durable desired-connected state.
  These operations return opaque refs and sanitized results only.
- `account.session.status` is an agent-readable Telegram TDLib session
  projection for one explicitly attached Account. `account.session.connect` and
  `account.session.disconnect` are agent-controlled writes with the same
  project attachment check. They set or clear one durable desired-connected
  state; they never attach an Account, start a user authorization challenge, or
  expose native secrets. Their safe response includes connection state, attached
  project ids, whether another project shares the impact, and the next action.
  Connect is the only path that records restart restoration; disconnect is the
  only path that clears it. Local user authorization may open TDLib to complete
  a challenge, but it saves the sign-in and leaves the session disconnected; it
  does not choose the durable connection state. A later attached-project
  connect normally restores that saved user authorization without a phone,
  code, or password unless Telegram invalidated it. Disconnect preserves saved
  authorization. While desired-connected is set, `account.update` permits a
  display-name change only; Telegram, API, token, and proxy changes require
  explicit disconnect, save, then connect, with no automatic reconnect.
  An application identity or bot-token edit invalidates the saved authorization
  and requires local setup again. A proxy-only edit preserves authorization;
  `account.test` checks the new proxy while leaving the session disconnected.
  An unauthorized user connect returns `authorization_required` while retaining
  the explicit connection request; local authorization status exposes the
  current phone, code, password, or QR challenge so an operator continues it
  directly without another `account.start`.
- `connection.attach` and `connection.detach` change only project
  authorization. They are local-admin setup mutations, never copy, rotate, or
  delete Account secrets, and are not callable by normal agent sessions.
- `account.start` is a local-admin setup operation. For an interactive method
  it accepts the provider, auth method, existing opaque `credential_ref`,
  optional project to attach after success, and trusted return surface. It
  returns the provider authorization URL created from the provider contract and
  never accepts authorization codes or caller-selected redirect URIs.
- For Telegram, `account.start` is the local-admin native authorization action
  after an operator begins or retries sign-in. User Accounts continue through
  Telegram's phone, code, and optional password challenge. Bot Account creation
  checks the saved token once and closes TDLib; a failed first check leaves the
  Account pending so local setup can retry. A normal agent that connects an
  unauthorized user Account receives repair guidance instead of challenge
  material. An explicit bot session connect can use its daemon-held token
  without returning it.
- `account.create`, `account.get`, and `account.update` are registered
  REST-only local-admin contracts because their write-only credential fields
  and edit state must not cross MCP or CLI. The exact REST routes and the other
  Account/Connection adapters dispatch through these same operation handlers;
  there is no parallel repository route lifecycle.
- `account.revoke` remains a local-admin setup mutation. Revocation is blocked
  while project Connections still use the Account.
- `auth.callback` is REST-only at exact GET
  `/api/v1/auth/oauth/callback`. It is absent from MCP and CLI so provider
  callback values cannot be routed through an agent or command invocation.

The callback consumes the bound transaction, attaches the Account when a
project origin was requested, and returns a sanitized 303 to Accounts or that
project's Connections page. Action execution later resolves a fresh token and
enforces both the project attachment and action scopes inside the daemon. No
operation adapter or connector may implement a parallel OAuth callback,
refresh, or token-acquisition path.

`toolProfile.resolve` is the agent-friendly target resolver. Use it before
`action.run` or workflow setup when the agent needs one safe execution tuple for
a provider: optional project tool profile, daemon-held `credential_ref`,
provider auth status, and a concise setup `next_action` when the tuple is not
ready. For Telegram it resolves the `communication-profile` plus its
attached Account. For SMTP, IMAP, and other providers it resolves the requested
Account directly. It never returns secret payloads and never
chooses the workflow or provider action for the agent.

No operation adapter should bypass repository/connector auth, grant, idempotency,
or audit code.
