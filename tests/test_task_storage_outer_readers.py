"""Outer task readers keep public identities after typed storage publication."""

from datetime import datetime, timedelta, timezone


from app.audit_web import _queue_attention_rows, _queue_status_snapshots
from app.dispatcher.adapters import ReplyQueueAdapter, WorkSummaryQueueAdapter
from app.store import AutoReplyStore, WORK_SUMMARY_INPUT_SQL
from tests.support.native_task_fields import set_native_task_fields


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def test_dispatcher_and_status_use_native_work_item_id(tmp_path):
    store = AutoReplyStore(tmp_path / "outer.db")
    store.enqueue_reply_task(
        conversation_id="chat", conversation_title="Conversation", single_chat=False,
        trigger_message_id="message", trigger_create_time=NOW.isoformat(),
        trigger_sender="Derek", trigger_text="Please check.",
    )
    public_work_id = store.enqueue_work_summary_input(
        "reply_attempt", "one", '{"summary":"Work summary"}',
    )
    with store._connect() as db:
        assert db.execute(f"select id from ({WORK_SUMMARY_INPUT_SQL}) where id=?", (public_work_id,)).fetchone()[0] == public_work_id
    reply = ReplyQueueAdapter(store)
    work = WorkSummaryQueueAdapter(store)
    assert reply.metrics(NOW).pending == 1
    assert work.metrics(NOW).pending == 1
    reply_claim = reply.claim(NOW, owner="reply-owner", lease=timedelta(minutes=5))
    assert reply_claim is not None and reply_claim.source_id == "1"
    reply.release(reply_claim, owner="reply-owner", now=NOW)
    work_claim = work.claim(NOW, owner="work-owner", lease=timedelta(minutes=5))
    assert work_claim is not None and work_claim.source_id == str(public_work_id)
    with store._connect() as db:
        assert db.execute(
            f"select status from ({WORK_SUMMARY_INPUT_SQL}) where id=?",
            (public_work_id,),
        ).fetchone()[0] == "processing"
    work.release(work_claim, owner="work-owner", now=NOW)
    with store._connect() as db:
        assert db.execute(
            f"select status from ({WORK_SUMMARY_INPUT_SQL}) where id=?",
            (public_work_id,),
        ).fetchone()[0] == "pending"
    snapshots = _queue_status_snapshots(store)
    work_snapshot = next(item for item in snapshots if item["name"] == "Work items")
    assert work_snapshot["pending"] == 1
    assert work_snapshot["processing"] == 0
    with store._connect() as db:
        set_native_task_fields(db, "work_summary_inputs", public_work_id, {
            "status": "failed", "error": "failed work",
        })
    rows = _queue_attention_rows(store)
    work_row = next(item for item in rows if item["category"] == "Work item")
    assert work_row["id"] == str(public_work_id)
    assert work_row["error"] == "failed work"


def test_work_summary_payload_keeps_codec_and_terminal_provenance(tmp_path):
    import hashlib
    import json

    from app.storage_maintenance import compact_terminal_work_summary_inputs
    from app.work_summary_payload import decode_work_summary_payload, migrate_work_summary_payloads

    store = AutoReplyStore(tmp_path / "codec.db")
    payload = json.dumps({
        "source": {"created_at": "2026-10-01T00:00:00Z"},
        "summary": "repeated text " * 200,
    })
    pending_id = store.enqueue_work_summary_input("test", "pending", payload)
    done_id = store.enqueue_work_summary_input("test", "done", payload)
    with store._connect() as db:
        # Rows written uncompressed before the codec; a terminal row not yet compacted.
        db.execute("update work_summary_inputs set payload_json=? where task_id in (?,?)", (payload, pending_id, done_id))
        set_native_task_fields(db, "work_summary_inputs", done_id, {"status": "done"})
    compressed = migrate_work_summary_payloads(store.path)
    assert compressed["compressed"] == 1
    with store._connect() as db:
        stored = db.execute(
            f"select payload_json from ({WORK_SUMMARY_INPUT_SQL}) where id=?", (pending_id,)
        ).fetchone()[0]
        assert isinstance(stored, bytes)
        assert decode_work_summary_payload(stored) == payload
    compacted = compact_terminal_work_summary_inputs(store.path)
    assert compacted["rows_compacted"] == 1
    with store._connect() as db:
        row = db.execute(f"select * from ({WORK_SUMMARY_INPUT_SQL}) where id=?", (done_id,)).fetchone()
        assert row["payload_json"] == "{}"
        assert row["source_created_at"] == "2026-10-01T00:00:00Z"
        assert row["body_sha256"] == hashlib.sha256(payload.encode()).hexdigest()
        assert row["body_bytes"] == len(payload.encode())
        assert row["body_compacted"] == 1


