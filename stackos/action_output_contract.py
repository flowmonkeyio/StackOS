"""Shared contract for file-backed action output envelopes."""

from __future__ import annotations

from typing import Any

ACTION_OUTPUT_SCHEMA_REF = "stackos.action-output.v1"
ACTION_OUTPUT_SCHEMA_OPERATION = "schema.get"
ACTION_OUTPUT_SCHEMA_CONTENT_TYPE = "application/schema+json"

ACTION_OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "stackos:schema:stackos.action-output.v1",
    "title": "StackOS action output file envelope",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "recorded_at",
        "project",
        "run",
        "action_call",
        "action",
        "request",
        "response",
        "summaries",
    ],
    "properties": {
        "schema_version": {"const": ACTION_OUTPUT_SCHEMA_REF},
        "recorded_at": {"type": "string", "format": "date-time"},
        "project": {
            "type": "object",
            "additionalProperties": False,
            "required": ["project_id"],
            "properties": {"project_id": {"type": "integer"}},
        },
        "run": {
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id", "run_plan_id", "run_plan_step_id"],
            "properties": {
                "run_id": {"type": ["integer", "null"]},
                "run_plan_id": {"type": ["integer", "null"]},
                "run_plan_step_id": {"type": ["integer", "null"]},
            },
        },
        "action_call": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "status", "created_at", "completed_at"],
            "properties": {
                "id": {"type": "integer"},
                "status": {"type": "string"},
                "created_at": {"type": ["string", "null"], "format": "date-time"},
                "completed_at": {"type": ["string", "null"], "format": "date-time"},
            },
        },
        "action": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "action_ref",
                "plugin_slug",
                "action_key",
                "provider_key",
                "connector_key",
                "operation",
                "risk_level",
            ],
            "properties": {
                "action_ref": {"type": "string"},
                "plugin_slug": {"type": "string"},
                "action_key": {"type": "string"},
                "provider_key": {"type": ["string", "null"]},
                "connector_key": {"type": ["string", "null"]},
                "operation": {"type": "string"},
                "risk_level": {"type": "string"},
            },
        },
        "request": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "input_json",
                "provider_context_json",
                "credential_ref",
                "context_ref",
            ],
            "properties": {
                "input_json": {"type": "object", "additionalProperties": True},
                "provider_context_json": {
                    "type": ["object", "null"],
                    "additionalProperties": True,
                },
                "credential_ref": {"type": ["string", "null"]},
                "context_ref": {"type": ["string", "null"]},
            },
        },
        "response": {
            "type": "object",
            "additionalProperties": False,
            "required": ["output_json", "metadata_json", "cost_cents", "duration_ms", "dry_run"],
            "properties": {
                "output_json": {"type": "object", "additionalProperties": True},
                "metadata_json": {"type": ["object", "null"], "additionalProperties": True},
                "cost_cents": {"type": "integer"},
                "duration_ms": {"type": ["integer", "null"]},
                "dry_run": {"type": "boolean"},
            },
        },
        "summaries": {
            "type": "object",
            "additionalProperties": False,
            "required": ["request", "response"],
            "properties": {
                "request": {"$ref": "#/$defs/json_summary"},
                "response": {"$ref": "#/$defs/json_summary"},
            },
        },
    },
    "$defs": {
        "json_summary": {
            "type": "object",
            "additionalProperties": True,
            "required": ["top_level_shape"],
            "properties": {
                "top_level_shape": {"type": "object", "additionalProperties": True},
                "keys": {"type": "array", "items": {"type": "string"}},
                "length": {"type": "integer"},
            },
        }
    },
}


def action_output_schema_hint() -> dict[str, str]:
    """Return the compact pointer fields agents need to fetch this schema."""
    return {
        "schema_ref": ACTION_OUTPUT_SCHEMA_REF,
        "schema_operation": ACTION_OUTPUT_SCHEMA_OPERATION,
    }


__all__ = [
    "ACTION_OUTPUT_SCHEMA",
    "ACTION_OUTPUT_SCHEMA_CONTENT_TYPE",
    "ACTION_OUTPUT_SCHEMA_OPERATION",
    "ACTION_OUTPUT_SCHEMA_REF",
    "action_output_schema_hint",
]
