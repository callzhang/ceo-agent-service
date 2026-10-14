"""Attention selects compact current Email actions before classification payloads."""

from app.action_storage import insert_action
from app.audit_web import _queue_attention_rows
from tests.test_email_action_external_consumers import _two_failed_actions


def test_attention_selects_failed_action_ids_before_loading_classifications(
    tmp_path, monkeypatch
):
    store = _two_failed_actions(tmp_path / "worker.sqlite3")
    with store._connect() as db:
        for number in range(1200):
            insert_action(db, "sent_replies", dict(
                conversation_id=f"unrelated-{number}",
                trigger_message_id=f"message-{number}", reply_text="sent",
            ))
    statements = []
    original_open = store._open_connection

    def traced_connection():
        db = original_open()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, "_open_connection", traced_connection)
    with store.read_snapshot():
        rows = [row for row in _queue_attention_rows(store)
                if row["category"] == "Email action"]
    assert len(rows) == 2
    query = next(sql for sql in statements if "select a.action_id as id" in sql)
    with store._connect() as db:
        plan = [row[3] for row in db.execute("explain query plan " + query)]
        steps = 0

        def count_steps():
            nonlocal steps
            steps += 1000
            return steps > 300_000

        db.set_progress_handler(count_steps, 1000)
        try:
            assert len(db.execute(query).fetchall()) == 2
        finally:
            db.set_progress_handler(None, 0)
    assert any("idx_actions_email_public_key (record_type=?)" in step for step in plan), plan
    assert not any(step.startswith("SCAN c") for step in plan), plan
    assert steps < 300_000


def test_attention_does_not_hide_nullable_public_action_ids(tmp_path):
    store = _two_failed_actions(tmp_path / "worker.sqlite3")
    rows = [row for row in _queue_attention_rows(store)
            if row["category"] == "Email action"]
    assert len(rows) == 2
    assert [row["id"] for row in rows] == ["None", "None"]
