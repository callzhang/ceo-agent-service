import sqlite3
from contextvars import ContextVar

from app.audit_web import _queue_attention_rows
from app.store import AutoReplyStore


def test_email_attention_reads_payloads_only_for_failed_ids(tmp_path):
    path = tmp_path / "attention.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            create table email_agent_classification_tasks (
                task_id text primary key, status text, input_json text,
                stable_message_identity text, updated_at text, error text,
                available_at text, lease_expires_at text
            );
            create index idx_email_agent_classification_tasks_status
            on email_agent_classification_tasks
                (status, available_at, lease_expires_at, task_id);
        """)
        db.executemany(
            "insert into email_agent_classification_tasks values (?,?,?,?,?,?,?,?)",
            [(str(i), "done", '{"padding":"' + "x" * 8192 + '"}',
              str(i), "2026-10-10", "", "", "") for i in range(100)],
        )
        db.execute(
            "insert into email_agent_classification_tasks values (?,?,?,?,?,?,?,?)",
            ("failed-id", "FAILED", '{"message":{"subject":"failed subject"}}',
             "stable-id", "2026-10-11", "provider failed", "", ""),
        )
    plans = []

    class Connection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "from email_agent_classification_tasks" in sql.lower():
                plans.extend(
                    row[3] for row in super().execute(
                        "explain query plan " + sql, parameters
                    ).fetchall()
                )
            return super().execute(sql, parameters)

    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = path
    store._read_snapshot_connection = ContextVar("attention_test", default=None)
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
        ("failed-id", "failed subject", "provider failed")
    ]
    assert any("USING COVERING INDEX idx_email_agent_classification_tasks_status" in p
               for p in plans)
    assert "SCAN email_agent_classification_tasks" not in plans
