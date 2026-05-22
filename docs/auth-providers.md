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

Normal agents may not use:

- `auth.start`: starts local setup or OAuth and is a human/admin operation.
- `auth.revoke`: removes daemon-held secrets and is a human/admin operation.
- plaintext credential setup routes or local UI admin mutations.

The MCP bridge advertises only `auth.status` and `auth.test`.

## Setup Flow

1. The agent inspects required providers through plugin/catalog metadata.
2. The agent calls `auth.status` for the project and provider key.
3. If setup is missing, the agent points the operator to the local setup URL
   returned by REST or the UI integration screen.
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

OAuth providers use the generic auth provider boundary:

- setup creates an encrypted placeholder credential and an `oauth_states` row
- the provider callback consumes the state once and exchanges the code server-side
- refresh/callback audit metadata is redacted before persistence

Provider-specific OAuth callbacks must be added deliberately by the provider
plugin/integration. The generic model can describe the flow, but provider code
owns the token exchange, refresh, scopes, and callback validation.
