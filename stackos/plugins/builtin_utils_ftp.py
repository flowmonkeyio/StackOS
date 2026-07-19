"""Model-independent FTP fragments for the built-in utils plugin."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_FTP_DIRECTORY_LIST_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["remote_path"],
    "properties": {
        "remote_path": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Absolute or current-directory-relative remote FTP directory selected "
                "by the agent. Each call opens a fresh connection."
            ),
        }
    },
}
_FTP_TRANSFER_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["local_path", "remote_path"],
    "properties": {
        "local_path": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Agent-selected daemon-local file or directory path. StackOS does not "
                "restrict transfers to generated assets."
            ),
        },
        "remote_path": {
            "type": "string",
            "minLength": 1,
            "description": "Exact remote file or directory root for this mapping.",
        },
    },
}
_FTP_TRANSFER_BASE_PROPERTIES = {
    "items": {
        "type": "array",
        "minItems": 1,
        "items": _FTP_TRANSFER_ITEM_SCHEMA,
        "description": "One or more explicit local/remote path mappings.",
    },
    "conflict_policy": {
        "type": "string",
        "enum": ["overwrite", "skip", "fail"],
        "description": "Agent-selected handling when the destination already exists.",
    },
    "error_policy": {
        "type": "string",
        "enum": ["stop", "continue"],
        "description": "Stop at the first failed path or continue the remaining batch.",
    },
}
_FTP_UPLOAD_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "conflict_policy", "error_policy"],
    "properties": {
        **_FTP_TRANSFER_BASE_PROPERTIES,
        "follow_symlinks": {
            "type": "boolean",
            "default": False,
            "description": (
                "Follow local symlinks under their link names with cycle detection. "
                "False skips and reports symlinks."
            ),
        },
    },
}
_FTP_DOWNLOAD_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "conflict_policy", "error_policy"],
    "properties": _FTP_TRANSFER_BASE_PROPERTIES,
}
_FTP_REMOTE_PATH_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["remote_path"],
    "properties": {
        "remote_path": {
            "type": "string",
            "minLength": 1,
            "description": "Exact remote FTP path selected by the agent.",
        },
    },
}
_FTP_DIRECTORY_DELETE_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["remote_path", "recursive"],
    "properties": {
        "remote_path": {
            "type": "string",
            "minLength": 1,
            "description": "Exact remote FTP directory selected by the agent.",
        },
        "recursive": {
            "type": "boolean",
            "description": (
                "False sends one RMD for the selected directory. True deletes its "
                "machine-readable child tree post-order before removing the root."
            ),
        },
    },
}
_FTP_RENAME_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_path", "destination_path"],
    "properties": {
        "source_path": {
            "type": "string",
            "minLength": 1,
            "description": "Exact existing remote FTP path selected by the agent.",
        },
        "destination_path": {
            "type": "string",
            "minLength": 1,
            "description": "Exact new remote FTP path selected by the agent.",
        },
    },
}
_FTP_DIRECTORY_LIST_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "provider",
        "operation",
        "status",
        "remote_path",
        "entry_count",
        "directory_count",
        "file_count",
        "symlink_count",
        "entries",
    ],
    "properties": {
        "provider": {"type": "string", "const": "ftp"},
        "operation": {"type": "string", "const": "directory.list"},
        "status": {"type": "string", "const": "success"},
        "remote_path": {"type": "string"},
        "entry_count": {"type": "integer", "minimum": 0},
        "directory_count": {"type": "integer", "minimum": 0},
        "file_count": {"type": "integer", "minimum": 0},
        "symlink_count": {"type": "integer", "minimum": 0},
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "remote_path", "type", "safe_to_traverse"],
                "properties": {
                    "name": {"type": "string"},
                    "remote_path": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "type": {
                        "type": "string",
                        "enum": ["directory", "file", "symlink", "unknown"],
                    },
                    "safe_to_traverse": {"type": "boolean"},
                    "size": {"anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
                    "modified": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "unique": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "permissions": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
        },
    },
}
_FTP_TRANSFER_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "provider",
        "operation",
        "status",
        "completed_count",
        "skipped_count",
        "failed_count",
        "created_directory_count",
        "bytes_transferred",
        "completed",
        "skipped",
        "failed",
        "created_directories",
        "completed_paths",
        "skipped_paths",
        "failed_paths",
    ],
    "properties": {
        "provider": {"type": "string", "const": "ftp"},
        "operation": {"type": "string", "enum": ["file.upload", "file.download"]},
        "status": {"type": "string", "enum": ["success", "partial", "failed"]},
        "completed_count": {"type": "integer", "minimum": 0},
        "skipped_count": {"type": "integer", "minimum": 0},
        "failed_count": {"type": "integer", "minimum": 0},
        "created_directory_count": {"type": "integer", "minimum": 0},
        "bytes_transferred": {"type": "integer", "minimum": 0},
        "completed": {"type": "array", "items": {"type": "object"}},
        "skipped": {"type": "array", "items": {"type": "object"}},
        "failed": {"type": "array", "items": {"type": "object"}},
        "created_directories": {"type": "array", "items": {"type": "object"}},
        "completed_paths": {"type": "array", "items": {"type": "string"}},
        "skipped_paths": {"type": "array", "items": {"type": "string"}},
        "failed_paths": {"type": "array", "items": {"type": "string"}},
    },
}
_FTP_FILE_DELETE_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["provider", "operation", "status", "remote_path"],
    "properties": {
        "provider": {"type": "string", "const": "ftp"},
        "operation": {"type": "string", "const": "file.delete"},
        "status": {"type": "string", "const": "success"},
        "remote_path": {"type": "string"},
    },
}
_FTP_DIRECTORY_CREATE_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["provider", "operation", "status", "remote_path"],
    "properties": {
        "provider": {"type": "string", "const": "ftp"},
        "operation": {"type": "string", "const": "directory.create"},
        "status": {"type": "string", "const": "success"},
        "remote_path": {"type": "string"},
    },
}
_FTP_DIRECTORY_DELETE_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "provider",
        "operation",
        "status",
        "remote_path",
        "recursive",
        "deleted_count",
        "file_count",
        "directory_count",
        "symlink_count",
        "deleted_paths",
    ],
    "properties": {
        "provider": {"type": "string", "const": "ftp"},
        "operation": {"type": "string", "const": "directory.delete"},
        "status": {"type": "string", "const": "success"},
        "remote_path": {"type": "string"},
        "recursive": {"type": "boolean"},
        "deleted_count": {"type": "integer", "minimum": 0},
        "file_count": {"type": "integer", "minimum": 0},
        "directory_count": {"type": "integer", "minimum": 0},
        "symlink_count": {"type": "integer", "minimum": 0},
        "deleted_paths": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["remote_path", "type"],
                "properties": {
                    "remote_path": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["file", "directory", "symlink"],
                    },
                },
            },
        },
    },
}
_FTP_RENAME_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "provider",
        "operation",
        "status",
        "source_path",
        "destination_path",
    ],
    "properties": {
        "provider": {"type": "string", "const": "ftp"},
        "operation": {"type": "string", "const": "path.rename"},
        "status": {"type": "string", "const": "success"},
        "source_path": {"type": "string"},
        "destination_path": {"type": "string"},
    },
}

_FTP_PROVIDER_KWARGS: dict[str, Any] = {
    "key": "ftp",
    "name": "FTP / Explicit FTPS",
    "description": (
        "Protocol connection for stateless remote browsing, recursive "
        "agent-selected transfers, and exact remote path management."
    ),
    "auth_type": "ftp-password",
    "auth_methods": [
        {
            "key": "ftp-password",
            "label": "FTP password",
            "auth_type": "ftp-password",
            "payload_format": "json",
            "fields": [
                {
                    "key": "password",
                    "label": "Password",
                    "type": "secret",
                    "secret": True,
                    "required": True,
                },
                {
                    "key": "host",
                    "label": "Host",
                    "type": "text",
                    "required": True,
                    "placeholder": "ftp.example.com",
                    "description": (
                        "Hostname or IP address only; do not include ftp://, "
                        "a path, or credentials."
                    ),
                },
                {
                    "key": "port",
                    "label": "Port",
                    "type": "number",
                    "placeholder": "21",
                    "description": "Defaults to 21 when omitted.",
                },
                {
                    "key": "tls_mode",
                    "label": "Transport security",
                    "type": "select",
                    "options": [
                        {"value": "explicit", "label": "Explicit FTPS"},
                        {"value": "none", "label": "Plain FTP"},
                    ],
                    "description": (
                        "Explicit uses AUTH TLS and protects data transfers "
                        "with PROT P. None deliberately selects plain FTP."
                    ),
                },
                {
                    "key": "username",
                    "label": "Username",
                    "type": "text",
                    "required": True,
                },
                {
                    "key": "passive_mode",
                    "label": "Passive mode",
                    "type": "select",
                    "options": [
                        {"value": "true", "label": "Enabled (default)"},
                        {"value": "false", "label": "Disabled"},
                    ],
                    "description": "Defaults to enabled when omitted.",
                },
                {
                    "key": "timeout_s",
                    "label": "Timeout seconds",
                    "type": "number",
                    "description": "Per-connection timeout; defaults to 30 seconds.",
                },
                {
                    "key": "encoding",
                    "label": "Filename encoding",
                    "type": "text",
                    "placeholder": "utf-8",
                    "description": "Defaults to UTF-8.",
                },
            ],
        }
    ],
    "config": {
        "setup_note": (
            "Use the FTP server's host, username, password, and transport "
            "settings. Explicit FTPS is the default; plain FTP is available "
            "when deliberately selected."
        ),
        "setup": {
            "credential_label": "FTP host, username, and password",
            "setup_note": (
                "FTP is a protocol, not one vendor. Obtain connection "
                "settings from the server operator and store the password "
                "only in StackOS."
            ),
            "docs_url": "https://www.rfc-editor.org/rfc/rfc4217",
            "fallback_url": "https://docs.python.org/3/library/ftplib.html",
            "fallback_reason": "Server provisioning and credential pages are provider-specific.",
            "verified_at": "2026-07-15",
            "url_confidence": {
                "docs_url": "verified",
                "fallback_url": "verified",
            },
        },
        "docs": [
            "docs/integration-contracts/ftp.md",
            "https://www.rfc-editor.org/rfc/rfc959",
            "https://www.rfc-editor.org/rfc/rfc3659",
            "https://www.rfc-editor.org/rfc/rfc4217",
        ],
    },
}

_FTP_ACTION_KWARGS: tuple[dict[str, Any], ...] = (
    {
        "key": "ftp.directory.list",
        "name": "List FTP Directory",
        "description": (
            "List one remote FTP directory without downloading its files; "
            "returned child paths can be reused in later actions."
        ),
        "provider": "ftp",
        "capability": "file-transfer",
        "risk_level": "read",
        "input_schema": _FTP_DIRECTORY_LIST_INPUT_SCHEMA,
        "output_schema": _FTP_DIRECTORY_LIST_OUTPUT_SCHEMA,
        "config": {
            "schema_version": "stackos.action.v1",
            "connector": "ftp",
            "operation": "directory.list",
            "requires_credential": True,
            "enforce_budget": False,
            "agent_guidance": (
                "Pass the remote directory selected by the user or agent. The "
                "action opens a fresh connection and returns metadata only; it "
                "does not retain a hidden working-directory session or download."
            ),
            "docs": [
                "docs/integration-contracts/ftp.md",
                "https://www.rfc-editor.org/rfc/rfc3659",
            ],
        },
    },
    {
        "key": "ftp.file.upload",
        "name": "Upload Files Or Directories To FTP",
        "description": (
            "Upload one or more agent-selected local files or directories; "
            "directories recurse automatically and preserve their relative tree."
        ),
        "provider": "ftp",
        "capability": "file-transfer",
        "risk_level": "write",
        "input_schema": _FTP_UPLOAD_INPUT_SCHEMA,
        "output_schema": _FTP_TRANSFER_OUTPUT_SCHEMA,
        "config": {
            "schema_version": "stackos.action.v1",
            "connector": "ftp",
            "operation": "file.upload",
            "requires_credential": True,
            "enforce_budget": False,
            "agent_guidance": (
                "Choose any daemon-readable local path or paths and exact remote "
                "targets. StackOS does not require generated assets or another "
                "FTP-specific path gate. Select overwrite, skip, or fail and "
                "stop or continue explicitly for each call."
            ),
            "docs": [
                "docs/integration-contracts/ftp.md",
                "https://www.rfc-editor.org/rfc/rfc959",
                "https://www.rfc-editor.org/rfc/rfc4217",
            ],
        },
    },
    {
        "key": "ftp.file.download",
        "name": "Download Files Or Directories From FTP",
        "description": (
            "Download one or more remote FTP files or directories to exact "
            "agent-selected local destinations with recursive tree preservation."
        ),
        "provider": "ftp",
        "capability": "file-transfer",
        "risk_level": "write",
        "input_schema": _FTP_DOWNLOAD_INPUT_SCHEMA,
        "output_schema": _FTP_TRANSFER_OUTPUT_SCHEMA,
        "config": {
            "schema_version": "stackos.action.v1",
            "connector": "ftp",
            "operation": "file.download",
            "requires_credential": True,
            "enforce_budget": False,
            "agent_guidance": (
                "Choose any daemon-writable local destination path or paths. "
                "Directories recurse automatically; select overwrite, skip, or "
                "fail and stop or continue explicitly. Remote child names are "
                "kept inside the selected destination tree."
            ),
            "docs": [
                "docs/integration-contracts/ftp.md",
                "https://www.rfc-editor.org/rfc/rfc959",
                "https://www.rfc-editor.org/rfc/rfc3659",
                "https://www.rfc-editor.org/rfc/rfc4217",
            ],
        },
    },
    {
        "key": "ftp.file.delete",
        "name": "Delete FTP File",
        "description": "Delete one exact agent-selected remote file with FTP DELE.",
        "provider": "ftp",
        "capability": "file-transfer",
        "risk_level": "write",
        "input_schema": _FTP_REMOTE_PATH_INPUT_SCHEMA,
        "output_schema": _FTP_FILE_DELETE_OUTPUT_SCHEMA,
        "config": {
            "schema_version": "stackos.action.v1",
            "connector": "ftp",
            "operation": "file.delete",
            "requires_credential": True,
            "enforce_budget": False,
            "agent_guidance": (
                "Pass the exact remote file path selected by the user or agent. "
                "StackOS sends DELE without another FTP-specific confirmation or "
                "path gate; missing paths and permission failures remain provider "
                "errors."
            ),
            "docs": [
                "docs/integration-contracts/ftp.md",
                "https://www.rfc-editor.org/rfc/rfc959",
                "https://docs.python.org/3/library/ftplib.html#ftplib.FTP.delete",
            ],
        },
    },
    {
        "key": "ftp.directory.create",
        "name": "Create FTP Directory",
        "description": "Create one exact agent-selected remote directory with FTP MKD.",
        "provider": "ftp",
        "capability": "file-transfer",
        "risk_level": "write",
        "input_schema": _FTP_REMOTE_PATH_INPUT_SCHEMA,
        "output_schema": _FTP_DIRECTORY_CREATE_OUTPUT_SCHEMA,
        "config": {
            "schema_version": "stackos.action.v1",
            "connector": "ftp",
            "operation": "directory.create",
            "requires_credential": True,
            "enforce_budget": False,
            "agent_guidance": (
                "Pass one exact remote directory path. This action sends one MKD; "
                "it does not silently create missing parents or convert an "
                "existing-path provider rejection into success."
            ),
            "docs": [
                "docs/integration-contracts/ftp.md",
                "https://www.rfc-editor.org/rfc/rfc959",
                "https://docs.python.org/3/library/ftplib.html#ftplib.FTP.mkd",
            ],
        },
    },
    {
        "key": "ftp.directory.delete",
        "name": "Delete FTP Directory",
        "description": (
            "Delete one exact remote directory, either with one RMD or with an "
            "agent-selected recursive post-order deletion."
        ),
        "provider": "ftp",
        "capability": "file-transfer",
        "risk_level": "write",
        "input_schema": _FTP_DIRECTORY_DELETE_INPUT_SCHEMA,
        "output_schema": _FTP_DIRECTORY_DELETE_OUTPUT_SCHEMA,
        "config": {
            "schema_version": "stackos.action.v1",
            "connector": "ftp",
            "operation": "directory.delete",
            "requires_credential": True,
            "enforce_budget": False,
            "agent_guidance": (
                "Set recursive explicitly. False sends one RMD and lets the server "
                "reject non-empty directories. True plans the selected tree from "
                "machine-readable MLSx facts, deletes files and identified links "
                "with DELE, then removes directories post-order. Partial effects "
                "are returned if a later mutation fails."
            ),
            "docs": [
                "docs/integration-contracts/ftp.md",
                "https://www.rfc-editor.org/rfc/rfc959",
                "https://www.rfc-editor.org/rfc/rfc3659",
                "https://docs.python.org/3/library/ftplib.html#ftplib.FTP.rmd",
            ],
        },
    },
    {
        "key": "ftp.path.rename",
        "name": "Rename Or Move FTP Path",
        "description": (
            "Ask the FTP server to rename or move one exact remote file or "
            "directory path with RNFR followed by RNTO."
        ),
        "provider": "ftp",
        "capability": "file-transfer",
        "risk_level": "write",
        "input_schema": _FTP_RENAME_INPUT_SCHEMA,
        "output_schema": _FTP_RENAME_OUTPUT_SCHEMA,
        "config": {
            "schema_version": "stackos.action.v1",
            "connector": "ftp",
            "operation": "path.rename",
            "requires_credential": True,
            "enforce_budget": False,
            "agent_guidance": (
                "Pass exact source and destination paths. StackOS does not "
                "pre-delete a destination, force replacement, create destination "
                "parents, or emulate rejected directory moves. Existing-target and "
                "directory support remain server-defined."
            ),
            "docs": [
                "docs/integration-contracts/ftp.md",
                "https://www.rfc-editor.org/rfc/rfc959",
                "https://docs.python.org/3/library/ftplib.html#ftplib.FTP.rename",
            ],
        },
    },
)


def ftp_provider_kwargs() -> dict[str, Any]:
    """Return a fresh plain-data provider fragment for manifest validation."""
    return deepcopy(_FTP_PROVIDER_KWARGS)


def ftp_action_kwargs() -> list[dict[str, Any]]:
    """Return fresh plain-data action fragments in canonical manifest order."""
    return deepcopy(list(_FTP_ACTION_KWARGS))


__all__ = ["ftp_action_kwargs", "ftp_provider_kwargs"]
