"""Global Account and project Connection operation contracts."""

from __future__ import annotations

from stackos.auth_providers import (
    AccountOut,
    AuthCredentialEditOut,
    AuthCredentialSetOut,
    AuthRevokeOut,
    AuthStartOut,
    AuthStatusOut,
    AuthTestOut,
    OAuthCallbackOut,
)
from stackos.mcp.contract import WriteEnvelope
from stackos.operations.auth_handlers import (
    AccountCreateInput,
    AccountGetInput,
    AccountListInput,
    AccountRevokeInput,
    AccountStartInput,
    AccountTestInput,
    AccountUpdateInput,
    AuthCallbackInput,
    ConnectionAccountInput,
    ConnectionListInput,
    account_create,
    account_get,
    account_list,
    account_revoke,
    account_start,
    account_test,
    account_update,
    auth_callback,
    connection_attach,
    connection_detach,
    connection_list,
)
from stackos.operations.spec import (
    OperationExample,
    OperationSpec,
    OperationSurface,
    OperationSurfaces,
)


def _surfaces(*, rest_path: str, cli_name: str) -> OperationSurfaces:
    return OperationSurfaces(
        mcp=OperationSurface(enabled=True),
        rest=OperationSurface(enabled=True, path=rest_path),
        cli=OperationSurface(enabled=True, command=f"ops call {cli_name}"),
    )


def _rest_only_surfaces(*, rest_path: str) -> OperationSurfaces:
    return OperationSurfaces(
        mcp=OperationSurface(
            enabled=False,
            notes="Local-admin Account secrets and edit state do not cross MCP.",
        ),
        rest=OperationSurface(enabled=True, path=rest_path),
        cli=OperationSurface(
            enabled=False,
            notes="Use the local Accounts UI or exact local-admin REST route.",
        ),
    )


def operation_specs() -> list[OperationSpec]:
    return [
        OperationSpec(
            name="account.list",
            summary="List sanitized reusable Accounts across projects.",
            input_model=AccountListInput,
            output_model=AuthStatusOut,
            handler=account_list,
            surfaces=_surfaces(rest_path="/api/v1/auth/accounts", cli_name="account.list"),
            purpose=(
                "Inspect reusable Account readiness and safe provider identity without "
                "exposing encrypted or plaintext credential material."
            ),
            when_to_use=(
                "Before attaching an existing Account to a project.",
                "When the operator needs global Account health or usage counts.",
            ),
            returns=(
                "Sanitized Accounts, opaque credential_ref values, and attached project ids.",
                "No secret payloads or raw tokens.",
            ),
            examples=(
                OperationExample(
                    title="List one provider's Accounts",
                    arguments={"provider_key": "slack-bot"},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="account.create",
            summary="Create one named reusable Account through local-admin REST.",
            input_model=AccountCreateInput,
            output_model=WriteEnvelope[AuthCredentialSetOut],
            handler=account_create,
            surfaces=_rest_only_surfaces(rest_path="/api/v1/auth/accounts/{provider_key}"),
            purpose=(
                "Create one global Account identity and daemon-held credential backing, "
                "optionally attaching it to the originating project."
            ),
            when_to_use=("An operator explicitly creates an Account in the local UI.",),
            prerequisites=(
                "Requires local-admin authority.",
                "Credential fields are write-only and must never be returned or logged.",
            ),
            returns=("A sanitized Account and opaque credential_ref.",),
            examples=(
                OperationExample(
                    title="Create a named API-key Account",
                    arguments={
                        "provider_key": "firecrawl",
                        "auth_method_key": "api_key",
                        "display_name": "Firecrawl - Default",
                        "fields": {"api_key": "[write-only]"},
                    },
                ),
            ),
            mutating=True,
            grant_policy="local-admin-auth-write",
            secret_policy="write-only-input-no-secret-output",
        ),
        OperationSpec(
            name="account.get",
            summary="Read safe Account edit state through local-admin REST.",
            input_model=AccountGetInput,
            output_model=AuthCredentialEditOut,
            handler=account_get,
            surfaces=_rest_only_surfaces(rest_path="/api/v1/auth/accounts/{credential_ref}"),
            purpose=(
                "Populate the Account editor with non-secret fields and secret-presence flags."
            ),
            when_to_use=("An operator explicitly opens an existing Account for editing.",),
            returns=(
                "Safe editable values, immutable auth method metadata, and secret-presence flags.",
            ),
            examples=(
                OperationExample(
                    title="Open one Account",
                    arguments={"credential_ref": "cred_..."},
                ),
            ),
            mutating=False,
            grant_policy="local-admin-auth-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="account.update",
            summary="Update one reusable Account through local-admin REST.",
            input_model=AccountUpdateInput,
            output_model=WriteEnvelope[AuthCredentialSetOut],
            handler=account_update,
            surfaces=_rest_only_surfaces(rest_path="/api/v1/auth/accounts/{credential_ref}"),
            purpose=(
                "Rename an Account or rotate explicitly supplied credential fields without "
                "changing its provider or authentication method."
            ),
            when_to_use=("An operator explicitly saves changes in the Account editor.",),
            prerequisites=(
                "Requires local-admin authority.",
                "Omitted secret fields preserve their current daemon-held value.",
            ),
            returns=("The updated sanitized Account and opaque credential_ref.",),
            examples=(
                OperationExample(
                    title="Rename an Account",
                    arguments={
                        "credential_ref": "cred_...",
                        "display_name": "Firecrawl - Production",
                    },
                ),
            ),
            mutating=True,
            grant_policy="local-admin-auth-write",
            secret_policy="write-only-input-no-secret-output",
        ),
        OperationSpec(
            name="connection.list",
            summary="List Accounts explicitly attached to one project.",
            input_model=ConnectionListInput,
            output_model=AuthStatusOut,
            handler=connection_list,
            surfaces=_surfaces(
                rest_path="/api/v1/projects/{project_id}/connections/accounts",
                cli_name="connection.list",
            ),
            purpose=(
                "Inspect project-authorized Accounts. An Account omitted from this result "
                "cannot be used by actions in the project."
            ),
            when_to_use=("Before selecting a credential_ref for project execution.",),
            returns=("Sanitized attached Accounts and provider setup metadata.",),
            examples=(
                OperationExample(
                    title="List project Connections",
                    arguments={"project_id": 1},
                ),
            ),
            mutating=False,
            grant_policy="direct-read",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="account.test",
            summary="Run a sanitized health probe for one reusable Account.",
            input_model=AccountTestInput,
            output_model=WriteEnvelope[AuthTestOut],
            handler=account_test,
            surfaces=_surfaces(
                rest_path="/api/v1/auth/accounts/{credential_ref}/test",
                cli_name="account.test",
            ),
            purpose=("Verify daemon-held Account material without returning it to the caller."),
            when_to_use=("After an Account is created, repaired, or rotated.",),
            prerequisites=(
                "Use account.list to obtain the opaque credential_ref.",
                "Do not ask the operator to paste secrets into chat.",
            ),
            returns=("Sanitized provider and account probe evidence.",),
            examples=(
                OperationExample(
                    title="Test an Account",
                    arguments={"credential_ref": "cred_..."},
                ),
            ),
            mutating=True,
            grant_policy="direct-setup-write",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="account.start",
            summary="Start a daemon-owned Account authorization flow.",
            input_model=AccountStartInput,
            output_model=WriteEnvelope[AuthStartOut],
            handler=account_start,
            surfaces=_surfaces(
                rest_path="/api/v1/auth/accounts/{provider_key}/start",
                cli_name="account.start",
            ),
            purpose=(
                "Start interactive OAuth for a pending global Account while preserving "
                "the exact Accounts or project Connections return surface."
            ),
            when_to_use=("An operator explicitly starts or repairs OAuth setup.",),
            prerequisites=(
                "Requires local-admin authority.",
                "Never pass OAuth codes or raw credential material through this operation.",
            ),
            returns=("A sanitized authorization URL and exact return-surface context.",),
            examples=(
                OperationExample(
                    title="Start from project Connections",
                    arguments={
                        "provider_key": "google-analytics",
                        "credential_ref": "cred_...",
                        "attach_project_id": 1,
                        "return_surface": "project-connections",
                    },
                ),
            ),
            mutating=True,
            grant_policy="local-admin-auth-write",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="connection.attach",
            summary="Attach a reusable Account to one project.",
            input_model=ConnectionAccountInput,
            output_model=WriteEnvelope[AccountOut],
            handler=connection_attach,
            surfaces=_surfaces(
                rest_path="/api/v1/projects/{project_id}/connections/accounts/{credential_ref}",
                cli_name="connection.attach",
            ),
            purpose=("Grant one project explicit access to a daemon-held reusable Account."),
            when_to_use=("After the operator selects an existing Account for a project.",),
            returns=("The sanitized Account with its updated attached project ids.",),
            examples=(
                OperationExample(
                    title="Attach an Account",
                    arguments={"project_id": 1, "credential_ref": "cred_..."},
                ),
            ),
            mutating=True,
            grant_policy="local-admin-auth-write",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="connection.detach",
            summary="Detach an Account from one project without revoking it.",
            input_model=ConnectionAccountInput,
            output_model=WriteEnvelope[AccountOut],
            handler=connection_detach,
            surfaces=_surfaces(
                rest_path="/api/v1/projects/{project_id}/connections/accounts/{credential_ref}",
                cli_name="connection.detach",
            ),
            purpose=("Remove project authorization while retaining the reusable global Account."),
            when_to_use=("The operator explicitly removes a project Connection.",),
            prerequisites=(
                "Active project consumers must be rebound or disabled before detaching.",
            ),
            returns=("The sanitized Account with its updated attached project ids.",),
            examples=(
                OperationExample(
                    title="Detach an Account",
                    arguments={"project_id": 1, "credential_ref": "cred_..."},
                ),
            ),
            mutating=True,
            grant_policy="local-admin-auth-write",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="account.revoke",
            summary="Revoke one detached reusable Account and wipe its encrypted backing.",
            input_model=AccountRevokeInput,
            output_model=WriteEnvelope[AuthRevokeOut],
            handler=account_revoke,
            surfaces=_surfaces(
                rest_path="/api/v1/auth/accounts/{credential_ref}/revoke",
                cli_name="account.revoke",
            ),
            purpose=(
                "Permanently revoke daemon-held Account material after it has been "
                "detached from every project while retaining a non-executable audit tombstone."
            ),
            when_to_use=("The operator explicitly revokes an Account.",),
            prerequisites=(
                "Detach the Account from every project first.",
                "Requires local-admin authority.",
            ),
            returns=("A sanitized revoke receipt; encrypted backing is removed.",),
            examples=(
                OperationExample(
                    title="Revoke an Account",
                    arguments={"credential_ref": "cred_..."},
                ),
            ),
            mutating=True,
            grant_policy="local-admin-auth-write",
            secret_policy="no-secret-output",
        ),
        OperationSpec(
            name="auth.callback",
            summary="Complete one provider callback through the fixed public REST boundary.",
            input_model=AuthCallbackInput,
            output_model=OAuthCallbackOut,
            handler=auth_callback,
            surfaces=OperationSurfaces(
                mcp=OperationSurface(
                    enabled=False,
                    notes="Provider callback values are accepted only by the fixed REST route.",
                ),
                rest=OperationSurface(
                    enabled=True,
                    path="/api/v1/auth/oauth/callback",
                    notes="GET only; the route returns an immediate sanitized 303 redirect.",
                ),
                cli=OperationSurface(
                    enabled=False,
                    notes="Provider callback values must not cross the CLI surface.",
                ),
            ),
            purpose=(
                "Consume a short-lived bound OAuth transaction and redirect to its "
                "server-stored Accounts or project Connections origin."
            ),
            when_to_use=("Only for a provider redirect to the fixed callback.",),
            returns=("A sanitized callback outcome used to select the local return surface.",),
            mutating=True,
            grant_policy="public-oauth-callback",
            secret_policy="no-secret-output",
        ),
    ]


__all__ = ["operation_specs"]
