# Agent Presets

Agent presets are reusable operating contracts for agents that call StackOS
tools. They are not daemon-run agents, not hidden orchestration, and not final
project prompts.

Presets implement the responsibility model in the canonical
[`Product Contract`](./product-direction.md#product-contract): the main agent
owns integration and judgment, specialists own bounded contracts, and StackOS
owns durable state and explicit execution.

All preset families follow [canonical project guidance consumption](./agent-operating-model.md#canonical-project-guidance-consumption).
Resolve the same approved sources and their current revision/approval evidence,
then pass the role-relevant subset through existing context and handoff contracts.
The required `canonical project guidance` context ref asks for that resolution;
it does not make every possible brand or business source mandatory. Public-copy
authors and voice reviewers use the same selected foundation refs/revisions and candidate;
research, technical, communication and finance roles apply their own relevant
boundaries. A host adaptation records this method and source-preset version,
never a copied voice profile or approval snapshot. Presets include the protocol
summary so projects without this repository's docs can apply it.

Every bundled preset is generic and must be adapted before use:

- `generic_preset: true`
- `project_adaptation.required: true`
- `project_adaptation.do_not_use_verbatim: true`
- discovered project instruction entrypoints (such as `AGENTS.md` when present),
  `stackos:stackos`, and project-local docs or skills when present
- a prompt assembly order:
  `generic_agent_preset -> project_adaptation_overlay -> workflow_agent_requirements -> current_tracker_or_run_plan_context -> user_request`

Every bundled preset also declares `role_class`:

- `reasoning`: makes bounded judgments and returns the rationale
- `mechanical`: performs a defined transformation or handoff without taking
  over strategy
- `review`: challenges another role's result independently and returns claims
  for the orchestrator to adjudicate

`role_class` is an expectation signal, not a model selector. The actual
`prompt_contract` still owns mission, boundaries, handoff inputs, handoff
outputs, success criteria, and self-checks.

The adapting agent must rewrite the generic role for the current project:
technology stack, rules, documentation references, available MCP tools,
workflow/run-plan model, tracker task/ticket conventions, verification
commands, and release expectations.

On a fresh or resumed session, re-resolve the current workspace binding,
effective workflow/extension/presets, readiness, usable provider route, phase
grants, and relevant external state. Historical setup proof and saved host or
browser references may help navigation, but do not prove current readiness or
authority. Resolve packaged workflow and preset references from their effective
response origin, not an assumed source checkout.

Adaptation starts with investigation, not invention. The agent identifies the
nearest project rules, current architecture and flows, canonical owner, active
consumers, reusable helpers and contracts, and established test boundaries.
Project-native patterns are the default. A new abstraction needs a demonstrated
gap, rejected project-native alternatives, a named canonical owner and
consumers, and a migration or removal boundary. Specialist/subagents return
evidence and recommendations; the main agent retains scope, integration, and
architecture decisions. This keeps one source of truth with multiple consumers
or adapters instead of parallel helpers, services, repositories, policies, or
pass-through layers.

Workflow infrastructure setup uses one deterministic materialization policy:

- every required role must be materialized when the host supports local agents,
  or represented by an explicit session-only fallback with the cross-session
  limitation reported;
- recommended roles are materialized by default unless the adapting agent
  records a project, host, or risk reason for omission;
- optional roles are materialized only when selected by the operator, project
  extension, or current workflow branch;
- agent presets become specialist roles, skill presets become main-agent
  orchestrator guidance, and installed host skills remain skills;
- every materialized target records the applicable workflow key(s), preset
  key/version, requirement level, project references used for adaptation, and
  exact host target; syntax/registration and any reload requirement are verified
  before setup is reported complete.

A request to set up workflow infrastructure or local agents authorizes reviewed
host-local execution contracts when project guidance permits them. It does not
authorize `.stackos/agent-presets` or `.stackos/skill-presets` overrides, global
domain agents, a new workflow template, or workflow execution. Repository-local
StackOS preset overrides require an explicit checked-in override request.

`recommended_tools` are StackOS operation references. Some hosts mount those
operations as direct MCP tools, while the StackOS bridge may expose only
`workspace.startSession`, `workspace.resolve`, `toolbox.describe`, and
`toolbox.call`. Treat the operation refs as the intent-level tool list: call
them directly only when the host exposes them, otherwise inspect the exact names
with `toolbox.describe` and invoke them through `toolbox.call`.

Setup guidance must be scoped to the selected preset or workflow. Generic
agent responses explain host adaptation, the toolbox boundary, tracker/run-plan
state, and skill-preset resolution. Domain chains such as feedback intake to
support investigation appear only for presets or workflows in that chain.
Agents should not have to filter unrelated support or engineering setup from a
branding, SEO, media, or GTM handoff.

Provider-action presets should prefer stable `action.list` refs plus
`action.describe`/`action.validate` before execution. External provider action
outputs are file-backed by default for MCP and REST calls, so presets must tell
agents to inspect the returned file paths before rerunning provider calls and
to call `schema.get` with `schema_ref` only when they need the response-file
envelope schema. CLI calls default to raw inline output unless an explicit
output policy says otherwise.
When repeated calls share credential, provider scope, output policy, request
budget, or reusable execution context, use
`executionContext.discover`/`executionContext.resolve`
or create a context and pass `context_ref` to `action.run` or
`action.execute`. Keep endpoint payload in `input_json`; keep provider scope in
provider context or the execution context.

Bundled presets must not assume customer repositories contain StackOS' own
documentation files. StackOS operating guidance comes from the installed
`stackos:stackos` skill and MCP tool descriptions. Repo-local docs such as
`docs/README.md`, `docs/task-tracker.md`, or local skills should be used when
they exist, but absence of those files is a project-context gap, not a broken
StackOS install.

## Engineering Setup

StackOS presets are not local agent files and they are not registered back into
StackOS as daemon-managed agents. They are generic contracts that a main agent
may adapt into whatever the host/project supports: `.codex` agents, markdown
frontmatter files, plugin-specific agent files, or no local files at all.

### Actual project Codex definitions

The local console operations `projectAgent.list`, `projectAgent.get`,
`projectAgent.create`, and `projectAgent.update` manage actual Codex TOML files.
These are separate from the generic preset catalog. The file is authoritative;
there is no independently editable database prompt copy. Saving never launches
an agent, adopts delivery defaults, or changes an admitted execution snapshot.

Every call supplies `project_id`, explicit `project_dir`, and `host_key: codex`.
The directory must match an existing workspace binding for that project.
Association and filesystem authority are separate: these operations require an
authenticated local-admin REST session and reject run tokens and CLI requests.
They are browser-safe at `/api/v1/operations/{operation_name}/call`, with MCP and
CLI disabled. This bounded file-management path does not grant a tool consumer
general filesystem access or permission to read personal/global agent files.

Discovery reads only `.codex/agents/*.toml` and existing
`.codex/config.toml` `agents.<key>.config_file` references. The latter resolve
relative to `.codex`, within the chosen tree. Same-source registrations
deduplicate; duplicate native names, malformed files and unsafe references
produce individual diagnostics. Legacy registered files may obtain missing
name/description metadata from their registration. Symlinks, hard links,
traversal, nonregular sources and sources over 1 MiB are rejected. Discovery is
bounded to 1,000 sources, with explicit failure above that limit. Refresh reads
current files without a watcher or a last-used-folder fallback.

`source_ref` is `{host_key, native_name, source_path}`, where `source_path` is
the exact workspace-relative path. Reads return the SHA256 `digest`, editable
`fields`, native settings, inherited field names, unsupported field names,
registration metadata, capabilities and diagnostics. Compact reads keep every
identity/digest/diagnostic and omit full instructions; request `raw` for editing.
Unknown native values are preserved on disk but are not exposed as settings or
claimed to be supported by execution. File fields never grant provider,
credential, sandbox, permission or MCP authority.

Create accepts `fields` with `name`, `description`, `developer_instructions`
and optional native settings. It exclusively creates
`.codex/agents/<name>.toml`. The supported standalone format needs no new config
registration. Update accepts `source_ref`, `expected_digest` and a `patch`.
Omitted fields survive; null optional fields inherit. Nested setting patches
preserve untouched siblings and comments. Supported settings include model,
reasoning effort/summary, verbosity, and reduction-only native feature/skill
restrictions. Unsupported or authority-expanding patches reject. Writes retain
full instructions, unknown keys, comments and file permissions, recheck source
and registration identity before replacement, and verify exact saved-byte
readback. Writes are raw-only; neither compact nor ack can hide a failed save.
Retries recheck authority, workspace and current bytes in the handler. Exclusive
create collisions and stale updates reject safely, without replaying prompt
content from generic database idempotency storage. A write failure reports
whether the source was untouched, rolled back, or remains unconfirmed.

Native-name changes keep the filename stable. Reassign saved project defaults,
schedules and legacy registrations before renaming a referenced identity;
rejection returns safe reference IDs and reassign-first guidance. The service
never moves files, rewrites references, or edits active/historical snapshots.
`stale_source` means refresh and reconcile the draft, `ambiguous_identity`
requires repairing duplicate names, and `write_failed` means inspect current
bytes before retrying. Errors do not echo parser contents. This source service
does not itself establish native launch or installed-host consumption; those
consumers use its exact source identity and settings under their own authority.

### Workflow setup

Engineering setup uses the same workflow path as other domains. Start with the
workflow that matches the work, resolve its agents with
`agentPreset.resolveForWorkflow`, then create/start a run plan when executing
the work. Use `communications.customer-feedback-intake` when customer feedback
needs a canonical Slack thread, `support.issue-investigation` when the thread
needs an evidence-backed conclusion, `support.delivery-task-handoff` when a
same-thread operator instruction asks for task creation, and
`engineering.tracked-delivery` once work is scoped for implementation,
verification, review, and release. The workflows reference generic presets as
required and recommended roles, but the workflow owns the flow.

When an operator explicitly asks to use a workflow, engineering workflow,
StackOS workflow, "the workflow", to follow the SDLC, for release-grade
delivery, or equivalent lifecycle enforcement, agents must create or resolve the
workflow-backed run plan before creating tracker tickets. All discovery, design,
delivery, verification, and closeout tickets for that work belong under the
workflow task/run plan from the start. Direct tracker tasks are valid only when
the operator asks for task/dependency tracking without invoking a workflow.

The tracked-delivery workflow uses this baseline:

```text
scope
-> requirements and flow definition
-> codebase impact discovery
-> architecture/design
-> test and verification design
-> executable ticket planning
-> delivery readiness review (design, proof and ticket plan)
-> delegated delivery with immediate independent ticket review
-> integrated verification
-> integrated delivery review
-> tracker truth audit
-> release closeout
```

The customer feedback/support workflow chain uses this baseline:

```text
incoming feedback
-> route and media preflight
-> canonical Slack thread
-> intake reaction
-> full-thread read
-> same-thread clarification when needed
-> support investigation conclusion in the same thread
-> separate same-thread instruction for task creation
-> delivery task creation
-> task handoff message in the same thread
-> task-created reaction
-> engineering.tracked-delivery handoff
```

This baseline is intentionally project-neutral. When adapted into another
repository, keep the engineering mechanics and replace the stack, docs, test
commands, host-agent format, and release/signoff rules with that project's own
sources. Do not carry product or domain facts from the project where the
baseline was derived.

For non-Slack feedback, communications agents must not infer route approval
from a resolvable Slack target. `communicationTarget.resolve` answers where a
named target would send; `communicationRoute.*` or a current operator
instruction answers whether this source is allowed to go there. The same
preflight owns media fidelity: every inbound image, document, video, audio,
voice note, screenshot, URL, or provider file ref must either be
forwarded in the same canonical Slack handoff message when supported, or become
an explicit blocker/waiver before the workflow continues.

The core support and engineering preset subset is deliberately small:

| Preset | Role |
| --- | --- |
| `communications.workflow.customer-feedback-intake` | Normalizes customer feedback into one route-approved Slack support thread, preserves source media, and returns support investigation refs. |
| `support.workflow.issue-investigator` | Investigates the full canonical thread and project evidence, asks same-thread clarifications, and posts a support conclusion with root-cause evidence or bounded uncertainty. |
| `support.workflow.delivery-handoff` | Converts a same-thread operator instruction and support conclusion into delivery-ready tracker work with durable chat refs. |
| `stackos.sdlc.requirements-flow-definer` | Defines actors, flows, acceptance criteria, non-goals, and evidence expectations. |
| `stackos.sdlc.codebase-explorer` | Maps real execution paths, ownership, downstream fallout, tests, and docs before changes. |
| `stackos.sdlc.planning` | Converts requirements, impact, design and proof into actionable ticket packets with ownership and dependencies. |
| `stackos.sdlc.architecture` | Chooses and challenges the project-native design, canonical owner, contracts, rollout, and validation plan. |
| `stackos.sdlc.test-designer` | Owns the full proof plan: TDD/red-first gates, automated checks, risk-appropriate manual proof, expected outcomes, and signoff evidence. |
| `stackos.sdlc.delivery` | Implements assigned tickets, self-checks, repairs admitted findings and returns candidate evidence for acceptance. |
| `stackos.sdlc.delivery-reviewer` | Independently reviews readiness, individual tickets or integrated delivery within an explicit scope; returns findings without repairing implementation or accepting tickets. |

Use this subset as the only support/engineering agent set for a project.
Workflows pick the roles they need. For example, customer feedback intake uses
the communications intake role; support investigation uses the support
investigator and may use codebase exploration; delivery task handoff uses the
support handoff role and may use planning; tracked delivery uses requirements,
discovery, planning, architecture, test design, delivery, delivery review, and
release closeout coordinated by the main agent. Test design is the single owner
for test planning: it maps accepted requirements to automated proof, red-first
TDD slices when needed, manual proof depth, expected outcomes, and evidence
links, screenshots, or logs. The main orchestrator chooses that proof depth by reasoning from
quality, production risk, and user/business impact; a quick smoke is not a
substitute for full manual signoff or rehearsal when the risk calls for it.
When browser-assisted platform work depends on login state, the test plan must
name the stable StackOS browser `profile_key`, whether the operator must log in
once, whether cookies/session reuse is expected, and whether that precondition
is closeout-blocking or operator-owned. The main orchestrator or a designated
reviewer verifies that plan with the design and ticket breakdown before delivery
starts. Preparation roles need not each be a separate agent invocation. The
main agent may apply their contracts directly when sufficient current context
exists; tracked delivery delegates implementation and requires independent
review of every executable child ticket, including an explicitly ticketed
discovery task. Generated workflow step mirrors retain their phase gates.

### Engineering Delegation And Acceptance

The workflow owns phase order and gates; the
`stackos.sdlc.delivery-orchestrator` skill preset owns scheduling, model choice,
recovery and acceptance. Specialist presets own bounded task behavior. The
main agent reads their complete applicable contracts before execution. Compact
preset resolution is useful for routing and may truncate adaptation text;
request targeted raw resolution or description when it omits instructions.
The resulting assignment can still be bounded and reference only relevant
context. The
main agent stays in the existing thread with its current model/settings and
selects supported child models and reasoning effort per assignment. Generic
presets carry no model/version pins. Host capabilities, operator preferences,
risk and observed performance guide selection; a requested model and its
effective fallback must not be reported as the same when the host overrides it.

The orchestrator coordinates at most three active ticket assignments by
default, including implementation, queued review, review and repair. Ready
reviews take priority over starting new tickets. Capacity must allow an
independent reviewer: reserve and reuse a reviewer slot when completed agent
threads cannot be retired. A reviewer can review multiple tickets, but cannot
review work it implemented. Each agent has one active assignment; queue work
without retasking a busy reviewer.

The ticket packet includes current operator and acceptance refs, design,
accepted prerequisites, contract/assignment identity, owned paths and shared
resources, proof steps and expected results, authorized environment, and return
and stop conditions. Read the full targeted `tracker.brief` with
`response_mode="raw"` when compact navigation omits required fields, then send
the specialist a bounded packet. Keep durable facts in existing run/ticket
context and evidence, and reference them from schema-valid phase outputs.

Plans, listed paths, design and proof are fallible evidence, not exhaustive
boundaries. Each agent owns its assigned outcome, investigates obstacles and
takes the smallest proportionate action within existing authority and role to
resolve necessary omissions or routine setup, tooling and integration gaps.
Verify and record the change, necessity, affected consumers and proof. Delivery
may change in-scope implementation/proof; independent review executes checks and
may prepare or repair its verification environment, but sends product fixes back
to delivery without editing product/acceptance tests or weakening criteria.
Explicit exclusions, new or unavailable authority, actual ownership conflicts,
materially different outcomes or compromised review independence stop affected
work for reconciliation; continue unaffected work. Unrelated naming/style/debt
stays in nonblocking followups; reject drift, unnecessary abstractions and
duplicate ownership, not justified unlisted changes.

Ticket-local acceptance must be distinct from integration-only obligations.
An interface ticket can pass its contract checks before a consumer is built;
the complete user journey remains an open workflow obligation. Readiness
checks coverage against authoritative operator input as well as derived
criteria, and rejects dependencies that make a ticket's required proof depend
on its own blocked consumers.

After final edits, delivery executes relevant tests, lint and area checks derived
from current requirements/amendments, the whole diff, actual consumers and project
rules, including necessary omitted checks. It reviews the final diff and artifact
hygiene, keeps scratch logs/captures/one-off QA scripts outside Git, and records
commands/results, candidate, prerequisites, environment and proof gaps in the
existing receipt. Intentional maintained assets and regressions receive their
applicable checks. Failed or unavailable required proof prevents candidate-ready;
self-signoff does not replace independent acceptance.

Test design traces the real caller through the owning producer, affected consumer
and actual authorization boundary, with expected results independent of the
implementation. Delivery declares mocks/substitutions and their limits; review
challenges any missing real wiring. Asynchronous changes select relevant lifetime
risks, including late outcomes after cancellation where applicable, rather than
requiring a fixed state matrix for every change. Planning assigns delivery,
review, integration and release checks to their existing owners. Keep dependency
mutation exclusive against checks consuming that environment, and check readiness
after the relevant transition. Retrieve bounded current source sections once and
expand for missing contracts or contradictions without skipping required guidance.

Source implementation and repairs produce separate scoped commits after self-checks.
A separate reviewer checks that exact commit against the assigned
requirements and actual evidence, returning the full `commit_sha`. The main
agent adjudicates findings, routes repairs, integrates the reviewed result,
rechecks affected evidence, then marks the ticket complete in terminal-managed
delivery. An explicit `tracker.startDelivery` handoff instead lets StackOS
mechanically complete approved ordinary tickets and advance serially through all
remaining dependency-ready task work. Mirrors execute their remaining frozen
contract once using an existing selected role; only RunPlanRepository records
their lifecycle. A valid scoped
rejection is persisted before each of up to three automatic repair passes in the
same native delivery session, with a separate new descendant repair commit and
re-verification in the same independent review session. The consumed count survives explicit
requeue; blocked required verification, native/access/model/resume failure,
candidate mismatch or rejection after the third repair stops that task.
This count covers ordinary review rejections. Integration-origin correction after
prior child approval preserves the count and may requeue at `repair_count=3`;
its independent ordinary review may approve, but a rejection there permits no
fourth ordinary repair. This is not a total cap on corrections from all origins.
Main retains scope admission and final claims. In automatic delivery, assigned agents execute
integration and workflow phase gates; main observes without lifecycle writes.
Defaults use `TaskTracker.config_json.delivery`
via local-admin REST-only `tracker.configureDelivery`, merged with task overrides by role once.
Fresh Codex handoff selects actual project sources; non-Codex hosts retain inline
contracts. Explicit native model/effort wins, omitted values inherit task, project
then host choices. Supported source restrictions can only reduce capabilities.
Reusable presets recommend no fixed models. The shared
host capability owner selects test-capable native permissions and approval review and uses
normal native sessions for Gemini and Hermes reviewers with review-only
instructions, truthfully reporting no enforced read-only isolation. Until acceptance,
the ticket stays `in-progress`. There is no separate review ticket by default
and no new tracker status. Main-agent scheduling and acceptance are guidance,
not daemon-enforced leases or semantic verification.

Reviewers independently derive coverage from current requirements/amendments,
the whole diff, actual affected consumers and project rules. They execute
relevant planned specs and integration checks, plus necessary omitted tests,
lint, area and interaction checks themselves,
reporting commands/results/current SHA and clean unchanged HEAD for reject and
approve. Failed required specs reject; genuinely unavailable required checks
block. Within ticket plus integration scope, review business regression,
architecture gaps, inaccurate wiring and canonical ownership where relevant.
Required repairs cite scoped criteria/evidence. Unrelated findings go only in
separate nonblocking `followups` text objects, never required-repair summary,
dependencies or actionable repair prompts. All current automatic launches return
`status` (success/repair/blocked/failed), `summary`, `outputs` (a JSON-encoded
object string), and `followups` (text objects). Only success advances; ordinary
review repair uses the bounded loop. Supported persisted correction/reopen returns
to dependency scanning; mirror repair without progress stops. Agents explicitly map
domain verdicts to status and return declared frozen mirror outputs. Honor an
older installed launcher's actual occurrence schema. Source changes and repairs
require exact scoped commits in `outputs.commit_sha`; observation may cite
unchanged HEAD and non-Git work needs no fabricated commit. Ordinary test outputs are
allowed; production files, acceptance tests and commits remain review-only.

Only delivery and reviewer contracts carry the detailed bounded
[automatic correction method](task-tracker.md#agent-owned-automatic-corrections).
Other roles retain the authority boundary and return findings without those
mutations; recommended tools and role names grant no lifecycle authority.
An assigned automatic verifier/reviewer full-reads and merges scoped feedback on
the existing owning ticket, preserving source/target/attempt/candidate identity,
failed criterion/check/evidence, required outcome/recheck, unrelated context and
all sessions/receipts/consumed allowance. It saves failed outputs before rewind,
requeues affected children and calls `tracker.reopen` at the earliest affected
bound gate as its final mutation, then returns `repair`. It finishes checks and
quiesces other affected work first, without waiting for itself to terminate.
Only the runner resumes original sessions and records workflow lifecycle.
Fresh persisted correction outranks historical approval; corrected source needs
a separate descendant commit and independent exact-SHA review. This exception
does not permit self-acceptance, claim/record, dispatch, new work from followups,
or bypass of actual execution errors or the ordinary three-pass repair limit.
Terminal-managed specialists still return evidence for main to route.

Review must identify its candidate, assignment attempt, contract revision and
relevant environment. Disjoint file ownership does not prove independence: a
check may import another ticket's unaccepted helper or use a shared fixture.
Wait for the actual dependency or isolate and rerun against an accepted base.
Parallel engineering assignments follow
[task-local native routing](task-tracker.md#task-local-native-tool-routing):
clean distinct trees, a frozen task-only route reference, canonical per-tree
tools and same-project bindings, independent native review, and explicit
integration by the named owner. A working-directory flag alone cannot establish
MCP targeting. Source/local guidance changes do not update saved instructions
or running snapshots; adoption and installed proof remain separately owned.
Final verification/review covers assembled behavior and remaining obligations,
reusing valid ticket receipts instead of automatically repeating them.

Repair review reassesses original coverage as well as the fix. In the existing
receipt, distinguish checks executed on the current candidate, valid unchanged
evidence with scope/prerequisite/environment justification, invalidated evidence
requiring rerun, and never-executed required checks. A repair-only slice cannot
inherit an incomplete original review. Preserve failed proof as history; identify
original omissions separately from later operator amendments.

For visual changes, delivery and review compare the candidate with the approved
design and current amendments: exact wording, content applicability, hierarchy,
actual consumer widths and the reported interaction and relevant states. Record
design/source refs and observed environment. Screenshots and passing counts alone
do not prove behavior. Source, installed and external observations remain distinct;
missing required visual or interaction proof blocks the affected claim.

The role revision reviewed official provider guidance on 2026-11-08. OpenAI's
[skills and prompts guidance](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)
supports applicable context and clear completion boundaries; its model-specific
advice does not remove this project's required executed checks.
[Codex review rules](https://developers.openai.com/blog/custom-code-review-rules-for-codex)
and [Claude Code practices](https://code.claude.com/docs/en/best-practices)
support consequential, scoped review against the specification and executable
evidence. [Google's prompting guidance](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/prompts/prompt-design-strategies)
informs review of the assembled instructions for ambiguity and unnecessary
overlap. These are design inputs, not effectiveness proof. Raw/compact resolution
and source/local contract checks prove instruction availability; the assigned
native rehearsal must demonstrate omitted-check discovery, final tests/lint,
truthful self-signoff, independent review and restraint on unrelated cleanup.
No source-only check proves saved adoption or current native/installed consumption.

The focused source revision reviewed the supplied official guidance on 2026-11-09:
[issue-to-tested-PR](https://learn.chatgpt.com/use-cases/issue-to-tested-pr#how-to-use)
informs repository-specific baseline, regression and final-diff proof;
[Codex review rules](https://learn.chatgpt.com/docs/agent-configuration/agents-md#add-code-review-rules)
informs concise applicable instructions with a violation and safe alternative.
These are instruction-design inputs. A bounded exercise after explicit consumption
must demonstrate actual-caller/lifetime gap discovery, exact-payload revalidation,
readback before uncertain retry, justified proof reuse, exclusive setup and restraint
on unrelated cleanup. Retain consumed candidate/hashes and observed actions outside
Git. Phrase assertions alone establish availability, not effectiveness or adoption.

The bounded automated planning permission was reviewed on 2026-11-09 against
[Codex instruction discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
and the OpenAI skills/prompt guidance linked above. Applicability: state the
permitted child-materialization path and its completion boundary alongside the
prohibited lifecycle writes, without adding model-specific workflow policy.
Source/local parity checks establish availability; a bounded native rehearsal
must exercise child creation, refusal of self-claim/record/dispatch, the safe
return of outputs, and preservation of unrelated work. Full scheduled native
workflow progression remains a separate integration proof.

Source upgrades do not rewrite existing run-plan snapshots or host-local
files. Existing runs keep their frozen structure and output contracts. Local
adaptations record the version they actually implement and are refreshed only
in an authorized adaptation phase; a source-only change must not relabel old
local prompts as current. During a scoped source revision, use an explicit
current session packet where needed. The workflow's required outputs remain
schema-valid even when preparation is concise.

Adoption is manual per project. Method belongs in reusable/local guidance;
occurrence phase/attempt/SHA/persisted scoped feedback belongs in prompts, and
models remain StackOS configuration. After independent source/local review,
the main agent may manually refresh selected saved instruction strings through
local-admin REST-only `tracker.configureDelivery`, preserving every other setting and reading
back the result, including absent/null fields. Explicitly authorized instruction-only
saved-default adoption may occur during a running task after that review.
Installed activation is separately authorized and main-owned after task closure.
Source edits alone update neither saved instructions nor active
or historical task snapshots. Queued/running executions retain their snapshot;
an explicit rehandoff after completed/stopped execution resolves current defaults
and overrides while preserving prior ticket receipts, sessions and consumed repair
allowance. Completed tasks and frozen run/templates stay unchanged. Prove a subsequent handoff receives the new
instructions. Other projects adapt individually; there is no synchronizer,
watcher, automatic rollout or requirement for a `.codex` folder.

The main agent should detect or read the host convention before writing local
agents. For Codex-style projects, inspect `.codex/config.toml` and existing
`.codex/agents/*.toml` before proposing new files or updates. For other hosts,
look for that host's agent convention first. Preset resolution provides generic
contracts and workflow role requirements without scanning or writing local files.
Actual Codex file management uses the explicit local-admin operations described
above; other host formats have no definition editor. If no convention is available, use the resolved
workflow agents as operating guidance in the current session or ask the
operator which host format to use. Preserve unrelated operator-authored content
and do not create a second competing agent registry.

Missing workspace profile fields such as `framework` or `content_model_json`
mean the project is under-described for future adaptation. They do not block
project-scoped tools. After reading repo guidance and stack details, record
durable hints with `workspace.updateProfile` when they will help later agents.

## StackOS Skill And Skill Presets

Workflow templates declare `skill_requirements`. Requirements may be required,
recommended, or optional according to the workflow. The tracked-delivery
workflow requires `stackos:stackos`, the host-side skill that teaches an agent
how to use StackOS MCP, operations, workflows, run plans, tracker
tasks/tickets, dependencies, and evidence.

Workflow templates declare `skill_preset_requirements` for reusable main-agent
operating guidance that should be resolved and adapted like agent presets. A
skill preset is not an installed host skill, not an agent preset, and not a
subagent role. For example, `stackos.sdlc.delivery-orchestrator` teaches the
main agent how to sequence SDLC delivery, while the active project still
provides the concrete rules, docs, tests, reviewers, and release expectations.

This is explicit setup guidance. The main agent decides whether its host can
load installed skills such as `stackos:stackos`. Skill presets are StackOS
contracts returned by `skillPreset.*` and workflow resolution operations; the
agent adapts them before use.

## Finance Workflow Presets

Minimal agency/client-project setup uses `agency.setup` and
`agency.project-setup`, both with the existing `stackos.workflow-orchestrator`.
They need no specialist presets, agency-manager agent, or project-manager agent.
In this repository their main-agent adaptation is
`.codex/orchestrator/workflow-orchestrator.md`; see the
[agency package](../plugins/agency/README.md) for the setup/persistence/handoff
boundary. Finance remains independently usable without this package.

From a standalone business directory or an agency root, the finance main agent
uses one automatically managed Finance master per StackOS project. Client
engagements are descriptive context, never shared storage or inherited access. Optional
`external_project_ref` on billing versions and prepared bookkeeping records
retains sourced attribution; receipt custody needs no project. Company-wide costs
remain unassigned, and no allocation/profitability engine is implied. Provider
connections must be explicitly available to the bound finance-owning project;
host directory access is a separate capability.

The finance plugin is an external-system-of-record package, not a finance-agent
runtime or ledger. Its six workflows resolve six generic specialist presets:

| Preset | Role and boundary |
| --- | --- |
| `stackos.finance.receipt-operator` | Mechanically prepares a receipt provenance/completeness packet. It proposes extraction/copy/disposition by default and writes only when explicitly delegated as sole external writer; IMAP acknowledgement follows verified storage. |
| `stackos.finance.bookkeeping-preparer` | Prepares source-complete, prepared/unposted close/reconciliation packets. It can apply established rules and propose evidence-based categories with rationale/confidence, but routes uncertain/new judgments. |
| `stackos.finance.billing-collections-operator` | Prepares/reconciles the exact approved invoice or received-payment recording action. It requires current-object recovery, fresh approval after material change, distinct finalize/send/report/attach/external-settlement gates, source/allocation matching and lifecycle-aware follow-up suppression. It never charges or moves money. |
| `stackos.finance.cashflow-preparer` | Prepares an external 13-week base/downside packet. Each week reconciles opening cash plus receipts minus cash payments to closing cash; a reviewed reserve is a once-only spendable-cash earmark, not bank cash. |
| `stackos.finance.tax-preparer` | Gathers current sources and prepares an obligation-matrix packet, including incomplete/awaiting-advisor work. CPA/EA review is required before adoption or reserve handoff, not before preparation; it never advises, files, remits, or moves money. |
| `stackos.finance.control-reviewer` | Independently reviews safe evidence, action grants, approval gates, and stopping boundaries. It is read-only and cannot self-approve or substitute for the owner/CPA/EA. |

`stackos.finance.department-orchestrator` is the corresponding main-agent skill
preset. It is the single high-reasoning owner of routing, one-writer external
materialization, specialist integration, and final claims. Before execution or
resume it rehydrates the effective workflow/extension, selected backend/workspace
and safe account refs, applicable workflow/route setup, actual record version/digest, provider
route, active grant, declared approval occurrence, action audit, handoffs, and
recovery state. It selectively dispatches roles: a complete routine receipt does
not require a committee. Raw finance contents stay external; workflow/tracker
outputs carry safe refs, bounded state, and recovery proof only.

Use the finance package's selected-workflow setup matrix. Receipt custody needs
workspace/writer/source route, not tax, advisor or bank setup. Reuse confirmed
defaults, ask only missing current-work questions, and preserve independent
partial work. Tax/business profiles are conditional context rather than blanket
activation requirements.

Dispatch includes a capability preflight of actual mounted tools, bound
project/run/step/account, active grants/approvals and required host-file access.
`recommended_tools` does not prove a particular session can execute. If a
specialist lacks capability, it returns a proposed packet; the capable main agent
executes with the same grant/approval and stable operation identity after checking
the prior executor has stopped and reconciling any unknown outcome. Never race
two executors, duplicate retries or broaden permissions. If neither can act,
report the precise missing capability. Required control review remains independent
and read-only; unavailable actual evidence leaves review pending.

For the initial local backend, `backend_key` is `local-json` and `finance.json`
under `local-json-v1` is the sole authoritative local financial record, including
mutable setup. `FINANCE.md` is guidance/navigation, reports are derived explanations,
and CSV is import/export, not another master. Originals stay immutable evidence;
providers own their actual state and JSON stores dated observations. Packet refs
resolve to stable JSON record IDs and scoped immutable versions, not separate
editable packet files. Authenticated `finance.status` provisions only an enabled
project's never-initialized managed store. Missing/corrupt existing data requires
repair; standalone helper initialization does not create its ownership metadata.

There is one writer per materialization:
specialists return packets, then the delegated sole writer compares actual
backend/account/object and document revision/hash before validating and atomically
replacing the complete JSON document and reading it back. A mismatch is a conflict,
not an overwrite. Document revision/hash is concurrency proof; approvals bind the
immutable proposal record/version/digest and material dependencies. Unrelated JSON
updates need reread/rebase, not fresh approval. Material scoped changes need a fresh
approval occurrence. One human decision
may cover distinct finalize/send gates only for the same immutable facts/version; both
technical gates remain recorded separately.

Billing approvals bind the external recipient-settings version/scope, including
primary/email hash, additional To/CC or `verified-none` versus `unknown`, verifier/
time/evidence and validity/recheck condition. Compare current primary hashes and
applicable external scope immediately before send. Reuse current verified setup;
reverify changed/expired/uncertain facts and obtain fresh approval after material
change. Unknown recipients block delivery, not drafts or settlement-only work.

Codex materialization lives in `.codex/agents/` with main guidance in
`.codex/orchestrator/finance-department-orchestrator.md`. Finance agent files omit
`model`, preserving the operator-selected model, and set only role effort: medium for
receipt/billing, high for bookkeeping/cashflow/tax, and xhigh for independent review.
This follows [official Codex subagent configuration guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents).

Finance presets are adapted like every bundled preset. They do not authorize a
new StackOS resource type, filesystem connector, scheduler, or finance core model.
The host filesystem is used only by the selected external-backend contract, while
provider credentials remain daemon-held and finance actions use their active workflow
grant and any technical approval gate the action declares.

For customers who pay directly, the existing receivables follow-up workflow has
an explicit `settlement-only` mode; `followup-only` remains the default. The
main agent matches external cleared-payment evidence to one invoice and obtains
review/approval before the billing operator reports and attaches a PaymentRecord,
attaches an existing succeeded PaymentIntent, or uses the separate full-balance
out-of-band option. Partial payments remain partial. Unknown outcomes, duplicate
source allocations, split payments and overpayments stop for reconciliation;
settlement-only never sends a reminder. The external invoice/payment record,
not a StackOS resource or role output, owns the amounts and allocation history.
Unknown PaymentRecord reports first inspect retained action audit/response files,
then independently retrieve a surviving known ref against the exact pre-report
UTF-8 `payment_reference_sha256` and current payment/account/allocation facts.
This digest is not a receipt-file hash. PaymentRecord listing is temporarily
unavailable in StackOS and rejects before dispatch with a reason; it is optional
recovery, not a prerequisite for ordinary follow-ups or known-ref settlement.
Missing verified refs hold for owner/provider resolution. Do not retry listing,
change credentials/URLs, or create a replacement report/key. Persist the verified
ref before attachment. The finance backend contract owns the recovery method.

## Tracker Use

All presets are expected to work through the existing StackOS tracker:

- planning agents prepare scoped tasks/tickets; the current workflow or
  assignment names the durable writer (the main agent for tracked delegation)
- explicit workflow intent takes precedence over direct tracker tasks; create or
  resolve the workflow-backed run plan before tracker ticket creation
- all discovery, design, delivery, verification, and closeout tickets for an
  explicit workflow request belong under the workflow task/run plan from the
  start
- support investigation agents create tasks only after explicit same-thread human
  instruction in the canonical Slack thread
- support investigation tasks/tickets preserve source, thread, conclusion,
  instruction, handoff, and delivery refs in tracker context or references
- dependencies are encoded so ready work and blockers are visible
- for workflow-backed child tickets, `run_plan_id` and `step_id` are
  attachment/provenance only; agents update child ticket status and evidence
  with `tracker.updateTicket` while reserving `runPlan.claimStep` and
  `runPlan.recordStep` for generated workflow step mirror tickets
- workflow-backed ticket creation must pass `run_plan_id` and `step_id`
  together or omit both; if an agent only has one, it should fetch
  `tracker.brief`, `tracker.get`, or `runPlan.get` to recover the workflow
  handoff before calling `tracker.createTicket`
- when an operator wants closed work continued or extended, agents call
  `tracker.reopen` with the task, run-plan, or linked run id plus a reason
- planning agents must dependency-bridge child tickets into the mirrored
  workflow step chain
- in engineering tracked delegation, the main agent assigns explicit ticket
  keys and owns lifecycle/evidence writes; delivery agents return candidate
  evidence and blockers rather than independently pulling or completing work
- verifier and reviewer agents compare completion claims with actual evidence
- reviewers verify evidence before closeout
- reviewer findings are claims to check against
  evidence, root cause, and user/business impact
- tracker truth reviewers check that durable state matches code, docs, tests,
  run-plan steps, and verification outcomes
- detached workflow step tickets versus child-ticket chains are blocking
  findings, not generic missing-dependency notes
- release agents compare signoff claims with tracker state

Planning agents should produce deliverable tickets with logical sequencing,
clear dependencies, no dangling loose ends, and concrete definition of done.
For workflow-backed work, their plan must include a graph check covering the
parent step ticket, every independent first child, terminal children, next-step
handoff, and detached branches. After creating or changing workflow-backed
tickets, call `tracker.get` with `run_plan_id` and `include_graph=true`;
repair warnings that hide required work or invalidate readiness claims, and
record non-blocking cleanup explicitly instead of freezing progress.

## Operations

Use these operations through MCP, REST, or CLI:

- `operation.list`: discover available StackOS operations before asking for
  exact schemas or invoking hidden toolbox tools
- `agentPreset.list`: discover available generic presets
- `agentPreset.describe`: read one preset and its adaptation contract
- `agentPreset.resolveForWorkflow`: resolve a workflow template into required
  and recommended agents plus skill requirements
- `resource.query`: read existing workflow resources such as
  `engineering-decision` and `engineering-evidence`
- `resource.upsert`, `decision.record`: write durable workflow evidence only
  from a started run-plan step with an explicit grant

Example:

```text
agentPreset.resolveForWorkflow({ "workflow_key": "core.project-memory-review" })
```

The response includes:

- workflow summary
- `agent_requirements`
- `skill_requirements`
- `skill_preset_requirements`
- resolved required/recommended/optional presets
- resolved required/recommended/optional skill presets
- unresolved preset refs, if any
- unresolved skill preset refs, if any
- setup guidance that reminds the caller to adapt presets and use tracker state

## Workflow Relationship

Workflow templates are inert reusable contracts. They do not act by themselves.
An agent selects a workflow template, resolves its preset/skill guidance, then
creates a concrete run plan with `runPlan.create`. Work execution then happens
through the run plan, granted tools, provider actions, and tracker tickets.
If the operator explicitly invoked a workflow, this run-plan selection must
happen before tracker task/ticket creation so delivery truth is not split across
a direct task and a later workflow task.

New workflow authoring is contract-driven today:

- validate with `workflowTemplate.validate`
- save project/user templates with `workflowTemplate.save`
- fork built-ins with `workflowTemplate.fork`
- create executable workflow state with `runPlan.create`

The UI can inspect and use templates, but it is not yet a full visual workflow
builder.
