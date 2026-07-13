---
title: 'AI workflow automation: automate the rules, not the judgment'
description: A practical way to decide what AI workflows should enforce in code, what agents should decide at runtime, and when a person genuinely needs to step in.
publishedAt: '2026-07-12'
updatedAt: '2026-07-12'
author: StackOS team
category: AI operations
topics:
  - AI workflow automation
  - agent orchestration
  - workflow architecture
readingTime: 8 min read
featured: true
visual: connections
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

AI workflow automation combines two different kinds of control. Code should enforce the rules that must hold every time: state, dependencies, permissions, schemas, receipts, and stopping conditions. An agent should decide what cannot be known until the work is underway: which evidence matters, whether the plan should adapt, and which feedback belongs in the result.

Treating the whole workflow as either a fixed automation or an autonomous agent misses the useful middle. The practical design question is not, “How much AI can we add?” It is, “Which decisions should still require judgment?”

::article-concept-visual{mode="connections" title="One workflow, three kinds of control" caption="The runtime enforces invariants. Agents make evidence-dependent decisions. People enter for missing intent or consequential choices."}
::

## A failure that a better prompt could not fix

We ran into a useful example while refining our own content workflow.

The workflow definition had changed, but part of the running system still held an older generation of the plugin and resource contracts. The agent could see the current content workflow while receiving an older schema for the record it needed to write. One part of the system described the right job; another enforced the wrong shape.

There was a second gap. Each workflow step already declared an output contract, but a step could still be recorded as successful without proving that its result matched that contract.

Neither problem belonged in the prompt. Telling the agent to “use the latest schema” would not make two runtime generations consistent. Telling it to “return every required field” would not make success verifiable.

We moved both responsibilities into the mechanical layer. Editable plugin manifests now carry a source-generation fingerprint, and the catalog resynchronizes when that generation changes. Successful step results are validated against their frozen JSON Schema before any lifecycle transition is persisted. If a required field is missing, the step remains running and the error points to the exact field that needs repair.

We then exercised the failure path live: an incomplete result was rejected without advancing the step, and the corrected result completed it. This is bounded first-party evidence from one implementation, not proof that the same architecture fits every system. The lesson is narrower: if correctness depends on an invariant, the system should enforce it without asking the model to remember.

## Put stable rules in the mechanical layer

[Anthropic distinguishes workflows from agents](https://www.anthropic.com/engineering/building-effective-agents) by who controls the path: workflows use predefined code paths, while agents dynamically direct their process and tool use. Microsoft’s [orchestration pattern guidance](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) and Google Cloud’s [agentic design-pattern guide](https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system) make a similar distinction between predictable sequences and dynamic orchestration.

That distinction is useful inside one workflow, not only when choosing an architecture for the whole system.

The mechanical layer should own conditions whose answer does not improve with another model call:

- A dependent step cannot start before its prerequisite finishes.
- A research step can read sources but cannot publish.
- A successful result must contain the fields and types its consumer expects.
- A completed external action needs a receipt the workflow can inspect before retrying.
- A packet-only request must stop before publication.
- The current state and evidence refs must survive the conversation that produced them.

These controls reduce ambiguity without reducing useful autonomy. The agent no longer spends judgment on whether a required field is optional this time or whether “packet only” might permit a deployment. It can use that judgment on the work itself.

## Keep evidence-dependent choices with the agent

Some decisions look repetitive but do not have a stable answer.

In this article’s workflow, the interview step was always present, but the interview was not mandatory. The agent had to inspect the current voice guide, prior pieces, recorded operator statements, and the new topic. Those sources already captured the relevant judgment, so the interview was skipped with a reason and a list of perspectives the article could not claim.

Hard-coding “always interview” would add ceremony. Hard-coding “never interview” would remove a source when first-hand experience was actually missing. The stable rule is that the decision must be made and explained. The answer remains contextual.

The same boundary applies to research and review. A schema can require a source ledger; it cannot decide which source resolves a contradiction. A workflow can require independent claim and voice review; it should not automatically turn every reviewer preference into another delivery cycle.

The orchestrator owns that gate. It can accept a supported blocker, send a specific defect back for repair, retain a preference as advice, and reject an unsupported or out-of-scope finding. Review produces evidence. It does not acquire authority over the original goal merely because it happened later.

This is where an [AI agent workflow](/library/articles/how-to-build-ai-agent-workflow) differs from a long automation script. The contract defines the operating boundary. Reasoning handles the parts whose answer depends on meaning, evidence, or changed conditions.

## One workflow can mix all three control modes

Our content workflow follows a visible sequence: decide on interview scope, collect evidence, choose an angle, draft, review claims and voice, check disclosure risk, render the selected channel packet, preserve the final record, and stop or publish according to the request.

The sequence and handoffs are deterministic. The work inside them is not.

| Responsibility | Best owner | Why |
| --- | --- | --- |
| Require a source ledger and claim map | Runtime contract | The requirement is stable and machine-checkable. |
| Decide whether existing evidence is sufficient | Agent | The answer depends on the topic, source quality, and claims being considered. |
| Prevent drafting before research completes | Workflow state | The dependency should hold on every run. |
| Choose the article angle | Agent | Reader value and evidence fit require interpretation. |
| Decide whether a review finding changes delivery | Orchestrator | The finding must be tested against the accepted goal and evidence. |
| Prevent a packet-only run from publishing | Runtime contract | Execution intent is an explicit boundary, not a suggestion. |
| Clarify a missing destination or sensitive disclosure choice | Person | Guessing would materially change the requested action or public boundary. |

The person is not a rubber stamp between every row. Human participation belongs where the system lacks legitimate authority or information: the goal is ambiguous, evidence conflicts on a consequential point, disclosure ownership is unclear, or an external action needs a choice that was never supplied.

That is different from inserting approval because AI is involved. A mandatory checkpoint can be appropriate for a risky action. It is not a substitute for a clear workflow.

## Use failure location to improve the boundary

When an AI workflow fails, ask which layer was forced to compensate.

If the agent searched for a source the workflow already knew, context selection failed. If it guessed which account or destination to use, authority was unresolved. If it returned a malformed packet and the system accepted it, validation failed. If a reviewer’s optional rewrite expanded the task, feedback adjudication failed. If the system stopped on missing operator intent and asked one focused question, the boundary may have worked exactly as intended.

This makes ordinary friction useful evidence. A workflow does not need to eliminate every search, retry, or clarification. It needs to keep recovery local and prevent that friction from turning into silent drift.

The [agent experience](/library/articles/ai-agent-experience) article develops that point at the claimed-step level. The automation boundary is the system-level version: decide which uncertainty the agent should resolve and which uncertainty the system should remove before the step begins.

## A compact automation-boundary test

For each responsibility in a workflow, ask:

1. Does the rule need to hold on every valid run?
2. Can the result be checked without interpreting meaning?
3. Does the answer change with evidence or intermediate results?
4. Would a wrong guess change scope, expose data, spend money, or create an external side effect?
5. Can a failed attempt be repaired locally without replaying completed work?

Stable and machine-checkable responsibilities belong in code, schemas, state transitions, or scoped tool grants. Evidence-dependent responsibilities belong with a reasoning agent. Missing authority or a genuinely consequential choice belongs with the person who owns it.

The boundary will not be perfect on the first run. Ours was not. The useful signal was that a live failure identified two invariants the runtime had left to convention. We fixed those invariants mechanically and kept the editorial decisions with the agents.

That is the point of AI workflow automation: not to automate judgment away, but to stop wasting it on rules the system can already know.
