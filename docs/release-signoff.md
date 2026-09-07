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
- `make test-transfer-connectors` for Amazon S3 and FTP manifest, auth,
  integration, action, MCP/grant/audit, response-file, regression, and wheel
  packaging proof
- targeted pytest coverage for unit contracts, REST operations, CLI mock
  provider execution, REST/CLI/MCP operation parity, auth setup, Telegram
  setup-to-action flow, Slack signed-ingress/action flow, SMTP/IMAP mocked
  connectors, MCP action, tracker, and communication setup execution, workflow
  template loading, and action/auth/tracker repositories
- UI unit tests
- the UI production build into `stackos/ui_dist/`

## Agent Flow Matrix

For agency/setup package changes, include
`tests/unit/test_agency_plugin.py` and
`tests/integration/test_repositories/test_agency_setup.py`, the plugin catalog
and workflow/preset suites, and the finance schema/workspace slice. Rehearse a
standalone engagement, an agency with no projects, two projects for one client,
setup rerun/readback, and rejected cross-project access. Verify standalone
finance has no agency prerequisite, optional attribution changes the invoice
approval digest, and company-wide/ambiguous costs do not become duplicated or
automatically allocated. Independently compare the shipped definitions and
host adaptations with the existing setup, resource, and orchestrator patterns.

Use this matrix before release to choose the smallest meaningful test slice
while still covering the agent-facing contract. Run full `make signoff` when a
change crosses more than one row, changes operation schemas, changes grants, or
touches committed UI assets.

| Agent Flow | What Must Stay True | Targeted Check |
| --- | --- | --- |
| Workspace-bound MCP bootstrap | The bridge resolves the current project, injects `project_id`, and rejects cross-project calls. | `uv run pytest tests/integration/test_mcp/test_mcp_workspaces.py tests/integration/test_mcp/test_mcp_bridge_agent_path.py tests/unit/test_mcp_bridge.py -q` |
| MCP operation discovery | Agents can inspect OperationSpec purpose, schemas, grants, examples, and toolbox categories from MCP. | `uv run pytest tests/unit/test_mcp_bridge.py tests/unit/test_operations_registry.py -q` |
| Account/profile resolution | Agents see safe Account refs/status only, never secrets, project profiles bind exact attached Accounts, and `toolProfile.resolve` gives repair guidance. | `uv run pytest tests/integration/test_mcp/test_mcp_communications.py::test_tool_profile_resolve_mcp_resolves_telegram_profile_and_credential tests/integration/test_repositories/test_auth_providers.py -q` |
| Account, OAuth, and Connection lifecycle | Global named Accounts, explicit multi-project attachments, fixed callback/state/PKCE, migration, renewal, scope gates, and sanitized failures stay aligned. | `uv run pytest tests/integration/test_repositories/test_oauth_lifecycle.py tests/integration/test_routes/test_auth_provider_routes.py tests/integration/test_repositories/test_auth_providers.py tests/integration/test_repositories/test_global_accounts.py tests/integration/test_schema.py::test_global_account_migration_preserves_and_reencrypts_legacy_credentials -q`<br>`pnpm --dir ui exec vitest run src/views/AccountsView.spec.ts src/views/ConnectionsView.accounts.spec.ts` |
| Direct action execution | `action.describe/validate/run` and direct dry-runs use the same connector/auth/audit path. | `uv run pytest tests/integration/test_mcp/test_mcp_actions.py tests/integration/test_routes/test_cli_mock_provider.py -q` |
| Finance external-backend workflows | Six finance templates, five focused specialists plus an independent reviewer, main-agent skill/orchestrator guidance, safe reference outputs, technical approval gates, and external prepared bookkeeping/13-week cashflow/annual tax packets load and resolve without finance state in StackOS. | `uv run pytest tests/unit/test_finance_plugin.py tests/unit/test_finance_workspace_contract.py tests/unit/test_finance_imap_handoff_contract.py tests/unit/test_agent_presets.py tests/unit/test_skill_presets.py tests/integration/test_repositories/test_finance_workflows.py tests/integration/test_mcp/test_mcp_agent_presets.py -q` plus the agent-executed rehearsal below |
| Stripe finance transport | Pinned API, daemon-held auth, explicit invoice currency, selected required/optional observations, typed reconciliation and unavailable-linkage recovery, fixed no-charge settlement/report forms, retry veto/idempotency conflict safety, test-only send status, separate gates and non-authoritative audit stay intact. | `uv run pytest tests/integration/test_integrations/test_stripe_transport.py tests/integration/test_repositories/test_stripe_actions.py tests/integration/test_mcp/test_mcp_stripe_actions.py tests/integration/test_mcp/test_mcp_finance_billing_workflow.py tests/integration/test_repositories/test_actions.py -q` |
| Integrated finance billing | Actual payment-request/follow-up templates execute through isolated MCP and mocked Stripe HTTP with external fixture records: two exact lines, distinct finalization/send gates, paid-before-send suppression, unknown-send recovery, and full/partial bank receipts with lost report or attachment responses. | `uv run pytest tests/integration/test_mcp/test_mcp_finance_billing_workflow.py -q`; these tests simulate agent/owner judgment and provider responses, not real email delivery or production custody. |
| IMAP finance evidence handoff | Verified TLS, UID-only read-only `BODY.PEEK[]` export, bounded MIME staging, host store-before-ack, epoch-qualified `mark_seen`, and transfer-id-only cleanup remain separate; raw evidence never becomes a StackOS record. | `uv run pytest tests/integration/test_repositories/test_imap_actions.py tests/unit/test_finance_imap_handoff_contract.py -q` |
| S3 and FTP transfer connectors | Both providers keep their seven explicit file-management action categories while S3 preserves flat-key, AWS-policy authorization, conditional/versioning, bounded-prefix, and non-atomic move semantics. Direct and granted calls retain response-file and audit evidence without secrets. | `make test-transfer-connectors`<br>`pnpm --dir ui exec vitest run src/components/domain/ProviderMark.spec.ts` |
| Workflow/run-plan execution | `runPlan.validate/create/start/claimStep/recordStep`, step grants, and non-executable warnings behave predictably. | `uv run pytest tests/unit/test_run_plan_schema.py tests/integration/test_mcp/test_mcp_run_plans.py tests/integration/test_mcp/test_mcp_tool_grants.py -q` |
| Tracker task/ticket workflow | Bulk create/review/update, dependency previews, compact reads, history, and verification stay agent-friendly. | `uv run pytest tests/integration/test_mcp/test_mcp_tracker.py tests/integration/test_repositories/test_tracker.py tests/unit/test_operation_responses.py tests/unit/test_operations_registry.py -q` |
| Communication delivery | `communicationTarget.resolve`, `communication.send/reply`, dry-run effects, rich-feature rejection, local chat, and stored context field repair are clear. | `uv run pytest tests/integration/test_mcp/test_mcp_communications.py -q` |
| Communication ingress | Slack/Telegram ingress verifies transport auth, stores normalized resources, and creates agent requests only through shared policy. | `uv run pytest tests/integration/test_routes/test_slack_ingress_routes.py tests/integration/test_routes/test_telegram_ingress_routes.py -q` |
| Agent request handoff | Agent requests claim, prepare run plans atomically, link, complete, release, and hide claim tokens correctly. | `uv run pytest tests/integration/test_mcp/test_mcp_agent_requests.py tests/integration/test_repositories/test_agent_requests.py -q` |
| UI human signoff surfaces | Tracker, setup, connections, runs, resources, and operation pages render the generic objects agents act on. | `pnpm --dir ui test && pnpm --dir ui build` |
| Setup/package smoke | Install, daemon start/doctor, MCP registration, assets, and docs match the release shape. | `make install && make doctor` |
| AI-tool host lifecycle | Shared canonical states, fail-closed ownership, ChatGPT/Codex capability fallback, explicit Hermes profiles, desktop pre-ready reconciliation, and backend-owned UI labels stay aligned. | `uv run pytest tests/unit/test_host_mcp.py tests/unit/test_claude_mcp.py tests/unit/test_cli_install.py -q`<br>`pnpm --dir ui exec vitest run src/views/home/agentHostPresentation.spec.ts`<br>`node desktop/scripts/test-service-upgrade.cjs` |
| Local daemon lifecycle | Restart ignores stale pid files and zombie/defunct children, refuses non-StackOS port blockers, and does not leave launchd booted out. | `uv run pytest tests/unit/test_cli_daemon.py -q` |
| macOS desktop app | Electron metadata, service bridge, update endpoint config, frozen payload dependencies, executable packaged CLI, install/repair, and desktop docs stay aligned with the installer contract. | `make desktop-doctor` plus `make desktop-payload` for payload or dependency changes |
| Visible Chromium runtime | The app ships one signed arm64 `Chromium.app`, no Chrome for Testing/headless shell, and a stable StackOS profile persists a nonce cookie across a visible restart. | `pnpm --dir desktop check` plus installed-app proof |

## Finance Production Activation Gate

Passing the fixture and temporary-workspace checks above proves the package
contract; it does not authorize a live finance action. Do not call the finance
package production-ready unless the operator has separately completed the
relevant route gate:

- **External backend:** select one backend and safe workspace reference, verify
  it is writable by the trusted host, confirm backup/retention/access controls,
  and create/re-read a non-production test record under the external backend's
  own custody rules. `local-json` uses the checked-in `finance.json` template and
  `local-json-v1` schema as the single financial-data owner and still remains
  `prepared/unposted`. `FINANCE.md` is guidance; CSV/reports are non-authoritative
  interchange/views. Verify no second editable packet master or mutable setup
  table exists. First-time setup validates and reads back the new workspace;
  never overwrite existing files with an empty template.
- **IMAP receipts:** connect through the local Connections UI, verify SSL or
  STARTTLS with `account.test`, and perform an operator-owned test-message
  rehearsal. It must prove original `.eml`/attachments and `finance.json` are
  re-read before an expected-UIDVALIDITY `mark_seen`, with cleanup by the exact
  opaque transfer id. No raw email evidence may enter StackOS.
  Private-CA mailboxes save only the approved public CA PEM on the existing
  Account. Verify persistence, omission/replacement/explicit clearing, Account
  isolation and default-root preservation; invalid PEM/private keys must fail
  before saving. The canonical gate includes real synthetic TLS handshakes
  proving trusted success and untrusted, wrong-host and expired-leaf rejection.
- **Stripe invoices:** connect a restricted key through the local Connections
  UI and prove the safe account probe. Any live customer/invoice finalization,
  send, or resend requires the selected workflow, active step grant, external
  business-scoped approval record matching the safe object/recipient, and its
  exact action-level owner gate. Reconcile an ambiguous write before a retry;
  never use a production customer as a test fixture.
- **Stripe received-payment settlement:** verify selected PaymentIntent and
  PaymentRecord restricted-key permissions in an explicitly authorized test
  account. Rehearse full and partial bank receipts, existing succeeded Stripe
  payment attachment, duplicate-source rejection and report-success/attach-failure
  recovery. Match external source/customer/currency/account/mode and current
  remaining balance; record distinct report/attach/full-external-settlement
  gates, then verify the exact InvoicePayment linkage and external single-write
  proof. Settlement-only must not send a reminder or initiate a charge. A
  PaymentRecord report with an unknown outcome first uses retained audit/response
  files and a surviving known safe ref. Listing is temporarily unavailable in
  StackOS: prove direct and granted calls reject with an unavailable reason and
  no HTTP request, while the optional recovery contract does not block ordinary
  follow-ups. Compare the exact UTF-8 reference digest retained externally before
  reporting and current customer/account/mode/currency/amount facts; independently
  retrieve and retain the verified ref before attachment. Missing, ambiguous or
  incomplete evidence requires owner/provider resolution, never a replacement
  report key. Re-enabling listing requires a later verified availability fix.
- **Cashflow and tax:** confirm legal form separately from tax election and its
  effective period, source coverage, annual taxpayer inputs, exactly 13 dated
  base/downside cash weeks, and current applicable IRS/California FTB sources.
  Verify opening cash + receipts - cash outflows = closing cash; earmarked
  reserves reduce spendable cash, not the bank balance. Tax preparation may
  remain incomplete/awaiting-advisor; reliance requires CPA/EA review and owner
  approval. This package never files, remits, pays, or changes an election.

Before source-package closeout, execute the checked-in guidance against a
synthetic month in a disposable host workspace. Retain originals, extract
multiple receipts from one source, recognize a duplicate upload, isolate an
unreadable item, prepare supported transactions with a missing statement, check
an exact two-line invoice proposal, and suppress a reminder after payment. Build
the cash rollforward and annual tax packet, then rehearse restart and one-time
application of a reviewed reserve version. Have a separate agent inspect the
actual external records and arithmetic. Record which provider responses and
owner/advisor decisions were simulated. Static string/schema assertions do not
prove agent behavior, OCR, real email delivery, or unattended scheduling.

The local JSON rehearsal also validates the complete schema, unique record IDs,
resolved references, immutable approval targets, whole-document revision/hash
conflict rejection, atomic replacement/readback and safe first-time setup.
Run `uv run pytest tests/unit/test_finance_json_schema.py tests/unit/test_finance_workspace_contract.py tests/unit/test_finance_plugin.py -q`
for the schema, template and workflow contract slice before the behavioral rehearsal.
Prove that unrelated JSON updates require reread/rebase without invalidating an
unchanged approved proposal, while a material scoped change does invalidate it.
Derived Markdown/CSV changes must not alter the financial record; exports retain
the exact source JSON revision/digest. These remain host fixture checks, not a
new StackOS finance validator or storage service.

No automated release check may perform live mailbox, customer, payment, refund,
transfer, payout, tax, or filing work. Record operator-owned production
rehearsal evidence truthfully in the selected external backend and StackOS'
safe run/action audit refs; do not reproduce finance contents in tracker,
resource, or artifact fields.

Source verification and installed/native verification are separate evidence.
If the operator prohibits a shared-runtime restart, do not replace the app,
refresh plugin assets or host sessions, restart the daemon/bridge, or reset
shared fixtures to complete signoff. Run isolated source checks, record native
activation as deferred with an explicit resume condition, and wait for a
separately approved activation window. A source-only pass is not proof that an
already running bridge exposes the new compact diagnostics or action catalog.

For operational guidance changes, independently rehearse receipt-only setup
with missing tax inputs, unknown/changed billing recipients, and a specialist
without actual toolbox or filesystem capability. Expected outcomes are bounded
receipt progress, a held send with invalidated stale approval, and a prepared
handoff to the capable main agent under the same grants and approval. Required
independent review must remain independent. Resolve the actual compact preset
packet as well as the source files: missing reference paths or applicability
conditions are agent-facing defects, not cosmetic response differences.

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
pnpm --dir ui exec vitest run src/views/AccountsView.spec.ts src/views/ConnectionsView.accounts.spec.ts
npm --prefix workers/oauth-callback-relay test
```

For provider connector changes, `make signoff` includes the integration wrapper
tests and provider action execution tests. Amazon S3 and FTP share an explicit
focused gate:

```bash
make test-transfer-connectors
```

This gate uses deterministic provider fakes and locked SDK models. It does not
replace the operator-owned disposable live-AWS smoke required before calling
the S3 connector production-ready.

To isolate the broader provider slice while fixing another connector, run:

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

`make desktop-payload` and every macOS distribution build execute the generated
CLI from an isolated working directory with hostile ambient Python variables.
Distribution builds repeat the check against the final `.app` bundle. A CLI
import failure, dependency conflict, source-checkout shadow, or version mismatch
in the payload must fail before signing. A final-app-only failure must stop the
release before separate DMG notarization, metadata refresh, alias creation, or
publication.

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
