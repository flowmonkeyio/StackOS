"""Auth provider operation contracts."""

from __future__ import annotations

from stackos.auth_providers import AuthStatusOut, AuthTestOut
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.tools.auth import AuthStatusInput, AuthTestInput, _auth_status, _auth_test
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="auth.status",
            summary="Inspect sanitized provider credential status for one project.",
            input_model=AuthStatusInput,
            output_model=AuthStatusOut,
            handler=_auth_status,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/projects/{project_id}/auth",
                ),
                cli=OperationSurface(enabled=True, command="ops call auth.status"),
            ),
            purpose=(
                "Use this when an agent needs safe credential readiness diagnostics. "
                "It returns provider metadata, opaque credential refs, and sanitized "
                "status only; it never returns secret material."
            ),
            when_to_use=(
                "Before choosing a provider action or workflow that requires credentials.",
                "After the operator connects credentials in the UI and the agent needs to "
                "confirm readiness.",
            ),
            returns=(
                "Sanitized provider auth methods and connection status.",
                "Opaque credential_ref values suitable for action validation/execution.",
                "No secret payloads or raw tokens.",
            ),
            examples=(
                OperationExample(
                    title="Check one provider",
                    arguments={"project_id": 1, "provider_key": "slack-bot"},
                ),
                OperationExample(
                    title="Check all providers",
                    arguments={"project_id": 1},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="auth.test",
            summary="Run a daemon-side sanitized health probe for one credential ref.",
            input_model=AuthTestInput,
            output_model=WriteEnvelope[AuthTestOut],
            handler=_auth_test,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/projects/{project_id}/auth/test",
                ),
                cli=OperationSurface(enabled=True, command="ops call auth.test"),
            ),
            purpose=(
                "Use this after auth.status returns a credential_ref and the agent needs "
                "to verify the daemon can use that credential. The daemon performs the "
                "provider-specific probe and returns only sanitized diagnostics."
            ),
            when_to_use=(
                "After a user connects or repairs a provider credential.",
                "Before starting a workflow whose first provider call would otherwise fail.",
            ),
            prerequisites=(
                "Call auth.status first to obtain the opaque credential_ref.",
                "Do not ask the user to paste secrets into chat.",
            ),
            returns=(
                "A write envelope with sanitized probe result and provider/account metadata.",
                "No secret payloads or raw tokens.",
            ),
            examples=(
                OperationExample(
                    title="Test connected credential",
                    arguments={"project_id": 1, "credential_ref": "cred_..."},
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
            secret_policy="no-secret-output",
        ),
    ]


__all__ = ["operation_specs"]
