"""Selected canonical Email runtime integration checks."""

import sqlite3
import subprocess
import sys

import pytest

from app.email_store import EmailStore
from app.email_canonical_storage import insert_email_message, insert_email_classification
from app.email_training_labeler import _all_classified_identities
from app.email_classifier_contracts import EmailClassificationStatus
from app.store import AutoReplyStore
from tests.support.current_inventory import CURRENT_TABLES
from tests.test_email_store import _classification, _persist_scan


def _assert_selected_graph(db):
    objects = {(row[1], row[2]) for row in db.execute("pragma table_list")
               if row[0] == "main" and not row[1].startswith("sqlite_")}
    assert objects == {(name, "table") for name in CURRENT_TABLES}


def test_fresh_public_constructors_select_eleven_table_email_graph(tmp_path):
    path = tmp_path / "current11.sqlite"
    AutoReplyStore(path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        _assert_selected_graph(db)


@pytest.mark.parametrize("order", ("store_first", "email_first"))
def test_cold_constructor_orders_keep_selected_graph(tmp_path, order):
    path = tmp_path / "cold.sqlite"
    script = (
        "from pathlib import Path; import sys; "
        "from app.store import AutoReplyStore; from app.email_store import EmailStore; "
        "p=Path(sys.argv[1]); "
        + ("AutoReplyStore(p); EmailStore(p); " if order == "store_first"
           else "EmailStore(p); AutoReplyStore(p); ")
        + "AutoReplyStore(p); EmailStore(p)"
    )
    subprocess.run([sys.executable, "-c", script, str(path)], check=True)
    with sqlite3.connect(path) as db:
        _assert_selected_graph(db)


def test_cold_email_constructor_refuses_mixed_native_parent_before_mutation(tmp_path):
    path = tmp_path / "mixed.sqlite"
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute("create table email_messages(unrelated integer)")
        before = tuple(db.iterdump())
        schema_version = db.execute("pragma schema_version").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed Store source schema"):
        EmailStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before
        assert db.execute("pragma schema_version").fetchone()[0] == schema_version


def test_training_identity_reader_uses_selected_classification_alias(tmp_path):
    store = EmailStore(tmp_path / "training.sqlite")
    with store._connect() as db:
        insert_email_message(db, dict(
            account_id="account", stable_message_identity="message-only", folder="INBOX",
            uidvalidity=1, uid=1, rfc_message_id="", in_reply_to="",
            references_json="[]", thread_identity="", sender="", recipients_json="[]",
            subject="", normalized_text="", preview="",
            attachment_metadata_json="[]", received_at="", created_at="", updated_at="",
        ))
        insert_email_message(db, dict(
            account_id="account", stable_message_identity="classified", folder="INBOX",
            uidvalidity=1, uid=2, rfc_message_id="", in_reply_to="",
            references_json="[]", thread_identity="", sender="", recipients_json="[]",
            subject="", normalized_text="", preview="",
            attachment_metadata_json="[]", received_at="", created_at="", updated_at="",
        ))
        insert_email_classification(db, dict(
            account_id="account", stable_message_identity="classified", folder="INBOX",
            uidvalidity=1, uid=2, confidence=0.5, margin=0.1,
            probabilities_json="{}", model_id="model", config_version="config",
            status="pending_feedback", classification_source="model",
        ))
    assert _all_classified_identities(store) == frozenset({"classified"})


def test_selected_direct_claim_uses_action_and_email_indexes(tmp_path):
    store = EmailStore(tmp_path / "claim.sqlite")
    _persist_scan(store, _classification(status=EmailClassificationStatus.PROCESSED))
    statements = []
    with store._connect() as db:
        db.set_trace_callback(statements.append)
        row = store._next_direct_action_row(db, "2026-08-30T12:00:00+00:00", None)
        db.set_trace_callback(None)
        assert row is not None
        steps = [item[3] for statement in statements
                 if statement.lstrip().lower().startswith("select")
                 for item in db.execute("explain query plan " + statement)]
    assert any("SEARCH a USING INDEX idx_actions_email_" in step for step in steps), steps
    assert any("SEARCH main.emails USING INDEX sqlite_autoindex_emails_1 "
               "(classification_id=?)" in step for step in steps), steps
    assert not any(step.startswith("SCAN main.emails") for step in steps), steps
