"""Task Results is a read projection, never a producer of content or associations."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy import select as sql_select
from sqlmodel import Session, col, select

from stackos.artifacts import redact_secrets
from stackos.context.repository import ContextRepository
from stackos.db.models import (
    Artifact,
    ContextSnapshot,
    Decision,
    ExecutionContextArtifact,
    Experiment,
    Learning,
    Plugin,
    Resource,
    ResourceRecord,
)
from stackos.repositories.resources import ArtifactRepository, ResourceRepository
from stackos.repositories.tracker.inspection_schema import (
    ResultSourceKind,
    TaskResultAssociation,
    TaskResultItem,
    TaskResultsOut,
)
from stackos.repositories.tracker.inspection_scope import (
    InspectionScope,
    decode_cursor,
    older_than,
    page_items,
    validate_limit,
)


class TaskResultsMixin:
    _s: Session

    def task_results(
        self,
        *,
        project_id: int,
        task_key: str,
        source_kind: ResultSourceKind | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> TaskResultsOut:
        validate_limit(limit)
        scope = InspectionScope(self._s, project_id, task_key)
        binding = ["results", project_id, scope.task.id, task_key, source_kind]
        last = decode_cursor(cursor, binding)
        linked_records = {link.resource_record_id for link in scope.links} - {None}
        linked_artifacts = {link.artifact_id for link in scope.links} - {None}
        record_condition = or_(
            col(ResourceRecord.id).in_(linked_records),
            scope.provenance_condition(ResourceRecord.provenance_json),
        )
        record_ids = select(ResourceRecord.id).where(
            ResourceRecord.project_id == project_id, record_condition
        )
        context_artifacts = list(
            self._s.exec(
                select(ExecutionContextArtifact).where(
                    ExecutionContextArtifact.project_id == project_id,
                    col(ExecutionContextArtifact.context_id).in_(scope.contexts),
                )
            ).all()
        )
        snapshot_ids = select(ContextSnapshot.id).where(
            ContextSnapshot.project_id == project_id, col(ContextSnapshot.run_id).in_(scope.runs)
        )
        # json_each admits only integer array entries, never string/bool IDs.
        runs = func.json_each(Experiment.linked_run_ids_json).table_valued("value", "type")
        experiment_condition = (
            sql_select(1)
            .select_from(runs)
            .where(runs.c.type == "integer", runs.c.value.in_(scope.runs))
            .exists()
        )
        candidates: list[tuple[ResultSourceKind, Any, Any]] = [
            ("resource_record", ResourceRecord, record_condition),
            (
                "artifact",
                Artifact,
                or_(
                    col(Artifact.id).in_(linked_artifacts),
                    scope.provenance_condition(Artifact.provenance_json),
                    col(Artifact.resource_record_id).in_(record_ids),
                    col(Artifact.id).in_({a.artifact_id for a in context_artifacts}),
                ),
            ),
            ("decision", Decision, col(Decision.run_id).in_(scope.runs)),
            ("learning", Learning, col(Learning.source_snapshot_id).in_(snapshot_ids)),
            (
                "experiment",
                Experiment,
                and_(
                    func.json_type(Experiment.linked_run_ids_json) == "array", experiment_condition
                ),
            ),
        ]
        items: list[TaskResultItem] = []
        for kind, model, condition in candidates:
            if source_kind is not None and source_kind != kind:
                continue
            rows = self._s.exec(
                select(model)
                .where(
                    model.project_id == project_id,
                    condition,
                    older_than(model.created_at, model.id, kind, last),
                )
                .order_by(model.created_at.desc(), model.id.desc())
                .limit(limit + 1)
            ).all()
            for row in rows:
                reasons: list[tuple[str, str, str]] = []
                if isinstance(row, (ResourceRecord, Artifact)):
                    reasons.extend(scope.provenance_reasons(row.provenance_json))
                    field = (
                        "resource_record_id" if isinstance(row, ResourceRecord) else "artifact_id"
                    )
                    reasons.extend(
                        ("tracker_link", str(link.id), f"tracker_ticket_links.{link.id}.{field}")
                        for link in scope.links
                        if getattr(link, field) == row.id
                    )
                if isinstance(row, Artifact):
                    for association in context_artifacts:
                        if association.artifact_id == row.id:
                            reasons.extend(
                                (
                                    "execution_context",
                                    str(scope.contexts[association.context_id].context_ref),
                                    f"execution_context_links.{link.id}→execution_context_artifacts.{association.id}",
                                )
                                for link in scope.context_links
                                if link.context_id == association.context_id
                            )
                    record = self._s.exec(
                        select(ResourceRecord).where(
                            ResourceRecord.id == row.resource_record_id,
                            ResourceRecord.project_id == project_id,
                            record_condition,
                        )
                    ).first()
                    if record is not None:
                        inherited = scope.provenance_reasons(record.provenance_json)
                        inherited.extend(
                            (
                                "tracker_link",
                                str(link.id),
                                f"tracker_ticket_links.{link.id}.resource_record_id",
                            )
                            for link in scope.links
                            if link.resource_record_id == record.id
                        )
                        reasons.extend(
                            (
                                "artifact.resource_record_id",
                                str(record.id),
                                f"artifact.resource_record_id→{path}",
                            )
                            for _, _, path in inherited
                        )
                if isinstance(row, Decision):
                    reasons.append(("decision.run_id", str(row.run_id), "run_id"))
                if isinstance(row, Learning):
                    snapshot = self._s.get(ContextSnapshot, row.source_snapshot_id)
                    assert snapshot is not None
                    reasons.append(
                        (
                            "learning.source_snapshot_id",
                            str(snapshot.run_id),
                            f"source_snapshot_id→context_snapshots.{snapshot.id}.run_id",
                        )
                    )
                if isinstance(row, Experiment):
                    reasons.extend(
                        (
                            "experiment.linked_run_ids_json",
                            str(run),
                            f"linked_run_ids_json[{index}]",
                        )
                        for index, run in enumerate(row.linked_run_ids_json or [])
                        if type(run) is int and run in scope.runs
                    )
                data = self._result_data(row)
                items.append(
                    TaskResultItem(
                        source_kind=kind,
                        source_id=row.id,
                        occurred_at=row.created_at,
                        data=data,
                        associations=[
                            TaskResultAssociation(kind=k, ref=r, path=p)
                            for k, r, p in sorted(set(reasons))
                        ],
                    )
                )
        page, next_cursor = page_items(items, limit, binding)
        return TaskResultsOut(
            project_id=project_id,
            task_id=scope.task_id,
            task_key=task_key,
            source_kind=source_kind,
            items=page,
            next_cursor=next_cursor,
            warnings=scope.warnings,
        )

    def _result_data(self, row: Any) -> Any:
        context = ContextRepository(self._s)
        data: Any
        if isinstance(row, ResourceRecord):
            resource = self._s.get(Resource, row.resource_id)
            assert resource is not None
            plugin = self._s.get(Plugin, resource.plugin_id)
            assert plugin is not None
            data = ResourceRepository(self._s)._record_out(row, resource, plugin)
        elif isinstance(row, Artifact):
            plugin = self._s.get(Plugin, row.plugin_id) if row.plugin_id is not None else None
            data = ArtifactRepository(self._s)._artifact_out(row, plugin)
        elif isinstance(row, Learning):
            data = context._learning_out(row)
        elif isinstance(row, Decision):
            data = context._decision_out(row)
        else:
            data = context._experiment_out(row)
        return type(data).model_validate(redact_secrets(data.model_dump()))
