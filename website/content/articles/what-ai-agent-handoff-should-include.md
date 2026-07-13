---
title: 'What should an AI agent handoff include?'
description: A practical handoff packet for AI agents, covering objective, state, evidence, context, tools, output, recovery, ownership, and stopping rules.
publishedAt: '2026-07-12'
updatedAt: '2026-07-12'
author: StackOS team
category: AI operations
topics:
  - AI agent handoffs
  - agent context
  - multi-agent workflows
readingTime: 10 min read
featured: true
visual: workflow
searchIntent: Learn what an AI agent handoff packet should contain
relatedWorkflows:
  - branding-content-production
  - engineering-tracked-delivery
relatedAgents:
  - stackos-sdlc-delivery
  - branding-narrative-writer
  - branding-claim-auditor
relatedArticles:
  - ai-agent-experience
  - how-to-build-ai-agent-workflow
  - how-ai-orchestrators-triage-feedback
---

An AI agent handoff should include the next objective, the accepted state and supporting evidence, only the context that changes the next decision, the agent’s authority and tools, the required output and acceptance criteria, recovery guidance, the next destination, and a stopping rule.

That is the minimum useful packet. A role prompt, a conversation transcript, or a message such as “review the draft” may be part of the handoff, but none of them makes the next step executable by itself.

## A handoff message is not an execution packet

Handoffs are often described as transfers between agents. One agent decides that another specialist should take over, calls a handoff tool, and passes some context.

That description covers the routing event. It does not answer the receiving agent’s operating questions:

- Which output is authoritative?
- What has already been accepted?
- Which evidence and policies apply?
- What may this agent read or change?
- What result must it return?
- Who decides what happens after the result?
- When should it stop?

A capable agent can investigate these questions. That is not always a failure. Research, diagnosis, and discovery may be the work. The avoidable friction is different: forcing the agent to rediscover workflow state the system already knows.

We encountered this distinction while testing our own workflow setup. Fresh agents could work around incomplete context by reading more files and reconstructing prior decisions. They still reached the goal. But broad role-level investigation added latency, while a targeted step packet let the agent move directly to the relevant action. The useful goal was not zero friction. It was to remove system-created guessing.

This is the narrow focus of a handoff packet. The broader [agent experience](/library/articles/ai-agent-experience) includes tool design, recovery, authority, and orchestration across the whole run. The packet is the local execution surface one agent receives now.

## Include eight operating fields

The following structure is a practical synthesis from our workflow work, not a standard imposed by one agent framework. The fields can be assembled from durable state, defaults, and prior outputs; they do not need to become eight paragraphs in every prompt.

| Field | Question it answers |
| --- | --- |
| Objective and ownership | What must happen now, and who owns the next decision? |
| Accepted state and evidence | What is already true, and which refs prove it? |
| Bounded context and policies | Which prior decisions and rules affect this step? |
| Authority and tools | What may the agent inspect, change, or execute? |
| Output contract | What exact result, fields, or artifact must be returned? |
| Acceptance criteria | How can the agent tell that its responsibility is complete? |
| Recovery path | What should happen when an expected input, tool, or criterion fails? |
| Destination and stopping rule | Where does the result go, and when must the agent return control? |

Each field removes a different ambiguity. Combining them into a long prose brief often hides those distinctions, so a structured packet is usually easier to inspect.

## Name the objective and ownership

“Act as an editor” is a role. “Review the supplied article for unsupported material claims and return findings to the orchestrator” is an objective.

The packet should also state whether control transfers or returns. This varies across architectures. Microsoft’s [handoff orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/handoff) transfers task ownership to the receiving agent. In a manager or agent-as-tool pattern, the primary agent retains overall responsibility and receives a bounded specialist result.

The receiver should not need to infer which model applies. A useful packet might say:

> You own the claim review only. Return findings to the orchestrator. Do not revise the article or expand its scope.

That sentence defines both responsibility and its boundary.

## Pass accepted state, not a scavenger hunt

A handoff should identify the current authoritative inputs directly. It should name the accepted brief, current draft, relevant predecessor output, and evidence refs. “Use the latest version in the project” moves state resolution back to the receiving agent.

Accepted state is more than a summary of what happened. It separates settled facts from open questions:

- the angle was accepted;
- the draft at a specific ref is current;
- two claims are intentionally marked unresolved;
- the publication intent is packet only;
- no image generation was selected.

This lets the agent preserve prior decisions instead of accidentally reopening them.

Evidence refs matter for the same reason. A reviewer should receive the claim map or source ledger it must evaluate, not a claim that “research was completed.” A downstream agent can then inspect the receipt when necessary without replaying the entire research phase.

## Select context that changes the next decision

Conversation history is useful, but it is not automatically the right handoff payload.

The [OpenAI Agents SDK handoff guide](https://openai.github.io/openai-agents-python/handoffs/) separates small model-generated handoff metadata from application state and from the receiving agent’s main input. It also provides input filters that change which history items the next agent sees. Microsoft’s [orchestration guidance](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) recommends deciding whether the next agent needs full raw context, a compacted version, or only a new instruction set.

The practical rule is to include context when it changes a valid action or judgment. Keep the rest as durable references.

For an editorial review, the current voice guide and disclosure policy change the decision. A long transcript about earlier keyword research may not. For a debugging step, the failing test output and accepted requirement matter; unrelated implementation discussion does not.

Full history can still be correct when nuance across the whole conversation is the task. Bounded context is a selection rule, not a blanket instruction to summarize everything.

## Resolve tools and authority together

A tool name without an authority boundary leaves the agent guessing.

The packet should say which operations are available, what they are for, and which side effects are outside the step. If a reviewer can read files and run checks but cannot edit or publish, that boundary belongs beside the tools.

Resolved tools are better than broad discovery when the workflow already knows the target. “Run the website content sync in this directory” is more executable than “use the repository tools to verify the article.” A tool description should still expose important limits and likely failure modes, but the agent should not have to inspect an entire toolbox to locate the intended operation.

Authority should also end with the active responsibility. A review step does not inherit publication rights merely because publication exists later in the workflow.

## Define the output and acceptance contract

The receiving agent needs to know what a valid result looks like.

For a claim review, “provide feedback” is weak. A useful output contract could require:

- finding;
- affected claim or section;
- evidence basis;
- classification;
- repair status;
- unresolved question.

Acceptance criteria then define completion: all material claims were checked, unsupported claims were removed or marked unresolved, and findings were returned in the expected shape.

The distinction matters. An output schema can prove that required fields exist. Acceptance criteria evaluate whether the responsibility was actually fulfilled. A packet should carry both when the result feeds another agent.

## Include targeted recovery and a stopping rule

“Retry if needed” is not recovery guidance.

Targeted recovery names the next safe action for failures the workflow can anticipate. If a predecessor handoff was truncated, the packet can provide the exact read needed to recover it. If a source is unavailable, it can tell the reviewer to return an unresolved finding instead of inventing support. If an external action lacks authority, it can require the agent to stop with a structured blocker.

The stopping rule is equally important. It prevents a specialist from turning one responsibility into a broader improvement project.

A reviewer should stop when its checks are complete and return findings. It should not implement every preference. A writer should stop when the accepted repair is made and the required evidence is attached. It should not redesign the workflow.

The [orchestrator then triages feedback](/library/articles/how-ai-orchestrators-triage-feedback) against the accepted plan.

## A worked handoff packet

The packet below is deliberately compact. Stable project rules and tool schemas can remain durable references; this payload resolves what the next agent needs for one article review.

```yaml
step: editorial-review

objective:
  task: Review the current article for unsupported material claims.
  ownership: Return findings to the orchestrator; do not edit or publish.

accepted_state:
  draft_ref: website/content/articles/example.md
  angle_status: accepted
  publication_intent: packet_only
  evidence_refs:
    - evidence-item:research-basis
    - source-ledger:example

context:
  voice_guide_ref: artifact:current-voice-guide
  disclosure_policy_ref: resource:public-disclosure

authority:
  allowed:
    - read the draft and named evidence
    - inspect public primary sources
  prohibited:
    - modify the draft
    - create publication jobs
    - add work outside claim review

output:
  required_fields:
    - claim
    - evidence_basis
    - classification
    - repair_needed
  acceptance:
    - every material claim was checked
    - unsupported claims are identified
    - preferences are separated from blockers

recovery:
  missing_evidence: Return an unresolved finding with the missing ref.
  truncated_handoff: Read the named predecessor result once.

destination: orchestrator
stop_when: The claim report is complete or a blocking input is unavailable.
```

The exact keys can change. The operating questions should not disappear.

## Watch for common handoff failures

Several packets look informative but still force reconstruction:

- **Summary only:** explains what happened but does not identify authoritative state.
- **Role only:** describes expertise but not the current objective or boundary.
- **History dump:** passes everything and makes the receiver search for what matters.
- **Tool catalog:** exposes operations without resolving which one applies or what authority is active.
- **Output without evidence:** asks for a result but not the receipts its consumer needs.
- **Criteria without recovery:** defines success but gives no valid response to missing inputs.
- **No destination:** leaves the agent unsure whether to act, delegate, or return.
- **No stopping rule:** lets a bounded task expand into open-ended improvement.

These are packet problems even when the model is capable enough to work around them.

## Use the first-valid-action test

Give the packet to a fresh agent with a short instruction such as “continue this step.” Then observe what it must do before taking a valid action.

The packet is probably sufficient when the agent can answer:

- What am I responsible for?
- Which input is authoritative?
- Which decisions are already settled?
- Which tools and side effects are allowed?
- What must I return, and where?
- What should I do if the expected path fails?
- When must I stop?

Some investigation will remain. That is part of agent work. The warning sign is workflow archaeology: searching for the current draft, guessing which policy applies, discovering tool authority by failure, or reconstructing the terminal condition from conversation history.

A good handoff does not eliminate reasoning. It reserves reasoning for the task instead of spending it on avoidable ambiguity.
