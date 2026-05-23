# StackOS Documentation Index

Use this index before editing StackOS. The goal is to make the right source
obvious without loading every document.

## Read This When

| Work | Primary Docs |
| --- | --- |
| Installing, starting, or repairing StackOS | [`setup.md`](./setup.md), [`upgrade.md`](./upgrade.md), [`security.md`](./security.md) |
| Understanding the product model | [`architecture.md`](./architecture.md), [`operations.md`](./operations.md) |
| Adding or changing callable behavior | [`operations.md`](./operations.md), [`action-executor.md`](./action-executor.md), [`extending.md`](./extending.md) |
| Adding providers, auth, or credentials | [`auth-providers.md`](./auth-providers.md), [`security.md`](./security.md), [`integration-contracts/AGENTS.md`](./integration-contracts/AGENTS.md) |
| Adding or changing plugins | [`plugins.md`](./plugins.md), [`extending.md`](./extending.md), [`workflow-templates.md`](./workflow-templates.md) |
| Changing workflow templates or runs | [`workflow-templates.md`](./workflow-templates.md), [`run-plans.md`](./run-plans.md), [`project-memory.md`](./project-memory.md) |
| Changing resources or artifacts | [`resources-and-artifacts.md`](./resources-and-artifacts.md), [`project-memory.md`](./project-memory.md) |
| Changing UI | [`ui-design-system.md`](./ui-design-system.md), [`ui-component-inventory.md`](./ui-component-inventory.md) |
| Reviewing provider contracts | [`integration-contracts/`](./integration-contracts/) |

## Canonical Rules

- StackOS stores project state, validates explicit inputs, resolves
  daemon-held credentials, executes configured calls, and records audit.
- Agents and operators make strategy decisions. Tools and connectors stay
  decision-free.
- Register callable behavior once as an operation or plugin action contract;
  expose it through MCP, REST, CLI, and UI docs from that contract.
- Direct MCP tools are only for generic StackOS primitives. Provider/vendor
  calls belong in plugin actions executed through `action.execute`.
- Agents never receive secrets. They receive safe provider keys, account refs,
  auth status, scopes, diagnostics, and opaque `credential_ref` values.
- SEO, media buying, GTM, publishing, and utilities are plugins. Core StackOS
  remains domain-agnostic.

## Verification Commands

For documentation-only edits:

```bash
TPF_LLM_TOOL=codex tpf git diff --check
TPF_LLM_TOOL=codex tpf rg "StackOS" AGENTS.md CLAUDE.md README.md docs plugins -n
TPF_LLM_TOOL=codex tpf rg "content-stack" docs README.md plugins/content-stack -n
```

Run targeted tests when documentation changes command contracts, generated API
expectations, operation examples, or UI integration notes:

```bash
TPF_LLM_TOOL=codex tpf uv run pytest tests/unit/test_operations_registry.py tests/unit/test_cli_ops.py -q
TPF_LLM_TOOL=codex tpf pnpm --dir ui type-check
```

## Cleanup Rule

Do not keep temporary planning docs, migration audits, or old workflow notes in
the active docs set after their current state has been merged into canonical
docs. If historical context is needed, move only the durable facts into the
relevant canonical document.
