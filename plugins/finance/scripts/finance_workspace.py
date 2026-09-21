"""Optional local-json host mechanics; not a daemon backend or financial decision engine.

Python 3.11+ on a host with fcntl and jsonschema. Resolve this script relative to
the installed finance plugin origin. Financial files remain in the explicitly
selected external directory. Validation proves only the named integrity checks.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator, FormatChecker, validators
    from jsonschema.exceptions import ValidationError
except ImportError:
    raise SystemExit(
        "Finance host tools require the jsonschema Python package. "
        "Use an operator-approved environment with that dependency; no files were changed."
    ) from None

if __package__:
    from .finance_records import CurrentRecords
else:
    from finance_records import CurrentRecords

TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates/finance-workspace"
# JSON Schema's mathematical integer accepts integral floats. Financial integer
# fields require actual integer tokens/values, never a rounded float or bool.
FinanceValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _checker, value: type(value) is int
    ),
)


def decode_json(payload: bytes | str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid(value: str) -> None:
        raise ValueError("non-finite JSON number")

    def finite(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite JSON number")
        return result

    return json.loads(payload, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite)


def encode_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()


def checked_path(value: str | Path, within: Path | None = None) -> Path:
    path = Path(value)
    if ".." in path.parts:
        raise ValueError("parent traversal is not allowed")
    path = path.absolute()
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError("symlink is not allowed; supply a canonical host path")
    if within is not None and not path.is_relative_to(Path(within).absolute()):
        raise ValueError("path escapes selected host directory")
    return path


def read_regular(path: str | Path) -> bytes:
    path = checked_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("expected a regular file")
        payload = stream.read()
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ) or len(payload) != before.st_size:
            raise ValueError("file changed during read")
    return payload


def digest(path: Path) -> str:
    return hashlib.sha256(read_regular(path)).hexdigest()


def new_document() -> dict[str, Any]:
    return decode_json(read_regular(TEMPLATE_ROOT / "finance.json"))


def records(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return CurrentRecords(document).records


def validate(document: dict[str, Any], *, schema_path: Path | None = None) -> None:
    schema = decode_json(
        read_regular(schema_path or TEMPLATE_ROOT / "schemas/finance-v1.schema.json")
    )
    FinanceValidator(schema, format_checker=FormatChecker()).validate(document)
    resolver = CurrentRecords(document)
    by_id = resolver.records
    for row in by_id.values():
        if row["status_history"][-1]["to_status"] != row["status"]:
            raise ValueError("current status differs from status history")
        for field in ("created_at", "updated_at"):
            datetime.fromisoformat(row[field].replace("Z", "+00:00"))
    # Only internal record links are resolved here. Provider/account/action refs
    # and provenance refs can name external evidence and are not local records.
    local_keys = {
        "source_ref": "sources",
        "attachment_refs": "attachments",
        "verified_attachment_refs": "attachments",
        "receipt_refs": "receipts",
        "reconciliation_refs": "reconciliations",
        "correction_refs": "corrections",
        "exception_refs": "exceptions",
        "customer_mapping_ref": "customer_mappings",
        "recipient_settings_ref": "recipient_settings",
        "billing_version_ref": "billing_versions",
        "invoice_observation_ref": "provider_observations",
        "settlement_refs": "settlements",
        "review_refs": "reviews",
        "approval_refs": "reviews",
        "attempt_ref": "mutation_attempts",
        "attempt_refs": "mutation_attempts",
        "original_attempt_ref": "mutation_attempts",
        "write_proof_refs": "write_proofs",
        "handoff_refs": "handoffs",
        "profile_refs": "business_profiles",
        "tax_packet_ref": "tax_packets",
        "forecast_ref": "cashflow_forecasts",
        "reserve_application_ref": "reserve_applications",
        "duplicate_of_ref": None,
        "duplicate_candidate_refs": None,
    }

    def check_links(fields: dict[str, Any], links: dict[str, str | None], collection: str) -> None:
        for key in links.keys() & fields.keys():
            refs = fields[key] if isinstance(fields[key], list) else [fields[key]]
            if any(
                ref not in by_id or resolver.collections[ref] != (links[key] or collection)
                for ref in refs
            ):
                raise ValueError(f"unresolved local reference: {key}")

    for row in by_id.values():
        collection = resolver.collections[row["record_id"]]
        check_links(row, local_keys, collection)
        # These named schema objects own local links. Do not recursively treat
        # arbitrary provider payload keys or opaque provenance as local records.
        if collection == "bookkeeping" and "conversion" in row:
            check_links(row["conversion"], {"review_ref": "reviews"}, collection)
        if collection == "settlements" and "recovery" in row:
            check_links(row["recovery"], {"original_attempt_ref": "mutation_attempts"}, collection)
    for row in document["reviews"]:
        target = by_id.get(row["target_ref"])
        if target is None:
            raise ValueError("review target does not resolve to a canonical record")
        if target and "version" in target and row.get("target_version") != target["version"]:
            raise ValueError("review must match exact versioned target")
        if target:
            collection = next(
                key
                for key, items in document.items()
                if isinstance(items, list) and target in items
            )
            if row["target_digest_sha256"] != material_record_digest(target, collection):
                raise ValueError("review must match exact material digest")
    for row in document["attachments"]:
        if row["record_id"] not in by_id[row["source_ref"]]["attachment_refs"]:
            raise ValueError("attachment lacks its source backlink")
    for row in document["handoffs"]:
        target = by_id.get(row["source_packet_ref"])
        if target is None:
            raise ValueError("handoff source does not resolve to a canonical record")
        if target and "version" in target and row.get("source_version") != target["version"]:
            raise ValueError("handoff must match exact versioned target")
        if (
            target
            and "version" not in target
            and row.get("source_digest_sha256") != snapshot_digest(target)
        ):
            raise ValueError("handoff must match exact unversioned snapshot")
    sources = [
        row["source_ref"] for row in document["settlements"] if row["status"] != "superseded"
    ]
    if len(sources) != len(set(sources)):
        raise ValueError("duplicate current source allocation")
    pairs = [
        (
            row["tax_packet_ref"],
            row["tax_packet_version"],
            row["forecast_ref"],
            row["forecast_version"],
        )
        for row in document["reserve_applications"]
        if row["status"] != "superseded"
    ]
    if len(pairs) != len(set(pairs)):
        raise ValueError("duplicate tax/forecast reserve application")
    for billing in document["billing_versions"]:
        if material_billing_digest(billing) != billing["digest_sha256"]:
            raise ValueError("billing material digest mismatch")
        values = [line["amount"] for line in billing["lines"] if "amount" in line]
        totals = [billing[key] for key in ("subtotal", "total") if key in billing]
        if any(value["currency"] != billing["currency"] for value in [*values, *totals]) or any(
            line["currency"] != billing["currency"]
            for line in billing["lines"]
            if "currency" in line
        ):
            raise ValueError("billing currency mismatch")
        if (
            len(values) == len(billing["lines"])
            and "subtotal" in billing
            and sum(value["amount_minor"] for value in values)
            != billing["subtotal"]["amount_minor"]
        ):
            raise ValueError("billing subtotal mismatch")
    for forecast in document["cashflow_forecasts"]:
        for rows in forecast["scenarios"].values():
            for ordinal, row in enumerate(rows):
                if row["week_number"] != ordinal + 1 or date.fromisoformat(
                    row["week_start"]
                ) != date.fromisoformat(forecast["week_1_start"]) + timedelta(weeks=ordinal):
                    raise ValueError("forecast weeks not sequential")
                fields = (
                    "opening_cash",
                    "operating_inflows",
                    "operating_outflows",
                    "actual_tax_payments",
                    "closing_cash",
                    "earmarked_reserve",
                    "free_cash",
                )
                if not all(field in row for field in fields):
                    if not row["gaps"]:
                        raise ValueError("unknown forecast cash needs an explicit gap")
                    continue
                values = [row[field] for field in fields]
                if len({value["currency"] for value in values}) != 1:
                    raise ValueError("forecast currency mismatch")
                opening, inflows, outflows, paid, closing, reserve, free = [
                    value["amount_minor"] for value in values
                ]
                if closing != opening + inflows - outflows - paid or free != closing - reserve:
                    raise ValueError("forecast cash arithmetic mismatch")
                if ordinal and row["opening_cash"] != rows[ordinal - 1].get("closing_cash"):
                    raise ValueError("forecast opening does not roll forward")


def material_billing_digest(billing: dict[str, Any]) -> str:
    """Approved material only; unrelated workspace revisions cannot invalidate it."""
    keys = (
        "version",
        "customer_mapping_ref",
        "recipient_settings_ref",
        "currency",
        "terms",
        "lines",
        "source_refs",
    )
    payload = {key: billing[key] for key in keys}
    for key in ("subtotal", "total"):
        if key in billing:
            payload[key] = billing[key]
    if "effective_at" in billing:
        payload["effective_at"] = billing["effective_at"]
    if "external_project_ref" in billing:
        payload["external_project_ref"] = billing["external_project_ref"]
    if "supersedes_ref" in billing:
        payload["supersedes_ref"] = billing["supersedes_ref"]
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def snapshot_digest(value: dict[str, Any]) -> str:
    # Unversioned material excludes common lifecycle metadata and its own
    # result links, not provenance, gaps, identity or other business facts.
    excluded = {
        "created_at",
        "updated_at",
        "status",
        "status_history",
        "review_refs",
        "write_proof_refs",
        "handoff_refs",
    }
    value = {key: item for key, item in value.items() if key not in excluded}
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def material_record_digest(value: dict[str, Any], collection: str) -> str:
    if collection == "billing_versions":
        return material_billing_digest(value)
    projections = {
        "tax_packets": (
            "version",
            "tax_year",
            "as_of",
            "profile_refs",
            "annual_inputs",
            "source_refs",
            "obligations",
            "gaps",
            "supersedes_ref",
        ),
        "collection_decisions": (
            "version",
            "invoice_ref",
            "invoice_observation_ref",
            "eligibility",
            "decision_reason",
            "reminder_owner",
            "due_status",
            "dispute_state",
            "pause_state",
            "promise_state",
            "correction_state",
            "last_contact_at",
            "cooldown_until",
            "recipient_settings_ref",
            "outreach",
            "settlement_refs",
            "gaps",
            "supersedes_ref",
        ),
        "settlements": (
            "version",
            "source_ref",
            "source_identity",
            "source_sha256",
            "payment_reference_sha256",
            "provider_ref",
            "account_ref",
            "customer_ref",
            "invoice_ref",
            "received_amount",
            "received_at",
            "selected_route",
            "allocation",
            "match_evidence_refs",
            "duplicate_of_ref",
            "gaps",
            "supersedes_ref",
        ),
    }
    if collection not in projections:
        return snapshot_digest(value)
    payload = {key: value[key] for key in projections[collection] if key in value}
    if collection == "settlements" and value["selected_route"] == "attach-payment":
        payload["payment_ref"] = value["payment_ref"]
    return snapshot_digest(payload)


def evidence_path(finance: Path, relative: str) -> Path:
    if (
        not isinstance(relative, str)
        or Path(relative).is_absolute()
        or ".." in Path(relative).parts
    ):
        raise ValueError("evidence path must be relative and contained")
    schema = decode_json(read_regular(finance / "schemas/finance-v1.schema.json"))
    definition = schema["$defs"]["attachment"]["allOf"][1]["properties"]["relative_path"]
    try:
        Draft202012Validator(
            {"$ref": "#/$defs/evidence_path", "$defs": schema["$defs"]}
            if "evidence_path" in schema["$defs"]
            else definition
        ).validate(relative)
    except ValidationError as error:
        raise ValueError("unsupported evidence path") from error
    if Path(relative).is_absolute():
        raise ValueError("evidence path must be relative")
    return checked_path(finance / relative, within=finance)


def verify_originals(document: dict[str, Any], finance: Path) -> dict[str, Any]:
    verified: list[str] = []
    unavailable: list[dict[str, str]] = []
    for row in document["attachments"]:
        if row["status"] == "disposed" or "quarantine_ref" in row or row["status"] == "quarantined":
            unavailable.append({"attachment_ref": row["record_id"], "state": row["status"]})
            continue
        if "relative_path" not in row:
            raise ValueError("current attachment has no original path")
        raw = read_regular(evidence_path(finance, row["relative_path"]))
        if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("original custody hash/size mismatch")
        verified.append(row["record_id"])
    return {"verified_attachment_refs": verified, "unavailable_attachments": unavailable}


def read_document(path: Path) -> dict[str, Any]:
    path = checked_path(path)
    raw = read_regular(path)
    document = decode_json(raw)
    validate(document, schema_path=path.parent / "schemas/finance-v1.schema.json")
    verify_originals(document, path.parent)
    if read_regular(path) != raw:
        raise ValueError("finance document changed during validation; reread")
    return document


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, payload: bytes) -> None:
    """Replace one derived/master file; caller must own the writer lock."""
    path = checked_path(path)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if read_regular(temporary) != payload:
            raise ValueError("temporary readback failed")
        os.replace(temporary, path)
        _sync_directory(path.parent)
        if read_regular(path) != payload:
            raise ValueError("committed file readback failed; reconcile before retry")
    finally:
        temporary.unlink(missing_ok=True)


def _retain(path: Path, payload: bytes) -> None:
    path = checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    checked_path(path)
    if path.exists():
        if read_regular(path) != payload:
            raise ValueError("existing original content conflict")
        return
    descriptor, name = tempfile.mkstemp(prefix=".original-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if read_regular(temporary) != payload:
            raise ValueError("original temporary readback failed")
        # Hard-link publication is no-clobber, unlike replacing an existing original.
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if read_regular(path) != payload:
                raise ValueError("existing original content conflict") from None
        _sync_directory(path.parent)
        if read_regular(path) != payload:
            raise ValueError("retained original readback failed")
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def exclusive_lock(finance: Path) -> Iterator[None]:
    """Persistent nonblocking host lock; never deleted, replaced or stale-reclaimed."""
    try:
        import fcntl
    except ImportError as error:
        raise RuntimeError(
            "exclusive host locking requires fcntl; persistence unavailable"
        ) from error
    finance = checked_path(finance)
    descriptor = os.open(
        checked_path(finance / ".writer.lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("writer lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise BlockingIOError(
                "writer busy; wait or defer without choosing another lock"
            ) from error
        yield
    finally:
        os.close(descriptor)


def initialize(finance: Path) -> Path:
    """Explicit prerequisite initialization only; never reset nonempty storage."""
    finance = checked_path(finance)
    finance.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(finance.iterdir()):
        raise ValueError("existing workspace requires readback, not empty bootstrap overwrite")
    with exclusive_lock(finance):
        if any(child.name != ".writer.lock" for child in finance.iterdir()):
            raise ValueError("existing workspace requires readback, not empty bootstrap overwrite")
        schema_path = finance / "schemas/finance-v1.schema.json"
        schema_path.parent.mkdir()
        _retain(schema_path, read_regular(TEMPLATE_ROOT / "schemas/finance-v1.schema.json"))
        _retain(finance / "FINANCE.md", read_regular(TEMPLATE_ROOT / "FINANCE.md"))
        document = new_document()
        validate(document, schema_path=schema_path)
        _retain(finance / "finance.json", encode_json(document))
        read_document(finance / "finance.json")
    return finance / "finance.json"


def _validate_history(prior: dict[str, Any], document: dict[str, Any]) -> None:
    previous_records, current_records = records(prior), records(document)
    previous_collections = CurrentRecords(prior).collections
    current_collections = CurrentRecords(document).collections
    for record_id, previous in previous_records.items():
        if (
            record_id not in current_records
            or previous_collections[record_id] != current_collections[record_id]
        ):
            raise ValueError("canonical history cannot be silently deleted or moved")
        current = current_records[record_id]
        for field in ("status_history", "provenance"):
            if current[field][: len(previous[field])] != previous[field]:
                raise ValueError("canonical history must remain additive")
        if current["created_at"] != previous["created_at"]:
            raise ValueError("canonical creation identity is immutable")
        if previous_collections[record_id] == "sources":
            for field in (
                "source_kind",
                "source_identity",
                "account_ref",
                "provider_ref",
                "transport_identity",
            ):
                if current.get(field) != previous.get(field):
                    raise ValueError("source identity is immutable; retain an additive observation")
            if (
                current["attachment_refs"][: len(previous["attachment_refs"])]
                != previous["attachment_refs"]
            ):
                raise ValueError("source attachment history must remain additive")
        if previous_collections[record_id] == "attachments":
            for field in (
                "source_ref",
                "relative_path",
                "sha256",
                "bytes",
                "media_type",
                "original_filename",
                "role",
            ):
                if current.get(field) != previous.get(field):
                    raise ValueError(
                        "original custody identity is immutable; "
                        "reviewed relocation is outside this helper"
                    )
        if (
            previous_collections[record_id] == "billing_versions"
            and previous["status"] in {"approved", "reviewed"}
            and material_billing_digest(current) != material_billing_digest(previous)
        ):
            raise ValueError("approved billing material is immutable")


class WriteSession:
    def __init__(self, path: Path, expected_hash: str, expected_revision: int | None):
        self.path = checked_path(path)
        self.prior_bytes = read_regular(path)
        self.document = read_document(path)
        if hashlib.sha256(self.prior_bytes).hexdigest() != expected_hash or (
            expected_revision is not None and self.document["revision"] != expected_revision
        ):
            raise ValueError("stale host revision")
        if read_regular(path) != self.prior_bytes:
            raise ValueError("stale host revision")
        self._prior = copy.deepcopy(self.document)
        self._active = True
        self._retained: dict[str, dict[str, Any]] = {}

    def _check_active(self) -> None:
        if not self._active:
            raise ValueError("writer session is no longer active")

    def retain_original(
        self, relative_path: str, data: bytes, *, expected_sha256: str | None = None
    ) -> dict[str, Any]:
        self._check_active()
        if not isinstance(data, bytes) or not data:
            raise ValueError("original requires nonempty exact bytes")
        checksum = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and checksum != expected_sha256:
            raise ValueError("original expected hash mismatch")
        evidence_path(self.path.parent, relative_path)
        if checksum in self._retained:
            relative_path = self._retained[checksum]["relative_path"]
        # Reuse an observed exact original even if another requested label was supplied.
        for row in self.document["attachments"]:
            if (
                row["status"] == "retained"
                and row["sha256"] == checksum
                and row["bytes"] == len(data)
                and "relative_path" in row
            ):
                relative_path = row["relative_path"]
                break
        target = evidence_path(self.path.parent, relative_path)
        _retain(target, data)
        result = {"relative_path": relative_path, "sha256": checksum, "bytes": len(data)}
        self._retained[checksum] = result
        return dict(result)

    def commit(self, document: dict[str, Any]) -> dict[str, Any]:
        self._check_active()
        if read_regular(self.path) != self.prior_bytes:
            raise ValueError("stale host revision")
        candidate = copy.deepcopy(document)
        if candidate["revision"] != self._prior["revision"] + 1:
            raise ValueError("revision must advance exactly once")
        validate(candidate, schema_path=self.path.parent / "schemas/finance-v1.schema.json")
        _validate_history(self._prior, candidate)
        verify_originals(candidate, self.path.parent)
        if read_regular(self.path) != self.prior_bytes:
            raise ValueError("stale host revision")
        atomic_write(self.path, encode_json(candidate))
        reread = read_document(self.path)
        if reread != candidate:
            raise ValueError("committed finance readback failed; reconcile before retry")
        proof = {
            "prior_revision": self._prior["revision"],
            "prior_sha256": hashlib.sha256(self.prior_bytes).hexdigest(),
            "new_revision": candidate["revision"],
            "reread_result": "verified",
            **verify_originals(reread, self.path.parent),
        }
        self.document = reread
        self._prior = copy.deepcopy(reread)
        self.prior_bytes = read_regular(self.path)
        return proof


@contextmanager
def writer(
    path: Path, expected_hash: str, expected_revision: int | None = None
) -> Iterator[WriteSession]:
    path = checked_path(path)
    with exclusive_lock(path.parent):
        session = WriteSession(path, expected_hash, expected_revision)
        try:
            yield session
        finally:
            session._active = False


def write_document(path: Path, document: dict[str, Any], expected_hash: str) -> None:
    with writer(path, expected_hash) as session:
        session.commit(document)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "initialize"))
    parser.add_argument("--finance-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        finance = checked_path(args.finance_dir)
        path = initialize(finance) if args.command == "initialize" else finance / "finance.json"
        document = read_document(path)
        print(
            json.dumps(
                {
                    "status": "verified",
                    "revision": document["revision"],
                    "checks": ["schema", "record-integrity", "retained-originals"],
                    **verify_originals(document, finance),
                }
            )
        )
        return 0
    except ValidationError as error:
        parser.exit(
            1, f"ValidationError: schema invalid at {'.'.join(map(str, error.absolute_path))}\n"
        )
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
