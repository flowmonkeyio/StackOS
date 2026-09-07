# Minimal Agency and Client Project Setup

This package gives future agents enough business context to work from an agency
root or a standalone client-project workspace. It does not add an agency
management system, project delivery process, nested StackOS projects, or finance
storage.

## What gets set up

| Workflow | Useful result | Boundary |
| --- | --- | --- |
| [`agency.setup`](workflows/agency-setup.yaml) | One minimal agency profile: name, brief context, and supplied guidance or safe workspace reference. | No client-project creation or financial operation. |
| [`agency.project-setup`](workflows/project-setup.yaml) | One client engagement: stable identity, name, client reference, short description, optional agency association and supplied guidance. | No delivery workflow, new StackOS project, or financial record. |

Both use the existing [`stackos.workflow-orchestrator`](../core/skill-presets/workflow-orchestrator.yaml)
main-agent skill preset and the installed `stackos:stackos` mechanics skill.
Neither needs a new specialist agent, agency-manager agent, or project-manager
agent. The main agent handles the small amount of setup reasoning and mechanical
persistence. Presets are adapted to the host; they are not daemon-run agents.

The [manifest](plugin.yaml) owns two nonfinancial resource schemas:

- `agency-profile`: agency identity and compact operating context.
- `project-context`: a client-project/engagement identity and compact context.

Use one agency profile in its owning StackOS project. A client can have several
engagements with different stable references. An engagement may exist without
an agency profile. Discover and reuse existing references; for a new record the
agent selects a stable reference once from the confirmed identity. The operator
should not have to invent opaque reference strings or repeat known details.

## Scope and source of truth

A StackOS **Project** is a workspace/security boundary. A **client engagement**
is a descriptive business record inside the currently bound Project. An agency
can retain many engagement records while its finance agent stays at the agency
root. This is an association, not runtime nesting or inherited permissions.

| Information | Authoritative owner |
| --- | --- |
| Workspace binding, enabled plugins, connections, workflow defaults and audit | Existing StackOS project primitives |
| Nonfinancial agency/client-engagement context | The agency plugin's project-scoped resources |
| Financial facts, mutable finance settings, approvals and project attribution | The selected external finance backend |
| Actual provider state | The provider, with dated observations in the finance backend |
| Agent operating method | Workflow/preset definitions and their host-local adaptations |

`agency_profile_ref` links only to context in the same bound StackOS project.
Resolve a supplied or preserved association there before writing; a missing or
unresolved profile holds that update. Omitting the association for a standalone
engagement needs no agency profile.
`finance_workspace_association_ref` is an optional navigation pointer, not a
second backend selector or mutable finance configuration. Reconcile it with the
effective finance setup and actual external owner before using it. No amounts,
invoices, receipt contents, balances, tax calculations, credentials, or financial
settings belong in agency resources.

The host's directory access is separate from StackOS's binding. A project record,
folder relationship or finance pointer grants no cross-project MCP calls,
credentials, or filesystem access. If a client needs a separately bound StackOS
workspace, set it up explicitly from that workspace in a separate authorized
session. These workflows do not switch the agency session or create sibling
bindings. Client-facing agents receive only their authorized context, never the
agency's full finance data by association.

## Setup versus operation

Use `workflowTemplate.authoringGuide` as the canonical setup protocol. The agency
package follows it rather than defining a separate bootstrap mechanism.

1. **Infrastructure setup:** bind the intended workspace, resolve the selected
   workflow/extension and generic orchestrator, adapt host guidance, and perform
   structural plus strict read-only validation. Do not create a run, tracker
   task, resource, or business output merely to install the capability.
2. **Agency or engagement onboarding:** after explicit authorization to save
   context, resolve the minimal supplied facts and stable identity. Strictly
   validate and start this workflow's onboarding run. Inventory existing
   records, prepare the reviewed update, persist only through its resource-write
   step, and read back the exact record before claiming success.
3. **Finance setup or operation:** a separately authorized handoff using the
   existing finance workflows. No agency workflow starts it implicitly.

On rerun, reuse the stable resource identity and preserve existing optional
guidance/associations unless the operator explicitly changes or clears them.
Resource upsert replaces its document; merge the approved update with the current
record before writing. If the current identity or agency association conflicts,
hold that change and ask one bounded question. Do not silently create another
agency profile or use a new engagement reference to escape an ambiguity.

Read back the saved identity and supplied/preserved fields. An unavailable or
ambiguous readback is unfinished setup, not success. A partial context page is
not proof that a record does not exist; resolve the exact reference through the
available resource query path or stop with the missing evidence.

## Finance with or without agency

Finance is independently usable. In a standalone business folder, use the
[finance package](../finance/README.md) directly. An agency may use the same
finance department from its root:

```text
business or agency root
  finance/finance.json        one authoritative local financial master
  finance/attachments/        original financial evidence
  client engagements         descriptive context, no financial master per project
```

This illustrates ownership; neither agency workflow creates these folders.
Finance initialization follows the authorized host-owned finance setup contract.
Do not copy an empty template over existing financial files.

The handoff names `setup_existing` and the selected existing workflow key(s):

- `finance.receipt-intake`
- `finance.bookkeeping-close`
- `finance.payment-request`
- `finance.payment-request-followups`
- `finance.cashflow-management`
- `finance.tax-estimates`

Resolve the deduplicated union of the selected finance roles and the existing
[`stackos.finance.department-orchestrator`](../finance/skill-presets/finance.yaml).
Reuse its [specialist presets](../finance/agent-presets/finance.yaml); agency
setup adds no finance agent roles. Ask only for the selected workflow's missing
prerequisites, following the [setup matrix](../finance/references/backend-contract.md#setup-and-execution).
Receipt-only setup requires neither agency/project metadata nor tax/advisor/bank
inputs. Setting up finance capability does not send invoices, acknowledge mail,
record payments, or perform tax work.

The external finance schema supports optional `external_project_ref` on a
single-project billing version or prepared bookkeeping record. Verify it against
the nonfinancial engagement identity and client/business ownership; do not infer
it merely from a customer mapping. The same client can have multiple projects.
Company-wide expenses remain unassigned; ambiguous/shared attribution stays
absent with a gap. No automatic allocation, profitability engine, duplicated
amount, or project-level financial master is introduced. Billing attribution is
part of the immutable approval digest. See the
[business/project scope contract](../finance/references/backend-contract.md#business-and-project-scope).

## Verification and delivery

Focused package tests live in `tests/unit/test_agency_plugin.py` and
`tests/integration/test_repositories/test_agency_setup.py`. They use synthetic
identities and temporary repository sessions for loading, validation, preset
resolution, grants, persistence/readback, reruns, same-client engagements and
scope isolation. The finance schema/workspace tests cover optional attribution
and digest/approval behavior. Independent review additionally checks definition
patterns and agent discovery from realistic requests.

These are source-package checks, not proof that an already running installed
app has loaded the new definitions. App rebuild/activation and real business
setup require their own authorized step; no restart or provider action is part
of this package's source verification.
