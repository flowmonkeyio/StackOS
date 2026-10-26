---
title: 'Agent experience: the missing layer in AI agent orchestration'
description: Turn a vague review request into an assignment an agent can carry out. See how exact inputs, authority, evidence, recovery, and a stopping rule fit together.
publishedAt: '2026-07-11'
updatedAt: '2026-10-04'
author: StackOS team
category: AI operations
topics:
  - AI agent orchestration
  - agent experience
  - workflow design
readingTime: 6 min read
featured: true
visual: none
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

“Review the implementation” leaves the agent a second job: work out what it is supposed to review.

Which version, against which requirements, with permission to do what? The coordinating agent may already know those answers. If the assignment leaves them out, the reviewer has to search for them, ask, or guess before it can begin.

Agent experience is the work an agent must do to understand and carry out its assigned step. You can inspect it through the inputs it receives, the actions available to it, and what happens when something is missing. Start at the moment the assignment arrives.

## Replace the vague request with something usable

Here is a small, self-contained exercise. The task is to review a Python function that checks a file count. Everything needed for this review is below; there is no repository or hidden test fixture to discover.

**Task:** compare `candidate-v1` with `requirements-v1` and return a review to the coordinating agent.

**Requirements, version 1:**

- The input is an integer. Type handling is outside this exercise.
- Return `True` for a count from 1 through 5, inclusive.
- Return `False` for every other integer, including zero and negative values.

**Candidate, version 1:**

```python
def valid_file_count(count):
    return 0 <= count <= 5
```

**Scope:** inspect the supplied requirement and code. This review needs no tools. Do not run the code, edit it, install anything, search a repository, or contact an external service.

**Return:** name the requirement and candidate versions reviewed, say whether the candidate meets the rule, and give the evidence for any discrepancy. Include the input that exposes it, the expected result, the result implied by the code, and the relevant expression. State the method used and any check not performed.

**Stop:** return that review to the coordinating agent. Any code change belongs to a subsequent assignment. If the required input is missing in an adaptation of this exercise, identify the missing version and the check it prevents; do not substitute another version or invent a requirement.

That is the whole assignment. In a real system, configure the host's available tools and permissions to match its scope. The instruction to avoid edits is useful guidance, but it does not itself remove a write capability.

## Follow the receiver's first action

The reviewer can begin by comparing the permitted range with the expression. At `count = 0`, the requirement says `False`, while `0 <= count <= 5` evaluates to `True`. The lower bound is wrong.

The other boundaries help delimit the finding. Counts 1 and 5 satisfy both the requirement and the expression. Negative integers and values greater than 5 are excluded by the expression. For the stated integer input, zero is the discrepancy.

A useful return could be:

> Reviewed `candidate-v1` against `requirements-v1` by inspection. The candidate does not meet the count rule: for input `0`, the required result is `False`, but `0 <= 0 <= 5` is `True`. The lower bound admits zero. Code was not executed or changed. No required input was missing.

This is the expected reasoning for the exercise, not a report from an executed agent trial. It gives the coordinator enough to inspect the finding and assign a repair. The reviewer has completed its part while the implementation still fails a requirement.

That separation matters when assigning an [AI agent, workflow, and orchestrator](/library/articles/ai-agent-vs-workflow-vs-orchestrator/) their responsibilities. Here the reviewer supplies evidence; the coordinator decides what happens to delivery. Quietly changing the function would make the return about different code from the version the reviewer was asked to inspect.

You can try the assignment with a fresh agent. Give it the assignment above without the worked answer, then inspect its actual return. Did it compare the supplied versions, identify zero, explain the discrepancy, state that it used inspection, and stop without editing? If it starts searching for a repository, find out whether the packet was incomplete, the tool setup was misleading, or the agent overlooked the supplied input. Record what happened before choosing a repair.

## Select context for the work at hand

The example is small enough to include every relevant input. A real review may need source files, requirements, test instructions, project rules, and earlier decisions. Resolve those references before dispatch where the workflow already knows which ones apply.

A directory containing several drafts still leaves the reviewer to choose an authoritative version. An exact candidate reference and the applicable requirement resolve that choice. Keep additional history available when the task may need it, with a clear way to retrieve it.

Context is useful when it reduces uncertainty. Beyond that point, it becomes another search surface.

StackOS documents its claimed-step packet around this distinction: the active instructions, resolved inputs, selected context, expected outputs, allowed tools, and bounded results from direct predecessor steps. If supplied material is truncated, the documented response includes a targeted read for the full step result. That is a recovery path the receiver can follow without reconstructing where the input came from.

For your own assignments, name the recovery action that fits the gap. A missing requirement needs its reference or a decision from its owner. An unavailable test command needs a report of the check it prevents. The reviewer should not silently replace either with a guess. A good recovery path narrows the next decision without pretending every failure can be anticipated.

An [agentic workflow](/library/articles/what-is-an-agentic-workflow/) can choose later work from these findings. The current receiver still needs enough information to perform its own assignment and return control.

## What one StackOS replay recorded

In a July 2026 StackOS foundation replay, a draft specialist reported needing no tools and no guessing for its bounded assignment. The same step record described its work as slow. Elsewhere, the replay notes recorded a fresh subagent missing its allotted execution window. A complete packet and an easy or fast run are separate things to investigate.

Another step's saved report says it recovered a truncated handoff through a targeted read of the prior result. The final step instructed the agent to return the handoff and stop. Its terminal record reports that the run ended without starting the next workflow, changing that workflow's setup, or creating content.

These records preserve the agents' reports and the stopping instruction. They do not isolate what caused the reported behavior. The useful evidence is specific: what the receiver said it needed, how it reported recovering missing material, where it stopped, and which friction remained.

## Put a missing decision where the next agent can find it

When [building an AI agent workflow](/library/articles/how-to-build-ai-agent-workflow/), inspect the receiver's searches and questions alongside the work it was assigned. Investigating an undocumented behavior may be the substance of a review. Locating the approved requirement is avoidable reconstruction when the coordinator already selected it.

The file-count reviewer still has to reason about the comparison. The assignment supplies the rule, candidate, authority, and destination so that reasoning has a clear object. If a real reviewer has to ask which range was approved, put the answer and its source into the next assignment. Then it can spend its effort checking whether the code satisfies that rule.
