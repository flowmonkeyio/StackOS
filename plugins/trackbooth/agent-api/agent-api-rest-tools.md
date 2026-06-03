# Agent REST API Tools

Trackbooth exposes agent actions through the existing admin-api REST surface.
There is no MCP server in Trackbooth. StackOS is the integration layer that
syncs the permission-filtered catalog and generates StackOS actions. Codex
uses those StackOS actions; it does not call Trackbooth endpoints directly.

## Trackbooth Server Runtime Contract

- StackOS authenticates Trackbooth server requests with
  `X-API-Key: <account api key>`.
- StackOS scopes on behalf of a managed account with
  `X-Acting-As-Account: <managed account id>`.
- JWT auth remains supported with `Authorization: Bearer <token>`.
- Account API keys also support `Authorization: ApiKey <key>` for clients that
  cannot send custom headers.

The server keeps the same enforcement path for agents and humans:

1. `SupabaseAuthGuard` authenticates either JWT or account API key credentials.
2. `AdminRequestInterceptor` resolves the effective account, managed-by
   on-behalf scope, and permission profile.
3. `FeatureGateInterceptor` verifies the effective account has
   `agent_api.access` for agent API discovery and key-management routes.
4. `PermissionEnforcementInterceptor` enforces endpoint permission decorators.
5. Existing service/repository account scoping still owns data visibility.

For account API keys:

- the backing account must have `agent_api.access` enabled manually before its
  API key can authenticate.
- root account keys behave as platform/root sessions.
- non-root account keys behave as account-owner admin sessions for their own
  account.
- `X-Acting-As-Account` is allowed for the key's own account or for an active
  `account_management` relationship managed by that account.
- managed-account actions use the relationship permission profile.

## Account Key Lifecycle

- New accounts receive an `accounts.api_key` on creation.
- Existing accounts are backfilled by
  `supabase/migrations/20260531010000_backfill_account_api_keys.sql`.
- API keys can be revealed with `GET /api/accounts/:id/api-key`.
- Missing API keys can be generated with
  `POST /api/accounts/:id/api-key/generate`. Existing keys are not overwritten.
- Normal account responses expose only the same masked-key pattern used by
  advertiser postback secrets; raw keys are returned only by reveal/generate.
- Reveal and generation require account scope, `accounts.configure`,
  `agent_api.configure`, `agent_api.access`, and the `accounts.credential` field
  group. The target account must have the feature enabled; this is checked in
  service code as well as route metadata.

## Generated Catalog

Nx targets:

```bash
nx run admin-api:agent-docs-generate
nx run admin-api:agent-docs-check
```

`admin-api:build` depends on `admin-api:agent-docs-generate`, so normal build,
dev, and deploy paths refresh the generated catalog automatically. The check
target exists for CI or review validation when a non-mutating freshness gate is
needed.

Generated outputs:

- `docs/generated/agent-api/catalog.json`
- `docs/generated/agent-api/openapi.json`
- `docs/generated/agent-api/stackos-tools.json`
- `docs/generated/agent-api/README.md`
- `apps/admin-api/src/generated/agent-api-catalog.generated.ts`

The generated catalog is the runtime source for
`GET /api/agent-api/catalog` and
`GET /api/agent-api/catalog/:operationId`. These endpoints return only the
endpoints visible to the current permission context.
Routes protected only by `InternalKeyGuard`, public portal routes, and health
checks are excluded from the agent catalog; they are not account API-key tools.
Feature-gated routes carry their required account feature keys, and the runtime
catalog filters them against the effective request context.

## Endpoint Metadata

Every generated endpoint must have authored title, subtitle, category, and tag
metadata. Full-catalog copy lives beside each route as
`@EndpointContext(...)` on the controller method.

The generator reads controller decorators into the runtime catalog, OpenAPI
output, and StackOS tool manifest. `admin-api:agent-docs-generate` and
`admin-api:agent-docs-check` fail when a generated endpoint is missing authored
metadata or when the decorator shape is incomplete.

Use simple operator wording. Titles should be short action labels and subtitles
should explain the business action without HTTP paths, controller names, DTOs,
or implementation details. The writing standard lives in
`docs/agent-api/endpoint-metadata-standard.md`.

```ts
@EndpointContext({
  title: 'Generate account API key',
  subtitle: 'Generates and stores a REST API key when the selected account is missing one.',
  category: 'accounts',
  tags: ['accounts', 'api-keys', 'agents'],
})
```

Use `@AgentCatalogFieldGroups(...)` when an endpoint exposes credential,
financial, payload, or other field-group-gated data. The authenticated catalog
uses that metadata to hide tools the current permission profile cannot use.
Use `@AgentCatalogExclude(reason)` only when an endpoint should not appear in
agent discovery.

OpenAPI schema components are source-tracked references to the controller DTO or
Zod schema names. They preserve query, body, and response contract names and the
source file that owns them.

Endpoint context is documentation metadata only. It must not be used for
authorization, permission decisions, data scoping, or impersonation behavior.

## Admin UI

The admin platform includes `/agent-api`, which consumes the same
permission-filtered catalog endpoints as StackOS. Account settings also expose
the account API key generate/reveal controls for users with `accounts.configure`,
`agent_api.configure`, `agent_api.access`, and the account credential field
group. Among normal system profiles, the agent API permission is granted to the
`Admin` profile only.

## Rollout And Rollback

Rollout:

1. Apply the account API-key backfill migration before enabling StackOS traffic.
2. Build or deploy through Nx so `admin-api:agent-docs-generate` refreshes the
   catalog before admin-api compilation.
3. Enable `agent_api.access` manually for each account that should be able to
   use REST API keys or view the agent API catalog. Do not force-enable it for
   all accounts through seed or account-override migration data.
4. Grant StackOS only account-level REST API keys for the acting account.
5. Use `X-Acting-As-Account` only for self or actively managed accounts.
6. Watch auth failures, managed-on-behalf denials, catalog endpoint counts, and
   account key reveal/generate audit events.

Rollback:

1. Remove StackOS API-key credentials from the integration layer.
2. Disable account API-key auth in `SupabaseAuthGuard` if emergency server-side
   cutoff is needed.
3. Clear or replace affected `accounts.api_key` values for compromised accounts.
4. Keep JWT admin access and existing human admin flows unchanged.
5. Regenerate the catalog after any controller/decorator rollback.
