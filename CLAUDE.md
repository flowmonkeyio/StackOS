# StackOS Claude Notes

Read [AGENTS.md](./AGENTS.md) for repository rules and
[docs/README.md](./docs/README.md) to select the documents relevant to the task.
The product contract is [docs/product-direction.md](./docs/product-direction.md).
Do not load every implementation guide for an unrelated change.

Use the project-scoped StackOS MCP bridge and the workflow's resolved guidance.
Host-local agent files are adaptations, not another source of project state.

## Useful Commands

```bash
TPF_LLM_TOOL=codex tpf make test
TPF_LLM_TOOL=codex tpf make lint
TPF_LLM_TOOL=codex tpf make typecheck
TPF_LLM_TOOL=codex tpf make gen-types
TPF_LLM_TOOL=codex tpf make build-ui
```
