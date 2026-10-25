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
from app.state_storage import update_state_rows
from app.store import AutoReplyStore
from app.store_schema_release import prepare_database_schema_release
from app.action_task_delivery_storage import _source_declaration
from tests.test_email_canonical_storage import _message, _classification

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


def _source(tmp_path, *, variant="fresh_memory_fk"):
    """Independent fixture: install the frozen Email52 factory, never B1 inverse."""
    path = tmp_path / "source.sqlite3"
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        db.execute("drop table emails")
        _create_emails(db)
        if variant != "fresh_memory_fk":
            from app.email_canonical_storage import _create_actions
            from app.model_result_schema import result_action_objects
            db.execute("drop table actions")
            _create_actions(db, result_action_objects(variant))
        update_state_rows(db, "service_state", {"value": "2026-10-19.2", "updated_at": b"source-clock"},
                          where_sql="key='store_schema_version'")
        db.execute("delete from email_schema_migrations")
        db.executemany("insert into email_schema_migrations values(?,?)", [(51, b"clock51"), (52, b"clock52")])
        message = _message(folder="INBOX", sender="sender", subject="subject", received_at="date", preview="", id=41)
        classification = _classification(folder="INBOX", sender="sender", subject="subject", received_at="date",
                                         preview="中" * 999 + "😀", id=7, rfc_message_id=None)
        values = {canonical_column("email_messages", k): v for k, v in message.items()}
        values.update({canonical_column("email_classifications", k): v for k, v in classification.items()})
        values["id"] = 301
        db.execute("insert into emails(" + ",".join(values) + ") values(" + ",".join("?" for _ in values) + ")",
                   tuple(values.values()))
        db.execute("insert into sqlite_sequence(name,seq) values('email_messages',800)")
        db.execute("insert into sqlite_sequence(name,seq) values('unrelated-water',12345)")
        db.commit()
    return path


def test_fresh_target_has_exactly_one_owner_and_retains_independent_ids(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    EmailStore(path)
    with closing(sqlite3.connect(path)) as db:
        columns = {row[1] for row in db.execute("pragma table_xinfo(emails)")}
        assert not columns & RETIRED
        assert len(columns) == 42
        assert _markers(db)[0][0] == "2026-10-19.3"
        assert _markers(db)[1][-1][0] == 53
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


@pytest.mark.parametrize("variant", ["actual_no_memory_fk", "fresh_memory_fk"])
def test_registered_release_inverse_uses_new_current_facts_and_exact_markers(tmp_path, variant):
    path = _source(tmp_path, variant=variant)
    with closing(sqlite3.connect(path)) as db:
        from app.run_storage import insert_run
        insert_run(db, "agent_runs", dict(id=190042, task_id=999999,
                   execution_generation="dangling", role="consumer"))
        db.commit()
        marker = _markers(db)
        declaration = _source_declaration(db, "emails")
        unrelated = {name: _facts(db, name) for name in ("actions", "runs", "tasks")}
        fk = tuple(db.execute("pragma foreign_key_check"))
        assert len(fk) == 1 and fk[0][:3] == ("runs", 190042, "tasks")
    release = prepare_database_schema_release(ROOT, path)
    assert type(release).__name__ == "EmailMetadataSchemaRelease"
    release.publish()
    release.verify_loaded()
    store = EmailStore(path, validate_rows=False)
    with store._connect(foreign_keys=False) as db:
        assert tuple(db.execute("select id,classification_id,message_id,message_preview from emails").fetchone()) == (301, 7, 41, "中" * 999 + "😀")
        new_message_id = insert_email_message(db, _message("new", folder="INBOX", sender=b"BLOB", subject="one"))
        assert new_message_id == 801
        insert_email_classification(db, _classification("new", id=-8, sender=b"BLOB", subject="one", preview="new preview"))
        assert insert_email_message(db, _message("only", preview="only preview")) == 802
        update_email_message(db, new_message_id, {"subject": "current subject", "received_at": b"current date"})
        update_email_paired_locator(db, classification_id=-8, account_id="account", stable_message_identity="new",
                                   class_folder="Archive", message_folder="Archive", uidvalidity=12, uid=25)
        rows = [dict(row) for row in db.execute("select * from emails order by id")]
        water = _facts(db, "sqlite_sequence")
    release.finalize()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert _markers(db) == marker
        assert _source_declaration(db, "emails") == declaration
        assert _facts(db, "sqlite_sequence") == water
        assert tuple(db.execute("pragma foreign_key_check")) == fk
        assert {name: _facts(db, name) for name in unrelated} == unrelated
        db.row_factory = sqlite3.Row
        restored = [dict(row) for row in db.execute("select * from emails order by id")]
        for old, new in zip(rows, restored, strict=True):
            expected = dict(old)
            for field in OWNED:
                expected["class_" + field] = old["message_" + field] if old["classification_id"] is not None else None
            if old["classification_id"] is not None:
                expected["message_preview"] = ""
            assert new == expected
        assert db.execute("select typeof(class_sender),typeof(class_received_at) from emails where classification_id=-8").fetchone()[:] == ("blob", "blob")
    release.rollback()
    release.publish()
    release.finalize()


@pytest.mark.parametrize("ddl", [
    "create index unknown_email_index on emails(message_subject)",
    "create view email_dependency as select message_subject from emails",
    "create table email_dependency(parent integer references emails(classification_id))",
    "create temp table emails(x)",
    "create temp table _email_metadata_rows(x)",
    "create temp view email_dependency as select message_subject from main.emails",
])
def test_unknown_declarations_dependencies_and_temp_shadows_refuse_before_writes(tmp_path, ddl):
    from app.email_metadata_schema import migrate_email_metadata
    path = _source(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        db.execute(ddl)
        db.commit()
        before = (tuple(db.iterdump()), db.execute("pragma schema_version").fetchone(), _facts(db, "emails"))
        db.execute("begin immediate")
        with pytest.raises(ValueError, match="unsupported|dependency|dependent|TEMP|external"):
            migrate_email_metadata(db)
        assert (tuple(db.iterdump()), db.execute("pragma schema_version").fetchone(), _facts(db, "emails")) == before
        assert db.execute("select name from temp.sqlite_schema where name='_email_metadata_water'").fetchone() is None


@pytest.mark.parametrize("owner", ["store", "email"])
def test_both_constructor_orders_and_mixed_pairs(tmp_path, owner):
    path = tmp_path / "fresh-order.sqlite3"
    first, second = (AutoReplyStore, EmailStore) if owner == "store" else (EmailStore, AutoReplyStore)
    first(path)
    second(path)
    assert prepare_database_schema_release(ROOT, path) is None
    with closing(sqlite3.connect(path)) as db:
        db.execute("update email_schema_migrations set version=52 where version=53")
        db.commit()
    before = path.read_bytes()
    for constructor in (AutoReplyStore, EmailStore):
        with pytest.raises(sqlite3.IntegrityError, match="unsupported or mixed"):
            constructor(path)
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or mixed"):
        prepare_database_schema_release(ROOT, path)
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


@pytest.mark.parametrize("column,value", [
    ("class_sender", "different"), ("class_subject", b"subject"),
    ("class_received_at", "different"), ("class_folder", " INBOX "),
    ("message_preview", "second owner"), ("class_preview", "中" * 1001),
    ("class_preview", b"typed preview"), ("class_preview", "a\x00" + "中" * 999),
])
def test_source_mismatch_refuses_before_any_write(tmp_path, column, value):
    path = _source(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        db.execute(f"update emails set {column}=?", (value,))
        db.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="metadata|preview"):
        release = prepare_database_schema_release(ROOT, path)
        release.publish()
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", OWNED)
def test_message_only_source_with_latent_class_metadata_refuses_without_writes(tmp_path, field):
    path = _source(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("update emails set classification_id=null," + ",".join(
            "class_" + name + "=null" for name in OWNED))
        db.execute("update emails set class_" + field + "=?", (b"latent metadata",))
        db.commit()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="without a classification owner"):
        prepare_database_schema_release(ROOT, path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("constructor", [AutoReplyStore, EmailStore])
def test_predecessor_cold_constructor_refuses_without_writes(tmp_path, constructor):
    path = _source(tmp_path)
    before = path.read_bytes()
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or mixed"):
        constructor(path)
    assert path.read_bytes() == before


def test_cold_failure_can_restore_exact_predecessor(tmp_path, monkeypatch):
    from app import store_schema_release
    path = _source(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        before = (_markers(db), _source_declaration(db, "emails"), _facts(db, "emails"))
    release = prepare_database_schema_release(ROOT, path)
    def fail(*_):
        raise RuntimeError("cold failure")
    monkeypatch.setattr(store_schema_release, "_migrate_store", fail)
    with pytest.raises(RuntimeError, match="cold failure"):
        release.publish()
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert (_markers(db), _source_declaration(db, "emails"), _facts(db, "emails")) == before


def test_metadata_inverse_keeps_retired_inputs_without_source_reads(tmp_path, monkeypatch):
    from app import email_body_source, native_trajectory

    path = _source(tmp_path)
    reference = '{"kind":"provider","sha256":"body-proof","model_input":{"sha256":"model-proof"}}'
    with closing(sqlite3.connect(path)) as db:
        db.execute("update emails set message_normalized_text='',class_model_text='',message_source_reference_json=?", (reference,))
        db.commit()
        before = _facts(db, "emails")
    def forbidden(*args, **kwargs):
        pytest.fail("metadata publication must not read original sources")
    monkeypatch.setattr(email_body_source, "locate_original", forbidden)
    monkeypatch.setattr(email_body_source, "verify_original", forbidden)
    monkeypatch.setattr(native_trajectory, "read_native_source_body", forbidden)
    release = prepare_database_schema_release(ROOT, path)
    release.publish()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select message_normalized_text,class_model_text,message_source_reference_json from emails").fetchone() == ("", "", reference)
    release.rollback()
    with closing(sqlite3.connect(path)) as db:
        assert _facts(db, "emails") == before


def test_current_inverse_refuses_new_dependencies_without_partial_reconstruction(tmp_path):
    from app.email_metadata_schema import migrate_email_metadata, restore_email_metadata

    path = _source(tmp_path)
    with closing(sqlite3.connect(path)) as db:
        db.execute("begin immediate")
        metadata = migrate_email_metadata(db)
        db.execute("create view new_dependency as select message_sender from emails")
        before = (tuple(db.iterdump()), _facts(db, "emails"))
        with pytest.raises(ValueError, match="dependency|dependent|external"):
            restore_email_metadata(db, metadata)
        assert (tuple(db.iterdump()), _facts(db, "emails")) == before
