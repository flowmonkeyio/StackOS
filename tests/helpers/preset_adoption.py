"""Exact source-only updates awaiting the operator's project adaptation."""

PENDING_PRESET_ADOPTIONS = {
    "stackos.sdlc.requirements-flow-definer": ("0.6.7", "0.6.8"),
    "stackos.sdlc.codebase-explorer": ("0.6.7", "0.6.8"),
    "stackos.sdlc.planning": ("0.7.23", "0.7.26"),
    "stackos.sdlc.architecture": ("0.6.8", "0.6.9"),
    "stackos.sdlc.test-designer": ("0.6.12", "0.6.13"),
    "stackos.sdlc.delivery": ("0.7.22", "0.7.25"),
    "stackos.sdlc.delivery-reviewer": ("0.6.21", "0.6.25"),
    "stackos.sdlc.delivery-orchestrator": ("0.8.25", "0.8.28"),
    "branding.evidence-curator": ("0.2.2", "0.2.3"),
    "branding.narrative-writer": ("0.1.6", "0.1.7"),
    "branding.channel-strategist": ("0.1.6", "0.1.7"),
    "branding.claim-auditor": ("0.1.1", "0.1.2"),
    "branding.voice-reviewer": ("0.2.6", "0.2.7"),
    "branding.sanitization-reviewer": ("0.1.1", "0.1.2"),
    "branding.profile-architect": ("0.1.1", "0.1.2"),
    "seo.workflow.keyword-research": ("0.2.1", "0.2.2"),
    "seo.workflow.content-refresh": ("0.2.1", "0.2.2"),
    "seo.workflow.website-analysis": ("0.5.1", "0.5.2"),
    "stackos.finance.receipt-operator": ("0.4.5", "0.4.7"),
    "stackos.finance.bookkeeping-preparer": ("0.5.6", "0.5.8"),
    "stackos.finance.billing-collections-operator": ("0.6.7", "0.6.9"),
    "stackos.finance.cashflow-preparer": ("0.4.5", "0.4.7"),
    "stackos.finance.tax-preparer": ("0.4.5", "0.4.7"),
    "stackos.finance.control-reviewer": ("0.6.7", "0.6.9"),
    "stackos.workflow-orchestrator": ("0.1.6", "0.1.7"),
    "branding.brand-orchestrator": ("0.6.3", "0.6.4"),
    "stackos.finance.department-orchestrator": ("0.8.1", "0.8.3"),
}


def assert_preset_adoption(key: str, local_version: str, source_version: str) -> None:
    assert local_version == source_version or (
        local_version,
        source_version,
    ) == PENDING_PRESET_ADOPTIONS.get(key), (key, local_version, source_version)
