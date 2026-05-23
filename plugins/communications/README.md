# Communications Plugin

The communications plugin is the StackOS package for Telegram bot messaging,
rich chat interactions, SMTP email send, IMAP mailbox status, and
communication-driven agent requests.

The plugin is implemented in slices. Generic agent request operations are
executable in core StackOS. Telegram bot identity checks, text sends, photo
sends, callback answers, and update polling are executable through the generic
action registry. Telegram secret-token ingress can store callback/message events
as resources and generic agent requests. SMTP, IMAP, and Telegram webhook
set/delete/info actions remain planned until their connectors and mocked
contract tests land.

## Providers

- `local-agent-chat`: local StackOS conversation surface for direct human-to-agent
  messages, rich response blocks, and button/image/file interactions.
- `telegram-bot`: bot token auth for identity checks, text/photo sends, inline
  button callback answers, update polling, and future webhook ingestion.
- `smtp`: password/app-password SMTP send. SMTP acceptance is not delivery or
  read confirmation.
- `imap`: password/app-password mailbox listing, search, fetch, and `Seen` flag
  lifecycle using UIDs.

## Resources

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

## Architecture Boundary

Communications is an input/output and trigger layer. Agents decide what a
message or button click means, create run plans, select actions, and write
replies. StackOS stores provider state, resolves credentials daemon-side,
validates explicit payloads, executes configured calls, and records audit.

Telegram inline buttons use opaque `callback_data` only. Store the meaningful
state in `communication-interaction` resources, then let the agent read that
resource before deciding whether to respond, create a run plan, or ignore the
event.
