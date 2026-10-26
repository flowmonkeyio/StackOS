# Completed decision sheet

**Illustrative exercise.** Every row, page and product detail is fictional. This is a proposed work queue, not a report of edits or results. Scope: September 1–28, 2026, Pacific time; United States; web search; all devices; finalized data. No page totals are inferred from the twelve query rows.

## The decision

Improve **P1, `/guides/review-workflow-errors/`, first**. Its queries describe a relevant decision, and the current body offers an oldest-first instruction where a reader needs a way to judge consequences. The edit can fix a visible problem without changing what the page is about.

| Existing page | Query rows | Reader task | Decision | Why it waits or goes first |
| --- | --- | --- | --- | --- |
| P1: review workflow errors | Q01–Q03 | Decide which failed work needs attention first | **Refresh first** | Relevant task; the inspected answer does not explain how to prioritize consequences or check whether retrying is safe. |
| P2: task queue | Q04–Q06 | Mixed: find a Python library, understand a term, or hand an exception to a person | **Create elsewhere, if validated** | Preserve the definition. A separate handoff guide is a candidate for Q06; first check the task, inventory and product. Do not pursue Q04 for this business. |
| P3: approval delays | Q07–Q09 | Find why approvals are waiting, possibly report on delays | **Investigate** | The body answers a basic request check. The tiny sample does not establish whether readers need that check or a reporting method. |
| P4: export audit log | Q10–Q12 | Export events and understand what the file contains | **Leave alone for this task** | The body directly covers the procedure, UTC timestamps and failed runs; no demonstrated answer gap. |

These are judgments about the supplied exercise, not rules that a given impression count or position will always produce the same decision.

## P1: Refresh the weak answer

**Evidence.** Q01 has 320 impressions and 8 clicks; Q02 has 120 and 3; Q03 has 80 and 4. All three queries ask how to triage failures. Their word counts differ, but they belong to the same reader task. Each row describes its own query/page pair; the sheet makes no claim about the page's overall impressions.

**Inspected gap.** P1 says, “Start with the oldest failed run and work down the list.” It then says to correct an input and retry. The complete body never asks what is blocked, whether several failed runs have one cause, who owns the fix or whether an earlier attempt already performed an external action. A reader could follow the instructions and still prioritize the wrong work.

**Proposed edit.** Replace that paragraph with a short decision procedure and a three-row example:

1. Check the consequence: a blocked customer workflow or time-sensitive obligation comes before an internal report that can wait.
2. Check whether failures share a cause before treating every row as separate work.
3. Check what the failed run already did before retrying; a visible error does not tell you whether a message or record was already created.
4. Assign a person and write the next check when the cause or safe recovery step is uncertain.

The example can compare a customer onboarding step stopped before account creation, a failed daily internal summary, and a timed-out notification whose delivery is unknown. State the assumed facts and the next check for each. These would be hypothetical cases, not customer incidents. Product-specific retry instructions need separate verification before they are published.

**Counter-case.** An older internal report could go first if it is needed for a deadline that morning; the customer workflow could already have a safe manual workaround. Consequence and current state change the order. If inspecting the real page revealed this procedure already existed and was easy to find, the proposed refresh would lose its basis.

**Verification.** Before publication, give the revised answer and its three cases to a reader who did not write it. Can they explain the next action and why another case waits? Check the actual product's retry behavior and remove any unsupported instruction. After publication, record the change and retain the same report scope when observing query/page data. A useful answer is the acceptance test for the edit; traffic movement would need a separate assessment.

## P2: Keep the definition; propose a separate handoff guide

**Evidence.** Q04, “python task queue,” has 4,200 impressions, 21 clicks and an average position of 8.4. It is the largest impression row in this set. The fictional product does not sell a Python library, and P2 provides no Python instructions. Q05, “task queue,” is ambiguous. Q06, “workflow exception handoff template,” has 150 impressions and 3 clicks and fits the product's manual-owner feature.

**Inspected gap.** P2 explains what a queue is and ends with, “You can assign a failed task to another teammate when it needs their attention.” It supplies no handoff template, required context or example of what the new owner receives. That missing answer belongs to a different reader task from the definition.

**Proposed work.** Keep P2's definition. Put a separate `/guides/workflow-exception-handoff/` into the research queue, behind the selected refresh. Before committing to write it, verify the reader task, existing inventory and product behavior. Its concrete contribution would be a completed handoff containing the failed task, known state, customer consequence, actions already attempted, next check and named owner. Link the definition's assignment sentence to it only once a reviewed guide exists. Within the exercise's four-page inventory, none of the other pages owns this handoff task.

**Counter-case.** If a real inventory found a handoff guide already answering this question, improve or link to that page instead of creating another. If the product served Python developers, Q04 could merit investigation; that is explicitly outside this exercise's business. Q06 alone does not prove enough demand or commercial value to justify a large content project.

**Verification.** Check the real inventory and the product's actual assignment fields. Ask whether a new owner can continue a sample failed task from the proposed handoff without guessing what already happened. Before adding a link, verify that the destination exists and answers the separate task. No edit is proposed to chase the Python query, and no indexed page is removed because of it.

## P3: Investigate the useful but tiny signal

**Evidence.** Q07 has 12 impressions and 1 click; Q08 has 8 and 0; Q09 has 4 and 1. Q09's 25% CTR is one click divided by four impressions. That number is too little evidence to call this the strongest page or a reliable source of future traffic.

**Inspected gap.** P3 says, “Sort by age to find requests that have waited longest.” It then tells the reader to check the approver and missing decision material. That answers a basic single-request investigation. It does not explain how to report on repeated delays across requests, which Q09 may be asking for. The query wording suggests that distinction; it does not settle it.

**Proposed next step.** Determine whether the intended job is checking one waiting approval or comparing approval delays across a period. Look for actual customer questions and inspect the product's available data. Compare an additional completed period using the same filters, but do not wait for an arbitrary impression threshold if direct reader evidence establishes a useful fix sooner. No rewrite is approved from this sample alone.

**Counter-case.** One well-supported customer question plus a verified product capability could justify a reporting explanation even if search impressions stay low. Equally, a larger later sample would not justify teaching a report the product cannot produce. Missing query rows are not evidence that people never need the task.

**Verification.** The investigation is complete when the owner can name the reader's decision, show the available inputs and point to the existing passage that fails to answer it. Then choose between a bounded addition to P3 and a separate reporting guide. If that evidence is absent, keep the page as it is and record the uncertainty.

## P4: Leave an adequate answer alone

**Evidence.** Q10 asks for a CSV export, Q11 asks about its timezone, and Q12 asks whether it includes failed tasks. Their respective rows have 240, 100 and 60 impressions. The modest average position of 8.6 for Q12 does not, by itself, reveal a content defect.

**Inspected answer.** P4 gives the path **Activity → Audit log**, the date and workflow controls, and **Export CSV**. It says, “Timestamps use UTC.” It also says, “Successful runs and failed runs are both included unless you add a status filter before exporting.” It explains permissions and gives a check for a missing failed run. These passages answer the three observed tasks.

**Proposed edit.** None for this selection exercise. Keep the page in the observation list and retain its current instructions. This does not declare every aspect of the page optimal; the supplied evidence identifies no specific answer repair worth doing before P1.

**Counter-case.** A changed product interface, incorrect export fields, a broken download or evidence that readers cannot find the timezone answer would reopen the page. A confirmed conversion or search-result problem would be a different investigation. None is provided here.

**Verification.** When checking the real page, run the export and compare a sample file against the instructions. If the answer remains accurate and usable, record “no content change” with that basis. Do not rewrite it just to make every row in the work queue produce an edit.

## Record the selected work

| Field | Completed entry |
| --- | --- |
| URL | `https://example.com/guides/review-workflow-errors/` |
| Reader decision | Which failed work should I handle first, and what must I check before acting? |
| Evidence | Q01–Q03 and complete snapshot P1 |
| Current gap | Oldest-first advice skips consequence, shared cause and the state of an earlier attempt. |
| Bounded change | Replace that instruction with a short triage procedure and three explained cases. |
| Review condition | A reader can justify the next action; product behavior in the example has been checked. |
| Baseline | Retain this window and filters; obtain separate page-level data if page totals are needed. |
| Outcome | Proposed. No edit, publication or ranking result is represented in this exercise. |
