# Communications Integration Design And Delivery Plan

Status: implementation in progress. Generic agent request operations, Telegram
Bot API messaging, Telegram webhook set/delete/info, project-scoped Telegram bot
profiles, and Telegram secret-token ingress are executable. SMTP and IMAP
connectors remain tracked follow-up work. This document owns the contract for
the first StackOS communications layer and the generic agent request inbox that
lets external agents treat messages as triggers.

Plan review status: signed off with minor implementation notes by sub-agent
review on 2026-05-23.

## Source Documents

Official provider and protocol references:

- Telegram Bot API: https://core.telegram.org/bots/api
- Telegram bot features: https://core.telegram.org/bots/features
- Official local Telegram Bot API server: https://github.com/tdlib/telegram-bot-api
- SMTP: https://www.rfc-editor.org/rfc/rfc5321.html
- SMTP AUTH: https://www.rfc-editor.org/rfc/rfc4954
- IMAP4rev2: https://www.rfc-editor.org/rfc/rfc9051.html

StackOS references this plan must stay aligned with:

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
-> action.execute
-> daemon-side credential resolution
-> one provider connector call
-> normalized safe output and action-call audit
-> communication resources and optional agent_request records
-> agent claims request
-> agent creates a run plan
-> agent executes granted actions
-> StackOS records audit, resources, learnings, and decisions
```

StackOS may store communication records, cursors, claim state, safe provider
metadata, and static trigger configuration. StackOS must not interpret intent,
choose business actions, decide whether SEO/media/GTM work is needed, or run a
model invisibly inside the daemon.

## Product Boundary

StackOS owns:

- Provider catalog entries for Telegram Bot API, SMTP, and IMAP.
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
StackOS treats a bot profile with `ingress_mode: local-webhook` as the normal
local listener path. It uses webhook ingress through the official local
`telegram-bot-api --local` server so the loopback daemon can receive updates
without public tunnels during development. `updates.poll` remains a bounded
diagnostic/bootstrap action only, for example discovering chat/user ids before a
bot profile is locked down.

The local Bot API server is official Telegram infrastructure backed by TDLib. In
`--local` mode it can accept HTTP webhook URLs on local IPs and arbitrary local
ports, so StackOS can listen at `127.0.0.1:5180` without public tunnels during
development. If a bot moves from Telegram's cloud Bot API endpoint to the local
server, Telegram requires `logOut` before the local server can own that bot.

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
- `telegram-bot.callback.answer`: acknowledge an inline button callback through
  Telegram `answerCallbackQuery`.
- Future actions may add edit/delete, document/video/audio sends, and media
  group sends, but only after each provider method has its own schema and tests.

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

Inbound callback handling uses the local-webhook listener path by default. The
explicit webhook endpoint verifies Telegram's secret-token header, resolves the
project-scoped bot profile, stores the update idempotently, and creates
resources/requests from static policy. It still does not invoke a model.
`updates.poll` can inspect callback updates only as a bounded diagnostic action
and must not become the normal listener loop.

### Telegram Bot Profiles

Telegram is not globally connected. A project owns one or more
`communication-bot-profile` records:

```text
project
-> communication-bot-profile
-> telegram credential profile
-> identity / agent guidance / access / trigger / context / response policies
```

Each profile binds to exactly one project-scoped Telegram credential profile by
`auth_profile_key`. There is no global Telegram credential, no cross-project
fallback credential, and no agent-visible token handoff. The credential stores
only bot token material, webhook secret, and safe transport endpoint
configuration such as `api_base_url`. The bot profile owns behavior and agent
setup:

- `identity`: display name, purpose, and voice. This is the bot's project-level
  identity, not the credential identity returned by Telegram `getMe`.
- `agent_guidance`: default instructions, boundaries, and escalation guidance
  attached to every agent request created by this bot.
- `access_policy`: numeric Telegram user/chat ids first; usernames are metadata
  and setup convenience because they can change.
- `trigger_policy`: DM, mention, structured slash-command intents,
  reply-to-bot, callback button, or configured wake patterns. Command intents
  may carry their own description, guidance, expected inputs, and output
  expectations for the operating agent.
- `visibility_policy`: whether allowed chats may be observed without triggering
  a request.
- `context_policy`: bounded history selection from messages StackOS already
  stored, filtered by project/profile/chat/thread/lookback/fields.
- `response_policy`: same chat/thread defaults, invoker-only behavior,
  broadcast/DM constraints, and reply requirements.

Setup is exposed through shared StackOS operations, not provider-specific MCP
tools:

- `communicationBotProfile.upsert`: creates or updates the safe bot-profile
  identity, agent guidance, trigger policy, and delivery policy after a
  project-scoped `telegram-bot` credential profile exists.
- `communicationBotProfile.get`: returns one safe profile, including response
  reference maps such as `reply_to_message_refs`, `thread_refs`, and
  `direct_messages_topic_refs`.
- `communicationBotProfile.list`: lists safe profiles for a project.

These operations are available through REST, CLI `ops call`, and MCP. The
browser UI token may call only this narrow setup mutation because it never
includes token material; provider secrets still go through the typed auth
credential setup route and remain daemon-side.

Visibility is not activation. A bot profile may observe and store allowed group
messages for future context, but StackOS creates an `agent_request` only when a
configured trigger is matched by an allowed invoker. If a disallowed user tags
the bot in an observed chat, StackOS may keep the message as context but must
not create a request.

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

### SMTP

SMTP sends mail. It can report that the SMTP server accepted or rejected a
message for relay. It does not prove delivery, inbox placement, open, read,
click, reply, or bounce unless additional systems provide those events.

SMTP AUTH can use username/password or app-password style credentials. OAuth or
XOAUTH2 must remain deferred until StackOS implements provider-specific token
refresh and auth test behavior.

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
- `smtp`
- `imap`

`local-agent-chat` is the provider-neutral local conversation surface for a
user who wants to talk directly to an agent through StackOS. Telegram is a
remote transport adapter, not the only agent conversation channel.

The plugin may later add Slack, Discord, WhatsApp Business, Twilio, Gmail API,
Microsoft Graph mail, or project-local communication connectors, but those
providers need their own contract review before execution.

## Resource Model

Communication records should be plugin resources first. Avoid bespoke
provider-specific core tables unless a generic queue or lock invariant requires
one.

### `communication-channel`

Represents a durable inbound/outbound communication surface.

Example fields:

- `channel_ref`
- `provider_key`
- `credential_ref`
- `kind`: `telegram-private`, `telegram-group`, `telegram-supergroup`,
  `telegram-channel`, `smtp-identity`, `imap-mailbox`
- `display_name`
- `safe_external_ref`
- `send_enabled`
- `ingest_enabled`
- `metadata`

Provider object ids may be stored in provenance or safe refs after redaction,
but reusable templates should refer to `channel_ref`, not raw Telegram chat ids
or mailbox internals.

### `communication-bot-profile`

Represents one project-scoped bot identity and policy bundle.

Example fields:

- `key`
- `provider_key`
- `auth_profile_key`
- `bot_username`
- `ingress_mode`: `local-webhook` for the normal local listener path, or
  `webhook` for an explicitly hardened public/relay boundary
- `allowed_updates`
- `identity`
- `agent_guidance`
- `access_policy`
- `trigger_policy`
- `visibility_policy`
- `context_policy`
- `response_policy`

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

- `bot_profile_key`
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
`communication-bot-profile.auth_profile_key`. Agents and action payloads name
the bot profile, not a raw credential. The daemon resolves the credential
server-side and rejects profile/credential mismatches.

Safe config fields:

- `api_base_url`: optional Bot API base URL, commonly
  `http://127.0.0.1:8081` when using the official local Telegram Bot API
  server.

Secret fields:

- `bot_token`
- `webhook_secret_token`

Bot behavior fields such as identity, agent guidance, command intent guidance,
allowed users, allowed chats, trigger patterns, context windows, and response
constraints belong to
`communication-bot-profile` resource records, not credentials.

Credential tests:

- `getMe` should verify token validity and return safe bot identity.
- Do not include the token-bearing request URL in diagnostics.

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

- OAuth/XOAUTH2 until token refresh, scope diagnostics, and safe auth tests are
  implemented.

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

- OAuth/XOAUTH2 until provider-specific refresh and scope handling exists.

## Action Contracts

Provider operations must be plugin actions executed through `action.execute`.
Do not add provider-specific MCP tools such as `telegram.sendMessage` or
`smtp.sendEmail`.

### Telegram Actions

Connector file: `content_stack/actions/telegram_bot.py`

Action refs:

- `communications.telegram-bot.identity.get`
- `communications.telegram-bot.message.send`
- `communications.telegram-bot.photo.send`
- `communications.telegram-bot.callback.answer`
- `communications.telegram-bot.updates.poll`
- `communications.telegram-bot.webhook.set`
- `communications.telegram-bot.webhook.delete`
- `communications.telegram-bot.webhook.info`

Executable in the current Telegram connector:

- `identity.get`
- `message.send`
- `photo.send`
- `callback.answer`
- `updates.poll`
- `webhook.set`
- `webhook.delete`
- `webhook.info`

Deferred until separate tests/contracts:

- media downloads, video/audio/document sends, and media groups.
- edit/delete message.
- channel administration.
- database artifact-id resolution for `photo.artifact_ref`; generated asset
  URIs are supported now.

Validation rules:

- `message.send` requires explicit `bot_profile_key`, `chat_ref` or
  provider-safe `chat_id` resolved from resources, plus explicit text payload.
- `message.send`, `photo.send`, `callback.answer`, and webhook actions must
  resolve the bot profile server-side and verify that the profile's
  `auth_profile_key` matches the daemon-resolved credential.
- If the bot profile's response policy requires origin binding, outbound
  `message.send` and `photo.send` must include `source_agent_request_id`; the
  connector verifies the request's bot profile, chat, thread, and source message
  before sending.
- `message.send` and `photo.send` may include `reply_markup`. Inline keyboard
  callback buttons must keep `callback_data` within Telegram's 1-64 byte limit
  and must not contain secrets.
- When `reply_markup` includes callback buttons, StackOS stores outbound
  `communication-interaction` records keyed by bot profile, provider message
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
- `updates.poll` requires explicit `bot_profile_key`, bounded `limit`,
  `timeout_s`, and `allowed_updates`. It is profile-bound diagnostic/bootstrap
  access, not a background listener.
  `callback_query` may be included only to inspect callback update delivery
  during setup/debugging.
- If webhook is set at Telegram, polling is invalid per Telegram contract.
  StackOS does not run polling as the normal listener path.
- `webhook.set` sends Telegram `setWebhook` with the profile-bound
  `webhook_url`, optional `allowed_updates`, optional `drop_pending_updates`,
  and the daemon-side `webhook_secret_token` as Telegram `secret_token`.
  Public webhook hosts must be explicitly allowlisted on the bot profile;
  loopback hosts are only for the official local Bot API server flow.
- `webhook.delete` and `webhook.info` call Telegram `deleteWebhook` and
  `getWebhookInfo` through the same profile-bound credential.
- Returned provider error metadata must redact token-bearing URLs.

### SMTP Actions

Connector file: `content_stack/actions/smtp.py`

Action refs:

- `communications.smtp.email.send`

Validation rules:

- Require explicit recipients.
- Require subject and body or artifact/body refs.
- Require from identity from safe credential config or explicit allowed
  `from_ref`.
- Enforce max recipient count in schema.
- Return accepted/rejected recipient counts and safe SMTP status metadata.
- Do not claim delivery/read/open state.

### IMAP Actions

Connector file: `content_stack/actions/imap.py`

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

## Trigger And Ingestion Modes

### Normal Telegram Listener: Local Webhook

Current local webhook endpoint:

```text
POST /api/v1/ingress/telegram/{project_id}/{bot_profile_key}
Header: X-Telegram-Bot-Api-Secret-Token: <configured webhook_secret_token>
```

This endpoint is bearer-token whitelisted because Telegram cannot send the
daemon bearer token. It resolves the `communication-bot-profile`, verifies the
Telegram secret-token header against the encrypted `telegram-bot` credential
bound by that profile's `auth_profile_key`, and then applies bot-profile
policy. For local development, run the official local Telegram Bot API server in
`--local` mode and set the webhook URL directly to the loopback StackOS daemon.
Direct public Telegram webhooks still require an explicit relay or hardened
deployment boundary.

Flow:

1. Operator creates a Telegram credential profile with server-side bot token and
   webhook secret fields.
2. Operator or setup agent calls `communicationBotProfile.upsert` to create a
   project-scoped `communication-bot-profile` whose `auth_profile_key` points at
   that credential profile.
3. Operator defines bot identity, default agent guidance, access policy, and
   optional structured command intents on the bot profile.
4. Operator sets `ingress_mode: local-webhook` and an explicit
   `allowed_updates` list on the bot profile.
5. Operator runs the official local Telegram Bot API server or a hardened public
   relay and calls `communications.telegram-bot.webhook.set`.
6. The listener verifies Telegram secret token against the daemon-held
   credential bound by `auth_profile_key`.
7. The listener rejects the wrong project, bot profile, or secret with the same
   invalid-secret response.
8. The listener applies bot-profile update/chat visibility policy. Blocked
   chats, disabled profiles, and no-store non-triggers write nothing.
9. The listener upserts `communication-event`, `communication-message`, and
   `communication-interaction` records by bot-profile-scoped provider ids.
10. The listener creates or replays one idempotent generic `agent_requests` row
   only when trigger policy matches and invoker access policy allows it.
11. The listener copies safe identity, agent guidance, context policy, response
   policy, and matched command guidance into request metadata for the operating
   agent.
12. The listener does not call a model and does not infer business intent.

Rules:

- Webhook endpoints must be explicitly authenticated/verified.
- Token-bearing provider URLs must not be logged.
- Webhooks must be idempotent by bot-profile-scoped provider update id/event id.
- Visibility is not activation: observed messages may become context without
  creating agent requests.
- Webhooks must preserve the action-call audit path for outbound ACKs.
- Webhooks do not invoke a model directly.
- Webhook management is executable through
  `communications.telegram-bot.webhook.set`,
  `communications.telegram-bot.webhook.delete`, and
  `communications.telegram-bot.webhook.info`.

### Diagnostic Telegram Poll

`communications.telegram-bot.updates.poll` is executable, but only as bounded
diagnostic/bootstrap access. It must require `bot_profile_key`, `limit`,
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
intent beyond bot-profile policy, and agents still claim requests and decide
what to do.

## Agent Flow Examples

### Direct Local Agent Chat

```text
User opens local StackOS agent chat
-> StackOS creates or reuses communication-thread
-> user message is stored as communication-message
-> StackOS creates generic agent_request for the selected agent/runner
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

### Telegram DM Trigger

```text
User sends DM to bot
-> local-webhook ingress receives Telegram message update
-> StackOS stores communication-message
-> allowlist creates agent_request
-> agentRequest.list shows unread request
-> agent claims request
-> agent creates run plan from a chosen template
-> agent links the run plan to the request
-> agent executes needed actions
-> agent sends reply with communications.telegram-bot.message.send
-> agent completes request
```

### Telegram Group Mention

```text
Message appears in allowed group
-> update type and chat_ref pass static allowlist
-> StackOS stores message and source chat metadata
-> agent_request includes group/thread/message refs
-> agent claims request and decides if action is needed
```

The connector must not decide that a group message is actionable unless the
configured allowlist says it should become a request. Even then, the agent
decides the workflow.

### Telegram Inline Button Flow

```text
Agent sends message with inline keyboard
-> action.execute calls communications.telegram-bot.message.send
-> StackOS stores outbound communication-message and interaction refs
-> user presses button
-> local-webhook ingress receives callback_query
-> StackOS stores communication-event and marks interaction clicked
-> optional static callback.answer clears Telegram client loading state
-> allowlist creates agent_request with event/interaction refs
-> agent claims request and decides follow-up
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
1. local-webhook receives a private message for project A / bot profile support.
2. profile support resolves auth_profile_key support-telegram server-side.
3. access_policy allows the chat and user; trigger_policy allows DM.
4. StackOS stores communication-event and communication-message.
5. StackOS creates one agent_request with bot_profile_key, chat_ref, and source_message_ref.
```

Allowed group mention with history context:

```text
1. local-webhook receives a group message that mentions @support_bot.
2. profile support allows the group, user, and mention trigger.
3. StackOS stores the new message and selects bounded stored history by context_policy.
4. StackOS creates one agent_request with group/thread refs and context hints.
5. The agent claims the request and decides whether the history changes the response.
```

Observed non-trigger:

```text
1. local-webhook receives an allowed group message without mention, command, or reply-to-bot.
2. visibility_policy permits storing non-trigger messages.
3. StackOS stores the message with observed policy status.
4. StackOS creates no agent_request.
```

No-store non-trigger:

```text
1. local-webhook receives an allowed group message without a configured trigger.
2. visibility_policy.store_non_trigger_messages is false.
3. StackOS writes no communication records for the update.
4. StackOS creates no agent_request.
```

Unauthorized user:

```text
1. local-webhook receives a trigger from an allowed chat but a disallowed user.
2. StackOS verifies the bot profile and secret before applying user policy.
3. StackOS may store the event/message as invoker_blocked context.
4. StackOS creates no agent_request.
```

Outbound reply tied to `source_agent_request_id`:

```text
1. Agent claims agent_request 42 from bot profile support and chat telegram-chat:100.
2. Agent calls message.send with bot_profile_key support, chat_ref telegram-chat:100, and source_agent_request_id 42.
3. Connector resolves support.auth_profile_key and verifies request/chat/thread origin when response_policy requires it.
4. Telegram sendMessage executes through action.execute with daemon-held credentials.
5. StackOS records the outbound communication-message and action-call audit.
```

Authorized callback:

```text
1. Agent sends message telegram-message:100:501 with callback_data ixn_123 and
   allowed_user_refs.
2. StackOS stores a communication-interaction keyed by support /
   telegram-message:100:501 / ixn_123.
3. local-webhook receives callback_query ixn_123 from the allowed user/chat.
4. StackOS resolves the stored interaction by bot profile, provider message ref,
   and callback token, then marks it clicked.
5. StackOS creates one agent_request with event_ref and interaction_ref; the
   agent can read the interaction's source_agent_request_id.
```

Unauthorized callback:

```text
1. local-webhook receives callback_query ixn_123 from a disallowed user or chat.
2. StackOS verifies the profile secret and resolves the interaction.
3. Interaction access policy blocks the click.
4. StackOS stores the event as callback_blocked when policy permits storage.
5. StackOS creates no agent_request.
```

Multiple bots in one project:

```text
1. Project A has profiles support and ops with distinct auth_profile_key values.
2. Each webhook URL includes its own bot_profile_key path segment.
3. Each incoming update verifies against that profile's own webhook secret.
4. Provider ids, interactions, and requests are scoped by bot_profile_key.
5. A support update cannot use ops credentials or wake the ops profile.
```

Local Bot API webhook:

```text
1. Operator runs the official telegram-bot-api server with --local.
2. Credential safe config points api_base_url to the local Bot API server.
3. Operator calls webhook.set with the loopback StackOS ingress URL.
4. Local Bot API posts updates to /api/v1/ingress/telegram/{project_id}/{bot_profile_key}.
5. StackOS verifies the secret token and processes the update through normal local-webhook policy.
```

### SMTP Outbound Notification

```text
Agent completes a run plan
-> run plan has granted SMTP send action
-> agent composes explicit recipient/subject/body payload
-> action.execute resolves smtp credential
-> connector sends message
-> StackOS records accepted/rejected status in action_calls
```

No delivery/read claim should be made from SMTP acceptance alone.

### IMAP Inbox Sweep

```text
Agent starts inbox-review run plan
-> action.execute calls imap.messages.search with bounded mailbox/query
-> agent fetches selected messages by UID
-> agent stores communication-message resources
-> agent creates agent_requests for messages needing action
-> agent may mark selected messages seen after approval/grant
```

## UI Surface

Keep UI generic and object-driven:

- Plugin catalog shows `communications` with provider setup status.
- Connections page renders typed Telegram, SMTP, and IMAP auth methods.
- Resources browser renders communication channels, threads, messages, events,
  interactions, and cursors by schema.
- A generic Agent Requests view can list claimable work across providers.
- Action Calls ledger shows Telegram/SMTP/IMAP calls through the existing audit
  path.

Do not build bespoke workflow screens such as "Telegram Command Runner" or
"Email Assistant" in the first pass. If a specialized operator screen is needed
later, it must still render the same resources and queue records.

## Security And Privacy

- Agents never receive secrets.
- Telegram bot tokens must never appear in URLs returned to agents or stored in
  audit metadata.
- Telegram callback data is untrusted input and must not contain secrets.
- Public webhook exposure is opt-in only. The default StackOS daemon remains
  loopback-only; production ingress needs secret-token verification and host
  allowlisting or a relay.
- SMTP and IMAP passwords stay in encrypted credential payloads.
- OAuth/XOAUTH2 stays deferred until refresh and safe diagnostics are real.
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
- Redaction tests prove Telegram token-bearing URLs are never persisted or
  returned.
- Run-plan grant tests prove `action.execute` is required for provider calls.
- REST/CLI/MCP parity tests cover generic `agentRequest.*` operations.
- Resource tests cover idempotent upsert by `external_id`/provider ref.
- Cursor tests cover Telegram update offsets and IMAP UID/UIDVALIDITY behavior.
- Interaction tests cover inline keyboard payload validation, callback query
  normalization, idempotency, and optional static ACK audit.
- UI smoke tests show provider connections, plugin catalog, resources, agent
  requests, and action calls render with generic components.
- Docs update this file, [README](README.md), [Connector Quality Gate](connector-quality.md),
  [Action Executor](../action-executor.md) connector list when executable, and
  provider setup docs if auth UX changes.

## Delivery Tasks And Dependencies

Each task should be delivered with targeted verification, a sub-agent review
when the blast radius is meaningful, and a detailed commit message after signoff.

| Task | Scope | Dependencies | Verification | Commit gate |
| --- | --- | --- | --- | --- |
| C00 | Approve this design plan. | None. | Plan reviewer signoff; docs diff check. | Plan doc committed. |
| C01 | Add `plugins/communications/plugin.yaml` with capabilities, providers, auth methods, resources, and initial action metadata. | C00. | Manifest parser tests; plugin catalog sync tests. | Plugin appears in catalog with no executable false claims. |
| C02 | Add provider setup docs and local `plugins/communications/AGENTS.md`. | C01. | Docs stale-ref scan; auth field review. | Agents can find provider expectations without code archaeology. |
| C03 | Add `agent_requests` model, migration, repository, and invariants. | C00. | Repository tests for create/list/claim/release/complete/ignore and project isolation. | Queue state is generic and provider-agnostic. |
| C04 | Register `agentRequest.*` operation specs and REST/CLI/MCP adapters. | C03. | REST/CLI/MCP parity tests against the same operation registry. | No provider-specific MCP tools added. |
| C05 | Add generic Agent Requests UI page or resource view integration. | C03, C04. | UI unit/build smoke; manual browser pass when server is running. | UI stays generic and object-driven. |
| C06 | Delivered: implement Telegram connector file for `identity.get`, `message.send`, `photo.send`, `callback.answer`, `updates.poll`, and `webhook.set/delete/info`. | C01. | Mocked Telegram tests; validation tests; inline keyboard, photo, webhook, diagnostic poll, and no-token redaction tests. | Connector has official docs links near provider calls and no token-bearing URLs. |
| C07 | Add Telegram credential test wrapper and auth diagnostics. | C01, C06. | Auth test success/failure mocks; no token in diagnostics. | `auth.test` returns safe bot identity/status only. |
| C08 | Add Telegram normalization, cursor, message, interaction, and callback resource flow for local-webhook ingress plus bounded diagnostic poll output. | C03, C04, C06. | Route/connector tests for message/callback normalization, resource writes, idempotency, and diagnostic poll bounds. | Local-webhook is the normal listener path; `updates.poll` is not a daemon listener. |
| C09 | Implement SMTP send connector and credential test. | C01. | Mock SMTP server tests for accepted/rejected/auth/TLS paths. | SMTP output never claims delivery/read state. |
| C10 | Implement IMAP list/search/fetch/mark connector and credential test. | C01. | Mock IMAP tests for UID search/fetch, `\\Seen`, UIDVALIDITY, size caps. | No sequence-number-only operations. |
| C11 | Add communications workflow templates for inbox review, rich Telegram reply, callback follow-up, and outbound notification. | C01, C03, C04, C06, C08, C09, C10 as relevant. | Template validation; run-plan grant tests. | Templates describe setup/context, not business decisions. |
| C12 | Add static scheduled ingestion runner. | C03, C04, C06, C08, C10. | Scheduler tests; system run-plan audit; idempotent cursor and optional callback ACK tests. | Runner stores events/requests only; no model invocation. |
| C13 | Delivered: add Telegram secret-token local-webhook ingress for message/callback storage and generic agent-request creation through project-scoped bot profiles. | C03, C04, C06, C08. | Route tests for missing/wrong secret, auth_profile_key binding, callback/message resource writes, idempotent request creation, and no secret leakage. | Ingress does not invoke a model, does not bypass queue state, and uses server-side secrets only. |
| C14 | Update connector quality matrix and release signoff docs for executable communications actions. | First executable connector tasks. | `make signoff` or targeted signoff set. | Docs and tests agree on executable/deferred state. |

## Dependency Graph

```text
C00
-> C01
   -> C02
   -> C06 -> C07 -> C08 -> C13
   -> C09
   -> C10
-> C03 -> C04 -> C05
   -> C08
   -> C13
   -> C11
   -> C12
C06/C09/C10 -> C14
```

Recommended delivery order:

1. C00.
2. C01 and C02.
3. C03 and C04.
4. C06 and C07.
5. C08 Telegram resource normalization and callback state.
6. C09 SMTP send.
7. C10 IMAP lifecycle.
8. C11 templates.
9. C12 scheduled ingestion.
10. C13 local-webhook ingress.
11. C14 final connector-quality/release signoff.

## Explicit Non-Goals For First Pass

- Running an LLM/model from inside the StackOS daemon.
- Provider-specific MCP tools.
- Generic `message.send` abstraction across all providers.
- SMTP delivery/read/open tracking.
- Telegram read receipts.
- OAuth/XOAUTH2 for custom SMTP/IMAP.
- Telegram video/audio/document/media-group support beyond the explicitly
  modeled first `photo.send` action.
- Public webhook deployment automation.
- Specialized workflow UI for each communication use case.

## Signoff Criteria

The plan is accepted when:

- A reviewer confirms the design preserves StackOS as decision-free tool infra.
- The delivery tasks have clear dependencies and commit gates.
- The auth model keeps secrets daemon-side.
- The trigger model does not invoke models in the daemon.
- Provider limitations are documented clearly enough that agents do not infer
  fake read/delivery semantics.
- The first executable slice can be verified with mock/local tests before real
  provider credentials exist.
