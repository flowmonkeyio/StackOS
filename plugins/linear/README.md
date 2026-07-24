# Linear Plugin

The built-in Linear plugin exposes a curated issue-work integration with either
OAuth or a personal API key. It uses the shared StackOS OAuth lifecycle where
app authorization is selected, one fixed GraphQL endpoint, and 34
repository-owned GraphQL documents. It does not expose raw GraphQL or
provider-specific MCP tools.

Shared method selection, profile isolation, evidence, readiness, and migration
rules live in the
[one-brain auth-method contract](../../docs/auth-providers.md#one-brain-auth-method-contract).

## Authentication

Choose **Connect with Linear** to create a Linear OAuth application and
register the callback shown in the generic StackOS Connections view. The OAuth
contract uses authorization code with required PKCE `S256`, fixed `actor=user`,
and `read,write` scopes.

Alternatively, choose **Personal API key** and enter a key created in Linear
account settings. StackOS stores it as a raw daemon-held credential and sends
it as the complete `Authorization` header value, without a `Bearer` prefix.
Linear enforces the personal key's workspace and action permissions at request
time; this method does not renew or create locally verified scope grants.

The saved auth-method key selects the transport. OAuth payloads are parsed as
JSON access-token material, while a personal key remains raw. Unknown or
mismatched methods fail before a provider request. Client credentials, OAuth
tokens, and personal keys remain daemon-held.

After either setup path, run the generic **Test** action separately. The same
probe reads the authenticated viewer and organization so StackOS can store a
safe workspace/account label and opaque refs. Generic reconnect repairs
expired or missing OAuth scopes; replace or adjust a personal key in Linear
when Linear rejects it. Generic revoke removes the local daemon-held
credential. There is no Linear-specific remote revoke flow. Linear's documented
`POST https://api.linear.app/oauth/revoke` endpoint is intentionally deferred,
so local cleanup does not claim to invalidate the provider token.

## Execution Surface

The plugin contains:

- one provider, `linear.linear`;
- one issue-work capability;
- 20 read actions and 14 write actions;
- fixed documents under `graphql/`;
- the pinned public schema and exhaustive root policy under `schema/`.

All actions execute through the normal `action.run` or granted
`action.execute` path. Agents pass account-bound `provider-object:` refs, never
raw Linear ids. The connector constructs the reviewed filter/input shape and
converts every returned provider id to an opaque safe ref.

## Verification

The canonical contract, official source ledger, action matrix, exclusions, and
signoff state are in
[`../../docs/integration-contracts/linear.md`](../../docs/integration-contracts/linear.md).
`tests/unit/test_linear_schema_contract.py` enforces exact schema/document/
manifest/connector parity. Focused integration tests cover OAuth and
personal-key transport, actions, grants, MCP visibility, audit, safe refs, and
idempotency.

Live OAuth and personal-key smoke testing require operator-held Linear
credentials and are intentionally not part of repository fixtures.
