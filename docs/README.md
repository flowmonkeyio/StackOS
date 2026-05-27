# StackOS Documentation Index

Use this index before editing StackOS. The goal is to make the right source
obvious without loading every document.

## Read This When

| Work | Primary Docs |
| --- | --- |
| Installing, starting, or repairing StackOS | [`setup.md`](./setup.md), [`upgrade.md`](./upgrade.md), [`security.md`](./security.md) |
| Renaming this repository for release | [`repository-rename.md`](./repository-rename.md), [`setup.md`](./setup.md), [`upgrade.md`](./upgrade.md) |
| Understanding the product model | [`architecture.md`](./architecture.md), [`operations.md`](./operations.md), [`agent-operating-model.md`](./agent-operating-model.md) |
| Auditing agent-facing flows and release clarity | [`agent-experience-audit.md`](./agent-experience-audit.md), [`agent-operating-model.md`](./agent-operating-model.md), [`operations.md`](./operations.md) |
| Setting up generic agents or workflow roles | [`agent-presets.md`](./agent-presets.md), [`agent-operating-model.md`](./agent-operating-model.md), [`workflow-templates.md`](./workflow-templates.md), [`task-tracker.md`](./task-tracker.md) |
| Adding or changing callable behavior | [`operations.md`](./operations.md), [`action-executor.md`](./action-executor.md), [`extending.md`](./extending.md) |
| Adding or changing task/ticket tracking | [`task-tracker.md`](./task-tracker.md), [`run-plans.md`](./run-plans.md), [`operations.md`](./operations.md) |
| Adding providers, auth, or credentials | [`auth-providers.md`](./auth-providers.md), [`security.md`](./security.md), [`integration-contracts/AGENTS.md`](./integration-contracts/AGENTS.md) |
| Adding or changing communications, chat, email, targets, or memberships | [`integration-contracts/communications.md`](./integration-contracts/communications.md), [`operations.md`](./operations.md), [`resources-and-artifacts.md`](./resources-and-artifacts.md) |
| Adding or changing plugins | [`plugins.md`](./plugins.md), [`extending.md`](./extending.md), [`workflow-templates.md`](./workflow-templates.md) |
| Changing workflow templates or runs | [`workflow-templates.md`](./workflow-templates.md), [`run-plans.md`](./run-plans.md), [`project-memory.md`](./project-memory.md) |
| Changing resources or artifacts | [`resources-and-artifacts.md`](./resources-and-artifacts.md), [`project-memory.md`](./project-memory.md) |
| Changing UI | [`ui-design-system.md`](./ui-design-system.md), [`ui-component-inventory.md`](./ui-component-inventory.md) |
| Reviewing provider contracts | [`integration-contracts/`](./integration-contracts/) |
| Before-commit or release validation | [`release-signoff.md`](./release-signoff.md) |

## Canonical Rules

- StackOS stores project state, validates explicit inputs, resolves
  daemon-held credentials, executes configured calls, and records audit.
- Agents and operators make strategy decisions. In StackOS docs, an agent is
  the MCP/tool consumer; repository filesystem access is a separate host
  capability, not something StackOS grants. Tools and connectors stay
  decision-free.
- Register callable behavior once as an operation or plugin action contract;
  expose it through MCP, REST, CLI, and UI docs from that contract.
- Direct MCP tools are only for generic StackOS primitives. Provider/vendor
  calls belong in plugin actions executed through `action.run` for one explicit
  action or `action.execute` inside a granted run-plan step.
- Agents never receive secrets. They receive safe provider keys, account refs,
  auth status, scopes, diagnostics, and opaque `credential_ref` values.
- Agents should resolve known provider targets with `toolProfile.resolve`
  before broad auth/profile discovery.
- Communications are provider-neutral state plus explicit provider actions. Use
  one-brain ingress, surface intent/data-scope metadata, allowlisted invokers,
  named targets, and route policy instead of provider-specific bot decisions.
- Task tracking is project-scoped work state for agents and human navigation.
  Workflow runs mirror into tasks/tickets automatically, and manual agent work
  uses `tracker.*` operations. The tracker stores state; agents decide the work.
- Project bootstrap is MCP-native. Agents start with `workspace.resolve`; when
  unbound, `workspace.bootstrap` explicitly creates or reuses one project for
  the current workspace root and records the daemon-owned binding. `project.*`
  discovery is available for intentional setup, while project switching and
  deletion stay admin-only.
- Agent presets are generic role contracts for MCP/tool consumers. They must be
  adapted to project rules, stack, tracker workflow, references, and signoff
  before use. Workflow templates can recommend host-side skills such as
  `stackos:stackos` to teach StackOS MCP, workflows, run plans, tasks, tickets,
  dependencies, and evidence.
- SEO, media buying, GTM, publishing, and utilities are plugins. Core StackOS
  remains domain-agnostic.

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

## Cleanup Rule

Do not keep temporary planning docs, migration audits, or old workflow notes in
the active docs set after their current state has been merged into canonical
docs. If historical context is needed, move only the durable facts into the
relevant canonical document.
