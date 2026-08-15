# StackOS SEO site inventory

Captured: 2026-08-08
Canonical host: `stackos.flowmonkey.io`
Completeness: bounded-complete against the 165 URLs in the live sitemap.

## URL sets

| Set | Count | Interpretation |
| --- | ---: | --- |
| Current live sitemap | 165 | Intended current canonical HTML set. |
| Live sitemap URLs returning 200/indexable/self-canonical | 165 | Current technical observation. |
| Reachable from homepage graph | 164 | One plugin hub is not reachable. |
| Sitemap URLs with `lastmod` | 16 | Getting-started and current article records. |
| Sitemap URLs without `lastmod` | 149 | Homepage, hubs, agents, workflows, orchestrators, and integrations. |
| Reconstructed pre-Jul 28 sitemap | 162 | Slashless publisher URL convention. |
| Reconstructed Jul 28 sitemap | 165 | Trailing-slash publisher URL convention. |
| GSC indexed screenshot | 218 | Property-wide Google-known indexed set; URL rows unavailable. |
| GSC not-indexed screenshot | 41 | 38 redirects, 2 crawled-not-indexed, 1 redirect error. |
| GSC total known | 259 | Not directly equivalent to current sitemap membership. |

## Sitemap template inventory

| Template | Sitemap count | Detail count | Notes |
| --- | ---: | ---: | --- |
| Homepage | 1 | 1 | 200, self-canonical, indexable. |
| Getting started | 1 | 1 | 200, self-canonical, indexable. |
| Library root | 1 | 1 | Main section hub. |
| Agent family | 47 | 46 | Hub plus 46 detail pages. |
| Article family | 16 | 15 | Hub plus 15 articles. |
| Integration family | 65 | 64 | Hub, 56 providers, and 8 plugin hubs. |
| Orchestrator family | 5 | 4 | Hub plus 4 details. |
| Workflow family | 29 | 28 | Hub plus 28 details. |

## Full-sitemap checks

| Check | Failures |
| --- | ---: |
| Non-200 response | 0 |
| Final URL mismatch | 0 |
| Missing title | 0 |
| Missing description | 0 |
| Missing canonical | 0 |
| Canonical mismatch | 0 |
| Missing robots directive | 0 |
| `noindex` inside sitemap | 0 |
| H1 count other than one | 0 |
| Invalid JSON-LD | 0 |
| Duplicate title group | 0 |
| Duplicate description group | 0 |
| Exact duplicate main-text group | 0 |
| Internal HTML link to slashless redirect variant | 0 |

The full crawl found 29 URLs below a bounded sparse-content diagnostic. That threshold was used only to locate pages for review; it is not a word-count recommendation or ranking rule.

## Internal-link graph

- Reachable sitemap URLs: 164 of 165.
- Maximum depth: 4.
- Depth 0: 1 URL.
- Depth 1: 4 URLs.
- Depth 2: 59 URLs.
- Depth 3: 97 URLs.
- Depth 4: 3 URLs.
- URLs with no raw inlink source: 0.
- URLs with fewer than three unique inlink sources: 7.
- Sitemap URL not reachable from homepage: `https://stackos.flowmonkey.io/library/integrations/plugins/linear/`.

## Representative affected URLs

| URL | Live state | GSC/context state | Interpretation |
| --- | --- | --- | --- |
| `/library/articles/how-to-use-product-evidence-without-writing-a-product-pitch/` | 200, indexable, self-canonical, SSR Article schema | Crawled – currently not indexed | No observed technical blocker; Google selection unresolved. |
| `/feed.xml` | 200 XML, `X-Robots-Tag: noindex`, not in sitemap | Crawled – currently not indexed | Correct, intentional exclusion. |
| `/library/articles/how-to-refine-ai-agent-workflow` | One live redirect to slash target | Redirect error | Historical failure not reproducible. |
| `/library/articles/how-to-refine-ai-agent-workflow/` | 200, indexable, self-canonical | Target not shown | Current canonical target. |
| `/library/integrations/x-api` | Redirect | Page with redirect | Expected historical source. |
| `/library/integrations/x-api/` | 200, indexable, self-canonical | Target not shown | Current canonical target. |
| `/library/integrations/plugins/linear/` | 200, indexable, self-canonical | Not shown | In sitemap but not homepage-reachable; overlaps provider page. |
| Unknown route | Real 404, noindex/nofollow, no canonical | Not shown | Correct 404 behavior. |

## Robots, feed, sitemap, and 404

- `robots.txt`: `200 text/plain`; `User-agent: *`; no disallow; canonical sitemap declared.
- `sitemap.xml`: `200 application/xml`; 165 unique absolute HTTPS trailing-slash canonical HTML URLs.
- `feed.xml`: `200 application/xml`; valid RSS; 15 items; outside sitemap; `X-Robots-Tag: noindex`.
- Missing routes: HTTP 404; branded body; `noindex, nofollow`; no canonical.
- `/404.html`: static 200 asset; `noindex, nofollow`; no canonical; absent from sitemap.

## Crawl Stats reconciliation

Operator screenshot metrics:

- Total requests: 5.53K.
- Total bytes: 28.7 MB.
- Average response: 146 ms.
- Host health: no problems in the last 90 days.
- Responses: 92% 200; 8% permanent redirect; less than 1% each 304, 404, and other 4xx.
- File types: 45% JSON; 26% JavaScript; 12% HTML; 8% other; 7% CSS.

The total request graph includes resources and each redirect hop. A live Nuxt article exposed or loaded route `_payload.json` resources and build metadata JSON, explaining much of the JSON share. The post-Jul 29 decline therefore cannot be interpreted as 165 HTML pages becoming inaccessible. Green host health, fast average response, and virtually no bad response class rule out a broad crawl-capacity failure in this window.

## Metadata observations

- 27 titles exceeded a bounded 65-character presentation diagnostic.
- 43 descriptions exceeded a bounded 165-character presentation diagnostic.
- Four agent titles were under 25 characters and generic: Review Agent, Delivery Agent, Planning Agent, and Test Designer.
- No title/description was missing or duplicated.
- No missing `alt` attribute was observed on images inside sitemap-page main content.

Character counts are presentation diagnostics, not Google limits. The actionable issue is intent specificity on generic agent pages, not mechanical shortening of every long title or description.

## Content-template observations

- Article details: substantial; median main-text count approximately 1,338 words in the raw crawl.
- Workflow details: median approximately 476 words.
- Agent details: median approximately 322 words; several sparse role contracts.
- Integration family: median approximately 350 words; strongest pairwise structural similarity.
- Orchestrator family: median approximately 185 words; several sparse pages.
- Exact main-text duplicates: none.

The strongest five-word-shingle integration pair scored 0.513 similarity. This is a diagnostic for consolidation/editorial review, not a measured Google penalty.

## Evidence boundaries

Observed completely: current sitemap response/indexability/canonical/metadata/schema state and current homepage-rooted graph.

Measured from operator screenshots: GSC Page Indexing counts/examples, validation dates, Crawl Stats totals, response/file-type distributions, and host status.

Unmeasured: GSC page/query performance, Google-selected canonical for targets, GA4 traffic/conversions, historical redirect chain, field/mobile Core Web Vitals, backlink authority, and exact SERP losses.
