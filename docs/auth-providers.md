# StackOS Auth Providers

StackOS treats credentials as daemon-owned infrastructure, not agent context.
Agents can inspect sanitized provider state, test whether a connection works,
and pass opaque credential references into granted tools. They must never
receive API keys, OAuth tokens, refresh tokens, encrypted payloads, or local
setup secrets.

## Model

The auth-provider layer uses:

- `auth_providers`: provider metadata synced from plugin manifests.
- `credentials`: opaque refs over encrypted secrets.
- `credential_scopes`: granted scopes for a credential ref.
- `credential_accounts`: provider account metadata safe to show to agents.
- `oauth_states`: local-human OAuth state nonces with expiry and consumption.
- `credential_usage_events`: redacted audit trail for tests/revocations/use.
- `credential_refresh_events`: redacted audit trail for OAuth/refresh attempts.

The stable agent identifier is `credential_ref`, for example `cred_...`.

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
4. The operator enters secrets or completes OAuth in the local UI/browser.
5. The agent calls `auth.test` with a `credential_ref` or provider key.
6. The daemon decrypts the secret inside its process, calls the connector,
   records a redacted usage event, and returns sanitized status/metadata.

No step requires an agent prompt, workflow template, or repository file to carry
secret material.

## OAuth Providers

OAuth providers use the generic auth provider boundary:

- setup creates an encrypted placeholder credential and an `oauth_states` row
- the provider callback consumes the state once and exchanges the code server-side
- refresh/callback audit metadata is redacted before persistence

Provider-specific OAuth callbacks must be added deliberately by the provider
plugin/integration. The callback path is not part of the default StackOS
surface.
