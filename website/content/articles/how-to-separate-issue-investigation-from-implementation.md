---
title: How to separate issue investigation from implementation
description: Turn a software issue report into a supported conclusion, an authorized change, and acceptance checks. Follow a worked CSV export example.
publishedAt: '2026-07-28'
updatedAt: '2026-09-30'
author: StackOS team
category: AI operations
topics:
  - issue investigation
  - engineering delivery
  - operator authorization
  - support operations
  - AI workflows
readingTime: 6 min read
featured: false
visual: none
searchIntent: Learn how to separate issue investigation, operator authorization, and engineering implementation so a plausible diagnosis does not silently become a fix plan
relatedWorkflows:
  - support-issue-investigation
  - support-delivery-task-handoff
  - engineering-tracked-delivery
relatedAgents:
  - support-workflow-issue-investigator
  - support-workflow-delivery-handoff
relatedArticles:
  - what-ai-agent-handoff-should-include
  - how-ai-orchestrators-triage-feedback
  - how-to-build-ai-agent-workflow
---

A task titled “Fix CSV export” leaves engineering to guess what should change. Even a convincing diagnosis can leave the scope and the test for success undecided.

Separate issue investigation from implementation by saving what the evidence establishes, identifying the work already authorized, and giving engineering observable acceptance criteria. Carry the unresolved parts into the task so the next agent can check them before choosing a fix.

## Follow one report far enough to know its limits

Here is a hypothetical software issue. The report, records, and instructions below are invented to show the method.

A user filters a table to show open items, then downloads a CSV. The file includes closed items too. For this example, the agreed behavior is that the export contains every record matching the selected status, across all table pages.

The investigator uses a test fixture with five records: A1, A2, and A3 are open; B1 and B2 are closed. The table shows two records per page. With the Open filter selected, its first page shows A1 and A2, and its second shows A3. The downloaded CSV contains all five records.

That is enough to reproduce a specific mismatch. Keep the fixture, application version, reproduction steps, and resulting CSV together. They give another person or agent a way to repeat the check.

Next, the investigator compares the requests. The table request includes the selected status. The export request omits it. The CSV has the expected columns, and an unfiltered export returns the five expected records.

The missing status is a useful lead. The investigator has not yet traced how the server handles the export request, so the exact cause remains unverified. A change to the client might be sufficient; the server might also ignore a supplied status. Those possibilities affect the implementation.

An investigation conclusion for this case could read:

> The filtered export failure is reproduced in the named test version. Selecting Open yields a CSV containing A1, A2, A3, B1, and B2; the expected set is A1, A2, and A3. The export request omits the status sent by the table request. The server's handling of that parameter has not been checked. Trace that handling before selecting the correction. Production impact and the version that introduced the issue remain unknown.

Link that conclusion to the fixture, request captures, export, and relevant source references. Keep observations separate from the suspected cause. Correct columns make a column-format defect less relevant to this report; they do not establish that every part of CSV generation works.

If the investigator cannot reproduce the failure, record the conditions tried and the missing evidence. A report that only fails with a particular saved filter may need that filter's configuration next. “Could not reproduce” is a useful result when its limits are clear; it does not establish that the reported issue is resolved.

## Use the authorization you already have

The conclusion helps an operator decide what to do. A likely cause does not itself say whether the agent may create a task, change code, or deploy a result.

Check the current instruction and the project's rules. If they already authorize the necessary work, carry that instruction forward and proceed within its scope. A change of phase is not a reason to ask for the same permission again. If the request only authorized investigation, return the conclusion and recommended next action for the operator's decision.

For the export example, assume the operator has now given this instruction:

> Create the delivery task and correct status filtering in CSV export. Keep the columns and other export behavior unchanged. Return a reviewed change with checks for filtered and unfiltered exports. Do not deploy it.

This gives engineering room to trace the server behavior and choose the correction. It does not commit the team to the investigator's first suspected code path. If that path turns out to be wrong, update the explanation and supporting evidence while pursuing the authorized behavior.

A broader change needs its own scope decision. Replacing the reporting system would go beyond this instruction. So would silently changing the export to include only the current table page.

## Give engineering a behavior it can test

The delivery task should carry the investigation conclusion, its evidence links, the applicable instruction, the affected export path, and the remaining uncertainty. Add any dependencies and a named owner for the work. Engineering should be able to retrieve the original report without searching a chat history for it.

For this case, write the desired behavior explicitly: **CSV export uses the selected status and includes matching records from every page.** Keep the current columns and their order. The task can require checking the server's handling of the status before deciding where to change the code.

Use the same fixture to make acceptance concrete:

| Check | Expected exported records |
| --- | --- |
| Open is selected while the first table page is visible | A1, A2, and A3, including the record on the second page |
| Closed is selected | B1 and B2 |
| No status filter is selected | All five records |
| A status with no matching records is selected | The agreed CSV headers, with no data rows |

In this example, a header-only file is the chosen behavior for an empty result. A real project should use its existing export contract or settle that question before treating it as a test expectation.

Compare record identifiers as well as counts. A three-row export can still contain the wrong three records. Check the column names and order alongside the records so a filtering fix does not quietly change the file format.

The verification plan should also name where the checks will run and what will be saved. Here, engineering can reproduce the failure against the original version in a local test environment, add a regression check that exposes it, then run that check against the changed version. Exercise the export through its normal entry point so the result covers the request and server behavior together. Retain the fixture, version identifiers, command or test procedure, results, and sample CSVs.

Those checks cover the stated export behavior. They do not answer the investigation's separate question about which production users were affected. Keep that question visible if someone still needs to investigate it.

## How StackOS separates these responsibilities

StackOS's [Issue Investigation workflow](/library/workflows/support-issue-investigation/) instructs the agent to read the full support thread, distinguish facts from inferences and eliminated hypotheses, and return a conclusion with evidence and remaining uncertainty. It allows a verified cause, bounded uncertainty with a next diagnostic, or no issue. Its assignment ends with the conclusion; creating delivery tasks and implementing production fixes belong to later work.

The [Delivery Task Handoff workflow](/library/workflows/support-delivery-task-handoff/) checks for a completed conclusion and a current operator instruction in the same support thread. It carries the evidence, scope, acceptance criteria, verification expectations, and residual risk into tracker work, then hands implementation to [engineering delivery](/library/workflows/engineering-tracked-delivery/).

The same-thread requirement belongs to this support method. It tells the handoff agent where to establish the instruction for that issue. The agent still has to interpret the instruction and judge what the evidence supports; a saved reference alone cannot make a diagnosis correct.

## Review the fix against the report

Before accepting the export change, open the original report, the authorized scope, and the verification results together. Confirm that the test actually failed for the reported reason on the original version, then check the output produced by the changed version. Review the neighboring cases as well as the Open filter that first exposed the problem.

A note saying “all tests pass” leaves the reviewer to reconstruct that connection. A record showing the original five-row export, the corrected A1/A2/A3 export, and the unchanged columns makes the result inspectable. It gives the reviewer enough evidence to accept the correction against the behavior the operator asked for.
