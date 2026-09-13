# StackOS library redirect and indexing investigation

Investigation date: September 5, 2026, America/Los_Angeles (live requests September 6 UTC).
Scope: the current Search Console overview and redirect examples, the subsequent Reve source and canonical URL Inspection screenshots, all ten visible redirect examples, the complete public sitemap, repository redirect/canonical history, and current generated output.

## Conclusion

The ten visible screenshot URLs all redirect once from a slashless path to the same path with a trailing slash. Every destination returns HTTP 200, declares itself canonical, and permits indexing. The redirect comes from Hostinger/LiteSpeed directory URL normalization, enabled explicitly by `DirectorySlash On` in `website/public/.htaccess:4`. It is not a client-side redirect or a redirect to a different content page.

There was a historical website defect: the publisher advertised slashless URLs in canonicals, sitemaps, and internal links even though the static host served directory URLs. Commit `2c9bef4` on July 28 aligned those signals to trailing slashes. The screenshot includes July 17–22 crawls, so the redirect behavior predates that explicit configuration change. Repository history cannot establish the exact first day the hosting default took effect.

The current technical correction is present in production. All 164 live sitemap URLs return direct 200 responses, have one self-referencing canonical, allow indexing, and are reachable from homepage HTML links. None of the sampled redirects loops, changes content destination, or introduces multiple hops. Reversing the slash convention would create another URL migration without a demonstrated current defect. The operator subsequently inspected the Reve canonical destination and confirmed **URL is on Google / Page is indexed**: this specific valuable content page is already indexed, while its redirect source is correctly excluded.

This does not establish that all valuable content is indexed. Later screenshots identify seven of the 33 discovered-but-not-indexed canonical URLs; all seven pass the same-session public technical checks, but Google's crawl scheduling cause remains unresolved. The two crawled-but-not-indexed and one redirect-error URL identities/details were not supplied. A public successful fetch is not proof of Google's selected canonical, a verified Googlebot fetch, or inclusion in Google's index. The operator screenshots independently confirm successful Google fetching, canonical selection, and actual index inclusion for Reve, as detailed below.

## Fresh production evidence

Requests used HTTP GET, with at most four concurrent requests. The full sitemap crawl used an audit user-agent; the screenshot URLs were additionally tested with a Googlebot Smartphone user-agent. A changed user-agent does not reproduce Google's network identity or establish whether host protection treats verified Google crawlers differently.

| Check | Result |
| --- | --- |
| Current live sitemap URLs | 164 |
| Sitemap URLs returning direct HTTP 200 | 164/164 |
| Sitemap URLs declaring exactly one matching canonical | 164/164 |
| Sitemap content URLs blocked by observed meta/header `noindex` | 0 |
| Sitemap URLs missing H1 in raw HTML | 0 |
| Sitemap URLs unreachable from homepage HTML link graph | 0 |
| Internal links advertising slashless variants of sitemap URLs | 0 |
| Visible screenshot sources reaching their target in one 301 | 10/10 |
| Same result with Googlebot Smartphone user-agent | 10/10 |
| Sitemap entries with `lastmod` | 164/164 |
| robots.txt | HTTP 200; empty Disallow for `*`; correct sitemap URL |

All source paths below are on `https://stackos.flowmonkey.io`. Each destination adds `/`, returns 200, and declares that destination canonical.

| Screenshot source path | HTTP chain |
| --- | --- |
| `/library/integrations/reve` | 301 → 200 |
| `/library/integrations/smtp` | 301 → 200 |
| `/library/workflows` | 301 → 200 |
| `/library/articles` | 301 → 200 |
| `/library` | 301 → 200 |
| `/library/articles/use-codex-claude-gemini-with-existing-tools` | 301 → 200 |
| `/library/integrations/xai-imagine` | 301 → 200 |
| `/library/integrations/x-api` | 301 → 200 |
| `/library/workflows/media-buying-budget-reallocation-review` | 301 → 200 |
| `/library/integrations/reddit` | 301 → 200 |

The prior August 8 audit identified `/library/articles/how-to-refine-ai-agent-workflow` as a historical redirect-error example. It also currently returns one 301 to a self-canonical 200 target, with both user-agents. The new screenshot does not establish that this is still the single current redirect-error URL.

The Reve target returned identical decoded HTML for ordinary and compressed curl requests (SHA-256 `d29a9bbe81be4fc2ebcf6ba8a6058a687319405f6b8c336f30712504b11601d5`). This sample found no stale compressed variant; it is not a complete CDN/cache audit. Server Last-Modified headers alone do not identify the deployed source revision.

Evidence files:

- [Compact live HTTP evidence](./2026-09-05-indexing-live-evidence.json), including per-page status, chain, canonical, robots, title, HTML hash, and selected headers.
- [Canonical URL inventory](./2026-09-05-indexing-canonical-inventory.csv), suitable for joining with a Search Console export. Reve is marked indexed and the seven subsequently supplied discovered examples are marked not indexed, based on the operator's screenshots; all other Google index-status fields are explicitly unverified.

## Code ownership and change history

- `website/public/.htaccess:3–5`: the directory-slash contract.
- `website/public/.htaccess:8–11`: only four explicit plugin consolidation redirects; none matches the ten screenshot examples.
- `website/shared/utils/siteSeo.ts:17`: current HTML path normalization.
- `website/app/composables/useSiteSeo.ts:16`: canonical and Open Graph URL generation through that normalization.
- `website/nuxt.config.ts:27–31,51–56`: trailing-slash link and SEO settings.
- `website/server/api/__sitemap__/urls.ts:38`: canonical sitemap URL construction.
- `website/DEPLOYMENT.md:154`: static directory URLs and the expected one-hop redirect.

Before July 28 commit `2c9bef4`, `useLibrarySeo.ts` used `new URL(route.path, siteUrl)` without slash normalization and sitemap/prerender/internal-link sources were slashless. That was a conflict between the site's advertised URL and the host's served URL. July 28 corrected the advertised form; it was not proof that redirects started that day. August 14 commit `de7d9b0` added the Linear consolidation and sitemap freshness work, not the general slash redirect.

Independent repository review found that the existing generated export also has 164 canonical HTML routes, consistent sitemap/internal links, indexable robots directives, and no missing sitemap target files. The existing generated SEO gate passed. These static checks do not replace the production HTTP checks above.

Independent live/local comparison also found identical sitemap URL sets, page titles, canonical tags, and robots directives; robots.txt is byte-identical. One article, `how-ai-agents-use-accounts-safely`, has an older MCP specification citation and lastmod in production. That narrow content difference does not explain redirects or establish that the canonical correction is undeployed.

## Additional first-party Reve inspection

The operator subsequently supplied a Search Console URL Inspection screenshot for the Reve example. It reports:

- Page is not indexed: **Page with redirect**.
- Last crawl: **August 27, 2026, 11:17:34 AM**, crawled as Googlebot smartphone.
- Crawl allowed: **Yes**; page fetch: **Successful**; indexing allowed: **Yes**.
- User-declared canonical: **`https://stackos.flowmonkey.io/library/integrations/reve/`**.
- Google-selected canonical: **Same as user-declared canonical**.
- Sitemaps: **No referring sitemaps detected**; referring page: **`https://stackos.flowmonkey.io/sitemap.xml`**.

This is first-party evidence that the recorded Google fetch succeeded and Google selected the intended slashful URL. It contradicts a claim that Google could not crawl this example. The inspected address itself is outside the crop; the surrounding conversation identifies it as the redirect example.

Google explicitly states that inspection of a redirecting URL reports the source's index record, and the INSPECT link in the Indexing section opens the canonical's record. [Google URL Inspection documentation](https://support.google.com/webmasters/answer/9012289?hl=en).

The sitemap discovery fields do not prove that the current sitemap is missing or malformed. The current live sitemap contains the trailing-slash Reve URL and excludes its slashless source; it is declared in robots.txt. Google's source discovery record may reflect older discovery or incomplete sitemap association. The screenshot alone cannot identify which explanation applies.

The operator then followed INSPECT and supplied the canonical destination's report:

- **URL is on Google** and **Page is indexed**.
- Sitemap: **`https://stackos.flowmonkey.io/sitemap.xml`**.
- Last crawl: **August 27, 2026, 11:17:34 AM**; Googlebot smartphone.
- Crawl allowed: **Yes**; page fetch: **Successful**; indexing allowed: **Yes**.
- User-declared canonical: **`https://stackos.flowmonkey.io/library/integrations/reve/`**.
- Google-selected canonical: **Inspected URL**.

Reve is therefore a confirmed expected source exclusion with an indexed destination, not a missing content page. Its source and destination discovery fields also explain why the earlier source report's sitemap message must not be treated as a site-level sitemap failure. No indexing request or redirect change is needed for this example.

## What the Search Console rows mean here

The 43 count is for redirecting URL forms, not evidence that 43 destination content pages are unavailable. The screenshot exposes ten examples; their exact source/target behavior is verified above, but the other 33 redirect examples were not individually supplied. Google may retain and revisit old URLs after the site stops linking to them.

Google documents that a redirect source is normally excluded and the target may or may not be indexed. A failed validation badge means the tested condition remains: these old source URLs still redirect. It does not establish that their content targets are broken. Successful HTTP fetching and actual indexing must be checked separately. [Google Page indexing report](https://support.google.com/webmasters/answer/7440203?hl=en).

The other groups require their own URL-level evidence:

- **33 discovered, currently not indexed:** Google knows the URLs but has not crawled them in that reported state. A later screenshot identifies seven current canonical examples, assessed in the follow-up below; the other 26 and Google's scheduling cause remain unknown.
- **Two crawled, currently not indexed:** Google fetched those URLs but has not indexed them in that reported state. Their present identities are unknown; do not assume they are the same article/feed pair from the August audit.
- **One redirect error:** an actual failed chain may have occurred. The historical candidate works now, but the exact current URL, crawl date, and inspection result are needed to investigate the reported event.

## Concrete resolution

1. Keep the current URL contract: slashless historical URL → one permanent redirect → trailing-slash canonical 200 page. Keep the canonical tags, sitemap, and internal links aligned. This is already true in the tested production state. Google recommends consistent canonical signals. [Canonical URL guidance](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls).
2. Use Search Console's current `https://stackos.flowmonkey.io/sitemap.xml` filter to evaluate intended content URLs. Inspect the trailing-slash destination of each valuable missing page directly; inspecting the slashless source reports on a different URL. For example, inspect `https://stackos.flowmonkey.io/library/articles/use-codex-claude-gemini-with-existing-tools/`.
3. Join the current canonical inventory to the 33 discovered, two crawled, one redirect-error, and indexed URL exports. Record each important target's last crawl, fetch status, user-declared canonical, Google-selected canonical, and index verdict. This is the missing evidence needed to establish the actual affected content set; another waiting period alone will not answer it.
4. If a canonical target passes Google's live test but is unindexed, request indexing for that priority target and diagnose discovery/selection using its exact report. Do not bulk request indexing for old redirect sources. Index requests are a separate operator action; none was performed in this investigation.
5. If Google reproduces a fetch or redirect error, use the exact URL and crawl timestamp to examine Hostinger access/error logs, redirect rules, and cache variants. Repair the observed failure and rerun the same source/target test before validation. No current failing host rule was reproduced, so there is no justified redirect-code patch from this evidence.

For prevention, extend the existing release verification with a read-only production check of direct-200 sitemap URLs, self-canonicals, robots directives, and one-hop historical redirects. Current Playwright runs Nuxt development rather than LiteSpeed, and the generated SEO gate only inspects the `.htaccess` directive as text. That gap explains why a local build cannot certify hosting behavior; it is not itself a current indexing blocker.

No website code, production configuration, Search Console state, or public content was changed. This investigation created only local evidence and tracker records. It establishes the redirect origin and present technical behavior; it does not claim to resolve the remaining Google indexing decisions without their URL-level data.

## Follow-up: seven discovered-but-not-indexed canonical examples

The operator supplied a later screenshot showing 33 affected pages and seven visible example URLs, each with Last crawled = N/A. A separate inspection screenshot shows discovery through the current sitemap and N/A for crawl, fetching, and canonical fields. Its inspected URL is outside the crop, so that individual record is not assigned to one example without confirmation. Google defines this status as a discovered URL that has not yet been crawled; N/A canonical fields are not evidence of missing canonical markup. [Page indexing status definitions](https://support.google.com/webmasters/answer/7440203?hl=en).

The same-session public crawl at 04:12 UTC already covered all seven exact URLs. Each returns direct 200, includes rendered content, declares its own trailing-slash canonical, permits indexing, and appears in the sitemap.

| Canonical path | Homepage link depth | Other HTML pages linking to it |
| --- | ---: | ---: |
| `/getting-started/` | 1 | 163 |
| `/library/agents/branding-profile-architect/` | 2 | 4 |
| `/library/agents/gtm-workflow-account-research/` | 3 | 3 |
| `/library/agents/gtm-workflow-crm-export-handoff/` | 3 | 2 |
| `/library/agents/gtm-workflow-customer-follow-up/` | 3 | 2 |
| `/library/agents/gtm-workflow-marketing-program-lifecycle/` | 3 | 2 |
| `/library/agents/gtm-workflow-outbound-sequence-preparation/` | 3 | 2 |

These link counts exclude self-links and include shared navigation; they measure discoverability, not link authority. The group is a real canonical indexing gap in the supplied report, unlike the expected Reve redirect-source exclusion. It does not demonstrate that the server refused Google or that redirects caused the delay.

The getting-started guide is the first priority: it has a distinct post-installation purpose, copyable prompts, recovery guidance, and success criteria. It is prominently linked, so it has no demonstrated orphan/depth problem. Adding more generic links or rewriting it merely to increase word count is not supported by this evidence.

A bounded review of the GTM account-research and CRM-export agent pages found useful role contracts, with mission, boundaries, and handoff details. Their paired workflow pages cover the same practical jobs with more complete starting conditions, steps, tools, and recovery. This overlap is a candidate for an editorial search-intent review, not proof that Google rejected the agent pages or a justification for immediate noindex/consolidation changes.

Next actions:

1. Run Google's TEST LIVE URL on `https://stackos.flowmonkey.io/getting-started/` to verify the actual Google inspection fetch. If available to Google, request indexing once for this priority canonical URL. Do the same for a small number of valuable missing agent pages, starting with Brand Profile Architect if it is a priority. No Search Console action was executed by the agent.
2. If the live test fails, capture its exact fetch result and examine host logs at that timestamp. If it passes but the priority URL remains uncrawled after a reasonable review interval (about two weeks is an operational checkpoint, not a Google SLA), inspect current Crawl Stats host health and HTML responses before attributing the delay to capacity or content quality.
3. Review the remaining 26 discovered URLs before treating the entire group as one cause. Prioritize distinct guides/articles and valuable role pages, then consider evidence-based improvements to overlapping catalog content. Do not change slash conventions.

Google states that crawl requests can take days to weeks, do not guarantee indexing, and repeated requests for the same URL do not accelerate crawling. [Requesting a crawl](https://developers.google.com/search/docs/crawling-indexing/ask-google-to-recrawl). The current finding is that no technical code repair has been demonstrated for these seven examples; targeted first-party fetch/indexing follow-up is warranted.

### Subsequent Brand Profile Architect live test

The operator supplied an actual Google live test at September 5, 2026, 9:22:41 PM for `https://stackos.flowmonkey.io/library/agents/branding-profile-architect/`. It reports **URL is available to Google / Page can be indexed**, Google Inspection Tool smartphone, crawling allowed, successful fetching, indexing allowed, the matching user-declared canonical, and one valid Breadcrumbs item. Google-selected canonical is **Only determined after indexing**. This proves current Google inspection access for this page; it does not mean the page has become indexed or guarantee later selection. It is a different URL from the requested getting-started test, so the latter remains outstanding.

The concrete next step is one Request indexing for the Brand Profile Architect canonical, then the same live-test/request process for the priority getting-started guide. The generic yellow notice that indexing depends on conditions is not a reported fetch, robots, or canonical failure. No technical repair is demonstrated for the inspected page. No indexing-request completion was shown and none was submitted by the agent.
