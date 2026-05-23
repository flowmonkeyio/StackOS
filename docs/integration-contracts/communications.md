# Communications Integration Design And Delivery Plan

Status: implementation in progress. Generic agent request operations, the
first Telegram Bot API connector slice, and Telegram secret-token ingress are
executable; SMTP, IMAP, Telegram webhook set/delete/info actions, and scheduled
Telegram sync runners remain tracked follow-up work. This document owns the
contract for the first StackOS communications layer and the generic agent
request inbox that lets external agents treat messages as triggers.

Plan review status: signed off with minor implementation notes by sub-agent
review on 2026-05-23.

## Source Documents

Official provider and protocol references:

- Telegram Bot API: https://core.telegram.org/bots/api
- Telegram bot features: https://core.telegram.org/bots/features
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
The plan must model the active ingestion mode per credential/profile.

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

Inbound callback handling has two modes:

- Polling mode: `updates.poll` requests `callback_query` in `allowed_updates`
  and returns normalized callback events for an agent-run sync plan.
- Webhook mode: an explicit webhook ingress endpoint verifies Telegram's secret
  token header, stores the update idempotently, and creates resources/requests
  from static allowlist rules. It still does not invoke a model.

Telegram clients show a loading state after a callback button is pressed until
`answerCallbackQuery` is called. StackOS may perform a static configured ACK in
an ingestion runner or webhook handler, but it must be recorded through the
action/audit path and must not decide business outcome. Rich follow-up replies
remain agent-authored actions.

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
- `allowed_user_refs`
- `allowed_chat_refs`
- `send_enabled`
- `ingest_enabled`
- `metadata`

Provider object ids may be stored in provenance or safe refs after redaction,
but reusable templates should refer to `channel_ref`, not raw Telegram chat ids
or mailbox internals.

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
- `interaction_type`: `inline-button`, `reply-keyboard`, `force-reply`
- `button_key`
- `callback_data`
- `state_ref`
- `status`: `sent`, `clicked`, `acknowledged`, `expired`, `ignored`
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

- `credential_ref`
- `ingestion_mode`
- `last_update_id`
- `allowed_updates`
- `pending_update_count`
- `last_polled_at`

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

Safe config fields:

- `bot_username`
- `ingestion_mode`: `polling` or `webhook`
- `allowed_updates`
- `allowed_chat_refs`
- `allowed_user_refs`
- `privacy_mode_expected`
- `default_parse_mode`

Secret fields:

- `bot_token`
- `webhook_secret_token`

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

Deferred until separate tests/contracts:

- webhook set/delete/info if local deployment cannot expose HTTPS safely.
- media downloads, video/audio/document sends, and media groups.
- edit/delete message.
- channel administration.
- database artifact-id resolution for `photo.artifact_ref`; generated asset
  URIs are supported now.

Validation rules:

- `message.send` requires explicit `chat_ref` or provider-safe `chat_id`
  resolved from resources, plus explicit text payload.
- `message.send` and `photo.send` may include `reply_markup`. Inline keyboard
  callback buttons must keep `callback_data` within Telegram's 1-64 byte limit
  and must not contain secrets.
- `photo.send` requires exactly one of `photo.file_id`, `photo.url`, or
  `photo.artifact_ref`. URL sends require a public HTTPS URL. Local/generated
  assets require daemon multipart upload from a `/generated-assets/...` URI.
- `callback.answer` requires `callback_query_id`. It may include notification
  text, alert mode, URL, and cache time, but it must not claim work was
  completed unless the agent actually completed it.
- `updates.poll` requires bounded `limit`, `timeout_s`, and `allowed_updates`.
  `callback_query` must be included when the agent expects button clicks.
- If credential profile says `ingestion_mode=webhook`, `updates.poll` should
  fail with a clear validation error unless explicitly overridden for migration.
- If webhook is set, polling is invalid per Telegram contract.
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

### V1: Agent-Pulled Inbox

This is the first implementation target.

Flow:

1. Agent or script periodically calls `agentRequest.list`.
2. If no requests exist, the agent may run a granted sync run plan that calls
   `communications.telegram-bot.updates.poll` or IMAP search/fetch actions.
3. Agent stores relevant messages, callback events, and interaction state as
   communication resources.
4. Agent creates `agentRequest.create` records for items that should become
   work.
5. Agent claims one request, creates a run plan, executes actions, replies, and
   completes the request.

Why first:

- Uses current action/run-plan/grant model.
- No daemon model runner.
- Easy to verify through mocked provider tests.
- Keeps intent decisions with the agent.
- Resource writes and `agentRequest.create` happen inside a granted run-plan
  context, matching the existing resource/artifact write boundary.

### V2: Static Daemon Ingestion Runner

Allowed only after V1 is green.

Flow:

1. Operator creates a project schedule for communication sync.
2. Scheduler starts a native StackOS run plan from a communications sync
   template.
3. The ingestion runner executes only static sync steps:
   provider poll/search, cursor update, resource upsert, and optional
   `agentRequest.create` based on explicit allowlists.
4. If a Telegram callback needs immediate acknowledgement, the runner may call
   `telegram-bot.callback.answer` with static configured ACK text.
5. The runner does not invoke a model.
6. Agents still claim requests and decide what to do.

Rules:

- Must produce action-call audit rows for provider calls.
- Must use run-plan grants or equivalent system-run snapshots.
- Must not run business workflows directly.
- Must not infer intent beyond explicit allowlist/filter config.

### V3: Webhook Ingestion

Current local relay endpoint:

```text
POST /api/v1/ingress/telegram/{project_id}/{profile_key}
Header: X-Telegram-Bot-Api-Secret-Token: <configured webhook_secret_token>
```

This endpoint is bearer-token whitelisted because Telegram cannot send the
daemon bearer token. It verifies the Telegram secret-token header against the
encrypted `telegram-bot` credential for the project/profile before writing
anything. The default daemon is still loopback-only, so direct public Telegram
webhooks require an explicit public relay or hardened deployment boundary.

Flow:

1. Operator configures webhook mode for a Telegram bot profile.
2. StackOS exposes an explicitly enabled Telegram ingress endpoint or receives
   events from a small public relay. The default loopback daemon remains
   loopback-only unless the operator opts into a hardened ingress deployment.
3. Webhook handler verifies Telegram secret token when configured.
4. Handler rejects updates for the wrong project/profile with the same invalid
   secret response used for wrong secrets.
5. Handler enforces static profile allowlists for `allowed_updates`,
   `allowed_chat_refs`, and `allowed_user_refs` before writing resources.
6. Handler upserts `communication-event`, `communication-message`, and
   `communication-interaction` records by provider id and creates or replays one
   idempotent generic `agent_requests` row keyed by `update_id`.
7. Handler does not call a model and does not infer business intent. Future
   static provider calls needed for transport hygiene, such as `callback.answer`,
   must still preserve the action-call audit path.

Rules:

- Webhook endpoints must be explicitly authenticated/verified.
- Token-bearing provider URLs must not be logged.
- Webhooks must be idempotent by provider update id/event id.
- Webhooks must preserve the action-call audit path for outbound ACKs.
- Webhooks do not invoke a model directly.

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
-> Telegram update is polled or received by webhook
-> StackOS stores communication-message
-> allowlist creates agent_request
-> agentRequest.list shows unread request
-> agent claims request
-> agent creates run plan from a chosen template
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
-> updates.poll or webhook receives callback_query
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
| C06 | Delivered: implement Telegram connector file for `identity.get`, `message.send`, `photo.send`, `callback.answer`, and `updates.poll`. | C01. | Mocked Telegram tests; validation tests; inline keyboard and photo payload tests; no-token redaction tests. | Connector has official docs links near provider calls. |
| C07 | Add Telegram credential test wrapper and auth diagnostics. | C01, C06. | Auth test success/failure mocks; no token in diagnostics. | `auth.test` returns safe bot identity/status only. |
| C08 | Add Telegram normalization, cursor, message, interaction, and callback resource flow for manual sync runs. | C03, C04, C06. | Run-plan test polling messages/callbacks, storing resources, creating requests. | Manual agent-pulled flow works end to end. |
| C09 | Implement SMTP send connector and credential test. | C01. | Mock SMTP server tests for accepted/rejected/auth/TLS paths. | SMTP output never claims delivery/read state. |
| C10 | Implement IMAP list/search/fetch/mark connector and credential test. | C01. | Mock IMAP tests for UID search/fetch, `\\Seen`, UIDVALIDITY, size caps. | No sequence-number-only operations. |
| C11 | Add communications workflow templates for inbox review, rich Telegram reply, callback follow-up, and outbound notification. | C01, C03, C04, C06, C08, C09, C10 as relevant. | Template validation; run-plan grant tests. | Templates describe setup/context, not business decisions. |
| C12 | Add static scheduled ingestion runner. | C03, C04, C06, C08, C10. | Scheduler tests; system run-plan audit; idempotent cursor and optional callback ACK tests. | Runner stores events/requests only; no model invocation. |
| C13 | Partially delivered: add Telegram secret-token ingress for message/callback storage and generic agent-request creation. Webhook set/delete/info and outbound callback ACK automation remain deferred. | C06. | Route tests for missing/wrong secret, callback/message resource writes, idempotent request creation, and no secret leakage. | Ingress does not invoke a model and does not bypass queue state or daemon security posture. |
| C14 | Update connector quality matrix and release signoff docs for executable communications actions. | First executable connector tasks. | `make signoff` or targeted signoff set. | Docs and tests agree on executable/deferred state. |

## Dependency Graph

```text
C00
-> C01
   -> C02
   -> C06 -> C07 -> C08
   -> C09
   -> C10
-> C03 -> C04 -> C05
   -> C08
   -> C11
   -> C12 -> C13
C06/C09/C10 -> C14
```

Recommended delivery order:

1. C00.
2. C01 and C02.
3. C03 and C04.
4. C06 and C07.
5. C08 manual Telegram trigger vertical slice.
6. C09 SMTP send.
7. C10 IMAP lifecycle.
8. C11 templates.
9. C12 scheduled ingestion.
10. C13 webhooks.
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
