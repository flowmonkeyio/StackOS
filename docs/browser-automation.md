# Native Browser Sessions

StackOS creates, reuses, discovers, and stops project browser sessions. Agents
operate them from their terminal with the upstream
[gstack browse CLI](https://github.com/garrytan/gstack/tree/71f6048e8ada25180e61438abc1d98cb151fe9a7/browse):

```sh
stackos.browser --session browser-session:project-1:default:main goto https://example.com
stackos.browser --session browser-session:project-1:default:main snapshot
stackos.browser --session browser-session:project-1:default:main --help
```

Pass the full session ref every time. There is no global active or last-used
session. Session discovery returns `cli_argv`, a ready command prefix:

```json
{"cli_argv":["stackos.browser","--session","browser-session:project-1:default:main"]}
```

## Client Flow

1. Use `browser.session.list` to discover project sessions. Select the intended
   session or call `browser.session.start` with stable `profile_key` and
   `session_key` values to create/reuse it.
2. Append native gstack arguments to its `cli_argv` and execute in the host
   terminal. Use the same prefix for every command in that session.
3. Read the [native browser cookbook](../plugins/stackos/skills/stackos/references/browser.md)
   shipped with the StackOS skill for interaction, screenshots, comparisons,
   batching, and diagnostics. Consult native `--help` or the bundled
   [command reference](https://github.com/garrytan/gstack/blob/71f6048e8ada25180e61438abc1d98cb151fe9a7/browse/sections/command-list.md)
   for syntax. StackOS does not maintain a second command vocabulary.

Session lifecycle/discovery remains available through MCP, generic REST,
`stackos ops call`, and the generic Operations UI. The pinned upstream package
provides a CLI; agents need host terminal access to operate it. StackOS does not
install gstack's full skill suite or run its global setup.

The cookbook is the shared agent reference. Browser-assisted engineering, SEO,
and branding guidance reaches it through this document or the installed
`stackos:stackos` skill. Maintain command recipes there; upstream owns the full
command vocabulary.

The browser opens visibly. For authenticated work, let the operator complete
login/MFA in the selected profile, then keep reusing its `profile_key`. Different
profiles have separate cookies and storage. One live process owns a profile;
another session cannot use it concurrently.

For background work in an existing tab, read native `tabs`, confirm the target
ID, and append upstream `--tab-id <id>` to each command:

```bash
stackos.browser --session <full-ref> tabs
stackos.browser --session <full-ref> goto https://example.com --tab-id 3
stackos.browser --session <full-ref> snapshot --tab-id 3
stackos.browser --session <full-ref> screenshot /tmp/page.png --tab-id 3
```

Native `tab <id>` explicitly brings the page to the front; do not use it before
each automated capture. `--tab-id` selects the command's tab without that focus
call and restores the prior active tab afterward. This is upstream behavior,
not a StackOS command rewrite. Verify the ID still exists before a work batch:
the pinned upstream warns and continues on the active tab if pinning fails.
Recheck page identity when accepting captures. Explicit `focus`, `connect`,
`tab`, and opening a new browser/tab can still raise a window.

## Launcher Contract

`stackos.browser` consumes only the leading `--session <full-ref>`. It resolves
that exact project-scoped session with one authenticated `browser.session.status`
request, applies its native working directory and environment, and replaces
itself with the upstream executable using `execve`.

Every remaining argument goes to gstack unchanged, including `--help`, `stop`,
`restart`, `--force-restart`, `--`, a later `--session`, and future commands or
flags. Native stdin, stdout, stderr, terminal descriptors, exit status, and
signals remain ordinary OS behavior. There is no command capture, response
envelope, command filter, extra retry, or StackOS browser-command audit.

The local daemon must be reachable to resolve the selection. Missing or malformed
refs, unavailable runtime/context, and a competing live profile owner fail before
native execution. A stopped, stale, busy, or unhealthy selected session can still
resolve its native context; health is reported separately. Upstream decides what
its requested command does, including native startup and recovery.

Native commands can stop or restart the selected browser. Later StackOS session
observations reconcile its state. `browser.session.stop` remains available for
StackOS-managed shutdown and waits for observed process/state retirement.

## Context And Discovery

Selected start/status results retain `native_cli={executable,cwd,env}` for local
clients that need the launch context. Ordinary agents use `cli_argv` to avoid
assembling it themselves. Bulk discovery returns summary metadata and `cli_argv`
for every known session, including stopped/stale rows, without native contexts.
A prefix identifies a session; it does not promise that the runtime is ready.

The native environment selects the exact state file, persistent profile,
visible browser, bundled tools and browser assets. Ambient `BROWSE_`, `GSTACK_`,
`CHROMIUM_`, and `PLAYWRIGHT_` controls are removed before applying that context.
The handoff does not set `BROWSE_NO_AUTOSTART`. Other terminal environment and
standard streams are inherited.

StackOS never returns state-file contents, authentication tokens, cookies, or
stored credentials in session discovery. Selected paths are local capabilities.
Native page output is working data; agents decide what to preserve with generic
artifact operations under their normal authority. Historical browser receipts
remain stored.

Discovery inspects owned native state and OS process identity after daemon
restart. A live busy/unhealthy owner keeps its profile. Runtime repair preserves
profile directories and session history.

## Install And Upgrade

Normal StackOS install/repair installs the current-user command at
`~/.local/bin/stackos.browser`; that directory must be on the agent terminal's
`PATH`. The managed launcher retains the configured daemon host, port, data and
state directories and points to the current StackOS installation. Repair refreshes
owned entries after package moves/upgrades and preserves unrelated same-name
files. Uninstall removes only StackOS-owned launchers.

The desktop payload includes a relocatable `bin/stackos.browser`. Doctor checks
launcher readiness and the installed upstream runtime. Restart host MCP sessions
after upgrading to refresh the lifecycle-only tool catalog.

The distribution pins gstack revision `71f6048e8ada25180e61438abc1d98cb151fe9a7`
(version `1.84.1.0`), Bun `1.3.8`, and the upstream locked dependencies/browser
assets, retaining licenses and notices. It lives under `browser-runtime` in the
desktop payload or daemon data directory.

The macOS ARM64 installer prunes the Anthropic SDK development packages and
ONNX's Linux/Windows binaries after compilation and sidebar asset preparation.
Darwin ARM64 ONNX, Transformers, the security classifier, and sidebar assets
remain. The runtime manifest includes a packaging revision so install/repair
rebuilds an older managed distribution instead of accepting its bulkier layout.
Packaged app runtimes stay immutable and are refreshed by replacing the app.

Persistent profiles retain their existing `browser-profiles/playwright-chromium`
paths; the directory name preserves data and does not select the old driver.
Retired launch fields remain historical metadata. Native gstack may write its
extension bootstrap to `~/.gstack/.auth.json`; StackOS does not use it as a
session registry or expose its contents.

## Verification

Capture helpers should wait for page-specific content after navigation and
visually inspect each saved screenshot. Navigation success alone does not prove
the page has rendered. Helpers own their command deadlines and failure evidence:
retain elapsed time, exit status, stdout/stderr and host-tool errors. These are
caller responsibilities; the browser launcher does not capture or time commands.

Use a synthetic localhost page and stable proof profile. Create/reuse and
discover the same session, then operate it with the global command from fresh
shells. Verify native help, output, stdin, exit status, terminal behavior and
signals. Set a synthetic cookie/localStorage marker and verify it survives native
browser restart and StackOS daemon restart. Installed-app signoff also covers
managed-launcher repair, doctor, and restart from `/Applications/StackOS.app`.
