"""Finite host diagnostic metadata; no transport, output or authority decisions."""

STAGES = frozenset({
    "preparation", "host_admission", "turn_start", "turn_receive",
    "tool_admission", "tool_dispatch", "tool_response", "agent_output",
    "turn_completion", "thread_close", "host_notification", "rpc_response",
    "observation_admission", "observation_dispatch", "observation_result",
})
ERROR_CODES = frozenset({
    "contextWindowExceeded", "sessionBudgetExceeded", "usageLimitExceeded",
    "rateLimitExceeded", "flexUnavailable", "serverOverloaded", "cyberPolicy",
    "misalignmentPolicyViolation", "tooManyDenials", "internalServerError",
    "unauthorized", "badRequest", "threadRollbackFailed", "sandboxError", "other",
})
HTTP_ERRORS = frozenset({
    "httpConnectionFailed", "responseStreamConnectionFailed",
    "responseStreamDisconnected", "responseTooManyFailedAttempts",
})
DENIAL_CODES = frozenset({
    "unauthorized", "cyberPolicy", "misalignmentPolicyViolation",
    "tooManyDenials", "sandboxError",
})
RPC_ERROR_CODES = frozenset({-32700, -32600, -32601, -32602, -32603})
RPC_METHODS = frozenset({
    "initialize", "configRequirements/read", "permissionProfile/list", "config/read",
    "skills/list", "command/exec", "thread/start", "thread/resume", "turn/start",
    "thread/unsubscribe",
})


def error_metadata(error, *, will_retry=None):
    """Project admitted input onto finite fields; never copy or stringify text."""
    info = error.get("codexErrorInfo") if type(error) is dict else None
    code, http_status = "unknown", None
    if type(info) is str and info in ERROR_CODES:
        code = info
    elif type(info) is dict and len(info) == 1:
        variant = next(iter(info))
        if variant in HTTP_ERRORS and type(info[variant]) is dict:
            code = variant
            status = info[variant].get("httpStatusCode")
            if type(status) is int and 100 <= status <= 599:
                http_status = status
    return {
        "host_code": code,
        "http_status": http_status,
        "will_retry": will_retry if type(will_retry) is bool else None,
    }


def observation_telemetry(row):
    """Recognize only bounded non-denial notification telemetry before a result.

    This never establishes completion/authority. The consumer still needs the
    existing command completion and custody checks; all other extra rows hold.
    """
    if type(row) is not dict or set(row) != {
        "type", "stage", "host_code", "http_status", "will_retry",
    }:
        return False
    code, status, retry = row["host_code"], row["http_status"], row["will_retry"]
    return (
        row["type"] == "host.diagnostic"
        and row["stage"] == "host_notification"
        and type(code) is str
        and code in (ERROR_CODES | HTTP_ERRORS | {"unknown"}) - DENIAL_CODES
        and (status is None or (
            code in HTTP_ERRORS and type(status) is int and 100 <= status <= 599
        ))
        and (retry is None or type(retry) is bool)
    )
