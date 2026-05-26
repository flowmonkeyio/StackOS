"""ContextRepository facade composed from semantic domain mixins."""

from __future__ import annotations

from .base import ContextRepositoryBase
from .decisions import ContextDecisionMixin
from .experiments import ContextExperimentMixin
from .index import ContextIndexMixin
from .learnings import ContextLearningMixin
from .metrics import ContextMetricMixin
from .outputs import ContextOutputMixin
from .projection import ContextProjectionMixin
from .queries import ContextQueryMixin
from .snapshots import ContextSnapshotMixin


class ContextRepository(
    ContextQueryMixin,
    ContextSnapshotMixin,
    ContextLearningMixin,
    ContextExperimentMixin,
    ContextDecisionMixin,
    ContextMetricMixin,
    ContextProjectionMixin,
    ContextIndexMixin,
    ContextOutputMixin,
    ContextRepositoryBase,
):
    """Data-only project memory facade with bounded, sanitized retrieval."""


__all__ = ["ContextRepository"]
