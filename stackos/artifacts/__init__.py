"""Generic artifact primitives for StackOS."""

from __future__ import annotations

from stackos.artifacts.redaction import redact_secret_text, redact_secrets

__all__ = ["redact_secret_text", "redact_secrets"]
