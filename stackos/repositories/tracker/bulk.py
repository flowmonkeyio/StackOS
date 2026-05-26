# mypy: disable-error-code=attr-defined
"""Bulk tracker ticket validation, preview, create, and update flows."""

from __future__ import annotations

from typing import (
    Any,
    Literal,
)

from sqlmodel import select

from stackos.db.models import (
    TaskTracker,
    TrackerItemStatus,
    TrackerSourceKind,
    TrackerTask,
    TrackerTicket,
    TrackerTicketDependency,
    TrackerTicketKind,
)
from stackos.repositories.base import (
    ConflictError,
    Envelope,
    NotFoundError,
    ValidationError,
)
from stackos.repositories.tracker.schema import (
    TrackerDependencyPreviewOut,
    TrackerListIssueOut,
    TrackerListItemResultOut,
    TrackerMutationOut,
)
from stackos.repositories.tracker.utils import (
    DEPENDENCY_PATCH_FIELDS,
    _clean_text,
    _required_id,
    _slug,
    _utcnow,
)


class TrackerBulkMixin:
    """Bulk tracker ticket validation, preview, create, and update flows."""

    def validate_ticket_list(
        self,
        *,
        project_id: int,
        ticket_list_json: dict[str, Any],
    ) -> TrackerMutationOut:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            return TrackerMutationOut(
                tracker=self._empty_tracker_out(project_id=project_id),
                errors=[
                    TrackerListIssueOut(
                        field="task_key",
                        message="tracker task not found for list validation",
                    )
                ],
                valid=False,
                dry_run=True,
                rev=0,
            )
        task, specs, errors, warnings = self._prepare_ticket_list(tracker, ticket_list_json)
        return TrackerMutationOut(
            tracker=self._tracker_out(tracker),
            task=self._task_out(task) if task is not None else None,
            results=[
                TrackerListItemResultOut(index=spec["index"], action="validated", key=spec["key"])
                for spec in specs
            ],
            errors=errors,
            warnings=warnings,
            valid=not errors,
            dry_run=True,
            rev=tracker.rev,
        )

    def create_ticket_list(
        self,
        *,
        project_id: int,
        ticket_list_json: dict[str, Any],
        actor: str | None = None,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            out = TrackerMutationOut(
                tracker=self._empty_tracker_out(project_id=project_id),
                errors=[
                    TrackerListIssueOut(
                        field="task_key",
                        message="tracker task not found for list create",
                    )
                ],
                valid=False,
                rev=0,
            )
            return Envelope(data=out, project_id=project_id)
        task, specs, errors, warnings = self._prepare_ticket_list(tracker, ticket_list_json)
        if task is None or errors:
            out = TrackerMutationOut(
                tracker=self._tracker_out(tracker),
                task=self._task_out(task) if task is not None else None,
                results=[
                    TrackerListItemResultOut(index=spec["index"], action="error", key=spec["key"])
                    for spec in specs
                ],
                errors=errors,
                warnings=warnings,
                valid=False,
                rev=tracker.rev,
            )
            return Envelope(data=out, project_id=project_id)

        now = _utcnow()
        created: list[TrackerTicket] = []
        created_by_key: dict[str, TrackerTicket] = {}
        for spec in specs:
            ticket = self._create_ticket_row(
                tracker=tracker,
                task=task,
                key=spec["key"],
                title=spec["title"],
                goal=spec["goal"],
                status=spec["status"],
                kind=spec["kind"],
                assignee=spec["assignee"],
                priority_key=spec["priority_key"],
                lane_key=spec["lane_key"],
                blocker_reason=spec["blocker_reason"],
                outcome=spec["outcome"],
                effort=spec["effort"],
                source_kind=spec["source_kind"],
                source_json=spec["source_json"],
                definition_of_done_json=spec["definition_of_done_json"],
                constraints_json=spec["constraints_json"],
                expected_changes_json=spec["expected_changes_json"],
                allowed_paths_json=spec["allowed_paths_json"],
                completion_evidence_json=spec["completion_evidence_json"],
                context_json=spec["context_json"],
                metadata_json=spec["metadata_json"],
                created_by=spec["created_by"] or actor,
                now=now,
            )
            created.append(ticket)
            created_by_key[ticket.key] = ticket

        for spec in specs:
            ticket = created_by_key[spec["key"]]
            parent_key = spec["parent_ticket_key"]
            if parent_key:
                parent = created_by_key.get(parent_key) or self._ticket_by_key(
                    tracker.id, parent_key
                )
                ticket.parent_ticket_id = parent.id
                self._s.add(ticket)
            for dependency_key in spec["dependency_keys"]:
                dependency = created_by_key.get(dependency_key) or self._ticket_by_key(
                    tracker.id, dependency_key
                )
                self._add_dependency(tracker, ticket, dependency)
            for reference in spec["references_json"]:
                self._add_reference(tracker, ticket, reference)
            self._record_revision(
                tracker,
                actor=actor or spec["created_by"],
                change_kind="list-create",
                entity_kind="ticket",
                entity_id=ticket.id,
                entity_key=ticket.key,
                summary=f"Created ticket {ticket.key} from list.",
                after_json=self._ticket_snapshot(ticket),
                commit=False,
            )

        self._sync_task_status(task, now=now)
        self._s.commit()
        for ticket in created:
            self._s.refresh(ticket)
        out = TrackerMutationOut(
            tracker=self._tracker_out(tracker),
            task=self._task_out(task),
            tickets=self._ticket_out_many(created),
            dependencies=self._dependency_out_for_tickets(created),
            results=[
                TrackerListItemResultOut(
                    index=spec["index"],
                    action="created",
                    key=spec["key"],
                    id=created_by_key[spec["key"]].id,
                    ticket=self._ticket_out(created_by_key[spec["key"]]),
                )
                for spec in specs
            ],
            warnings=warnings,
            valid=True,
            rev=tracker.rev,
        )
        return Envelope(data=out, project_id=project_id)

    def update_ticket_list(
        self,
        *,
        project_id: int,
        updates_json: list[dict[str, Any]],
        actor: str | None = None,
        dry_run: bool = False,
    ) -> Envelope[TrackerMutationOut]:
        tracker = self._tracker_or_none(project_id=project_id)
        if tracker is None:
            out = TrackerMutationOut(
                tracker=self._empty_tracker_out(project_id=project_id),
                errors=[TrackerListIssueOut(message="tracker not found for ticket list update")],
                valid=False,
                rev=0,
            )
            return Envelope(data=out, project_id=project_id)
        if not isinstance(updates_json, list):
            raise ValidationError("updates_json must be a list")
        if dry_run:
            return self._preview_ticket_list_update(
                project_id=project_id,
                tracker=tracker,
                updates_json=updates_json,
            )

        results: list[TrackerListItemResultOut] = []
        changed: list[TrackerTicket] = []
        for index, item in enumerate(updates_json):
            if not isinstance(item, dict):
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        error="update entries must be objects",
                    )
                )
                continue
            patch_json = item.get("patch_json", item.get("patch"))
            if not isinstance(patch_json, dict):
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                        error="update entry patch_json must be an object",
                    )
                )
                continue
            if not patch_json:
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="noop",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                    )
                )
                continue
            try:
                with self._s.begin_nested():
                    ticket = self._ticket_from_list_update(tracker.id, item)
                    task = self._s.get(TrackerTask, ticket.task_id)
                    if task is None:
                        raise NotFoundError(
                            "ticket task not found", data={"ticket_key": ticket.key}
                        )
                    before = self._ticket_snapshot(ticket)
                    self._apply_ticket_patch(tracker, ticket, patch_json)
                    self._sync_task_status(task, now=_utcnow())
                    self._record_revision(
                        tracker,
                        actor=actor,
                        change_kind="list-update",
                        entity_kind="ticket",
                        entity_id=ticket.id,
                        entity_key=ticket.key,
                        summary=f"Updated ticket {ticket.key} from list.",
                        before_json=before,
                        after_json=self._ticket_snapshot(ticket),
                        patch_json=patch_json,
                        commit=False,
                    )
                changed.append(ticket)
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="updated",
                        key=ticket.key,
                        id=ticket.id,
                        changed_fields=list(patch_json.keys()),
                        ticket=self._ticket_out(ticket),
                    )
                )
            except (ConflictError, NotFoundError, ValidationError, ValueError) as exc:
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                        error=str(exc),
                    )
                )

        if changed:
            self._s.commit()
            for ticket in changed:
                self._s.refresh(ticket)
        errors = [
            TrackerListIssueOut(
                index=result.index,
                key=result.key,
                message=result.error or "ticket update failed",
            )
            for result in results
            if result.action == "error"
        ]
        out = TrackerMutationOut(
            tracker=self._tracker_out(tracker),
            tickets=self._ticket_out_many(changed),
            dependencies=self._dependency_out_for_tickets(changed),
            results=results,
            errors=errors,
            valid=not errors,
            rev=tracker.rev,
        )
        return Envelope(data=out, project_id=project_id)

    def _prepare_ticket_list(
        self,
        tracker: TaskTracker,
        ticket_list_json: dict[str, Any],
    ) -> tuple[TrackerTask | None, list[dict[str, Any]], list[TrackerListIssueOut], list[str]]:
        errors: list[TrackerListIssueOut] = []
        warnings: list[str] = []
        specs: list[dict[str, Any]] = []
        if not isinstance(ticket_list_json, dict):
            return (
                None,
                [],
                [TrackerListIssueOut(message="ticket_list_json must be an object")],
                warnings,
            )
        task_key = str(ticket_list_json.get("task_key") or "").strip()
        if not task_key:
            errors.append(TrackerListIssueOut(field="task_key", message="task_key is required"))
            return None, specs, errors, warnings
        task = self._task_by_key(tracker.id, task_key, missing_ok=True)
        if task is None:
            errors.append(
                TrackerListIssueOut(
                    field="task_key",
                    key=task_key,
                    message="tracker task not found",
                )
            )
            return None, specs, errors, warnings

        raw_tickets = ticket_list_json.get("tickets")
        if not isinstance(raw_tickets, list) or not raw_tickets:
            errors.append(
                TrackerListIssueOut(field="tickets", message="tickets must be a non-empty list")
            )
            return task, specs, errors, warnings

        seen_keys: set[str] = set()
        for index, raw in enumerate(raw_tickets):
            if not isinstance(raw, dict):
                errors.append(
                    TrackerListIssueOut(
                        index=index, field="tickets", message="ticket must be an object"
                    )
                )
                continue
            key = _slug(str(raw.get("key") or ""), fallback="", max_length=180)
            if not key:
                errors.append(
                    TrackerListIssueOut(index=index, field="key", message="ticket key is required")
                )
                continue
            if key in seen_keys:
                errors.append(
                    TrackerListIssueOut(
                        index=index, key=key, field="key", message="duplicate ticket key"
                    )
                )
            seen_keys.add(key)
            if self._ticket_by_key(tracker.id, key, missing_ok=True) is not None:
                errors.append(
                    TrackerListIssueOut(
                        index=index,
                        key=key,
                        field="key",
                        message="tracker ticket key already exists",
                    )
                )
            title = _clean_text(raw.get("title")) if raw.get("title") is not None else ""
            if not title:
                title = key
                warnings.append(f"ticket {key} has no title; key will be used as title")
            status = self._list_enum(
                raw.get("status", TrackerItemStatus.NOT_STARTED.value),
                TrackerItemStatus,
                errors,
                index=index,
                key=key,
                field="status",
            )
            kind = self._list_enum(
                raw.get("kind", TrackerTicketKind.TICKET.value),
                TrackerTicketKind,
                errors,
                index=index,
                key=key,
                field="kind",
            )
            source_kind = self._list_enum(
                raw.get("source_kind", TrackerSourceKind.MANUAL.value),
                TrackerSourceKind,
                errors,
                index=index,
                key=key,
                field="source_kind",
            )
            dependency_keys = self._json_string_list(
                raw.get("dependency_keys", []),
                errors,
                index=index,
                key=key,
                field="dependency_keys",
            )
            references_json = raw.get("references_json", [])
            if not isinstance(references_json, list):
                errors.append(
                    TrackerListIssueOut(
                        index=index,
                        key=key,
                        field="references_json",
                        message="references_json must be a list",
                    )
                )
                references_json = []
            elif any(not isinstance(item, dict) for item in references_json):
                errors.append(
                    TrackerListIssueOut(
                        index=index,
                        key=key,
                        field="references_json",
                        message="references_json entries must be objects",
                    )
                )
                references_json = []
            completion_evidence_json = raw.get("completion_evidence_json")
            if completion_evidence_json is not None and not isinstance(
                completion_evidence_json, dict
            ):
                errors.append(
                    TrackerListIssueOut(
                        index=index,
                        key=key,
                        field="completion_evidence_json",
                        message="completion_evidence_json must be an object",
                    )
                )
                completion_evidence_json = None
            specs.append(
                {
                    "index": index,
                    "key": key,
                    "title": title,
                    "goal": _clean_text(raw.get("goal")),
                    "status": status or TrackerItemStatus.NOT_STARTED,
                    "kind": kind or TrackerTicketKind.TICKET,
                    "assignee": raw.get("assignee"),
                    "priority_key": str(raw.get("priority_key") or "p2"),
                    "lane_key": str(raw.get("lane_key") or "implementation"),
                    "parent_ticket_key": str(raw.get("parent_ticket_key") or "").strip() or None,
                    "dependency_keys": dependency_keys,
                    "blocker_reason": raw.get("blocker_reason"),
                    "outcome": raw.get("outcome"),
                    "effort": raw.get("effort"),
                    "source_kind": source_kind or TrackerSourceKind.MANUAL,
                    "source_json": raw.get("source_json")
                    if isinstance(raw.get("source_json"), dict)
                    else None,
                    "definition_of_done_json": self._json_string_list(
                        raw.get("definition_of_done_json", []),
                        errors,
                        index=index,
                        key=key,
                        field="definition_of_done_json",
                    ),
                    "constraints_json": self._json_string_list(
                        raw.get("constraints_json", []),
                        errors,
                        index=index,
                        key=key,
                        field="constraints_json",
                    ),
                    "expected_changes_json": self._json_string_list(
                        raw.get("expected_changes_json", []),
                        errors,
                        index=index,
                        key=key,
                        field="expected_changes_json",
                    ),
                    "allowed_paths_json": self._json_string_list(
                        raw.get("allowed_paths_json", []),
                        errors,
                        index=index,
                        key=key,
                        field="allowed_paths_json",
                    ),
                    "references_json": references_json,
                    "completion_evidence_json": completion_evidence_json,
                    "context_json": raw.get("context_json")
                    if isinstance(raw.get("context_json"), dict)
                    else None,
                    "metadata_json": raw.get("metadata_json")
                    if isinstance(raw.get("metadata_json"), dict)
                    else None,
                    "created_by": raw.get("created_by") or ticket_list_json.get("created_by"),
                }
            )

        by_key = {spec["key"]: spec for spec in specs}
        raw_dependencies = ticket_list_json.get("dependencies", [])
        if raw_dependencies is not None and not isinstance(raw_dependencies, list):
            errors.append(
                TrackerListIssueOut(field="dependencies", message="dependencies must be a list")
            )
        elif isinstance(raw_dependencies, list):
            for dep_index, raw_dep in enumerate(raw_dependencies):
                if not isinstance(raw_dep, dict):
                    errors.append(
                        TrackerListIssueOut(
                            index=dep_index,
                            field="dependencies",
                            message="dependency must be an object",
                        )
                    )
                    continue
                ticket_key = _slug(
                    str(raw_dep.get("ticket_key") or ""), fallback="", max_length=180
                )
                dependency_key = _slug(
                    str(
                        raw_dep.get("depends_on_ticket_key") or raw_dep.get("dependency_key") or ""
                    ),
                    fallback="",
                    max_length=180,
                )
                if not ticket_key or not dependency_key:
                    errors.append(
                        TrackerListIssueOut(
                            index=dep_index,
                            field="dependencies",
                            message="dependency requires ticket_key and depends_on_ticket_key",
                        )
                    )
                    continue
                target = by_key.get(ticket_key)
                if target is None:
                    errors.append(
                        TrackerListIssueOut(
                            index=dep_index,
                            key=ticket_key,
                            field="dependencies",
                            message="dependency ticket_key must refer to a list ticket",
                        )
                    )
                    continue
                if dependency_key not in target["dependency_keys"]:
                    target["dependency_keys"].append(dependency_key)

        for spec in specs:
            if spec["parent_ticket_key"]:
                self._validate_ticket_list_ref(
                    tracker, spec["parent_ticket_key"], by_key, errors, spec, "parent_ticket_key"
                )
            for dependency_key in spec["dependency_keys"]:
                self._validate_ticket_list_ref(
                    tracker, dependency_key, by_key, errors, spec, "dependency_keys"
                )
            if spec["key"] in spec["dependency_keys"]:
                errors.append(
                    TrackerListIssueOut(
                        index=spec["index"],
                        key=spec["key"],
                        field="dependency_keys",
                        message="ticket cannot depend on itself",
                    )
                )
        return task, specs, errors, warnings

    def _list_enum(
        self,
        value: Any,
        enum_cls: Any,
        errors: list[TrackerListIssueOut],
        *,
        index: int,
        key: str,
        field: str,
    ) -> Any:
        try:
            return enum_cls(str(value))
        except ValueError:
            errors.append(
                TrackerListIssueOut(
                    index=index,
                    key=key,
                    field=field,
                    message=f"unsupported {field}: {value}",
                )
            )
            return None

    def _json_string_list(
        self,
        value: Any,
        errors: list[TrackerListIssueOut],
        *,
        index: int,
        key: str,
        field: str,
    ) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            errors.append(
                TrackerListIssueOut(
                    index=index,
                    key=key,
                    field=field,
                    message=f"{field} must be a list",
                )
            )
            return []
        return [str(item) for item in value]

    def _validate_ticket_list_ref(
        self,
        tracker: TaskTracker,
        ticket_key: str,
        list_specs: dict[str, dict[str, Any]],
        errors: list[TrackerListIssueOut],
        spec: dict[str, Any],
        field: str,
    ) -> None:
        if ticket_key in list_specs:
            return
        if self._ticket_by_key(tracker.id, ticket_key, missing_ok=True) is not None:
            return
        errors.append(
            TrackerListIssueOut(
                index=spec["index"],
                key=spec["key"],
                field=field,
                message=f"referenced ticket {ticket_key!r} does not exist",
            )
        )

    def _preview_ticket_list_update(
        self,
        *,
        project_id: int,
        tracker: TaskTracker,
        updates_json: list[dict[str, Any]],
    ) -> Envelope[TrackerMutationOut]:
        results: list[TrackerListItemResultOut] = []
        tickets: list[TrackerTicket] = []
        total_added = 0
        total_removed = 0
        for index, item in enumerate(updates_json):
            if not isinstance(item, dict):
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        error="update entries must be objects",
                    )
                )
                continue
            patch_json = item.get("patch_json", item.get("patch"))
            if not isinstance(patch_json, dict):
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                        error="update entry patch_json must be an object",
                    )
                )
                continue
            if not patch_json:
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="noop",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                    )
                )
                continue
            try:
                ticket = self._ticket_from_list_update(tracker.id, item)
                dependency_preview = self._preview_dependency_patch(tracker, ticket, patch_json)
                if dependency_preview is not None:
                    total_added += len(dependency_preview.added_dependency_keys)
                    total_removed += len(dependency_preview.removed_dependency_keys)
                tickets.append(ticket)
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="validated",
                        key=ticket.key,
                        id=ticket.id,
                        changed_fields=list(patch_json.keys()),
                        dependency_preview=dependency_preview,
                        ticket=self._ticket_out(ticket),
                    )
                )
            except (ConflictError, NotFoundError, ValidationError, ValueError) as exc:
                results.append(
                    TrackerListItemResultOut(
                        index=index,
                        action="error",
                        key=str(item.get("ticket_key") or "") or None,
                        id=item.get("ticket_id")
                        if isinstance(item.get("ticket_id"), int)
                        else None,
                        error=str(exc),
                    )
                )

        errors = [
            TrackerListIssueOut(
                index=result.index,
                key=result.key,
                message=result.error or "ticket update preview failed",
            )
            for result in results
            if result.action == "error"
        ]
        warnings: list[str] = []
        if total_removed >= 3 and total_removed > max(total_added * 2, total_added + 2):
            warnings.append(
                "dependency preview removes "
                f"{total_removed} edges and adds {total_added}; review direction before applying"
            )
        out = TrackerMutationOut(
            tracker=self._tracker_out(tracker),
            tickets=self._ticket_out_many(tickets),
            results=results,
            errors=errors,
            warnings=warnings,
            valid=not errors,
            dry_run=True,
            rev=tracker.rev,
        )
        return Envelope(data=out, project_id=project_id)

    def _preview_dependency_patch(
        self,
        tracker: TaskTracker,
        ticket: TrackerTicket,
        patch_json: dict[str, Any],
    ) -> TrackerDependencyPreviewOut | None:
        if not any(field in patch_json for field in DEPENDENCY_PATCH_FIELDS):
            return None
        if "dependency_keys" in patch_json and (
            "add_dependency_keys" in patch_json or "remove_dependency_keys" in patch_json
        ):
            raise ValidationError(
                "dependency_keys cannot be combined with add_dependency_keys or "
                "remove_dependency_keys"
            )

        current_rows = self._dependency_rows_for_ticket(ticket.id)
        current_ids = [
            row.depends_on_ticket_id for row in current_rows if row.depends_on_ticket_id is not None
        ]
        current_keys = self._dependency_keys_from_ids(current_ids)
        final_ids = list(current_ids)
        requested_by_id: dict[int, str] = {
            dependency_id: dependency_key
            for dependency_id, dependency_key in zip(current_ids, current_keys, strict=False)
        }

        if "dependency_keys" in patch_json:
            mode: Literal["replace", "add-remove"] = "replace"
            final_ids = []
            for dependency_key in self._dependency_patch_keys(patch_json, "dependency_keys"):
                dependency = self._ticket_by_key(tracker.id, dependency_key)
                dependency_id = _required_id(dependency.id, "ticket")
                if dependency_id not in final_ids:
                    final_ids.append(dependency_id)
                requested_by_id[dependency_id] = dependency.key
        else:
            mode = "add-remove"
            for dependency_key in self._dependency_patch_keys(patch_json, "remove_dependency_keys"):
                dependency = self._ticket_by_key(tracker.id, dependency_key)
                dependency_id = _required_id(dependency.id, "ticket")
                if dependency_id not in final_ids:
                    raise ValidationError(
                        "ticket dependency edge does not exist",
                        data={"ticket_key": ticket.key, "depends_on": dependency.key},
                    )
                final_ids = [item for item in final_ids if item != dependency_id]
                requested_by_id[dependency_id] = dependency.key
            for dependency_key in self._dependency_patch_keys(patch_json, "add_dependency_keys"):
                dependency = self._ticket_by_key(tracker.id, dependency_key)
                dependency_id = _required_id(dependency.id, "ticket")
                if dependency_id not in final_ids:
                    final_ids.append(dependency_id)
                requested_by_id[dependency_id] = dependency.key

        self._validate_dependency_preview_graph(tracker, ticket, final_ids)
        final_keys = [
            requested_by_id.get(dependency_id) or self._dependency_keys_from_ids([dependency_id])[0]
            for dependency_id in final_ids
        ]
        current_id_set = set(current_ids)
        final_id_set = set(final_ids)
        added_ids = [
            dependency_id for dependency_id in final_ids if dependency_id not in current_id_set
        ]
        removed_ids = [
            dependency_id for dependency_id in current_ids if dependency_id not in final_id_set
        ]
        kept_ids = [dependency_id for dependency_id in final_ids if dependency_id in current_id_set]
        warnings: list[str] = []
        if current_ids and not final_ids:
            warnings.append("dependency preview removes all existing dependency edges")
        if len(removed_ids) >= 3 and len(removed_ids) > max(len(added_ids) * 2, len(added_ids) + 2):
            warnings.append(
                "dependency preview removes "
                f"{len(removed_ids)} edges and adds {len(added_ids)}; review direction"
            )
        return TrackerDependencyPreviewOut(
            ticket_key=ticket.key,
            mode=mode,
            current_dependency_keys=current_keys,
            final_dependency_keys=final_keys,
            added_dependency_keys=[
                requested_by_id.get(dependency_id)
                or self._dependency_keys_from_ids([dependency_id])[0]
                for dependency_id in added_ids
            ],
            removed_dependency_keys=[
                requested_by_id.get(dependency_id)
                or self._dependency_keys_from_ids([dependency_id])[0]
                for dependency_id in removed_ids
            ],
            kept_dependency_keys=[
                requested_by_id.get(dependency_id)
                or self._dependency_keys_from_ids([dependency_id])[0]
                for dependency_id in kept_ids
            ],
            warnings=warnings,
        )

    def _dependency_patch_keys(self, patch_json: dict[str, Any], field: str) -> list[str]:
        if field not in patch_json:
            return []
        if not isinstance(patch_json[field], list):
            raise ValidationError(f"{field} must be a list")
        return list(dict.fromkeys(str(key) for key in patch_json[field]))

    def _dependency_keys_from_ids(self, dependency_ids: list[int]) -> list[str]:
        keys: list[str] = []
        for dependency_id in dependency_ids:
            dependency = self._s.get(TrackerTicket, dependency_id)
            if dependency is not None:
                keys.append(dependency.key)
        return keys

    def _validate_dependency_preview_graph(
        self,
        tracker: TaskTracker,
        ticket: TrackerTicket,
        final_dependency_ids: list[int],
    ) -> None:
        ticket_id = _required_id(ticket.id, "ticket")
        if ticket_id in final_dependency_ids:
            raise ValidationError("ticket cannot depend on itself", data={"ticket_key": ticket.key})
        rows = list(
            self._s.exec(
                select(TrackerTicketDependency).where(
                    TrackerTicketDependency.tracker_id == tracker.id
                )
            )
        )
        adjacency: dict[int, set[int]] = {}
        for row in rows:
            row_ticket_id = row.ticket_id
            depends_on_id = row.depends_on_ticket_id
            if row_ticket_id is None or depends_on_id is None or row_ticket_id == ticket_id:
                continue
            adjacency.setdefault(row_ticket_id, set()).add(depends_on_id)
        adjacency[ticket_id] = set(final_dependency_ids)
        for dependency_id in final_dependency_ids:
            if self._dependency_graph_reaches(adjacency, dependency_id, ticket_id):
                dependency_key = self._dependency_keys_from_ids([dependency_id])[0]
                raise ConflictError(
                    "ticket dependency would create a cycle",
                    data={"ticket_key": ticket.key, "depends_on": dependency_key},
                )

    def _dependency_graph_reaches(
        self,
        adjacency: dict[int, set[int]],
        start_id: int,
        target_id: int,
    ) -> bool:
        stack = [start_id]
        seen: set[int] = set()
        while stack:
            current = stack.pop()
            if current == target_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(adjacency.get(current, set()))
        return False

    def _ticket_from_list_update(
        self,
        tracker_id: int | None,
        item: dict[str, Any],
    ) -> TrackerTicket:
        if isinstance(item.get("ticket_id"), int):
            ticket = self._s.get(TrackerTicket, item["ticket_id"])
            if ticket is not None and ticket.tracker_id == tracker_id:
                return ticket
            raise NotFoundError("tracker ticket not found", data={"ticket_id": item["ticket_id"]})
        return self._ticket_by_key(tracker_id, str(item.get("ticket_key") or ""))


__all__ = [
    "TrackerBulkMixin",
]
