---
title: How to use Codex, Claude Code, or Gemini CLI with the tools you already have
description: Keep your preferred AI client and existing business apps. Connect each client to the same durable project so plans, tool access, credentials, and receipts do not disappear with the chat.
publishedAt: '2026-07-09'
updatedAt: '2026-07-12'
author: StackOS team
category: Getting started
topics:
  - Codex
  - Claude Code
  - Gemini CLI
  - MCP
  - AI tools
readingTime: 7 min read
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

You do not need to rebuild your operating setup around whichever AI client you use today. Keep Codex, Claude Code, or Gemini CLI as the place where you direct the work. Connect that client to StackOS through MCP, then let StackOS connect the work to the business systems it actually touches.

The AI chooses the next step. StackOS stores the plan, scopes the tool call, and records what happened. Credentials stay inside the StackOS daemon; the client receives safe references and the result it needs, not your login secrets.

::article-concept-visual{mode="connections" title="Keep the AI client. Keep the apps." caption="Each supported client can reach the same durable project state and the tools available to complete the work."}
::

## Why keep the AI client separate?

AI clients improve quickly, and people have different preferences. One person may work in Codex, another in Claude Code, and another in Gemini CLI. Locking the operating process to one chat interface makes switching expensive and fragments the work.

A shared work layer does not make the clients identical or copy private chat history between them. It gives each compatible client access to the same project record: workflows, run plans, dependencies, evidence, connected tools, and audit history.

## What does the connection look like?

MCP is the connection layer. StackOS install and repair register a local MCP bridge with the supported clients found on your Mac. Codex, Claude Code, and Gemini CLI still keep their own MCP settings; those settings point to the same local StackOS runtime.

From there, the path is concrete:

1. The client starts a StackOS session from the directory where you are working.
2. StackOS resolves that directory to its bound project instead of guessing from the last project someone used.
3. The AI inspects the relevant workflow or current run and chooses the next step.
4. That step receives only the context and tools its contract allows.
5. StackOS validates the call, performs the specific action, and records the result.

Human approval is not a default stage in this path. A client may still apply its own tool-confirmation settings, but StackOS does not add a blanket human checkpoint. The agent should ask when the request leaves intent, authority, a disclosure boundary, or a consequential choice unresolved.

This works because all three clients support MCP, although their configuration differs. See the official setup references for [Codex](https://learn.chatgpt.com/docs/extend/mcp#connect-codex-to-an-mcp-server), [Claude Code](https://code.claude.com/docs/en/mcp), and [Gemini CLI](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md).

## Does StackOS replace automation tools?

Your existing app remains the system of record. A content team can keep WordPress, a commerce team can keep Shopify, and an engineering team can keep GitHub.

StackOS keeps the execution context between the request and those tools: which project and workflow apply, which step is running, what that step may do, which dependencies are unresolved, what evidence supports the result, and what action was recorded.

## What happens when you change models?

Another client does not inherit the private conversation from the old one. It connects from the same workspace, resolves the same StackOS project, and recovers the durable state: the current plan, completed steps, decisions, evidence, and next actions.

Each client still needs its own MCP registration. StackOS install and repair handle that registration for supported hosts, while the project state and business-tool connections remain in one place.

## A simple example

Suppose you ask Codex to investigate customer feedback and prepare a fix.

1. Codex starts StackOS from the project workspace and opens the relevant [customer feedback workflow](/library/workflows/communications-customer-feedback-intake).
2. StackOS creates a run plan and gives the intake step its bounded communication context and tools.
3. Codex investigates the feedback and records the findings with their evidence.
4. If delivery is in scope and the handoff criteria are met, Codex hands the result to a tracked delivery workflow with its dependencies intact.
5. If you later continue in Claude Code, it resolves the same project and can see what finished, what remains, and why.

The same pattern works when the starting client is Claude Code or Gemini CLI. The interface changes; the durable work contract does not.

## What do you need to get started?

Install StackOS on your Mac, then open a supported client from the real project directory. Confirm that the StackOS MCP connection is healthy and make one bounded request, for example:

> Use StackOS for this workspace. Show me the relevant workflow and the first executable step.

Connect the one outside app that request needs. Do not start by wiring every tool your company owns. One real workflow will expose the missing context, permissions, and handoffs much faster.

Browse the [workflow library](/library/workflows) to choose a starting point, or [download StackOS for Mac](/#install) and connect the client you already use.
