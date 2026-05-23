# Communications Plugin

The communications plugin is the planned StackOS package for Telegram bot
messaging, SMTP email send, IMAP mailbox status, and communication-driven agent
requests.

The plugin is currently a contract surface only. Its actions are intentionally
deferred until provider connectors and tests are implemented.

## Providers

- `telegram-bot`: bot token auth for identity checks, message send, update
  polling, and future webhook ingestion.
- `smtp`: password/app-password SMTP send. SMTP acceptance is not delivery or
  read confirmation.
- `imap`: password/app-password mailbox listing, search, fetch, and `Seen` flag
  lifecycle using UIDs.

## Resources

- `communication-channel`
- `communication-thread`
- `communication-message`
- `communication-event`
- `communication-cursor`
- `agent-request-source`

The generic `agent_requests` queue belongs to core StackOS, not this plugin.
Communications can feed it after the core queue is delivered.

## Architecture Boundary

Communications is an input/output and trigger layer. Agents decide what a
message means, create run plans, select actions, and write replies. StackOS
stores provider state, resolves credentials daemon-side, validates explicit
payloads, executes configured calls, and records audit.
