# StackOS Auth Providers

StackOS treats credentials as daemon-owned infrastructure, not agent context.
Agents can inspect sanitized provider state, test whether a connection works,
and pass opaque credential references into granted tools. They must never
receive API keys, OAuth tokens, refresh tokens, encrypted payloads, or local
setup secrets.

## Model

The auth-provider layer uses:

- `auth_providers`: provider metadata synced from plugin manifests.
- `credentials`: opaque refs over encrypted provider credential profiles.
- `integration_credentials`: encrypted secret payloads keyed by project,
  provider, and profile.
- `credential_scopes`: granted scopes for a credential ref.
- `credential_accounts`: provider account metadata safe to show to agents.
- `oauth_states`: local-human OAuth state nonces with expiry and consumption.
- `credential_usage_events`: redacted audit trail for tests/revocations/use.
- `credential_refresh_events`: redacted audit trail for OAuth/refresh attempts.

The stable agent identifier is `credential_ref`, for example `cred_...`.
Agents may also see safe labels, profile keys, auth method keys, status,
scopes, and account metadata. They never receive credential field values.

## Agent Surface

Normal agents may use:

- `auth.status`: list provider metadata and sanitized connection status.
- `auth.test`: run a daemon-side health probe and return a sanitized result.
- `toolProfile.resolve`: resolve one safe provider/profile/credential tuple
  for execution without dumping the broader auth/provider catalog into context.

Normal agents may not use:

- `auth.start`: starts local setup or OAuth and is a human/admin operation.
- `auth.revoke`: removes daemon-held secrets and is a human/admin operation.
- plaintext credential setup routes or local UI admin mutations.

The MCP bridge exposes these through `toolbox.call` in normal agent sessions.
Agents should prefer `toolProfile.resolve` when they already know which
provider/profile they need; `auth.status` is still available for diagnostics.

## Setup Flow

1. The agent inspects required providers through plugin/catalog metadata.
2. The agent calls `toolbox.call` for `toolProfile.resolve`, or for
   `auth.status` when it needs full sanitized diagnostics.
3. If setup is missing, the agent points the operator to
   `/projects/{project_id}/connections?provider_key={provider_key}` in the local
   UI and uses the provider manifest `setup` metadata to answer where to
   register, where to find the vendor API key/token, where billing/credits live,
   and which official docs apply. Only the operator/local admin uses setup
   routes or interactive OAuth starts.
4. The provider's plugin must be enabled for the project. The UI filters setup
   choices and the daemon independently rejects start/store attempts for an
   explicitly disabled plugin.
5. The operator chooses the provider auth method and enters the fields required
   by that method, or starts the provider OAuth flow when one is configured.
   Local UI setup stores the credential and immediately attempts the same
   provider-neutral credential test. A failed or unavailable test remains a
   repairable connection; it is never reported as verified.
6. An agent may later call `toolbox.call` for `auth.test` with the selected
   `credential_ref` when work needs a fresh readiness check.
7. The daemon decrypts the secret inside its process, calls the connector,
   records a redacted usage event, and returns sanitized status/metadata.

No step requires an agent prompt, workflow template, or repository file to carry
secret material.

Provider manifests declare `auth_methods`. Each method defines its fields,
which fields are daemon-secret, whether the payload is raw or JSON, and whether
setup is an interactive OAuth-style flow. The Connections UI renders this
schema directly:

Provider manifests also declare safe self-service setup metadata under
`config.setup`. It is not credential material. It may include
`credential_label`, `setup_note`, official `homepage_url`, `signup_url`,
`console_url`, `credential_url`, `billing_url`, `docs_url`, `support_url`,
`fallback_url`, `fallback_reason`, per-field `url_confidence`, and
`verified_at`. If an exact API-key or billing page is not publicly verifiable,
use the closest official homepage/docs/console URL and mark that field
`directional` so agents can say it is the starting point rather than a verified
deep link.

- API-key providers usually have one secret `api_key` field.
- Slack bot providers expose only secret `bot_token` and `signing_secret`
  setup fields. StackOS discovers safe workspace and bot identity metadata with
  Slack `auth.test`; communication identity and trigger policy live in project
  resources, not credentials.
- SMTP-style systems can expose host, port, username, password, TLS, and sender
  fields in a single method, with only password/token fields encrypted.
- OAuth providers can expose an interactive method or a daemon-side
  refresh-token/client-credentials method, depending on the provider contract.

Non-secret method fields are persisted only as safe credential config. Secret
method fields are serialized into the encrypted backing payload. The old
untyped secret blob route is not part of the public contract.

## OAuth Providers

OAuth uses one daemon-owned lifecycle plus a small trusted contract for each
provider. The shared lifecycle owns transaction state, callback handling, token
storage, refresh/acquisition, scope enforcement, concurrency, audit, and safe
failure behavior. A provider contract supplies only protocol facts such as its
authorization/token endpoints, consent scopes, client-auth style, PKCE mode,
fixed authorization parameters, trusted response metadata, and an exceptional
post-exchange hook when the provider actually requires one. Connectors receive
an already-usable credential; they do not implement OAuth.

### Operator OAuth Quick Guide

StackOS currently uses bring-your-own OAuth applications. For each interactive
provider, an operator first creates or selects an application in that provider's
developer console and registers StackOS's exact callback URI.

The provider-by-provider console walkthrough, scope ledger, and static
Cloudflare Pages callback design live in
[`oauth-provider-setup.md`](./oauth-provider-setup.md). Its upload-ready source
exists under `workers/oauth-callback-relay/public`, but it is not deployed. The
page uses browser navigation to reach this same existing callback route on
loopback; it does not add a Worker, Pages Function, separate completion route,
or OAuth lifecycle. The current callback behavior below remains the runtime
truth.

The default local callback URI is:

```text
http://127.0.0.1:5180/api/v1/auth/oauth/callback
```

Providers that require a public HTTPS callback can use the operator-controlled
Pages origin configured with `STACKOS_OAUTH_CALLBACK_BASE_URL`:

```text
STACKOS_OAUTH_CALLBACK_BASE_URL=https://auth.stackos.flowmonkey.io
https://auth.stackos.flowmonkey.io/api/v1/auth/oauth/callback
```

That HTTPS page must navigate the callback to the fixed loopback StackOS route.
The callback origin and local target are application-owned; a user, agent, or
provider cannot select an arbitrary return path.

The operator then completes this flow:

1. Open `/projects/{project_id}/connections` in the StackOS UI and choose
   **Add connection**.
2. Select the provider and interactive OAuth method, enter the provider
   application's client fields, and choose **Connect**.
3. StackOS encrypts those fields, creates a short-lived one-use transaction,
   and sends the browser to the provider's authorization page.
4. The user signs in and approves the requested permissions at the provider.
5. The provider returns the browser to the fixed StackOS callback. StackOS
   validates the transaction, exchanges the code, encrypts the tokens, and
   records provider-returned scopes and safe account metadata.
6. StackOS returns the browser to the committed local UI at:

   ```text
   http://127.0.0.1:5180/projects/{project_id}/connections?oauth_status={status}&provider_key={provider_key}
   ```

   `{status}` is only `connected`, `denied`, `expired`, `repair-required`, or
   `error`. The UI displays the result and immediately removes those query
   fields. Provider codes, state, tokens, secrets, and error descriptions are
   never placed in this URL.

The canonical post-callback UI is the daemon-served UI on port `5180`, including
when development uses the Vite UI on port `5173`. This local return assumes the
OAuth flow was started in a browser on the same machine as StackOS.

The authorization-code flow is:

1. The local operator stores the provider application's required fields through
   Connections. Secret fields remain encrypted; safe account/tenant fields stay
   in credential config.
2. `auth.start` accepts the provider, auth method, and opaque `credential_ref`.
   It never accepts a caller-selected callback URL. StackOS uses the fixed
   `/api/v1/auth/oauth/callback` route at the configured callback origin.
3. StackOS creates a short-lived transaction bound to the project, provider,
   credential profile, and auth method. Only a digest of the random state is
   stored. Any PKCE verifier and pending application values remain encrypted.
   Starting again consumes earlier uncompleted transactions for that profile.
4. The exact public callback route consumes the state atomically, handles denial
   or expiry, exchanges the code once, applies any provider hook, and stores the
   normalized token payload. A failed reconnect preserves a still-active prior
   credential; otherwise the connection becomes `repair-required`.
5. Before an action runs, the resolver refreshes an expired authorization-code
   token or acquires a client-credentials token when needed. A per-credential
   async lock plus an `updated_at` compare-and-swap prevents concurrent requests
   from overwriting newer token material. Generic profile editing exposes only
   provider-declared setup fields; unchanged setup secrets retain the acquired
   token and grants, while changed token/application material resets them.
6. The resolver compares the action manifest's `required_scopes` with the
   credential's known grants before connector dispatch. Missing or unknown
   required scopes fail safely without calling the provider action.

Callback, refresh, and acquisition failures are redacted in persisted audit and
returned diagnostics. The callback immediately redirects with HTTP 303 to the
local Connections page using only `connected`, `denied`, `expired`,
`repair-required`, or `error`; provider codes, state, token values, and provider
error descriptions are never forwarded into the UI URL. A timeout, network
failure, rate limit, or provider 5xx during renewal leaves the stored credential
retryable; only a terminal authorization failure such as `invalid_grant` moves
it to `repair-required`.

Manual OAuth-token methods remain supported for compatibility. A manual profile
with a refresh token uses the same core renewal path when the provider contract
supports it. Replacing manual token material clears any grants recorded for the
old token. Provider-returned grants from a later exchange restore known scope
state; StackOS does not invent grants when that response omits them. An
access-token-only profile can be tested and can run actions with no declared
scope requirement, but a scope-gated action fails closed until grants are known.
Use the interactive method for the normal scope-gated path. This compatibility
surface is not a second OAuth implementation.

### Built-In OAuth Provider Matrix

“Interactive” means StackOS implements start, fixed callback, exchange, and
renewal for that provider. “Core client credentials” means the operator stores
the application fields and StackOS acquires/renews the access token before
dispatch. “Manual compatible” is an intentionally retained token-import method,
not a claim that the operator must manage tokens for the interactive method or
that an imported token bypasses the known-scope gate.

| Provider | Current StackOS auth path | Provider-specific contract notes | Action scope gate |
| --- | --- | --- | --- |
| Google Ads | Interactive authorization code; manual refresh-token compatible | Shared Google endpoints; PKCE supported; offline consent | `adwords` |
| Google Workspace | Interactive authorization code; manual token compatible | Shared Google endpoints; PKCE supported; offline consent | Gmail send and Calendar events |
| Google Search Console | Interactive authorization code; manual access/refresh-token compatible | Shared Google endpoints; PKCE supported; offline consent | `webmasters.readonly` |
| Google Analytics | Interactive authorization code; manual access/refresh-token compatible | Shared Google endpoints; PKCE supported; offline consent | `analytics.readonly` |
| Google Tag Manager | Interactive authorization code; manual access/refresh-token compatible | Shared Google endpoints; PKCE supported; offline consent | `tagmanager.readonly` |
| Meta Ads | Interactive authorization code; manual token compatible | Meta login exchange plus the required short-to-long-lived token exchange | Per-action `ads_read` / `ads_management` |
| Microsoft 365 | Interactive authorization code; manual token compatible | Tenant-validated endpoints; PKCE required | Graph `Mail.Send` / `Calendars.ReadWrite` |
| Outreach | Interactive authorization code; manual token compatible | Provider endpoints and consent scope | `sequenceStates.write` |
| Pipedrive | Interactive authorization code; manual OAuth token or API-token alternative | HTTP Basic token exchange; trusted `api_domain` metadata | `deals:read` / `search:read` |
| Salesforce | Interactive authorization code; manual token compatible | Production, sandbox, or validated My Domain; PKCE required; trusted `instance_url` | `api` |
| Salesloft | Interactive authorization code; manual OAuth token or API-key alternative | Provider endpoints; body client authentication | `cadences:write` |
| Taboola | Core client credentials | Body client authentication | No action-level scope declaration currently required |
| Reddit | Core client credentials | HTTP Basic token acquisition; `user_agent` stays in the encrypted application payload | No action-level scope declaration currently required |
| HubSpot | Manual OAuth token only | Existing actions remain available; interactive OAuth is explicitly outside this delivery | Existing action contracts |
| X API | Manual OAuth token only | Provider actions are explicitly deferred | Not executable |
| LinkedIn | Manual OAuth token only | Provider actions are explicitly deferred | Not executable |

Do not add a provider subclass merely to repeat the generic flow. Add a trusted
contract row for protocol data, and add dedicated code only for a real variant
such as Meta's second exchange or trusted provider-specific response metadata.

## Connections UI Contract

The local Connections screen is service/account first:

- primary action: `Add connection`
- main list: connected services grouped by provider, with multiple named
  connections per service; revoked history is excluded
- connection rows: safe label, account metadata, profile key, status, last
  tested time, expiry, and opaque `credential_ref`
- edit action: reuse the selected provider auth method and the same credential
  form used for create; prefill safe fields, leave secrets blank, and preserve
  an existing secret unless the operator supplies a replacement
- setup panel: enabled-plugin providers only, rendered from `auth_methods`
- interactive setup: one `Connect` action validates and stores the application
  fields, calls `auth.start` with only the auth method and opaque
  `credential_ref`, then navigates to the returned HTTPS authorization URL
- callback result: a global callout renders `connected`, `denied`, `expired`,
  `repair-required`, or `error`, and the UI clears callback query parameters
  immediately after reading them
- diagnostics: `auth.test` returns a sanitized result and records the same
  redacted outcome in the existing credential usage audit; a failed test does
  not disable the stored credential

`GET /projects/{project_id}/auth/status` is the UI's canonical provider and
connection read model. Its `providers` collection avoids a second provider
catalog synchronization request during normal Connections loading.

Built-in placeholder providers for project-local custom tools, such as
`custom-media-tool` and `custom-gtm-tool`, are not normal service credentials.
They stay hidden from the add-connection picker until a project-local plugin
declares a concrete HTTP connector, allowlisted endpoint, auth injection fields,
timeout policy, and response contract.

## Known Architecture Follow-Ups

- Provider identity is still mostly bare `provider_key`. User-installed plugins
  can collide unless auth routes, credential storage, and action manifests move
  to a stable provider ref such as `plugin_slug.provider_key` or
  `auth_provider_id`.
- Multiple credentials are supported through `profile_key`, but account/scopes
  need richer population from safe setup fields and provider test metadata.
- Template `auth_ref` is a local requirement label. Execution should document
  or model the binding from template auth requirement to selected
  `credential_ref`, for example `auth_bindings`.
