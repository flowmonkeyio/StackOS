---
title: 'What should an AI agent handoff include?'
description: Build an AI agent handoff with the objective, evidence, context, tools, expected result, and stopping rule. See how to continue after an interrupted update.
publishedAt: '2026-07-12'
updatedAt: '2026-09-30'
author: StackOS team
category: AI operations
topics:
  - AI agent handoffs
  - agent context
  - multi-agent workflows
readingTime: 9 min read
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

This is the practical packet structure used in this guide. Stable instructions can stay in referenced project records; the handoff needs to resolve the parts that matter to the current task.

| Field | What the receiver needs to know |
| --- | --- |
| Objective and ownership | The task to perform now and who owns the next decision. |
| Accepted state and evidence | The current inputs, settled decisions, completed work, unresolved results, and supporting records. |
| Context and policies | The relevant history and rules that affect this step. |
| Authority and tools | The operations the agent may use, their targets, and the changes it may make. |
| Required output | The result, fields, or artifact it must return, including evidence. |
| Acceptance criteria | What must be true for this responsibility to be complete. |
| Recovery guidance | What to do when a necessary input or operation is missing, fails, or returns an uncertain result. |
| Destination and stopping rule | Where to return the result and when to stop working. |

## Say who owns the next decision

“Act as an editor” names a role. “Review the supplied article for unsupported material claims and return findings to the coordinating agent” gives the editor a task and a destination.

Add the boundary that matters: may the editor repair the article, or should it return findings for someone else to resolve? Having access to an editing tool does not answer that question.

The meaning of a handoff also varies across architectures. Microsoft's [handoff orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/handoff) transfers control and task ownership to the receiving agent. Its agents-as-tools pattern leaves responsibility with the primary agent, which receives the specialist's result.

Name the arrangement in your packet. In the shared-file example, the receiving agent will inspect the interrupted update and return what it can verify. The coordinating agent still owns the file-update task and any decision to try again.

## Record completed work and unresolved outcomes

Give the receiver a specific version of the input. “Use the latest file” leaves it to work out which file was approved and whether someone has changed it since. A stable reference, revision, or content fingerprint gives it something to check.

For the interrupted update, three facts belong in the handoff:

- The local file passed its checks and was approved. Include the exact file and those records.
- An update request was sent to a named remote target. Include the request record and any identifier available for looking it up.
- The response was not received. The external result remains unconfirmed.

A timeout tells you that the caller stopped waiting. It does not establish whether the remote service applied the change. Keep that uncertainty visible instead of reducing the whole task to “failed.” Repeating the request could cause another change, depending on the operation and provider.

Use evidence references for the conclusions the next agent must rely on. A note saying “checks passed” should point to the check result for this file version. An approval should identify what was approved. A request record should identify the target and attempted change.

Keep settled decisions with that evidence. If the approved file is the intended output, the receiving agent should not rewrite it while investigating whether the update arrived. If new evidence calls an earlier decision into question, it can return that finding to the decision owner.

## Choose context the receiver can use

Include context when it changes the receiver's action or judgment. In this example, the approved file, target, request history, and rules for checking the remote result matter. The discussion that led to an earlier, discarded draft can remain retrievable without filling the packet.

The [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/handoffs/) separates model-generated handoff arguments from local application context. Those arguments do not replace the receiving agent's main input, and input filters can change the history it sees. Deciding which information belongs in each place is part of designing the handoff.

Microsoft's [agent design guidance](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) likewise describes choosing full context, compacted context, or new instructions according to the next agent's needs. It recommends durable external state for work that spans interruptions.

Full history may be necessary when the task depends on nuance across a conversation. A shorter packet is useful only if it preserves what the receiver needs and points to anything it may have to recover.

In StackOS, a claimed step includes its instructions, expected outputs, allowed tools, and bounded results from direct predecessor steps. When one of those predecessor results is too large for the handoff, the response provides a targeted read for the complete result. That gives the agent a place to retrieve missing detail without searching the whole project. The broader [agent-experience guide](/library/articles/ai-agent-experience/) covers context, tools, and recovery across the workflow.

## Make the tools and return value specific

Put each permitted operation beside its purpose and target. A reader checking one shared file needs the operations for inspecting that file and its request record. A catalog of every available tool adds little if the agent still has to discover which operation fits.

Also state the limits of the assignment. The agent in this example may read the request status and remote file. It may not issue another update, change the approved local file, or broaden the investigation to other files. The original agent's permission to write does not automatically authorize every action by its successor.

Define the returned result precisely enough for its consumer to use. For this check, ask for the request's observed status, the remote file's observed version, the comparison with the approved content, the supporting records, and any unresolved question.

Acceptance criteria then explain what those fields must establish. A response with every field filled in can still confuse a request being accepted with a file being updated. To report the update complete, the agent needs evidence of the required external result.

Recovery guidance should cover the expected gaps. Name the read that retrieves an omitted predecessor result. Say how to report an unavailable source. For an uncertain external write, identify the permitted status checks and the point at which the agent should return its unresolved finding. “Retry if needed” leaves the consequential decision unspecified.

## Worked example: an interrupted shared-file update

Here is the full hypothetical case. A local document has passed its checks and been approved. Its update request timed out. The file service in this example supports looking up that request by its saved identifier and reading the remote file's current version and contents. The receiving agent has permission to perform those reads.

The YAML is a human-readable illustration with placeholder references, not a StackOS API payload. Replace the operations and references with ones your actual tools support.

```yaml
objective:
  task: Determine the outcome of the interrupted file update.
  ownership: Return evidence to the coordinating agent, which owns the task.

accepted_state:
  approved_file_ref: LOCAL_FILE_VERSION
  content_fingerprint_ref: APPROVED_CONTENT_FINGERPRINT
  checks_ref: LOCAL_CHECK_RESULT
  approval_ref: FILE_APPROVAL_RECORD
  target_ref: SHARED_FILE_REF
  attempted_request_ref: UPDATE_REQUEST_RECORD
  request_lookup_id: REQUEST_IDENTIFIER
  local_result: Timed out before receiving the response.
  external_result: Unconfirmed.

context:
  file_requirements_ref: ACCEPTED_FILE_REQUIREMENTS
  provider_read_instructions_ref: REQUEST_AND_FILE_READ_GUIDE
  decision: The approved local content remains the intended update.

authority:
  allowed:
    - Read the named local inputs and records.
    - Look up the saved request identifier.
    - Read the named remote file's version and contents.
  prohibited:
    - Send or retry a file update.
    - Edit the approved content or act on another file.

output:
  required:
    - Request status and its supporting record.
    - Remote file version and content comparison, with readback evidence.
    - Conclusion: applied, confirmed_no_effect, or unresolved.
    - Any missing evidence or decision needed from the coordinating agent.

acceptance:
  - Each conclusion is supported by the retrieved records.
  - Applied means the request completed and the target matches the approved content.
  - Confirmed_no_effect requires a terminal record proving no change occurred.
  - Missing, pending, partial, or conflicting evidence remains explicit.
  - No new write was attempted.

recovery:
  unavailable_input: Return the missing reference and the check it prevents.
  failed_read: Return the failed read and keep its conclusion unresolved.
  pending_request: Report the pending state for a later check.
  conflicting_results: Return both records and the exact conflict.

destination: Coordinating agent.
stop_when: The permitted checks are complete or a required check is blocked.
```

You can inspect this packet by giving it to a fresh agent and asking what it would do first. It should be able to identify the local evidence and permitted request lookup. If it proposes uploading the file again, check whether it missed the authority boundary or whether the packet left the external result ambiguous.

The returned evidence determines what happens next:

- **The request completed and the target matches the approved content.** Return the completion record and file comparison. The coordinating agent can record the update as complete without another write.
- **A terminal request record proves that no change occurred.** Return that record and the observed file state. The coordinating agent can decide whether a retry is appropriate under the provider's behavior and current authorization.
- **The evidence is incomplete or conflicts.** Return the precise unresolved result. A matching remote file can establish that the desired content is present, but a missing request record may still leave the attempted operation unaccounted for. A pending request, partial change, or different file version also needs its own next decision.

In the last case, the receiving agent has finished its check while the file-update task remains open. Its return tells the coordinating agent which evidence is still missing, so the next attempt to continue can start there.
