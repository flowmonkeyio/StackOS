# Current Executable Connector Contract Audit

Audit date: 2026-07-22

Scope: executable connector contracts for OpenAI Images, xAI Imagine, Reve,
Google Gemini Image, Google Veo, Ideogram, BytePlus Seedream/Seedance,
Alibaba Wan, Kling, Firecrawl, Jina Reader, FTP/FTPS, Cloudflare DNS, Reddit,
DataForSEO, Serper.dev,
Ahrefs, Google Search Console, Google Analytics 4, Google Tag Manager,
HubSpot, WordPress, Ghost, sitemap, Trackbooth, Shopify, generic HTTP, and the internal Branding
evidence connector, plus
connection-only setup providers that intentionally do not expose actions yet,
and the built-in OAuth/auth-protocol classification.
This file is an
integration-point audit only. It does not change manifests, tests, or runtime
behavior.

## StackOS Contract Boundary

Current code has the right architectural split: actions are static contracts, connectors are small execution adapters, agents choose strategy, and plaintext credentials stay inside daemon-side connector requests. The core contract lives in `stackos/actions/connectors.py:24`, `stackos/actions/connectors.py:46`, and `stackos/actions/connectors.py:56`; connector registration is explicit in `stackos/actions/__init__.py:40`. Manifest parsing rejects secret-looking static config in `stackos/actions/manifest.py:44` and derives executable fields in `stackos/actions/manifest.py:96`. Availability is static and project-aware in `stackos/action_availability.py:108`.

Important consequence: provider docs should shape action schemas and connector comments, but provider-specific decisions should not move into connectors. Connectors should validate payload shape, receive an already-resolved daemon-held credential, call one documented provider operation, normalize safe output, and surface rate-limit/error metadata; the shared executor records audit.

## OAuth and auth-protocol status

The canonical provider-by-provider matrix is in
[`../auth-providers.md`](../auth-providers.md#built-in-oauth-provider-matrix).
The integration-contract summary is:

| Runtime status | Providers |
| --- | --- |
| Interactive authorization code through the shared core | Google Ads, Google Workspace, Google Search Console, Google Analytics, Google Tag Manager, Meta Ads, Microsoft 365, Outreach, Pipedrive, Salesforce, Salesloft |
| Core client-credentials token acquisition | Reddit, Taboola |
| Mixed OAuth and non-OAuth alternatives | Pipedrive (API token), Salesloft (API key) |
| Interactive authorization code with capability-scoped optional consent | HubSpot |
| Existing manual OAuth token; provider actions explicitly deferred | X API, LinkedIn |

The root classification fix is complete for the affected built-in manifests:
OAuth client credentials remain OAuth, Reddit and Taboola declare
`oauth-client-credentials`, and WordPress declares `application-password`.
Connectors no longer acquire or refresh OAuth access tokens. The shared auth
resolver does that work and enforces each action's declared scopes before
connector dispatch.

## Docs Ledger

| Provider | Official docs used | Auth docs | Rate/error/pagination docs |
| --- | --- | --- | --- |
| OpenAI Images | [Image generation guide](https://developers.openai.com/api/docs/guides/image-generation), [Images API reference](https://developers.openai.com/api/reference/resources/images) | [OpenAI API authentication](https://platform.openai.com/docs/api-reference/authentication) | [OpenAI rate limits guide](https://platform.openai.com/docs/guides/rate-limits), image generation pricing table in the guide above |
| xAI Imagine | [Image generation](https://docs.x.ai/developers/model-capabilities/images/generation), [image editing](https://docs.x.ai/developers/model-capabilities/images/editing), [multi-image editing](https://docs.x.ai/developers/model-capabilities/images/multi-image-editing), [video generation](https://docs.x.ai/developers/model-capabilities/video/generation), [image-to-video](https://docs.x.ai/developers/model-capabilities/video/image-to-video), [reference-to-video](https://docs.x.ai/developers/model-capabilities/video/reference-to-video), [models](https://docs.x.ai/developers/models) | xAI examples use bearer auth with an API key from the xAI console | Video generation docs define submit/poll status values; [pricing](https://docs.x.ai/developers/pricing) documents Imagine output/input cost units |
| Reve | [Docs overview](https://api.reve.com/console/docs), [create](https://api.reve.com/console/docs/create), [edit](https://api.reve.com/console/docs/edit), [remix](https://api.reve.com/console/docs/remix), [pricing](https://api.reve.com/console/pricing) | Reve examples use bearer auth with an API key from the Reve console | Image endpoints are synchronous and return `request_id`, `content_violation`, `credits_used`, and `credits_remaining`; pricing page documents base credit costs |
| Google Gemini Image | [Nano Banana image generation](https://ai.google.dev/gemini-api/docs/image-generation), [image understanding/input](https://ai.google.dev/gemini-api/docs/image-understanding), [generateContent API](https://ai.google.dev/api/generate-content), [pricing](https://ai.google.dev/gemini-api/docs/pricing) | Gemini Developer API examples use `x-goog-api-key` with an API key from Google AI Studio | Image endpoints are synchronous `models/{model}:generateContent`; generated image parts arrive as inline MIME/base64 data; pricing docs define image output prices and tokenized input caveats |
| Google Veo | [Veo video generation](https://ai.google.dev/gemini-api/docs/video), [pricing](https://ai.google.dev/gemini-api/docs/pricing), [official python-genai SDK](https://github.com/googleapis/python-genai) | Gemini Developer API examples use `x-goog-api-key` with an API key from Google AI Studio | Veo uses Gemini long-running operations; StackOS submits `predictLongRunning`, polls `operations/{id}`, downloads generated media, and persists it as video artifacts |
| Ideogram | [Generate v4](https://developer.ideogram.ai/api-reference/api-reference/generate-v4), [Remix v4](https://developer.ideogram.ai/api-reference/api-reference/remix-v4), [pricing](https://ideogram.ai/api-pricing/) | Ideogram examples use an `Api-Key` header with an API key from the Ideogram API dashboard | v4 image endpoints are synchronous multipart calls that return temporary URLs; docs define 23 2K resolutions, speed tiers, remix upload limits, and output prices |
| BytePlus ModelArk | [Seedream image generation API](https://docs.byteplus.com/en/docs/ModelArk/1541523), [Seedance task create](https://docs.byteplus.com/en/docs/ModelArk/1520757), [Seedance task retrieve](https://docs.byteplus.com/en/docs/ModelArk/1521309), [model list](https://docs.byteplus.com/en/docs/ModelArk/1330310), [pricing](https://docs.byteplus.com/en/docs/ModelArk/1544106), [base URL/auth](https://docs.byteplus.com/en/docs/ModelArk/1298459) | ModelArk uses bearer API-key auth from the BytePlus console; organization verification and model activation may be required | Seedream image endpoint is synchronous; Seedance video endpoint is async task create/retrieve with 24-hour output URLs and status values including queued/running/succeeded/failed/expired |
| Alibaba Wan | [video model lineup](https://www.alibabacloud.com/help/en/model-studio/video-generate-edit-model/), [text-to-video](https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference/), [image-to-video](https://www.alibabacloud.com/help/en/model-studio/image-to-video-general-api-reference), [official DashScope SDK](https://github.com/dashscope/dashscope-sdk-python) | DashScope examples use bearer API-key auth and `X-DashScope-Async: enable` for async jobs | Wan uses async task submit/poll with `task_id`; output `video_url` is temporary and is persisted immediately |
| Kling | [common info/auth](https://kling.ai/document-api/apiReference%2FcommonInfo), [text-to-video](https://kling.ai/document-api/apiReference%2Fmodel%2FtextToVideo), [image-to-video](https://kling.ai/document-api/apiReference%2Fmodel%2FimageToVideo) | Kling docs require AccessKey + SecretKey converted to a short-lived HS256 JWT sent as `Authorization: Bearer` | Kling text/image video endpoints create async tasks, poll `task_status`, and return `task_result.videos[].url` with 30-day provider retention |
| Firecrawl | [v2 introduction](https://docs.firecrawl.dev/api-reference/v2-introduction), [scrape](https://docs.firecrawl.dev/api-reference/v2-endpoint/scrape), [crawl](https://docs.firecrawl.dev/api-reference/v2-endpoint/crawl-post), [crawl status](https://docs.firecrawl.dev/api-reference/v2-endpoint/crawl-get), [map](https://docs.firecrawl.dev/api-reference/v2-endpoint/map), [extract](https://docs.firecrawl.dev/api-reference/v2-endpoint/extract) | v2 introduction and endpoint pages use bearer auth | [Errors](https://docs.firecrawl.dev/api-reference/errors), [rate limits](https://docs.firecrawl.dev/rate-limits) |
| Jina Reader | [Reader API](https://jina.ai/en-US/reader/), [Reader repo](https://github.com/jina-ai/reader) | Reader API documents free and authenticated tiers | Reader API documents RPM tiers |
| Reddit | [reddit.com API docs](https://www.reddit.com/dev/api/), [Reddit Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki), [Data API Terms](https://redditinc.com/policies/data-api-terms), [OAuth2 wiki](https://github.com/reddit-archive/reddit/wiki/oauth2) | OAuth2 wiki and Data API Wiki | API docs listing pagination; Data API Wiki and Terms for usage limits/policy |
| DataForSEO | [Live SERP advanced](https://docs.dataforseo.com/v3/serp-se-type-live-advanced/), [Google Ads search volume live](https://docs.dataforseo.com/v3/keywords_data-google_ads-search_volume-live/), [DataForSEO Labs Google domain intersection](https://docs.dataforseo.com/v3/dataforseo_labs-google-domain_intersection-live/), [DataForSEO Labs Google keywords for site](https://docs.dataforseo.com/v3/dataforseo_labs-google-keywords_for_site-live/), [user data probe](https://docs.dataforseo.com/v3/appendix-user-data/) | DataForSEO examples use API login/password Basic auth | Endpoint pages document task arrays, per-minute limits, and `tasks[].cost`; [errors appendix](https://docs.dataforseo.com/v3/appendix-errors/) |
| Serper.dev | [Serper.dev Google Search API product/docs entrypoint](https://serper.dev/) | The public product page verifies the Google Search API and response shape; the exact endpoint/header contract comes from provider dashboard/playground examples rather than a stable public docs URL | Response examples include result blocks and provider credit metadata when available; StackOS bounds `num` and `page` inputs |
| Ahrefs | [API v3 introduction](https://docs.ahrefs.com/en/api/docs/introduction), [API keys](https://docs.ahrefs.com/en/api/docs/api-keys-creation-and-management), [limits consumption](https://docs.ahrefs.com/en/api/docs/limits-consumption), [subscription limits/usage](https://docs.ahrefs.com/en/api/reference/subscription-info/get-limits-and-usage), [Site Explorer](https://docs.ahrefs.com/en/api/reference/site-explorer), [organic keywords](https://docs.ahrefs.com/en/api/reference/site-explorer/get-organic-keywords), [all backlinks](https://docs.ahrefs.com/en/api/reference/site-explorer/get-all-backlinks) | API keys page | API v3 introduction, pricing API row limits, limits consumption, and free subscription-info endpoint |
| Google Search Console | [authorization/scopes](https://developers.google.com/webmaster-tools/v1/how-tos/authorizing), [sites.list](https://developers.google.com/webmaster-tools/v1/sites/list), [searchAnalytics.query](https://developers.google.com/webmaster-tools/v1/searchanalytics/query), [sitemaps.list](https://developers.google.com/webmaster-tools/v1/sitemaps/list), [urlInspection.index.inspect](https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect) | Google OAuth with `webmasters.readonly` for this first pass | Search Analytics uses `startRow`/`rowLimit` and returns top rows; URL Inspection is indexed-version only with provider-enforced quotas |
| Google Analytics 4 | [Data API overview](https://developers.google.com/analytics/devguides/reporting/data/v1), [runReport](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport), [getMetadata](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/getMetadata), [runRealtimeReport](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runRealtimeReport), [account summaries](https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/accountSummaries/list) | Google OAuth with `analytics.readonly` for this first pass | Google account summaries use `pageToken`; StackOS exposes public `page_cursor`/`next_page_cursor`. Report quotas are request-complexity based and `propertyQuota` is returned as redaction-safe `quota` output |
| Google Tag Manager | [API v2 overview](https://developers.google.com/tag-platform/tag-manager/api/v2), [authorization/scopes](https://developers.google.com/tag-platform/tag-manager/api/v2/authorization), [accounts.list](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/list), [containers.list](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/containers/list), [containers.snippet](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/containers/snippet), [workspaces.list](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/containers/workspaces/list), [tags.list](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/containers/workspaces/tags/list), [triggers.list](https://developers.google.com/tag-platform/tag-manager/api/v2/reference/accounts/containers/workspaces/triggers/list) | Google OAuth with `tagmanager.readonly` for this first pass | Google list operations return `nextPageToken`; StackOS exposes public `page_cursor`/`next_page_cursor`. Wrapper defaults to conservative QPS because GTM daily/QPS quotas are low |
| HubSpot | [Canonical StackOS contract and official source ledger](hubspot.md) | Project-based HubSpot app; private OAuth distribution for the initial phase; CRM Core required plus selected optional capability bundles; fixed public callback | Bounded provider cursors, multi-status batches, opaque async refs, safe correlation/retry evidence, and explicit readiness/entitlement/ingress prerequisites |
| OpenRouter | [authentication](https://openrouter.ai/docs/api/reference/authentication), [models](https://openrouter.ai/docs/api/api-reference/models/get-models) | OpenRouter requests use a bearer token and optional attribution headers | The normal setup probe uses the read-only models endpoint; no text-generation action is exposed yet |
| WordPress | [REST API authentication](https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/), [Posts endpoint](https://developer.wordpress.org/rest-api/reference/posts/), [Application Passwords](https://developer.wordpress.org/rest-api/reference/application-passwords/) | Authentication and Application Passwords pages | [Pagination](https://developer.wordpress.org/rest-api/using-the-rest-api/pagination/), [global parameters](https://developer.wordpress.org/rest-api/using-the-rest-api/global-parameters/) |
| Ghost | [Admin API overview](https://docs.ghost.org/admin-api/), [Admin posts overview](https://docs.ghost.org/admin-api/posts/overview), [creating a post](https://docs.ghost.org/admin-api/posts/creating-a-post), [uploading an image](https://docs.ghost.org/admin-api/images/uploading-an-image) | Admin API token authentication/JWT section | Admin API overview covers JSON shape, pagination, parameters, filtering, and errors |
| Sitemap | [sitemaps.org protocol](https://www.sitemaps.org/protocol.html), [sitemaps.org FAQ](https://www.sitemaps.org/faq.html), [Google robots.txt sitemap field](https://developers.google.com/search/reference/robots_txt) | none | Protocol/FAQ define URL/index limits and optional fields |
| FTP / explicit FTPS | [FTP protocol](https://www.rfc-editor.org/rfc/rfc959), [MLST/MLSD](https://www.rfc-editor.org/rfc/rfc3659), [FTP over TLS](https://www.rfc-editor.org/rfc/rfc4217), [Python ftplib](https://docs.python.org/3/library/ftplib.html) | Username/password with server settings; password is daemon-held | Stateless browse/transfer plus exact DELE, MKD, RMD, and RNFR/RNTO operations; recursive delete requires typed MLSx planning; no monetary budget |
| Cloudflare DNS | [zones list](https://developers.cloudflare.com/api/resources/zones/methods/list/), [DNS record API](https://developers.cloudflare.com/api/resources/dns/subresources/records/), [official OpenAPI](https://github.com/cloudflare/api-schemas) | [API tokens](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/) with Zone Read and DNS Write as needed | One-page `result_info`, standard errors plus RFC 9457/text fallback, [rate-limit headers](https://developers.cloudflare.com/fundamentals/api/reference/limits/), and zero automatic mutation retries |
| Trackbooth | Live Agent API catalog export fetched by `trackbooth.catalog.sync`; local generated bundle in `plugins/trackbooth/agent-api/` is a reference/test fixture | `X-API-Key` with optional `X-Acting-As-Account`; default API URL `https://apis.trackbooth.com`; credential config may point to localhost or remote HTTPS | Live `GET /api/agent-api/catalog/export` for sync, live compact catalog/search and operation detail only for diagnostics, generated OpenAPI/catalog fixtures, and schema constraints audit |
| Shopify | [Admin GraphQL reference](https://shopify.dev/docs/api/admin-graphql/latest), [access scopes](https://shopify.dev/docs/api/usage/access-scopes), [versioning](https://shopify.dev/docs/api/usage/versioning), [limits](https://shopify.dev/docs/api/usage/limits) | Static operator-supplied Admin API access token sent as `X-Shopify-Access-Token`; no OAuth/token acquisition in StackOS | One versioned Admin GraphQL endpoint; StackOS exposes 58 curated actions, preserves `extensions.cost`, and documents the 751 schema root-field count in `shopify.md` |
| Generic HTTP | [RFC 9110 HTTP semantics](https://www.rfc-editor.org/rfc/rfc9110), [RFC 7617 Basic auth](https://www.rfc-editor.org/rfc/rfc7617), [RFC 6750 bearer usage](https://www.rfc-editor.org/rfc/rfc6750), [RFC 9457 problem details](https://www.rfc-editor.org/rfc/rfc9457) | RFC 7617 and RFC 6750 | RFC 9110 and RFC 9457 |
| Branding internal evidence | `branding.evidence.capture` and `branding.evidence.sanitize-mark` are internal StackOS resource actions documented by `plugins/branding/plugin.yaml`, `plugins/branding/README.md`, and `docs/plugins.md`; they have no external provider API. | none | Not paginated; one evidence resource is captured or marked per action call. |

## Current Executable Surface

| Connector key | Action refs | Current implementation refs | Manifest refs | Auth/setup implication |
| --- | --- | --- | --- | --- |
| `openai-images` | `utils.image.generate`, `utils.image.edit` | `stackos/actions/openai_images.py`, `stackos/integrations/openai_images.py` | `stackos/plugins/manifest.py` built-in utils media actions | API key payload; budget enforced by `openai-images` kind. |
| `xai-imagine` | `utils.xai.image.generate`, `utils.xai.image.edit`, `utils.xai.video.generate` | `stackos/actions/xai_imagine.py`, `stackos/integrations/xai_imagine.py` | `stackos/plugins/manifest.py` built-in utils xAI media actions | API key payload; budget enforced by `xai-imagine` kind; images/videos are persisted to generated assets and registered as generic media artifacts. |
| `reve` | `utils.reve.image.generate`, `utils.reve.image.edit`, `utils.reve.image.remix` | `stackos/actions/reve_images.py`, `stackos/integrations/reve_images.py` | `stackos/plugins/manifest.py` built-in utils Reve media actions | API key payload; budget enforced by `reve` kind; JSON base64 image outputs are persisted to generated assets and registered as generic image artifacts. `auth.test` is format-only because Reve does not document a free live credential probe. |
| `google-gemini-image` | `utils.google.image.generate`, `utils.google.image.edit` | `stackos/actions/google_gemini_image.py`, `stackos/integrations/google_gemini_image.py` | `stackos/plugins/manifest.py` built-in utils Google Gemini image actions | API key payload; budget enforced by `google-gemini-image` kind; inline MIME/base64 outputs are persisted to generated assets and registered as generic image artifacts. `auth.test` is format-only because Google does not document a free live image credential probe. |
| `google-veo` | `utils.google.video.generate` | `stackos/actions/google_veo.py`, `stackos/integrations/google_veo.py` | `stackos/plugins/manifest.py` built-in utils Google Veo action | API key payload; budget kind `google-veo`; long-running operation ids are retained in output metadata, and generated videos are persisted as generic video artifacts. |
| `ideogram` | `utils.ideogram.image.generate`, `utils.ideogram.image.remix` | `stackos/actions/ideogram_images.py`, `stackos/integrations/ideogram_images.py` | `stackos/plugins/manifest.py` built-in utils Ideogram image actions | API key payload; budget enforced by `ideogram` kind; temporary provider URLs are downloaded immediately, stripped from outputs/audit, persisted to generated assets, and registered as generic image artifacts. `auth.test` is format-only because Ideogram does not document a free live image probe. |
| `byteplus-seedream` / `byteplus-seedance` (provider `byteplus-ark`) | `utils.byteplus.image.generate`, `utils.byteplus.image.edit`, `utils.byteplus.video.generate` | `stackos/actions/byteplus_seedream.py`, `stackos/actions/byteplus_seedance.py`, `stackos/integrations/byteplus_ark.py` | `stackos/plugins/manifest.py` built-in utils BytePlus media actions | API key payload; budget enforced by `byteplus-ark` kind; provider URLs/base64 outputs are persisted to generated assets and registered as generic media artifacts. Seedance video uses the documented ap-southeast data plane with model-gated reference/first-last/audio/priority support. `auth.test` is format-only because ModelArk does not document a free live media probe. |
| `alibaba-wan` | `utils.alibaba.video.generate` | `stackos/actions/alibaba_wan.py`, `stackos/integrations/alibaba_wan.py` | `stackos/plugins/manifest.py` built-in utils Alibaba Wan action | API key payload; budget kind `alibaba-wan`; text mode uses the documented `wan2.6-t2v` contract, while media-input modes use `wan2.7-i2v` and require provider-fetchable URLs because the official API does not accept local generated-assets refs directly. |
| `kling-video` (provider `kling`) | `utils.kling.video.generate` | `stackos/actions/kling_video.py`, `stackos/integrations/kling_video.py` | `stackos/plugins/manifest.py` built-in utils Kling action | JSON credential payload with `access_key` and `secret_key`; JWT signing happens inside the daemon. StackOS v1 exposes `kling-v3` only and explicitly defers Omni/O1 model ids; async task ids and generated video artifacts are preserved without returning provider URLs. |
| `firecrawl` | `utils.web.scrape`, `utils.web.crawl`, `utils.web.map` | `stackos/actions/firecrawl.py`, `stackos/integrations/firecrawl.py:24` | `stackos/plugins/manifest.py` built-in utils actions | Bearer API key payload; budget enforced by `firecrawl`; `utils.web.extract` is deferred, not executable. |
| `jina` | `utils.web.read` | `stackos/actions/jina.py`, `stackos/integrations/jina_reader.py:17` | `stackos/plugins/manifest.py:384`, `stackos/plugins/manifest.py:506` | Optional bearer key: action sets `requires_credential: false` and `allows_credential: true`. |
| `reddit` | `utils.reddit.search-subreddit`, `utils.reddit.top-questions` | `stackos/actions/reddit.py`, `stackos/integrations/reddit.py` | `stackos/plugins/manifest.py` built-in utils actions | JSON OAuth application fields; the core resolver acquires and renews the application-only access token before the connector runs. |
| `sitemap` | `utils.sitemap.fetch` | `stackos/actions/sitemap.py`, `stackos/integrations/sitemap.py:84` | `stackos/plugins/manifest.py:525` | No provider and no credential. |
| `ftp` | `utils.ftp.directory.list`, `utils.ftp.file.upload`, `utils.ftp.file.download`, `utils.ftp.file.delete`, `utils.ftp.directory.create`, `utils.ftp.directory.delete`, `utils.ftp.path.rename` | `stackos/actions/ftp.py`, `stackos/integrations/ftp.py` | `stackos/plugins/manifest.py` built-in utils file-transfer actions | Password payload plus safe host/port/user/TLS/passive/timeout/encoding config; explicit FTPS protects data with `PROT P`; arbitrary agent-selected local and remote paths are supported without a generated-assets or FTP-only approval gate. |
| `cloudflare` | `utils.cloudflare.zones.list`, `utils.cloudflare.dns.records.list`, `utils.cloudflare.dns.records.get`, `utils.cloudflare.dns.records.create`, `utils.cloudflare.dns.records.edit`, `utils.cloudflare.dns.records.replace`, `utils.cloudflare.dns.records.delete` | `stackos/actions/cloudflare.py`, `stackos/integrations/cloudflare.py` | `stackos/plugins/manifest.py` built-in utils DNS actions | Daemon-held bearer API token; DNS-only scope; exact agent-selected ids and record payloads; no hidden preflight read or connector-specific zone restriction. |
| `dataforseo` | `seo.keyword.research`, `seo.serp.analyze`, `seo.paa.extract` | `stackos/actions/dataforseo.py`, `stackos/integrations/dataforseo.py:25` | `plugins/seo/plugin.yaml:20`, `plugins/seo/plugin.yaml:36`, `plugins/seo/plugin.yaml:66`, `plugins/seo/plugin.yaml:94` | Basic auth: `login` in credential config and password in encrypted payload. |
| `serper` | `seo.serper.search` | `stackos/actions/serper.py`, `stackos/integrations/serper.py` | `plugins/seo/plugin.yaml` | API key payload sent through the provider header; provider credit metadata is surfaced when present. |
| `ahrefs` | `seo.competitor.keywords`, `seo.backlink.research` | `stackos/actions/ahrefs.py`, `stackos/integrations/ahrefs.py:22` | `plugins/seo/plugin.yaml:31`, `plugins/seo/plugin.yaml:120`, `plugins/seo/plugin.yaml:148` | Bearer API key payload; requires eligible paid plan/API units. Connector reads the free subscription-info endpoint first, enforces documented row caps (Lite 100, Standard 250, Advanced 500, Enterprise action-bounded at 1000), and surfaces Ahrefs API-unit headers in metadata. |
| `google-search-console` | `seo.search-console.sites.list`, `seo.search-console.search-analytics.query`, `seo.search-console.sitemaps.list`, `seo.search-console.url.inspect` | `stackos/actions/google_search_console.py`, `stackos/integrations/google_search_console.py` | `plugins/seo/plugin.yaml` | Interactive Google authorization code plus manual access/refresh-token compatibility; core renewal and `webmasters.readonly` enforcement happen before connector dispatch. |
| `google-analytics` | `seo.ga4.account_summaries.list`, `seo.ga4.properties.metadata.get`, `seo.ga4.properties.run_report`, `seo.ga4.properties.run_realtime_report` | `stackos/actions/google_analytics.py`, `stackos/integrations/google_analytics.py` | `plugins/seo/plugin.yaml` | Interactive Google authorization code plus manual access/refresh-token compatibility; core renewal and `analytics.readonly` enforcement happen before connector dispatch. Safe `property_ref`, paging, and quota normalization remain connector concerns. |
| `google-tag-manager` | `seo.google-tag-manager.accounts.list`, `seo.google-tag-manager.containers.list`, `seo.google-tag-manager.container.snippet.get`, `seo.google-tag-manager.workspaces.list`, `seo.google-tag-manager.workspace.tags.list`, `seo.google-tag-manager.workspace.triggers.list` | `stackos/actions/google_tag_manager.py`, `stackos/integrations/google_tag_manager.py` | `plugins/seo/plugin.yaml` | Interactive Google authorization code plus manual access/refresh-token compatibility; core renewal and `tagmanager.readonly` enforcement happen before connector dispatch. Edit/publish/user-management scopes remain deferred. |
| `hubspot` | Provider-specific `gtm.hubspot.*` CRM Core, Sales, Marketing, and Bulk actions; HubSpot delivery through `communication.send`; signed webhook/workflow ingress | `stackos/actions/hubspot.py`, `stackos/api/hubspot_ingress.py`, shared communication delivery and ingress operations | `plugins/gtm/plugin.yaml`, focused `plugins/gtm/workflows/*.yaml`, [`hubspot.md`](hubspot.md) | Interactive authorization code; CRM Core is required, optional bundles are incrementally authorized, and returned scopes/account metadata determine readiness. App-level webhook/custom-action administration, imports, publish/bulk send, sequences, and beta revenue mutations remain explicit non-executable boundaries. |
| `wordpress` | `publishing.wordpress.post.create` | `stackos/actions/wordpress.py`, `stackos/integrations/wordpress.py:17` | `plugins/publishing/plugin.yaml:12`, `plugins/publishing/plugin.yaml:40` | WordPress site URL in config; username/application password in encrypted payload. |
| `ghost` | `publishing.ghost.post.create` | `stackos/actions/ghost.py`, `stackos/integrations/ghost.py:17` | `plugins/publishing/plugin.yaml:23`, `plugins/publishing/plugin.yaml:87` | Ghost URL and optional API version in config; Admin API key in encrypted payload. |
| `branding` | `branding.evidence.capture`, `branding.evidence.sanitize-mark` | `stackos/actions/branding.py` | `plugins/branding/plugin.yaml` | Internal resource connector; no provider credentials. Capture writes raw evidence only, and public clearance is a separate sanitize-mark action with reviewer, reason, and decision ref. |
| `http` | plugin-defined custom actions only | `stackos/actions/http.py:170` | documented in `docs/plugins.md:77`; no first-party manifest row | Static plugin config supplies URL/method/auth mode; daemon injects credential if allowed. |
| `trackbooth` | Fixed `trackbooth.catalog.sync`, `trackbooth.catalog.search`, `trackbooth.operation.describe`; sync upserts generated action rows from the live bulk export and exposes stable refs like `trackbooth.api.links_create` | `stackos/actions/trackbooth.py`, `stackos/integrations/trackbooth.py` | `plugins/trackbooth/plugin.yaml`, `plugins/trackbooth/agent-api/*` | API key payload; safe `api_base_url` config defaults to production and may point to localhost for local testing. |
| `shopify` | 58 refs under `shopify.*`, including products, orders, inventory, customers, and analytics actions | `stackos/actions/shopify.py`, `stackos/integrations/shopify.py` | `plugins/shopify/plugin.yaml`, `plugins/shopify/graphql/*`, `docs/integration-contracts/shopify-action-signoff.md` | Static Admin API access token payload; safe `store_domain` and optional `api_version` config. OAuth/token acquisition is out of scope. |

## Connection-Only Setup Providers

| Provider key | Current implementation refs | Manifest refs | Auth/setup implication |
| --- | --- | --- | --- |
| `openrouter` | `stackos/integrations/openrouter.py`, `stackos/integrations/__init__.py` | `stackos/plugins/manifest.py` built-in utils provider | API key payload with optional `HTTP-Referer` and `X-OpenRouter-Title` attribution config. Auth tests use read-only models metadata; no generic text-generation action is exposed. |

## Cross-Cutting Contract Principles

- Keep action refs provider-specific when provider schemas differ. The current `utils.*`, `seo.*`, and `publishing.*` refs are acceptable because the provider is part of the manifest and connector config; do not add generic `post.create`, `keyword.research`, or `campaign.create` as an executable abstraction without a project-local plugin that owns the mapping.
- Inputs should be explicit request payloads, not goals. For example, `publishing.ghost.post.create` receives a Ghost Admin API post payload; it should not decide title, status, author, tags, or schedule.
- Output should be safe JSON plus provenance. The shared `_result()` wrapper currently adds vendor and operation metadata in `stackos/actions/vendor_utils.py`, while OpenAI Images strips `b64_json` when persisted by `stackos/integrations/openai_images.py:110`.
- Rate-limit behavior must be visible at the action-call boundary. `BaseIntegration` has token-bucket pacing and retries for 429/5xx in `stackos/integrations/_base.py:177`, but generic HTTP does not use that base and currently collapses HTTP errors to status-only validation errors in `stackos/actions/http.py:230`.
- Budget availability is only meaningful when pre-call estimates or post-call actual costs match provider billing. DataForSEO reconciles `tasks[].cost`; Ahrefs captures API-unit headers in action metadata, but `estimate_cost_cents == 0` and `enforce_budget: false` remain correct until StackOS has an API-unit budget model.
- Pagination/status contracts must be modeled as actions before templates rely on them. Firecrawl crawl currently starts a job and returns an id, but the docs require polling `GET /v2/crawl/{id}` and following `next` when the result exceeds 10 MB.

## Provider Findings

### OpenAI Images

Current: `utils.image.generate` and `utils.image.edit` map to
`OpenAIImagesActionConnector`. The connector validates prompt length, explicit
GPT Image model profile, size, quality, `n`, output format, generated-asset
input refs, edit ref count, and `input_fidelity` model support. It then calls
`/v1/images/generations` or `/v1/images/edits` with default model
`gpt-image-2`.

Gaps/mismatches:

- Resolved: manifest and connector now share GPT Image profiles for `gpt-image-2`, `gpt-image-1.5`, `gpt-image-1`, and `gpt-image-1-mini`. Legacy DALL-E quality names are not accepted by the generic StackOS action.
- Resolved: budget estimates use the current GPT Image table for the supported model/size/quality presets. `auto` quality is estimated conservatively as high for pre-check purposes.
- Resolved: GPT Image base64 outputs are persisted under generated assets,
  registered as generic `image` artifacts during repository-backed execution,
  and returned as artifact URLs/ids instead of raw `b64_json`.
- Resolved: edit inputs are bounded to generated-asset png/jpg/webp refs and
  preflighted against OpenAI's 50 MB input image limit before vendor upload.
- Remaining: `gpt-image-2` supports broader arbitrary resolutions in OpenAI docs, but StackOS exposes only explicit presets plus `auto` until arbitrary-size budget estimates and UI affordances are modeled.
- Remaining: OpenAI's image guide and `gpt-image-2` model page document
  `gpt-image-2` Image API generation/edit support, while some API-reference
  enum snippets can lag the model listing. Treat the guide/model page as the
  source for `gpt-image-2` support and re-check before changing defaults.

Recommended corrections:

- Add a scheduled provider-doc audit for OpenAI image pricing and model profile changes.
- Keep persisted output URL/artifact-id behavior documented in the manifest or action docs, because consumers should not expect raw base64.

### xAI Imagine

Current: `utils.xai.image.generate`, `utils.xai.image.edit`, and
`utils.xai.video.generate` map to `XAIImagineActionConnector`. The connector
uses the StackOS v1 Grok Imagine model choices, validates image/video
size-control enums, enforces mode-specific image-reference rules, downloads
temporary xAI-hosted media promptly, and registers generated outputs as generic
`image` or `video` artifacts when repository context is available.

Gaps/mismatches:

- Resolved: image actions use `grok-imagine-image-quality`; cheaper
  `grok-imagine-image` and deprecated `grok-imagine-image-pro` are not exposed.
- Resolved: video generation uses async submit/poll/download for
  `grok-imagine-video` and preserves provider `request_id` in output metadata.
- Deferred: `grok-imagine-video-1.5-preview` is not exposed because xAI
  documents it as an image-to-video-only preview model; add it only after a
  dedicated preview-model contract/signoff.
- Resolved: pre-call budget estimates follow official Imagine pricing units:
  image output by `1k`/`2k`, video output by `480p`/`720p` seconds, and
  input-image charges for edit/image-reference modes. Successful responses
  reconcile action-call cost from xAI `usage.cost_in_usd_ticks`, with budget
  top-up when actual cost exceeds the pre-call estimate.
- Resolved: single-image edits reject `aspect_ratio`; xAI keeps the input image
  ratio for that mode.
- Remaining: xAI video editing and video extension are documented separate
  endpoint families and are not exposed until dedicated actions exist.
- Remaining: official docs do not expose StackOS-ready controls for watermark,
  custom fps, custom audio controls, or exact temporary URL expiry.
- Remaining: generated-assets input refs are limited to PNG/JPEG for v1; public
  URL/file-id inputs can be added after the asset and trust model is explicit.

Recommended corrections:

- Re-audit pricing and model names on the normal provider-doc schedule.
- Add dedicated edit/extend video actions only when those workflows need them
  and the output artifact/audit contract is reviewed separately.

### Reve

Current: `utils.reve.image.generate`, `utils.reve.image.edit`, and
`utils.reve.image.remix` map to `ReveImagesActionConnector`. The connector
uses Reve's synchronous JSON endpoints, requests `application/json`, decodes
base64 PNG output, persists generated bytes under generated assets, and
registers generic `image` artifacts during repository-backed execution.

Gaps/mismatches:

- Resolved: create/edit/remix action schemas follow the official rendered
  Reve docs and official JS bundle facts: 2560-character prompt/instruction
  cap, 1-15 `test_time_scaling`, one edit reference image, and 1-6 remix
  reference images.
- Resolved: remix generated-assets refs are preflighted against Reve's
  documented combined 32 million pixel cap; the 10 MiB per-file input check is
  a StackOS safety cap before encoding local files as base64. StackOS v1 local
  refs support WEBP/JPEG/PNG/GIF/TIFF because those formats have explicit
  preflight parsers in the wrapper.
- Resolved: pre-call budget estimates use official base credit costs
  (create 18 credits, edit/remix 30 credits, fast edit/remix 5 credits), while
  successful action-call cost reconciles from `credits_used`.
- Resolved: reference images are daemon-local generated-assets files, encoded
  as base64 inside the connector, and stripped from audit/request metadata.
- Remaining: Reve does not document a free live credential probe. StackOS
  `auth.test` for `reve` is format-only and does not make a billable image
  request; operators should verify live credentials with a deliberate action.
- Remaining: postprocessing (`upscale`, `remove_background`, `fit_image`,
  `effect`) and binary `Accept: image/*` response modes are not exposed in v1.
- Remaining: docs are console-rendered and JS-bundle-backed rather than a
  stable OpenAPI spec; re-check against live official pages before adding
  options.

Recommended corrections:

- Add live operator smoke evidence after a real Reve credential is connected.
- Add endpoint-specific postprocessing actions only when their variable credit
  costs and artifact outputs are modeled.

### Google Gemini Image

Current: `utils.google.image.generate` and `utils.google.image.edit` map to
`GoogleGeminiImageActionConnector`. The connector uses the Gemini Developer API
`models/{model}:generateContent` endpoint with `x-goog-api-key`, validates
model-specific aspect ratios, Gemini 3 image sizes, generated-assets image
refs, model-specific input-image counts, and the inline 20 MB request envelope
before provider calls. Generated inline MIME/base64 image parts are persisted
under generated assets and registered as generic `image` artifacts during
repository-backed execution.

Gaps/mismatches:

- Resolved: the StackOS provider key is `google-gemini-image`, covering
  `gemini-3.1-flash-image`, `gemini-3-pro-image`, and
  `gemini-2.5-flash-image` as independent Google image actions rather than a
  provider-neutral image abstraction.
- Resolved: `utils.google.image.generate` supports text-to-image, while
  `utils.google.image.edit` supports text plus generated-assets image refs for
  image-to-image, multi-image references, style transfer, object-preserving
  edits, and character consistency.
- Resolved: Gemini 3 image refs are capped at 14 and `gemini-2.5-flash-image`
  refs are capped at 3. Local generated-assets refs support jpg/jpeg/png/webp;
  HEIC/HEIF are not exposed until parser/test coverage exists.
- Resolved: `gemini-3.1-flash-image` exposes `512`, `1K`, `2K`, and `4K`
  output size controls; `gemini-3-pro-image` exposes `1K`, `2K`, and `4K`;
  `gemini-2.5-flash-image` does not expose image-size control.
- Resolved: budget estimates include official output-image prices and the
  documented Gemini 3 Pro Image input-image equivalent where applicable. The
  tokenized text/image input charge remains provider-invoiced and is not
  pre-estimated.
- Remaining: Google Search/Image grounding, conversational chat state, video
  input, Files API input, output compression/MIME controls, person-generation
  controls, and Vertex AI parity are not exposed in v1.

Recommended corrections:

- Add live operator smoke evidence after a real Google Gemini credential is
  connected.

### Ideogram

Current: `utils.ideogram.image.generate` and `utils.ideogram.image.remix` map to
`IdeogramImagesActionConnector`. The connector uses the first-party Ideogram
4.0 multipart endpoints, validates documented 2K resolutions, excludes
`rendering_speed=FLASH` because the v4 docs say it currently returns 400,
validates one generated-assets remix image as signed JPEG/PNG/WEBP at <=10 MB
(10,000,000 bytes), and persists every temporary provider image URL under
generated assets before returning action output.

Gaps/mismatches:

- Resolved: the StackOS provider key is `ideogram`, with provider-specific
  action refs `utils.ideogram.image.generate` and
  `utils.ideogram.image.remix`.
- Resolved: Ideogram 4.0 generate/remix expose the 23 documented 2K
  resolution enums and rendering speeds `TURBO`, `DEFAULT`, and `QUALITY`;
  `FLASH` is listed as unsupported in StackOS v1.
- Resolved: temporary signed provider URLs are downloaded immediately and
  stripped from outputs and action-call audit records before agents see
  results.
- Resolved: budget estimates use official per-output prices: $0.03 Turbo,
  $0.06 Default, and $0.10 Quality; successful calls reconcile against the
  actual number of returned output images.
- Remaining: structured `json_prompt`, magic-prompt, describe, Ideogram 3.0
  inpaint/reframe/replace-background, remove-background, upscale, legacy edit,
  and custom-model paths are not exposed in v1.

Recommended corrections:

- Add live operator smoke evidence after a real Ideogram credential is
  connected.

### BytePlus Seedream

Current: `utils.byteplus.image.generate` and `utils.byteplus.image.edit` map to
`BytePlusSeedreamImageActionConnector`. The connector uses the reusable
`BytePlusArkIntegration` wrapper for ModelArk `POST /api/v3/images/generations`,
validates exact priced Seedream model ids, BytePlus region support,
model-specific abstract size shortcuts, custom `WxH` size limits,
sequential-generation controls, combined reference-plus-output count,
`output_format` model support, and generated-assets JPEG/PNG/WEBP input refs
with official dimension limits before provider calls. It requests URL output,
downloads 24-hour provider URLs immediately, also persists `b64_json` fallback
outputs, strips provider media payloads from agent-visible output/audit, and
registers generic `image` artifacts.

Gaps/mismatches:

- Resolved: the StackOS provider key is `byteplus-ark`, covering the ModelArk
  wrapper and the Seedream image action connector.
- Resolved: StackOS v1 exposes only priced official model ids
  `seedream-5-0-lite-260128`, `seedream-4-5-251128`, and
  `seedream-4-0-250828`; non-lite `seedream-5-0-260128` remains deferred
  until pricing and account availability are modeled.
- Resolved: custom size validation follows official total-pixel and aspect-ratio
  limits, while shortcuts are model-specific: 5 Lite accepts `2K`/`3K`/`4K`,
  4.5 accepts `2K`/`4K`, and 4.0 accepts `1K`/`2K`/`4K`.
- Resolved: pre-call budget estimates use official per-output image prices and
  reserve the requested maximum generated image count for sequential calls;
  action-call cost records reconcile against `usage.generated_images` or
  persisted output count, while the budget ledger remains a conservative
  precharge/top-up guardrail.
- Resolved: Seedance video now has a dedicated `byteplus-seedance` connector
  and `utils.byteplus.video.generate` action using the shared ModelArk wrapper.
- Remaining: streaming partial images, external URL image inputs,
  BMP/TIFF/GIF/HEIC/HEIF uploads for Seedream image actions,
  `seededit-3-0-i2i` specialized controls, and advanced Seedance edit,
  extension, callback, draft-task, and provider asset-id modes remain
  unsupported until dedicated schemas and tests land.

Recommended corrections:

- Add live operator smoke evidence after a real BytePlus ModelArk credential,
  billing, and model activation are confirmed.

### Firecrawl

Current: three executable utility actions map to v2 `scrape`, `crawl`, and `map`. `utils.web.extract` is a manifest-deferred contract because Firecrawl extract is async and needs status polling before StackOS can present it as an executable workflow action. The wrapper uses `Authorization: Bearer`, v2 base URL, and estimated costs for executable actions.

Gaps/mismatches:

- `web.crawl` only starts the crawl job. Official v2 crawl returns `id`/`url`; result retrieval requires `GET /v2/crawl/{id}` and may return `next` for large results. There is no `utils.web.crawl-status` action yet.
- Current `scrape.formats` validation only accepts string arrays in `stackos/actions/firecrawl.py`, while Firecrawl v2 documents string and object format entries such as JSON extraction and screenshot objects.
- `map` does not expose documented `limit`, `sitemap`, `includeSubdomains`, `ignoreQueryParameters`, or location options.
- Resolved for agent safety: `extract` is no longer wired as executable. The wrapper implementation remains a daemon helper until a status action and output artifact contract exist.

Recommended corrections:

- Before templates assume crawl content, add a separate status/pagination action or rename/describe `utils.web.crawl` as a submit-only action everywhere.
- Link `stackos/integrations/firecrawl.py` comments to the v2 endpoint pages and error docs.
- Expand schemas only when each additional option has bounded defaults and budget implications, especially `proxy`, `actions`, and `maxConcurrency`.

### Jina Reader

Current: `utils.web.read` maps to `https://r.jina.ai/{url}` with optional bearer auth. Manifest correctly sets optional credential behavior.

Gaps/mismatches:

- Provider manifest says `auth_type="api-key"` in `stackos/plugins/manifest.py:384`, which may imply setup is required even though execution works without a credential.
- The wrapper builds `https://r.jina.ai/{url}` directly in `stackos/integrations/jina_reader.py:36`. That matches the Reader pattern, but input validation should explicitly require absolute `http(s)` target URLs before concatenation.
- No response-size/token-budget contract is attached to action output; Reader pages can be large.

Recommended corrections:

- Add provider setup copy saying the key is optional and improves quota.
- Add official Reader API link near the wrapper base URL.
- Consider output truncation/resource persistence guidance for long Markdown instead of returning every byte in action output.

### Reddit

Current: `utils.reddit.search-subreddit` and `utils.reddit.top-questions` receive
an application-only OAuth access token from the shared credential resolver and
call Reddit listing endpoints. The resolver, not the connector, exchanges the
stored client id/secret and renews the short-lived token.

Gaps/mismatches:

- Resolved in the manifest and runtime: Reddit declares
  `auth_type="oauth-client-credentials"`; `client_secret` and the configured
  `user_agent` remain in the encrypted application payload, and token
  acquisition is core-owned. The wrapper still needs richer pagination and enum
  validation.
- Reddit listing pagination uses `after`/`before`, `limit`, `count`, and `show`; actions expose only `limit`, so callers cannot page beyond the first listing slice.
- `sort` and `time_filter` are unbounded strings in `stackos/actions/reddit.py`, so invalid provider enum values reach Reddit.
- Resolved in the manifest: `top_questions` is described as raw top Reddit posts, and the executing agent owns any question-shaped filtering.

Recommended corrections:

- Constrain `sort` and `time_filter` enums and add optional `after` pagination input/output.

### DataForSEO

Current: SEO actions call DataForSEO live endpoints via Basic auth. `login` lives in credential config; password lives in the encrypted payload. Cost reconciliation reads `tasks[].cost` in `stackos/integrations/dataforseo.py:57`.

Gaps/mismatches:

- Resolved: keyword volume now caps requests at 1000 keywords and the wrapper default QPS is 0.2, matching the Google Ads Live 12 requests/minute contract.
- Resolved: SERP live depth is capped at 100 for the exposed `seo.serp.analyze` action.
- Resolved: exposed DataForSEO locale inputs now use provider-native `language_code` and `location_name` keys; schema enums reject display names such as `English` before a provider call.
- DataForSEO docs distinguish current and legacy Labs routes. The wrapper uses `/dataforseo_labs/google/.../live` in `stackos/integrations/dataforseo.py:127` and `stackos/integrations/dataforseo.py:145`; comments should link current route docs so maintainers do not accidentally follow legacy pages.
- `domain_intersection` and `keywords_for_site` are implemented in the connector but not exposed as plugin actions, except Ahrefs equivalents. That is okay, but undocumented dormant operations can confuse future action expansion.

Recommended corrections:

- Add optional `limit`/`offset` where Labs endpoints support them before exposing more Labs actions.
- Add comments linking each wrapper method to the exact DataForSEO endpoint doc.
- Keep current exposed actions narrow until schemas cover provider limits and pagination.

### Serper.dev

Current: `seo.serper.search` posts explicit search inputs to the Serper Google
Search endpoint and returns the provider JSON as evidence. The daemon-held
credential is sent only inside the connector boundary.

Gaps/mismatches:

- Public docs and examples focus on the product/API playground path rather than
  a stable versioned docs tree; the concrete `google.serper.dev` endpoint,
  `X-API-KEY` header, and payload field contract are based on provider
  dashboard/playground examples. Keep comments and manifests tied to the
  provider entrypoint and re-check the concrete request shape before expansion.
- Provider credit metadata is surfaced when present, but StackOS cannot
  pre-budget Serper calls until provider credit cost semantics are modeled.
- The action exposes page input but no cursor abstraction. That is fine for live
  search evidence; templates must ask for bounded pages explicitly.

Recommended corrections:

- Keep the search action narrow and provider-specific instead of abstracting it
  as a generic `search.google` action.
- Add provider-credit budget mapping only after verified response metadata and
  account plan behavior are documented.

### OpenRouter

Current: OpenRouter is a Utilities connection provider and auth-test wrapper
only. `auth.test` calls the read-only models endpoint so operators can verify
stored credentials and optional attribution headers.

Gaps/mismatches:

- There is no StackOS text-generation action yet by design. A future model
  action needs explicit workflow policy, step grants, output persistence,
  privacy/retention expectations, budget semantics, and operator approval.
- The credits endpoint requires credit-management access and is not part of the
  normal StackOS connection test.

Recommended corrections:

- Do not wire OpenRouter into `action.list` until a provider-safe action
  contract exists.
- When adding model execution later, define project/workflow ownership before
  accepting arbitrary prompts or routing decisions.

### Ahrefs

Current: two SEO actions call Site Explorer organic keywords and all backlinks using bearer auth.

Gaps/mismatches:

- Resolved for agent safety: Ahrefs actions do not claim monetary StackOS budget enforcement while provider billing is API-unit based.
- Resolved: wrapper reads Ahrefs unit headers such as `x-api-units-cost-total-actual` and the action connector stores them in metadata.
- Resolved: action execution calls the free `subscription-info/limits-and-usage` endpoint before costed row calls and enforces documented row caps: Lite 100, Standard 250, Advanced 500, Enterprise action-bounded at 1000.
- Resolved: backlink `mode` is constrained to documented values: `exact`, `prefix`, `domain`, and `subdomains`.
- Remaining gap: API-unit budgets are not yet modeled as first-class StackOS budgets, so Ahrefs monetary budget enforcement stays disabled.

Recommended corrections:

- Add an API-unit budget policy before re-enabling StackOS budget gates for Ahrefs.
- Keep wrapper method docs, manifest plan-cap copy, and API-unit header capture synced with Ahrefs API documentation.

### Google SEO Measurement

Current: the SEO plugin exposes read-only Google Search Console, GA4, and
Google Tag Manager actions through provider-specific connectors and daemon-held
OAuth credentials. Search Console covers site inventory, search analytics,
sitemaps, and indexed-version URL inspection. GA4 covers account summaries,
metadata, standard reports, and realtime reports. Tag Manager covers account,
container, snippet, workspace, tag, and trigger inventory.

Gaps/mismatches:

- Resolved: all three providers use the shared interactive authorization-code
  start/fixed-callback path. Manual access/refresh-token methods remain only for
  compatibility, and the core resolver renews tokens before dispatch. Replacing
  imported token material clears old grants; scope-gated actions require grants
  returned by a trusted exchange and do not infer them from the provider
  manifest.
- Resolved: action manifests declare the exact read scope and scope failure
  prevents connector execution.
- Google write/admin scopes are intentionally not exposed: Search Console
  write, Analytics edit/Measurement Protocol ingestion, Tag Manager edit,
  publish, version, and user/account-management scopes all require separate
  contracts.
- Search Console Search Analytics is top-row reporting, not guaranteed full
  export. URL Inspection returns Google's indexed version, not a live test.
- No legacy `gsc`, `gscOauth`, `gsc_metrics`, or `gsc_metrics_daily` surfaces
  are resurrected. SEO measurement summaries should use
  `search-performance-snapshot` resources or explicit future import contracts.

Recommended corrections:

- Add workflow template `action_refs` only when a specific SEO workflow needs
  these reads; run-plan `action.execute` grants must name exact action refs.
- Add service-account auth only as a deliberate new auth method with signing
  tests and dependency review.
- Keep endpoint docs and scope copy synced with official Google API docs before
  expanding any write/publish/admin surface.

### WordPress

Current: `publishing.wordpress.post.create` sends a raw REST post payload to `/wp-json/wp/v2/posts` using Basic auth with an Application Password.

Gaps/mismatches:

- Resolved: the manifest uses `auth_type: application-password`, renders
  username/application-password fields, and labels the site field as an HTTPS
  site URL.
- Current credential test calls `users/me?context=edit`, but post creation also needs the user capability to create posts; roles are returned in `stackos/integrations/wordpress.py:87`, but availability does not reflect publish capability.
- No media upload, post update, status transition, taxonomy lookup, or pagination actions exist. That is fine, but templates should not assume these are available.

Recommended corrections:

- Add docs links in wrapper comments to Application Passwords and Posts.
- Add capability warnings to manifest/template docs before offering publish flows beyond create-post.

### Ghost

Current: `publishing.ghost.post.create` signs an Admin API key into a short-lived JWT and posts `{posts: [post]}` with `source=html`.

Gaps/mismatches:

- Setup label says `Admin URL` in `plugins/publishing/plugin.yaml:30`, while the wrapper appends `/ghost/api/admin/...` in `stackos/integrations/ghost.py:94`. This should be documented as the Ghost site/admin domain root, not a full endpoint URL.
- Default `api_version` is `v5.0` in `stackos/actions/ghost.py`; current Ghost installs may use newer API versions. The contract should specify which Admin API version is targeted and test against it.
- Only `source: html` is allowed in the manifest in `plugins/publishing/plugin.yaml:99`; that matches the current connector, but image upload and mobiledoc/lexical pathways are absent.
- No image upload, post update, scheduling status validation, or pagination actions exist.

Recommended corrections:

- Add official Admin API auth/posts/images links to wrapper comments.
- Clarify `ghost_url` and `api_version` setup semantics.
- Keep action availability limited to create-post until upload/update contracts exist.

### Sitemap

Current: no-auth public sitemap fetch with XML parsing, recursion caps, byte caps, timeout caps, and partial errors.

Gaps/mismatches:

- The wrapper caps each response at 10 MiB in `stackos/integrations/sitemap.py:45`, while the protocol allows 50 MB uncompressed and 50,000 URLs. This lower cap is a deliberate daemon safety choice, but it should be documented as such in action output/contract docs.
- Connector validation allows `max_entries` up to 20,000 in `stackos/actions/sitemap.py`, while wrapper default is 5,000 in `stackos/integrations/sitemap.py:60`. That is okay, but templates should choose conservative values.
- Namespace comments cite sitemaps.org already in `stackos/integrations/sitemap.py:38`; good.

Recommended corrections:

- Add the sitemaps.org protocol link to the action/contract doc and call out StackOS-specific safety caps.
- Preserve partial-result behavior; do not turn per-sitemap failures into all-or-nothing exceptions.

### Generic HTTP

Current: `http` is a static custom HTTP/Webhook connector for plugin-authored actions. URL, method, request mode, auth mode, static headers, timeout, and response mode live in `config_json.http`; agents supply only schema-validated action input.

Gaps/mismatches:

- No retry/rate-limit policy is applied. Unlike vendor integrations, this connector does not use `BaseIntegration`.
- HTTP errors lose response body/details: any status >=400 raises `ValidationError` with only the status in `stackos/actions/http.py:232`.
- Static headers reject secret-looking names in `stackos/actions/http.py:45`, but custom header auth intentionally injects a credential header at execution time in `stackos/actions/http.py:214`. That split is good and should be documented in plugin-authoring examples.
- SSRF/sensitive-network policy is not visible here. The static URL constraint helps, but project-local plugin installation should have review/approval around private network targets.

Recommended corrections:

- Document this as an escape hatch for user-owned/internal systems, not a web browsing tool.
- Return redacted structured error bodies where possible, preferably compatible with RFC 9457 problem details.
- Add optional static retry policy fields only after deciding how action-call audit should represent retries for user-owned endpoints.

## Action Availability Implications

- `missing_connector` is reliable because registry keys are explicit in `stackos/actions/__init__.py:40`.
- `missing_credential` is reliable for required credentials. The known root
  auth classifications are now semantic: OAuth/client-secret pairs remain
  OAuth, Reddit/Taboola are OAuth client credentials, and WordPress is an
  application password.
- `missing_budget` is reliable as a gate, but not necessarily as a cost control. Ahrefs captures provider API-unit headers but still estimates zero monetary cost until StackOS models unit budgets. Reddit/Jina/WordPress/Ghost/sitemap/HTTP do not enforce budgets.
- Optional credentials work for Jina because the manifest has `requires_credential: false` and `allows_credential: true` in `stackos/plugins/manifest.py:515`.
- Provider-disabled/plugin-disabled statuses are generic and correct, but they do not express provider-specific plan eligibility, scopes, roles, or endpoint permissions.

## Gaps Before Expanding Actions

1. Add exact provider doc links near wrapper methods before adding options. This prevents “old docs by memory” drift.
2. Keep auth method fields and semantic `auth_type` labels synchronized when
   adding providers; do not infer protocol from the presence of a secret field.
3. Add pagination/status actions before expanding crawl, listing, backlinks, posts, or keyword inventories.
4. Normalize provider errors into safe, structured action-call metadata without exposing secrets or raw stack traces.
5. Make budget semantics honest: use actual costs/headers where available, or mark budget as call-count/approval-only instead of monetary.
6. Constrain provider enums and limits in action schemas, not only in code.
7. Keep dormant wrapper operations hidden until plugin manifests, tests, grants, and docs exist.

## Recommended Manifest/Template/Code Comments

- `stackos/integrations/openai_images.py`: keep links to the Image generation guide and Images API reference beside `_IMAGE_COSTS`; refresh GPT Image pricing on provider-doc audits.
- `stackos/plugins/manifest.py`: add new GPT Image model profiles only when their size, quality, format, and cost semantics are documented.
- `stackos/integrations/firecrawl.py`: link each method to Firecrawl v2 endpoint docs; add a comment that `crawl()` submits a job only.
- `stackos/plugins/manifest.py`: rename/describe `utils.web.crawl` as submit-only unless a crawl-status action is added.
- `stackos/integrations/jina_reader.py`: link Reader API and validate absolute target URL shape before path concatenation.
- `stackos/plugins/manifest.py`: add optional-auth setup copy for Jina.
- `stackos/integrations/reddit.py`: keep OAuth2/Data API links and document listing pagination headers/fields; token acquisition remains in the core resolver.
- `stackos/integrations/dataforseo.py`: link exact endpoint docs above each method; note keyword volume 12 RPM and task-size limits.
- `plugins/seo/plugin.yaml`: keep DataForSEO keyword count, SERP depth, provider-native locale keys, and locale enums synced with endpoint docs.
- `stackos/integrations/ahrefs.py`: keep API v3 intro, API keys, limits consumption, subscription usage, organic keywords, and all backlinks links synced with unit-header capture.
- `plugins/seo/plugin.yaml`: keep Ahrefs plan row caps, API-unit requirements, and budget meaning synced with pricing/API docs.
- `stackos/integrations/wordpress.py`: link WordPress Authentication, Application Passwords, and Posts docs; mention HTTPS and post capability.
- `plugins/publishing/plugin.yaml`: keep the WordPress application-password credential shape and Ghost URL root/API version explicit.
- `stackos/integrations/ghost.py`: link Ghost Admin API auth/posts/images docs; document JWT `aud`, expiration, and `Accept-Version` target.
- `stackos/actions/http.py`: link RFC 9110, RFC 7617, RFC 6750, and RFC 9457; document why static config cannot contain secret headers while injected auth headers can.
