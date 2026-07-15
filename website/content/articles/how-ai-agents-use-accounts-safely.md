---
title: How can AI agents use business accounts without seeing the login?
description: The model does not need the password. It needs a safe account reference, a bounded action, and a trusted execution layer that keeps credentials outside its context.
publishedAt: '2026-07-09'
updatedAt: '2026-07-12'
author: StackOS team
category: Security
topics:
  - AI agent security
  - credentials
  - local software
readingTime: 6 min read
featured: false
visual: security
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

An AI agent does not need a password or API key to use a business account. It needs three things: a safe reference to the account, authority to request a named action, and the result of that action.

The credential can stay inside a trusted action layer. The agent chooses what to request. The action layer validates the request, uses the credential, and returns a sanitized result.

This is the boundary we use in StackOS. It lets the agent work without turning its prompt, workflow state, or logs into a credential store.

::article-concept-visual{mode="security" title="The agent requests. The action layer executes." caption="StackOS keeps the credential inside its local daemon, checks the requested action, and returns a sanitized result."}
::

## What should the model receive?

The model needs enough information to choose the right connection and action:

- A provider and account profile name, or another safe reference
- Whether the connection is ready
- The capabilities and scopes available to it
- The contract for the action it wants to request

It does not need the raw token, password, private key, or OAuth refresh token. In StackOS, an opaque credential reference identifies the connection without functioning as the credential itself.

## Where does the secret stay?

StackOS runs locally on the user’s Mac. The operator enters credentials through the local admin surface, and the daemon owns their storage. When an action runs, StackOS decrypts the credential inside the provider connector. The plaintext value is not serialized into the agent-facing request or response.

That gives us a practical rule: credentials do not belong in prompts, workflow files, project resources, content artifacts, or repository configuration. All of those can be copied, logged, or shared long after the action finishes.

## What prevents an agent from doing anything it wants?

The useful question is not whether the account is connected. It is whether this agent, in this step, can request this action through this account.

In StackOS, a call passes through a concrete sequence:

1. StackOS resolves one provider profile instead of handing the agent a collection of credentials.
2. The agent names a registered action and supplies a payload that must pass that action’s contract.
3. Inside a workflow, the current step must have an explicit tool grant and a matching action reference. A research step cannot become a publishing step simply because both use the same connected account.
4. The daemon resolves the credential and calls the provider. Only the connector sees the plaintext secret.
5. Writes use idempotency protection, and the result is stored as a redacted action receipt with status, timing, and error context.

For a one-off write outside a workflow, the caller must explicitly confirm the named action and state its intent. That is an execution check, not a rule that a human must approve every agent step.

Human approval can still be added when a genuinely consequential action or missing authority calls for it. It is not the main security boundary. A broad token behind an approval click is still a broad token.

## Is local software enough by itself?

No. Running locally reduces how far secrets travel, but location is only one part of the design. Safe account access also needs narrow permissions, typed actions, grant enforcement, input validation, idempotency, redaction, revocation, and an audit trail.

The general principle is least privilege: give the agent only the resources and authority it needs for the current work. That is the same boundary described by [NIST’s definition of least privilege](https://csrc.nist.gov/glossary/term/least_privilege).

If the action layer is remote, the same boundary has to survive the network. The current [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) uses resource-bound authorization and scope minimization, while the official [MCP security guidance](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices) forbids token passthrough. A server should not accept a broad token intended for something else and simply forward it downstream.

An agent can still make a bad decision. These controls limit what it can reach, leave a receipt, and give the operator a place to revoke access or recover.

## What should teams ask before connecting an account?

Ask these questions:

1. Where is the credential entered, stored, and decrypted?
2. Can the model, a tool response, or a log ever receive the raw value?
3. Which exact account, scopes, and actions does the connection allow?
4. Which workflow steps can request each action?
5. What prevents a retry from creating a duplicate external change?
6. What receipt is recorded, and which fields are redacted?
7. How is access tested, rotated, and revoked?

If those answers are vague, the connection is too broad.

We built StackOS around this boundary because hiding a password in the interface is not enough. The important line is where the credential becomes usable, who can ask for which action, and what trace remains afterward. The [workflow library](/library/workflows) shows how those account actions fit into visible work rather than appearing as isolated tool calls.
