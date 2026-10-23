from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
import hashlib
import json
import sqlite3

import pytest

from app.store import (
    AutoReplyStore,
    AgentRunLeaseLostError,
    EmailReplyTaskAuthorizationConflict,
    FEEDBACK_EVENT_SQL,
    ReplyTaskIdentityConflict,
    ReplyTaskSpec,
)
from app import task_entity_storage
from app.action_storage import insert_action, insert_task_delivery_action
from app.action_entity_storage import append_action_receipt, create_action, record_action_result
from app.action_feedback_storage import (
    insert_feedback_action,
    reopen_feedback_event,
    resolve_feedback_events,
    set_feedback_action,
)
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


def _source_feedback_upsert_projection(
    db, key, *, current_status=None, current_resolved_at=None,
):
    # Frozen from the source Task12 upsert SQL at d930a2493. Keep this inline
    # oracle self-contained so regressions do not depend on Git history.
    source_value = f"(select resolved_at from ({FEEDBACK_EVENT_SQL}) where key=?)"
    if current_status is None:
        row = db.execute(
            f"select case when trim({source_value})<>'' then 'resolved' else 'pending' end, "
            f"coalesce({source_value}, '')",
            (key, key),
        ).fetchone()
    else:
        row = db.execute(
            f"select case when trim({source_value})<>'' then 'resolved' else ? end, "
            f"case when trim({source_value})<>'' then {source_value} else ? end",
            (key, current_status, key, key, current_resolved_at),
        ).fetchone()
    return tuple(row)


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


def test_failed_reply_run_retry_uses_native_task_and_run_owners(tmp_path):
    store = _target_store(tmp_path / "native-retry.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("retry-message", business_key="business:one")))
    with store._connect() as db:
        native = task_entity_storage.get_task(db, task.id)
        db.execute(
            "update tasks set status='failed',attempts=3,locked_at='prior-lock',"
            "error='prior failure' where id=?",
            (task.id,),
        )
        run_id = db.execute(
            "insert into runs(task_id,execution_generation,role,proposal_revision,"
            "turn_attempt,operation_id,status,session_id,transcript_start,transcript_end,"
            "structured_error_json,lease_owner,lease_expires_at,started_at,completed_at,updated_at) "
            "values(?,?,'consumer',0,0,'','failed','session',0,0,'{}','','','','','') "
            "returning id",
            (task.id, native["execution_generation"]),
        ).fetchone()[0]
        db.execute(
            "update tasks set claimed_input_id=current_input_id,claimed_input_version=input_version "
            "where id=?",
            (task.id,),
        )

    recovered = store.retry_failed_reply_task(
        task.id, int(run_id), reason="  retry after repair  ", recovery_code="  "
    )

    assert recovered.status == "pending"
    assert recovered.attempts == 0
    assert recovered.error == "retry after repair"
    assert recovered.execution_generation != task.execution_generation
    assert recovered.current_input_id == task.current_input_id
    assert recovered.claimed_input_id == task.current_input_id
    with pytest.raises(ValueError, match="not retryable"):
        store.retry_failed_reply_task(task.id, int(run_id), reason="second retry")
    with store._connect() as db:
        owner = task_entity_storage.get_task(db, task.id)
        assert owner["parameters"]["force_new_decision"] == 1
        assert owner["parameters"]["recovery_code"] == ""
        assert db.execute("select count(*) from runs where task_id=?", (task.id,)).fetchone()[0] == 1
        assert db.execute("select count(*) from task_business_keys where task_id=?", (task.id,)).fetchone()[0] == 1


def test_failed_reply_run_retry_refuses_stale_run_and_accepts_latest_audit(tmp_path):
    store = _target_store(tmp_path / "native-stale-run.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("stale-run-message")))
    with store._connect() as db:
        owner = task_entity_storage.get_task(db, task.id)
        db.execute("update tasks set status='failed' where id=?", (task.id,))
        run_ids = []
        stale_generation_run = db.execute(
            "insert into runs(task_id,execution_generation,role,proposal_revision,turn_attempt,"
            "operation_id,status,session_id,transcript_start,transcript_end,structured_error_json,"
            "lease_owner,lease_expires_at,started_at,completed_at,updated_at) "
            "values(?,?,'consumer',0,0,'','failed','old-session',0,0,'{}','','','','','') returning id",
            (task.id, "old-generation"),
        ).fetchone()[0]
        for role in ("consumer", "audit"):
            run_ids.append(db.execute(
                "insert into runs(task_id,execution_generation,role,proposal_revision,turn_attempt,"
                "operation_id,status,session_id,transcript_start,transcript_end,structured_error_json,"
                "lease_owner,lease_expires_at,started_at,completed_at,updated_at) "
                "values(?,?,?,0,0,'','failed','session',0,0,'{}','','','','','') returning id",
                (task.id, owner["execution_generation"], role),
            ).fetchone()[0])

    with pytest.raises(ValueError, match="not retryable"):
        store.retry_failed_reply_task(task.id, int(stale_generation_run), reason="stale generation")
    with pytest.raises(ValueError, match="not retryable"):
        store.retry_failed_reply_task(task.id, int(run_ids[0]), reason="stale run")
    recovered = store.retry_failed_reply_task(task.id, int(run_ids[1]), reason="latest audit")
    assert recovered.status == "pending"
    assert recovered.execution_generation != task.execution_generation



def test_pre_agent_retry_refuses_sent_reply_and_cas_loss_is_atomic(tmp_path, monkeypatch):
    store = _target_store(tmp_path / "native-pre-agent-fences.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("pre-agent-sent")))
    with store._connect() as db:
        db.execute("update tasks set status='failed' where id=?", (task.id,))
        sent = create_action(db, values={
            "operation": "send_reply_to_trigger", "status": "verified",
            "delivery_channel": "dingtalk", "conversation_id": "conversation",
            "trigger_message_id": "pre-agent-sent",
        })
        receipt_id = append_action_receipt(db, int(sent["id"]), values={
            "operation": "send_reply_to_trigger", "status": "verified", "target": "conversation",
            "observed_at": "2026-10-23T00:00:00Z",
        })
        record_action_result(db, int(sent["id"]), expected={"delivery_receipt_id": None},
                             changes={"delivery_receipt_id": receipt_id})
    with pytest.raises(ValueError, match="already has a sent reply"):
        store.retry_failed_pre_agent_reply_task(task.id, reason="must not replay sent reply")

    no_effect = store.ensure_reply_task(**_creator_kwargs(_spec("pre-agent-cas-loss")))
    with store._connect() as db:
        db.execute("update tasks set status='failed' where id=?", (no_effect.id,))
    monkeypatch.setattr(task_entity_storage, "update_task_cas", lambda *args, **kwargs: None)
    with pytest.raises(AgentRunLeaseLostError, match="superseded"):
        store.retry_failed_pre_agent_reply_task(no_effect.id, reason="lost CAS")
    with store._connect() as db:
        owner = task_entity_storage.get_task(db, no_effect.id)
        assert (owner["status"], owner["attempts"], owner["error"]) == ("failed", 0, "")


def test_reply_deferrals_update_native_task_without_touching_input_owners(tmp_path):
    store = _target_store(tmp_path / "native-defer.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("defer-message")))
    with store._connect() as db:
        db.execute("update tasks set status='processing',attempts=2,locked_at='lease' where id=?", (task.id,))

    store.defer_reply_task(task.id, "wait", expected_execution_generation=task.execution_generation)

    with store._connect() as db:
        owner = task_entity_storage.get_task(db, task.id)
        assert (owner["status"], owner["attempts"], owner["locked_at"], owner["error"]) == (
            "pending", 1, None, "wait",
        )
        assert owner["current_input_id"] == task.current_input_id

    with store._connect() as db:
        db.execute("update tasks set status='processing',attempts=2,locked_at='lease' where id=?", (task.id,))
    with pytest.raises(AgentRunLeaseLostError, match="superseded"):
        store.defer_reply_task(
            task.id, "stale", expected_execution_generation="stale-generation",
        )
    with store._connect() as db:
        owner = task_entity_storage.get_task(db, task.id)
        assert (owner["status"], owner["attempts"], owner["locked_at"]) == (
            "processing", 2, "lease",
        )
    store.defer_reply_task_for_authorization(
        task.id, "reauth", expected_execution_generation=task.execution_generation,
        available_at="2030-01-01 00:00:00",
    )
    with store._connect() as db:
        owner = task_entity_storage.get_task(db, task.id)
        assert (owner["status"], owner["attempts"], owner["available_at"], owner["error"]) == (
            "pending", 2, "2030-01-01 00:00:00", "reauth",
        )


def test_native_codex_auth_recovery_clears_only_eligible_failed_task(tmp_path):
    store = _target_store(tmp_path / "native-auth-recovery.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("auth-message")))
    with store._connect() as db:
        owner = task_entity_storage.get_task(db, task.id)
        parameters = dict(owner["parameters"])
        parameters["recovery_code"] = "codex_provider_auth_failed"
        assert task_entity_storage.update_task_cas(
            db, task_id=task.id, expected={"kind": "reply", "status": "pending"},
            changes={"status": "failed", "error": "auth failure"},
            parameters=parameters,
        )

    assert store.has_failed_native_codex_auth_tasks(channel="dingtalk")
    recovered = store.recover_failed_native_codex_auth_tasks(
        channel="dingtalk", reason="native login restored"
    )

    assert recovered == [task.id]
    with store._connect() as db:
        owner = task_entity_storage.get_task(db, task.id)
        assert owner["status"] == "pending"
        assert owner["error"] == "native login restored"
        assert owner["execution_generation"] != task.execution_generation
        assert owner["parameters"]["recovery_code"] == ""
        assert owner["current_input_id"] == task.current_input_id


def test_native_auth_recovery_preserves_latest_attempt_channel_and_effect_fences(tmp_path):
    store = _target_store(tmp_path / "native-auth-fences.sqlite3")

    def failed_auth(message_id):
        task = store.ensure_reply_task(**_creator_kwargs(_spec(message_id)))
        with store._connect() as db:
            native = task_entity_storage.get_task(db, task.id)
            params = dict(native["parameters"])
            params["recovery_code"] = "codex_provider_auth_failed"
            task_entity_storage.update_task_cas(
                db, task_id=task.id, expected={"kind": "reply", "status": "pending"},
                changes={"status": "failed", "error": "auth failure"}, parameters=params,
            )
        return task

    eligible = failed_auth("bulk-eligible")
    preaction = failed_auth("bulk-preaction")
    running = failed_auth("blocked-running-run")
    unknown = failed_auth("blocked-wechat-unknown")
    latest_attempt = failed_auth("blocked-latest-attempt")
    sent_task = failed_auth("blocked-sent")
    stale = store.ensure_reply_task(**_creator_kwargs(_spec("stale-auth", channel="dingtalk")))
    fresh = store.ensure_reply_task(**_creator_kwargs(_spec("fresh-auth", channel="dingtalk")))
    wrong_channel = failed_auth("wrong-current-channel")

    with store._connect() as db:
        latest_owner = task_entity_storage.get_task(db, latest_attempt.id)
        latest_parameters = dict(latest_owner["parameters"])
        latest_parameters["recovery_code"] = ""
        task_entity_storage.update_task_cas(
            db, task_id=latest_attempt.id,
            expected={"kind": "reply", "status": "failed"},
            changes={"error": ""}, parameters=latest_parameters,
        )
        db.execute("update tasks set status='processing',error='codex_auth_recovered',"
                   "locked_at=datetime('now','-91 seconds') where id=?", (stale.id,))
        db.execute("update tasks set status='processing',error='codex_auth_recovered',"
                   "locked_at=datetime('now','-30 seconds') where id=?", (fresh.id,))
        run_id = db.execute(
            "insert into runs(task_id,execution_generation,role,proposal_revision,turn_attempt,"
            "operation_id,status,session_id,transcript_start,transcript_end,structured_error_json,"
            "lease_owner,lease_expires_at,started_at,completed_at,updated_at) "
            "values(?,?,'consumer',0,0,'','running','session',0,0,'{}','','','','','') returning id",
            (running.id, running.execution_generation),
        ).fetchone()[0]
        insert_task_delivery_action(db, "wechat_deliveries", {
            "reply_task_id": preaction.id, "account_id": "acct", "target_type": "direct",
            "target_id": "friend", "conversation_id": "conversation", "reply_text": "hi",
            "status": "failed", "pre_action_failure": 1,
        })
        insert_task_delivery_action(db, "wechat_deliveries", {
            "reply_task_id": unknown.id, "account_id": "acct", "target_type": "direct",
            "target_id": "friend", "conversation_id": "conversation", "reply_text": "hi",
            "status": "send_unknown", "pre_action_failure": 0,
        })
        for send_error in ("codex_provider_auth_failed: old", "other failure"):
            insert_action(db, "reply_attempts", {
                "conversation_id": "conversation", "conversation_title": "Conversation",
                "trigger_message_id": "blocked-latest-attempt", "trigger_sender": "sender",
                "trigger_text": "body", "action": "failed", "sensitivity_kind": "general",
                "send_status": "failed", "send_error": send_error, "channel": "dingtalk",
            })
        native = task_entity_storage.get_task_with_inputs(db, wrong_channel.id)
        changed = {key: native["current_input"][key] for key in (
            "channel", "conversation_id", "conversation_title", "single_chat",
            "trigger_message_id", "trigger_create_time", "trigger_sender", "trigger_text",
            "trigger_message_json", "oa_url", "business_object_key", "input_revision_key",
            "input_provenance_json", "input_compacted",
        )}
        changed.update(channel="wechat", input_revision_key="channel-revision")
        next_input, _ = task_entity_storage.append_task_input(db, task_id=wrong_channel.id, values=changed)
        task_entity_storage.bind_current_input(
            db, task_id=wrong_channel.id, input_id=int(next_input["id"]),
            expected_input_version=int(native["input_version"]),
            input_version=int(native["input_version"]) + 1,
        )
        # A provider receipt is a durable Sent effect even when its body is opaque.
        sent = create_action(db, values={
            "operation": "send_reply_to_trigger", "status": "verified", "delivery_channel": "dingtalk",
            "conversation_id": "conversation", "trigger_message_id": "blocked-sent",
        })
        receipt_id = append_action_receipt(db, int(sent["id"]), values={
            "operation": "send_reply_to_trigger", "status": "verified", "target": "conversation",
            "observed_at": "2026-10-23T00:00:00Z",
        })
        record_action_result(db, int(sent["id"]), expected={"delivery_receipt_id": None},
                             changes={"delivery_receipt_id": receipt_id})

    assert store.has_failed_native_codex_auth_tasks(channel="dingtalk")
    assert store.has_failed_native_codex_auth_tasks(channel="wechat")
    recovered = store.recover_failed_native_codex_auth_tasks(
        channel="dingtalk", reason="verified auth recovery"
    )
    assert recovered == [eligible.id, preaction.id, stale.id]
    assert running.id not in recovered
    assert unknown.id not in recovered
    assert latest_attempt.id not in recovered
    assert fresh.id not in recovered
    assert wrong_channel.id not in recovered
    assert sent_task.id not in recovered
    with store._connect() as db:
        assert db.execute("select status from runs where id=?", (run_id,)).fetchone()[0] == "running"


def test_native_pre_agent_retry_keeps_generation_and_requires_no_run(tmp_path):
    store = _target_store(tmp_path / "native-pre-agent-retry.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("pre-agent-message")))
    with store._connect() as db:
        db.execute("update tasks set status='failed',attempts=2,error='lock failure' where id=?", (task.id,))

    recovered = store.retry_failed_pre_agent_reply_task(task.id, reason="  lock cleared  ")

    assert recovered.status == "pending"
    assert recovered.error == "lock cleared"
    assert recovered.execution_generation == task.execution_generation
    with store._connect() as db:
        owner = task_entity_storage.get_task(db, task.id)
        assert owner["attempts"] == 0
        db.execute("update tasks set status='failed' where id=?", (task.id,))
        db.execute(
            "insert into runs(task_id,execution_generation,role,proposal_revision,turn_attempt,"
            "operation_id,status,session_id,transcript_start,transcript_end,structured_error_json,"
            "lease_owner,lease_expires_at,started_at,completed_at,updated_at) "
            "values(?,?,'consumer',0,0,'','failed','session',0,0,'{}','','','','','')",
            (task.id, task.execution_generation),
        )
    with pytest.raises(ValueError, match="already has an agent run"):
        store.retry_failed_pre_agent_reply_task(task.id, reason="must not reopen")


def test_feedback_event_upsert_creates_one_native_task_and_preserves_processing_state(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")

    store.upsert_feedback_event(
        key="feedback:one", feedback_token="token-one", comment="first",
    )
    with store._connect() as db:
        created = db.execute(
            "select task.id,task.kind,task.status,item.feedback_key,item.resolved_at "
            "from tasks task join feedback_processing_items item on item.task_id=task.id "
            "where item.feedback_key=?",
            ("feedback:one",),
        ).fetchone()
        task_id = int(created["id"])
        assert tuple(created)[1:] == (
            "feedback_processing", "pending", "feedback:one", "",
        )
        db.execute(
            "update tasks set status='processing',updated_at='prior-event-time' where id=?",
            (task_id,),
        )
        db.execute(
            "update feedback_processing_items set workbench_task_id='wb-task',"
            "workbench_turn_id='wb-turn',attempt_id=7,agent_run_id=11,"
            "test_evidence_json=?,note='kept' where task_id=?",
            ('{"passed":true}', task_id),
        )

    store.upsert_feedback_event(
        key="feedback:one", feedback_token="token-one", comment="updated",
    )

    with store._connect() as db:
        rows = db.execute(
            "select task.id,task.status,item.workbench_task_id,item.workbench_turn_id,"
            "item.attempt_id,item.agent_run_id,item.test_evidence_json,item.note,task.updated_at "
            "from tasks task join feedback_processing_items item on item.task_id=task.id "
            "where item.feedback_key=?",
            ("feedback:one",),
        ).fetchall()
        assert len(rows) == 1
        assert tuple(rows[0])[:8] == (
            task_id, "processing", "wb-task", "wb-turn", 7, 11,
            '{"passed":true}', "kept",
        )
        assert rows[0]["updated_at"] != "prior-event-time"
        assert db.execute(
            f"select comment from ({FEEDBACK_EVENT_SQL}) where key=?",
            ("feedback:one",),
        ).fetchone()[0] == "updated"
    assert store.list_user_feedback_items()[0].processing_status == "processing"
    assert store.list_user_feedback_items_by_keys(["feedback:one"])[0].processing_status == "processing"
    assert store.count_pending_user_feedback_items() == 0


def test_feedback_event_and_task_intake_roll_back_together(tmp_path, monkeypatch):
    store = _target_store(tmp_path / "target.sqlite3")

    def fail_create_task(*args, **kwargs):
        raise RuntimeError("injected Task creation failure")

    monkeypatch.setattr(task_entity_storage, "create_task", fail_create_task)
    with pytest.raises(RuntimeError, match="injected Task creation failure"):
        store.upsert_feedback_event(
            key="feedback:rollback", feedback_token="token-rollback",
        )

    with store._connect() as db:
        assert db.execute(
            f"select count(*) from ({FEEDBACK_EVENT_SQL}) where key=?",
            ("feedback:rollback",),
        ).fetchone()[0] == 0
        assert db.execute("select count(*) from tasks").fetchone()[0] == 0


def test_feedback_event_repeat_projects_resolved_source_and_preserves_blank_source_facts(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    key = "feedback:resolution-projection"
    store.upsert_feedback_event(key=key, feedback_token="token-resolution")
    with store._connect() as db:
        item = db.execute(
            "select task.id from tasks task join feedback_processing_items item "
            "on item.task_id=task.id where item.feedback_key=?",
            (key,),
        ).fetchone()
        task_id = int(item["id"])
        db.execute(
            "update tasks set status='processing' where id=?",
            (task_id,),
        )
        db.execute(
            "update feedback_processing_items set workbench_task_id='workbench-task',"
            "workbench_turn_id='workbench-turn',attempt_id=31,agent_run_id=32,note='keep note',"
            "test_evidence_json=? where task_id=?",
            ('{"passed":true}', task_id),
        )
        assert resolve_feedback_events(db, [key], "source-resolved-at") == 1

    store.upsert_feedback_event(key=key, feedback_token="token-resolution", comment="resolved")
    with store._connect() as db:
        resolved = db.execute(
            "select task.status,item.resolved_at,item.workbench_task_id,item.workbench_turn_id,"
            "item.attempt_id,item.agent_run_id,item.note,item.test_evidence_json "
            "from tasks task join feedback_processing_items item on item.task_id=task.id "
            "where task.id=?",
            (task_id,),
        ).fetchone()
        assert tuple(resolved) == (
            "resolved", "source-resolved-at", "workbench-task", "workbench-turn", 31, 32,
            "keep note", '{"passed":true}',
        )
    item = store.list_user_feedback_items_by_keys([key])[0]
    assert item.processing_status == "resolved"
    assert item.resolved_at == "source-resolved-at"

    with store._connect() as db:
        db.execute(
            "update tasks set status='processing' where id=?",
            (task_id,),
        )
        db.execute(
            "update feedback_processing_items set resolved_at='native-current-resolution' "
            "where task_id=?",
            (task_id,),
        )
        assert reopen_feedback_event(db, key) == 1

    store.upsert_feedback_event(key=key, feedback_token="token-resolution", comment="reopened")
    with store._connect() as db:
        reopened = db.execute(
            "select task.status,item.resolved_at,item.workbench_task_id,item.workbench_turn_id,"
            "item.attempt_id,item.agent_run_id,item.note,item.test_evidence_json "
            "from tasks task join feedback_processing_items item on item.task_id=task.id "
            "where task.id=?",
            (task_id,),
        ).fetchone()
        assert tuple(reopened) == (
            "processing", "native-current-resolution", "workbench-task", "workbench-turn", 31, 32,
            "keep note", '{"passed":true}',
        )
    assert store.list_user_feedback_items_by_keys([key])[0].processing_status == "processing"


@pytest.mark.parametrize(
    ("resolved_at", "source_type", "source_value"),
    [
        (sqlite3.Binary(b"2026-10-23 10:00:00"), "blob", b"2026-10-23 10:00:00"),
        (sqlite3.Binary(b""), "blob", b""),
        ("2026-10-23 10:00:00", "text", "2026-10-23 10:00:00"),
        ("", "text", ""),
        ("   ", "text", "   "),
        ("\t", "text", "\t"),
    ],
)
@pytest.mark.parametrize("existing", [False, True])
def test_feedback_event_intake_preserves_source_resolved_value_and_sqlite_classification(
    tmp_path, resolved_at, source_type, source_value, existing,
):
    store = _target_store(tmp_path / f"feedback-raw-{existing}-{source_type}-{len(source_value)}.sqlite3")
    key = "feedback:raw-value"
    if existing:
        store.upsert_feedback_event(key=key, feedback_token="token-raw")
        with store._connect() as db:
            task_id = db.execute(
                "select task_id from feedback_processing_items where feedback_key=?", (key,),
            ).fetchone()[0]
            db.execute("update tasks set status='processing' where id=?", (task_id,))
            db.execute(
                "update feedback_processing_items set resolved_at='native-resolution' where task_id=?",
                (task_id,),
            )
            source_rowid = db.execute(
                "select cast(legacy_key as integer) from actions where fb_event_key=?", (key,),
            ).fetchone()[0]
            assert set_feedback_action(
                db, "feedback_events", source_rowid, {"resolved_at": resolved_at},
            ) == 1
    else:
        _add_feedback_event(store, key, resolved_at=resolved_at)
    with store._connect() as db:
        source = db.execute(
            f"select typeof(resolved_at),resolved_at from ({FEEDBACK_EVENT_SQL}) where key=?",
            (key,),
        ).fetchone()
        assert tuple(source) == (source_type, source_value)
        if existing:
            source_expected = _source_feedback_upsert_projection(
                db, key, current_status="processing",
                current_resolved_at="native-resolution",
            )
        else:
            source_expected = _source_feedback_upsert_projection(db, key)

    store.upsert_feedback_event(key=key, feedback_token="token-raw", comment="updated")
    with store._connect() as db:
        projected = db.execute(
            "select task.status,typeof(item.resolved_at),item.resolved_at "
            "from tasks task join feedback_processing_items item on item.task_id=task.id "
            "where item.feedback_key=?",
            (key,),
        ).fetchone()
        expected_status, expected_resolved_at = source_expected
        expected = (
            expected_status,
            "blob" if isinstance(expected_resolved_at, bytes) else "text",
            expected_resolved_at,
        )
        assert tuple(projected) == expected
        source_after = db.execute(
            f"select typeof(resolved_at),resolved_at from ({FEEDBACK_EVENT_SQL}) where key=?",
            (key,),
        ).fetchone()
        assert tuple(source_after) == (source_type, source_value)


def test_feedback_backlog_keeps_latest_token_and_manual_attempt_context(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    with store._connect() as db:
        sent = create_action(db, values={
            "conversation_id": "conversation",
            "trigger_message_id": "message",
            "prepared_body": "sent reply",
            "feedback_token": "token-latest",
            "status": "done",
            "delivery_channel": "dingtalk",
            "delivery_key": "conversation:message",
        })
        receipt_id = append_action_receipt(db, sent["id"], values={
            "operation": "send_reply_to_trigger",
            "target": "conversation:message",
            "status": "done",
            "dispatched_at": "2026-10-23T10:00:00Z",
            "observed_at": "2026-10-23T10:00:01Z",
        })
        assert record_action_result(
            db, sent["id"], expected={"delivery_receipt_id": None},
            changes={"delivery_receipt_id": receipt_id},
        ) is not None
        for attempt_id, final_reply, reviewer_feedback, corrected_reply in (
            (101, "earlier", "earlier review", "earlier correction"),
            (102, "latest", "latest review", "latest correction"),
            (103, "manual context", "manual review", "manual correction"),
        ):
            conversation_id = "manual-conversation" if attempt_id == 103 else "conversation"
            trigger_message_id = "manual-message" if attempt_id == 103 else "message"
            insert_action(db, "reply_attempts", {
                "id": attempt_id,
                "conversation_id": conversation_id,
                "conversation_title": "Conversation",
                "trigger_message_id": trigger_message_id,
                "trigger_sender": "sender",
                "trigger_text": "trigger context",
                "action": "send_reply",
                "sensitivity_kind": "general",
                "send_status": "sent",
                "final_reply_text": final_reply,
                "reviewer_feedback": reviewer_feedback,
                "corrected_reply_text": corrected_reply,
            })

    store.upsert_feedback_event(
        key="token-feedback", feedback_token="token-latest",
    )
    store.upsert_feedback_event(key="manual:103", feedback_token="")

    token_item = store.list_user_feedback_items_by_keys(["token-feedback"])[0]
    manual_item = store.list_user_feedback_items_by_keys(["manual:103"])[0]
    assert (token_item.attempt_id, token_item.final_reply_text) == (102, "latest")
    assert (token_item.reviewer_feedback, token_item.corrected_reply_text) == (
        "latest review", "latest correction",
    )
    assert token_item.attempt_role == ""
    assert (manual_item.attempt_id, manual_item.final_reply_text) == (
        103, "manual context",
    )
    assert (manual_item.reviewer_feedback, manual_item.corrected_reply_text) == (
        "", "",
    )


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
    with store._connect() as db:
        db.execute(
            "insert into run_failures(run_id,phase,structured_error_json,created_at) values(?,?,?,?)",
            (claimed.run.id, "prior_recovery", '{"code":"prior"}', "2026-10-23T11:59:00Z"),
        )

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
        prior_failures = db.execute(
            "select phase,structured_error_json,created_at from run_failures where run_id=?",
            (claimed.run.id,),
        ).fetchall()
        assert [tuple(row) for row in prior_failures] == [
            ("prior_recovery", '{"code":"prior"}', "2026-10-23T11:59:00Z"),
        ]


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


def test_expired_native_run_failure_does_not_create_failure_event(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("expired-run-message")))
    run = store.claim_agent_run(
        task.id, task.execution_generation, role="consumer",
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="worker-a", lease_seconds=1,
        now="2026-10-23T14:00:00Z",
    ).run
    with store._connect() as db:
        native_task = db.execute("select status, execution_generation from tasks where id=?", (task.id,)).fetchone()
        updated = task_entity_storage.update_task_cas(
            db, task_id=task.id,
            expected={"status": native_task["status"], "execution_generation": native_task["execution_generation"]},
            changes={"status": "processing"},
        )
        assert updated is not None

    failed = store.fail_expired_agent_run(
        run.id, {"code": "lease_expired"},
        expected_execution_generation=task.execution_generation,
        now="2026-10-23T14:00:02Z",
    )

    assert failed.status == "failed"
    with store._connect() as db:
        assert db.execute(
            "select count(*) from run_failures where run_id=?", (run.id,),
        ).fetchone()[0] == 0


def test_native_run_claim_rejections_and_expired_lease_reclaim_keep_source_contract(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("run-lease-message")))
    with pytest.raises(ValueError, match="reply task does not exist"):
        store.claim_agent_run(
            task.id + 1000, task.execution_generation, role="consumer",
            proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
            operation_id="", owner="worker-a", now="2026-10-23T15:00:00Z",
        )
    with pytest.raises(ValueError, match="execution generation mismatch"):
        store.claim_agent_run(
            task.id, "stale-generation", role="consumer",
            proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
            operation_id="", owner="worker-a", now="2026-10-23T15:00:00Z",
        )

    first = store.claim_agent_run(
        task.id, task.execution_generation, role="consumer",
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="worker-a", lease_seconds=30,
        now="2026-10-23T15:00:00Z",
    )
    assert first.claimed is True
    with pytest.raises(AgentRunLeaseLostError, match="lease lost"):
        store.renew_agent_run_lease(
            first.run.id, owner="worker-b", now="2026-10-23T15:00:01Z",
        )
    with pytest.raises(AgentRunLeaseLostError, match="lease lost"):
        store.renew_agent_run_lease(
            first.run.id, owner="worker-a", now="2026-10-23T15:00:31Z",
        )

    reclaimed = store.claim_agent_run(
        task.id, task.execution_generation, role="consumer",
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="worker-b", lease_seconds=30,
        now="2026-10-23T15:00:31Z",
    )
    assert reclaimed.claimed is True
    assert reclaimed.run.id == first.run.id
    assert reclaimed.run.lease_owner == "worker-b"
    with pytest.raises(AgentRunLeaseLostError, match="lease lost"):
        store.set_agent_run_session(
            first.run.id, "late-session", owner="worker-a",
            now="2026-10-23T15:00:32Z",
        )


def test_native_audit_rejection_can_anchor_only_the_next_consumer_revision(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("run-revision-message")))
    consumer = store.claim_agent_run(
        task.id, task.execution_generation, role="consumer",
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer-worker", now="2026-10-23T16:00:00Z",
    ).run
    consumer = store.complete_agent_run(
        consumer.id, {"outcome": "proposal", "revision": 0},
        owner="consumer-worker", now="2026-10-23T16:00:10Z",
    )
    candidate = store.adopted_candidate_for_consumer_run(consumer.id)
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role="audit",
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id="audit-op-0", owner="audit-worker", now="2026-10-23T16:00:20Z",
    ).run
    audit = store.complete_agent_run(
        audit.id, {"outcome": "reject", "candidate_digest": candidate["candidate_digest"]},
        owner="audit-worker", now="2026-10-23T16:00:30Z",
    )
    review = store.reviewed_candidate_for_audit_run(audit.id)
    with pytest.raises(ValueError, match="previous Audit turn"):
        store.claim_agent_run(
            task.id, task.execution_generation, role="consumer",
            proposal_revision=1, turn_attempt=0, parent_agent_run_id=consumer.id,
            operation_id="", owner="consumer-worker", now="2026-10-23T16:00:40Z",
        )

    revision = store.claim_agent_run(
        task.id, task.execution_generation, role="consumer",
        proposal_revision=1, turn_attempt=0, parent_agent_run_id=audit.id,
        operation_id="", owner="consumer-worker", now="2026-10-23T16:00:40Z",
    ).run
    with pytest.raises(ValueError, match="matching Consumer turn"):
        store.claim_agent_run(
            task.id, task.execution_generation, role="audit",
            proposal_revision=1, turn_attempt=0, parent_agent_run_id=consumer.id,
            operation_id="audit-op-1", owner="audit-worker", now="2026-10-23T16:00:41Z",
        )
    revised_audit = store.claim_agent_run(
        task.id, task.execution_generation, role="audit",
        proposal_revision=1, turn_attempt=0, parent_agent_run_id=revision.id,
        operation_id="audit-op-1", owner="audit-worker", now="2026-10-23T16:00:42Z",
    ).run
    assert revision.parent_agent_run_id == audit.id
    assert revised_audit.parent_agent_run_id == revision.id
    assert review["review_id"] != audit.id
    assert store.reviewed_candidate_for_audit_run(audit.id) == review


def test_new_task_generation_blocks_old_run_writes_without_rewriting_old_run(tmp_path):
    store = _target_store(tmp_path / "target.sqlite3")
    task = store.ensure_reply_task(**_creator_kwargs(_spec("run-generation-message")))
    run = store.claim_agent_run(
        task.id, task.execution_generation, role="consumer",
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="worker-a", now="2026-10-23T17:00:00Z",
    ).run
    with store._connect() as db:
        row = db.execute("select status, execution_generation from tasks where id=?", (task.id,)).fetchone()
        updated = task_entity_storage.update_task_cas(
            db, task_id=task.id,
            expected={"status": row["status"], "execution_generation": row["execution_generation"]},
            changes={"execution_generation": "next-generation"},
        )
        assert updated is not None

    with pytest.raises(AgentRunLeaseLostError, match="superseded"):
        store.renew_agent_run_lease(
            run.id, owner="worker-a", now="2026-10-23T17:00:01Z",
        )
    with store._connect() as db:
        current_task = db.execute("select execution_generation from tasks where id=?", (task.id,)).fetchone()[0]
        old_run = db.execute("select status, execution_generation, lease_owner from runs where id=?", (run.id,)).fetchone()
    assert current_task == "next-generation"
    assert tuple(old_run) == ("running", task.execution_generation, "worker-a")


def _add_feedback_event(store, key, *, resolved_at=""):
    with store._immediate_write_transaction() as db:
        insert_feedback_action(db, "feedback_events", {
            "key": key, "feedback_token": "token-" + key,
            "resolved_at": resolved_at,
        })


def test_native_feedback_batch_claim_replay_and_public_reads(tmp_path, monkeypatch):
    store = _target_store(tmp_path / "feedback-native.sqlite3")
    monkeypatch.setattr(store, "feedback_iteration_enabled", lambda: True)
    _add_feedback_event(store, "feedback-b")
    _add_feedback_event(store, "feedback-a")

    batch = store.create_feedback_processing_batch(
        [" feedback-b ", "feedback-a", "feedback-b"], batch_id=" batch-native "
    )
    assert batch.batch_id == "batch-native"
    assert batch.requested_count == 2
    assert store.create_feedback_processing_batch(
        ["feedback-a", "feedback-b"], batch_id="batch-native"
    ) == batch

    claimed = store.claim_feedback_processing_items(
        "batch-native", ["feedback-b", "feedback-a", "feedback-b"]
    )
    assert [item.feedback_key for item in claimed] == ["feedback-a", "feedback-b"]
    assert all(item.status == "processing" and item.current_round_id > 0 for item in claimed)
    replay = store.claim_feedback_processing_items(
        "batch-native", ["feedback-a", "feedback-b"]
    )
    assert replay == claimed
    assert store.get_feedback_processing_batch("batch-native").status == "processing"
    assert store.get_feedback_processing_item("feedback-a") == claimed[0]
    assert len(store.list_feedback_processing_rounds("feedback-a")) == 1
    assert len(store.list_feedback_processing_rounds_for_batch("batch-native")) == 2
    with store._connect() as db:
        assert db.execute("select count(*) from tasks where kind='feedback_processing'").fetchone()[0] == 2
        assert db.execute("select count(*) from feedback_processing_rounds").fetchone()[0] == 2
        assert db.execute("select count(*) from actions where record_type='feedback_processing_transitions'").fetchone()[0] == 2
        ownership = db.execute(
            "select task.kind,task.status,typeof(task.parameters_json),json(task.parameters_json), "
            "item.feedback_key,item.batch_id from tasks task join feedback_processing_items item "
            "on item.task_id=task.id order by item.feedback_key"
        ).fetchall()
        assert [tuple(row) for row in ownership] == [
            ("feedback_processing", "processing", "blob", "{}", "feedback-a", "batch-native"),
            ("feedback_processing", "processing", "blob", "{}", "feedback-b", "batch-native"),
        ]


def test_native_feedback_completion_decision_resolve_reopen_cycle(tmp_path, monkeypatch):
    from app.feedback_processing import (
        FeedbackIterationAcceptance, FeedbackIterationDecision, ResolutionEvidence,
    )

    store = _target_store(tmp_path / "feedback-native-completion.sqlite3")
    monkeypatch.setattr(store, "feedback_iteration_enabled", lambda: True)
    for key in ("feedback-a", "feedback-b"):
        _add_feedback_event(store, key)
    store.create_feedback_processing_batch(["feedback-a", "feedback-b"], batch_id="batch-a")
    claimed = store.claim_feedback_processing_items("batch-a", ["feedback-a", "feedback-b"])
    for item in claimed:
        empty = store.associate_feedback_processing_turn(
            item.feedback_key, expected_batch_id="batch-a",
            workbench_task_id="", workbench_turn_id="", attempt_id=0, agent_run_id=0,
        )
        assert empty.workbench_task_id == empty.workbench_turn_id == ""
        store.associate_feedback_processing_turn(
            item.feedback_key, expected_batch_id="batch-a",
            workbench_task_id="wb-task", workbench_turn_id="wb-turn",
            attempt_id=13, agent_run_id=27,
        )
        store.patch_feedback_processing_item_evidence(
            item.feedback_key, commit_sha="a" * 40,
            test_evidence={"pytest": {"exit_code": 0}},
            restart_evidence={"launchd_label": "com.ceo-agent-service.main",
                              "before_pid": 10, "after_pid": 11},
            health_evidence={"url": "http://127.0.0.1:8765/healthz",
                             "status_code": 200, "ok": True},
        )
        before = store.get_feedback_processing_item(item.feedback_key)
        store.patch_feedback_processing_item_evidence(
            item.feedback_key, commit_sha="a" * 40,
            test_evidence={"pytest": {"exit_code": 0}},
            restart_evidence={"launchd_label": "com.ceo-agent-service.main",
                              "before_pid": 10, "after_pid": 11},
            health_evidence={"url": "http://127.0.0.1:8765/healthz",
                             "status_code": 200, "ok": True},
        )
        assert store.get_feedback_processing_item(item.feedback_key).updated_at == before.updated_at
        store.patch_feedback_processing_item_evidence(
            item.feedback_key, test_evidence={"pytest": {"exit_code": 0, "cases": 1}},
        )
        after_change = store.get_feedback_processing_item(item.feedback_key)
        with store._connect() as db:
            round_evidence = db.execute(
                "select test_evidence_json,updated_at from feedback_processing_rounds "
                "where id=?", (after_change.current_round_id,),
            ).fetchone()
        assert json.loads(round_evidence["test_evidence_json"]) == after_change.test_evidence
        assert after_change.updated_at and round_evidence["updated_at"]
    decision = FeedbackIterationDecision(
        scope="code", root_cause="verified source defect",
        feedback_keys=["feedback-a", "feedback-b"],
        source_references=["source record"], why_not_code="not applicable",
        acceptance=FeedbackIterationAcceptance(
            scenario="both feedback items", expected_behavior="fixed behavior",
            verification=["focused test"],
        ),
    )
    record = store.record_feedback_iteration_decision(
        "batch-a", decision, workbench_task_id="wb-task", workbench_turn_id="wb-turn",
    )
    assert set(record.feedback_keys) == {"feedback-a", "feedback-b"}
    assert len(record.round_ids) == 2
    assert len(store.list_feedback_iteration_decisions("batch-a")) == 1
    with store._connect() as db:
        assert db.execute("select count(*) from tasks where kind='feedback_processing'").fetchone()[0] == 2
        assert db.execute("select count(*) from feedback_processing_rounds").fetchone()[0] == 2
        assert db.execute("select count(*) from feedback_iteration_decisions").fetchone()[0] == 1
        assert db.execute(
            "select count(*) from actions where record_type='feedback_iteration_decision_items'"
        ).fetchone()[0] == 2
        assert db.execute(
            "select count(*) from tasks task join feedback_processing_items item "
            "on item.task_id=task.id where task.status='processing'"
        ).fetchone()[0] == 2
    receipt = ResolutionEvidence(
        commit_sha="a" * 40,
        test_evidence={"pytest": {"exit_code": 0, "cases": 1}},
        restart_evidence={"launchd_label": "com.ceo-agent-service.main",
                          "before_pid": 10, "after_pid": 11},
        health_evidence={"url": "http://127.0.0.1:8765/healthz",
                         "status_code": 200, "ok": True},
        backlog_evidence={"processing": 0, "failed": 0, "retryable": 0},
        associations={key: {"workbench_task_id": "wb-task", "workbench_turn_id": "wb-turn",
                            "attempt_id": 13, "agent_run_id": 27}
                      for key in ("feedback-a", "feedback-b")},
    )
    assert store.resolve_feedback_processing_batch(
        "batch-a", receipt, commit_is_ancestor=True,
    ) is True
    assert store.resolve_feedback_processing_batch(
        "batch-a", None, commit_is_ancestor=True,
    ) is True
    assert store.resolve_feedback_processing_batch(
        "batch-a", receipt, commit_is_ancestor=True,
    ) is True
    reopened = store.reopen_feedback_processing_item("feedback-a", reason="confirmed follow-up")
    assert reopened.status == "pending" and reopened.current_round_id == 0
    assert store.get_feedback_processing_item("feedback-b").status == "resolved"
    store.create_feedback_processing_batch(["feedback-a"], batch_id="batch-b")
    next_round = store.claim_feedback_processing_items("batch-b", ["feedback-a"])[0]
    assert next_round.status == "processing"
    assert next_round.current_round_id != record.round_ids[0]


def test_native_feedback_create_and_capability_refusals_are_atomic(tmp_path, monkeypatch):
    store = _target_store(tmp_path / "feedback-native-refusal.sqlite3")
    monkeypatch.setattr(store, "feedback_iteration_enabled", lambda: True)
    _add_feedback_event(store, "open")
    _add_feedback_event(store, "other")
    _add_feedback_event(store, "resolved", resolved_at="resolved-clock")

    with pytest.raises(ValueError, match="feedback processing batch"):
        store.create_feedback_processing_batch(["open", "missing"], batch_id="missing")
    with pytest.raises(ValueError, match="feedback processing batch"):
        store.create_feedback_processing_batch(["resolved"], batch_id="resolved")
    with store._connect() as db:
        assert db.execute("select count(*) from feedback_processing_batches").fetchone()[0] == 0
        assert db.execute("select count(*) from tasks where kind='feedback_processing'").fetchone()[0] == 0

    store.create_feedback_processing_batch(["open"], batch_id="disabled")
    with pytest.raises(ValueError, match="feedback processing batch"):
        store.create_feedback_processing_batch(["other"], batch_id="disabled")
    with pytest.raises(ValueError, match="feedback processing claim"):
        store.claim_feedback_processing_items("other-batch", ["open"])
    with store._connect() as db:
        assert db.execute(
            "select count(*) from feedback_processing_batches where batch_id='other-batch'"
        ).fetchone()[0] == 0
        assert db.execute(
            "select batch_id from feedback_processing_items where feedback_key='open'"
        ).fetchone()[0] == "disabled"
    monkeypatch.setattr(store, "feedback_iteration_enabled", lambda: False)
    with pytest.raises(Exception, match="feedback iteration is disabled"):
        store.claim_feedback_processing_items("disabled", ["open"])
    with store._connect() as db:
        assert db.execute("select status from feedback_processing_batches where batch_id='disabled'").fetchone()[0] == "pending"
        assert db.execute("select status from tasks where kind='feedback_processing'").fetchone()[0] == "pending"
        assert db.execute("select count(*) from feedback_processing_rounds").fetchone()[0] == 0


def _existing_target_store(path):
    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = path
    store.busy_timeout_seconds = 5
    store.busy_timeout_milliseconds = 5000
    store._read_snapshot_connection = ContextVar(f"existing_read_snapshot_{id(store)}", default=None)
    store._reused_connection = ContextVar(f"existing_reused_connection_{id(store)}", default=None)
    return store


def test_native_feedback_competing_connections_have_one_claim_winner(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from threading import Barrier, Condition, current_thread

    path = tmp_path / "feedback-native-compete.sqlite3"
    first = _target_store(path)
    second = _existing_target_store(path)
    monkeypatch.setattr(first, "feedback_iteration_enabled", lambda: True)
    monkeypatch.setattr(second, "feedback_iteration_enabled", lambda: True)
    _add_feedback_event(first, "race")
    with first._immediate_write_transaction() as db:
        task = task_entity_storage.create_task(
            db, values={"kind": "feedback_processing", "status": "pending",
                        "created_at": "2026-10-23 12:00:00",
                        "updated_at": "2026-10-23 12:00:00"},
        )
        db.execute(
            "insert into feedback_processing_items(task_id,feedback_key,batch_id,workbench_task_id,workbench_turn_id,attempt_id,agent_run_id,commit_sha,test_evidence_json,restart_evidence_json,health_evidence_json,note,resolved_at,current_round_id) "
            "values(?,'race','', '', '',0,0,'','{}','{}','{}','','',0)",
            (task["id"],),
        )

    def claim(store, batch):
        return store.claim_feedback_processing_items(batch, ["race"])[0].batch_id

    transaction_boundary = Barrier(2)
    begin_condition = Condition()
    begin_attempts = []

    def instrument_claim_boundary(store):
        original_connect = store._connect
        original_transaction = store._immediate_write_transaction

        class TracedConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def execute(self, statement, *parameters):
                if statement.strip().casefold() == "begin immediate":
                    with begin_condition:
                        begin_attempts.append(current_thread().name)
                        begin_condition.notify_all()
                return self.connection.execute(statement, *parameters)

        @contextmanager
        def traced_connect():
            with original_connect() as db:
                yield TracedConnection(db)

        @contextmanager
        def synchronized_transaction():
            transaction_boundary.wait(timeout=5)
            with original_transaction() as db:
                yield db

        monkeypatch.setattr(store, "_connect", traced_connect)
        monkeypatch.setattr(store, "_immediate_write_transaction", synchronized_transaction)

    instrument_claim_boundary(first)
    instrument_claim_boundary(second)

    blocker = sqlite3.connect(path, timeout=5, isolation_level=None)
    blocker.execute("begin immediate")
    blocker_held = True
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(claim, first, "winner-a"),
                pool.submit(claim, second, "winner-b"),
            ]
            with begin_condition:
                both_attempted = begin_condition.wait_for(
                    lambda: len(begin_attempts) == 2, timeout=5,
                )
            if both_attempted:
                # The blocker still owns SQLite's write lock. Both wrappers
                # observed their actual BEGIN IMMEDIATE call before delegating
                # to SQLite, so both native claims overlap at the write lock.
                assert not any(future.done() for future in futures)
                assert len(set(begin_attempts)) == 2
            blocker.execute("rollback")
            blocker.close()
            blocker_held = False
    finally:
        if blocker_held:
            blocker.execute("rollback")
            blocker.close()

    assert both_attempted, (
        f"both claims must attempt BEGIN IMMEDIATE while blocked; attempts={begin_attempts}"
    )

    outcomes = []
    for future in futures:
        try:
            outcomes.append(("won", future.result()))
        except ValueError:
            outcomes.append(("lost", None))
    assert sorted(kind for kind, _ in outcomes) == ["lost", "won"]
    with first._connect() as db:
        winner = db.execute("select batch_id from feedback_processing_items where feedback_key='race'").fetchone()[0]
        assert winner in {"winner-a", "winner-b"}
        assert db.execute("select count(*) from feedback_processing_rounds where feedback_key='race'").fetchone()[0] == 1
        assert db.execute("select count(*) from actions where record_type='feedback_processing_transitions' and fb_transition_feedback_key='race'").fetchone()[0] == 1


def test_native_feedback_reopened_history_claims_next_round(tmp_path, monkeypatch):
    store = _target_store(tmp_path / "feedback-native-reopen.sqlite3")
    monkeypatch.setattr(store, "feedback_iteration_enabled", lambda: True)
    _add_feedback_event(store, "reopened")
    with store._immediate_write_transaction() as db:
        task = task_entity_storage.create_task(
            db, values={"kind": "feedback_processing", "status": "pending",
                        "created_at": "2026-10-23 12:00:00",
                        "updated_at": "2026-10-23 12:00:00"},
        )
        db.execute(
            "insert into feedback_processing_items(task_id,feedback_key,batch_id,workbench_task_id,workbench_turn_id,attempt_id,agent_run_id,commit_sha,test_evidence_json,restart_evidence_json,health_evidence_json,note,resolved_at,current_round_id) "
            "values(?,'reopened','', '', '',0,0,'','{}','{}','{}','','',0)",
            (task["id"],),
        )
        db.execute(
            "insert into feedback_processing_rounds(feedback_key,round_number,batch_id,status,receipt_version,started_at,resolved_at,reopened_at,reopen_reason) "
            "values('reopened',1,'old-batch','resolved',2,'start','resolve','reopen','confirmed reopen')"
        )

    item = store.claim_feedback_processing_items("next-batch", ["reopened"])[0]
    rounds = store.list_feedback_processing_rounds("reopened")
    assert item.status == "processing"
    assert item.current_round_id == rounds[0].id
    assert rounds[0].round_number == 2
    assert rounds[1].reopened_at == "reopen"
    assert rounds[1].reopen_reason == "confirmed reopen"


def test_native_feedback_replay_rejects_malformed_current_round_lineage(tmp_path, monkeypatch):
    store = _target_store(tmp_path / "feedback-native-malformed.sqlite3")
    monkeypatch.setattr(store, "feedback_iteration_enabled", lambda: True)
    _add_feedback_event(store, "malformed")
    store.create_feedback_processing_batch(["malformed"], batch_id="malformed-batch")
    store.claim_feedback_processing_items("malformed-batch", ["malformed"])
    with store._immediate_write_transaction() as db:
        db.execute(
            "update feedback_processing_rounds set status='resolved' "
            "where feedback_key='malformed'"
        )
    with pytest.raises(ValueError, match="feedback processing claim"):
        store.claim_feedback_processing_items("malformed-batch", ["malformed"])
    with store._connect() as db:
        assert db.execute("select count(*) from feedback_processing_rounds").fetchone()[0] == 1
        assert db.execute(
            "select count(*) from actions where record_type='feedback_processing_transitions' "
            "and fb_transition_feedback_key='malformed'"
        ).fetchone()[0] == 1


@pytest.mark.parametrize("scope", ["skill_only", "runtime_config", "mixed"])
def test_native_feedback_supported_config_scopes_use_actual_load_receipts(
    tmp_path, scope,
):
    from app.feedback_processing import (
        FeedbackIterationAcceptance, FeedbackIterationDecision, ResolutionEvidence,
    )

    store = _target_store(tmp_path / f"feedback-native-{scope}.sqlite3")
    skill = revision_one = revision_two = None
    if scope in {"skill_only", "mixed"}:
        skill = store.create_managed_skill("scope-test", "Scope Test")
        revision_one = store.create_managed_skill_revision(
            skill.id,
            "---\nname: scope-test\ndescription: Test managed Skill\n"
            "metadata:\n  managed_by: ceo-agent-service\n---\n\n# One\n",
            source="settings",
        )
        revision_two = store.create_managed_skill_revision(
            skill.id,
            "---\nname: scope-test\ndescription: Test managed Skill\n"
            "metadata:\n  managed_by: ceo-agent-service\n---\n\n# Two\n",
            source="settings",
        )
        previous_bindings = {skill.id: revision_one.id}
        target_bindings = {skill.id: revision_two.id}
    else:
        previous_bindings = target_bindings = {}
    previous_config = store.create_runtime_skill_config(
        previous_bindings, expected_parent_id=None,
    )
    store.record_runtime_skill_load(
        previous_config.id, pid=201, loaded=(
            {skill.id: revision_one.sha256} if skill is not None else {}
        ),
    )
    target_config = store.create_runtime_skill_config(
        target_bindings, expected_parent_id=previous_config.id,
    )
    target_load = store.record_runtime_skill_load(
        target_config.id, pid=202, loaded=(
            {skill.id: revision_two.sha256} if skill is not None else {}
        ),
    )

    key = f"feedback-{scope}"
    _add_feedback_event(store, key)
    batch_id = f"batch-{scope}"
    store.create_feedback_processing_batch([key], batch_id=batch_id)
    item = store.claim_feedback_processing_items(batch_id, [key])[0]
    store.associate_feedback_processing_turn(
        key, expected_batch_id=batch_id,
        workbench_task_id=f"task-{scope}", workbench_turn_id=f"turn-{scope}",
        attempt_id=21, agent_run_id=31,
    )
    commit_sha = "c" * 40 if scope == "mixed" else ""
    test_evidence = {"focused": {"exit_code": 0}}
    restart_evidence = {
        "launchd_label": "com.ceo-agent-service.main",
        "before_pid": 10, "after_pid": 11,
    }
    health_evidence = {
        "url": "http://127.0.0.1:8765/healthz", "status_code": 200, "ok": True,
    }
    store.patch_feedback_processing_item_evidence(
        key, commit_sha=commit_sha, test_evidence=test_evidence,
        restart_evidence=restart_evidence, health_evidence=health_evidence,
    )
    target_skill_revisions = (
        [{"skill_id": skill.id, "from_revision": revision_one.id,
          "to_revision": revision_two.id}]
        if skill is not None else []
    )
    target_runtime_config_id = (
        target_config.id if scope in {"runtime_config", "mixed"} else None
    )
    decision = FeedbackIterationDecision(
        scope=scope, root_cause="verified managed configuration change",
        feedback_keys=[key], source_references=["actual record APIs"],
        target_skill_revisions=target_skill_revisions,
        target_runtime_config_id=target_runtime_config_id,
        why_not_code="The existing application code already supports this behavior.",
        acceptance=FeedbackIterationAcceptance(
            scenario="current feedback", expected_behavior="loaded configured state",
            verification=["native scope receipt"],
        ),
    )
    store.record_feedback_iteration_decision(
        batch_id, decision, workbench_task_id=f"task-{scope}",
        workbench_turn_id=f"turn-{scope}",
    )
    evidence = ResolutionEvidence(
        commit_sha=commit_sha, test_evidence=test_evidence,
        restart_evidence=restart_evidence, health_evidence=health_evidence,
        backlog_evidence={"processing": 0, "failed": 0, "retryable": 0},
        runtime_config_id=target_config.id,
        previous_runtime_config_id=previous_config.id,
        load_receipt_id=target_load.id,
        skill_revisions=(
            [{"skill_id": skill.id, "revision_id": revision_two.id,
              "sha256": revision_two.sha256}]
            if skill is not None else []
        ),
        associations={key: {
            "workbench_task_id": f"task-{scope}",
            "workbench_turn_id": f"turn-{scope}",
            "attempt_id": 21, "agent_run_id": 31,
        }},
    )
    assert store.resolve_feedback_processing_batch(
        batch_id, evidence, commit_is_ancestor=(scope == "mixed"),
    ) is True
    assert store.resolve_feedback_processing_batch(batch_id, None, commit_is_ancestor=False) is True
    with store._connect() as db:
        row = db.execute(
            "select scope_receipt_json from feedback_processing_rounds where id=?",
            (item.current_round_id,),
        ).fetchone()
        native_receipt = json.loads(row["scope_receipt_json"])
        assert native_receipt["scope"] == scope
        assert native_receipt["evidence"]["load_receipt_id"] == target_load.id
        assert db.execute(
            "select status from tasks where id=(select task_id from feedback_processing_items "
            "where feedback_key=?)", (key,),
        ).fetchone()[0] == "resolved"
    alternate_load = store.record_runtime_skill_load(
        target_config.id, pid=203, loaded=(
            {skill.id: revision_two.sha256} if skill is not None else {}
        ),
    )
    assert alternate_load.id != target_load.id
    with pytest.raises(ValueError, match="scope receipt"):
        store.resolve_feedback_processing_batch(
            batch_id, evidence.model_copy(update={"load_receipt_id": alternate_load.id}),
            commit_is_ancestor=(scope == "mixed"),
        )
    assert store.get_feedback_processing_batch(batch_id).status == "resolved"


def test_native_feedback_needs_human_decision_remains_unresolvable(tmp_path):
    from app.feedback_processing import (
        FeedbackIterationAcceptance, FeedbackIterationDecision, ResolutionEvidence,
    )

    store = _target_store(tmp_path / "feedback-native-needs-human.sqlite3")
    _add_feedback_event(store, "feedback-needs-human")
    store.create_feedback_processing_batch(
        ["feedback-needs-human"], batch_id="needs-human-batch",
    )
    store.claim_feedback_processing_items(
        "needs-human-batch", ["feedback-needs-human"],
    )
    store.associate_feedback_processing_turn(
        "feedback-needs-human", expected_batch_id="needs-human-batch",
        workbench_task_id="task-needs-human", workbench_turn_id="turn-needs-human",
        attempt_id=22, agent_run_id=32,
    )
    store.patch_feedback_processing_item_evidence(
        "feedback-needs-human", commit_sha="d" * 40,
        test_evidence={"focused": {"exit_code": 0}},
        restart_evidence={"launchd_label": "com.ceo-agent-service.main",
                          "before_pid": 12, "after_pid": 13},
        health_evidence={"url": "http://127.0.0.1:8765/healthz",
                         "status_code": 200, "ok": True},
    )
    decision = FeedbackIterationDecision(
        scope="needs_human", root_cause="requires human judgment",
        feedback_keys=["feedback-needs-human"], source_references=["actual record APIs"],
        why_not_code="The decision needs a human owner.",
        acceptance=FeedbackIterationAcceptance(
            scenario="current feedback", expected_behavior="remain unresolved",
            verification=["human decision"],
        ),
    )
    store.record_feedback_iteration_decision(
        "needs-human-batch", decision,
        workbench_task_id="task-needs-human", workbench_turn_id="turn-needs-human",
    )
    evidence = ResolutionEvidence(
        commit_sha="d" * 40, test_evidence={"focused": {"exit_code": 0}},
        restart_evidence={"launchd_label": "com.ceo-agent-service.main",
                          "before_pid": 12, "after_pid": 13},
        health_evidence={"url": "http://127.0.0.1:8765/healthz",
                         "status_code": 200, "ok": True},
        backlog_evidence={"processing": 0, "failed": 0, "retryable": 0},
    )
    with pytest.raises(ValueError, match="needs_human feedback iteration decisions cannot resolve"):
        store.resolve_feedback_processing_batch(
            "needs-human-batch", evidence, commit_is_ancestor=True,
        )
    assert store.get_feedback_processing_batch("needs-human-batch").status == "processing"
    assert store.get_feedback_processing_item("feedback-needs-human").status == "processing"
