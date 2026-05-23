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

1. **`HostHeaderMiddleware`** rejects any `Host:` header that is not
   `localhost`, `127.0.0.1`, or `[::1]` with HTTP 421. Defends against
   DNS rebinding and stray cross-origin probes.
2. **`CORSMiddleware`** is configured `same-origin` only — a cross-origin
   browser fetch can never read responses even if the request went out.
3. **`BearerTokenMiddleware`** enforces the constant-time bearer check,
   minus an explicit whitelist (see below).

## Whitelisted paths

`WHITELIST_PREFIXES` in `stackos/auth.py` lists paths that bypass
the bearer-token check. Currently:

| Path | Why it is whitelisted | Residual exposure |
|---|---|---|
| `/api/v1/health` | `doctor` probes liveness before it has resolved the token (when diagnosing token-related failures). | None worth caring about; the response carries only liveness booleans + version. |
| `/api/v1/auth/ui-token` | The Vue SPA cannot read the on-disk daemon token file from the browser, so it fetches a derived console bearer token at app boot via this endpoint. | **See below.** |
| `/api/v1/ingress/telegram/*` | Telegram webhooks and local relay processes cannot carry the daemon bearer token. The route verifies `X-Telegram-Bot-Api-Secret-Token` against the encrypted Telegram credential before writing communication resources or agent requests. | A caller with the webhook secret can submit Telegram-shaped events for that profile. The default daemon remains loopback-only; public ingress requires an explicit relay/deployment boundary. |

## No-secret auth provider boundary

Provider credentials are daemon-owned. Agents may inspect sanitized auth status
and run daemon-side health probes, but they do not receive raw API keys, OAuth
tokens, refresh tokens, encrypted payloads, or local setup secrets.

`credentials` are the public, opaque profile records. `integration_credentials`
is the encrypted backing store keyed by project, provider, and profile; it is
not an agent-facing credential API. OAuth state rows remain generic
infrastructure for provider flows, not a hard-coded product integration.

The agent-facing MCP bridge exposes `auth.status` and `auth.test` only. Local
human/admin REST operations such as `auth.start`, `auth.revoke`, and
`auth/{provider}/credentials` are daemon-admin setup paths, not agent MCP
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

## REST vs agent execution

REST mutation routes are local-admin surfaces behind the daemon bearer token.
The browser UI receives only a derived REST-only console token. That token can
read REST state, call operation-registry entries whose specs are read-only,
create projects during local setup, and manage provider auth setup for a
project (`auth.start`, secret storage, sanitized auth tests, and revoke), but it
cannot access MCP, mutating operation calls, or general mutation routes. The
installable MCP bridge keeps the daemon bearer inside the bridge process rather
than giving it to the agent. Normal agent workflow writes and external action
execution go through MCP run-plan grants (`runPlan.claimStep` + step-scoped
`resource.upsert`, `artifact.create`, `learning.create`, `decision.record`,
`experiment.*`, `context.snapshot`, and `action.execute`). Possession of the
raw daemon token is therefore treated as local administrator authority, not as a
normal agent credential.

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
calls, `POST /api/v1/projects`, and the narrow provider-auth setup routes under
`/api/v1/projects/{id}/auth/*`; it cannot access `/mcp` and cannot mutate
existing projects, resources, runs, action execution, templates, or project
data.
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
  read-only operation-registry calls, and `POST` to the exact project auth
  setup endpoints. It is never accepted for `/mcp`.
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
  `integration_credentials` rows are unrecoverable. See
  [`./upgrade.md#cross-machine-moves`](./upgrade.md). Rotation stages
  `seed.bin.new` before committing re-encrypted rows; if a crash leaves
  that staged file behind, daemon startup refuses to continue until the
  operator finishes or restores the rotation.
- **Wheel layout (pipx)**: skills and plugins are bundled under
  `stackos/_assets/`. The console script hydrates the user-local plugin
  from those assets via `importlib.resources` so users without the repo on disk
  get the same install. The committed `ui_dist/` ships inside the package, so
  no `pnpm` is needed at user install time.
- **launchd plist**: optional. The plist runs the daemon as the invoking user;
  never as root. `stackos autostart install` owns plist generation in
  both clone and package installs; `make install-launchd` delegates to that
  command. The plist itself does not store the auth token; the daemon reads it
  from `~/.local/state/stackos/auth.token` at startup.

The pipx + launchd path does not change the threat model: the daemon binds
loopback only, the bearer token gates every call, and the seed encrypts
integration credentials at rest. The only delta is installation ergonomics.
