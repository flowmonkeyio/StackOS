---
title: How to use Codex, Claude Code, or Gemini CLI with the tools you already have
description: Keep your preferred AI client and existing business apps. Connect each client to the same durable project so plans, tool access, credentials, and receipts do not disappear with the chat.
publishedAt: '2026-07-09'
updatedAt: '2026-09-29'
author: StackOS team
category: Getting started
topics:
  - Codex
  - Claude Code
  - Gemini CLI
  - MCP
  - AI tools
readingTime: 5 min read
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

You ask your AI client to look at search traffic for your website. Before it reads a row, it needs to know which project you mean, which account it can use, and which Search Console property to query.

You can do that from Codex, Claude Code, or Gemini CLI. Connect the client to StackOS through MCP so it can call StackOS tools. Then connect Google Search Console to the project and ask for a report you can check.

Start with the [getting-started guide](/getting-started/) for installation and client setup. The walkthrough below uses one read-only report to check the connection, save a useful result, and pick up the work in another client.

## Connect the client to the right project

StackOS install and repair register its local MCP bridge with supported clients found on your Mac. Each client keeps its own settings and needs a fresh session after registration.

Open Codex CLI, Claude Code, or Gemini CLI from the real folder for the site you want to work on. Ask it to use StackOS and confirm the project name and project link. StackOS uses the folder's project binding; it does not choose whichever project someone used last. If you use a client without a project folder, such as Claude Desktop, explicitly select the intended project.

The AI chooses the next step. StackOS stores the plan, scopes the tool call, and records what happened. Provider credentials stay inside the StackOS daemon; the client receives safe references and action results. The [account-access guide](/library/articles/how-ai-agents-use-accounts-safely/) explains that boundary in detail.

::article-concept-visual{mode="connections" title="Keep the AI client. Keep the apps." caption="Each supported client can reach the same durable project state and the tools available to complete the work."}
::

## Confirm access to your Search Console property

Open the project's Connections page and add Google Search Console if it is missing. Follow the [Search Console integration guide](/library/integrations/google-search-console/) for provider setup and permissions. Keep credentials in StackOS.

A connected Google account still needs access to the particular Search Console property. Ask the agent to list the properties it can read, then use the exact property identifier from that result.

## Ask for one dated report

Replace `[SEARCH_CONSOLE_PROPERTY]` with your property. The dates below are an example of a completed reporting period; replace them with the dates you want to inspect.

```text
Use StackOS for this project. Confirm the project name and that you can
read the Search Console property [SEARCH_CONSOLE_PROPERTY].

Read web search performance from 2026-08-29 through 2026-09-25, inclusive,
using final data. Show the top 10 pages by clicks with clicks, impressions,
CTR, and average position.

Choose one existing page from that table for a closer look. Say why you
selected it, then show the queries returned for that exact page using
the same dates, search type, and data state. Explain any missing data
or limits that affect the interpretation.

Save the report, its source references, and one proposed next step in
this StackOS project. Give me the saved report or task reference.
Keep this read-only: do not edit the site or submit anything to Google.
```

The report should name the property, dates, search type, and data state, then show the page table and the selected page's query table. You should also have a reference you can use to retrieve the report later.

Check that the selected page belongs to the site you intended. Read it alongside the returned queries, then decide whether the proposed next step would help answer those searches.

Google's [Search Analytics API](https://developers.google.com/webmaster-tools/v1/searchanalytics/query) returns top rows and doesn't guarantee every row. Some queries are omitted. An absent query is not evidence of zero traffic, and a page with only a few impressions gives little basis for deciding what to change.

If the report supports an edit, the [workflow library](/library/workflows/) includes content refresh and website analysis. Ask the agent to choose the appropriate workflow and carry the report's source references into that work. Reading a report does not itself start an editing or publishing workflow.

## Continue from the saved result

On the same Mac, open another supported client with its own working StackOS connection. Use the same project folder, confirm the project, and give it the reference returned by the first client:

```text
Use the same StackOS project for this workspace. Read the saved report
or task at [SAVED_REPORT_OR_TASK_REF]. Confirm its property and reporting
dates, summarize what was completed and what remains, and show me the
proposed next step before making changes.
```

StackOS keeps the project records the agent saved: reports and evidence, plans and completed steps, decisions, and action history. Private chat transcripts and unsaved conversation context don't transfer between clients. Include any extra instruction or decision that was left only in the previous chat.

## If the result is missing or wrong

- **The client cannot see StackOS.** Start a fresh client session. If registration is missing, use the repair steps in the [getting-started guide](/getting-started/).
- **The project is wrong.** Reopen the client from the correct folder or explicitly select the intended project before continuing.
- **The property is missing or access is denied.** Check the exact property and the connected account's Search Console permissions. Reconnecting the same account won't grant it access to a property it cannot read.
- **The report has no rows.** Check the property, dates, search type, and filters before interpreting the result. Ask the agent to distinguish an empty response from a failed request.
- **The next client cannot find the work.** Confirm the project and saved reference. If the result existed only in chat, return to that session and have the agent save the report and any decisions needed to continue.
