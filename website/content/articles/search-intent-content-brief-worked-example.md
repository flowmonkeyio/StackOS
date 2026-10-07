---
title: How do I turn search results into a content brief a writer can use?
description: Follow a researched question from search results to a complete writing brief, with a rejected outline, verified sources and four worked cases.
publishedAt: '2026-10-06'
updatedAt: '2026-10-07'
author: StackOS team
category: SEO
topics:
  - content briefs
  - search intent
  - content research
readingTime: 7 min read
featured: false
visual: none
searchIntent: Turn observed search results into an original, executable article brief with sources, examples, exclusions and a concrete reader outcome
relatedWorkflows:
  - seo-keyword-research
  - branding-content-production
relatedAgents:
  - branding-channel-strategist
  - branding-evidence-curator
  - branding-narrative-writer
relatedArticles:
  - how-to-build-ai-content-workflow-that-does-not-sound-generic
  - how-to-fact-check-ai-generated-content
---

Define the reader's decision, then give the writer an answer plan, reliable sources, a worked example and clear exclusions. [Google's helpful-content guidance](https://developers.google.com/search/docs/fundamentals/creating-helpful-content) says crediting sources does not replace original effort, so the brief should spell out what the article adds to the results you reviewed.

## What does the reader need to decide or do?

Describe the choice the reader should be able to make after reading. For this demonstration, the selected question is **“When should I update a page's sitemap lastmod date?”** The assignment is for an editor or developer who has changed a page and needs to choose the value in its existing sitemap.

The reader can inspect a page diff and find the sitemap entry. Those assumptions remove a lot of potential material: they do not need a sitemap generator comparison or an introduction to every XML tag. They need to decide whether to update the date, keep a known date or omit a date they cannot support.

Write that finish into the brief:

> Help a small-site editor choose a page's lastmod value from its change record. Use four supplied cases to explain the choice and show what to check in the generated entry.

The [completed brief](/examples/search-intent-content-brief-worked-example/completed-brief.md) carries the reader, answer plan, cases, sources, exclusions and checks. Here is how the research shaped those fields.

That gives the writer a way to judge what belongs. A paragraph on choosing a CMS would need to help with this decision to earn its place. If the intended reader could not inspect a diff, the assignment would need different prerequisites or a simpler way to establish what changed.

## What do the current results reveal, and which claims need separate verification?

Use results to inspect the answers available, then take technical claims to their primary sources. In an October 6, 2026 public search for `when should I update a page sitemap lastmod date`, four of five selected pages could be opened. This was a small English-language sample without location or device controls, not a controlled Google ranking capture or evidence of demand.

Each useful observation changed an instruction:

**The official rules were already available.** Google's sitemap documentation and the Sitemaps protocol supplied the rule and field definition. The brief therefore keeps the introduction short and asks the writer to spend the explanation on change records.

**“Small edit” left an important judgment unresolved.** A [secondary tag explainer](https://instantsitemap.com/resources/sitemap-structure-explained) treated minor and major edits broadly alike. [Google's guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap) makes the change's significance relevant. The brief adds a tiny price correction and asks the writer to explain why it matters.

**An unknown date needed its own answer.** [Google's discussion of uncertain dates](https://developers.google.com/search/blog/2023/06/sitemaps-lastmod-ping) supports omitting a date that cannot be determined reliably. The brief adds a page with no reliable change history.

**One relevant result remained unread.** A focused lastmod guide could not be opened. Its snippet could not establish what its examples covered, so the brief makes no claim that competing guides lack these cases.

The [result-observation sheet](/examples/search-intent-content-brief-worked-example/result-observations.md) retains the capture details and how each observation changed the assignment. The [primary-source sheet](/examples/search-intent-content-brief-worked-example/source-ledger.md) identifies what the writer can cite for the technical answer.

## What useful answer or worked example will this article add?

Give the writer a completed exercise that makes the decision visible. Here, four different change records are more useful than asking for “a comprehensive article about sitemap dates.” Each forces a choice the writer must explain.

For comparison, this rejected outline was authored for the demonstration:

1. What is a sitemap?
2. Every XML tag explained.
3. Why recent dates improve SEO.
4. Refresh all dates after each deployment.
5. Choose a sitemap generator.
6. Submit the sitemap and track rankings.

It spends most of the assignment on other jobs, and it builds in an unsupported promise about recent dates. The deployment instruction also needs correcting against the field's definition. Simply adding sources to those headings would leave the writer pursuing the wrong article.

The revised assignment supplies four cases instead. **Every page change, date and price below is fictional.** All four pages are rebuilt on October 6, 2026; a recorded update means the change became part of the public page on that date.

1. **C1 — Export guide:** its last substantive version was September 12. On October 3, obsolete instructions are replaced with current steps and a verification example; nothing changes afterward. Use `2026-10-03` and explain why the changed task answer supplies the date despite the later rebuild.
2. **C2 — Retry guide:** its last substantive version was September 15. Only the footer copyright year changes on October 6; the body, structured data and links stay identical. Keep `2026-09-15`.
3. **C3 — Homepage:** it aggregates recent content, but reliable change history is unavailable. Only the October 6 build timestamp is known. Omit `lastmod` and identify the missing history.
4. **C4 — Pricing:** its last substantive version was September 1. On October 5, the public monthly price changes from $49 to $59 to match the approved price; no other text changes. Use `2026-10-05`.

C4 makes the writer explain a judgment the research left open: a small correction changes a material fact used in a buying decision. That is our application of the rule, not an example supplied by Google. “Include an edge case” would not give the writer those inputs or that reasoning.

The [rejected outline](/examples/search-intent-content-brief-worked-example/rejected-outline.md) preserves why the broader approach was dropped. Keeping that reasoning with the assignment helps an editor recognize scope drift if a later draft starts adding generator recommendations again.

## Which sources, boundaries and links does the writer need?

Give each important claim a source location and a job in the article. For this assignment, three short primary passages are enough:

- **Google's sitemap documentation, XML additional notes:** [dependable dates for significant page changes](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap). Use it to review the cases' change classifications.
- **Sitemaps protocol, page-level lastmod row:** [an optional field describing the page's modification, rather than sitemap generation](https://www.sitemaps.org/protocol.html). Use it for the date choice and date-only notation.
- **Google's lastmod discussion, uncertain-date paragraph:** [omit the field when the date cannot be determined reliably](https://developers.google.com/search/blog/2023/06/sitemaps-lastmod-ping). Use it for C3.

The source sheet names the exact sections and their limits, so the writer can reopen the relevant passage without reconstructing the research. For a disputed sentence, the [claim-checking guide](/library/articles/how-to-fact-check-ai-generated-content/) explains how to compare wording with its source. The content brief itself should settle the assignment's direction before that sentence-level review begins.

The brief also makes an explicit internal-link decision: no internal destination is required for the commissioned lastmod article. Its complete exercise and primary citations serve the selected task. The claim-checking link above belongs to this article about writing briefs; it is not an instruction to insert that link into every article the brief produces.

With the reader, cases and sources established, the answer plan becomes specific:

1. **What changed on the page?** Start from the diff and use C4.
2. **Which date does the record support?** Compare C1 and C2.
3. **What if the history is unreliable?** Use C3 and name what the generator owner would need to investigate.
4. **How do I check the result?** Compare the four decisions with the generated entries. If adapting them to a real site, rebuild an unchanged page again and check whether its value moves without a supported event.

Keep sitemap creation, submission, CMS configuration, canonical selection, byline dates and search-performance analysis outside this article. Sitemap-index and news-sitemap rules also belong to other assignments. These exclusions prevent an apparently useful detour from replacing the decision the reader came to make.

The deliverable is the draft, its completed case table and any unresolved claims. The [packet notes](/examples/search-intent-content-brief-worked-example/README.md) explain how the files fit together.

## Could a writer complete this brief without inventing experience?

Yes: the primary sources establish the rules, and the labeled cases supply the inputs for applying them. No personal incident or client result is needed.

To check sufficiency, ask a reader to choose an output for each case using only the brief. They should be able to cite the relevant rule and explain the choice from the supplied record. A request to guess the homepage's history would expose a missing branch; a claim that the price correction improved sales would exceed the evidence.

If a choice still depends on something the writer must invent, repair that missing input or instruction before asking for more polished prose.
