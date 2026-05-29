from stackos.operations.actions import _compact_action_output


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
