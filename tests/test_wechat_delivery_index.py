"""Latest-delivery projection stays indexed on fresh and upgraded databases."""

import app.audit_web as audit_web
import app.store as store_module
from app.store import WECHAT_DELIVERY_SQL, AutoReplyStore
from tests.wechat.support import create_wechat_delivery


INDEX_NAME = "idx_actions_td_wechat_task_generation"


def test_latest_wechat_delivery_index_survives_schema_initialization(tmp_path):
    path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="chat", conversation_title="Morgan",
        single_chat=True, trigger_message_id="message", trigger_create_time="2026-10-04 13:00:00",
        trigger_sender="Morgan", trigger_text="Can you help?",
    )
    task = store.get_reply_task_for_message("chat", "message", channel="wechat")
    delivery_id = create_wechat_delivery(store,
        reply_task_id=task.id, account_id="acct", target_type="direct", target_id="chat",
        conversation_id="chat", reply_text="Yes.",
    )
    with store._connect() as db:
        original_delivery = dict(db.execute(
            f"select * from ({WECHAT_DELIVERY_SQL}) where id=?", (delivery_id,),
        ).fetchone())
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    store = AutoReplyStore(path)
    assert store._schema_is_current() is True
    with store._connect() as db:
        indexes = {row["name"]: row for row in db.execute("pragma index_list(actions)")}
        assert INDEX_NAME in indexes
        assert not indexes[INDEX_NAME]["unique"]
        assert indexes[INDEX_NAME]["partial"]
        # The third key is the expression cast(legacy_key as integer), the delivery id.
        assert [row["name"] for row in db.execute(f"pragma index_info({INDEX_NAME})")] == [
            "td_reply_task_id", "td_execution_generation", None,
        ]
        assert dict(db.execute(
            f"select * from ({WECHAT_DELIVERY_SQL}) where id=?", (delivery_id,),
        ).fetchone()) == original_delivery


def test_history_and_queue_latest_delivery_queries_use_the_task_generation_index(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    statements = []
    original_open = store._open_connection

    def traced_open():
        connection = original_open()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store, "_open_connection", traced_open)
    store.list_operation_logs_with_count(limit=20, statuses=["failed"])
    with store._connect() as db:
        audit_web._reply_attempt_queue_snapshot(db)
    projected_queries = [
        sql for sql in statements if "select max(current_delivery.id)" in sql.lower()
    ]
    assert len(projected_queries) >= 2
    with store._connect() as db:
        for sql in projected_queries:
            plan = [row["detail"] for row in db.execute("explain query plan " + sql)]
            assert any(
                detail.startswith("SEARCH a USING INDEX " + INDEX_NAME
                                  + " (td_reply_task_id=? AND td_execution_generation=?)")
                for detail in plan
            ), plan
