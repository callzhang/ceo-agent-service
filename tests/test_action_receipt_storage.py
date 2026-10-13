"""Nine-type target must retain independently measured source contracts."""

from contextlib import closing
import sqlite3

import pytest

from app import action_storage as storage
from app.run_storage import create_run_schema, insert_run
from app.task_storage import create_task_schema, insert_task
from tests.test_action_receipt_source import RECEIPT_TYPES, source_values
from tests.test_action_receipt_source import SOURCE as RECEIPT_SOURCE, insert_source
from tests.test_action_storage import source_database


@pytest.fixture
def target_db():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("pragma foreign_keys=on")
        create_task_schema(db)
        create_run_schema(db, include_task_runs=True)
        insert_task(db, "reply_tasks", dict(id=1, conversation_id="chat",
                    conversation_title="source", single_chat=0, trigger_message_id="message",
                    trigger_create_time="time", trigger_sender="sender"))
        insert_run(db, "agent_runs", dict(id=1, task_id=1, execution_generation="generation", role="consumer"))
        insert_run(db, "agent_runtime_attempts", dict(id=2, legacy_key="2", workload_kind="reply",
                    workload_key="1", attempt_number=1, route_name="route", runtime_kind="codex_cli",
                    credential_mode="oauth", model="model", status="completed"))
        storage.create_action_schema(db, include_receipts=True)
        yield db


def test_text_keys_and_raw_receipt_fields_are_not_cast_or_reserialized(target_db):
    for key in ("1", "01", "A", "a", " key "):
        result = storage.insert_action(target_db, "external_action_results", source_values(
            "external_action_results", token=key,
        ))
        assert result.lastrowid == key
        assert storage.action_physical_id(target_db, "external_action_results", key) is not None
    rows = target_db.execute(
        f"select external_action_key,provider_result_json,target_identifiers_json "
        f"from ({storage.ACTION_PROJECTIONS['external_action_results']}) order by external_action_key"
    ).fetchall()
    assert {row[0] for row in rows} == {"1", "01", "A", "a", " key "}
    assert all(row[1:] == (' { "原始": [2, 1] } ', "not parsed JSON") for row in rows)
    storage.update_action(target_db, "external_action_results", "01", {"external_action_key": "001"})
    storage.delete_action(target_db, "external_action_results", "A")
    assert target_db.execute("select count(*) from actions").fetchone()[0] == 4
    assert storage.action_physical_id(target_db, "external_action_results", "001") is not None
    assert storage.action_schema_is_current(target_db, include_receipts=True)


def test_receipt_schema_creates_native_sequence_without_persisting_an_extra_table():
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript("""
            create table tasks(id integer primary key,record_type text,legacy_key text);
            create table runs(id integer primary key,record_type text,legacy_key text);
            insert into runs values(1,'agent_runs','1');
        """)
        assert not db.execute("select 1 from sqlite_schema where name='sqlite_sequence'").fetchone()
        storage.create_action_schema(db, include_receipts=True)
        assert storage.insert_action(db, "agent_execution_receipts", source_values("agent_execution_receipts")).lastrowid == 1
        assert {row[0] for row in db.execute("select name from sqlite_schema where type='table'")} == {
            "tasks", "runs", "actions", "sqlite_sequence",
        }


@pytest.mark.parametrize("kind", ["agent_effect_intents", "agent_execution_receipts"])
def test_autoincrement_deleted_high_id_and_ignored_unique_insert(target_db, kind):
    storage.insert_action(target_db, kind, {**source_values(kind), "id": 560})
    storage.delete_action(target_db, kind, 560)
    assert storage.insert_action(target_db, kind, source_values(kind)).lastrowid == 561
    assert storage.insert_action(target_db, kind, source_values(kind), ignore=True).rowcount == 0
    assert storage.insert_action(target_db, kind, source_values(kind, token="next")).lastrowid == 563


@pytest.mark.parametrize("kind", ["agent_effect_intents", "agent_execution_receipts"])
def test_autoincrement_exhaustion_is_sqlite_full_before_mutation(target_db, kind):
    storage.insert_action(target_db, kind, {**source_values(kind), "id": 2**63 - 1})
    storage.delete_action(target_db, kind, 2**63 - 1)
    target_db.commit()
    before = tuple(target_db.iterdump())
    with pytest.raises(sqlite3.OperationalError) as error:
        storage.insert_action(target_db, kind, source_values(kind))
    assert error.value.sqlite_errorcode == sqlite3.SQLITE_FULL
    assert tuple(target_db.iterdump()) == before


@pytest.mark.parametrize("kind", RECEIPT_TYPES)
def test_wrong_run_type_is_rejected_on_insert_and_update(target_db, kind):
    with pytest.raises(sqlite3.IntegrityError):
        storage.insert_action(target_db, kind, source_values(kind, run_id=2))
    result = storage.insert_action(target_db, kind, source_values(kind))
    field = "first_agent_run_id" if kind == "external_action_results" else "agent_run_id"
    with pytest.raises(sqlite3.IntegrityError):
        storage.update_action(target_db, kind, result.lastrowid, {field: 2})
    assert not target_db.execute("pragma foreign_key_check").fetchall()


def test_independent_receipts_defaults_flags_and_no_extra_payload_keys(target_db):
    storage.insert_action(target_db, "agent_execution_receipts", source_values("agent_execution_receipts"))
    storage.insert_action(target_db, "agent_effect_intents", {
        **source_values("agent_effect_intents"), "external_action_key": "no-global-parent",
    })
    flags = target_db.execute(
        f"select completed,persisted,safe_to_confirm,effect_counted "
        f"from ({storage.ACTION_PROJECTIONS['agent_execution_receipts']})"
    ).fetchone()
    assert flags == (2, -1, 3, 0)
    defaults = target_db.execute(
        f"select state,result_digest,exit_code,dispatched_at,acknowledged_at "
        f"from ({storage.ACTION_PROJECTIONS['agent_effect_intents']})"
    ).fetchone()
    assert defaults == ("prepared", "", None, "", "")
    with pytest.raises(sqlite3.IntegrityError):
        target_db.execute("update actions set payload_json=json_set(payload_json,'$.invented',1)")
    with pytest.raises(ValueError):
        storage.insert_action(target_db, "external_action_results", source_values(
            "external_action_results", token=None,
        ))


def source15_database(path, row_factory=None, *, fresh=False):
    import json
    from pathlib import Path
    fresh_source = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-13-database-redesign-phase5b-fresh-source.json").read_text())["tables"]
    db = source_database(path, row_factory)
    db.execute("drop table external_action_results")
    for kind in RECEIPT_TYPES:
        objects = fresh_source[kind]["objects"] if fresh else RECEIPT_SOURCE[kind]["objects"]
        db.execute(next(obj["sql"] for obj in objects if obj["type"] == "table"))
        for obj in objects:
            if obj["type"] != "table" and obj["sql"]:
                db.execute(obj["sql"])
    for key in ("1", "01", "A", "a"):
        insert_source(db, "external_action_results", source_values("external_action_results", token=key))
    for kind, watermark in (("agent_effect_intents", 560), ("agent_execution_receipts", 115)):
        insert_source(db, kind, {**source_values(kind, token="deleted"), "id": watermark})
        db.execute(f"delete from {kind}")
        insert_source(db, kind, {**source_values(kind), "id": 7})
    db.commit()
    db.execute("begin immediate")
    storage.migrate_action_schema(db)
    db.commit()
    return db


def receipt_snapshot(db, *, target=False):
    return {kind: [tuple(row) for row in db.execute(
        f"select * from ({storage.ACTION_PROJECTIONS[kind]}) order by {storage.IDENTITY_FIELDS[kind]}"
        if target else f"select {','.join(storage.SOURCE_COLUMNS[kind])} from {kind} order by {storage.IDENTITY_FIELDS[kind]}"
    )] for kind in RECEIPT_TYPES}


def six_snapshot(db):
    return {kind: [tuple(row) for row in db.execute(storage.ACTION_PROJECTIONS[kind])]
            for kind in storage.CANDIDATE_ACTION_TYPES}


@pytest.mark.parametrize("row_factory", [None, sqlite3.Row])
@pytest.mark.parametrize("fresh", [False, True])
def test_source15_forward_current_fact_inverse_and_re_forward(tmp_path, row_factory, fresh):
    from app.action_receipt_storage import (
        capture_action_receipt_schema, migrate_action_receipt_schema, restore_action_receipt_schema,
    )
    with closing(source15_database(tmp_path / "source.db", row_factory, fresh=fresh)) as db:
        old_six = six_snapshot(db)
        before = receipt_snapshot(db)
        state = capture_action_receipt_schema(db)
        old_objects = storage._objects(db, "actions")
        old_ids = [tuple(row) for row in db.execute("select id,record_type,legacy_key from actions order by id")]
        db.execute("begin immediate")
        migrate_action_receipt_schema(db)
        assert receipt_snapshot(db, target=True) == before
        assert six_snapshot(db) == old_six
        assert [tuple(row) for row in db.execute(
            "select id,record_type,legacy_key from actions where record_type not in "
            "('external_action_results','agent_effect_intents','agent_execution_receipts') order by id"
        )] == old_ids
        assert not set(RECEIPT_TYPES) & {row[0] for row in db.execute("select name from sqlite_schema where type='table'")}
        assert storage.insert_action(db, "agent_effect_intents", source_values("agent_effect_intents", token="new")).lastrowid == 561
        assert storage.insert_action(db, "agent_execution_receipts", source_values("agent_execution_receipts", token="new")).lastrowid == 116
        storage.insert_action(db, "external_action_results", source_values("external_action_results", token="independent"))
        storage.update_action(db, "external_action_results", "01", {"provider_result_json": "raw current receipt"})
        storage.delete_action(db, "external_action_results", "A")
        storage.insert_action(db, "candidate_supplements", {"candidate_id": 7, "instruction": "current A fact"})
        expected_receipts, expected_six = receipt_snapshot(db, target=True), six_snapshot(db)
        restore_action_receipt_schema(db, state)
        assert storage._objects(db, "actions") == old_objects
        assert receipt_snapshot(db) == expected_receipts
        assert six_snapshot(db) == expected_six
        assert insert_source(db, "agent_effect_intents", source_values("agent_effect_intents", token="after-inverse")).lastrowid == 562
        assert insert_source(db, "agent_execution_receipts", source_values("agent_execution_receipts", token="after-inverse")).lastrowid == 117
        before_re_forward = receipt_snapshot(db)
        migrate_action_receipt_schema(db)
        assert receipt_snapshot(db, target=True) == before_re_forward
        assert storage.action_schema_is_current(db, include_receipts=True)


@pytest.mark.parametrize("mutation", ["view", "inbound", "column", "null-key", "blob", "enum", "wrong-run"])
def test_unsupported_source15_is_refused_before_mutation(tmp_path, mutation):
    from app.action_receipt_storage import migrate_action_receipt_schema
    with closing(source15_database(tmp_path / "refusal.db")) as db:
        if mutation == "view":
            db.execute('create view observed as select * from "EXTERNAL_ACTION_RESULTS"')
        elif mutation == "inbound":
            db.execute('create table child(id integer,foreign key(id) references "AGENT_EFFECT_INTENTS"(id))')
        elif mutation == "column":
            db.execute("alter table agent_effect_intents add column invented text")
        elif mutation == "null-key":
            insert_source(db, "external_action_results", source_values("external_action_results", token=None))
        elif mutation == "blob":
            db.execute("update external_action_results set provider_result_json=x'00ff'")
        elif mutation == "enum":
            db.execute("pragma ignore_check_constraints=on")
            db.execute("update agent_effect_intents set state='invalid-source-state'")
            db.execute("pragma ignore_check_constraints=off")
        else:
            trigger = next(obj for obj in RECEIPT_SOURCE['agent_effect_intents']['objects']
                           if obj['name'] == 'trg_runs_child_agent_effect_intents_agent_run_id_update')
            db.execute(f"drop trigger {trigger['name']}")
            db.execute("update agent_effect_intents set agent_run_id=6")
            db.execute(trigger['sql'])
        db.commit()
        db.execute("begin immediate")
        before = tuple(db.iterdump())
        with pytest.raises(ValueError):
            migrate_action_receipt_schema(db)
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize("kind", ["agent_effect_intents", "agent_execution_receipts"])
@pytest.mark.parametrize("ignore", [False, True])
@pytest.mark.parametrize("connection_options", [{"isolation_level": ""}, {"isolation_level": None}, {"autocommit": True}])
def test_concurrent_automatic_receipts_are_both_saved(tmp_path, target_db, kind, ignore, connection_options):
    from threading import Event, Thread

    path = tmp_path / "concurrent.db"
    target_db.commit()
    with closing(sqlite3.connect(path)) as copy:
        target_db.backup(copy)
    insertion_started, competitor_done = Event(), Event()
    errors, ids = [], []

    def competitor():
        try:
            with closing(sqlite3.connect(path, timeout=5, **connection_options)) as other:
                assert insertion_started.wait(5)
                ids.append(storage.insert_action(other, kind, source_values(kind, token="second"), ignore=ignore).lastrowid)
                other.commit()
        except BaseException as exc:
            errors.append(exc)
        finally:
            competitor_done.set()

    worker = Thread(target=competitor)
    worker.start()
    with closing(sqlite3.connect(path, timeout=5, **connection_options)) as first:
        def interleave(sql):
            if not insertion_started.is_set() and sql.lower().startswith("insert") and "into actions(" in sql.lower():
                insertion_started.set()
                competitor_done.wait(0.25)
        first.set_trace_callback(interleave)
        try:
            ids.append(storage.insert_action(first, kind, source_values(kind, token="first"), ignore=ignore).lastrowid)
            first.commit()
        finally:
            insertion_started.set()
    worker.join(6)
    assert not worker.is_alive()
    assert not errors
    assert sorted(ids) == [1, 2]
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("select count(*) from actions").fetchone()[0] == 2
