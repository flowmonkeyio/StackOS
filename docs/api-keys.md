# StackOS API Key + OAuth Setup

This guide walks through obtaining credentials for vendor integrations. Every
key is stored encrypted at rest in ``integration_credentials`` and exposed to
agents only as sanitized status plus opaque credential refs.

Recommended setup flow:

1. Let the agent identify the vendors needed for the current run plan.
2. Let the agent call `auth.status` to see which provider refs already exist.
3. Open the project connections page the agent gives you, for example
   ``http://localhost:5180/projects/1/connections?provider_key=dataforseo``.
4. Connect vendors from the named cards. Do not paste secrets into agent
   chat and do not add vendor keys to the website repository.
5. Return to the agent. The agent should run `auth.test` with the opaque
   `credential_ref` or provider key before continuing.

`integration_credentials` is still the encrypted backing table, but agents use
the generic auth-provider boundary. `auth.status` and `auth.test` return
sanitized provider state and credential refs; local UI/REST setup is the only
place plaintext keys or OAuth secrets are accepted.

---

## DataForSEO

Used by: SEO plugin actions such as keyword research, SERP analysis, and
competitor discovery.

1. Sign up at <https://app.dataforseo.com>.
2. Top up a small balance (~$5 covers thousands of test queries).
3. From the dashboard copy your **API login** + **API password**.
4. In the StackOS Connections screen pick "DataForSEO" and paste
   the pair. The login lands in ``config_json.login``; the password
   lands in the encrypted payload.

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

Used by: scraping, crawl, page extraction, and any plugin action that needs
web-page material.

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

Used by: the operator runtime, outside content-stack. Writing and planning
are agent-led: Codex, Claude Code, or another MCP client performs the work
and uses that runtime's own model credentials.
Do not store prose-generation OpenAI/Anthropic keys in content-stack
just so the daemon can spawn unattended writer sessions.

The only OpenAI key content-stack itself needs is the vendor integration
key for image generation below.

---

## OpenAI Images

Used by: ``image-generator``.

1. Open <https://platform.openai.com/api-keys>.
2. Create a new key — restrict it to "Restricted" with only
   ``image.generation`` enabled if your account supports scopes.
3. Paste into Connections → OpenAI Images.

This row is **separate** from the LLM key used by your external agent
(PLAN.md L1057-L1063), so you can budget images independently from prose.

Default path: the utility image action uses the current GPT Image API
(``gpt-image-1.5`` by default). GPT Image responses return base64 image data,
so the daemon wrapper persists the bytes under
``CONTENT_STACK_DATA_DIR/generated-assets`` and returns local artifact URLs.

Cost: treat the wrapper's image estimates as a budget guardrail, not final
billing. The wrapper records the vendor response and the operator should
reconcile against OpenAI's current pricing page.

Env var equivalent:

```
OPENAI_API_KEY=sk-...
```

---

## WordPress

Used by: WordPress credential probes when a project enables the provider.

1. In WordPress, open **Users → Profile → Application Passwords**.
2. Create a dedicated application password for a least-privileged user. Do not
   use an administrator account for automation.
3. In Connections → WordPress, store the payload as either JSON
   ``{"username":"...", "application_password":"..."}`` or compact
   ``username:application-password``.
4. Set ``config_json.wp_url`` to the site root, e.g.
   ``https://example.com``.

The wrapper probes ``GET /wp-json/wp/v2/users/me?context=edit`` using Basic Auth
with the application password. Posting content should be added later as an
explicit plugin action with its own schema and run-plan grant.

---

## Ghost

Used by: Ghost credential probes when a project enables the provider.

1. In Ghost Admin, create a **Custom Integration**.
2. Copy the Admin API key in ``id:secret`` form.
3. In Connections → Ghost, store the key as either raw ``id:secret`` or
   JSON ``{"admin_api_key":"id:secret"}``.
4. Set ``config_json.ghost_url`` to the Ghost Admin domain root, e.g.
   ``https://example.com``.
5. Optionally set ``config_json.api_version``; default is ``v5.0``.

The wrapper signs a short-lived HS256 JWT with ``aud="/admin/"`` and
sends it as ``Authorization: Ghost <token>`` with ``Accept-Version``.
The probe hits ``GET /ghost/api/admin/users/?limit=1&include=roles``. Posting
content should be added later as an explicit plugin action with its own schema
and run-plan grant.

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
   (e.g. ``content-stack/0.1 by your-username``).
7. Paste all three into Connections → Reddit. The wrapper persists
   them as a JSON bundle inside the encrypted payload.

Env var equivalent:

```
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
```

---

## Google PAA

Used by: plugin actions that need People Also Ask style discovery.

No credential needed. The Google PAA wrapper delegates to Firecrawl
under the hood. If you set a direct budget for the wrapper, use kind
``google-paa``; older ``paa`` budget rows are normalized to ``google-paa``
by the REST routes.

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
