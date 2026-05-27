# StackOS Connection Credential Setup

This guide walks through obtaining credentials for provider connections. Every
secret is stored encrypted at rest in `integration_credentials` and exposed to
agents only as sanitized status plus opaque `credential_ref` values.

Recommended setup flow:

1. Let the agent identify the vendors needed for the current run plan.
2. Let the agent call `toolbox.call` for `auth.status` to see which provider
   refs already exist.
3. Open the project Connections page the agent gives you, for example
   `http://127.0.0.1:5180/projects/1/connections?provider_key=dataforseo`.
4. Connect vendors from the named cards. Do not paste secrets into agent
   chat and do not add vendor keys to the website repository.
5. Return to the agent. The agent should run `toolbox.call` for `auth.test`
   with the selected opaque `credential_ref` before continuing.

Providers define typed `auth_methods`. The local UI renders those schemas and
stores one or more named credential profiles per provider. Secret fields are
encrypted in `integration_credentials`; safe fields are stored as redacted
credential config. `auth.status` and `auth.test` return sanitized provider
state and credential refs only.

Do not paste secrets into tracker tickets, run-plan metadata, communication
surface metadata, plugin config, or agent chat. If a workflow needs a provider,
store the credential through Connections and pass the resulting
`credential_ref`.

---

## DataForSEO

Used by: SEO plugin actions such as keyword research, SERP analysis, and People
Also Ask extraction. Competitor discovery currently uses the Ahrefs action
connector when that optional provider is enabled.

1. Sign up at <https://app.dataforseo.com>.
2. Top up a small balance (~$5 covers thousands of test queries).
3. From the dashboard copy your **API login** + **API password**.
4. In the StackOS Connections screen pick "DataForSEO", choose the
   `basic` method, enter the API login and API password, and save the profile.
   The login is safe config; the password is encrypted.

Cost notes: ~$0.001-$0.003 per SERP call; the wrapper reads the
vendor's ``tasks[].cost`` value back into the budget so the cap stays
accurate.

Env var equivalent (server side / tests):

```
DATAFORSEO_LOGIN=...
DATAFORSEO_PASSWORD=...
```

---

## Firecrawl

Used by: scraping, crawl/map submission, and plugin actions that need
web-page material. Async extraction remains deferred until StackOS has a
status/read action and artifact contract.

1. Sign up at <https://firecrawl.dev>.
2. Verify email; the dashboard issues an API key starting ``fc-...``.
3. Paste the key into Connections → Firecrawl.

Cost: ~$0.001 per scrape; crawl runs ~$0.002 per page. Set the project
budget cap in Settings → Budgets.

Env var equivalent:

```
FIRECRAWL_API_KEY=fc-...
```

## Runtime LLM keys

Used by: the operator runtime, outside StackOS. Writing and planning
are agent-led: Codex, Claude Code, or another MCP client performs the work
and uses that runtime's own model credentials.
Do not store prose-generation OpenAI/Anthropic keys in StackOS
just so the daemon can spawn unattended writer sessions.

The only OpenAI key StackOS itself needs is the vendor integration
key for image generation below.

---

## OpenAI Images

Used by: ``image-generator``.

1. Open <https://platform.openai.com/api-keys>.
2. Create a new key — restrict it to "Restricted" with only
   ``image.generation`` enabled if your account supports scopes.
3. Paste into Connections → OpenAI Images.

This row is **separate** from the LLM key used by your external agent
runtime, so you can budget images independently from prose.

Default path: the utility image action uses the current GPT Image API
(``gpt-image-2`` by default). GPT Image responses return base64 image data,
so the daemon wrapper persists the bytes under
``STACKOS_DATA_DIR/generated-assets`` and returns local artifact URLs.

Cost: treat the wrapper's image estimates as a budget guardrail, not final
billing. The wrapper records the vendor response and the operator should
reconcile against OpenAI's current pricing page.

Env var equivalent:

```
OPENAI_API_KEY=sk-...
```

---

## WordPress

Used by: `publishing.wordpress.post.create` and WordPress credential probes.

1. In WordPress, open **Users → Profile → Application Passwords**.
2. Create a dedicated application password for a least-privileged user. Do not
   use an administrator account for automation.
3. In Connections → WordPress, store the secret payload as either JSON
   ``{"username":"...", "application_password":"..."}`` or compact
   ``username:application-password``.
4. Fill the safe "Site URL" setup field. It is stored as
   ``config_json.wp_url`` and should be the site root, e.g.
   ``https://example.com``.

The wrapper probes ``GET /wp-json/wp/v2/users/me?context=edit`` using Basic Auth
with the application password. The publishing action posts the agent-supplied
``post`` object to ``POST /wp-json/wp/v2/posts`` through direct ``action.run``
for one explicit call or run-plan-scoped ``action.execute``.

---

## Ghost

Used by: `publishing.ghost.post.create` and Ghost credential probes.

1. In Ghost Admin, create a **Custom Integration**.
2. Copy the Admin API key in ``id:secret`` form.
3. In Connections → Ghost, store the secret key as either raw ``id:secret`` or
   JSON ``{"admin_api_key":"id:secret"}``.
4. Fill the safe "Admin URL" setup field. It is stored as
   ``config_json.ghost_url`` and should be the Ghost Admin domain root, e.g.
   ``https://example.com``.
5. Optionally fill the "API Version" setup field; it is stored as
   ``config_json.api_version`` and defaults to ``v5.0``.

The wrapper signs a short-lived HS256 JWT with ``aud="/admin/"`` and
sends it as ``Authorization: Ghost <token>`` with ``Accept-Version``.
The probe hits ``GET /ghost/api/admin/users/?limit=1&include=roles``. The
publishing action posts the agent-supplied ``post`` object to
``POST /ghost/api/admin/posts/?source=html`` through direct ``action.run`` for
one explicit call or run-plan-scoped ``action.execute``.

---

## Reddit

Used by: plugin actions that need Reddit audience or question research.

1. Open <https://www.reddit.com/prefs/apps>.
2. Click **create another app...** at the bottom.
3. Pick **script**.
4. Set the redirect URI to anything (we use application-only auth so
   the redirect is unused).
5. Copy the **client_id** (under the app name) + **client_secret**.
6. Choose a unique **user_agent** string per Reddit's API rules
   (e.g. ``stackos/0.1 by your-username``).
7. Paste all three into Connections → Reddit. The wrapper persists
   them as a JSON bundle inside the encrypted payload.

Env var equivalent:

```
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
```

---

## Google PAA

Used by: future plugin actions that need Firecrawl-derived People Also Ask
style discovery.

The current first-party PAA action is ``seo.paa.extract`` and uses
DataForSEO credentials plus the ``dataforseo`` project budget. The separate
``google-paa`` wrapper still delegates to Firecrawl under the hood, but it is
not exposed as a first-party plugin action yet. If we add that wrapper later,
its dependency and budget policy should be explicit in the action manifest.

---

## Jina Reader

Used by: plugin actions that need markdown extraction from URLs.

1. Optional: sign up at <https://jina.ai/reader> for an API key (free
   tier exists; paid raises limits).
2. Without a key the wrapper hits the public ``r.jina.ai`` endpoint at
   reduced rate. Most use cases work without one.

Env var equivalent:

```
JINA_API_KEY=...
```

---

## Ahrefs

Used by: optional SEO plugin actions. DataForSEO remains the default fallback.

Ahrefs API v3 is optional for our first internal tests. Ahrefs' current
docs describe API v3 access and API-unit limits by paid plan, with higher
limits and additional-unit purchases on Enterprise. For solo/SMB
operators, DataForSEO covers the same keyword/SERP surface area we need
first.

If you have a paid Ahrefs plan with API v3 access:

1. Go to <https://ahrefs.com/api>.
2. Generate a new token.
3. Paste it into StackOS Connections → Ahrefs.

If you don't have a plan: skip this integration. The credential test returns a
sanitized failure with a setup hint, and templates can choose DataForSEO-backed
actions instead.
