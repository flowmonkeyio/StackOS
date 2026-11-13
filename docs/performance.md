# Local UI Performance

StackOS runs locally, so normal navigation should feel immediate. A local
request taking hundreds of milliseconds is a defect to investigate, not an
expected network cost.

## Evidence surfaces

| Evidence | Where to find it | What it shows |
| --- | --- | --- |
| Daemon request log | `~/.local/state/stackos/daemon.log` | `http.request.slow` for HTTP requests at or above 100 ms |
| Operation log | `~/.local/state/stackos/daemon.log` | `operation.request.slow` with the operation name and dispatcher duration |
| Desktop lifecycle log | `~/Library/Logs/StackOS/main.log` | Electron startup, updater, window, and packaged lifecycle events |
| Browser Network panel | Response headers | `Server-Timing`, `X-StackOS-Request-Duration-Ms`, and operation duration |

Slow-request logging records the method, route path, status, duration, and
response size. It deliberately excludes query values, request bodies, auth
headers, and operation arguments so credentials and user data do not leak into
logs.

Operation REST responses include two timing layers:

```text
Server-Timing: stackos-operation;dur=34, stackos-http;dur=36.8
X-StackOS-Operation-Duration-Ms: 34
X-StackOS-Request-Duration-Ms: 36.8
```

The difference between operation and HTTP duration approximates adapter and
serialization overhead. The browser's total duration also includes transfer,
parsing, and UI work.

## Investigation workflow

1. Reproduce one cold page load with the browser Network panel open.
2. Sort by duration and response size. Record request count, largest response,
   longest server timing, and time until the useful page surface appears.
3. Match requests at or above 100 ms with `http.request.slow` and
   `operation.request.slow` in the daemon log.
4. Fix broad reads, request fan-out, repeated catalog synchronization, or
   sequential dependencies before considering caches.
5. Repeat the same cold navigation in the packaged app and record before/after
   evidence in the tracker ticket.

Do not use `networkidle` as the desktop ready signal: StackOS keeps event
streams open for live state. Measure the primary useful surface and its required
requests instead.

## Current navigation budgets

- Health and local navigation shell: under 100 ms server time.
- Home's primary supervision data: under 300 ms on the normal local dataset.
- Work dependency-map index: load the complete task index with
  `tracker.get(task_index_only=true, include_graph=false)`, including aggregate
  ticket progress, then load only the selected task's graph. Do not make
  terminal work disappear to save response bytes, and do not fetch the complete
  ticket archive for the initial graph.
- Portfolio: one compact status request per active project with bounded
  concurrency; fetch only non-terminal task headers and aggregate ticket
  summaries for projects that actually have open work. Use `task_statuses` with
  the task index rather than loading full ticket rows. Open means every
  non-terminal task (`not-started` and `in-progress`), not only work already in
  progress.
- Setup may load the complete integration/action inventory. Home must not load
  that inventory during initial render or routine polling.
- The global project shell requests compact plugin navigation metadata. Full
  plugin manifests belong on catalog/setup surfaces, not every project page.

Response filtering must reduce database work as well as payload size. Tracker
list reads bulk-hydrate task, parent, dependency, reference, and link state;
never reintroduce per-ticket relation queries. Synchronous SQLite-backed reads
used by async operation routes must run outside the event-loop thread so one
large local read cannot make unrelated health, shell, or status requests queue
behind it.

These are regression budgets, not permission to make small local datasets wait
for the full budget.

## Current UI read ownership

`usageUpdates` retains one lightweight `ui_demand={"projects":{}}` transport
per mounted project/global scope. View selection changes metric demand, not
transport identity. Complete headers, counts and revisions remain available;
heavy usage is requested through `tracker.get(projection="ui")` in bounded
viewport batches. Board excludes filtered/offscreen columns and adds 160px of
vertical overscan. Selected Graph requests the visible ticket window plus its
task, within the existing 200-identity contract. A scoped response older than
the accepted revision cannot establish Live freshness.

Home's analytics and active-task readers share pending admission through
`usageReads`: scope/filter/group changes have independent identities, manual
callers join pending work, and real in-flight invalidations require one fresh
follow-up per burst. The initial stream replacement precedes the first bulk
read. An error or a one-second admission deadline releases useful reads while
freshness remains unconfirmed; reconnect/visibility changes still revalidate.
No response cache survives the last subscriber. Graph and one full primary
detail can be reused only within the mounted project and accepted revision.
Content saves and latest-content retries use the same UI-demand index queue.
While a full detail read is pending, its owner retains accepted metric patches
for that task and applies them before publishing the primary content; patches
are released when the read settles or the project scope exits.

Home connected tools use the existing `summary_only=true` account read.
Project headings reuse canonical project metadata rather than polling the
portfolio's work aggregates. Full account setup reads remain unchanged.
Task context facts appear independently of file previews. More details admits
artifact reads with at most four in flight; one failed preview preserves other
contexts and files. These local ownership checks do not replace the assembled
Home/graph/render benchmarks or final release signoff.

The mounted graph session retains one geometry-only layout at `graphModel`.
Node membership/order/grouping, dependency endpoints and fixed card dimensions
identify layout; telemetry, labels, selection and highlight expiry only project
current indexed node/edge data. No old metric snapshot is kept in the layout.
Graph metric summaries mount only for the existing viewport demand. Board
retains its mounted controls and scroll, while inactive summaries unmount.
Full graph topology, edges, keyboard selection and the minimap remain available;
Vue Flow's visible-element culling is not enabled (uninitialized nodes still
mount and it would change keyboard discovery without demonstrated necessity).

`AgentUsageSummary` prepares timestamps once for all three metric traces.
The existing feed clock advances a window only at a bucket boundary or when a
retained source observation becomes eligible. `useUsageCanvas` shares viewport,
size, theme and pixel-density observation; its event-driven paints join the
`usageUpdates` visibility owner in one animation-frame batch, without a new
timer. Offscreen summaries defer sample preparation with 160px vertical
overscan. Hidden dirty data does not invalidate unrelated visible charts.
Canonical `WorkTime` uses the same viewport/document visibility owner and the
Board/Graph metric demand. Hidden footers retain their fixed slot and last
formatted value without reading duration or the heartbeat; reveal formats the
current projection. Only visible live executions consume the shared heartbeat.
Home's existing Chart.js primitive uses this same size/paint ownership with
Chart.js animation and responsive scheduling disabled, and defers series work
until visible. Meaningful comparable geometry updates use one compositor-only
opacity fade with the shared base duration/easing; there is no canvas animation
loop. Compact traces require an active execution and fresh feed. Home consumes
freshness from its existing statistics subscription. Initial/revealed charts,
scope/metric changes and unrelated renders stay still.
Pending snapshots cancel motion while retaining the last drawing for comparison;
only a fresh accepted change may animate its replacement. Hidden/offscreen,
reduced-motion and teardown boundaries cancel finite motion. Live dots use the
same visibility owner and a restrained opacity pulse only while actual execution
and fresh connection are present; reduced motion disables the pulse. Exact
values, unknown coverage and temporal gaps are unchanged.
