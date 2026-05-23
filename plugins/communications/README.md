# Communications Plugin

The communications plugin is the StackOS package for Telegram bot messaging,
rich chat interactions, SMTP email send, IMAP mailbox/message lifecycle, and
communication-driven agent requests.

The plugin is implemented in slices. Generic agent request operations are
executable in core StackOS. Telegram bot identity checks, text sends, photo
sends, callback answers, bounded diagnostic `updates.poll`, and webhook
set/delete/info are executable through the generic action registry. Telegram
secret-token ingress resolves project-scoped bot profiles, stores
callback/message events as resources, and creates generic agent requests only
when trigger/access policy allows it. SMTP send and IMAP mailbox/message
lifecycle actions are executable through daemon-side credentials and mocked
contract tests.

## Providers

- `local-agent-chat`: local StackOS conversation surface for direct human-to-agent
  messages, rich response blocks, and button/image/file interactions.
- `telegram-bot`: bot token auth for identity checks, text/photo sends, inline
  button callback answers, local-webhook setup through Telegram Bot API, and
  bounded diagnostics.
- `smtp`: password/app-password SMTP send. SMTP acceptance is not delivery or
  read confirmation.
- `imap`: password/app-password mailbox listing, search, fetch, and `Seen` flag
  lifecycle using UIDs.

## Resources

- `communication-bot-profile`
- `communication-channel`
- `communication-thread`
- `communication-message`
- `communication-interaction`
- `communication-event`
- `communication-cursor`
- `agent-request-source`

The generic `agent_requests` queue belongs to core StackOS, not this plugin.
Its `agentRequest.*` operations are executable through REST, CLI, and MCP.
Communications can feed it only through trusted daemon ingestion or a run-plan
step that explicitly grants `agentRequest.create`.
`agentRequest.prepareRunPlan` is the generic handoff from an inbound request to
a caller-supplied run plan; it claims, creates, links, and returns the claim
token without choosing strategy or executing tools.

Telegram bot profiles are project scoped. Each profile binds to one credential
profile through `auth_profile_key`; there are no global Telegram credentials or
agent-visible bot tokens. Credentials store token material, webhook secrets, and
safe transport endpoints only; bot profiles store identity, agent guidance,
structured command intents, access policy, trigger policy, context policy,
response policy, and ingress mode. Visibility is
not activation: allowed group messages may be stored as bounded context without
creating an agent request. `local-webhook` is the normal local listener path;
`updates.poll` is diagnostic/bootstrap-only.

SMTP and IMAP are project-scoped typed auth profiles. Agents see safe status and
opaque credential refs; host, username, password, TLS mode, and mailbox mapping
resolve only inside the daemon. SMTP acceptance is recorded as outbound message
submission metadata, not delivery or read state. IMAP uses UID/UIDVALIDITY-based
resources for mailbox cursor, message fetch, and local read/unread lifecycle.

Built-in templates cover inbox review, rich Telegram replies, callback
follow-up, and outbound notifications. They provide context/action structure for
agents; concrete action payloads still belong in run plans.

Project setup uses shared StackOS operations:

- `localAgentChat.createMessage` stores local human/agent chat messages as
  communication resources and can create a generic agent request for inbound
  messages. It does not run a model or decide workflow intent.
- `communicationBotProfile.upsert` creates or updates safe bot identity,
  guidance, and policy after the typed `telegram-bot` credential profile exists.
- `communicationBotProfile.get` and `communicationBotProfile.list` let agents
  inspect profiles without receiving token material.
- REST, CLI `ops call`, MCP, and the local Connections UI all use the same
  operation registry path for this setup.

## Architecture Boundary

Communications is an input/output and trigger layer. Agents decide what a
message or button click means, create run plans, select actions, and write
replies. StackOS stores provider state, resolves credentials daemon-side,
validates explicit payloads, executes configured calls, and records audit.

Telegram inline buttons use opaque `callback_data` only. Store the meaningful
state in `communication-interaction` resources keyed by bot profile, provider
message ref, and callback token, then let the agent read that resource before
deciding whether to respond, create a run plan, or ignore the event. Replies
that are bound to inbound work should carry
`source_agent_request_id` so response policy can enforce the originating bot
profile, chat, thread, and source message.
