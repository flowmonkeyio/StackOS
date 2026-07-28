---
title: How to do keyword research for AI search
description: A case-led research method for deciding what a natural-language AI-search question can support when conventional keyword fields are missing.
publishedAt: '2026-07-27'
updatedAt: '2026-07-27'
author: StackOS team
category: AI operations
topics:
  - keyword research
  - AI search
  - content research
  - editorial evidence
readingTime: 7 min read
featured: false
visual: none
searchIntent: Learn how to research natural-language questions for AI search using problem evidence, conventional keyword data, result inspection, and accountable source records
relatedWorkflows:
  - seo-keyword-research
  - branding-content-production
relatedAgents:
  - seo-workflow-keyword-research
  - branding-evidence-curator
  - branding-channel-strategist
  - branding-narrative-writer
relatedArticles:
  - how-to-build-ai-content-workflow-that-does-not-sound-generic
  - how-to-use-product-evidence-without-writing-a-product-pitch
  - how-to-build-ai-agent-workflow
---

The query “how to do keyword research for AI search” returned `null` for search volume, CPC, and paid competition in our corrected July research batch. The research decision began there. The empty fields left the question active, while the team checked whether the query came from a concrete operating problem and whether the available record could support a useful article.

The July case was kept as a small ledger instead of compressed into a single volume judgment.

| Record | What the July case established | Use in the decision |
| --- | --- | --- |
| Question origin | Public workflow jobs, decisions, handoffs, failure modes, and existing material produced 472 exact-new candidates across 15 clusters after deduplication against a 500-keyword baseline. | The query belonged to a documented problem-first research corpus. |
| Measurement receipt | A 472-phrase request hit a phrase-length limit; a corrected 448-phrase request completed after 24 over-limit questions were excluded. | Provider task status and endpoint limits became part of the interpretation. |
| Result snapshots | Eight selected US/en snapshots from July 12 each contained an AI Overview; the primary query was outside that sample. | The snapshots described related observations from that date. |
| Evidence and ownership | The research ledger preserved the method and the July 25 article corpus was reviewed. | The question could be assessed as a separate reader job with a named evidence record. |

## What the measurement instrument actually returned

The first request contained all 472 phrases. At least one question exceeded the endpoint’s 10-word limit, so the provider task failed. The corrected request excluded 24 over-limit questions and returned results for 448 phrases. That sequence made the task-level result part of the evidence because it established whether the measurement completed.

The selected [DataForSEO Google Ads endpoint](https://docs.dataforseo.com/v3/keywords_data-google_ads-search_volume-live/) accepts up to 1,000 phrases, with an 80-character and 10-word limit per phrase. Its documentation also records that Google Ads may return no data for some keyword groups. Within the corrected batch, four phrases had positive returned search volume and 444 had a null `search_volume` field. The primary query also had null CPC and paid-competition fields.

[Google Ads describes historical keyword metrics](https://support.google.com/google-ads/answer/3022575?hl=en) as rounded observations for a keyword and close variants under the chosen month range, location, and Search Network settings. In this ledger, the result records what that historical Ads measurement supplied for the chosen query and settings. Human need, AI-search value, and future page performance remained unknown.

The eight result snapshots formed a separate observation beside that measurement. Each selected July 12 snapshot contained an AI Overview. The primary query was outside the sample, so the record remained specific to the eight selected queries and their date. Google’s [AI features guidance](https://developers.google.com/search/docs/appearance/ai-features) says AI Overviews and AI Mode may use query fan-out across related subtopics and data sources. It applies the same SEO fundamentals to AI features and treats eligibility, crawling, indexing, serving, and inclusion as separate outcomes. For this team, that supported inspecting related questions while keeping the sample’s scope visible.

## The query still needed an evidence owner and a place in the corpus

The keyword batch identified a candidate. The article required a record that could explain the method and a corpus decision about where that explanation belonged.

For this query, the evidence owner is a dated research ledger: the problem-first expansion, the failed and corrected measurement receipts, the selected result snapshots, and the primary documentation that defines their limits. The StackOS records preserve which request failed, what was excluded, what the corrected response returned, and how the corpus check was resolved. The article uses that provenance to explain the research decision.

The July 25 corpus review found no existing article responsible for this exact reader job. The candidate could therefore remain a separate canonical packet. A different question could land elsewhere: an existing article may already carry the explanation, or the research may lack a source that can substantiate one. Those are content-ownership decisions, separate from the availability of a volume field.

The dated case supports one explanation: problem-first questions can be examined through several bounded signals while null Ads fields remain unknowns. It leaves rankings, inclusion, traffic, conversions, and citation-format outcomes outside the case record.

## Disposition for this candidate

| Status | Record |
| --- | --- |
| Proceed | Assign a narrow case-led canonical packet. The question has an operating origin, corrected measurement receipts, dated result observations, a named evidence record, and a reader job that was distinct in the July 25 corpus review. |
| Hold | Retain the question in research when attribution or content ownership is incomplete. Further keyword data may refine measurement, while those two decisions require their own evidence. |
| Reject | Reject use of this record for a ranking, inclusion, traffic, conversion, or citation-format conclusion. Those outcomes sit beyond the case and the primary sources. |
