# Before Commit And Release Signoff

Use this command set when a change touches setup, actions, operation adapters,
MCP, REST, CLI, UI wiring, provider contracts, or docs that agents rely on.
Command examples show the operator form. Codex agents working in this repository
must still apply the shell wrapper rules in [`../AGENTS.md`](../AGENTS.md).

```bash
make signoff
```

`make signoff` runs:

- `make lint`
- `make typecheck`
- targeted pytest coverage for unit contracts, REST operations, CLI mock
  provider execution, REST/CLI/MCP operation parity, auth setup, Telegram
  setup-to-action flow, Slack signed-ingress/action flow, SMTP/IMAP mocked
  connectors, MCP action and
  communication setup execution, workflow template loading, and action/auth
  repositories
- UI unit tests
- the UI production build into `stackos/ui_dist/`

## Agent Flow Matrix

Use this matrix before release to choose the smallest meaningful test slice
while still covering the agent-facing contract. Run full `make signoff` when a
change crosses more than one row, changes operation schemas, changes grants, or
touches committed UI assets.

| Agent Flow | What Must Stay True | Targeted Check |
| --- | --- | --- |
| Workspace-bound MCP bootstrap | The bridge resolves the current project, injects `project_id`, and rejects cross-project calls. | `uv run pytest tests/integration/test_mcp/test_mcp_workspaces.py tests/unit/test_mcp_bridge.py -q` |
| MCP operation discovery | Agents can inspect OperationSpec purpose, schemas, grants, examples, and toolbox categories from MCP. | `uv run pytest tests/unit/test_mcp_bridge.py tests/unit/test_operations_registry.py -q` |
| Auth/profile resolution | Agents see safe credential refs/status only, never secrets, and `toolProfile.resolve` gives repair guidance. | `uv run pytest tests/integration/test_mcp/test_mcp_communications.py::test_tool_profile_resolve_telegram_profile_returns_safe_tuple tests/integration/test_repositories/test_auth_providers.py -q` |
| Direct action execution | `action.describe/validate/run` and direct dry-runs use the same connector/auth/audit path. | `uv run pytest tests/integration/test_mcp/test_mcp_actions.py tests/integration/test_routes/test_cli_mock_provider.py -q` |
| Workflow/run-plan execution | `runPlan.validate/create/start/claimStep/recordStep`, step grants, and non-executable warnings behave predictably. | `uv run pytest tests/unit/test_run_plan_schema.py tests/integration/test_mcp/test_mcp_run_plans.py tests/integration/test_mcp/test_mcp_tool_grants.py -q` |
| Tracker task/ticket workflow | Bulk create/review/update, dependency previews, compact reads, history, and verification stay agent-friendly. | `uv run pytest tests/integration/test_repositories/test_tracker.py tests/unit/test_operations_registry.py -q` |
| Communication delivery | `communicationTarget.resolve`, `communication.send/reply`, dry-run effects, rich-feature rejection, local chat, and stored context field repair are clear. | `uv run pytest tests/integration/test_mcp/test_mcp_communications.py -q` |
| Communication ingress | Slack/Telegram ingress verifies transport auth, stores normalized resources, and creates agent requests only through shared policy. | `uv run pytest tests/integration/test_routes/test_slack_ingress_routes.py tests/integration/test_routes/test_telegram_ingress_routes.py -q` |
| Agent request handoff | Agent requests claim, prepare run plans atomically, link, complete, release, and hide claim tokens correctly. | `uv run pytest tests/integration/test_mcp/test_mcp_agent_requests.py tests/integration/test_repositories/test_agent_requests.py -q` |
| UI human signoff surfaces | Tracker, setup, connections, runs, resources, and operation pages render the generic objects agents act on. | `pnpm --dir ui test && pnpm --dir ui build` |
| Setup/package smoke | Install, daemon start/doctor, MCP registration, assets, and docs match the release shape. | `make install && make doctor` |
| macOS desktop app | Electron metadata, service bridge, update endpoint config, and desktop docs stay aligned with the installer contract. | `make desktop-doctor` |

For a faster local check while iterating on action execution, run the mock
provider and connector-contract slice directly:

```bash
uv run pytest \
  tests/unit/test_connector_contract_docs.py \
  tests/integration/test_routes/test_operations_routes.py \
  tests/integration/test_routes/test_cli_mock_provider.py \
  tests/integration/test_mcp/test_mcp_actions.py::test_action_execute_mock_provider_vertical_slice_through_mcp \
  tests/integration/test_repositories/test_smtp_actions.py \
  tests/integration/test_repositories/test_imap_actions.py \
  tests/integration/test_repositories/test_slack_bot_actions.py \
  tests/integration/test_routes/test_slack_ingress_routes.py \
  tests/integration/test_repositories/test_agent_requests.py::test_agent_request_prepare_run_plan_is_atomic_and_links_request \
  -q
```

For provider connector changes, `make signoff` includes the integration wrapper
tests and provider action execution tests. To isolate that slice while fixing a
connector, run:

```bash
uv run pytest tests/integration/test_integrations -q
uv run pytest tests/integration/test_repositories/test_video_provider_actions.py -q
```

For documentation-only edits that do not change commands, schemas, operation
examples, generated API expectations, or UI integration notes:

```bash
git diff --check
uv run pytest tests/unit/test_connector_contract_docs.py -q
```

Release signoff should include a clean setup smoke after packaging or install
changes:

```bash
make install
make doctor
```

For desktop release candidates, also build the Python payload and macOS
artifacts after dependencies, signing, notarization, and the custom update
endpoint are configured:

```bash
make desktop-payload
STACKOS_UPDATE_URL="https://updates.example.com/stackos/macos" make desktop-dist
```

`doctor` may return daemon-down during first install before `make serve`; that
is expected for setup checks and should be noted in the release notes if it is
the only failing check. Plugin or managed skill drift is not expected; a doctor
code `9` means install/upgrade did not refresh StackOS plugin assets correctly.
