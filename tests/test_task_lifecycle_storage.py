"""Source-field ownership, without provider or runtime effects."""
from contextlib import closing
import sqlite3

from app.task_lifecycle_current_schema import create_current_task20_schema
from app.task8_storage import register_task8_functions, task8_insert_parts
import pytest


@pytest.mark.parametrize("size", [0, 11, 12, 250, 255, 256, 65530, 65535, 65536])
def test_native_jsonb_cells_and_array_headers_survive_stepwise_updates(size):
    from app import task20_definitions as source
    from app.task_lifecycle_schema import migrate_task_lifecycle, _inverse_payload
    from app.task_lifecycle_storage import encoded
    from app.task8_storage import task8_set_parts
    from app.state_storage import create_state_schema
    from tests.test_task20_definitions import insert, payload

    kind = "okr_review_requests"
    with closing(sqlite3.connect(":memory:")) as old, closing(sqlite3.connect(":memory:")) as new:
        for db in (old, new):
            register_task8_functions(db)
            source.create_task20_schema(db)
            create_state_schema(db)
            insert(db, kind, values=payload(db, kind, status="processing",
                trigger_text="x" * size, error=b"\x00\xff", created_at="source-clock", updated_at=-0.0))
            db.execute("update tasks set payload_json=jsonb_set(payload_json,?,json(?))",
                       (source.TASK20_LAYOUTS[kind].path("status"), '"failed"'))
        original = old.execute("select payload_json from tasks").fetchone()[0]
        migrate_task_lifecycle(new)
        assert new.execute("select " + _inverse_payload(kind) + " from tasks").fetchone()[0] == original
        assert new.execute("select typeof(error),lifecycle_error,lifecycle_updated_at,attempts "
                           "from tasks").fetchone() == ("blob", b"\x00\xff", "0.0", None)
        for changes in (
            {"status": "processing", "trigger_text": "y" * (size + 257)},
            {"status": "failed", "trigger_text": "", "error": ""},
            {"status": "pending", "trigger_text": "z" * size, "error": -0.0},
            {"status": "processing", "error": 1.125},
            {"status": "done", "trigger_text": "", "error": b""},
        ):
            update = source.TASK20_LAYOUTS[kind].assignments_sql(
                "payload_json", dict.fromkeys(changes, "?"))
            old.execute("update tasks set payload_json=" + update, tuple(map(encoded, changes.values())))
            parts = task8_set_parts(kind, changes)
            new.execute("update tasks set " + parts.sql, parts.parameters)
            assert new.execute("select " + _inverse_payload(kind) + " from tasks").fetchone()[0] == (
                old.execute("select payload_json from tasks").fetchone()[0])


def test_executable_lifecycle_has_one_physical_owner():
    with closing(sqlite3.connect(":memory:")) as db:
        register_task8_functions(db)
        create_current_task20_schema(db)
        physical = {row[1] for row in db.execute("pragma table_xinfo(tasks)") if not row[6]}
        expected = {
            "status", "available_at", "attempts", "error", "created_at", "updated_at",
            "locked_at", "lease_owner", "lease_expires_at", "owner_pid", "generation",
            "adapter_name",
        }
        assert expected <= physical
        parts = task8_insert_parts("work_summary_inputs", {
            "source_type": "report", "source_ref": "source:1", "payload_json": "{}",
        })
        db.execute(f"insert into tasks({parts.columns}) values({parts.values})", parts.parameters)
        row = db.execute(
            "select lifecycle_status,lifecycle_attempts,lifecycle_available_at,"
            "lifecycle_locked_at,lifecycle_lease_owner,lifecycle_generation "
            "from tasks"
        ).fetchone()
        assert row == ("pending", 0, "", None, None, None)


def test_current_data_inverse_preserves_exact_source_scalars_and_passive_ledger():
    from app.task20_definitions import create_task20_schema
    from app.state_storage import create_state_schema, insert_state
    from tests.test_task20_definitions import insert, payload
    from app.task_lifecycle_schema import migrate_task_lifecycle, restore_task_lifecycle
    with closing(sqlite3.connect(":memory:")) as db:
        register_task8_functions(db)
        create_task20_schema(db)
        create_state_schema(db)
        values = payload(db, "reply_tasks", status="pending", error=b"\x00\xff",
                         attempts=-0.0, updated_at=b"clock")
        insert(db, "reply_tasks", physical_id=7, legacy_key="7", values=values)
        insert_state(db, "dispatcher_claim_leases", {
            "adapter_name": "reply", "source_id": "7", "owner": "",
            "owner_pid": 123, "generation": 4, "lease_expires_at": "",
            "terminal_at": "old-terminal", "last_error": "old-error",
            "created_at": b"ledger-created", "updated_at": b"ledger-updated",
        }, source_rowid=-31)
        original = db.execute("select * from tasks").fetchone()
        ledger = db.execute("select * from state").fetchall()
        waters = db.execute("select rowid,name,seq from sqlite_sequence").fetchall()
        metadata = migrate_task_lifecycle(db)
        assert db.execute(
            "select lifecycle_adapter_name,lifecycle_generation,lifecycle_lease_owner,"
            "lifecycle_owner_pid from tasks"
        ).fetchone() == ("reply", 4, "", 123)
        assert db.execute("select * from state").fetchall() == ledger
        restore_task_lifecycle(db, metadata)
        assert db.execute("select * from tasks").fetchone() == original
        assert db.execute("select * from state").fetchall() == ledger
        assert db.execute("select rowid,name,seq from sqlite_sequence").fetchall() == waters


def test_common_reads_keep_exact_source_public_affinities():
    from app.task20_definitions import create_task20_schema, task20_projection
    from app.state_storage import create_state_schema, insert_state
    from tests.test_task20_definitions import insert, payload
    from app.task_lifecycle_schema import migrate_task_lifecycle
    with closing(sqlite3.connect(":memory:")) as db:
        create_task20_schema(db)
        create_state_schema(db)
        insert(db, "reply_tasks", values=payload(db, "reply_tasks",
            error=1.125, created_at=10, updated_at=-0.0, available_at=22, attempts=-0.0))
        insert_state(db, "dispatcher_claim_leases", {
            "adapter_name": "reply", "source_id": "1", "owner": 1.125,
            "owner_pid": 7.0, "generation": "4", "lease_expires_at": 22})
        task_fields = ("error", "created_at", "updated_at", "available_at", "attempts")
        expected_task = db.execute("select " + ",".join(task_fields) + " from ("
                                  + task20_projection("reply_tasks") + ")").fetchone()
        expected_claim = db.execute("select owner,owner_pid,generation,lease_expires_at from state "
                                   "where record_type='dispatcher_claim_leases'").fetchone()
        migrate_task_lifecycle(db)
        actual_task = db.execute("select " + ",".join("lifecycle_" + f for f in task_fields) + " from tasks").fetchone()
        actual_claim = db.execute("select lifecycle_lease_owner,lifecycle_owner_pid,lifecycle_generation,"
                                 "lifecycle_lease_expires_at from tasks").fetchone()
        assert [(type(v), v) for v in actual_task] == [(type(v), v) for v in expected_task]
        assert [(type(v), v) for v in actual_claim] == [(type(v), v) for v in expected_claim]
        assert db.execute("select 1 from tasks where lifecycle_lease_owner=? and lifecycle_generation=?",
                          (expected_claim[0], expected_claim[2])).fetchone() == (1,)


@pytest.mark.parametrize("width", [3, 5, 9])
def test_nonminimal_array_root_and_explicit_null_are_not_missing(width):
    from app import task20_definitions as source
    from app.task_lifecycle_jsonb import _array
    from app.task_lifecycle_schema import migrate_task_lifecycle, restore_task_lifecycle
    from app.state_storage import create_state_schema
    from tests.test_task20_definitions import insert
    with closing(sqlite3.connect(":memory:")) as db:
        register_task8_functions(db)
        source.create_task20_schema(db)
        create_state_schema(db)
        insert(db, "reply_tasks")
        raw = db.execute("select payload_json from tasks").fetchone()[0]
        _, cells = _array(raw)
        body = b"".join(cells)
        raw = bytes([((12 + (width - 1).bit_length() - 1) << 4) | 11]) + len(body).to_bytes(width - 1, "big") + body
        db.execute("update tasks set payload_json=?", (raw,))
        assert db.execute("select ceo_task_cell(jsonb('[null]'),0),ceo_task_cell(jsonb('[null]'),1)").fetchone() == (b"\x00", None)
        metadata = migrate_task_lifecycle(db)
        assert db.execute("select locked_at,lease_owner from tasks").fetchone() == (b"\x00", None)
        restore_task_lifecycle(db, metadata)
        assert db.execute("select payload_json from tasks").fetchone()[0] == raw


@pytest.mark.parametrize("kind,source_status,generic", [
    ("meeting_alignment_jobs", "ready_to_send", "processing"),
    ("meeting_alignment_jobs", "quarantined", "quarantined"),
    ("weekly_okr_analysis_jobs", "running", "processing"),
    ("wechat_memory_import_jobs", "completed", "done"),
    ("feedback_processing_items", "resolved", "done"),
    ("email_agent_classification_tasks", "running", "processing"),
])
def test_generic_status_has_only_one_source_status_owner(kind, source_status, generic):
    from tests.test_task20_definitions import payload
    from app.task12_storage import insert_task12
    from app.task20_definitions import task20_column, TASK20_NEW_KINDS
    with closing(sqlite3.connect(":memory:")) as db:
        register_task8_functions(db)
        create_current_task20_schema(db)
        values = payload(db, kind, status=source_status)
        if kind in TASK20_NEW_KINDS:
            insert_task12(db, kind, values)
        else:
            parts = task8_insert_parts(kind, values)
            db.execute(f"insert into tasks({parts.columns}) values({parts.values})", parts.parameters)
        row = db.execute("select lifecycle_status," + task20_column(kind, "status") + " from tasks").fetchone()
        assert row == (generic, source_status)


def test_dispatch_claim_fences_and_renewal_use_task_owner(tmp_path):
    from datetime import datetime, timedelta, timezone
    from app.store import AutoReplyStore
    from app.dispatcher.adapters import ReplyQueueAdapter
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.enqueue_reply_task(conversation_id="chat", conversation_title="Chat", single_chat=True,
        trigger_message_id="trigger", trigger_create_time="time", trigger_sender="sender",
        trigger_text="source")
    adapter = ReplyQueueAdapter(store, owner_alive=lambda _: False)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    first = adapter.claim(now, owner="first", owner_pid=17, lease=timedelta(minutes=5))
    assert first is not None
    with store._immediate_write_transaction() as db:
        assert db.execute("select lifecycle_lease_owner,lifecycle_generation from tasks "
                          "where record_type='reply_tasks'").fetchone()[:] == ("first", first.generation)
        db.execute("update tasks set lease_owner=jsonb('\"new-owner\"') "
                   "where record_type='reply_tasks'")
    # A stale passive ledger cannot authorize a claim whose Task owner changed.
    with pytest.raises(ValueError, match="no longer owned"):
        adapter.assert_current(first, owner="first", now=now + timedelta(seconds=1))
    with store._immediate_write_transaction() as db:
        db.execute("update tasks set lease_owner=jsonb('\"first\"') "
                   "where record_type='reply_tasks'")
    adapter.renew(first, owner="first", now=now + timedelta(seconds=1), lease=timedelta(minutes=5))
    adapter.assert_current(first, owner="first", now=now + timedelta(seconds=2))
    adapter.release(first, owner="first", now=now + timedelta(seconds=3))
    assert store.get_reply_task(int(first.source_id)).attempts == 0
    with pytest.raises(ValueError, match="no longer owned"):
        adapter.renew(first, owner="first", now=now + timedelta(seconds=4), lease=timedelta(minutes=5))


def test_all_twenty_kinds_exact_bytes_and_only_nine_lifecycles():
    from app import task20_definitions as old
    from app.task_lifecycle_definitions import SOURCE_FIELDS, LIFECYCLE_FIELDS
    from app.task_lifecycle_schema import migrate_task_lifecycle, restore_task_lifecycle, _inverse_payload
    from app.state_storage import create_state_schema, insert_state
    from tests.test_task20_definitions import insert, payload
    with closing(sqlite3.connect(":memory:")) as db:
        register_task8_functions(db)
        old.create_task20_schema(db)
        create_state_schema(db)
        insert(db, "reply_tasks", physical_id=1, legacy_key="1")
        insert(db, "workbench_tasks", physical_id=2, legacy_key="text-task")
        insert(db, "workbench_turns", physical_id=3, legacy_key="text-turn", parent_id=2)
        insert(db, "feedback_processing_batches", physical_id=4, legacy_key="7")
        for kind in old.TASK20_COLUMNS:
            if kind in ("reply_tasks", "workbench_tasks", "workbench_turns", "feedback_processing_batches"):
                continue
            parents = {"reply_task_inputs": 1, "business_object_tasks": 1,
                       "workbench_artifacts": 3, "workbench_attachments": 2,
                       "feedback_iteration_decisions": 4}
            values = payload(db, kind)
            for field in ("created_at", "updated_at", "error"):
                if field in SOURCE_FIELDS.get(kind, {}):
                    values[field] = b"\x00\xffsource"
            if kind in ("work_summary_inputs", "email_agent_classification_tasks"):
                values["attempts" if kind == "work_summary_inputs" else "attempt_count"] = -0.0
            insert(db, kind, parent_id=parents.get(kind), values=values)
        status_path = old.TASK20_LAYOUTS["reply_tasks"].path("status")
        db.execute("update tasks set payload_json=jsonb_set(payload_json,?,jsonb(?)) where id=1",
                   (status_path, '"\\u0070ending"'))
        for index, adapter in enumerate(("reply", "scheduled_execution", "unrelated")):
            insert_state(db, "dispatcher_claim_leases",
                {"adapter_name": adapter, "source_id": "1", "owner": adapter,
                 "owner_pid": 70 + index, "generation": 10 + index,
                 "lease_expires_at": "2099-01-01", "last_error": adapter,
                 "created_at": b"created", "updated_at": b"updated"}, source_rowid=-10 - index)
        before = db.execute("select * from tasks order by id").fetchall()
        public = {kind: db.execute(old.task20_projection(kind)).fetchall() for kind in old.TASK20_COLUMNS}
        payloads = dict(db.execute("select id,payload_json from tasks"))
        ledger = db.execute("select * from state order by id").fetchall()
        waters = db.execute("select rowid,name,seq from sqlite_sequence order by rowid").fetchall()
        metadata = migrate_task_lifecycle(db)
        for kind in old.TASK20_COLUMNS:
            assert db.execute(old.task20_projection(kind)).fetchall() == public[kind]
            if kind in SOURCE_FIELDS:
                row = db.execute("select id," + _inverse_payload(kind) + " from tasks where record_type=?", (kind,)).fetchone()
                assert row[1] == payloads[row[0]]
            else:
                row = db.execute("select id,payload_json," + ",".join(LIFECYCLE_FIELDS)
                                 + " from tasks where record_type=?", (kind,)).fetchone()
                assert row[1] == payloads[row[0]]
                assert row[2:] == (None,) * len(LIFECYCLE_FIELDS)
        assert db.execute("select lifecycle_adapter_name,lifecycle_lease_owner,lifecycle_generation "
                          "from tasks where id=1").fetchone() == ("reply", "reply", 10)
        assert db.execute("select * from state order by id").fetchall() == ledger
        restore_task_lifecycle(db, metadata)
        assert db.execute("select * from tasks order by id").fetchall() == before
        assert db.execute("select rowid,name,seq from sqlite_sequence order by rowid").fetchall() == waters


@pytest.mark.parametrize("adapter_name", ["ReplyQueueAdapter", "ScheduledExecutionQueueAdapter",
                                         "MeetingQueueAdapter", "WorkSummaryQueueAdapter", "OkrReviewQueueAdapter"])
def test_due_claim_and_metrics_seek_kind_status_without_materialized_history(tmp_path, monkeypatch, adapter_name):
    from datetime import datetime, timezone, timedelta
    from app.store import AutoReplyStore
    from app.dispatcher import adapters
    store = AutoReplyStore(tmp_path / "plans.sqlite3")
    plans = []
    connect = sqlite3.connect

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql.startswith("select") and "task_status" in sql:
                plans.append([row[3] for row in super().execute("explain query plan " + sql, parameters)])
            return super().execute(sql, parameters)

    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: connect(*args, **kwargs, factory=ObservedConnection))
    adapter = getattr(adapters, adapter_name)(store, owner_alive=lambda _: False)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    assert adapter.claim(now, owner="fixture", owner_pid=7, lease=timedelta(minutes=5)) is None
    assert adapter.metrics(now).due == 0
    assert len(plans) == 6
    for plan in plans:
        assert any("SEARCH tasks USING INDEX idx_task_lifecycle_" in step
                   and "_kind=? AND lifecycle_status=?" in step for step in plan), plan
        assert not any("MATERIALIZE" in step or "SCAN tasks" in step for step in plan), plan


def test_history_pruning_cannot_clear_live_newer_or_other_adapter_claim(tmp_path):
    from app.store import AutoReplyStore
    from app.dispatcher.adapters import ReplyQueueAdapter
    from app.task_lifecycle_storage import forget_pruned_claims
    from datetime import datetime, timezone, timedelta
    store = AutoReplyStore(tmp_path / "retention.sqlite3")
    store.enqueue_reply_task(conversation_id="chat", conversation_title="Chat", single_chat=True,
        trigger_message_id="message", trigger_create_time="clock", trigger_sender="sender", trigger_text="text")
    adapter = ReplyQueueAdapter(store)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    first = adapter.claim(now, owner="live", owner_pid=7, lease=timedelta(minutes=5))
    with store._immediate_write_transaction() as db:
        before = db.execute("select adapter_name,lease_owner,owner_pid,generation,lease_expires_at from tasks "
                            "where record_type='reply_tasks'").fetchone()[:]
        forget_pruned_claims(db, [("reply", first.source_id, first.generation)])
        assert db.execute("select adapter_name,lease_owner,owner_pid,generation,lease_expires_at from tasks "
                          "where record_type='reply_tasks'").fetchone()[:] == before
    adapter.release(first, owner="live", now=now)
    with store._immediate_write_transaction() as db:
        forget_pruned_claims(db, [("reply", first.source_id, first.generation - 1),
                                 ("scheduled_execution", first.source_id, first.generation)])
        assert db.execute("select lifecycle_generation from tasks where record_type='reply_tasks'").fetchone()[0] == first.generation


def test_task_domain_and_claim_commit_together_with_passive_history(tmp_path, monkeypatch):
    from app.store import AutoReplyStore
    from app.dispatcher.adapters import ReplyQueueAdapter
    from app import task_lifecycle_storage as writer
    from datetime import datetime, timezone, timedelta
    store = AutoReplyStore(tmp_path / "atomic.sqlite3")
    store.enqueue_reply_task(conversation_id="atomic", conversation_title="Atomic", single_chat=True,
        trigger_message_id="atomic", trigger_create_time="clock", trigger_sender="sender", trigger_text="text")
    query = ("select lifecycle_status,lifecycle_attempts,lifecycle_lease_owner,lifecycle_generation "
             "from tasks where record_type='reply_tasks'")
    original = writer._write_claim_history

    def fail_after_task_cas(db, *args, **kwargs):
        assert db.execute(query).fetchone()[:] == ("processing", 1, "worker", 1)
        with store._connect() as reader:
            assert reader.execute(query).fetchone()[:] == ("pending", 0, None, None)
        raise RuntimeError("injected passive history write failure")

    monkeypatch.setattr(writer, "_write_claim_history", fail_after_task_cas)
    adapter = ReplyQueueAdapter(store)
    now = datetime(2026, 10, 19, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="passive history"):
        adapter.claim(now, owner="worker", owner_pid=7, lease=timedelta(minutes=5))
    with store._connect() as db:
        assert db.execute(query).fetchone()[:] == ("pending", 0, None, None)
        assert db.execute("select count(*) from state where record_type='dispatcher_claim_leases'").fetchone()[0] == 0
    monkeypatch.setattr(writer, "_write_claim_history", original)
    assert adapter.claim(now, owner="worker", owner_pid=7, lease=timedelta(minutes=5)).generation == 1
