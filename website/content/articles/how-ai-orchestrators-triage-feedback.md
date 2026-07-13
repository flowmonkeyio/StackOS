---
title: 'How should an AI orchestrator triage feedback?'
description: A practical way for AI orchestrators to separate blocking findings from repairs, preferences, and scope drift without weakening review.
publishedAt: '2026-07-12'
updatedAt: '2026-07-12'
author: StackOS team
category: AI operations
topics:
  - AI orchestrators
  - feedback triage
  - agent orchestration
readingTime: 8 min read
featured: true
visual: roles
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

An AI orchestrator should treat reviewer feedback as evidence, not as an instruction. Its job is to test each finding against the accepted goal, evidence, constraints, and terminal condition, then classify it as a blocker, a repair, a preference, or out of scope. Only findings that protect the agreed outcome should enter delivery.

That gate does not weaken review. It gives review a boundary. Without one, a capable reviewer can always imagine another improvement, and a workflow that accepts every suggestion can keep moving without getting closer to done.

## Why reviewer output is not automatically work

A reviewer has a deliberately narrow responsibility. A claim auditor looks for unsupported claims. A security reviewer looks for unsafe behavior. An editor looks for structural and voice problems. That narrowness makes the review useful, but it does not give the reviewer ownership of the whole plan.

The orchestrator has the wider view. It knows what the operator asked for, which constraints were accepted, what evidence is available, which changes have already been made, and what state counts as complete. It should consider a reviewer’s finding from that position.

We learned this while refining our own workflows. Independent reviews surfaced plausible improvements, and it was tempting to treat each one as a new requirement. The result was scope drift: work expanded beyond the plan we had agreed to finish. The individual suggestions were not necessarily bad. The failure was allowing the act of suggesting something to redefine delivery.

The correction was not to remove reviewers or make their instructions weaker. It was to make one responsibility explicit: reviewers report findings; the orchestrator decides which findings belong in the current delivery.

[Anthropic’s evaluator-optimizer pattern](https://www.anthropic.com/engineering/building-effective-agents) makes evaluation criteria a condition for a useful review loop. Microsoft’s [maker-checker guidance](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) similarly calls for clear acceptance criteria, an iteration cap, and defined fallback behavior. A review loop is controlled by criteria and a stopping rule, not by the mere existence of more feedback.

## Start with an accepted plan

Feedback cannot be triaged against a vague intention such as “make it better.” The orchestrator needs a small accepted plan that remains stable while the work is underway.

At minimum, that plan should name:

- the problem being solved;
- the requested output and its scope;
- the evidence and constraints that apply;
- the acceptance criteria;
- the terminal condition;
- any decisions reserved for the operator.

This is not a demand for a long specification. A compact plan is often better because the orchestrator can apply it consistently. The important part is that a reviewer finding must point to something in that plan if it is going to change delivery.

Suppose the accepted outcome is a public article with supported material claims, the required sections, the current brand voice, and no sensitive internal details. “A material claim has no source” threatens an acceptance criterion. “The introduction would be more dramatic as a personal story” may be a reasonable editorial preference, but it does not necessarily threaten the outcome.

The orchestrator should not pretend those findings have equal weight.

## Use four feedback classes

The following classification is a practical operating model, not an industry standard. Its value is that every category has a different consequence.

| Class | What it means | Orchestrator action |
| --- | --- | --- |
| Blocker | The output cannot meet an accepted criterion, is materially false or unsafe, or violates a hard constraint. | Admit it into delivery and prevent completion until it is resolved or explicitly marked unresolved. |
| Repair | A bounded correction is needed inside the agreed scope. | Route it to the responsible agent with the failed criterion and expected evidence. |
| Preference | The suggestion is defensible, but the current output can meet the accepted outcome without it. | Record it if useful; do not reopen delivery by default. |
| Out of scope | The suggestion changes the goal, adds a new capability, or introduces work not required by the accepted plan. | Reject it for this run or return it as a separate proposal. Do not create follow-up work automatically. |

The distinction between a blocker and a repair is useful. A blocker describes the state of the output. A repair describes bounded work that may remove the blocker. Keeping them separate prevents a reviewer from prescribing a large solution when a smaller correction would satisfy the criterion.

Preferences also deserve an explicit category. Otherwise they tend to masquerade as defects. A preference can still be valuable, but “valuable” is not the same as “required now.”

## Make the triage decision inspectable

The orchestrator does not need a complicated scoring system. It needs a repeatable sequence that exposes why a finding was accepted or rejected.

1. **Restate the finding as a testable claim.** Replace “this section is weak” with the specific condition the reviewer believes has failed.
2. **Identify the affected criterion.** Ask which accepted requirement, constraint, or terminal condition is threatened.
3. **Check the evidence.** Confirm that the finding refers to the current output and has enough evidence to justify action.
4. **Classify the finding.** Choose blocker, repair, preference, or out of scope.
5. **Choose the smallest valid action.** Admit a blocker, route a repair, record a preference, or reject scope expansion.
6. **Re-evaluate completion.** Decide whether the terminal condition is still unmet after the admitted findings are considered.

A useful finding record can stay compact:

```yaml
finding: "Two factual claims have no source"
criterion: "Material claims are supported or marked unresolved"
evidence: ["paragraph-6", "paragraph-9"]
classification: blocker
action: "Return to the writer for a bounded evidence repair"
status: admitted
```

The important field is not a severity score. It is the connection between the finding and the accepted plan.

## Keep judgment with the orchestrator and invariants in code

Feedback triage contains both mechanical and judgment-dependent work.

Code can require every finding to contain a criterion, evidence reference, classification, and status. It can prevent a dependent step from starting while an admitted blocker remains open. It can enforce an iteration limit and preserve the decision record.

Code cannot reliably decide whether a new suggestion protects the operator’s goal or quietly replaces it. That depends on the current evidence, tradeoffs, and intent. The orchestrator should make that judgment.

This follows the same boundary described in [AI workflow automation](/library/articles/ai-workflow-automation): enforce stable rules mechanically, but leave evidence-dependent choices with the agent responsible for the plan.

The reviewers should also remain independent. The writer should not silently grade its own claims, and the orchestrator should not rewrite findings to make them easier to dismiss. Specialists report what they observe. The orchestrator owns admission into delivery.

## Do not turn every disagreement into human approval

Human input is useful when the workflow lacks something only the operator can supply: intent, authority, a disclosure decision, acceptance of consequential risk, or a real change in scope.

It is not necessary merely because two agents disagree. If the accepted plan already resolves the disagreement, the orchestrator should apply it. Requiring a person to approve every classification would move the gate without improving the decision.

Microsoft’s orchestration guidance distinguishes feedback that loops work back for refinement from approval that advances a workflow. That distinction matters. A content preference is not an authorization decision. A sensitive external action may be.

When a finding would materially change the accepted plan, the orchestrator should stop and present the choice rather than assume permission. That is not routine review. It is a new operator decision.

## Stop when the accepted outcome is reached

A workflow should finish when its terminal condition is true and no admitted blocker remains open. It should not wait until reviewers have no further ideas.

This gives the orchestrator a concrete stopping test:

- required output exists;
- acceptance criteria pass;
- material claims have evidence or an explicit unresolved status;
- admitted blockers are repaired or deliberately escalated;
- required safety and sanitization checks pass;
- preferences and out-of-scope suggestions are not blocking delivery;
- the iteration limit has not been exceeded.

If the iteration limit is reached with a blocker still open, the workflow should return the best preserved state, the failed criterion, and the next safe action. It should not hide the problem behind another review round.

The broader lesson is simple: feedback improves a workflow only when someone owns the decision to use it. In an [AI agent workflow](/library/articles/how-to-build-ai-agent-workflow), that owner is the orchestrator. Reviewers protect specific quality boundaries. The orchestrator protects the agreed outcome.
