# Communications Plugin Agent Notes

This plugin defines StackOS communication provider contracts and resources. It
does not run an assistant, classify intent, or decide workflows.

## Read First

- [`../../docs/integration-contracts/communications.md`](../../docs/integration-contracts/communications.md)
- [`../../docs/action-executor.md`](../../docs/action-executor.md)
- [`../../docs/auth-providers.md`](../../docs/auth-providers.md)
- [`../../docs/resources-and-artifacts.md`](../../docs/resources-and-artifacts.md)
- [`../../docs/operations.md`](../../docs/operations.md)

## Rules

- Provider operations are plugin actions executed through `action.execute`.
- Do not add provider-specific MCP tools for Telegram, SMTP, or IMAP.
- Keep Telegram, SMTP, and IMAP connectors in separate provider files.
- Local agent chat is a communication transport, not a model runner hidden in
  the daemon. Store messages/interactions, create generic agent requests, and
  let the selected agent runner decide the response.
- Telegram behavior is project-scoped through `communication-bot-profile`
  records. Credentials store token material and transport endpoints only.
- Bot profiles store identity, default agent guidance, and optional structured
  command intents. Commands are not plain strings; each command may carry
  guidance/configuration for the operating agent.
- Each Telegram bot profile binds to one credential profile through
  `auth_profile_key`; do not add global Telegram credentials or fallback token
  lookup.
- Create and update bot profiles through `communicationBotProfile.upsert`.
  Inspect them through `communicationBotProfile.get` and
  `communicationBotProfile.list`. These are setup operations shared by REST,
  CLI, MCP, and UI; do not bypass them with raw resource writes in product code.
- `local-webhook` is the normal Telegram listener path for local StackOS.
  `updates.poll` is bounded diagnostic/bootstrap access only, never a
  background listener.
- Telegram webhook set/delete/info actions are executable through
  `action.execute`; they must resolve the bot profile and daemon-held credential
  server-side.
- Visibility is not activation. A bot profile may observe/store allowed chat
  messages as context, but StackOS creates an `agent_request` only when trigger
  policy matches and invoker access policy allows it.
- Agents never receive bot tokens, SMTP passwords, IMAP passwords, webhook
  secrets, OAuth tokens, refresh tokens, or raw authorization headers.
- Telegram inline buttons must use opaque `callback_data` only. Keep it within
  Telegram's 1-64 byte limit and never place secrets, prompts, credentials, or
  business decisions in it.
- Store button/callback state as `communication-interaction` resources keyed by
  bot profile, provider message ref, and callback token. Treat callback payloads
  as untrusted routing hints until the agent has read the linked project, run,
  resource, and interaction context.
- Outbound replies that are tied to inbound work should include
  `source_agent_request_id` so response policy can enforce the originating bot
  profile, chat, thread, and message.
- `telegram-bot.callback.answer` may clear Telegram's client-side loading state
  with static acknowledgement text. It must not claim a workflow was completed
  unless the responsible agent or granted run actually completed it.
- Telegram `read` and `unread` are StackOS-local attention states only.
- SMTP acceptance is not delivery, inbox placement, read, open, click, or reply.
- IMAP message operations must use UIDs and UIDVALIDITY; do not model
  sequence-number-only actions.
- OAuth/XOAUTH2 for SMTP or IMAP stays deferred until provider-specific refresh,
  scope diagnostics, and safe auth tests exist.
- Message bodies may contain private data. Store long or raw bodies as artifacts
  and return previews/selected fields unless a granted run needs full content.
- `agent_requests` are generic core queue records. Communications can create
  them only through trusted ingestion or granted run-plan steps.

## Current Status

Telegram bot identity checks, text messages, photo sends, callback answers,
bounded diagnostic `updates.poll`, webhook set/delete/info, and
bot-profile-scoped secret-token ingress are executable through `action.execute`
and the `telegram-bot` connector. Bot-profile setup is executable through
`communicationBotProfile.*` across REST, CLI, and MCP. Webhook ingress stores
communication resources and creates generic agent requests only after
bot-profile trigger and access policy allow it. SMTP, IMAP, automatic
background callback ACK jobs, and richer Telegram media/admin operations remain
deferred until their connector, mocked provider tests, redaction tests, and
run-plan grant coverage are delivered.

The core `agentRequest.*` operations are executable through the shared
operation registry. Use `agentRequest.list`, `agentRequest.get`,
`agentRequest.claim`, `agentRequest.release`, `agentRequest.linkRunPlan`,
`agentRequest.complete`, and `agentRequest.ignore` for queue lifecycle.
`agentRequest.create` is not bootstrap granted; it requires a run token whose
active step explicitly grants `agentRequest.create`.

## Implementation Checklist

- Update this plugin manifest and the integration contract together.
- Add connector comments linking official provider docs beside each provider
  call.
- Add mocked provider tests before marking any action executable.
- Prove no-secret output for auth status, auth tests, action calls, resources,
  artifacts, and UI-visible metadata.
- Keep workflow templates generic. Templates may describe setup, context,
  approvals, and expected outputs; concrete action payloads belong in run plans.
