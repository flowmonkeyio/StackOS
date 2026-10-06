---
title: Which page should I improve first using Search Console data?
description: Compare four pages using Search Console queries and their actual content. Choose one useful edit with a worked CSV, page examples and completed decision sheet.
publishedAt: '2026-10-06'
updatedAt: '2026-10-06'
author: StackOS team
category: SEO
topics:
  - Search Console
  - content prioritization
  - SEO content refresh
readingTime: 8 min read
featured: false
visual: none
searchIntent: Choose which existing page to improve using query and page evidence, business relevance and an inspected answer gap
relatedWorkflows:
  - seo-content-refresh
  - seo-keyword-research
relatedAgents:
  - seo-workflow-content-refresh
  - seo-workflow-keyword-research
relatedArticles:
  - how-to-do-keyword-research-for-ai-search
---

Start with a page that answers a useful customer question but leaves a clear gap you can fix, then use its query and page data to compare it with the alternatives. [Search Console's API](https://developers.google.com/webmaster-tools/v1/searchanalytics/query) returns top rows rather than every row, so a missing query is not evidence that nobody searches for it.

## What reader problem would improving this page solve?

Name the decision your intended customer needs to make, then check whether the page helps them make it. “Get more traffic” gives you no way to distinguish a useful visitor from someone looking for a product you do not sell.

Consider a small software company whose customers review workflow runs, assign someone to handle a failure and export audit logs. Its readers are operations managers. It has four guides:

| Page | Current subject |
| --- | --- |
| P1 | Review workflow errors |
| P2 | What is a task queue? |
| P3 | Find delays in approvals |
| P4 | Export an audit log |

Everything in this exercise is fictional: the company, queries, figures and page bodies. The following twelve rows use one scope: September 1–28, 2026, Pacific time; United States; web search; all devices; finalized data. They are selected query/page pairs, with no page totals supplied. Adding their impressions would give you a sum of these rows, not a complete page report.

| Row / page | Query | Clicks | Impressions |
| --- | --- | ---: | ---: |
| Q01 / P1 | how to prioritize failed automated tasks | 8 | 320 |
| Q02 / P1 | which workflow failures should i fix first | 3 | 120 |
| Q03 / P1 | workflow error triage checklist | 4 | 80 |
| Q04 / P2 | python task queue | 21 | 4,200 |
| Q05 / P2 | task queue | 18 | 1,800 |
| Q06 / P2 | workflow exception handoff template | 3 | 150 |
| Q07 / P3 | why are approval requests delayed | 1 | 12 |
| Q08 / P3 | find approval bottlenecks | 0 | 8 |
| Q09 / P3 | workflow approval wait time report | 1 | 4 |
| Q10 / P4 | export workflow audit log csv | 24 | 240 |
| Q11 / P4 | audit log csv timezone | 6 | 100 |
| Q12 / P4 | audit log export includes failed tasks | 3 | 60 |

You can download the [CSV](/examples/choose-pages-to-improve-with-search-console/query-page-data.csv), [four complete page bodies](/examples/choose-pages-to-improve-with-search-console/page-snapshots.md), [completed decision sheet](/examples/choose-pages-to-improve-with-search-console/decision-sheet.md) and [report settings](/examples/choose-pages-to-improve-with-search-console/report-scope.json). The CSV also includes CTR and average position. Google defines [CTR as clicks divided by impressions](https://support.google.com/webmasters/answer/7042828); the CSV stores it as a fraction, so `0.025` means 2.5%.

For your own comparison, keep the date range and filters together with the export. Our [Search Console guide](/library/integrations/google-search-console/) covers getting a report. The decision here starts after you have one.

## Which queries belong to the same reader task?

Group queries when a useful answer would let those readers do the same thing. Q01–Q03 use different wording, but each asks how to choose which failed work needs attention first. Counting words or treating each phrase as a new article would split one answer into three jobs.

Now open P1. After explaining how to find failed runs, it says:

> Start with the oldest failed run and work down the list.

The next instruction is to correct the input and retry. There is no explanation of how to compare the consequences of two failures. Someone following this guide could spend the morning fixing an old internal summary while a customer's onboarding remains blocked.

That is a specific reason to improve P1. You have both a relevant question and the passage that fails to answer it.

P2 is less straightforward. Its three queries could mean finding a Python library, understanding a term or handing a failed task to another person. Those readers need different things. The page explains a queue, then ends with an assignment instruction:

> You can assign a failed task to another teammate when it needs their attention.

A reader asking for a handoff template still has to decide what information to send. A reader asking for a Python library has found the wrong company: this business does not sell one. The common phrase “task queue” cannot settle either problem.

In a real report, you can [filter to a query and inspect the Pages tab](https://support.google.com/webmasters/answer/17010961) to see which URLs appeared for it. Then read the answer on those pages; the report cannot tell you whether it is useful.

## Why might the largest impression count point to the wrong work?

Visibility can come from a task your business cannot help with. Q04 has 4,200 impressions, the largest row here, but adding Python instructions to P2 would pull the definition toward a different audience and product.

Q06 suggests a more useful possibility: a separate exception-handoff guide. It could show a completed handoff with the failed task, what already happened, the customer consequence, the next check and a named owner. Keep the definition focused. Before commissioning that guide, check that no existing page answers the question and that the actual product supports the proposed instructions. The four-page exercise has no such guide; a real site might.

P3 deserves investigation for another reason. Its query “workflow approval wait time report” has a 25% CTR. That is one click from four impressions. More significantly, its body teaches checking one request: find its approver, look for missing decision material and ask what is holding it up. A report comparing delays across many requests would require a different explanation.

Find out which of those jobs the reader needs. A concrete customer question and available product data could justify a useful reporting guide even while impressions remain low. Another completed period with the same filters may add context, but no arbitrary minimum count should substitute for understanding the task.

For the supplied pages, the work queue is:

| Page | Decision | Basis |
| --- | --- | --- |
| P1 | Refresh first | A relevant question meets a visible flaw in the instructions. |
| P2 | Consider a separate guide | The handoff question needs its own answer, subject to inventory and product checks. |
| P3 | Investigate | Checking one approval and reporting on delays may be different needs. |
| P4 | Leave alone for now | Its existing instructions answer the three supplied queries. |

P3 is the strongest unresolved alternative. It might become the better assignment once someone establishes the reporting need. P1 is ready to work on now because you can identify the deficient passage and specify its repair.

## What exact change would make the selected page more useful?

Replace P1's oldest-first instruction with a way to compare consequences, then show how that changes the next action. This is a bounded [content refresh](/library/workflows/seo-content-refresh/): the page keeps its purpose and gains the decision its readers are asking for.

The selected URL is `https://example.com/guides/review-workflow-errors/`. A replacement paragraph could say:

> Check what each failure is blocking and when that work is needed. Look for failures with a shared cause before treating each row as a separate fix. Read what the previous attempt already did before retrying it. If you cannot establish the next action, assign an owner and write down what they need to check.

Three short cases would make those instructions usable:

| Case and assumed facts | Next action |
| --- | --- |
| Customer onboarding stopped before account creation; the customer cannot continue. | Assign an owner to diagnose the block and confirm how to resume. |
| An internal daily summary failed; nobody needs it until tomorrow. | Schedule it behind the current customer block. |
| A notification attempt timed out; delivery is unknown. | Check whether it arrived before deciding to send another. |

Those assumptions matter. If the internal summary is needed for a deadline this morning and the customer has a working manual route, the order changes. The revised page should teach that judgment. Product-specific recovery steps need to be checked against the product before publication.

The editing task is now concrete: replace one paragraph and add three explained cases. You can give the revised section to someone who did not write it and ask them to choose the next action in each case, including the deadline variation. If they still default to the oldest row, the explanation needs more work.

## What evidence would justify leaving a page alone?

An accurate, findable answer to the relevant task is a reason to leave it alone. P4 gives the export path, permissions and date controls. It says “Timestamps use UTC” and explains that successful and failed runs are included unless a status filter excludes them. It even tells the reader to find a known failed run in the CSV to check the export.

Those passages answer Q10–Q12. Q12's average position of 8.6 does not identify a missing explanation. Neither would selecting every query in a position band of 5–20. You still need to name what a reader cannot understand or do.

A changed interface, an incorrect field or evidence that readers cannot find the timezone answer would reopen P4. For now, the useful work is on P1.

Record that assignment with its URL, Q01–Q03, the oldest-first passage, the proposed replacement and the three-case check. Keep the original report scope and note the eventual edit date so later observations can be compared sensibly. Whether traffic changes after publication is a separate question; this assignment is complete when the revised page helps a reader make the decision it currently skips.
