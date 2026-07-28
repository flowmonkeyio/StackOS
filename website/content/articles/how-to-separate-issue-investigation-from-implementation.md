---
title: How to separate issue investigation from implementation
description: A record-based way to keep a persuasive issue diagnosis from silently becoming an unapproved engineering change.
publishedAt: '2026-07-28'
updatedAt: '2026-07-28'
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

An investigation conclusion can be persuasive enough to make the next move feel obvious. The evidence points to a likely cause. Some alternatives have been eliminated. A fix direction even seems to suggest itself.

That still does not answer the delivery question: should this change be made, by whom, with what scope, and against which acceptance and verification conditions?

For the current StackOS support-to-delivery contract, those are different records with different decision rights. The investigation records what the evidence supports. An operator instruction authorizes delivery work. Engineering takes ownership after that handoff has created delivery-ready state. When evidence remains incomplete, the investigation can close as `bounded_uncertainty` only with its limit and next diagnostic named. That conclusion still leaves the delivery choice with the operator.

The distinction is small in prose and consequential in the record. Without it, a diagnosis can quietly acquire scope, authority, and implementation commitments that nobody explicitly chose.

## Let the investigation stop at what it knows

In the current StackOS method, an investigation record carries the reported and expected behavior, gathered evidence, reproduction work, affected scope, facts, inferences, eliminated hypotheses, and residual uncertainty. Its purpose is to establish the condition under investigation and the limit of the evidence.

The current StackOS investigation workflow makes that boundary explicit. It can conclude with a verified cause, `bounded_uncertainty`, or no issue. A `bounded_uncertainty` conclusion names the unresolved limit and the next diagnostic step; the workflow then returns the decision about delivery rather than creating a task or changing production behavior itself.

That produces a useful conclusion without converting a likelihood into a plan. “The evidence supports this likely failing path; this observation remains unconfirmed” belongs in the investigation record. “Implement this correction” requires a new decision.

Verified root cause is one conclusion state, not a prerequisite for closing an investigation. `Bounded_uncertainty` can lead to more diagnostics, a decision to leave delivery unopened, or a later operator decision to create narrowly scoped work. The conclusion carries the uncertainty forward; it does not choose among those responses.

## Authorization changes the kind of work being done

The next record states that an accountable operator has decided to create delivery work from the conclusion. In the current StackOS handoff contract, task creation requires both a completed conclusion and a current instruction in the same canonical thread. The instruction supplies authority for a different action; it adds no new evidence about the cause.

The operator can request another diagnostic, leave product work unopened, or authorize a delivery task that preserves uncertainty in its context. The investigation record supports those choices; it does not select one by sounding compelling.

The handoff changes the active question from “What do we know?” to “What work is authorized now?” It preserves the evidence trail and the operator’s decision for the record that follows.

## Delivery begins when implementation can be evaluated

Within the current StackOS delivery contract, engineering begins from a record that carries the conclusion and its limits alongside affected surfaces, an authorized direction, scope, acceptance criteria, verification expectations, dependencies, and residual risk. Those fields let engineering evaluate an implementation decision instead of reconstructing one from a support conclusion.

Its delivery-handoff workflow preserves the conclusion and safe thread references, creates tracker work after the same-thread instruction, and then hands implementation to `engineering.tracked-delivery`. That is current StackOS contract evidence: it establishes this system’s boundary without supplying adoption or outcome evidence.

The FDA-published [MDSAP nonconformity and corrective-action procedure](https://www.fda.gov/medical-devices/medical-device-single-audit-program-mdsap/mdsap-qms-p0009-nonconformity-and-corrective-action-procedure) shows a similar record boundary in a medical-device quality-system context. Cause investigation precedes the development and implementation of corrective action, while action planning records roles, resources, acceptance criteria, and verification or validation. It is a domain-specific example, not general operating or legal guidance; its value here is to show that a finding about cause and a planned corrective action carry different obligations.

## Urgent containment is a separate lane

A cause-first sequence cannot cover every response that may be required while evidence is still developing.

[NIST CSF 2.0](https://tsapps.nist.gov/publication/get_pdf.cfm?pub_id=957258) treats incident analysis, mitigation, and recovery as distinct outcomes while allowing related functions to occur concurrently. WHO’s complaints-handling guidance for manufacturers of prequalified in vitro diagnostics likewise describes circumstances in which corrective action may be needed before definitive root-cause identification. Together, those examples show that a response record can run alongside investigation without turning an unverified hypothesis into a corrective plan.

When containment is warranted, its authority, scope, and verification belong in a separate record. It can run concurrently with investigation. A root-cause corrective implementation makes a different claim: that the selected change addresses the cause and can be evaluated against its acceptance conditions.

The current StackOS evidence used here establishes the normal boundary between investigation, same-thread operator authorization, and later engineering delivery. It contains no emergency-containment contract, so that remains an open design question rather than a current capability.

## Inspect the authority boundary, not the confidence of the conclusion

The next useful test is a future one: can a reviewer find a distinct investigation conclusion, authorization, and delivery record—and, when urgent containment occurs, identify its separate authority without backfilling root-cause certainty into the plan?
