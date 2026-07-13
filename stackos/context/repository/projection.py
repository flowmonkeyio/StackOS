"""Context query projection and item shaping."""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlmodel import col, select

from stackos.db.models import (
    ActionCall,
    AgentRequest,
    ContextIndexEntry,
    ContextSnapshot,
    Decision,
    Experiment,
    Learning,
    MetricSnapshot,
    ProjectEvent,
    Run,
)
from stackos.repositories.resources import (
    ArtifactOut,
    ArtifactRepository,
    ResourceRecordOut,
    ResourceRepository,
)

from .schema import ContextItemOut
from .support import ContextRepositorySupport
from .utils import _FETCH_MULTIPLIER, _FIELD_MAP, _has_all_tags, _safe_json


class ContextProjectionMixin(ContextRepositorySupport):
    def _source_items(
        self,
        *,
        source: str,
        project_id: int,
        fields: list[str],
        limit: int,
        tags: list[str] | None,
        domain: str | None,
        statuses: list[str] | None,
        plugin_slug: str | None,
        resource_keys: list[str] | None,
    ) -> list[ContextItemOut]:
        if source == "resources":
            return self._resource_items(
                project_id=project_id,
                fields=fields,
                limit=limit,
                tags=tags,
                domain=domain,
                statuses=statuses,
                plugin_slug=plugin_slug,
                resource_keys=resource_keys,
            )
        if source == "artifacts":
            return self._artifact_items(
                project_id=project_id,
                fields=fields,
                limit=limit,
                tags=tags,
                domain=domain,
                statuses=statuses,
                plugin_slug=plugin_slug,
            )
        if source == "agent_requests":
            conditions: list[Any] = []
            if statuses:
                conditions.append(col(AgentRequest.status).in_(statuses))
            rows = self._project_rows(
                AgentRequest,
                project_id=project_id,
                limit=limit,
                conditions=conditions,
                unbounded=bool(tags or domain),
            )
            return [
                self._item_from_agent_request(row, fields)
                for row in rows
                if _has_all_tags((row.metadata_json or {}).get("tags"), tags)
                and (domain is None or (row.metadata_json or {}).get("domain") == domain)
            ][: limit * _FETCH_MULTIPLIER]
        if source == "action_calls":
            conditions = []
            if statuses:
                conditions.append(col(ActionCall.status).in_(statuses))
            if plugin_slug is not None:
                conditions.append(col(ActionCall.plugin_slug) == plugin_slug)
            rows = self._project_rows(
                ActionCall,
                project_id=project_id,
                limit=limit,
                conditions=conditions,
                unbounded=bool(tags or domain),
            )
            return [
                self._item_from_action_call(row, fields)
                for row in rows
                if _has_all_tags((row.metadata_json or {}).get("tags"), tags)
                and (domain is None or (row.metadata_json or {}).get("domain") == domain)
            ][: limit * _FETCH_MULTIPLIER]
        if source == "runs":
            if tags or domain is not None:
                return []
            return self._run_items(
                project_id=project_id,
                fields=fields,
                limit=limit,
                statuses=statuses,
            )
        if source == "events":
            if domain is not None or statuses:
                return []
            rows = self._project_rows(
                ProjectEvent,
                project_id=project_id,
                limit=limit,
                unbounded=bool(tags),
            )
            return [
                self._item_from_event(row, fields)
                for row in rows
                if _has_all_tags(row.tags_json, tags)
            ][: limit * _FETCH_MULTIPLIER]
        if source == "index":
            conditions: list[Any] = []
            if domain is not None:
                conditions.append(col(ContextIndexEntry.domain) == domain)
            if statuses:
                conditions.append(col(ContextIndexEntry.status).in_(statuses))
            rows = self._project_rows(
                ContextIndexEntry,
                project_id=project_id,
                limit=limit,
                conditions=conditions,
                unbounded=bool(tags),
            )
            return [
                self._item_from_index(row, fields)
                for row in rows
                if _has_all_tags(row.tags_json, tags)
            ][: limit * _FETCH_MULTIPLIER]
        if source == "snapshots":
            if tags or domain is not None or statuses:
                return []
            rows = self._project_rows(ContextSnapshot, project_id=project_id, limit=limit)
            return [self._item_from_snapshot(row, fields) for row in rows]
        if source == "learnings":
            conditions = []
            if domain is not None:
                conditions.append(col(Learning.domain) == domain)
            if statuses:
                conditions.append(
                    or_(
                        col(Learning.status).in_(statuses),
                        col(Learning.review_state).in_(statuses),
                    )
                )
            rows = self._project_rows(
                Learning,
                project_id=project_id,
                limit=limit,
                conditions=conditions,
                unbounded=bool(tags),
            )
            return [
                self._item_from_learning(row, fields)
                for row in rows
                if _has_all_tags(row.tags_json, tags)
            ][: limit * _FETCH_MULTIPLIER]
        if source == "experiments":
            conditions = []
            if domain is not None:
                conditions.append(Experiment.domain == domain)
            if statuses:
                conditions.append(Experiment.status.in_(statuses))  # type: ignore[attr-defined]
            rows = self._project_rows(
                Experiment,
                project_id=project_id,
                limit=limit,
                conditions=conditions,
                unbounded=bool(tags),
            )
            return [
                self._item_from_experiment(row, fields)
                for row in rows
                if _has_all_tags((row.metadata_json or {}).get("tags"), tags)
            ][: limit * _FETCH_MULTIPLIER]
        if source == "decisions":
            conditions = []
            if statuses:
                conditions.append(Decision.status.in_(statuses))  # type: ignore[attr-defined]
            rows = self._project_rows(
                Decision,
                project_id=project_id,
                limit=limit,
                conditions=conditions,
                unbounded=bool(tags),
            )
            return [
                self._item_from_decision(row, fields)
                for row in rows
                if _has_all_tags(row.tags_json, tags)
            ][: limit * _FETCH_MULTIPLIER]
        if tags or domain is not None or statuses:
            return []
        rows = self._project_rows(MetricSnapshot, project_id=project_id, limit=limit)
        return [self._item_from_metric(row, fields) for row in rows]

    def _artifact_items(
        self,
        *,
        project_id: int,
        fields: list[str],
        limit: int,
        tags: list[str] | None,
        domain: str | None,
        statuses: list[str] | None,
        plugin_slug: str | None,
    ) -> list[ContextItemOut]:
        page = ArtifactRepository(self._s).query(
            project_id=project_id,
            plugin_slug=plugin_slug,
            include_inactive=True,
            limit=limit,
        )
        rows = sorted(page.items, key=lambda item: item.updated_at, reverse=True)
        filtered = [
            item
            for item in rows
            if (not statuses or item.status in statuses)
            and _has_all_tags((item.metadata_json or {}).get("tags"), tags)
            and (domain is None or (item.metadata_json or {}).get("domain") == domain)
        ]
        return [self._item_from_artifact(item, fields) for item in filtered[:limit]]

    def _item_from_artifact(self, row: ArtifactOut, fields: list[str]) -> ContextItemOut:
        values = row.model_dump(mode="python")
        return ContextItemOut(
            source="artifacts",
            id=row.id,
            project_id=row.project_id,
            title=row.name or row.kind,
            occurred_at=row.updated_at,
            fields={field: _safe_json(values[field]) for field in fields if field in values},
            provenance={
                "table": "artifacts",
                "id": row.id,
                "plugin_slug": row.plugin_slug,
                "kind": row.kind,
            },
        )

    def _item_from_agent_request(
        self,
        row: AgentRequest,
        fields: list[str],
    ) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("agent_requests", row, fields)
        if "status" in data:
            data["status"] = str(data["status"])
        if "attention_status" in data:
            data["attention_status"] = str(data["attention_status"])
        if "attachments" in fields:
            data["attachments"] = _safe_json((row.metadata_json or {}).get("attachments", []))
        return ContextItemOut(
            source="agent_requests",
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            occurred_at=row.updated_at,
            fields=data,
            provenance={"table": "agent_requests", "id": row.id},
        )

    def _item_from_action_call(
        self,
        row: ActionCall,
        fields: list[str],
    ) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("action_calls", row, fields)
        if "status" in data:
            data["status"] = str(data["status"])
        return ContextItemOut(
            source="action_calls",
            id=row.id,
            project_id=row.project_id,
            title=row.action_key,
            occurred_at=row.completed_at or row.created_at,
            fields=data,
            provenance={
                "table": "action_calls",
                "id": row.id,
                "plugin_slug": row.plugin_slug,
                "action_key": row.action_key,
            },
        )

    def _resource_items(
        self,
        *,
        project_id: int,
        fields: list[str],
        limit: int,
        tags: list[str] | None,
        domain: str | None,
        statuses: list[str] | None,
        plugin_slug: str | None,
        resource_keys: list[str] | None,
    ) -> list[ContextItemOut]:
        keys = list(dict.fromkeys(resource_keys or []))
        pages = (
            [
                ResourceRepository(self._s).query_records(
                    project_id=project_id,
                    plugin_slug=plugin_slug,
                    resource_key=key,
                    limit=limit,
                )
                for key in keys
            ]
            if keys
            else [
                ResourceRepository(self._s).query_records(
                    project_id=project_id,
                    plugin_slug=plugin_slug,
                    limit=limit,
                )
            ]
        )
        rows = [item for page in pages for item in page.items]
        rows.sort(key=lambda item: item.updated_at, reverse=True)
        filtered = [
            item
            for item in rows
            if _has_all_tags(item.data_json.get("tags"), tags)
            and (domain is None or item.data_json.get("domain") == domain)
            and (not statuses or item.data_json.get("status") in statuses)
        ]
        return [self._item_from_resource(item, fields) for item in filtered[:limit]]

    def _item_from_resource(
        self,
        row: ResourceRecordOut,
        fields: list[str],
    ) -> ContextItemOut:
        values: dict[str, Any] = {
            "plugin_slug": row.plugin_slug,
            "resource_key": row.resource_key,
            "external_id": row.external_id,
            "title": row.title,
            "status": row.data_json.get("status"),
            "data_json": row.data_json,
            "provenance_json": row.provenance_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        return ContextItemOut(
            source="resources",
            id=row.id,
            project_id=row.project_id,
            title=row.title or row.resource_key,
            occurred_at=row.updated_at,
            fields={field: _safe_json(values[field]) for field in fields if field in values},
            provenance={
                "table": "resource_records",
                "id": row.id,
                "plugin_slug": row.plugin_slug,
                "resource_key": row.resource_key,
            },
        )

    def _project_rows(
        self,
        model: type[Any],
        *,
        project_id: int,
        limit: int,
        conditions: list[Any] | None = None,
        unbounded: bool = False,
    ) -> list[Any]:
        stmt = (
            select(model)
            .where(col(model.project_id) == project_id, *(conditions or []))
            .order_by(col(model.id).desc())
        )
        if not unbounded:
            stmt = stmt.limit(limit * _FETCH_MULTIPLIER)
        return list(self._s.exec(stmt).all())

    def _run_items(
        self,
        *,
        project_id: int,
        fields: list[str],
        limit: int,
        statuses: list[str] | None,
    ) -> list[ContextItemOut]:
        stmt = select(Run).where(col(Run.project_id) == project_id)
        if statuses:
            stmt = stmt.where(col(Run.status).in_(statuses))
        rows = list(
            self._s.exec(stmt.order_by(col(Run.id).desc()).limit(limit * _FETCH_MULTIPLIER)).all()
        )
        return [self._item_from_run(row, fields) for row in rows]

    def _fields(self, source: str, row: Any, fields: list[str]) -> dict[str, Any]:
        return {
            field: _safe_json(getattr(row, field))
            for field in fields
            if field in _FIELD_MAP[source] and hasattr(row, field)
        }

    def _item_from_run(self, row: Run, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("runs", row, fields)
        data["kind"] = str(data["kind"]) if "kind" in data else data.get("kind")
        data["status"] = str(data["status"]) if "status" in data else data.get("status")
        if "completed_at" in fields:
            data["completed_at"] = _safe_json(row.ended_at)
        return ContextItemOut(
            source="runs",
            id=row.id,
            project_id=row.project_id,
            title=row.kind.value,
            occurred_at=row.started_at,
            fields=data,
            provenance={"table": "runs", "id": row.id},
        )

    def _item_from_event(self, row: ProjectEvent, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("events", row, fields)
        if "tags" in fields:
            data["tags"] = list(row.tags_json or [])
        return ContextItemOut(
            source="events",
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            occurred_at=row.occurred_at,
            fields=data,
            provenance={"table": "project_events", "id": row.id},
        )

    def _item_from_index(self, row: ContextIndexEntry, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("index", row, fields)
        if "tags" in fields:
            data["tags"] = list(row.tags_json or [])
        return ContextItemOut(
            source="index",
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            occurred_at=row.occurred_at,
            fields=data,
            provenance={"table": "context_index_entries", "id": row.id},
        )

    def _item_from_snapshot(self, row: ContextSnapshot, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        return ContextItemOut(
            source="snapshots",
            id=row.id,
            project_id=row.project_id,
            title=row.name,
            occurred_at=row.created_at,
            fields=self._fields("snapshots", row, fields),
            provenance={"table": "context_snapshots", "id": row.id},
        )

    def _item_from_learning(self, row: Learning, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("learnings", row, fields)
        if "tags" in fields:
            data["tags"] = list(row.tags_json or [])
        return ContextItemOut(
            source="learnings",
            id=row.id,
            project_id=row.project_id,
            title=row.statement[:120],
            occurred_at=row.updated_at,
            fields=data,
            provenance={"table": "learnings", "id": row.id},
        )

    def _item_from_experiment(self, row: Experiment, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("experiments", row, fields)
        if "variants" in fields:
            data["variants"] = [
                variant.model_dump(mode="json") for variant in self._variant_outs(row)
            ]
        return ContextItemOut(
            source="experiments",
            id=row.id,
            project_id=row.project_id,
            title=row.name or row.key,
            occurred_at=row.updated_at,
            fields=data,
            provenance={"table": "experiments", "id": row.id},
        )

    def _item_from_decision(self, row: Decision, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        data = self._fields("decisions", row, fields)
        if "tags" in fields:
            data["tags"] = list(row.tags_json or [])
        return ContextItemOut(
            source="decisions",
            id=row.id,
            project_id=row.project_id,
            title=row.title,
            occurred_at=row.created_at,
            fields=data,
            provenance={"table": "decisions", "id": row.id},
        )

    def _item_from_metric(self, row: MetricSnapshot, fields: list[str]) -> ContextItemOut:
        assert row.id is not None
        return ContextItemOut(
            source="metrics",
            id=row.id,
            project_id=row.project_id,
            title=row.metric_key,
            occurred_at=row.captured_at,
            fields=self._fields("metrics", row, fields),
            provenance={"table": "metric_snapshots", "id": row.id},
        )
