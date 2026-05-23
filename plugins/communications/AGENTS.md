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
- Agents never receive bot tokens, SMTP passwords, IMAP passwords, webhook
  secrets, OAuth tokens, refresh tokens, or raw authorization headers.
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

The manifest is catalog metadata only. The actions are intentionally deferred
until provider connectors, mocked provider tests, redaction tests, and run-plan
grant coverage are delivered.

## Implementation Checklist

- Update this plugin manifest and the integration contract together.
- Add connector comments linking official provider docs beside each provider
  call.
- Add mocked provider tests before marking any action executable.
- Prove no-secret output for auth status, auth tests, action calls, resources,
  artifacts, and UI-visible metadata.
- Keep workflow templates generic. Templates may describe setup, context,
  approvals, and expected outputs; concrete action payloads belong in run plans.
