"""Registered REST/CLI finance console contracts over the finance plugin owner."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from pydantic import ValidationError as PydanticValidationError

from stackos.mcp.contract import MCPInput, WriteEnvelope
from stackos.mcp.errors import ToolNotGrantedError
from stackos.operations.spec import (
    OperationExample,
    OperationResponsePolicy,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)
from stackos.plugins.builtin_finance import FinanceConsole
from stackos.repositories.base import RepositoryError


class SnapshotRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FinanceSnapshotOut(SnapshotRef):
    schema_version: str
    read_at: str
    custody_issues: list[dict[str, Any]] = Field(default_factory=list)


class FinanceInput(MCPInput):
    project_id: StrictInt = Field(gt=0)
    workspace_ref: str = Field(
        min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )
    # These human console contracts never derive authority from run tokens.
    run_token: None = None
    expected_etag: None = None
    idempotency_key: str | None = Field(
        default=None, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )


class FinanceAccessInput(FinanceInput):
    access_generation: StrictInt = Field(ge=1)


class FinanceWorkspaceSelectInput(FinanceInput):
    root: str = Field(min_length=1, max_length=4096)
    modes: list[Literal["read", "correct", "export"]] = Field(
        default=["read", "correct", "export"], min_length=1
    )
    expected_generation: StrictInt | None = Field(default=None, ge=0)
    authority_ref: str = Field(
        min_length=1, max_length=240, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    )


class FinanceWorkspaceRevokeInput(FinanceInput):
    expected_generation: StrictInt = Field(ge=1)


class FinanceWorkspaceStatusInput(MCPInput):
    project_id: StrictInt = Field(gt=0)
    workspace_ref: str | None = Field(default=None, min_length=1, max_length=160)
    run_token: None = None
    idempotency_key: None = None


class FinanceComparisonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    clamped: bool | None = None


class FinanceQueryInput(FinanceAccessInput):
    start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    comparison: FinanceComparisonInput | None = None
    recognition_basis: Literal["cash", "accrual"]
    date_basis: Literal["transaction", "posting", "issue", "cash"]
    timezone: str = Field(min_length=1, max_length=80)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    scope: Literal["business", "external-project"] = "business"
    external_project_ref: str | None = Field(default=None, min_length=1, max_length=240)
    kind: Literal["all", "expense", "income", "bill", "receipt"] = "all"
    account_ref: str | None = Field(default=None, max_length=240)
    category_ref: str | None = Field(default=None, max_length=240)
    counterparty_ref: str | None = Field(default=None, max_length=240)
    needs_review: bool | None = None
    q: str | None = Field(default=None, max_length=240)
    sort: Literal["date", "amount", "counterparty", "kind", "review"] = "date"
    descending: bool = False
    offset: StrictInt = Field(default=0, ge=0)
    limit: StrictInt = Field(default=50, ge=1, le=200)
    expected_snapshot: SnapshotRef | None = None
    idempotency_key: None = None

    def query_arguments(self):
        fields = set(FinanceQueryInput.model_fields) - {
            "project_id",
            "response_mode",
            "run_token",
            "idempotency_key",
            "expected_etag",
        }
        return self.model_dump(mode="json", include=fields, exclude_none=True)


class FinanceRecordGetInput(FinanceAccessInput):
    record_ref: str = Field(min_length=1, max_length=240)
    expected_snapshot: SnapshotRef | None = None
    idempotency_key: None = None


class FinanceMoneyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minor: str = Field(pattern=r"^-?\d+$", max_length=100)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # A factory preserves optional input in generators that require schema defaults.
    exponent: StrictInt | None = Field(default_factory=lambda: None, ge=0, le=6)


class FinanceEditFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    business_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    amount: FinanceMoneyInput | None = None
    category: dict[str, Any] | None = None
    business_purpose: str | None = Field(default=None, max_length=2000)
    external_project_ref: str | None = Field(default=None, max_length=240)
    reporting: dict[str, Any] | None = None
    merchant: str | None = Field(default=None, max_length=240)
    account_ref: str | None = Field(default=None, max_length=240)


class FinanceEditIntent(FinanceAccessInput):
    request_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$")
    expected_snapshot: SnapshotRef
    action: Literal[
        "create-bookkeeping",
        "correct-bookkeeping",
        "correct-receipt-facts",
        "propose-billing-correction",
    ]
    record_ref: str | None = Field(default=None, max_length=240)
    fields: FinanceEditFields
    source_refs: list[str] = Field(default_factory=list, max_length=200)
    attestation: str | None = Field(default=None, min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)

    def intent_arguments(self):
        fields = set(FinanceEditIntent.model_fields) - {
            "project_id",
            "response_mode",
            "run_token",
            "idempotency_key",
            "expected_etag",
        }
        return self.model_dump(mode="json", include=fields, exclude_none=True)


class FinanceCommitInput(FinanceEditIntent):
    preview_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class FinanceEvidenceReadInput(FinanceAccessInput):
    attachment_ref: str = Field(min_length=1, max_length=240)
    expected_snapshot: SnapshotRef
    idempotency_key: None = None


class FinanceReportRenderInput(FinanceQueryInput):
    format: Literal["json", "csv", "markdown"] = "markdown"
    report_kind: Literal["period", "analysis"] = "period"
    analysis_request_id: StrictInt | None = Field(default=None, gt=0)


class FinanceAccessOut(BaseModel):
    access_id: str
    project_id: int
    workspace_ref: str
    backend_key: Literal["local-json"]
    modes: list[str]
    generation: int
    revoked_at: str | None = None


class FinanceStatusOut(BaseModel):
    project_id: int
    readiness: str
    access: list[dict[str, Any]]


class FinanceMoneyOut(BaseModel):
    minor: str
    currency: str
    exponent: int | None = None


class FinanceProjectionOut(BaseModel):
    snapshot: FinanceSnapshotOut
    query: dict[str, Any]
    rows: list[dict[str, Any]]
    page: dict[str, Any]
    metrics_by_currency: list[dict[str, Any]]
    breakdowns: Any
    trend: Any
    review_items: list[dict[str, Any]]
    coverage: dict[str, Any]
    exclusions: list[dict[str, Any]]
    undated_or_period_only: list[str]
    warnings: list[str]
    comparison: dict[str, Any] | None = None


class FinanceCorrectionHistoryItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correction_ref: str
    status: Literal["applied"]
    superseded_ref: str
    replacement_ref: str
    reason: str
    actor: str | None
    at: str | None
    decision_ref: str | None
    evidence_refs: list[str]
    source_refs: list[str]


class FinanceRecordOut(BaseModel):
    requested_ref: str
    current_ref: str
    collection: str
    record: dict[str, Any]
    original: dict[str, Any]
    amount: FinanceMoneyOut | None = None
    lineage: list[str]
    correction_history: list[FinanceCorrectionHistoryItemOut] = Field(default_factory=list)
    source_refs: list[str]
    evidence: list[dict[str, Any]]
    editable_fields: list[str]
    provider_fields_editable: Literal[False] = False


class FinancePreviewOut(BaseModel):
    preview_digest: str
    source_snapshot: FinanceSnapshotOut
    changed_refs: list[str]
    diff: dict[str, Any]
    state: str
    financial_decision_adopted: Literal[False] = False


class FinanceCommitOut(BaseModel):
    request_id: str
    changed_refs: list[str]
    snapshot: FinanceSnapshotOut
    reread_result: str
    idempotency_replay: bool


class FinanceContentOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    state: str
    content_base64: str | None = None
    content_type: str | None = None
    filename: str | None = None
    snapshot: FinanceSnapshotOut | None = None
    sha256: str | None = None
    bytes: int | None = None
    inline_allowed: bool | None = None
    disposition: str | None = None
    request_id: int | None = None
    next_action: str | None = None
    result: dict[str, Any] | None = None
    report: str | None = None


def _api_values(value):
    # Original record observations retain their keys, but minor-unit integers
    # cross JavaScript as exact decimal strings rather than rounded JSON numbers.
    if isinstance(value, dict):
        return {
            k: str(v) if k.endswith("_minor") and type(v) is int else _api_values(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_api_values(v) for v in value]
    return value


def _handler(method, output, *, write=False):
    async def execute(inp, ctx, emitter):
        if ctx.extras.get("surface") != "rest" or ctx.extras.get("trusted_local_admin") is not True:
            raise ToolNotGrantedError("Finance console requires authenticated local REST authority")
        settings = ctx.extras.get("settings")
        if settings is None:
            raise ToolNotGrantedError("Finance console configuration unavailable")
        try:
            result = getattr(FinanceConsole(ctx.session, settings), method)(inp)
        except (OSError, ValueError, KeyError, TypeError):
            raise RepositoryError(
                "Finance operation requires repair",
                data={
                    "finance_code": "unreadable_or_invalid_workspace",
                    "next_action": "inspect_and_retry",
                },
            ) from None
        try:
            data = output.model_validate(_api_values(result))
        except PydanticValidationError:
            raise RepositoryError(
                "Finance output contract failed",
                data={
                    "finance_code": "output_contract_invalid",
                    "next_action": "reconcile_and_repair",
                },
            ) from None
        return (
            WriteEnvelope(data=data, project_id=inp.project_id, run_id=ctx.run_id)
            if write
            else data
        )

    return execute


def operation_specs() -> list[OperationSpec]:
    rows = (
        (
            "financeWorkspace.select",
            FinanceWorkspaceSelectInput,
            FinanceAccessOut,
            "select",
            True,
            False,
        ),
        (
            "financeWorkspace.revoke",
            FinanceWorkspaceRevokeInput,
            FinanceAccessOut,
            "revoke",
            True,
            False,
        ),
        (
            "financeWorkspace.status",
            FinanceWorkspaceStatusInput,
            FinanceStatusOut,
            "status",
            False,
            False,
        ),
        ("finance.query", FinanceQueryInput, FinanceProjectionOut, "query", False, False),
        ("finance.record.get", FinanceRecordGetInput, FinanceRecordOut, "record", False, False),
        ("finance.record.preview", FinanceEditIntent, FinancePreviewOut, "preview", False, False),
        ("finance.record.commit", FinanceCommitInput, FinanceCommitOut, "commit", True, False),
        (
            "finance.evidence.read",
            FinanceEvidenceReadInput,
            FinanceContentOut,
            "evidence",
            False,
            True,
        ),
        (
            "finance.report.render",
            FinanceReportRenderInput,
            FinanceContentOut,
            "report",
            False,
            True,
        ),
    )
    return [
        OperationSpec(
            name=name,
            summary="Inspect or manage the explicitly selected external finance workspace.",
            input_model=inp,
            output_model=WriteEnvelope.__class_getitem__(out) if write else out,
            handler=_handler(method, out, write=write),
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=False),
                rest=OperationSurface(
                    enabled=True, browser_safe=True, path=f"/api/v1/operations/{name}/call"
                ),
                cli=OperationSurface(enabled=True, command=f"ops call {name}"),
            ),
            purpose="Authenticated local finance console; access scope and current writer "
            "authority checked separately.",
            prerequisites=("Enable finance and deliberately authorize its external root.",),
            examples=(
                OperationExample(
                    title="Inspect selected synthetic workspace readiness",
                    arguments={"project_id": 1, "workspace_ref": "finance:synthetic"},
                    notes="Read financeWorkspace.status for generation and snapshot. "
                    "Never supply a root, actor or provider status to record/report calls.",
                ),
            )
            if name == "financeWorkspace.status"
            else (),
            returns=(
                "Current snapshot/record refs, explicit readiness/repair or verified safe receipt.",
            ),
            mutating=write,
            grant_policy="local-admin-console-write" if write else "local-admin-console-read",
            idempotency_policy="handler" if write else "dispatcher",
            response_policy=OperationResponsePolicy(
                default_mode="raw" if content else "compact",
                allowed_modes=("raw",) if content else ("compact", "raw"),
                ack_safe=False,
                raw_only_reason="Preserve exact private bytes and state." if content else None,
                compact_notes=(
                    "Keep source revision/hash, page, coverage, issues and retry outcomes.",
                ),
            ),
        )
        for name, inp, out, method, write, content in rows
    ]
