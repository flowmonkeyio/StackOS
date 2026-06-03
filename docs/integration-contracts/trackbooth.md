# Trackbooth Agent API StackOS Action Bridge

Status: executable first pass.

Agents interact with Trackbooth only through StackOS actions. StackOS integrates
with the Trackbooth server through an internal REST connector: the agent runs a
manual catalog sync action, StackOS upserts generated actions from that live
inventory, and the daemon sends provider requests with daemon-held API-key
credentials.

## Auth And URL Contract

- Default Trackbooth API URL: `https://apis.trackbooth.com`.
- Custom API URLs are stored in the Trackbooth credential safe config as
  `api_base_url`. They are not accepted as per-action input.
- Remote custom URLs must use HTTPS.
- Plain HTTP is allowed only for `localhost`, `127.0.0.1`, or `[::1]` local
  testing targets.
- The daemon sends `X-API-Key` as the primary auth header.
- `X-Acting-As-Account` is optional and sent only when the action input
  explicitly includes `acting_as_account`.
- The bridge does not bypass Trackbooth permissions, feature gates, or account
  management rules.

## Runtime Inventory And Agent Flow

The builtin `trackbooth` plugin installs three fixed actions:

| Action | Purpose |
| --- | --- |
| `trackbooth.catalog.sync` | Manual live inventory refresh. The StackOS connector uses the configured API URL internally and upserts generated `trackbooth.api.*` actions. |
| `trackbooth.catalog.search` | Optional live catalog lookup through StackOS for permission-context discovery and diagnostics. |
| `trackbooth.operation.describe` | Optional live operation detail with expanded request/response schemas from the live catalog detail. |

Generated StackOS actions are not installed from copied files during plugin
sync. They are created or refreshed only when an agent explicitly executes
`trackbooth.catalog.sync` with a connected Trackbooth credential. The sync uses
the credential's configured `api_base_url`, so the inventory can come from
production, a remote staging URL, or a local Trackbooth server.

Generated action identity is scoped to the StackOS project, credential ref, API
URL, and explicit acting-account context used for the sync. A local sync cannot
overwrite production inventory, a different project cannot discover another
project's generated actions, and generated execution rejects a mismatched
credential/API URL/acting-account context.

Normal agent flow:

1. The agent connects a Trackbooth credential. Production is the default URL;
   localhost/custom URLs live in the credential safe config.
2. The agent runs `trackbooth.catalog.sync` when it wants to initialize or
   refresh runtime inventory. There is no automatic background sync.
3. StackOS exposes generated direct inventory actions such as
   `trackbooth.api.ctx_<scope>.advertiser_create`,
   `trackbooth.api.ctx_<scope>.links_create`, or
   `trackbooth.api.ctx_<scope>.offers_findbyid`, according to the live catalog
   returned for that credential and optional acting-account context.
4. The action input schema already contains the selected operation's structured
   `path_params`, `query`, and `body` fields where applicable.
5. StackOS sends exactly that operation to Trackbooth with daemon-held auth.
6. Trackbooth remains the permission, feature flag, account-scope, and
   on-behalf authority. If the connected account cannot call the endpoint, the
   server error is preserved for repair.

The copied source bundle lives in `plugins/trackbooth/agent-api/` and contains
the prior bootstrap manifest, generated OpenAPI spec, generated catalog, schema
audit, and source docs. These files are reference fixtures for development and
tests; the production inventory source is the live server catalog fetched by
`trackbooth.catalog.sync`.

## Safety Boundaries

- Agents must not make direct HTTP requests to Trackbooth or construct
  Trackbooth URLs. The agent-facing surface is StackOS action discovery,
  description, validation, and execution.
- API-key reveal and generate operations are skipped during sync and blocked by
  the connector if invoked through a lower-level diagnostic path.
- Server validation, auth, feature-flag, permission, and on-behalf failures are
  preserved in the connector error string for agent repair.
- Generated StackOS actions do not preflight the live catalog on every call.
  They use the stored runtime metadata from the last manual sync and rely on
  the Trackbooth server to enforce permissions and return authoritative
  failures.
- Full sync retires missing generated actions only within the same inventory
  scope. Retired actions are hidden from normal discovery but retained for
  audit/history rather than deleted.
- Agents can rerun `trackbooth.catalog.sync` during runtime whenever local or
  remote Trackbooth catalog changes should be reflected.
- The schema resolver reports weak live schema areas instead of inventing
  fields.

## Business Dry Run

Catalog-only dry-run planning found a viable sequence for the requested setup:

1. `AdvertiserController.create` creates one advertiser.
2. `ProductsController.create` creates one product for that advertiser.
3. `OffersController.create` creates three offers. Offer-level targeting uses
   required `country_codes`, so US, UK, and SE can be represented directly.
4. `CampaignsController.create` creates one campaign.
5. `LinksController.create` creates one smart link with `routing_mode: rules`.
6. `LinksController.createRule` creates one routing rule.
7. `LinksController.addOfferToRule` adds each of the three offers to the rule.
8. `LinksController.testRouting` can validate routing for country codes `US`,
   `UK`, and `SE`.

The combined `LinksController.createRuleWithOffers` endpoint exists, but its
generated nested request schema only expands to `rule: object` and
`offers: syncOfferEntrySchema[]`. The smaller `createRule` plus
`addOfferToRule` path is the preferred catalog-driven flow until Trackbooth
exports nested rule and offer-entry schemas in detail.

No live side-effect dry run was performed during this delivery because no
Trackbooth API key or local Trackbooth server target was supplied in the
workspace. Mocked HTTP integration tests cover representative discovery, detail,
read, write, path/query/body, enum, URL-safety, and permission-error flows.
