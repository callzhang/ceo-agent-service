"""Explicit source substitute for existing Console business-route fixtures.

Fixtures seed originals in SQLite. Only this test substitute reads those inputs;
default-wiring tests use fake IMAP and forbid all stored-body readers instead.
"""

import json

import pytest

from app.email_canonical_definitions import email_projection


def address(value):
    text = str(value or "").strip()
    local, separator, domain = text.rpartition("@")
    return (
        {"name": "", "email": text}
        if separator and local and domain
        else {"name": text, "email": ""}
    )


class StoredEmailSource:
    def __init__(self, store):
        self.store = store

    def read_views(self, account, identities):
        from email.parser import Parser
        from app.email_store import _display_message_body

        views = {}
        with self.store._connect() as db:
            for identity in identities:
                row = db.execute(
                    f"select normalized_text,recipients_json from ({email_projection('email_messages')}) "
                    "where account_id=? and stable_message_identity=?", (account, identity),
                ).fetchone()
                parsed = Parser().parsestr(row["normalized_text"] or "")
                views[identity] = {
                    "message_text": _display_message_body(parsed),
                    "cc": parsed.get("Cc", ""),
                    "recipients": json.loads(row["recipients_json"]),
                }
        return views

    def read_many(self, account, identities):
        messages = {}
        with self.store._connect() as db:
            for identity in identities:
                row = db.execute(
                    f"select * from ({email_projection('email_messages')}) where account_id=? and stable_message_identity=?",
                    (account, identity),
                ).fetchone()
                messages[identity] = {
                    "from": address(row["sender"]),
                    "toRecipients": [
                        address(item) for item in json.loads(row["recipients_json"])
                    ],
                    "ccRecipients": [],
                    "subject": row["subject"],
                    "textBody": row["normalized_text"],
                    "messageId": row["rfc_message_id"],
                    "inReplyTo": row["in_reply_to"],
                    "references": json.loads(row["references_json"]),
                    "attachments": json.loads(row["attachment_metadata_json"]),
                }
        return messages


@pytest.fixture(autouse=True)
def stored_email_source(monkeypatch):
    monkeypatch.setattr(
        "app.email_source_reader.build_email_source_reader",
        lambda store, **kwargs: StoredEmailSource(store),
    )
