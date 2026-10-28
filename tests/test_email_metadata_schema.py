"""B1: one metadata owner, explicit Email52 source, and current-fact inverse."""

from contextlib import closing
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app.email_canonical_definitions import canonical_column
from app.email_canonical_storage import (
    _create_emails, _target_hash, insert_email_message, insert_email_classification,
    update_email_message, update_email_classification, update_email_paired_locator,
    delete_email_classification, delete_email_message, email_classification_projection,
    email_message_projection,
)
from app.email_store import EmailStore
from app.state_definitions import state_projection
from app.store import AutoReplyStore
from app.store import STORE_SCHEMA_VERSION
from app.store_schema_release import prepare_database_schema_release
from tests.support.email_native_fixtures import _message, _classification

ROOT = Path(__file__).resolve().parents[1]
RETIRED = {"class_" + name for name in ("sender", "subject", "received_at", "folder", "preview")}
OWNED = ("sender", "subject", "received_at", "folder", "preview")


def _markers(db):
    return (tuple(db.execute(f"select value,updated_at from ({state_projection('service_state')}) "
                            "where key='store_schema_version'").fetchone()),
            tuple(tuple(row) for row in db.execute(
                "select version,applied_at from email_schema_migrations order by version")))


def _facts(db, table):
    columns = [row[1] for row in db.execute(f"pragma table_xinfo({table})") if row[6] == 0]
    sql = ",".join('"' + name + '"' for name in columns)
    types = ",".join('typeof("' + name + '")' for name in columns)
    return tuple(tuple(row) for row in db.execute(f"select {sql},{types} from main.{table} order by rowid"))


def test_fresh_target_has_exactly_one_owner_and_retains_independent_ids(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        columns = {row[1] for row in db.execute("pragma table_xinfo(emails)")}
        assert not columns & RETIRED
        assert len(columns) == 42
        assert _markers(db)[0][0] == STORE_SCHEMA_VERSION
        assert _markers(db)[1][-1][0] == 54
        assert {"classification_id", "message_id", "id", "class_rfc_message_id", "message_rfc_message_id",
                "class_model_text", "message_normalized_text"} <= columns
        assert "message_folder" in db.execute("select sql from sqlite_schema where name='idx_emails_class_account_locator'").fetchone()[0]


def test_frozen_email52_default_and_explicit_target_declarations():
    with closing(sqlite3.connect(":memory:")) as db:
        _create_emails(db)
        assert RETIRED <= {row[1] for row in db.execute("pragma table_xinfo(emails)")}
        assert _target_hash() == "ea47fb6f57d0a32fce49daa5982b44e1ef978d2ec0f2a167c81e77c21d425f15"
        assert _target_hash() != _target_hash(metadata_owner=True)
        assert canonical_column("email_classifications", "sender") == "class_sender"
        assert canonical_column("email_classifications", "sender", metadata_owner=True) == "message_sender"


def test_runtime_class_and_message_writes_read_the_same_metadata_owner(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store = EmailStore(path)
    with store._connect() as db:
        message_id = insert_email_message(db, _message(id=41, folder="INBOX"))
        assert insert_email_classification(db, _classification(id=7)) == 7
        first = dict(db.execute("select * from (" + email_classification_projection() + ")").fetchone())
        for field in OWNED:
            assert first[field] == _classification()[field]
        assert update_email_classification(db, 7, {field: "class " + field for field in OWNED}) == 1
        message = dict(db.execute("select * from (" + email_message_projection() + ")").fetchone())
        assert all(message[field] == "class " + field for field in OWNED)
        assert update_email_message(db, message_id, {field: "message " + field for field in OWNED}) == 1
        classified = dict(db.execute("select * from (" + email_classification_projection() + ")").fetchone())
        assert all(classified[field] == "message " + field for field in OWNED)
        assert classified["id"] == 7 and message_id == 41
        assert classified["rfc_message_id"] is None
        assert db.execute("select message_rfc_message_id from emails").fetchone()[0] == "<message>"
        with pytest.raises(ValueError, match="remove classification"):
            delete_email_message(db, message_id)
        with pytest.raises(ValueError, match="no canonical message"):
            insert_email_classification(db, _classification("missing", id=8))
        assert delete_email_classification(db, 7) == 1
        row = db.execute("select classification_id,message_sender,message_preview from emails").fetchone()
        assert tuple(row) == (None, "message sender", "message preview")
        assert delete_email_message(db, message_id) == 1


def test_paired_locator_cas_writes_one_folder_and_keeps_two_clocks(tmp_path):
    store = EmailStore(tmp_path / "locator.sqlite3")
    with store._connect() as db:
        insert_email_message(db, _message(folder="INBOX"))
        insert_email_classification(db, _classification())
        options = dict(classification_id=101, account_id="account", stable_message_identity="stable-1",
                       class_folder="Archive", message_folder="Archive", uidvalidity=11, uid=21,
                       expected_class_folder="INBOX", expected_message_folder="INBOX",
                       expected_uidvalidity=10, expected_uid=20,
                       class_updated_at="class-clock", message_updated_at="message-clock")
        assert update_email_paired_locator(db, **options) == 1
        assert update_email_paired_locator(db, **options) == 0
        assert tuple(db.execute("select message_folder,uidvalidity,uid,class_updated_at,message_updated_at from emails").fetchone()) == (
            "Archive", 11, 21, "class-clock", "message-clock")
        before = _facts(db, "emails")
        with pytest.raises(ValueError, match="folder"):
            update_email_paired_locator(db, **(options | {"class_folder": "different"}))
        assert _facts(db, "emails") == before


def test_preview_unicode_bound_and_default_search_never_reads_sources(tmp_path):
    from app.email_classification_reader import EmailClassificationReader
    from app.email_preview import populate_email_previews
    from tests.test_email_store import _classification as scan_classification
    from app.email_classifier_contracts import EmailClassificationStatus

    store = EmailStore(tmp_path / "preview.sqlite3")
    original = "中" * 999 + "😀" + "deep-keyword"
    store.persist_scan_result(scan_classification(status=EmailClassificationStatus.PENDING_FEEDBACK), normalized_text=original, model_text="exact model " + original,
                              sender="Owner", subject="Subject")
    calls = []
    def read_views(account, identities):
        calls.extend(identities)
        return {identity: {"message_text": original} for identity in identities}
    reader = EmailClassificationReader(store, message_reader=SimpleNamespace(read_views=read_views))
    rows, total = reader.list_classifications(status=None, limit=10, offset=0)
    assert total == 1 and rows[0]["preview"] == original[:1000]
    assert calls == [] and "message_text" not in rows[0]
    assert reader.list_classifications(status=None, q="deep-keyword", limit=10, offset=0) == ([], 0)
    assert calls == []
    rows, total = reader.list_classifications(status=None, q="deep-keyword", search_scope="full", limit=10, offset=0)
    assert total == 1 and rows[0]["message_text"] == original
    assert reader.get_classification(rows[0]["id"])["message_text"] == original
    with store._connect() as db:
        assert populate_email_previews(db) == 0
        assert db.execute("select message_preview from emails").fetchone()[0] == original[:1000]
        before = _facts(db, "emails")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            update_email_message(db, 1, {"preview": original})
        assert _facts(db, "emails") == before


@pytest.mark.parametrize("owner", ["store", "email"])
def test_both_constructor_orders_and_mixed_pairs(tmp_path, owner):
    path = tmp_path / "fresh-order.sqlite3"
    first, second = (AutoReplyStore, EmailStore) if owner == "store" else (EmailStore, AutoReplyStore)
    first(path)
    second(path)
    assert prepare_database_schema_release(ROOT, path) is None
    with closing(sqlite3.connect(path)) as db:
        db.execute("update email_schema_migrations set version=53 where version=54")
        db.commit()
    before = path.read_bytes()
    for constructor in (AutoReplyStore, EmailStore):
        with pytest.raises(sqlite3.IntegrityError, match="unsupported Email version"):
            constructor(path)
    assert path.read_bytes() == before


def test_absent_classification_metadata_does_not_erase_message_owner(tmp_path):
    store = EmailStore(tmp_path / "absent-class-fields.sqlite3")
    with store._connect() as db:
        insert_email_message(db, _message())
        values = {key: value for key, value in _classification().items() if key not in OWNED}
        insert_email_classification(db, values)
        row = dict(db.execute("select * from (" + email_classification_projection() + ")").fetchone())
        assert all(row[field] == _message()[field] for field in OWNED)


@pytest.mark.parametrize("preview", ["中" * 999 + "😀", "a\x00" + "中" * 998], ids=["unicode", "nul"])
def test_preview_bound_counts_unicode_and_embedded_nul(tmp_path, preview):
    store = EmailStore(tmp_path / "preview-bound.sqlite3")
    with store._connect() as db:
        insert_email_message(db, _message(preview=preview))
        assert db.execute("select message_preview from emails").fetchone()[0] == preview
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            update_email_message(db, 1, {"preview": preview + "x"})


