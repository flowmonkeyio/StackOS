# Repository Agent Notes

Repositories are the daemon's durable-state boundary. Keep them boring,
predictable, and fast.

## Expectations

- Read [`../../docs/operations.md`](../../docs/operations.md),
  [`../../docs/action-executor.md`](../../docs/action-executor.md), and the
  domain docs for the object you are changing before altering repository
  behavior.
- Preserve operation, REST, MCP, and CLI contracts when optimizing a repository.
  Do not remove an aggregate endpoint from the model just because one UI route
  should not call it during bootstrap.
- Fix root causes inside the repository path when an aggregate read is slow:
  batch child reads, group rows in memory, preload shared project state once,
  and eliminate N+1 list calls before pushing complexity into callers.
- Keep list endpoints scoped to the minimum data a screen or operation needs.
  App-shell bootstrap should use narrow list reads; aggregate reads belong on
  pages/tools that explicitly need aggregate data.
- Treat manifest/catalog sync as setup state, not request work. Sync once per
  live engine or repository scope where possible, and make tests cover fresh
  in-memory databases so cache identity mistakes are caught.
- Do not run per-row credential, budget, policy, or availability queries inside
  large list loops. Build a request/repository-scoped context and pass it into
  row mappers.
- Keep repository outputs provider-neutral and secret-free. Repositories may
  expose credential refs, status, scopes, and diagnostics, never decoded secret
  payloads.
- Be careful with process-level caches. They must be invalidated or scoped by
  durable identity, and they must not leak rows across projects, tests, or
  database engines.
- Prefer structured SQL queries and typed row grouping over ad hoc filtering
  after repeated repository calls.
- If you change a repository flow, update operation behavior, adapters, UI
  callers, and tests together so the StackOS model remains coherent.

## Decomposition Pattern

- Keep the public repository class as the transaction and query boundary.
  Extract a long, independently testable persistence lifecycle only when its
  inputs are explicit and it uses the caller's `Session`; do not open a second
  session or commit inside the helper.
- `PluginRepository` is the reference: catalog reads and project enablement
  stay in `plugins.py`, while one-manifest persistence lives in
  `plugin_manifest_sync.py`. The repository still owns commit timing and
  engine-scoped sync idempotency.
- Preserve repository output types and public imports. Do not create mixins or
  pass-through classes just to lower a line count.

## Performance Checklist

- Measure the route/tool before changing it, ideally in browser and at the
  direct repository/API level.
- Check for duplicate bootstrap requests, aggregate calls on screens that only
  need narrow lists, repeated sync/setup work, and N+1 child queries.
- Keep aggregate endpoints available when they are part of the public StackOS
  model, but make them efficient enough for direct REST/MCP use.
- Add regression tests that fail if a narrow flow starts calling an aggregate
  endpoint again.
- Add repository tests for sync idempotency, batching, project filtering, and
  permission/auth visibility when those behaviors are performance-sensitive.
- Re-run focused tests for the repository and any UI store/view that changed.
  Use full signoff for shared repository behavior, catalog/action availability,
  or committed UI bundle changes.
