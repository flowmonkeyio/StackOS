---
title: How an AI agent can explain a blocker without assigning the repair
description: A case-led way to report one recoverable AI agent authority interruption without assuming who can make the repair.
publishedAt: '2026-07-27'
updatedAt: '2026-07-27'
author: StackOS team
category: AI operations
topics:
  - AI agent blockers
  - human-AI interaction
  - workflow recovery
  - agent authority
readingTime: 6 min read
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

In the observed run, an evidence write stopped because its active-step grant was unavailable. The record named no authorized repair owner. One fact identifies the interrupted action; the other leaves open who, if anyone, can change the state that blocked it.

For that occurrence, the report can carry both facts without assigning the repair to its recipient.

> **Blocked:** The current record shows the evidence write lacks an active-step grant and names no authorized repair owner. If you are authorized to claim `research-fact-collection`, claim it; otherwise use an approved route if one exists, or return ownership unresolved. I will retry the write only after the step is actually claimed and `resource.upsert` is observed available.

## The condition is known; the owner is not

The first sentence keeps the interruption narrow. The action waiting to run is the evidence write, and the condition attached to it is a missing active-step grant. It makes no assumption about a person's role, permissions, or ability to alter workflow state.

The middle sentence is the decision request. It offers a conditional action to a recipient who can make the claim. Where an approved route exists, the request can move through it. Otherwise, the recipient can return ownership unresolved. Neither response assigns the repair to the recipient by default.

Microsoft’s [Guidelines for Human-AI Interaction](https://www.microsoft.com/en-us/research/wp-content/uploads/2019/01/Guidelines-for-Human-AI-Interaction-camera-ready.pdf) include explanations of why an AI system behaved as it did and support for correction or recovery. GOV.UK’s [error-message guidance](https://design-system.service.gov.uk/components/error-message/) asks services to state what happened and how to fix it. In this case, the explanation is the unavailable grant; the next decision is whether the recipient can take the conditional action or route it.

The message intentionally says nothing about prior research or source notes. The record at hand establishes an unavailable write and a later available grant, rather than the state of earlier material. When retained work is known, it can be named. When the record is silent, the report leaves it silent.

## A reply is a signal; state confirmation comes later

“I can claim the step” is an ownership signal. It does not show that the claim has occurred or that the write tool is now available. In this proposed method, the agent inspects the active state before returning to its interrupted action.

One StackOS receipt gives the relevant sequence: before the research step was claimed, `resource.upsert` was unavailable; after it was claimed, `resource.upsert` appeared granted in the active-step tool list. The record reaches no farther. It does not identify the repair owner or show the result of a retry.

[RFC 9457](https://www.rfc-editor.org/rfc/rfc9457.html) offers a structural analogy for this handoff point. Its problem-details model separates a problem from occurrence-specific detail and directs detail toward correction rather than debugging information. This proposed report makes an editorial choice to identify the grant state, the conditional decision, and the verification point without expanding into raw payloads, tokens, credential references, private paths, or security-sensitive configuration.

The recipient may provide an ownership signal, use an approved route when one exists, or return ownership unresolved; the agent verifies that the step is claimed and `resource.upsert` is available, then retries the evidence write.
