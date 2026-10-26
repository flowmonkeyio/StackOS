---
title: How can AI agents use business accounts without seeing the login?
description: See how StackOS keeps credentials in its runtime, checks a Search Console action against separate permissions, and distinguishes three ways to remove account access.
publishedAt: '2026-07-09'
updatedAt: '2026-10-07'
author: StackOS team
category: Security
topics:
  - AI agent security
  - credentials
  - local software
readingTime: 6 min read
featured: false
visual: none
searchIntent: Understand how AI agents can use connected accounts without receiving credentials
relatedWorkflows:
  - engineering-tracked-delivery
  - branding-content-production
relatedAgents:
  - branding-sanitization-reviewer
relatedArticles:
  - use-codex-claude-gemini-with-existing-tools
  - what-is-an-agentic-workflow
---

An AI agent can use a business account through an action runtime that holds the credential. The agent receives an account reference and requests a particular operation. The runtime checks the request, uses the credential, and returns the result.

In StackOS, that runtime is the local daemon. Shared authentication code resolves the credential, and the provider connector uses it to carry out the action. Both handle credential material inside the daemon; the agent-facing interface supplies safe references and account information.

Keeping the login there answers one question. The next is which actions this agent is allowed to request through that account.

## A stored account and a project's connection have different jobs

An **Account** is the reusable identity StackOS stores for a provider. A **Connection** attaches that Account to a particular project. The attachment does not make another copy of its credential.

The agent can receive the provider and account name, authentication method, saved connection status, known scopes, and an opaque credential reference. That reference lets StackOS find the credential for an authorized action. It is not the password or token itself.

Credentials belong in account setup. Putting them in a prompt, project resource, workflow file, or article would create another place that can be copied or shared. The agent only needs enough information to select the intended account and action.

A connection marked ready tells the agent about its saved setup. Access to a particular provider endpoint or property still depends on the credential's permissions. A successful account test does not establish every permission the next task might need.

## A report step cannot borrow the account's permission to write

Consider a hypothetical Search Console workflow. An operator connects a Google Account and attaches it to a project. The active step may read Search Analytics for an authorized website property. Its grant excludes sitemap submission, even if the Google Account has permission to submit one.

The agent requests the report with the property and date range, using the safe account reference. Several separate checks matter:

- **Project attachment:** this project must be allowed to use that Account.
- **Step authority:** the running workflow step must name and permit the report action.
- **Provider access:** the credential must have the required scope, and Google must permit access to the requested property.

StackOS validates the named action and its inputs, checks the workflow authority, and resolves usable credentials inside the daemon before the connector performs the business request. Google applies its own permissions to that request.

Now suppose the same reporting step asks to submit a sitemap. Its action grant does not allow that operation, so StackOS rejects the request before dispatching the sitemap action. The agent should report the missing authority. It should not broaden the step's grant just because the connected Account could perform the write.

An authorized submission step would also need the Account's sitemap write access, the required Google OAuth scope, and permission for the property. Google's [sitemap submission endpoint](https://developers.google.com/webmaster-tools/v1/sitemaps/submit) requires the `webmasters` scope and registers a sitemap URL; it does not edit the sitemap file. Google treats [sitemap submission as a hint](https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap), with no guarantee that it will download or crawl it.

This is a practical application of [NIST's least-privilege principle](https://csrc.nist.gov/glossary/term/least_privilege): give a process the resources and authority needed for its task. The reporting step can use the account for its assigned read while remaining unable to submit the sitemap. The [workflow library](/library/workflows/) shows the broader jobs in which these action boundaries sit.

One-off writes outside a workflow have their own execution contract. The caller must explicitly confirm the action and state its intent; dry runs are exempt. Authority already supplied by the operator can satisfy that contract without asking the person to approve the same action again.

## What comes back from the action

A permitted action can return private business data. Keeping the credential out of the response does not make that data public. The agent still needs to follow the project's rules for who may receive the report and which details may be shared.

Execution receipts help inspect what was requested and what result was returned. For an action that changes an external system, an error after the request may leave its effect uncertain. Check the provider's state or request status before deciding whether to repeat it. Idempotency provides retry protection, but it is not a universal guarantee that every provider will apply an operation exactly once.

### When the connection uses MCP

The authorization boundary also matters when an agent connects to a remote tool server. The [MCP authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization) describes its HTTP flow with scopes for the intended operations and tokens bound to the intended MCP server. Stdio connections are treated differently. The official [MCP security guidance](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices) prohibits token passthrough: a server accepting a token without checking that it was intended for that server, then forwarding it to a downstream API.

## Choose which access you want to remove

“Disconnect the account” can mean removing one project's access, deleting the credential from StackOS, or invalidating it at Google. Choose the operation that matches the intended effect.

| What you want to do | Operation and effect |
| --- | --- |
| Stop this project using the Account | Detach its Connection. The reusable Account and attachments to other projects remain. Running work and enabled communication profiles can still depend on this Account. You may need to assign them another Account or disable them before detaching. |
| Remove the credential stored by StackOS | Detach the Account from every project, then revoke it locally. StackOS removes the encrypted credential backing and retains a non-executable audit record. |
| Revoke or rotate the credential at its provider | Use the provider's controls or an explicitly supported provider operation. Local account removal does not generally invalidate the credential at the provider. |

Removing access does not undo completed actions or guarantee that every request already in flight is cancelled. For the Search Console example, detaching the reporting project's Connection leaves the Account available to other attached projects. If the goal is to invalidate the Google credential itself, use Google's revocation or rotation controls and check that result separately.
