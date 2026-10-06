---
title: How do I turn search results into a content brief a writer can use?
description: Follow a researched question from search results to a complete writing brief, with a rejected outline, verified sources and four worked cases.
publishedAt: '2026-10-06'
updatedAt: '2026-10-06'
author: StackOS team
category: SEO
topics:
  - content briefs
  - search intent
  - content research
readingTime: 8 min read
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

That gives the writer a way to judge what belongs. A paragraph on choosing a CMS would need to help with this decision to earn its place. If the intended reader could not inspect a diff, the assignment would need different prerequisites or a simpler way to establish what changed.

## What do the current results reveal, and which claims need separate verification?

Use results to inspect the answers available, then take technical claims to their primary sources. For this example, research on October 6, 2026 used the English query `when should I update a page sitemap lastmod date` in a public web search tool. Five returned items were selected; four bodies were opened and one opening failed. The sample had no location or device filter and was not a controlled Google ranking capture. The question was chosen to demonstrate the method, without a claim about its search volume.

Google's sitemap documentation and the Sitemaps protocol already supplied the official rule and field definition. That made a long technical introduction less useful to this assignment. The writer could cite those sources and spend more attention on applying them to an actual change record.

A [secondary tag explainer](https://instantsitemap.com/resources/sitemap-structure-explained) treated minor and major edits broadly alike. That needed checking before it became an instruction in the brief. [Google's guidance](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap) makes the significance of the change relevant. The useful editorial question became: what would make a small correction matter?

[Google's discussion of uncertain dates](https://developers.google.com/search/blog/2023/06/sitemaps-lastmod-ping) suggested another case: a page with no reliable history. Meanwhile, a focused lastmod guide appeared in the results but could not be opened. Its snippet supplied no basis for saying that other guides lacked the example being planned here.

Those observations changed the assignment in concrete ways:

| Research finding | Instruction added or changed |
| --- | --- |
| Authoritative rules and syntax are already available. | Keep the rule brief; build the explanation around change records. |
| A broad claim about small edits needs qualification. | Include a tiny price correction and require the writer to explain its significance. |
| An official source addresses unreliable history. | Include a case where the date cannot be recovered. |
| One relevant result could not be inspected. | Remove any claim that competing guides lack these examples. |

The [result-observation sheet](/examples/search-intent-content-brief-worked-example/result-observations.md) retains the returned URLs, the pages actually opened and the effect of each finding. Its job is to explain how the assignment changed. The [primary-source sheet](/examples/search-intent-content-brief-worked-example/source-ledger.md) separately identifies what the writer can cite for the technical answer.

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

The revised assignment supplies the following cases instead. **Every page change, date and price in this table is fictional.** All four pages are rebuilt on October 6, 2026. A recorded update means the change became part of the public page on that date.

| Case | Complete change record | Output and explanation required |
| --- | --- | --- |
| C1: export guide | Last substantive version: September 12. On October 3, obsolete export instructions are replaced with current steps and a verification example. Nothing changes afterward. | `2026-10-03`. Explain why the changed task answer supplies the date despite the later rebuild. |
| C2: retry guide | Last substantive version: September 15. On October 6, only the footer copyright year changes; body, structured data and links are identical. | `2026-09-15`. Explain why the existing date stays. |
| C3: homepage | The homepage aggregates recent content. Its reliable change history is unavailable; only the October 6 build timestamp is known. | Omit `lastmod` for this entry. Identify the missing history instead of filling the gap with a guessed date. |
| C4: pricing | Last substantive version: September 1. On October 5, the displayed monthly price changes from $49 to $59 to match the approved price. No other text changes. | `2026-10-05`. Explain the editorial judgment: a small correction changes a material fact used in a buying decision. |

C4 does more work than an instruction to “include an edge case.” Its inputs expose the specific judgment that the result review left unresolved. It is our application of the rule, not an example supplied by Google. The writer needs to explain the consequence of the changed price rather than count edited characters.

The [rejected outline](/examples/search-intent-content-brief-worked-example/rejected-outline.md) preserves why the broader approach was dropped. Keeping that reasoning with the assignment helps an editor recognize scope drift if a later draft starts adding generator recommendations again.

## Which sources, boundaries and links does the writer need?

Give each important claim a source location and a job in the article. For this assignment, three short primary passages are enough:

- **Google's sitemap documentation, XML additional notes:** [dependable dates for significant page changes](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap). Use it to review the cases' change classifications.
- **Sitemaps protocol, page-level lastmod row:** [an optional field describing the page's modification, rather than sitemap generation](https://www.sitemaps.org/protocol.html). Use it for the date choice and date-only notation.
- **Google's lastmod discussion, uncertain-date paragraph:** [omit the field when the date cannot be determined reliably](https://developers.google.com/search/blog/2023/06/sitemaps-lastmod-ping). Use it for C3.

The source sheet names the exact sections and their limits, so the writer can reopen the relevant passage without reconstructing the research. For a disputed sentence, the [claim-checking guide](/library/articles/how-to-fact-check-ai-generated-content/) explains how to compare wording with its source. The content brief itself should settle the assignment's direction before that sentence-level review begins.

With the reader, cases and sources established, the answer plan becomes specific:

1. **What changed on the page?** Start from the diff. Use C4 to show why the label “minor edit” leaves a judgment unresolved.
2. **Which date does the record support?** Compare C1 and C2 with the common rebuild date. Preserve their distinct outputs and reasons.
3. **What if the history is unreliable?** Give C3 a complete answer and name the history the generator owner would need to investigate.
4. **How do I check the result?** Compare the four decisions with the generated entries. If adapting them to a real site, rebuild an unchanged page again and check whether its value moves without a supported event.

Keep sitemap creation, submission, CMS configuration, canonical selection, byline dates and search-performance analysis outside this article. Sitemap-index and news-sitemap rules also belong to other assignments. These exclusions prevent an apparently useful detour from replacing the decision the reader came to make.

The deliverable is the draft, its completed case table and any unresolved claims. The [complete brief](/examples/search-intent-content-brief-worked-example/completed-brief.md) includes the fictional URLs and verification instructions; [the packet notes](/examples/search-intent-content-brief-worked-example/README.md) explain how the files fit together.

## Could a writer complete this brief without inventing experience?

Yes: the researched sources establish the technical rules, and the labeled cases supply the inputs for the explanation. No personal incident or client result is needed to complete this assignment.

Test the brief before handing it over by trying its two least comfortable cases. For C3, the writer has no reliable page history. If the assignment merely said “give every example a date,” the writer would have to guess or reject the instruction. This brief supplies an omission branch and its source, so the case can be finished.

For C4, the writer knows what changed, when it became public and why the corrected figure matters. They can explain the significance of the price correction while identifying that classification as the article's reasoning. They do not need to invent a customer who bought the product or a search result that improved afterward.

An independent reader should be able to recover October 3, September 15, no field and October 5 from the four records. If they cannot, check whether the brief is missing an input, source or explanation before asking for more polished prose.
