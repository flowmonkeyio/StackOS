# Communications Integration Contract

Status: first executable slice delivered. Generic agent request operations,
Telegram Bot API messaging, Telegram webhook set/delete/info, project-scoped
Telegram bot profiles, Telegram secret-token ingress, Slack Web API actions,
Slack signed HTTP ingress, SMTP send, and IMAP mailbox/message lifecycle actions
are executable. Slack Socket Mode remains deferred until StackOS has a daemon
runner contract. This document owns the current contract and limitation record
for the StackOS communications layer and generic agent request inbox; it is not
a delivery task ledger.

Plan review status: signed off with minor implementation notes by sub-agent
review on 2026-05-23.

## Source Documents

Official provider and protocol references:

- Telegram Bot API: https://core.telegram.org/bots/api
- Telegram bot features: https://core.telegram.org/bots/features
- Official local Telegram Bot API server: https://github.com/tdlib/telegram-bot-api
- ngrok agent API: https://ngrok.com/docs/agent/api/
- Slack Events API: https://docs.slack.dev/apis/events-api/
- Slack Socket Mode: https://docs.slack.dev/apis/events-api/using-socket-mode/
- Slack request verification: https://docs.slack.dev/authentication/verifying-requests-from-slack/
- Slack message events: https://docs.slack.dev/reference/events/message/
- Slack `chat.postMessage`: https://docs.slack.dev/reference/methods/chat.postMessage/
- Slack Conversations API: https://docs.slack.dev/tools/python-slack-sdk/legacy/conversations/
- SMTP: https://www.rfc-editor.org/rfc/rfc5321.html
- SMTP AUTH: https://www.rfc-editor.org/rfc/rfc4954
- IMAP4rev2: https://www.rfc-editor.org/rfc/rfc9051.html

StackOS references this contract must stay aligned with:

- [Architecture](../architecture.md)
- [Action Executor](../action-executor.md)
- [Auth Providers](../auth-providers.md)
- [Operations](../operations.md)
- [Plugins](../plugins.md)
- [Project Memory](../project-memory.md)
- [Resources And Artifacts](../resources-and-artifacts.md)
- [Workflow Templates](../workflow-templates.md)
- [Connector Quality Gate](connector-quality.md)

## Architecture Decision

Communications is an input, output, and trigger layer for agents. It is not an
agent brain inside StackOS.

There are two related but separate planes:

- **Agent execution plane**: MCP, CLI, and REST entrypoints let an agent or
  script call StackOS operations/actions, create run plans, read context, and
  persist results.
- **Agent communication plane**: humans talk to an agent through a transport
  such as the local StackOS chat UI, CLI chat, Telegram, Slack, email, or a
  future provider. Those transports store messages/interactions and wake an
  agent runner through generic agent requests.

Telegram is only one communication transport. It must not become the product's
agent-chat model. A direct local "talk to the agent like this chat" experience
uses the same `communication-thread`, `communication-message`,
`communication-interaction`, and `agent_requests` contracts as Telegram, with a
local/web provider adapter instead of Telegram Bot API calls.

The aligned runtime shape is:

```text
Local chat / Telegram / SMTP / IMAP / future communication providers
-> plugin provider/action manifest
-> communication.send/reply for normal delivery, or action.run/action.execute
   for explicit provider escape hatches
-> daemon-side credential resolution
-> one provider connector call
-> normalized safe output and action-call audit
-> communication resources and optional agent_request records
-> agent prepares or claims request and links a chosen run plan
-> agent executes granted actions
-> StackOS records audit, resources, learnings, and decisions
```

StackOS may store communication records, cursors, claim state, safe provider
metadata, and static trigger configuration. StackOS must not interpret intent,
choose business actions, decide whether SEO/media/GTM work is needed, or run a
model invisibly inside the daemon.

## Provider-Neutral Communication Graph

Communication must be modeled as a graph that can span Telegram, Slack, local
chat, SMTP/IMAP email, and future transports. Telegram behavior lives as a
`telegram-bot` facet inside generic `communication-profile` records; it is not a
separate communications model.

Canonical graph:

```text
communication-profile
-> provider facets / credential profile refs
-> communication-channel surfaces
-> communication-membership permission state
-> communication-thread / communication-message / communication-event
-> communication-target / communication-route
-> agent_request
-> explicit provider action
```

The graph is configuration and state, not workflow logic. A profile can say
"support agent may send to internal-support target"; the agent still decides
whether sending is the right next action. A target can resolve to
`communications.slack-bot.message.send` or
`communications.telegram-bot.message.send`, but the normal agent-facing send
path is `communication.send` or `communication.reply`. Provider actions remain
the lower-level escape hatch when an agent intentionally needs a
provider-specific payload.

Policies are split so one concept does not silently authorize another:

- `access_policy`: which users may invoke/respond. The normal bot stance is
  broad visibility with a narrow user allowlist.
- `visibility_policy`: which surfaces may be observed and what can be stored
  without creating work.
- `trigger_policy`: DM, mention, command, email criteria, reaction, button, or
  provider event shapes that create agent requests.
- `context_policy`: what stored history can be retrieved and which fields are
  safe.
- `response_policy`: same-origin reply constraints. It applies when the agent
  is replying to an inbound request, not to proactive sends that target an
  explicit `communication-target`.
- `send_policy`: explicit outbound/handoff constraints when the project wants
  stricter limits than "approved invoker may choose any reachable target."
- `handoff_policy`: allowed movement from one surface to another.
- `approval_policy`: whether target use requires human or run-plan approval.

Default trigger stance is deny for unknown users, not for every visible channel.
Read/context operations are bounded and field-selected. Live provider history
fetches are not part of `communicationContext.query`; they must be separate
provider actions with scopes, pagination, rate-limit handling, and audit.
Practically, agents can read communication state that StackOS sent, ingested, or
stored. They cannot ask `communicationContext.query` to fetch Slack messages
that were posted while ingress was disabled, before the bot was configured, or
outside StackOS visibility. Telegram bots are even narrower: normal bot flows
receive updates going forward through webhook/polling and should not promise
arbitrary prior chat history fetch.

### Setup Secret Boundary

Communication setup operations store durable, agent-readable state. They reject
secret-like fields in `provider_facets`, `driver_config`,
`action_input_defaults`, and setup `metadata_json`; use auth profiles and opaque
`credential_ref` values for credentials instead. Resource redaction remains a
defense-in-depth output guard, not the normal setup path for secrets.

## Surface Intent And Data Scope

Communication surfaces are the main safety boundary for cross-platform work.
A Slack channel, Telegram group, email mailbox, customer DM, or local chat
thread must carry enough static context for an agent to understand where it is
acting before it reads history, forwards content, or sends a reply.

`communicationSurface.upsert` stores this provider-neutral setup on the
`communication-channel` resource:

- `audience`: `internal`, `customer`, `partner`, `vendor`, `public`, `mixed`,
  or `unknown`. This is a context label for agents and operators, not a hidden
  authorization engine.
- `intent`: durable purpose for the surface, for example
  `customer-support`, `roadmap-planning`, `customer-onboarding`, or
  `incident-review`, with a short summary of what belongs there.
- `agent_guidance`: per-surface instructions such as "customer-visible",
  "internal coordination only", escalation rules, voice overrides, or topics
  that should not be shared.
- `data_scope`: classification and sharing guidance, for example
  `internal`, `customer-confidential`, `public`, allowed target refs,
  restricted topics, and whether approval is expected before moving data to
  another surface.
- `external_context`: safe cross-system metadata such as customer safe refs,
  CRM account ids, support ticket ids, account owner refs, and public contact
  email addresses. It must not contain secrets, private tokens, or raw provider
  credentials.

This metadata deliberately does not decide the workflow. It gives the operating
agent the context needed to decide whether to use a workflow, ask for approval,
retrieve bounded history, resolve a named target, or refuse a risky handoff.

### Surface Vs Channel Terminology

Use `surface_ref` as the policy and routing identifier. It is the stable ref an
agent should pass to `communicationContext.query`, `communicationTarget.resolve`
as `source_surface_ref`, `communicationMembership.*`, `communicationRoute.*`,
and reusable templates.

`channel_ref` is retained as the stored resource's compatibility alias and may
match `surface_ref` for current records. New docs, tests, targets, routes, and
agent guidance should prefer `surface_ref`. Provider-native ids such as Slack
channel ids, Telegram chat ids, email addresses, and mailbox paths belong in
safe refs, provenance, or provider metadata after redaction; they should not be
the reusable workflow contract.

### Data Sharing And Field Policy

`data_scope` tells the agent what kind of information the surface can contain.
`communicationRoute.field_policy` tells the agent what can move between
surfaces when a handoff is configured. Both are static guidance and both should
be conservative for customer or mixed-audience surfaces.

Recommended `field_policy` keys:

- `allowed_fields`: fields safe to move automatically, for example
  `body_preview`, `summary`, `message_ref`, `sender_ref`, or `ticket_ref`.
- `redact_fields`: fields that must not be copied to the target, for example
  raw bodies, artifacts, attachments, secrets, pricing, or unrelated customer
  identifiers.
- `requires_approval_fields`: fields that require approval before sharing,
  often `raw_body_artifact_ref`, generated files, attachments, and internal
  notes.
- `customer_visible_summary`: whether the route expects a rewritten
  customer-safe summary instead of a direct quote or transcript.
- `approval_reason`: short human-readable reason shown to the agent/operator.

Example:

```json
{
  "route_ref": "communication-route:customer-acme-to-internal-support",
  "source_surface_refs": ["telegram-chat:-100123"],
  "target_refs": ["communication-target:internal-support"],
  "field_policy": {
    "allowed_fields": ["message_ref", "sender_ref", "body_preview", "ticket_ref"],
    "redact_fields": ["raw_body_artifact_ref", "attachments", "other_customer_refs"],
    "requires_approval_fields": ["generated_report_artifact_ref"],
    "customer_visible_summary": false
  }
}
```

### Audience-Aware Routing Examples

- Customer Telegram group -> internal Slack support: the customer surface has
  `audience: customer`, customer safe refs, and customer-confidential
  `data_scope`. The internal Slack target is resolved by name. The agent sends a
  summary or allowed fields internally, not a raw transcript unless the route
  permits it.
- Internal Slack -> customer email: the source surface is internal, the target
  is a customer-visible email or customer chat target, and the route should
  require a customer-safe summary plus approval for attachments or raw evidence.
- Internal roadmap channel -> operator DM: both surfaces are internal, but
  `allowed_invoker_refs` still determines who can ask the bot to send the DM.
- Mixed channel -> any customer target: treat as high risk. Use route policy,
  field redaction, and approval before moving details out of the mixed surface.

### Origin, Invoker, And Response Binding

Provider visibility is not authority to answer. Store these refs when an
inbound event creates work:

- `source_surface_ref`: where the request came from.
- `invoker_ref`: who asked, for example `slack-user:U111` or
  `telegram-user:7151482796`.
- `source_agent_request_id`: the request that caused an outbound reply or
  callback acknowledgement.
- `thread_ref` and provider message refs when same-thread replies are expected.

Same-origin replies should use the source surface/thread defaults from the
stored request. Cross-surface replies or proactive sends should use a named
target through `communication.send`; same-origin answers should use
`communication.reply`. A target may allow broad reachable destinations, but it
must never bypass the invoker allowlist or route/data-scope guidance.

### Named Target Resolution Flow

Targets are project vocabulary for destinations an agent can safely reason
about. Use names such as `internal-support`, `customer-acme-support`,
`ops-alerts`, `roadmap`, or `operator-dm` instead of raw provider ids in
workflow guidance.

The normal agent send flow is one high-level operation:

1. Check source surface intent/data-scope and any route field policy.
2. Call `communication.send` with `to`, optional `from`, content, and source
   context. Use `communication.reply` with `request_id` for same-origin replies.
3. Let StackOS resolve actor/profile, target, provider action, daemon-held
   credential, policy, capabilities, idempotency, and action audit.
4. If StackOS rejects, inspect `error.failed_paths`, `error.resolved`, and
   `error.repair`. Do not retry unchanged or silently degrade semantics.

`communicationTarget.resolve` is still useful for planning, debugging, or
provider-specific escape hatches. It does not send. It evaluates target policy
with the same target/default actor profile that `communication.send` would use
when `from` is omitted and returns `policy_profile_ref`. Pass `profile_ref`
when debugging a specific actor profile.

Example surfaces:

```json
{
  "surface_ref": "slack-channel:C0B5W8YPAKT",
  "provider_key": "slack-bot",
  "kind": "slack-channel",
  "display_name": "roadmap",
  "audience": "internal",
  "intent": {
    "category": "roadmap-planning",
    "summary": "Internal roadmap and architecture coordination."
  },
  "data_scope": {
    "classification": "internal",
    "restricted_topics": ["secrets", "raw customer exports"]
  }
}
```

```json
{
  "surface_ref": "telegram-chat:-100123",
  "provider_key": "telegram-bot",
  "kind": "telegram-supergroup",
  "display_name": "Acme support",
  "audience": "customer",
  "intent": {
    "category": "customer-support",
    "summary": "Customer-facing support group for Acme."
  },
  "agent_guidance": {
    "default_instructions": "Assume replies are customer-visible.",
    "restricted_topics": ["other customers", "internal financials"]
  },
  "data_scope": {
    "classification": "customer-confidential",
    "requires_approval_for_targets": ["communication-target:public-announcement"]
  },
  "external_context": {
    "customer": {
      "safe_ref": "customer:acme",
      "crm_account_id": "crm-account-123",
      "primary_email": "ops@acme.example"
    }
  }
}
```

## One-Brain Ingress Model

Telegram, Slack, email, local chat, and future communication plugins must share
one processing model after provider verification:

1. The provider adapter verifies the transport secret/signature and normalizes
   the payload into provider-neutral fields such as `profile_ref`,
   `surface_ref`, `user_ref`, `thread_ref`, `message_ref`, `text`,
   `interaction_ref`, and `event_type`.
2. The shared communication processor applies the profile's visibility,
   trigger, and user access policy.
3. The same processor stores communication resources and creates at most one
   `agent_request` when the normalized trigger is from an allowlisted user.
4. Provider-specific code may parse fields and map capabilities; it must not
   invent separate business rules for when a bot should answer.

The approval boundary is the invoker user, not the channel. A bot may observe any
reachable channel/DM/group when visibility allows it. If an allowlisted user tags
or DMs the bot, an agent request may be created; if any other user does the same,
the event is ignored for activation. Once an allowlisted user asks for an
outbound message, the target may be any reachable channel unless the project
adds an explicit send/handoff restriction.

### One-Brain Architecture Audit

Current status: Slack and Telegram HTTP ingress now verify provider transport,
normalize payloads, and call shared communication code for static policy
evaluation, resource writes, stable request dedupe, click-state patches, and
`agent_request` creation. The shared policy path splits visibility from
activation: a bot can observe configured visible channels, but only approved
users can create work or trigger responses. Local chat, SMTP, IMAP, outbound
action recording, and route derivation still need to converge before more
channels are added.

Current deviations to remove:

- Slack, Telegram, SMTP, and IMAP connectors write communication resources
  directly instead of returning normalized outbound effects for shared recording.
- Provider route derivation in `ingressEndpoint.*` is hard-coded for Slack and
  Telegram instead of being driven by provider/plugin manifest metadata.
- Local agent chat can create requests directly instead of feeding the same
  normalized inbound-event path.
- The UI now surfaces generic profiles, targets, and ingress state, but the
  editable setup flow is still deepest for Telegram profiles.

Required direction:

1. Refactor provider ingress modules to keep only transport verification,
   profile lookup, and provider-specific field mapping.
2. Keep provider transport details in provider facets and prevent new
   provider-specific profile resources from appearing.
3. Make connectors return normalized outbound communication effects and record
   them after the action call through shared lifecycle/audit code.
4. Move ingress route capabilities into provider manifests so adding Slack,
   Telegram, email, WhatsApp, Discord, or future channels does not require core
   route code for each provider.

## Product Boundary

StackOS owns:

- Provider catalog entries for Telegram Bot API, Slack Web API, SMTP, and IMAP.
- Typed auth setup methods and daemon-held credential storage.
- Static action contracts and connector execution.
- Generic communication resources and artifacts.
- A generic `agent_requests` queue for claimable inbound work.
- Safe status, audit, cursor, and history records.
- REST, CLI, and MCP exposure through the operation registry where the callable
  is generic StackOS infrastructure.

Agents own:

- Deciding what an inbound message means.
- Deciding whether a message should become SEO, media buying, GTM, support,
  operations, or custom work.
- Creating workflow templates or run plans.
- Selecting granted actions.
- Writing outbound replies.
- Recording learnings, decisions, observations, and outcomes.

Provider connectors own:

- Provider-specific validation.
- One documented provider operation per action.
- Credential use through `ActionConnectorRequest.credential`.
- Provider error normalization.
- Redaction of tokens, passwords, request URLs that contain secrets, and raw
  credential payloads.

Connectors do not own:

- Prompting.
- Intent classification.
- Workflow branching.
- Business policy.
- Hidden model invocation.
- Cross-provider orchestration.

## Provider Reality

### Telegram Bot API

Telegram bots can receive updates through `getUpdates` long polling or
webhooks. These modes are mutually exclusive for a bot while a webhook is set.
StackOS treats a communication profile whose `provider_facets.telegram-bot.ingress_mode`
is `webhook` as the normal listener path. The project owns one
provider-neutral public ingress endpoint; provider routes are derived from that
endpoint and the communication profiles that need webhooks. In production, the
endpoint is a deployed HTTPS URL. During local development, it can be discovered
from a local-tunnel driver whose provider is configured inside `driver_config`.
`updates.poll` remains a bounded
diagnostic/bootstrap action only, for example discovering chat/user ids before
a communication profile is locked down.

The public ingress endpoint is generic StackOS infrastructure, not a Telegram or
ngrok resource. Its public API is `driver`, `public_base_url`, `local_base_url`,
and `driver_config`. `driver=public-url` stores an explicit deployed HTTPS URL.
`driver=local-tunnel` is the local-development driver. Its provider-specific
details live only inside `driver_config`, for example `provider: ngrok` and a
`discovery_url` pointing at that provider's local agent API. Future drivers can
implement the same endpoint contract without changing provider routes or bot
profiles.

Telegram supports private chats, groups, supergroups, channels, callbacks,
edited messages, channel posts, membership updates, and other update types. The
first StackOS pass should accept a narrow `allowed_updates` list and expand only
when the provider action schema and tests cover each update type.

Telegram does not provide a normal cross-chat read receipt lifecycle for bots.
StackOS `read` and `unread` are local attention states only. They must not be
presented as Telegram-side read receipts.

Telegram bot tokens are embedded in the Bot API request path. The Telegram
connector must never expose a full request URL in logs, action-call metadata,
error messages, tests, or returned JSON.

### Telegram Rich Interaction Model

Telegram is not just text transport. The StackOS contract must support outbound
messages with buttons and media, plus inbound updates created when users press
those buttons.

Outbound capabilities are still explicit actions:

- `telegram-bot.message.send`: text message through Telegram `sendMessage`.
- `telegram-bot.photo.send`: image/photo message through Telegram `sendPhoto`.
- `telegram-bot.file.download`: download a Telegram `file_id` through
  `getFile` into a generated StackOS artifact for forwarding or inspection.
- `telegram-bot.file.upload`: send one generated artifact/file id/HTTPS URL as
  `sendPhoto` or `sendDocument`, or send 2-10 files through `sendMediaGroup`.
- `telegram-bot.callback.answer`: acknowledge an inline button callback through
  Telegram `answerCallbackQuery`.
- `telegram-bot.message.reaction.set`: set a native emoji reaction on a
  specific Telegram message through Telegram `setMessageReaction`.
- `telegram-bot.message.delete`: delete a specific Telegram message through
  Telegram `deleteMessage` when Telegram permits the bot to delete it.
- Future actions may add edit and specialized video/audio sends, but only after
  each provider method has its own schema and tests.

Button support must be modeled as payload, not workflow logic:

- `reply_markup.inline_keyboard` may contain URL buttons and callback buttons.
- Callback buttons must use short opaque `callback_data` values. Telegram caps
  callback data at 1-64 bytes, so callback data must not contain long payloads,
  secrets, raw prompts, or business decisions.
- If a callback needs local state, store it in a `communication-interaction`
  resource and put only an opaque `interaction_ref` or button token in
  `callback_data`.
- StackOS treats incoming callback data as untrusted input. The agent decides
  what it means after reading the stored message/event/interaction resources.

Image/media support has two safe paths:

- `photo.file_id` or `photo.url` when Telegram can already access the file.
- `photo.artifact_ref` for daemon-side multipart upload from a generated asset
  URI under `/generated-assets/...`. This is required for local generated
  images because Telegram cannot fetch `127.0.0.1` generated asset URLs from
  the public internet. Resolving database artifact ids can be added later
  without changing the agent-facing action shape.

Inbound callback handling uses the same webhook listener path as message
updates. The explicit webhook endpoint verifies Telegram's secret-token header,
resolves the project-scoped communication profile, stores the update idempotently, and
creates resources/requests from static policy. It still does not invoke a model.
`updates.poll` can inspect callback updates only as a bounded diagnostic action
and must not become the normal listener loop.

### Telegram Profiles

Telegram is not globally connected. A project owns one or more
`communication-profile` records:

```text
project
-> communication-profile
-> telegram credential profile
-> identity / agent guidance / access / trigger / context / response policies
```

Each profile binds to exactly one project-scoped Telegram credential profile by
`auth_profile_key`. There is no global Telegram credential, no cross-project
fallback credential, and no agent-visible token handoff. The credential stores
only bot token material, webhook secret, and safe transport endpoint
configuration such as `api_base_url`. The communication profile owns behavior and agent
setup:

- `identity`: display name, purpose, and voice. This is the bot's project-level
  identity, not the credential identity returned by Telegram `getMe`.
- `agent_guidance`: default instructions, boundaries, and escalation guidance
  attached to every agent request created by this bot.
- `access_policy`: approved invoker refs first. Numeric Telegram user ids are
  preferred; usernames are setup convenience because they can change. Chat refs
  may scope issued buttons or explicit visibility, but chat membership is not
  the primary answer restriction.
- `trigger_policy`: DM, mention, structured slash-command intents,
  reply-to-bot, callback button, or configured wake patterns. Command intents
  may carry their own description, guidance, expected inputs, and output
  expectations for the operating agent.
- `visibility_policy`: whether visible chats/channels may be observed without
  triggering a request.
- `context_policy`: bounded history selection from messages StackOS already
  stored, filtered by project/profile/chat/thread/lookback/fields.
- `response_policy`: same chat/thread defaults, invoker-only behavior,
  broadcast/DM constraints, and reply requirements for responses tied to a
  `source_agent_request_id`. Proactive target sends are governed by
  `send_policy` and target policy instead.

Setup is exposed through shared StackOS operations, not provider-specific MCP
tools:

- `communicationProfile.upsert`: creates or updates the safe communication-profile
  identity, agent guidance, trigger policy, and delivery policy after a
  project-scoped `telegram-bot` credential profile exists.
- `communicationProfile.get`: returns one safe profile, including response
  reference maps such as `reply_to_message_refs`, `thread_refs`, and
  `direct_messages_topic_refs`.
- `communicationProfile.list`: lists safe profiles for a project.

These operations are available through REST, CLI `ops call`, and MCP. The
browser UI token may call only this narrow setup mutation because it never
includes token material; provider secrets still go through the typed auth
credential setup route and remain daemon-side.

Visibility is not activation. A communication profile may observe and store messages from
any reachable group/channel/DM for future context, but StackOS creates an
`agent_request` only when a configured trigger is matched by an allowed invoker.
If a disallowed user tags or DMs the bot, StackOS may keep the message as
context but must not create a request or send a reply.

Bot API updates are not arbitrary historical chat access. Telegram keeps updates
temporarily until delivered; StackOS "history" means messages StackOS has
already observed and stored.

Telegram clients show a loading state after a callback button is pressed until
`answerCallbackQuery` is called. StackOS may perform a static configured ACK in
an ingestion runner or webhook handler, but it must be recorded through the
action/audit path and must not decide business outcome. Rich follow-up replies
remain agent-authored actions.

Outbound Telegram messages issue callback buttons as stored interaction state.
When `message.send` or `photo.send` includes callback buttons, StackOS stores a
`communication-interaction` record for each opaque `callback_data` token,
including optional allowed user/chat refs and the `source_agent_request_id` that
caused the outbound message. A later callback query can only wake an agent after
the webhook handler resolves that stored interaction and access policy permits
the click.

### Slack Provider Contract

Slack must use the same communication graph, but its provider contract is richer
than Telegram:

- Events can arrive through HTTP Events API or Socket Mode. Current StackOS
  support implements signed HTTP ingress; Socket Mode is deferred until a
  long-running daemon runner owns reconnect and ACK state. HTTP ingress must
  verify Slack's signing secret using the raw body, request timestamp,
  `X-Slack-Signature`, replay-window checks, and constant-time comparison.
- Socket Mode requires an app-level token, `apps.connections.open`,
  reconnect/refresh behavior, and acknowledgement by `envelope_id`.
- Slack event ingestion must acknowledge quickly and idempotently store events.
  Retry headers and duplicate event ids must not create duplicate agent work.
- `app_mention` is not a substitute for all messages. DMs require `message.im`;
  public channels, private channels, MPIMs, and DMs have separate event/scopes.
- `chat.postMessage` posts to public channels, private channels, MPIMs, or DMs
  only when the token/scopes and membership permit it. Threads use `thread_ts`.
- `reactions.add` adds a native Slack emoji reaction to a specific message by
  channel and timestamp. It requires `reactions:write` and stores reaction state
  as a `communication-interaction` record.
- `chat.delete` deletes a Slack message by channel and timestamp. With bot
  tokens, Slack only permits deleting messages posted by that bot.
- `conversations.open`, `conversations.info`, `conversations.list`, and
  `conversations.members` are provider actions for resolving Slack DMs,
  surfaces, and memberships. They must record safe surface/membership state
  and never expose bot/user tokens.
- Block Kit actions and buttons map to `communication-interaction` records.
  Action payloads are untrusted routing hints until the agent reads the stored
  interaction and source context.

Slack-specific support lands as explicit actions and ingress routes. Normal
agent sends should use `communication.send`/`communication.reply`; those
operations resolve to Slack provider actions internally and execute with a
daemon-resolved credential. Direct `action.run`/`action.execute` is reserved for
provider-specific Slack operations such as discovery or custom payloads.

### SMTP

SMTP sends mail. It can report that the SMTP server accepted or rejected a
message for relay. It does not prove delivery, inbox placement, open, read,
click, reply, or bounce unless additional systems provide those events.

SMTP AUTH can use username/password or app-password style credentials. OAuth or
XOAUTH2 remains deferred until the relevant mail provider contracts, scopes,
auth methods, and safe auth tests are implemented on top of the shared OAuth
core.

### IMAP

IMAP owns mailbox read/search/fetch and message flags such as `\Seen`. Read and
unread for email should be represented through IMAP flags plus StackOS local
attention state.

IMAP sync must use stable UIDs and UIDVALIDITY, not volatile sequence numbers.
Cursor resources should store enough provider metadata to detect mailbox
rebuilds and avoid duplicate ingestion.

## First-Party Plugin

Add `plugins/communications/plugin.yaml`.

Capabilities:

- `messaging`: send and receive chat-style messages.
- `email-send`: send email through SMTP or future provider APIs.
- `email-inbox`: inspect and update mailbox messages.
- `agent-triggering`: expose inbound provider events as claimable agent work.

Providers:

- `local-agent-chat`
- `telegram-bot`
- `slack-bot`
- `smtp`
- `imap`

`local-agent-chat` is the provider-neutral local conversation surface for a
user who wants to talk directly to an agent through StackOS. Telegram is a
remote transport adapter, not the only agent conversation channel.

The plugin may later add Discord, WhatsApp Business, Twilio, Gmail API,
Microsoft Graph mail, or project-local communication connectors, but those
providers need their own contract review before execution.

`slack-bot` is executable for Web API identity, message send, native message
reaction add, message delete, conversation discovery, membership sync, and
signed HTTP Events API/Interactivity ingress. Socket Mode, live history reads,
files, reaction remove, and administration remain deferred until their provider
contracts, runner lifecycle, tests, and safe audit paths are delivered. Stored
Slack reads come through StackOS communication records; live channel/thread
history and backfill require a future explicit Slack history action.

## Resource Model

Communication records should be plugin resources first. Avoid bespoke
provider-specific core tables unless a generic queue or lock invariant requires
one.

### `communication-profile`

Represents a provider-neutral agent/human-facing communication identity.

Example fields:

- `profile_ref`
- `key`
- `enabled`
- `identity`
- `agent_guidance`
- `provider_facets`: safe provider refs such as Telegram `auth_profile_key`
  and `bot_username`, or Slack `auth_profile_key`/`bot_user_id`; never token
  material
- `access_policy`
- `visibility_policy`
- `trigger_policy`
- `context_policy`
- `response_policy`
- `send_policy`
- `handoff_policy`
- `approval_policy`
- `metadata_json`

### `communication-contact`

Represents a project-local person, customer, team, bot, or organization identity
that can be linked to provider user/email refs.

Example fields:

- `contact_ref`
- `display_name`
- `kind`: `person`, `customer`, `team`, `bot`, `organization`
- `provider_refs`
- `safe_external_refs`
- `status`
- `metadata_json`

### `communication-target`

Represents a named destination alias that resolves to one explicit provider
action plus safe action defaults.

Example fields:

- `target_ref`
- `provider_key`
- `surface_ref`
- `profile_ref`
- `thread_ref`
- `action_ref`
- `action_input_defaults`
- `send_policy`
- `metadata_json`

Targets do not send messages. Agents normally pass the target key to
`communication.send`, which resolves the target, enforces policy/capabilities,
and executes one audited provider action. Agents use `communicationTarget.resolve`
only for planning/debugging or before an intentional provider-action escape
hatch.

`communicationTarget.resolve` returns provider-ready `action_input_defaults`
where StackOS can derive them safely. Slack targets include `surface_ref`,
optional `profile_ref`, and optional `thread_ref`. Telegram targets include
`chat_ref`, optional `thread_ref`, and `profile_key` when it is explicitly
stored or resolvable from a `communication-profile` Telegram facet. The
high-level delivery operation adds message body/media/callback details and
rejects if the resolved provider cannot represent them exactly.

`send_policy` may scope target use by `allowed_profile_refs`,
`allowed_invoker_refs`, `allowed_source_surface_refs`, and
`allowed_target_refs`. All supplied allowlists are enforced together. Use
`invoker_ref` on `communicationTarget.resolve` when the source request has a
human/bot actor, for example `telegram-user:7151482796` or `slack-user:U111`.
This keeps the important restriction on who is allowed to ask, while still
letting an approved user route messages to any explicitly configured target.

### `communication-route`

Represents a static cross-surface policy. Example: Telegram customer issue group
may hand off to internal Slack support channel, but public client channels
require approval before posting.

Example fields:

- `route_ref`
- `source_surface_refs`
- `target_refs`
- `allowed_profile_refs`
- `requires_approval`
- `field_policy`
- `metadata_json`

### `communication-membership`

Represents membership, role, permissions, and availability state for a profile
or contact in a communication surface.

Example fields:

- `membership_ref`
- `surface_ref`
- `member_ref`
- `provider_key`
- `membership_kind`: `profile`, `contact`, `bot`, `user`, `external`
- `status`: `joined`, `invited`, `left`, `removed`, `unknown`
- `roles`
- `permissions`: `can_read`, `can_write`, `can_reply_thread`,
  `can_open_dm`, `can_upload_files`
- `scope_status`
- `last_verified_at`
- `metadata_json`

### `communication-channel`

Represents a durable inbound/outbound communication surface.

Example fields:

- `channel_ref`
- `surface_ref`
- `provider_key`
- `credential_ref`
- `kind`: `telegram-private`, `telegram-group`, `telegram-supergroup`,
  `telegram-channel`, `slack-channel`, `slack-private-channel`, `slack-dm`,
  `slack-mpim`, `smtp-identity`, `imap-mailbox`, `local-agent-chat`
- `display_name`
- `safe_external_ref`
- `send_enabled`
- `ingest_enabled`
- `audience`: `internal`, `customer`, `partner`, `vendor`, `public`, `mixed`,
  or `unknown`
- `intent`: purpose/category/summary that tells agents what belongs on the
  surface
- `agent_guidance`: per-surface instructions and share boundaries
- `data_scope`: classification, restricted topics, and handoff/share guidance
- `external_context`: safe CRM/customer/account/ticket/contact refs
- `metadata_json`

Provider object ids may be stored in provenance or safe refs after redaction,
but reusable templates should refer to `channel_ref`, not raw Telegram chat ids
or mailbox internals.

### `communication-profile`

Represents one project-scoped communication identity and policy bundle.

Example fields:

- `key`
- `profile_ref`
- `enabled`
- `identity`
- `agent_guidance`
- `access_policy`
- `trigger_policy`
- `visibility_policy`
- `context_policy`
- `response_policy`
- `send_policy`
- `handoff_policy`
- `approval_policy`
- `metadata_json`
- `provider_facets.telegram-bot.auth_profile_key`
- `provider_facets.telegram-bot.bot_username`
- `provider_facets.telegram-bot.ingress_mode`: `webhook` for the active
  Telegram webhook listener, or `disabled` to stop storing inbound events for
  that provider facet
- `provider_facets.telegram-bot.allowed_updates`
- `provider_facets.telegram-bot.refs`

### `ingress-endpoint`

Stores the project-level public webhook endpoint used by provider ingress
routes. This is generic infrastructure, not a Telegram/Slack-specific resource.

Example fields:

- `key`
- `endpoint_ref`
- `driver`: `public-url` for deployed HTTPS, or `local-tunnel` for local tunnel
  discovery
- `enabled`
- `status`
- `public_base_url`
- `local_base_url`
- `driver_config`
- `last_refreshed_at`
- `last_synced_at`

Agents use `ingressEndpoint.routes` to inspect the exact provider webhook URLs
and `ingressEndpoint.sync` to write safe route metadata into provider profiles.
The agent never receives Telegram bot tokens, Slack signing secrets, or webhook
secret material through this resource.

### `communication-thread`

Groups messages into a conversation.

Example fields:

- `thread_ref`
- `channel_ref`
- `provider_key`
- `subject`
- `participant_refs`
- `last_message_at`
- `status`
- `metadata`

For Telegram, a thread can represent a chat or forum topic. For email, it can
represent a message thread derived from provider headers or mailbox metadata.

### `communication-message`

Normalized inbound or outbound message record.

Example fields:

- `message_ref`
- `provider_key`
- `channel_ref`
- `thread_ref`
- `direction`: `inbound` or `outbound`
- `message_type`: `text`, `html`, `media`, `command`, `callback`, `system`
- `sender_ref`
- `recipient_refs`
- `subject`
- `body_preview`
- `body_artifact_ref`
- `raw_artifact_ref`
- `content_type`
- `attachments`
- `reply_markup`
- `interaction_refs`
- `transport_status`
- `processing_status`
- `attention_status`
- `provider_status`
- `provider_message_ref`
- `provider_update_ref`
- `received_at`
- `sent_at`
- `metadata`

Message bodies may contain private or commercially sensitive content. Long or
raw bodies should be stored as artifacts with retention policy metadata. Agents
should receive previews and field-selected content unless a run explicitly needs
full text.

### `communication-interaction`

Represents interactive controls attached to an outbound message and their local
lifecycle. This keeps Telegram `callback_data` short and lets agents query the
state behind a button without putting state into the provider payload.

Example fields:

- `interaction_ref`
- `provider_key`
- `channel_ref`
- `thread_ref`
- `message_ref`
- `interaction_type`: `outbound_inline_button`, `inline_callback`,
  `reply-keyboard`, `force-reply`
- `button_key`
- `callback_data`
- `state_ref`
- `status`: `active`, `clicked`, `acknowledged`, `expired`, `ignored`
- `created_by_run_plan_id`
- `expires_at`
- `metadata`

Interaction records are static state. They do not decide what happens after a
button click.

### `communication-event`

Represents provider events that are not simply message bodies.

Examples:

- Telegram edited message.
- Telegram channel post.
- Telegram callback query.
- IMAP flag change.
- SMTP rejection.
- Future bounce/webhook event.

Example fields:

- `event_ref`
- `provider_key`
- `channel_ref`
- `message_ref`
- `interaction_ref`
- `event_type`
- `event_status`
- `provider_event_ref`
- `occurred_at`
- `metadata`

### `communication-cursor`

Stores provider sync position.

Telegram examples:

- `profile_key`
- `auth_profile_key`
- `ingress_mode`
- `last_update_id`
- `allowed_updates`
- `pending_update_count`
- `last_webhook_at`

IMAP examples:

- `credential_ref`
- `mailbox_ref`
- `uidvalidity`
- `last_seen_uid`
- `last_sync_at`
- `search_query`

Cursor records are static state. They do not decide what work should happen.

## Core Agent Request Queue

Add `agent_requests` as generic core infrastructure. It is not a communications
table and should be usable later by webhooks, filesystem watchers, scheduled
jobs, CI events, Slack, or project-local tooling.

The queue exists because agents need a clean way to ask "what needs my
attention?" without each provider inventing a different polling model.

Suggested table fields:

- `id`
- `project_id`
- `request_key`
- `title`
- `body_preview`
- `source_provider`
- `source_kind`
- `source_resource_key`
- `source_resource_record_id`
- `source_message_ref`
- `priority`
- `status`
- `attention_status`
- `claimed_by`
- `claim_token_hash`
- `claimed_at`
- `claim_expires_at`
- `run_plan_id`
- `completed_at`
- `ignored_at`
- `metadata_json`
- `created_at`
- `updated_at`

Suggested statuses:

- `new`
- `claimed`
- `run-created`
- `run-started`
- `responded`
- `resolved`
- `ignored`
- `failed`

Suggested attention states:

- `unread`
- `read`
- `archived`

Queue operations are generic StackOS operations, registered once and exposed
through REST, CLI, and MCP where appropriate:

- `agentRequest.list`
- `agentRequest.get`
- `agentRequest.create`
- `agentRequest.claim`
- `agentRequest.prepareRunPlan`
- `agentRequest.release`
- `agentRequest.linkRunPlan`
- `agentRequest.complete`
- `agentRequest.ignore`

Operation policy:

- `list` and `get` are read-only project operations.
- `claim` and `release` are bootstrap work-queue operations, not provider calls.
- `create` is allowed for daemon ingestion paths and granted run-plan steps.
- `create` must not be exposed as an unrestricted bootstrap write; a caller
  without a run token can create requests only through a trusted daemon
  ingestion path with explicit static configuration.
- `linkRunPlan`, `complete`, and `ignore` should require either a valid claim or
  a run token associated with the linked run plan.
- `claim` should require a stable caller identity, an idempotency key or replay
  protection, and a lease/expiration so abandoned requests can be recovered.
- `release` should require the active claim token or an admin/system override.
- None of these operations may call Telegram, SMTP, IMAP, or any provider API.
- None of these operations may expose secrets.

## Communication Platform Operations

The generic communication setup/read operations are registry-backed operations
available through MCP, REST, and CLI:

- `communicationProfile.upsert/get/list`: provider-neutral communication
  identity and policy setup.
- `communicationSurface.upsert/list`: safe surface metadata, stored on the
  `communication-channel` resource for current repo alignment.
- `communicationContact.upsert/list`: safe cross-provider person, customer,
  team, bot, or organization refs.
- `communicationMembership.upsert/list`: membership, role, permission, and
  scope state for profiles/contacts/bots inside surfaces.
- `communicationTarget.upsert/list/resolve`: named destination aliases that
  resolve to one explicit provider action ref plus safe defaults.
- `communicationRoute.upsert/list`: static handoff policy between source
  surfaces and named targets, including field/data-sharing guidance.
- `communicationContext.query`: bounded stored-history lookup for agents.
- `communication.send`: normal provider-neutral outbound delivery to a named
  target. Media-bearing Slack sends resolve to `slack-bot.file.upload` so text
  and files are completed as one Slack file-upload message; media-bearing
  Telegram sends resolve to `telegram-bot.file.upload` so one file uses a
  captioned message and multiple files use Telegram media group semantics.
- `communication.reply`: normal provider-neutral reply to the origin of a
  stored agent request.

Setup/read operations do not execute provider APIs. `communication.send` and
`communication.reply` execute exactly one resolved provider action through the
same daemon-side action executor and audit ledger; agents do not receive
secrets or raw credentials. `communicationTarget.resolve` returns `allowed`,
`denial_reason`, `action_ref`, `surface_ref`, and `action_input_defaults` for
planning/debugging. `communicationContext.query` returns stored StackOS
communication-message records only. Live Slack history, Telegram updates, IMAP
fetches, or Gmail/Graph reads must be explicit provider actions with their own
scopes, pagination, rate limits, and audit records.

High-level communication operations reject by default. Unsupported controls,
attachments, private delivery, threading, notification flags, or provider
action variants return a structured error with `effect: none`,
`same_input_will_fail`, failed JSON paths, safe resolved route details, and
repair options. This is intentional: agents are the users, and an unexpected
mechanical degradation can leak data or create the wrong visible message.

## Status Model

Do not collapse provider delivery, local processing, and attention state into a
single overloaded status.

### Transport Status

Provider or protocol-level state:

- `received`
- `stored`
- `send_submitted`
- `accepted`
- `rejected`
- `failed`
- `bounced`
- `unknown`

SMTP `accepted` means accepted by the SMTP server. It does not mean delivered or
read.

### Processing Status

StackOS/agent workflow state:

- `new`
- `claimed`
- `run-created`
- `run-started`
- `responded`
- `resolved`
- `ignored`
- `failed`

### Attention Status

Local attention state:

- `unread`
- `read`
- `archived`

For IMAP-backed email, `attention_status` can be derived from or synchronized
with `\Seen` when the user explicitly grants mark-seen/mark-unseen actions. For
Telegram, this is only StackOS-local state.

### Provider Status

Provider-specific structured metadata:

- Telegram `update_id`, `message_id`, chat type, allowed update type, callback
  query id/data, originating message ref, safe user/chat refs, and safe request
  metadata.
- IMAP UID, UIDVALIDITY, flags, mailbox, internal date, and safe headers.
- SMTP response code, enhanced status code where present, server id where safe,
  and accepted/rejected recipient counts.

## Auth Contracts

Agents receive `provider_key`, `credential_ref`, `profile_key`,
`auth_method_key`, connection status, safe account metadata, scopes/permissions,
and safe diagnostics. They never receive tokens, passwords, refresh tokens,
authorization headers, webhook secrets, or raw credential payloads.

### Telegram Bot Auth

Provider key: `telegram-bot`

Auth method: `bot-token`

Telegram credentials are project-scoped credential profiles bound from
`communication-profile.provider_facets.telegram-bot.auth_profile_key`. Agents
and action payloads name the communication profile, not a raw credential. The
daemon resolves the credential server-side and rejects profile/credential
mismatches.

Safe config fields:

- `api_base_url`: optional Bot API base URL, commonly
  `http://127.0.0.1:8081` when using the official local Telegram Bot API
  server.

Secret fields:

- `bot_token`
- `webhook_secret_token`

Bot behavior fields such as identity, agent guidance, command intent guidance,
allowed users, optional visible chat constraints, trigger patterns, context
windows, and response constraints belong to
`communication-profile` resource records, not credentials.

Credential tests:

- `getMe` should verify token validity and return safe bot identity.
- Do not include the token-bearing request URL in diagnostics.

### Slack Bot Auth

Provider key: `slack-bot`

Auth method: `bot-token`

Slack credentials are project-scoped credential profiles bound from
`communication-profile.provider_facets.slack-bot.auth_profile_key`. Agents and
action payloads name the communication profile, surface, channel, user, thread,
or target refs; they never receive Slack tokens or signing secrets.

Safe profile/account metadata may include:

- `team_id`
- `app_id`
- `bot_user_id`
- `profile_key`

Current setup fields:

- `bot_token`
- `signing_secret`

`app_token` is reserved for future Socket Mode support and is not part of the
current connection setup form.

Credential tests:

- `auth.test` verifies the bot token, returns safe team/user/bot metadata, and
  syncs that metadata onto the credential account record.
- Do not include bearer tokens, signing secrets, `response_url`, `trigger_id`,
  or raw Slack payload secrets in diagnostics or resources.

Profile behavior fields such as identity, agent guidance, access policy,
trigger rules, context windows, response policy, send policy, and handoff policy
belong to `communication-profile` records. The credential only stores Slack app
credential material and safe account metadata.

### SMTP Auth

Provider key: `smtp`

Auth method: `smtp-password`

Safe config fields:

- `host`
- `port`
- `tls_mode`: `starttls`, `ssl`, or `none`
- `username`
- `from_email`
- `from_name`
- `reply_to`
- `timeout_s`

Secret fields:

- `password`

Credential tests:

- Connect and authenticate without sending a message.
- Return safe server capability and TLS/auth status where available.
- Do not return passwords, raw auth exchanges, or full server transcripts.

Deferred auth methods:

- OAuth/XOAUTH2 until the relevant mail provider contract, scope mapping, auth
  method, and safe auth test are implemented.

### IMAP Auth

Provider key: `imap`

Auth method: `imap-password`

Safe config fields:

- `host`
- `port`
- `tls_mode`: `ssl`, `starttls`, or `none`
- `username`
- `default_mailbox`
- `mailbox_refs`
- `search_limit`

Secret fields:

- `password`

Credential tests:

- Connect, authenticate, select the default mailbox, and return safe mailbox
  capability/status metadata.
- Do not return raw mailbox transcripts or message bodies.

Deferred auth methods:

- OAuth/XOAUTH2 until the relevant mail provider contract, scope mapping, auth
  method, and safe auth test exist.

## Action Contracts

Provider operations must be plugin actions executed through `action.run` for one
explicit direct call or `action.execute` inside a granted run-plan step. Normal
agent messaging goes through `communication.send`/`communication.reply`, which
resolve and audit one provider action internally. Do not add provider-specific
MCP tools such as `telegram.sendMessage` or
`smtp.sendEmail`.

### Telegram Actions

Connector file: `stackos/actions/telegram_bot.py`

Action refs:

- `communications.telegram-bot.identity.get`
- `communications.telegram-bot.message.send`
- `communications.telegram-bot.photo.send`
- `communications.telegram-bot.file.download`
- `communications.telegram-bot.file.upload`
- `communications.telegram-bot.callback.answer`
- `communications.telegram-bot.message.reaction.set`
- `communications.telegram-bot.message.delete`
- `communications.telegram-bot.updates.poll`
- `communications.telegram-bot.webhook.set`
- `communications.telegram-bot.webhook.delete`
- `communications.telegram-bot.webhook.info`

Executable in the current Telegram connector:

- `identity.get`
- `message.send`
- `photo.send`
- `file.download`
- `file.upload`
- `callback.answer`
- `message.reaction.set`
- `message.delete`
- `updates.poll`
- `webhook.set`
- `webhook.delete`
- `webhook.info`

- `file.download` uses Telegram `getFile`, downloads the returned provider
  file path into a generated StackOS artifact, returns only safe artifact and
  source file refs, and never returns token-bearing file URLs.
- `file.upload` sends one artifact/file id/public HTTPS URL as `sendPhoto` or
  `sendDocument` with caption in the same Telegram message. For 2-10 files, it
  uses `sendMediaGroup`; the first media item carries the caption and Telegram
  returns multiple message refs. Transient generated artifacts may be deleted
  after successful upload.
- edit message.
- channel administration.
- database artifact-id resolution for `photo.artifact_ref`; generated asset
  URIs are supported now.

Validation rules:

- `message.send` requires explicit `profile_key`, `chat_ref` or
  provider-safe `chat_id` resolved from resources, plus explicit text payload.
- `message.send`, `photo.send`, `callback.answer`, and webhook actions must
  resolve the communication profile server-side and verify that the profile's
  `auth_profile_key` matches the daemon-resolved credential.
- If the communication profile's response policy requires origin binding, outbound
  `message.send` and `photo.send` must include `source_agent_request_id`; the
  connector verifies the request's communication profile, chat, thread, and source message
  before sending.
- `message.send` and `photo.send` may include `reply_markup`. Inline keyboard
  callback buttons must keep `callback_data` within Telegram's 1-64 byte limit
  and must not contain secrets.
- When `reply_markup` includes callback buttons, StackOS stores outbound
  `communication-interaction` records keyed by communication profile, provider message
  ref, and callback token so repeated `callback_data` values on different
  messages cannot overwrite one another. `source_agent_request_id`, when
  supplied, ties the button state back to the originating request and defaults
  callback access to that request's invoker/chat unless the action supplies a
  narrower static scope.
- `photo.send` requires exactly one of `photo.file_id`, `photo.url`, or
  `photo.artifact_ref`. URL sends require a public HTTPS URL. Local/generated
  assets require daemon multipart upload from a `/generated-assets/...` URI.
- `callback.answer` requires `callback_query_id`. It may include notification
  text, alert mode, URL, and cache time, but it must not claim work was
  completed unless the agent actually completed it.
- `message.reaction.set` requires `profile_key`, `message_ref`, and an emoji.
  The connector resolves the project communication profile, verifies the
  daemon-held credential profile, enforces the chat allow/deny policy derived
  from `message_ref`, calls Telegram `setMessageReaction`, and stores a
  `communication-interaction` audit record.
- `message.delete` requires `profile_key` and `message_ref`. The connector
  enforces the same project/profile/chat boundary, calls Telegram
  `deleteMessage`, and marks the stored outbound `communication-message`
  deleted when StackOS has that record.
- `updates.poll` requires explicit `profile_key`, bounded `limit`,
  `timeout_s`, and `allowed_updates`. It is profile-bound diagnostic/bootstrap
  access, not a background listener.
  `callback_query` may be included only to inspect callback update delivery
  during setup/debugging.
- If webhook is set at Telegram, polling is invalid per Telegram contract.
  StackOS does not run polling as the normal listener path.
- `webhook.set` sends Telegram `setWebhook` with the profile-bound
  `webhook_url`, optional `allowed_updates`, optional `drop_pending_updates`,
  and the daemon-side `webhook_secret_token` as Telegram `secret_token`.
  Public webhook hosts must be explicitly allowlisted on the communication profile;
  loopback hosts are only for the official local Bot API server flow.
- `webhook.delete` and `webhook.info` call Telegram `deleteWebhook` and
  `getWebhookInfo` through the same profile-bound credential.
- Returned provider error metadata must redact token-bearing URLs.

### Slack Actions

Connector file: `stackos/actions/slack_bot.py`

Action refs:

- `communications.slack-bot.identity.get`
- `communications.slack-bot.message.send`
- `communications.slack-bot.file.upload`
- `communications.slack-bot.reaction.add`
- `communications.slack-bot.message.delete`
- `communications.slack-bot.conversation.open`
- `communications.slack-bot.conversation.info`
- `communications.slack-bot.conversation.list`
- `communications.slack-bot.conversation.members`

Executable in the current Slack connector:

- `identity.get` through Slack `auth.test`
- `message.send` through Slack `chat.postMessage`
- `file.upload` through Slack `files.getUploadURLExternal` and
  `files.completeUploadExternal`
- `reaction.add` through Slack `reactions.add`
- `message.delete` through Slack `chat.delete`
- `conversation.open` through Slack `conversations.open`
- `conversation.info` through Slack `conversations.info`
- `conversation.list` through Slack `conversations.list`
- `conversation.members` through Slack `conversations.members`

Validation rules:

- `message.send` requires `channel_ref` or `surface_ref` plus `text` or
  `blocks`. It may include optional `profile_ref` to bind outbound message,
  channel, and interaction state to a communication profile; the connector
  resolves that profile server-side and rejects the call unless
  `provider_facets.slack-bot.auth_profile_key` matches the daemon-resolved
  credential profile. If omitted, the credential profile key is used as the
  state owner.
- Slack Block Kit button values are opaque routing tokens only. They must not
  contain credentials, bearer strings, prompts, secrets, or business decisions.
- `message.send` stores outbound `communication-message` records and stores
  outbound button `communication-interaction` records scoped by communication
  profile, message ref, block id, action id, and value.
- `file.upload` requires one or more generated asset `artifact_ref` values. It
  uploads bytes through Slack's external upload flow, completes the upload with
  `initial_comment`, channel, and optional `thread_ts`, stores one outbound
  file-upload `communication-message`, and can delete transient generated
  artifacts after successful upload. Text plus files must be one Slack file
  upload completion, not a separate text message plus file messages.
- `reaction.add` requires a `message_ref` and Slack emoji `name`; optional
  `channel_ref`/`surface_ref` may override the channel resolved from
  `message_ref` when a safe profile ref map is used. It stores a
  `communication-interaction` record scoped by profile, message ref, and
  reaction name.
- `message.delete` requires `message_ref`; optional `channel_ref`/`surface_ref`
  may override the channel resolved from `message_ref`. It marks the matching
  stored `communication-message` as deleted when StackOS has the record.
- `conversation.open`, `conversation.info`, and `conversation.list` store safe
  communication-profile-scoped `communication-channel` metadata.
  `conversation.members` stores safe communication-profile-scoped
  `communication-membership` refs.
- List/member operations expose bounded limits and Slack cursors in safe output
  metadata. They do not fetch live history.
- Provider errors redact Slack token-shaped strings and authorization material.

Deferred until separate tests/contracts:

- Socket Mode listener and `apps.connections.open` runtime.
- Slack history reads, reaction remove, user/profile lookup,
  channel administration, and message update.
- Automatic response URL usage. Slack `response_url` is transient sensitive
  material and is not persisted by ingress.

### SMTP Actions

Connector file: `stackos/actions/smtp.py`

Action refs:

- `communications.smtp.email.send`

Validation rules:

- Require explicit recipients.
- Require subject and either `text` or `html` body content.
- Require from identity from safe credential config or explicit allowed
  `from_ref`.
- Enforce max recipient count in schema.
- Return accepted/rejected recipient counts and safe SMTP status metadata.
- Do not claim delivery/read/open state.
- Persist an outbound `communication-message` resource for the accepted/rejected
  submission record only.

### IMAP Actions

Connector file: `stackos/actions/imap.py`

Action refs:

- `communications.imap.mailbox.list`
- `communications.imap.messages.search`
- `communications.imap.message.fetch`
- `communications.imap.message.mark_seen`
- `communications.imap.message.mark_unseen`

Validation rules:

- Use mailbox refs and UIDs.
- Reject sequence-number-only operations.
- Bound search limit and fetch body size.
- Let agents request only selected fields unless full body/artifact storage is
  explicitly needed.
- Mark-seen and mark-unseen are write actions and need approval/grant coverage.
- Persist mailbox cursor/channel/message/event resources from connector output
  without exposing IMAP passwords.

## Trigger And Ingestion Modes

### Normal Telegram Listener: Webhook

Current webhook endpoint:

```text
POST /api/v1/ingress/telegram/{project_id}/{profile_key}
Header: X-Telegram-Bot-Api-Secret-Token: <configured webhook_secret_token>
```

This endpoint is bearer-token whitelisted because Telegram cannot send the
daemon bearer token. It resolves the `communication-profile`, verifies the
Telegram secret-token header against the encrypted `telegram-bot` credential
bound by that profile's `provider_facets.telegram-bot.auth_profile_key`, and
then applies communication-profile policy. For local development, expose the
loopback daemon through the configured project ingress endpoint, for example a
local tunnel provider. Production uses a deployed HTTPS endpoint with the same
StackOS route shape.

Flow:

1. Operator creates a Telegram credential profile with server-side bot token and
   webhook secret fields.
2. Operator or setup agent calls `communicationProfile.upsert` to create a
   project-scoped `communication-profile` whose
   `provider_facets.telegram-bot.auth_profile_key` points at that credential
   profile.
3. Operator defines bot identity, default agent guidance, access policy, and
   optional structured command intents on the communication profile.
4. Operator keeps `provider_facets.telegram-bot.ingress_mode: webhook` and sets
   an explicit `provider_facets.telegram-bot.allowed_updates` list on the
   communication profile.
5. Operator configures `ingressEndpoint` with the deployed HTTPS URL, or uses
   `driver=local-tunnel` with provider details in `driver_config`, then runs
   `ingressEndpoint.sync`.
   Telegram webhook application can be dry-run first and then applied through
   daemon-held credentials.
6. The listener verifies Telegram secret token against the daemon-held
   credential bound by `auth_profile_key`.
7. The listener rejects the wrong project, communication profile, or secret with the same
   invalid-secret response.
8. The listener applies communication-profile update/chat visibility policy. Blocked
   chats, disabled profiles, and no-store non-triggers write nothing.
9. The listener upserts `communication-event`, `communication-message`, and
   `communication-interaction` records by communication-profile-scoped provider ids.
10. The listener creates or replays one idempotent generic `agent_requests` row
   only when trigger policy matches and invoker access policy allows it.
11. The listener copies safe identity, agent guidance, context policy, response
   policy, and matched command guidance into request metadata for the operating
   agent.
12. The listener does not call a model and does not infer business intent.

Rules:

- Webhook endpoints must be explicitly authenticated/verified.
- Token-bearing provider URLs must not be logged.
- Webhooks must be idempotent by communication-profile-scoped provider update id/event id.
- Visibility is not activation: observed messages may become context without
  creating agent requests.
- Webhooks must preserve the action-call audit path for outbound ACKs.
- Webhooks do not invoke a model directly.
- Webhook management is executable through
  `communications.telegram-bot.webhook.set`,
  `communications.telegram-bot.webhook.delete`, and
  `communications.telegram-bot.webhook.info`.

### Normal Slack Listener: Signed HTTP Ingress

Current Slack HTTP ingress endpoint:

```text
POST /api/v1/ingress/slack/{project_id}/{profile_key}
Headers:
  X-Slack-Request-Timestamp: <unix seconds>
  X-Slack-Signature: v0=<hmac>
```

This endpoint is bearer-token whitelisted because Slack cannot send the daemon
bearer token. It resolves a `communication-profile`, reads the profile's
`provider_facets.slack-bot.auth_profile_key`, verifies Slack's raw-body HMAC
signature against the encrypted Slack signing secret, and then applies static
profile policy. Invalid profile, credential, timestamp, or signature failures
all return the same invalid-signature class of response.

Flow:

1. Operator creates a project-scoped `slack-bot` credential profile with
   `bot_token` and `signing_secret`.
2. Operator or setup agent calls `communicationProfile.upsert` to create a
   project-scoped communication profile with Slack identity, safe bot refs,
   access policy, trigger policy, context policy, response policy, send policy,
   and `provider_facets.slack-bot.auth_profile_key`.
3. Operator configures both Slack app URLs to the profile-specific ingress URL:
   Event Subscriptions for message/mention events and Interactivity & Shortcuts
   for Block Kit button clicks.
4. Slack sends Events API JSON or Interactivity form payloads.
5. The listener verifies timestamp freshness, raw-body signature, and profile
   credential binding before parsing intent-relevant fields.
6. URL verification returns the challenge without storing resources.
7. The listener applies surface visibility policy first, then trigger policy,
   then invoker access policy. Blocked surfaces can write nothing; allowed
   observed messages can become context without creating work.
8. The listener upserts `communication-event`, `communication-message`, and
   `communication-interaction` records by communication-profile-scoped provider
   ids.
9. Block action clicks create agent requests only when the click matches a
   stored outbound `communication-interaction`, unless the profile explicitly
   allows unknown interactions for a setup/debug case.
10. The listener creates or replays one idempotent generic `agent_requests` row
    only when trigger policy matches and invoker access policy allows it.
11. The listener copies safe identity, agent guidance, context policy, response
    policy, matched command guidance, surface refs, and invoker refs into
    request metadata for the operating agent.
12. The listener does not call Slack, does not call a model, does not use
    `response_url`, and does not infer business intent.

Rules:

- Slack signing verification must use the raw request body and constant-time
  compare with a five-minute replay window.
- Retry headers and duplicate event ids must not create duplicate agent work.
- Interactivity payloads must be acknowledged quickly by returning from ingress;
  provider follow-up work stays in explicit agent actions.
- `response_url` and `trigger_id` are transient sensitive values and must not be
  persisted.
- Slack HTTP ingress is the current normal listener. Socket Mode remains
  deferred until a daemon runner owns app-token connection lifecycle.

### Diagnostic Telegram Poll

`communications.telegram-bot.updates.poll` is executable, but only as bounded
diagnostic/bootstrap access. It must require `profile_key`, `limit`,
`timeout_s`, and `allowed_updates`, and it must resolve the same
`auth_profile_key` binding as webhook ingress. It may help an operator discover
safe chat/user refs or inspect a provider issue while no Telegram webhook is
set. It must not run as a daemon listener, scheduled background poller, or
normal agent-request source.

### Static Scheduled Ingestion Runner

Scheduled ingestion remains useful for providers such as IMAP, or for future
static maintenance jobs that run inside audited StackOS run plans. For Telegram,
the scheduled runner is not the normal listener path. Any Telegram provider call
from a runner must be explicit, granted, bounded, and audited; it must not infer
intent beyond communication-profile policy, and agents still claim requests and decide
what to do.

## Agent Flow Examples

### Direct Local Agent Chat

```text
User opens local StackOS agent chat
-> localAgentChat.createMessage creates or reuses communication-thread
-> user message is stored as communication-message
-> operation creates generic agent_request when requested
-> agent runner claims the request
-> agent reads thread/context, creates run plans or calls actions as needed
-> agent writes response communication-message with content blocks, artifacts,
   and optional communication-interaction records for buttons/controls
-> UI renders text, images, files, and buttons
-> button click stores a communication-interaction event
-> StackOS creates another generic agent_request for the agent runner
```

Rules:

- StackOS stores the conversation and interactions; the agent runner owns model
  invocation and decisions.
- Direct chat buttons use the same opaque interaction model as Telegram
  callbacks. The button payload is a handle to stored context, not the decision.
- Direct chat can render richer UI than Telegram, but outbound content should
  still normalize into provider-neutral message blocks and artifacts so other
  transports can reuse it.
- A local chat runner may be bundled later, but it must still use the same
  action registry and run-plan grants as any external agent.
- `localAgentChat.createMessage` is the current executable local-chat ingress
  path across REST, CLI `ops call`, and MCP. It stores resources and creates
  agent work only; it does not invoke a model.
- Agent responses in local chat use the same operation with
  `direction=outbound`, the same `thread_key`, a new `message_key`, and
  `create_request=false`. This stores an outbound `communication-message` and
  does not create another `agent_request`.

### Telegram DM Trigger

```text
User sends DM to bot
-> webhook ingress receives Telegram message update
-> StackOS stores communication-message
-> allowlist creates agent_request
-> agentRequest.list shows unread request
-> agent calls agentRequest.prepareRunPlan with a chosen template or run plan
-> agent executes needed actions
-> agent replies with communication.reply
-> agent completes request
```

### Telegram Group Mention

```text
Message appears in a visible group
-> update type passes profile configuration
-> user_ref passes static allowlist
-> StackOS stores message and source chat metadata
-> agent_request includes group/thread/message refs
-> agent prepares or claims request and decides if action is needed
```

The connector must not decide that a group message is actionable unless the
configured trigger and user allowlist say it should become a request. The target
chat is context, not the approval boundary. Even then, the agent decides the
workflow.

### Telegram Inline Button Flow

```text
Agent sends message with inline keyboard
-> communication.send/reply resolves and calls the Telegram provider action
-> StackOS stores outbound communication-message and interaction refs
-> user presses button
-> webhook ingress receives callback_query
-> StackOS stores communication-event and marks interaction clicked
-> optional static callback.answer clears Telegram client loading state
-> allowlist creates agent_request with event/interaction refs
-> agent prepares or claims request and decides follow-up
-> agent may answer callback, edit buttons, send photo/text, or run other tools
```

Callback data is a routing hint, not trusted workflow logic. If the click should
mean "approve budget" or "generate variants", the agent must verify the linked
project/run/resource context before acting.

### Telegram Image Reply

```text
Agent generates or selects image artifact
-> if public HTTPS URL exists, action uses photo.url
-> if local generated asset exists, connector uploads photo_artifact_ref by multipart
-> Telegram returns sent Message
-> StackOS records outbound communication-message with provider_message_ref
```

The action result may include provider file ids and message ids, but it must not
return a token-bearing URL or local secret path.

### Simulated End-To-End Flows

These traces are local/mockable flows for policy and storage behavior. They
describe what StackOS records; they do not imply daemon-side model execution.

Allowed DM:

```text
1. Webhook ingress receives a private message for project A / communication profile support.
2. profile support resolves `provider_facets.telegram-bot.auth_profile_key`
   server-side.
3. access_policy allows the chat and user; trigger_policy allows DM.
4. StackOS stores communication-event and communication-message.
5. StackOS creates one agent_request with profile_key, chat_ref, and source_message_ref.
```

Allowed group mention with history context:

```text
1. Webhook ingress receives a group message that mentions @support_bot.
2. profile support can observe the group and allows the user plus mention trigger.
3. StackOS stores the new message and selects bounded stored history by context_policy.
4. StackOS creates one agent_request with group/thread refs and context hints.
5. The agent claims the request and decides whether the history changes the response.
```

Observed non-trigger:

```text
1. Webhook ingress receives a visible group message without mention, command, or reply-to-bot.
2. visibility_policy permits storing non-trigger messages.
3. StackOS stores the message with observed policy status.
4. StackOS creates no agent_request.
```

No-store non-trigger:

```text
1. Webhook ingress receives a visible group message without a configured trigger.
2. visibility_policy.store_non_trigger_messages is false.
3. StackOS writes no communication records for the update.
4. StackOS creates no agent_request.
```

Unauthorized user:

```text
1. Webhook ingress receives a trigger from a visible chat but a disallowed user.
2. StackOS verifies the communication profile and secret before applying user policy.
3. StackOS may store the event/message as invoker_blocked context.
4. StackOS creates no agent_request.
```

Outbound reply tied to `source_agent_request_id`:

```text
1. Agent claims agent_request 42 from communication profile support and chat telegram-chat:100.
2. Agent calls communication.reply with request_id 42 and message content.
3. StackOS resolves support's Telegram facet `auth_profile_key` and verifies request/chat/thread origin when response_policy requires it.
4. Telegram sendMessage executes through the daemon action executor with daemon-held credentials.
5. StackOS records the outbound communication-message and action-call audit.
```

Proactive target send:

```text
1. Agent calls communication.send with an explicit target such as telegram-operator-dm.
2. StackOS verifies the target send_policy, actor profile, surface, and credential.
3. Provider action executes without requiring source_agent_request_id, because this is not a same-origin reply.
4. StackOS records the outbound communication-message and action-call audit.
```

Authorized callback:

```text
1. Agent sends message telegram-message:100:501 with callback_data ixn_123 and
   allowed_user_refs.
2. StackOS stores a communication-interaction keyed by support /
   telegram-message:100:501 / ixn_123.
3. Webhook ingress receives callback_query ixn_123 from the allowed user/chat.
4. StackOS resolves the stored interaction by communication profile, provider message ref,
   and callback token, then marks it clicked.
5. StackOS creates one agent_request with event_ref and interaction_ref; the
   agent can read the interaction's source_agent_request_id.
```

Unauthorized callback:

```text
1. Webhook ingress receives callback_query ixn_123 from a disallowed user or chat.
2. StackOS verifies the profile secret and resolves the interaction.
3. Interaction access policy blocks the click.
4. StackOS stores the event as callback_blocked when policy permits storage.
5. StackOS creates no agent_request.
```

Multiple bots in one project:

```text
1. Project A has profiles support and ops with distinct auth_profile_key values.
2. Each webhook URL includes its own profile_key path segment.
3. Each incoming update verifies against that profile's own webhook secret.
4. Provider ids, interactions, and requests are scoped by profile_key.
5. A support update cannot use ops credentials or wake the ops profile.
```

Local Bot API webhook:

```text
1. Operator runs the official telegram-bot-api server with --local.
2. Credential safe config points api_base_url to the local Bot API server.
3. Operator calls webhook.set with the loopback StackOS ingress URL.
4. Local Bot API posts updates to /api/v1/ingress/telegram/{project_id}/{profile_key}.
5. StackOS verifies the secret token and processes the update through normal webhook policy.
```

### SMTP Outbound Notification

```text
Agent completes a run plan
-> agent chooses a named email target and calls communication.send
-> StackOS resolves the SMTP provider action and credential
-> connector sends message
-> StackOS records accepted/rejected status in action_calls
```

No delivery/read claim should be made from SMTP acceptance alone.

### HubSpot One-to-One Transactional Email

```text
Agent chooses one named HubSpot contact target
-> communication.send validates target allowlists and transactional/legal policy
-> StackOS verifies the connection's add-on confirmation and exact OAuth scopes
-> connector resolves the safe contact and transactional-template refs internally
-> connector sends one template with a shared StackOS/HubSpot idempotency key
-> StackOS stores provider-safe async status, message/event refs, and action audit
```

The call uses `content.template_ref` and optional scalar
`content.template_data`. It does not accept raw recipient email, raw HubSpot
IDs, arbitrary text/HTML overrides, contact-property updates, or a recipient
list. `pending`/`processing` means provider acceptance only; it is not a
delivery/read claim. The provider-specific action remains an explicit
lower-level escape hatch, and bulk marketing send remains unavailable.

### IMAP Inbox Sweep

```text
Agent starts inbox-review run plan
-> action.run or action.execute calls imap.messages.search with bounded mailbox/query
-> agent fetches selected messages by UID
-> StackOS stores selected mailbox/message/cursor resources from connector output
-> agent creates or prepares generic agent_requests for messages needing action
-> agent may mark selected messages seen after approval/grant
```

### Agent Request To Run Plan Handoff

```text
Trusted ingress or granted workflow creates agent_request
-> agent reads sanitized request/context
-> agent calls agentRequest.prepareRunPlan with explicit run_plan_json or template_key
-> StackOS atomically claims the request, creates the run plan, and links both
-> agent starts/claims run-plan steps through runPlan.* and uses granted actions
-> agent completes the original request with claim_token after work is done
```

`agentRequest.prepareRunPlan` is a mechanical queue-to-plan handoff. It does not
classify intent, choose a template, start a model, start the run plan, execute
provider actions, or send a response. The caller supplies the plan/template
choice and action refs.

## UI Surface

Keep UI generic and object-driven:

- Plugin catalog shows `communications` with provider setup status.
- Connections page renders typed Telegram, Slack, SMTP, IMAP, and HubSpot auth
  methods through provider manifests.
- Connections page renders generic communication profiles, surfaces, named
  targets, and ingress readiness so operators can inspect whether agents have
  the right identity, intent, audience, and destination setup.
- Resources browser renders communication channels, threads, messages, events,
  interactions, and cursors by schema.
- A generic Agent Requests view can list claimable work across providers.
- Action Calls ledger shows Telegram/Slack/SMTP/IMAP calls through the existing
  audit path.

Do not build bespoke workflow screens such as "Telegram Command Runner" or
"Email Assistant" in the first pass. If a specialized operator screen is needed
later, it must still render the same resources and queue records.

## Refactor Watchlist

Communications moved quickly and several files are now intentionally flagged
for the next cleanup slice before adding more providers:

- `stackos/operations/communication_platform.py`: split operation groups into
  profile, surface, contact, membership, target, route, context, and ingress
  modules once the shared communication processor lands.
- `ui/src/views/ConnectionsView.vue`: extract reusable panels for connected
  services, communication setup, Telegram communication profiles, and add-connection
  forms. The page should keep composing generic setup state, not accumulate
  provider-specific flows.
- `docs/integration-contracts/communications.md`: keep as the canonical
  contract, but move provider-specific method checklists into smaller linked
  contract files if Slack, Telegram, email, or another channel grows further.
- `stackos/api/slack_ingress.py` and `stackos/api/telegram_ingress.py`: move
  duplicated policy/storage/request creation into the shared one-brain
  processor; provider adapters should only verify and normalize.
- `stackos/actions/slack_bot.py` and `stackos/actions/telegram_bot.py`: keep
  provider calls in provider files, but return normalized outbound effects for
  shared communication recording instead of each connector growing its own
  resource lifecycle.

## Security And Privacy

- Agents never receive secrets.
- Telegram bot tokens must never appear in URLs returned to agents or stored in
  audit metadata.
- Telegram callback data is untrusted input and must not contain secrets.
- Slack bearer tokens, signing secrets, `response_url`, and `trigger_id` must
  never be returned to agents or persisted in resources/audit metadata.
- Slack HTTP ingress must verify raw-body HMAC signatures and reject stale
  timestamps before storing payload-derived records.
- Public webhook exposure is opt-in only. The default StackOS daemon remains
  loopback-only; production ingress needs secret-token verification and host
  allowlisting or a relay.
- SMTP and IMAP passwords stay in encrypted credential payloads.
- OAuth/XOAUTH2 stays deferred until mail-provider contracts, scope mappings,
  auth methods, and safe diagnostics are real.
- Allowlist Telegram numeric user/chat ids through safe refs; do not trust
  mutable usernames as the only authorization boundary.
- Message bodies can include PII, customer data, confidential plans, or access
  instructions. Store raw/long bodies as artifacts with retention metadata.
- Provider raw events should be redacted before persistence.
- Outbound actions should be approval-gated when they can send external
  messages on behalf of a business.
- Inbound triggers should not bypass run-plan grants.

## Test And Verification Requirements

Before a communications action is marked executable:

- Manifest validation covers providers, auth methods, resources, and actions.
- Auth split tests prove safe fields and secret fields are stored separately.
- Auth status/test responses expose no secret payloads.
- Action validation rejects malformed inputs and provider-invalid mode
  combinations.
- Connector tests use mocked providers for success, validation failures, auth
  failures, rate/temporary failures, and provider error bodies.
- Redaction tests prove Telegram token-bearing URLs and Slack token/transient
  callback fields are never persisted or returned.
- Run-plan grant tests prove `action.execute` is required for workflow provider calls.
- REST/CLI/MCP parity tests cover generic `agentRequest.*` operations.
- Queue-to-plan tests cover `agentRequest.prepareRunPlan` idempotent replay,
  rollback on invalid plans, and run-plan metadata linkage.
- Resource tests cover idempotent upsert by `external_id`/provider ref.
- Cursor tests cover Telegram update offsets and IMAP UID/UIDVALIDITY behavior.
- Interaction tests cover inline keyboard payload validation, Slack Block Kit
  button validation, callback/action normalization, idempotency, and optional
  static ACK audit.
- UI smoke tests show provider connections, plugin catalog, resources, agent
  requests, and action calls render with generic components.
- Docs update this file, [README](README.md), [Connector Quality Gate](connector-quality.md),
  [Action Executor](../action-executor.md) connector list when executable, and
  provider setup docs if auth UX changes.

## Current Limitations And Non-Goals

- Running an LLM/model from inside the StackOS daemon.
- Provider-specific MCP tools.
- Provider-specific decisions about whether a bot should answer.
- SMTP delivery/read/open tracking.
- Telegram read receipts.
- OAuth/XOAUTH2 for custom SMTP/IMAP.
- Telegram video/audio/document/media-group support beyond the explicitly
  modeled first `photo.send` action.
- Public webhook deployment automation.
- Specialized workflow UI for each communication use case.
- Slack Socket Mode, broad history sync, files, and admin actions.

## Signoff Criteria

The communications surface is release-ready when:

- Normal delivery guidance points agents to `communication.send` and
  `communication.reply`; `action.run` and `action.execute` remain explicit
  provider escape hatches or workflow-granted actions.
- Ingress stores provider-normalized state and agent requests without invoking
  a model.
- Provider credentials and webhook secrets remain daemon-side.
- Mock/local tests cover actions, ingress, resource writes, idempotency, and
  redaction before real provider credentials are required.
- Provider limitations are documented clearly enough that agents do not infer
  fake read, delivery, or approval semantics.
