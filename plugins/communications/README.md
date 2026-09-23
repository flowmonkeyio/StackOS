# Communications Plugin

The communications plugin is the StackOS package for provider-neutral
communication state plus Telegram bot and user messaging, local chat interactions, SMTP
email send, IMAP mailbox/message lifecycle, and communication-driven agent
requests.

Use the [provider contract](../../docs/integration-contracts/communications.md)
for protocol, setup, output, and limitation details. The [manifest](plugin.yaml)
declares resources and action schemas; `action.list`/`action.describe` report
availability in the current project. Generic communication operations store
setup and context without calling providers or models.

## Providers

- `local-agent-chat`: local StackOS conversation surface for direct human-to-agent
  messages, rich response blocks, and button/image/file interactions.
- `telegram`: reusable bot or user Accounts using one managed native TDLib
  session per Account. It supports identity and peer checks, text, media,
  album, forward, edit, delete, reaction, callback-answer, file-download, and
  durable direct or bounded broadcast sends. It also offers live, paginated
  chat-list and per-chat history reads for a connected user Account. Telegram does
  not use the retired HTTP connector, webhooks, or long polling.
- `slack-bot`: bot token and signing-secret auth for identity checks, text or
  Block Kit sends, conversation discovery, membership sync, and signed HTTP
  Events API/Interactivity ingress. Socket Mode is deferred.
- `smtp`: password/app-password SMTP send. SMTP acceptance is not delivery or
  read confirmation.
- `imap`: password/app-password mailbox listing, bounded UID search, selected
  field fetch, `Seen` flag lifecycle, and bounded staged-evidence export with
  transfer-id-only cleanup for the finance receipt route.

## Reading provider content

Stored context comes from `communicationContext.query`; live reads use provider
actions. Slack history defaults to previews and accepts `include_content: true`
for full selected content. IMAP search/fetch expose continuation and completeness
facts; original MIME and attachments use export. Follow the exact
[Slack](../../docs/integration-contracts/communications.md#reading-complete-selected-slack-content)
and [IMAP](../../docs/integration-contracts/communications.md#imap-result-coverage-and-continuation)
output contracts before treating a page or preview as complete.

For Telegram, explicitly connect the attached Account. A user Account can use
`communications.telegram.chat.list` to navigate available chats and
`communications.telegram.message.history` for a bounded page in one selected
chat. Bot Accounts receive selected new messages through retained updates and
can resolve known chats and read known messages; Telegram does not expose the
same dialog-list or history-pagination methods to bots. `chat.resolve`,
`chat.inspect`, `chat.sender.list`, and `message.get` supply selected peer,
rights, available sender identities, and message facts. All live reads need an enabled
project profile bound to that Account. Chat lists support `main` and `archive`;
pass `next_cursor` as `cursor` to continue. For history, pass
`next_before_message_id` as `before_message_id` for older messages and set
`include_content: true` when previews are insufficient. Both paged actions cap
a page at 50 entries. A short TDLib page does not necessarily mean the end; if
history returns `pagination_inconclusive`, stop the scan and consider a later
retry. Chat reordering can shift chat-list pages, and a chat-list scan stops at
5,000 entries with `scan_limit_reached`. Use returned `surface_ref` values for
later reads, preserving the sign of each native chat ID; do not guess IDs from
chat names. Bot and secret-chat summaries report `history_supported: false`.

Live pages are audited as action results, without copying them into StackOS
communication history. All six navigation reads default to transient output.
Use direct `action.run` or granted foreground `action.execute` with
`response_mode: "raw"`. Omit direct `intent_id`/`idempotency_key` and any
explicit workflow replay key; `output_policy_json: {"mode": "transient"}` may
be passed explicitly. This non-replayable mode retains a safe audit receipt
without a response file. TDLib may still cache data in its native Account
database. The agent chooses any facts worth persisting through a separate
authorized write.

Posting into a channel requires the Account to own it or hold its
`can_post_messages` administrator right. To post as a channel in another chat,
read that destination's `chat.sender.list`, then pass its available
`telegram-chat:<channel-id>` as `sender_ref` on a send, album, or forward action.
Broadcasts verify the sender for each recipient. The sender is chosen within
the Account's durable delivery lease and the returned identity is checked;
an ambiguous default requires an explicit choice. A named communication target
can carry `sender_ref` as an action input default for `communication.send`.

For an explicit recipient-list broadcast, StackOS seals syntactically valid,
policy-allowed `telegram-user:<id>` and `telegram-chat:<id>` refs without
preflight reachability calls. Each leased item resolves its recipient, submits
through TDLib, and reports its own provider result through `actionCall.items`.
The agent decides whether an unsuccessful recipient belongs in its distribution
list. The dispatcher overlaps independent pending receipts within a bounded
in-flight window while preserving Account and destination submission pacing.
A `PEER_FLOOD` result visibly pauses the affected job for agent review;
`actionCall.resume` or `actionCall.cancel` is an explicit operator choice.

Photo reads and retained photo attachments omit Telegram's embedded `i`/`j`
preview sizes; a zero-size native file can still be a valid download. Native
`telegram-file:<credential_ref>:<id>` refs are scoped to the TDLib Account
session, so refresh them with `message.get` or `message.history` after a
reconnect. `telegram.file.download` returns a generated artifact after native
completion. Its background `actionCall.get` reports `retryable_timeout` and
safe retry guidance if TDLib does not answer in time; that timeout is not a
permanent-unavailability verdict.

For selective future retention, use a navigation profile with
`visibility_policy.surface_mode: allowlist`, empty `allowed_surface_refs`, and
empty `allowed_update_types`. After discovering chats, select both the chosen
`telegram-chat:<id>` refs and update types through `communicationProfile.upsert`.
The shared processor then retains only new inbound updates matching both
selections; trigger/access policy separately controls agent requests. Telegram
uses empty selectors by default for bot and user Accounts. Updates without a
chat surface, such as `updateUser` or `updateFile`, need an explicitly broader
`surface_mode: all` plus their update type to be retained. Per-kind modes can
still narrow chat visibility. This selection does not import prior history.
See the [live navigation contract](../../docs/integration-contracts/communications.md#live-telegram-navigation-and-storage).

## Setup And Workflow Entry Points

- Attach a reusable Account, then configure project identity and policies through
  `communicationProfile.*`. Credentials remain daemon-held; profiles bind safe
  Account refs and own project-specific behavior.
- If the shared Telegram application ID/hash needs correction, detach and revoke
  every Telegram Account. Revoking the final Account resets the application
  setting, so the next Account setup can accept a corrected pair.
- A project-attached agent explicitly controls a Telegram Account's shared TDLib
  connection with `account.session.connect` and `account.session.disconnect`.
  Connect records the durable desired-connected state, which permits daemon
  restart restoration; disconnect clears it. These calls do not attach an
  Account or begin a user sign-in challenge. A user Account that still needs
  sign-in returns safe repair guidance for the local Accounts authorization UI.
  Completing the one-time phone, code, password, or QR sign-in saves the user
  authorization but leaves TDLib disconnected; attach the Account and explicitly
  connect it when work needs it. Disconnect preserves that sign-in, so later
  connects normally do not need another challenge unless Telegram invalidates it.
  Telegram, API, token, and proxy edits require explicit disconnect, save, then
  connect; only a display-name edit is allowed while the Account is connected.
- Use `communicationSurface.*`, `communicationContact.*`, and
  `communicationMembership.*` for people and surfaces; `communicationTarget.*`
  and `communicationRoute.*` for destinations and sharing policy.
- Configure public ingress through `ingressEndpoint.*` for providers that use
  HTTP ingress; use `localAgentChat.createMessage` for local chat. A Telegram
  Account's managed TDLib session receives its native updates. Neither runs a
  model.
- Deliver normal messages through `communication.send`/`communication.reply`.
  Explicit provider actions remain available for provider-specific work and
  workflow-granted execution.

The [resource model](../../docs/integration-contracts/communications.md#resource-model)
and [operation contracts](../../docs/integration-contracts/communications.md#communication-platform-operations)
own field and setup details. The generic core
[agent-request queue](../../docs/integration-contracts/communications.md#core-agent-request-queue)
owns claim/release/completion and caller-supplied run-plan handoff;
communications does not choose a workflow or execute the resulting plan.

Built-in [templates](workflows/) cover inbox review, rich Telegram replies,
callback follow-up, and outbound notifications. Concrete action payloads belong
in run plans.

For finance receipt intake, use the existing
[IMAP host handoff](../finance/references/imap-host-handoff-contract.md):
verified TLS, non-acknowledging export, host-only staging, external persistence
and reread, then separately granted acknowledgement and transfer cleanup.
Original evidence belongs to the external finance workspace, not communication
resources or action audit.

## Business Flow Model

Agents should treat communication surfaces as business context. A surface says
who is present, why the channel exists, what data may appear there, and which
external customer/account/ticket refs are safe to use. A target says where a
message can be sent. A route says what can move between the source surface and
target. StackOS stores and validates that setup; the agent still decides the
workflow, reads bounded context, and uses `communication.send` or
`communication.reply` for normal delivery. Direct provider actions are reserved
for explicit diagnostics or provider-specific escape hatches.

Common examples:

- Customer Telegram support group -> internal Slack support target.
- Internal Slack roadmap channel -> operator DM target.
- Customer issue email -> internal investigation channel, with raw attachments
  requiring approval before forwarding.

## Architecture Boundary

Communications is an input/output and trigger layer. Agents decide what a
message or button click means, create run plans, select actions, and write
replies. StackOS stores provider state, resolves credentials daemon-side,
validates explicit payloads, executes configured calls, and records audit.

The architecture is one shared communication processor after provider-specific
auth/normalization. Slack HTTP ingress and Telegram TDLib updates use the shared inbound
processor for static policy evaluation, resource storage, stable request
dedupe, and agent-request creation, including button/callback click-state
patches. The policy model separates visibility from activation: channels and DMs
can be observed as context, while only approved users may create work or trigger
responses. New channels should normalize events into provider-neutral refs and
reuse shared communication profile, target, route, context, and agent-request
infrastructure.

Telegram callback data uses opaque non-secret values only. Store the meaningful
state in `communication-interaction` resources keyed by communication profile, provider
message ref, and callback token, then let the agent read that resource before
deciding whether to respond, create a run plan, or ignore the event. Replies
that are bound to inbound work should carry
`source_agent_request_id` so response policy can enforce the originating Telegram
profile, chat, thread, and source message.
