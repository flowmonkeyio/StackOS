# Native Browser Sessions

StackOS creates, reuses, discovers, and stops project browser sessions. Browser
commands belong to the upstream [gstack browse CLI](https://github.com/garrytan/gstack/tree/71f6048e8ada25180e61438abc1d98cb151fe9a7/browse).
A client can run that executable directly or send its exact arguments and stdin
through `browser.cli.run` over MCP, REST, or the generic StackOS CLI.

## Ownership

StackOS owns the installed runtime, project/profile/session identities, persistent
profile directories, session discovery, and lifecycle transitions. The existing
operation registry supplies the MCP, REST, CLI, and generic Operations UI contracts.

Upstream gstack owns command names, argument parsing, browser behavior, snapshots,
JavaScript, screenshots, tabs, retries inside its CLI, extensions, and companion
processes. StackOS does not translate commands, maintain a browser-method allowlist,
rewrite native output, or automatically create browser receipts or artifacts.
Agents decide which captures to preserve as generic artifacts under normal grants.
Historical browser receipt rows remain stored.

## Client Flow

1. Call `browser.runtime.status` and `browser.session.list`.
2. Select an existing session, or call `browser.session.start` with a stable
   `profile_key` and `session_key`. Starting the same healthy session reuses it.
3. Read `browser.session.status` or the start result for `native_cli`.
4. Run the upstream executable with that working directory and environment,
   or call `browser.cli.run` with the session ref and upstream arguments.
5. Call `browser.session.stop` when the session is no longer needed.

The lifecycle opens a visible browser. For authenticated work, let the operator
complete login/MFA in that profile, then reuse its `profile_key`. A different
profile key has separate cookies and storage. A live process exclusively owns a
profile; a different session key cannot start another process against it.

`browser.session.list` returns scoped metadata for known sessions, including
stopped or stale records. It does not return native CLI contexts in bulk.
Discovery inspects the owned native state and OS process identity, so a live
session remains discoverable after the StackOS daemon restarts. An alive but busy
or unhealthy process retains ownership; a health timeout does not permit a
competing start. Stopping waits for actual process and state retirement, rather
than treating upstream acknowledgement as completed shutdown.

## Native CLI Handoff

A usable selected session returns:

```json
{"native_cli":{"executable":"<installed upstream browse>","cwd":"<session working directory>","env":{"BROWSE_STATE_FILE":"<owned state file>","BROWSE_NO_AUTOSTART":"1","CHROMIUM_PROFILE":"<persistent profile>","BROWSE_HEADED":"1","BROWSE_PARENT_PID":"0","GSTACK_HOME":"<owned home>","PLAYWRIGHT_BROWSERS_PATH":"<installed browsers>","PATH":"<bundled tool path>"}}}
```

Use the returned values as supplied. For example, a client with subprocess access
can invoke the real executable directly:

```python
subprocess.run(
    [native_cli["executable"], "goto", "https://example.com"],
    cwd=native_cli["cwd"],
    env={**os.environ, **native_cli["env"]},
)
```

No StackOS process receives that command. Native stdout, stderr, and exit status
remain ordinary OS streams. The context includes paths needed to operate the
selected local session; treat it as a local capability. StackOS never returns
the state-file contents, authentication token, cookies, or stored credentials
as part of session discovery.

## MCP Relay

The pinned upstream distribution exposes a CLI, not an upstream MCP server.
`browser.cli.run` is a mechanical transport to that same executable for clients
that only have MCP. After `workspace.startSession`, the bridge injects project
scope. These are the equivalent toolbox payloads:

```json
{"tool_name":"browser.session.start","arguments":{"profile_key":"default","session_key":"main"}}
```

```json
{"tool_name":"browser.cli.run","arguments":{"session_ref":"browser-session:project-1:default:main","argv":["goto","https://example.com"]}}
```

```json
{"tool_name":"browser.cli.run","arguments":{"session_ref":"browser-session:project-1:default:main","argv":["chain"],"stdin":"[[\"js\",\"document.title\"]]"}}
```

The relay invokes the native executable once. `argv` is an ordered array of
strings; no shell joining, command filtering, flag insertion, or retries occur.
Optional `stdin` is UTF-8 encoded without adding a newline. The selected command
environment prevents automatic session creation. Native command errors remain
native results, including nonzero exit codes.

The raw result contains exactly these command-result fields inside the normal
StackOS write envelope:

```json
{"stdout":"native output\n","stderr":"","exit_code":0,"encoding":"utf-8"}
```

Both streams are captured as bytes. When both strictly decode as UTF-8, their
text is returned unchanged, including CRLF, control characters, and final
newlines. If either stream is not valid UTF-8, both stream fields contain base64
and `encoding` is `base64`. This preserves bytes across JSON transport. The
relay rejects non-null `idempotency_key` and `expected_etag` before any generic
replay/cache path. Compact and acknowledgement response modes are unavailable.

Normal project binding and run-plan grants apply to StackOS operations. Grant
`browser.cli.run` for native browser work; grants do not enumerate gstack
commands. Direct local CLI access uses the selected session capability and does
not pass through StackOS grants or audit.

## Runtime And Upgrade

Install and repair use the existing StackOS installer. The runtime pins gstack
revision `71f6048e8ada25180e61438abc1d98cb151fe9a7` (version `1.84.1.0`) and Bun
`1.3.8`, retaining the upstream locked dependencies, browser assets, licenses,
and notices. The distribution lives under `browser-runtime` in the desktop
payload or daemon data directory. Doctor checks this installed distribution.
StackOS does not run gstack's global setup or install its agent skills.

Persistent profiles keep their existing `browser-profiles/playwright-chromium`
directory locations during replacement; the directory name preserves existing
data and does not select the old driver. Profile/session/history rows and old
stored launch fields remain intact. Retired launch fields are no longer active
controls. Runtime repair replaces the runtime distribution, not browser profiles.

Native gstack currently writes its extension authentication bootstrap to
`~/.gstack/.auth.json` even with `GSTACK_HOME` set. That is upstream behavior;
StackOS neither adopts it as a session registry nor reads or publishes it.

The first-layer MCP catalog contains runtime status, profile create/list,
session start/list/status/stop, and the CLI relay. Restart the host MCP session
after upgrade to refresh its mounted catalog. Session operations are also
available through `toolbox.call`, `stackos ops call`, and generic operation REST
routes.

## Verification

Use a synthetic localhost page and a stable `gstack-native-proof` profile.
Start twice and verify one native process, use direct CLI and MCP relay, then
set a synthetic cookie/localStorage marker. Restart the StackOS daemon and
discover the same session. Stop the browser, verify process retirement, start
again, and verify the marker persists. Installed-app signoff additionally runs
doctor, supported install/repair, and restart from `/Applications/StackOS.app`.
