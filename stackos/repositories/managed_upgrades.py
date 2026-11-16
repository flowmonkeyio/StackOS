"""Minimal installation fence, read under the caller's SQLite writer boundary."""

from sqlmodel import select

from stackos.db.models import ManagedUpgrade, TrackerTask
from stackos.repositories.base import ConflictError, NotFoundError


class ManagedUpgradeRepository:
    def __init__(self, session):
        self.session = session

    def active(self):
        return self.session.exec(select(ManagedUpgrade).where(ManagedUpgrade.active == 1)).first()

    def require_unfenced(self):
        if row := self.active():
            raise ConflictError(
                "Managed upgrade has fenced admission and dispatch",
                data={
                    "code": "managed_upgrade_fenced",
                    "upgrade_id": row.upgrade_id,
                    "revision": row.revision,
                    "status": row.status,
                    "next_operations": ["maintenance.upgradeStatus"],
                },
            )

    def status(self, upgrade_id=None):
        row = self.session.get(ManagedUpgrade, upgrade_id) if upgrade_id else self.active()
        if row is None:
            if upgrade_id:
                raise NotFoundError("Managed upgrade not found", data={"upgrade_id": upgrade_id})
            return {"status": "idle", "revision": 0, "checkpoint_refs": []}
        return {
            "upgrade_id": row.upgrade_id,
            "source_identity": row.source_identity,
            "target_identity": row.target_identity,
            "revision": row.revision,
            "status": row.status,
            "fenced": row.active == 1,
            "postflight": row.postflight_json,
            "checkpoint_refs": row.checkpoint_refs_json,
            "checkpoints": [
                self._checkpoint(ref, row.upgrade_id) for ref in row.checkpoint_refs_json
            ],
        }

    def _checkpoint(self, ref, upgrade_id):
        task = self.session.get(TrackerTask, ref["task_id"])
        state = (task.metadata_json or {}).get("delivery", {}) if task else {}
        checkpoint = next(
            (
                item
                for item in [
                    state.get("upgrade_checkpoint", {}),
                    *state.get("upgrade_checkpoint_history", []),
                ]
                if isinstance(item, dict)
                and item.get("handoff_id") == ref["handoff_id"]
                and item.get("upgrade_id") == upgrade_id
            ),
            {},
        )
        if checkpoint.get("handoff_id") != ref["handoff_id"]:
            return {**ref, "status": "unavailable", "issues": ["checkpoint_identity_changed"]}
        return {
            **ref,
            **{
                key: checkpoint.get(key)
                for key in ("status", "phase", "attempt", "ticket_key", "issues")
            },
        }
