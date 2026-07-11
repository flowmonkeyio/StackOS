---
title: How to use Codex, Claude Code, or Gemini with the tools you already have
description: Keep your preferred AI client and existing business apps. Add a shared work layer so requests become trackable plans with safe, explicit actions.
publishedAt: '2026-07-09'
updatedAt: '2026-07-09'
author: StackOS team
category: Getting started
topics:
  - Codex
  - Claude Code
  - Gemini
  - AI tools
readingTime: 6 min read
featured: true
visual: connections
searchIntent: Learn how to connect an existing AI client to existing business tools
relatedWorkflows:
  - communications-customer-feedback-intake
  - engineering-tracked-delivery
relatedAgents: []
relatedArticles:
  - what-is-an-agentic-workflow
  - how-ai-agents-use-accounts-safely
---

You do not need to replace Codex, Claude Code, Gemini, or the business apps your team already uses. Connect the AI client to StackOS, connect StackOS to your approved tools, and keep working from the interface you prefer.

This creates a clean division of responsibility: the AI understands and directs the work; StackOS organizes its plan, state, tool access, approvals, and history.

::article-concept-visual{mode="connections" title="Keep the AI client. Keep the apps." caption="StackOS connects the conversation to a visible plan and the approved tools that complete it."}
::

## Why keep the AI client separate?

AI clients improve quickly and people have different preferences. One person may work in Codex, another in Claude Code, and another through Gemini. Locking the whole operating process to one chat interface makes switching expensive and fragments the work.

A shared work layer lets the client change without losing the project’s workflows, connected apps, or history.

## What does the connection look like?

The AI client connects to StackOS through a standard tool interface. When you make a request, the model can find the relevant workflow, adapt it to the request, and present the plan.

After approval, work moves step by step. When a stage needs an outside app—such as Slack, Shopify, WordPress, an ad platform, or an analytics tool—StackOS performs the specific approved action and returns a safe result to the AI.

Your private login stays in the local StackOS process. The model receives a safe reference and the result it needs, not the secret itself.

## Does StackOS replace automation tools?

StackOS is designed to coordinate work across existing tools. It does not ask a content team to abandon WordPress, a commerce team to replace Shopify, or an engineering team to stop using GitHub.

It adds the missing continuity between the request and those tools: which workflow applies, what is currently ready, what needs review, what action was taken, and where the result belongs.

## What happens when you change models?

The durable work remains in StackOS. A new compatible AI client can recover the project, current plan, completed steps, decisions, and next actions instead of relying on the memory of one chat thread.

That makes model choice a practical preference rather than an operating-system decision.

## A simple example

Suppose you ask Codex to investigate customer feedback and prepare a fix.

1. Codex identifies the request and opens the appropriate [customer feedback workflow](/library/workflows/communications-customer-feedback-intake).
2. StackOS gathers the approved conversation context and creates visible work.
3. The investigation uses the connected communication and project tools.
4. If delivery is needed, the result moves into a tracked delivery workflow with its dependencies intact.
5. You can review what happened, what remains, and the evidence behind the conclusion.

The same pattern works when the starting client is Claude Code or Gemini.

## What do you need to get started?

Install StackOS on your Mac, connect a supported AI client, and add the apps you want it to use. Start with one real workflow that matters to your team rather than trying to automate everything at once.

Browse the [workflow library](/library/workflows) to choose a starting point, or [download StackOS for Mac](/#install) and connect the client you already use.
