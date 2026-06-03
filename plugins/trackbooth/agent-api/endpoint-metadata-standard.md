# Agent API Endpoint Metadata Standard

Every generated agent API endpoint must have authored metadata. The metadata is
for discovery and documentation only; auth, permissions, field visibility, and
scope remain enforced by the existing admin-api guards, interceptors, services,
and repositories.

## Required Fields

Each endpoint entry must provide:

- `title`: a short action label, usually 2-6 words.
- `subtitle`: one plain sentence that says what the endpoint does for an
  operator or agent.
- `category`: one stable product area such as `accounts`, `offers`, or
  `reporting`.
- `tags`: 2-5 search terms that help agents and operators find the endpoint.

Metadata is authored with `@EndpointContext(...)` on the controller method that
owns the route.

## Writing Style

Use simple operator language:

- Good: `View account details`
- Good: `Shows the selected account's settings, status, and contact details.`
- Avoid: `Hydrate account DTO`
- Avoid: `Execute scoped repository query for account aggregate`

Titles should start with a clear verb:

- `List`
- `View`
- `Create`
- `Update`
- `Activate`
- `Deactivate`
- `Generate`
- `Reveal`
- `Export`
- `Retry`

Subtitles should be specific, but not implementation-heavy:

- Say what the endpoint does.
- Mention the main object being changed or read.
- Mention important business context when it helps, such as managed accounts,
  payout rules, postbacks, or reports.
- Do not promise access. The catalog is filtered by the current permission
  profile, and route enforcement still happens on the server.
- Do not repeat the path, HTTP method, controller, handler, or schema name.
- Keep it to one sentence and avoid long clauses.

## Category And Tag Rules

Use the generated catalog's existing category when it is accurate. Tags should be
lowercase words or short hyphenated phrases:

- Good: `accounts`, `api-keys`, `managed-accounts`
- Good: `postbacks`, `delivery-logs`, `retries`
- Avoid: `AccountController`, `POST`, `ZodSchema`

## Decorator Shape

Each exposed controller method must carry endpoint-local metadata next to the
HTTP route decorator:

```ts
@Get(':id')
@EndpointContext({
  title: 'View account details',
  subtitle: 'Shows settings, status, and contact details for the selected account.',
  category: 'accounts',
  tags: ['accounts', 'settings', 'status'],
})
@RequireRole(...ROLE_SETS.STAFF_READ)
@RequirePermission('accounts', 'view')
async getAccount(...)
```

The generator fails closed when a protected route appears in the agent catalog
without `@EndpointContext(...)`.

## Prohibited Wording

Avoid backend implementation words unless the endpoint is explicitly for an
operator-facing technical function:

- DTO
- endpoint
- hydrate
- repository
- resolver
- interceptor
- serializer
- internal guard
- SQL query
- database row
- mutation pipeline

Avoid permission claims such as:

- `Allows anyone to`
- `Bypasses`
- `Grants access`
- `Ignores scope`

## Source Of Truth

Full-catalog endpoint copy lives in controller source through
`@EndpointContext(...)`. Do not keep a detached endpoint-copy directory or JSON
fragment source of truth. If an endpoint changes purpose, update the metadata in
the same controller method.

Use `@AgentCatalogFieldGroups(...)` when an endpoint exposes credential,
financial, payload, or other field-group-gated data.

Use `@AgentCatalogExclude(reason)` only when an endpoint should not appear in
agent discovery at all.
