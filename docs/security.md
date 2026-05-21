# content-stack — security notes

This document records security trade-offs and threat-model decisions that
deviate from the simplest "lock everything down" posture. Each section
explains *what* is loosened, *why*, and *what* still defends the surface.

## Auth posture (PLAN.md §D5)

The daemon binds to `127.0.0.1` only. Every `/api/v1/*` and `/mcp/*` call
must carry `Authorization: Bearer <token>` where the token is the contents
of `~/.local/state/content-stack/auth.token` (32 bytes, mode 0600,
generated atomically at first boot, rotated on `make install`).

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

`WHITELIST_PREFIXES` in `content_stack/auth.py` lists paths that bypass
the bearer-token check. Currently:

| Path | Why it is whitelisted | Residual exposure |
|---|---|---|
| `/api/v1/health` | `doctor` probes liveness before it has resolved the token (when diagnosing token-related failures). | None worth caring about; the response carries only liveness booleans + version. |
| `/api/v1/auth/ui-token` | The Vue SPA cannot read the on-disk daemon token file from the browser, so it fetches a derived read-only bearer token at app boot via this endpoint. | **See below.** |

## No-secret auth provider boundary

Provider credentials are daemon-owned. Agents may inspect sanitized auth status
and run daemon-side health probes, but they do not receive raw API keys, OAuth
tokens, refresh tokens, encrypted payloads, or local setup secrets.

`integration_credentials` is the encrypted backing store for provider secrets
until the generic auth-provider tables are promoted. OAuth state rows remain
generic infrastructure for future provider flows, not a hard-coded product
integration.

The agent-facing MCP bridge exposes `auth.status` and `auth.test` only. Local
human/admin REST operations such as `auth.start`, `auth.revoke`, and
`auth/{provider}/credentials` are daemon-admin setup paths, not agent MCP
tools. When a tool needs a credential, the agent passes an opaque
`credential_ref`; the daemon resolves and decrypts the backing secret inside
the vendor wrapper process.

Every auth usage/refresh audit payload is passed through the shared redactor
before persistence. Secret-like keys such as `api_key`, `access_token`,
`refresh_token`, `authorization`, and nested equivalents are stored as
`[redacted]`.

## REST vs agent execution

REST mutation routes are local-admin surfaces behind the daemon bearer token.
The browser UI receives only the derived read-only UI token, and the installable
MCP bridge keeps the daemon bearer inside the bridge process rather than giving
it to the agent. Normal agent workflow writes and external action execution go
through MCP run-plan grants (`runPlan.claimStep` + step-scoped
`resource.upsert`, `artifact.create`, `learning.create`, `decision.record`,
`experiment.*`, `context.snapshot`, and `action.execute`). Possession of the
raw daemon token is therefore treated as local administrator authority, not as a
normal agent credential.

## Token-bootstrap trade-off (M5.A)

Adding `/api/v1/auth/ui-token` accepts a small reduction in defence depth.

**What's gained.** The browser-based UI works without prompting the user
to paste a token by hand. The disk-backed daemon token never leaves the
daemon machine and never lands in a localStorage / sessionStorage where
a hostile script could read it (the SPA holds only the derived UI token
in a Pinia-store ref).

**What's lost.** Any other process on the same machine that can connect
to `127.0.0.1:5180` can fetch the UI token by sending `GET
/api/v1/auth/ui-token` with no credentials. That token is accepted only
for safe REST reads under `/api/v1/*`; it cannot mutate data and cannot
access `/mcp`. Previously, only a process that could read `auth.token`
(mode 0600, owned by the daemon's user) could obtain any bearer token.
On a single-user macOS or Linux box that's a near-zero delta (same-user
processes already had file access). On a multi-user / shared-tenant box,
the residual exposure is read access to the local operator console data.

**Mitigations already in place.**

- The endpoint never logs the token. Server logs and structured-logging
  output redact it as part of normal practice.
- The returned token is derived from, but not equal to, the disk-backed
  daemon token. `BearerTokenMiddleware` accepts it only for `GET`,
  `HEAD`, and `OPTIONS` requests under `/api/v1/*`.
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
machines and want even read-only browser bootstrap removed can:

1. **Run the UI in a separate browser profile** that has no other tabs.
   This eliminates same-origin script attacks against the SPA itself.
2. **Disable the bootstrap and paste a UI token by hand.** A future
   hardening flag (out of scope for M5) will let operators turn the
   bootstrap off; the SPA will then prompt for a token at first load
   and store it in `sessionStorage` for the tab's lifetime. Operators
   who need this today can `chmod 0400` the token file and short-circuit
   the bootstrap by editing `content_stack/auth.py`'s
   `WHITELIST_PREFIXES`. This is intentionally a code change, not a
   runtime flag, to make sure any operator going down that path has read
   the implications.

## Per-tool rate limits (PLAN.md §828)

Middleware enforces 100 calls/min per MCP tool and 1000 calls/min
aggregate per `Authorization: Bearer` token. Breach returns 429 with
`retry_after` seconds and JSON-RPC code -32011. Bulk tools count as N
calls. This caps blast radius if a token is exfiltrated and an attacker
tries to drive a tight loop against a paid integration.

## Distribution + install posture (M9)

Both the clone-mode `make install` and the pipx-mode
`content-stack install` paths share the same install code:

- **Auth token**: created by `content-stack init`, `content-stack install`,
  or `make install` before MCP registration. `pipx upgrade` does NOT rotate
  by itself; operators rotate explicitly via `content-stack rotate-token
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
  `content_stack/_assets/`. The console script hydrates the user-local plugin
  from those assets via `importlib.resources` so users without the repo on disk
  get the same install. The committed `ui_dist/` ships inside the package
  (D8) — no `pnpm` needed at user install time.
- **launchd plist**: optional. The plist runs the daemon as the
  invoking user; never as root. `make install-launchd` writes the
  plist with mode 0644 (world-readable, owner-writable). The plist
  itself does not store the auth token; the daemon reads it from
  `~/.local/state/content-stack/auth.token` at startup.

The M9 pipx + launchd path does not change the threat model: the
daemon binds loopback only, the bearer token gates every call, and
the seed encrypts integration credentials at rest. The only delta is
installation ergonomics.
