from tests.test_email_legacy_schema import published_source as published_source
import sqlite3

import pytest

from app.store import AutoReplyStore, _INITIALIZED_STORE_PATHS, STORE_SCHEMA_VERSION
from app.workbench_storage import workbench_schema_is_current, migrate_workbench_schema
from tests.test_sent_delivery_source import SOURCE as SENT_SOURCE
from tests.workbench_schema_fixtures import full_source_database, fingerprint, SOURCE


def test_source13_auxiliary_copy_preserves_fresh_business_declarations(
    tmp_path, published_source,
):
    from app.action_reply_storage import _fingerprint
    from app.business_schema_release import (
        PARENT_HASHES, SOURCE_CHILD_FULL_HASHES, SOURCE_PARENTS,
        capture_business_source,
    )

    with full_source_database(
        tmp_path / "source.db", tmp_path / "auxiliary.db", published_source,
    ) as db:
        captured = capture_business_source(db)
        assert captured["variant"] == "fresh"
        assert len(captured["parents"]) == 4
        assert len(captured["children"]) == 15
        assert tuple(_fingerprint(captured["parents"][name]) for name in SOURCE_PARENTS.values()) == PARENT_HASHES["fresh"]
        assert _fingerprint(captured["children"]) == SOURCE_CHILD_FULL_HASHES["fresh"]


def test_actual_source_declarations_initialize_transactionally_and_idempotently(
    tmp_path, published_source,
):
    path = tmp_path / "source.db"
    db = full_source_database(path, tmp_path / "auxiliary.db", published_source)
    db.execute('begin immediate')
    migrate_workbench_schema(db)
    from app.action_storage import migrate_action_schema
    migrate_action_schema(db)
    from app.action_receipt_storage import migrate_action_receipt_schema
    migrate_action_receipt_schema(db)
    from app.action_postfix_storage import migrate_action_postfix_schema
    migrate_action_postfix_schema(db)
    for definition in SENT_SOURCE["fresh_candidate"].values():
        db.execute(next(obj["sql"] for obj in definition["objects"] if obj["type"] == "table"))
    for definition in SENT_SOURCE["fresh_candidate"].values():
        for obj in definition["objects"]:
            if obj["type"] != "table":
                db.execute(obj["sql"])
    db.execute("update service_state set value='2026-10-13.3' where key='store_schema_version'")
    db.commit()
    db.close()
    from tests.workbench_caller_fixtures import advance_source17_to_current_store
    advance_source17_to_current_store(path, published_source)
    store = AutoReplyStore(path)
    with store._connect() as db:
        assert workbench_schema_is_current(db)
        assert store._schema_manifest_is_current_in_connection(
            db, include_run_snapshots=True
        )
        assert (
            db.execute(
                "select value from service_state where key='store_schema_version'"
            ).fetchone()[0]
            == STORE_SCHEMA_VERSION
        )
    with sqlite3.connect(path) as db:
        before = fingerprint(db)
    _INITIALIZED_STORE_PATHS.discard(path.resolve())
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert fingerprint(db) == before


@pytest.mark.parametrize(
    "name,kind,literal",
    [
        ("idx_workbench_one_active_turn", "index", "'queued'"),
        ("idx_scheduled_task_runs_scheduled_instant", "index", "'scheduled'"),
        ("trg_runs_workbench_turn_insert", "trigger", "'workbench_turns'"),
        (
            "trg_tasks_child_workbench_artifacts_turn_id_insert",
            "trigger",
            "'workbench_turns'",
        ),
    ],
)
@pytest.mark.parametrize("allow_missing", [False, True])
def test_current_manifest_preserves_actual_literals(
    tmp_path, name, kind, literal, allow_missing
):
    store = AutoReplyStore(tmp_path / "current.db")
    with sqlite3.connect(store.path) as db:
        sql = db.execute(
            "select sql from sqlite_master where name=?", (name,)
        ).fetchone()[0]
        db.execute(f"drop {kind} {name}")
        db.execute(sql.replace(literal, literal.upper(), 1))
        assert not workbench_schema_is_current(db, allow_missing_objects=allow_missing)
    _INITIALIZED_STORE_PATHS.discard(store.path.resolve())
    with sqlite3.connect(store.path) as db:
        before = fingerprint(db)
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed"):
        AutoReplyStore(store.path)
    with sqlite3.connect(store.path) as db:
        assert fingerprint(db) == before


def test_missing_current_auxiliary_index_repairs_without_recreating_retired_tables(
    tmp_path,
):
    store = AutoReplyStore(tmp_path / "current.db")
    with sqlite3.connect(store.path) as db:
        db.execute("drop index idx_workbench_events_event_type")
        db.execute("drop trigger trg_tasks_child_workbench_artifacts_turn_id_insert")
        assert not workbench_schema_is_current(db)
        assert workbench_schema_is_current(db, allow_missing_objects=True)
    _INITIALIZED_STORE_PATHS.discard(store.path.resolve())
    store = AutoReplyStore(store.path)
    with store._connect() as db:
        assert workbench_schema_is_current(db)
        assert not db.execute(
            "select 1 from sqlite_master where name in ('workbench_tasks','workbench_turns','workbench_events','scheduled_task_runs')"
        ).fetchone()


def test_current_marker_with_mixed_source_is_rejected_without_mutation(tmp_path):
    store = AutoReplyStore(tmp_path / "mixed.db")
    with sqlite3.connect(store.path) as db:
        db.execute("create table workbench_turns(id text primary key)")
        before = fingerprint(db)
    _INITIALIZED_STORE_PATHS.discard(store.path.resolve())
    with pytest.raises(sqlite3.IntegrityError, match="unsupported or malformed"):
        AutoReplyStore(store.path)
    with sqlite3.connect(store.path) as db:
        assert fingerprint(db) == before


@pytest.mark.parametrize("marker", [None, "unknown"])
@pytest.mark.parametrize(
    "table,insert_sql",
    [
        (
            "scheduled_task_runs",
            "insert into scheduled_task_runs(event_id,scheduled_task_id,trigger_kind,scheduled_for) values('kept',7,'manual','instant')",
        ),
        (
            "workbench_tasks",
            "insert into workbench_tasks(id,title,runtime_kind) values('01','kept','codex')",
        ),
        (
            "workbench_turns",
            "insert into workbench_turns(id,task_id,client_request_id,task_sequence,user_text,status) values('01','parent','kept',1,x'00ff','completed')",
        ),
        (
            "workbench_events",
            "insert into workbench_events(turn_id,sequence,event_type,payload_json) values('turn',0,'unknown',x'00ff')",
        ),
    ],
    ids=[
        "scheduled_task_runs",
        "workbench_tasks",
        "workbench_turns",
        "workbench_events",
    ],
)
def test_partial_retired_source_without_known_marker_refuses_before_bootstrap(
    tmp_path, table, insert_sql, marker
):
    path = tmp_path / "partial.db"
    with sqlite3.connect(path) as db:
        declarations = SOURCE["tables"][table]["objects"]
        for kind, _, sql in declarations:
            if kind == "table":
                db.execute(sql)
        for kind, _, sql in declarations:
            if kind != "table" and sql:
                db.execute(sql)
        db.execute(insert_sql)
        if marker is not None:
            db.execute(
                "create table service_state(key text primary key,value text,updated_at text)"
            )
            db.execute(
                "insert into service_state values('store_schema_version',?,'kept time')",
                (marker,),
            )
        before = fingerprint(db)
    expected_diagnostic = (
        "unsupported or malformed"
        if marker is None
        else "unsupported or mixed Store/Email source schema"
    )
    with pytest.raises(sqlite3.IntegrityError, match=expected_diagnostic):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert fingerprint(db) == before
