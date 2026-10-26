import sqlite3
from contextvars import ContextVar

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore


def test_email_attention_reads_payloads_only_for_failed_ids(tmp_path):
    path = tmp_path / "attention.sqlite3"
    initialized = AutoReplyStore(path)
    with initialized._connect() as db:
        def add_task(status, stable, input_json, updated_at, error):
            task_id = db.execute(
                "insert into tasks(kind,status,error,parameters_json,created_at,updated_at) "
                "values('email_classification',?,?,jsonb('{}'),?,?)",
                (status, error, updated_at, updated_at),
            ).lastrowid
            db.execute(
                "insert into email_classification_inputs"
                "(task_id,channel,stable_message_identity,input_json) values(?,?,?,?)",
                (task_id, "email", stable, input_json),
            )
            return task_id

        for i in range(100):
            add_task(
                "done", f"stable-{i}", '{"padding":"' + "x" * 8192 + '"}', "2026-10-10", "",
            )
        failed_id = add_task(
            "failed", "stable-id", '{"message":{"subject":"failed subject"}}',
            "2026-10-11", "provider failed",
        )
    plans = []

    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "from tasks" in sql.lower() and "email_classification_inputs" in sql.lower():
                plans.extend(
                    row[3] for row in super().execute(
                        "explain query plan " + sql, parameters
                    ).fetchall()
                )
            return super().execute(sql, parameters)

    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = path
    store._read_snapshot_connection = ContextVar("attention_test", default=None)
    store._reused_connection = ContextVar(f"test_reused_connection_{id(store)}", default=None)
    store.list_current_unresolved_problem_attempt_summaries = lambda **kwargs: []

    def connect():
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, factory=Connection)
        db.row_factory = sqlite3.Row
        db.execute("pragma query_only=on")
        return db

    store._open_connection = connect
    with store.read_snapshot():
        rows = _queue_attention_rows(store)
    assert [
        (row["id"], row["summary"], row["error"])
        for row in rows if row["category"] == "Email classification"
    ] == [(str(failed_id), "failed subject", "provider failed")]
    with store.read_snapshot():
        limited = _queue_attention_rows(store, limit=1)
    assert [
        row["id"] for row in limited if row["category"] == "Email classification"
    ] == [str(failed_id)]
    # Failed ids come from a compact (kind, status) index; payloads are then
    # read by primary key, never by scanning the input table.
    assert any(p.startswith("SEARCH t USING INDEX") and "status=?" in p for p in plans), plans
    assert any("SEARCH i USING INTEGER PRIMARY KEY" in p for p in plans), plans
    assert not any(p.startswith("SCAN") for p in plans), plans
