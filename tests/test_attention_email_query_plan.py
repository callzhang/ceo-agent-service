import sqlite3
from contextvars import ContextVar

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore
from app.task_entity_storage import create_task


def test_email_attention_reads_payloads_only_for_failed_ids(tmp_path):
    path = tmp_path / "attention.sqlite3"
    initialized = AutoReplyStore(path)
    with initialized._connect() as db:
        def insert_email_classification(*, status, input_json, stable_identity,
                                        updated_at, error):
            task = create_task(db, values={
                "kind": "email_classification", "status": status,
                "available_at": "", "attempts": 0, "error": error,
                "created_at": updated_at, "updated_at": updated_at,
            })
            db.execute(
                "insert into email_classification_inputs "
                "(task_id,stable_message_identity,input_json) values(?,?,?)",
                (task["id"], stable_identity, input_json),
            )
            return str(task["id"])

        for i in range(100):
            insert_email_classification(
                status="done", input_json='{"padding":"' + "x" * 8192 + '"}',
                stable_identity=f"stable-{i}", updated_at="2026-10-10", error="",
            )
        failed_id = insert_email_classification(
            status="failed", input_json='{"message":{"subject":"failed subject"}}',
            stable_identity="stable-id", updated_at="2026-10-11", error="provider failed",
        )
        nullable_id = insert_email_classification(
            status="failed", input_json='{"message":{"subject":"nullable identity"}}',
            stable_identity="nullable-stable-id", updated_at="2026-10-12", error="nullable error",
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
    assert [(row["id"], row["summary"], row["error"]) for row in rows] == [
        (nullable_id, "nullable identity", "nullable error"),
        (failed_id, "failed subject", "provider failed")
    ]
    with store.read_snapshot():
        limited = _queue_attention_rows(store, limit=1)
    assert [(row["id"], row["summary"]) for row in limited] == [
        (nullable_id, "nullable identity")
    ]
    # Failed ids come from a compact (kind, status) index; payloads are then
    # read by primary key, never by scanning the input table.
    assert any(p.startswith("SEARCH t USING INDEX") and "status=?" in p for p in plans), plans
    assert any("SEARCH i USING INTEGER PRIMARY KEY" in p for p in plans), plans
    assert not any(p.startswith("SCAN") for p in plans), plans
