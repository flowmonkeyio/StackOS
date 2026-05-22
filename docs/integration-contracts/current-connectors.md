# Current Executable Connector Contract Audit

Audit date: 2026-05-22

Scope: executable connector contracts for OpenAI Images, Firecrawl, Jina Reader, Reddit, DataForSEO, Ahrefs, WordPress, Ghost, sitemap, and generic HTTP. This file is an integration-point audit only. It does not change manifests, tests, or runtime behavior.

## StackOS Contract Boundary

Current code has the right architectural split: actions are static contracts, connectors are small execution adapters, agents choose strategy, and plaintext credentials stay inside daemon-side connector requests. The core contract lives in `content_stack/actions/connectors.py:24`, `content_stack/actions/connectors.py:46`, and `content_stack/actions/connectors.py:56`; connector registration is explicit in `content_stack/actions/__init__.py:40`. Manifest parsing rejects secret-looking static config in `content_stack/actions/manifest.py:44` and derives executable fields in `content_stack/actions/manifest.py:96`. Availability is static and project-aware in `content_stack/action_availability.py:108`.

Important consequence: provider docs should shape action schemas and connector comments, but provider-specific decisions should not move into connectors. Connectors should validate payload shape, resolve daemon-held credentials, call one documented provider operation, normalize safe output, surface rate-limit/error metadata, and record audit.

## Docs Ledger

| Provider | Official docs used | Auth docs | Rate/error/pagination docs |
| --- | --- | --- | --- |
| OpenAI Images | [Images API reference](https://platform.openai.com/docs/api-reference/images), [GPT Image 1.5 model/pricing](https://developers.openai.com/api/docs/models/gpt-image-1.5/) | [OpenAI API authentication](https://platform.openai.com/docs/api-reference/authentication) | [OpenAI rate limits guide](https://platform.openai.com/docs/guides/rate-limits), model pricing page above |
| Firecrawl | [v2 introduction](https://docs.firecrawl.dev/api-reference/v2-introduction), [scrape](https://docs.firecrawl.dev/api-reference/v2-endpoint/scrape), [crawl](https://docs.firecrawl.dev/api-reference/v2-endpoint/crawl-post), [crawl status](https://docs.firecrawl.dev/api-reference/v2-endpoint/crawl-get), [map](https://docs.firecrawl.dev/api-reference/v2-endpoint/map), [extract](https://docs.firecrawl.dev/api-reference/v2-endpoint/extract) | v2 introduction and endpoint pages use bearer auth | [Errors](https://docs.firecrawl.dev/api-reference/errors), [rate limits](https://docs.firecrawl.dev/rate-limits) |
| Jina Reader | [Reader API](https://jina.ai/en-US/reader/), [Reader repo](https://github.com/jina-ai/reader) | Reader API documents free and authenticated tiers | Reader API documents RPM tiers |
| Reddit | [reddit.com API docs](https://www.reddit.com/dev/api/), [Reddit Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki), [Data API Terms](https://redditinc.com/policies/data-api-terms), [OAuth2 wiki](https://github.com/reddit-archive/reddit/wiki/oauth2) | OAuth2 wiki and Data API Wiki | API docs listing pagination; Data API Wiki and Terms for usage limits/policy |
| DataForSEO | [Live SERP advanced](https://docs.dataforseo.com/v3/serp-se-type-live-advanced/), [Google Ads search volume live](https://docs.dataforseo.com/v3/keywords_data-google_ads-search_volume-live/), [DataForSEO Labs Google domain intersection](https://docs.dataforseo.com/v3/dataforseo_labs-google-domain_intersection-live/), [DataForSEO Labs Google keywords for site](https://docs.dataforseo.com/v3/dataforseo_labs-google-keywords_for_site-live/), [user data probe](https://docs.dataforseo.com/v3/appendix-user-data/) | DataForSEO examples use API login/password Basic auth | Endpoint pages document task arrays, per-minute limits, and `tasks[].cost`; [errors appendix](https://docs.dataforseo.com/v3/appendix-errors/) |
| Ahrefs | [API v3 introduction](https://docs.ahrefs.com/en/api/docs/introduction), [API keys](https://docs.ahrefs.com/en/api/docs/api-keys-creation-and-management), [limits consumption](https://docs.ahrefs.com/api/docs/limits-consumption), [Site Explorer](https://docs.ahrefs.com/en/api/reference/site-explorer), [organic keywords](https://docs.ahrefs.com/api/reference/site-explorer/get-organic-keywords), [all backlinks](https://docs.ahrefs.com/api/reference/site-explorer/get-all-backlinks) | API keys page | API v3 introduction and limits consumption |
| WordPress | [REST API authentication](https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/), [Posts endpoint](https://developer.wordpress.org/rest-api/reference/posts/), [Application Passwords](https://developer.wordpress.org/rest-api/reference/application-passwords/) | Authentication and Application Passwords pages | [Pagination](https://developer.wordpress.org/rest-api/using-the-rest-api/pagination/), [global parameters](https://developer.wordpress.org/rest-api/using-the-rest-api/global-parameters/) |
| Ghost | [Admin API overview](https://docs.ghost.org/admin-api/), [Admin posts overview](https://docs.ghost.org/admin-api/posts/overview), [creating a post](https://docs.ghost.org/admin-api/posts/creating-a-post), [uploading an image](https://docs.ghost.org/admin-api/images/uploading-an-image) | Admin API token authentication/JWT section | Admin API overview covers JSON shape, pagination, parameters, filtering, and errors |
| Sitemap | [sitemaps.org protocol](https://www.sitemaps.org/protocol.html), [sitemaps.org FAQ](https://www.sitemaps.org/faq.html), [Google robots.txt sitemap field](https://developers.google.com/search/reference/robots_txt) | none | Protocol/FAQ define URL/index limits and optional fields |
| Generic HTTP | [RFC 9110 HTTP semantics](https://www.rfc-editor.org/rfc/rfc9110), [RFC 7617 Basic auth](https://www.rfc-editor.org/rfc/rfc7617), [RFC 6750 bearer usage](https://www.rfc-editor.org/rfc/rfc6750), [RFC 9457 problem details](https://www.rfc-editor.org/rfc/rfc9457) | RFC 7617 and RFC 6750 | RFC 9110 and RFC 9457 |

## Current Executable Surface

| Connector key | Action refs | Current implementation refs | Manifest refs | Auth/setup implication |
| --- | --- | --- | --- | --- |
| `openai-images` | `utils.image.generate` | `content_stack/actions/openai_images.py:17`, `content_stack/integrations/openai_images.py:22` | `content_stack/plugins/manifest.py:372`, `content_stack/plugins/manifest.py:398` | API key payload; budget enforced by `openai-images` kind. |
| `firecrawl` | `utils.web.scrape`, `utils.web.crawl`, `utils.web.map`, `utils.web.extract` | `content_stack/actions/firecrawl.py`, `content_stack/integrations/firecrawl.py:24` | `content_stack/plugins/manifest.py:378`, `content_stack/plugins/manifest.py:434`, `content_stack/plugins/manifest.py:452`, `content_stack/plugins/manifest.py:470`, `content_stack/plugins/manifest.py:488` | Bearer API key payload; budget enforced by `firecrawl`. |
| `jina` | `utils.web.read` | `content_stack/actions/jina.py`, `content_stack/integrations/jina_reader.py:17` | `content_stack/plugins/manifest.py:384`, `content_stack/plugins/manifest.py:506` | Optional bearer key: action sets `requires_credential: false` and `allows_credential: true`. |
| `reddit` | `utils.reddit.search-subreddit`, `utils.reddit.top-questions` | `content_stack/actions/reddit.py`, `content_stack/integrations/reddit.py:29` | `content_stack/plugins/manifest.py:390`, `content_stack/plugins/manifest.py:542`, `content_stack/plugins/manifest.py:558` | Credential payload is JSON OAuth app data, not a plain API key. |
| `sitemap` | `utils.sitemap.fetch` | `content_stack/actions/sitemap.py`, `content_stack/integrations/sitemap.py:84` | `content_stack/plugins/manifest.py:525` | No provider and no credential. |
| `dataforseo` | `seo.keyword.research`, `seo.serp.analyze`, `seo.paa.extract` | `content_stack/actions/dataforseo.py`, `content_stack/integrations/dataforseo.py:25` | `plugins/seo/plugin.yaml:20`, `plugins/seo/plugin.yaml:36`, `plugins/seo/plugin.yaml:66`, `plugins/seo/plugin.yaml:94` | Basic auth: `login` in credential config and password in encrypted payload. |
| `ahrefs` | `seo.competitor.keywords`, `seo.backlink.research` | `content_stack/actions/ahrefs.py`, `content_stack/integrations/ahrefs.py:22` | `plugins/seo/plugin.yaml:31`, `plugins/seo/plugin.yaml:120`, `plugins/seo/plugin.yaml:148` | Bearer API key payload; requires eligible paid plan/API units. |
| `wordpress` | `publishing.wordpress.post.create` | `content_stack/actions/wordpress.py`, `content_stack/integrations/wordpress.py:17` | `plugins/publishing/plugin.yaml:12`, `plugins/publishing/plugin.yaml:40` | WordPress site URL in config; username/application password in encrypted payload. |
| `ghost` | `publishing.ghost.post.create` | `content_stack/actions/ghost.py`, `content_stack/integrations/ghost.py:17` | `plugins/publishing/plugin.yaml:23`, `plugins/publishing/plugin.yaml:87` | Ghost URL and optional API version in config; Admin API key in encrypted payload. |
| `http` | plugin-defined custom actions only | `content_stack/actions/http.py:170` | documented in `docs/plugins.md:77`; no first-party manifest row | Static plugin config supplies URL/method/auth mode; daemon injects credential if allowed. |

## Cross-Cutting Contract Principles

- Keep action refs provider-specific when provider schemas differ. The current `utils.*`, `seo.*`, and `publishing.*` refs are acceptable because the provider is part of the manifest and connector config; do not add generic `post.create`, `keyword.research`, or `campaign.create` as an executable abstraction without a project-local plugin that owns the mapping.
- Inputs should be explicit request payloads, not goals. For example, `publishing.ghost.post.create` receives a Ghost Admin API post payload; it should not decide title, status, author, tags, or schedule.
- Output should be safe JSON plus provenance. The shared `_result()` wrapper currently adds vendor and operation metadata in `content_stack/actions/vendor_utils.py`, while OpenAI Images strips `b64_json` when persisted by `content_stack/integrations/openai_images.py:110`.
- Rate-limit behavior must be visible at the action-call boundary. `BaseIntegration` has token-bucket pacing and retries for 429/5xx in `content_stack/integrations/_base.py:177`, but generic HTTP does not use that base and currently collapses HTTP errors to status-only validation errors in `content_stack/actions/http.py:230`.
- Budget availability is only meaningful when pre-call estimates or post-call actual costs match provider billing. DataForSEO reconciles `tasks[].cost`; Ahrefs currently has `estimate_cost_cents == 0` despite API-unit billing.
- Pagination/status contracts must be modeled as actions before templates rely on them. Firecrawl crawl currently starts a job and returns an id, but the docs require polling `GET /v2/crawl/{id}` and following `next` when the result exceeds 10 MB.

## Provider Findings

### OpenAI Images

Current: `utils.image.generate` maps to `OpenAIImagesActionConnector`, validates prompt, size, quality, `n`, and output format, then calls `/v1/images/generations` with default model `gpt-image-1.5`.

Gaps/mismatches:

- Pricing is stale for GPT Image 1.5. The current guardrail table in `content_stack/integrations/openai_images.py:35` and connector estimate in `content_stack/actions/openai_images.py:84` do not match the current model pricing page. The model page shows lower per-image prices for GPT Image 1.5 than the hard-coded estimates.
- Manifest and connector disagree on allowed sizes. Manifest schema allows only `1024x1024`, `1536x1024`, and `1024x1536` in `content_stack/plugins/manifest.py:411`, while connector validation also accepts DALL-E mapped sizes `1792x1024` and `1024x1792` in `content_stack/actions/openai_images.py:43`.
- The connector accepts legacy `standard`/`hd` quality for all models in `content_stack/actions/openai_images.py:58`, but current GPT image models use `low`/`medium`/`high`; DALL-E compatibility is handled later in `content_stack/integrations/openai_images.py:95`. That is workable but should be documented as legacy model compatibility.

Recommended corrections:

- Add a short comment above `_IMAGE_COSTS` linking to the GPT Image 1.5 model/pricing page and mark the table as an estimate requiring scheduled refresh.
- Either align manifest size enum with connector-supported DALL-E sizes or make the connector reject DALL-E-only sizes unless `model` is a DALL-E model.
- Keep persisted output URL behavior documented in the manifest or action docs, because consumers should not expect raw base64.

### Firecrawl

Current: four utility actions map to v2 `scrape`, `crawl`, `map`, and `extract`. The wrapper uses `Authorization: Bearer`, v2 base URL, and estimated costs.

Gaps/mismatches:

- `web.crawl` only starts the crawl job. Official v2 crawl returns `id`/`url`; result retrieval requires `GET /v2/crawl/{id}` and may return `next` for large results. There is no `utils.web.crawl-status` action yet.
- Current `scrape.formats` validation only accepts string arrays in `content_stack/actions/firecrawl.py`, while Firecrawl v2 documents string and object format entries such as JSON extraction and screenshot objects.
- `map` does not expose documented `limit`, `sitemap`, `includeSubdomains`, `ignoreQueryParameters`, or location options.
- `extract` accepts only one `url` and wraps it as `urls: [url]` in `content_stack/integrations/firecrawl.py:113`; official docs accept an array/glob-style URL list and have options like `ignoreInvalidURLs`, `includeSubdomains`, `showSources`, and `scrapeOptions`.

Recommended corrections:

- Before templates assume crawl content, add a separate status/pagination action or rename/describe `utils.web.crawl` as a submit-only action everywhere.
- Link `content_stack/integrations/firecrawl.py` comments to the v2 endpoint pages and error docs.
- Expand schemas only when each additional option has bounded defaults and budget implications, especially `proxy`, `actions`, and `maxConcurrency`.

### Jina Reader

Current: `utils.web.read` maps to `https://r.jina.ai/{url}` with optional bearer auth. Manifest correctly sets optional credential behavior.

Gaps/mismatches:

- Provider manifest says `auth_type="api-key"` in `content_stack/plugins/manifest.py:384`, which may imply setup is required even though execution works without a credential.
- The wrapper builds `https://r.jina.ai/{url}` directly in `content_stack/integrations/jina_reader.py:36`. That matches the Reader pattern, but input validation should explicitly require absolute `http(s)` target URLs before concatenation.
- No response-size/token-budget contract is attached to action output; Reader pages can be large.

Recommended corrections:

- Add provider setup copy saying the key is optional and improves quota.
- Add official Reader API link near the wrapper base URL.
- Consider output truncation/resource persistence guidance for long Markdown instead of returning every byte in action output.

### Reddit

Current: `utils.reddit.search-subreddit` and `utils.reddit.top-questions` acquire an application-only OAuth token, then call OAuth Reddit listing endpoints.

Gaps/mismatches:

- Resolved in the manifest: Reddit now declares `auth_type="oauth-client-credentials"` with credential payload metadata for `client_id`, `client_secret`, and `user_agent`. The wrapper still needs richer pagination and enum validation.
- Reddit listing pagination uses `after`/`before`, `limit`, `count`, and `show`; actions expose only `limit`, so callers cannot page beyond the first listing slice.
- `sort` and `time_filter` are unbounded strings in `content_stack/actions/reddit.py`, so invalid provider enum values reach Reddit.
- `top_questions` is described as question-shaped in manifests, but the connector returns raw top listing data; the wrapper doc says the caller filters for question-shaped titles in `content_stack/integrations/reddit.py:121`.

Recommended corrections:

- Add safe auth method fields for `client_id` and `user_agent`, with `client_secret` in encrypted payload. Do not store the OAuth access token in agent-visible state.
- Constrain `sort` and `time_filter` enums and add optional `after` pagination input/output.
- Rewrite the `top_questions` description or implement deterministic filtering in the wrapper; current wording over-promises.

### DataForSEO

Current: SEO actions call DataForSEO live endpoints via Basic auth. `login` lives in credential config; password lives in the encrypted payload. Cost reconciliation reads `tasks[].cost` in `content_stack/integrations/dataforseo.py:57`.

Gaps/mismatches:

- Keyword volume docs allow up to 1000 keywords per request and only 12 requests per minute for Google Ads Live endpoints. Current connector does not cap keyword count in `content_stack/actions/dataforseo.py`, and the wrapper default QPS is 5.0 in `content_stack/integrations/dataforseo.py:28`, which could exceed endpoint-specific limits if many calls are made.
- SERP live advanced docs describe top 100 live results; connector allows `depth` up to 700 in `content_stack/actions/dataforseo.py`. Confirm whether that is still accepted for the exact endpoint or lower the max.
- DataForSEO docs distinguish current and legacy Labs routes. The wrapper uses `/dataforseo_labs/google/.../live` in `content_stack/integrations/dataforseo.py:127` and `content_stack/integrations/dataforseo.py:145`; comments should link current route docs so maintainers do not accidentally follow legacy pages.
- `domain_intersection` and `keywords_for_site` are implemented in the connector but not exposed as plugin actions, except Ahrefs equivalents. That is okay, but undocumented dormant operations can confuse future action expansion.

Recommended corrections:

- Add endpoint-specific validation: keyword count max, SERP depth max from the exact docs page, and optional `limit`/`offset` where Labs endpoints support them.
- Add comments linking each wrapper method to the exact DataForSEO endpoint doc.
- Keep current exposed actions narrow until schemas cover provider limits and pagination.

### Ahrefs

Current: two SEO actions call Site Explorer organic keywords and all backlinks using bearer auth.

Gaps/mismatches:

- Ahrefs API v3 consumes API units, with minimum/request and per-row/field cost headers. Current connector estimates zero cost in `content_stack/actions/ahrefs.py`, while the manifest enforces budget in `plugins/seo/plugin.yaml:141` and `plugins/seo/plugin.yaml:167`. This makes StackOS budget availability mostly ceremonial for Ahrefs execution.
- Wrapper does not read Ahrefs cost headers such as `x-api-units-cost-total-actual`, even though official docs say those headers are the source of unit consumption.
- API v3 docs emphasize eligible paid plans and API key limits. Manifest has no setup note for plan eligibility or key-level limits.
- `mode` for backlinks is a free string in `content_stack/actions/ahrefs.py`; constrain to documented modes before expanding.

Recommended corrections:

- Add API-unit accounting: record unit headers in `metadata_json`, map units to budget policy, or stop claiming monetary budget enforcement for Ahrefs until a conversion exists.
- Link wrapper methods to organic keywords and all backlinks docs.
- Add provider setup guidance for eligible paid plans and key limits.

### WordPress

Current: `publishing.wordpress.post.create` sends a raw REST post payload to `/wp-json/wp/v2/posts` using Basic auth with an Application Password.

Gaps/mismatches:

- `auth_type: api-key` in `plugins/publishing/plugin.yaml:15` is semantically loose. The actual credential is username plus application password, parsed in `content_stack/integrations/wordpress.py:33`.
- WordPress docs require HTTPS for Application Passwords in normal remote use. The setup field is a URL but does not document HTTPS expectation.
- Current credential test calls `users/me?context=edit`, but post creation also needs the user capability to create posts; roles are returned in `content_stack/integrations/wordpress.py:87`, but availability does not reflect publish capability.
- No media upload, post update, status transition, taxonomy lookup, or pagination actions exist. That is fine, but templates should not assume these are available.

Recommended corrections:

- Clarify provider auth method fields: site root URL over HTTPS, username, application password.
- Add docs links in wrapper comments to Application Passwords and Posts.
- Add capability warnings to manifest/template docs before offering publish flows beyond create-post.

### Ghost

Current: `publishing.ghost.post.create` signs an Admin API key into a short-lived JWT and posts `{posts: [post]}` with `source=html`.

Gaps/mismatches:

- Setup label says `Admin URL` in `plugins/publishing/plugin.yaml:30`, while the wrapper appends `/ghost/api/admin/...` in `content_stack/integrations/ghost.py:94`. This should be documented as the Ghost site/admin domain root, not a full endpoint URL.
- Default `api_version` is `v5.0` in `content_stack/actions/ghost.py`; current Ghost installs may use newer API versions. The contract should specify which Admin API version is targeted and test against it.
- Only `source: html` is allowed in the manifest in `plugins/publishing/plugin.yaml:99`; that matches the current connector, but image upload and mobiledoc/lexical pathways are absent.
- No image upload, post update, scheduling status validation, or pagination actions exist.

Recommended corrections:

- Add official Admin API auth/posts/images links to wrapper comments.
- Clarify `ghost_url` and `api_version` setup semantics.
- Keep action availability limited to create-post until upload/update contracts exist.

### Sitemap

Current: no-auth public sitemap fetch with XML parsing, recursion caps, byte caps, timeout caps, and partial errors.

Gaps/mismatches:

- The wrapper caps each response at 10 MiB in `content_stack/integrations/sitemap.py:45`, while the protocol allows 50 MB uncompressed and 50,000 URLs. This lower cap is a deliberate daemon safety choice, but it should be documented as such in action output/contract docs.
- Connector validation allows `max_entries` up to 20,000 in `content_stack/actions/sitemap.py`, while wrapper default is 5,000 in `content_stack/integrations/sitemap.py:60`. That is okay, but templates should choose conservative values.
- Namespace comments cite sitemaps.org already in `content_stack/integrations/sitemap.py:38`; good.

Recommended corrections:

- Add the sitemaps.org protocol link to the action/contract doc and call out StackOS-specific safety caps.
- Preserve partial-result behavior; do not turn per-sitemap failures into all-or-nothing exceptions.

### Generic HTTP

Current: `http` is a static custom HTTP/Webhook connector for plugin-authored actions. URL, method, request mode, auth mode, static headers, timeout, and response mode live in `config_json.http`; agents supply only schema-validated action input.

Gaps/mismatches:

- No retry/rate-limit policy is applied. Unlike vendor integrations, this connector does not use `BaseIntegration`.
- HTTP errors lose response body/details: any status >=400 raises `ValidationError` with only the status in `content_stack/actions/http.py:232`.
- Static headers reject secret-looking names in `content_stack/actions/http.py:45`, but custom header auth intentionally injects a credential header at execution time in `content_stack/actions/http.py:214`. That split is good and should be documented in plugin-authoring examples.
- SSRF/sensitive-network policy is not visible here. The static URL constraint helps, but project-local plugin installation should have review/approval around private network targets.

Recommended corrections:

- Document this as an escape hatch for user-owned/internal systems, not a web browsing tool.
- Return redacted structured error bodies where possible, preferably compatible with RFC 9457 problem details.
- Add optional static retry policy fields only after deciding how action-call audit should represent retries for user-owned endpoints.

## Action Availability Implications

- `missing_connector` is reliable because registry keys are explicit in `content_stack/actions/__init__.py:40`.
- `missing_credential` is reliable for required credentials, but setup semantics can still be misleading when manifests use `auth_type: api-key` for OAuth/client-secret pairs or username/application-password pairs.
- `missing_budget` is reliable as a gate, but not necessarily as a cost control. Ahrefs currently estimates zero. Reddit/Jina/WordPress/Ghost/sitemap/HTTP do not enforce budgets.
- Optional credentials work for Jina because the manifest has `requires_credential: false` and `allows_credential: true` in `content_stack/plugins/manifest.py:515`.
- Provider-disabled/plugin-disabled statuses are generic and correct, but they do not express provider-specific plan eligibility, scopes, roles, or endpoint permissions.

## Gaps Before Expanding Actions

1. Add exact provider doc links near wrapper methods before adding options. This prevents “old docs by memory” drift.
2. Tighten auth method fields for Reddit, WordPress, Ghost, and Ahrefs so operators know what credential shape is expected.
3. Add pagination/status actions before expanding crawl, listing, backlinks, posts, or keyword inventories.
4. Normalize provider errors into safe, structured action-call metadata without exposing secrets or raw stack traces.
5. Make budget semantics honest: use actual costs/headers where available, or mark budget as call-count/approval-only instead of monetary.
6. Constrain provider enums and limits in action schemas, not only in code.
7. Keep dormant wrapper operations hidden until plugin manifests, tests, grants, and docs exist.

## Recommended Manifest/Template/Code Comments

- `content_stack/integrations/openai_images.py`: add links to OpenAI Images API reference and GPT Image 1.5 pricing beside `_IMAGE_COSTS`; document legacy DALL-E compatibility.
- `content_stack/plugins/manifest.py`: align `utils.image.generate` size/quality enums with connector behavior or model-gate legacy options.
- `content_stack/integrations/firecrawl.py`: link each method to Firecrawl v2 endpoint docs; add a comment that `crawl()` submits a job only.
- `content_stack/plugins/manifest.py`: rename/describe `utils.web.crawl` as submit-only unless a crawl-status action is added.
- `content_stack/integrations/jina_reader.py`: link Reader API and validate absolute target URL shape before path concatenation.
- `content_stack/plugins/manifest.py`: add optional-auth setup copy for Jina.
- `content_stack/plugins/manifest.py`: add Reddit auth method fields for `client_id` and `user_agent`; keep `client_secret` daemon-side.
- `content_stack/integrations/reddit.py`: link OAuth2/Data API docs and document listing pagination headers/fields.
- `content_stack/integrations/dataforseo.py`: link exact endpoint docs above each method; note keyword volume 12 RPM and task-size limits.
- `plugins/seo/plugin.yaml`: add DataForSEO keyword count/depth/limit constraints once confirmed against endpoint docs.
- `content_stack/integrations/ahrefs.py`: link API v3 intro, API keys, limits consumption, organic keywords, and all backlinks; capture unit-cost headers.
- `plugins/seo/plugin.yaml`: clarify Ahrefs plan/API-unit requirements and budget meaning.
- `content_stack/integrations/wordpress.py`: link WordPress Authentication, Application Passwords, and Posts docs; mention HTTPS and post capability.
- `plugins/publishing/plugin.yaml`: clarify WordPress credential shape and Ghost URL root/API version.
- `content_stack/integrations/ghost.py`: link Ghost Admin API auth/posts/images docs; document JWT `aud`, expiration, and `Accept-Version` target.
- `content_stack/actions/http.py`: link RFC 9110, RFC 7617, RFC 6750, and RFC 9457; document why static config cannot contain secret headers while injected auth headers can.
