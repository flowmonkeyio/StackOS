# Browser Automation

StackOS includes a daemon-owned Camoufox browser runtime for agent-driven web
work such as platform posting, admin UI publishing, QA, and operator-assisted
login.

## Model

- Browser automation is a core StackOS capability, not a branding-only plugin
  action.
- Setup installs the Python `camoufox[geoip]` package and fetches the Camoufox
  browser binary during `make install` or `stackos install`.
- Profiles, sessions, pages, screenshots, and action receipts are
  project-scoped. Profile directories stay inside the daemon data directory and
  are never returned to agents.
- The daemon owns the browser executable, persistent-context mode, and profile
  directory. Agent-provided launch options are passed through except those
  daemon-owned controls.
- Agents get full public browser control, in the same capability class as a
  normal Playwright/test browser session. `browser.page.call` and
  `browser.context.call` accept a method name plus raw `args`, `kwargs`, or
  named `arguments` so agents can use the Camoufox/Playwright API directly.
- Page operations accept an optional `page_ref`. Context calls that create or
  return pages refresh the session's page refs, so agents can target new
  tabs/windows instead of being limited to the first page.
- JavaScript execution and injection are first-class. Use
  `browser.script.run` for `page.evaluate` and `browser.script.inject` for
  `page.add_init_script`.
- Screenshots are persisted under `/generated-assets/browser/...` and recorded
  as generic artifacts plus browser receipts.
- The method manifest is documentation and drift-test input only. It names
  convenience methods, but it is not a restrictive allowlist. Safety lives in
  daemon ownership, project scoping, run-plan grants, and receipts rather than
  in a narrowed browser API. When a method is not listed in the manifest, use
  the public Camoufox/Playwright page or context method name through
  `browser.page.call` or `browser.context.call`.

## Agent Flow

1. Call `browser.runtime.status`.
2. Call `browser.session.start` with `headless=false` when the operator may
   need to log in or observe posting.
3. Use `browser.page.call` for page operations such as `goto`, `click`, `fill`,
   `press`, `set_input_files`, or any other public page method. Pass `page_ref`
   to target a known tab/page.
4. Use `browser.context.call` for context operations such as `cookies`,
   `storage_state`, `grant_permissions`, `pages`, downloads, routing, or any
   other public context method.
5. Use `browser.script.run` or `browser.script.inject` when direct DOM/page
   JavaScript is the fastest or most faithful control path.
6. Use `browser.page.snapshot` for text state and
   `browser.page.screenshot` for visual evidence.
7. Call `browser.session.stop` when the session is no longer needed.

## First-Layer MCP

The StackOS bridge exposes the browser tools directly alongside
`workspace.startSession` and `workspace.resolve`. Existing Codex sessions need a
restart after installing this change before native `mcp__stackos__browser...`
tools appear. Until then, agents can call the same operations through
`toolbox.call` after the daemon is restarted.

## Toolbox Payloads

Use these payloads when direct `browser.*` MCP tools are not mounted yet:

```json
{"tool_name":"browser.session.start","arguments":{"project_id":1,"profile_key":"default","session_key":"linkedin","headless":false,"response_mode":"raw"}}
```

```json
{"tool_name":"browser.page.call","arguments":{"project_id":1,"session_ref":"browser-session:project-1:default:linkedin","method":"goto","arguments":{"url":"https://www.linkedin.com/"},"response_mode":"raw"}}
```

Pause here for operator login/MFA when needed, then continue with the same
session ref.

```json
{"tool_name":"browser.script.run","arguments":{"project_id":1,"session_ref":"browser-session:project-1:default:linkedin","script":"() => ({ title: document.title, url: location.href })","response_mode":"raw"}}
```

```json
{"tool_name":"browser.script.inject","arguments":{"project_id":1,"session_ref":"browser-session:project-1:default:linkedin","script":"window.__stackosInjected = true;","response_mode":"raw"}}
```

```json
{"tool_name":"browser.page.screenshot","arguments":{"project_id":1,"session_ref":"browser-session:project-1:default:linkedin","full_page":true,"name":"linkedin-publication-proof","response_mode":"raw"}}
```

```json
{"tool_name":"browser.session.stop","arguments":{"project_id":1,"session_ref":"browser-session:project-1:default:linkedin","response_mode":"raw"}}
```

## Receipts

Every mutating browser operation returns raw browser output and records a
redacted receipt. Treat immediate operation output from page/context/script
calls as potentially sensitive: it can include DOM text, cookies, storage
state, or any value returned by arbitrary JavaScript.

Receipts store:

- project, profile, session, and page refs
- operation and method
- URL/origin when available
- redacted input summary, including URL redaction plus script/value lengths and
  SHA-256 hashes
- result summary, with values represented as type/count/length/hash metadata
  instead of raw returned payloads
- title summaries as length/hash metadata, not raw page titles
- screenshot artifact refs when applicable

Receipts are accountability and future continuity. They are not a policy layer
that restricts what public browser actions agents can perform.

Failed live operations also record failed receipts once the project/session can
be resolved. Failure receipts store error type plus a message hash/length, not
the raw browser exception text.

## Manual Smoke Checklist

Use this checklist after runtime or MCP wiring changes:

1. Restart the StackOS daemon and run `stackos doctor`.
2. Start a visible session with `browser.session.start`.
3. Navigate to a simple page with `browser.page.call(method="goto")`.
4. Exercise user actions with `fill` and `click`.
5. Run arbitrary JavaScript with `browser.script.run`.
6. Inject JavaScript with `browser.script.inject`, navigate, and verify it ran.
7. Call at least one raw context method through `browser.context.call`.
8. Capture a screenshot with `browser.page.screenshot` and confirm the artifact
   URI opens under `/generated-assets/browser/...`.
9. Stop the session with `browser.session.stop`.
