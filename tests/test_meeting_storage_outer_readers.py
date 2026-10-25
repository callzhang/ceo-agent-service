"""Meeting outer readers use typed physical storage and public queue IDs."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from app.dispatcher.adapters import MeetingQueueAdapter
from app.error_code_migration import MEETING_JOB_SQL as MIGRATION_MEETING_JOB_SQL, migrate_legacy_meeting_projections
from app.quality_gate import _check_meetings
from app.repository_updater import in_flight_work
from app.audit_web import _queue_attention_rows, _queue_status_snapshots
from app.action_storage import insert_task_delivery_action, task_delivery_projection
from app.store import AutoReplyStore
from app.task_entity_storage import update_task_cas


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def _job(store, public_id=700, status="pending"):
    return store.upsert_meeting_alignment_job(
        meeting_id=f"meeting-{public_id}", title="Review", source_json="{}",
        participants_json="[]", ended_at="2026-10-11 00:00:00",
        eligible_at="2026-10-11 00:00:00", status=status,
    )


def test_meeting_dispatcher_uses_public_key_and_stale_generation(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    task_id = _job(store)
    adapter = MeetingQueueAdapter(store, owner_alive=lambda pid: False)
    before = adapter.metrics(NOW)
    assert before.pending == 1 and before.due == 1
    first = adapter.claim(NOW, owner="first", owner_pid=999999, lease=timedelta(seconds=1))
    assert first is not None and first.source_id == str(task_id)
    with store._connect() as db:
        assert db.execute("select source_id from state where record_type='dispatcher_claim_leases' and adapter_name='meeting'").fetchone()[0] == str(task_id)
    adapter.renew(first, owner="first", now=NOW, lease=timedelta(seconds=1))
    second = adapter.claim(NOW + timedelta(seconds=2), owner="second", owner_pid=999998, lease=timedelta(minutes=1))
    assert second is not None and second.source_id == str(task_id)
    assert second.generation > first.generation
    with pytest.raises(ValueError):
        adapter.release(first, owner="first", now=NOW + timedelta(seconds=2))
    adapter.release(second, owner="second", now=NOW + timedelta(seconds=2))
    assert store.get_meeting_alignment_job(task_id).status == "retry"


def test_current_meeting_claim_counts_as_in_flight(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    _job(store)
    now = datetime.now(timezone.utc)
    claim = MeetingQueueAdapter(store).claim(now, owner="current", lease=timedelta(minutes=5))
    assert claim is not None and in_flight_work(store.path) > 0


def test_current_meeting_quiet_count_follows_native_task_parent(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    task_id = _job(store, status="processing")
    store.create_professional_run(task_id, status="running")
    assert in_flight_work(store.path) > 0


def test_quality_meeting_projection_reads_only_meeting_type(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    _job(store, status="failed")
    with store._connect() as db:
        violations, attention = [], []
        _check_meetings(db, NOW, violations, attention)
    assert [(issue.source, issue.code, issue.count) for issue in violations] == [
        ("meeting_alignment_jobs", "failed", 1)]


def test_attention_status_and_memory_child_use_physical_meeting_parent(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    physical = _job(store, status="failed")
    store.enqueue_reply_task(
        conversation_id="decoy", conversation_title="Wrong type", single_chat=False,
        trigger_message_id="decoy-msg", trigger_create_time="2026-10-11 00:00:00",
        trigger_sender="sender", trigger_text="decoy",
    )
    with store._connect() as db:
        reply_id = db.execute(
            "select id from tasks where kind='reply' and status='pending'"
        ).fetchone()[0]
        update_task_cas(db, task_id=physical, expected={"status": "failed"},
                        changes={"error": "meeting failed"})
        insert_task_delivery_action(db, "meeting_memory_write_events", {
            "meeting_job_id": physical, "payload_json": "{}", "status": "failed",
            "error": "memory failed", "execution_generation": "",
        })
        with pytest.raises(sqlite3.IntegrityError, match="meeting job"):
            insert_task_delivery_action(db, "meeting_memory_write_events", {
                "meeting_job_id": reply_id, "payload_json": "{}", "status": "failed",
                "error": "wrong type", "execution_generation": "",
            })
    snapshots = _queue_status_snapshots(store)
    meeting = next(row for row in snapshots if row["name"] == "Meeting jobs")
    assert meeting["failed"] == 1 and meeting["latest_error"] == "meeting failed"
    attention = _queue_attention_rows(store)
    assert any(row["category"] == "Meeting" and row["id"] == str(physical) for row in attention)
    assert not any(row.get("root_cause") == "meeting_memory_write_failed" for row in attention)
    with store._connect() as db:
        historical = db.execute(
            f"select id,error from ({task_delivery_projection('meeting_memory_write_events')}) "
            "where meeting_job_id=?", (physical,),
        ).fetchone()
    assert historical is not None and historical[1] == "memory failed"


def test_explicit_error_migration_changes_typed_projection_only(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    physical = _job(store, status="failed")
    original = json.dumps({"kind": "meeting_agent", "message": "runtime_effect_policy_violation"})
    with store._connect() as db:
        update_task_cas(db, task_id=physical, expected={"status": "failed"},
                        changes={"error": original})
    assert migrate_legacy_meeting_projections(store).changed == 1
    with store._connect() as db:
        assert db.execute(f"select error from ({MIGRATION_MEETING_JOB_SQL}) where id=?", (physical,)).fetchone()[0] == original
    assert migrate_legacy_meeting_projections(store, dry_run=False).changed == 1
    with store._connect() as db:
        changed = json.loads(db.execute(f"select error from ({MIGRATION_MEETING_JOB_SQL}) where id=?", (physical,)).fetchone()[0])
    assert changed["code"] == "runtime_execution_failed"
