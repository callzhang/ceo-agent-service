from tests.test_email_legacy_schema import published_source as published_source
import sqlite3

import pytest

from app.store import AutoReplyStore, _INITIALIZED_STORE_PATHS
from app.task20_current_schema import task20_workbench_schema_is_current
from tests.workbench_schema_fixtures import fingerprint, SOURCE


@pytest.mark.parametrize(
    "name,kind,literal",
    [
        ("idx_task20_active_turn", "index", "'queued'"),
        ("idx_scheduled_task_runs_scheduled_instant", "index", "'scheduled'"),
        ("trg_runs_workbench_turn_insert", "trigger", "'workbench_turns'"),
        (
            "trg_task20_parent_workbench_artifacts_insert",
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
        assert not task20_workbench_schema_is_current(
            db, allow_missing_objects=allow_missing, include_feedback=True
        )
    _INITIALIZED_STORE_PATHS.discard(store.path.resolve())
    with sqlite3.connect(store.path) as db:
        before = fingerprint(db)
    with pytest.raises(sqlite3.IntegrityError, match="malformed"):
        AutoReplyStore(store.path)
    with sqlite3.connect(store.path) as db:
        assert fingerprint(db) == before


def test_missing_current_auxiliary_index_rejects_without_recreating_retired_tables(tmp_path):
    path = tmp_path / "damaged.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute("drop index idx_workbench_events_event_type")
        before = tuple(db.iterdump())
    with pytest.raises(sqlite3.IntegrityError, match="malformed"):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before


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
    expected_diagnostic = "unsupported or malformed"
    with pytest.raises(sqlite3.IntegrityError, match=expected_diagnostic):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert fingerprint(db) == before
