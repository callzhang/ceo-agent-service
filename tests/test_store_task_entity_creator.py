from contextvars import ContextVar
import hashlib
import json
import sqlite3

import pytest

from app.store import (
    AutoReplyStore,
    EmailReplyTaskAuthorizationConflict,
    ReplyTaskIdentityConflict,
    ReplyTaskSpec,
)
from app import task_entity_storage
from app.task8_storage import register_task8_functions
from app.task_run_entity_assembly import task_run_entity_assembly_objects


def _target_store(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    register_task8_functions(connection)
    for ddl in task_run_entity_assembly_objects().values():
        if ddl.lstrip().lower().startswith("create table"):
            connection.execute(ddl)
    for ddl in task_run_entity_assembly_objects().values():
        if not ddl.lstrip().lower().startswith("create table"):
            connection.execute(ddl)
    connection.close()

    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = path
    store.busy_timeout_seconds = 5
    store.busy_timeout_milliseconds = 5000
    store._read_snapshot_connection = ContextVar(
        f"creator_read_snapshot_{id(store)}", default=None,
    )
    store._reused_connection = ContextVar(
        f"creator_reused_connection_{id(store)}", default=None,
    )
    return store


def _spec(message_id, *, business_key="", channel="dingtalk", **overrides):
    values = {
        "channel": channel,
        "conversation_id": "conversation",
        "conversation_title": "Conversation",
        "single_chat": False,
        "trigger_message_id": message_id,
        "trigger_create_time": "2026-10-22T12:00:00Z",
        "trigger_sender": "sender",
        "trigger_text": f"message {message_id}",
        "trigger_message_json": f'{{"id":"{message_id}"}}',
        "business_object_key": business_key,
    }
    values.update(overrides)
    return ReplyTaskSpec(**values)


def _creator_kwargs(spec):
    values = spec.model_dump()
    values.pop("error")
    return values


def test_reply_creator_uses_native_owners_and_returns_same_task_for_message_dedupe(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")

    assert store.enqueue_reply_task(**_creator_kwargs(_spec("enqueued-message"))) is True
    first = store.ensure_reply_task(**_creator_kwargs(_spec(
        "message-1", force_new_decision=True, manual_rerun_attempt_id=17,
    )))
    duplicate = store.ensure_reply_task(**_creator_kwargs(_spec("message-1", trigger_text="changed")))
    inserted = store.enqueue_reply_task(**_creator_kwargs(_spec("message-1")))

    assert duplicate.id == first.id
    assert duplicate.trigger_text == "message message-1"
    assert duplicate.force_new_decision is True
    assert duplicate.manual_rerun_attempt_id == 17
    assert inserted is False
    with store._connect() as db:
        assert db.execute("select kind from tasks where id=?", (first.id,)).fetchone()[0] == "reply"
        parameters = task_entity_storage.get_task(db, first.id)["parameters"]
        assert set(parameters) == {
            "force_new_decision", "manual_rerun_attempt_id",
            "manual_rerun_revision_key", "recovery_code",
        }
        assert parameters["manual_rerun_attempt_id"] == 17
        assert db.execute("select count(*) from task_inputs where task_id=?", (first.id,)).fetchone()[0] == 1
        assert db.execute("select count(*) from task_business_keys where task_id=?", (first.id,)).fetchone()[0] == 1


def test_business_key_reuses_task_and_advances_current_without_moving_claimed_input(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    first = store.ensure_reply_task(**_creator_kwargs(_spec("message-1", business_key="business-1")))
    with store._immediate_write_transaction() as db:
        task_entity_storage.associate_business_key(
            db, task_id=first.id, business_key="business-alias",
        )
        task_entity_storage.bind_claimed_input(
            db, task_id=first.id, input_id=first.current_input_id,
            claimed_input_version=first.input_version,
        )
        task_entity_storage.update_task_cas(
            db, task_id=first.id,
            expected={"status": "pending"},
            changes={"status": "processing"},
        )

    second = store.ensure_reply_task(**_creator_kwargs(_spec("message-2", business_key="business-1")))

    assert second.id == first.id
    assert second.trigger_message_id == "message-2"
    assert second.input_version == first.input_version + 1
    assert second.claimed_input_id == first.current_input_id
    assert second.current_input_id != second.claimed_input_id
    assert second.execution_generation == first.execution_generation
    assert second.status == "processing"
    with store._connect() as db:
        assert db.execute("select count(*) from task_inputs where task_id=?", (first.id,)).fetchone()[0] == 2
        assert [row[0] for row in db.execute("select business_key from task_business_keys where task_id=? order by id", (first.id,))] == ["business-1", "business-alias"]


def test_new_message_resets_terminal_generation_and_retry_state(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    first = store.ensure_reply_task(**_creator_kwargs(_spec("message-1", business_key="business-1")))
    with store._immediate_write_transaction() as db:
        task_entity_storage.update_task_cas(
            db,
            task_id=first.id,
            expected={"status": "pending"},
            changes={
                "status": "failed",
                "attempts": 3,
                "error": "previous failure",
                "locked_at": "old lock",
                "available_at": "old availability",
            },
        )

    revised = store.ensure_reply_task(**_creator_kwargs(_spec("message-2", business_key="business-1")))

    assert revised.id == first.id
    assert revised.status == "pending"
    assert revised.attempts == 0
    assert revised.error == ""
    assert revised.locked_at is None
    assert revised.available_at == ""
    assert revised.execution_generation != first.execution_generation
    assert revised.input_version == first.input_version + 1


def test_append_rejects_full_key_byte_conflict_and_different_owner(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    first = store.ensure_reply_task(**_creator_kwargs(_spec("owner-1", business_key="owner-key-1")))
    second = store.ensure_reply_task(**_creator_kwargs(_spec("owner-2", business_key="owner-key-2")))
    snapshot = {
        "channel": "dingtalk",
        "conversation_id": "revision-conversation",
        "conversation_title": "Revision",
        "single_chat": 0,
        "trigger_message_id": "revision-message",
        "trigger_create_time": "time",
        "trigger_sender": "sender",
        "trigger_text": "original bytes",
        "trigger_message_json": '{"raw":"original"}',
        "oa_url": "",
        "business_object_key": "revision-key",
    }
    with store._immediate_write_transaction() as db:
        input_id = store._append_reply_input_snapshot(
            db, first.id, snapshot, revision_key="revision-1",
        )
        assert input_id > 0
        with pytest.raises(ReplyTaskIdentityConflict, match="different bytes"):
            store._append_reply_input_snapshot(
                db, first.id, {**snapshot, "trigger_text": "changed bytes"},
                revision_key="revision-1",
            )
        with pytest.raises(ReplyTaskIdentityConflict, match="another task"):
            store._append_reply_input_snapshot(
                db, second.id, snapshot, revision_key="revision-1",
            )


def test_creator_keeps_distinct_channels_separate_and_oa_provisional_key_refines(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    first = store.ensure_reply_task(**_creator_kwargs(_spec("same-message", channel="dingtalk")))
    other_channel = store.ensure_reply_task(**_creator_kwargs(_spec("same-message", channel="wechat")))
    provisional = store.ensure_reply_task(**_creator_kwargs(_spec(
        "oa-first", business_key="oa:process-1:",
    )))
    refined = store.ensure_reply_task(**_creator_kwargs(_spec(
        "oa-second", business_key="oa:process-1:node-1",
    )))

    assert first.id != other_channel.id
    distinct_key = store.ensure_reply_task(**_creator_kwargs(_spec(
        "different-message", business_key="different-business", channel="dingtalk",
    )))
    assert distinct_key.id != first.id
    assert refined.id == provisional.id
    with store._connect() as db:
        assert [row[0] for row in db.execute(
            "select business_key from task_business_keys where task_id=? order by id",
            (provisional.id,),
        )] == ["oa:process-1:node-1"]
        assert db.execute("select count(*) from task_inputs where task_id=?", (provisional.id,)).fetchone()[0] == 2


def test_native_reply_queue_claim_and_input_refresh_keep_claimed_snapshot(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    first = store.ensure_reply_task(**_creator_kwargs(_spec(
        "queue-1", business_key="queue-business", available_at="2026-10-23 10:00:00",
    )))
    other_channel = store.ensure_reply_task(**_creator_kwargs(_spec(
        "queue-2", channel="wechat", business_key="wechat-business",
    )))
    later = store.ensure_reply_task(**_creator_kwargs(_spec(
        "queue-3", business_key="later-business", available_at="2026-10-23 12:00:00",
    )))
    bulk = store.ensure_reply_task(**_creator_kwargs(_spec(
        "queue-4", business_key="bulk-business",
    )))

    assert store.peek_reply_tasks(0, now="2026-10-23 11:00:00") == []
    assert [task.id for task in store.peek_reply_tasks(5, now="2026-10-23 11:00:00", channel="dingtalk")] == [first.id, bulk.id]
    assert store.max_pending_reply_task_id(now="2026-10-23 11:00:00", channel="dingtalk") == bulk.id
    assert store.peek_reply_tasks(5, now="2026-10-23 11:00:00", channel="wechat")[0].id == other_channel.id
    assert store.peek_reply_tasks(
        5, now="2026-10-23 11:00:00", channel="dingtalk",
        after_id=first.id, max_id=bulk.id,
    )[0].id == bulk.id

    claimed = store.claim_reply_task(first.id, now="2026-10-23 11:00:00")
    assert claimed is not None
    assert claimed.status == "processing" and claimed.attempts == 1
    assert claimed.claimed_input_id == claimed.current_input_id
    execution = store.get_reply_task_for_execution(first.id, claimed.execution_generation)
    assert execution is not None and execution.trigger_text == "message queue-1"

    revised = store.ensure_reply_task(**_creator_kwargs(_spec(
        "queue-5", business_key="queue-business", trigger_text="new current input",
    )))
    assert revised.id == first.id
    assert revised.current_input_id != claimed.claimed_input_id
    assert revised.claimed_input_id == claimed.claimed_input_id
    assert store.get_reply_task(first.id).trigger_text == "new current input"
    assert store.get_reply_task_for_execution(first.id, claimed.execution_generation).trigger_text == "message queue-1"
    assert store.list_reply_task_inputs(first.id)[-1]["task_id"] == first.id
    assert "reply_task_id" not in store.list_reply_task_inputs(first.id)[-1]
    assert [task.id for task in store.claim_reply_tasks(
        4, now="2026-10-23 11:00:00", channel="dingtalk",
    )] == [bulk.id]
    assert store.claim_reply_task(first.id, now="2026-10-23 11:00:00") is None
    assert store.get_reply_task(later.id).attempts == 0


def test_native_bulk_claim_rechecks_after_preview_loses_task(tmp_path, monkeypatch):
    store = _target_store(tmp_path / "target.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("race-1")))
    original_peek = store.peek_reply_tasks

    def claim_after_preview(*args, **kwargs):
        preview = original_peek(*args, **kwargs)
        assert preview and preview[0].id == task.id
        assert store.claim_reply_task(task.id, now="2026-10-23 11:00:00")
        return preview

    monkeypatch.setattr(store, "peek_reply_tasks", claim_after_preview)
    assert store.claim_reply_tasks(1, now="2026-10-23 11:00:00") == []
    assert store.get_reply_task(task.id).attempts == 1


def test_native_reply_refresh_guards_and_explicit_same_bytes_increment(tmp_path, monkeypatch):
    store = _target_store(tmp_path / "target.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("refresh-1")))

    assert store.update_pending_reply_task_trigger_for_message(
        "conversation", "refresh-1", trigger_text=task.trigger_text,
        trigger_message_json=task.trigger_message_json,
    ) == 0
    assert store.update_reply_task_trigger(
        task.id, trigger_text=task.trigger_text,
        trigger_message_json=task.trigger_message_json,
    ) == 1
    same_bytes_refresh = store.get_reply_task(task.id)
    assert same_bytes_refresh.input_version == task.input_version + 1
    assert same_bytes_refresh.current_input_id != task.current_input_id

    original_inputs = store.list_reply_task_inputs(task.id)

    def fail_current_bind(*args, **kwargs):
        raise RuntimeError("input pointer bind failed")

    monkeypatch.setattr(task_entity_storage, "bind_current_input", fail_current_bind)
    with pytest.raises(RuntimeError, match="input pointer bind failed"):
        store.update_reply_task_trigger(
            task.id, trigger_text="rolled back", trigger_message_json="{}",
        )
    assert store.get_reply_task(task.id).input_version == same_bytes_refresh.input_version
    assert store.list_reply_task_inputs(task.id) == original_inputs
    monkeypatch.undo()

    assert store.update_pending_reply_task_trigger_for_message(
        "conversation", "refresh-1", trigger_text="changed",
        trigger_message_json='{"id":"refresh-1"}',
    ) == 1
    refreshed = store.get_reply_task(task.id)
    assert refreshed.input_version == same_bytes_refresh.input_version + 1
    assert refreshed.trigger_text == "changed"
    assert [row["trigger_text"] for row in store.list_reply_task_inputs(task.id)] == [
        "message refresh-1", "message refresh-1", "changed",
    ]
    provenance = store.list_reply_task_inputs(task.id)[-1]["input_provenance_json"]
    with store._immediate_write_transaction() as db:
        db.execute(
            "update task_inputs set trigger_text='',trigger_message_json='',input_compacted=1,"
            "input_provenance_json=? where id=?",
            (provenance, refreshed.current_input_id),
        )
    compacted = store.list_reply_task_inputs(task.id)[-1]
    assert compacted["input_compacted"] == 1
    assert '"input_compacted":true' in compacted["trigger_message_json"]
    claimed = store.claim_reply_task(task.id, now="2026-10-23 11:00:00")
    assert claimed is not None
    assert store.update_reply_task_trigger(
        task.id, trigger_text="too late", trigger_message_json="{}",
    ) == 0
    execution = store.get_reply_task_for_execution(task.id, claimed.execution_generation)
    assert execution is not None and execution.input_compacted
    assert '"input_compacted":true' in execution.trigger_message_json


def test_oa_refinement_updates_claimed_context_key_but_keeps_claimed_source_bytes(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    provisional = store.ensure_reply_task(**_creator_kwargs(_spec(
        "oa-first", business_key="oa:process-claimed:",
        trigger_text="first immutable body",
        trigger_message_json='{"raw":"first body"}',
    )))
    claimed_message_digest = hashlib.sha256(
        b'{"raw":"first body"}'
    ).hexdigest()
    claimed_text_digest = hashlib.sha256(b"first immutable body").hexdigest()
    assert store.get_reply_task_for_execution(
        provisional.id, provisional.execution_generation,
    ) is None
    with store._immediate_write_transaction() as db:
        task_entity_storage.bind_claimed_input(
            db, task_id=provisional.id,
            input_id=provisional.current_input_id,
            claimed_input_version=provisional.input_version,
        )
        task_entity_storage.update_task_cas(
            db, task_id=provisional.id,
            expected={"status": "pending"},
            changes={"status": "processing"},
        )

    refined = store.ensure_reply_task(**_creator_kwargs(_spec(
        "oa-second", business_key="oa:process-claimed:node-1",
        trigger_text="second body",
        trigger_message_json='{"raw":"second body"}',
    )))
    execution = store.get_reply_task_for_execution(
        refined.id, refined.execution_generation,
    )

    assert refined.id == provisional.id
    assert refined.current_input_id != provisional.claimed_input_id
    assert refined.claimed_input_id == provisional.current_input_id
    assert execution is not None
    assert execution.trigger_message_id == "oa-first"
    assert execution.trigger_text == "first immutable body"
    assert execution.trigger_message_json == '{"raw":"first body"}'
    assert execution.business_object_key == "oa:process-claimed:node-1"
    assert execution.current_input_id == refined.current_input_id
    assert execution.claimed_input_id == provisional.current_input_id
    assert execution.input_version == refined.input_version
    assert execution.claimed_input_version == provisional.input_version
    assert store.get_reply_task_for_execution(refined.id, "wrong-generation") is None
    with store._connect() as db:
        inputs = db.execute(
            "select trigger_message_id, trigger_text, trigger_message_json, input_provenance_json, business_object_key from task_inputs where task_id=? order by id",
            (refined.id,),
        ).fetchall()
        assert [row["business_object_key"] for row in inputs] == [
            "oa:process-claimed:node-1", "oa:process-claimed:node-1",
        ]
        assert [row["trigger_message_id"] for row in inputs] == ["oa-first", "oa-second"]
        assert [row["trigger_text"] for row in inputs] == [
            "first immutable body", "second body",
        ]
        assert hashlib.sha256(inputs[0]["trigger_text"].encode()).hexdigest() == claimed_text_digest
        assert [row["trigger_message_json"] for row in inputs] == [
            '{"raw":"first body"}', '{"raw":"second body"}',
        ]
        assert [row["input_provenance_json"] for row in inputs] == ["{}", "{}"]
        assert hashlib.sha256(inputs[0]["trigger_message_json"].encode()).hexdigest() == claimed_message_digest
        assert db.execute(
            "select business_key from task_business_keys where task_id=?",
            (refined.id,),
        ).fetchone()[0] == "oa:process-claimed:node-1"


def test_batch_creator_rolls_back_task_input_and_key_on_immutable_identity_conflict(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    conflicting = (
        _spec("one", business_key="batch-key"),
        _spec("one", business_key="batch-key", trigger_text="different bytes"),
    )

    with pytest.raises(ReplyTaskIdentityConflict, match="different immutable input"):
        store.ensure_reply_tasks(conflicting)

    with store._connect() as db:
        assert db.execute("select count(*) from tasks where kind='reply'").fetchone()[0] == 0
        assert db.execute("select count(*) from task_inputs").fetchone()[0] == 0
        assert db.execute("select count(*) from task_business_keys").fetchone()[0] == 0


def test_authorized_email_creator_checks_current_persisted_plan_on_target_owners(tmp_path):
    from app.email_canonical_storage import insert_email_classification, upsert_email_message
    from tests.test_email_canonical_storage import _classification, _message

    store = _target_store(tmp_path / "target.sqlite3")
    identity = "account:message-id:<creator@example.test>"
    with store._immediate_write_transaction() as db:
        upsert_email_message(db, _message(
            identity, uid=21, thread_identity="email-thread",
        ))
        classification_id = insert_email_classification(db, _classification(
            identity, id=101, uid=21, status="processed",
            current_action_plan_id="plan-current",
        ))

    result = store.ensure_authorized_email_reply_tasks(
        classification_id=classification_id,
        account_id="account",
        stable_message_identity=identity,
        thread_identity="email-thread",
        action_plan_id="plan-current",
        task_specs=(
            _spec("email-trigger-1", channel="email", conversation_id="email-thread"),
            _spec("email-trigger-2", channel="email", conversation_id="email-thread"),
        ),
    )

    assert len(result) == 2
    assert result[0].channel == "email"
    assert result[0].conversation_id == "email-thread"
    with pytest.raises(EmailReplyTaskAuthorizationConflict, match="current persisted authorization"):
        store.ensure_authorized_email_reply_tasks(
            classification_id=classification_id,
            account_id="account",
            stable_message_identity=identity,
            thread_identity="email-thread",
            action_plan_id="stale-plan",
            task_specs=(_spec("must-not-insert", channel="email", conversation_id="email-thread"),),
        )
    with store._connect() as db:
        assert db.execute("select count(*) from tasks where kind='reply'").fetchone()[0] == 2


def test_native_consumer_run_claim_lease_session_and_failure_use_run_owner(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("native-run-message")))
    now = "2026-10-23T12:00:00Z"

    claimed = store.claim_agent_run(
        task.id, task.execution_generation,
        role="consumer", proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=None, operation_id="", owner="worker-a",
        now=now,
    )

    assert claimed.claimed is True
    assert claimed.run.id > 0
    assert claimed.run.reply_task_id == task.id
    assert store.get_agent_run(claimed.run.id, load_events=False).role.value == "consumer"
    assert store.renew_agent_run_lease(
        claimed.run.id, owner="worker-a", now="2026-10-23T12:00:30Z",
    ).lease_owner == "worker-a"
    assert store.set_agent_run_session(
        claimed.run.id, "session-native", owner="worker-a",
        now="2026-10-23T12:00:45Z",
    ).codex_session_id == "session-native"

    failed = store.fail_agent_run(
        claimed.run.id, {"code": "runtime_failed", "retryable": False},
        owner="worker-a", now="2026-10-23T12:01:00Z",
    )

    assert failed.status == "failed"
    with store._connect() as db:
        row = db.execute(
            "select role, status, session_id from runs where id=?",
            (claimed.run.id,),
        ).fetchone()
        assert tuple(row) == ("consumer", "failed", "session-native")
        assert db.execute(
            "select count(*) from run_failures where run_id=?",
            (claimed.run.id,),
        ).fetchone()[0] == 0


def test_native_audit_adoption_keeps_review_identity_separate_and_terminal_write_idempotent(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("native-audit-message")))
    now = "2026-10-23T13:00:00Z"
    consumer = store.claim_agent_run(
        task.id, task.execution_generation, role="consumer",
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer-worker", now=now,
    ).run
    candidate_result = {"outcome": "proposal", "stage_index": 0}
    consumer = store.complete_agent_run(
        consumer.id, candidate_result, owner="consumer-worker",
        now="2026-10-23T13:00:10Z",
    )
    candidate = store.adopted_candidate_for_consumer_run(consumer.id)
    assert candidate is not None

    audit = store.claim_agent_run(
        task.id, task.execution_generation, role="audit",
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id="audit-op-1", owner="audit-worker",
        now="2026-10-23T13:00:20Z",
    ).run
    audit_result = {
        "outcome": "reject", "candidate_digest": candidate["candidate_digest"],
    }
    completed = store.complete_agent_run(
        audit.id, audit_result, owner="audit-worker",
        now="2026-10-23T13:00:30Z",
    )
    repeated = store.complete_agent_run(
        audit.id, audit_result, owner="audit-worker",
        now="2026-10-23T13:00:40Z",
    )
    assert completed.status == repeated.status == "completed"
    review = store.reviewed_candidate_for_audit_run(audit.id)
    assert review is not None
    assert review["audit_run_id"] == audit.id
    assert review["review_id"] != audit.id
    # The adopted value remains readable without a native CLI invocation;
    # no trajectory event is fabricated to stand in for missing runtime data.
    read_back = store.get_agent_run(audit.id)
    assert json.loads(read_back.adopted_result_json) == audit_result
    assert read_back.tool_events == []
    assert read_back.final_result_json == ""
    with pytest.raises(ValueError, match="conflicting terminal rewrite"):
        store.complete_agent_run(
            audit.id, {**audit_result, "feedback": "changed"},
            owner="audit-worker", now="2026-10-23T13:00:50Z",
        )
