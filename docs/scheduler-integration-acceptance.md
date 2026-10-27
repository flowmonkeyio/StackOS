# Scheduler source integration and acceptance

This candidate consolidates the reviewed worker infrastructure and adds a bounded
controller for explicitly admitted canonical tickets. It is not installed or
enabled in the daemon. Passing the deterministic scenario below is a source
acceptance gate; it does not establish native worker or unattended service readiness.

## Delivered scope

The base is `ed69afc30179784af091b7d0da3a0997990b77ef`. PR17's admission and
credential-session repair and PR18's numeric accounting validation repair are
already ancestors of that base. They are not duplicated here.

| Capability | Canonical implementation and evidence |
| --- | --- |
| Restart context | `RunRepository` exposes persisted original ownership, attempts, holds and unresolved work. Reading context does not reclaim ownership. |
| Real worker adapter and custody | `stackos/actions/codex_worker.py` uses a pinned, explicitly injected profile, command identity, bounded output, deadline and cleanup receipts. `tests/unit/test_codex_worker.py` covers these contracts without a model call. |
| Canonical admission and observation | `action.execute`, `ActionRepository` and owner operations enforce the active step, grants, approval, original attempt and fresh owner state; `tests/integration/test_codex_action_runtime.py` exercises the real control plane. |
| Accounting and completion | Unknown cost and usage remain unknown. Correlated natural completion can return a useful result; it does not prove remote termination or reusable capacity. Public output schemas and the Action Calls UI accept unknown cost. |
| Bounded selection and recovery | `AgentDispatchLoop` consumes an explicit finite allowlist through canonical tracker readiness, acquisition and action execution. It observes original receipts, persists no-ready reasons, and retains occupied capacity after uncertainty. |
| Explicit lifecycle composition | `create_app` accepts an approved profile, transport and dispatch scope as host-owned dependencies. The lifespan starts selection after action services and stops selection before command custody cleanup. Default startup has no dispatch scope and remains inert; `test_app_lifespan.py` checks both paths and ordering. |

Five previously accepted source commits are preserved by patch identity:
`ff643793`, `5de90e67`, `a3ba0fc4`, `99e67022`, and `9b07b504`.
This does not restore PR15's synthetic execution/publication bundle or create
another ownership ledger, backlog, service, or credential store.

## Required parent-absent scenario

Run `tests/integration/test_agent_dispatch.py` against the committed candidate.
Its fixture creates the isolated project, tracker items, plan, exact grants,
approval and owner bindings using canonical operations. Only the worker process
and pipe boundary is deterministic; no model, network, Docker or MongoDB is used.

The test starts the background loop once. After that, it observes persisted
state rather than calling the scheduler's selection, dispatch, observation or
escalation methods to manufacture transitions. Required outcomes are:

1. Eligible admitted work receives one original owner, attempt and action call.
2. A stalled worker produces a durable escalation naming the original evidence,
   the operator and the required reconciliation action. Unknown effects remain
   unknown; the controller performs zero automatic retries.
3. An unrelated eligible item runs using its separately admitted capacity.
   The held slot is not cleared or reused to make this happen.
4. An unfinished canonical RCA/prevention ticket remains linked to the failed
   work. The controller does not close that work or accept its own success.
5. Restart observes the original attempt and holds rather than launching again.
   Ineligible work, permission waits and exhausted capacity have recorded reasons.

Successful action output is only review-ready. The existing tracker completion
operation does not globally enforce dependency closure. This controller never
closes failed work; independent review and operator-controlled closure remain
required. A persisted escalation is not evidence that a notification reached Adi.
Rejection before owner acquisition consumes no worker slot. A later action-level
denial retains its acquired slot conservatively; unrelated work needs separately
admitted capacity. Absence of an action row does not prove a slot safe to release.

## What remains before unattended operation

These are separate gates, not claims that source helpers already run the service:

| Gate | Owner and required evidence |
| --- | --- |
| Native execution | The operator must authorize any further real call under a new bounded packet. Reconcile the original held evidence before retrying that attempt or reusing its capacity; a deterministic result cannot release it. This hold does not block separately admitted independent work. Prove useful result, original-ID readback, custody and accounting with the permitted real adapter. |
| Service activation | An authorized operator must supply the reviewed profile, existing scoped authority and explicit ticket scope to a durable host through the opt-in lifecycle input. Installation, persistent access and activation are not performed by this PR. |
| Host and scheduler failure | Prove restart, sleep/offline recovery and supervision when the loop itself stops. An uncaught control-plane error stops this loop with `last_error` in retained host memory; durable escalation is not guaranteed when authority is lost. It cannot fall back to unscoped writes. In-process deadlines are not whole-host hard preemption. |
| Notification and independent review | Resolve an approved operator route, prove delivery or explicitly retain unconfirmed delivery, and dispatch/accept independent review through its real authorized adapter before closing work. No review-agent or notification delivery is fabricated here. |
| Release signoff | Focused source checks and the generated UI build are separate from full repository signoff, hosted CI, installed/native acceptance and deployment. Report each scope independently. |

The original native attempt remains `OWNER_LOST_UNKNOWN`, with no remaining
invocation allowance or safe automatic retry. The prior 21,525-token consumption
exceeded a non-hard 16,000-token allowance. The subsequent non-hard allowance was
32,000 tokens; cumulative planning exposure is 53,525, not a hard ceiling or a
new budget. The timed-out attempt's actual usage, monetary cost, effects and
remote/descendant termination remain unknown. This source integration adds zero
native calls and does not release its capacity.
