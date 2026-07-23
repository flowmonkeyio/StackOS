# StackOS Security Notes

This document records security trade-offs and threat-model decisions that
deviate from the simplest "lock everything down" posture. Each section
explains *what* is loosened, *why*, and *what* still defends the surface.

## Local Daemon Auth Posture

The daemon binds to `127.0.0.1:5180` only and serves the committed StackOS UI
bundle from the same origin. The Vue/Vite development UI, when used, runs on
`127.0.0.1:5173` and proxies `/api` and `/mcp` to the daemon on `5180`. Every
direct `/api/v1/*` and `/mcp/*` call must carry `Authorization: Bearer <token>`
where the token is the contents of `~/.local/state/stackos/auth.token`
(32 bytes, mode 0600, generated atomically when missing). Installs and upgrades
do not rotate an existing token; operators rotate explicitly with
`stackos rotate-token --yes` or `make rotate-token`.

Three middlewares form the request gauntlet, applied in this order
(outermost first):

1. **`HostHeaderMiddleware`** rejects any non-ingress `Host:` header that is not
   `localhost`, `127.0.0.1`, or `[::1]` with HTTP 421. Provider webhook ingress
   paths accept tunnel/deployed hosts because Telegram and Slack must call them
   from outside the machine; those paths still verify provider secrets or
   signatures before any write. The only other host exception is an exact GET
   to `/api/v1/auth/oauth/callback` whose host must equal the operator-configured
   OAuth callback origin.
2. **`CORSMiddleware`** is configured `same-origin` only — a cross-origin
   browser fetch can never read responses even if the request went out.
3. **`BearerTokenMiddleware`** enforces the constant-time bearer check,
   minus an explicit whitelist (see below).

## Public auth exceptions

`WHITELIST_PREFIXES` in `stackos/auth.py` lists paths that bypass
the bearer-token check. OAuth callback handling is a separate exact-method and
exact-path exception so no sibling route inherits it. Currently:

| Path | Why it is whitelisted | Residual exposure |
|---|---|---|
| `/api/v1/health` | `doctor` probes liveness before it has resolved the token (when diagnosing token-related failures). | None worth caring about; the response carries only liveness booleans + version. |
| `/api/v1/auth/ui-token` | The Vue SPA cannot read the on-disk daemon token file from the browser, so it fetches a derived console bearer token at app boot via this endpoint. | **See below.** |
| `GET /api/v1/auth/oauth/callback` (exact route) | OAuth providers cannot carry the daemon bearer token when redirecting the browser. The short-lived, one-time state transaction authorizes only this callback. | The request host must equal the configured callback host; state is digested, bound, expiring, and atomically consumed. The response is an immediate sanitized 303 redirect. |
| `/api/v1/ingress/telegram/*` | Telegram webhooks cannot carry the daemon bearer token. The route verifies `X-Telegram-Bot-Api-Secret-Token` against the encrypted Telegram credential before writing communication resources or agent requests. | A caller with the webhook secret can submit Telegram-shaped events for that profile. This path also bypasses loopback-only Host checks so deployed/tunnel hosts work; all non-ingress API paths remain loopback-host guarded. |
| `/api/v1/ingress/slack/*` | Slack Events API and Interactivity requests cannot carry the daemon bearer token. The route verifies `X-Slack-Signature` against the encrypted Slack signing secret using the raw body and timestamp before writing communication resources or agent requests. | A caller with the Slack signing secret can submit Slack-shaped events for that profile. This path also bypasses loopback-only Host checks so deployed/tunnel hosts work; all non-ingress API paths remain loopback-host guarded. |
| `/api/v1/ingress/hubspot/*` | HubSpot webhook and custom workflow-action requests cannot carry the daemon bearer token. The route verifies the provider-documented v3 timestamped HMAC for webhook batches or v2 digest for workflow actions against the daemon-held app client secret and configured public HTTPS URI before parsing or writing. | A caller with the app client secret can submit HubSpot-shaped events for the one configured app/account profile. Exact portal/app checks and event/definition allowlists gate agent-request creation. This path bypasses loopback-only Host checks; the signed URI is derived from the configured ingress endpoint, never the incoming `Host` header. |

## No-secret auth provider boundary

Provider credentials are daemon-owned. Agents may inspect sanitized auth status
and run daemon-side health probes, but they do not receive raw API keys, OAuth
tokens, refresh tokens, encrypted payloads, or local setup secrets.

`credentials` are the public, opaque profile records. `integration_credentials`
is the encrypted backing store keyed by project, provider, and profile; it is
not an agent-facing credential API. OAuth state rows are the provider-neutral
transaction core; trusted provider contracts supply protocol data without
turning OAuth into agent-visible or plugin-stored secret state.

In normal agent sessions, the MCP bridge exposes `auth.status` and `auth.test`
through `toolbox.call`; it does not advertise them as direct tools. Local
human/admin REST operations such as `auth.start`, `auth.revoke`, and
`auth/{provider}/credentials` are daemon-admin setup paths, not agent toolbox
tools. When a tool needs a credential, the agent passes an opaque
`credential_ref`; the daemon resolves and decrypts the backing secret inside
the vendor wrapper process.

Provider manifests declare typed `auth_methods`. The UI renders those methods
directly, so an API-key system, SMTP system, OAuth2 system, and custom webhook
system each get the right fields without exposing secrets to agents or storing
credential material in plugin config.

Every auth usage/refresh audit payload is passed through the shared redactor
before persistence. Secret-like keys such as `api_key`, `access_token`,
`refresh_token`, `authorization`, and nested equivalents are stored as
`[redacted]`.

## OAuth callback and renewal threat model

StackOS owns one callback path: `/api/v1/auth/oauth/callback`. The callback
origin defaults to the local daemon and can be changed with
`STACKOS_OAUTH_CALLBACK_BASE_URL`; a configured value must be one origin with
no path, query, credentials, or fragment, and must use HTTPS except for an HTTP
loopback origin. `auth.start` never accepts a redirect URI from its caller, so a
tool or UI request cannot turn the daemon into an open OAuth redirect target.

Each start creates a random state value but persists only its SHA-256 digest,
binds it to the project/provider/credential transaction, gives it a short TTL,
and atomically consumes both earlier attempts and the callback attempt. PKCE
S256 is generated when the provider contract marks it supported or required;
the verifier and pending provider application values stay in the encrypted
credential payload. Reconnect uses compare-and-swap semantics so a late or
duplicate callback cannot overwrite newer credentials, and a denied or failed
reconnect does not destroy a still-active prior token.

The callback accepts only bounded `state`, `code`, and provider error fields.
It never logs, persists, or forwards raw callback values to the UI. Success and
failure both leave the callback URL through an HTTP 303 response with
`Cache-Control: no-store`, `Pragma: no-cache`, `Referrer-Policy: no-referrer`,
and a deny-all content security policy. The destination query contains only a
sanitized status plus safe project/provider identity when known.

At action time, refresh and client-credentials acquisition happen inside the
daemon under a per-credential async lock. An `updated_at` compare-and-swap
prevents a slow response from replacing a newer token. Known action-required
scopes are checked before connector dispatch. Token endpoint failures and
missing/unknown scope failures return repair guidance and persist only redacted
audit data. Timeouts, network failures, rate limits, and provider 5xx responses
remain retryable without poisoning the stored connection; terminal authorization
failures such as `invalid_grant` mark it `repair-required`. Replacing manual
token material clears the prior token's recorded grants.

## Write-only action payload transit

Some multi-tenant actions need a tenant-owned string such as an SMTP password
while the connected provider credential belongs to an administrator. These are
different trust domains. Do not create another provider Connection or overload
`credential_ref` for the tenant value.

The authorized agent sends the value once to the project-scoped, MCP-only
`secret.set` operation and receives an opaque `secret_ref`. StackOS encrypts the
value at rest and exposes no plaintext read/list operation. Action input carries
only the exact marker `{"$secret_ref":"secret_..."}`. Validation checks the
reference without decryption; immediately before connector dispatch the daemon
resolves it into a fresh request copy. Audit, idempotency, and file-backed
request envelopes retain the symbolic marker.

For every value StackOS resolves, the shared action boundary removes exact
occurrences from connector results, structured errors, metadata, response
files, and controlled exception text before persistence or return. Connector
request representations omit payloads entirely. This defense does not claim to
control telemetry in an external agent host or a provider that transforms or
partially echoes a value; connectors must still avoid logging input and
providers should not return credentials.

## REST vs agent execution

REST mutation routes are local-admin surfaces behind the daemon bearer token.
The browser UI receives only a derived REST-only console token. That token can
read REST state, call operation-registry entries whose specs are read-only,
create projects during local setup, and manage provider auth setup for a
project (`auth.start`, secret storage and edits, sanitized auth tests, and
revoke). Provider-auth route definitions own method validity inside that
namespace. Mutating operation calls are allowed only when the operation's REST
surface declares `browser_safe=True` for explicit local setup. It cannot access
MCP, arbitrary mutating operation calls, tracker lifecycle writes, or general
mutation routes. The
installable MCP bridge keeps the daemon bearer inside the bridge process rather
than giving it to the agent. Normal agent workflow writes and external action
execution go through MCP run-plan grants (`runPlan.claimStep` + step-scoped
`resource.upsert`, `artifact.create`, `artifact.update`, `artifact.archive`,
`artifact.supersede`, `learning.create`, `decision.record`, `experiment.*`,
`context.snapshot`, and `action.execute`). One explicit direct
provider action can use `action.run`, which still requires workspace project
scope, daemon-held credentials, direct-action confirmation, derived or
caller-provided idempotency for non-read calls, redaction, and action-call
audit. External provider action outputs are sanitized and file-backed by
default; callers receive response-file paths, `schema_ref`, metadata, and no
plaintext secrets. Intentional artifact rows can be inspected with bounded
`artifact.read`. Possession of the raw daemon token is therefore treated as
local administrator authority, not as a normal agent credential.

## Daemon-Owned Browser Automation

StackOS browser automation uses a daemon-owned Playwright Chromium runtime. Agents can
open persistent sessions, call public page/context methods, run arbitrary page
JavaScript, inject scripts, and capture screenshots. This is intentionally a
full-control automation surface, similar to a normal browser automation test
session, because publishing/admin workflows often require the same freedom.
It is a local trusted-administrator capability, not an externally exposed
multi-tenant browser sandbox.

The boundary is not a browser-method allowlist. The boundary is local daemon
auth, workspace/project scoping, run-plan grants when a workflow step uses the
tools, and audit receipts. Browser profile directories, executable paths, raw
handles, and daemon-local profile storage stay daemon-side. Screenshots are
stored as generated-assets artifacts, and browser receipts record method names,
session/page refs, URL/origin where available, hashed input summaries, result
summaries, and artifact refs.

Immediate browser operation responses are intentionally raw. Page/context calls,
storage-state reads, cookie reads, DOM reads, screenshots, and arbitrary
JavaScript can return sensitive page data to the calling agent because that is
the requested full-control browser surface. Persisted receipts and transport
errors are the redacted surfaces; callers must treat raw browser outputs as
sensitive working data.

Agents may pass normal Playwright launch options, but StackOS rejects launch
options that would override daemon-owned controls such as the executable path,
browser channel, persistent-context mode, or profile directory. Runtime status
exposes readiness booleans and same-project live session refs; it does not
expose local browser executable paths or profile paths.

## UI Token Bootstrap Trade-Off

Adding `/api/v1/auth/ui-token` accepts a reduction in defence depth.

**What's gained.** The browser-based UI works without prompting the user
to paste a token by hand. The disk-backed daemon token never leaves the
daemon machine and never lands in a localStorage / sessionStorage where
a hostile script could read it (the SPA holds only the derived UI token
in a Pinia-store ref).

**What's lost.** Any other process on the same machine that can connect
to `127.0.0.1:5180` can fetch the UI token by sending `GET
/api/v1/auth/ui-token` with no credentials. That token is accepted only
for REST reads, read-only `POST /api/v1/operations/{operation}/call` transport
calls, `POST /api/v1/projects`, narrow no-secret setup operations such as
`communicationProfile.upsert` and `ingressEndpoint.*`, and the
provider-auth setup routes under `/api/v1/projects/{id}/auth/*`. It can also
call local setup operations whose REST surface is explicitly marked
browser-safe; it cannot access `/mcp` and cannot mutate existing projects,
tracker lifecycle, resources, runs, action execution, templates, or project data.
Previously, only a process that could read `auth.token` (mode 0600, owned by
the daemon's user) could obtain any bearer token. On a single-user macOS or
Linux box that's a near-zero delta (same-user processes already had file
access). On a multi-user / shared-tenant box, the residual exposure is read
access to the local operator console data plus the ability to create projects
and add/test/revoke provider credentials through the local setup surface.

**Mitigations already in place.**

- The endpoint never logs the token. Server logs and structured-logging
  output redact it as part of normal practice.
- The returned token is derived from, but not equal to, the disk-backed
  daemon token. `BearerTokenMiddleware` accepts it only for `GET`,
  `HEAD`, and `OPTIONS` requests under `/api/v1/*`, `POST /api/v1/projects`,
  read-only operation-registry calls, narrow no-secret setup operations, and
  explicit local console operations such as `tracker.updateTask`, plus `POST`
  to the exact project auth setup endpoints. It is never accepted for `/mcp`.
- The `HostHeaderMiddleware` rejects requests with a forged `Host:`
  header, so a remote attacker who has somehow proxied to the loopback
  port (e.g. through a compromised tunnel) is rebuffed.
- `CORSMiddleware` is same-origin, so a malicious page in another tab of
  the user's browser cannot read the response — the browser will refuse
  to expose the body to the attacker's JavaScript.
- The daemon binds to loopback only and rejects `--host 0.0.0.0` at CLI
  parse time, so off-machine callers get connection-refused before any
  middleware runs.

**When to reach for stricter posture.** Operators who run multi-user
machines and want browser bootstrap removed can:

1. **Run the UI in a separate browser profile** that has no other tabs.
   This eliminates same-origin script attacks against the SPA itself.
2. **Disable the bootstrap and paste a UI token by hand.** A future
   hardening flag could let operators turn the bootstrap off; the SPA would
   then prompt for a token at first load and store it in `sessionStorage` for
   the tab's lifetime. Operators who need this today can `chmod 0400` the
   token file and short-circuit the bootstrap by editing `stackos/auth.py`
   `WHITELIST_PREFIXES`. This is intentionally a code change, not a runtime
   flag, to make sure any operator going down that path has read the
   implications.

## Rate-Limit Posture

StackOS does not currently advertise a global per-tool request-rate middleware.
Provider wrappers may apply provider-specific pacing, retries, and budget
pre-checks where those contracts are implemented. Do not document hard
per-tool daemon rate limits until enforcement exists in middleware and tests.

## Distribution And Install Posture

The canonical setup contract is in [`./setup.md`](./setup.md). Clone-mode
`make install` and package-mode `stackos install` land at the same local
state, plugin, and MCP bridge contract:

- **Auth token**: created by `stackos init`, `stackos install`,
  or `make install` before MCP registration. `pipx upgrade` does NOT rotate
  by itself; operators rotate explicitly via `stackos rotate-token
  --yes` or `make rotate-token`. Rotation refreshes saved MCP configs, but a
  daemon that is already running keeps the token it loaded at startup until it
  is restarted.
- **Seed file**: never rotated by install. Cross-machine moves
  require copying `seed.bin` alongside the DB; without it,
  encrypted provider credentials and payload transit values are unrecoverable.
  See [`./upgrade.md#cross-machine-moves`](./upgrade.md). Rotation stages
  `seed.bin.new` before committing re-encrypted rows; if a crash leaves
  that staged file behind, daemon startup refuses to continue until the
  operator finishes or restores the rotation.
- **Wheel layout (pipx)**: the StackOS plugin and any root-level skills that
  exist in the source tree are bundled under `stackos/_assets/`. The console
  script hydrates the user-local plugin from those assets via
  `importlib.resources` so users without the repo on disk get the same install.
  The committed `ui_dist/` ships inside the package, so no `pnpm` is needed at
  user install time.
- **launchd plist**: optional. The plist runs the daemon as the invoking user;
  never as root. `stackos autostart install` owns plist generation in
  both clone and package installs; `make install-launchd` delegates to that
  command. The plist itself does not store the auth token; the daemon reads it
  from `~/.local/state/stackos/auth.token` at startup.
- **macOS desktop app**: the `stackos` Electron shell loads only the loopback
  daemon UI, keeps Node.js out of a sandboxed renderer, uses a preload bridge
  for bounded service/update commands, and opens non-StackOS links in the
  system browser. First launch and post-update repair call the existing CLI
  install path, so desktop distribution does not add a second credential store
  or rotate seed/token material.

The pipx + launchd path does not change the threat model: the daemon binds
loopback only, the bearer token gates every call, and the seed encrypts
integration credentials at rest. The only delta is installation ergonomics.
