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
  connectors, MCP action, tracker, and communication setup execution, workflow
  template loading, and action/auth/tracker repositories
- UI unit tests
- the UI production build into `stackos/ui_dist/`

## Agent Flow Matrix

Use this matrix before release to choose the smallest meaningful test slice
while still covering the agent-facing contract. Run full `make signoff` when a
change crosses more than one row, changes operation schemas, changes grants, or
touches committed UI assets.

| Agent Flow | What Must Stay True | Targeted Check |
| --- | --- | --- |
| Workspace-bound MCP bootstrap | The bridge resolves the current project, injects `project_id`, and rejects cross-project calls. | `uv run pytest tests/integration/test_mcp/test_mcp_workspaces.py tests/integration/test_mcp/test_mcp_bridge_agent_path.py tests/unit/test_mcp_bridge.py -q` |
| MCP operation discovery | Agents can inspect OperationSpec purpose, schemas, grants, examples, and toolbox categories from MCP. | `uv run pytest tests/unit/test_mcp_bridge.py tests/unit/test_operations_registry.py -q` |
| Auth/profile resolution | Agents see safe credential refs/status only, never secrets, and `toolProfile.resolve` gives repair guidance. | `uv run pytest tests/integration/test_mcp/test_mcp_communications.py::test_tool_profile_resolve_telegram_profile_returns_safe_tuple tests/integration/test_repositories/test_auth_providers.py -q` |
| OAuth lifecycle and Connections | Fixed callback/state/PKCE, provider exchanges, renewal/client credentials, scope gates, sanitized failures, and the one-action Connect UI stay aligned. | `uv run pytest tests/integration/test_repositories/test_oauth_lifecycle.py tests/integration/test_routes/test_auth_provider_routes.py tests/integration/test_repositories/test_auth_providers.py -q`<br>`pnpm --dir ui exec vitest run src/views/ConnectionsView.credentials.spec.ts` |
| Direct action execution | `action.describe/validate/run` and direct dry-runs use the same connector/auth/audit path. | `uv run pytest tests/integration/test_mcp/test_mcp_actions.py tests/integration/test_routes/test_cli_mock_provider.py -q` |
| Workflow/run-plan execution | `runPlan.validate/create/start/claimStep/recordStep`, step grants, and non-executable warnings behave predictably. | `uv run pytest tests/unit/test_run_plan_schema.py tests/integration/test_mcp/test_mcp_run_plans.py tests/integration/test_mcp/test_mcp_tool_grants.py -q` |
| Tracker task/ticket workflow | Bulk create/review/update, dependency previews, compact reads, history, and verification stay agent-friendly. | `uv run pytest tests/integration/test_mcp/test_mcp_tracker.py tests/integration/test_repositories/test_tracker.py tests/unit/test_operation_responses.py tests/unit/test_operations_registry.py -q` |
| Communication delivery | `communicationTarget.resolve`, `communication.send/reply`, dry-run effects, rich-feature rejection, local chat, and stored context field repair are clear. | `uv run pytest tests/integration/test_mcp/test_mcp_communications.py -q` |
| Communication ingress | Slack/Telegram ingress verifies transport auth, stores normalized resources, and creates agent requests only through shared policy. | `uv run pytest tests/integration/test_routes/test_slack_ingress_routes.py tests/integration/test_routes/test_telegram_ingress_routes.py -q` |
| Agent request handoff | Agent requests claim, prepare run plans atomically, link, complete, release, and hide claim tokens correctly. | `uv run pytest tests/integration/test_mcp/test_mcp_agent_requests.py tests/integration/test_repositories/test_agent_requests.py -q` |
| UI human signoff surfaces | Tracker, setup, connections, runs, resources, and operation pages render the generic objects agents act on. | `pnpm --dir ui test && pnpm --dir ui build` |
| Setup/package smoke | Install, daemon start/doctor, MCP registration, assets, and docs match the release shape. | `make install && make doctor` |
| Local daemon lifecycle | Restart ignores stale pid files and zombie/defunct children, refuses non-StackOS port blockers, and does not leave launchd booted out. | `uv run pytest tests/unit/test_cli_daemon.py -q` |
| macOS desktop app | Electron metadata, service bridge, update endpoint config, packaged install/repair, and desktop docs stay aligned with the installer contract. | `make desktop-doctor` |

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

For OAuth core, provider-contract, callback, and Connections changes, run:

```bash
uv run pytest \
  tests/integration/test_repositories/test_oauth_lifecycle.py \
  tests/integration/test_routes/test_auth_provider_routes.py \
  tests/integration/test_repositories/test_auth_providers.py \
  -q
pnpm --dir ui exec vitest run src/views/ConnectionsView.credentials.spec.ts
npm --prefix workers/oauth-callback-relay test
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

For daemon lifecycle or desktop install/repair changes, also exercise the
installed app path, not only the source checkout:

```bash
uv run pytest tests/unit/test_cli_daemon.py -q
make desktop-doctor
pnpm --dir desktop run dist:mac:dev
/Applications/StackOS.app/Contents/Resources/stackos/bin/stackos autostart status --json
/Applications/StackOS.app/Contents/Resources/stackos/bin/stackos restart --timeout 20
```

The restart smoke must cover a dirty local lifecycle, not just a clean boot:
stale `daemon.pid`, launchd currently loaded, launchd currently missing, and a
non-StackOS process occupying port `5180` are distinct states. Preserve
`~/.local/share/stackos/stackos.db` across upgrade, repair, and uninstall unless
the operator explicitly asks for data removal.

For packaged macOS desktop signoff, include the launchd ownership handoff case:
a StackOS daemon may already be listening on `5180` when
`stackos install --launchd --force` refreshes the plist. The follow-up
`stackos restart --timeout 20` must stop that existing daemon, bootstrap the
installed launchd job, and leave `stackos autostart status --json` reporting the
job loaded/running. A passing Doctor result is not sufficient by itself because
Doctor can be satisfied by any healthy local daemon process.

For desktop release candidates, also build the Python payload and macOS
artifacts after dependencies, signing, notarization, and the custom update
endpoint are configured:

```bash
CSC_NAME="Example Org (ABCDE12345)" \
APPLE_KEYCHAIN_PROFILE="stackos-notary" \
STACKOS_UPDATE_URL="https://stackos.flowmonkey.io/StackOS/" \
pnpm --dir desktop run release:preflight
STACKOS_DESKTOP_BUILD_DRY_RUN=1 \
STACKOS_REQUIRE_SIGNING=1 \
STACKOS_REQUIRE_UPDATE_URL=1 \
CSC_NAME="Example Org (ABCDE12345)" \
APPLE_KEYCHAIN_PROFILE="stackos-notary" \
STACKOS_UPDATE_URL="https://stackos.flowmonkey.io/StackOS/" \
node desktop/scripts/build-mac.mjs
CSC_NAME="Example Org (ABCDE12345)" \
APPLE_KEYCHAIN_PROFILE="stackos-notary" \
STACKOS_UPDATE_URL="https://stackos.flowmonkey.io/StackOS/" \
pnpm --dir desktop run dist:mac:release
```

Before public desktop distribution, record the managed-update evidence:

- `latest-mac.yml`, DMG, ZIP, and generated blockmap artifacts are present in
  the website static update directory.
- `stackos-latest-mac-arm64.dmg` is byte-identical to the signed, notarized,
  stapled versioned DMG used for the release.
- The public update endpoint is HTTPS. FTP, if used, is only the upload/deploy
  transport and no FTP credentials are packaged into the app.
- Artifact URLs referenced by the update metadata are reachable and are not
  stale behind website/CDN cache.
- When changing the production update origin, the prior endpoint remains
  mirrored or redirected through one higher-version bridge release so already
  installed apps can receive the new packaged endpoint.
- The strict release dry-run passed with signing and notarization env supplied
  through the environment or CI secrets, and no Apple credential values were
  written into generated config files or logs.
- Apple signing and notarization status is recorded for each published macOS
  artifact. Include `codesign --verify --deep --strict`, `spctl --assess`, and
  `xcrun stapler validate` output for the shipped DMG/ZIP app contents.
- `pyproject.toml`, `stackos/__init__.py`, and `desktop/package.json` versions
  are synchronized for the release.
- A local `STACKOS_UPDATE_URL=http://127.0.0.1:<port>/...` smoke has covered
  update discovery, download, the in-app prompt, post-update Doctor where
  install/relaunch can run, and state preservation.
- macOS install/relaunch signoff was performed from a signed installed app.
  Unsigned local builds are not valid evidence for the final Squirrel.Mac
  install/relaunch gate.
- A bad-feed smoke has shown readable failure and no mutation of
  `stackos.db`, `seed.bin`, `auth.token`, or provider credentials.

`doctor` may return daemon-down during first install before `make serve`; that
is expected for setup checks and should be noted in the release notes if it is
the only failing check. Plugin or managed skill drift is not expected; a doctor
code `9` means install/upgrade did not refresh StackOS plugin assets correctly.
