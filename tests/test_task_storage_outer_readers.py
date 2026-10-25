"""Outer task readers keep public identities after typed storage publication."""

from datetime import datetime, timedelta, timezone


from app.audit_web import _queue_attention_rows, _queue_status_snapshots
from app.dispatcher.adapters import ReplyQueueAdapter, WorkSummaryQueueAdapter
from app.store import AutoReplyStore, WORK_SUMMARY_INPUT_SQL
from app.task8_storage import task8_insert_parts, task8_set_parts


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def test_dispatcher_and_status_use_public_work_item_id(tmp_path):
    store = AutoReplyStore(tmp_path / "outer.db")
    store.enqueue_reply_task(
        conversation_id="chat", conversation_title="Conversation", single_chat=False,
        trigger_message_id="message", trigger_create_time=NOW.isoformat(),
        trigger_sender="Derek", trigger_text="Please check.",
    )
    public_work_id = 17
    with store._connect() as db:
        parts = task8_insert_parts("work_summary_inputs", {
            "legacy_key": public_work_id, "source_type": "reply_attempt",
            "source_ref": "one", "payload_json": '{"summary":"Work summary"}',
        })
        physical_work_id = db.execute(
            f"insert into tasks ({parts.columns}) values ({parts.values})",
            parts.parameters,
        ).lastrowid
    with store._connect() as db:
        assert physical_work_id != public_work_id
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
        parts = task8_set_parts("work_summary_inputs", {
            "status": "failed", "error": "failed work",
        })
        db.execute(
            f"update tasks set {parts.sql} where id=?",
            (*parts.parameters, physical_work_id),
        )
    rows = _queue_attention_rows(store)
    work_row = next(item for item in rows if item["category"] == "Work item")
    assert work_row["id"] == str(public_work_id)
    assert work_row["error"] == "failed work"


def test_public_work_id_keeps_codec_and_terminal_provenance(tmp_path):
    import hashlib
    import json

    from app.storage_maintenance import compact_terminal_work_summary_inputs
    from app.task8_storage import task8_insert_parts
    from app.work_summary_payload import decode_work_summary_payload, migrate_work_summary_payloads

    store = AutoReplyStore(tmp_path / "codec.db")
    payload = json.dumps({
        "source": {"created_at": "2026-10-01T00:00:00Z"},
        "summary": "repeated text " * 200,
    })
    with store._connect() as db:
        pending = task8_insert_parts("work_summary_inputs", {
            "legacy_key": 17, "source_type": "test", "source_ref": "pending",
            "payload_json": payload,
        })
        pending_physical = db.execute(
            f"insert into tasks ({pending.columns}) values ({pending.values})",
            pending.parameters,
        ).lastrowid
        done = task8_insert_parts("work_summary_inputs", {
            "legacy_key": 18, "source_type": "test", "source_ref": "done",
            "payload_json": payload, "status": "done",
        })
        done_physical = db.execute(
            f"insert into tasks ({done.columns}) values ({done.values})",
            done.parameters,
        ).lastrowid
        assert pending_physical not in (17, 18)
        assert done_physical not in (17, 18)
    compressed = migrate_work_summary_payloads(store.path)
    assert compressed["compressed"] == 1
    with store._connect() as db:
        stored = db.execute(
            f"select payload_json from ({WORK_SUMMARY_INPUT_SQL}) where id=17"
        ).fetchone()[0]
        assert isinstance(stored, bytes)
        assert decode_work_summary_payload(stored) == payload
    compacted = compact_terminal_work_summary_inputs(store.path)
    assert compacted["rows_compacted"] == 1
    with store._connect() as db:
        row = db.execute(f"select * from ({WORK_SUMMARY_INPUT_SQL}) where id=18").fetchone()
        assert row["payload_json"] == "{}"
        assert row["source_created_at"] == "2026-10-01T00:00:00Z"
        assert row["body_sha256"] == hashlib.sha256(payload.encode()).hexdigest()
        assert row["body_bytes"] == len(payload.encode())
        assert row["body_compacted"] == 1


