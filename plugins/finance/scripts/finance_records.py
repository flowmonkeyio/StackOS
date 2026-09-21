"""Pure current-record resolution for an external finance document.

Schema, file custody and writer authority are checked by finance_workspace.
This module does not infer financial identity, accounting treatment or completeness.
"""

from __future__ import annotations

from typing import Any


class CurrentRecords:
    def __init__(self, document: dict[str, Any]):
        self.document = document
        self.records: dict[str, dict[str, Any]] = {}
        self.collections: dict[str, str] = {}
        self.next: dict[str, str] = {}
        self.previous: dict[str, str] = {}
        self._edge_refs: dict[str, list[str]] = {}
        for collection, rows in document.items():
            if not isinstance(rows, list):
                continue
            for record in rows:
                ref = record["record_id"]
                if ref in self.records:
                    raise ValueError("Duplicate global record identity")
                self.records[ref], self.collections[ref] = record, collection
        for correction in document.get("corrections", []):
            state = correction["status"]
            if state != "applied":
                if state not in {"proposed", "rejected", "withdrawn"}:
                    raise ValueError("Unsupported correction status")
                continue
            if (
                not isinstance(correction.get("decision_ref"), str)
                or not correction["decision_ref"].strip()
            ):
                raise ValueError("Applied correction requires a decision reference")
            self._edge(
                correction["superseded_ref"], correction["replacement_ref"], correction["record_id"]
            )
        for ref, record in self.records.items():
            if "supersedes_ref" in record:
                self._edge(record["supersedes_ref"], ref, ref)
        for ref in self.next:
            self.resolve(ref)
        self._accounts = self._build_account_map()

    def _edge(self, old: str, new: str, evidence: str) -> None:
        if old not in self.records or new not in self.records:
            raise ValueError("Unknown supersession target")
        if self.collections[old] != self.collections[new]:
            raise ValueError("Cross-collection supersession target")
        if old == new:
            raise ValueError("Self-referential supersession")
        if old in self.next and self.next[old] != new:
            raise ValueError("Forked supersession relationship")
        if new in self.previous and self.previous[new] != old:
            raise ValueError("Merged supersession relationship")
        self.next[old], self.previous[new] = new, old
        refs = self._edge_refs.setdefault(old, [])
        if evidence not in refs:
            refs.append(evidence)

    def resolve(self, ref: str) -> str:
        if ref not in self.records:
            raise ValueError("Unknown canonical reference")
        seen = set()
        while ref in self.next:
            if ref in seen:
                raise ValueError("Supersession cycle")
            seen.add(ref)
            ref = self.next[ref]
        return ref

    def root(self, ref: str) -> str:
        self.resolve(ref)
        while ref in self.previous:
            ref = self.previous[ref]
        return ref

    def lineage(self, ref: str) -> list[str]:
        current, result = self.root(ref), []
        while current in self.next:
            result.extend(self._edge_refs[current])
            current = self.next[current]
        return result

    def active(self, collection: str) -> list[dict[str, Any]]:
        rows = self.document.get(collection)
        if not isinstance(rows, list):
            raise ValueError("Unknown record collection")
        return [record for record in rows if record["record_id"] not in self.next]

    def account_identities(self) -> list[dict[str, Any]]:
        return [
            record
            for record in self.active("operating_settings")
            if "account_identity" in record and record["status"] == "confirmed"
        ]

    def _build_account_map(self) -> dict[str, dict[str, Any]]:
        if "operating_settings" not in self.document:
            return {}
        mapping: dict[str, dict[str, Any]] = {}
        for setting in self.account_identities():
            if not setting.get("decision_refs"):
                raise ValueError("Confirmed account identity requires decision evidence")
            identity = setting["account_identity"]
            if not identity.get("display_name", "").strip() or not identity.get("aliases"):
                raise ValueError("Account identity requires display name and exact aliases")
            aliases = set(identity["aliases"])
            current = setting["record_id"]
            while True:
                aliases.add(current)
                if current not in self.previous:
                    break
                current = self.previous[current]
            aliases.update(self.resolve(ref) for ref in list(aliases) if ref in self.records)
            for alias in aliases:
                if alias in mapping and mapping[alias]["record_id"] != setting["record_id"]:
                    raise ValueError("Ambiguous confirmed account alias")
                mapping[alias] = setting
        # Old source refs must resolve the same way as the mapped current source.
        for ref in self.records:
            resolved = self.resolve(ref)
            if resolved in mapping:
                if ref in mapping and mapping[ref]["record_id"] != mapping[resolved]["record_id"]:
                    raise ValueError("Ambiguous corrected account alias")
                mapping[ref] = mapping[resolved]
        return mapping

    def account_map(self) -> dict[str, dict[str, Any]]:
        return dict(self._accounts)

    def select(
        self,
        collection: str,
        *,
        record_ids: list[str] | None = None,
        offset: int = 0,
        limit: int = 50,
        current: bool = True,
    ) -> dict[str, Any]:
        """Bounded ID/collection lookup, not a query language or financial aggregate."""
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("Lookup requires limit 1..200 and a nonnegative offset")
        rows = self.active(collection) if current else self.document.get(collection)
        if not isinstance(rows, list):
            raise ValueError("Unknown record collection")
        if record_ids:
            if len(record_ids) > 200:
                raise ValueError("At most 200 record IDs per lookup")
            for ref in record_ids:
                if self.collections.get(ref) != collection:
                    raise ValueError("Requested ID is unknown or belongs to another collection")
            selected = {self.resolve(ref) if current else ref for ref in record_ids}
            rows = [record for record in rows if record["record_id"] in selected]
        rows = sorted(rows, key=lambda record: record["record_id"])
        return {
            "collection": collection,
            "current_only": current,
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < len(rows),
            "records": rows[offset : offset + limit],
        }
