"""Host Slack text redaction for communication projections."""

from stackos.artifacts import redact_secret_text

from .constants import _SLACK_TOKEN_RE


def _redact_slack_text(value: str) -> str:
    return _SLACK_TOKEN_RE.sub("[redacted]", redact_secret_text(value))
