# StackOS canonical SEO finding register

Reviewed: 2026-08-08
Canonical findings: 10
Rejected false alarms: 2

Evidence classes:

- **Measured:** reported by first-party Search Console screenshot data.
- **Observed:** directly verified in the live site or repository history.
- **Inferred:** a bounded hypothesis supported by observations but requiring first-party validation.

## Findings

### SEO-01 — Site-wide canonical URL migration

- Category: canonicals and redirects
- Evidence: observed
- Confidence: high
- Scope: 162 pre-release publisher URLs; 165 current sitemap URLs; 38 GSC redirect examples
- Root cause: the July 28 release changed slashless sitemap/canonical/internal-link/RSS signals to trailing-slash targets.
- Impact: directly explains the GSC `Page with redirect` group and may require temporary canonical consolidation; does not prove the entire ranking decline.
- Action: keep the current contract stable; retain exactly one permanent redirect; inspect/index targets rather than sources.
- Validation: inspect five source/target pairs across templates and confirm Google's selected/indexed canonical is the target.

### SEO-02 — Historical redirect error is not reproducible

- Category: canonicals and redirects
- Evidence: observed
- Confidence: medium
- Scope: one slashless refine-workflow article source
- State: Google reported an error after a Jul 31 crawl; the source now navigates once to a 200 self-canonical target.
- Impact: unresolved one-crawl defect, not current proof of a site-wide redirect problem.
- Action: monitor and inspect logs/URL Inspection before changing working redirect code.
- Validation: two subsequent Google crawls with no error or a captured reproducible chain.

### SEO-03 — Product-evidence article index selection is unresolved

- Category: crawl and indexability
- Evidence: inferred
- Confidence: medium
- Scope: one canonical article
- State: 200, indexable, self-canonical, server-rendered, substantial article text, Article schema, three internal inlink sources; GSC says crawled but not indexed.
- Impact: no observed technical blocker; lag, maturity, demand, distinctiveness, and other selection signals remain possible.
- Action: inspect the URL alone, define its search job, make one meaningful improvement if warranted, and observe for 28 days.
- Validation: URL Inspection plus relevant page-query impressions after the observation window.

### SEO-04 — Agent-detail title specificity regressed on July 28

- Category: on-page
- Evidence: observed
- Confidence: high
- Scope: 46 agent-detail pages; four generic titles under the bounded 25-character diagnostic
- Root cause: title templates changed from `{role} AI agent — StackOS Library` to `{role} | StackOS`.
- Impact: the live title can omit the AI-agent page type and weaken relevance/click clarity. Ranking/CTR impact is unmeasured.
- Action: restore concise intent on priority pages, for example `{Specific role} AI agent | StackOS` where accurate.
- Validation: matched query impressions, CTR, title-link rendering, and position versus unchanged agent pages.

### SEO-05 — Some catalog URLs lack a distinct indexable job

- Category: content
- Evidence: inferred
- Confidence: medium
- Scope: sparse plugin/orchestrator/agent pages; strongly similar integration pairs
- State: 29 pages triggered a bounded sparse-content diagnostic; top integration pair similarity 0.513; no exact title, description, or main-text duplicates.
- Impact: selective indexing/usefulness risk, not evidence of a penalty.
- Action: consolidate redundant single-provider hubs and enrich retained pages with unique capabilities, constraints, evidence, comparisons, and next decisions.
- Validation: bounded pilot versus unchanged control pages using URL-level index state and relevant impressions.

### SEO-06 — Linear plugin hub is effectively orphaned

- Category: internal links
- Evidence: observed
- Confidence: high
- Scope: `/library/integrations/plugins/linear/`
- State: indexable and in sitemap but not reachable from the homepage graph; overlaps `/library/integrations/linear/`.
- Impact: weak discovery and unresolved ownership between plugin and provider pages.
- Action: permanently consolidate it or create a distinct, intentionally linked plugin-level purpose.
- Validation: homepage crawl proves either one-hop consolidation and sitemap removal or meaningful reachability/differentiation.

### SEO-07 — Sitemap lastmod coverage is incomplete

- Category: robots and sitemaps
- Evidence: observed
- Confidence: high
- Scope: 149 of 165 sitemap URLs lack `lastmod`
- State: missing mainly across catalog families and hubs.
- Impact: not an indexing block; materially changed pages lack a verifiable sitemap freshness hint.
- Action: emit `lastmod` only from source-owned significant-update timestamps, never blanket build time.
- Validation: source revision date equals emitted sitemap date; compare future recrawl dates.

### SEO-08 — Google's known URL set is unreconciled

- Category: measurement
- Evidence: measured
- Confidence: high
- Scope: 259 GSC-known URLs versus 165 current sitemap URLs
- State: 218 indexed and 41 not indexed in screenshot; URL-level indexed export unavailable.
- Impact: cannot verify whether Google's indexed set is the intended current canonical set or includes unexpected legacy/duplicate URLs.
- Action: classify exported URLs as current canonical, intended redirect, intentional noindex/file, 404, or unexpected legacy/duplicate.
- Validation: zero unexpected indexable legacy/duplicate classes after joining export to live response/canonical data.

### SEO-09 — The reported ranking decline is unmeasured

- Category: measurement
- Evidence: observed
- Confidence: high
- Scope: Jul 28 change point; missing clicks, impressions, CTR, position, query, page, device, country, search appearance, organic sessions, and conversions
- State: Page Indexing screenshots do not show a wholesale index-coverage collapse.
- Impact: cannot distinguish ranking loss from demand, CTR, canonical processing, page mix, device/country, or measurement change.
- Action: test canonical reprocessing and agent-title specificity with matched pre/post page-query data when available.
- Validation: Jul 14–27 versus Jul 29–Aug 11 matched page/query comparison, corroborated by GA4 organic landing pages if available.

### SEO-10 — Field/mobile performance is unmeasured

- Category: performance
- Evidence: observed
- Confidence: high
- Scope: all major templates
- State: one warm desktop homepage run was fast; no CrUX/mobile/cold-cache distribution exists.
- Impact: no evidence currently ties speed to the Jul 28 ranking report.
- Action: collect field/mobile evidence before opening performance work.
- Validation: GSC CWV/CrUX or three-run mobile medians for representative templates.

## Crawl Stats addendum

Late first-party evidence supports SEO-01 and SEO-09:

- 5.53K requests; 28.7 MB; 146 ms average response.
- Green host status with no problems in 90 days.
- 92% 200; 8% permanent redirects; negligible error classes.
- 45% JSON; 26% JavaScript; only 12% HTML.
- Live Nuxt rendering fetched route `_payload.json` and build-metadata JSON resources.

Conclusion: the post-Jul 29 drop in total requests is not evidence that the host blocked Google. The Jul 29 bump is consistent with a concentrated migration/validation processing pass, and much of the total graph is page-resource crawling. Google's exact scheduling motive is not measurable from the screenshot.

## Rejected false alarms

### `feed.xml` should be indexed

Rejected. It is an RSS file, deliberately outside the sitemap, and returns `X-Robots-Tag: noindex`. Its exclusion does not remove a rankable HTML page.

### Long titles/descriptions are a ranking defect

Rejected as a standalone issue. Google has no fixed title length limit and may create different title links/snippets. The actionable subset—generic agent titles—is captured in SEO-04.

## Priority order

1. Establish matched page/query loss and reconcile Google's indexed URL set.
2. Keep the canonical migration stable and validate canonical targets.
3. Restore descriptive intent to priority agent titles.
4. Consolidate/differentiate catalog pages, beginning with the Linear plugin hub.
5. Resolve the product-evidence article individually.
6. Add accurate source-owned `lastmod` values.
7. Collect field/mobile performance only after higher-confidence work.
