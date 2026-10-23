# Internal Diagnostics

`support.internal-diagnostics` is the reusable method for internal StackOS
incidents that otherwise require each agent to reconstruct the same history.
It lives in the Support plugin and uses existing workflow, run-plan, tracker,
resource, artifact, and audit owners. It adds no diagnostic engine, preset,
resource schema, background repair process, or collector/import endpoint.

Read the [product contract](./product-direction.md#product-contract),
[workflow authoring lifecycle](./workflow-templates.md#agent-workflow-setup-lifecycle),
[run-plan recovery](./run-plans.md), and [security boundaries](./security.md).
The exact input/output schemas and operating instructions are in
[`internal-diagnostics.yaml`](../plugins/support/workflows/internal-diagnostics.yaml).

## Smallest reusable boundary

Customer support's `support.issue-investigation` already provides evidence and
uncertainty conventions, but it requires a canonical Slack thread and ends in a
posted support conclusion. Internal diagnostics must also work over retained
evidence when the local MCP bridge or upstream task launcher is unavailable.
It therefore has its own workflow identity, without copying the customer
communication requirements or adding another state store.

The main agent adapts `stackos.workflow-orchestrator` and the existing
`stackos.workflow.project-memory-review` role. The recommended
`stackos.sdlc.codebase-explorer` traces source contracts read-only. These roles
do not acquire host/computer access or repair authority from a workflow.
Implementation and release work belong in separately authorized
`engineering.tracked-delivery`.

## Four stages

| Stage | Required output | Purpose |
| --- | --- | --- |
| `resume-provenance` | `incident_resume` | Rehydrate scope, authority, published package provenance, existing effects, and cumulative budgets. |
| `collect-classify` | `diagnostic_findings` | Collect only authorized narrow observations and distinguish failure layers. |
| `propose-remediation` | `remediation_receipt` | Propose a bounded remedy, or record a separately authorized attempt and its effect. |
| `verify-escalate` | `incident_receipt` | Verify the original symptom or escalate with an exact evidence gap and resume cursor. |

Each output has a strict object schema. The shared receipt carries:

- Stable incident key, mode, stage, and reviewed source revision.
- Explicit project/target scope and current authority refs.
- Installed package identity, executable ref, version, fingerprint, optional
  package source revision, and independent published-release evidence.
- Prior attempts, request refs, outcomes, effect certainty, cumulative
  attempt/failure counts, limits, retry disposition, and original observation
  time. `observed_at=null` explicitly means the original time is unavailable;
  do not substitute the current analysis time. Source refs preserve known
  original evidence capture times.
- Safe evidence refs, action boundaries, next action, and a cursor naming the
  existing run, step, ticket, last consumed tracker revision, previous receipt,
  and capture time.

Findings use typed layer/classification codes. Remediation records
`not_attempted`, `proposal_only`, `attempted`, or `blocked`, separately from
effect certainty. `attempted` requires live mode, a current authority ref,
nonempty operation/target/attempt refs, and a retained attempt record. An
unknown remediation effect permits only reconciliation or escalation as the
next action. Final verification records the symptom check, cause status,
current effect disposition, original symptom observation time, matching runtime
fingerprint, evidence refs, and missing-evidence codes. Schema validity proves shape, not
root cause, privacy, authority freshness, counter monotonicity, or safe retry.
The main agent checks those against actual evidence before writing or acting.

## Live versus recorded

`live` means actual current observations from a verified published installed
package. Its successful receipts require `runtime.installed_package` to be
`verified_published`, with a nonempty package/executable/version, SHA-256
fingerprint, and release-evidence ref. The reviewed `source_revision` is not
the installed package revision. A version string, app path, build-info file,
or timestamp-like `buildId` alone does not prove published origin.

Resolve the exact installed executable from independently checked package
context before any live invocation. Do not use a repository `.venv`, checkout
`python -m stackos`, `uv run`, development server, or fallback through `env`
or `PATH`. Unknown, local-build, mismatched, or unverifiable provenance blocks
live execution. Preserve the blocker instead of manufacturing release proof.

`recorded` means analysis of already authorized observations. Runtime fields
may remain explicitly null/unverified. It performs no current runtime probes
or remediation, and cannot yield `verified_resolved`. Use it deliberately,
not as a silent fallback that changes the meaning of live proof. A blocked
live run may be left blocked while a clearly linked recorded analysis is
authorized; do not rewrite its history or claim a live check occurred.

The [recorded incident example](../examples/diagnostics/launch-handshake-2026-10-23.json)
retains two distinct boundaries: external task create/send uncertainty and a
local bridge initialization failure with a missing leaf cause. The old
project-virtualenv invocation is historical evidence, not a permitted live
runtime. A manual working host-session report is also not independent proof
that its command/package matches the failing invocation. Running this example
over isolated source tests is a recorded workflow demonstration, not a live
installed-package reproduction or repair.

## Setup and run through existing operations

First verify the actual mounted/installed operation schemas. Current source
supports this path, but source presence does not prove an older package does:

1. Read `workflowTemplate.authoringGuide` and select `author_project` when
   saving this method to a project before a distributed package includes it.
2. Validate the complete template with `workflowTemplate.validate`, then save
   it with `workflowTemplate.save` using `project_id`, `template_json` or
   `template_yaml`, and `source="project"`. Saving creates no run or repair.
   The exact verified packaged CLI can invoke the same operation with
   `ops call workflowTemplate.save --project <id> --input <arguments.json>`.
   That file contains the operation-argument wrapper, not the bare template.
3. Describe the effective workflow, resolve its existing agent/skill presets,
   and adapt them to current project rules and host capabilities. For a
   project-specific policy/default change, prefer
   `workflowExtension.validate` / `workflowExtension.upsert`.
4. Run scoped `readiness.check` and structural plus strict read-only
   `runPlan.validate` with `enforce_required_inputs=true`. Separate structural
   validity, available context, package provenance, and execution capability.
5. For authorized operation, create/start the run, retain its controller
   capability privately, claim the step, perform only permitted work, and
   record its schema-valid output through `runPlan.recordStep`.

Use `toolbox.describe` for exact hidden operation names and `toolbox.call` for
calls when the host exposes only the compact bridge surface. If an older
installed build rejects project template setup as
`local-admin-workflow-template-write`, report the supported app/session update
requirement; do not invent a grant, edit the database, or run a source build.

## Durable state and resume

`runPlan.create` owns the workflow task/step mirror. Resume the selected run
with `runPlan.get`, `runPlan.checkConsistency`, `tracker.brief`, and targeted
`runPlan.getStep`; do not create another incident to escape a blocker. Preserve
retained receipts in tracker evidence before a rewind or result replacement.
If the intended step is already `running`, continue its existing state and
frozen grants with valid recovered controller authority. Do not call
`runPlan.claimStep` again while that step is running. If the existing run or
controller capability cannot be recovered through a supported route, report
that exact blocker and stop dependent work. Do not call `runPlan.start` on an
already started plan, create a replacement incident, or fabricate authority.
Revalidate when the installed package/executable/fingerprint, project/target
identity, authority/grants, or relevant effective workflow contract changes.
Keep old observations as historical evidence but invalidate current claims that
depended on the changed identity. Recheck only the smallest necessary probes
that remain authorized. Freshness follows evidence and identity changes, not
an arbitrary elapsed-time TTL; a recent receipt is not proof of unchanged
runtime or authority.
When refreshing `tracker.changed`, advance from the last returned change's
revision and drain bounded pages. Its `current_rev` is the tracker head, not
necessarily the last returned change.

The existing `support/support-investigation` resource is a compact index.
Its context feed is bounded, and `resource.query` is paginated. Absence from
the first context/query page is not absence of an incident. Follow returned
`next_cursor`/`after_id` pagination, or a known tracker/run reference, to find
the exact incident key before deciding that a new run is needed.
Use `resource.upsert` with `external_id=incident_key` and only its declared
`title`, `summary`, `status`, `root_cause`, `evidence_refs`, `chat_refs`,
`task_key`, and optional `created_at` data fields. Detailed attempt ledgers and
cursors stay in run-step results and tracker evidence/context, not undeclared
resource fields. Preserve existing JSON contents when patching tracker fields;
supplied JSON fields replace the previous field rather than deep-merging it.

Resource and artifact writes require a started run, active step, and matching
frozen grants. This template grants the incident resource at collection and
closeout and allows intentional `artifact.create` refs at those steps only.
It grants no provider action or communication send. `artifact.create` records
a reference; it does not copy/upload a file or make a private path accessible.
Check existing receipt identity/digest before creating another artifact.

## When MCP is down

Do not require the failing bridge to diagnose itself. If separately authorized
host access and verified package provenance are available, the exact installed
executable can run narrow `mcp-host-status --json --host <selected-host>`.
The configured unauthenticated `/api/v1/health` endpoint can independently
report liveness/version. Neither is a repair, neither needs the bridge, and
neither proves that host initialization or external task creation succeeds.
Do not guess another local port or attribute a reachable listener to StackOS.

If those reads are unavailable, stop with the precise gap. There is no
dedicated collector or automatic offline import in this slice. Under existing
host-file authorization, retain only a minimal sanitized receipt with original
capture time, mode, incident/request identity, safe refs/digest, and resume
condition. It is an evidence transfer copy, not a competing incident master.
Do not store full logs, provider bodies, auth material, environment/config
contents, or a raw exception dump. Review/redact before every durable write.

Once the normal runtime route is restored, re-resolve current project/run/step
and grants. Reuse the incident index, preserve the original receipt identity,
and import retained evidence through granted `resource.upsert`, intentional
`artifact.create`, tracker evidence, and `runPlan.recordStep`. Link original
and import capture refs; do not relabel old observations as current or replay
the original action to obtain a cleaner receipt.

## Effects, retries, and stopping

`unknown` is not `failed`, and neither implies `effect=none`. Reconcile the
original request/action/object through its own read/list/status surface first.
For a StackOS action call use `actionCall.get` with returned poll arguments;
an external task launcher needs its own upstream read/list evidence. A task
not appearing in a list does not reveal the launch service's internal cause.

Keep all denied/failed attempts and cumulative budgets across agents and
resumes. Never reset counters, broaden targets, race executors, or extend a
limit. Exhausted budgets stop. A denied action requires materially new
relevant authority; an uncertain effect requires reconciliation before retry.
Missing bridge leaf error is a request for already captured, sanitized narrow
evidence, not authorization to repeat initialization or a denied process scan.

Diagnosis alone authorizes no restart, install, configuration/authentication
change, process inspection, external send, source fix, or release. A permitted
repair must have its own exact current authority and capabilities; any required
StackOS step grants/approvals still apply. Record actual outcome and effect,
then independently check the original symptom. If blocked, use recoverable
`runPlan.recordStep(blocked)` and retain exact next evidence/approval and resume
conditions. An unresolved investigation is a valid conclusion, not a claim
that the incident was repaired.

Final `verification` requires `current_effect_status` (`reconciled`,
`unresolved`, or `unknown`), `observed_at` (the original symptom-check UTC time
or null), and `runtime_fingerprint_sha256` (the observed runtime digest or
null). `verified_resolved` requires live mode, a passing current symptom check,
`cause_status` of `verified` or `bounded_uncertainty`, reconciled current
effects, a nonnull observation time and runtime digest, and no evidence gaps.
The main agent must compare that digest with the current runtime and verify
matching project/target/authority; a syntactically valid hash is not that proof.
Historical attempts with unknown effects remain intact. Only current
authoritative evidence can establish their reconciled disposition, linked by
the original operation/request identity. An unresolved upstream effect, unknown
cause, or missing current symptom/runtime proof prevents verified closeout.

## Adapt while investigating

Validate and save a new workflow version when the reusable method changes;
changed content under the same version is rejected. Read the saved effective
contract back. Existing runs retain frozen snapshots, steps, grants, outputs,
and approvals. `runPlan.update` updates metadata/approval decisions, not the
frozen step contract. Review `workflow_contract` mismatch before further
effects. Continue only if remaining work fits current requirements and the
frozen authority; otherwise leave it blocked and prepare explicitly linked,
authorized follow-up work that reuses valid incident evidence. No template
upgrade justifies a duplicate create/send or a fabricated repair receipt.
