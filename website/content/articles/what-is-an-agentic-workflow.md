---
title: What is an agentic workflow? A practical guide to AI-powered work
description: An agentic workflow lets AI choose its next action from the evidence. Follow a content example through source checks, decisions, review, recovery, and a finished draft.
publishedAt: '2026-07-09'
updatedAt: '2026-09-29'
author: StackOS team
category: Agentic workflows
topics:
  - agentic workflows
  - AI agents
  - workflow automation
readingTime: 8 min read
featured: true
visual: workflow
searchIntent: Understand what an agentic workflow is, how it works, and when to use one
relatedWorkflows:
  - engineering-tracked-delivery
  - branding-content-production
  - marketing-campaign-production
relatedAgents:
  - branding-evidence-curator
  - branding-narrative-writer
relatedArticles:
  - ai-agent-vs-workflow-vs-orchestrator
  - use-codex-claude-gemini-with-existing-tools
---

An agentic workflow lets AI choose the next action as it works toward a goal. A source may contradict the brief, or a reviewer may find a claim the draft cannot support. The agent has to decide how to continue.

It might read another source, change the proposed angle, repair the draft, or stop because the requested result is ready. The useful distinction is that the decision depends on what the agent finds during the work.

In StackOS, that judgment sits inside a saved plan with scoped tools, evidence, checks, and a definition of done. The agent makes decisions; StackOS stores the state, validates tool calls, and records their results.

::article-workflow-visual{workflow="branding-content-production" title="A complete content workflow, from request to verified result"}
::

## What makes a workflow agentic?

Fixed automation follows a known mapping: when this happens, do that. Agent judgment is useful when the next action depends on meaning, evidence, or a changing situation.

Consider four decisions from a content workflow:

- Is an operator interview needed, or is the relevant experience already captured?
- Does the evidence support the proposed angle?
- Does a reviewer finding require a correction, or does it expand the assignment?
- Does the finished article answer the request, or is a material claim still unresolved?

The workflow gives those decisions boundaries: the allowed actions, expected outputs, and stopping rule. A person needs to step in when a material choice, permission, or piece of context is missing. Routine decisions can stay with the agent responsible for the work.

## What should the workflow contain?

The practical model used in StackOS covers six things:

1. **Problem and outcome.** Name the failure, friction, or decision AI should help with, the useful result, and which external changes are allowed.
2. **State and dependencies.** Record what is pending, active, accepted, blocked, or complete, and which later work depends on it.
3. **Context, tools, and authority.** Give each step the information and operations it needs without exposing unrelated history or broader access.
4. **Decision ownership.** State what the coordinating agent decides and which bounded responsibilities belong to specialists, if the work needs them.
5. **Outputs, evidence, and acceptance.** Define what each step must return, which claims need support, and how the result will be checked.
6. **Recovery and a definition of done.** Describe how to continue after anticipated failures and what must be true before the job ends.

Stable rules can stay in the workflow, project records, and tool contracts. The agent needs the part of that context that affects its next decision.

## What happens from request to result?

Consider this hypothetical content request:

> Write a practical article for editors deciding when to interview someone before drafting. Use the supplied notes, our approved voice guide, and the StackOS content-workflow documentation. Return a reviewed Markdown draft with its sources. Do not publish it.

One supplied note says, “Every article must start with an interview.” The agent needs to check that claim before building the article around it.

### Read the source and use what it says

The coordinating agent selects the [content-production workflow](/library/workflows/branding-content-production/) and records the audience, sources, and requested output for this job. It asks a reading tool to retrieve that workflow's documentation and return the interview conditions.

A successful tool response is only the first check. The agent must confirm that the returned text belongs to the intended workflow and contains the relevant rule. An error message or unrelated page cannot support the note.

The workflow makes the interview conditional. Ask for missing firsthand context when the person's experience or judgment matters; an interview can be skipped when the existing material is sufficient. That contradicts the note's blanket instruction.

For this explanatory article, the documentation supplies the rule the editor needs. The agent records why an interview is unnecessary and chooses an angle about recognizing missing context. The writer can explain the difference between an article whose sources already answer the question and one that needs to establish what a person actually did or learned.

The source check has changed both the next step and the draft's advice. The agent skips the interview for a reason it can show, then writes from the supported rule.

### Review the advice against the same source

An independent claim reviewer receives the draft, its sources, and the original request. Suppose a later paragraph still tells editors to start every draft with an interview. The reviewer points to that sentence and the conditional rule it contradicts.

The coordinating agent checks the finding, sends the passage back for correction, and checks the revision. If the reviewer also suggests turning the article into a campaign, that suggestion does not expand this job. The request is for one reviewed draft.

Separate voice and disclosure reviews check whether the piece follows the approved guidance and whether its details are cleared for public use. The coordinating agent resolves the findings before accepting the final version.

### Resume the source check if it fails

If the reading tool returns an error or the wrong page, the central claim stays unverified. The agent saves the source it tried to read, the failed result, and the work that depends on that check. Once the source is available or the input is corrected, it can resume the check and continue from the recorded state.

The job ends when the reviewed Markdown file, source references, and resolved findings are saved and retrievable. The coordinating agent opens the saved draft and checks it against the original request: it explains when an interview is useful, its material claims are supported, and no blocking review finding remains. The editor receives the draft reference. Publication is outside this request.

## How is this different from a chatbot or fixed automation?

| Mode | Best for | How it adapts | What marks completion |
| --- | --- | --- | --- |
| Conversation | A question, explanation, or one-off draft | The model responds within the current context | A useful response is returned |
| Fixed automation | A stable trigger with a known action | Predetermined rules and branches | The configured action finishes |
| Agentic workflow | Multi-step work where evidence or state changes the next action | An agent evaluates results and chooses how to continue | The requested result meets its acceptance criteria |

A conversation can call tools, and fixed automation can have many branches. In the content example, the consequential choice is what to do after a source contradicts the supplied note. The saved state carries that choice through writing and review.

Terminology varies. [Anthropic's guide to building agents](https://www.anthropic.com/engineering/building-effective-agents) distinguishes predefined workflows from agents that direct their own process and tool use, while grouping both as agentic systems. The definition here emphasizes adaptive judgment; the saved plan and review boundaries describe how StackOS organizes that work.

## Where can agentic workflows be used?

They are useful candidates when:

- the outcome spans several dependent stages or sessions;
- the next step depends on evidence rather than a fixed rule;
- different responsibilities need different context or independent review;
- connected tools make external changes that need narrow permissions and recorded results;
- failure needs recovery and continuation rather than a full restart.

For example, an engineering task may need another implementation pass after a test exposes a missed requirement. A support investigation may produce an answer or establish that a software fix is needed. A content task may need a different angle after its strongest proposed claim fails a source check. In each case, the result of one step changes the next decision.

For the surrounding roles, see [AI agent vs. workflow vs. orchestrator](/library/articles/ai-agent-vs-workflow-vs-orchestrator/). For the context and tools each agent needs, see [agent experience](/library/articles/ai-agent-experience/).

## When should you use one?

Do not build one for every request. A normal conversation is enough for a quick question or one-off draft. Fixed automation is usually better for a deterministic transformation. A single explicit tool action does not need a ten-step workflow around it.

Use a workflow when the work needs judgment and enough continuity to carry decisions, evidence, and unfinished work through to the result.
