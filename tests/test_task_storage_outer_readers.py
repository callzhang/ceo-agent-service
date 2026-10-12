"""Outer task readers keep public identities after typed storage publication."""

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from app.audit_web import _queue_attention_rows, _queue_status_snapshots
from app.dispatcher.adapters import ReplyQueueAdapter, WorkSummaryQueueAdapter
from app.repository_updater import in_flight_work
from app.store import AutoReplyStore
from app.task_schema_release import publication_task_sources
from app.task_storage import REPLY_TASK_SQL, WORK_SUMMARY_INPUT_SQL, migrate_task_schema
from tests.test_task_storage import source_database


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("row_factory", [None, sqlite3.Row])
def test_publication_quiet_sources_read_exact_source_and_target(tmp_path, row_factory):
    path = tmp_path / "source.db"
    source_database(path).close()
    with sqlite3.connect(path) as db:
        db.row_factory = row_factory
        db.executescript("""
            create table dispatcher_claim_leases(adapter_name text,source_id text,terminal_at text,lease_expires_at text);
            create table scheduled_task_runs(dispatch_status text,lease_owner text,lease_expires_at text);
            update work_summary_inputs set status='processing' where id=2;
        """)
        db.commit()
        assert in_flight_work(path) == 1
        reply_sql, work_sql = publication_task_sources(db)
        assert db.execute(f"select count(*) from ({reply_sql}) where status='processing'").fetchone()[0] == 1
        assert db.execute(f"select count(*) from ({work_sql}) where status='processing'").fetchone()[0] == 1
        db.execute("pragma foreign_keys=off")
        db.execute("begin immediate")
        migrate_task_schema(db)
        db.execute("update service_state set value='2026-10-11.3' where key='store_schema_version'")
        db.commit()
        assert in_flight_work(path) == 1
        reply_sql, work_sql = publication_task_sources(db)
        assert reply_sql == REPLY_TASK_SQL
        assert "record_type='work_summary_inputs'" in work_sql
        assert db.execute(f"select count(*) from ({reply_sql}) where status='processing'").fetchone()[0] == 1
        assert db.execute(f"select count(*) from ({work_sql}) where status='processing'").fetchone()[0] == 1
        db.execute("create table reply_tasks(id integer)")
        with pytest.raises(ValueError, match="mixed"):
            publication_task_sources(db)


def test_dispatcher_and_status_use_public_work_item_id(tmp_path):
    store = AutoReplyStore(tmp_path / "outer.db")
    store.enqueue_reply_task(
        conversation_id="chat", conversation_title="Conversation", single_chat=False,
        trigger_message_id="message", trigger_create_time=NOW.isoformat(),
        trigger_sender="Derek", trigger_text="Please check.",
    )
    public_work_id = 17
    with store._connect() as db:
        from app.task_storage import insert_task
        insert_task(db, "work_summary_inputs", {"legacy_key": public_work_id, "source_type": "reply_attempt", "source_ref": "one", "payload_json": '{"summary":"Work summary"}'})
    with store._connect() as db:
        physical_work_id = db.execute(
            "select id from tasks where record_type='work_summary_inputs' and cast(legacy_key as integer)=?",
            (public_work_id,),
        ).fetchone()[0]
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
        assert db.execute("select status from tasks where id=?", (physical_work_id,)).fetchone()[0] == "processing"
    work.release(work_claim, owner="work-owner", now=NOW)
    with store._connect() as db:
        assert db.execute("select status from tasks where id=?", (physical_work_id,)).fetchone()[0] == "pending"
    snapshots = _queue_status_snapshots(store)
    work_snapshot = next(item for item in snapshots if item["name"] == "Work items")
    assert work_snapshot["pending"] == 1
    assert work_snapshot["processing"] == 0
    with store._connect() as db:
        db.execute("update tasks set status='failed',error='failed work' where id=?", (physical_work_id,))
    rows = _queue_attention_rows(store)
    work_row = next(item for item in rows if item["category"] == "Work item")
    assert work_row["id"] == str(public_work_id)
    assert work_row["error"] == "failed work"


def test_public_work_id_keeps_codec_and_terminal_provenance(tmp_path):
    import hashlib
    import json

    from app.storage_maintenance import compact_terminal_work_summary_inputs
    from app.task_storage import insert_task
    from app.work_summary_payload import decode_work_summary_payload, migrate_work_summary_payloads

    store = AutoReplyStore(tmp_path / "codec.db")
    payload = json.dumps({
        "source": {"created_at": "2026-10-01T00:00:00Z"},
        "summary": "repeated text " * 200,
    })
    with store._connect() as db:
        pending_physical = insert_task(db, "work_summary_inputs", {
            "legacy_key": 17, "source_type": "test", "source_ref": "pending",
            "payload_json": payload,
        }).lastrowid
        done_physical = insert_task(db, "work_summary_inputs", {
            "legacy_key": 18, "source_type": "test", "source_ref": "done",
            "payload_json": payload, "status": "done",
        }).lastrowid
        assert pending_physical not in (17, 18)
        assert done_physical not in (17, 18)
    compressed = migrate_work_summary_payloads(store.path)
    assert compressed["compressed"] == 1
    with store._connect() as db:
        stored = db.execute("select payload_json from tasks where id=?", (pending_physical,)).fetchone()[0]
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
