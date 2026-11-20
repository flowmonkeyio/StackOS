# QuickBooks Online host integration

The Finance plugin exposes two read actions through the canonical connector package:

| StackOS action | Contract |
| --- | --- |
| `finance.quickbooks-online.company-info.get` | Empty input; CompanyInfo for the configured company |
| `finance.quickbooks-online.invoices.list` | Inclusive `txn_date_from` / `txn_date_to`, explicit 1-based `start_position`, and `max_results` from 1–100 |

Provider transport, validation, schemas and protocol notes live in the package's
`connectors/quickbooks_online/catalog.json`, `docs/quickbooks-online.md` and
`docs/auth.md`. StackOS retains Account custody, project attachment, action
grants, response files and audit. These actions do not post books, create
invoices, settle payments or choose accounting treatment.

## Accounts and company binding

Enable Finance, then add a QuickBooks Online Account in the generic Accounts or
project Connections screen. Both methods require an explicit `sandbox` or
`production` environment and numeric company `realm_id` of 1–32 digits:

- **Connect with QuickBooks** uses an operator-owned Intuit application's client
  ID and secret, the shared OAuth callback, and daemon-managed token renewal.
- **OAuth2 access token** stores an externally managed token. This host setup
  does not accept refresh-token/application fields for that method.

Register the exact configured StackOS callback in the Intuit app. If the public
HTTPS relay is used, its deployed version must forward the bounded `realmId`
field. StackOS freezes the intended realm and environment in encrypted pending
authorization state. The callback requires exactly one matching numeric realm
and unchanged Account configuration before exchanging the code. Invalid state,
expiry, a different company or changed configuration cannot replace the tokens.
A failed reconnect preserves a still-usable Account.

A fresh response with no scope evidence records unknown grants and clears old
grant rows. Returned scopes are recorded exactly; requested accounting consent
is not evidence. The two package actions declare no local scope requirements;
Intuit enforces access. Account Test reuses the native CompanyInfo probe and
does not infer grants. `CompanyInfo.Id` is a company entity identifier;
`realm_id` in output repeats configured request context rather than independent
provider attestation.

## Records, permission and failures

Attach the Account to each project explicitly. Agents receive safe Account refs;
tokens and application secrets remain daemon-held. Direct calls use
`action.run`; workflow calls use an active step granting the exact action through
`action.execute`. MCP and REST responses use the shared response-file envelope.
Successful and failed calls retain the normal action audit and run references.

Invoices carry exact `raw_json` source fragments, preserving decimal precision,
scale, whitespace and escaping. Optional revision/update metadata comes from the
provider. Missing currency stays unknown. The caller owns page traversal,
cross-page duplicates, scan bounds and completeness checks. Offset pages are
current provider observations, not a historical or isolated snapshot. Keep
financial source records within the project's authorized data boundaries.

Host credential masking still applies to returned text. `SyncToken` revision
text is preserved, but other secret-like assignments can be masked. Such a
response is not a byte-exact copy of the original record; use the accounting
backend's source when that distinction matters.

Reads use bounded shared retries; errors expose safe HTTP status/reason fields,
never arbitrary provider bodies or credential values. Monetary budget
enforcement is disabled because API calls are not invoice economics.

## Evidence and limits

Focused synthetic proof covers host registration, both methods, callback company
binding, omitted/returned scopes, attachment, Account Test, direct/granted reads,
files/audit, safe failures and exact invoice source text. Live Intuit consent and
company access require separate operator evidence. Public relay deployment is
an external release step, not implied by a source change.

Official references:

- [OAuth client and realm callback](https://github.com/intuit/oauth-pythonclient/blob/master/intuitlib/client.py)
- [Production discovery](https://developer.intuit.com/.well-known/openid_configuration/)
- [Sandbox discovery](https://developer.intuit.com/.well-known/openid_sandbox_configuration/)
- [CompanyInfo](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/companyinfo)
- [Invoice](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/most-commonly-used/invoice)
- [Query builder](https://github.com/intuit/QuickBooks-V3-PHP-SDK/blob/master/src/QueryFilter/QueryMessage.php)
- [Limits and throttling](https://static.developer.intuit.com/output_html/qbo/docs/learn/limits-and-throttles.html)
