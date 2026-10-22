import json
from types import SimpleNamespace

import pytest

from app.agent_context import email_attachment_metadata_materials
from app.email_context_source import EmailContextSource
from app.email_source_reader import EmailSourceReader
from app.email_imap_readonly import ephemeral_body_html, parse_rfc822_message
from app.email_task_adapter import EmailAgentTaskAdapter
from app.email_unsubscribe import UnsubscribeAuthenticationEvidence


def _source_location(classification):
    return {key: classification.get(key) for key in (
        "account_id", "stable_message_identity", "folder", "uidvalidity", "uid",
        "rfc_message_id", "updated_at", "state", "provider_folder_name", "observed_at",
    )} | {"thread_identity": classification.get("thread_id")}


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


def test_context_source_projects_complete_text_metadata_versions_and_receipts(monkeypatch):
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

    source_calls = []

    def read_many(reader, account_id, identities):
        source_calls.append((account_id, identities))
        return {
            row["stable_message_identity"]: {
                "from": {"email": row["sender"]},
                "subject": row["subject"],
                "textBody": row["normalized_text"].replace("thread text", "source text").replace("message text", "source text"),
                "attachments": row["attachment_metadata"],
            }
            for row in rows
        }

    monkeypatch.setattr("app.email_source_reader.EmailSourceReader.read_many", read_many)
    store = PublicStore()
    task_input = EmailContextSource(store, source_factory=lambda account: None).load_task_input(
        SimpleNamespace(trigger_message_json=json.dumps(_payload(stable_identity)))
    )

    assert task_input.trigger.text == "Current source text"
    assert [message.text for message in task_input.thread_messages] == [
        "Earlier source text"
    ]
    assert source_calls == [("account-1", [row["stable_message_identity"] for row in rows])]
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
    assert task_input.body_text == "Current source text"
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
        def get_email_source_locations(self, keys):
            return {key: self.get_email_source_location(*key) for key in keys}

        def get_email_source_location(self, account_id, identity):
            return _source_location(self.get_classification(17))

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
        def fetch_message(self, locator, *, stable_message_identity):
            assert locator.uid == 17 and locator.uidvalidity == 42
            assert stable_message_identity == provider_message["stableMessageIdentity"]
            return provider_message

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


def test_context_source_rejects_unavailable_source_even_with_cached_body():
    stable_identity = "account-1:message-id:<current@example.com>"

    class Store:
        def get_email_source_locations(self, keys):
            return {key: self.get_email_source_location(*key) for key in keys}

        def get_email_source_location(self, account_id, identity):
            return _source_location(self.get_classification(17))

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
                    "subject": "Persisted subject",
                    "normalized_text": "Persisted body",
                    "attachment_metadata": [],
                    "received_at": "2026-08-30T08:00:00+00:00",
                }
            ]

        def list_email_context_receipts(self, **_kwargs):
            return []

        def get_account(self, _account_id):
            return {"account_id": "account-1"}

    def unavailable_source_factory(_account):
        raise OSError("provider DNS unavailable")

    with pytest.raises(OSError, match="provider DNS unavailable"):
        EmailContextSource(
            Store(), source_factory=unavailable_source_factory,
        ).load_task_input(
            SimpleNamespace(trigger_message_json=json.dumps(_payload(stable_identity)))
        )


def test_context_source_rejects_missing_original_even_with_cached_body():
    stable_identity = "account-1:message-id:<current@example.com>"

    class Store:
        def get_email_source_locations(self, keys):
            return {key: self.get_email_source_location(*key) for key in keys}

        def get_email_source_location(self, account_id, identity):
            return _source_location(self.get_classification(17))

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
                    "subject": "Moved message",
                    "normalized_text": "Persisted moved body",
                    "attachment_metadata": [],
                    "received_at": "2026-08-30T08:00:00+00:00",
                }
            ]

        def list_email_context_receipts(self, **_kwargs):
            return []

        def get_account(self, _account_id):
            return {"account_id": "account-1"}

    class Source:
        def fetch_message(self, locator, *, stable_message_identity):
            raise LookupError("email source message moved")

        def logout(self):
            return None

    with pytest.raises(LookupError, match="email source message moved"):
        EmailContextSource(
            Store(), source_factory=lambda _account: Source(),
        ).load_task_input(
            SimpleNamespace(trigger_message_json=json.dumps(_payload(stable_identity)))
        )


def test_current_provider_reader_uses_exact_full_message_without_neighbor_search():
    from app.email_classifier_contracts import EmailProviderLocator
    stable_identity = 'account-1:message-id:<current@example.com>'
    body = '完整正文' * 30000
    calls = []

    class Store:
        def get_email_source_locations(self, keys):
            return {key: self.get_email_source_location(*key) for key in keys}

        def get_email_source_location(self, account_id, identity):
            return _source_location({'account_id':account_id, 'stable_message_identity':identity,
                'folder':'INBOX', 'uidvalidity':42, 'uid':17, 'rfc_message_id':'<current@example.com>'})

        def get_account(self, account_id):
            assert account_id == 'account-1'
            return {'account_id': account_id}

    class Source:
        def fetch_message(self, locator, *, stable_message_identity):
            calls.append((locator, stable_message_identity))
            return {'stableMessageIdentity': stable_message_identity, 'textBody': body}

        def logout(self):
            calls.append('logout')

    message = EmailSourceReader(Store(), source_factory=lambda account: Source()).read(
        'account-1', stable_identity,
    )
    assert message['textBody'] == body
    assert calls == [(EmailProviderLocator(account_id='account-1', folder='INBOX', uidvalidity=42,
        uid=17, rfc_message_id='<current@example.com>'), stable_identity), 'logout']


@pytest.mark.parametrize("missing", [None, "prior", "current"])
def test_thread_source_batch_needs_no_stored_body_and_never_returns_partial_context(missing):
    current = "account-1:message-id:<current@example.com>"
    prior = "account-1:message-id:<prior@example.com>"
    identities = [prior, current]
    calls = []
    rows = [
        {"stable_message_identity": identity, "thread_identity": "provider-thread-1",
         "sender": "metadata@example.com", "subject": "Metadata subject",
         "received_at": "2026-08-30T08:00:00+00:00"}
        for identity in identities
    ]
    locations = {
        identity: _source_location({
            "account_id": "account-1", "stable_message_identity": identity,
            "folder": "INBOX", "uidvalidity": 42, "uid": index + 1,
            "rfc_message_id": f"<{name}@example.com>",
        })
        for index, (name, identity) in enumerate(zip(["prior", "current"], identities))
    }

    class Store:
        def get_classification(self, classification_id):
            return {"account_id": "account-1", "stable_message_identity": current,
                    "thread_id": "provider-thread-1",
                    "current_action_plan_id": "email-plan:17:v3",
                    "action_plan": {name: _payload(current)[name] for name in
                        ("action_plan_id", "action_plan_version", "model_id", "config_version")}}

        def list_email_context_thread(self, **kwargs):
            return rows

        def get_email_source_locations(self, keys):
            return {key: locations[key[1]] for key in keys}

        def get_account(self, account_id):
            return {"account_id": account_id}

        def list_email_context_receipts(self, **kwargs):
            calls.append("receipts")
            return []

    class Source:
        def fetch_message(self, locator, *, stable_message_identity):
            calls.append(stable_message_identity)
            if missing and stable_message_identity == identities[["prior", "current"].index(missing)]:
                raise LookupError("original unavailable")
            return {"from": {"email": "source@example.com"}, "subject": "Source subject",
                    "textBody": stable_message_identity + "完整正文" * 30000}

        def logout(self):
            calls.append("logout")

    def factory(account):
        calls.append("lease")
        return Source()

    source = EmailContextSource(Store(), source_factory=factory)
    task = SimpleNamespace(trigger_message_json=json.dumps(_payload(current)))
    if missing:
        with pytest.raises(LookupError, match="original unavailable"):
            source.load_task_input(task)
        assert "receipts" not in calls
        assert calls[-1] == "logout"
    else:
        result = source.load_task_input(task)
        assert result.trigger.text == current + "完整正文" * 30000
        assert [message.text for message in result.thread_messages] == [prior + "完整正文" * 30000]
        assert result.trigger.sender == "source@example.com"
        assert result.subject == "Source subject"
        assert calls == ["lease", prior, current, "logout", "receipts"]
