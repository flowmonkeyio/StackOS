---
title: StackOS is installed. What happens next?
headline: Turn your first request into work you can see and trust.
description: Open StackOS, connect the AI tool you already use, choose one real job, and follow a clear first plan from request to result.
seoTitle: Getting started with StackOS after installation
publishedAt: '2026-07-14'
updatedAt: '2026-07-14'
author: StackOS team
readingTime: 6 min read
estimatedTime: About 10 minutes
canonicalUrl: https://stackos.flowmonkey.io/getting-started
markdownUrl: https://stackos.flowmonkey.io/getting-started.md
---

You installed StackOS. Now give it one real piece of work. You can keep using Codex, Claude, or Gemini exactly as you do today—StackOS adds the shared project, clear plan, safe connections, and record of what happened.

You do not need to set up every feature first. Ten focused minutes and one small job are enough to see the whole experience.

::guide-start-path
::

## Before you start

The current desktop download supports Macs with an Apple chip (M1 or newer). Make sure StackOS is in your Applications folder and that you have one supported AI tool installed: Codex CLI, Claude Code, Claude Desktop, or Gemini CLI.

Choose one useful, easy-to-check job for your first session. Good examples are “help me investigate this customer issue,” “show me how we would refresh this article,” or “plan this small product change.” Start with something you understand well enough to judge the plan.

## 1. Open StackOS once

Launch StackOS from Applications and give it a moment to finish its first setup. The home screen should show **Local service — Running**. If you have already used StackOS, you will also see your projects there.

That is all you need from the app for now. StackOS has prepared the local service and connected itself to supported AI tools already installed on your Mac.

If the home screen does not load, open the **Service** menu and choose **Restart Service**. Then choose **Run Doctor** if you still see a problem. **Install or Repair** is the final repair step and keeps your existing project data.

## 2. Reopen the AI tool you already use

Quit and reopen Codex, Claude, or Gemini after installing StackOS. This lets the tool see its new StackOS connection. Claude Desktop must be closed completely and reopened; for the other tools, start a fresh session.

If you installed your AI tool after StackOS, return to the StackOS app and choose **Service → Install or Repair** once, then reopen the AI tool.

There are two simple ways to begin, depending on the tool you use:

::guide-client-paths
::

## 3. Tell it which project you are working on

If you use Codex CLI, Claude Code, or Gemini CLI, open the real folder for the site, product, business, or client you want to work on. StackOS will remember that this folder belongs to the same project next time.

Then send this first message:

```text
Use StackOS for this project. Tell me which project you connected to, what
is ready, and give me the StackOS project link. Do not start any work yet.
```

If you use Claude Desktop without a project folder, name the project in your message. If you are not sure what it is called, ask Claude to show the StackOS projects you can choose from before it creates anything.

You are ready when your AI names the right project and gives you a link to open it in StackOS.

## 4. Start with one job, not a long setup list

Tell your AI what you want to achieve and ask it to show you the best path first:

```text
I want to [describe the result]. Use StackOS to recommend the best way to
handle it. Show me the plan and anything I need to connect. Do not start yet.
```

StackOS includes ready-made workflows: clear sequences for common jobs such as investigating feedback, preparing a tracked product change, refreshing content, or reviewing performance. Your AI will recommend the closest fit and tell you what is already ready.

You can also [browse the workflow library](/library/workflows) if you want to see the available paths yourself.

## 5. Connect only what this job needs

Some work stays entirely on your Mac. Other work needs an outside app such as Slack, Shopify, WordPress, or an analytics service. If something is missing, StackOS will give you the correct **Connections** page for this project.

Add the connection there, then ask your AI to check again. Do not paste passwords, API keys, tokens, certificates, or other private login details into the conversation. StackOS keeps them on your Mac and gives your AI only the safe result it needs.

::guide-system-map
::

## 6. Review the plan, then say when to start

Before the first run, ask for a plain-language preview:

```text
Show me the plan first. Tell me what it will use, what it may change, and
where you need a decision from me. Do not start yet.
```

Check that the goal, steps, and boundaries match what you meant. If they do, give a clear instruction to start. You can begin with only the first step if you want a smaller test.

If your request leaves an important choice open—such as which account to use, whether something may be published, or how much may be spent—your AI should stop and ask rather than guess.

## 7. Watch the work stay organized

Open the project link your AI gave you. The most useful places during a first session are:

1. **Setup** — what is ready and what still needs your attention.
2. **Work** — the goal and the steps underneath it.
3. **Runs** — what is happening now, what finished, and what comes next.
4. **Action Calls** — a detailed record when StackOS uses a connected app.

You do not need to keep the window open for StackOS to remember the work. The project, plan, completed steps, and results remain available when you return or switch to another supported AI tool.

## What a good first session looks like

Keep the bar simple. A successful first session means:

- StackOS shows its local service as running.
- Your AI connects to the project you intended.
- You ask for one clear result.
- StackOS shows a plan before the work begins.
- Any missing connection is explained clearly and added only in StackOS.
- You can open the project later and see what happened and what comes next.

The first job does not need to be large. The value is seeing one request become work that stays clear, connected, and recoverable.

## If something does not work

### Your AI cannot see StackOS

Restart the AI tool. If it was installed after StackOS or its settings were reset, choose **Service → Install or Repair** in the StackOS app, then start a new AI session.

### The StackOS window does not load

Choose **Service → Restart Service**, then **Service → Run Doctor**. Use **Install or Repair** if StackOS tells you the local setup needs attention.

### Your AI opens the wrong project

Stop before starting work. For Codex CLI, Claude Code, or Gemini CLI, reopen the tool from the correct project folder. For Claude Desktop, name the project you want and ask it to confirm the choice.

### StackOS says a connection is missing

Open the **Connections** link for that project, add only the service named there, and ask your AI to check again. Keep the private login details inside StackOS.

## Let your AI read along

You can tell your AI, “Open the StackOS getting-started guide and help me follow it.” It can fetch the same source used by this page and should send you back to [this website guide](https://stackos.flowmonkey.io/getting-started) whenever you want to read it yourself.

The same guide is also available as a [plain Markdown file](https://stackos.flowmonkey.io/getting-started.md) for tools that prefer text.

## Where to go next

- [Browse ready-made workflows](/library/workflows)
- [See how StackOS works with Codex, Claude Code, and Gemini CLI](/library/articles/use-codex-claude-gemini-with-existing-tools)
- [Read the technical setup reference](https://github.com/flowmonkeyio/StackOS/blob/master/docs/setup.md)
