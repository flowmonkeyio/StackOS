# StackOS Auth Providers

StackOS treats credentials as daemon-owned infrastructure, not agent context.
Agents can ask what providers are connected, test whether a connection works,
and pass opaque credential references into tools. They must never receive API
keys, OAuth tokens, refresh tokens, encrypted payloads, or local setup secrets.

## Model

The current implementation keeps `integration_credentials` as the encrypted
backing store and adds a generic auth-provider layer on top:

- `auth_providers`: provider metadata synced from plugin manifests.
- `credentials`: opaque aliases over encrypted backing rows.
- `credential_scopes`: granted scopes for a credential ref.
- `credential_accounts`: provider account metadata safe to show to agents.
- `oauth_states`: local-human OAuth state nonces with expiry and consumption.
- `credential_usage_events`: redacted audit trail for tests/revocations/use.
- `credential_refresh_events`: redacted audit trail for OAuth/refresh attempts.

The stable agent identifier is `credential_ref`, for example `cred_...`.
Backing row ids may still exist for compatibility, but new generic flows should
prefer credential refs.

## Agent Surface

Normal agents may use:

- `auth.status`: list provider metadata and sanitized connection status.
- `auth.test`: run a daemon-side health probe and return a sanitized result.

Normal agents may not use:

- `auth.start`: starts local setup or OAuth and is a human/admin operation.
- `auth.revoke`: removes the daemon-held secret and is a human/admin operation.
- `integration.set`: legacy plaintext ingestion, kept for REST/UI compatibility
  and tests only.
- `gscOauth.start`: legacy GSC starter, wrapped by the generic auth flow.

The MCP bridge advertises only `auth.status` and `auth.test`. Local admin setup
continues through REST routes and UI screens until an explicit admin grant model
exists.

## Setup Flow

1. The agent inspects required providers through plugin/catalog metadata.
2. The agent calls `auth.status` for the project and provider key.
3. If setup is missing, the agent points the operator to the local setup URL
   returned by the REST `auth.start` route or the UI integration screen.
4. The operator enters secrets or completes OAuth in the local UI/browser.
5. The agent calls `auth.test` with a `credential_ref` or `provider_key`.
6. The daemon decrypts the secret inside its process, calls the vendor wrapper,
   records a redacted usage event, and returns only sanitized status/metadata.

No step requires an agent prompt, workflow template, or repository file to carry
secret material.

The same boundary is used by the D08 action executor. Internal action execution
passes an opaque `credential_ref` to the auth provider layer; the daemon
resolves plaintext into a non-serializable `ResolvedCredential` object and hands
it to the connector adapter in process. Audit rows keep only the opaque ref,
credential id, provider key, and redacted metadata.

## GSC OAuth

Google Search Console now uses the generic auth provider boundary while keeping
the old browser callback route for compatibility:

- `auth.start` creates the encrypted placeholder credential and an
  `oauth_states` row.
- The Google callback consumes the state once, exchanges the code server-side,
  stores the token bundle encrypted, and removes legacy `oauth_state` config.
- Refresh/callback audit metadata is redacted before persistence.

The browser callback remains bearer-token whitelisted because Google cannot send
the daemon bearer header. The state nonce is the gate.

## Compatibility

Existing integration rows remain usable. `auth.status` lazily creates a
credential ref for an existing encrypted row, so old projects get the new
boundary without a destructive migration.

Legacy project integration REST routes still accept plaintext from the local UI.
That is intentionally a local-admin surface, not an agent MCP surface.
