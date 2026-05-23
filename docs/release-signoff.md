# Before Commit And Release Signoff

Use this command set when a change touches setup, actions, operation adapters,
MCP, REST, CLI, UI wiring, provider contracts, or docs that agents rely on.

```bash
UV_CACHE_DIR=/private/tmp/uv-cache TPF_LLM_TOOL=codex tpf make signoff
```

`make signoff` runs:

- `make lint`
- `make typecheck`
- targeted pytest coverage for unit contracts, REST operations, CLI mock
  provider execution, REST/CLI/MCP operation parity, auth setup, Telegram
  setup-to-action flow, SMTP/IMAP mocked connectors, MCP action and
  communication setup execution, workflow template loading, and action/auth
  repositories
- UI unit tests
- the UI production build into `stackos/ui_dist/`

For a faster local check while iterating on action execution, run the mock
provider and connector-contract slice directly:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache TPF_LLM_TOOL=codex tpf uv run pytest \
  tests/unit/test_connector_contract_docs.py \
  tests/integration/test_routes/test_operations_routes.py \
  tests/integration/test_routes/test_cli_mock_provider.py \
  tests/integration/test_mcp/test_mcp_actions.py::test_action_execute_mock_provider_vertical_slice_through_mcp \
  tests/integration/test_repositories/test_smtp_actions.py \
  tests/integration/test_repositories/test_imap_actions.py \
  tests/integration/test_repositories/test_agent_requests.py::test_agent_request_prepare_run_plan_is_atomic_and_links_request \
  -q
```

For provider connector changes, add the relevant integration wrapper tests:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache TPF_LLM_TOOL=codex tpf uv run pytest tests/integration/test_integrations -q
```

For documentation-only edits that do not change commands, schemas, operation
examples, generated API expectations, or UI integration notes:

```bash
TPF_LLM_TOOL=codex tpf git diff --check
UV_CACHE_DIR=/private/tmp/uv-cache TPF_LLM_TOOL=codex tpf uv run pytest tests/unit/test_connector_contract_docs.py -q
```

Release signoff should include a clean setup smoke after packaging or install
changes:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache TPF_LLM_TOOL=codex tpf make install
UV_CACHE_DIR=/private/tmp/uv-cache TPF_LLM_TOOL=codex tpf make doctor
```

`doctor` may return daemon-down during first install before `make serve`; that
is expected for setup checks and should be noted in the release notes if it is
the only failing check.
