import sqlite3

import pytest

from app.audit_web import _email_action_queue_snapshot, _reply_task_queue_snapshot
from app.email_store import DIRECT_ACTION_MAX_ATTEMPTS


@pytest.fixture
def database():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
        create table tasks (
            id integer primary key, status text, business_object_key text,
            updated_at text, error text, record_type text not null default 'reply_tasks'
        );
        create table business_object_tasks (
            business_object_key text primary key, reply_task_id integer
        );
        create table email_classifications (
            id integer primary key, status text, current_action_plan_id text
        );
        create table email_actions (
            action_id text primary key, classification_id integer,
            action_plan_id text, status text, attempt_count integer,
            next_attempt_at text, updated_at text, error text
        );
    """)
    try:
        yield db
    finally:
        db.close()


@pytest.mark.parametrize("snapshot", [_reply_task_queue_snapshot, _email_action_queue_snapshot])
def test_queue_summary_evaluates_current_projection_once(database, snapshot):
    queries = []
    database.set_trace_callback(queries.append)
    result = snapshot(database)
    assert result["counts"] == {}
    assert result["latest_updated_at"] == ""
    assert result["latest_error"] == ""
    assert len(queries) == 1


def test_reply_summary_preserves_current_business_identity_and_latest_error(database):
    database.executemany(
        "insert into tasks(id,status,business_object_key,updated_at,error) values(?,?,?,?,?)",
        [
            (1, "failed", "object-a", "2026-10-12", "retired error"),
            (2, "done", "object-a", "2026-10-11", ""),
            (3, "FAILED", "", "2026-10-10", "older failure"),
            (4, "error", None, "2026-10-11", ""),
        ],
    )
    database.execute("insert into business_object_tasks values('object-a',2)")
    result = _reply_task_queue_snapshot(database)
    assert result["counts"] == {"done": 1, "error": 1, "failed": 1}
    assert result["failed"] == 2
    assert result["latest_updated_at"] == "2026-10-11"
    assert result["latest_error"] == ""


def test_reply_summary_latest_failed_tie_uses_id(database):
    database.executemany(
        "insert into tasks(id,status,business_object_key,updated_at,error) values(?,?,?,?,?)",
        [(1, "failed", "", "2026-10-11", "first"),
         (2, "failed", "", "2026-10-11", "second")],
    )
    assert _reply_task_queue_snapshot(database)["latest_error"] == "second"


def test_email_summary_preserves_plan_retry_and_latest_row_semantics(database):
    database.executemany(
        "insert into email_classifications values(?,?,?)",
        [(1, "processed", "current"), (2, "pending", "inactive")],
    )
    database.executemany(
        "insert into email_actions values(?,?,?,?,?,?,?,?)",
        [
            ("old", 1, "retired", "processing", 0, "", "2026-10-12", "retired error"),
            ("retry", 1, "current", "FAILED", DIRECT_ACTION_MAX_ATTEMPTS - 1,
             "2026-10-11T00:00:00+00:00", "2026-10-10", "retryable"),
            ("exhausted", 1, "current", "failed", DIRECT_ACTION_MAX_ATTEMPTS,
             "2026-10-11T00:00:00+00:00", "2026-10-10", "terminal"),
            ("invalid", 1, "current", "failed", 0, "invalid date", "2026-10-10", "invalid"),
            ("b", 1, "current", "done", 1, "", "2026-10-11", "first"),
            ("z", 1, "current", "done", 1, "", "2026-10-11", "最新\x1f错误"),
            ("inactive", 2, "inactive", "pending", 0, "", "2026-10-12", "inactive"),
        ],
    )
    result = _email_action_queue_snapshot(database)
    assert result["counts"] == {"done": 2, "failed": 3}
    assert result["retryable"] == 1
    assert result["failed"] == 2
    assert result["processing"] == 0
    assert result["latest_updated_at"] == "2026-10-11"
    assert result["latest_error"] == "最新\x1f错误"


def test_email_summary_scans_actions_in_storage_order_for_current_plan(database):
    database.execute(
        "create index idx_email_classifications_status "
        "on email_classifications(status, id desc)"
    )
    database.execute(
        "create index idx_email_actions_classification_plan "
        "on email_actions(classification_id, action_plan_id)"
    )
    database.execute(
        "insert into email_classifications values(1, 'processed', 'current')"
    )
    database.execute(
        "insert into email_actions values('one', 1, 'current', 'done', 1, '', '2026-10-14', '')"
    )

    statements = []
    database.set_trace_callback(statements.append)
    snapshot = _email_action_queue_snapshot(database)
    database.set_trace_callback(None)
    plan = [
        str(row[3])
        for row in database.execute("explain query plan " + statements[0])
    ]

    assert snapshot["counts"] == {"done": 1}
    assert any("SCAN a" in step for step in plan)
    assert any("SEARCH c USING INTEGER PRIMARY KEY" in step for step in plan)


def test_reply_summary_excludes_other_task_types(database):
    database.execute(
        "insert into tasks(id,record_type,status,updated_at,error) values(99,'work_summary_inputs','failed','future','other-type-error')"
    )
    result = _reply_task_queue_snapshot(database)
    assert result["counts"] == {}
    assert result["failed"] == 0
    assert result["latest_error"] == ""
