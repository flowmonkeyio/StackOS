# Shopify Action Documentation Signoff

Audit date: July 8, 2026

Scope: Shopify Admin GraphQL `2026-07`/latest documentation, the local
`plugins/shopify/plugin.yaml` catalog, static GraphQL files under
`plugins/shopify/graphql/`, and `stackos/actions/shopify.py`.

This is a documentation and contract signoff, not a live-store smoke. A real
Shopify token remains a release gate for provider connectivity.

This session also could not use `action.list` on the local MCP daemon as final
registry proof because port `5180` was owned by the packaged
`/Applications/StackOS.app` launchd daemon, which did not contain this source
tree's Shopify plugin yet. Package/install reload must be run before production
MCP registry verification.

## Verification Sources

- Admin GraphQL reference: https://shopify.dev/docs/api/admin-graphql/latest
- Admin GraphQL endpoint and auth examples:
  https://shopify.dev/docs/api/admin-graphql/latest/queries/shop
- ShopifyQL GraphQL query:
  https://shopify.dev/docs/api/admin-graphql/latest/queries/shopifyqlQuery
- ShopifyQL reference: https://shopify.dev/docs/api/shopifyql
- Access scopes: https://shopify.dev/docs/api/usage/access-scopes
- Product status enum:
  https://shopify.dev/docs/api/admin-graphql/latest/enums/ProductStatus
- Products query filters:
  https://shopify.dev/docs/api/admin-graphql/latest/queries/products
- Orders query filters:
  https://shopify.dev/docs/api/admin-graphql/latest/queries/orders
- `orderUpdate` / `OrderInput`:
  https://shopify.dev/docs/api/admin-graphql/latest/mutations/orderUpdate
- Inventory adjustments and quantity inputs:
  https://shopify.dev/docs/api/admin-graphql/latest/mutations/inventoryAdjustQuantities
  and
  https://shopify.dev/docs/api/admin-graphql/latest/mutations/inventorySetQuantities
- Product variant bulk user errors:
  https://shopify.dev/docs/api/admin-graphql/latest/objects/ProductVariantsBulkCreateUserError
  and
  https://shopify.dev/docs/api/admin-graphql/latest/objects/ProductVariantsBulkUpdateUserError

## Verifier Coverage

- First-pass verifier agents reviewed the full copied catalog action by action
  against official Shopify docs and found the blockers fixed in this patch.
- Second-pass verifier agents rechecked the corrected analytics, customer,
  inventory, and order slices that initially failed.
- Additional agent spawns for the final product/order slices hit the thread cap;
  those remaining rechecks were completed manually by the main agent against the
  official docs above and backed by focused tests.

The safe exposed surface is 58 curated actions. The original copied
`shopifyql_query` action is intentionally removed because it accepted arbitrary
ShopifyQL, which does not match the StackOS agent-wrapper model.

## One-By-One Matrix

Status legend:

- PASS: local contract matches the documented Shopify operation.
- PASS_WITH_LIMITATION: contract matches the documented operation, with an
  explicit bounded-query, pagination, protected-data, or live-smoke limitation.
- REMOVED: action is deliberately not exposed.

| Action | Surface | Status | Evidence |
| --- | --- | --- | --- |
| `conversion_funnel` | ShopifyQL `shopifyqlQuery` | PASS_WITH_LIMITATION | Re-verifier confirmed curated sessions/orders/customers/conversion_rate queries; no arbitrary query input. |
| `customer_cohort_analysis` | ShopifyQL `shopifyqlQuery` | PASS_WITH_LIMITATION | First-pass verifier accepted curated customer/order aggregation; requires `read_reports`. |
| `customer_lifetime_value` | ShopifyQL `customers` dataset | PASS_WITH_LIMITATION | Re-verifier confirmed documented customer_name/total_amount_spent/total_orders fields; names are not unique IDs. |
| `discount_performance` | ShopifyQL `sales` dataset | PASS_WITH_LIMITATION | First-pass verifier accepted bounded discount-code sales query. |
| `inventory_risk_report` | Admin GraphQL products/orders/inventory | PASS_WITH_LIMITATION | Re-verifier confirmed added `read_orders`; connector caps internal pagination and reports truncation. |
| `orders_by_date_range` | ShopifyQL `orders`/sales date filters | PASS_WITH_LIMITATION | First-pass verifier accepted bounded date-range query. |
| `product_vendor_performance` | ShopifyQL `sales` dataset | PASS_WITH_LIMITATION | First-pass verifier accepted vendor grouping query. |
| `refund_rate_summary` | ShopifyQL `sales_reversals` metric | PASS_WITH_LIMITATION | Re-verifier confirmed replacement of deprecated/invalid `returns` metric. |
| `repeat_customer_rate` | ShopifyQL customer/order aggregation | PASS_WITH_LIMITATION | First-pass verifier accepted approximate report description. |
| `sales_by_channel` | ShopifyQL sales channel grouping | PASS_WITH_LIMITATION | First-pass verifier accepted curated query. |
| `sales_by_geography` | ShopifyQL geography grouping | PASS_WITH_LIMITATION | First-pass verifier accepted curated query. |
| `sales_comparison` | ShopifyQL `WITH PERCENT_CHANGE` | PASS_WITH_LIMITATION | Re-verifier confirmed `compare_to`/`group_by` enums and percent-change query form. |
| `sales_summary` | ShopifyQL `sales` dataset | PASS_WITH_LIMITATION | First-pass verifier accepted bounded summary query. |
| `top_products` | ShopifyQL product ranking | PASS_WITH_LIMITATION | First-pass verifier accepted bounded top-products query. |
| `traffic_analytics` | ShopifyQL sessions/traffic | PASS_WITH_LIMITATION | First-pass verifier accepted bounded traffic query. |
| `add_customer_tag` | `tagsAdd` on Customer | PASS | First-pass verifier accepted tags mutation contract. |
| `create_customer` | `customerCreate` | PASS_WITH_LIMITATION | First-pass verifier accepted mutation; Shopify protected customer data requirements apply. |
| `get_customer` | `customer` query | PASS_WITH_LIMITATION | First-pass verifier accepted query; requires `read_customers` and PCD approval for sensitive fields. |
| `get_customer_lifetime_value` | Customer amount/orders fields | PASS_WITH_LIMITATION | Re-verifier confirmed `read_customers` + `read_orders`; older order history can require `read_all_orders`. |
| `get_customer_orders` | Customer `orders` connection | PASS_WITH_LIMITATION | Re-verifier confirmed max 250 and cursor input; older order history can require `read_all_orders`. |
| `list_customers` | `customers` connection | PASS_WITH_LIMITATION | First-pass verifier accepted bounded cursor pagination. |
| `remove_customer_tag` | `tagsRemove` on Customer | PASS | First-pass verifier accepted tags mutation contract. |
| `search_customers` | `customers(query:)` | PASS_WITH_LIMITATION | First-pass verifier accepted documented customer search syntax and bounded pagination. |
| `update_customer` | `customerUpdate` | PASS_WITH_LIMITATION | First-pass verifier accepted mutation; Shopify protected customer data requirements apply. |
| `adjust_inventory` | `inventoryAdjustQuantities` | PASS | Re-verifier confirmed idempotency key, `changeFromQuantity`, integer delta, and `userErrors.code`. |
| `get_inventory_by_sku` | Products search by `sku:` | PASS_WITH_LIMITATION | First-pass verifier accepted SKU search; connector quotes search input and bounds first page. |
| `get_inventory_item` | `inventoryItem` query | PASS_WITH_LIMITATION | First-pass verifier accepted query; first inventory levels are bounded. |
| `get_location_inventory` | Location inventory levels | PASS_WITH_LIMITATION | First-pass verifier accepted bounded inventory-level connection. |
| `list_inventory_levels` | Inventory items/levels connection | PASS_WITH_LIMITATION | First-pass verifier accepted bounded cursor pagination. |
| `low_stock_report` | Inventory levels read + connector filter | PASS_WITH_LIMITATION | First-pass verifier accepted connector-owned threshold filter over bounded inventory data. |
| `set_inventory_level` | `inventorySetQuantities` | PASS_WITH_LIMITATION | Re-verifier confirmed `changeFromQuantity` replaces invalid `ignoreCompareQuantity`; null skips CAS intentionally. |
| `add_order_note` | `orderUpdate` note | PASS | First-pass verifier accepted `OrderInput.note`. |
| `add_order_tag` | `tagsAdd` on Order | PASS | First-pass verifier accepted tags mutation contract. |
| `create_draft_order` | `draftOrderCreate` | PASS | Re-verifier confirmed `DraftOrder.note2` response field. |
| `get_order` | `order` query | PASS_WITH_LIMITATION | Re-verifier confirmed `read_orders` + `read_customers`; returns first 50 line items plus pageInfo. |
| `get_order_by_name` | `orders(query: name:...)` | PASS_WITH_LIMITATION | Re-verifier confirmed exact-name search without forced `#`; first matching order only. |
| `get_order_fulfillment_status` | `order` fulfillment status | PASS_WITH_LIMITATION | Re-verifier confirmed `read_orders` scope is sufficient for selected fields. |
| `get_order_timeline` | Order events/timeline | PASS_WITH_LIMITATION | First-pass verifier accepted bounded events connection. |
| `list_orders` | `orders` connection | PASS_WITH_LIMITATION | Re-verifier confirmed `read_customers` when selecting customer fields; bounded first page. |
| `mark_order_paid` | `orderMarkAsPaid` | PASS_WITH_LIMITATION | First-pass verifier accepted mutation; Shopify staff permission `mark_orders_as_paid` is also required. |
| `search_orders` | `orders(query:)` | PASS_WITH_LIMITATION | Manual final recheck confirmed documented order search filters and `read_customers` scope for customer fields. |
| `update_order_note` | `orderUpdate` note | PASS | First-pass verifier accepted `OrderInput.note`. |
| `update_order_tags` | `orderUpdate` tags | PASS | Manual final recheck confirmed `OrderInput.tags` replacement semantics and no tagsAdd-only append behavior. |
| `create_collection` | `collectionCreate(collection:)` | PASS | Manual final recheck confirmed current `CollectionCreateInput`/`collection` argument. |
| `create_product` | `productCreate` | PASS | First-pass verifier accepted mutation; manifest now includes documented `UNLISTED` status enum. |
| `create_product_variant` | `productVariantsBulkCreate` | PASS | Manual final recheck confirmed bulk-create contract and `userErrors.code`. |
| `get_collection` | `collection` query | PASS_WITH_LIMITATION | First-pass verifier accepted bounded products connection. |
| `get_product` | `product` query | PASS_WITH_LIMITATION | First-pass verifier accepted first 10 media/first 100 variants plus pageInfo. |
| `get_product_by_handle` | `productByHandle` query | PASS_WITH_LIMITATION | First-pass verifier accepted first 10 media/first 100 variants plus pageInfo. |
| `get_product_variant` | `productVariant` query | PASS | First-pass verifier accepted variant fields. |
| `list_collections` | `collections` connection | PASS_WITH_LIMITATION | First-pass verifier accepted bounded cursor pagination. |
| `list_product_variants` | Product variants connection | PASS_WITH_LIMITATION | First-pass verifier accepted bounded cursor pagination up to 250. |
| `list_products` | `products(query:)` | PASS_WITH_LIMITATION | Manual final recheck confirmed lowercase `status:` search values and documented `unlisted` filter. |
| `manage_product_tags` | `tagsAdd`/`tagsRemove` on Product | PASS | First-pass verifier accepted add/remove tag behavior. |
| `search_products` | `products(query:)` | PASS_WITH_LIMITATION | First-pass verifier accepted documented product search syntax and bounded pagination. |
| `update_product` | `productUpdate(product:)` | PASS | Manual final recheck confirmed `ProductUpdateInput.status` uses `ProductStatus`, including `UNLISTED`. |
| `update_product_status` | `productUpdate(product:)` | PASS | Manual final recheck confirmed status-only wrapper uses documented `ProductStatus`, including `UNLISTED`. |
| `update_product_variant` | `productVariantsBulkUpdate` | PASS | Manual final recheck confirmed bulk-update contract and `userErrors.code`. |
| `shopifyql_query` | Arbitrary ShopifyQL | REMOVED | Re-verifier found this unsafe for agent exposure; catalog now rejects `shopify.shopifyql_query`. |

## Automated Evidence

- `uv run pytest tests/integration/test_repositories/test_shopify_actions.py -q`
  passed: 11 tests.
- The focused suite asserts the catalog exposes exactly 58 Shopify actions,
  rejects `shopify.shopifyql_query`, lowercases product status filters, exposes
  `UNLISTED` for product status actions, maps order tag replacement through
  `orderUpdate`, preserves variant `userErrors.code`, and verifies draft-order
  `note2`, inventory idempotency/CAS input shape, and collection creation
  argument shape.
- `action.list` against this machine's active `5180` daemon returned zero
  Shopify actions because the daemon was the packaged app runtime rather than
  this checkout; package/install reload is required before live MCP registry
  signoff.
