---
title: 'What should an AI agent handoff include?'
description: Build an AI agent handoff with the objective, evidence, context, tools, expected result, and stopping rule. See how to continue after an interrupted update.
publishedAt: '2026-07-12'
updatedAt: '2026-10-07'
author: StackOS team
category: AI operations
topics:
  - AI agent handoffs
  - agent context
  - multi-agent workflows
readingTime: 6 min read
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

Suppose an agent prepares a new version of a shared file, sends the update, and loses the connection before the response arrives. Another agent takes over.

The local file is ready. The remote file may already have changed. The next agent needs to know both before deciding what to do.

An AI agent handoff should carry the objective, accepted state and evidence, relevant context, authority and tools, required output, acceptance criteria, recovery guidance, and a destination and stopping rule. Together, these tell the receiver what finished, what remains uncertain, and which action it can take next.

## Eight fields for a useful handoff

Stable instructions can stay in referenced project records. Resolve the parts that matter to the current task:

- **Objective and ownership:** the task to perform now and who owns the next decision.
- **Accepted state and evidence:** current inputs, settled decisions, completed work, unresolved results and supporting records.
- **Context and policies:** the relevant history and rules for this step.
- **Authority and tools:** permitted operations, their targets and the changes the receiver may make.
- **Required output:** the result, fields or artifact to return, including evidence.
- **Acceptance criteria:** what must be true for this responsibility to be complete.
- **Recovery guidance:** what to do when an input or operation is missing, fails or leaves an uncertain result.
- **Destination and stopping rule:** where to return the result and when to stop.

## A packet for the interrupted update

The [complete example packet](/examples/what-ai-agent-handoff-should-include/interrupted-update.yaml) puts those fields together. It is hypothetical YAML with placeholder references, not a StackOS API payload. Replace its operations and references with ones your tools support.

In this example, the local document passed its checks and was approved. The file service supports looking up the timed-out request by its saved identifier and reading the remote file's current version and contents. The receiving agent may perform those reads; it may not send another update or edit the approved file.

The packet starts with the approved file, its content fingerprint, checks and approval records, the target and the attempted request. This excerpt makes the unresolved state explicit:

```yaml
accepted_state:
  local_result: >-
    Timed out before
    receiving the
    response.
  external_result: >-
    Unconfirmed.

stop_when: >-
  The permitted checks
  are complete or a
  required check
  is blocked.
```

A timeout tells you that the caller stopped waiting. It does not establish whether the service applied the change. The receiver must inspect the request and file before the coordinating agent can decide what happens next.

The full packet asks for the observed request status, remote version, content comparison and supporting records. Missing, pending, partial or conflicting evidence stays visible in the return. The stopping rule lets the receiver finish a bounded check without pretending that the file-update task is finished too.

## Say who owns the next decision

“Act as an editor” names a role. “Review the supplied article for unsupported material claims and return findings to the coordinating agent” gives the editor a task and a destination. Add whether it may repair the article or should return findings. An editing tool's availability cannot decide that.

Handoff also means different things across architectures. Microsoft's [handoff orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/handoff) transfers control and task ownership to the receiving agent. Its agents-as-tools pattern leaves responsibility with the primary agent, which receives the specialist's result.

Our file-checking agent returns evidence. The coordinating agent still owns the update and any decision to try again. The original writer's permission does not silently give its successor permission to retry or act on another file.

## Keep the evidence and settled decisions together

“Use the latest file” leaves the receiver to discover which version was approved and whether it has changed. A stable reference, revision or content fingerprint gives it something to check.

For the interrupted update, retain three separate records: the approved local file and its checks, the attempted request to the named target, and any evidence about the external result. A note saying “checks passed” needs the result for that file version. An approval needs to identify what was approved.

The approved content remains the intended update while the receiver investigates. Rewriting it would change the comparison the check depends on. If new evidence calls that decision into question, return the finding to its owner.

Put each permitted operation beside its purpose and target. Here, request lookup establishes the attempted operation's status, while reading the file establishes what is currently there. Neither a sent request nor a filled-in result form is proof that the requested update completed.

## Choose context the receiver can use

Include context when it changes the next action or judgment. The approved file, target, request history and rules for checking the remote result matter here. Discussion of a discarded draft can remain retrievable.

The [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/handoffs/) separates model-generated handoff arguments from local application context. Those arguments do not replace the receiving agent's main input, and input filters can change the history it sees. Microsoft's [agent design guidance](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) likewise distinguishes full, compacted and new context, and recommends durable external state for work across interruptions. Choose what the task needs; a shorter packet is useful only if the receiver can still recover necessary detail.

In StackOS, a claimed step includes its instructions, expected outputs, allowed tools and bounded results from direct predecessor steps. When a predecessor result is too large for the handoff, the response provides a targeted read for the complete result. The [agent-experience guide](/library/articles/ai-agent-experience/) covers context, tools and recovery across the workflow.

## Return the outcome the evidence supports

The packet gives the receiver three possible conclusions:

- **The request completed and the target matches the approved content.** Return the completion record and file comparison. The coordinating agent can record the update as complete without another write.
- **A terminal request record proves that no change occurred.** Return that record and the observed file state. The coordinating agent can decide whether a retry is appropriate under the provider's behavior and current authorization.
- **The evidence is incomplete or conflicts.** Return the precise unresolved result. A matching remote file can establish that the desired content is present, but a missing request record may still leave the attempted operation unaccounted for. A pending request, partial change or different file version also needs its own next decision.

The recovery instructions say what to return if an input is unavailable, a read fails, a request is pending or records conflict. The receiver does not substitute another write for a check it could not complete.

To inspect the packet, give it to a fresh agent and ask what it would do first. It should identify the local evidence and permitted request lookup. If it proposes uploading again, check whether it missed the authority boundary or the packet left the external state ambiguous.

When evidence remains missing, the receiving agent can finish its check while the file-update task stays open. Its return should make the next missing check or decision clear.
