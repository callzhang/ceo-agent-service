"""Meeting outer readers use typed physical storage and public queue IDs."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest

from app.dispatcher.adapters import MeetingQueueAdapter
from app.quality_gate import _check_meetings
from app.repository_updater import in_flight_work
from app.audit_web import _queue_attention_rows, _queue_status_snapshots
from app.action_storage import insert_task_delivery_action
from app.error_code_migration import migrate_legacy_meeting_projections
from app.store import AutoReplyStore
from app.task_schema_release import publication_meeting_sources
from app.task_storage import MEETING_JOB_SQL
from app.task20_definitions import task20_projection
from app.task8_storage import task8_insert_parts, task8_set_parts
from app.store import MEETING_LOGICAL_RUN_SQL
from tests.test_meeting_task_storage import forward, meeting_source


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def _job(store, public_id=700, status="pending"):
    with store._connect() as db:
        parts = task8_insert_parts("meeting_alignment_jobs", {
            "legacy_key": public_id, "meeting_id": f"meeting-{public_id}",
            "title": "Review", "ended_at": "2026-10-11 00:00:00",
            "eligible_at": "2026-10-11 00:00:00", "status": status,
        })
        cursor = db.execute(
            f"insert into tasks ({parts.columns}) values ({parts.values})", parts.parameters,
        )
        return int(cursor.lastrowid)


def test_meeting_dispatcher_uses_public_key_and_stale_generation(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    physical = _job(store)
    assert physical != 700
    adapter = MeetingQueueAdapter(store, owner_alive=lambda pid: False)
    before = adapter.metrics(NOW)
    assert before.pending == 1 and before.due == 1
    first = adapter.claim(NOW, owner="first", owner_pid=999999, lease=timedelta(seconds=1))
    assert first is not None and first.source_id == "700"
    with store._connect() as db:
        assert db.execute("select source_id from state where record_type='dispatcher_claim_leases' and adapter_name='meeting'").fetchone()[0] == "700"
    adapter.renew(first, owner="first", now=NOW, lease=timedelta(seconds=1))
    second = adapter.claim(NOW + timedelta(seconds=2), owner="second", owner_pid=999998, lease=timedelta(minutes=1))
    assert second is not None and second.source_id == "700"
    assert second.generation > first.generation
    with pytest.raises(ValueError):
        adapter.release(first, owner="first", now=NOW + timedelta(seconds=2))
    adapter.release(second, owner="second", now=NOW + timedelta(seconds=2))
    assert store.get_meeting_alignment_job(700).status == "retry"


def test_current_meeting_claim_counts_as_in_flight(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    _job(store)
    now = datetime.now(timezone.utc)
    claim = MeetingQueueAdapter(store).claim(now, owner="current", lease=timedelta(minutes=5))
    assert claim is not None and in_flight_work(store.path) > 0


def test_old_and_current_meeting_quiet_count_follow_public_job_parent(tmp_path):
    path = tmp_path / "source.db"
    with meeting_source(path) as db:
        db.execute("create table dispatcher_claim_leases(adapter_name text,source_id text,terminal_at text,lease_expires_at text)")
        db.execute("create table scheduled_task_runs(dispatch_status text,lease_owner text,lease_expires_at text)")
        db.execute("update meeting_alignment_jobs set status='processing' where id=1")
        db.execute("update meeting_alignment_runs set status='running' where id=1")
        db.commit()
    assert in_flight_work(path) > 0
    with sqlite3.connect(path) as db:
        forward(db)
    assert in_flight_work(path) > 0


def test_meeting_publication_sources_accept_exact_old_and_current(tmp_path):
    path = tmp_path / "source.db"
    with meeting_source(path) as db:
        assert publication_meeting_sources(db) == (
            "select * from meeting_alignment_jobs", "select * from meeting_alignment_runs")
        db.row_factory = sqlite3.Row
        assert publication_meeting_sources(db) == (
            "select * from meeting_alignment_jobs", "select * from meeting_alignment_runs")
        db.row_factory = None
        forward(db)
        assert publication_meeting_sources(db) == (MEETING_JOB_SQL, MEETING_LOGICAL_RUN_SQL)
        db.row_factory = sqlite3.Row
        assert publication_meeting_sources(db) == (MEETING_JOB_SQL, MEETING_LOGICAL_RUN_SQL)
        db.execute("update service_state set value='unknown' where key='store_schema_version'")
        with pytest.raises(ValueError, match="unknown"):
            publication_meeting_sources(db)
        db.execute("update service_state set value='2026-10-11.3' where key='store_schema_version'")
        db.execute("create table meeting_alignment_jobs (id integer)")
        with pytest.raises(ValueError, match="mixed"):
            publication_meeting_sources(db)


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
            f"select id from ({task20_projection('reply_tasks')}) where conversation_id='decoy'"
        ).fetchone()[0]
        parts = task8_set_parts("meeting_alignment_jobs", {"error": "meeting failed"})
        db.execute(f"update tasks set {parts.sql} where id=?", (*parts.parameters, physical))
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
    assert any(row["category"] == "Meeting" and row["id"] == "700" for row in attention)
    memory = [row for row in attention if row.get("root_cause") == "meeting_memory_write_failed"]
    assert len(memory) == 1 and memory[0]["context"] == "Meeting Memory: Review"


def test_explicit_error_migration_changes_typed_projection_only(tmp_path):
    store = AutoReplyStore(tmp_path / "store.db")
    physical = _job(store, status="failed")
    original = json.dumps({"kind": "meeting_agent", "message": "runtime_effect_policy_violation"})
    with store._connect() as db:
        parts = task8_set_parts("meeting_alignment_jobs", {"error": original})
        db.execute(f"update tasks set {parts.sql} where id=?", (*parts.parameters, physical))
    assert migrate_legacy_meeting_projections(store).changed == 1
    with store._connect() as db:
        assert db.execute(f"select error from ({task20_projection('meeting_alignment_jobs')}) where id=?", (700,)).fetchone()[0] == original
    assert migrate_legacy_meeting_projections(store, dry_run=False).changed == 1
    with store._connect() as db:
        changed = json.loads(db.execute(f"select error from ({task20_projection('meeting_alignment_jobs')}) where id=?", (700,)).fetchone()[0])
    assert changed["code"] == "runtime_execution_failed"
