"""Agent-readable public guide operation contracts."""

from __future__ import annotations

from stackos.mcp.tools.guides import (
    GettingStartedGuideInput,
    GettingStartedGuideOutput,
    _guide_getting_started,
)
from stackos.operations._helpers import operation_spec
from stackos.operations.spec import OperationExample, OperationResponsePolicy


def operation_specs():
    return [
        operation_spec(
            name="guide.gettingStarted",
            summary="Fetch the canonical public StackOS getting-started guide and its references.",
            input_model=GettingStartedGuideInput,
            output_model=GettingStartedGuideOutput,
            handler=_guide_getting_started,
            purpose=(
                "Use the StackOS website as the single source of truth for first-run guidance. "
                "When helping a person, link guide_url; use the fetched Markdown for context "
                "instead of creating a separate onboarding guide."
            ),
            when_to_use=(
                "A person asks what to do after installing StackOS.",
                "An agent needs the current first-run guide before helping with project setup.",
            ),
            prerequisites=(
                "The public StackOS website must be reachable to include the Markdown content; "
                "canonical URLs are still returned when the fetch is unavailable.",
            ),
            returns=(
                "guide_url is the designed page to share with people.",
                "markdown_url is the public plain-text source for agents and tools.",
                "content contains the fetched Markdown when content_available is true.",
                "warnings explain a fetch failure without hiding the canonical references.",
            ),
            examples=(OperationExample(title="Open the getting-started guide", arguments={}),),
            mutating=False,
            grant_policy="direct-read",
            category="setup",
            response_policy=OperationResponsePolicy(
                default_mode="raw",
                allowed_modes=("compact", "raw"),
                ack_safe=False,
                compact_notes=(
                    "Compact returns keep the website and Markdown references but omit content. "
                    "Use raw, the default, when the agent needs to read the guide.",
                ),
            ),
        )
    ]


__all__ = ["operation_specs"]
