---
title: 'How to build an AI agent workflow: start with the problem'
description: Design an AI agent workflow around a useful result. Follow one article-preparation example through inputs, dependencies, review, recovery, and a clear stopping point.
publishedAt: '2026-07-11'
updatedAt: '2026-10-07'
author: StackOS team
category: AI operations
topics:
  - AI agent workflows
  - workflow architecture
  - agent orchestration
readingTime: 7 min read
featured: true
visual: none
searchIntent: Learn how to build an AI agent workflow from the problem and outcome backward
relatedWorkflows:
  - branding-content-production
  - engineering-tracked-delivery
relatedAgents:
  - stackos-workflow-workflow-author
  - branding-channel-strategist
  - branding-claim-auditor
relatedArticles:
  - ai-agent-vs-workflow-vs-orchestrator
  - what-is-an-agentic-workflow
  - ai-agent-experience
---

Start a useful AI agent workflow with an operational problem and a result someone can use. “Research, write, review” leaves a lot undecided: what the writer can rely on, what the review must establish, and what happens when the sources cannot answer the brief.

Work backward from that result. Define the evidence it needs, the decisions that lead to it, and who owns those decisions. Then give each agent the part it can actually carry out.

## Give the workflow a job with an endpoint

Consider a hypothetical request: prepare an article explaining a proposed support-triage process. The reader is a support lead deciding whether the proposal fits their team. They need to understand who suggests a ticket category, who makes the decision, and what happens next.

Our workflow produces the article; the support-triage process is the subject it explains.

The finished result is a reviewed article packet: the canonical text, its supporting sources, and the review decisions for that version. Publication sits outside this example. Nobody needs access to a live support queue to write the explanation.

## Work backward into a small plan

A reviewed packet depends on an accepted candidate and its reviews. Work backward again: reviewers need the draft and its sources, and the writer needs a brief those sources can support.

For this job, use one coordinating agent, a writer, and independent reviewers. The coordinator owns progression and the final result; specialists return bounded work. The distinction between an [agent, workflow, and orchestrator](/library/articles/ai-agent-vs-workflow-vs-orchestrator/) helps keep those responsibilities clear.

The resulting plan is small enough to inspect:

| Stage and owner | What must be available | Return that permits the next stage |
| --- | --- | --- |
| Scope and sources — coordinator | The brief, source note, voice guidance, and action boundary | Accepted scope, usable source references, and no missing fact essential to the brief |
| Draft — writer | Those accepted inputs | Article candidate and a map from material claims to sources; any unresolved questions remain explicit |
| Review — independent reviewers | The exact candidate and the same accepted inputs | Findings tied to passages, requirements, and evidence, covering claims, voice, and disclosure |
| Resolve findings — coordinator and writer | Reviews for that candidate | Decisions on the findings, bounded repairs where needed, and review of the affected text |
| Return the packet — coordinator | The current candidate and completed checks | A ready packet if acceptance is met; otherwise a blocked return with the unfinished condition and recovery information |

StackOS's workflow authoring guidance starts with this same design order: the problem, useful outcome, operator path, and agent path. A reusable [agentic workflow](/library/articles/what-is-an-agentic-workflow/) needs those decisions even when its individual steps involve judgment.

## Supply the inputs and acceptance criteria

Here are the inputs for the exercise. The names are illustrative references; replace them with accessible files or records in your own workflow.

| Input | What it contains |
| --- | --- |
| `brief-v1` | Explain the proposed process to support leads, including who decides and where the process stops. Include one clearly invented ticket example. Return the reviewed article packet. |
| `process-notes-v1` | The invented source note below, approved for use in the exercise. It describes a proposal, with no trial results. |
| `voice-v1` | Use plain language, explain unfamiliar terms, and write for a peer considering the proposal. Exclude private customer details. |

The source note says:

> A support operator supplies a ticket's text. An agent suggests a category and gives its reason. The support lead accepts or corrects the suggestion. This process ends with the lead's decision; it does not send a customer reply or change the live queue.

That is enough to explain this small proposal. It gives the writer no basis for claims about faster replies, better decisions, or results from a real team.

Before assigning work, write down what acceptance requires:

- The article explains the proposed steps and the lead's decision, with an invented example a reader can follow.
- Material statements about the proposal match the supplied note. The source record identifies what supports them.
- Independent review checks the claims, the requested voice, and public-use boundaries. Findings that prevent acceptance are resolved in the final text.
- The returned packet identifies the exact article version and its reviews, and the next person can retrieve them.

These conditions also give the operator a clear role. They supply the brief, source material, audience, and authority. If the source omits a decision essential to the explanation, the workflow returns that specific gap to them. Ordinary drafting and repair can proceed within the authority already supplied.

## Give each step enough to act on

The writer's assignment can now be direct:

> Use `brief-v1`, `process-notes-v1`, and `voice-v1` to write the explanatory article. Show how an invented ticket moves from the operator's supplied text to the lead's category decision. Keep the proposal's limits visible. Return the draft and its claim-to-source map to the coordinator. If a required process detail is missing, identify the gap instead of inventing it.

Provide the writer with read access to those inputs and a place to write its candidate. Reviewers need the same inputs and candidate, plus a place to return findings. Keep source records unchanged during drafting, and leave publishing and live support operations outside these tool grants.

Give the claim reviewer a specific question: does the article accurately explain the proposal in the supplied note? The voice reviewer checks whether a support lead can follow it in the agreed register. The disclosure reviewer checks the public-use boundary, including whether the example contains private details. Each returns evidence for its judgment to the coordinator.

This is the local [agent experience](/library/articles/ai-agent-experience/): the receiver can find its inputs, perform the assigned work, and return something the next person can use. A role name such as “editor” would leave most of that assignment unstated.

## Decide how review can end

For this example, allow up to two repair rounds after the first review. Each round addresses the findings the coordinator has accepted and includes the checks affected by those changes. Two is a limit chosen for this exercise; change it deliberately for another job.

The coordinator should evaluate [which reviewer findings become work](/library/articles/how-ai-orchestrators-triage-feedback/) before sending the article back. A request for a different introduction needs a reason grounded in the brief or voice guidance. It should not keep reopening a usable article just because another opening is possible.

Suppose the writer adds an unsupported claim about faster replies. Removing it can be a sufficient repair: the brief asks for an explanation of the proposal, and that explanation remains useful without a performance promise. The reviewer still needs to check the revised paragraph for any remaining implication of measured results.

A missing decision owner is different. If the source note said only that the agent suggests a category, the writer could not explain who accepts or corrects it. Removing that part of the article would leave the brief unanswered. Return the gap to the operator: who makes the final category decision, and where is that decision recorded in the proposal?

There are two possible returns:

**Ready:** the candidate meets the acceptance conditions defined above. Return that exact text with its sources and review decisions.

**Blocked:** an essential fact remains unavailable, or an acceptance condition is still unmet when the repair limit is reached. Preserve the latest candidate, attempted repairs, evidence, unresolved condition, and the person or agent who can resolve it. State the smallest missing input or decision.

A review limit stops the work from looping. It does not turn the remaining defect into an acceptable result.

## Save enough state to continue

Conversation context is useful working memory. It is a poor system of record.

Keep the original brief and authority, source versions, current candidate, accepted outputs, review decisions, and open work in the project's durable records. Link the article to that record so someone returning later can find the evidence behind its status.

Imagine the run pauses after review, before repair. The next coordinator should be able to retrieve the reviewed candidate, see which findings were accepted, and assign the outstanding changes. A newer file needs comparison with that record before the old review can be relied on. Otherwise “reviewed” may describe text that is no longer there.

The operator should be able to inspect the same state: the article is in repair, these findings remain, and this is what would make it ready. They should not have to reconstruct it from a conversation between agents.

## Try it without filling in the blanks yourself

A design can look complete while its author is quietly supplying the missing context. Give a fresh agent the brief and the three exercise inputs, with access to the workflow instructions and permitted tools. Ask it to carry out the article-preparation job without additional design hints.

With the complete source note, it should first check that the material covers the brief, then produce the defined packet. Inspect its article, example, source map and candidate-bound reviews against the acceptance criteria; a “complete” label is only a claim about the result.

Then repeat with a source note that omits the decision owner:

> A support operator supplies a ticket's text. An agent suggests a category and gives its reason. The proposal excludes customer replies and live queue changes.

The expected return is a specific block: the proposal does not establish who accepts or corrects the category, so the article cannot yet explain that part. The saved record should identify the missing source detail and where work can resume when it arrives.

Record what the fresh agent did first, what it returned, and where it needed an extra hint. Those observations tell you which part of this design still depends on information you have not given it.
