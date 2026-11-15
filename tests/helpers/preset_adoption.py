"""Exact source-only updates awaiting the operator's project adaptation."""

PENDING_PRESET_ADOPTIONS = {
    "stackos.sdlc.requirements-flow-definer": ("0.6.7", "0.6.8"),
    "stackos.sdlc.codebase-explorer": ("0.6.7", "0.6.8"),
    "stackos.sdlc.planning": ("0.7.23", "0.7.26"),
    "stackos.sdlc.architecture": ("0.6.8", "0.6.9"),
    "stackos.sdlc.test-designer": ("0.6.12", "0.6.13"),
    "stackos.sdlc.delivery": ("0.7.22", "0.7.25"),
    "stackos.sdlc.delivery-reviewer": ("0.6.21", "0.6.24"),
    "stackos.sdlc.delivery-orchestrator": ("0.8.25", "0.8.28"),
}


def assert_preset_adoption(key: str, local_version: str, source_version: str) -> None:
    assert local_version == source_version or (
        local_version,
        source_version,
    ) == PENDING_PRESET_ADOPTIONS.get(key), (key, local_version, source_version)
