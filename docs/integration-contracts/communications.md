# Communications Integration Contract

Status: generic agent request operations, native Telegram TDLib messaging for
bot and user Accounts, Slack Web API actions with signed HTTP ingress, SMTP
send, and IMAP mailbox/message lifecycle actions are executable. Telegram uses
one managed TDLib session per Account for outbound actions and native updates;
the retired HTTP connector, webhooks, and long polling are not supported. The IMAP
connector also provides bounded staged-evidence export and transfer-id-only
cleanup for the finance receipt workflow; it remains transport rather than a
finance store. Slack Socket Mode remains deferred until StackOS has a daemon
runner contract. This document owns the current contract and limitation record
for the StackOS communications layer and generic agent request inbox; it is not
a delivery task ledger.

Plan review status: signed off with minor implementation notes by sub-agent
review on 2026-05-23.

## Source Documents

Official provider and protocol references:

- Telegram TDLib: https://core.telegram.org/tdlib
- TDLib authorization: https://core.telegram.org/tdlib/docs/td__api_8h.html
- TDLib chat-list reads: https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1get_chats.html
- TDLib chat-list loading: https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1load_chats.html
- TDLib chat history: https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1get_chat_history.html
- Telegram dialog list (user-only): https://core.telegram.org/method/messages.getDialogs
- Telegram history (user-only): https://core.telegram.org/method/messages.getHistory
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
- Python IMAP client errors and read-only mailbox selection: https://docs.python.org/3/library/imaplib.html
- Python certificate verification errors: https://docs.python.org/3/library/ssl.html#ssl.SSLCertVerificationError
- Python DNS errors: https://docs.python.org/3/library/socket.html#socket.gaierror

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
local/web provider adapter instead of Telegram TDLib calls.

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
`telegram` facet inside generic `communication-profile` records; it is not a
separate communications model.

Canonical graph:

```text
communication-profile
-> provider facets / Account credential refs
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
`communications.telegram.message.send`, but the normal agent-facing send
path is `communication.send` or `communication.reply`. Provider actions remain
the lower-level escape hatch when an agent intentionally needs a
provider-specific payload.

Policies are split so one concept does not silently authorize another:

- `access_policy`: which users may invoke/respond. The normal bot stance is
  broad visibility with a narrow user allowlist.
- `visibility_policy`: which surfaces may be observed and what can be stored
  without creating work. Telegram also requires selected native update types;
  both its surface and update-type selectors default to no retention.
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
outside StackOS visibility. Telegram chat lists and history are separate live
TDLib actions against a connected Account. Telegram's own access and history
rules still determine which chats and messages that Account can read.

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
  "provider_key": "telegram",
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
one processing model after provider authentication/verification:

1. The provider adapter verifies the transport where applicable, or receives an
   update from its authenticated native session, and normalizes the payload into
   provider-neutral fields such as `profile_ref`,
   `surface_ref`, `user_ref`, `thread_ref`, `message_ref`, `text`,
   `interaction_ref`, and `event_type`.
2. The shared communication processor applies the profile's visibility,
   trigger, and user access policy.
3. The same processor stores communication resources and creates at most one
   `agent_request` when the normalized trigger is from an allowlisted user.
4. Provider-specific code may parse fields and map capabilities; it must not
   invent separate business rules for when an Account should answer.

The approval boundary is the invoker user, not the channel. A bot may observe any
reachable channel/DM/group when visibility allows it. If an allowlisted user tags
or DMs the bot, an agent request may be created; if any other user does the same,
the event is ignored for activation. Once an allowlisted user asks for an
outbound message, the target may be any reachable channel unless the project
adds an explicit send/handoff restriction.

### Current Implementation Boundaries

Slack HTTP adapters and the Telegram TDLib update adapter call
`stackos/communications/processor.py` for policy evaluation, resource writes,
stable request dedupe, click-state patches, and `agent_request` creation.
Provider adapters verify and normalize; they do not independently decide when
an agent should answer.

Not every recording path uses that processor: outbound connectors and IMAP
still record communication resources, and local chat creates requests through
`stackos/operations/communications.py`. Ingress route derivation remains
provider-aware in `stackos/operations/communication_platform/ingress.py`.
These are current ownership boundaries, not an additional prerequisite for
using the implemented actions. New providers must reuse shared policy and
resource contracts without duplicating business decisions.

## Product Boundary

StackOS owns:

- Provider catalog entries for Telegram TDLib, Slack Web API, SMTP, and IMAP.
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

### Telegram TDLib

Telegram is one provider (`telegram`) with two Account kinds: bot and user. A
project attaches an Account explicitly, and each `communication-profile` binds
to exactly one attached Account through its safe `credential_ref`. There is no
provider-wide credential lookup, token handoff, legacy HTTP request, webhook, or
long-polling fallback.

Each Account owns one daemon-managed TDLib session. The session authenticates,
reconnects, sends, and receives native updates for that Account. Its native
chat and message identifiers remain provider identifiers; the adapter normalizes
them to the generic communication graph and passes them to the shared processor.
The adapter does not decide whether a message should create work or receive a
reply. Shared profile policy owns visibility, trigger matching, allowed
invokers, storage, deduplication, and agent-request creation.

TDLib requires one daemon-held Telegram application `api_id` and `api_hash`
pair, configured once and reused by bot and user Accounts. It is not a
per-Account field or an agent-visible credential.
If the pair is wrong, the local operator detaches and revokes all Telegram
Accounts; final Account revocation clears the shared pair and a new setup can
store a corrected one. Revoking fewer than all Accounts does not alter it.
Bot Accounts also require a daemon-held bot token, which Account creation
authenticates once before saving the native authorization and closing TDLib.
User Accounts complete the native authorization challenge through Account setup;
both kinds remain disconnected until an agent explicitly connects them for
operations. No user phone, code,
password, session database, or other credential material is returned to an
agent. An optional Account-local proxy may be configured as SOCKS5, HTTP, or
MTProto with host and port, plus the authentication fields that its type
requires. Proxy fields remain encrypted/redacted and a proxy failure never
falls back to a direct connection.

The profile owns project behavior: identity and guidance, access, visibility,
trigger, context, response, send, handoff, and approval policies. A native
session routes an update only to enabled profiles attached to its Account's
project. A profile does not configure a public endpoint, an update allowlist,
or a webhook owner.

TDLib receives DMs, groups, supergroups, channels, message edits/deletes,
callbacks, membership/chat facts, and file facts as native updates. StackOS
stores only the profile-authorized, normalized facts. It does not promise
arbitrary historical access; `communicationContext.query` reads stored StackOS
records, while live peer/message lookup and paginated navigation remain explicit
Telegram actions.
Telegram `read` and `unread` remain StackOS-local attention states.

#### Live Telegram navigation and storage

An agent explicitly calls `account.session.connect` for an attached Account
before live work and `account.session.disconnect` when the operator wants to
close it. Live Telegram actions require that connected TDLib session and an
enabled project `communication-profile` bound to the same Account. Disconnected
or mismatched setup returns repair guidance; a read does not silently create a
new connection or change the Account's desired connection state.

`telegram.chat.list` lets the agent navigate a user Account's available chats in
bounded pages, including channels it can access. Select the `main` or `archive`
chat list, request at most 50 chats, and pass `next_cursor` back as `cursor` for
the next page. This cursor slices TDLib's current ordered list; chat reordering
between calls can shift page boundaries. The action scans at most 5,000 loaded
chats per list and reports `scan_limit_reached` separately from `end_reached`.
Use the returned `surface_ref` (or one returned by `telegram.chat.resolve`) for
later reads; do not guess a chat ID from its name or username. Keep the sign of
native IDs in `telegram-chat:<id>` refs: private chats are commonly positive,
while groups and channels are negative. A chat summary reports
`history_supported: false` for a secret chat or bot Account; history reads reject it.
`telegram.message.history` reads at most 50 messages from one selected user Account
`surface_ref`. It defaults to 160-character previews;
set `include_content: true` to return selected text up to 8,192 characters.
Pass `next_before_message_id` back as `before_message_id` for the next older page.
If an unusually short native response cannot establish whether older messages
exist after at most two bounded probes, `pagination_inconclusive` and
`next_action` explain the unresolved page. `next_before_message_id` is null in
that case. The agent stops this scan and may retry later; it must not loop on
the same cursor.
`telegram.chat.resolve`, `telegram.chat.inspect`, `telegram.chat.sender.list`, and
`telegram.message.get` handle selected peer, rights, available sender identities,
and message facts. All six are live navigation
reads. History is newest first, and a short TDLib response may precede more
history. `getChats` returns a beginning-of-list snapshot; its cursor slices the
loaded list.

Bot Accounts cannot use `telegram.chat.list` or `telegram.message.history`.
Telegram's underlying [dialog list](https://core.telegram.org/method/messages.getDialogs)
and [history](https://core.telegram.org/method/messages.getHistory) methods are
user-only, and the connector rejects those two actions before a provider call
with repair guidance. A bot can select new messages from its retained updates,
resolve a known chat, inspect it, and fetch a known message by ID.

These live reads do not backfill `communication-message`,
`communication-channel`, or `agent_request` records. Each requested page goes
through the explicit action response and audit path; the agent chooses
whether selected facts warrant a separate authorized write. The daemon-held
TDLib session still uses its native encrypted Account database for authorization
and local chat/message caching, which is distinct from StackOS communication
resources.

The six navigation reads default to transient output. Call them directly
through `action.run`, or through granted foreground `action.execute` in an
active run-plan step, with `response_mode: "raw"`. An explicit
`output_policy_json: {"mode": "transient"}` has the same effect. The bounded
redacted result appears only in that immediate response. The
`actionCall` audit retains safe receipt shape/count instead of the page body;
the call cannot be replayed, so omit `intent_id` and `idempotency_key` for a
direct call and do not provide an explicit workflow idempotency key. A granted
workflow read retains its run/plan/step audit linkage without a replay key.
This foreground mode defaults to a 64 KiB response ceiling and can be explicitly
raised to 256 KiB through `output_policy_json.max_inline_bytes`. Reduce `limit`
or leave `include_content` false if a full-text page exceeds that bound. Other
output modes require an explicit override and follow the normal file/inline
action-output policy. `identity.get` returns safe Account metadata; file
downloads remain separate artifact actions. Transient output does not disable
TDLib's native Account cache.

For a navigation-first profile, use
`visibility_policy.surface_mode: allowlist` with empty
`visibility_policy.allowed_surface_refs` and
`visibility_policy.allowed_update_types` lists. Telegram applies this
allowlist/no-update-type behavior when those fields are omitted, so a newly
bound bot or user Account retains no inbox history by default.
The agent can inspect the live chat list, then select both the
`telegram-chat:<id>` refs and TDLib update types (for example,
`updateNewMessage`) worth following through
`communicationProfile.upsert`. The shared communication processor retains
subsequent inbound updates only when both selectors match; it does not backfill
earlier messages. Empty selectors grant no blanket inbox retention for either
bot or user Accounts. The separate trigger and access policies still decide
whether a stored message creates agent work. Profile visibility is the one
retention-policy owner; the Telegram adapter only normalizes update types and
surface refs, without a separate storage decision or follow registry. Native
updates without a chat surface, such as `updateUser` or `updateFile`, cannot
match a selected-chat allowlist; retaining them requires an explicit broader
`surface_mode: all` and an update-type selection. That mode also admits selected
chat updates from all chats unless `dm_mode`, `group_mode`, and `channel_mode`
narrow those chat scopes.

Outbound content uses the typed schemas of `telegram.message.send` and
`telegram.album.send`. They cover text and the supported native media/content
forms, with daemon-held artifact/file resolution. `telegram.message.forward`,
`telegram.message.edit`, `telegram.message.delete`, `telegram.message.react`,
`telegram.poll.stop`, and `telegram.file.download` are explicit provider
actions. A profile/Account/surface mismatch rejects before a provider call.

Buttons are bot-only. A bot send can include URL buttons or opaque callback
data. Callback data is untrusted, has the provider size limit, and must not
contain secrets, prompts, or business decisions. StackOS records the meaningful
state as a `communication-interaction`; a later native callback can create work
only through shared policy. User-Account sends that request buttons or callback
answers reject with repair context; they never silently drop the feature.

`telegram.message.broadcast` and `communication.sendBatch` are durable,
paced fan-out paths. `communication.sendBatch` accepts a bounded inline list
of at most 1,000 recipient refs only when the selected target has
`send_policy.destination_mode: recipient-list` and permits the actor profile.
The accepted call freezes its own recipient snapshot—there is no campaign,
staging, or recipient-management subsystem. Recipient refs are
`telegram-user:<id>` or `telegram-chat:<native-id>`; a raw numeric ID is not a
recipient ref. Acceptance validates the syntax and target policy, then queues
each recipient without asking Telegram whether it is reachable. Resolution and
send happen within that recipient's leased delivery item. Telegram's result is
recorded for that item, including inaccessible peers, so one bad recipient does
not reject the list. The agent uses those results to decide whether to change
its own distribution list. Agents submit another explicit job when a larger
audience needs chunking. The durable executor uses bounded asynchronous sends,
Account and destination pacing, and queued/running/terminal receipts for
polling. A pending Telegram send is not reported as delivered until TDLib
returns a final success update.

The current free-bot pace is one submission slot per 0.047619 seconds per
Account (about 21 per second) and one per 1.43 seconds per private chat, shared
across jobs attached to that Account. Negative group/channel chat refs use a
4.29-second destination interval. This targets roughly 70% of Telegram's
[published Bot API guidance](https://core.telegram.org/bots/faq#broadcasting-to-users)
of about 30 bulk messages per second, one per second per chat, and 20 per
minute per group. TDLib's dialog-ID ranges let StackOS distinguish a private
user chat from a negative group/channel ref without a provider preflight. Both
destination intervals allow a broadcast with one send to each distinct chat to
use the Account-wide pace.
User Accounts use a conservative one-second Account interval because Telegram
does not publish an equivalent universal user-account send rate. These are
per-message reservation floors, not guarantees of TDLib/MTProto acceptance.
An album reserves one unit per media item, and a forward reserves one unit per
message ID before its one TDLib request runs. A proven no-effect FloodWait or
slow-mode rejection defers the affected item with its delay. A terminal TDLib
failed-send update retains the item as failed and extends the shared Account or
destination admission for the reported delay, without automatically resending.
`PEER_FLOOD` records the recipient failure and visibly pauses the affected job
for agent inspection and an explicit resume or cancel decision. The pause stops
new claims; up to the bounded in-flight window may already have been submitted
and still receive independent final receipts.

Telegram configuration and output are safe by default: Account/setup reads
return opaque credential refs, account kind, safe identity/status, and redacted
proxy facts. Native session database paths, secrets, authorization challenges,
and transport internals never appear in public output.

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

#### External evidence handoff

An IMAP message can be untrusted source evidence for a workflow whose
authoritative records live outside StackOS. In that case, mailbox search is
discovery, not acknowledgement; `communications.imap.message.export` retrieves
one selected UID with read-only `BODY.PEEK[]`; and `\Seen` is a separately
granted provider action only after the host has atomically persisted and
verified the original in the selected external backend. The generic search
cursor's `last_observed_uid` remains observation-only state, not finance
progress or receipt acknowledgement.

The connector stages bounded raw MIME and extracted attachments beneath its
existing daemon-owned `ActionConnectorRequest.asset_dir`, returning only an
allowlisted manifest with a project-contained `staging_uri`, opaque transfer id,
safe source identity, canonical staged paths, hashes, byte counts, and media
types. The URI is not a daemon path or a `host_handoff` object; the trusted host
maps it through its own local filesystem runtime and reads only the exact
manifest files. It is a host-filesystem-only locator, not an HTTP URL: the
normalized `imap-transfers` generated-assets subtree always returns HTTP 404,
including to a bearer-authenticated request, and resolved-target checks prevent
a symlink or other public-looking alias from exposing it. Staging is a temporary
transport copy—not a communication resource, artifact, finance record, StackOS
filesystem integration, or storage service. Raw MIME, attachment bytes,
headers, bodies, addresses, and attachment filenames must not enter action
output, durable resources, artifacts, or action-audit fields. The host agent
owns validated evidence writes and a separately granted transfer-id-only
connector cleanup action removes the temporary directory after acknowledgement.
The full implementation contract, including the 10 MiB raw-message limit, 20
attachment/8 MiB-per-attachment/10 MiB-total limits, 200 MIME parts, 20 MIME
levels, identity, deduplication, recovery, no-ack cases, and the current search
cursor's observation-only meaning, is
[`plugins/finance/references/imap-host-handoff-contract.md`](../../plugins/finance/references/imap-host-handoff-contract.md).

The generic IMAP actions retain plaintext compatibility for non-finance
mailboxes. A finance workflow must instead preflight a successfully verified
SSL or STARTTLS credential, use the TLS-only evidence export, and pass the
observed UIDVALIDITY back to the separately granted acknowledgement action.
It must never treat a plaintext search/mark result as receipt-intake progress.

## First-Party Plugin

The implemented communications plugin is
[`plugins/communications/plugin.yaml`](../../plugins/communications/plugin.yaml).

Capabilities:

- `messaging`: send and receive chat-style messages.
- `email-send`: send email through SMTP or future provider APIs.
- `email-inbox`: inspect and update mailbox messages.
- `agent-triggering`: expose inbound provider events as claimable agent work.

Providers:

- `local-agent-chat`
- `telegram`
- `slack-bot`
- `smtp`
- `imap`

`local-agent-chat` is the provider-neutral local conversation surface for a
user who wants to talk directly to an agent through StackOS. Telegram is a
remote transport adapter, not the only agent conversation channel.

`telegram` is executable for a bot or user Account's managed TDLib identity,
peer inspection, typed direct/media sends, bounded durable broadcasts, message
mutation, bot callbacks, native file download, and normalized native updates.

The plugin may later add Discord, WhatsApp Business, Twilio, Gmail API,
Microsoft Graph mail, or project-local communication connectors, but those
providers need their own contract review before execution.

`slack-bot` is executable for Web API identity, message send, native message
reaction add, message delete, conversation discovery, membership sync, and
signed HTTP Events API/Interactivity ingress. Live conversation history reads
and external file uploads are also executable explicit actions. Socket Mode,
thread-reply expansion, file downloads, reaction remove, and administration
remain deferred. Stored Slack reads come through StackOS communication records;
`communications.slack-bot.conversation.history` reads one live conversation page.

## Resource Model

Communication records should be plugin resources first. Avoid bespoke
provider-specific core tables unless a generic queue or lock invariant requires
one.

### `communication-profile`

Represents one project-scoped, provider-neutral agent/human-facing identity
and policy bundle.

Example fields:

- `profile_ref`
- `key`
- `enabled`
- `identity`
- `agent_guidance`
- `provider_facets`: safe provider refs such as Telegram `credential_ref` and
  Account kind, or Slack `credential_ref`/`bot_user_id`; never secret material
- `access_policy`
- `visibility_policy`
- `trigger_policy`
- `context_policy`
- `response_policy`
- `send_policy`
- `handoff_policy`
- `approval_policy`
- `metadata_json`

Telegram facets carry safe provider `refs` and explicit profile binding. See
[Telegram TDLib](#telegram-tdlib) for Account binding and policy semantics.

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
optional `profile_ref`, and optional `thread_ref`. Telegram targets include the
canonical `surface_ref`, optional `thread_ref`, and an explicit `profile_ref`.
The
high-level delivery operation adds message body/media/callback details and
rejects if the resolved provider cannot represent them exactly.

`send_policy` may scope target use by `allowed_profile_refs`,
`allowed_invoker_refs`, `allowed_source_surface_refs`, and
`allowed_target_refs`. All supplied allowlists are enforced together. Use
`invoker_ref` on `communicationTarget.resolve` when the source request has a
human/bot actor, for example `telegram-user:7151482796` or `slack-user:U111`.
This keeps the important restriction on who is allowed to ask, while still
letting an approved user route messages to any explicitly configured target.

A Telegram fan-out target sets `send_policy.destination_mode: recipient-list`.
It has no fixed `surface_ref`; it may be used only by `communication.sendBatch`
with its bounded explicit recipient list. Fixed targets require
`communication.send` and reject a recipient list.

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

Agents use `ingressEndpoint.routes` to inspect the exact HTTP provider routes
and `ingressEndpoint.sync` to write safe route metadata into HTTP provider
profiles.
Slack manual setup can be attested only for the exact current route URL through
the local-admin-only `ingressEndpoint.confirmManualUpdate`; changing the endpoint
invalidates the attestation automatically. Local-tunnel endpoints are considered
reachable only for a short interval after successful discovery, so a stale
ngrok URL cannot keep the project in a false-ready state or be synced back to a
provider. `communicationProfile.upsert` drops every daemon-owned route field,
including public URL/host policy, nested ingress refs, and
`manual_ingress_confirmation`; an agent cannot attest to a provider-console
change through normal profile setup. Telegram never uses this resource: its
native TDLib session owns the authenticated connection and update stream.
Telegram secrets never appear through this resource, and neither do Slack
signing or HTTP-ingress secret fields.

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
- `credential_ref`
- `ingress_mode`
- `last_update_id`
- `allowed_updates`
- `pending_update_count`
- `last_webhook_at`

IMAP examples:

- `credential_ref`
- `mailbox_ref`
- `uidvalidity`
- `last_observed_uid` (a search observation only; it is never an acknowledgement)
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
- `communication.send`: normal provider-neutral outbound delivery to one fixed
  named target. Media-bearing Slack sends resolve to `slack-bot.file.upload`;
  Telegram sends resolve to `telegram.message.send` or
  `telegram.album.send` according to the typed content schema.
- `communication.sendBatch`: queues durable, paced delivery to an explicit
  inline Telegram recipient list for one target whose
  `send_policy.destination_mode` is `recipient-list`. It accepts 1–1,000
  `telegram-user:<known-id>` or `telegram-chat:<native-id>` refs, validates the
  Account can resolve each peer before freezing the snapshot, and returns a
  durable action/receipt reference. It is not a campaign, artifact, or
  subscriber-list management operation.
- `communication.reply`: normal provider-neutral reply to the origin of a
  stored agent request.

Setup/read operations do not execute provider APIs. `communication.send`,
`communication.sendBatch`, and `communication.reply` execute through the same
daemon-side action executor and audit ledger; agents do not receive secrets or
raw credentials. A batch is accepted before its asynchronous per-recipient
work runs, then exposes queued/running/terminal receipt state and individual
safe outcomes through the normal action lifecycle. `communicationTarget.resolve` returns `allowed`,
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

Agents receive `provider_key`, `credential_ref`, Account display name,
`auth_method_key`, connection status, safe account metadata, scopes/permissions,
and safe diagnostics. They never receive tokens, passwords, refresh tokens,
authorization headers, webhook secrets, or raw credential payloads.

### Telegram TDLib Auth

Provider key: `telegram`

Auth methods: `tdlib-bot-token` and `tdlib-user-session`.

Telegram credentials are global reusable Accounts attached explicitly to the
project and referenced by `communication-profile.provider_facets.telegram`.
Actions name the profile, surface, target, or recipient—not a raw credential.
The daemon resolves and validates profile/Account binding before starting a
native TDLib operation.

Both Account kinds reuse one encrypted daemon-held application `api_id` and
`api_hash` pair configured on first Telegram Account setup. A bot Account
holds its own daemon-held `bot_token` and verifies it once during Account
creation; a failed first attempt leaves local setup retryable. A user Account
completes its native authorization transaction during Account setup. Both
retain authorization in daemon-managed native state and close their setup
session. Safe Account output contains Account kind, safe identity,
connection status, and opaque `credential_ref`.

Optional per-Account proxy setup accepts `proxy_enabled`, `proxy_type`
(`socks5`, `http`, or `mtproto`), `proxy_host`, `proxy_port`, and the
type-appropriate secret authentication fields (`proxy_username`,
`proxy_password`, or `proxy_secret`). It redacts secret proxy fields on every
read. Updating, removing, testing, or reconnecting a proxy is Account-local and
cannot cause direct-connect fallback.

Bot behavior and user behavior—identity, guidance, access, visible surface
constraints, trigger patterns, context windows, response policy, and send
policy—belong to `communication-profile`, never to Account credentials.

Credential tests verify managed runtime readiness and native authorization
state, then return safe identity/diagnostics. They never return application
hashes, bot tokens, user phone/code/password, authorization state payloads,
proxy secrets, native database paths, or transport transcripts.

### Slack Bot Auth

Provider key: `slack-bot`

Auth method: `bot-token`

Slack credentials are global reusable Accounts bound from
`communication-profile.provider_facets.slack-bot.credential_ref`. The Account
must be explicitly attached to the communication profile's project. Agents and
action payloads name the communication profile, surface, channel, user, thread,
or target refs; they never receive Slack tokens or signing secrets.

Slack owns one Events API request URL per app Account. StackOS therefore permits
one inbound-enabled communication profile to own ingress for a given Slack
Account. Profiles default to inbound enabled for backward compatibility. Set
`provider_facets.slack-bot.ingress_enabled: false` for an outbound-only profile
in another attached project; it remains valid for sends but is omitted from
ingress route discovery and webhook sync.

Safe profile/account metadata may include:

- `team_id`
- `app_id`
- `bot_user_id`
- Account display name and opaque `credential_ref`

Current setup fields:

- `bot_token`
- `signing_secret`

`app_token` is reserved for future Socket Mode support and is not part of the
current connection setup form.

Credential tests:

- `account.test` verifies the bot token, returns safe team/user/bot metadata, and
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
- `tls_ca_pem`: optional public CA certificate bundle (PEM, maximum 64 KiB),
  stored on the Account and used by both the Account probe and IMAP actions
- `mailbox_refs`
- `search_limit`

Secret fields:

- `password`

Credential tests:

- Connect, authenticate, select the default mailbox, and return safe mailbox
  capability/status metadata.
- Do not return raw mailbox transcripts or message bodies.
- Failures return the existing normalized Account-test summary, next action,
  retryability, and finite `metadata.stage` / `metadata.reason_code` fields.
  Connection refusal, DNS, timeout/network, certificate verification, TLS
  negotiation, rejected login, unavailable default mailbox, and protocol failures
  stay distinguishable without exposing exception text. A rejected login does
  not prove a wrong password; an unavailable mailbox does not distinguish a
  missing mailbox from denied access. Failed probes remain saved diagnostics,
  not Account revocation. Probes select read-only and use LOGOUT, never CLOSE,
  message fetches, or flag changes.

For a private CA or temporary local GreenMail fixture, save the reviewed public
CA certificates in the Account's optional `tls_ca_pem` field. StackOS accepts
certificate-only PEM bundles, validates CA constraints, and rejects private keys,
malformed certificates, non-CA certificates, and other content before saving.
The bundle adds trust only to that Account's IMAP connections; default roots,
certificate validity checks, and hostname verification remain enabled. Omitted
values preserve saved trust on edit; explicitly clearing the field removes the
additional trust. Invalid edits leave the saved Account unchanged. Invalid
stored trust configuration returns `tls_configuration_error` at the TLS stage.

Account trust lives in the existing local database and survives daemon restart
and app replacement without a temporary CA-file path or `SSL_CERT_FILE` setting.
The live server, approved listener ports, and certificate lifetime still belong
to the operator's fixture setup. A saved CA does not extend its validity period.
Rerun the read-only Account test before receipt work after a server or certificate
change. Never disable verification or install global trust as a workaround.

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

Action connector: `stackos/actions/telegram.py`, backed by the managed native
runtime in `stackos/integrations/telegram_tdlib/`.

Action refs:

- `communications.telegram.identity.get`
- `communications.telegram.chat.resolve`
- `communications.telegram.chat.inspect`
- `communications.telegram.chat.list`
- `communications.telegram.message.get`
- `communications.telegram.message.history`
- `communications.telegram.message.send`
- `communications.telegram.message.broadcast`
- `communications.telegram.album.send`
- `communications.telegram.message.forward`
- `communications.telegram.message.edit`
- `communications.telegram.message.delete`
- `communications.telegram.message.react`
- `communications.telegram.poll.stop`
- `communications.telegram.callback.answer`
- `communications.telegram.file.download`

Every action takes an explicit project `profile_ref`; chat/message actions also
take the canonical surface/message identifiers required by their schema. The
connector resolves the attached daemon-held Account, validates the profile and
target grant, and reports Telegram's peer or send failure for each attempted
delivery. It returns
safe native result refs and durable receipt/status facts, never native database
paths, secrets, raw authorization states, or transport payloads.

`telegram.message.send` carries one typed Telegram content value plus optional
delivery options. Its schema defines the supported text, media, file, poll, and
other native content forms. `telegram.album.send` accepts the schema-defined
album forms. File values refer to an approved artifact, a public URL, or the
Account-qualified native reference returned by Telegram reads and downloads:
`telegram-file:<credential_ref>:<positive TDLib file id>`. Raw TDLib file IDs
and remote IDs are not send inputs. The connector rejects a qualified native
file from another Account before a provider effect.
Numeric TDLib file IDs are scoped to the current native Account session; they
are not permanent content identifiers. After reconnecting, refresh a selected
file reference through `telegram.message.get` or `telegram.message.history`
before downloading or sending it. Message read results and retained photo
attachments exclude Telegram's embedded `i`/`j` preview sizes, which are not
downloadable files. A reported size of zero alone does not exclude a file.
`telegram.file.download` runs in the background and publishes a generated
artifact only after TDLib confirms completion. If TDLib does not answer before
the request timeout, `actionCall.get` reports `retryable_timeout`, the same
Account-qualified `file_ref`, and `next_action` for a safe retry; the timeout
does not establish that the file is permanently unavailable.

A sealed durable job pins
each referenced artifact until every dependent item has a definite success,
failure, or cancellation receipt; an unknown native outcome remains pinned
until reconciliation resolves it. The pin seals the artifact URI and SHA-256
of the generated file. A selected retry or queued provider effect rechecks that
same active artifact and digest before it can proceed, so archive, URI changes,
or replacement bytes require a newly submitted delivery.

`telegram.message.broadcast` is provider-specific asynchronous fan-out. The
normal `communication.sendBatch` operation supplies the same capability through
a target's policy/grant boundary. Both freeze syntactically valid recipient
refs, use the shared durable executor, and report queued/running/terminal status
with safe individual outcomes. The provider decides whether a recipient can
actually receive the message. Neither accepts a bare numeric Telegram ID or
issues an unpaced all-at-once send.

`telegram.chat.list` and `telegram.message.history` are read-only live TDLib
navigation actions for user Accounts, each scoped to an explicit project profile and attached
Account. They return bounded pages and continuation facts without importing
those pages into StackOS communication resources. `telegram.message.history`
preserves the Account's Telegram access boundary and returns messages in
descending message-id order. `telegram.chat.list` supplies `next_cursor` for
Main/Archive list continuation; its offset reflects the current TDLib ordering,
so concurrent chat activity may cause a repeated or skipped entry across pages.
`telegram.message.history` supplies `next_before_message_id` for older messages;
a short page is not by itself proof that history is exhausted. An inconclusive
empty page sets `pagination_inconclusive` with `next_action` after bounded
native probes. These actions
require an explicitly connected Account; a profile
visibility allowlist governs future inbound retention, not which already
accessible chats an agent can navigate live. Stored history remains available
separately through `communicationContext.query`.

For channel posts, `telegram.chat.inspect` reads the connected Account's own
channel status and `can_post_messages` right from TDLib. It also returns the
Account's boolean administrator rights and effective per-content send permissions.
A channel owner or an
administrator with that right may post to the channel. A subscriber or an
administrator without the posting right may not. The destination chat and the
sender identity are separate facts: `telegram.chat.sender.list` returns the
currently selected sender and the sender refs TDLib permits for that destination.
To send as a channel into a different chat, use an available
`telegram-chat:<channel-id>` as `sender_ref` on the Telegram send, album, or
forward action. Broadcasts validate that sender for each recipient. The sender
choice is selected immediately before the send under the Account's durable
delivery lease, and the returned message sender is checked before success is
reported. If more than one sender is available, the agent must choose one
explicitly; StackOS does not reuse an earlier sender selection implicitly.
For the normal `communication.send` path, a named target can carry the selected
`sender_ref` in its action input defaults. A TDLib sender choice is specific to
the destination chat and may change when Telegram rights or Premium status
change; inspect it again before a later campaign.

`telegram.callback.answer` is valid only for bot Accounts and a stored callback
query. `telegram.message.send` rejects button/callback payloads for user
Accounts. `telegram.message.edit`, `telegram.message.delete`,
`telegram.message.react`, and `telegram.poll.stop` enforce the Account's
actual peer/message rights; capability or provider denial is a structured,
non-degrading result. `telegram.chat.inspect` provides the explicit rights
check needed before a channel send. None of these actions registers a webhook,
calls a legacy HTTP endpoint, or starts a polling loop.

### Slack Actions

Connector package: `stackos/actions/slack_bot/`

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
- `communications.slack-bot.conversation.history`

Executable in the current Slack connector:

- `identity.get` through Slack Web API `auth.test`
- `message.send` through Slack `chat.postMessage`
- `file.upload` through Slack `files.getUploadURLExternal` and
  `files.completeUploadExternal`
- `reaction.add` through Slack `reactions.add`
- `message.delete` through Slack `chat.delete`
- `conversation.open` through Slack `conversations.open`
- `conversation.info` through Slack `conversations.info`
- `conversation.list` through Slack `conversations.list`
- `conversation.members` through Slack `conversations.members`
- `conversation.history` through Slack `conversations.history`

Validation rules:

- `message.send` requires `channel_ref` or `surface_ref` plus `text` or
  `blocks` and an explicit `profile_ref` to bind outbound message, channel, and
  interaction state to a project communication profile; the connector
  resolves that profile server-side and rejects the call unless
  `provider_facets.slack-bot.credential_ref` matches the daemon-resolved
  attached Account. There is no Account-name or provider-wide fallback profile.
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

#### Reading complete selected Slack content

`conversation.history` requires the selected project profile and channel/surface.
Default results retain message refs and 500-character `text_preview` values.
Each message reports `text_preview_truncated`. Set the strict boolean
`include_content: true` only when the task needs full selected content; the
response then includes untruncated `text`, `blocks`, `attachments`, and selected
`files` descriptors. The option controls local output, not a Slack query field.
Read these fields from the normal action response file; raw mode cannot recover
content omitted by a preview-only call.

File descriptors use `file_ref` and selected metadata, not private download URLs,
thumbnail URLs or file bytes. Rich Slack file references are similarly mapped;
URL-only Slack file objects report `url_omitted`. Secret-bearing URLs and known
credential values remain sanitized. This read does not grant forwarding rights.

`content_included` reports the selected output mode, not archive completeness.
Inspect `has_more` and `next_cursor`, and continue with the same profile/channel
and time bounds. Preserve optional provider `is_limited` as reported; an absent
flag is not proof of complete retention or access. `reply_count` describes thread
presence, not fetched replies. This endpoint does not fetch full thread replies
or content beyond the Account's access/retention scope. Slack's rate limits and
effective page caps vary by app class; honor returned cursors and `Retry-After`.
Official contracts: [history](https://docs.slack.dev/reference/methods/conversations.history/),
[file objects](https://docs.slack.dev/reference/objects/file-object/),
[thread replies](https://docs.slack.dev/reference/methods/conversations.replies/).

Deferred until separate tests/contracts:

- Socket Mode listener and `apps.connections.open` runtime.
- Thread reply reads, file downloads, reaction remove, user/profile lookup,
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
- `communications.imap.message.export`
- `communications.imap.message.export.cleanup`
- `communications.imap.message.mark_seen`
- `communications.imap.message.mark_unseen`

Validation rules:

- Use mailbox refs and UIDs.
- Reject sequence-number-only operations.
- Bound search limit and fetch body size.
- Let agents request only selected fields for metadata fetches. Evidence export
  uses a separate bounded, read-only `BODY.PEEK[]` transfer with RFC822.SIZE
  preflight; it is never artifact or resource storage.
- Evidence export and cleanup are write-risk actions because they create/delete
  connector staging, even though export does not mutate the provider mailbox.
- Mark-seen and mark-unseen are write actions and need approval/grant coverage;
  finance acknowledgement supplies the selected mailbox's expected UIDVALIDITY.
  A tagged STORE success alone is not acknowledgement: read back the exact UID
  and intended flags, or return an outcome-unknown recovery error without
  recording success. Generic body fetch also requires one exact UID-bound
  literal; unsolicited or ambiguous literals must never replace that message.
  Cleanup uses LOGOUT directly, never CLOSE or EXPUNGE, so unrelated messages
  already flagged deleted are preserved (RFC 9051 sections 6.4.1 and 6.4.9).
- Persist mailbox cursor/channel/message/event resources from connector output
  without exposing IMAP passwords. Search cursor state is observation-only
  (`last_observed_uid`), never acknowledgement.

#### IMAP result coverage and continuation

`messages.search` returns `matched_count` before applying the caller's `limit`,
`has_more`, and `next_after_uid` (null at the end). Counts describe the current
remaining search, not a frozen mailbox snapshot. To continue, keep the Account,
mailbox and criteria unchanged, pass `next_after_uid` as `after_uid`, and pass
the observed `uidvalidity` as `expected_uidvalidity`. A changed/missing epoch
stops continuation before search. `after_uid` is exclusive; it is not a message
acknowledgement. Matching UIDs are ordered and filtered to the requested bounds:
RFC 9051 defines `UID n:*` to include the last UID even when `n` exceeds it, so
that provider behavior must not create repeated final pages. Mailbox mutation
can change later results; neither `has_more: false` nor a cursor proves receipt
processing or immutable coverage.

`message.fetch` uses a nonzero bounded `max_body_bytes` MIME-prefix read and
always returns `content_completeness`:

- `scope: parsed_fields_from_mime_prefix` distinguishes selected parsed fields
  from the complete original message and attachment evidence.
- `fetched_bytes`, `parsed_bytes`, and `max_body_bytes` report the byte boundary;
  top-level `size_bytes` is the provider's RFC822.SIZE observation.
- `raw_message_complete` and `raw_message_truncated` compare that observation
  with the available MIME bytes. Unknown size remains null, not a complete claim.
- `truncated_fields` names selected previews/bodies clipped locally;
  `mime_parse_defects` reports parser defects.
- `full_content_action_ref` points to `communications.imap.message.export` for
  complete original MIME and bounded attachment evidence under its own grant.

A fully fetched MIME literal does not mean every MIME part is represented in
`body_text`. For receipt intake, continue using the existing export/store/verify/
acknowledge method, not preview text as financial evidence. See
[RFC 9051 FETCH](https://www.rfc-editor.org/rfc/rfc9051.html#section-6.4.5) and
[UID semantics](https://www.rfc-editor.org/rfc/rfc9051.html#section-6.4.9).

## Trigger And Ingestion Modes

### Normal Telegram Listener: Managed TDLib Session

There is no public Telegram ingress route. After an attached bot or user
Account completes native TDLib authorization and an agent explicitly connects
its session, TDLib receives native updates and passes them to the single
Telegram update normalizer. The
normalizer validates the Account generation/profile attachment, creates stable
provider refs, and invokes the shared communication processor.

Flow:

1. Operator creates or selects a reusable bot or user Telegram Account, enters
   application credentials, completes its applicable native authorization, and
   attaches the Account to the project.
2. Operator configures an enabled `communication-profile` with the Account's
   safe `provider_facets.telegram.credential_ref`, identity/guidance, and
   access, visibility, trigger, context, response, and send policies. The
   visibility policy selects exact surface refs and native update types before
   any inbound update can be retained.
3. The managed session receives a TDLib update and rejects a stale session
   generation or detached Account before processing it.
4. The normalizer maps native chat/message/callback/member/file facts to
   canonical provider/profile/surface refs and supplies the normalized event to
   `stackos/communications/processor.py`.
5. Shared policy decides whether it can store the event and whether it creates
   or replays one idempotent `agent_request`. Edits, deletes, membership facts,
   and file facts never create work by themselves.
6. The processor stores allowed resources and safe policy context. It does not
   call a model, select a workflow, infer business intent, or send an automatic
   reply.

The session reconnects under its Account owner. It does not fall back to Bot
API, webhook delivery, polling, a second client session, a direct connection
after proxy failure, or a provider-wide credential. Native update inputs and
callback data are untrusted; callbacks wake work only when their stored
interaction and shared policy permit it.

### Normal Slack Listener: Signed HTTP Ingress

Current Slack HTTP ingress endpoint:

```text
POST /api/v1/ingress/slack/{project_id}/{profile_key}
Headers:
  X-Slack-Request-Timestamp: <unix seconds>
  X-Slack-Signature: v0=<hmac>
```

This endpoint is bearer-token whitelisted because Slack cannot send the daemon
bearer token. It resolves a project-scoped `communication-profile`, requires
that its `provider_facets.slack-bot.credential_ref` Account is attached to the
same project, verifies Slack's raw-body HMAC signature against the encrypted
Slack signing secret, and then applies static profile policy. Invalid profile,
Account attachment, timestamp, or signature failures all return the same
invalid-signature class of response.

Flow:

1. Operator creates or selects a reusable `slack-bot` Account with `bot_token`
   and `signing_secret`, then attaches it to the project.
2. Operator or setup agent calls `communicationProfile.upsert` to create a
   project-scoped communication profile with Slack identity, safe bot refs,
   access policy, trigger policy, context policy, response policy, send policy,
   and `provider_facets.slack-bot.credential_ref`.
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

### Static Scheduled Ingestion Runner

Scheduled ingestion remains useful for providers such as IMAP, or for future
static maintenance jobs that run inside audited StackOS run plans. Telegram's
managed TDLib session is its normal listener; a scheduled job must not imitate
an update listener. Any explicit Telegram provider call from a runner is
granted, bounded, and audited; it cannot infer intent beyond
communication-profile policy, and agents still claim requests and decide what
to do.

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
User sends a DM to a bot or user Account
-> managed TDLib session receives updateNewMessage
-> normalizer supplies canonical profile/surface/message refs to shared policy
-> shared visibility selects that DM surface and updateNewMessage
-> StackOS stores allowed communication-message
-> allowlist and trigger policy create agent_request
-> agentRequest.list shows unread request
-> agent calls agentRequest.prepareRunPlan with a chosen template or run plan
-> agent executes needed actions
-> agent replies with communication.reply
-> agent completes request
```

### Telegram Group Mention

```text
Message appears in a visible group
-> TDLib update normalizes the native chat/message identity
-> shared visibility selects that group surface and updateNewMessage
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
-> bot-only `communication.send`/reply resolves the Telegram provider action
-> StackOS stores outbound communication-message and interaction refs
-> user presses button
-> managed TDLib session receives the native callback update
-> StackOS stores communication-event and marks interaction clicked
-> explicit `telegram.callback.answer` may clear Telegram client loading state
-> allowlist creates agent_request with event/interaction refs
-> agent prepares or claims request and decides follow-up
-> agent may answer callback, edit buttons, send typed media/text, or run other tools
```

Callback data is a routing hint, not trusted workflow logic. If the click should
mean "approve budget" or "generate variants", the agent must verify the linked
project/run/resource context before acting.

### Telegram Image Reply

```text
Agent generates or selects image artifact
-> typed `telegram.message.send` file value resolves the approved artifact or URL
-> Telegram TDLib returns native message facts
-> StackOS records outbound communication-message with provider_message_ref
```

The action result may include safe native file and message refs, but never a
native database path or secret.

### Simulated End-To-End Flows

These traces are local/mockable flows for policy and storage behavior. They
describe what StackOS records; they do not imply daemon-side model execution.
Each retained Telegram trace assumes the profile selected both the cited chat
surface and TDLib update type. With either selector missing, shared policy stores
no inbound record and creates no request.

Allowed DM:

```text
1. A managed TDLib session receives a private-message update for project A /
   communication profile support.
2. Profile support resolves `provider_facets.telegram.credential_ref` server-side.
3. access_policy allows the chat and user; trigger_policy allows DM.
4. StackOS stores communication-event and communication-message.
5. StackOS creates one agent_request with profile_key, chat_ref, and source_message_ref.
```

Allowed group mention with history context:

```text
1. A managed TDLib session receives a group message that mentions @support_bot.
2. profile support can observe the group and allows the user plus mention trigger.
3. StackOS stores the new message and selects bounded stored history by context_policy.
4. StackOS creates one agent_request with group/thread refs and context hints.
5. The agent claims the request and decides whether the history changes the response.
```

Observed non-trigger:

```text
1. A managed TDLib session receives a visible group message without mention, command, or reply-to-bot.
2. visibility_policy permits storing non-trigger messages.
3. StackOS stores the message with observed policy status.
4. StackOS creates no agent_request.
```

No-store non-trigger:

```text
1. A managed TDLib session receives a visible group message without a configured trigger.
2. visibility_policy.store_non_trigger_messages is false.
3. StackOS writes no communication records for the update.
4. StackOS creates no agent_request.
```

Unauthorized user:

```text
1. A managed TDLib session receives a trigger from a visible chat but a disallowed user.
2. StackOS verifies the communication profile/Account binding before applying user policy.
3. StackOS may store the event/message as invoker_blocked context.
4. StackOS creates no agent_request.
```

Outbound reply tied to `source_agent_request_id`:

```text
1. Agent claims agent_request 42 from communication profile support and chat telegram-chat:100.
2. Agent calls communication.reply with request_id 42 and message content.
3. StackOS resolves support's Telegram facet `credential_ref` and verifies request/chat/thread origin when response_policy requires it.
4. Telegram TDLib delivery executes through the daemon action executor with daemon-held credentials.
5. StackOS records the outbound communication-message and action-call audit.
```

Proactive target send:

```text
1. Agent calls communication.send with an explicit target such as telegram-operator-dm.
2. StackOS verifies the target send_policy, actor profile, surface, and credential.
3. Provider action executes without requiring source_agent_request_id, because this is not a same-origin reply.
4. StackOS records the outbound communication-message and action-call audit.
```

Paced subscriber broadcast:

```text
1. Agent calls communication.sendBatch for an authorized Telegram recipient-list target.
2. StackOS checks the allowed actor profile and target policy, then freezes at
   most 1,000 syntactically valid telegram-user:<id> or
   telegram-chat:<native-id> refs without provider reachability checks.
3. The durable executor accepts the job and returns its receipt before sending.
4. Bounded concurrent workers submit eligible items under shared Account and
   destination pacing; each item's Telegram result is recorded independently.
5. The agent polls the durable receipt to observe queued, running, or terminal
   status and its safe individual outcomes, then chooses how to maintain its
   distribution list or whether to resume a paused job.
```

Authorized callback:

```text
1. Agent sends message telegram-message:100:501 with callback_data ixn_123 and
   allowed_user_refs.
2. StackOS stores a communication-interaction keyed by support /
   telegram-message:100:501 / ixn_123.
3. The managed TDLib session receives callback ixn_123 from the allowed user/chat.
4. StackOS resolves the stored interaction by communication profile, provider message ref,
   and callback token, then marks it clicked.
5. StackOS creates one agent_request with event_ref and interaction_ref; the
   agent can read the interaction's source_agent_request_id.
```

Unauthorized callback:

```text
1. The managed TDLib session receives callback ixn_123 from a disallowed user or chat.
2. StackOS verifies the profile/Account binding and resolves the interaction.
3. Interaction access policy blocks the click.
4. StackOS stores the event as callback_blocked when policy permits storage.
5. StackOS creates no agent_request.
```

Multiple bots in one project:

```text
1. Project A has profiles support and ops with distinct credential_ref values.
2. Each profile binds explicitly to one attached Account.
3. A managed session validates its Account generation before normalization.
4. Provider ids, interactions, and requests are scoped by profile key.
5. A support update cannot use ops credentials or wake the ops profile.
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

## Security And Privacy

- Agents never receive secrets.
- Telegram application hashes, bot tokens, user authorization inputs/session
  state, proxy credentials, and TDLib database paths must never be returned to
  agents or stored in audit metadata.
- Telegram callback data is untrusted input and must not contain secrets.
- Slack bearer tokens, signing secrets, `response_url`, and `trigger_id` must
  never be returned to agents or persisted in resources/audit metadata.
- Slack HTTP ingress must verify raw-body HMAC signatures and reject stale
  timestamps before storing payload-derived records.
- Public HTTP ingress exposure is opt-in only for the providers that use it.
  Telegram's authenticated TDLib session remains daemon-owned and has no public
  ingress route.
- SMTP and IMAP passwords stay in encrypted credential payloads.
- OAuth/XOAUTH2 stays deferred until mail-provider contracts, scope mappings,
  auth methods, and safe diagnostics are real.
- Allowlist Telegram numeric user/chat ids through safe refs; do not trust
  mutable usernames as the only authorization boundary.
- Message bodies can include PII, customer data, confidential plans, or access
  instructions. Read selected content through sanitized action response files;
  do not create artifacts just to read it. Original IMAP evidence follows the
  [external evidence handoff](#external-evidence-handoff).
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
- Redaction tests prove Telegram credential/session/proxy/runtime fields and
  Slack token/transient callback fields are never persisted or returned.
- Run-plan grant tests prove `action.execute` is required for workflow provider calls.
- REST/CLI/MCP parity tests cover generic `agentRequest.*` operations.
- Queue-to-plan tests cover `agentRequest.prepareRunPlan` idempotent replay,
  rollback on invalid plans, and run-plan metadata linkage.
- Resource tests cover idempotent upsert by `external_id`/provider ref.
- Ingress tests cover Telegram native update dedupe/generation/profile binding
  and IMAP UID/UIDVALIDITY behavior.
- Telegram live-read tests cover explicit connection and profile binding,
  bounded chat/history continuation (including short TDLib pages), action audit,
  and no automatic communication-resource import from returned pages.
- Shared visibility tests cover the intersection of selected Telegram surface refs
  and update types, empty-selection no-retention defaults, and consistent bot
  and user Account behavior.
- Interaction tests cover bot-only Telegram button/callback validation,
  user-Account rejection, Slack Block Kit button validation, callback/action
  normalization, and idempotency.
- Durable-delivery tests cover recipient-list grants, recipient-ref validation,
  snapshot sealing, pacing, recovery, and receipt polling.
- UI smoke tests show provider connections, plugin catalog, resources, agent
  requests, and action calls render with generic components.
- Docs update this file, [README](README.md), [Connector Quality Gate](connector-quality.md),
  [Action Executor](../action-executor.md) connector list when executable, and
  provider setup docs if auth UX changes.

## Current Limitations And Non-Goals

- Running an LLM/model from inside the StackOS daemon.
- Provider-specific MCP tools.
- Provider-specific decisions about whether an Account should answer.
- SMTP delivery/read/open tracking.
- Telegram read receipts.
- OAuth/XOAUTH2 for custom SMTP/IMAP.
- Telegram server-side delivery/read confirmation beyond returned native send
  facts. A completed durable receipt records execution outcomes, not audience
  attention.
- Live Telegram credential proof. Current automated coverage exercises native
  contracts, managed-runtime verification, mocked service behavior, and package
  staging; it does not prove a real bot or user Account can authenticate or
  reach Telegram from an operator environment.
- Specialized workflow UI for each communication use case.
- Automatic bulk history synchronization; bounded live Telegram navigation is
  an explicit read, not a StackOS history import.
- Slack Socket Mode, Slack file downloads, and Slack admin actions.
- Slack thread-reply reads and reaction removal; `conversation.history` reads
  one selected page, not an entire thread or archive.
- Automatic background callback acknowledgement jobs.

## Signoff Criteria

The communications surface is release-ready when:

- Normal delivery guidance points agents to `communication.send` and
  `communication.reply`; `action.run` and `action.execute` remain explicit
  provider escape hatches or workflow-granted actions.
- Ingress stores provider-normalized state and agent requests without invoking
  a model.
- Provider credentials, native user-session state, proxy secrets, and managed
  runtime paths remain daemon-side.
- Mock/local tests cover actions, native updates, resource writes, idempotency,
  durable receipts/pacing, and redaction before real provider credentials are
  required.
- Provider limitations are documented clearly enough that agents do not infer
  fake read, delivery, or approval semantics.
