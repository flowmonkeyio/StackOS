# Shopify Admin GraphQL StackOS Action Bridge

StackOS exposes Shopify only through curated actions backed by the local
Shopify Admin GraphQL connector. Agents do not receive the Admin API token and
do not send arbitrary GraphQL. The connector reads static GraphQL documents from
`plugins/shopify/graphql/`, builds variables from action input schemas, sends
the request with daemon-held auth, and returns sanitized data plus Shopify cost
metadata.

## Official Contract Sources

- Admin GraphQL reference: https://shopify.dev/docs/api/admin-graphql/latest
- Access scopes: https://shopify.dev/docs/api/usage/access-scopes
- Versioning: https://shopify.dev/docs/api/usage/versioning
- Limits and GraphQL cost model: https://shopify.dev/docs/api/usage/limits

The implementation targets Admin API version `2026-07`, which Shopify marks as
the current stable version at the time of the July 8, 2026 audit.

## Endpoint And Auth

- Endpoint: `POST https://{store_domain}/admin/api/{api_version}/graphql.json`.
- Default `api_version`: `2026-07`.
- Credential method: static operator-supplied Admin API access token.
- Auth header: `X-Shopify-Access-Token`.
- Safe credential config: `store_domain`, optional `api_version`.
- Secret credential payload: `admin_api_access_token` only.
- OAuth, token exchange, app install, and Dev Dashboard token acquisition are
  deliberately out of scope for this provider.

The connector accepts only `*.myshopify.com` domains for the Admin GraphQL
endpoint. Operators should create a custom app/token in Shopify with the least
scopes needed for the specific actions they intend to run.

## GraphQL Surface Count

The official Admin GraphQL API is one HTTP endpoint with many root fields. The
2026-07 schema evidence gathered for this delivery showed:

- `QueryRoot`: 268 root query fields.
- `Mutation`: 483 root mutation fields.
- Total schema root actions: 751.
- Rendered documentation listed 478 mutation fields at audit time; the schema
  contained five additional mutation fields not present in that rendered list.

StackOS does not expose those 751 root fields directly. This plugin exposes 58
curated actions copied from the `cob-shopify-mcp` tool catalog and corrected
against Shopify's 2026-07 documentation. The generic `shopifyql_query` tool was
removed because it exposed arbitrary ShopifyQL instead of a curated agent
action.

| Domain | Action count |
| --- | ---: |
| Analytics | 15 |
| Customers | 9 |
| Inventory | 7 |
| Orders | 12 |
| Products | 15 |

## Exposed Actions

The manifest action refs are `shopify.<action_key>`, for example:

- `shopify.list_products`
- `shopify.create_product`
- `shopify.list_orders`
- `shopify.create_draft_order`
- `shopify.adjust_inventory`
- `shopify.create_customer`
- `shopify.sales_summary`

Each action carries human-readable description text, input schema, risk level,
required Shopify scopes, source path, and static GraphQL/ShopifyQL execution
mode in `plugins/shopify/plugin.yaml`.

## Execution Behavior

- GraphQL actions load a checked-in `.graphql` file and map action inputs to the
  exact variables Shopify expects.
- ShopifyQL analytics actions use the checked-in `shopifyqlQuery` wrapper with
  connector-owned query strings only; agents cannot submit arbitrary ShopifyQL
  or raw GraphQL.
- Product tag management may execute both `tagsAdd` and `tagsRemove` when both
  `add` and `remove` arrays are provided.
- `adjust_inventory` and `set_inventory_level` send Shopify's required
  `@idempotent` directive, an internally generated idempotency key for the
  2026-07 Admin API, and explicit `changeFromQuantity` fields. When
  `change_from_quantity` is omitted by the caller, StackOS sends `null` to skip
  the compare-and-swap check intentionally.
- Order actions that select customer name/email fields require `read_customers`
  in addition to `read_orders`, and live use can require Shopify protected
  customer data approval.
- `low_stock_report` reads inventory items, filters inventory levels below the
  requested threshold in the connector, sorts by available quantity ascending,
  and returns a bounded result set.
- `inventory_risk_report` combines product variant inventory and recent sales
  GraphQL reads. The connector caps internal pagination at 20 pages and reports
  truncation in metadata if the cap is reached.

## Error, Rate, And Cost Handling

- HTTP 4xx/5xx and 429 responses use the shared integration wrapper and surface
  as safe `ActionConnectorError` output without tokens.
- Top-level GraphQL `errors` fail the action with redacted provider details.
- Mutation `userErrors` fail the action with structured repair context.
- Successful responses preserve `extensions.cost` metadata, including throttle
  status when Shopify returns it.
- No StackOS monetary budget is enforced yet. Shopify cost is query-complexity
  points, not dollars.

## Verification State

Automated coverage added in this delivery:

- `tests/integration/test_integrations/test_shopify.py`
- `tests/integration/test_repositories/test_auth_providers.py`
- `tests/integration/test_repositories/test_shopify_actions.py`
- `docs/integration-contracts/shopify-action-signoff.md`

Covered behavior:

- Static token auth probe posts to the correct versioned endpoint with
  `X-Shopify-Access-Token`.
- Credential storage keeps the token encrypted and stores only safe config.
- Plugin catalog exposes exactly 58 static Shopify actions.
- `shopify.list_products` executes a checked-in GraphQL document and maps action
  inputs to Shopify variables.
- Draft order `note2`, inventory write idempotency/CAS input shape, low-stock
  filtering, lower-case product status search, order tag replacement,
  collection creation argument shape, product `UNLISTED` status, and mutation
  `userErrors` have focused repository tests.
- A July 8, 2026 manual verifier pass checked every Shopify action against
  official Shopify documentation. Rows with blockers were fixed locally and
  signed off in `shopify-action-signoff.md`.

Live smoke with a real Shopify store token remains an operator-provided release
gate, because this session did not have Shopify credentials.
