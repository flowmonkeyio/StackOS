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
- Work dependency-map index: active work only; load a selected task's graph on
  demand. Do not fetch the complete tracker archive for the initial graph.
- Portfolio: one compact status request per active project; fetch task detail
  only for projects that actually have active work.
- Setup may load the complete integration/action inventory. Home must not load
  that inventory during initial render or routine polling.
- The global project shell requests compact plugin navigation metadata. Full
  plugin manifests belong on catalog/setup surfaces, not every project page.

These are regression budgets, not permission to make small local datasets wait
for the full budget.
