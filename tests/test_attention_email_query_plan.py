import sqlite3
from contextvars import ContextVar

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore
from app.task12_storage import insert_task12


def test_email_attention_reads_payloads_only_for_failed_ids(tmp_path):
    path = tmp_path / "attention.sqlite3"
    initialized = AutoReplyStore(path)
    with initialized._connect() as db:
        for i in range(100):
            insert_task12(db, "email_agent_classification_tasks", {
                "task_id": str(i), "channel": "email", "stable_message_identity": f"stable-{i}",
                "status": "done", "input_json": '{"padding":"' + "x" * 8192 + '"}',
                "updated_at": "2026-10-10", "error": "",
            })
        insert_task12(db, "email_agent_classification_tasks", {
            "task_id": "failed-id", "channel": "email", "status": "failed",
            "input_json": '{"message":{"subject":"failed subject"}}',
            "stable_message_identity": "stable-id", "updated_at": "2026-10-11",
            "error": "provider failed",
        })
        insert_task12(db, "email_agent_classification_tasks", {
            "task_id": None, "channel": "email", "status": "failed",
            "input_json": '{"message":{"subject":"nullable identity"}}',
            "stable_message_identity": "nullable-stable-id", "updated_at": "2026-10-12",
            "error": "nullable error",
        })
    plans = []

    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "from tasks" in sql.lower() and "email_agent_classification_tasks" in sql.lower():
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
        ("None", "nullable identity", "nullable error"),
        ("failed-id", "failed subject", "provider failed")
    ]
    with store.read_snapshot():
        limited = _queue_attention_rows(store, limit=1)
    assert [(row["id"], row["summary"]) for row in limited] == [
        ("None", "nullable identity")
    ]
    assert any("idx_task20_native_1" in p
               for p in plans), plans
    assert not any("SCAN tasks" in p for p in plans)
