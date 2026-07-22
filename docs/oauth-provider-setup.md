# OAuth Provider Registration And Callback Setup

This is the operator runbook for registering the OAuth applications used by
StackOS and for configuring the public callback transport. It complements the
technical lifecycle in [`auth-providers.md`](./auth-providers.md) and the threat
model in [`security.md`](./security.md).

> **Current status:** the OAuth core changes in this repository have not been
> released. The upload-ready static callback page now exists at
> [`workers/oauth-callback-relay/public`](../workers/oauth-callback-relay/public),
> but it has not been uploaded or deployed. It reuses the existing local
> callback route; it does not add a Worker, Pages Function, daemon route, or
> OAuth lifecycle. Do not register the permanent callback with providers until
> the Pages custom domain is live and verified.

HubSpot is intentionally outside this runbook's interactive-OAuth delivery.
Do not paste client secrets, authorization codes, access tokens, refresh tokens,
or API keys into chat, tickets, documentation, source files, or Pages settings.
Provider application secrets belong only in the local StackOS Connections UI.

## Callback Contract

The selected callback origin is:

```text
https://auth.stackos.flowmonkey.io
```

The exact provider-facing callback registered in every interactive provider
console is:

```text
https://auth.stackos.flowmonkey.io/api/v1/auth/oauth/callback
```

The static page navigates the browser to the same callback path on the fixed
loopback origin:

```text
http://127.0.0.1:5180/api/v1/auth/oauth/callback
```

After StackOS completes or rejects the transaction, it returns the browser to:

```text
http://127.0.0.1:5180/projects/{project_id}/connections
```

`STACKOS_OAUTH_CALLBACK_BASE_URL` remains origin-only. After the Pages domain
is active, configure it with the public origin, without a path:

```text
STACKOS_OAUTH_CALLBACK_BASE_URL=https://auth.stackos.flowmonkey.io
```

StackOS derives the provider-facing callback path. A user, agent, provider, or
callback-page request must never select a different callback or local return
target.

### Why the browser reaches the correct machine

```text
StackOS Connect
  -> provider authorization page
  -> public HTTPS static Pages callback
  -> browser location.replace() to 127.0.0.1:5180
  -> the StackOS daemon on the browser's machine
```

Cloudflare does not connect to the user's loopback interface. The static page
performs a top-level browser navigation, so the browser makes the final request
and `127.0.0.1` identifies the machine running that browser.

The initial supported flow assumes that authorization is opened in a browser
on the same machine as StackOS. Cross-device authorization would require a
stateful claim/poll broker and is intentionally not part of the streamlined
design.

The shared relay deliberately targets the canonical StackOS port `5180` and
does not accept a port from the callback request. An installation running the
daemon on a non-default port must use a provider-supported direct loopback
callback or a deliberately configured temporary tunnel instead of this relay.
This limitation avoids turning the public endpoint into a redirector for
arbitrary local services.

### Current and target transports

| Transport | Current support | Permanent product choice |
| --- | --- | --- |
| Direct loopback callback | Implemented for providers that accept it | Retained as a local-development option |
| Named reverse tunnel to port `5180` | Compatible with the current direct callback design | Temporary testing only; one tunnel/hostname maps to one machine |
| Static Cloudflare Pages callback to loopback | Implemented in source, not uploaded | Yes; one public callback works for same-machine installations on canonical port `5180` |
| Stateful hosted OAuth/token broker | Not implemented | No; unnecessary unless cross-device authorization becomes a demonstrated requirement |

## Cloudflare Pages Static Callback

The upload is a static page, not an OAuth server, token broker, reverse proxy,
Worker, Pages Function, or credential store. Cloudflare Pages supports a custom
subdomain whose DNS remains with another provider by associating the domain in
Pages and adding a CNAME to the project's `pages.dev` hostname. See
[Cloudflare Pages custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/),
[serving Pages](https://developers.cloudflare.com/pages/configuration/serving-pages/),
and [custom headers](https://developers.cloudflare.com/pages/configuration/headers/).

### Cloudflare dashboard setup

Do these steps only after the static source is reviewed and the existing local
StackOS callback contract has passed its regression tests:

1. Run `npm test` and `npm run bundle` in
   `workers/oauth-callback-relay`.
2. Open **Workers & Pages** in the Cloudflare dashboard.
3. Select **Create** -> **Pages** -> **Direct Upload** and create a project such
   as `stackos-oauth-callback`.
4. Upload `workers/oauth-callback-relay/dist/stackos-oauth-callback-pages.zip`,
   or upload the contents of `workers/oauth-callback-relay/public`, including
   `_headers`. The upload needs no secret or runtime binding.
5. In the Pages project, open **Custom domains** -> **Set up a domain** and add
   `auth.stackos.flowmonkey.io` before creating the DNS record.
6. In Namecheap **Advanced DNS**, add a `CNAME Record` whose host is `auth` and
   whose value is the exact `<project>.pages.dev` target shown by Cloudflare.
   Keep TTL automatic and do not use a URL Redirect Record.
7. Wait for Cloudflare to report the hostname and certificate as active.
8. Do not enable analytics, browser recording, or log export that persists
   callback URLs or query strings.
9. Confirm HTTPS and the response headers before registering the URL with any
   provider.

Do not use a rotating development URL as the registered callback. Providers
compare redirect URIs exactly, and changing the hostname later requires
updating every application registration.

### Static page request contract

The upload must:

- be served at `GET /api/v1/auth/oauth/callback` through the Pages static
  single-page fallback;
- reject every other origin or path in browser code without navigating to
  loopback;
- require one bounded `state` value and exactly one success or failure outcome;
- forward only the bounded fields StackOS consumes: `state`, `code`, and
  `error`;
- ignore other provider-added query fields while ensuring they cannot affect
  the reconstructed local destination;
- construct the destination from the constant loopback origin, port, and path;
- remove the public query and fragment from browser history, then use
  `location.replace()` for a `GET` to the existing local callback route;
- set `Cache-Control: no-store`, `Pragma: no-cache`,
  `Referrer-Policy: no-referrer`, a script-hash Content Security Policy,
  `X-Content-Type-Options: nosniff`, and frame/indexing restrictions through
  `_headers`;
- avoid `fetch()` to localhost: Cloudflare cannot reach the user's loopback
  interface;
- never accept a destination, hostname, port, path, provider, project, or
  credential reference from the request;
- never persist request data or emit callback query values to application logs.

Static hosting necessarily returns an HTML `200`, not a relay-generated `303`,
and JavaScript is required. Invalid callback input is handled by the page and
does not navigate to loopback. This is the deliberate streamlined tradeoff for
keeping authoritative DNS on Namecheap without a Worker or Enterprise zone.

The authorization code necessarily passes through the public callback and the
browser, as it would through any HTTPS callback or reverse proxy. It is
short-lived and one-use. The provider client secret remains local, and StackOS
uses PKCE where the provider supports or requires it. Do not capture or export
the callback navigation from browser developer tools during live consent.

### Local StackOS contract reused by the relay

The audited implementation does not require a new daemon callback route or a
new middleware exception:

1. `/api/v1/auth/oauth/callback` remains the registered public path and the
   exact local handoff path.
2. StackOS already derives the provider-facing URI from
   `STACKOS_OAUTH_CALLBACK_BASE_URL`, stores that exact URI with the pending
   transaction, and reuses it during token exchange.
3. The existing authentication middleware bypass is already limited to an
   exact `GET /api/v1/auth/oauth/callback` request.
4. The existing Host middleware already permits the same exact callback on a
   loopback Host while retaining the narrow configured public-host exception.
5. The existing callback handler owns state validation, provider binding,
   encrypted pending values, atomic consumption, exchange, reconnect, and the
   sanitized final UI redirect.
6. The static page changes only the browser's transport between the registered
   public URI and that existing local URI. It must not change the
   provider-facing `redirect_uri` used for token exchange.
7. Implementation must prove these existing invariants with regression tests.
   Do not modify the callback route or middleware unless a failing test
   disproves the audited path and the design is reviewed again.

### Verification order

1. Unit-test the static page for exact origin/path, bounds, fixed destination,
   query allowlisting, headers, CSP hash, and open-redirect attempts.
2. Regression-test the existing daemon callback with a mock provider: success,
   denial, expiry, replay, mismatched provider/profile, wrong Host, and
   daemon-down recovery. The test must prove that no new route is required.
3. Verify top-level HTTPS-to-loopback navigation in the supported macOS browser
   path. Chromium and Safari coverage should be explicit.
4. Upload the Pages artifact only with explicit external-write authority.
5. Set `STACKOS_OAUTH_CALLBACK_BASE_URL`, restart the local daemon, and confirm
   the derived callback is exact.
6. Register and test one Google application first.
7. Test Microsoft and Salesforce next because their console requirements are
   stricter.
8. Register the remaining provider applications only after the common callback
   passes.

No StackOS release is required to create provider applications or upload the
static page. The unreleased OAuth core must still be present locally, but no
separate relay-handoff route is part of the Pages design.

## Provider Summary

The table separates user-consent OAuth from application-only client
credentials and from providers with a supported non-OAuth alternative.

| Provider | StackOS flow | Register an application? | Public callback? | Non-OAuth alternative |
| --- | --- | --- | --- | --- |
| Google Ads | Authorization code | Yes | Yes | No; the developer token is additional, not a replacement for OAuth |
| Google Workspace | Authorization code | Yes | Yes | No |
| Google Search Console | Authorization code | Yes | Yes | No |
| Google Analytics 4 | Authorization code | Yes | Yes | No |
| Google Tag Manager | Authorization code | Yes | Yes | No |
| Meta Ads | Authorization code | Yes | Yes | No API-key alternative; manual OAuth token import remains compatible |
| Microsoft 365 | Authorization code | Yes | Yes | No |
| Salesforce | Authorization code | Yes | Yes | No API-key alternative; manual OAuth token import remains compatible |
| Outreach | Authorization code | Yes | Yes | No |
| Pipedrive | Authorization code | Only when choosing OAuth | Yes for OAuth | Personal API token |
| Salesloft | Authorization code | Only when choosing OAuth | Yes for OAuth | Customer API key |
| Taboola | Client credentials | Credentials are issued by Taboola | No | Not applicable |
| Reddit | Client credentials | Approval and a confidential client are required | No StackOS callback | Not applicable |
| HubSpot | Manual token in this delivery | Not for interactive StackOS OAuth | No | Private-app/manual token; interactive OAuth is deferred |
| X API | Manual/deferred | Do not register for this delivery | No implemented callback | Deferred |
| LinkedIn | Manual/deferred | Do not register for this delivery | No implemented callback | Deferred |

## Google: One Application, Five StackOS Connections

One Google Cloud OAuth client can serve Google Ads, Google Workspace, Search
Console, Analytics, and Tag Manager when they share the same operator, consent
audience, and security ownership. Each service remains a separate StackOS
connection and requests only its declared scopes.

Official references:

- [Google OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google OAuth consent and app state](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview)
- [Google Ads developer token](https://developers.google.com/google-ads/api/docs/api-policy/developer-token)

### Create the Google application

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create or
   select one project owned by the correct organization.
2. Open **APIs & Services** -> **Library** and enable:
   - Google Ads API
   - Gmail API
   - Google Calendar API
   - Search Console API
   - Google Analytics Data API
   - Google Analytics Admin API
   - Tag Manager API
3. Open **Google Auth Platform** -> **Branding**. Set the application name,
   support email, developer contact, and any required homepage/privacy links.
4. Open **Audience**:
   - use **Internal** for users in one Google Workspace organization when that
     restriction is correct; or
   - use **External** with **Testing** and add the exact test users.
5. Open **Data Access** and add only the scopes listed below.
6. Open **Clients** -> **Create Client**.
7. Choose **Web application**.
8. Under **Authorized redirect URIs**, enter the exact public Pages callback.
   Do not enter only the origin and do not add a trailing slash.
9. Create the client and record the Client ID and Client Secret locally.
10. Enter that same client pair into each applicable StackOS connection. Never
    place it in the static page or Cloudflare project.

External applications left in Google's **Testing** state generally receive
refresh tokens that expire after seven days for these non-profile scopes. Use
Internal/trusted organization access where appropriate or complete the required
production verification before treating the connection as durable. See
[Google OAuth token expiration](https://developers.google.com/identity/protocols/oauth2).

### Google service scopes

| StackOS provider | Consent scopes | Additional setup |
| --- | --- | --- |
| Google Ads | `https://www.googleapis.com/auth/adwords` | Obtain a developer token from the Google Ads API Center in a manager account |
| Google Workspace | `https://www.googleapis.com/auth/gmail.send`; `https://www.googleapis.com/auth/calendar.events` | `gmail.send` is sensitive; Workspace admin policy may restrict it |
| Google Search Console | `https://www.googleapis.com/auth/webmasters.readonly` | The consenting user must have access to the required properties |
| Google Analytics 4 | `https://www.googleapis.com/auth/analytics.readonly` | The consenting user must have access to the required account/properties |
| Google Tag Manager | `https://www.googleapis.com/auth/tagmanager.readonly` | The consenting user must have access to the required account/containers |

For Google Ads, sign in to a manager account and open
[Google Ads API Center](https://ads.google.com/aw/apicenter). Complete the API
Access form and copy the developer token. StackOS needs the developer token in
addition to the OAuth client ID and secret.

## Meta Ads

Official starting points:

- [Meta app dashboard](https://developers.facebook.com/apps/)
- [Meta Marketing API](https://developers.facebook.com/docs/marketing-api)

1. Open the Meta app dashboard and select **Create App**.
2. Select the business/marketing use case offered by the current wizard and
   attach the correct Business Portfolio.
3. Add or configure **Marketing API** and **Facebook Login for Business**.
4. In Facebook Login for Business settings, enable **Client OAuth Login** and
   **Web OAuth Login**.
5. Add the exact public Pages callback under **Valid OAuth Redirect URIs**.
6. Request/configure:
   - `ads_read`
   - `ads_management`
   - `business_management`
7. While the app is in development, add the authorizing account as an app role,
   developer, or tester and confirm it has access to the target Business and ad
   accounts.
8. Record the App ID and App Secret locally and enter them in **Connect with
   Meta** in StackOS.
9. Keep the app unpublished for private testing. App Review, business
   verification, and production permission approval are separate later gates.

StackOS performs Meta's required long-lived-token exchange after the normal
authorization-code exchange. The static page contains no Meta-specific
behavior.

## Microsoft 365

Official references:

- [Register an application](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app)
- [Add a redirect URI](https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-redirect-uri)
- [Microsoft Graph permissions](https://learn.microsoft.com/en-us/graph/permissions-reference)

1. Open [Microsoft Entra admin center](https://entra.microsoft.com/).
2. Go to **Identity** -> **Applications** -> **App registrations** ->
   **New registration**.
3. Enter an application name.
4. Choose the supported account type. For one organization, prefer
   **Accounts in this organizational directory only**.
5. Set **Redirect URI** platform to **Web** and enter the exact public Pages
   callback.
6. Select **Register**.
7. Open **API permissions** -> **Add a permission** -> **Microsoft Graph** ->
   **Delegated permissions** and add:
   - `Mail.Send`
   - `Calendars.ReadWrite`
   - `offline_access`
8. Do not add application permissions for the current StackOS user-consent
   flow. Remove unrelated defaults when organization policy permits.
9. Select **Grant admin consent** only when tenant policy requires it and an
   authorized administrator approves the requested delegated permissions.
10. Open **Certificates & secrets** -> **Client secrets** -> **New client
    secret**. Copy the secret **Value** immediately; it is shown once.
11. Copy the **Application (client) ID** and **Directory (tenant) ID** from
    **Overview**.
12. In StackOS, enter the client ID, secret value, and tenant ID or verified
    tenant domain. Use `common` only when the app was intentionally registered
    for that audience.

## Salesforce

Starting in Spring '26, new Connected App creation is restricted. Use an
External Client App for new setup.

Official references:

- [Create an External Client App](https://developer.salesforce.com/docs/platform/mobile-sdk/guide/eca-create.html)
- [Configure External Client App OAuth](https://help.salesforce.com/s/articleView?id=sf.configure_external_client_app_oauth_settings.htm&language=en_US)

1. In Salesforce **Setup**, search for **External Client App Manager**.
2. Select **New External Client App**.
3. Fill in the name, API name, contact email, and select **Local** distribution
   for an app used only by this organization.
4. Under **API (Enable OAuth Settings)**, enable OAuth.
5. Enter the exact public Pages callback. Salesforce requires HTTPS or a
   custom application URI; use the HTTPS Pages URL.
6. Select these OAuth scopes:
   - **Manage user data via APIs (`api`)**
   - **Perform requests at any time (`refresh_token`, `offline_access`)**
7. Enable the authorization-code/web-server flow used by the current org UI.
8. Under Security:
   - enable **Require Secret for Web Server Flow**;
   - enable **Require Secret for Refresh Token Flow**;
   - enable **Require Proof Key for Code Exchange (PKCE)**;
   - keep refresh-token rotation enabled when offered.
9. Create/save the application.
10. Open its **Settings** -> **OAuth Settings** -> **Consumer Key and Secret**
    and record the consumer key and secret locally.
11. In StackOS, choose `production`, `sandbox`, or `my-domain`. For My Domain,
    enter the exact `company.my.salesforce.com` hostname.

Salesforce configuration can take time to propagate. Do not weaken callback,
PKCE, or secret requirements to work around an immediate post-creation error.

## Outreach

Official reference: [Outreach API access and OAuth](https://developers.outreach.io/api/oauth)

1. Open the [Outreach developer apps area](https://developers.outreach.io/apps/)
   and create an Outreach application.
2. Open the application's **API access** tab.
3. Add the exact public Pages callback as a redirect URI.
4. Select `sequenceStates.write`.
5. Save the development configuration.
6. Copy the development client ID and client secret immediately. Outreach shows
   a newly generated secret only once.
7. Enter them in **Connect with Outreach** in StackOS.
8. Keep development credentials limited to development/test users. Publishing
   and production review are separate later gates.

## Pipedrive

Pipedrive supports either OAuth or a personal API token in StackOS. Use OAuth
when several users/accounts should authorize the application; use the personal
token only for an explicitly private personal integration.

Official references:

- [Register a private app](https://pipedrive.readme.io/docs/marketplace-registering-a-private-app)
- [OAuth scopes](https://pipedrive.readme.io/docs/marketplace-scopes-and-permissions-explanations)

1. Create or use a Pipedrive developer sandbox account.
2. Open **Developer Hub** -> **Create an app** -> **Private app**.
3. In **Basic info**, enter the app name and exact public Pages callback.
   Pipedrive allows one callback URL per app.
4. Save, then open **OAuth & access scopes**.
5. Select:
   - `deals:read`
   - `search:read`
6. Copy the client ID and client secret and enter them in **Connect with
   Pipedrive** in StackOS.
7. Keep the app in draft while validating the callback. Pipedrive validates the
   callback when changing the app to live, and a live private app cannot be
   reverted to draft.

Pipedrive may also send background install/uninstall lifecycle requests to an
app callback. The streamlined StackOS static page handles only the interactive
OAuth `GET`; it is not a Pipedrive Marketplace lifecycle webhook. Do not publish a
Marketplace app until that separate lifecycle requirement is deliberately
designed and implemented.

## Salesloft

Salesloft supports OAuth and customer API keys. OAuth is the preferred path for
partner applications; a customer API key remains a supported private
alternative in StackOS.

Official references:

- [Salesloft OAuth authorization code](https://developers.salesloft.com/docs/platform/api-basics/oauth-authentication/)
- [Salesloft API keys](https://developers.salesloft.com/docs/platform/api-basics/api-key-authentication/)

1. In Salesloft, go to **Your Applications** -> **OAuth Applications** ->
   **Create New**.
2. Add the app name, description, logo when required, and exact public Pages
   callback.
3. Choose **No** for a private team application unless public distribution is
   intentionally planned.
4. Choose **Authorization Code** as the grant type.
5. Select `cadences:write` for the current StackOS cadence-membership action.
6. Submit the application.
7. Record the Application ID/Client ID and Client Secret locally and enter them
   in **Connect with Salesloft** in StackOS.

For a private customer API key instead, use **Your Applications** -> **API
Keys** -> **Create New**, select only the needed scope, and store the resulting
key through the Salesloft API-key method in Connections.

## Taboola

Taboola uses OAuth 2.0 client credentials, not a browser authorization flow.
There is no callback and no static-page involvement.

Official references:

- [Taboola authentication basics](https://developers.taboola.com/backstage-api/reference/authentication-basics)
- [Taboola client-credentials flow](https://developers.taboola.com/backstage-api/reference/client-credentials-flow)

1. Ask the Taboola Account Manager for Backstage API access, `client_id`, and
   `client_secret`.
2. Record the relevant Taboola account ID as a safe account reference.
3. In StackOS Connections, choose Taboola **Client credentials** and enter the
   client ID, client secret, and safe account reference.
4. StackOS acquires and renews the 12-hour access token inside the daemon.

Do not search for a self-service callback or consent application in Taboola
Realize; the official contract directs operators to their Account Manager for
the client credentials.

## Reddit

Reddit uses application-only client credentials for the current read-only
StackOS research actions. There is no user-consent callback and no static-page
involvement.

Official references:

- [Reddit Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/16471395473812-Moderation-Bots-Tooling)
- [Developer Platform and Data API access](https://support.reddithelp.com/hc/en-us/articles/14945211791892-Developer-Platform-Accessing-Reddit-Data)
- [Reddit API access request](https://support.reddithelp.com/hc/en-us/requests/new?ticket_form_id=20794104097300)

1. Submit the Reddit API access request and obtain explicit approval before
   making Data API calls.
2. Describe the StackOS use narrowly: read-only subreddit search and top-post
   research; no posting, voting, private messages, scraping, re-identification,
   or model training.
3. After approval, follow Reddit's instructions to create a confidential OAuth
   client. Do not choose an installed/public client because the StackOS
   client-credentials flow requires a client secret.
4. If Reddit's client form requires a redirect URI, use the value Reddit
   approves or instructs for that client. StackOS does not consume it for this
   application-only flow.
5. Record the client ID, client secret, and a descriptive User-Agent such as:

   ```text
   macos:stackos-reddit-research:v1.0 (by /u/YOUR_USERNAME)
   ```

6. Enter those values in Reddit **Client credentials** in StackOS Connections.

Reddit approval and use-policy compliance are prerequisites, not post-release
paperwork.

## Deferred Or Separate Providers

### HubSpot

HubSpot remains manual-token/private-app compatible in this delivery and was
explicitly excluded from interactive OAuth work. Keep it aside until a separate
provider investigation defines its app type, scopes, actions, verification,
and callback contract.

### X and LinkedIn

The current StackOS catalog has manual OAuth-token metadata while executable
provider actions remain deferred. Do not register production applications or
claim an interactive StackOS connection until those provider deliveries are
separately authorized and completed.

## Operator Completion Checklist

Before treating any interactive provider as ready:

- [ ] The Pages custom domain resolves over HTTPS through the Namecheap CNAME.
- [ ] The uploaded static artifact version is recorded and matches reviewed
      source, including `_headers`.
- [ ] Wrong origin/path, missing state, duplicate or oversized values,
      conflicting outcomes, and arbitrary return destinations fail safely.
- [ ] The local daemon is listening on canonical port `5180`, and the existing
      exact `GET /api/v1/auth/oauth/callback` regression proof passes.
- [ ] `STACKOS_OAUTH_CALLBACK_BASE_URL` contains only the public HTTPS origin.
- [ ] The provider console contains the exact full callback path.
- [ ] Only the documented scopes/permissions are enabled.
- [ ] Client/application secrets are stored only in local StackOS Connections.
- [ ] The flow is started and completed in a browser on the StackOS machine.
- [ ] Success, denial, replay, daemon-down, and reconnect behavior are checked.
- [ ] Persistent Pages, proxy, daemon, and audit logs contain no callback
      codes, state values, tokens, or secrets.
- [ ] Any provider review, business verification, admin consent, or live-app
      approval remains explicit.
- [ ] No release or production-readiness claim is made from registration alone.

Last provider-documentation review: 2026-07-21.
