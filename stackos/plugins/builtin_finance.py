"""Narrow first-party finance console adapter; financial truth stays external."""

from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import types
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, NoReturn

from stackos.config import Settings
from stackos.mcp.errors import ToolNotGrantedError
from stackos.repositories.base import ConflictError, ValidationError
from stackos.repositories.plugins import PluginRepository
from stackos.repositories.projects import ProjectRepository

MAX_EVIDENCE_BYTES = 20 * 1024 * 1024
_MODES = {"read", "correct", "export"}


def _error(code: str, *, conflict: bool = False) -> NoReturn:
    error = ConflictError if conflict else ValidationError
    raise error(
        "Finance operation requires repair",
        data={"finance_code": code, "next_action": "inspect_and_retry"},
    )


def _deny(code: str = "access_denied") -> NoReturn:
    raise ToolNotGrantedError(
        "Finance access denied", data={"finance_code": code, "next_action": "review_finance_access"}
    )


@lru_cache(maxsize=1)
def helpers():
    """Load a fixed shipped package origin, never the selected business root."""
    bundled = Path(str(resources.files("stackos").joinpath("_assets/plugins/finance/scripts")))
    source = Path(__file__).resolve().parents[2] / "plugins/finance/scripts"
    scripts = bundled if bundled.is_dir() else source
    if not (scripts / "finance_management.py").is_file():
        _error("helper_unavailable")
    package_name = "_stackos_builtin_finance_scripts"
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts.resolve())]
    sys.modules[package_name] = package
    name = package_name + ".finance_management"
    spec = importlib.util.spec_from_file_location(name, scripts / "finance_management.py")
    if spec is None or spec.loader is None:
        _error("helper_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def call_helper(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (ToolNotGrantedError, ConflictError, ValidationError):
        raise
    except helpers().ManagementError as exc:
        code = exc.code
        data = {"finance_code": code, "next_action": "inspect_and_retry"}
        if code == "outcome_unknown":
            data.update(exc.receipt)
        error = (
            ConflictError
            if code
            in {
                "stale_snapshot",
                "stale_preview",
                "intent_conflict",
                "writer_busy",
                "outcome_unknown",
                "commit_failed",
            }
            else ValidationError
        )
        raise error("Finance operation requires repair", data=data) from None
    except (OSError, ValueError, KeyError, TypeError):
        _error("unreadable_or_invalid_workspace")


def _canonical_root(root: str | Path) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute() or candidate == Path("/"):
        _deny("invalid_root")
    try:
        candidate = helpers().workspace.checked_path(candidate)
        if not candidate.is_dir():
            _deny("invalid_root")
        return candidate
    except (OSError, ValueError):
        _deny("invalid_root")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _snapshot_metadata(snapshot):
    return {
        k: copy.deepcopy(snapshot[k])
        for k in ("revision", "sha256", "schema_version", "read_at", "custody_issues")
        if k in snapshot
    }


class FinanceConsole:
    """Authority config only; no financial data, generic file service or ledger."""

    def __init__(self, session, settings: Settings):
        self.session, self.settings = session, settings
        self.config_dir = settings.data_dir / "plugin-state" / "finance"
        self.config_path = self.config_dir / "access.json"

    def _project(self, project_id: int) -> None:
        ProjectRepository(self.session).get(project_id)
        plugin = PluginRepository(self.session).get_plugin("finance", project_id=project_id)
        if plugin.enabled_for_project is not True:
            _deny("plugin_not_enabled")

    def _load(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"format": "finance-access-v1", "entries": [], "intents": {}}
        try:
            raw = helpers().workspace.read_regular(self.config_path)
            doc = json.loads(raw)
            if (
                not isinstance(doc, dict)
                or doc.get("format") != "finance-access-v1"
                or not isinstance(doc.get("entries"), list)
            ):
                _error("access_config_corrupt")
            if not isinstance(doc.get("intents"), dict):
                _error("access_config_corrupt")
            required = {
                "access_id",
                "project_id",
                "workspace_ref",
                "backend_key",
                "canonical_root",
                "root_device",
                "root_inode",
                "modes",
                "generation",
            }
            for entry in doc["entries"]:
                if (
                    not isinstance(entry, dict)
                    or not required <= set(entry)
                    or not all(
                        isinstance(entry[k], str)
                        for k in ("access_id", "workspace_ref", "canonical_root")
                    )
                    or not all(
                        type(entry[k]) is int
                        for k in ("project_id", "root_device", "root_inode", "generation")
                    )
                    or entry["generation"] < 1
                    or entry["backend_key"] != "local-json"
                    or not isinstance(entry["modes"], list)
                    or not all(isinstance(m, str) and m in _MODES for m in entry["modes"])
                ):
                    _error("access_config_corrupt")
            for key, intent in doc["intents"].items():
                if (
                    not isinstance(key, str)
                    or not isinstance(intent, dict)
                    or not isinstance(intent.get("digest"), str)
                    or type(intent.get("generation")) is not int
                ):
                    _error("access_config_corrupt")
            return doc
        except (ValueError, OSError, TypeError):
            _error("access_config_corrupt")

    @contextmanager
    def _config_writer(self) -> Iterator[dict[str, Any]]:
        # Never hold this lock while waiting on the finance writer lock.
        try:
            helpers().workspace.checked_path(self.config_dir)
            self.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.config_dir.chmod(0o700)
            fd = os.open(
                self.config_dir / ".access.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
            )
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    _error("access_config_corrupt")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield self._load()
            finally:
                os.close(fd)
        except BlockingIOError:
            _error("access_config_busy", conflict=True)
        except (OSError, ValueError):
            _error("access_config_unavailable")

    def _save(self, doc: dict[str, Any]) -> None:
        payload = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
        helpers().workspace.atomic_write(self.config_path, payload)
        self.config_path.chmod(0o600)
        if self._load() != doc:
            _error("access_config_outcome_unknown", conflict=True)

    @staticmethod
    def _entry(doc, project_id, workspace_ref):
        return next(
            (
                e
                for e in doc["entries"]
                if e["project_id"] == project_id and e["workspace_ref"] == workspace_ref
            ),
            None,
        )

    @staticmethod
    def _safe(entry):
        return {
            k: copy.deepcopy(entry[k])
            for k in (
                "access_id",
                "project_id",
                "workspace_ref",
                "backend_key",
                "modes",
                "generation",
                "revoked_at",
            )
            if k in entry
        }

    @staticmethod
    def _intent(doc, project_id, key, intent):
        if not key:
            return None, None
        digest = hashlib.sha256(
            json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        identity = str(project_id) + ":" + key
        prior = doc["intents"].get(identity)
        if prior and prior["digest"] != digest:
            _error("intent_conflict", conflict=True)
        return identity, digest

    def select(self, inp) -> dict[str, Any]:
        self._project(inp.project_id)
        root = _canonical_root(inp.root)
        root_stat = root.stat()
        modes = sorted(set(inp.modes))
        with self._config_writer() as doc:
            old = self._entry(doc, inp.project_id, inp.workspace_ref)
            identity, digest = self._intent(
                doc,
                inp.project_id,
                inp.idempotency_key,
                {
                    "action": "select",
                    "workspace_ref": inp.workspace_ref,
                    "root": str(root),
                    "modes": modes,
                    "authority_ref": inp.authority_ref,
                    "expected_generation": inp.expected_generation,
                },
            )
            # Revalidate current root before acknowledging a config-owned retry.
            if identity and identity in doc["intents"]:
                if (
                    not old
                    or old.get("revoked_at")
                    or (old["root_device"], old["root_inode"])
                    != (root_stat.st_dev, root_stat.st_ino)
                ):
                    _deny("revoked_or_changed_root")
                if doc["intents"][identity]["generation"] != old["generation"]:
                    _error("stale_access_generation", conflict=True)
                return self._safe(old)
            if inp.expected_generation is not None and inp.expected_generation != (
                old["generation"] if old else 0
            ):
                _error("stale_access_generation", conflict=True)
            for e in doc["entries"]:
                if (
                    not e.get("revoked_at")
                    and (e["root_device"], e["root_inode"]) == (root_stat.st_dev, root_stat.st_ino)
                    and (e["project_id"], e["workspace_ref"]) != (inp.project_id, inp.workspace_ref)
                ):
                    _deny("root_already_authorized")
            entry = {
                "access_id": old["access_id"] if old else "finance-access:" + uuid.uuid4().hex,
                "project_id": inp.project_id,
                "workspace_ref": inp.workspace_ref,
                "backend_key": "local-json",
                "canonical_root": str(root),
                "root_device": root_stat.st_dev,
                "root_inode": root_stat.st_ino,
                "modes": modes,
                "generation": old["generation"] + 1 if old else 1,
                "selected_by": "local-user:operator",
                "selected_at": _now(),
                "authority_ref": inp.authority_ref,
            }
            if old:
                doc["entries"].remove(old)
            doc["entries"].append(entry)
            if identity:
                doc["intents"][identity] = {
                    "digest": digest,
                    "access_id": entry["access_id"],
                    "generation": entry["generation"],
                }
            self._save(doc)
        return self._safe(entry)

    def revoke(self, inp) -> dict[str, Any]:
        self._project(inp.project_id)
        with self._config_writer() as doc:
            entry = self._entry(doc, inp.project_id, inp.workspace_ref)
            if not entry:
                _deny("not_configured")
            identity, digest = self._intent(
                doc,
                inp.project_id,
                inp.idempotency_key,
                {
                    "action": "revoke",
                    "workspace_ref": inp.workspace_ref,
                    "expected_generation": inp.expected_generation,
                },
            )
            if identity and identity in doc["intents"]:
                if not entry.get("revoked_at"):
                    _error("intent_conflict", conflict=True)
                if doc["intents"][identity]["generation"] != entry["generation"]:
                    _error("stale_access_generation", conflict=True)
                return self._safe(entry)
            if inp.expected_generation != entry["generation"]:
                _error("stale_access_generation", conflict=True)
            entry["revoked_at"], entry["generation"] = _now(), entry["generation"] + 1
            if identity:
                doc["intents"][identity] = {
                    "digest": digest,
                    "access_id": entry["access_id"],
                    "generation": entry["generation"],
                }
            self._save(doc)
        return self._safe(entry)

    def authorize(self, inp, mode: str = "read") -> tuple[dict[str, Any], Path]:
        self._project(inp.project_id)
        entry = self._entry(self._load(), inp.project_id, inp.workspace_ref)
        if not entry or entry.get("revoked_at"):
            _deny("not_configured_or_revoked")
        if inp.access_generation != entry["generation"]:
            _deny("stale_access_generation")
        if mode not in entry["modes"]:
            _deny("mode_denied")
        root = _canonical_root(entry["canonical_root"])
        info = root.stat()
        if (info.st_dev, info.st_ino) != (entry["root_device"], entry["root_inode"]):
            _deny("root_changed")
        return entry, root / "finance.json"

    def status(self, inp) -> dict[str, Any]:
        self._project(inp.project_id)
        doc = self._load()
        entries = [
            e
            for e in doc["entries"]
            if e["project_id"] == inp.project_id
            and (not inp.workspace_ref or e["workspace_ref"] == inp.workspace_ref)
        ]
        if not entries:
            return {"project_id": inp.project_id, "readiness": "not_configured", "access": []}
        out = []
        for e in entries:
            safe = self._safe(e)
            if e.get("revoked_at"):
                safe["readiness"] = "revoked"
            else:
                try:
                    root = _canonical_root(e["canonical_root"])
                    info = root.stat()
                    if (info.st_dev, info.st_ino) != (e["root_device"], e["root_inode"]):
                        _deny("root_changed")
                    snapshot = helpers().read_snapshot(root / "finance.json")
                    safe["readiness"] = "ready"
                    safe["snapshot"] = _snapshot_metadata(snapshot)
                except helpers().ManagementError as exc:
                    safe["readiness"] = exc.code
                except (ToolNotGrantedError, OSError, ValueError):
                    safe["readiness"] = "denied"
            out.append(safe)
        return {
            "project_id": inp.project_id,
            "access": out,
            "readiness": out[0]["readiness"] if len(out) == 1 else "select_workspace",
        }

    def snapshot(self, inp, mode="read"):
        _, path = self.authorize(inp, mode)
        snapshot = call_helper(helpers().read_snapshot, path)
        self.authorize(inp, mode)
        return snapshot, path

    def query(self, inp):
        snapshot, _ = self.snapshot(inp)
        return call_helper(helpers().query, snapshot, inp.query_arguments())

    def record(self, inp):
        snapshot, _ = self.snapshot(inp)
        self._expected(snapshot, inp.expected_snapshot)
        return call_helper(helpers().record_detail, snapshot, inp.record_ref)

    @staticmethod
    def _expected(snapshot, expected):
        if expected and (snapshot["revision"], snapshot["sha256"]) != (
            expected.revision,
            expected.sha256,
        ):
            _error("stale_snapshot", conflict=True)

    def preview(self, inp):
        snapshot, _ = self.snapshot(inp, "correct")
        return call_helper(helpers().preview, snapshot, inp.intent_arguments())

    def commit(self, inp):
        _, path = self.authorize(inp, "correct")
        return call_helper(
            helpers().commit,
            path,
            inp.intent_arguments(),
            inp.preview_digest,
            actor="local-user:operator",
            authorize=lambda: self.authorize(inp, "correct"),
        )

    def evidence(self, inp):
        snapshot, path = self.snapshot(inp)
        self._expected(snapshot, inp.expected_snapshot)
        row = next(
            (
                x
                for x in snapshot["document"]["attachments"]
                if x["record_id"] == inp.attachment_ref
            ),
            None,
        )
        if row is None:
            _deny("unknown_attachment")
        if row["status"] in {"disposed", "quarantined"} or "quarantine_ref" in row:
            return {"attachment_ref": inp.attachment_ref, "state": row["status"]}
        if row["bytes"] > MAX_EVIDENCE_BYTES:
            return {
                "attachment_ref": inp.attachment_ref,
                "state": "too_large",
                "next_action": "use_authorized_host_view",
            }
        try:
            target = helpers().workspace.evidence_path(path.parent, row["relative_path"])
            entry, _ = self.authorize(inp)
            payload = _read_contained(
                path.parent,
                target.relative_to(path.parent),
                MAX_EVIDENCE_BYTES,
                (entry["root_device"], entry["root_inode"]),
            )
            self.authorize(inp)
            if len(payload) != row["bytes"] or hashlib.sha256(payload).hexdigest() != row["sha256"]:
                _error("evidence_integrity")
        except (OSError, ValueError):
            return {
                "attachment_ref": inp.attachment_ref,
                "state": "unavailable",
                "next_action": "inspect_original_custody",
            }
        name = re.sub(r"[^A-Za-z0-9._-]", "_", target.name)[:160] or "original"
        mime, inline = _media(name, payload)
        return {
            "attachment_ref": inp.attachment_ref,
            "state": "available",
            "filename": name,
            "content_type": mime,
            "inline_allowed": inline,
            "disposition": "inline" if inline else "attachment",
            "bytes": len(payload),
            "sha256": row["sha256"],
            "content_base64": base64.b64encode(payload).decode(),
            "snapshot": _snapshot_metadata(snapshot),
        }

    def report(self, inp):
        snapshot, path = self.snapshot(inp, "export")
        if inp.report_kind == "analysis":
            if inp.analysis_request_id is None:
                _error("analysis_request_required")
            from stackos.repositories.agent_requests import AgentRequestRepository

            request = AgentRequestRepository(self.session).get(
                project_id=inp.project_id, request_id=inp.analysis_request_id
            )
            if request.project_id != inp.project_id:
                _deny("wrong_project")
            meta = request.metadata_json or {}
            if not isinstance(meta, dict):
                _error("analysis_request_invalid")
            selected = meta.get("metadata_json", meta)
            if not isinstance(selected, dict) or selected.get("workspace_ref") != inp.workspace_ref:
                _deny("wrong_workspace")
            if request.status != "resolved":
                expiry = request.claim_expires_at
                if (
                    request.status == "claimed"
                    and expiry is not None
                    and expiry.replace(tzinfo=UTC) <= datetime.now(UTC)
                ):
                    return {
                        "state": "claim_expired",
                        "request_id": request.id,
                        "next_action": "inspect_agent_request",
                    }
                return {
                    "state": "awaiting_agent" if request.status == "new" else request.status,
                    "request_id": request.id,
                    "next_action": "inspect_agent_request",
                }
            if selected.get("access_generation") != inp.access_generation:
                return {
                    "state": "historical_access",
                    "request_id": request.id,
                    "next_action": "request_current_analysis",
                }
            ref = meta.get("external_result_ref")
            digest = meta.get("external_result_sha256")
            original_query = selected.get("query")
            requested_source = selected.get("source_snapshot")
            if (
                not isinstance(ref, str)
                or not digest
                or meta.get("state") != "published"
                or not isinstance(original_query, dict)
                or not requested_source
                or meta.get("source_snapshot") != requested_source
            ):
                return {"state": "result_unavailable", "request_id": request.id}
            current_query = call_helper(helpers().normalize_query, inp.query_arguments())
            requested_query = call_helper(helpers().normalize_query, original_query)
            transient = {"expected_snapshot", "offset", "limit"}
            if {k: v for k, v in current_query.items() if k not in transient} != {
                k: v for k, v in requested_query.items() if k not in transient
            }:
                _error("analysis_query_mismatch")
            result = call_helper(
                helpers().read_result,
                path,
                ref,
                expected_digest=digest,
                expected_query=original_query,
            )
            if result["result"]["source_snapshot"] != requested_source:
                _error("analysis_source_mismatch")
            if result["result"]["request_id"] != str(request.id):
                _error("analysis_request_mismatch")
            self.authorize(inp, "export")
            return {**result, "request_id": request.id}
        projected = call_helper(helpers().query, snapshot, inp.query_arguments())
        content = call_helper(helpers().render_report, projected, inp.format, snapshot=snapshot)
        self.authorize(inp, "export")
        return {
            "state": "current",
            "snapshot": _snapshot_metadata(snapshot),
            "query": projected["query"],
            "content_type": {
                "json": "application/json",
                "csv": "text/csv",
                "markdown": "text/markdown",
            }[inp.format],
            "filename": "finance-period."
            + {"json": "json", "csv": "csv", "markdown": "md"}[inp.format],
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_base64": base64.b64encode(content).decode(),
            "warnings": projected["warnings"],
        }


def _read_contained(root: Path, relative: Path, cap: int, identity: tuple[int, int]) -> bytes:
    """Anchor every component to no-follow directory descriptors."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != identity:
            raise ValueError("root changed")
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        with os.fdopen(child, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > cap:
                raise ValueError("unavailable evidence")
            payload = stream.read(cap + 1)
            after = os.fstat(stream.fileno())
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) or len(payload) != before.st_size:
                raise ValueError("changed evidence")
            return payload
    finally:
        os.close(fd)


def _media(name: str, payload: bytes) -> tuple[str, bool]:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf" and payload.startswith(b"%PDF-"):
        return "application/pdf", True
    if suffix == ".png" and payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", True
    if suffix in {".jpg", ".jpeg"} and payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", True
    if suffix == ".webp" and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp", True
    if suffix == ".txt":
        return "text/plain", True
    return "application/octet-stream", False
