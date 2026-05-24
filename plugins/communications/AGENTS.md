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

- Provider operations are plugin actions executed through `action.run` for one
  explicit direct call or `action.execute` inside a granted run-plan step.
- Do not add provider-specific MCP tools for Telegram, Slack, SMTP, or IMAP.
- Keep Telegram, Slack, SMTP, and IMAP connectors in separate provider files.
- Treat communications as a provider-neutral graph. Use
  `communicationProfile.*`, `communicationSurface.*`,
  `communicationContact.*`, `communicationMembership.*`,
  `communicationTarget.*`, `communicationRoute.*`, and
  `communicationContext.query` for generic setup and stored-context reads.
  Provider-specific actions still execute through explicit action refs.
- Set surface intent before using a channel for real work. A
  `communication-channel` should describe its `audience`, `intent`,
  `agent_guidance`, `data_scope`, and safe `external_context` when it can
  contain internal, customer, partner, vendor, public, or mixed data. Do not put
  credentials, tokens, private headers, or raw provider secrets in those fields.
- Treat surface intent and data scope as agent guidance, not daemon business
  logic. The agent still decides the workflow and must use target/route policy
  plus explicit action validation before sending or forwarding anything.
- `communicationTarget.resolve` is not a send abstraction. It returns static
  allow/deny state plus the explicit provider action ref/defaults an agent may
  validate and execute.
- `communicationContext.query` returns stored StackOS history only. Live Slack
  history, Telegram updates, IMAP fetches, or future Gmail/Graph reads must be
  separate provider actions with scopes, pagination, rate-limit handling, and
  audit.
- New send/handoff routes are default-deny until a communication target/route
  and policy explicitly allow them. Do not infer cross-channel permission from
  a same-origin reply policy.
- Local agent chat is a communication transport, not a model runner hidden in
  the daemon. Store messages/interactions, create generic agent requests, and
  let the selected agent runner decide the response.
- Use `localAgentChat.createMessage` for local chat ingress. It stores
  communication resources and optionally creates a generic agent request; it
  must not invoke a model or select a workflow.
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
- `webhook` is the normal Telegram listener path. Local StackOS uses a public
  ingress endpoint, usually discovered from `driver=local-tunnel`; production
  uses a deployed HTTPS URL. `updates.poll` is bounded diagnostic/bootstrap
  access only, never a background listener.
- Use `ingressEndpoint.configure`, `ingressEndpoint.refresh`,
  `ingressEndpoint.routes`, `ingressEndpoint.sync`, and
  `ingressEndpoint.status` for project-level public ingress setup. The endpoint
  is generic; local tunnel provider settings belong only under `driver_config`.
- Telegram webhook set/delete/info actions are executable through
  `action.execute`; they must resolve the bot profile and daemon-held credential
  server-side.
- Visibility is not activation. A bot profile may observe/store messages from
  any reachable chat/channel as context, but StackOS creates an `agent_request`
  or sends a reply only when trigger policy matches and invoker access policy
  allows that user.
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
- Slack Web API identity, message send, conversation discovery, membership sync,
  and signed HTTP Events API/Interactivity ingress are executable through the
  `slack-bot` connector and `/api/v1/ingress/slack/{project_id}/{profile_key}`.
- When Slack actions include `profile_ref`, the connector must resolve the
  `communication-profile` server-side and reject mismatches between
  `provider_facets.slack-bot.auth_profile_key` and the daemon-resolved
  credential profile.
- Slack Socket Mode remains deferred until a daemon runner owns app-token
  connection lifecycle, reconnects, and envelope ACKs.
- Slack Block Kit buttons must use opaque non-secret values only. Store button
  state as `communication-interaction` resources keyed by communication profile,
  message ref, block id, action id, and value.
- Slack `response_url`, `trigger_id`, bearer tokens, and signing secrets must
  never be persisted or returned to agents.
- SMTP acceptance is not delivery, inbox placement, read, open, click, or reply.
- IMAP message operations must use UIDs and UIDVALIDITY; do not model
  sequence-number-only actions.
- OAuth/XOAUTH2 for SMTP or IMAP stays deferred until provider-specific refresh,
  scope diagnostics, and safe auth tests exist.
- Message bodies may contain private data. Store long or raw bodies as artifacts
  and return previews/selected fields unless a granted run needs full content.
- `agent_requests` are generic core queue records. Communications can create
  them only through trusted ingestion or granted run-plan steps.
- All communication ingress must follow one-brain processing. Provider adapters
  verify transport auth and normalize payloads; shared communication policy is
  responsible for visibility, trigger matching, user allowlists, resource
  storage, and agent-request creation. Do not add a provider-specific second
  brain for when a bot should answer.
- Slack and Telegram still contain transitional duplicated policy/storage code.
  When extending communications, move behavior toward a shared processor instead
  of copying those provider-specific paths into another channel.

## Adding A Communication Provider

For Slack-like, Telegram-like, email, or future chat providers:

1. Add typed auth setup and safe auth tests; never expose credential payloads.
2. Add provider facets to `communication-profile` instead of a provider-specific
   global setup path.
3. Store channels, DMs, mailboxes, and rooms as `communicationSurface.*` records
   with audience, intent, data scope, and safe external context.
4. Normalize inbound provider payloads into the shared communication processor;
   provider adapters should stop after transport verification and field mapping.
5. Expose sends, live history reads, discovery, and membership sync as explicit
   plugin actions with manifest entries, mocked connector tests, pagination,
   rate-limit handling, and audit.
6. Use named `communicationTarget.*` records for outbound destinations and
   `communicationRoute.*` for cross-surface handoff guidance.
7. Do not add provider-specific MCP tools or daemon-side workflow decisions.

## Current Status

Telegram bot identity checks, text messages, photo sends, callback answers,
bounded diagnostic `updates.poll`, webhook set/delete/info, and
bot-profile-scoped secret-token ingress are executable through `action.run` for
one explicit direct call or `action.execute` inside granted run-plan steps, via
the `telegram-bot` connector. Bot-profile setup is executable through
`communicationBotProfile.*` across REST, CLI, and MCP. Webhook ingress stores
communication resources and creates generic agent requests only after
bot-profile trigger and access policy allow it.

Provider-neutral setup operations for profiles, surfaces, memberships, targets,
and stored communication context are executable through REST, CLI, and MCP. They
write static resources and do not call Telegram, Slack, SMTP, IMAP, or a model.

SMTP send and IMAP mailbox/message lifecycle actions are executable through
`action.run` or `action.execute` with daemon-held credentials. SMTP covers
explicit outbound message send only and never claims delivery/read/open/click/
reply state. IMAP covers mailbox list, bounded UID search, selected message
fetch, and mark seen/unseen. Slack Web API actions and signed HTTP ingress are
executable. Automatic background callback ACK jobs, Slack Socket Mode, Slack
history/files/reactions/admin actions, richer Telegram media/admin operations,
broader chat/mail providers, and SMTP/IMAP OAuth or XOAUTH2 remain deferred
until their provider-specific contracts, tests, and safe auth diagnostics are
delivered.

The core `agentRequest.*` operations are executable through the shared
operation registry. Use `agentRequest.list`, `agentRequest.get`,
`agentRequest.claim`, `agentRequest.prepareRunPlan`, `agentRequest.release`,
`agentRequest.linkRunPlan`, `agentRequest.complete`, and
`agentRequest.ignore` for queue lifecycle.
`agentRequest.prepareRunPlan` atomically claims a request, creates the
caller-supplied run plan or template-backed plan, links both, and returns the
claim token. It does not choose a template, start a plan, call a model, call a
provider, or send a reply.
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
