from stackos.operations.actions import _approval_ref_for_action, _compact_action_output


def test_approval_ref_for_action_resolves_template_contract_refs() -> None:
    grant_snapshot = {
        "template_plugin_slug": "branding",
        "resolved_action_contracts": [
            {
                "key": "image_generate",
                "action_ref": "utils.image.generate",
            }
        ],
        "action_contracts": [
            {
                "key": "image_generate",
                "action": "utils.image.generate",
                "approval_ref": "image_generation_approval",
            },
            {
                "key": "publish_linkedin",
                "action": "publish.linkedin",
            },
        ],
    }

    assert (
        _approval_ref_for_action(grant_snapshot, "utils.image.generate")
        == "image_generation_approval"
    )
    assert _approval_ref_for_action(grant_snapshot, "branding.publish.linkedin") is None


def test_telegram_file_download_compact_output_keeps_artifact_handoff_fields() -> None:
    compact = _compact_action_output(
        provider_key="telegram-bot",
        operation="file.download",
        output_json={
            "artifact_ref": "/generated-assets/communication-media/telegram/issue.png",
            "artifact_id": 42,
            "filename": "issue.png",
            "mime_type": "image/png",
            "size_bytes": 123,
            "source_file_id": "tg_file_1",
            "source_message_ref": "telegram-message:12345:21",
            "body": {"ok": True},
        },
    )

    assert compact == {
        "operation": "file.download",
        "artifact_ref": "/generated-assets/communication-media/telegram/issue.png",
        "artifact_id": 42,
        "filename": "issue.png",
        "mime_type": "image/png",
        "size_bytes": 123,
        "source_file_id": "tg_file_1",
        "source_message_ref": "telegram-message:12345:21",
        "provider_ok": True,
    }


def test_file_backed_action_compact_output_keeps_artifact_pointer() -> None:
    compact = _compact_action_output(
        provider_key="provider",
        operation="operation.execute",
        output_json={
            "output_mode": "file",
            "file": {
                "absolute_path": "/tmp/generated/action-output.json",
                "uri": "/generated-assets/action-outputs/project-1/action-output.json",
                "artifact_id": 42,
                "semantic_name": "action-output",
                "bytes": 1200,
                "sha256": "abc",
                "read": {"operation": "executionContext.artifact.read"},
                "top_level_shape": {"type": "object"},
            },
        },
    )

    assert compact == {
        "output_mode": "file",
        "absolute_path": "/tmp/generated/action-output.json",
        "uri": "/generated-assets/action-outputs/project-1/action-output.json",
        "artifact_id": 42,
        "semantic_name": "action-output",
        "bytes": 1200,
        "sha256": "abc",
        "read": {"operation": "executionContext.artifact.read"},
    }
