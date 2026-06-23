# StackOS Product Roadmap

Date: 2026-06-23

This roadmap describes what StackOS should build next. It is intentionally
practical: polish the local runtime, make setup reliable, give agents better
operating tools, and then expand the workflow and integration surface.

## Product Thesis

StackOS is a local-first operating layer for agents that do real business work.
Agents are the primary operators of execution. Humans set up credentials,
approve risky work when policy requires it, inspect tracker state, and use the
desktop app to understand or repair what happened.

The product should feel like this:

```text
Install once
-> connect credentials once
-> choose a useful workflow
-> let agents execute safely
-> inspect notifications, tracker state, and audit when needed
```

The UI is not a workflow-builder clone. It is a local desktop console for setup,
readiness, visibility, approvals, recovery, and audit.

## Priority Order

### 1. Infrastructure And Lifecycle Fundamentals

The first priority is making StackOS boring to install, upgrade, repair, and
remove. Convenience starts before the first workflow.

Phase 1 should cover the whole local lifecycle, but only to the depth needed
for a dependable local product. Do the core path first; leave release-channel
hardening and advanced recovery for later unless they block actual use.

Needed work now:

- One reliable macOS install path that initializes local state, installs the
  daemon, registers supported agent clients, installs plugin/skill assets,
  starts the daemon, and opens the desktop app.
- Desktop first-run status for daemon health, project binding, installed agent
  clients, plugin assets, provider setup, and next required action.
- Idempotent repair from the desktop app: rerun install, repair launchd,
  refresh plugin assets, repair agent-client registration, restart daemon, and
  rerun doctor.
- Upgrade flow with release notes, migration status, daemon restart, and
  rollback guidance.
- Clean uninstall that explains what will be removed and what will be
  preserved: app bundle, launchd job, agent-client registrations, plugin
  assets, local DB, generated assets, seed, auth token, and credentials.
- Minimal backup/export before serious upgrades, with full restore treated as a
  follow-up once the export contract is solid.
- Doctor output written for both humans and agents, with exact repair steps.
- Clear release/update expectations for local and GitHub distribution. Signed
  macOS distribution and a release-grade update channel are later hardening
  unless public distribution requires them immediately.

Setup should not feel like developer onboarding. It should feel like a product.

Lifecycle contract:

- Install / first launch. User: install the desktop app, let it initialize or
  repair the local daemon, then land on readiness. Data: desktop calls the
  canonical local install path, which initializes local state, launchd, plugin
  assets, supported agent-client registrations, and daemon startup.
- Credential setup. User: connect providers once from setup or connections.
  Data: the daemon stores provider/account metadata and local credential refs;
  agents receive only safe refs, scopes, status, and repair hints.
- Agent-client registration. User: see whether Codex, Claude, and other
  supported clients are ready. Data: install/repair owns MCP registration, and
  agents start with `workspace.startSession` against the workspace-bound
  project.
- Normal operation. User: choose or request a workflow, then inspect runs,
  blockers, approvals, tracker state, and audit. Data: templates create run
  plans, steps freeze grants, the daemon resolves credentials internally, and
  tracker/audit records become the durable evidence.
- Doctor / repair. User: click repair or let an agent follow structured repair
  guidance. Data: diagnostics group daemon, state, launchd, desktop, MCP,
  plugin, and provider checks; safe repairs call the canonical lifecycle path.
- Upgrade / update. User: see update availability or upgrade instructions,
  release notes, migration risk, restart expectations, and post-update health.
  Data: the upgrade path preserves local state, migrations run locally, and
  postflight doctor confirms readiness. A signed update feed is hardening, not
  a dependency for the basic lifecycle.
- Backup / restore. User: export enough local state before serious upgrades or
  a machine move to avoid lock-in or silent loss. Data: the first version can
  be a constrained export with explicit exclusions and verification. Full
  restore, secret migration, and rollback can come after the export contract is
  proven.
- Uninstall. User: choose default uninstall that preserves user-owned state, or
  a separate full cleanup that is clearly destructive. Data: app/service/client
  registrations are removed while DB, seed, token, credentials, and backups are
  preserved unless the user explicitly asks for full cleanup.
- Release / distribution. User: install from the supported release path and
  understand how updates work. Data: release docs name the current supported
  path, packaging checks, rollback notes, and known gaps. Signing,
  notarization, and a managed update feed are release hardening, not day-one
  lifecycle mechanics.

The data-flow invariant is:

```text
Desktop / CLI
-> canonical lifecycle operation
-> daemon-owned local state
-> structured readiness and diagnostics
-> agent-safe refs and repair hints
-> tracker and audit evidence
```

Current Phase 1 tracking:

| Action item | Status | Tracker key | Notes |
| --- | --- | --- | --- |
| Lifecycle inventory and baseline | Done | `phase1-lifecycle-inventory` | Existing install, repair, upgrade, uninstall, desktop, and doctor foundations have been audited. |
| Public roadmap status tracking | Done | `phase1-roadmap-statuses` | This roadmap now carries the lifecycle contract and action-item status table. |
| Install and first-run readiness | Not started | `phase1-install-first-run` | Foundations exist; product-grade readiness, repair hints, and targeted tests still need delivery. |
| Repair and doctor lifecycle | Not started | `phase1-repair-doctor` | Doctor and repair paths exist; output grouping, desktop UX, and agent-readable repair guidance need polish. |
| Upgrade and desktop update lifecycle | Not started | `phase1-upgrade-update` | Upgrade docs and update plumbing exist; production channel, migration, restart, and signoff flow need polish. |
| Uninstall and state preservation | Not started | `phase1-uninstall-preserve` | Uninstall exists; default preservation and explicit full-cleanup behavior need product-level clarity. |
| Minimal backup/export before risky changes | Not started | `phase1-backup-restore` | This is a real gap; start with a constrained export and explicit exclusions, then add full restore later. |
| Public signing and managed update hardening | Later | `phase1-macos-distribution-signing` | Important before broad public distribution, but should not block the local lifecycle MVP. |
| Lifecycle smoke verification | Not started | `phase1-lifecycle-verification` | Start with a small proof set across install, repair, upgrade, uninstall, and export; expand only as the lifecycle grows. |

`Not started` means the product-grade delivery ticket is open. It does not mean
there is no existing foundation in the repository. `Later` means the item is
tracked and relevant, but it should not block the core local lifecycle.

### 2. Agent Guidance Activation And Workflow Reliability

Agents should not need a human to translate the StackOS operating model at run
time. The detailed guidance should live in the layered agent system: workflow
templates, agent presets, skill presets, host-local agents, and host-local
orchestrator guidance. Runtime tools should activate and point to that guidance
without overfeeding every tool response.

Needed work:

- One canonical native agent happy path:

  ```text
  workspace.startSession
  -> discover operation/action/workflow choices
  -> readiness.check
  -> direct action or workflow run plan
  -> runPlan.claimStep
  -> action.execute / resource writes / artifact writes
  -> runPlan.recordStep
  -> tracker.verify
  ```

- Guidance activation that stays compact:
  - workflow templates name required/recommended roles
  - agent presets define role behavior and project-adaptation contracts
  - skill presets define main-agent/orchestrator behavior
  - host-local agents materialize those presets when the host supports them
  - MCP/toolbox responses return scoped ids, refs, warnings, next calls, and
    pointers to the relevant operation/preset/workflow guidance
- Run-plan validation that separates structural validity from executable
  readiness.
- Suggested grant skeletons for template-derived run plans.
- Grant-denied repair payloads that include the missing tool, action ref,
  active step, allowed grants, and safe retry shape.
- Compact responses that preserve the ids, refs, warnings, and next calls an
  agent needs for the next step.
- Clear separation between:
  - direct action for one explicit operation
  - workflow run plan for multi-step audited work
  - tracker for durable work navigation and evidence
- Codex and Claude convenience tests that prove agents can install, connect,
  discover the attached guidance, execute, recover, and verify without custom
  explanation.

This is product work, not only documentation. If agents frequently need the
human to explain which StackOS tool to call next or which attached guidance
applies, the operating layer is not ready.

### 3. Desktop UX And Notifications

The desktop app should be the normal way to run and inspect StackOS on macOS.
It should help humans supervise agent work without turning humans into manual
workflow operators.

Needed work:

- Native macOS notifications for credential setup, approval needed, blocked
  runs, completed runs, daemon down, upgrade available, provider auth expired,
  and high-risk action attempts.
- In-app notification center so attention-worthy events have durable history.
- Work Queue view for active agent work, blockers, requests, approvals, and
  tracker next items.
- Menu bar or dock status for daemon health, active runs, and blocked work.
- Run detail view that answers: what is the agent doing, what is it allowed to
  touch, which safe credential ref is used, what happened, what evidence
  exists, and what needs repair.
- Setup/readiness surfaces that point the human to the exact missing provider,
  credential, agent client, plugin asset, or daemon repair.

Design rule: humans set up, approve, inspect, and repair. Agents execute.

### 4. Out-Of-The-Box Workflows

The fastest path to user value is not an empty platform. It is a small catalog
of workflows that work reliably on day one.

High-value starter workflows:

- Inbox triage and reply draft.
- Outbound notification.
- Customer feedback intake -> support investigation -> delivery handoff.
- SEO keyword research.
- SEO content refresh.
- Weekly site/search performance report.
- GitHub issue to tracked delivery.
- Release signoff.
- Content publish with approval.
- CRM lead enrichment and follow-up draft.
- Meeting notes to task tracker.

Each workflow should ship with:

- required integrations
- optional integrations
- setup checklist
- sample input
- expected tracker shape
- run-plan steps
- granted action skeletons
- evidence expectations
- failure and repair paths

Optimize for "I can use this today" before optimizing for breadth.

### 5. Executable Integration Depth

StackOS should expand integrations by completing useful work paths, not by
collecting logos.

Priority integration areas:

- Google Workspace: Gmail, Calendar, Drive, Docs, Sheets.
- Microsoft 365: Outlook Mail, Calendar, OneDrive, Teams.
- Work tracking: GitHub, Linear, Jira.
- Communications: Slack, Telegram, SMTP/IMAP, Discord.
- CRM/GTM: HubSpot, Salesforce, Pipedrive.
- Publishing: WordPress, Ghost, Webflow, Shopify content surfaces.
- Search and marketing: Google Search Console, GA4, DataForSEO, Ahrefs.
- Storage and knowledge: Notion, Airtable, Google Drive, local files.

The integration bar:

- daemon-side credential resolution
- action schema with examples
- validation and dry-run where possible
- action-call audit
- clear risk level
- file-backed output for large responses
- explicit readiness and repair guidance
- pagination, rate-limit, and provider-error behavior
- workflow coverage that proves the integration is useful

The right metric is executable workflows per integration.

### 6. Local Plugins And SDK

StackOS needs safe local extensibility because users will have custom business
tools before built-in coverage is complete.

Needed work:

- Local plugin install from a directory.
- Plugin manifest validation.
- Plugin trust state: local draft, trusted local, signed/package, disabled.
- Clear separation between planning-only metadata, internal actions, external
  provider actions, browser-assisted actions, and project-local HTTP actions.
- SDK for defining providers, auth methods, actions, schemas, resources,
  workflow templates, readiness checks, examples, agent guidance, and tests.
- Agent-authored plugin draft flow:
  1. agent creates local plugin draft
  2. StackOS validates manifest and schemas
  3. plugin is disabled by default
  4. human reviews trust and risk
  5. plugin is promoted for project use

Agent-written plugins must not bypass daemon-side credential resolution,
redaction, idempotency, grants, or audit.

### 7. Local Semantic Search

Local semantic search should be a real indexing subsystem, not prompt memory.
It should answer what the project already knows and where that knowledge came
from.

Useful search scopes:

- StackOS resources, artifacts, decisions, learnings, action-call summaries,
  run-plan summaries, tracker tasks, and tickets.
- Local project files selected by the user or workspace policy.
- Stored communications that StackOS ingressed or sent.
- Provider-fetched documents only when explicitly imported into StackOS.

Implementation direction:

- Local indexer process owned by the daemon.
- SQLite-backed metadata plus a local vector index.
- Local embeddings by default when feasible.
- Cloud embeddings only with explicit provider setup and audit.
- Incremental indexing with file/resource checksums.
- Permission-aware retrieval across project, provider, surface, workflow, and
  credential boundaries.
- Explainable result packets: source ref, snippet, timestamp, scope, score, and
  why the result is visible.

Hard requirements:

- Do not index secrets.
- Do not index arbitrary home-directory files by default.
- Do not let semantic search bypass run-plan grants or communication surface
  boundaries.

### 8. Website, Theming, And Look And Feel

There are two surfaces:

- Product UI: dense, operational, local-admin and observability focused.
- Public website: explains the product, proves trust, and gets users to
  install.

They should share brand identity but not the same layout language.

Product UI direction:

- Follow the existing design system: calm, dense, predictable, desktop-first.
- Keep semantic tokens as the source of truth.
- Show status, readiness, history, tracker state, and audit before decoration.
- Avoid promotional layouts inside the app.

Website direction:

- First viewport should make the product concrete: StackOS running locally,
  connected agents, active workflow, tracker, and credential-safe action audit.
- Lead with the practical promise:

  ```text
  Install StackOS locally. Connect your tools once. Let agents run real work
  with credentials they never see.
  ```

- Show proof flows, not abstract diagrams:
  - agent starts a workflow
  - run-plan step grants action
  - daemon resolves credential
  - action executes
  - tracker and audit update
  - desktop notification appears

Needed design work:

- Shared brand tokens for website and app.
- Consistent icon, app bundle icon, favicon, and social preview imagery.
- Product screenshot/story library based on real StackOS UI states.
- Release-ready visual language for docs, installer, desktop app, and website.

### 9. Optional Server Persistence

Server persistence comes last. It can help backup, sync, release channels, and
multi-device continuity, but it must not weaken the local-first model.

Possible server-backed capabilities:

- encrypted backup of local StackOS state
- update metadata and release channels
- workflow/plugin catalog distribution
- optional notification relay
- team/device registry
- remote status dashboard
- safe artifact sync

Boundaries:

- Provider secrets stay local unless the user explicitly chooses a managed
  custody mode.
- The server is not required for local workflows.
- Server sync is auditable, reversible, and clearly scoped.
- Local backup/restore must exist before server sync becomes a product
  dependency.

The clean framing is local-first with optional continuity.

## Sequencing

### Phase 1: Polish The Local Product

- Finish first-run setup and repair.
- Make install, upgrade, uninstall, doctor, backup, and restore product-grade.
- Ship signed macOS distribution and update flow.
- Improve desktop readiness and status surfaces.

### Phase 2: Prove Agent Guidance Activation

- Publish and test the native agent happy path.
- Improve MCP/toolbox operation guidance.
- Make run-plan validation reflect executable readiness.
- Normalize grant-denied and setup-repair payloads.
- Add Codex and Claude convenience tests.

### Phase 3: Make The Desktop Console Useful Daily

- Native macOS notifications.
- In-app notification center.
- Work Queue view.
- Better run detail and tracker navigation.
- Approval and blocker visibility.

### Phase 4: Ship Convenience Workflows

- Select the first starter workflows.
- Make each one executable end to end.
- Add setup checklists, grant skeletons, examples, and repair paths.
- Verify workflows with Codex and Claude.

### Phase 5: Expand Executable Work

- Prioritize integration depth required by the starter workflows.
- Add high-value business providers where they unlock complete workflows.
- Tighten pagination, rate-limit, error, and budget behavior.
- Keep deferred/project-local actions explicit until they are real.

### Phase 6: Extend The Platform

- Local plugin install.
- Plugin SDK and trust model.
- Local semantic search.
- Website and shared brand system.

### Phase 7: Add Optional Continuity

- Encrypted backup and restore.
- Optional server sync.
- Notification relay.
- Team/device continuity.
- Remote status only after local custody remains clear.

## Product Principles

- Agents operate; humans set up, approve, inspect, and repair.
- Local custody is a product feature, not an implementation detail.
- Setup and recovery must be as polished as execution.
- Do not overfeed agents through tool responses. Keep detailed behavior in
  skills, presets, workflow contracts, and project-local agent guidance; use
  runtime responses for compact routing, refs, warnings, and repair hints.
- Workflows should be convenient before they are infinitely flexible.
- Every action should answer who, what, when, why, and with which safe
  credential ref.
- A small number of excellent integrations beats a large planning-only catalog.
- The UI should make agent behavior legible, not replace the agent.
- Local plugins must go through the same validation, auth, grant, and audit
  boundaries as built-in plugins.
- Website claims should be narrower than the architecture and backed by real
  product flows.
