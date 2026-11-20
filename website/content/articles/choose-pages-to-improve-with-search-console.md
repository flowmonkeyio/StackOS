---
title: Which page should I improve first using Search Console data?
description: Compare four pages using Search Console queries and their actual content. Choose one useful edit with a worked CSV, page examples and completed decision sheet.
publishedAt: '2026-10-06'
updatedAt: '2026-10-07'
author: StackOS team
category: SEO
topics:
  - Search Console
  - content prioritization
  - SEO content refresh
readingTime: 7 min read
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

Name the decision your intended customer needs to make, then read the page's instructions. “Get more traffic” gives you no way to distinguish a useful visitor from someone looking for a product you do not sell.

Consider a fictional software company whose customers review workflow runs, assign someone to handle a failure and export audit logs. Its readers are operations managers. One guide, P1, explains how to find failed runs, then says:

> Start with the oldest failed run and work down the list.

The next instruction is to correct the input and retry. Following that advice, someone could spend the morning fixing an old internal summary while a customer's onboarding remains blocked. The page never explains how to compare the consequences of two failures. That is a gap an editor can name and repair.

The company also has guides defining a task queue (P2), finding approval delays (P3) and exporting an audit log (P4). We will compare all four before choosing the work.

The company, page bodies and figures in this exercise are invented. The [twelve-row CSV](/examples/choose-pages-to-improve-with-search-console/query-page-data.csv) uses September 1–28, 2026, Pacific time; United States; web search; all devices; finalized data. These are selected query/page pairs, not complete page totals. The [four page bodies](/examples/choose-pages-to-improve-with-search-console/page-snapshots.md), [completed decision sheet](/examples/choose-pages-to-improve-with-search-console/decision-sheet.md) and [report settings](/examples/choose-pages-to-improve-with-search-console/report-scope.json) supply the full exercise.

For your own comparison, keep the date range and filters with the export. The [Search Console guide](/library/integrations/google-search-console/) covers getting a report; the decision here starts after you have one.

## Which queries belong to the same reader task?

Group queries when a useful answer would let those readers do the same thing. Three supplied rows point to P1:

- **Q01:** “how to prioritize failed automated tasks” — 8 clicks from 320 impressions.
- **Q02:** “which workflow failures should i fix first” — 3 clicks from 120 impressions.
- **Q03:** “workflow error triage checklist” — 4 clicks from 80 impressions.

All three ask how to choose which failed work needs attention first. P1's oldest-first instruction does not answer that decision. Counting words or treating each phrase as a new article would split one useful answer into three jobs.

P2's queries point in different directions: “python task queue,” “task queue” and “workflow exception handoff template.” Those readers may want a library, a definition or instructions for passing failed work to another person. The page supplies a definition and ends with:

> You can assign a failed task to another teammate when it needs their attention.

That leaves the handoff reader to work out what information to send. The Python reader has found the wrong company: this business does not sell a Python library. The phrase “task queue” cannot settle either problem.

In a real report, [filter to a query and inspect the Pages tab](https://support.google.com/webmasters/answer/17010961) to see which URLs appeared for it. Then read their answers; the report cannot tell you whether they are useful.

## Why might the largest impression count point to the wrong work?

The largest row here is Q04, “python task queue,” with 4,200 impressions. Its audience mismatch gives us no reason to expand P2 in that direction.

Q06, the handoff-template query, suggests more useful work: **consider a separate handoff guide**. It could show the failed task, what already happened, the customer consequence, the next check and a named owner. Before commissioning it, check that no existing page answers the question and that the product supports those instructions. Keep P2's definition focused.

For P3, **investigate the reader's task**. “Workflow approval wait time report” has a 25% CTR, but that is one click from four impressions. Google defines [CTR as clicks divided by impressions](https://support.google.com/webmasters/answer/7042828); the companion CSV stores it as a fraction, so `0.25` means 25%.

The stronger question comes from the page itself. P3 explains how to check one request: find its approver, look for missing decision material and ask what is holding it up. A report comparing delays across many requests needs a different explanation. A concrete customer question and available product data could justify that guide even with few impressions. Another comparable reporting period may help, but an arbitrary minimum count cannot choose the task for us.

P3 is the strongest unresolved alternative. **Refresh P1 first** because its relevant question, deficient passage and feasible repair are already clear. P4 has a reason to wait too, which we will check against its actual instructions below.

## What exact change would make the selected page more useful?

Replace P1's oldest-first instruction with a way to compare consequences, then show how that changes the next action. This is a bounded [content refresh](/library/workflows/seo-content-refresh/): the page keeps its purpose and gains the decision its readers are asking for.

The selected URL is `https://example.com/guides/review-workflow-errors/`. A replacement paragraph could say:

> Check what each failure is blocking and when that work is needed. Look for failures with a shared cause before treating each row as a separate fix. Read what the previous attempt already did before retrying it. If you cannot establish the next action, assign an owner and write down what they need to check.

Then give the reader three cases:

**Customer onboarding is blocked.** Account creation stopped before it completed, so the customer cannot continue. Assign an owner to diagnose the block and confirm how to resume.

**An internal summary can wait.** The daily summary failed, but nobody needs it until tomorrow. Schedule it behind the current customer block.

**A notification's outcome is unknown.** The attempt timed out. Check whether the notification arrived before deciding to send another.

Those assumptions matter. If the summary is needed for a deadline this morning and the customer has a working manual route, the order changes. Product-specific recovery steps also need checking against the product before publication.

Ask someone who did not write the revision to choose the next action in these cases, including the deadline variation. If they still default to the oldest row, the explanation needs work. Keep the selected URL, old passage, replacement and report scope in the decision sheet, and record the edit date for later comparison. Whether traffic changes is a separate observation.

## What evidence would justify leaving a page alone?

An accurate, findable answer to the relevant task is a reason to leave it alone. P4 gives the export path, permissions and date controls. It says “Timestamps use UTC” and explains that successful and failed runs are included unless a status filter excludes them. It even tells the reader to find a known failed run in the CSV to check the export.

Those passages answer Q10–Q12. Q12's average position of 8.6 does not identify a missing explanation. Neither would selecting every query in a position band of 5–20. You still need to name what a reader cannot understand or do.

A changed interface, an incorrect field or evidence that readers cannot find the timezone answer would reopen P4. For now, the useful work is on P1.
