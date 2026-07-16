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
`console_url`, `api_key_url`, `billing_url`, `docs_url`, `support_url`,
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

OAuth providers use the same no-secret boundary, but OAuth execution is
provider-specific:

- provider manifests can declare an interactive OAuth-style auth method
- provider code owns the authorization URL, callback validation, token exchange,
  refresh, scopes, and safe account metadata
- `oauth_states` is generic infrastructure for provider flows, not a complete
  provider-neutral OAuth implementation by itself
- callback and refresh audit metadata must be redacted before persistence

If a provider does not implement a full start/callback/refresh path, its
manifest should say so through setup notes or an explicit deferred state rather
than implying OAuth is ready.

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
