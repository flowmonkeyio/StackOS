"""Provider-neutral communication processing primitives."""

from stackos.communications.contracts import (
    CommunicationDecision,
    CommunicationProcessingResult,
    NormalizedInboundEvent,
    NormalizedResourcePatch,
    NormalizedResourceWrite,
)
from stackos.communications.processor import process_inbound_event

__all__ = [
    "CommunicationDecision",
    "CommunicationProcessingResult",
    "NormalizedInboundEvent",
    "NormalizedResourcePatch",
    "NormalizedResourceWrite",
    "process_inbound_event",
]
