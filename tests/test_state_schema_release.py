"""Synthetic source-bound State release and inverse before runtime selection."""
from contextlib import closing
import json
import sqlite3

import pytest

from app import state_schema_release as release
from app.action_reply_storage import _source_declaration
from app.state_definitions import STATE_FIELDS, _quote
from app.state_storage import delete_state, insert_state, update_state
from tests.configs_source_fixtures import capture


@pytest.fixture(params=("actual", "fresh"))
def native(request):
    metadata = capture()[request.param]["tables"]
    with closing(sqlite3.connect(":memory:")) as db:
        for name in release.SOURCE_TABLES:
            db.execute(metadata[name]["sql"])
            for index in reversed(metadata[name]["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
        for name, fields in STATE_FIELDS.items():
            values = {}
            for column in metadata[name]["columns"]:
                if column["name"] not in fields:
                    continue
                if column["pk"] or column["notnull"] and column["dflt_value"] is None:
                    values[column["name"]] = column["name"] if column["type"] == "TEXT" else 1
            columns = ("rowid", *values)
            db.execute("insert into " + _quote(name) + "(" + ",".join(map(_quote, columns)) +
                       ") values(" + ",".join("?" for _ in columns) + ")", (-7, *values.values()))
        db.commit()
        db.execute("begin")
        yield db, request.param


def image(db):
    result = {}
    for (name,) in db.execute("select name from sqlite_schema where type='table' and name not glob 'sqlite_*'"):
        fields = [row[1] for row in db.execute("pragma table_xinfo(" + _quote(name) + ")") if row[6] == 0]
        selected = ("rowid", *fields)
        projection = ",".join(map(_quote, selected)) + "," + ",".join(
            "typeof(" + _quote(field) + ")" for field in selected)
        rows = [tuple(row) for row in db.execute("select " + projection +
                                               " from " + _quote(name) + " order by rowid")]
        result[name] = (_source_declaration(db, name), rows)
    return result


def test_forward_exact_inverse_reforward_and_current_facts(native):
    db, variant = native
    db.execute("update service_state set value='not-a-saved-input-body' where rowid=-7")
    before = image(db)
    metadata = release.capture_state_source(db)
    assert set(metadata) == {"variant", "tables"}
    assert metadata["variant"] == variant
    assert "not-a-saved-input-body" not in json.dumps(metadata)
    assert not release.state_schema_is_current(db)
    counts = release.migrate_state_schema(db, metadata)
    assert set(image(db)) == {"state"}
    assert release.state_schema_is_current(db)
    assert counts == {name: 0 if name == "corpus_sources" else 1 for name in release.SOURCE_TABLES}
    assert db.in_transaction
    assert release.restore_state_schema(db, metadata) == counts
    assert image(db) == before
    assert db.in_transaction
    release.migrate_state_schema(db, metadata)
    assert update_state(db, "service_state", -7, {"value": "changed-current-value"}) == 1
    assert delete_state(db, "seen_messages", -7) == 1
    assert insert_state(db, "service_state", {
        "key": "new-current-key", "value": "not-a-saved-input-body", "updated_at": "t",
    }, source_rowid=0).lastrowid == 0
    assert insert_state(db, "conversations", {
        "conversation_id": "new", "title": "new", "single_chat": 1,
    }, source_rowid=0).lastrowid == 0
    release.restore_state_schema(db, metadata)
    assert db.execute("select value from service_state where rowid=-7").fetchone() == ("changed-current-value",)
    assert db.execute("select value from service_state where rowid=0").fetchone() == ("not-a-saved-input-body",)
    assert db.execute("select count(*) from seen_messages").fetchone() == (0,)
    if variant == "actual":
        assert db.execute("select in_handoff,handoff_message_id,handoff_message_create_time,"
                          "manual_clear_message_id from conversations where rowid=0").fetchone() == (0, None, None, None)
    current = image(db)
    release.migrate_state_schema(db, metadata)
    release.restore_state_schema(db, metadata)
    assert image(db) == current
    assert db.execute("pragma quick_check").fetchone() == ("ok",)
    assert db.execute("pragma foreign_key_check").fetchall() == []


def test_corpus_nonempty_refused_before_ddl(native):
    db, _ = native
    db.execute("insert into corpus_sources(source_key,last_collected_at) values('source','t')")
    before = image(db)
    with pytest.raises(ValueError, match="nonempty corpus"):
        release.capture_state_source(db)
    assert image(db) == before


@pytest.mark.parametrize("field,value", (
    ("in_handoff", 1), ("in_handoff", b"0"), ("handoff_message_id", ""),
    ("handoff_message_create_time", "t"), ("manual_clear_message_id", "id"),
))
def test_actual_unused_nondefault_fields_refused_before_ddl(field, value):
    tables = capture()["actual"]["tables"]
    with closing(sqlite3.connect(":memory:")) as db:
        for kind in release.SOURCE_TABLES:
            db.execute(tables[kind]["sql"])
            for index in reversed(tables[kind]["indexes"]):
                if index["sql"] is not None:
                    db.execute(index["sql"])
        db.execute("insert into conversations(conversation_id,title,single_chat) values('c','t',1)")
        db.execute("update conversations set " + _quote(field) + "=?", (value,))
        before = image(db)
        with pytest.raises(ValueError, match="nondefault unused"):
            release.capture_state_source(db)
        assert image(db) == before


def test_source_drift_metadata_drift_and_target_drift_refused(native):
    db, _ = native
    metadata = release.capture_state_source(db)
    wrong = json.loads(json.dumps(metadata))
    wrong["tables"]["service_state"]["sql"] += " "
    before = image(db)
    with pytest.raises(ValueError, match="metadata"):
        release.migrate_state_schema(db, wrong)
    assert image(db) == before
    db.execute("create index unsupported on service_state(value)")
    before = image(db)
    with pytest.raises(ValueError, match="source declarations"):
        release.capture_state_source(db)
    assert image(db) == before
    db.execute("drop index unsupported")
    release.migrate_state_schema(db, metadata)
    db.execute("create index unsupported on state(value)")
    assert not release.state_schema_is_current(db)
    before = image(db)
    with pytest.raises(ValueError, match="target declaration"):
        release.restore_state_schema(db, metadata)
    assert image(db) == before


@pytest.mark.parametrize("sql", (
    "create temp table SERVICE_STATE(key)",
    "create view quoted_ref as select value from 'service_state'",
    "create table inbound(id text references conversations(conversation_id))",
))
def test_external_reference_or_temp_shadow_refused(native, sql):
    db, _ = native
    db.execute(sql)
    before = image(db)
    with pytest.raises(ValueError):
        release.capture_state_source(db)
    assert image(db) == before


def test_forward_failure_rollback_restores_original(native, monkeypatch):
    db, _ = native
    before = image(db)
    metadata = release.capture_state_source(db)
    real = release.insert_state
    calls = []

    def fail_second(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("injected-copy-failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(release, "insert_state", fail_second)
    with pytest.raises(RuntimeError, match="injected-copy-failure"):
        release.migrate_state_schema(db, metadata)
    assert db.in_transaction
    db.rollback()
    assert image(db) == before


def test_inverse_failure_rollback_restores_current_target(native, monkeypatch):
    db, _ = native
    metadata = release.capture_state_source(db)
    release.migrate_state_schema(db, metadata)
    update_state(db, "service_state", -7, {"value": "current-fact"})
    db.commit()
    before = image(db)
    db.execute("begin")
    real = release._same
    calls = []

    def fail_second(*args):
        calls.append(True)
        if len(calls) == 2:
            raise RuntimeError("injected-inverse-failure")
        return real(*args)

    monkeypatch.setattr(release, "_same", fail_second)
    with pytest.raises(RuntimeError, match="injected-inverse-failure"):
        release.restore_state_schema(db, metadata)
    assert db.in_transaction
    db.rollback()
    assert image(db) == before


def test_requires_caller_fk_off_transaction(native):
    db, _ = native
    db.rollback()
    before = image(db)
    with pytest.raises(sqlite3.ProgrammingError, match="caller FK-off"):
        release.capture_state_source(db)
    assert image(db) == before
    db.execute("pragma foreign_keys=on")
    db.execute("begin")
    with pytest.raises(sqlite3.ProgrammingError, match="caller FK-off"):
        release.capture_state_source(db)
    assert image(db) == before


def test_source_scalar_storage_classes_survive_copy_and_inverse(native):
    db, _ = native
    db.execute("update dispatcher_claim_leases set owner_pid=?,last_error=? where rowid=-7",
               (-2.2101102838387424e-85, 7.294214896064974e-137))
    db.execute("update org_user_profiles set has_subordinate=?,org_labels_json=? where rowid=-7",
               (b"\\x00", b"[]"))
    db.execute("update service_state set value=? where rowid=-7", (b"runtime-bytes",))
    before = image(db)
    metadata = release.capture_state_source(db)
    release.migrate_state_schema(db, metadata)
    release.restore_state_schema(db, metadata)
    assert image(db) == before


def test_eligibility_is_rechecked_after_capture(native):
    db, _ = native
    metadata = release.capture_state_source(db)
    db.execute("insert into corpus_sources(source_key,last_collected_at) values('late','t')")
    before = image(db)
    with pytest.raises(ValueError, match="nonempty corpus"):
        release.migrate_state_schema(db, metadata)
    assert image(db) == before


def test_inverse_external_reference_refused_before_ddl(native):
    db, _ = native
    metadata = release.capture_state_source(db)
    release.migrate_state_schema(db, metadata)
    db.execute("create view current_ref as select value from state")
    before = image(db)
    with pytest.raises(ValueError):
        release.restore_state_schema(db, metadata)
    assert image(db) == before
