"""Historical owner examples are supplemental to the current classification."""

import json
from types import SimpleNamespace

def _provider_row(account, identity):
    return {
        "account_id": account,
        "stable_message_identity": identity,
        "model_text": "",
        "message_source_reference_json": json.dumps(
            {
                "model_input": {
                    "kind": "provider",
                    "representation": "text",
                    "sha256": "0" * 64,
                    "bytes": 4,
                }
            }
        ),
    }


def test_unavailable_owner_example_source_does_not_abort_classification_context():
    from app.email_model_input_reader import read_owner_model_inputs

    rows = [
        _provider_row("account", "account:message-id:<available@example.test>"),
        _provider_row("account", "account:message-id:<missing@example.test>"),
    ]

    class SourceReader:
        def read_many(self, account, identities):
            assert account == "account"
            assert len(identities) == 2
            raise LookupError("email source UID is unavailable")

    result = read_owner_model_inputs(
        SimpleNamespace(), rows, source_reader=SourceReader()
    )

    assert result == {}


def test_owner_labelled_examples_omits_provider_rows_without_verified_model_input(
    monkeypatch, tmp_path
):
    from app.email_store import EmailStore

    rows = [
        {
            "classification_id": 1,
            "account_id": "account",
            "stable_message_identity": "account:message-id:<available@example.test>",
            "sender": "sender@example.test",
            "subject": "Available",
            "confirmed_category": "work",
            "updated_at": "2026-10-26 00:00:00",
        },
        {
            "classification_id": 2,
            "account_id": "account",
            "stable_message_identity": "account:message-id:<missing@example.test>",
            "sender": "sender@example.test",
            "subject": "Missing",
            "confirmed_category": "work",
            "updated_at": "2026-10-26 00:00:01",
        },
    ]
    stamp = "owner-label-stamp"
    store = SimpleNamespace(
        path=tmp_path / "email.sqlite3",
        _owner_labelled_rows=lambda: rows,
        _owner_labelled_rows_stamp=lambda _rows: stamp,
        owner_labelled_stamp=lambda: stamp,
    )

    def read_inputs(_store, source_rows, *, source_reader):
        assert source_rows == rows
        assert source_reader is not None
        return {
            ("account", "account:message-id:<available@example.test>"): "verified body"
        }

    monkeypatch.setattr("app.email_model_input_reader.read_owner_model_inputs", read_inputs)

    examples, returned_stamp = EmailStore.owner_labelled_examples(
        store, source_reader=object()
    )

    assert returned_stamp == stamp
    assert [item["identity"] for item in examples] == [
        "account:message-id:<available@example.test>"
    ]
    assert examples[0]["text"] == "verified body"
