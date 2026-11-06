"""Bounded engineering recovery; run-plan metadata is the only durable ledger.

The host lock excludes local controllers, not distributed writers. Missing attempt
completion is uncertainty and never permission to replay filesystem/model effects.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from uuid import uuid4

from jsonschema import ValidationError, validate

KEY = "engineering_recovery"
POLICY = "bounded-recovery-v3"


def evidence(value, *, decode=False):
    """Encode only typed controller path maps; arbitrary dictionary keys stay guarded."""
    if isinstance(value, list):
        return [evidence(item, decode=decode) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"file_digests", "candidate_digests"} and item is not None:
            if decode:
                if (
                    not isinstance(item, list)
                    or any(
                        not isinstance(row, dict)
                        or set(row) != {"path", "digest"}
                        or not all(isinstance(row[field], str) for field in row)
                        for row in item
                    )
                    or len({row["path"] for row in item}) != len(item)
                ):
                    raise ValueError("unknown or invalid durable path digest schema; reconcile")
                item = {row["path"]: row["digest"] for row in item}
            elif isinstance(item, dict):
                item = [{"path": path, "digest": digest} for path, digest in sorted(item.items())]
            if not decode and (
                not isinstance(item, list)
                or any(
                    not isinstance(row, dict)
                    or set(row) != {"path", "digest"}
                    or not isinstance(row["path"], str)
                    or not row["path"]
                    or "\0" in row["path"]
                    or Path(row["path"]).is_absolute()
                    or ".." in Path(row["path"]).parts
                    or not isinstance(row["digest"], str)
                    or not re.fullmatch(r"(?:[0-7]+:[0-9a-f]{64}|deleted)", row["digest"])
                    for row in item
                )
            ):
                raise ValueError("invalid controller path digest evidence")
            result[key] = item
        else:
            result[key] = evidence(item, decode=decode)
    return result


@contextlib.contextmanager
def controller_lock(repo):
    try:
        import fcntl
    except ImportError as exc:
        raise ValueError("engineering delivery requires a host with POSIX file locking") from exc
    root = Path(tempfile.gettempdir()) / "stackos-delivery-locks"
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.getuid():
        raise ValueError("controller lock directory ownership mismatch")
    root.chmod(0o700)
    name = hashlib.sha256(str(repo).encode()).hexdigest()
    fd = os.open(root / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise ValueError("controller lock ownership mismatch")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another local delivery controller owns this worktree") from None
        yield
    finally:
        os.close(fd)


class Ledger:
    def __init__(self, api, project, plan, run, models):
        self.api, self.project, self.plan, self.run = api, project, plan, run
        stored = self.read()
        self.value = copy.deepcopy(
            stored
            or {
                "policy": POLICY,
                "models": models,
                "events": [],
                "episodes": [],
            }
        )
        if self.value.get("policy") != POLICY or self.value.get("models") != models:
            raise ValueError("frozen recovery policy/models do not match this invocation")
        if self.value.get("designer_hold"):
            raise ValueError(
                "designer stopped or failed; reconcile recorded decision before continuation"
            )
        pending = {}
        for event in self.value["events"]:
            if event["kind"] == "dispatch":
                pending[event["attempt_id"]] = event
            elif event["kind"] == "completion":
                pending.pop(event["attempt_id"], None)
        if pending:
            # Read canonical state and local snapshots; absent completion still cannot
            # establish process custody or external effects. No worker is relaunched.
            self.value["last_reconciliation"] = {
                "pending_attempts": list(pending),
                "at": self.api._utc(),
                "disposition": "hold; worker effects unresolved",
            }
            self.save()
            raise ValueError(
                "unfinished dispatch intent: reconcile worker and effects before continuation"
            )
        self.save()

    def read(self):
        result = self.api._call(
            "runPlan.get", {"project_id": self.project, "run_plan_id": self.plan}
        )
        if (result.get("id"), result.get("run_id"), result.get("project_id")) != (
            self.plan,
            self.run,
            self.project,
        ):
            raise ValueError("recovery plan identity mismatch")
        value = (result.get("metadata_json") or {}).get(KEY)
        if value and value.get("policy") != POLICY:
            raise ValueError("saved recovery schema requires explicit reconciliation")
        return evidence(value, decode=True)

    def save(self):
        encoded = evidence(self.value)
        desired = self.api._sanitize(encoded)
        if desired != encoded:
            raise ValueError("recovery evidence contains secret-like content")
        try:
            self.api._call(
                "runPlan.update",
                {
                    "project_id": self.project,
                    "run_plan_id": self.plan,
                    "metadata_json": {KEY: desired},
                },
            )
        except Exception:
            # Read-only reconciliation. Never retry an ambiguous/denied write.
            if json.dumps(evidence(self.read()), sort_keys=True) != json.dumps(
                desired, sort_keys=True
            ):
                raise

    def append(self, kind, **facts):
        self.value["events"].append({"kind": kind, "at": self.api._utc(), **facts})
        self.save()

    def dispatch(
        self, role, model, prompt, repo, seconds, *, resume="", review_state=None, attempt_id=None
    ):
        attempt = attempt_id or uuid4().hex
        before = self.api._file_digests(repo, self.api._changed_paths(repo))
        self.append(
            "dispatch",
            attempt_id=attempt,
            role=role,
            model=model,
            prompt=prompt,
            resume_thread=resume,
            file_digests=before,
        )
        worker_state = None
        if role != "reviewer":
            sessions = self.value.setdefault("worker_sessions", {})
            try:
                if resume:
                    stored = sessions.get(resume)
                    if not stored or stored["model"] != model:
                        raise ValueError(
                            "original worker host/model unavailable; exact resume required"
                        )
                    worker_state = Path(stored["state"])
                    if not (worker_state / "host").is_dir():
                        raise ValueError("original private host unavailable; exact resume required")
                else:
                    worker_state = Path(tempfile.mkdtemp(prefix="stackos-controlled-worker-"))
                    (worker_state / "host").mkdir(mode=0o700)
                self.api._outcome_snapshot(repo, worker_state / "outcome", worker=True)
            except Exception as exc:
                receipt = {
                    "role": role,
                    "status": "held",
                    "dispatch_state": "not_started",
                    "error": self.api._sanitize(str(exc)),
                    "custody": {"worker_launched": False},
                    "effect_disposition": {
                        "state": "settled",
                        "basis": "controller pre-dispatch; no worker launched",
                    },
                }
                self.append("completion", attempt_id=attempt, role=role, receipt=receipt)
                return receipt
            self.append(
                "worker_host_prepared",
                attempt_id=attempt,
                role=role,
                state=str(worker_state),
                resume_thread=resume,
                model=model,
            )
        # A crash without a durable completion remains unresolved, never inferred safe.
        receipt = self.api._run_role(
            role,
            model,
            prompt,
            repo,
            seconds,
            self.run,
            resume_thread=resume,
            review_state=review_state,
            worker_state=worker_state,
        )
        if worker_state and receipt.get("thread_id"):
            self.value["worker_sessions"][receipt["thread_id"]] = {
                "state": str(worker_state),
                "model": model,
                "role": role,
            }
        after = self.api._file_digests(repo, self.api._changed_paths(repo))
        self.value["candidate_digests"] = after
        self.append(
            "completion", attempt_id=attempt, role=role, receipt=receipt, file_digests=after
        )
        if resume and receipt.get("thread_id") and receipt.get("thread_id") != resume:
            raise ValueError(
                "literal thread resume failed; identity/effects require reconciliation"
            )
        return receipt

    def archive_reopen(self, step, reason):
        # Preserve all affected results before reopen clears canonical step results.
        snapshots = [
            self.api._receipt_step(self.project, self.plan, self.run, key)
            for key in (*[key for key, _ in self.api.ROLE_STEPS], "handoff")
        ]
        self.append("reopen_intent", step=step, reason=reason, steps=snapshots)
        result = self.api._call(
            "runPlan.reopen",
            {
                "project_id": self.project,
                "run_plan_id": self.plan,
                "step_id": step,
                "reason": reason,
                "actor": "stackos.delivery.run",
            },
        )
        # No retry on denial or transport uncertainty; the intent remains inspectable.
        if int(result.get("run_id", -1)) != self.run:
            raise ValueError("reopen changed run identity")
        self.append("reopened", step=step)
        return str(result["run_token"])

    def episode(self, decision, failure, *, validate_only=False):
        """The designer owns semantic attribution; evidence and budget identity persist."""
        episodes = self.value["episodes"]
        comparisons = decision.get("comparisons")
        known = {item["id"] for item in episodes}
        valid_refs = {
            event["failure_id"] for event in self.value["events"] if event["kind"] == "failure"
        }
        refs = decision.get("evidence_refs", [])
        if (
            not str(decision.get("cause", "")).strip()
            or not refs
            or failure not in refs
            or not set(refs) <= valid_refs
            or not isinstance(comparisons, list)
            or len(comparisons) != len(known)
            or {item.get("episode_id") for item in comparisons} != known
        ):
            raise ValueError("failure classification lacks grounded identity/comparisons")
        same = []
        for item in comparisons:
            prior = next(row for row in episodes if row["id"] == item["episode_id"])
            cited = item.get("evidence_refs", [])
            if (
                item.get("relation") not in {"same", "distinct"}
                or not str(item.get("rationale", "")).strip()
                or failure not in cited
                or not set(cited) <= valid_refs
                or not set(cited).intersection(prior["evidence_refs"])
            ):
                raise ValueError("failure recurrence remains uncertain; no new allowance")
            if item["relation"] == "same":
                same.append(prior)
        if len(same) > 1:
            raise ValueError("failure spans multiple episodes; split/reconcile before retry")
        if validate_only:
            return None
        if same:
            episode = same[0]
            episode["evidence_refs"] = sorted(set(episode["evidence_refs"] + refs))
        else:
            episode = {
                "id": uuid4().hex,
                "cause": decision["cause"],
                "classification": decision["classification"],
                "evidence_refs": refs,
                "resolved": False,
            }
            episodes.append(episode)
        return episode

    def allowance(self, episode):
        consumed = [
            event["allowance"]
            for event in self.value["events"]
            if event["kind"] == "consume" and event["episode"] == episode["id"]
        ]
        if "final" in consumed:
            return "stop"
        return "final" if "retry" in consumed else "retry"

    def consume(self, episode, allowance, route):
        if allowance != self.allowance(episode) or allowance == "stop":
            raise ValueError("recovery budget exhausted or changed")
        self.append("consume", episode=episode["id"], allowance=allowance, route=route)


def stages(
    api,
    *,
    project_id,
    plan_id,
    run_id,
    token,
    repo,
    head,
    task,
    models,
    role_guidance,
    role_seconds,
    max_code_bytes,
    review_state,
):
    """Run/recover the existing plan. Reviewers never receive recovery context."""
    ledger = Ledger(api, project_id, plan_id, run_id, models)
    expected_candidate = ledger.value.get("candidate_digests")
    candidate_matches = (
        expected_candidate is None
        or api._file_digests(repo, api._changed_paths(repo)) == expected_candidate
    )
    handoff_checkpoint = evidence(
        api._receipt_step(project_id, plan_id, run_id, "handoff"), decode=True
    )
    if not candidate_matches and handoff_checkpoint.get("status") != "blocked":
        raise ValueError(
            "candidate differs from last durable worker/retirement "
            "checkpoint; reconcile before resume"
        )
    seen, threads = set(), {}
    plan_text = code = code_hash = reviewer_thread = ""
    before_tests, after = {}, {}
    index, feedback, resume = 0, "", ""
    # Reopening clears receipts, but their archived attempts remain authoritative
    # for thread lineage and design context on controller reinvocation.
    for event in ledger.value["events"]:
        prior = event.get("receipt") or {}
        role = event.get("role")
        if (
            event["kind"] == "completion"
            and role in models
            and isinstance(prior.get("thread_id"), str)
        ):
            threads[role] = prior["thread_id"]
            seen.add(prior["thread_id"])
            if role == "planner" and (prior.get("answer") or {}).get("status") == "complete":
                plan_text = prior["answer"].get("plan", plan_text)

    def recover(role, step, reason, receipt, answer, *, effects_safe=False):
        nonlocal token, feedback, index, resume, plan_text
        if denied(receipt):
            ledger.append("denied", role=role, receipt=receipt)
            raise ValueError("authority denial; recovery cannot bypass it")
        fingerprint = hashlib.sha256(
            json.dumps(
                evidence({"role": role, "reason": reason, "receipt": receipt}), sort_keys=True
            ).encode()
        ).hexdigest()
        cases = ledger.value.setdefault("recovery_cases", {})
        previous = ledger.value.get("recovery_case")
        if previous:
            cases.setdefault(previous["fingerprint"], previous)
        case = cases.get(fingerprint)
        if not case or case["fingerprint"] != fingerprint:
            failure_id = uuid4().hex
            case = {"fingerprint": fingerprint, "failure_id": failure_id}
            cases[fingerprint] = case
            ledger.value["recovery_case"] = case
            ledger.append(
                "failure",
                failure_id=failure_id,
                role=role,
                reason=reason,
                receipt=receipt,
                effects_safe=effects_safe,
                authority_disposition=authority_disposition(receipt),
                file_digests=api._file_digests(repo, api._changed_paths(repo)),
            )
        failure_id = case["failure_id"]
        prior_workers = [
            event
            for event in ledger.value["events"]
            if event["kind"] == "completion" and event.get("role") in {"planner", "designer"}
        ]
        unresolved = [
            event.get("attempt_id")
            for event in prior_workers
            if not event_effects_safe(event)
            or authority_disposition(event.get("receipt") or {}) != "clear"
        ]
        if unresolved:
            ledger.append("route_reconciliation_hold", attempts=unresolved, failure_id=failure_id)
            raise ValueError(
                "original designer effects or authority unresolved; triage "
                "cannot resume that worker"
            )
        if not threads.get("planner"):
            raise ValueError("original designer thread unavailable; reconcile before triage")

        def response_error(decision):
            try:
                validate(decision, api._schema("designer"))
                ledger.episode(decision, failure_id, validate_only=True)
                if decision["classification"] == "design" and decision["decision"] != "revise":
                    return "design defect requires revise"
                if decision["decision"] == "revise" and not decision["plan"].strip():
                    return "revision requires a nonempty plan"
            except (ValidationError, ValueError) as exc:
                return str(exc)
            return ""

        # Revalidate saved responses without changing episode identity or budgets.
        # Their original receipts remain in the append-only decision events.
        if "decision" not in case or (
            "episode_id" not in case and response_error(case["decision"])
        ):
            failures = [event for event in ledger.value["events"] if event["kind"] == "failure"]
            episode_context = [
                {**item, "remaining_allowance": ledger.allowance(item)}
                for item in ledger.value["episodes"]
            ]
            triage_prompt = (
                f"Original requirements:\n{task}\n\nCurrent design:\n{plan_text}\n"
                f"Current failure ID: {failure_id}. Controller effects settled: {effects_safe}.\n"
                f"Durable failure evidence:\n{json.dumps(evidence(failures))}\n"
                "Existing semantic episodes and remaining budgets:\n"
                f"{json.dumps(episode_context)}\n"
                "Inspect relevant source read-only. Classify this failure as design or execution. "
                "Give its concrete root cause. Every evidence_refs entry must be an EXACT bare "
                "failure_id from Durable failure evidence, including the current failure ID. "
                "Do not append event names or use paths, artifact digests or episode IDs as "
                "evidence_refs; explain those details in cause/rationale instead. "
                "Compare it with EVERY prior "
                "episode using both current and prior evidence IDs, marking same, distinct, or "
                "uncertain with a causal explanation. A phase, changed wording, revised plan, "
                "replacement agent or symptom change NEVER proves a new failure. Only a "
                "demonstrably unrelated root cause is distinct; uncertainty must stop. A "
                "recurrence keeps its original budget even after apparent resolution. "
                "Choose stop, revise, or try_again. Design defects require revise and a plan. "
                "Each failure gets one retry; a recurrence then gets only ONE designer-authorized "
                "final attempt, and no more. For unresolved effects, reconcile read-only and "
                "choose stop; stdout, no observed tools and unchanged files do not prove absence "
                "of external effects. Set authority=clear only when actual host and command "
                "diagnostics show no unresolved refusal; denied or uncertain authority must stop. "
                "Never replay commands or edit files or run/write tests. "
                "Return the recovery JSON schema."
            )
            attempts = case.setdefault("triage_attempts", [])
            if "decision" in case and not attempts:
                raise ValueError("saved decision lacks triage attempt identity; reconcile")
            while True:
                if attempts:
                    completed = [
                        event
                        for event in ledger.value["events"]
                        if event["kind"] == "completion" and event.get("attempt_id") == attempts[-1]
                    ]
                    if len(completed) != 1:
                        raise ValueError(
                            "triage intent lacks settled completion; reconcile before resume"
                        )
                    designer = completed[0]["receipt"]
                    # Retry failed execution/response processing only after positive
                    # custody, authority and literal model/session proof. A valid
                    # substantive decision (including STOP) is never retried.
                    designer_answer = designer.get("answer")
                    error = response_error(designer_answer)
                    comparisons = (
                        designer_answer.get("comparisons", [])
                        if isinstance(designer_answer, dict) else []
                    )
                    relations = [
                        item.get("relation") for item in comparisons if isinstance(item, dict)
                    ] if isinstance(comparisons, list) else []
                    explicit_hold = isinstance(designer_answer, dict) and (
                        designer_answer.get("decision") == "stop"
                        or designer_answer.get("authority") in {"denied", "uncertain"}
                        or "uncertain" in relations
                        or relations.count("same") > 1
                    )
                    identity = [
                        event
                        for event in designer.get("events") or []
                        if event.get("type") == "model.resolved"
                    ]
                    retryable = (
                        bool(error)
                        and not explicit_hold
                        and role_effects_safe(designer)
                        and authority_disposition(designer) == "clear"
                        and designer.get("requested_model") == models["planner"]
                        and len(identity) == 1
                        and identity[0].get("requested_model") == models["planner"]
                        and identity[0].get("resolved_model") == models["planner"]
                        and identity[0].get("thread_id") == threads["planner"]
                        and not any(
                            event.get("type") == "model.mismatch"
                            for event in designer.get("events") or []
                        )
                        and designer.get("thread_id") == threads["planner"]
                    )
                    if not retryable or len(attempts) >= 2:
                        break
                    ledger.append(
                        "designer_response_failure",
                        failure_id=failure_id,
                        attempt_id=attempts[-1],
                        error=error,
                        receipt=designer,
                    )
                    triage_prompt += (
                        "\nYour prior response failed the recovery contract: " + error
                        + "\nCorrect that response once in this same session. Keep the original "
                        "failure identity and budgets; do not reinterpret a hold as permission."
                    )
                # Persist the cycle identity and consumed auxiliary allowance
                # BEFORE dispatch. Interrupted intents cannot gain another turn.
                attempt = uuid4().hex
                attempts.append(attempt)
                ledger.save()
                designer = ledger.dispatch(
                    "designer",
                    models["planner"],
                    triage_prompt,
                    repo,
                    role_seconds,
                    resume=threads["planner"],
                    attempt_id=attempt,
                )
            decision = designer.get("answer") or {}
            case["decision"], case["designer_receipt"] = decision, designer
            ledger.append("decision", failure_id=failure_id, receipt=designer)
        else:
            decision, designer = case["decision"], case["designer_receipt"]
        if (
            authority_disposition(designer) != "clear"
            or authority_disposition(receipt) != "clear"
            or not role_effects_safe(designer)
            or designer.get("status") != "complete"
            or not effects_safe
            or decision.get("authority") != "clear"
            or decision.get("decision") not in {"revise", "try_again"}
            or decision.get("classification") not in {"design", "execution"}
            or ("episode_id" not in case and response_error(decision))
        ):
            ledger.value["designer_hold"] = {
                "reason": "designer stopped, effects/authority unresolved, or invalid decision",
                "authority_disposition": authority_disposition(receipt),
                "designer_authority_disposition": authority_disposition(designer),
                "failure_id": failure_id,
                "receipt": designer,
            }
            ledger.save()
            raise ValueError(
                "recovery held after read-only designer triage; operator reconciliation required"
            )
        if decision["classification"] == "design" and decision["decision"] != "revise":
            raise ValueError("design defect requires a revised plan")
        if decision["decision"] == "revise" and not str(decision.get("plan", "")).strip():
            raise ValueError("designer revision omitted its plan")
        if "episode_id" in case:
            episode = next(
                item for item in ledger.value["episodes"] if item["id"] == case["episode_id"]
            )
        else:
            episode = ledger.episode(decision, failure_id)
            case["episode_id"] = episode["id"]
            ledger.save()
        allowance = ledger.allowance(episode)
        if allowance == "stop":
            ledger.append("exhausted", episode=episode["id"], reason=reason)
            raise ValueError("final recovery attempt failed; stop and ask the operator")
        unresolved = [
            event.get("attempt_id")
            for event in ledger.value["events"]
            if event["kind"] == "completion"
            and (
                not event_effects_safe(event)
                or authority_disposition(
                    event.get("receipt") or {"observations": event.get("receipts") or []}
                )
                != "clear"
            )
        ]
        if unresolved:
            ledger.append("route_reconciliation_hold", attempts=unresolved, failure_id=failure_id)
            raise ValueError(
                "route history has unresolved effects or authority; allowance remains unconsumed"
            )
        # A settled response failure does not invalidate approval. A valid
        # test report still follows the existing implementation-failure route.
        test_response_failure = False
        if role == "test_writer" and receipt.get("status") in {"complete", "held"}:
            try:
                validate(receipt.get("answer"), api._schema("test_writer"))
            except ValidationError:
                test_response_failure = True
        if test_response_failure:
            identity = [
                event for event in receipt.get("events") or []
                if event.get("type") == "model.resolved"
            ]
            if (
                not threads.get("test_writer")
                or receipt.get("thread_id") != threads["test_writer"]
                or receipt.get("requested_model") != models["test_writer"]
                or receipt.get("resolved_model") != models["test_writer"]
                or len(identity) != 1
                or identity[0].get("thread_id") != threads["test_writer"]
                or identity[0].get("requested_model") != models["test_writer"]
                or identity[0].get("resolved_model") != models["test_writer"]
                or any(
                    event.get("type") == "model.mismatch"
                    for event in receipt.get("events") or []
                )
            ):
                raise ValueError(
                    "test response failure lacks exact model/session identity; reconcile"
                )
        current = api._file_digests(repo, api._changed_paths(repo))
        review = evidence(
            api._receipt_step(project_id, plan_id, run_id, "review-task"), decode=True
        ) if role == "test_writer" else {}
        approved_review = (review.get("result_json") or {}).get(api.OUTPUT_KEYS["reviewer"]) or {}
        retry_test_writer = (
            role == "test_writer"
            and decision["classification"] == "execution"
            and decision["decision"] == "try_again"
            and effects_safe
            and test_response_failure
            and review.get("status") == "success"
            and approved_review.get("verdict") == "approve"
            and approved_review.get("code_sha256") == code_hash
            and bool(code_hash)
            and all(current.get(path) == digest for path, digest in before_tests.items())
            and all(api._test_path(path) for path in set(current) - set(before_tests))
        )
        if role in {"test_writer", "handoff"} and not retry_test_writer:
            archive_tests(api, ledger, repo, before_tests)
        route = (
            "plan-task"
            if decision["decision"] == "revise"
            else step if retry_test_writer
            else ("implement-task" if role in {"reviewer", "test_writer", "handoff"} else step)
        )
        feedback = "Recovery feedback (same original task and scope):\n" + json.dumps(
            {"reason": reason, "findings": answer, "decision": decision}
        )
        index = [key for key, _ in api.ROLE_STEPS].index(route)
        next_role = api.ROLE_STEPS[index][1]
        if decision["decision"] == "revise":
            feedback += "\nUse this revised design:\n" + decision["plan"]
        # Design feedback always returns to the literal designer thread. An
        # execution recurrence changes only the executor identity, not the flow.
        resume = (
            threads.get(next_role, "") if next_role == "planner" or allowance == "retry" else ""
        )
        ledger.value["continuation"] = {
            "step": route,
            "feedback": feedback,
            "resume_thread": resume,
            "ready": False,
            "episode_id": episode["id"],
            "replace_executor": allowance == "final",
            "allowance": allowance,
        }
        # Allowance and continuation intent are one durable write. A crash in
        # reopen holds instead of spending another allowance or replaying.
        ledger.consume(episode, allowance, route)
        ledger.append(
            "route",
            episode=episode["id"],
            allowance=allowance,
            role=next_role,
            prior_thread=threads.get(next_role, ""),
            resume_thread=resume,
            replacement=not bool(resume),
        )
        token = ledger.archive_reopen(route, reason)
        ledger.value["continuation"]["ready"] = True
        ledger.save()

    # Rehydrate canonical receipts before admitting any worker or handoff replay.
    persisted = [
        evidence(api._receipt_step(project_id, plan_id, run_id, step), decode=True)
        for step, _ in api.ROLE_STEPS
    ]
    for pos, ((_step, role), saved) in enumerate(zip(api.ROLE_STEPS, persisted, strict=False)):
        if saved.get("status") != "success":
            index = pos
            break
        prior = (saved.get("result_json") or {}).get(api.OUTPUT_KEYS[role]) or {}
        thread = prior.get("thread_id")
        if not isinstance(thread, str):
            raise ValueError("saved success lacks a role identity")
        seen.add(thread)
        threads[role] = thread
        if role == "planner":
            plan_text = prior["answer"]["plan"]
        elif role == "executor":
            before_tests = prior["file_digests"]
            code, _current_hash, _ = api._actual_code(repo, max_bytes=max_code_bytes)
            code_hash = prior["code_sha256"]
        elif role == "reviewer":
            reviewer_thread = thread
            if not (prior.get("outcome_manifest") or {}).get("sha256"):
                raise ValueError(
                    "saved review lacks a controller outcome manifest; reconcile before continuing"
                )
            stored_review = ledger.value.get("review_state")
            if not stored_review or not Path(stored_review).is_dir():
                raise ValueError("saved reviewer host unavailable; reconcile before fresh review")
            review_state = Path(stored_review)
        else:
            after = prior["file_digests"]
        index = pos + 1
    continuation = ledger.value.get("continuation")
    if continuation:
        if not continuation.get("ready"):
            raise ValueError(
                "reopen disposition uncertain; inspect canonical steps before continuation"
            )
        target = [key for key, _ in api.ROLE_STEPS].index(continuation["step"])
        status = persisted[target].get("status")
        if status == "running":
            raise ValueError("continuation already dispatched; reconcile before any replay")
        if status == "success" and api.ROLE_STEPS[target][1] == "planner":
            continuation.update(
                step="implement-task",
                resume_thread=(
                    "" if continuation["replace_executor"] else threads.get("executor", "")
                ),
            )
            target += 1
            status = persisted[target].get("status")
        if status == "pending":
            index = target
            resume, feedback = continuation["resume_thread"], continuation["feedback"]
        elif status in {"success", "blocked"}:
            # Its consumed attempt has a receipt. A blocked result needs fresh
            # classification under the existing cap, never the old dispatch intent.
            ledger.value.pop("continuation")
            continuation = None
        else:
            raise ValueError("continuation checkpoint is not replayable")
        ledger.save()
    if not continuation and index == len(persisted):
        handoff = handoff_checkpoint
        if handoff.get("status") == "success":
            return {"handoff_done": True}
        if handoff.get("status") == "running":
            raise ValueError(
                "running handoff requires observation/effect reconciliation; no replay"
            )
        if handoff.get("status") == "blocked":
            saved = handoff.get("result_json") or {}
            if not saved.get("observations"):
                raise ValueError(
                    "blocked handoff has no effect evidence; reconcile before continuing"
                )
            safe = candidate_matches and handoff_effects_safe(saved)
            ledger.append(
                "outcome_reconciliation",
                effects_settled=safe,
                prior_result=saved,
                file_digests=api._file_digests(repo, api._changed_paths(repo)),
            )
            recover(
                "handoff",
                "handoff",
                saved.get("reason", "handoff blocked"),
                saved,
                (saved.get("outcome_review") or {}).get("answer", {}),
                effects_safe=safe,
            )
    elif (
        not continuation and index < len(persisted) and persisted[index].get("status") == "blocked"
    ):
        saved = persisted[index].get("result_json") or {}
        receipt = saved.get("receipt") or {}
        if isinstance(receipt.get("thread_id"), str):
            threads[api.ROLE_STEPS[index][1]] = receipt["thread_id"]
        recover(
            api.ROLE_STEPS[index][1],
            api.ROLE_STEPS[index][0],
            saved.get("reason", "previous attempt blocked"),
            receipt,
            receipt.get("answer", {}),
            effects_safe=candidate_matches and role_effects_safe(receipt),
        )
    elif (
        not continuation and index < len(persisted) and persisted[index].get("status") == "running"
    ):
        raise ValueError(
            "running checkpoint requires worker/effect reconciliation before continuation"
        )
    while index < len(api.ROLE_STEPS):
        step, role = api.ROLE_STEPS[index]
        api._call(
            "runPlan.claimStep",
            {
                "project_id": project_id,
                "run_plan_id": plan_id,
                "step_id": step,
                "claimed_by": "stackos.delivery.run",
                "run_token": token,
            },
        )
        event_start = len(ledger.value["events"])
        prompt = ""
        try:
            prompt = api._prompt(role, task, plan=plan_text, code=code)
            if role != "reviewer":
                prompt += (
                    "\nResolved role contract:\n"
                    + json.dumps(role_guidance[role])
                    + "\nRead AGENTS.md and routed project guidance. Do not commit, publish, "
                    "or deploy. Do not write or run tests before independent approval. "
                    "Do not access host role logs.\n" + feedback
                )
            else:
                # A genuinely fresh private host and thread for every candidate review.
                if not resume:
                    review_state = Path(tempfile.mkdtemp(prefix="stackos-outcome-review-"))
                    (review_state / "host").mkdir(mode=0o700)
                elif review_state is None:
                    review_state = Path(ledger.value["review_state"])
                api._outcome_snapshot(repo, review_state / "outcome")
                ledger.value["review_state"] = str(review_state)
                ledger.save()
            api._assert_base(repo, head)
            receipt = ledger.dispatch(
                role,
                models[role],
                prompt,
                repo,
                role_seconds,
                resume=resume,
                review_state=review_state if role == "reviewer" else None,
            )
        except Exception as exc:
            launched = any(
                event["kind"] == "dispatch" for event in ledger.value["events"][event_start:]
            )
            receipt = {
                "role": role,
                "error": api._sanitize(str(exc)),
                "prompt": prompt,
                "base_commit": head,
                "candidate_code_sha256": code_hash,
                "custody": {
                    "worker_launched": None if launched else False,
                    "scope": "unresolved dispatch" if launched else "controller preparation only",
                },
                "dispatch_state": "uncertain" if launched else "not_started",
                "effect_disposition": {
                    "state": "unknown" if launched else "settled",
                    "basis": "unresolved dispatch intent"
                    if launched
                    else "controller pre-dispatch; no worker launched",
                },
            }
            reason = (
                "worker dispatch unresolved"
                if launched
                else "role preparation failed before dispatch"
            )
            ledger.append("preparation_failure", role=role, receipt=receipt)
            api._record(
                project_id,
                plan_id,
                token,
                step,
                "blocked",
                {"reason": reason, "receipt": receipt, "outcome_unknown": launched},
                run_id=run_id,
            )
            if launched:
                raise ValueError(
                    "dispatch intent unresolved; reconcile before any worker launch"
                ) from exc
            # Source-integrity changes remain a hard hold even though no worker
            # was launched. Actual refusals are never recovered around.
            api._assert_base(repo, head)
            recover(role, step, reason, receipt, {}, effects_safe=role_effects_safe(receipt))
            continue
        api._assert_base(repo, head)
        thread = receipt.get("thread_id")
        if not isinstance(thread, str) and role_effects_safe(receipt):
            api._record(
                project_id,
                plan_id,
                token,
                step,
                "blocked",
                {
                    "reason": "worker identity unavailable with settled effects",
                    "receipt": receipt,
                    "outcome_unknown": False,
                },
                run_id=run_id,
            )
            recover(role, step, "worker identity unavailable", receipt, {}, effects_safe=True)
            continue
        if not isinstance(thread, str) or (thread in seen and thread != resume):
            api._record(
                project_id,
                plan_id,
                token,
                step,
                "blocked",
                {
                    "reason": "worker identity is missing or unexpectedly reused",
                    "receipt": receipt,
                    "outcome_unknown": True,
                },
                run_id=run_id,
            )
            raise ValueError(
                "worker identity is missing or unexpectedly reused; reconcile before replay"
            )
        seen.add(thread)
        threads[role] = thread
        answer = receipt.get("answer")
        reason = ""
        if denied(receipt):
            ledger.append("denied", role=role, receipt=receipt)
            api._record(
                project_id,
                plan_id,
                token,
                step,
                "blocked",
                {
                    "reason": "worker reported authority denial",
                    "receipt": receipt,
                    "outcome_unknown": True,
                },
                run_id=run_id,
            )
            raise ValueError("authority denial; no retry or replacement bypass")
        invalid_test_response = False
        if role == "test_writer":
            try:
                validate(answer, api._schema("test_writer"))
            except ValidationError:
                invalid_test_response = True
        if (
            receipt.get("status") != "complete"
            or not isinstance(answer, dict)
            or invalid_test_response
        ):
            # Missing events and unchanged files cannot establish external effect safety.
            ledger.append(
                "reconciliation",
                role=role,
                receipt=receipt,
                file_digests=api._file_digests(repo, api._changed_paths(repo)),
                effects_settled=role_effects_safe(receipt),
            )
            api._record(
                project_id,
                plan_id,
                token,
                step,
                "blocked",
                {
                    "reason": "worker response failed",
                    "receipt": receipt,
                    "outcome_unknown": not role_effects_safe(receipt),
                },
                run_id=run_id,
            )
            recover(
                role,
                step,
                "worker response failed",
                receipt,
                {},
                effects_safe=role_effects_safe(receipt),
            )
            continue
        if role == "planner":
            if answer.get("status") == "needs_clarification":
                reason = str(answer.get("question") or "requirements need clarification")
                api._record(
                    project_id,
                    plan_id,
                    token,
                    step,
                    "blocked",
                    {"reason": reason, "receipt": receipt},
                    run_id=run_id,
                )
                raise ValueError(reason)
            if answer.get("status") != "complete" or not str(answer.get("plan", "")).strip():
                reason = "planner returned incomplete design"
            else:
                plan_text = answer["plan"]
        elif role == "executor":
            if answer.get("status") != "complete":
                reason = "executor reported incomplete implementation"
            else:
                try:
                    code, code_hash, before_tests = api._actual_code(repo, max_bytes=max_code_bytes)
                except ValueError as exc:
                    reason = str(exc)
                    before_tests = api._file_digests(repo, api._changed_paths(repo))
                if any(api._test_path(path) for path in before_tests):
                    reason = "executor introduced tests before independent approval"
                receipt.update(
                    code_sha256=code_hash,
                    changed_paths=sorted(before_tests),
                    file_digests=before_tests,
                )
        elif role == "reviewer":
            reviewer_thread = thread
            if api._file_digests(repo, api._changed_paths(repo)) != before_tests:
                raise ValueError("candidate changed during review; reconcile before continuation")
            if answer.get("verdict") != "approve" or any(
                answer.get(key)
                for key in ("findings", "missing_requirements", "irrelevant_changes")
            ):
                reason = "independent reviewer rejected candidate"
            receipt.update(verdict=answer.get("verdict"), code_sha256=code_hash)
        else:
            after = api._file_digests(repo, api._changed_paths(repo))
            if any(after.get(path) != digest for path, digest in before_tests.items()):
                reason = "test writer changed approved implementation"
            elif any(not api._test_path(path) for path in set(after) - set(before_tests)):
                reason = "test writer added unrelated files"
            elif answer.get("status") != "complete" or not any(
                api._test_path(path) for path in set(after) - set(before_tests)
            ):
                reason = "test writer did not complete relevant tests"
            receipt.update(changed_paths=sorted(after), file_digests=after)
        receipt.update(prompt=prompt)
        if not reason:
            api._record(
                project_id,
                plan_id,
                token,
                step,
                "success",
                {api.OUTPUT_KEYS[role]: receipt},
                run_id=run_id,
            )
            continuation = ledger.value.get("continuation")
            index += 1
            resume, feedback = "", ""
            if (
                continuation
                and index < len(api.ROLE_STEPS)
                and api.ROLE_STEPS[index][1] == "executor"
            ):
                resume = "" if continuation["replace_executor"] else threads.get("executor", "")
                feedback = continuation["feedback"]
                continuation.update(step="implement-task", resume_thread=resume)
            else:
                ledger.value.pop("continuation", None)
            ledger.save()
            continue
        api._record(
            project_id,
            plan_id,
            token,
            step,
            "blocked",
            {
                "reason": reason,
                "receipt": receipt,
                "outcome_unknown": not role_effects_safe(receipt),
            },
            run_id=run_id,
        )
        recover(role, step, reason, receipt, answer, effects_safe=role_effects_safe(receipt))
    return dict(
        token=token,
        seen=seen,
        code_hash=code_hash,
        before_tests=before_tests,
        after=after,
        review_state=review_state,
        reviewer_thread=reviewer_thread,
        ledger=ledger,
    )


def denied(receipt):
    """Inspect actual host refusal diagnostics, never prompts/code/tool stdout."""
    codes = {
        "approval_denied",
        "authorization_denied",
        "permission_denied",
        "sandbox_denied",
        "approval_rejected",
        "policy_denied",
    }

    def refusal(error):
        if isinstance(error, dict):
            return (
                error.get("code") in codes
                or error.get("type") in codes
                or error.get("authorization_denied") is True
            )
        if isinstance(error, str):
            # Compatibility only for the host's top-level diagnostic field.
            text = error.casefold()
            return any(
                marker in text
                for marker in (
                    "approval denied",
                    "authorization denied",
                    "auto-review rejected",
                    "approval rejection",
                    "sandbox denied",
                )
            )
        return False

    if (
        receipt.get("host_refusal") is True
        or receipt.get("authorization_denied") is True
        or refusal(receipt.get("error"))
    ):
        return True
    # A single raw host event is also accepted at capture, before sanitization.
    for event in [receipt, *(receipt.get("events") or [])]:
        if not isinstance(event, dict):
            continue
        if (
            event.get("host_refusal") is True
            or refusal(event.get("error"))
            or event.get("type") in codes
        ):
            return True
        item = event.get("item")
        if isinstance(item, dict) and (
            refusal(item.get("error"))
            or item.get("authorization_status") in {"denied", "rejected"}
            or (
                item.get("type")
                in {"commandExecution", "fileChange", "command_execution", "file_change"}
                and item.get("status") == "declined"
            )
        ):
            return True
    if isinstance(receipt.get("outcome_review"), dict) and denied(receipt["outcome_review"]):
        return True
    return any(denied(item) for item in receipt.get("observations") or [] if isinstance(item, dict))


def authority_disposition(receipt):
    """Host/controller evidence only; effect custody and model assertions grant no authority.

    Unclassified RPC rejections remain unknown, including before turn dispatch.
    Ordinary nonzero command exits are not denials. No RPC numeric-code guesses.
    """
    if denied(receipt):
        return "denied"
    events = receipt.get("events") or []
    if receipt.get("refusal_state") in {"unknown", "unclassified_host_error"} or any(
        event.get("type") == "host.error"
        or event.get("refusal_state") in {"unknown", "unclassified_host_error"}
        for event in events
    ):
        return "unknown"
    children = list(receipt.get("observations") or [])
    if isinstance(receipt.get("outcome_review"), dict):
        children.append(receipt["outcome_review"])
    if children:
        return (
            "clear"
            if all(authority_disposition(item) == "clear" for item in children)
            else "unknown"
        )
    if receipt.get("refusal_state") == "no_refusal_reported":
        return "clear"
    if (
        receipt.get("dispatch_state") == "not_started"
        and (receipt.get("custody") or {}).get("worker_launched") is False
        and (receipt.get("effect_disposition") or {}).get("basis")
        == "controller pre-dispatch; no worker launched"
    ):
        return "clear"
    if any(
        event.get("type") == "turn.completed"
        or (
            event.get("type") == "worker.local_failure"
            and event.get("failure_kind") == "workspace_read_validation"
            and event.get("refusal_state") == "no_refusal_reported"
        )
        for event in events
    ):
        return "clear"
    return "unknown"


def role_effect_disposition(role, receipt):
    """Controller-owned scope evidence; semantic/transport success is insufficient.

    Ordinary workers need admitted scope and closed synchronous broker custody.
    Completion text, file events, or unchanged files never establish effect safety.
    """
    unknown = {"state": "unknown", "basis": "no positive effect reconciliation"}
    if denied(receipt):
        return unknown
    if role != "reviewer":
        events = receipt.get("events") or []
        not_started = [
            item
            for item in events
            if item.get("type") == "worker.failed"
            and item.get("dispatch_state") == "not_started"
            and item.get("worker_launched") is False
        ]
        if (
            len(not_started) == 1
            and receipt.get("group_cleared") is True
            and receipt.get("group_leaked") is False
            and not any(item.get("type") == "worker.started" for item in events)
        ):
            return {
                "state": "settled",
                "basis": "host attests no turn dispatch and process custody closed",
            }
        admissions = [item for item in events if item.get("type") == "scope.admitted"]
        closures = [item for item in events if item.get("type") == "effects.reconciled"]
        if (
            len(admissions) != 1
            or len(closures) != 1
            or any(item.get("type") == "effects.unreconciled" for item in events)
        ):
            return unknown
        scope, closed = admissions[0], closures[0]
        if (
            scope.get("scope") != "controlled_workspace"
            or scope.get("role") != role
            or scope.get("network") is not False
            or scope.get("actual_repository_command_access") is not False
            or scope.get("mutation_owner") != "synchronous_main_process_broker"
            or scope.get("custody") != "outer_existing_execute"
            or scope.get("manifest_sha256") != (receipt.get("outcome_manifest") or {}).get("sha256")
            or scope.get("requested_model") != receipt.get("requested_model")
            or scope.get("resolved_model") != receipt.get("requested_model")
            or scope.get("thread_id") != receipt.get("thread_id")
            or closed.get("thread_id") != receipt.get("thread_id")
            or closed.get("scope") != "controlled_workspace"
            or closed.get("broker_closed") is not True
            or closed.get("mutation_owner") != "synchronous_main_process_broker"
            or receipt.get("group_cleared") is not True
            or receipt.get("group_leaked") is not False
        ):
            return unknown
        return {
            "state": "settled",
            "basis": "verified controlled workspace and closed broker custody",
            "manifest_sha256": closed.get("manifest_sha256"),
        }
    manifest = (receipt.get("outcome_manifest") or {}).get("sha256")
    events = receipt.get("events") or []
    admissions = [event for event in events if event.get("type") == "isolation.verified"]
    settled = [event for event in events if event.get("type") == "effects.reconciled"]
    if (
        not manifest
        or len(admissions) != 1
        or len(settled) != 1
        or admissions[0].get("profile") != "stackos_outcome_review"
        or admissions[0].get("outcome_manifest_sha256") != manifest
        or settled[0].get("outcome_manifest_sha256") != manifest
        or settled[0].get("scope") != "read_only_outcome"
        or settled[0].get("thread_closed") is not True
        or not receipt.get("thread_id")
        or settled[0].get("thread_id") != receipt["thread_id"]
        or receipt.get("group_cleared") is not True
        or receipt.get("group_leaked") is not False
        or receipt.get("timed_out") is not False
        or receipt.get("exit_code") != 0
    ):
        return unknown
    return {
        "state": "settled",
        "basis": "verified read-only outcome adapter and closed custody",
        "outcome_manifest_sha256": manifest,
    }


def role_effects_safe(receipt):
    disposition = receipt.get("effect_disposition") or {}
    if disposition.get("state") != "settled" or denied(receipt):
        return False
    if disposition.get("basis") == "controller pre-dispatch; no worker launched":
        return (
            receipt.get("dispatch_state") == "not_started"
            and (receipt.get("custody") or {}).get("worker_launched") is False
        )
    return disposition == role_effect_disposition(receipt.get("role", "reviewer"), receipt)


def event_effects_safe(event):
    if event.get("role") == "observation":
        return handoff_effects_safe({"observations": event.get("receipts") or []})
    return role_effects_safe(event.get("receipt") or {})


def handoff_effects_safe(receipt):
    """Positive confinement is required; output/digests never prove no effects."""
    observations = receipt.get("observations") or []
    review = receipt.get("outcome_review")
    return (
        bool(observations)
        and not denied(receipt)
        and all(
            item.get("confinement", {}).get("mode") == "read_only"
            and item["confinement"].get("verified") is True
            and item["confinement"].get("network") is False
            and item.get("refusal_state") == "no_refusal_reported"
            and isinstance(item.get("exit_code"), int)
            and item.get("group_cleared") is True
            and not item.get("timed_out")
            for item in observations
        )
        and (review is None or role_effects_safe(review))
    )


def archive_tests(api, ledger, repo, approved):
    """Archive then restore only test deltas from the controlled test phase."""
    paths = [path for path in api._changed_paths(repo) if api._test_path(path)]
    snapshots = []
    for path in paths:
        file = repo / path
        if path in approved or file.is_symlink() or not file.is_file():
            raise ValueError("test custody is ambiguous; retain files for operator reconciliation")
        completed = [
            event
            for event in ledger.value["events"]
            if event["kind"] == "completion" and event.get("role") == "test_writer"
        ]
        if (
            not completed
            or completed[-1]["file_digests"].get(path) != api._file_digests(repo, [path])[path]
        ):
            raise ValueError("test content is not the exact recorded worker result; preserve it")
        content = file.read_text(encoding="utf-8")
        snapshots.append(
            {
                "path": path,
                "content": content,
                "mode": file.stat().st_mode,
                "digest": api._file_digests(repo, [path])[path],
            }
        )
    ledger.append("test_archive", files=snapshots)
    tracked = set(api._git(repo, "ls-files", "-z").decode().split("\0"))
    for saved in snapshots:
        path = saved["path"]
        if api._file_digests(repo, [path])[path] != saved["digest"]:
            raise ValueError("test file changed after archival; hold")
        if path in tracked:
            (repo / path).write_bytes(api._git(repo, "show", "HEAD:" + path))
        else:
            (repo / path).unlink()
    ledger.value["candidate_digests"] = api._file_digests(repo, api._changed_paths(repo))
    ledger.append("tests_retired", paths=paths)
