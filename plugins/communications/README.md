# Communications Plugin

The communications plugin is the StackOS package for provider-neutral
communication state plus Telegram bot messaging, local chat interactions, SMTP
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

## Reading provider content

Stored context comes from `communicationContext.query`; live reads use provider
actions. Slack history defaults to previews and accepts `include_content: true`
for full selected content. IMAP search/fetch expose continuation and completeness
facts; original MIME and attachments use export. Follow the exact
[Slack](../../docs/integration-contracts/communications.md#reading-complete-selected-slack-content)
and [IMAP](../../docs/integration-contracts/communications.md#imap-result-coverage-and-continuation)
output contracts before treating a page or preview as complete.

## Setup And Workflow Entry Points

- Attach a reusable Account, then configure project identity and policies through
  `communicationProfile.*`. Credentials remain daemon-held; profiles bind safe
  Account refs and own project-specific behavior.
- Use `communicationSurface.*`, `communicationContact.*`, and
  `communicationMembership.*` for people and surfaces; `communicationTarget.*`
  and `communicationRoute.*` for destinations and sharing policy.
- Configure public ingress through `ingressEndpoint.*`; use
  `localAgentChat.createMessage` for local chat. Neither runs a model.
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
