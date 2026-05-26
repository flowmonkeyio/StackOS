"""Provider-neutral communication processing primitives."""

from stackos.communications.contracts import (
    CommunicationDecision,
    CommunicationProcessingResult,
    NormalizedInboundEvent,
    NormalizedResourcePatch,
    NormalizedResourceWrite,
)
from stackos.communications.policy import (
    CommunicationInteractionCheck,
    CommunicationPolicyEvent,
    CommunicationPolicyProfile,
    candidate_refs,
    config_nested,
    config_policy,
    config_refs,
    config_string_list,
    evaluate_inbound_policy,
)
from stackos.communications.processor import process_inbound_event
from stackos.communications.profiles import (
    communication_profile_record_by_key,
    communication_profile_ref,
    merged_provider_profile,
    provider_facet,
)
from stackos.communications.provider_ids import telegram_callback_button_external_id
from stackos.communications.resources import communication_record_by_external_id

__all__ = [
    "CommunicationDecision",
    "CommunicationInteractionCheck",
    "CommunicationPolicyEvent",
    "CommunicationPolicyProfile",
    "CommunicationProcessingResult",
    "NormalizedInboundEvent",
    "NormalizedResourcePatch",
    "NormalizedResourceWrite",
    "candidate_refs",
    "communication_profile_record_by_key",
    "communication_profile_ref",
    "communication_record_by_external_id",
    "config_nested",
    "config_policy",
    "config_refs",
    "config_string_list",
    "evaluate_inbound_policy",
    "merged_provider_profile",
    "process_inbound_event",
    "provider_facet",
    "telegram_callback_button_external_id",
]
