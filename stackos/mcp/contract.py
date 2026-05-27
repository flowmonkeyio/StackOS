"""MCP tool input/output contract — base classes and verb-prefix logic.

Per PLAN.md L676-L763 every MCP tool exposes a pydantic Input/Output pair.
The base classes here enforce two project-wide invariants:

1. **Strict input shape** — ``MCPInput`` rejects extra fields so a typo in
   a tool argument surfaces as ``ValidationError`` (-32602), not as a
   silently-ignored stash.
2. **Mutating-vs-read envelope discipline** — every tool whose verb starts
   with one of the mutating prefixes (PLAN.md L758-L763) MUST declare
   ``WriteEnvelope[...]`` as its output type. Read tools return the bare
   data type. The runtime registration check in
    ``stackos.mcp.server.assert_envelope_discipline`` enforces this
    at daemon startup; the helper ``verb_is_mutating`` lives here so unit
    tests can pin the verb list without booting the server.

The full mutating-verb list is the source of truth. Adding a new verb
here is the single edit needed when M5/M7/M8 add new tools — the
registration check picks it up automatically.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Mutating-verb registry (PLAN.md L758-L763 + audit additions).
# ---------------------------------------------------------------------------


# Every mutating prefix accepted by the registration check. The list is
# intentionally over-inclusive — adding a verb here only relaxes the
# registration assertion, never tightens it.
MUTATING_VERBS: frozenset[str] = frozenset(
    {
        # CRUD verbs (PLAN.md L758).
        "create",
        "update",
        "set",
        "mark",
        "add",
        "remove",
        "revoke",
        "upsert",
        "toggle",
        "approve",
        "reject",
        "apply",
        "dismiss",
        "enable",
        "disable",
        "save",
        "send",
        "reply",
        "forward",
        "react",
        "respond",
        "continuePrivately",
        "patch",
        "pick",
        "connect",
        "bootstrap",
        # Bulk verbs.
        "bulkCreate",
        "bulkUpdate",
        "bulkApply",
        "bulkUpdateStatus",
        "bulkSet",
        "bulkRecord",
        # Lifecycle / orchestration verbs.
        "run",
        "claim",
        "prepareRunPlan",
        "release",
        "link",
        "complete",
        "ignore",
        "snapshot",
        "ingest",
        "bulkIngest",
        "test",
        "validate",
        "abort",
        "fork",
        "claimStep",
        "execute",
        "recordStepCall",
        "recordStep",
        "record",
        "rotate",
        "refresh",
        "reapStale",
        "rollup",
        "repair",
        "delete",
        "start",
        "finish",
        "heartbeat",
        "insertStep",
        "suggest",
        "diff",
    }
)

# Read prefixes — kept here so unit tests can assert they are *not*
# classified as mutating. The registration check uses ``MUTATING_VERBS``
# only; this set is documentation + test harness fuel.
READ_VERBS: frozenset[str] = frozenset(
    {
        "get",
        "list",
        "query",
        "score",
        "cost",
        "enums",
        "listVariants",
        "listVersions",
        "children",
        "status",
        "preview",
        "queryProject",
        "queryAll",
        "getReport",
        "lookup",
        "fetch",
    }
)

READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # D06 template validation is pure schema checking; it saves no state.
        # D07 run-plan validation is also pure schema/template resolution.
        # Action validation is schema/connector validation only; execution is
        # the separate mutating action.execute path.
        "action.validate",
        "runPlan.validate",
        "tracker.execute",
        "workflowTemplate.validate",
    }
)


def verb_is_mutating(name: str) -> bool:
    """Return ``True`` if a tool name's verb component is mutating.

    Tool names are dotted: ``namespace.verb`` (e.g. ``resource.upsert``).
    The verb after the last dot is matched against
    ``MUTATING_VERBS``. Names without a dot are treated as bare verbs so
    test fixtures can stress the helper without inventing namespaces.
    """
    if name in READ_ONLY_TOOL_NAMES:
        return False
    verb = name.rsplit(".", 1)[-1]
    if verb in MUTATING_VERBS:
        return True
    # CamelCase verbs inherit the base mutating prefix. Require an uppercase
    # boundary so read-ish verbs stay controlled by the explicit read registry.
    return any(
        verb.startswith(prefix) and len(verb) > len(prefix) and verb[len(prefix)].isupper()
        for prefix in MUTATING_VERBS
        if prefix.islower()
    )


# ---------------------------------------------------------------------------
# Pydantic base classes.
# ---------------------------------------------------------------------------


class MCPInput(BaseModel):
    """Common base for every tool's input schema.

    Strict-extra ``forbid``: any caller-supplied field that's not declared
    on the subclass surfaces as a 422 (-32602) at the SDK's input
    validation step. This protects against silent typos in skill prompts
    that would otherwise look like the call "succeeded".

    Every project-scoped tool declares ``project_id: int`` directly on
    its subclass — we deliberately do *not* declare it on the base so
    globally-scoped tools (e.g. ``meta.enums``, ``project.list``,
    ``project.create``) don't have to opt out via ``project_id: int |
    None = None`` ceremony.
    """

    model_config = ConfigDict(extra="forbid")

    # The four cross-cutting fields below are optional on the base so any
    # tool can declare or omit them. The registration-time discipline
    # check inspects subclass fields directly.
    idempotency_key: str | None = None
    """24h dedup token for mutating tools."""

    run_token: str | None = None
    """Active-run correlation token returned by ``run.start`` or ``runPlan.start``.

    The server resolves it via ``permissions.resolve_run_token`` to enforce
    the tool-grant matrix and link audit rows to the right ``runs.id``.
    """

    expected_etag: str | None = None
    """Optimistic-concurrency token for mutating tools that support it."""


class MCPOutput(BaseModel):
    """Common base for read-tool outputs.

    Bare data — no envelope. Pydantic ``model_config`` is left default so
    each output model can pick its own field semantics.
    """


class WriteEnvelope(BaseModel, Generic[T]):  # noqa: UP046 — explicit Generic for SDK schema gen
    """Mutating-tool return wrapper per PLAN.md L758-L763.

    Wire shape: ``{data: T, run_id: int | None, project_id: int | None}``.
    Mirrors ``stackos.repositories.base.Envelope`` so the M3 layer
    can pass repository envelopes through verbatim (the field names are
    identical) — see ``stackos.mcp.server.envelope_from_repo``.
    """

    data: T
    run_id: int | None = None
    project_id: int | None = None


__all__ = [
    "MUTATING_VERBS",
    "READ_ONLY_TOOL_NAMES",
    "READ_VERBS",
    "MCPInput",
    "MCPOutput",
    "WriteEnvelope",
    "verb_is_mutating",
]
