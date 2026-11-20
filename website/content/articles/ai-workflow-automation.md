---
title: 'AI workflow automation: automate the rules, not the judgment'
description: Decide which workflow checks belong in code, which need an agent to interpret evidence, and when a person must supply missing intent or permission.
publishedAt: '2026-07-12'
updatedAt: '2026-10-03'
author: StackOS team
category: AI operations
topics:
  - AI workflow automation
  - agent orchestration
  - workflow architecture
readingTime: 5 min read
featured: true
visual: none
searchIntent: Learn how to automate AI workflows without hard-coding the judgment they need
relatedWorkflows:
  - branding-content-production
  - engineering-tracked-delivery
relatedAgents:
  - branding-channel-strategist
  - branding-narrative-writer
  - branding-claim-auditor
relatedArticles:
  - how-to-build-ai-agent-workflow
  - ai-agent-experience
  - what-is-an-agentic-workflow
---

Marking research complete can release the next agent to start writing. An unchecked completion flag can send it a result that is missing required material.

Before automating that transition, decide what must be checked and who can check it. Required fields and allowed actions can be enforced in code. An agent must interpret the evidence. A person may need to supply a choice or permission the workflow does not yet have.

## Validate the result before advancing the step

A July 2026 StackOS content run exposed two problems in this boundary. Current workflow instructions could coexist with older cached plugin and resource contracts. The system also accepted successful step results without checking them against the step's output schema: the required fields, types, and structure.

The repair changed how the runtime refreshed those contracts when their source generation changed. It also added validation against the step's saved output schema before recording the state transition. In the live check of that July repair, a malformed result was rejected and the step stayed running. A corrected result then completed it.

Those responsibilities belong in the code that accepts a result. A prompt asking for every required field leaves the runtime free to accept an incomplete response. Validation has to happen before the workflow records success and releases dependent work.

For your own workflow, define the required output when you [design the step](/library/articles/how-to-build-ai-agent-workflow/). Then try submitting a result with a required field missing. The system should identify that field and leave the step incomplete. This gives you a concrete way to check whether the completion rule is being enforced.

## A valid source field still needs an evidence decision

Consider an illustrative research result with every required field present. It includes a proposed claim and a source reference. The source covers one tested configuration; the claim covers all configurations.

The structural check can pass while that mismatch remains. The agent assigned to evidence review needs to read the source in context and decide how far its support extends. Its return should identify the claim, the relevant source passage, and the decision: use a narrower statement, obtain more support, or leave the claim unresolved.

A schema can require a source ledger; it cannot decide which source resolves a contradiction. Make the evidence decision an explicit responsibility, with enough reasoning for the orchestrator to inspect. The runtime can require the review result before the next step proceeds. The orchestrator still has to determine whether the supported version serves the original request.

You can check this boundary separately from missing-field validation. Give the reviewer a structurally valid result containing the configuration mismatch above. A useful return names the unsupported scope and what must change. Returning another well-formed packet that simply says “supported” would leave the evidence decision unaccounted for.

The orchestrator's decision about [which reviewer findings become work](/library/articles/how-ai-orchestrators-triage-feedback/) belongs with this interpretation too. That guide develops the feedback decision without turning every comment into another delivery cycle.

These responsibilities can sit inside a straightforward sequence. [Anthropic's architecture guide](https://www.anthropic.com/engineering/building-effective-agents) distinguishes predefined workflow paths from agents that direct their process and tool use during the task. Microsoft's [orchestration guidance](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) recommends the least complex approach that meets the requirements. For this example, research, review, and drafting can run in a fixed order while the review itself requires judgment about the sources.

## Use the authority already supplied

Now suppose the draft includes a customer detail whose public use has not been authorized. The agent should identify the disclosure decision needed from the appropriate owner and keep that detail out of public output while it is unresolved. A relevant source and a completed research step supply no permission to disclose it.

The workflow should make the missing decision specific enough for that person to answer. Which detail would be shared, with whom, and for what purpose? Keep the request within the permitted private context. A broad “approve the workflow” question would leave the actual disclosure choice unclear.

An action and destination that the operator has already authorized require a different response. The agent should check that the planned action fits that authority and proceed when the required conditions are met. Preserve any approval that project policy requires; do not insert an additional confirmation merely because an agent performs the step.

Check both cases when evaluating the design. The case with missing disclosure permission should prevent the public action and identify the decision still needed. The already-authorized case should use the supplied permission, stay within its scope, and return evidence of the required result. If that case asks for the same permission again without a policy requirement, the workflow is ignoring a decision the operator has already made.
