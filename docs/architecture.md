# StackOS architecture

A reference for contributors and operators. The repository is pivoting from
SEO Stack/content-stack into StackOS: a local tool runtime where SEO is a
first-party compatibility/plugin domain, not the product boundary.

The current implementation is still named `content-stack` in many packages,
commands, paths, procedures, and docs. Those names are compatibility surface
until the signed-off delivery plan migrates them. For target design and
delivery order, use:

- [`stackos-pivot-design-and-delivery-plan.md`](./stackos-pivot-design-and-delivery-plan.md)
- [`stackos-current-setup-gap-analysis.md`](./stackos-current-setup-gap-analysis.md)
- [`stackos-deliverable-task-plan.md`](./stackos-deliverable-task-plan.md)

This file explains the working architecture and the guardrails for changing it.
Older sections below still document current SEO/procedure internals so they can
be preserved or migrated safely.

---

## 1. Vision

StackOS is a globally installed local daemon and agent plugin surface for
tooling across domains: SEO, media buying, GTM, utilities, and private
company workflows.

Three audiences:

1. **The agent** calls MCP tools, validates templates/run plans, retrieves
   bounded context, executes granted actions, and records outputs. The agent
   decides strategy; StackOS stores and enforces.
2. **The human operator** opens the UI at `http://localhost:5180`, connects
   providers, reviews runs, approves gated actions, and inspects project data.
3. **The end developer/company** installs StackOS once, enables plugins for
   the work they need, and optionally adds repo/company templates under
   `.stackos/`.

The critical boundary:

```text
Agent / human:
  decides, interprets, chooses strategy, writes decisions/learnings

StackOS:
  stores, retrieves, schema-checks, enforces auth/grants/budgets,
  executes external actions, redacts, persists, and audits
```

StackOS must not become a decision engine. It should not choose the campaign
strategy, select the winning experiment, decide the next SEO topic, or infer a
business learning. It should expose reliable primitives so the agent/human can
do those things intentionally.

## 1.1 Architecture Guardrails

- Core stays domain-neutral: projects, plugins, capabilities, providers,
  actions, resources, artifacts, auth refs, workflow templates, run plans,
  runs, context, learnings, experiments, decisions, audit, and generic UI.
- Domain behavior belongs to plugins. SEO is a plugin/compatibility domain;
  media buying, GTM, utilities, and private company tools follow the same
  plugin model.
- Tools are static operations: set data, retrieve data, validate input,
  trigger external actions, and record auditable outputs. Business decisions
  stay with agents/humans.
- Agents never receive secrets. Provider credentials, OAuth tokens, API keys,
  refresh tokens, and raw secret-bearing payloads stay inside the daemon.
- Agent-visible tools are split into direct/discovery, setup/admin-gated,
  step-scoped/gated, and compatibility surfaces.
- Workflow UI is generic by default. New work should render templates, run
  plans, resources, actions, artifacts, and Project Data through generic
  renderers instead of adding per-workflow pages.

## 1.2 Current-State Compatibility

The current codebase still has SEO-specific procedures, article tables, skills,
vendor tools, setup routes, and UI pages. Treat them as current-state behavior
that must keep working until a signed-off task migrates or removes them.

Procedures remain compatibility. New workflow modeling should use reusable
Workflow Templates, concrete Run Plans, and auditable Runs.

---

## 2. Process model

One Python process. One SQLite database. Three transports:

```
                    ┌─────────────────────────────────────┐
                    │  LLM clients (any of):              │
                    │   - Codex CLI                       │
                    │   - Claude Code                     │
                    │   - Cursor / Cline / Continue / …   │
                    └──────────────┬──────────────────────┘
                                   │  MCP (Streamable HTTP)
                                   ▼
        ┌──────────────────────────────────────────────────────┐
        │  content-stack daemon (single Python process)        │
        │   uvicorn + FastAPI                                  │
        │                                                      │
        │   ┌─────────┬──────────────┬─────────────────────┐  │
        │   │  /mcp   │   /api/v1    │   /  (static UI)    │  │
        │   │  MCP    │   REST       │   Vue 3 + Vite      │  │
        │   └────┬────┴──────┬───────┴──────────┬──────────┘  │
        │        └───────────┴──────────────────┘             │
        │                    ▼                                │
        │   ┌────────────────────────────────────────────┐   │
        │   │ repositories  +  integrations              │   │
        │   │ (SQLModel)    (DataForSEO, Ahrefs,         │   │
        │   │                Firecrawl, GSC, OpenAI      │   │
        │   │                Images, Reddit, PAA, Jina)  │   │
        │   └────────────────────┬───────────────────────┘   │
        │                        ▼                            │
        │   ┌──────────────────────────────────────┐         │
        │   │ ~/.local/share/content-stack/        │         │
        │   │   content-stack.db (SQLite + WAL)    │         │
        │   └──────────────────────────────────────┘         │
        └──────────────────────────────────────────────────────┘
                                ▲
                                │  http://localhost:5180
                                │
                    ┌───────────┴────────────┐
                    │  Browser (human)       │
                    └────────────────────────┘
```

### 2.1 Localhost-only binding

The daemon binds to `127.0.0.1:5180` and refuses any other host:

- `content_stack/config.py:85` `Settings._reject_non_loopback` rejects
  non-loopback values at parse time. Passing `--host 0.0.0.0` exits
  with status 1 before any port is opened.
- `content_stack/server.py:83` `HostHeaderMiddleware` rejects any HTTP
  request whose `Host:` header is not `localhost`, `127.0.0.1`, or
  `[::1]` with HTTP 421 Misdirected Request. Defends against DNS
  rebinding and stray cross-origin probes.
- `content_stack/server.py:362-371` configures `CORSMiddleware`
  same-origin only — a cross-origin browser fetch can never read
  responses, even if the request reached the server.

The combination of CLI parse + Host header check + same-origin CORS
means there is no supported path to expose the daemon over the
network. Operators who need cross-machine access run the daemon
behind their own SSH tunnel.

### 2.2 Per-install bearer token

Every REST + MCP request carries `Authorization: Bearer <token>`. The
token is 32 random bytes (base64-url encoded) at
`~/.local/state/content-stack/auth.token` (mode 0600). The
`BearerTokenMiddleware`
([`content_stack/auth.py`](../content_stack/auth.py)) does a
constant-time check on every request, with three whitelisted paths:

| Path                  | Why whitelisted                                                                                |
| --------------------- | ---------------------------------------------------------------------------------------------- |
| `/api/v1/health`      | Doctor probes liveness before resolving the token.                                             |
| `/api/v1/auth/ui-token` | The browser SPA bootstraps a derived read-only token from the daemon at app load; never lands in localStorage. |
| `/api/v1/integrations/gsc/oauth/callback` | Google redirects the operator's browser here; the route validates the OAuth `state` nonce. |

`content-stack init`, `content-stack install`, and `make install` create the
token before MCP registration. `content-stack rotate-token --yes` and
`make rotate-token` regenerate the Codex + Claude Code MCP configs to match;
restart any already-running daemon so middleware loads the new token. See
[`./security.md`](./security.md) for the threat-model trade-off on
the UI bootstrap endpoint.

Per [`../PLAN.md:97-114`](../PLAN.md) and locked decision **D5**.

### 2.3 SQLite PRAGMAs

`content_stack/db/connection.py` sets the following on every
connection:

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 268435456;
```

WAL mode means concurrent readers do not block each other; writers
queue against a single writer mutex. The repository layer keeps
transactions short (≤100 ms target) and batches enrichment writes in
100-row chunks. `MAX_CONCURRENCY` env var caps simultaneous procedure
runs (default 4) so the daemon's own jobs and the UI never starve.

---

## 3. Lifespan

`content_stack/server.py:_build_lifespan` defines the boot + shutdown
sequence wrapped in a FastAPI `@asynccontextmanager`. The full
sequence:

1. **Settings + dirs.** `settings.ensure_dirs()` creates
   `~/.local/share/content-stack/` and `~/.local/state/content-stack/`
   at mode 0700 (umask-resistant via explicit `os.chmod`).
2. **Logging.** `configure_logging` wires up structlog + the
   `RotatingFileHandler` against `~/.local/state/content-stack/daemon.log`
   (10 MB × 5 rotation).
3. **Seed file.** `_ensure_seed` creates `seed.bin` (32 random bytes,
   mode 0600) atomically via `os.open(... O_CREAT|O_EXCL ...)`. If the
   file exists with the wrong mode, the daemon refuses to boot rather
   than silently widen permissions.
4. **Crypto layer.** `configure_seed_path` registers the seed path
   with the AES-256-GCM module so `encrypt`/`decrypt` callers do not
   need to thread the path through every call.
   `cleanup_old_backup` removes the rotation backup left by the
   previous boot (per PLAN.md L1142 — the `seed.bin.bak` is kept for
   exactly one boot then auto-deleted).
5. **Auth token.** `ensure_token` creates `auth.token` (32 random
   bytes, base64-url, mode 0600) if absent. Existing-token mode
   checks mirror the seed file logic.
6. **Engine.** `make_engine(settings.db_path)` returns a SQLAlchemy
   engine with the WAL pragmas applied via a connection event hook.
7. **Schema bootstrap.** `SQLModel.metadata.create_all(engine)` is the
   safety-net for fresh installs and tests; production uses
   `make migrate` (alembic upgrade head) before `make serve`. The
   create_all is a no-op on warm starts. The lifespan also runs
   `_ensure_partial_indexes` to guarantee the partial-unique indexes
   (B-08 publish_targets primary, B-09 internal_links uniqueness)
   that SQLModel cannot express declaratively are present even if the
   path didn't go through alembic.
8. **Crash-recovery sweep.** `RunRepository.reap_stale` flips any
   `runs.status='running' AND heartbeat_at < now-5min` row to
   `aborted` with `error='daemon-restart-orphan'`, cascading to child
   runs. Per PLAN.md L1366-L1391 the sweep does NOT auto-resume; it
   surfaces a "Resumable" badge in the UI's RunsView. See section
   14 below.
9. **Scheduler.** `build_scheduler(settings, engine)` constructs an
   `AsyncIOScheduler` with a `SQLAlchemyJobStore` against the same
   DB. Configured with `coalesce=True`, `max_instances=1`,
   `misfire_grace_time=3600`. On startup, missed runs collapse to one
   run rather than firing N catch-ups.
10. **Procedure runner.** `ProcedureRunner` loads every
    `procedures/<slug>/PROCEDURE.md` at construction time. A malformed
    file aborts the lifespan rather than surfacing a 500 on the first
    `procedure.run` call.
11. **Recurring jobs.** Four are registered:
    - `runs_reaper` — every 5 minutes; idempotent re-run of the
      reap_stale sweep.
    - `oauth_refresh` — every 50 minutes; refreshes any GSC token
      whose `expires_at < now() + 10 min`.
    - `gsc_pull` — daily 03:15 UTC.
    - `drift_rollup` — daily 04:00 UTC, after gsc_pull.
12. **Cron-triggered procedures.** `register_cron_procedures` reads
    every active project's `scheduled_jobs` rows and registers
    procedures 6 (`weekly-gsc-review`) and 7 (`monthly-humanize-pass`)
    as APScheduler jobs with `job_id=procedure-{slug}-{project_id}`
    so per-project serialization holds across restarts.
13. **Scheduler.start().** APScheduler begins firing.
14. **Routers + MCP + UI.** `register_routers(app)` mounts every REST
    router under `/api/v1`; `register_mcp(app)` mounts the Streamable
    HTTP sub-app at `/mcp`; `_mount_ui(app, settings)` serves the Vue
    bundle from `content_stack/ui_dist/`.
15. **uvicorn binds 127.0.0.1:5180.**

On `SIGTERM` (or `launchctl stop`):

1. uvicorn enters its 30-second graceful drain.
2. `scheduler.shutdown(wait=True)` lets in-flight short jobs finish
   their DB writes.
3. In-flight procedure-run steps catch the signal, mark themselves
   `aborted`, and write `runs.error='shutdown'`.
4. `engine.dispose()` closes the connection pool.

After the 30-second window uvicorn issues `SIGKILL`. The next boot's
crash-recovery sweep (step 8) handles whatever was still running.

---

## 4. Schema

30 tables, grouped by domain. Full row-by-row spec lives in
[`../PLAN.md:341-486`](../PLAN.md); the summary below names the
tables and what they're for.

### 4.1 Project + content tables

| Domain                 | Tables                                                                                                           |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Project + per-project presets | `projects`, `voice_profiles`, `authors`, `compliance_rules`, `eeat_criteria`, `publish_targets`           |
| Topical map + topics queue    | `clusters`, `topics`                                                                                       |
| Article lifecycle             | `articles` (current), `article_versions`, `article_assets`, `article_publishes`, `research_sources`, `schema_emits`, `internal_links`, `redirects` |
| GSC + drift                   | `gsc_metrics` (raw, 90-day retention), `gsc_metrics_daily` (aggregated), `drift_baselines`                 |
| Per-criterion EEAT            | `eeat_evaluations`                                                                                         |
| Integration creds + budgets   | `integration_credentials`, `integration_budgets`                                                           |
| Workspace + plugins           | `workspace_bindings`, `agent_sessions`                                                                     |

### 4.2 Run + audit-trail tables

| Table                | Purpose                                                                                  |
| -------------------- | ---------------------------------------------------------------------------------------- |
| `runs`               | Top-level pipeline audit. Heartbeat-reapable.                                             |
| `procedure_run_steps` | One row per procedure step; pre-written before agent execution. Drives resume cursor.     |
| `run_steps`          | Per-skill audit grain. Cost-of-truth lives here.                                          |
| `run_step_calls`     | Per-MCP-call audit grain inside a skill step.                                             |
| `idempotency_keys`   | Mutating-tool dedup. UNIQUE(project_id, tool_name, idempotency_key); 24 h replay window.  |
| `scheduled_jobs`     | Per-project schedules. UI Schedules tab toggles `enabled`. APScheduler reads on startup.  |
| `workspace_bindings` | Daemon-owned repo fingerprint → project mapping for plugin-first workflows.               |
| `agent_sessions`     | Disposable bridge/session records from Codex, Claude Code, or another runtime.            |

### 4.3 Loaded indexes

`content_stack/db/migrations/versions/0002_*.py` provisions the
hot-path composite indexes per [`../PLAN.md:466-486`](../PLAN.md).
The two partial-unique indexes (publish_targets primary, internal_links
unique-when-active) are emitted by both alembic and the lifespan
fallback so they're present on every boot.

### 4.4 D-locked schema invariants

- **D3 singular locale** — `projects.locale TEXT NOT NULL`.
  Multi-locale = separate project per locale. Translation pipeline is
  out of scope.
- **D6 article_versions** — `articles` keeps the current row only.
  `article.createVersion` MCP copies the live row → `article_versions`
  before mutating; each refresh = one new version row.
- **D7 EEAT core floor** — `eeat_criteria.tier ENUM('core','recommended','project')`.
  T04 / C01 / R10 seed as `tier='core'`; the repository invariant
  refuses to deactivate or unrequire them. The eeat-gate skill refuses
  to score if any of the 8 dimensions has 0 active items.

---

## 5. Repository layer

`content_stack/repositories/` is the transport-agnostic seam between
the routers / MCP tools and the database. Each repository module
covers one or two related tables and owns:

- **Business logic.** State-machine transitions, slug normalisation,
  idempotency-key dedup.
- **Typed errors.** `RepositoryError` subclasses (`NotFoundError`,
  `ConflictError`, `ValidationError`, `BudgetExceededError`,
  `RateLimitedError`, `IntegrationDownError`) — see
  `content_stack/repositories/base.py`.
- **Pagination.** Cursor-based on `id ASC`; `limit` default 50, max
  200; envelope `{items, next_cursor, total_estimate}`.
- **Optimistic concurrency.** The articles fat row uses a UUID
  `step_etag` that regenerates on every write. Every `article.set*`
  MCP tool requires `expected_etag`; mismatch → 409 (-32008).
  REST `PATCH /articles/{id}` carries an `If-Match: <updated_at iso>`
  header. Per audit B-07.

Repositories return pydantic models exported as the canonical Output
shape — REST routers and MCP tools share the same typed surface.
Repository code is the only place that opens a SQLAlchemy session;
neither routes nor tools touch the engine directly.

---

## 6. REST API surface

77 endpoints across 16 routers (counted from
`content_stack/api/*.py`). Mounted under `/api/v1`. Drives the UI;
the LLM uses MCP.

### 6.1 Middleware order

Outermost first:

1. `HostHeaderMiddleware` — runs even on auth-whitelisted paths.
2. `CORSMiddleware` — same-origin only.
3. `BearerTokenMiddleware` — constant-time bearer check, with
   `WHITELIST_PREFIXES` at `content_stack/auth.py` for `/api/v1/health`,
   `/api/v1/auth/ui-token`, and the Google Search Console OAuth callback.

(Starlette runs the last-added middleware first on the request path,
so the wiring order in `server.py:362-372` adds them inside-out.)

### 6.2 OpenAPI + TS generation

FastAPI auto-publishes the OpenAPI spec at `/api/openapi.json` and
Swagger UI at `/api/docs`. `make gen-types` runs
`scripts/gen-types.sh` which invokes `openapi-typescript` against
the live spec and rewrites `ui/src/api.ts`. The committed file is
checked by release validation; mismatch fails the check so the UI
types never drift from the daemon shape.

### 6.3 Response envelope

Mutating endpoints return `{data, run_id, project_id}` so the UI can
correlate the write with its audit row. Read endpoints return the
bare data. The MCP envelope mirrors this; see section 7 below.

### 6.4 Permissive vs. state-machine

`PATCH /api/v1/articles/{id}` accepts arbitrary column updates and
writes a `runs` row with `kind='manual-edit'`. This is the human
escape hatch in the UI. MCP tools (`setBrief`, `setOutline`, etc.)
enforce the `articles.status` state machine and the etag check.
Procedure runs always go through MCP; humans can bypass via the UI
if they accept the audit-trail consequence
(`runs.kind='manual-edit'`).

### 6.5 Per-tool rate limits

Middleware enforces 100 calls/minute per tool, 1000 calls/minute
aggregate per Authorization token. Breach returns 429 with
`retry_after` and JSON-RPC code -32011. Bulk tools count as N calls.
Caps blast radius if a token is exfiltrated.

---

## 7. MCP server

163 tools registered (`content_stack/mcp/tools/*.py`) over
Streamable HTTP at `/mcp`. The MCP server is a single
`mcp.server.lowlevel.Server` instance built in
`content_stack/mcp/server.py`; the FastAPI sub-app mount means the
bearer-token middleware (via `PROTECTED_PREFIXES`) gates every tool
call before it lands on a handler.

The installable plugin does not expose all 163 tools to agents. Its
`content-stack mcp-bridge` proxies to the daemon but filters `tools/list`
to a compact control surface: workspace/project setup, procedure control,
selected run status calls, and the bridge-local `toolbox.describe` /
`toolbox.call` helpers. Setup helpers and current-step `allowed_tools`
remain callable through the toolbox, so context stays small while the
daemon retains the full internal surface for UI/tests/automation.

### 7.1 Tool contract

Every tool has `tools/<resource>.py` with a `class FooInput(MCPInput)`
and a `class FooOutput(BaseModel)` (pydantic v2). Mutating verbs
(starting with `create|update|set|mark|add|remove|toggle|approve|
reject|apply|dismiss|bulkCreate|bulkUpdate|bulkApply|run|snapshot|
ingest|test|validate|abort|resume|fork|activate|setPrimary|setActive`)
return a `WriteEnvelope[Inner]` (`content_stack/mcp/contract.py`);
read tools return the bare data. A registration-time check
(`assert_envelope_discipline`) refuses to boot the daemon if a
mutating tool's output annotation is not a `WriteEnvelope[...]`.

### 7.2 Tool-grant matrix

`content_stack/mcp/permissions.py:SKILL_TOOL_GRANTS` is the
load-bearing security seam. Every MCP request carries a `run_token`;
`resolve_run_token(token, session)` looks the token up against
`runs.client_session_id` and returns the calling skill name (from
`runs.metadata_json.skill_name` or the procedure slug). `check_grant`
then asserts that the requested tool is in
`SKILL_TOOL_GRANTS[skill_name]`; mismatch raises
`ToolNotGrantedError` (JSON-RPC -32007 forbidden).

Two sentinel skill names define bootstrap/test behaviour:

- `__system__` — narrow bootstrap grant for direct calls with no
  `run_token`.
- `__test__` — full-grant fixture sentinel used only by tests.

Production callers always present a provisioned token; the system
surface carries `None` and resolves to the explicit `__system__`
allow-list.

### 7.3 Idempotency keys

Every mutating MCP tool accepts an optional `idempotency_key: str`.
The repository keys `(project_id, tool_name, idempotency_key)` →
`idempotency_keys.id` and short-circuits replays within a 24 h
window with the cached response. Replays beyond 24 h are treated as
fresh. The dedicated table makes the window queryable for support.
Per audit M-20.

### 7.4 Streaming

Four tools emit `progress` events (one per logical step):
`procedure.run`, `topic.bulkCreate` (when N>50),
`gsc.bulkIngest`, `interlink.suggest`, `article.bulkCreate` (when
N>20). Streaming is opt-in per tool via `streaming: true` at
registration; clients that don't consume SSE see only the final
result.

### 7.5 Error model

JSON-RPC error code mapping; every error includes
`data: {run_id?, retryable, retry_after?, hint?}`.

| Code   | Meaning                                  | Retryable |
| ------ | ---------------------------------------- | --------- |
| -32602 | validation failed                        | no        |
| -32004 | not found                                | no        |
| -32007 | forbidden (tool-grant matrix violation)  | no        |
| -32008 | conflict (etag mismatch / state-machine) | no        |
| -32010 | integration down                         | yes       |
| -32011 | rate-limited                             | yes       |
| -32012 | budget exceeded                          | no        |
| -32603 | internal                                 | sometimes |

---

## 8. Procedure control

Per locked decision **D4**, procedures are agent-led. The MCP tool
`procedure.run(slug, project_id, args)` opens a durable run and
pre-writes the step skeleton. The current external agent then claims
each step, receives the step's `SKILL.md` body plus allowed tools,
executes the work directly through MCP, and records the output. The
daemon is the state, audit, grant, and validation layer; it is not the
writer brain.

### 8.1 PROCEDURE.md contract

`content_stack/procedures/parser.py:ProcedureSpec` is the canonical
shape:

| Field                | Purpose                                                                               |
| -------------------- | ------------------------------------------------------------------------------------- |
| `name`               | Human-readable label.                                                                 |
| `slug`               | Must equal the directory name; loader enforces.                                       |
| `version`            | Semver string for catalog drift detection.                                            |
| `description`        | UI + `procedure.list` body.                                                           |
| `triggers`           | Natural-language; informational only.                                                 |
| `prerequisites`      | Natural-language predicates over DB rows; first step's skill enforces.                |
| `produces`           | Tables this procedure mutates; surfaced in the UI for blast-radius.                   |
| `steps[]`            | Ordered playbook (`ProcedureStep`).                                                   |
| `variants[]`         | Named overrides (e.g., `pillar` for 4000-word target).                                |
| `concurrency_limit`  | Per-`(slug, project_id)` ceiling.                                                     |
| `resumable`          | Whether `procedure.resume(run_id)` is allowed after abort.                            |
| `schedule?`          | Cron metadata for procedures 6 + 7 (cron-triggered).                                  |

### 8.2 Agent-led step loop

For each step in order:

1. `procedure.run` pre-writes the `procedure_run_steps` rows as
   `pending`.
2. The current agent calls `procedure.claimStep`.
3. The daemon marks the row `running`, returns the step package, and
   binds the run token to that step's skill grant.
4. The current agent follows the skill prompt, calls MCP tools, and may
   spawn caller-owned subagents for bounded subtasks.
5. The current agent calls `procedure.recordStep`; the daemon persists
   `procedure_run_steps.output_json`, updates `runs.last_step`, and
   exposes the next step.
6. The current agent applies `ProcedureStep.on_failure` guidance (see
   section 8.4 below) and decides whether to retry, loop back, pause,
   fork, or abort.

### 8.3 Skill execution model

Each skill step is executed by the current operator agent. The step
package:

- Loads the skill's `SKILL.md` from
  `skills/<phase>/<name>/SKILL.md`.
- Includes merged procedure args, prior step outputs, article/project
  context, and the allowed MCP tools.
- Uses the run token as a scoped permission handle. `procedure.claimStep`
  updates `runs.metadata_json.skill_name` to the claimed skill so
  `permissions.py` enforces the same per-skill grants as the skill
  frontmatter.
- Keeps the current agent as the orchestration brain. Any subagents are
  spawned by the current agent and report back to it, not by the daemon.

### 8.4 Failure modes

| Mode           | Effect                                                                                              |
| -------------- | --------------------------------------------------------------------------------------------------- |
| `abort`        | A failed record marks `runs.status='failed'`.                                                       |
| `retry`        | The current agent retries up to `max_retries` (cap 3), then escalates.                              |
| `loop_back`    | The current agent returns to a prior step id, respecting the configured loop cap.                    |
| `skip`         | The current agent records `procedure_run_steps.status='skipped'` and advances.                      |
| `human_review` | The controller records a review pause; the current agent retries after operator action.             |

### 8.5 EEAT three-verdict branch

The eeat-gate step (procedure 4 step 8) is the only step the runner
branches on a verdict for. Per audit BLOCKER-09:

- `SHIP` → advance.
- `FIX` → `loop_back('editor')` with the gate's `fix_required[]`
  in `runs.metadata_json.eeat`.
- `BLOCK` → abort with `runs.status='aborted'` and
  `articles.status='aborted-publish'`.

### 8.6 Audit trail

Three nested tables:

- `procedure_run_steps` — one row per declared step, written before
  execution. Drives the resume cursor.
- `run_steps` — one row per skill invocation. Cost-of-truth in
  `cost_cents`.
- `run_step_calls` — one row per MCP call inside a skill step.
  Enables "show me what step 7 sent to GSC".

The UI's RunsView walks this hierarchy.

---

## 9. Skills + procedures catalogue

25 skills × 5 phases; 8 procedures + 1 template.

### 9.1 Skills

Each skill is a directory: `skills/<phase>/<name>/SKILL.md` plus
optional `scripts/`. Same directory works for Codex CLI
(`~/.codex/skills/`) and Claude Code (`~/.claude/skills/`).

Phases:

1. **Research** — `keyword-discovery`, `serp-analyzer`,
   `topical-cluster`, `content-brief`, `competitor-sitemap-shortcut`.
2. **Content production** — `outline`, `draft-intro`, `draft-body`,
   `draft-conclusion`, `editor`, `eeat-gate`, `humanizer`.
3. **Assets** — `image-generator`, `alt-text-auditor`.
4. **Publishing** — `interlinker`, `schema-emitter`, `agent-publish`,
   `nuxt-content-publish`. `agent-publish` is the targetless default:
   the main operator agent publishes through available external tooling and
   records the result via `publish.recordExternal`. `nuxt-content-publish`
   is auto-selected only for a primary active Nuxt target. `wordpress-publish`
   and `ghost-publish` remain documented skill drafts with doc-backed
   credential wrappers, but are not procedure-wired publishers until their
   hidden media/post toolkit operations are implemented.
5. **Ongoing operations** — `gsc-opportunity-finder`, `drift-watch`,
   `crawl-error-watch`, `refresh-detector`, `content-refresher`.

### 9.2 Tool-grant matrix

Per-skill `allowed_tools` in `SKILL.md` frontmatter mirrors
`content_stack/mcp/permissions.py:SKILL_TOOL_GRANTS`. A startup smoke
check (`tests/integration/test_skills_frontmatter.py`) enforces
parity. The matrix is the security seam: a skill cannot reach an
ungranted tool even if it wants to.

### 9.3 Procedures

Eight canonical playbooks plus the `_template/` scaffold:

| #    | Slug                          | Trigger             | Purpose                                                |
| ---- | ----------------------------- | ------------------- | ------------------------------------------------------ |
| 1    | `bootstrap-project`           | First run           | Project + voice + compliance + EEAT seed + targets.    |
| 2    | `one-site-shortcut`           | Manual              | Sitemap + Ahrefs → topic queue, fast.                  |
| 3    | `keyword-to-topic-queue`      | Periodic / on-demand | Keyword discovery → SERP → cluster → approve.          |
| 4    | `topic-to-published`          | Per topic           | Brief → outline → draft → editor → humanizer → EEAT → publish. |
| 5    | `bulk-content-launch`         | Batch               | Fan-out procedure 4 across approved topics.            |
| 6    | `weekly-gsc-review`           | Weekly cron         | GSC pull → opportunity finder → drift / crawl errors.  |
| 7    | `monthly-humanize-pass`       | Monthly cron        | Refresh detector → humanizer → editor → republish.     |
| 8    | `add-new-site`                | Operator            | Compose 1 + (optional) 2 + 5.                          |

`procedures/_template/PROCEDURE.md` is excluded from
`install-procedures-{codex,claude}.sh` (authoring artefact, not
runtime).

---

## 10. Integrations layer

`content_stack/integrations/` — vendor wrappers with no business
logic. Each extends `BaseIntegration` (`_base.py`) and implements
`call(op, **kwargs)` + `test_credentials()`. The base class handles:

- Token-bucket rate limiting (`_rate_limit.py`); per-integration QPS
  read from `integration_budgets.qps` (defaults: DataForSEO 5,
  Firecrawl 2, GSC 1, OpenAI Images 10).
- **Pre-call budget pre-emption.** Per audit M-25,
  `IntegrationBudgetRepository.record_call` fires *before* the
  vendor hit; `current_month_spend + estimated_cost > monthly_budget_usd`
  raises `BudgetExceededError` (-32012) without burning the call.
- Retry / backoff on 429 / 5xx (3 retries, 0.5 s → 1 s → 2 s).
- Per-call audit trail via `RunStepCallRepository.record_call` —
  every vendor hit lands in `run_step_calls` with cost_cents +
  duration_ms.
- Sanitised request / response logging — never log secret tokens.
- Artifact normalization where the vendor does not return URLs. OpenAI
  Images GPT models return base64 image data, so the wrapper writes it
  under `data_dir/generated-assets` and returns `/generated-assets/...`
  URLs for `article_assets.url`.

Wrappers can override `_estimate_cost_usd` and
`_extract_actual_cost_usd` so the cost-of-truth column
(`run_steps.cost_cents`) reconciles after the call (e.g., DataForSEO
returns `tasks[].cost` per call; the wrapper threads it back).

### 10.1 Encryption-at-rest

`integration_credentials` rows hold encrypted payloads:

- **Seed**: `~/.local/state/content-stack/seed.bin`, 32 bytes mode 0600.
- **Key derivation**: HKDF-SHA256(seed, info=`b'content-stack:integration-credentials:v1'`) → 32-byte AES key.
- **Per-row nonce**: 12 random bytes stored in
  `integration_credentials.nonce`.
- **AAD**: `f'project_id={p}|kind={k}'` — binds the ciphertext to its
  row context. Tampering with `kind` or `project_id` renders the row
  undecryptable.
- **Wire format**: `encrypted_payload = ciphertext + auth_tag` (16 bytes GCM tag); `nonce` separate.

`content_stack/crypto/aes_gcm.py` is the encrypt/decrypt seam.

### 10.2 Credential resolution

Project-scoped row first; falls back to global. Project-scoped
overrides global. All integration calls log `project_id` to vendor
logs where supported.

### 10.3 OAuth refresh

`jobs/oauth_refresh.py` runs every 50 minutes. It scans
`integration_credentials WHERE kind='gsc' AND expires_at < now() + 10 min`,
refreshes via `refresh_token`, re-encrypts the new pair, updates
`expires_at` and `last_refreshed_at`. Failures surface via
`/api/v1/health.integrations_reachable.gsc=false`; the UI prompts
re-auth.

---

## 11. UI

Vue 3 + Vite + TypeScript + Tailwind + Pinia. Source at `ui/src/`;
built bundle committed at `content_stack/ui_dist/` per locked
decision **D8**.

### 11.1 Verification protocol

UI is "done" only after:

- Playwright screenshots at 6 breakpoints (360 / 640 / 768 / 1024 /
  1280 / 1440).
- axe accessibility scan with zero violations.
- Zero console errors.

Type checks and unit tests verify code correctness, not feature
correctness. The protocol is mandatory and lives in CLAUDE.md.

### 11.2 D8 parity check

Release validation rebuilds `ui/src/` and diffs against the committed
`content_stack/ui_dist/`. Mismatch fails the check. This lowers the install
floor: end users do not need `pnpm` or Node.js.

### 11.3 Auto-generated TS types

`make gen-types` regenerates `ui/src/api.ts` from the daemon's live
OpenAPI spec via `openapi-typescript`. Release checks fail on diff so the
UI types never drift from the daemon shape.

---

## 12. Distribution

Two install paths, same code:

### 12.1 Clone mode (`make install`)

The Makefile orchestrator:

1. `uv sync --all-extras` — install Python deps.
2. `python -m content_stack init` — create XDG dirs, seed, token.
3. `python -m content_stack migrate` — alembic upgrade head.
4. `make build-ui` (only if `ui_dist/` absent — D8 means it's
   committed).
5. `register-mcp-codex.sh` + `register-mcp-claude.sh` — upsert MCP
   server entries with current bearer token.
6. `install-plugins.sh` — mirrors `plugins/content-stack/` into
   `~/.codex/plugins/content-stack/`, hydrates it with `skills/catalog/` and
   `procedures/`, and upserts the local plugin marketplace.
7. `scripts/doctor.sh` — post-install diagnose.

Re-running is idempotent. Auth token rotation is explicit via
`content-stack rotate-token --yes` / `make rotate-token`, which
regenerates the MCP configs to match.

### 12.2 pipx mode

`pipx install content-stack` then `content-stack install` hydrates
`~/.codex/plugins/content-stack/` from the wheel's bundled
`content_stack/_assets/{plugins,skills,procedures}/` via the same code
paths. The wheel includes the committed `ui_dist/` (it's inside the
package), so end users never need pnpm.

When an enabled Codex plugin starts before the daemon is listening, the
plugin bridge auto-starts `python -m content_stack serve` on the configured
loopback host and waits for the port before continuing the MCP handshake.
Startup output lands at
`~/.local/state/content-stack/mcp-bridge-autostart.log`.

`pyproject.toml:[tool.hatch.build.targets.wheel.force-include]`
copies `plugins/`, `skills/`, and `procedures/` under
`content_stack/_assets/` at wheel-build time.

### 12.3 launchd plist (optional)

`make install-launchd` writes
`~/Library/LaunchAgents/com.content-stack.daemon.plist`. Optional;
`make serve` always works for the foreground path.

---

## 13. Operational invariants

The audit identified eight invariants the system must preserve. Each
maps to load-bearing code:

| Invariant            | Code                                                              | Behaviour                                                                                          |
| -------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| **D7 EEAT core floor** | `content_stack/repositories/eeat.py`, seed.py                    | T04 / C01 / R10 cannot be deactivated or unrequired. Eeat-gate refuses to score with 0 active items in any of 8 dimensions. |
| **B-07 article etag**  | `content_stack/repositories/articles.py`                          | Every `article.set*` requires `expected_etag`; mismatch → 409 (-32008). Step etag regenerates per write. |
| **B-13 reaper**        | `content_stack/jobs/runs_reaper.py`, lifespan sweep               | Every 5 minutes; flips `running` rows whose heartbeat is > 5 min stale to `aborted`.               |
| **B-21 agent-led procedures** | `content_stack/procedures/runner.py`, `content_stack/mcp/tools/runs.py` | Procedures are managed by the current agent; the daemon stores state, grants step-scoped tools, and records outputs. Per D4. |
| **M-20 idempotency**   | `content_stack/repositories/runs.py:IdempotencyKeyRepository`     | (project_id, tool_name, key) replays within 24 h short-circuit; beyond 24 h are fresh.             |
| **M-25 budget pre-emption** | `content_stack/integrations/_base.py`                        | Pre-call budget check refuses if `current_month_spend + estimated_cost > monthly_budget_usd`.       |
| **B-08 publish primary** | partial-unique index `uq_publish_targets_primary`               | Exactly one `is_primary=true` row per project; CHECK + index.                                       |
| **B-09 internal_links** | partial-unique index `uq_internal_links_unique`                  | (from, to, anchor, position) UNIQUE WHERE status != 'dismissed'.                                    |

---

## 14. Crash recovery

On daemon start, the lifespan's recovery sweep runs (see section 3
step 8):

```sql
UPDATE runs
SET status = 'aborted', error = 'daemon-restart-orphan', ended_at = now()
WHERE status = 'running'
  AND heartbeat_at < datetime('now', '-5 minutes');
```

For each aborted parent, child runs (`parent_run_id`) get the same
treatment. Then for each `runs` row whose procedure declares
`resumable: true`:

1. The sweep inspects `procedure_run_steps` for the run's last
   `success` row.
2. The next step (`status='pending'` or `'running'`) is the resume
   point.
3. The sweep does NOT auto-resume; it surfaces a "Resumable" badge
   in the UI's RunsView.
4. The operator clicks "Resume" to call
   `procedure.resume(run_id)`.

Resume policy: the current agent receives the next pending, running,
or failed step and decides whether to retry, skip, fork, or abort.
`procedure.fork(run_id, from_step)` creates a new child run for "redo
from step N onward" — used by humanizer chains.

Heartbeat: the controller updates `runs.heartbeat_at` on run open,
resume, claim, and record. Agents can also call `run.heartbeat` during
long steps.

---

## 15. Observability

### 15.1 Logging

`content_stack/logging.py` configures `structlog` for JSON output
with the format `{ts, level, logger, run_id?, project_id?, msg, kv}`.
Rotation via `RotatingFileHandler` at 10 MB × 5 to
`~/.local/state/content-stack/daemon.log`. `runs.id` is set in a
`contextvars.ContextVar` at procedure-run start so all downstream
calls inherit it without explicit threading.

### 15.2 Per-step audit trail

The three audit tables (`procedure_run_steps`, `run_steps`,
`run_step_calls`) are queryable via `/api/v1/runs/{id}` and
`run.get(run_id)` MCP. The UI's RunsView shows the step list;
clicking opens the input/output diff. Cost-of-truth lives in
`run_steps.cost_cents`, summed per run; the
`runs.metadata_json.cost.by_integration` key is denormalised for
fast UI display.

### 15.3 Health + doctor

`GET /api/v1/health` returns `{daemon_uptime_s, version, milestone,
db_status, scheduler_running, ...}`. Auth-whitelisted so
`scripts/doctor.sh` can probe before resolving the token.

`scripts/doctor.sh` performs read-only local checks per
[`../PLAN.md`](../PLAN.md): daemon up, auth token mode, seed file mode,
DB presence, alembic head, credential decryptability, scheduler health,
and optional install/runtime hints for MCP, skills, procedures, plugins, and
launchd. JSON output is `{ok, code, checks, info}`.

### 15.4 Swagger + OpenAPI

`/api/docs` serves Swagger UI;
`/api/openapi.json` serves the raw spec. Both auth-whitelisted is
NOT the case — they require the bearer token. Operators usually hit
them with `curl -H "Authorization: Bearer $(cat ~/.local/state/content-stack/auth.token)"`.
The current REST API publishes 88 OpenAPI paths.

---

## See also

- [`../PLAN.md`](../PLAN.md) — the canonical spec.
- [`./extending.md`](./extending.md) — how to add skills /
  procedures / integrations.
- [`./procedures-guide.md`](./procedures-guide.md) — PROCEDURE.md
  authoring contract.
- [`./api-keys.md`](./api-keys.md) — vendor credential setup.
- [`./security.md`](./security.md) — threat-model trade-offs.
- [`./upgrade.md`](./upgrade.md) — pipx and clone-mode upgrade
  semantics.
