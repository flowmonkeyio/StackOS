# StackOS Auth Providers

StackOS treats Accounts as reusable daemon-owned infrastructure, not agent
context. A project Connection is an explicit attachment to one Account. Agents
can inspect sanitized provider state, test an Account, and pass opaque
credential references into granted tools. They must never
receive API keys, OAuth tokens, refresh tokens, encrypted payloads, or local
setup secrets.

## Model

The auth-provider layer uses:

- `auth_providers`: provider metadata synced from plugin manifests.
- `credentials`: global Account identity, display name, safe configuration,
  auth method, status, and opaque `credential_ref`.
- `integration_credentials`: encrypted secret backing for an Account. It has no
  project, provider, label, or profile ownership.
- `project_credentials`: explicit project-to-Account attachments. These are
  Connections; they contain no credential material.
- `credential_scopes`: granted scopes for a credential ref.
- `credential_accounts`: provider account metadata safe to show to agents.
- `oauth_states`: local-human OAuth state nonces with expiry, consumption, and
  an optional project attachment origin plus exact return surface.
- `credential_usage_events`: redacted audit trail for tests/revocations/use.
- `credential_refresh_events`: redacted audit trail for OAuth/refresh attempts.

The stable identifier is `credential_ref`, for example `cred_...`. Accounts
have a user-facing `display_name`; names are unique per provider using
case-insensitive normalized comparison. Agents may also see auth method keys,
status, scopes, safe account metadata, and attached project ids. They never
receive credential field values.

## Agent Surface

Normal agents may use:

- `account.list`: list global Accounts and sanitized provider metadata.
- `connection.list`: list Accounts explicitly attached to the current project.
- `account.test`: run a daemon-side health probe and return a sanitized result.
- `toolProfile.resolve`: resolve one attached provider/Account tuple for
  execution without dumping the broader Account catalog into context.

Normal agents may not use:

- `account.start`: starts local setup or OAuth and is a human/admin operation.
- `account.revoke`: removes daemon-held secrets and is a human/admin operation;
  the Account must first be detached from every project.
- `connection.attach` and `connection.detach`: project setup mutations.
- plaintext credential setup routes or local UI admin mutations.

The MCP bridge exposes these through `toolbox.call` in normal agent sessions.
Agents should prefer `toolProfile.resolve` when they already know which
provider/Account they need; `connection.list` is still available for
project-scoped diagnostics.

## Setup Flow

1. The agent inspects required providers through plugin/catalog metadata.
2. The agent calls `toolbox.call` for `toolProfile.resolve`, or for
   `connection.list` when it needs project attachment diagnostics.
3. If setup is missing, the agent points the operator to
   `/projects/{project_id}/connections?provider_key={provider_key}`. The
   operator selects an existing reusable Account or opens the shared Add
   Account panel. The dedicated `/accounts` page manages all Accounts.
4. The provider's plugin must be enabled for a project before an Account can be
   attached or executed there. Global Account creation does not silently enable
   a project plugin.
5. When creating an Account, the operator gives it a display name, chooses the
   provider auth method, and enters the fields required by that method, or
   starts the provider OAuth flow when one is configured.
   A provider with multiple methods requires an explicit choice. A provider
   with one method may select it automatically.
   Local UI setup stores the Account and immediately attempts the same
   provider-neutral credential test. Creation from a project Connection
   attaches the new Account automatically after success. A failed or unavailable
   test remains a repairable Account; it is never reported as verified.
6. An agent may later call `toolbox.call` for `account.test` with the selected
   `credential_ref` when work needs a fresh readiness check.
7. Before any project execution, the daemon verifies the exact Account is
   attached to that project, decrypts the secret inside its process, calls the
   connector, records a redacted usage event, and returns sanitized
   status/metadata.

No step requires an agent prompt, workflow template, or repository file to carry
secret material.

Provider manifests declare `auth_methods`. Each method defines its fields,
which fields are daemon-secret, whether the payload is raw or JSON, and whether
setup is an interactive OAuth-style flow. The Connections UI renders this
schema directly:

Provider manifests also declare safe self-service setup metadata under
`config.setup`. It is not credential material. It may include
`credential_label`, `setup_note`, official `homepage_url`, `signup_url`,
`console_url`, `credential_url`, `billing_url`, `docs_url`, `support_url`,
`fallback_url`, `fallback_reason`, per-field `url_confidence`, and
`verified_at`. If an exact API-key or billing page is not publicly verifiable,
use the closest official homepage/docs/console URL and mark that field
`directional` so agents can say it is the starting point rather than a verified
deep link.

- API-key providers usually have one secret `api_key` field.
- Amazon S3 uses the provider-declared `aws-access-key` method. Access key id,
  secret access key, and optional session token are encrypted; bucket and region
  are safe Account config. The daemon constructs an explicit SDK session without
  the ambient AWS credential chain, and `account.test` performs a `HeadBucket`
  reachability probe. AWS IAM and bucket policy determine access.
- Slack bot providers expose only secret `bot_token` and `signing_secret`
  setup fields. StackOS discovers safe workspace and bot identity metadata with
  Slack `account.test`; communication identity and trigger policy live in project
  resources, not credentials.
- SMTP-style systems can expose host, port, username, password, TLS, and sender
  fields in a single method, with only password/token fields encrypted.
- OAuth providers can expose an interactive method or a daemon-side
  refresh-token/client-credentials method, depending on the provider contract.

Non-secret method fields are persisted only as safe credential config. Secret
method fields are serialized into the encrypted backing payload. The old
untyped secret blob route is not part of the public contract.

## One-Brain Auth-Method Contract

StackOS has one credential lifecycle for OAuth, API keys, private-app tokens,
and other provider-declared methods. A provider method changes how a credential
is acquired, verified, renewed, and placed on a provider request; it does not
create a second storage, readiness, UI, or audit system.

### Choosing A Method

Use this decision order:

1. If StackOS has a reviewed managed OAuth-application contract for the
   provider, use it. StackOS does **not** currently operate a shared managed
   OAuth application for the providers in this guide; that would be a separate
   product, distribution, security-review, support, and tenant-isolation
   decision.
2. Choose a configured bring-your-own OAuth application when the connection
   needs provider consent, user/admin account selection, returned grant
   evidence, renewable access, multi-user distribution, or provider-side
   revocation semantics.
3. Choose a personal API key or private-app token only when the provider
   officially supports it and its single-user/single-account ownership matches
   the intended connection. Prefer the static method for local/private
   automation when its operator burden and provider-enforced permissions are
   acceptable.
4. Use client credentials only when the provider documents a non-user
   service-to-service grant and the provider contract validates its returned
   lifecycle and permission evidence.
5. If no official credential can satisfy the account model, grant evidence,
   and safe transport requirements, publish no alternative. Do not relabel an
   OAuth access token as an API key or weaken local scope checks to make it run.

| Choice | Account/distribution fit | Renewal | Permission evidence | Operator responsibility |
| --- | --- | --- | --- | --- |
| Managed StackOS OAuth app | Future product option for centrally operated multi-tenant distribution | StackOS-managed | Provider OAuth response | Consent and account selection; StackOS would own app operations and review |
| Configured/BYO OAuth app | Project/operator owns the provider application and callback registration | Shared OAuth lifecycle | Provider OAuth response | Create the app, configure fields, consent, and reconnect when authorization is lost |
| Personal/private credential | One user, workspace, portal, or private app with provider-documented static authentication | Usually manual rotation | Trusted read-only probe when available; otherwise provider-enforced or fail-closed | Create, scope, rotate, and remotely revoke the value at the provider |
| No supported alternative | Provider/account model or evidence contract is unsafe or undocumented | Not applicable | Not available | Use the supported OAuth/service method or defer the integration |

The operator flow is:

1. Choose a provider on Accounts, or choose **Create new Account** from a
   project's Connections page.
2. When the provider offers multiple methods, choose one explicit method card.
   StackOS does not preselect the first method or enable Connect/Save before
   this choice.
3. For interactive OAuth, enter the operator-owned provider application's
   fields and continue to provider consent. StackOS does not currently supply a
   shared managed OAuth application.
4. For a static method, create the key or token in the provider's official
   console, then enter it locally. Agents never ask for the value in chat.
5. Give the Account a display name that identifies its purpose or provider
   account. Each Account has one immutable `auth_method_key`.
6. Verify the Account. StackOS reports whether permission evidence is known
   locally, enforced by the provider, or unavailable and therefore blocked.
7. To change methods, create and verify a separate named Account, attach it to
   the required projects, update exact `credential_ref` bindings, detach the old
   Account, and only then revoke it. Editing an Account never converts it in
   place.

The complete ownership and execution path is:

```text
Account method choice
  -> manifest auth_method_key + verification posture
  -> global Account identity + encrypted credential backing
  -> shared account.test + normalized safe evidence
  -> project attachment + CredentialResolver renewal/readiness/exact-Account gate
  -> existing provider action connector transport
  -> provider
```

The daemon flow is:

1. The plugin manifest declares each method once with its stable key, fields,
   payload format, interactivity, and `permission_verification` posture.
2. The auth repository validates the method, encrypts only secret material,
   stores safe config plus the saved method key, and rejects an in-place method
   change before decrypting, merging, or writing credential state.
3. `account.start` invokes the shared OAuth lifecycle only for an interactive
   method. Static methods use the same credential record and test lifecycle but
   do not create OAuth state.
4. `account.test` resolves the saved method and passes only a non-secret probe
   context to the provider wrapper. A wrapper may perform a documented,
   read-only provider probe and return normalized safe account/grant evidence;
   it does not decide readiness or write credential state.
5. The credential resolver owns renewal, exact-Account selection, local grant
   enforcement, and denial before action dispatch. The action connector receives
   the already selected credential and may only apply method-specific request
   transport using the saved method key.
6. Provider responses, tests, actions, and local revocation use the shared
   redaction and audit paths. No layer infers a method from token shape, field
   presence, or a provider-specific UI branch.

`permission_verification` has four reviewed postures:

| Evidence source | Enforcement | Meaning |
| --- | --- | --- |
| `oauth_response` | `local_required` | The shared OAuth exchange/refresh contract records provider-returned grants; missing or unknown required grants block before provider action HTTP. |
| `provider_probe` | `local_required` | A documented read-only probe returns authoritative grants; the core validates and persists them before locally gated actions can run. |
| `unavailable` | `provider_enforced` | StackOS can verify reachability/account identity when a safe probe exists, but does not invent grants; the provider enforces permissions during the action. |
| `unavailable` | `local_required` | No trusted permission evidence exists for a locally gated method, so scoped actions fail closed before provider HTTP. |

A stored connection is not automatically authorized for every action.
`oauth_response/local_required` is verified only after the OAuth lifecycle
records the required grants. `provider_probe/local_required` is
**awaiting-probe** until a successful trusted probe records them.
`unavailable/provider_enforced` is usable with an explicit
**provider-enforced/unverifiable** readiness state, while
`unavailable/local_required` is **fail-closed** for scope-requiring actions.
Tests, status, and UI must preserve these distinctions.

When adding or changing a provider method, update the same contract surfaces
together:

1. Record the official provider auth and permission-evidence sources in the
   provider integration contract.
2. Add the method and one reviewed verification posture to the manifest.
3. Reuse encrypted Account storage, exact Account selection, test/audit, and
   resolver enforcement. Do not add a provider-owned lifecycle.
4. Add a read-only test wrapper only when an official safe endpoint and
   response shape are known. Return normalized safe evidence; never raw secret
   values or arbitrary provider responses.
5. Make action transport branch only on the saved `auth_method_key`; reject
   missing, unknown, or mismatched method/payload combinations before HTTP.
6. Let the shared Add Account panel render the method on both Accounts and
   Connections. Provider-specific UI selection or readiness rules are not
   allowed.
7. Test method isolation, redaction, evidence handling, resolver behavior, and
   provider transport, then link the provider contract back to this section.

### Rotation, Repair, And Revocation

- Editing an Account may rotate secret material only within its saved method.
  Leaving an existing secret field blank preserves it. Supplying replacement
  material clears stale grant evidence when the method requires local grants,
  and the Account must be tested or reauthorized again.
- OAuth expiry uses the shared renewal path when the provider contract supports
  refresh/acquisition. Terminal authorization failure moves the connection to
  repair-required; reconnect uses the same Account and method. A transient test
  failure is diagnostic and does not silently disable an otherwise stored
  credential.
- Static credentials do not enter OAuth renewal. A provider rejection means
  the operator must correct permissions or rotate the value at the provider,
  update the same-method Account, and test again.
- `account.revoke` is local cleanup: it removes daemon-held material and prevents
  future StackOS use. It does not claim to invalidate the credential at the
  provider. Remote revocation/rotation remains operator-owned unless a
  separately reviewed provider action explicitly implements it. After recording
  the redacted revoke audit, StackOS wipes the encrypted backing and retains a
  hidden, non-executable Account tombstone for historical audit joins. The
  display name can be reused by a new Account.
- Changing methods is migration, not rotation. Create a second Account, verify
  it, deliberately rebind consumers to its exact `credential_ref`, then revoke
  the old Account and remotely invalidate its provider credential when
  required.

## OAuth Providers

OAuth uses one daemon-owned lifecycle plus a small trusted contract for each
provider. The shared lifecycle owns transaction state, callback handling, token
storage, refresh/acquisition, scope enforcement, concurrency, audit, and safe
failure behavior. A provider contract supplies only protocol facts such as its
authorization/token endpoints, consent scopes, client-auth style, PKCE mode,
fixed authorization parameters, trusted response metadata, token-response
lifecycle requirements for each grant stage, and an exceptional post-exchange
hook when the provider actually requires one. The shared lifecycle validates
those requirements before changing credential state. Connectors receive an
already-usable credential; they do not implement OAuth.

### Operator OAuth Quick Guide

StackOS currently uses bring-your-own OAuth applications. For each interactive
provider, an operator first creates or selects an application in that provider's
developer console and registers StackOS's exact callback URI.

The provider-by-provider console walkthrough, scope ledger, and static
Cloudflare Pages callback design live in
[`oauth-provider-setup.md`](./oauth-provider-setup.md). Its upload-ready source
exists under `workers/oauth-callback-relay/public` and is live at the configured
Pages custom domain. The deployed `index.html` matched the reviewed source
byte-for-byte on 2026-07-22. The page uses browser navigation to reach this same
existing callback route on loopback; it does not add a Worker, Pages Function,
separate completion route, or OAuth lifecycle. The current callback behavior
below remains the runtime truth.

The default local callback URI is:

```text
http://127.0.0.1:5180/api/v1/auth/oauth/callback
```

Providers that require a public HTTPS callback can use the operator-controlled
Pages origin configured with `STACKOS_OAUTH_CALLBACK_BASE_URL`:

```text
STACKOS_OAUTH_CALLBACK_BASE_URL=https://auth.stackos.flowmonkey.io
https://auth.stackos.flowmonkey.io/api/v1/auth/oauth/callback
```

That HTTPS page must navigate the callback to the fixed loopback StackOS route.
The callback origin and local target are application-owned; a user, agent, or
provider cannot select an arbitrary return path.

The operator then completes this flow:

1. Open `/accounts` and choose **Add Account**, or open
   `/projects/{project_id}/connections` and choose **Create new Account**.
2. Select the provider and interactive OAuth method, enter the provider
   application's client fields, and choose **Connect**.
3. StackOS encrypts those fields, creates a short-lived one-use transaction,
   and sends the browser to the provider's authorization page.
4. The user signs in and approves the requested permissions at the provider.
5. The provider returns the browser to the fixed StackOS callback. StackOS
   validates the transaction, exchanges the code, encrypts the tokens, and
   records provider-returned scopes and safe account metadata.
6. StackOS returns the browser to the committed local UI at:

   ```text
   http://127.0.0.1:5180/accounts?oauth_status={status}&provider_key={provider_key}
   ```

   When OAuth began from a project Connection, StackOS instead returns to that
   exact project's Connections page and attaches the Account after success.
   `{status}` is only `connected`, `denied`, `expired`, `repair-required`, or
   `error`. The UI displays the result and immediately removes those query
   fields. Provider codes, state, tokens, secrets, and error descriptions are
   never placed in this URL.

The canonical post-callback UI is the daemon-served UI on port `5180`, including
when development uses the Vite UI on port `5173`. This local return assumes the
OAuth flow was started in a browser on the same machine as StackOS.

The authorization-code flow is:

1. The local operator stores the provider application's required fields through
   the shared Add Account panel. Secret fields remain encrypted; safe
   account/tenant fields stay in Account config.
2. `account.start` accepts the provider, auth method, and opaque `credential_ref`.
   It never accepts a caller-selected callback URL. StackOS uses the fixed
   `/api/v1/auth/oauth/callback` route at the configured callback origin.
3. StackOS creates a short-lived transaction bound to the Account, provider,
   auth method, exact return surface, and optional project attachment origin.
   Only a digest of the random state is stored. Any PKCE verifier and pending
   application values remain encrypted. Starting again consumes earlier
   uncompleted transactions for that Account.
4. The exact public callback route consumes the state atomically, handles denial
   or expiry, exchanges the code once, applies any provider hook, and validates
   the provider contract's account, renewal, expiry, and scope evidence before
   storing the normalized token payload. An incomplete first connection becomes
   `repair-required`; a failed reconnect preserves a still-active prior
   credential.
5. Before an action runs, the resolver refreshes an expired authorization-code
   token or acquires a client-credentials token when needed. The same
   provider-declared response requirements are validated before rotated token,
   expiry, or scope state is committed. A per-credential async lock plus an
   `updated_at` compare-and-swap prevents concurrent requests from overwriting
   newer token material. Generic Account editing exposes only provider-declared
   setup fields; unchanged setup secrets retain the acquired token and grants,
   while changed token/application material resets them.
6. The resolver compares the action manifest's `required_scopes` with the
   credential's known grants before connector dispatch. Missing or unknown
   required scopes fail safely without calling the provider action.

Callback, refresh, and acquisition failures are redacted in persisted audit and
returned diagnostics. The callback immediately redirects with HTTP 303 to the
server-stored Accounts or exact project Connections return surface using only
`connected`, `denied`, `expired`, `repair-required`, or `error`; provider codes,
state, token values, and provider error descriptions are never forwarded into
the UI URL. A timeout, network
failure, rate limit, or provider 5xx during renewal leaves the stored credential
retryable; only a terminal authorization failure such as `invalid_grant` moves
it to `repair-required`.

Provider-declared manual OAuth-token methods use the same lifecycle. A manual Account
with a refresh token uses the same core renewal path when the provider contract
supports it. Replacing manual token material clears any grants recorded for the
old token. Provider-returned grants from a later exchange restore known scope
state; StackOS does not invent grants when that response omits them. An
access-token-only profile can be tested and can run actions with no declared
scope requirement, but a scope-gated action fails closed until grants are known.
Use the interactive method for the normal scope-gated path. This compatibility
surface is not a second OAuth implementation.

### Built-In OAuth Provider Matrix

“Interactive” means StackOS implements start, fixed callback, exchange, and
renewal for that provider. “Core client credentials” means the operator stores
the application fields and StackOS acquires/renews the access token before
dispatch. “Manual compatible” is an intentionally retained token-import method,
not a claim that the operator must manage tokens for the interactive method or
that an imported token bypasses the known-scope gate.

| Provider | Current StackOS auth path | Provider-specific contract notes | Action scope gate |
| --- | --- | --- | --- |
| Google Ads | Interactive authorization code; manual refresh-token compatible | Shared Google endpoints; PKCE supported; offline consent | `adwords` |
| Google Workspace | Interactive authorization code; manual token compatible | Shared Google endpoints; PKCE supported; offline consent | Gmail send and Calendar events |
| Google Search Console | Interactive authorization code; manual access/refresh-token compatible | Shared Google endpoints; PKCE supported; offline consent | `webmasters.readonly` |
| Google Analytics | Interactive authorization code; manual access/refresh-token compatible | Shared Google endpoints; PKCE supported; offline consent | `analytics.readonly` |
| Google Tag Manager | Interactive authorization code; manual access/refresh-token compatible | Shared Google endpoints; PKCE supported; offline consent | `tagmanager.readonly` |
| Meta Ads | Interactive authorization code; existing user/system-user token compatible | Meta login exchange plus the required short-to-long-lived token exchange; imported-token permission evidence is unavailable | Interactive OAuth uses returned grants; imported tokens fail closed for scoped actions |
| Microsoft 365 | Interactive authorization code; manual token compatible | Tenant-validated endpoints; PKCE required | Graph `Mail.Send` / `Calendars.ReadWrite` |
| Outreach | Interactive authorization code; manual token compatible | Provider endpoints and consent scope | `sequenceStates.write` |
| Pipedrive | Interactive authorization code; manual OAuth token; personal API-token alternative | HTTP Basic token exchange; trusted `api_domain`; read-only current-user probe | OAuth uses returned grants; manual OAuth fails closed; API token is provider-enforced |
| Salesforce | Interactive authorization code; manual token compatible | Production, sandbox, or validated My Domain; PKCE required; trusted `instance_url` | `api` |
| Salesloft | Interactive authorization code; manual OAuth token; customer API-key alternative | Provider endpoints; body client authentication; read-only `/v2/me` probe | OAuth uses returned grants; manual OAuth fails closed; API key is provider-enforced |
| Taboola | Core client credentials | Body client authentication | No action-level scope declaration currently required |
| Reddit | Core client credentials | HTTP Basic token acquisition; `user_agent` stays in the encrypted application payload | No action-level scope declaration currently required |
| HubSpot | Interactive authorization code with capability-scoped optional consent; single-account private-app token | OAuth response owns OAuth grants; the documented private-app token-information probe returns authoritative scopes and account evidence | Both methods use the same per-action CRM Core, Sales, Marketing, Bulk, Automation, and Transactional local scope gates |
| Linear | Interactive authorization code; personal API-key alternative | OAuth requires PKCE `S256`, fixed `actor=user`, renewal evidence, and returned `read,write`; both methods use the same safe viewer/organization probe | OAuth is locally gated by returned grants; personal-key permissions are provider-enforced |
| X API | Manual OAuth token only | Provider actions are explicitly deferred | Not executable |
| LinkedIn | Manual OAuth token only | Provider actions are explicitly deferred | Not executable |

Do not add a provider subclass merely to repeat the generic flow. Add a trusted
contract row for protocol data, and add dedicated code only for a real variant
such as Meta's second exchange or trusted provider-specific response metadata.

Linear personal keys use the same Account and action catalog as OAuth but
remain a separate Account with `auth_method_key=personal_api_key`. The saved
method selects raw `Authorization: <key>` transport; StackOS does not infer it
from payload shape. Linear enforces personal-key permissions at action time,
while the shared read-only Test records only safe viewer/workspace identity.

Local disconnect uses the existing `account.revoke` path. Linear's documented
OAuth revoke endpoint remains unsupported/deferred in this delivery, and local
revocation of any static credential removes daemon-held material without
invalidating the value at the provider. An operator who needs immediate remote
invalidation must revoke or rotate it in Linear before local cleanup.

## Accounts And Connections UI Contract

The dedicated `/accounts` page owns Account lifecycle:

- primary action: **Add Account**
- main list: reusable Accounts grouped by provider, including display name,
  safe provider metadata, status, last test, expiry, attached projects, and
  opaque `credential_ref`; revoked history is excluded
- account actions: edit, test, and revoke; revoke is unavailable while any
  project attachment remains
- method choice: a one-method provider may select automatically; a
  multi-method provider renders accessible method cards and requires an
  explicit choice before credential fields or submit actions become active
- edit action: pin the saved provider auth method and reuse the shared Account
  form; prefill safe fields, leave secrets blank, and preserve an existing
  secret unless the operator supplies a replacement; changing methods requires
  a separate named Account
- interactive setup: one **Connect** action validates and stores application
  fields, calls `account.start`, and navigates to the returned HTTPS
  authorization URL
- callback result: a callout renders `connected`, `denied`, `expired`,
  `repair-required`, or `error`, and the UI clears callback query parameters
  immediately after reading them
- diagnostics: `account.test` returns a sanitized result and records the same
  redacted outcome in the credential usage audit; a failed test does not disable
  the stored Account
- communication usage: the page joins the communications-owned
  `communicationProfile.accountUsage` read model so a Slack manual-update
  warning appears only under the exact Account/profile that owns that route;
  an unattached Account does not create project attention

The project `/projects/{project_id}/connections` page only lists attached
Accounts, attaches an existing Account, detaches an unused Account, and opens
the same Add Account panel when a new one is needed. A newly created Account is
attached and selected automatically. Slack and Telegram communication profiles,
webhook routes, and ingress endpoints remain on this project surface.
Because each Slack app and Telegram bot has one provider-owned inbound endpoint,
one Account may have only one inbound-enabled communication profile. New and
legacy profiles default to inbound enabled. Set the Slack or Telegram facet's
`ingress_enabled` to `false` when reusing the Account in another project for
outbound messages only; outbound-only profiles are omitted from ingress routes
and provider webhook sync. A second inbound profile must use a different Account
or explicitly release the first profile's inbound ownership.

`GET /api/v1/auth/accounts` is the global Accounts read model.
`GET /api/v1/projects/{project_id}/connections/accounts` is the project
Connections read model.

Built-in placeholder providers for project-local custom tools, such as
`custom-media-tool` and `custom-gtm-tool`, are not normal service credentials.
They stay hidden from the add-connection picker until a project-local plugin
declares a concrete HTTP connector, allowlisted endpoint, auth injection fields,
timeout policy, and response contract.

## Known Architecture Follow-Ups

- Provider identity is still mostly bare `provider_key`. User-installed plugins
  can collide unless auth routes, credential storage, and action manifests move
  to a stable provider ref such as `plugin_slug.provider_key` or
  `auth_provider_id`.
- Safe Account/scopes metadata still needs richer provider-specific population
  from setup fields and provider test evidence.
- Template `auth_ref` is a local requirement label. Execution should document
  or model the binding from template auth requirement to selected
  `credential_ref`, for example `auth_bindings`.
