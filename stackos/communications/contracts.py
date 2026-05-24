"""Provider-neutral communication processor contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CommunicationDecision:
    """Static policy outcome for one normalized inbound event."""

    store: bool
    create_request: bool
    status: str
    trigger_reason: str | None = None
    matched_command: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedResourceWrite:
    """One provider-neutral resource write prepared by an adapter."""

    resource_key: str
    external_id: str
    title: str
    data_json: dict[str, Any]
    provenance_json: dict[str, Any]
    preserve_existing_on_dedupe: bool = False


@dataclass(frozen=True)
class NormalizedResourcePatch:
    """One safe merge patch against an existing communication resource."""

    resource_key: str
    external_id: str
    data_json: dict[str, Any]


@dataclass(frozen=True)
class NormalizedInboundEvent:
    """Provider-normalized inbound event ready for shared processing."""

    provider_key: str
    profile_key: str
    event_key: str
    update_type: str
    source_kind: str
    request_title: str
    body_preview: str
    event: NormalizedResourceWrite
    request_key: str | None = None
    source_message_ref: str | None = None
    surface: NormalizedResourceWrite | None = None
    message: NormalizedResourceWrite | None = None
    interaction: NormalizedResourceWrite | None = None
    state_patches: list[NormalizedResourcePatch] = field(default_factory=list)
    request_metadata_json: dict[str, Any] = field(default_factory=dict)
    response_json: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CommunicationProcessingResult:
    """Compact outcome returned to ingress adapters."""

    ok: bool
    provider_key: str
    profile_key: str
    event_key: str
    update_type: str
    policy_status: str
    event_record_id: int | None = None
    message_record_id: int | None = None
    interaction_record_id: int | None = None
    agent_request_id: int | None = None
    response_json: dict[str, Any] = field(default_factory=dict)

    def to_response(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            **self.response_json,
            "policy_status": self.policy_status,
            "event_record_id": self.event_record_id,
            "message_record_id": self.message_record_id,
            "interaction_record_id": self.interaction_record_id,
            "agent_request_id": self.agent_request_id,
        }
