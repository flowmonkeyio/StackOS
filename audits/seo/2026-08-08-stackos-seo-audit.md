# StackOS SEO root-cause audit

Audit date: 2026-08-08
Site: https://stackos.flowmonkey.io/
Change point: 2026-07-28
Scope: technical SEO, indexability, redirects/canonicals, sitemap/robots/feed, internal linking, structured data, rendered HTML, content-template risk, on-page metadata, bounded performance observation, Search Console screenshot reconciliation, and repository change-forensics.

## Executive conclusion

The live site does not have a broad crawl or indexability failure. Every one of the 165 URLs currently declared in `sitemap.xml` returned `200`, shipped meaningful server-rendered HTML, allowed indexing, had one self-referencing canonical, one H1, unique title and description text, and valid JSON-LD. The sitemap contains no redirecting URLs, no slashless HTML URLs, no duplicates, and no wrong-host or non-HTTPS URLs. Current internal HTML links also avoid redirecting slashless variants.

The 41 non-indexed URLs in the supplied Search Console screenshots are three different cases, not one site-wide failure:

1. **38 “Page with redirect” URLs are old slashless URL forms.** July 28 changed the site-wide publisher contract to trailing-slash canonicals. These source URLs should remain excluded; Google should index their trailing-slash targets.
2. **`feed.xml` is intentionally non-indexable.** It is an RSS file, is absent from the sitemap, and now returns `X-Robots-Tag: noindex`. It is not a missing rankable page.
3. **The product-evidence article is a real canonical HTML page that Google crawled but had not selected for indexing.** No current technical blocker was observed. The remaining explanation is an unresolved selection/lag/demand/distinctiveness question, not a robots, rendering, canonical, status-code, or structured-data error.

The one “Redirect error” URL is not currently broken. Its slashless source resolves in one browser navigation to the `200` trailing-slash article. The historical Google crawl failure remains unexplained without URL Inspection detail or host/CDN logs.

The July 28 release is still the important change point. It did two things that can affect search visibility:

- it completed a site-wide URL/canonical migration, requiring Google to consolidate old slashless URLs into trailing-slash targets; and
- it removed the phrase **“AI agent”** from all 46 agent-detail page titles, leaving generic titles such as `Review Agent | StackOS`, `Delivery Agent | StackOS`, and `Planning Agent | StackOS`.

The first change directly explains the Search Console redirect population. The second is the clearest exact-date on-page regression and could reduce relevance or CTR for agent-role queries. Neither can be proven as the cause of the entire reported ranking decline because no Search Console Performance page/query export was supplied. The Page Indexing screenshots do not show a wholesale deindexation collapse.

The later Crawl Stats screenshot does **not** show Google being locked out. It shows 5.53K total requests, 28.7 MB downloaded, a 146 ms average response, green host health, 92% `200` responses, 8% permanent redirects, and less than 1% in each error class. The Jul 29 crawl bump followed the canonical release and validation request; the subsequent low request rate is therefore best classified as reduced crawl demand after a concentrated processing pass, not a server-capacity shutdown. This interpretation is high confidence for host health and medium confidence for Google's scheduling motive.

The total crawl graph also does not equal HTML page crawls. Only 12% of requests were HTML; 45% were JSON and 26% JavaScript. A live rendered article loaded route-specific Nuxt `_payload.json` files, build metadata JSON, and prefetched payloads for linked hubs. Those page resources explain much of the request volume and why requests can fall sharply once Google has rendered/cached the release. At this site size, this is not evidence of a crawl-budget emergency.

## What happened on July 28

Repository history shows this sequence:

- `2c9bef4` at 13:02 PDT: the main 2.1.18 release changed shared SEO ownership, canonicals, sitemap behavior, internal URL form, RSS URLs, redirect policy, titles, page templates, catalog content, and added five articles.
- `984a3d4` at 16:07 PDT: added a prerendered branded static 404 page.
- `48ce4a9` at 16:27 PDT: removed hydration from the static 404 route and locked down its metadata.

Before the release, the publisher-generated sitemap contained 162 slashless URLs and page canonicals followed the route path. The release reconstructed a 165-URL canonical set with trailing slashes, added the homepage and five articles, and removed three consolidated plugin hubs from the sitemap. It also added an explicit `DirectorySlash` contract and documented slashless paths as historical sources.

Search Console validation was started on July 28, the same day as the corrective release. Starting validation did not cause a ranking change. Google states that it updates issue counts when it recrawls known URLs even when validation is not requested, and redirect source URLs are intentionally not indexed.

## Search Console issue reconciliation

| Search Console reason | URLs | Audit conclusion | Required action |
| --- | ---: | --- | --- |
| Page with redirect | 38 | Expected legacy slashless source URLs. Current sampled sources redirect once; canonical targets return `200`. | Keep redirects and trailing-slash canonicals stable. Inspect/index targets, not sources. |
| Crawled – currently not indexed: `feed.xml` | 1 | Correct outcome for a public RSS feed that now returns `noindex`. | No indexing request. Keep outside sitemap. |
| Crawled – currently not indexed: product-evidence article | 1 | Canonical HTML page; no observed technical blocker. Google selection remains unresolved. | Inspect and evaluate this URL alone; improve only for a distinct reader/search job. |
| Redirect error: refine-workflow article source | 1 | Historical failure not reproducible now. | Monitor exact source/target; use URL Inspection or logs before changing working redirects. |

## Current live technical audit

### Crawl and indexability

- Current sitemap URLs: **165**.
- Sitemap URLs returning `200`: **165**.
- Missing titles/descriptions/canonicals/robots directives: **0**.
- Canonical mismatches: **0**.
- `noindex` pages inside the sitemap: **0**.
- H1 count other than one: **0**.
- Invalid JSON-LD pages: **0**.
- Exact duplicate title groups: **0**.
- Exact duplicate description groups: **0**.
- Exact duplicate main-text groups: **0**.
- Feed or 404 URLs in the sitemap: **0**.
- Non-HTTPS, wrong-host, duplicate, or slashless HTML sitemap URLs: **0**.

### Robots, feed, and 404s

- `robots.txt` returns `200`, permits crawling, and declares the canonical sitemap.
- `feed.xml` returns `200 application/xml`, parses successfully, contains 15 items, is absent from the sitemap, and returns `X-Robots-Tag: noindex`.
- Unknown URLs return a real HTTP `404`, branded content, `noindex, nofollow`, and no canonical.
- The static `/404.html` file returns `200` as an asset but remains `noindex, nofollow` and absent from the sitemap.

### Internal linking

- 164 of 165 sitemap URLs are reachable from the homepage graph.
- Maximum observed click depth: 4.
- Depth distribution: 1 URL at depth 0, 4 at depth 1, 59 at depth 2, 97 at depth 3, and 3 at depth 4.
- Internal HTML links to slashless redirecting variants: **0**.
- The sole URL not reachable from the homepage graph is `https://stackos.flowmonkey.io/library/integrations/plugins/linear/`.

### Google crawl activity after July 29

- Total crawl requests in the screenshot window: **5.53K**.
- Total downloaded: **28.7 MB**.
- Average response time: **146 ms**.
- Host status: **no problems in the last 90 days**.
- Responses: **92% `200`**, **8% permanent redirects**, and less than 1% each for `304`, `404`, and other `4xx` responses.
- File types: **45% JSON**, **26% JavaScript**, **12% HTML**, **8% other**, and **7% CSS**.
- Approximate request composition from rounded percentages: about 2.49K JSON requests, 1.44K JavaScript requests, 664 HTML requests, and 442 permanent-redirect responses.

Google's Crawl Stats documentation says the total includes page resources and counts each redirect hop as a separate request. It also says sites below roughly 1,000 pages generally should not need to worry about crawl-budget detail. The report's green host status and near-absence of bad responses rule out the common conditions that force Google to slow or stop for availability reasons.

The live Nuxt page exposed and loaded URLs such as:

- `/_payload.json?...`
- `/library/articles/how-to-refine-ai-agent-workflow/_payload.json?...`
- `/library/_payload.json?...`
- `/library/workflows/_payload.json?...`
- `/_nuxt/builds/meta/<build-id>.json`

These are rendering/navigation resources, not additional canonical HTML pages. Do not block JavaScript or JSON resources in `robots.txt` as a reaction to this graph. If resource prefetch is ever optimized, do it as a measured Nuxt performance/crawl-efficiency change, not as the primary indexing fix.

### Rendering and structured data

The product-evidence article returned approximately 38.9 KB of raw HTML and roughly 9,083 characters inside the server-rendered article element. Its source includes a title, description, indexable robots directive, one H1, self-canonical, Article, WebPage, Organization, BreadcrumbList, and ImageObject schema. Its content is not dependent on client-side JavaScript becoming available before Google can see it.

The homepage, library hubs, guides, articles, agents, workflows, orchestrators, provider integrations, and plugin hubs all returned structured server-rendered content in the full sitemap crawl.

### Bounded performance observation

One warm desktop Chromium observation on the homepage produced a 12 ms TTFB, 231 ms DOMContentLoaded, 270 ms load, approximately 308 ms observed LCP, and 0.0022 CLS. This is useful negative evidence against an obvious current desktop failure, but it is **not** mobile Lighthouse, CrUX, field Core Web Vitals, or a cold-cache distribution. No ranking claim is based on these values.

## Canonical findings

### 1. Site-wide canonical migration — high confidence

The July 28 release replaced the slashless publisher convention with trailing-slash canonical URLs across the site. This directly explains the 38 redirect exclusions. The current implementation is coherent, so reverting it would restart consolidation and create more instability.

Action: keep the current URL contract stable and verify target URLs in Search Console. A “Page with redirect” source is not supposed to become indexed.

The Crawl Stats screenshot reinforces this finding: 8% of all Google requests were permanent redirects, with no meaningful server-error population. This is consistent with Google processing historical URL sources after the migration.

### 2. Historical redirect error — medium confidence

Google recorded one redirect error after crawling the slashless refine-workflow article on July 31. The same source now resolves through one redirect to a `200` target. No current loop, excessive chain, bad target, or target error was reproduced.

Action: do not edit working redirect logic without recurrence evidence. Inspect the source and target separately and use host/CDN logs if available.

### 3. Product-evidence article selection — medium confidence

The article is technically eligible and discoverable. The page was new, was crawled July 30, and had not been indexed by the screenshot date. Public evidence cannot determine whether this is normal lag, site maturity, weak demand, insufficient distinctiveness, or another Google selection decision.

Action: define the exact search job, inspect the URL alone, strengthen only genuinely relevant cluster links/evidence, and observe for 28 days after one meaningful change. Do not keep bulk-validating it alongside `feed.xml`.

### 4. Agent-title specificity regression — high confidence

The July 28 template change removed `AI agent` from 46 agent-detail titles. Four live titles are under 25 characters and generic enough to conceal the page type. This is the strongest exact-date on-page regression.

Action: restore concise page intent on priority pages, for example `{Specific role} AI agent | StackOS` where accurate. Avoid keyword stuffing and keep the title aligned with the H1 and actual role contract.

### 5. Catalog distinctiveness risk — medium confidence

The site has no exact duplicate pages, but 29 pages fell below a bounded sparse-content diagnostic in the first crawl. Integration pairs reached a five-word-shingle similarity of 0.513, and several orchestrator/plugin pages expose little beyond shared template language and catalog facts. This does not prove a penalty. It does mean not every URL has a strong independent reason to be selected.

Action: assign one distinct user/search job to each indexable URL. Consolidate redundant single-provider hubs; enrich retained pages with unique capabilities, limitations, evidence, comparisons, and next decisions. Do not add filler to satisfy a word count.

### 6. Linear plugin orphan — high confidence

`/library/integrations/plugins/linear/` is in the sitemap and indexable but is not reachable from the homepage graph. It substantially overlaps the richer `/library/integrations/linear/` page.

Action: either permanently consolidate the plugin hub into the provider page and remove it from the sitemap, or give it a distinct plugin-level job and intentional navigation.

### 7. Sitemap freshness coverage — high confidence, limited impact

Only 16 of 165 sitemap URLs have a `lastmod`. Removing inaccurate build-time dates was correct, but materially changed catalog pages now provide no source-owned freshness hint.

Action: emit `lastmod` only from verifiable significant content/update dates. Google says it uses `lastmod` when the value is consistently accurate.

### 8. GSC/current URL-set gap — high confidence

The screenshot shows 259 Google-known URLs: 218 indexed and 41 not indexed. The current canonical sitemap has 165 URLs. The 94-URL difference can contain historical redirects, feeds/files, and old canonical forms, but the screenshot cannot show whether all 218 indexed URLs are intended current pages.

Action: when an export is available, classify every indexed/excluded URL as current canonical, intended redirect, intentional noindex/file, 404, or unexpected legacy/duplicate.

### 9. Reported ranking decline is unmeasured — high confidence

Page Indexing coverage is not a ranking report. The supplied graph does not show a dramatic indexed-page collapse. It does not contain clicks, impressions, CTR, position, query, page, device, country, or search-appearance rows.

Action: if data is later exported, compare July 14–27 with July 29–August 11 using matched page/query rows. Test the two strongest exact-date hypotheses: canonical reprocessing and the agent-title change.

The post-Jul 29 crawl decline does not fill this gap. Google crawl demand, indexing, ranking, impressions, and clicks are different systems and metrics. Low total requests after a completed recrawl cannot identify which queries or pages lost visibility.

### 10. Field/mobile performance gap — high confidence

No field or mobile evidence was available. Current warm desktop behavior was fast, so performance is not a supported root-cause claim.

Action: collect CrUX/GSC Core Web Vitals or repeatable mobile cold-cache medians before assigning performance engineering work.

## Recovery plan

1. **Measure the loss before broad changes.** Export matched pre/post GSC Performance page/query data and the indexed/excluded URL samples when available. This is the only way to identify which page families and queries actually fell.
2. **Do not revert the trailing-slash migration.** Keep one permanent redirect, self-canonical targets, canonical sitemap URLs, and canonical internal links.
3. **Validate targets, not sources.** Check five trailing-slash targets across articles, integrations, workflows, and agents. The old source should stay excluded as a redirect.
4. **Repair agent titles in a bounded pilot.** Start with strategically important or previously visible pages; monitor matched queries and title-link rendering.
5. **Consolidate the Linear plugin hub and pilot catalog pruning/enrichment.** Use an unchanged template subset as a control.
6. **Handle the product-evidence article independently.** One meaningful editorial/internal-link change, one inspection, then a 28-day observation window.
7. **Add source-owned `lastmod` dates.** Never use blanket build timestamps.
8. **Collect mobile/field performance only after the higher-confidence work.**

If further crawl confirmation is needed, use the Crawl Stats drill-downs for **HTML**, **crawl purpose** (Discovery versus Refresh), and **Googlebot type** (Smartphone versus Page resource load). Those views would separate canonical page recrawls from Nuxt resource fetching without requiring a site change.

## What not to do

- Do not request indexing for `feed.xml`.
- Do not expect intentional redirect source URLs to become indexed.
- Do not revert all URLs to slashless form.
- Do not repeatedly restart bulk validation without changing the failing canonical HTML page.
- Do not add filler copy to every catalog page to hit a word-count threshold.
- Do not make site-wide content or technical changes before identifying the lost page/query segment.
- Do not treat a validation badge as a ranking recovery metric.
- Do not block Nuxt JSON/JavaScript resources merely to make the Crawl Stats graph smaller.

## Evidence quality and limitations

Live technical coverage is high and bounded-complete against the current sitemap. Change-forensics confidence is high because the exact July 28 commits were inspected. Index evidence is moderate because it is limited to operator screenshots. Ranking, traffic, conversion, field performance, authority, and historical hosting behavior are not measured.

Unavailable evidence:

- Search Console Performance and URL Inspection results;
- Search Console indexed-page and exclusion exports;
- GA4 organic landing-page/session/conversion data;
- Hostinger/LiteSpeed/CDN logs;
- field/mobile Core Web Vitals;
- backlink and actual lost-query SERP evidence.

## Google documentation used for interpretation

- [Page indexing report](https://support.google.com/webmasters/answer/7440203?hl=en)
- [Redirects and Google Search](https://developers.google.com/search/docs/crawling-indexing/301-redirects)
- [Canonical URL guidance](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls)
- [Build and submit a sitemap](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)
- [Influencing title links](https://developers.google.com/search/docs/appearance/title-link)
- [People-first content guidance](https://developers.google.com/search/docs/fundamentals/creating-helpful-content)
- [Crawl Stats report](https://support.google.com/webmasters/answer/9679690?hl=en)
- [Troubleshoot crawling errors](https://developers.google.com/search/docs/crawling-indexing/troubleshoot-crawling-errors)

This audit is analysis-only. No website, Search Console, analytics, tag, indexing, or provider state was changed.
