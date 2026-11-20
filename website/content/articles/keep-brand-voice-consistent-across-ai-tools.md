---
title: How do I keep the same brand voice across different AI writing tools?
description: Give Codex CLI and Claude Code one shared voice guide, inspect the sources behind their drafts, and reconcile old references when your guidance changes.
publishedAt: '2026-10-07'
updatedAt: '2026-10-07'
author: StackOS team
category: AI content
topics:
  - brand voice
  - AI writing
  - Codex CLI
  - Claude Code
  - editorial guidance
readingTime: 5 min read
featured: false
visual: none
searchIntent: Keep one approved brand voice guide across AI writing tools, verify which revision each uses, and repair stale references after an editorial change
relatedWorkflows:
  - branding-brand-foundation-setup
  - branding-content-production
relatedAgents:
  - branding-narrative-writer
  - branding-voice-reviewer
relatedArticles:
  - how-to-build-ai-content-workflow-that-does-not-sound-generic
  - use-codex-claude-gemini-with-existing-tools
  - how-to-fact-check-ai-generated-content
---

Keep one approved voice guide with an owner, a revision and examples, and have each writing tool read that source. [GitLab does this for documentation](https://docs.gitlab.com/development/documentation/ai-instruction-files-documentation/): its authoring instructions refer to, or are generated from, the authoritative style guide.

The setup below uses Codex CLI and Claude Code in a project folder. Each gets a short instruction file pointing to the same guide. When the guide changes, check both the reference and the resulting prose.

## What belongs in a shared voice guide?

Give the writer concrete choices: who they are addressing, how to explain an action, which tone fits the surface, and an example showing what you mean. Name the person who owns those choices and the current approved revision.

Draft Pier, an invented app for reviewing articles, provides a small example:

- **Owner and revision:** the example editorial lead; revision 1.0, approved for this fictional exercise.
- **Stable voice:** address someone with a draft to review. Use ordinary words, name the action and explain what happens next.
- **Announcement tone:** allow one brief harbor metaphor, then explain the behavior plainly.
- **Good example:** “Give your draft a place to land. Assign a reviewer, then leave a note about the decision you need.” It moves from a brief image to a concrete action.
- **Weak example:** “Unlock effortless collaboration with our revolutionary review experience.” It praises the experience without telling the reader what to do.

The explanation beside each example matters. “Use this style” leaves the writer to decide whether to copy the metaphor, sentence length or subject. Here, the intended choice is explicit: a brief metaphor followed by an action.

## How do I give Codex CLI and Claude Code the same guidance?

Keep `voice-guide.md` and the task's `facts.md` in the project folder, then point both clients to those files. Use the guide for expression and the facts for the behavior being described.

For **Codex**, add this to the project's `AGENTS.md`:

```text
Read voice-guide.md and facts.md.
Follow the guide when writing.
Use facts.md for product behavior.
Report the guide revision
and source paths you read.
```

[OpenAI documents](https://learn.chatgpt.com/docs/agent-configuration/agents-md) that Codex assembles its instructions when a run starts. This instruction explicitly asks it to read the shared files.

For **Claude Code**, add this to `CLAUDE.md`:

```text
@voice-guide.md
@facts.md

Follow the guide when writing.
Use facts.md for product behavior.
Report the guide revision
and source paths you read.
```

[Claude Code's imports](https://code.claude.com/docs/en/memory#import-additional-files) resolve relative to `CLAUDE.md`. Put the import lines directly in that file, outside a code fence.

Start a fresh session in the folder and give each client the same task and facts. For the example, ask each to write an in-app announcement explaining review reminders and the action the writer can take. The [example package](/examples/keep-brand-voice-consistent-across-ai-tools/voice-package/README.md) contains the full guides, adapters, facts and reusable prompt.

These files handle writing guidance. The [tool-connections guide](/library/articles/use-codex-claude-gemini-with-existing-tools/) covers sharing connected accounts and saved work between clients.

## How do I tell whether the drafts follow the guide?

Compare a sentence with the specific choice it should follow. Check the source path and revision actually read as well; the tool's statement that it “followed the guide” does not show whether the wording fits.

In the recorded Codex exercise, the editorial lead's fictional revision changed announcement openings from a brief metaphor to a literal action naming the reminder or reviewer. These excerpts show the difference:

::article-evidence-row{label="Opening sentence" record-heading="Example" evidence-label="Guide 1.0" decision-label="Guide 2.0"}
#evidence
“Your article is waiting in harbor.”

Codex used the brief metaphor requested by the original guide. The next sentence named the review-reminder action.

#decision
“Enable review reminders for your article while it waits for your colleague’s review.”

The later Codex opening starts with the action and names the reminder, as revision 2.0 requests.
::

The first opening fit the earlier choice. For a new announcement under revision 2.0, a useful repair is specific: replace the harbor sentence with the action the writer can take. Another literal sentence can also fit; consistency does not require every tool to return identical wording.

If the sentence follows the current choice but still leaves the reader confused, [diagnose what the passage fails to explain](/library/articles/how-to-build-ai-content-workflow-that-does-not-sound-generic/). Repeating “use our voice” will not supply a missing explanation.

## What changes when I revise the voice guide?

Update the owned guide, fix references that still select an old copy, and check the affected writing in a fresh session.

1. Save the new editorial choice with its owner and revision. In Draft Pier's revision 2.0, announcement openings become literal; the product facts stay the same.
2. Check `AGENTS.md`, `CLAUDE.md` and any saved task instructions for references to superseded copies.
3. Start a fresh session, inspect which guide it reads, and compare the affected sentence with the new choice.

Readable prose can hide a stale reference. In our Codex exercise, an adapter still pointed to guide 1.0, but Codex found guide 2.0, reported the conflict and followed it. Fixing the pointer did not rescue a bad paragraph; it directed the next run to the intended source without that workaround. The [recorded comparison](/examples/keep-brand-voice-consistent-across-ai-tools/observations.md) keeps the outputs and source reads together.

## What should stay outside the voice guide?

Keep product capabilities, schedules and limits with their factual owner. An approved expression example mentioning a note does not establish a comments feature; [check the product source](/library/articles/how-to-fact-check-ai-generated-content/) before turning that phrase into an instruction.

Apply tone to the surface it was chosen for, too. Draft Pier's playful announcement opening is no instruction to make a payment error playful. The same direct voice can tell someone what failed and how to fix it without borrowing the announcement's metaphor.
