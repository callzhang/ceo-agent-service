"""Latest-delivery projection stays indexed on fresh and upgraded databases."""

import pytest

import app.audit_web as audit_web
import app.store as store_module
from app.store import AutoReplyStore


INDEX_NAME = "idx_wechat_deliveries_task_generation"


@pytest.mark.parametrize("schema", ["fresh", "missing_index", "legacy_unique"])
def test_latest_wechat_delivery_index_survives_schema_initialization(tmp_path, schema):
    path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        channel="wechat", conversation_id="chat", conversation_title="Morgan",
        single_chat=True, trigger_message_id="message", trigger_create_time="2026-10-04 13:00:00",
        trigger_sender="Morgan", trigger_text="Can you help?",
    )
    task = store.get_reply_task_for_message("chat", "message", channel="wechat")
    delivery_id = store.create_wechat_delivery(
        reply_task_id=task.id, account_id="acct", target_type="direct", target_id="chat",
        conversation_id="chat", reply_text="Yes.",
    )
    with store._connect() as db:
        original_delivery = dict(db.execute(
            "select * from wechat_deliveries where id=?", (delivery_id,),
        ).fetchone())
        if schema != "fresh":
            db.execute(f"drop index if exists {INDEX_NAME}")
        if schema == "legacy_unique":
            db.execute(
                "create unique index legacy_wechat_task_unique "
                "on wechat_deliveries(reply_task_id)"
            )
            db.execute(
                "update service_state set value='2026-09-24.2' where key=?",
                (store_module.STORE_SCHEMA_VERSION_KEY,),
            )
    if schema != "fresh":
        assert store._schema_is_current() is False
        store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
        store = AutoReplyStore(path)
    assert store._schema_is_current() is True
    with store._connect() as db:
        indexes = {row["name"]: row for row in db.execute("pragma index_list(wechat_deliveries)")}
        assert INDEX_NAME in indexes
        assert not indexes[INDEX_NAME]["unique"]
        assert [row["name"] for row in db.execute(f"pragma index_info({INDEX_NAME})")] == [
            "reply_task_id", "execution_generation", "id",
        ]
        assert dict(db.execute(
            "select * from wechat_deliveries where id=?", (delivery_id,),
        ).fetchone()) == original_delivery


def test_history_and_queue_latest_delivery_queries_use_covering_index(tmp_path, monkeypatch):
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
                "SEARCH current_delivery USING COVERING INDEX " + INDEX_NAME in detail
                for detail in plan
            ), plan
            assert not any(detail == "SEARCH current_delivery" for detail in plan), plan
