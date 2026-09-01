import json
from types import SimpleNamespace

import pytest

from app.email_context_source import EmailContextSource


def _payload(stable_identity: str) -> dict[str, object]:
    return {
        "schema": "email_agent_action.v1",
        "account_id": "account-1",
        "stable_message_identity": stable_identity,
        "thread_identity": "provider-thread-1",
        "classification_id": 17,
        "action_identity": "email-action:unsubscribe-17",
        "action_plan_id": "email-plan:17:v3",
        "action_plan_version": 3,
        "model_id": "email-model:2026-08-30:sha256:abc",
        "config_version": "email-config:v7",
    }


def test_context_source_projects_complete_text_metadata_versions_and_receipts():
    stable_identity = "account-1:message-id:<current@example.com>"
    rows = [
        {
            "stable_message_identity": "account-1:message-id:<prior@example.com>",
            "thread_identity": "provider-thread-1",
            "sender": "prior@example.com",
            "subject": "Earlier",
            "normalized_text": "Earlier thread text",
            "attachment_metadata": [],
            "received_at": "2026-08-29T08:00:00+00:00",
        },
        {
            "stable_message_identity": stable_identity,
            "thread_identity": "provider-thread-1",
            "sender": "sender@example.com",
            "subject": "Current subject",
            "normalized_text": "Current message text",
            "attachment_metadata": [
                {
                    "filename": "contract.pdf",
                    "mime_type": "application/pdf",
                    "size_bytes": 1234,
                    "inline": False,
                }
            ],
            "received_at": "2026-08-30T09:00:00+00:00",
        },
    ]

    class PublicStore:
        def __init__(self):
            self.calls = []

        def get_classification(self, classification_id):
            self.calls.append(("classification", classification_id))
            return {
                "account_id": "account-1",
                "stable_message_identity": stable_identity,
                "thread_id": "provider-thread-1",
                "current_action_plan_id": "email-plan:17:v3",
                "action_plan": {
                    "action_plan_id": "email-plan:17:v3",
                    "action_plan_version": 3,
                    "model_id": "email-model:2026-08-30:sha256:abc",
                    "config_version": "email-config:v7",
                },
            }

        def list_email_context_thread(self, **kwargs):
            self.calls.append(
                ("thread", kwargs["account_id"], kwargs["stable_message_identity"])
            )
            return rows

        def list_email_context_receipts(self, **kwargs):
            self.calls.append(
                ("receipts", kwargs["account_id"], kwargs["stable_message_identity"])
            )
            return [
                {
                    "receipt_id": "sent-17",
                    "operation": "sent_readback",
                    "summary": "Prior automatic reply is present in Sent.",
                    "completed": True,
                },
                {
                    "receipt_id": "unsubscribe-16",
                    "operation": "unsubscribe_readback",
                    "summary": "Automatic unsubscribe outcome: already_unsubscribed.",
                    "completed": True,
                },
            ]

    store = PublicStore()
    task_input = EmailContextSource(store).load_task_input(
        SimpleNamespace(trigger_message_json=json.dumps(_payload(stable_identity)))
    )

    assert task_input.trigger.text == "Current message text"
    assert [message.text for message in task_input.thread_messages] == [
        "Earlier thread text"
    ]
    assert task_input.attachments[0].filename == "contract.pdf"
    assert task_input.prior_receipts[0].operation == "sent_readback"
    assert task_input.prior_receipts[1].operation == "unsubscribe_readback"
    assert task_input.list_unsubscribe == ""
    assert task_input.body_text == "Current message text"
    assert store.calls == [
        ("classification", 17),
        ("thread", "account-1", stable_identity),
        ("receipts", "account-1", stable_identity),
    ]


def test_context_source_rejects_action_plan_version_drift():
    stable_identity = "account-1:message-id:<current@example.com>"

    class DriftedStore:
        def get_classification(self, _classification_id):
            return {
                "account_id": "account-1",
                "stable_message_identity": stable_identity,
                "thread_id": "provider-thread-1",
                "current_action_plan_id": "email-plan:17:v4",
                "action_plan": {
                    "action_plan_id": "email-plan:17:v4",
                    "action_plan_version": 4,
                    "model_id": "email-model:current",
                    "config_version": "email-config:current",
                },
            }

    with pytest.raises(
        ValueError,
        match="email task action plan version does not match durable state",
    ):
        EmailContextSource(DriftedStore()).load_task_input(
            SimpleNamespace(trigger_message_json=json.dumps(_payload(stable_identity)))
        )
