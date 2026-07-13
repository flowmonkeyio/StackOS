---
title: 'Agent experience: the missing layer in AI agent orchestration'
description: AI agent orchestration is experienced one claimed step at a time. Here is how context, authority, tools, recovery, and stopping rules shape whether an agent can execute.
publishedAt: '2026-07-11'
updatedAt: '2026-07-11'
author: StackOS team
category: AI operations
topics:
  - AI agent orchestration
  - agent experience
  - workflow design
readingTime: 9 min read
featured: true
visual: roles
searchIntent: Understand how to design the operating experience inside AI agent orchestration
relatedWorkflows:
  - branding-content-production
  - engineering-tracked-delivery
relatedAgents:
  - branding-narrative-writer
  - branding-claim-auditor
  - branding-voice-reviewer
relatedArticles:
  - ai-agent-vs-workflow-vs-orchestrator
  - what-is-an-agentic-workflow
  - how-to-build-ai-agent-workflow
---

An orchestration plan can be logically correct and still fail at the moment an agent receives its next step. The workflow says “draft the article,” but the agent must discover the brief, infer the audience, locate the allowed tools, reconstruct prior decisions, decide what completion means, and determine where the result belongs.

That gap lives in the agent’s operating environment at the point of action.

::article-concept-visual{mode="roles" title="The workflow an agent actually receives" caption="Orchestration is experienced one claimed step at a time: context, authority, tools, outputs, recovery, and a stopping rule."}
::

Agent experience is the per-step operating layer that makes an orchestration plan executable for a fresh agent. Operationally, it is the burden a system places on that agent to search, guess, rediscover, and recover before it can perform valid work.

This definition keeps the idea concrete. Search burden is time spent locating relevant state or instructions. Guessing burden appears when inputs, authority, or completion criteria are ambiguous. Rediscovery burden comes from reconstructing decisions the system already knows. Recovery burden is the work required to understand and repair a failed attempt.

These burdens are separate from the distinctions among an [AI agent, workflow, and orchestrator](/library/articles/ai-agent-vs-workflow-vs-orchestrator). Those components may all be present while the claimed step remains difficult to execute.

## An assignment is not yet an executable step

“Review the implementation” is an assignment. It identifies an activity but leaves most operating questions unanswered.

Which implementation? Review against which requirements? May the reviewer run tests, inspect external systems, or modify files? What evidence should the review produce? Does “ready” mean no defects, no blocking defects, or acceptance of documented risk? If the review finds a problem, who receives it and in what form?

A capable agent can often fill these gaps. That is precisely the problem: successful execution now depends on inference that the orchestration system could have resolved before dispatch.

An executable step should let an agent move from claim to first valid action without rebuilding the workflow in its own context window. This is especially important in an [agentic workflow](/library/articles/what-is-an-agentic-workflow), where later actions depend on runtime findings. Dynamic behavior increases the need for explicit local operating conditions; it does not remove it.

The claimed step is therefore the useful unit for examining agent experience.

## The claimed-step packet

When an agent claims a step, it should receive a bounded packet containing what that step needs now. This prepared execution surface selects from project state instead of passing all of it through.

A useful packet includes:

- **Purpose:** why the work exists and what downstream decision or action it supports.
- **Instructions and policies:** the relevant rules, already selected for this step.
- **Completion criteria:** observable conditions that distinguish finished work from plausible-looking progress.
- **Exact tools:** callable operations, expected use, and important limitations.
- **Resolved inputs:** concrete artifact references, resource identifiers, prior outputs, and configuration values rather than instructions to “find the latest.”
- **Bounded context:** enough history to understand the work without replaying the whole run.
- **Output contracts:** required fields, artifact formats, evidence, and status semantics.
- **Direct handoff:** where the result goes and what the next actor needs from it.
- **Targeted recovery:** likely failure modes and the next safe action for each one.
- **A stopping rule:** when to return control instead of expanding the task.

These fields turn hidden orchestration knowledge into local operating knowledge.

The distinction between resolved inputs and broad context matters. A packet should say which approved brief to use, not provide a folder and ask the agent to identify the authoritative version. It should name the relevant test command, not merely mention that tests exist. It should link a predecessor’s accepted artifact, not force the agent to search conversation history for the last apparently complete draft.

Context is useful when it reduces uncertainty. Beyond that point, it becomes another search surface.

The same principle applies to recovery. “Retry if needed” transfers diagnosis back to the agent. A targeted recovery hint might instead say that a truncated handoff can be retrieved with one exact read, that an unavailable credential requires returning the step with a specific reason, or that a validation failure should go back to the producing step with the failed criterion attached.

A good recovery path narrows the next decision without pretending every failure can be anticipated.

## Authority should match the active step

Instructions alone do not define what an agent can do. The executable step also needs an authority boundary.

A practical model grants tools and resources when the step becomes active, scoped to the operations and inputs required for that step. Completion, cancellation, or release of the step ends that authority. The agent does not need ambient access to every workflow capability, and it should not have to discover whether a documented operation is actually permitted.

The relationship should be legible: purpose leads to a permitted action, the action produces required evidence, and the evidence has a defined handoff.

If any link is missing, the agent must guess. If tools are described but unavailable, it enters recovery before substantive work begins. If broad tools are available without a step-level purpose, the system invites drift.

Approval can be part of this boundary when risk or organizational policy calls for it, but approval is not inherently required for every step. The important property is that authority is explicit, scoped, and legible to the acting agent.

## Review feedback does not own delivery

Independent review is often represented as a simple gate after production. In operation, the reviewer also needs a claimed-step packet: the artifact under review, the governing criteria, relevant evidence, permitted verification tools, and a structured way to report findings.

An independent reviewer still needs the relevant context. It evaluates the work against declared criteria rather than inheriting the producer’s conclusions as facts.

The orchestrator has a different responsibility: adjudicating what the findings are allowed to change. A supported blocker can stop progression. A specific repair can return to the producing step. A preference can remain advice. An unsupported or out-of-scope finding should not expand the delivery.

This is a feedback gate, but not a ritual approval step. It protects the agreed goal from review-driven drift while preserving independent scrutiny where it matters.

## A bounded observation from one StackOS replay

One recent StackOS cold-start replay provides a small implementation example. It should be read as first-party operating evidence, not as a productivity benchmark or universal proof.

During that replay, a draft specialist reported that it had used no tools and made no guesses while completing its assigned work. That report records the agent’s operating behavior alongside the artifact. We did not run a comparative test to isolate why it needed no additional discovery.

Another step received a truncated dependency handoff. Instead of searching broadly, the agent followed the packet’s targeted recovery hint and retrieved the one complete prior-step result it needed. Near the end of the run, the final stopping rule kept the active agent from starting another workflow, changing the workflow setup, or creating unrelated content after the requested outcome had been reached.

The run still contained ordinary latency and friction. Agents had to process context, produce outputs, and move through orchestration boundaries. One fresh subagent missed its bounded execution window. The observation establishes neither lower overhead nor gains across models or workflows.

Within that boundary, the replay records three useful behaviors: one specialist reported no guessing, targeted recovery constrained the response to a partial handoff, and an explicit stopping rule limited drift.

## The vague cold-start test

A direct way to inspect agent experience is to remove accumulated familiarity.

Give a fresh agent the kind of vague request a real operator might provide. Do not give it the workflow key, the design rationale, or a warm context window containing earlier exploration. Then observe whether the system helps it discover the right workflow and whether the claimed step answers these questions:

1. What is the first valid action?
2. Which exact inputs should be used?
3. Which tools are permitted and available?
4. What observable criteria define completion?
5. What must be produced, and where does it go?
6. What should happen if the expected path fails?
7. When must the agent stop and return control?

The test exposes friction when the agent must search broadly for policy, infer which artifact is authoritative, guess whether it has permission, recreate prior decisions, or invent a recovery strategy. Those behaviors may still produce a successful result, but they reveal orchestration work leaking into the execution step.

Teams can apply the same test while following a practical guide to [building an AI agent workflow](/library/articles/how-to-build-ai-agent-workflow). For each step, record the agent’s first action, unresolved questions, searches, inferred assumptions, unavailable tools, recovery attempts, and work performed after the completion condition. The result is a friction map grounded in behavior rather than a subjective rating.

Some ambiguity belongs to the work, some discovery is intentional, and some failures require judgment. The design target is narrower: stop making each fresh agent reconstruct decisions that the system has already made.

Orchestration determines what should happen next. Agent experience determines whether the next agent can actually do it.
