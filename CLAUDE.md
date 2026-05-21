# StackOS Claude Notes

This repository is now StackOS: a generic tool and plugin runtime for
agent-operated projects.

## Read First

- `AGENTS.md`: current repo instructions and change checklist.
- `README.md`: product overview and repository map.
- `docs/architecture.md`: core architecture.
- `docs/workflow-templates.md`: reusable workflow setup.
- `docs/run-plans.md`: concrete execution instances.
- `docs/auth-providers.md`: no-secret auth boundary.
- `docs/plugins.md`: plugin manifest and extension model.

## Working Rules

- Keep core domain-agnostic.
- Put domain behavior in plugins.
- Keep tools static and explicit.
- Never expose secrets to agents.
- Use workflow templates and run plans for execution state.
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
