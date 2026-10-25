"""Selected Email history storage and reversible source facts."""

import json
import hashlib
import sqlite3

import pytest

import app.store as store_module
from app.action_email_history_definitions import HISTORY_FIELDS, HISTORY_KINDS, history_projection
from app.action_email_history_storage import (
    capture_email_history_source, migrate_email_history_schema,
    restore_email_history_schema, insert_email_history, _projected_snapshot,
)
from app.action_task_delivery_storage import _hash
from app.business_schema_helpers import _snapshot
from app.business_storage import insert_business
from app.store import AutoReplyStore
from app.email_store import EmailStore


def _insert_text_email_history(db, kind, values, *, ignore_public_duplicate=False):
    """Write only at the frozen historical TEXT Actions migration boundary."""
    from app import action_storage as actions

    if kind not in HISTORY_KINDS or set(values) - set(HISTORY_FIELDS[kind]):
        raise ValueError("unsupported Email history insert fields")
    fields = HISTORY_FIELDS[kind]
    data = {field: values.get(field, "" if kind == HISTORY_KINDS[1]
                              and field == "action_outcome" else None) for field in fields}
    actions._email_lock(db, tuple(data.values()))
    rowid = actions._email_rowid_next(db, kind)
    sql = ("insert or ignore " if ignore_public_duplicate else "insert ")
    sql += "into actions(record_type,legacy_key,payload_json) values(?,?,?)"
    return db.execute(sql, (kind, str(rowid), actions._native_payload(data))).rowcount


def test_default_current15_action_oracles_remain_exact():
    from pathlib import Path
    from app.action_storage import _current_objects
    capsule = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs"
                          / "2026-10-17-email-history-current15-source.json").read_text())
    for variant, expected in capsule["pre_edit_action_oracles"].items():
        actual = _current_objects(reply_variant=variant,
                                  **capsule["pre_edit_action_options"])
        assert hashlib.sha256(json.dumps(actual, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest() == expected


def test_fresh_current_email_history_is_in_actions(tmp_path):
    path = tmp_path / "current.sqlite"
    AutoReplyStore(path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        tables = {name for (name,) in db.execute(
            "select name from main.sqlite_schema where type='table' and name not like 'sqlite_%'"
        )}
        assert len(tables) == 11
        assert "email_historical_classification_history" not in tables
        assert "email_historical_operations" not in tables
        columns = {row[1] for row in db.execute("pragma main.table_xinfo(actions)")}
        assert "email_history_event_id" in columns
        assert "email_operation_operation_id" in columns


def _source15_store(path, monkeypatch):
    """Build the frozen TEXT current15 boundary without invoking current stores."""
    from pathlib import Path
    from app.action_storage import create_action_schema
    from app.action_business_storage import SOURCE_OPTIONS
    from app.action_task_delivery_storage import _source_declaration
    from app.configs_storage import create_configs_schema
    from app.schedules_storage import create_schedules_schema
    from app.task20_current_schema import create_current_task20_schema
    from app.runs11_current_schema import create_current_run11_schema

    capsule = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs"
                          / "2026-10-17-email-history-current15-source.json").read_text())
    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        create_configs_schema(db)
        create_schedules_schema(db)
        create_current_task20_schema(db)
        create_current_run11_schema(db)
        create_action_schema(db, reply_variant="fresh_memory_fk",
                             **(SOURCE_OPTIONS | {"include_business_actions": True,
                                                  "include_email_history": False}))
        for sql in store_module.BUSINESS_SOURCE_DOCUMENT_SCHEMA:
            db.execute(sql)
        for name, key in (("business", "unchanged_business_parent_declaration"),
                          ("business_legacy_links", "unchanged_legacy_child_declaration")):
            declaration = capsule[key]
            db.execute(next(obj[3] for obj in declaration["objects"] if obj[0] == "table"))
            sql_by_name = {obj[1]: obj[3] for obj in declaration["objects"] if obj[0] == "index"}
            for item in reversed(declaration["indexes"]):
                sql = sql_by_name[item["list"][1]]
                if sql:
                    db.execute(sql)
            for obj in declaration["objects"]:
                if obj[0] == "trigger":
                    db.execute(obj[3])
            assert _hash(_source_declaration(db, name)) == _hash(declaration)
        for kind, declaration in capsule["fresh_native_declarations"].items():
            db.execute(next(obj[3] for obj in declaration["objects"] if obj[0] == "table"))
            sql_by_name = {obj[1]: obj[3] for obj in declaration["objects"] if obj[0] == "index"}
            for item in reversed(declaration["indexes"]):
                sql = sql_by_name[item["list"][1]]
                if sql:
                    db.execute(sql)
            for obj in declaration["objects"]:
                if obj[0] == "trigger":
                    db.execute(obj[3])
        source = capture_email_history_source(db)
        assert source["variant"] == "fresh_memory_fk"
        assert _hash(source["source_tables"][HISTORY_KINDS[0]]) == (
            "77af2546065da3a40e79f69c9b5b883b82b7ffdd99d06cccf32fd1b14b54b1a3")
        assert _hash(source["source_tables"][HISTORY_KINDS[1]]) == (
            "2cf9b1103b170b04e639676f0f0ad9569d7510274ff8986b2af7555771faba6b")
        db.commit()


def test_nonempty_history_rows_blob_pk_retained_parents_and_inverse(tmp_path, monkeypatch):
    path = tmp_path / "source15.sqlite"
    _source15_store(path, monkeypatch)
    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        db.execute("insert into business_source_documents(id,identity_key,body) "
                   "values(1,'synthetic:document','Synthetic body')")
        from tests.test_action_business_storage import _insert_text_boundary_business_action
        signal = _insert_text_boundary_business_action(db, "business_task_signals", {
            "source_type": "meeting", "source_ref": "synthetic:meeting",
            "dedupe_key": "synthetic:signal", "context_json": "{}",
            "source_document_id": 1,
        }).lastrowid
        project = insert_business(db, "work_project", {"id": 1, "title": "Synthetic"}).lastrowid
        db.execute("insert into business_legacy_links(signal_id,work_project_id) values(?,?)",
                   (signal, project))
        db.execute("update sqlite_sequence set seq=50 where name='actions:business_task_signals'")
        db.execute("insert into email_historical_classification_history "
                   "values(?,?,?,?,?,?,?,?,?)",
                   (sqlite3.Binary(b'event'), "stable", "model", None, 0.0, 1.0,
                    None, "done", "time"))
        db.execute("insert into email_historical_classification_history "
                   "values(null,'stable2','model2',null,null,null,null,'done','time')")
        db.execute("insert into email_historical_classification_history "
                   "values(null,'stable3','model3',null,null,null,null,'done','time')")
        db.execute("insert into email_historical_operations values("
                   "'', 'account', 'stable', 'model', -5, 'plan', '{}', 'category', "
                   "1.25, -0.25, 1, 'processing', '', 'time', 'time')")
        db.execute("insert into email_historical_operations values("
                   "null, 'account', 'stable2', 'model', -5, 'plan', '5', 'category', "
                   "1.25, -0.25, 0, 'processing', '', 'time', 'time')")
        db.execute("insert into email_historical_operations values("
                   "null, 'account', 'stable3', 'model', -5, 'plan', 'null', 'category', "
                   "1.25, -0.25, 0, 'processing', '', 'time', 'time')")
        source = capture_email_history_source(db)
        family = {kind: _snapshot(db, kind, hidden_rowid=True) for kind in HISTORY_KINDS}
        parents = {kind: _snapshot(db, kind) for kind in ("actions", "business", "business_legacy_links")}
        waters = dict(db.execute("select name,seq from sqlite_sequence"))
        migrate_email_history_schema(db, json.loads(json.dumps(source)))
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert dict(db.execute("select name,seq from sqlite_sequence")) == waters
        assert _snapshot(db, "business") == parents["business"]
        assert _snapshot(db, "business_legacy_links") == parents["business_legacy_links"]
        for kind in HISTORY_KINDS:
            assert _projected_snapshot(db, kind) == family[kind]
        _insert_text_email_history(db, HISTORY_KINDS[0], {
            "event_id": "new", "stable_message_identity": "new", "model_id": "model",
            "predicted_category": None, "threshold": None, "probability": None,
            "important": None, "action_outcome": "new", "created_at": "later",
        })
        restore_email_history_schema(db, json.loads(json.dumps(source)))
        assert db.execute("pragma foreign_key_check").fetchall() == []
        assert dict(db.execute("select name,seq from sqlite_sequence")) == waters
        for kind in HISTORY_KINDS:
            restored = _snapshot(db, kind, hidden_rowid=True)
            if kind == HISTORY_KINDS[0]:
                assert restored["rows"][:-1] == family[kind]["rows"]
                assert restored["rows"][-1][1] == "new"
            else:
                assert restored == family[kind]
        assert _snapshot(db, "actions") == parents["actions"]
        assert _snapshot(db, "business") == parents["business"]
        assert _snapshot(db, "business_legacy_links") == parents["business_legacy_links"]
        # The inverse reads live target facts, so a second forward pass carries
        # writes made after the first migration as well as all original rows.
        second_source = capture_email_history_source(db)
        second_family = {kind: _snapshot(db, kind, hidden_rowid=True)
                         for kind in HISTORY_KINDS}
        migrate_email_history_schema(db, json.loads(json.dumps(second_source)))
        for kind in HISTORY_KINDS:
            assert _projected_snapshot(db, kind) == second_family[kind]
        assert db.execute("pragma foreign_key_check").fetchall() == []
        db.rollback()


def test_cold_reopen_repairs_only_missing_current_history_index(tmp_path):
    path = tmp_path / "repair.sqlite"
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute("drop index idx_actions_email_history_created")
    AutoReplyStore(path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("select 1 from sqlite_schema where type='index' "
                          "and name='idx_actions_email_history_created'").fetchone()
        assert not db.execute("select 1 from sqlite_schema where type='table' and name in (?,?)",
                              HISTORY_KINDS).fetchone()


def test_source_constraint_and_binding_results_match_actions(tmp_path, monkeypatch):
    path = tmp_path / "constraints.sqlite"
    _source15_store(path, monkeypatch)
    history = dict(event_id="history", stable_message_identity="stable",
                   model_id="model", predicted_category=None, threshold=.5,
                   probability=.5, important=1, action_outcome="done",
                   created_at="time")
    operation = dict(operation_id="operation", account_id="account",
                     stable_message_identity="stable", model_id="model",
                     classification_id=-5, action_plan_id="plan",
                     action_ids_json="{}", predicted_category="custom",
                     threshold=1.25, probability=-.25, important=1,
                     status="processing", action_outcome="", created_at="time",
                     updated_at="time")

    def source_insert(db, kind, values):
        fields = tuple(values)
        db.execute("insert into main." + kind + "(" + ",".join(fields) + ") values("
                   + ",".join("?" for _ in fields) + ")",
                   tuple(values.values()))

    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        cases = (
            (HISTORY_KINDS[0], history | {"event_id": "  "}, sqlite3.IntegrityError),
            (HISTORY_KINDS[0], history | {"threshold": 1.25}, sqlite3.IntegrityError),
            (HISTORY_KINDS[0], history | {"stable_message_identity": None}, sqlite3.IntegrityError),
            (HISTORY_KINDS[1], operation | {"action_ids_json": "{"}, sqlite3.IntegrityError),
            (HISTORY_KINDS[1], operation | {"important": 2}, sqlite3.IntegrityError),
            (HISTORY_KINDS[1], operation | {"action_ids_json": []}, sqlite3.ProgrammingError),
            (HISTORY_KINDS[1], operation | {"classification_id": 2**63}, OverflowError),
        )
        for kind, values, error in cases:
            db.execute("savepoint source_case")
            with pytest.raises(error):
                source_insert(db, kind, values)
            db.execute("rollback to source_case")
            db.execute("release source_case")
        source = capture_email_history_source(db)
        migrate_email_history_schema(db, json.loads(json.dumps(source)))
        for kind, values, error in cases:
            db.execute("savepoint action_case")
            try:
                _insert_text_email_history(db, kind, values)
            except error:
                pass
            else:
                pytest.fail(f"Actions accepted native-invalid {kind}: {values!r}")
            db.execute("rollback to action_case")
            db.execute("release action_case")
        # Ordinary PK NULLs and non-array JSON remain accepted in both roles.
        _insert_text_email_history(db, HISTORY_KINDS[0], history | {"event_id": None})
        _insert_text_email_history(db, HISTORY_KINDS[0], history | {"event_id": None})
        _insert_text_email_history(db, HISTORY_KINDS[1], operation | {"operation_id": None})
        _insert_text_email_history(db, HISTORY_KINDS[1], operation | {
            "operation_id": None, "stable_message_identity": "other", "account_id": "other-account"})
        assert db.execute("select count(*) from (" + history_projection(HISTORY_KINDS[0])
                          + ") where event_id is null").fetchone()[0] == 2
        assert db.execute("select count(*) from (" + history_projection(HISTORY_KINDS[1])
                          + ") where operation_id is null").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            _insert_text_email_history(db, HISTORY_KINDS[1], operation | {
                "operation_id": "different", "account_id": "other-account"})
        db.rollback()


def test_unknown_main_and_temp_dependencies_still_block_history_conversion(tmp_path, monkeypatch):
    path = tmp_path / "dependencies.sqlite"
    _source15_store(path, monkeypatch)
    with sqlite3.connect(path) as db:
        db.execute("pragma foreign_keys=off")
        db.execute("begin")
        db.execute("create view unknown_history_reader as select event_id from email_historical_classification_history")
        with pytest.raises(ValueError, match="dependenc"):
            capture_email_history_source(db)
        db.execute("drop view unknown_history_reader")
        db.execute("create temp view unknown_action_reader as select id from main.actions")
        with pytest.raises(ValueError, match="dependenc"):
            capture_email_history_source(db)
        db.rollback()


def test_history_rowid_max_allocation_and_caller_rollback(tmp_path):
    path = tmp_path / "rowid.sqlite"
    EmailStore(path)
    values = dict(event_id="new", stable_message_identity="stable", model_id="model",
                  predicted_category=None, threshold=None, probability=None,
                  important=None, action_outcome="done", created_at="time")
    with sqlite3.connect(path) as db:
        db.execute("begin")
        from app import action_storage as actions
        db.execute("insert into actions(_kind,legacy_key,payload_json) values(?,?,?)", (
            actions.ACTION_RECORD_TYPES.code(HISTORY_KINDS[0]), str(2**63 - 1),
            actions._action_payload(db, HISTORY_KINDS[0], values | {"event_id": "max"}),
        ))
        db.execute("savepoint caller")
        assert insert_email_history(db, HISTORY_KINDS[0], values) == 1
        source_rowid = db.execute("select cast(legacy_key as integer) from actions "
                                  "where record_type=? and email_history_event_id='new'",
                                  (HISTORY_KINDS[0],)).fetchone()[0]
        assert 1 <= source_rowid < 2**63 - 1
        db.execute("rollback to caller")
        db.execute("release caller")
        assert db.execute("select count(*) from actions where record_type=?",
                          (HISTORY_KINDS[0],)).fetchone()[0] == 1
        db.rollback()
