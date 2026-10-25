"""Attention selects compact current Email actions before classification payloads."""

from app.audit_web import _queue_attention_rows
from tests.test_email_action_external_consumers import _two_failed_actions


def test_attention_selects_failed_action_ids_before_loading_classifications(
    tmp_path, monkeypatch
):
    store = _two_failed_actions(tmp_path / "worker.sqlite3")
    with store._connect() as db:
        for number in range(1200):
            store.record_sent_reply(f"unrelated-{number}", f"message-{number}", "sent")
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
    assert any("SEARCH a USING INDEX " in step and "(record_type=?)" in step
               for step in plan), plan
    assert any("SEARCH main.emails USING INDEX sqlite_autoindex_emails_1 "
               "(classification_id=?)" in step for step in plan), plan
    assert not any(step.startswith("SCAN main.emails") for step in plan), plan
    assert steps < 300_000


def test_attention_lists_every_failed_action_with_its_native_id(tmp_path):
    store = _two_failed_actions(tmp_path / "worker.sqlite3")
    rows = [row for row in _queue_attention_rows(store)
            if row["category"] == "Email action"]
    assert len(rows) == 2
    assert all(row["id"].isdigit() for row in rows)
    assert len({row["id"] for row in rows}) == 2
