# Communications Plugin

The communications plugin is the StackOS package for provider-neutral
communication state plus Telegram bot messaging, local chat interactions, SMTP
email send, IMAP mailbox/message lifecycle, and communication-driven agent
requests.

The plugin is implemented in slices. Generic agent request operations are
executable in core StackOS. Telegram bot identity checks, text sends, photo
sends, callback answers, bounded diagnostic `updates.poll`, and webhook
set/delete/info are executable through the generic action registry. Telegram
secret-token ingress resolves project-scoped communication profiles, stores
callback/message events as resources, and creates generic agent requests only
when trigger/access policy allows it. Slack bot identity, message send,
conversation discovery, membership sync, and signed HTTP Events
API/Interactivity ingress are executable through the same action/resource
model. SMTP send and IMAP mailbox/message lifecycle actions are executable
through daemon-side credentials and mocked contract tests. Generic communication
profile/surface/membership/target/context operations are executable setup/read
operations; they do not call providers or models.

## Providers

- `local-agent-chat`: local StackOS conversation surface for direct human-to-agent
  messages, rich response blocks, and button/image/file interactions.
- `telegram-bot`: bot token auth for identity checks, text/photo sends, inline
  button callback answers, webhook setup through Telegram Bot API, and
  bounded diagnostics.
- `slack-bot`: bot token and signing-secret auth for identity checks, text or
  Block Kit sends, conversation discovery, membership sync, and signed HTTP
  Events API/Interactivity ingress. Socket Mode is deferred.
- `smtp`: password/app-password SMTP send. SMTP acceptance is not delivery or
  read confirmation.
- `imap`: password/app-password mailbox listing, bounded UID search, selected
  field fetch, `Seen` flag lifecycle, and bounded staged-evidence export with
  transfer-id-only cleanup for the finance receipt route.

## Resources

- `communication-profile`
- `communication-contact`
- `communication-target`
- `communication-route`
- `communication-membership`
- `communication-profile`
- `communication-channel`
- `communication-thread`
- `communication-message`
- `communication-interaction`
- `communication-event`
- `communication-cursor`
- `ingress-endpoint`
- `agent-request-source`

The generic `agent_requests` queue belongs to core StackOS, not this plugin.
Its `agentRequest.*` operations are executable through REST, CLI, and MCP.
Communications can feed it only through trusted daemon ingestion or a run-plan
step that explicitly grants `agentRequest.create`.
`agentRequest.prepareRunPlan` is the generic handoff from an inbound request to
a caller-supplied run plan; it claims, creates, links, and returns the claim
token without choosing strategy or executing tools.

Telegram Accounts are global and reusable. Each project-scoped communication
profile binds to one explicitly attached Account through `credential_ref`;
there are no agent-visible bot tokens or provider-wide fallback lookups.
Accounts store token material, webhook secrets, and safe transport configuration
only; communication profiles store identity, agent guidance,
structured command intents, access policy, trigger policy, context policy,
response policy, and ingress mode. Visibility is
not activation: visible messages may be stored as bounded context without
creating an agent request; only allowlisted users can trigger work or replies.
`webhook` is the normal listener path. Local development uses the same public
ingress endpoint contract as production, usually with `driver=local-tunnel` and
provider details in `driver_config`. `updates.poll` is diagnostic/bootstrap-only.

Slack Accounts are global and reusable. Each project-scoped communication
profile binds to an explicitly attached `slack-bot` Account through
`provider_facets.slack-bot.credential_ref`. Accounts store Slack token material
and the signing secret only; communication profiles store identity,
agent guidance, access policy, trigger policy, context policy, response policy,
and send/handoff policy. HTTP ingress verifies Slack signatures before storing
events or creating agent requests. `response_url` and `trigger_id` are transient
sensitive values and are not persisted.

SMTP and IMAP use reusable global Accounts with explicit project attachments.
Agents see safe status and opaque credential refs; host, username, password, TLS
mode, and mailbox mapping resolve only inside the daemon. SMTP acceptance is recorded as outbound message
submission metadata, not delivery or read state. IMAP uses UID/UIDVALIDITY-based
resources for mailbox cursor, message fetch, and local read/unread lifecycle.

For an external evidence backend such as the finance local Markdown workspace,
IMAP remains a transport only. The receipt flow uses non-acknowledging UID
`BODY.PEEK[]` export into a bounded connector staging directory under its
daemon-owned asset path. Its safe result includes a project-contained
`staging_uri`, opaque transfer id, source identity, canonical staged paths, and
hash/byte manifest; it has no `host_handoff` field and exposes no raw MIME or
attachment bytes. The `staging_uri` is a host-filesystem-only locator, never an
HTTP URL; the daemon's static mount returns 404 for every normalized
`imap-transfers` path even when a bearer token is supplied, and it rejects a
public-looking symlink or path alias whose resolved target is inside that tree.
The trusted host maps that locator through its own local filesystem runtime,
verifies and writes the original outside StackOS, re-reads the external record,
then separately invokes the granted epoch-qualified `mark_seen` action and
transfer-id-only cleanup. The generic search cursor is observation-only, not
receipt progress. Raw MIME and attachment bytes stay in temporary staging,
never action output, communication resources, artifacts, or action-audit data.
This narrow handoff does not create general filesystem access, storage
infrastructure, or a finance evidence store.
The complete handoff and recovery contract—including the fixed export limits and
transfer-id-only staging cleanup—lives in
[`plugins/finance/references/imap-host-handoff-contract.md`](../finance/references/imap-host-handoff-contract.md).
Finance receipt intake requires a successfully verified SSL or STARTTLS
credential before it searches, exports, or acknowledges; legacy plaintext IMAP
operations remain outside that finance workflow.

Built-in templates cover inbox review, rich Telegram replies, callback
follow-up, and outbound notifications. They provide context/action structure for
agents; concrete action payloads still belong in run plans.

Project setup uses shared StackOS operations:

- `localAgentChat.createMessage` stores local human/agent chat messages as
  communication resources and can create a generic agent request for inbound
  messages. It does not run a model or decide workflow intent. Agent responses
  in the same local thread use `direction=outbound`, the same `thread_key`, a
  new `message_key`, and `create_request=false`.
- `communicationProfile.*` stores provider-neutral identity, guidance, facets,
  and static policy.
- `communicationSurface.*` stores safe channel/DM/mailbox/local-chat surface
  metadata on the `communication-channel` resource, including audience,
  durable intent, per-surface agent guidance, data-scope/share boundaries, and
  safe external customer/account/ticket refs.
- `communicationContact.*` stores safe cross-provider person, customer, team,
  bot, or organization refs.
- `communicationMembership.*` stores provider-neutral membership, permission,
  role, and scope state.
- `communicationTarget.*` stores and resolves named send destinations to
  explicit provider action refs. It does not send messages. Resolve with
  `profile_ref`, `source_surface_ref`, and `invoker_ref` when available so the
  target can enforce both project profile policy and the approved human/bot
  actor that requested the send. If `profile_ref` is omitted, resolve uses the
  same target/default actor selection as `communication.send` and returns
  `policy_profile_ref`.
- `communicationRoute.*` stores static cross-surface handoff policy. It does
  not send messages or choose workflow behavior.
- `communicationContext.query` returns bounded stored communication-message
  history. It never fetches live provider history. Invalid field errors return
  both rejected `fields` and the safe `allowed_fields` set.
- `communicationProfile.upsert` creates or updates safe bot identity,
  guidance, and policy after a reusable `telegram-bot` Account is attached to
  the project.
- `communicationProfile.get` and `communicationProfile.list` let agents
  inspect profiles without receiving token material.
- `ingressEndpoint.*` stores one project-level public ingress endpoint, derives
  provider webhook URLs, and syncs safe route metadata into profiles. Applying
  Telegram webhooks uses `communications.telegram-bot.webhook.set/delete/info`
  through `action.run` or granted `action.execute`.
- REST, CLI `ops call`, MCP, and the local Connections UI all use the same
  operation registry path for this setup.

## Business Flow Model

Agents should treat communication surfaces as business context. A surface says
who is present, why the channel exists, what data may appear there, and which
external customer/account/ticket refs are safe to use. A target says where a
message can be sent. A route says what can move between the source surface and
target. StackOS stores and validates that setup; the agent still decides the
workflow, reads bounded context, and uses `communication.send` or
`communication.reply` for normal delivery. Direct provider actions are reserved
for explicit diagnostics, webhook setup, or provider-specific escape hatches.

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
auth/normalization. Slack and Telegram HTTP ingress now use the shared inbound
processor for static policy evaluation, resource storage, stable request
dedupe, and agent-request creation, including button/callback click-state
patches. The policy model separates visibility from activation: channels and DMs
can be observed as context, while only approved users may create work or trigger
responses. New channels should normalize events into provider-neutral refs and
reuse shared communication profile, target, route, context, and agent-request
infrastructure.

Telegram inline buttons use opaque `callback_data` only. Store the meaningful
state in `communication-interaction` resources keyed by communication profile, provider
message ref, and callback token, then let the agent read that resource before
deciding whether to respond, create a run plan, or ignore the event. Replies
that are bound to inbound work should carry
`source_agent_request_id` so response policy can enforce the originating bot
profile, chat, thread, and source message.
