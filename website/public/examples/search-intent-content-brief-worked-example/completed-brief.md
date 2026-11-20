# Completed assignment: choosing a page's lastmod date

**Status:** researched demonstration brief; no article is commissioned for publication by this file. Research date: October 6, 2026. The factual references were opened; the four change records below are fictional. This brief demonstrates an editorial handoff inside the content-brief article and does not become a second sitemap tutorial.

## Reader and decision

**Question:** When should I update a page's sitemap lastmod date?

**Reader:** a small-site editor or developer who has changed a page and must choose the date to emit in its existing sitemap. Assume they can inspect a page diff and locate its sitemap entry. They do not need another explanation of every XML tag.

**Finish line:** the reader can choose **update**, **keep the known date**, or **omit an unsupported date**, explain the choice from a concrete change record, and name what to check in the generated output.

**Business relevance:** the task connects content updates to accurate website maintenance. Do not insert a product pitch or imply the example proves demand for a service.

## Answer plan

Open with the rule in CL1 and the page-date distinction in CL2. Keep the answer short enough that a reader can apply it before learning implementation details. Then use this sequence:

1. **What changed on the page?** Give the reader a way to identify the relevant diff. Explain why a label such as “minor edit” is insufficient for C4.
2. **Which date is supported by the record?** Use C1 and C2 to separate the recorded page event from the later rebuild.
3. **What if the history is unreliable?** Use C3 and CL3 to give a clear next step without guessing.
4. **How do I check the result?** Show the case table, then explain the narrow output check below.

These are editor-authored questions. They are not customer quotations or a copied result-page outline. Each section should answer its question promptly and use a case where it helps; do not stretch the packet into a predetermined word count.

## Four fictional change records

All URLs use `example.com`. Every date, price and change below is invented. “Recorded update” means the date the change became part of the public page in this fictional record. The rebuild date is **October 6, 2026** in every case.

| Case | Inputs the writer must preserve | Expected choice | Reason to explain |
| --- | --- | --- | --- |
| **C1** — `https://example.com/guides/export-audit-log/` | Last known substantive version: September 12. On October 3, the editor replaces obsolete export instructions with the current steps and a verification example. Nothing changes after October 3. | **Update:** `2026-10-03` | The documented task answer changed. Use CL1 and CL2. |
| **C2** — `https://example.com/guides/retry-a-workflow/` | Last known substantive version: September 15. On October 6, only the footer copyright year changes; the guide body, structured data and links remain identical. | **Keep:** `2026-09-15` | Apply the maintenance example in S1; the rebuild adds no later qualifying event. |
| **C3** — `https://example.com/` | The homepage aggregates recent content. Its reliable change history is unavailable; the only timestamp available is the October 6 build. | **Omit:** no `lastmod` field for this entry | Apply CL3. Record the missing history for the generator owner to investigate. |
| **C4** — `https://example.com/pricing/` | Last known substantive version: September 1. On October 5, an editor corrects the public monthly price from $49 to $59 to match the approved price. No other text changes. | **Update:** `2026-10-05` | Our interpretation under CL1: a small edit changes a material fact used in a buying decision. State that reasoning rather than present it as a Google example. |

The writer may create a compact illustration of these inputs, but must keep the cases fictional and retain all four decisions. In C3, a blank date, guessed date or build date would not satisfy the assignment. Case C4 is an interpretation to explain and review, not a universal policy that every corrected character warrants a date change.

## Sources, links and boundaries

Use [the source ledger](source-ledger.md) to cite CL1–CL4. Use [the observation sheet](result-observations.md) only to explain how research changed the assignment. A returned snippet is not evidence that the full page was read.

Link to the relevant primary reference when the reader needs syntax or the official rule.

**Internal-link decision for the commissioned lastmod article:** no internal destination is required. The supplied exercise and primary references answer the selected task; this brief does not assign an unvetted internal page.

**Navigation in the surrounding content-brief article:** the existing [fact-checking guide](https://stackos.flowmonkey.io/library/articles/how-to-fact-check-ai-generated-content/) is a follow-up for claim review. That link serves the editor reading about briefs, rather than the commissioned lastmod article. Its path is established project material; no fresh HTTP check of that destination was performed for this demonstration.

Exclude full sitemap creation, submission and resubmission, CMS/plugin recipes, sitemap-index dates, canonical selection, byline dates, news sitemap rules, and search-performance attribution. Do not claim that changing a date guarantees crawling or rankings. Do not turn this exercise into a benchmark of sitemap tools.

## Verification the writer can execute

Before drafting, reopen the three precise passages in the source ledger. If the rules changed, repair the assignment before carrying them into prose. Check that each technical sentence has a relevant primary source and that each case is identified as an application of the rule.

For the worked example, compare every output against its input record in the table. C1 must retain October 3 when the rebuild happens October 6; C2 must retain September 15; C3 must omit the element; C4 must carry October 5 with its reasoning stated.

If adapting the exercise to an authorized real site, inspect the history that feeds the date, generate the sitemap locally, and compare these decisions with the emitted entry. Rebuild an unchanged page again and check whether its value changes without a supported page event. Do not describe that check as executed unless it was. No site access, deployment or external submission is required to complete this writing brief.

**Review deliverable:** the draft, the completed case table and a short list of any unresolved claims. A reviewer should be able to recreate each choice without assuming the author has personal results or client experience.
