---
title: 'How should an AI orchestrator triage feedback?'
description: Work through a mixed review queue to decide which findings need a repair, who should make it, and what to check before calling the work complete.
publishedAt: '2026-07-12'
updatedAt: '2026-10-07'
author: StackOS team
category: AI operations
topics:
  - AI orchestrators
  - feedback triage
  - agent orchestration
readingTime: 6 min read
featured: true
visual: none
searchIntent: Learn how an AI orchestrator should evaluate reviewer feedback without allowing scope drift
relatedWorkflows:
  - branding-content-production
  - engineering-tracked-delivery
relatedAgents:
  - branding-claim-auditor
  - branding-voice-reviewer
  - stackos-sdlc-delivery-reviewer
relatedArticles:
  - how-to-build-ai-agent-workflow
  - ai-agent-experience
  - ai-workflow-automation
---

An unsupported claim and a request for a more dramatic introduction can arrive in the same review. Only one may need to stop the article.

The AI orchestrator has to inspect each finding against the current draft, the original request, and the evidence. Then it can tell the writer what to change, what to leave alone, and what still needs a decision.

## Which comments become work?

Consider a hypothetical article explaining a team's proposed content-review process. The brief asks for a usable explanation and an example. Material claims need support, required links must work, the writing must follow the project's voice, and private details must stay out of the public copy.

The orchestrator has the brief, the current draft, the source notes, and these comments:

::article-evidence-row{label="Unsupported claim" record-heading="Reviewer finding" evidence-label="What the orchestrator checks" decision-label="Decision for this draft"}
#evidence
**“Every claim is checked before publication” has no supporting evidence.**

The source notes assign a separate reviewer to check material claims. They do not show that every claim was checked.

#decision
Admit the finding. The unsupported statement blocks acceptance until it is supported, narrowed, or removed.
::

::article-evidence-row{label="Wrong source link" record-heading="Reviewer finding" evidence-label="What the orchestrator checks" decision-label="Decision for this draft"}
#evidence
**A required source link points to the wrong page.**

Open the destination and compare it with the intended source.

#decision
Assign a local link repair and check the corrected destination.
::

::article-evidence-row{label="Opening preference" record-heading="Reviewer finding" evidence-label="What the orchestrator checks" decision-label="Decision for this draft"}
#evidence
**The introduction would be more dramatic as a personal story.**

Does the current opening leave the reader confused, break the brief, or conflict with the approved voice? In this example, it does none of those.

#decision
Keep this as a preference. The writer does not need to invent a story or reopen the introduction.
::

::article-evidence-row{label="Additional deliverable" record-heading="Reviewer finding" evidence-label="What the orchestrator checks" decision-label="Decision for this draft"}
#evidence
**Add a downloadable checklist.**

Does the article need a separate file to deliver the explanation and example the operator requested? Here, it does not.

#decision
Leave it outside this delivery. A useful additional deliverable still needs its own scope decision.
::

::article-evidence-row{label="Stale finding" record-heading="Reviewer finding" evidence-label="What the orchestrator checks" decision-label="Decision for this draft"}
#evidence
**The draft contains a customer name.**

Search the current candidate and inspect the quoted passage. The reviewer used an earlier version; the name is already gone.

#decision
Reject the finding as stale, with the version and passage checked. Keep the current disclosure review.
::

These decisions use four practical terms: a **blocker** prevents acceptance, a **repair** is work to correct a defect, a **preference** is an optional choice, and **out of scope** describes work beyond the agreed delivery. A blocker can need a small repair. Calling it a blocker does not justify rewriting the whole article.

The labels are part of the method used here. What matters is the reason attached to each decision. “Critical” in a review report gives the orchestrator something to investigate; it does not settle whether the finding is valid.

Keep the original request beside the plan during that check. Suppose the plan omitted the example the operator asked for. A reviewer who points out the missing example has found a defect in the plan too. Correct the acceptance criteria and the article. A plan cannot make an unmet request disappear.

New evidence can also change the decision. A newly identified disclosure risk needs investigation even if nobody anticipated it when writing the brief.

## Follow the unsupported claim through a repair

The first finding in the queue has enough evidence to act on. Give it to the writer with the exact passage, the source mismatch, and a defined check for the return.

For this example, the record could be:

```yaml
candidate: draft-v3
passage: "Every claim is checked before publication."
failed_criterion: "Material claims have support."
evidence: "Source notes specify a review task; no completed review is recorded."
decision: "Admit; this statement blocks acceptance."
repair_owner: writer
repair: "Describe the required review without claiming it has happened."
verification: "Independent claim reviewer compares the revision with the notes."
status: open
```

That request gives the writer room to solve the wording while keeping the factual boundary clear. A suitable replacement would be:

> Assign a separate reviewer to compare material claims with their sources before publication.

The sentence now gives the instruction the notes support. It makes no claim that the team has carried it out. The writer should also read the surrounding paragraph: removing one sentence would be insufficient if the next still promised that the process catches every error.

The claim reviewer checks the revised passage and its context against the notes. The orchestrator then records the version reviewed, the verification result, and whether the finding is resolved. The writer's report that it made the change starts that check; it does not finish it.

Keep the original finding in the record. If the repair fails, the next attempt needs to know which condition remains unmet. Replacing the finding with a vague “improve accuracy” comment would discard the useful part of the review.

## When another round needs a decision

Choose an iteration limit and a fallback before the review loop keeps going. [Anthropic's evaluator-optimizer guidance](https://www.anthropic.com/engineering/building-effective-agents) depends on clear evaluation criteria. Microsoft's [maker-checker guidance](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) calls for acceptance criteria, an iteration cap, and fallback behavior. Set that limit for the task and the consequence of leaving a defect unresolved.

If the chosen limit is reached with an unsupported material claim still present, preserve the latest draft, attempted repairs, evidence, and unresolved criterion. Report that acceptance is still blocked. Writing “unresolved” beside the claim makes its status visible; it does not satisfy the requirement for supported copy.

Bring the operator a decision when the existing instructions cannot resolve it. That might be permission to disclose a detail, a missing fact only they can supply, or agreement to expand the deliverable. Explain [what is blocked and which input is needed](/library/articles/how-ai-agents-should-explain-blockers-to-humans/). If two reviewers merely prefer different introductions and the current one meets the brief, the orchestrator already has enough to proceed.

This is the judgment involved in [AI workflow automation](/library/articles/ai-workflow-automation/). A system can preserve a decision record and control which actions are permitted. The orchestrator still needs to decide whether the evidence warrants the change.

## Close against the revised draft

To finish the hypothetical queue, suppose the claim reviewer confirms that the revised instruction matches the notes and the surrounding paragraph contains no remaining outcome promise. The corrected link opens the intended source. The current candidate also passes the required voice and disclosure checks and contains the usable explanation and example the operator requested.

Record those checks against that candidate and close the two admitted repairs. The stale finding has its rejection reason. The more dramatic opening remains a preference, and the separate checklist remains outside the delivery.

The article is ready under the agreed criteria. The review record still contains ideas that were not used.
