# StackOS Documentation Index

Use this index before editing StackOS. The goal is to make the right source
obvious without loading every document.

## Read This When

| Work | Primary Docs |
| --- | --- |
| Helping a person take the first steps after installing StackOS | [Public getting-started guide](https://stackos.flowmonkey.io/getting-started); use [`setup.md`](./setup.md) only for the technical install and repair contract |
| Installing, starting, repairing, or lifecycle-smoke testing StackOS | [`setup.md`](./setup.md), [`upgrade.md`](./upgrade.md), [`lifecycle-smoke-verification.md`](./lifecycle-smoke-verification.md), [`security.md`](./security.md) |
| Building the macOS desktop app or installer | [`desktop-distribution.md`](./desktop-distribution.md), [`setup.md`](./setup.md), [`upgrade.md`](./upgrade.md), [`security.md`](./security.md), [`release-signoff.md`](./release-signoff.md) |
| Renaming this repository for release | [`repository-rename.md`](./repository-rename.md), [`setup.md`](./setup.md), [`upgrade.md`](./upgrade.md) |
| Understanding the product model | [`architecture.md`](./architecture.md), [`operations.md`](./operations.md), [`agent-operating-model.md`](./agent-operating-model.md) |
| Product direction or roadmap priorities | [`product-direction.md`](./product-direction.md), [`architecture.md`](./architecture.md), [`agent-operating-model.md`](./agent-operating-model.md) |
| Auditing agent-facing flows and release clarity | [`agent-operating-model.md`](./agent-operating-model.md), [`workflow-templates.md`](./workflow-templates.md), [`operations.md`](./operations.md); use [`agent-experience-audit.md`](./agent-experience-audit.md) only as the May 2026 historical baseline |
| Setting up generic agents or workflow roles | [`agent-presets.md`](./agent-presets.md), [`agent-operating-model.md`](./agent-operating-model.md), [`workflow-templates.md`](./workflow-templates.md), [`task-tracker.md`](./task-tracker.md) |
| Adding or changing callable behavior | [`operations.md`](./operations.md), [`action-executor.md`](./action-executor.md), [`extending.md`](./extending.md) |
| Passing a tenant/customer secret in an action payload | [`action-executor.md`](./action-executor.md), [`security.md`](./security.md) |
| Adding or using browser automation | [`browser-automation.md`](./browser-automation.md), [`operations.md`](./operations.md), [`setup.md`](./setup.md), [`security.md`](./security.md) |
| Adding or changing task/ticket tracking | [`task-tracker.md`](./task-tracker.md), [`run-plans.md`](./run-plans.md), [`operations.md`](./operations.md) |
| Adding providers, auth, or credentials | [`auth-providers.md`](./auth-providers.md), [`oauth-provider-setup.md`](./oauth-provider-setup.md), [`security.md`](./security.md), [`integration-contracts/AGENTS.md`](./integration-contracts/AGENTS.md) |
| Adding or changing communications, chat, email, targets, or memberships | [`integration-contracts/communications.md`](./integration-contracts/communications.md), [`operations.md`](./operations.md), [`resources-and-artifacts.md`](./resources-and-artifacts.md) |
| Adding or changing plugins | [`plugins.md`](./plugins.md), [`extending.md`](./extending.md), [`workflow-templates.md`](./workflow-templates.md) |
| Building or changing workflow packages or templates | [`workflow-templates.md`](./workflow-templates.md), [`plugins.md`](./plugins.md), [`run-plans.md`](./run-plans.md), [`agent-presets.md`](./agent-presets.md), [`project-memory.md`](./project-memory.md) |
| Changing resources or artifacts | [`resources-and-artifacts.md`](./resources-and-artifacts.md), [`project-memory.md`](./project-memory.md) |
| Changing UI | [`ui-design-system.md`](./ui-design-system.md), [`ui-component-inventory.md`](./ui-component-inventory.md), [`ui-page-layout-map.md`](./ui-page-layout-map.md), [`ui-screen-redesign.md`](./ui-screen-redesign.md) |
| Diagnosing slow desktop or local API loads | [`performance.md`](./performance.md), [`setup.md`](./setup.md) |
| Reviewing provider contracts | [`integration-contracts/`](./integration-contracts/) |
| Selecting or integrating image/video generation providers | [`integration-contracts/media-generation.md`](./integration-contracts/media-generation.md), [`action-executor.md`](./action-executor.md) |
| Before-commit or release validation | [`release-signoff.md`](./release-signoff.md), [`lifecycle-smoke-verification.md`](./lifecycle-smoke-verification.md) |

## Canonical Rules

- StackOS stores project state, validates explicit inputs, resolves
  daemon-held credentials, executes configured calls, and records audit.
- Agents and operators make strategy decisions. In StackOS docs, an agent is
  the MCP/tool consumer; repository filesystem access is a separate host
  capability, not something StackOS grants. Tools and connectors stay
  decision-free.
- Register callable behavior once as an operation or plugin action contract;
  expose it through MCP, REST, CLI, and UI docs from that contract.
- The public [getting-started guide](https://stackos.flowmonkey.io/getting-started)
  is the user-facing source of truth after installation. The desktop app links
  to that page, and agents fetch its Markdown through `guide.gettingStarted`
  and send people back to the same page. Do not maintain a second onboarding
  walkthrough in desktop UI, skills, or technical docs.
- Direct MCP tools are only for generic StackOS primitives. Provider/vendor
  calls belong in plugin actions executed through `toolbox.call` for
  `action.run` on one explicit action, or through `action.execute` inside a
  granted run-plan step.
- Provider credentials stay daemon-held; agents receive only safe provider
  keys, account refs, auth status, scopes, diagnostics, and opaque
  `credential_ref` values. Authorized non-auth string payload values use the
  write-only MCP `secret.set` operation and exact `$secret_ref` action markers,
  never a second provider Connection.
- Agents should resolve known provider targets with `toolProfile.resolve`
  before broad auth/profile discovery.
- Communications are provider-neutral state plus explicit provider actions. Use
  one-brain ingress, surface intent/data-scope metadata, allowlisted invokers,
  named targets, and route policy instead of provider-specific bot decisions.
- Task tracking is project-scoped work state for agents and human navigation.
  Workflow runs mirror into tasks/tickets automatically, and manual agent work
  uses `tracker.*` operations. The tracker stores state; agents decide the work.
- Project bootstrap is MCP-native. Agents start with `workspace.startSession`;
  when a reliable repo/directory identity exists, it creates or reuses one
  project for that workspace root and records the daemon-owned binding without
  writing repo files. Desktop/global hosts with no identity stay unbound until
  the agent explicitly chooses an existing `workspace_alias` or supplies
  business project metadata. `workspace.resolve` remains the read-only
  diagnostic path, and `project.*` discovery is available through
  `toolbox.call` for intentional setup while project switching and deletion
  stay admin-only.
- Agent presets are generic role contracts for MCP/tool consumers. They must be
  adapted to project rules, stack, tracker workflow, references, and signoff
  before use. Workflow templates can recommend host-side skills such as
  `stackos:stackos` to teach StackOS MCP, workflows, run plans, tasks, tickets,
  dependencies, and evidence.
- Engineering, branding, marketing, SEO, media buying, GTM, publishing,
  communications, and utilities are plugins. Core StackOS remains
  domain-agnostic.

## Verification Commands

For changes that touch setup, actions, operation adapters, MCP, REST, CLI, UI
wiring, provider contracts, or agent-facing docs, use the canonical signoff:

```bash
make signoff
```

See [`release-signoff.md`](./release-signoff.md) for the agent-flow test matrix,
faster targeted slices, and setup smoke commands.

For documentation-only edits:

```bash
git diff --check
rg "StackOS" AGENTS.md README.md docs plugins -n
rg "stackos" docs README.md plugins/stackos -n
```

Run targeted tests when documentation changes command contracts, generated API
expectations, operation examples, or UI integration notes:

```bash
uv run pytest tests/unit/test_operations_registry.py tests/unit/test_cli_ops.py -q
pnpm --dir ui type-check
```

For macOS desktop packaging changes, run the lightweight scaffold check during
iteration:

```bash
make desktop-doctor
```

## Cleanup Rule

Do not keep temporary planning docs, migration audits, or old workflow notes in
the active docs set after their current state has been merged into canonical
docs. If historical context is needed, move only the durable facts into the
relevant canonical document.
