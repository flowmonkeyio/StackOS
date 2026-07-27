"""Model-independent Amazon S3 fragments for the built-in utils plugin."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from stackos.s3_contract import AWS_S3_REGIONS

_S3_CONFLICT_POLICY = {
    "type": "string",
    "enum": ["overwrite", "skip", "fail"],
    "description": "Agent-selected handling when the destination already exists.",
}
_S3_ERROR_POLICY = {
    "type": "string",
    "enum": ["stop", "continue"],
    "description": "Stop at the first failed item or continue the remaining batch.",
}
_S3_DIRECTORY_LIST_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "prefix": {
            "type": "string",
            "default": "",
            "description": "Exact S3 key prefix to list; empty lists from the bucket root.",
        },
        "delimiter": {
            "type": "string",
            "minLength": 1,
            "default": "/",
            "description": "Optional S3 delimiter used to group common prefixes.",
        },
        "page_size": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "default": 1000,
            "description": "Maximum object and common-prefix entries requested from S3.",
        },
        "cursor": {
            "type": "string",
            "minLength": 1,
            "description": "Opaque continuation cursor returned by an earlier call.",
        },
    },
}
_S3_UPLOAD_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["local_path", "destination_key"],
    "properties": {
        "local_path": {
            "type": "string",
            "minLength": 1,
            "description": "Exact daemon-local file or directory selected by the agent.",
        },
        "destination_key": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Exact destination object key for a file or destination prefix root "
                "for a local directory."
            ),
        },
    },
}
_S3_DOWNLOAD_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["remote_key", "remote_kind", "local_path"],
    "properties": {
        "remote_key": {
            "type": "string",
            "minLength": 1,
            "description": "Exact S3 object key or prefix selected by the agent.",
        },
        "remote_kind": {
            "type": "string",
            "enum": ["object", "prefix"],
            "description": "Explicitly select one object or all current objects under a prefix.",
        },
        "local_path": {
            "type": "string",
            "minLength": 1,
            "description": "Exact daemon-local destination selected by the agent.",
        },
    },
}
_S3_UPLOAD_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "conflict_policy", "error_policy"],
    "properties": {
        "items": {
            "type": "array",
            "minItems": 1,
            "items": _S3_UPLOAD_ITEM_SCHEMA,
        },
        "conflict_policy": _S3_CONFLICT_POLICY,
        "error_policy": _S3_ERROR_POLICY,
        "follow_symlinks": {
            "type": "boolean",
            "default": False,
            "description": (
                "Follow local symlinks under their link names with ancestry-cycle "
                "detection. False skips and reports symlinks."
            ),
        },
    },
}
_S3_DOWNLOAD_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "conflict_policy", "error_policy"],
    "properties": {
        "items": {
            "type": "array",
            "minItems": 1,
            "items": _S3_DOWNLOAD_ITEM_SCHEMA,
        },
        "conflict_policy": _S3_CONFLICT_POLICY,
        "error_policy": _S3_ERROR_POLICY,
    },
}
_S3_FILE_DELETE_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key"],
    "properties": {
        "key": {
            "type": "string",
            "minLength": 1,
            "description": "Exact current object key to delete.",
        },
        "if_match": {
            "type": "string",
            "minLength": 1,
            "description": "Optional expected current ETag for a conditional delete.",
        },
    },
}
_S3_DIRECTORY_CREATE_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["prefix"],
    "properties": {
        "prefix": {
            "type": "string",
            "minLength": 1,
            "description": "Prefix whose normalized trailing-slash marker will be created.",
        }
    },
}
_S3_DIRECTORY_DELETE_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["prefix", "recursive", "max_objects"],
    "properties": {
        "prefix": {
            "type": "string",
            "minLength": 1,
            "description": "Exact prefix to normalize with a trailing slash and delete.",
        },
        "recursive": {
            "type": "boolean",
            "description": (
                "False removes only an empty prefix marker. True deletes the bounded "
                "current object set under the exact normalized prefix."
            ),
        },
        "max_objects": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10000,
            "description": "Hard pre-mutation bound for current objects under the prefix.",
        },
    },
}
_S3_RENAME_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_key", "destination_key", "conflict_policy"],
    "properties": {
        "source_key": {
            "type": "string",
            "minLength": 1,
            "description": "Exact current source object key; prefixes are not supported.",
        },
        "destination_key": {
            "type": "string",
            "minLength": 1,
            "description": "Exact destination object key in the same bound bucket.",
        },
        "conflict_policy": _S3_CONFLICT_POLICY,
    },
}

_S3_LIST_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": [
        "provider",
        "operation",
        "status",
        "bucket",
        "prefix",
        "objects",
        "common_prefixes",
        "next_cursor",
    ],
    "properties": {
        "provider": {"type": "string", "const": "aws-s3"},
        "operation": {"type": "string", "const": "directory.list"},
        "status": {"type": "string", "const": "success"},
        "bucket": {"type": "string"},
        "prefix": {"type": "string"},
        "objects": {"type": "array", "items": {"type": "object"}},
        "common_prefixes": {"type": "array", "items": {"type": "object"}},
        "next_cursor": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
}
_S3_TRANSFER_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": [
        "provider",
        "operation",
        "status",
        "completed",
        "skipped",
        "failed",
        "bytes_transferred",
    ],
    "properties": {
        "provider": {"type": "string", "const": "aws-s3"},
        "operation": {"type": "string", "enum": ["file.upload", "file.download"]},
        "status": {"type": "string", "enum": ["success", "partial", "failed"]},
        "completed": {"type": "array", "items": {"type": "object"}},
        "skipped": {"type": "array", "items": {"type": "object"}},
        "failed": {"type": "array", "items": {"type": "object"}},
        "bytes_transferred": {"type": "integer", "minimum": 0},
    },
}
_S3_MUTATION_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["provider", "operation", "status"],
    "properties": {
        "provider": {"type": "string", "const": "aws-s3"},
        "operation": {
            "type": "string",
            "enum": ["file.delete", "directory.create", "directory.delete", "path.rename"],
        },
        "status": {"type": "string", "enum": ["success", "partial", "failed", "skipped"]},
    },
}

_S3_PROVIDER_KWARGS: dict[str, Any] = {
    "key": "aws-s3",
    "name": "Amazon S3",
    "description": (
        "Amazon S3 general-purpose bucket and optional path connection for bounded "
        "object listing, transfer, marker, delete, and exact-object move operations."
    ),
    "auth_type": "aws-access-key",
    "auth_methods": [
        {
            "key": "aws-access-key",
            "label": "AWS access key",
            "auth_type": "aws-access-key",
            "payload_format": "json",
            "permission_verification": {
                "evidence_source": "unavailable",
                "enforcement": "provider_enforced",
            },
            "fields": [
                {
                    "key": "access_key_id",
                    "label": "Access key ID",
                    "type": "secret",
                    "secret": True,
                    "required": True,
                    "description": "Stored and returned only through the daemon secret boundary.",
                },
                {
                    "key": "secret_access_key",
                    "label": "Secret access key",
                    "type": "secret",
                    "secret": True,
                    "required": True,
                },
                {
                    "key": "session_token",
                    "label": "Session token",
                    "type": "secret",
                    "secret": True,
                    "required": False,
                    "description": "Required when the supplied credentials are temporary.",
                },
                {
                    "key": "bucket",
                    "label": "Bucket",
                    "type": "text",
                    "required": True,
                    "placeholder": "example-bucket",
                    "description": "One Amazon S3 general-purpose bucket.",
                },
                {
                    "key": "prefix",
                    "label": "Path",
                    "type": "path",
                    "required": False,
                    "placeholder": "data/",
                    "description": (
                        "Optional relative object-key path used as this Account's root. "
                        "Leave blank for the bucket root."
                    ),
                },
                {
                    "key": "region",
                    "label": "AWS region",
                    "type": "select",
                    "required": True,
                    "placeholder": "us-west-2",
                    "options": [{"value": region, "label": region} for region in AWS_S3_REGIONS],
                    "description": (
                        "Amazon S3 region supported by the bundled AWS SDK endpoint model, "
                        "including commercial, China, GovCloud, and isolated partitions."
                    ),
                },
            ],
        }
    ],
    "config": {
        "setup_note": (
            "Bind one Amazon S3 general-purpose bucket using explicit access-key "
            "credentials, its AWS region, and an optional relative object-key path."
        ),
        "setup": {
            "credential_label": "AWS access key and one S3 bucket",
            "setup_note": (
                "Prefer temporary least-privilege credentials. Store the access key "
                "id, secret access key, optional session token, bucket, optional path, "
                "and region only in StackOS."
            ),
            "homepage_url": "https://aws.amazon.com/s3/",
            "signup_url": "https://portal.aws.amazon.com/billing/signup",
            "console_url": "https://console.aws.amazon.com/s3/",
            "credential_url": "https://console.aws.amazon.com/iam/home#/security_credentials",
            "billing_url": "https://aws.amazon.com/s3/pricing/",
            "docs_url": "https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html",
            "fallback_url": "https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html",
            "fallback_reason": (
                "IAM credential provisioning depends on the operator's AWS account "
                "and organization policy."
            ),
            "verified_at": "2026-07-26",
            "url_confidence": {
                "homepage_url": "verified",
                "signup_url": "verified",
                "console_url": "verified",
                "credential_url": "directional",
                "billing_url": "verified",
                "docs_url": "verified",
                "fallback_url": "verified",
            },
        },
        "docs": [
            "docs/integration-contracts/s3.md",
            "https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-iam.html",
            "https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html",
        ],
    },
}


def _action(
    *,
    key: str,
    name: str,
    description: str,
    risk_level: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    execution_mode: str | None = None,
    agent_guidance: str,
    docs: list[str],
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "schema_version": "stackos.action.v1",
        "connector": "aws-s3",
        "operation": key.removeprefix("s3."),
        "requires_credential": True,
        "enforce_budget": False,
        "agent_guidance": agent_guidance,
        "docs": ["docs/integration-contracts/s3.md", *docs],
    }
    if execution_mode is not None:
        config["execution_mode"] = execution_mode
    return {
        "key": key,
        "name": name,
        "description": description,
        "provider": "aws-s3",
        "capability": "file-transfer",
        "risk_level": risk_level,
        "input_schema": input_schema,
        "output_schema": output_schema,
        "config": config,
    }


_S3_ACTION_KWARGS: tuple[dict[str, Any], ...] = (
    _action(
        key="s3.directory.list",
        name="List Amazon S3 Prefix",
        description="List one bounded page of objects and common prefixes.",
        risk_level="read",
        input_schema=_S3_DIRECTORY_LIST_INPUT_SCHEMA,
        output_schema=_S3_LIST_OUTPUT_SCHEMA,
        agent_guidance=(
            "Pass the exact optional prefix, delimiter, page size, and opaque cursor. "
            "The action returns one provider page and does not download objects."
        ),
        docs=["https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html"],
    ),
    _action(
        key="s3.file.upload",
        name="Upload Files Or Directories To Amazon S3",
        description="Upload explicit local mappings with conditional conflict handling.",
        risk_level="write",
        input_schema=_S3_UPLOAD_INPUT_SCHEMA,
        output_schema=_S3_TRANSFER_OUTPUT_SCHEMA,
        execution_mode="background",
        agent_guidance=(
            "Choose exact local paths and destination keys, then select conflict, "
            "error, and symlink policies. Directory uploads preserve relative keys "
            "and represent empty directories with marker objects."
        ),
        docs=[
            "https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html",
            "https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html",
        ],
    ),
    _action(
        key="s3.file.download",
        name="Download Objects Or Prefixes From Amazon S3",
        description="Download explicit object or prefix mappings to local destinations.",
        risk_level="write",
        input_schema=_S3_DOWNLOAD_INPUT_SCHEMA,
        output_schema=_S3_TRANSFER_OUTPUT_SCHEMA,
        execution_mode="background",
        agent_guidance=(
            "Declare object-versus-prefix intent for every mapping. Prefix downloads "
            "fully page the selected current-object tree and keep projected paths "
            "inside the exact local destination."
        ),
        docs=[
            "https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetObject.html",
            "https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html",
        ],
    ),
    _action(
        key="s3.file.delete",
        name="Delete Amazon S3 Object",
        description="Delete one exact current object and retain version/delete-marker facts.",
        risk_level="write",
        input_schema=_S3_FILE_DELETE_INPUT_SCHEMA,
        output_schema=_S3_MUTATION_OUTPUT_SCHEMA,
        agent_guidance=(
            "Pass one exact current object key and optional expected ETag. The action "
            "does not enumerate or purge historical versions."
        ),
        docs=["https://docs.aws.amazon.com/AmazonS3/latest/API/API_DeleteObject.html"],
    ),
    _action(
        key="s3.directory.create",
        name="Create Amazon S3 Directory Marker",
        description="Create one zero-byte trailing-slash marker object.",
        risk_level="write",
        input_schema=_S3_DIRECTORY_CREATE_INPUT_SCHEMA,
        output_schema=_S3_MUTATION_OUTPUT_SCHEMA,
        agent_guidance=(
            "Pass one exact prefix. The result describes a marker object, not a "
            "native directory or implicitly created parent hierarchy."
        ),
        docs=["https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-keys.html"],
    ),
    _action(
        key="s3.directory.delete",
        name="Delete Amazon S3 Prefix",
        description="Delete an empty marker or a bounded current-object prefix set.",
        risk_level="write",
        input_schema=_S3_DIRECTORY_DELETE_INPUT_SCHEMA,
        output_schema=_S3_MUTATION_OUTPUT_SCHEMA,
        execution_mode="background",
        agent_guidance=(
            "Set recursive and max_objects explicitly. Recursive deletion inventories "
            "the full bounded current-object set before mutation and never purges "
            "historical versions."
        ),
        docs=[
            "https://docs.aws.amazon.com/AmazonS3/latest/API/API_DeleteObjects.html",
            "https://docs.aws.amazon.com/AmazonS3/latest/userguide/DeletingObjectVersions.html",
        ],
    ),
    _action(
        key="s3.path.rename",
        name="Move Amazon S3 Object",
        description="Copy then conditionally delete one exact current object.",
        risk_level="write",
        input_schema=_S3_RENAME_INPUT_SCHEMA,
        output_schema=_S3_MUTATION_OUTPUT_SCHEMA,
        execution_mode="background",
        agent_guidance=(
            "Pass distinct exact object keys and a destination conflict policy. This "
            "is a non-atomic copy/delete operation; prefix moves are not supported."
        ),
        docs=[
            "https://docs.aws.amazon.com/AmazonS3/latest/API/API_CopyObject.html",
            "https://docs.aws.amazon.com/AmazonS3/latest/API/API_DeleteObject.html",
        ],
    ),
)


def s3_provider_kwargs() -> dict[str, Any]:
    """Return a fresh plain-data provider fragment for manifest validation."""
    return deepcopy(_S3_PROVIDER_KWARGS)


def s3_action_kwargs() -> list[dict[str, Any]]:
    """Return fresh plain-data action fragments in canonical manifest order."""
    return deepcopy(list(_S3_ACTION_KWARGS))


__all__ = ["s3_action_kwargs", "s3_provider_kwargs"]
