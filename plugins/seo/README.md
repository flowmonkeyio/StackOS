# SEO Plugin

`plugins/seo/plugin.yaml` is the StackOS catalog boundary for SEO work.

This package owns the SEO domain shape:

- SEO capabilities, providers, actions, resources, and nav live in the plugin
  manifest.
- SEO workflow templates live under `plugins/seo/workflows`.
- Action entries bind to daemon-side connectors through static
  `config.connector` and `config.operation` metadata; the manifest is
  declarative metadata only.
- Secrets never belong here. Provider credentials are resolved by daemon-side
  auth providers/connectors.

## Website analysis method

`seo.website-analysis` is the agency-style audit path. It is deliberately an
analysis workflow, not an automated score or a hidden crawler. The workflow
starts with the public site and uses connected sources when they are available:

1. Scope the canonical host, business goal, markets, important and excluded
   sections, date window, access boundary, expected scale, and representative
   template sample.
2. Map public evidence: homepage, robots and sitemap signals, navigation,
   representative templates, internal links, redirects/canonicals, metadata,
   headings, structured data, rendered behavior, media, and content patterns.
3. Select the smallest useful set of connected first-party and research evidence. Search Console provides
   property, query/page, sitemap, and sampled indexed-version evidence; GA4
   provides historical behavior/conversion reports; GTM provides configuration
   inventory; Ahrefs, DataForSEO, and Serper can add backlink, competitor,
   keyword, and live-result context. Optional Firecrawl map/scrape can broaden
   public discovery.
4. Reconcile sources into crawl/indexability, robots/sitemaps,
   canonicals/redirects, internal links, structured data, available performance
   evidence, on-page, content, international/local, measurement, and optional
   authority/competitive findings.
5. Independently review every draft claim, adjudicate accepted, revised, and
   rejected findings, and prioritize only the canonical findings by impact,
   confidence, effort, dependencies, owner handoff, sequence, and validation path.
6. Store one compact `website-seo-analysis` resource plus a durable final report
   and the inventory, finding-register, or evidence artifacts justified by the
   analysis completeness and claim-support needs.

The workflow follows the current complete-package authoring contract: one
operator-facing job and closeout, explicit specialist/main-agent preset
requirements, optional-provider readiness, an explicit artifact grant only on
the final storage step, representative run-plan grants, and a queryable durable
resource. The public map owns discovery coverage, the reconciled inventory is
the sole canonical URL ledger, and the compact resource uses the same source,
typed-evidence, review, summary, and roadmap contracts as the workflow outputs.

Missing optional connections do not block the public baseline. Paid sources are
used only when they materially improve the scoped audit and fit the available
budget. Every considered source is timestamped and recorded as used, unavailable,
skipped, or failed with coverage and limitations. Every evidence ref resolves
through one typed index, and temporary response-file paths are never presented
as durable proof. Every finding is classified as measured, observed, or inferred. Public page
inspection does not prove a complete crawl, orphan status, live indexability,
Core Web Vitals, rankings, or traffic. Search Console and GA4 are reconciled
rather than joined naively because their URL and metric semantics differ; URL
Inspection describes Google's indexed version, and GTM inventory does not prove
that tags fire correctly.

The initial workflow intentionally excludes the submit-only `utils.web.crawl`
action, GA4 realtime reporting, and PAA extraction. Public analysis is the
default; authenticated or private targets require explicit access and
external-sharing boundaries. The workflow does not publish fixes, submit forms,
change tags, request indexing, or mutate the website. Follow-up delivery belongs
in a separately authorized engineering, content-refresh, publishing, or other
domain workflow.

Method references:

- [Google Search Essentials](https://developers.google.com/search/docs/essentials)
- [Google SEO Starter Guide](https://developers.google.com/search/docs/fundamentals/seo-starter-guide)
- [Core Web Vitals](https://developers.google.com/search/docs/appearance/core-web-vitals)
- [Sitemap guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap)
- [Structured data introduction](https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data)
- [Search Console and Analytics comparison](https://developers.google.com/search/docs/monitor-debug/google-analytics-search-console)
- [URL Inspection API](https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect)
- [Search Analytics API](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)
