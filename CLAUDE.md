# StackOS Claude Notes

This repository is now StackOS: a generic tool and plugin runtime for
agent-operated projects.

## Read First

- `AGENTS.md`: current repo instructions and change checklist.
- `README.md`: business/product overview.
- `docs/README.md`: documentation router.
- `docs/setup.md`: install, start, autostart, repair, and first-run flow.
- `docs/architecture.md`: core architecture.
- `docs/operations.md`: operation registry and MCP/REST/CLI surfaces.
- `docs/action-executor.md`: daemon-side action execution.
- `docs/auth-providers.md`: no-secret auth boundary.
- `docs/plugins.md`: plugin manifest and extension model.

## Working Rules

- Keep core domain-agnostic.
- Put domain behavior in plugins.
- Keep tools static, explicit, and decision-free.
- Register callable behavior once as an operation or plugin action contract.
- Never expose secrets to agents.
- Use workflow templates and run plans for execution state.
- Use `action.execute` for provider/vendor calls; direct MCP tools are only for
  generic StackOS primitives.
- Render generic UI surfaces where possible.
- Delete removed flows from code, tests, docs, generated API types, and install
  assets in the same delivery.

## Useful Commands

```bash
TPF_LLM_TOOL=codex tpf make test
TPF_LLM_TOOL=codex tpf make lint
TPF_LLM_TOOL=codex tpf make typecheck
TPF_LLM_TOOL=codex tpf make gen-types
TPF_LLM_TOOL=codex tpf make build-ui
```
