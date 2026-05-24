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

The MCP bridge advertises `toolProfile.resolve`, `auth.status`, and `auth.test`.
Agents should prefer `toolProfile.resolve` when they already know which
provider/profile they need; `auth.status` is still available for diagnostics.

## Setup Flow

1. The agent inspects required providers through plugin/catalog metadata.
2. The agent calls `toolProfile.resolve` for the project and provider key, or
   `auth.status` when it needs full sanitized diagnostics.
3. If setup is missing, the agent points the operator to
   `/projects/{project_id}/connections?provider_key={provider_key}` in the local
   UI. Only the operator/local admin uses setup routes or interactive OAuth
   starts.
4. The operator chooses the provider auth method and enters the fields required
   by that method, or starts the provider OAuth flow when one is configured.
5. The agent calls `auth.test` with the selected `credential_ref`.
6. The daemon decrypts the secret inside its process, calls the connector,
   records a redacted usage event, and returns sanitized status/metadata.

No step requires an agent prompt, workflow template, or repository file to carry
secret material.

Provider manifests declare `auth_methods`. Each method defines its fields,
which fields are daemon-secret, whether the payload is raw or JSON, and whether
setup is an interactive OAuth-style flow. The Connections UI renders this
schema directly:

- API-key providers usually have one secret `api_key` field.
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
  connections per service
- connection rows: safe label, account metadata, profile key, status, last
  tested time, expiry, and opaque `credential_ref`
- setup panel: enabled-plugin providers only, rendered from `auth_methods`
- diagnostics: raw sanitized auth status only in a disclosure

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
  need richer population from safe setup fields and provider health-check
  metadata.
- `auth.test` should distinguish `untested`, `test_unavailable`, and
  `connected` instead of marking every stored credential connected before a
  provider health check exists.
- Template `auth_ref` is a local requirement label. Execution should document
  or model the binding from template auth requirement to selected
  `credential_ref`, for example `auth_bindings`.
