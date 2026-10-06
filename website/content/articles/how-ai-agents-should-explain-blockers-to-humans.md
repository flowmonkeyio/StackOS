---
title: How an AI agent can explain a blocker without assigning the repair
description: Use a short blocker message to explain the waiting action, ask for help within the recipient's authority, and show what the agent must check before continuing.
publishedAt: '2026-07-27'
updatedAt: '2026-10-03'
author: StackOS team
category: AI operations
topics:
  - AI agent blockers
  - human-AI interaction
  - workflow recovery
  - agent authority
readingTime: 3 min read
featured: false
visual: none
searchIntent: See how an AI agent can explain one recoverable blocker without assuming who owns the repair
relatedWorkflows:
  - branding-content-production
  - engineering-tracked-delivery
relatedAgents:
  - branding-narrative-writer
  - branding-claim-auditor
  - branding-sanitization-reviewer
relatedArticles:
  - how-ai-orchestrators-triage-feedback
  - ai-agent-experience
  - what-ai-agent-handoff-should-include
---

Receiving a blocker message does not make you responsible for the repair. It should tell you which action is waiting, why, and what help the agent actually needs.

If the agent already has the authority and context to recover, it should use that permitted path and continue. When it needs someone else's action or a decision about authority, a proposed message could read:

> I can't save the evidence record yet: the required write permission is unavailable. If you're authorized to start the research step, please start it. Otherwise, use the established route for this request if one exists, or let me know that ownership is still unresolved. I'll check the active step and write permission before attempting to save the record.

## Make the request conditional on authority

If they cannot act, an established route may lead to someone who can. Where no such route is known, leave the ownership question open. Asking the recipient to identify that gap is more precise than assigning them a repair they may have no way to perform.

GOV.UK separates these cases in its [error-message guidance](https://design-system.service.gov.uk/components/error-message/). Validation messages should explain a problem the user can correct. Permission or service problems the user cannot fix need an explanation and useful information about what happens next. This is service-design guidance; applying the distinction here means checking what the recipient can actually do before asking them to do it.

## Check the state before continuing

In a July 2026 StackOS run, a toolbox inspection showed the evidence-write operation unavailable before the research step was claimed. After the claim, another inspection listed the operation as granted. The receipt records that change in tool availability. It does not show a write attempt or a saved-record result, and it identifies no repair owner.

That observation gives the proposed message a concrete restart check. A reply saying someone can claim the step is an ownership signal. The agent still needs to inspect which step is active and whether the required write operation is available.

For an agent continuing this task, the procedure would be to verify those conditions, attempt the authorized write, then inspect the returned result and retrieve the record to check its contents before reporting it saved. Each check answers the next practical question: can the operation run, did it return success, and does the saved record contain the intended evidence?

If another agent takes over, include the waiting action and its unmet condition in the [handoff](/library/articles/what-ai-agent-handoff-should-include/). If permission or ownership remains unresolved, leave the evidence write waiting with the specific condition still needed to continue.
