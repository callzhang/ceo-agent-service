"""Outer task readers keep public identities after typed storage publication."""

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from app.audit_web import _queue_attention_rows, _queue_status_snapshots
from app.dispatcher.adapters import ReplyQueueAdapter, WorkSummaryQueueAdapter
from app.repository_updater import in_flight_work
from app.store import AutoReplyStore, REPLY_TASK_SQL, WORK_SUMMARY_INPUT_SQL
from app.task_schema_release import publication_task_sources
from app.task8_storage import task8_insert_parts, task8_set_parts
from app.task_storage import migrate_task_schema
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


@pytest.mark.parametrize("row_factory", [None, sqlite3.Row])
def test_current14_quiet_readers_are_read_only_and_preserve_busy(tmp_path, row_factory):
    from app.task_schema_release import publication_meeting_sources
    from app.scheduled_config_storage import publication_scheduled_source
    from tests.test_scheduled_task_store import _create_task, NOW as CRON_NOW

    store = AutoReplyStore(tmp_path / "quiet14.db")
    task = _create_task(store)
    run = store.create_scheduled_task_run(
        task.id, trigger_kind="manual", scheduled_for=CRON_NOW
    )
    snapshot_before = store.path.read_bytes()
    assert in_flight_work(store.path) == 0
    assert store.path.read_bytes() == snapshot_before
    with sqlite3.connect(store.path) as db:
        db.row_factory = row_factory
        before = db.total_changes
        statements = []
        db.set_trace_callback(statements.append)
        reply_sql, _ = publication_task_sources(db)
        publication_meeting_sources(db)
        scheduled_sql = publication_scheduled_source(db)
        assert reply_sql == REPLY_TASK_SQL
        assert db.execute(f"select id from ({scheduled_sql})").fetchone()[0] == run.id
        assert db.total_changes == before
        assert not any(
            sql.lower().startswith(("insert", "update", "delete", "begin"))
            for sql in statements
        )
        db.execute(
            "update runs set lease_owner='worker',lease_expires_at='2999-01-01T00:00:00+00:00' where record_type='scheduled_task_runs'"
        )
        db.commit()
        snapshot_before = store.path.read_bytes()
        assert in_flight_work(store.path) == 1
        assert store.path.read_bytes() == snapshot_before
        db.execute("create table scheduled_task_runs(id integer)")
        for reader in (
            publication_task_sources,
            publication_meeting_sources,
            publication_scheduled_source,
        ):
            with pytest.raises(ValueError, match="mixed"):
                reader(db)

@pytest.mark.parametrize("version", ["unknown", "2026-10-12.2"])
def test_scheduled_quiet_rejects_unknown_marker(tmp_path, version):
    from app.scheduled_config_storage import publication_scheduled_source
    from app.state_storage import update_state
    from app.state_definitions import state_projection

    store = AutoReplyStore(tmp_path / "unknown.db")
    with store._connect() as db:
        marker_id = db.execute(
            f"select rowid from ({state_projection('service_state', include_rowid=True)}) where key='store_schema_version'"
        ).fetchone()[0]
        db.execute("begin")
        update_state(db, "service_state", marker_id, {"value": version})
        with pytest.raises(ValueError, match="unknown"):
            publication_scheduled_source(db)


@pytest.mark.parametrize("version", ["2026-10-11.2", "2026-10-11.3"])
@pytest.mark.parametrize("row_factory", [None, sqlite3.Row])
@pytest.mark.parametrize("has_scheduled_row", [False, True])
def test_source_marker_cannot_hide_typed_scheduled_layout(tmp_path, version, row_factory, has_scheduled_row):
    from app.scheduled_config_storage import publication_scheduled_source
    from app.state_storage import update_state
    from app.state_definitions import state_projection
    from tests.test_scheduled_task_store import _create_task, NOW as CRON_NOW

    store = AutoReplyStore(tmp_path / "mixed-scheduled.db")
    if has_scheduled_row:
        task = _create_task(store)
        store.create_scheduled_task_run(task.id, trigger_kind="manual", scheduled_for=CRON_NOW)
        with store._connect() as db:
            db.execute("update runs set lease_owner='worker',lease_expires_at='2999-01-01T00:00:00+00:00' where record_type='scheduled_task_runs'")
        assert in_flight_work(store.path) == 1
    with sqlite3.connect(store.path) as db:
        db.row_factory = row_factory
        marker_id = db.execute(
            f"select rowid from ({state_projection('service_state', include_rowid=True)}) where key='store_schema_version'"
        ).fetchone()[0]
        db.execute("begin")
        update_state(db, "service_state", marker_id, {"value": version})
        db.execute("create table scheduled_task_runs(dispatch_status text,lease_owner text,lease_expires_at text)")
        db.commit()
        before_changes = db.total_changes
        before_dump = tuple(db.iterdump())
        before_bytes = store.path.read_bytes()
        statements = []
        db.set_trace_callback(statements.append)
        with pytest.raises(ValueError, match="mixed"):
            publication_scheduled_source(db)
        with pytest.raises(ValueError, match="mixed"):
            in_flight_work(store.path)
        assert db.total_changes == before_changes
        assert tuple(db.iterdump()) == before_dump
        assert store.path.read_bytes() == before_bytes
        assert not any(sql.lower().startswith(("insert", "update", "delete", "begin")) for sql in statements)


@pytest.mark.parametrize("version", ["2026-10-11.2", "2026-10-11.3"])
@pytest.mark.parametrize("row_factory", [None, sqlite3.Row])
def test_actual_scheduled_source_layout_remains_busy_and_read_only(tmp_path, version, row_factory):
    from app.scheduled_config_storage import publication_scheduled_source
    from tests.workbench_schema_fixtures import SOURCE, source_database as source13

    path = tmp_path / "actual-source.db"
    if version == "2026-10-11.3":
        db = source13(path)
    else:
        db = source_database(path)
        for table in ("scheduled_tasks", "scheduled_task_config_versions", "scheduled_task_runs"):
            for kind, _, sql in SOURCE["tables"][table]["objects"]:
                if kind == "table":
                    db.execute(sql)
            for kind, _, sql in SOURCE["tables"][table]["objects"]:
                if kind != "table" and sql:
                    db.execute(sql)
    with db:
        db.execute("insert into scheduled_task_runs(event_id,scheduled_task_id,trigger_kind,scheduled_for,lease_owner,lease_expires_at) values('live',7,'manual','2026-10-12T00:00:00+00:00','worker','2999-01-01T00:00:00+00:00')")
    db.row_factory = row_factory
    before_dump = tuple(db.iterdump())
    before_bytes = path.read_bytes()
    before_changes = db.total_changes
    statements = []
    db.set_trace_callback(statements.append)
    sql = publication_scheduled_source(db)
    assert db.execute(f"select count(*) from ({sql}) where dispatch_status='pending' and lease_owner<>'' and julianday(lease_expires_at)>julianday('now')").fetchone()[0] == 1
    assert db.total_changes == before_changes
    assert tuple(db.iterdump()) == before_dump
    assert path.read_bytes() == before_bytes
    assert not any(sql.lower().startswith(("insert", "update", "delete", "begin")) for sql in statements)
    db.close()
