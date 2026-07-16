"""Auth provider operation contracts."""

from __future__ import annotations

from stackos.auth_providers import AuthRevokeOut, AuthStartOut, AuthStatusOut, AuthTestOut
from stackos.mcp.contract import WriteEnvelope
from stackos.mcp.tools.auth import (
    AuthRevokeInput,
    AuthStartInput,
    AuthStatusInput,
    AuthTestInput,
    _auth_revoke,
    _auth_start,
    _auth_status,
    _auth_test,
)
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
                    path="/api/v1/projects/{project_id}/auth/status",
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
        OperationSpec(
            name="auth.start",
            summary="Start a local-admin provider auth flow without accepting secrets in chat.",
            input_model=AuthStartInput,
            output_model=WriteEnvelope[AuthStartOut],
            handler=_auth_start,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/projects/{project_id}/auth/{provider_key}/start",
                ),
                cli=OperationSurface(enabled=True, command="ops call auth.start"),
            ),
            purpose=(
                "Use this only for local-admin credential setup. Normal agents should inspect "
                "auth.status, send the operator to the connections UI, and never request "
                "secret values in chat."
            ),
            when_to_use=(
                "An operator explicitly asks the agent to start a daemon-owned auth flow.",
                "A local admin setup tool needs the sanitized authorization URL or next step.",
            ),
            prerequisites=(
                "Requires operator/admin authority.",
                "Do not pass API keys, OAuth codes, or raw credentials through this tool.",
            ),
            returns=("A write envelope with sanitized provider auth setup details.",),
            examples=(
                OperationExample(
                    title="Start provider auth",
                    arguments={"project_id": 1, "provider_key": "slack-bot"},
                ),
            ),
            mutating=True,
            grant_policy="local-admin-auth-write",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="auth.revoke",
            summary="Revoke one opaque credential ref through the daemon.",
            input_model=AuthRevokeInput,
            output_model=WriteEnvelope[AuthRevokeOut],
            handler=_auth_revoke,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(enabled=True),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/projects/{project_id}/auth/revoke",
                ),
                cli=OperationSurface(enabled=True, command="ops call auth.revoke"),
            ),
            purpose=(
                "Use this only for explicit local-admin credential cleanup. Agents should "
                "operate on opaque credential refs and never expose or store secret material."
            ),
            when_to_use=("The operator explicitly asks to disconnect or rotate a credential.",),
            prerequisites=(
                "Call auth.status first to identify the safe credential_ref.",
                "Requires operator/admin authority.",
            ),
            returns=("A write envelope with sanitized revoke status.",),
            examples=(
                OperationExample(
                    title="Revoke a credential",
                    arguments={"project_id": 1, "credential_ref": "cred_..."},
                ),
            ),
            mutating=True,
            grant_policy="local-admin-auth-write",
            secret_policy="no-secret-output",
        ),
    ]


__all__ = ["operation_specs"]
