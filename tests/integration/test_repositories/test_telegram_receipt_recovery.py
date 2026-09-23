"""Delayed native receipts resolve exact held attempts and never send again."""

from pathlib import Path

from sqlmodel import Session

from stackos.actions import ActionRepository
from stackos.integrations.telegram_tdlib.receipts import reconcile_telegram_receipt
from stackos.repositories.resources import ResourceRepository
from tests.integration.test_repositories.test_durable_action_runtime import _database


def test_delayed_album_receipts_reconcile_after_restart(tmp_path: Path) -> None:
    engine, project_id, _, account_ref, action_call_id = _database(tmp_path)
    with Session(engine) as session:
        repo = ActionRepository(session)
        job = repo.create_durable_action_job(
            project_id=project_id,
            action_call_id=action_call_id,
            credential_ref=account_ref,
            action_ref="durable-test.message.send",
            items=[
                {
                    "destination_ref": "telegram-chat:7",
                    "input_json": {
                        "profile_ref": "communication-profile:ops",
                        "content": {
                            "@type": "messageText",
                            "text": {"text": "Delayed delivery"},
                        },
                    },
                    "correlation_ref": "album-1",
                }
            ],
        )
        item = repo.claim_durable_action_items(project_id=project_id, job_id=job.id)[0]
        repo.record_durable_action_item_progress(
            project_id=project_id,
            item_id=item.id,
            attempt_ref=item.attempt_ref,
            progress_json={"chat_id": 7, "temporary_message_ids": [-11, -12]},
        )
        repo.reconcile_durable_action_jobs()
        for index in [11, 12]:
            reconcile_telegram_receipt(
                session,
                credential_ref=account_ref,
                update={
                    "@type": "updateMessageSendSucceeded",
                    "old_message_id": -index,
                    "message": {"id": index * 1048576, "chat_id": 7},
                },
            )
            current = repo.list_durable_action_items(project_id=project_id, job_id=job.id)[0]
            assert current.state == ("unknown-hold" if index == 11 else "succeeded")
            messages = (
                ResourceRepository(session)
                .query_records(
                    project_id=project_id,
                    plugin_slug="communications",
                    resource_key="communication-message",
                )
                .items
            )
            assert [message.external_id for message in messages] == [
                f"telegram-message:ops:7:{index * 1048576}" for index in range(11, index + 1)
            ]
            assert messages[-1].data_json["direction"] == "outbound"
            assert messages[-1].data_json["credential_ref"] == account_ref
        assert current.result_json["message_refs"] == [
            "telegram-message:7:11534336",
            "telegram-message:7:12582912",
        ]
        assert current.attempt_count == 1
        # Duplicate updates cannot create new attempts or change a completed result.
        reconcile_telegram_receipt(
            session,
            credential_ref=account_ref,
            update={
                "@type": "updateMessageSendSucceeded",
                "old_message_id": -11,
                "message": {"id": 99, "chat_id": 7},
            },
        )
        assert repo.list_durable_action_items(project_id=project_id, job_id=job.id)[0] == current
        assert [
            message.external_id
            for message in ResourceRepository(session)
            .query_records(
                project_id=project_id,
                plugin_slug="communications",
                resource_key="communication-message",
            )
            .items
        ] == [
            "telegram-message:ops:7:11534336",
            "telegram-message:ops:7:12582912",
        ]
