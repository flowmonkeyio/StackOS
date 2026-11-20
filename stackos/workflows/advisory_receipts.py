"""Pure, proposed v1 advisory records; no trusted producer or live consumer.

Native identity/owner resolution remains with TrackerRepository and
RunRepository. A future consumer must obtain those bindings independently,
authenticate the producer, and attest the actual artifact bytes. Equality here
only establishes consistency of supplied data. It is never permission or proof
of canonical currentness, approval, accepted completion, or a renewed budget.

Retention design: ``receipt_artifact_id`` identifies a standalone receipt held
by the existing Artifact owner, linked from existing ticket evidence. Retain a
new history record for each changed observation; rewind/retry/restart may clear
step.result_json but must not erase that record. Existing Artifact rows are
mutable: enforcing create-once history and re-resolving stale references are
future persistence/consumer obligations, not guarantees of this module.

Only this schema version is interpreted. Unknown/legacy versions stay unproven;
no migration, silent coercion, or lifecycle write occurs. Removing this unused
module/consumer cannot remove records or weaken existing authority fences.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator
from pydantic import ValidationError as SchemaError

RECEIPT_SCHEMA_VERSION = "stackos.advisory-receipt.v1"
PositiveId = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
Text = Annotated[
    str, StringConstraints(min_length=1, max_length=2048, pattern=r"^\S(?:[^\r\n]*\S)?$")
]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitRevision = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]

Outcome = Literal["passed", "failed", "blocked", "skipped", "not_run", "unknown", "error"]
GapKind = Literal["unsupported", "manual_required", "unknown", "error"]
DiagnosticKind = Literal[
    "malformed",
    "unsupported_version",
    "binding_mismatch",
    "stale",
    "future_capture",
    "invalid_check_context",
]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArtifactReference(_Record):
    """Claimed existing artifact identity/location and digest; bytes are unverified."""

    artifact_id: PositiveId
    uri: Text
    sha256: Digest


class ReceiptBinding(_Record):
    """Exact scope supplied by a producer and independently resolved by a consumer.

    ``run_plan_step_id`` is the native row ID, not a template step name.
    ``attempt_run_id=None`` is explicit inapplicability, and only matches None.
    Ownership revision/scope are the existing owner's values, not new counters.
    """

    project_id: PositiveId
    canonical_item_id: Annotated[
        str, StringConstraints(pattern=r"^(task|ticket):[1-9][0-9]{0,18}$")
    ]
    controller_run_id: PositiveId
    run_plan_id: PositiveId
    run_plan_step_id: PositiveId
    attempt_run_id: PositiveId | None
    owner_ref: Annotated[str, StringConstraints(min_length=1, max_length=160, pattern=r"^\S+$")]
    owner_epoch: PositiveId
    owner_revision: PositiveId
    scope_digest: Digest
    repository_ref: Text
    source_revision: GitRevision
    candidate_revision: GitRevision
    branch_ref: Text
    candidate_sha256: Digest
    obligation_id: Text
    assessment_contract_revision: Text
    verifier_revision: Text
    artifact: ArtifactReference

    @field_validator("canonical_item_id")
    @classmethod
    def _native_id_range(cls, value: str) -> str:
        if int(value.split(":")[1]) > 2**63 - 1:
            raise ValueError("canonical native ID exceeds signed-64-bit range")
        return value


class AdvisoryGap(_Record):
    kind: GapKind
    detail: Text


class AdvisoryReceipt(_Record):
    schema_version: Literal["stackos.advisory-receipt.v1"]
    binding: ReceiptBinding
    producer_ref: Text
    producer_provenance_ref: Text
    captured_at: AwareDatetime
    receipt_artifact_id: PositiveId
    outcome: Outcome
    gaps: tuple[AdvisoryGap, ...]


@dataclass(frozen=True)
class ReceiptDiagnostic:
    kind: DiagnosticKind
    field: str


@dataclass(frozen=True)
class ReceiptCheck:
    """Consistency only. All trust and authority decisions remain unproven."""

    receipt: AdvisoryReceipt | None
    diagnostics: tuple[ReceiptDiagnostic, ...]

    @property
    def consistent(self) -> bool:
        return not self.diagnostics

    @property
    def unproven_checks(self) -> tuple[str, ...]:
        return (
            "producer_authentication",
            "canonical_owner_currentness",
            "artifact_content_attestation",
            "approval_provenance",
            "durable_retention",
        )

    @property
    def granted_authorities(self) -> tuple[str, ...]:
        return ()


def check_advisory_receipt(
    receipt_json: str,
    *,
    expected: ReceiptBinding,
    now: datetime,
    max_age: timedelta,
) -> ReceiptCheck:
    """Validate supplied JSON against a supplied binding and explicit age policy.

    No I/O, callbacks, clocks, executables, storage, or owner mutations. A caller
    cannot turn matching assertions or a favorable outcome into attestation.
    Diagnostics omit raw input and provenance text. Same-input reuse is pure and
    idempotent; differing obligations/attempts must match their own bindings.
    """
    try:
        receipt = AdvisoryReceipt.model_validate_json(receipt_json)
    except SchemaError as exc:
        kinds: set[DiagnosticKind] = set()
        for error in exc.errors(include_input=False, include_url=False):
            kinds.add(
                "unsupported_version"
                if error["loc"] == ("schema_version",) and error["type"] == "literal_error"
                else "malformed"
            )
        return ReceiptCheck(
            None, tuple(ReceiptDiagnostic(kind, "receipt") for kind in sorted(kinds))
        )

    diagnostics: list[ReceiptDiagnostic] = []
    if (
        not isinstance(expected, ReceiptBinding)
        or not isinstance(now, datetime)
        or now.utcoffset() is None
        or not isinstance(max_age, timedelta)
        or max_age <= timedelta(0)
    ):
        return ReceiptCheck(
            receipt, (ReceiptDiagnostic("invalid_check_context", "expected/now/max_age"),)
        )

    for field in ReceiptBinding.model_fields:
        if getattr(receipt.binding, field) != getattr(expected, field):
            diagnostics.append(ReceiptDiagnostic("binding_mismatch", f"binding.{field}"))

    age = now - receipt.captured_at
    if age < timedelta(0):
        diagnostics.append(ReceiptDiagnostic("future_capture", "captured_at"))
    elif age > max_age:
        diagnostics.append(ReceiptDiagnostic("stale", "captured_at"))
    return ReceiptCheck(receipt, tuple(diagnostics))
