"""Compatibility facade for the tracker repository implementation."""

from __future__ import annotations

from sqlmodel import Session

from stackos.repositories.tracker.bulk import TrackerBulkMixin
from stackos.repositories.tracker.events import TrackerEventMixin
from stackos.repositories.tracker.graph import TrackerGraphMixin
from stackos.repositories.tracker.mirrors import TrackerMirrorMixin
from stackos.repositories.tracker.mutations import TrackerMutationMixin
from stackos.repositories.tracker.queries import TrackerQueryMixin
from stackos.repositories.tracker.relations import TrackerRelationsMixin
from stackos.repositories.tracker.setup import TrackerSetupMixin


class TrackerRepository(
    TrackerMirrorMixin,
    TrackerBulkMixin,
    TrackerMutationMixin,
    TrackerEventMixin,
    TrackerQueryMixin,
    TrackerGraphMixin,
    TrackerRelationsMixin,
    TrackerSetupMixin,
):
    """Canonical repository facade for tracker lifecycle and graph state."""

    def __init__(self, session: Session) -> None:
        self._s = session


__all__ = ["TrackerRepository"]
