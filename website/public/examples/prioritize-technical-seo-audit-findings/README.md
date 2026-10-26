# Turn twelve audit findings into five tasks

**This is an invented audit, not an audit of StackOS or a customer site.** URLs use the reserved `example.com` domain. Responses, HTML, code fragments, timings, business priorities and diagnostic records were authored for this exercise. No requests or browser tests were run against those URLs. The evidence snippets are teaching materials, not operational receipts.

The fictional site sells workflow software. Its operator has identified pricing and starting a trial as the immediate visitor journeys, public integration and guide pages as content that should be eligible for search, and the contact form as a useful fallback. No traffic, conversion or revenue data is supplied. Those stated priorities explain the queue order; a different business could choose a different order.

## Files

- [Twelve findings](audit-findings.csv): the compact export with evidence and task mappings.
- [Evidence snippets](evidence.md): invented responses, HTML and diagnostic notes for every row.
- [Five-item queue](queue.md): affected URLs, repair scope, consequences, uncertainties and acceptance checks.
- [Scope and mappings](scope.json): machine-readable relationships and source references.

The four template families are **product**, **integration**, **guide** and **utility**. Each has three findings. A finding's family belongs to the inspected page, so the missing `/start/` target in F02 is evidence for the product page's broken link, not a fifth template family. Repeated URLs and repeated symptoms remain visible in the export; tasks group only symptoms with a shared cause demonstrated in the supplied fixture.

The sample is bounded to the listed pages on a single fictional release, `demo-release-a`, dated October 6, 2026. The shared template sources shown in the evidence are declared owners within this exercise. They are not paths in the StackOS codebase. All pages in the sample are public. The intentionally excluded search-results page contains no private information; `noindex` is not an access-control mechanism.

## What the exercise establishes

Three integration warnings become one template repair. Two guide canonical warnings become another. A short-description warning is rejected as a defect based solely on the tool's length rule. The search-results exclusion is intentional. Two performance warnings need more context; the separately reproduced form movement has enough evidence for a bounded repair.

The five tasks are proposals. Their checks describe what a developer should execute on the real authorized environment. No repair, indexing result or search improvement is represented as completed here. Local fixture validation checks counts, references and internal consistency only.

## Primary references checked October 6, 2026

- [Google's robots meta rules](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag): crawlers must be able to fetch a page to see its indexing instructions.
- [Google's canonical guidance](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls): a canonical declaration is a signal; it does not establish Google's selected URL.
- [Google's description guidance](https://developers.google.com/search/docs/appearance/snippet): useful descriptions summarize the page; the tool's minimum character rule is not Google's specification.
- [web.dev's layout-shift guidance](https://web.dev/articles/optimize-cls): distinguish lab and field evidence, inspect shift attribution, and account for space used by late content.

These sources explain technical interpretation. They do not verify the fictional site or its invented observations.
