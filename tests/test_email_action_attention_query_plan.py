from app.audit_web import _queue_attention_rows
from app.email_store import EmailStore
from app.store import AutoReplyStore


def test_attention_selects_failed_action_ids_before_loading_classifications(
    tmp_path, monkeypatch
):
    path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(path)
    EmailStore(path)
    statements = []
    original_open = store._open_connection

    def traced_connection():
        db = original_open()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, "_open_connection", traced_connection)
    with store.read_snapshot():
        assert _queue_attention_rows(store) == []
    query = next(sql for sql in statements if "select a.action_id as id" in sql)
    with store._connect() as db:
        plan = [row[3] for row in db.execute("explain query plan " + query)]
    assert any("COVERING INDEX idx_email_actions_status" in step for step in plan), plan
    assert not any(step.startswith("SCAN c") or step == "SCAN a" for step in plan), plan
    assert any("SEARCH c USING INTEGER PRIMARY KEY" in step for step in plan), plan


def test_attention_does_not_hide_a_legacy_null_action_id(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._connect() as db:
        db.executescript("""
            create table email_classifications (
                id integer primary key, sender text, subject text,
                current_action_plan_id text, status text
            );
            create table email_actions (
                action_id text primary key, classification_id integer,
                action_plan_id text, status text, account_id text,
                provider_target text, action_type text, updated_at text,
                error text, attempt_count integer, next_attempt_at text
            );
            create index idx_email_actions_status on email_actions(status, updated_at, action_id);
            insert into email_classifications values (1,'Sender','Subject','plan','processed');
            insert into email_actions values (
                null,1,'plan','failed','account','','move','2026-10-08','failure',10,''
            );
        """)
    rows = _queue_attention_rows(store)
    assert len(rows) == 1
    assert rows[0]["category"] == "Email action"
    assert rows[0]["id"] == "None"
