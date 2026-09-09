import json
from types import SimpleNamespace

import pytest

from app.agent_context import email_attachment_metadata_materials
from app.email_context_source import EmailContextSource
from app.email_imap_readonly import ephemeral_body_html, parse_rfc822_message
from app.email_task_adapter import EmailAgentTaskAdapter
from app.email_unsubscribe import UnsubscribeAuthenticationEvidence


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
    attachment_materials = email_attachment_metadata_materials(
        task_input.attachments,
        source_message_id=task_input.trigger.message_id,
    )
    assert all(
        material.kind == "attachment_metadata" for material in attachment_materials
    )
    assert all(material.read_commands == () for material in attachment_materials)
    context = EmailAgentTaskAdapter.__new__(EmailAgentTaskAdapter)._build_context(
        task=SimpleNamespace(
            id=17,
            conversation_id="email:context-test",
            trigger_message_id="email-action:context-test",
        ),
        payload={"action_type": "label"},
        task_input=task_input,
    )
    assert context.image_paths == ()
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


def test_context_source_reloads_ephemeral_html_and_immutable_unsubscribe_bindings():
    stable_identity = "account-1:message-id:<current@example.com>"
    private_url = "https://news.example.com/unsubscribe?token=context-private"
    provider_message = parse_rfc822_message(
        (
            b"From: sender@example.com\r\n"
            b"To: derek@example.com\r\n"
            b"Subject: HTML only newsletter\r\n"
            b"Date: Sun, 30 Aug 2026 08:00:00 +0000\r\n"
            b"Message-ID: <current@example.com>\r\n"
            b"List-Unsubscribe: <https://news.example.com/one-click>\r\n"
            b"List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n\r\n"
            + f'<a href="{private_url}">Unsubscribe</a>'.encode()
        ),
        account_id="account-1",
        folder="INBOX",
        uidvalidity=42,
        uid=17,
    )
    payload = _payload(stable_identity)
    payload.update(
        {
            "unsubscribe_authentication": {
                "evidence_reference": "dkim-evidence:current-message",
                "one_click_verified": True,
            },
        }
    )

    class Store:
        def get_classification(self, _classification_id):
            return {
                "account_id": "account-1",
                "stable_message_identity": stable_identity,
                "thread_id": "provider-thread-1",
                "current_action_plan_id": "email-plan:17:v3",
                "folder": "INBOX",
                "uidvalidity": 42,
                "uid": 17,
                "action_plan": {
                    "action_plan_id": "email-plan:17:v3",
                    "action_plan_version": 3,
                    "model_id": "email-model:2026-08-30:sha256:abc",
                    "config_version": "email-config:v7",
                },
            }

        def list_email_context_thread(self, **_kwargs):
            return [
                {
                    "stable_message_identity": stable_identity,
                    "thread_identity": "provider-thread-1",
                    "sender": "sender@example.com",
                    "subject": "HTML only newsletter",
                    "normalized_text": "Newsletter",
                    "attachment_metadata": [],
                    "received_at": "2026-08-30T08:00:00+00:00",
                }
            ]

        def list_email_context_receipts(self, **_kwargs):
            return []

        def get_account(self, _account_id):
            return {"account_id": "account-1"}

    class Source:
        def fetch_uid_batch(self, *_args, **_kwargs):
            return SimpleNamespace(
                uidvalidity=42,
                messages=(provider_message,),
            )

        def logout(self):
            return None

    task_input = EmailContextSource(
        Store(),
        source_factory=lambda _account: Source(),
    ).load_task_input(
        SimpleNamespace(trigger_message_json=json.dumps(payload))
    )

    assert task_input.body_html == ephemeral_body_html(provider_message)
    assert private_url in task_input.body_html
    assert private_url not in repr(task_input)
    assert task_input.unsubscribe_authentication == UnsubscribeAuthenticationEvidence(
        dkim_covers_list_unsubscribe=True,
        dkim_covers_list_unsubscribe_post=True,
        evidence_reference="dkim-evidence:current-message",
    )
