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

These tables are additive sidecars. The clean-cut migration rule is that old
Existing project data remains in place unless a signed-off schema cleanup
explicitly removes it with backup/restore verification.

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

Project memory writes are registered in the daemon and available through
local/admin REST, but they are not normal agent-facing MCP operations before the
D09 run-plan grant model:

- `context.snapshot`
- `learning.create`
- `learning.update`
- `experiment.create`
- `experiment.recordObservation`
- `experiment.recordDecision`
- `decision.record`

This keeps the current model honest: agents may propose or supply records, but
StackOS only stores them. Run-plan scoped grants will later decide which writes
an agent can perform during a specific workflow.

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
