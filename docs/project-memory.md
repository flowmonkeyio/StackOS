# StackOS Project Memory

Project memory is data, not judgment. StackOS stores context, learnings,
experiments, observations, decisions, metric snapshots, and event history so an
agent or human can retrieve relevant history for the next run. StackOS does not
decide which learning is true, which experiment won, or what to do next.

## Primitives

- `project_events`: append-only timeline for context snapshots, learning
  changes, experiment observations, and decisions.
- `context_index_entries`: compact pointers used by bounded context retrieval.
- `context_snapshots`: immutable records of the context selected for a run.
- `learnings`: statements supplied by agents or humans, with confidence,
  review state, tags, scope, and evidence.
- `experiments`: project-level experiment definitions that can span runs,
  providers, resources, and domains.
- `experiment_variants`: experiment arms and linked resource/artifact refs.
- `experiment_observations`: supplied metric observations for an experiment.
- `decisions`: explicit decisions supplied by an agent or human.
- `metric_snapshots`: point-in-time metric values.

These tables are the current StackOS memory sidecars. Removed
workflow-specific storage should not be treated as project memory. Any schema
cleanup that deletes stored data needs explicit signoff and backup/restore
verification.

`agent_requests` are a core inbox/queue primitive, not project memory. They can
point at resources, messages, or future trigger sources, but the queue stores
claim state and run linkage rather than durable learnings or decisions.

## Agent Retrieval

Normal agents may read bounded, sanitized context:

- `context.query`
- `context.timeline`
- `learning.query`
- `experiment.query`
- `decision.query`

Every context query requires explicit sources or uses a small default source
set. Responses enforce hard item limits, field projection, provenance, and
redaction. Agents can request fields such as `statement`, `confidence`,
`hypothesis`, `status`, or `decision`; unsupported fields fail validation.

## Write Boundary

Project memory writes are normal workflow writes only after a run plan is
started and the current running step grants the specific operation. Agents use
them to persist explicit records they authored or observed; StackOS does not
decide whether a learning is true or an experiment won.

- `context.snapshot`
- `learning.create`
- `learning.update`
- `experiment.create`
- `experiment.recordObservation`
- `experiment.recordDecision`
- `decision.record`

These writes require the active `run_token`, one claimed/running step, matching
`project_id`, and a grant in the run-plan snapshot. Bootstrap agents can read
project memory before a run, but they cannot write learnings, observations, or
decisions until execution is inside that granted run-plan boundary.

Internal StackOS producers that need append-only timeline events should use the
shared event emitter contract (`stackos.events` and
`stackos.repositories.project_events`) instead of constructing `ProjectEvent`
rows directly. The emitter is an internal Python boundary, not a public MCP,
REST, or CLI operation. It validates a typed event envelope, writes to
`project_events` through the caller's transaction, preserves source provenance,
and applies the shared redaction rules to titles, summaries, and metadata.

## Experimentation

Experiments are project-level because they often outlive one run. SEO title
tests, media-buying creative tests, GTM sequence tests, and internal process
tests all fit the same shape:

- hypothesis
- variants
- metric targets
- observations
- explicit decision records

StackOS records observations and decisions. It does not evaluate winners unless
a future plugin action stores an explicit agent/human-authored decision.

## Redaction

All JSON metadata and vendor-controlled text fields pass through the shared
redaction helpers before storage or readback. Secret-like keys and inline
assignments such as `api_key=...`, `Authorization: Bearer ...`, and
`refresh_token=...` are returned as `[redacted]`.
