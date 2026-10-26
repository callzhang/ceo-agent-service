from tests.sent_fixtures import historical_sent_delivery

from tests.test_email_legacy_schema import published_source as published_source
import errno
import hashlib
import importlib.util
import inspect
import json
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from multiprocessing import get_context
from pathlib import Path
from queue import Queue
from threading import Barrier, Event, Thread, get_ident

import pytest

import app.store as store_module
from tests.wechat.support import approve_delivery
from tests.support.native_run_rows import (
    insert_native_run as _insert_native_run,
    insert_run_row as _insert_run_row,
    insert_runtime_attempt_row as _insert_runtime_attempt_row,
    insert_scheduled_occurrence as _insert_scheduled_occurrence,
)
from tests.reply_schema_fixtures import assert_store_reopen_rejected_without_mutation
from tests.run_reader_fixtures import assert_no_core_native_storage, insert_native_run_fixture
from app.action_storage import NATIVE_EXTERNAL_ACTION_RESULT_SQL as EXTERNAL_ACTION_RESULT_SQL, REPLY_ATTEMPT_COMMON_SQL, SENT_REPLY_SQL, SENT_REPLY_OBSERVER_SQL, insert_action, update_action
from app.action_storage import insert_email_action, update_email_action, error_set_parts, insert_error_action
from app.store import AGENT_RUN_SQL, TASK_LOGICAL_RUN_SQL

RUNTIME_ATTEMPT_SQL = "select *, parent_id as agent_run_id from runtime_attempts"
from app.store import ERROR_SQL, WORK_SUMMARY_INPUT_SQL
from app.state_storage import update_state_rows, insert_state
from app import task_lifecycle_storage as task_claims
from app.state_definitions import state_projection
from app.business_storage import insert_business
from app.business_candidate_storage import insert_business_candidate
from app.business_candidate_definitions import candidate_projection
from app.store import (
    REPLY_ATTEMPT_CLOSED_AFTER_REVIEW,
    AgentRole,
    AgentRunLeaseLostError,
    AutoReplyStore,
)
from tests.wechat.support import create_wechat_delivery


class _Inserted:
    def __init__(self, rowid):
        self.lastrowid = rowid


_TASK_COLUMNS = frozenset({
    "status", "error", "attempts", "available_at", "locked_at", "updated_at",
    "execution_generation", "claimed_input_version", "input_version",
})


RUN_STATE_EVENT_SQL = "select id, run_id as agent_run_id, phase, structured_error_json from run_failures"


def _bind_business_object_key(db, business_key, owner_task_id):
    """Point one business key at the Task that now owns it."""
    from app.task_entity_storage import associate_business_key

    db.execute("delete from task_business_keys where business_key=?", (business_key,))
    associate_business_key(db, task_id=owner_task_id, business_key=business_key)


def _insert_current_task8(db, kind, values):
    """Create one native reply Task with its first TaskInput."""
    assert kind == "reply_tasks", kind
    spec = {name: values[name] for name in (
        "channel", "conversation_id", "conversation_title", "single_chat",
        "trigger_message_id", "trigger_create_time", "trigger_sender", "trigger_text",
        "trigger_message_json", "oa_url", "business_object_key",
    ) if name in values}
    spec.setdefault("trigger_text", "")
    spec["single_chat"] = bool(spec.get("single_chat", 0))
    task, _ = store_module.AutoReplyStore._ensure_business_reply_task(
        db, store_module.ReplyTaskSpec(**spec),
    )
    changes = {name: value for name, value in values.items() if name in _TASK_COLUMNS}
    if changes:
        db.execute(
            "update tasks set " + ",".join(f"{name}=?" for name in changes) + " where id=?",
            (*changes.values(), task.id),
        )
    return _Inserted(task.id)


_TASK_KINDS = {
    "reply_tasks": "reply", "okr_review_requests": "okr_review",
    "meeting_alignment_jobs": "meeting_alignment", "work_summary_inputs": "work_summary",
}


def _update_current_task8(db, kind, values, *, where_sql="", parameters=(), update_sql=None, structural_sql=None):
    """Update native Tasks; non-Task fields go to the owner that stores them."""
    from app import task_entity_storage

    update_sql = update_sql or {}
    structural_sql = structural_sql or {}
    if kind == "reply_task_inputs":
        return db.execute(
            "update task_inputs set " + ",".join(f"{k}=?" for k in values)
            + " where 1=1 " + where_sql, (*values.values(), *parameters),
        )
    ids = [row[0] for row in db.execute(
        "select id from tasks where kind=? " + where_sql, (_TASK_KINDS[kind], *parameters),
    ).fetchall()]
    task_values = {k: v for k, v in values.items() if k in _TASK_COLUMNS}
    other = {k: v for k, v in values.items() if k not in _TASK_COLUMNS}
    assignments = [f"{name}=?" for name in task_values]
    args = list(task_values.values())
    assignments += [f"{name}={expression}" for name, expression in update_sql.items()]
    assignments += [f"{name}={expression}" for name, expression in structural_sql.items()]
    cursor = None
    for task_id in ids:
        if assignments:
            cursor = db.execute(
                "update tasks set " + ",".join(assignments) + " where id=?", (*args, task_id),
            )
        if other and kind == "reply_tasks":
            cursor = db.execute(
                "update task_inputs set " + ",".join(f"{k}=?" for k in other)
                + " where task_id=?", (*other.values(), task_id),
            )
        elif other and kind == "work_summary_inputs":
            cursor = db.execute(
                "update work_summary_inputs set " + ",".join(f"{k}=?" for k in other)
                + " where task_id=?", (*other.values(), task_id),
            )
        elif other:
            task = task_entity_storage.get_task(db, task_id)
            cursor = db.execute(
                "update tasks set parameters_json=? where id=?",
                (task_entity_storage._parameters_blob(db, {**task["parameters"], **other}), task_id),
            )
    return cursor if cursor is not None else db.execute("select 1 where 0")


def test_unsubscribe_page_parent_allows_only_retryable_terminal_receipts(tmp_path):
    from app.email_store import EmailStore
    from tests.test_email_action_storage import _cases

    path = tmp_path / "unsubscribe-parent.sqlite3"
    store = AutoReplyStore(path)
    EmailStore(path)
    with store._connect() as db:
        # The parent check under test reads the Task and its receipt, not the plan ancestry.
        db.execute("pragma foreign_keys=off")
        identity = "email-action:" + "a" * 64
        key = "email-unsubscribe-page:" + identity + ":" + "b" * 64
        _insert_current_task8(db, "reply_tasks", {
            "channel": "email", "conversation_id": "email-thread",
            "conversation_title": "Email", "single_chat": 0,
            "trigger_message_id": identity, "trigger_create_time": "",
            "trigger_sender": "", "status": "done",
        })
        assert not store_module.AutoReplyStore._runtime_operation_parent_exists(
            db, "email_unsubscribe_page", key
        )
        receipt = insert_email_action(
            db, "email_unsubscribe_receipts",
            _cases()["email_unsubscribe_receipts"] | {
                "action_identity": identity,
                "outcome": "skipped_no_reliable_entry",
            },
        )
        assert store_module.AutoReplyStore._runtime_operation_parent_exists(
            db, "email_unsubscribe_page", key
        )
        update_email_action(db, "email_unsubscribe_receipts", receipt.lastrowid, {"outcome": "done"})
        assert not store_module.AutoReplyStore._runtime_operation_parent_exists(
            db, "email_unsubscribe_page", key
        )
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'failed'},
        )
        update_email_action(
            db, "email_unsubscribe_receipts", receipt.lastrowid,
            {"outcome": "skipped_no_reliable_entry"},
        )
        assert not store_module.AutoReplyStore._runtime_operation_parent_exists(
            db, "email_unsubscribe_page", key
        )


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    (("done", "done"), ("skipped_no_reliable_entry", "skipped")),
)
def test_startup_reconciles_failed_unsubscribe_task_from_terminal_receipt(
    tmp_path, outcome, expected_status,
):
    from app.email_store import EmailStore
    from tests.test_email_action_storage import _cases

    path = tmp_path / "unsubscribe-startup.sqlite3"
    store = AutoReplyStore(path)
    EmailStore(path)
    identity = "email-action:" + "c" * 64
    with store._connect() as db:
        # This fixture exercises the startup projection from a persisted receipt;
        # provider-plan ancestry is outside the recovery query under test.
        db.execute("pragma foreign_keys=off")
        task_id = _insert_current_task8(db, "reply_tasks", {
            "channel": "email", "conversation_id": "email-thread",
            "conversation_title": "Email", "single_chat": 0,
            "trigger_message_id": identity, "trigger_create_time": "",
            "trigger_sender": "", "status": "failed",
            "error": "worker_interrupted",
        }).lastrowid
        insert_email_action(
            db, "email_unsubscribe_receipts",
            _cases()["email_unsubscribe_receipts"] | {
                "action_identity": identity,
                "outcome": outcome,
            },
        )

    assert store.reconcile_failed_email_unsubscribe_tasks_with_terminal_receipts() == 1
    with store._connect() as db:
        task = db.execute(
            f"select status, error from ({store_module.REPLY_TASK_SQL}) where id=?",
            (task_id,),
        ).fetchone()
    assert tuple(task) == (expected_status, "")
    assert store.reconcile_failed_email_unsubscribe_tasks_with_terminal_receipts() == 0


def test_agent_authored_message_requires_verified_provider_readback(tmp_path):
    store = AutoReplyStore(tmp_path / "agent-message-origin.sqlite3")

    def provider_result(state, message_id):
        return json.dumps({
            "verification": {
                "state": state,
                "status_result": {
                    "result": {"openMessageId": message_id, "sendStatus": "SUCCESS"},
                },
            },
        })

    store.record_sent_reply(
        "conversation", "incoming-trigger", "AI-generated body",
        send_result_json=provider_result("sent", "verified-outgoing-id"),
    )
    store.record_sent_reply(
        "conversation", "other-trigger", "Unverified body",
        send_result_json=provider_result("unknown", "unverified-outgoing-id"),
    )

    assert store.is_verified_agent_authored_message(
        "conversation", "verified-outgoing-id", channel="dingtalk"
    )
    assert not store.is_verified_agent_authored_message(
        "conversation", "unverified-outgoing-id", channel="dingtalk"
    )
    assert not store.is_verified_agent_authored_message(
        "another-conversation", "verified-outgoing-id", channel="dingtalk"
    )


def _sqlite_failure(
    message: str,
    *,
    code: int,
    name: str,
    error_type: type[sqlite3.Error] = sqlite3.OperationalError,
) -> sqlite3.Error:
    error = error_type(message)
    error.sqlite_errorcode = code
    error.sqlite_errorname = name
    return error


def _store_without_initialization(path: Path) -> AutoReplyStore:
    store = AutoReplyStore.__new__(AutoReplyStore)
    store.path = path
    store.busy_timeout_seconds = store_module.SQLITE_BUSY_TIMEOUT_SECONDS
    store.busy_timeout_milliseconds = store.busy_timeout_seconds * 1000
    store._read_snapshot_connection = ContextVar(
        f"test_read_snapshot_{id(store)}", default=None
    )
    store._reused_connection = ContextVar(f"test_reused_connection_{id(store)}", default=None)
    return store


def _write_native_run_events(
    store: AutoReplyStore,
    run_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    paths: dict[str, Path],
) -> None:
    """Materialize a test runtime's observed stream before terminal eviction."""
    from app import native_trajectory

    events = native_trajectory.live_events(str(store.path.resolve()), run_id) or []
    records = [{"type": "event_msg", "payload": {"type": "task_started"}}]
    for index, event in enumerate(events):
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        native_type = {
            "command_execution": "CommandExecution",
            "agent_message": "AgentMessage",
        }.get(item.get("type"))
        if native_type is None:
            continue
        records.append({
            "type": "event_msg",
            "payload": {"type": "item_completed", "item": {
                **item, "id": item.get("id") or f"item-{index}", "type": native_type,
            }},
        })
    records.append({"type": "event_msg", "payload": {"type": "task_complete"}})
    session_id = f"test-run-{run_id}"
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    paths[session_id] = path
    monkeypatch.setattr(
        native_trajectory,
        "find_codex_session_path",
        lambda session_id, **kwargs: paths.get(session_id),
    )
    with store._connect() as db:
        db.execute(
            "update runs set session_id=?, transcript_start=0, transcript_end=? where id=?",
            (session_id, len(records), run_id),
        )


def test_prepare_outbound_postfix_reuses_final_body_when_candidate_or_config_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "outbound-postfix.sqlite3")
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "https://first.example")

    first = store.prepare_outbound_postfix(
        "dingtalk",
        "conversation-1:message-1",
        "第一版回复",
        "请给出进度",
    )
    monkeypatch.setenv("CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL", "https://later.example")
    replay = store.prepare_outbound_postfix(
        "dingtalk",
        "conversation-1:message-1",
        "第二版回复",
        "新的原文",
    )

    assert first == replay
    assert "第一版回复" in replay.final_body
    assert "第二版回复" not in replay.final_body
    assert "https://first.example" in replay.final_body
    assert "https://later.example" not in replay.final_body
    assert replay.feedback_token


def test_prepare_outbound_postfix_rejects_invalid_replay_candidate(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "outbound-postfix.sqlite3")
    store.prepare_outbound_postfix("wechat", "message-1", "初始回复", "原文")

    with pytest.raises(ValueError, match="outbound body is required"):
        store.prepare_outbound_postfix("wechat", "message-1", " ", "原文")


def test_create_wechat_delivery_persists_prepared_final_body(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "wechat-create-postfix.sqlite3")
    store.enqueue_reply_task(
        channel="wechat", conversation_id="u1", conversation_title="Alex",
        single_chat=True, trigger_message_id="m1",
        trigger_create_time="2026-09-05T10:00:00", trigger_sender="Alex",
        trigger_text="原消息",
    )

    delivery_id = create_wechat_delivery(store,
        reply_task_id=1, account_id="acct-1", target_type="direct",
        target_id="u1", conversation_id="u1", reply_text="候选回复",
    )
    delivery = store.get_wechat_delivery_by_id(delivery_id)
    prepared = store.get_outbound_postfix("wechat", f"wechat:{delivery_id}")

    assert delivery is not None and prepared is not None
    assert delivery.status == "ready_to_send"
    assert delivery.reply_text == prepared.final_body
    assert delivery.reply_text != "候选回复"


def _claim_audit_run(
    store: AutoReplyStore,
    reply_task_id: int,
    execution_generation: str,
    **kwargs,
):
    return store.claim_agent_run(
        reply_task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id=f"direct-agent:{reply_task_id}:{execution_generation}",
        **kwargs,
    )


def _get_audit_run(
    store: AutoReplyStore,
    reply_task_id: int,
    execution_generation: str,
):
    return store.get_agent_run_for_turn(
        reply_task_id,
        execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    )


def test_restart_recovery_requeues_interrupted_run_without_classifying_effects(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-restart",
        conversation_title="产研管理群",
        single_chat=False,
        trigger_message_id="msg-restart",
        trigger_create_time="2026-08-28 08:00:00",
        trigger_sender="张晓民",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    task = store.get_reply_task_for_message("cid-restart", "msg-restart")
    assert task is not None
    task_id = task.id
    with store._connect() as db:
        _update_current_task8(
            db, "reply_tasks", {"status": "processing"},
            update_sql={"locked_at": "current_timestamp"},
            where_sql="and id=?",
            parameters=(task_id,),
        )
    claim = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="restart-test-owner",
    )
    runtime_attempt = store.claim_agent_runtime_attempt(
        claim.run.id,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.6-sol",
    )
    store.mark_agent_runtime_attempt_running_once(runtime_attempt.id)

    recovered = store.recover_interrupted_agent_runs_after_service_restart()

    assert [item.id for item in recovered] == [task_id]
    updated = store.get_reply_task(task_id)
    assert updated is not None
    assert updated.status == "pending"
    assert updated.execution_generation == task.execution_generation
    assert updated.error == "service_restart_interrupted"
    assert updated.force_new_decision is False
    run = store.get_agent_run(claim.run.id)
    assert run is not None and run.status == "failed"
    runtime = store.get_agent_runtime_attempt(runtime_attempt.id)
    assert runtime is not None and runtime.status == "failed"



def test_restart_requeues_a_processing_task_whose_last_run_already_failed(tmp_path):
    """Reply task 135612 sat `processing` for 33 minutes with no live run.

    Its last Audit run had already failed before the restart, so a recovery
    that looked for a `running` run found nothing and left the task holding a
    lock no process owned. Attention shows errors, not states, so it was
    invisible there too.
    """
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-orphan",
        conversation_title="审批待办",
        single_chat=False,
        trigger_message_id="msg-orphan",
        trigger_create_time="2026-09-17 22:41:18",
        trigger_sender="Derek OA",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    task = store.get_reply_task_for_message("cid-orphan", "msg-orphan")
    assert task is not None
    with store._connect() as db:
        _update_current_task8(
            db, "reply_tasks", {"status": "processing"},
            update_sql={"locked_at": "current_timestamp"},
            where_sql="and id=?",
            parameters=(task.id,),
        )
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="orphan-test-owner",
    )
    with store._connect() as db:
        db.execute(
            "update runs set status='failed' where id=?",
            (claim.run.id,),
        )

    recovered = store.recover_interrupted_agent_runs_after_service_restart()

    assert [item.id for item in recovered] == [task.id]
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "pending"
    assert updated.error == "service_restart_interrupted"


def test_recover_orphaned_agent_run_for_failed_reply_task(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-orphaned-agent-run",
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id="msg-orphaned-agent-run",
        trigger_create_time="2026-09-10 11:00:00",
        trigger_sender="sender@example.com",
        trigger_text="Handle this",
        trigger_message_json="{}",
    )
    task = store.get_reply_task_for_message(
        "cid-orphaned-agent-run",
        "msg-orphaned-agent-run",
    )
    assert task is not None
    with store._connect() as db:
        _update_current_task8(
            db, "reply_tasks", {"status": "processing"},
            update_sql={"locked_at": "current_timestamp"},
            where_sql="and id=?",
            parameters=(task.id,),
        )
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="orphaned-owner",
    )
    runtime_attempt = store.claim_agent_runtime_attempt(
        claim.run.id,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.6-sol",
    )
    store.mark_agent_runtime_attempt_running_once(runtime_attempt.id)
    with store._connect() as db:
        _update_current_task8(
            db, "reply_tasks", {"status": "failed", "locked_at": None, "error": "consumer_failed"},
            where_sql="and id=?",
            parameters=(task.id,),
        )

    recovered = store.recover_orphaned_agent_runs_for_terminal_reply_tasks()

    assert recovered == 1
    updated_task = store.get_reply_task(task.id)
    assert updated_task is not None
    assert updated_task.status == "failed"
    assert updated_task.error == "consumer_failed"
    run = store.get_agent_run(claim.run.id)
    assert run is not None
    assert run.status == "failed"
    assert "orphaned_agent_run_parent_not_processing" in run.structured_error_json
    runtime = store.get_agent_runtime_attempt(runtime_attempt.id)
    assert runtime is not None
    assert runtime.status == "failed"
    assert runtime.failure_code == "orphaned_agent_run_parent_not_processing"


def _enqueue_manual_rerun_in_process(
    db_path: str,
    attempt_id: int,
    barrier,
    results,
) -> None:
    store = AutoReplyStore(Path(db_path))
    barrier.wait(timeout=10)
    task = store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-process-rerun",
        conversation_title="Process rerun",
        single_chat=False,
        trigger_message_id="msg-process-rerun",
        trigger_create_time="2026-07-29 11:00:00",
        trigger_sender="ET",
        trigger_text="请重新处理",
        trigger_message_json="{}",
        attempt_id=attempt_id,
    )
    results.put((task.id, task.execution_generation))


def _enqueue_universal_reply_task(
    store: AutoReplyStore,
    *,
    execution_generation: str = "initial",
) -> int:
    inserted = store.enqueue_reply_task(
        conversation_id="cid-universal",
        conversation_title="Universal",
        single_chat=False,
        trigger_message_id="msg-universal",
        trigger_create_time="2026-07-20 10:00:00",
        trigger_sender="Derek",
        trigger_text="Handle this task",
        execution_generation=execution_generation,
    )
    assert inserted is True
    return store.claim_reply_tasks(limit=1)[0].id


def _claimed_runtime_agent_run(store: AutoReplyStore):
    task_id = _enqueue_universal_reply_task(store)
    return _claim_audit_run(store, task_id, "initial", owner="runtime-attempt").run


def _seed_native_task(db, task_id, kind, status, parameters=None):
    from app import task_entity_storage

    db.execute(
        "insert into tasks(id,kind,status,parameters_json,created_at,updated_at) "
        "values(?,?,?,?,current_timestamp,current_timestamp)",
        (task_id, kind, status, task_entity_storage._parameters_blob(db, parameters or {})),
    )


def _seed_native_run(db, run_id, task_id, source_type, status="running"):
    db.execute(
        "insert into runs(id,task_id,status,session_id,transcript_start,transcript_end,error,"
        "completed_at,updated_at) values(?,?,?,'',0,0,'','','')",
        (run_id, task_id, status),
    )
    db.execute("insert into run_provenance(run_id,source_type) values(?,?)", (run_id, source_type))


def _seed_runtime_operation_parent(
    store: AutoReplyStore, workload_kind: str, workload_key: str
) -> None:
    if workload_kind == "meeting":
        store.upsert_meeting_alignment_job(
            meeting_id="meeting-1", title="", source_json="{}", participants_json="[]",
            ended_at="", eligible_at="", status="processing",
        )
    elif workload_kind == "task" and ":" not in workload_key:
        summary_input_id = store.enqueue_work_summary_input("local_file", "task-parent", "{}")
        store.claim_work_summary_inputs(1)
    with store._immediate_write_transaction() as db:
        assert db.execute("pragma foreign_keys").fetchone()[0] == 1
        if workload_kind == "structured":
            _seed_native_task(db, int(workload_key), "okr_review", "processing", {
                "conversation_id": "cid", "conversation_title": "title",
                "trigger_message_id": "msg", "trigger_sender": "sender",
                "trigger_sender_user_id": "user", "trigger_text": "text",
                "period_label": "period", "period_start": "start", "period_end": "end",
                "okr_source_json": "{}", "codex_session_id": "",
            })
        elif workload_kind in {"meeting", "meeting_audit"}:
            job_id = db.execute("select id from tasks where kind='meeting_alignment'").fetchone()[0]
            _seed_native_run(db, int(workload_key), job_id, "meeting_alignment_runs")
        elif workload_kind == "task":
            source_id, separator, _ = workload_key.partition(":")
            if separator:
                insert_business(db, "work_project", {"id": int(source_id), "title": f"project-{source_id}", "category": "other", "status": "active"})
            else:
                _seed_native_run(db, int(source_id), summary_input_id, "task_agent_runs")
        elif workload_kind == "weekly_okr":
            week_end, manager_user_id, source_digest = workload_key.split(":", 2)
            _seed_native_task(db, 1, "okr_analysis", "running", {
                "week_end": week_end, "manager_user_id": manager_user_id,
                "source_digest": source_digest,
            })
        elif workload_kind == "memory":
            source, _, source_key = workload_key.partition(":")
            source_id, _, execution_generation = source_key.partition(":")
            table_name = {
                "memory_write_event": "memory_write_events",
                "wechat_memory_candidate": "wechat_memory_candidates",
                "wechat_memory_import_job": "wechat_memory_import_jobs",
            }[source]
            if table_name == "memory_write_events":
                insert_action(db, "reply_attempts", {"id": 1,
                    "conversation_id": "cid", "conversation_title": "title",
                    "trigger_message_id": "msg", "trigger_sender": "sender",
                    "trigger_text": "text", "action": "none",
                    "sensitivity_kind": "none", "final_reply_text": "",
                    "permission_action": "none", "permission_reason": "",
                    "send_status": "pending"})
                insert_action(db, "memory_write_events", {"id": int(source_id),
                    "attempt_id": 1, "event_type": "test"})
            elif table_name == "wechat_memory_candidates":
                insert_business_candidate(db, "wechat_memory_candidate", {
                    "id": int(source_id), "import_run_id": "import", "account_id": "account",
                    "statement": "statement", "category": "fact", "confidence": 1,
                    "sensitivity": "low", "status": "approved", "memory_write_status": "writing",
                })
            else:
                _seed_native_task(db, int(source_id), "wechat_import", "running", {
                    "import_run_id": "import", "account_id": "account",
                })
        elif workload_kind == "email_classification":
            _seed_native_task(db, int(workload_key), "email_classification", "running")
        elif workload_kind == "workbench":
            _seed_native_task(db, int(workload_key), "workbench_turn", "running")
        elif workload_kind == "email_unsubscribe_page":
            action_identity, _, _ = workload_key.removeprefix(
                "email-unsubscribe-page:"
            ).rpartition(":")
            task, _ = store._ensure_business_reply_task(db, store_module.ReplyTaskSpec(
                channel="email", conversation_id="email-thread:1",
                conversation_title="Email unsubscribe", single_chat=False,
                trigger_message_id=action_identity, trigger_create_time="",
                trigger_sender="", trigger_text="", trigger_message_json="{}",
            ))
            _update_current_task8(
                db, "reply_tasks", {"status": "processing"},
                structural_sql={"claimed_input_id": "current_input_id"},
                where_sql="and id=?",
                parameters=(task.id,),
            )
        elif workload_kind == "email_description_optimization":
            from app.configs_storage import insert_config

            snapshot_id, _, _ = workload_key.removeprefix(
                "description-optimization:"
            ).rpartition(":")
            insert_config(db, "email_training_snapshots", {
                "snapshot_id": snapshot_id, "snapshot_version": "v1",
                "description_version": "v1", "input_schema_version": "v1",
                "seed": 0, "observed_at": "now", "snapshot_digest": "a" * 64,
                "manifest_json": "{}", "frozen": 1, "created_at": "now",
                "folder_label_watermark": 0, "important_label_watermark": 0,
            })


def test_runtime_attempt_claim_is_ordered_and_idempotent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)

    first = store.claim_agent_runtime_attempt(
        run.id,
        route_name="codex_oauth",
        runtime_kind="codex_cli",
        credential_mode="local_oauth",
        model="gpt-5.5",
    )
    repeated = store.claim_agent_runtime_attempt(
        run.id,
        route_name="codex_oauth",
        runtime_kind="codex_cli",
        credential_mode="local_oauth",
        model="gpt-5.5",
    )

    assert first.attempt_number == 1
    assert repeated.id == first.id


@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
def test_runtime_attempt_claim_requires_running_agent_parent(
    tmp_path: Path, terminal_status: str
):
    store = AutoReplyStore(tmp_path / "runtime-parent.sqlite3")
    run = _claimed_runtime_agent_run(store)
    if terminal_status == "completed":
        store.complete_agent_run(run.id, {"outcome": "done"}, owner="runtime-attempt")
    else:
        store.fail_agent_run(
            run.id, {"code": "terminal"}, owner="runtime-attempt"
        )

    with pytest.raises(ValueError, match="does not exist or is not running"):
        store.claim_agent_runtime_attempt(
            run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
        )

    assert store.list_agent_runtime_attempts(run.id) == []


def test_runtime_attempt_claim_rejects_missing_agent_parent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-parent.sqlite3")

    with pytest.raises(ValueError, match="does not exist or is not running"):
        store.claim_agent_runtime_attempt(
            999, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
        )

    with store._connect() as db:
        assert db.execute(f'select count(*) from ({RUNTIME_ATTEMPT_SQL})').fetchone()[0] == 0


def _runtime_effect_started_event(operation_id: str) -> dict[str, object]:
    return {
        "type": "item.started",
        "item": {
            "type": "mcp_tool_call",
            "id": "write-1",
            "status": "in_progress",
            "metadata": {
                "effect": "effectful",
                "operation_id": operation_id,
                "capability": "agent_cli.dws",
                "operation": "chat message send",
                "operation_digest": "command-digest",
                "target_identifiers": {"group": "cid-universal"},
            },
        },
    }
















def test_agent_runtime_attempt_claim_atomically_rechecks_route_pause(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-parent.sqlite3")
    run = _claimed_runtime_agent_run(store)
    store.open_runtime_route_pause(
        "codex_oauth",
        "codex_login_required",
        retry_at="2099-01-01T00:00:00+00:00",
    )

    with pytest.raises(Exception, match="runtime route is paused") as raised:
        store.claim_agent_runtime_attempt(
            run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
        )

    assert type(raised.value).__name__ == "RuntimeRoutePausedError"
    assert store.list_agent_runtime_attempts(run.id) == []


def test_runtime_attempt_claim_numbers_follow_terminal_attempts(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    first = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    store.fail_agent_runtime_attempt(
        first.id,
        failure_class="authentication",
        failure_code="codex_login_required",
        failover_permitted=True,
    )
    second = store.claim_agent_runtime_attempt(
        run.id, "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )

    assert second.attempt_number == 2
    assert [attempt.id for attempt in store.list_agent_runtime_attempts(run.id)] == [
        first.id,
        second.id,
    ]
    assert store.mark_agent_runtime_attempt_superseded(first.id).status == "superseded"


def test_runtime_attempt_history_lookup_uses_run_index(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt-history.sqlite3")
    run = _claimed_runtime_agent_run(store)
    first = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    store.fail_agent_runtime_attempt(
        first.id,
        failure_class="authentication",
        failure_code="codex_login_required",
        failover_permitted=True,
    )
    second = store.claim_agent_runtime_attempt(
        run.id, "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )

    assert [attempt.id for attempt in store.list_agent_runtime_attempts(run.id)] == [
        first.id,
        second.id,
    ]
    with store._connect() as db:
        plan = db.execute(
            "explain query plan select * from runtime_attempts "
            "where parent_id=? order by attempt_number",
            (run.id,),
        ).fetchall()

    assert any("idx_runtime_attempts_run_order" in str(row[3]) for row in plan)


def test_runtime_attempts_for_runs_batch_preserves_run_and_attempt_order(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt-batch.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    first_run = _claim_audit_run(store, task.id, task.execution_generation,
                                 owner="runtime-batch-audit").run
    second_run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="runtime-batch-consumer",
    ).run
    first = store.claim_agent_runtime_attempt(
        first_run.id, "codex_oauth", "codex_cli", "local_oauth", "first"
    )
    second = store.claim_agent_runtime_attempt(
        first_run.id, "codex_api", "codex_cli", "service_api", "second"
    )
    other = store.claim_agent_runtime_attempt(
        second_run.id, "codex_oauth", "codex_cli", "local_oauth", "other"
    )

    attempts = store.list_agent_runtime_attempts_for_runs(
        [first_run.id, second_run.id, first_run.id, 0, -1]
    )

    assert [item.id for item in attempts[first_run.id]] == [first.id, second.id]
    assert [item.id for item in attempts[second_run.id]] == [other.id]
    assert store.list_agent_runtime_attempts_for_runs([]) == {}


def test_runtime_attempt_session_evidence_is_persisted_and_validated(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    fresh = store.claim_agent_runtime_attempt(
        run.id, "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )

    assert fresh.session_mode == "fresh"
    assert fresh.source_session_id == ""

    store.fail_agent_runtime_attempt(
        fresh.id, "session", "session_route_incompatible", True
    )
    resumed = store.claim_agent_runtime_attempt(
        run.id,
        "codex_api",
        "codex_cli",
        "service_api",
        "gpt-5.5",
        session_mode="resume",
        source_session_id="codex-session-1",
    )

    assert resumed.session_mode == "resume"
    assert resumed.source_session_id == "codex-session-1"
    assert store.get_agent_runtime_attempt(resumed.id) == resumed

    with pytest.raises(ValueError, match="fresh session evidence"):
        store.claim_agent_runtime_attempt(
            run.id,
            "other",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            session_mode="fresh",
            source_session_id="must-be-empty",
        )
    with pytest.raises(ValueError, match="resume session evidence"):
        store.claim_agent_runtime_attempt(
            run.id,
            "other",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            session_mode="resume",
        )
    with pytest.raises(TypeError, match="source_session_id must be a string"):
        store.claim_agent_runtime_attempt(
            run.id,
            "other",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
            source_session_id=1,
        )
    with store._connect() as db, pytest.raises(sqlite3.IntegrityError):
        _insert_runtime_attempt_row(
            db, {
                'parent_id': run.id,
                'workload_kind': 'agent_run',
                'workload_key': str(run.id),
                'attempt_number': 99,
                'route_name': 'direct',
                'runtime_kind': 'codex_cli',
                'credential_mode': 'local_oauth',
                'model': 'gpt-5.5',
                'session_mode': 'resume',
                'source_session_id': '   ',
                'status': 'starting',
                'started_at': '2026-08-20 10:00:00',
                'created_at': '2026-08-20 10:00:00',
                'updated_at': '2026-08-20 10:00:00',
            },
        )


def test_runtime_attempt_active_claim_compares_session_evidence(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    store.claim_agent_runtime_attempt(
        run.id, "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )

    with pytest.raises(ValueError, match="conflicting active runtime attempt claim"):
        store.claim_agent_runtime_attempt(
            run.id,
            "codex_api",
            "codex_cli",
            "service_api",
            "gpt-5.5",
            session_mode="resume",
            source_session_id="codex-session-1",
        )






def test_current_runtime_attempt_preserves_session_evidence_constraints(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "session-evidence.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    assert attempt.session_mode == "fresh"
    assert attempt.source_session_id == ""
    with store._connect() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "update runtime_attempts set session_mode='resume', source_session_id='   ' "
            "where id=?",
            (attempt.id,),
        )



def test_current_runtime_preserves_trim_session_evidence_constraint(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "session-triggers.sqlite3")
    with store._connect() as db:
        triggers = dict(db.execute("select name,sql from sqlite_master where type='trigger'"))
        assert "trg_runtime_attempt_session_evidence_insert" not in triggers
        assert "trg_runtime_attempt_session_evidence_update" not in triggers
        table = db.execute(
            "select sql from sqlite_master where name='runtime_attempts'"
        ).fetchone()[0]
        assert "trim(source_session_id)<>''" in table


@pytest.mark.parametrize(
    ("workload_kind", "workload_key"),
    [
        ("structured", "12"),
        ("meeting", "13"),
        ("task", "14"),
        ("task", "15:memory_backfill"),
        ("weekly_okr", "2026-08-16:manager-1:" + "a" * 64),
        ("memory", "memory_write_event:16"),
        ("memory", "wechat_memory_import_job:17"),
        ("email_classification", "18"),
        (
            "email_unsubscribe_page",
            "email-unsubscribe-page:email-action:" + "c" * 64 + ":" + "d" * 64,
        ),
        (
            "email_description_optimization",
            "description-optimization:snapshot-1:" + "b" * 64,
        ),
        ("workbench", "19"),
    ],
)
def test_runtime_attempt_operation_accepts_approved_stable_workload_keys(
    tmp_path: Path,
    workload_kind: str,
    workload_key: str,
):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    _seed_runtime_operation_parent(store, workload_kind, workload_key)

    attempt = store.claim_runtime_operation_attempt(
        workload_kind,
        workload_key,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.5",
    )

    assert attempt.workload_kind == workload_kind
    assert attempt.workload_key == workload_key
    assert attempt.parent_id == (
        int(workload_key.partition(":")[0])
        if workload_kind == "meeting" or (workload_kind == "task" and ":" not in workload_key)
        else None
    )


def test_runtime_operation_attempts_are_listed_only_for_exact_workload(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-operation-list.sqlite3")
    _seed_runtime_operation_parent(store, "structured", "12")
    _seed_runtime_operation_parent(store, "memory", "memory_write_event:13")
    first = store.claim_runtime_operation_attempt(
        "structured", "12", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    store.mark_agent_runtime_attempt_running_once(first.id)
    store.fail_agent_runtime_attempt(
        first.id, "authentication", "codex_login_required", True
    )
    second = store.claim_runtime_operation_attempt(
        "structured", "12", "codex_api", "codex_cli", "service_api", "gpt-5.5"
    )
    store.claim_runtime_operation_attempt(
        "memory", "memory_write_event:13", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    attempts = store.list_runtime_operation_attempts("structured", "12")

    assert [attempt.id for attempt in attempts] == [first.id, second.id]
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]


def test_meeting_audit_runtime_has_an_independent_valid_workload_identity(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "meeting-audit-runtime.sqlite3")
    _seed_runtime_operation_parent(store, "meeting", "13")

    attempt = store.claim_runtime_operation_attempt(
        "meeting_audit", "13", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    assert attempt.workload_kind == "meeting_audit"
    assert attempt.workload_key == "13"
    assert attempt.parent_id == 13


@pytest.mark.parametrize(
    ("workload_kind", "workload_key"),
    [
        ("agent_run", "1"),
        ("unknown", "1"),
        ("structured", "request-12"),
        ("meeting", "meeting-13"),
        ("task", "not-a-persisted-id"),
        ("structured", "999"),
        ("weekly_okr", "not-a-stable-key"),
        ("memory", "memory-16"),
        ("memory", "memory_write_event:999"),
        ("memory", "wechat_memory_import_job:999"),
        ("email_classification", "email-classification:not-a-digest"),
        ("email_unsubscribe_page", "email-unsubscribe-page:not-an-action"),
        ("email_unsubscribe_page", "email-unsubscribe-page:email-action:" + "c" * 64),
        (
            "email_description_optimization",
            "description-optimization:missing-digest",
        ),
    ],
)
def test_runtime_attempt_operation_rejects_unapproved_or_freeform_workload_keys(
    tmp_path: Path,
    workload_kind: str,
    workload_key: str,
):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")

    with pytest.raises(ValueError):
        store.claim_runtime_operation_attempt(
            workload_kind,
            workload_key,
            "codex_oauth",
            "codex_cli",
            "local_oauth",
            "gpt-5.5",
        )


def test_runtime_attempt_memory_keys_are_source_qualified_and_collision_free(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    _seed_runtime_operation_parent(store, "memory", "memory_write_event:1")
    _seed_runtime_operation_parent(store, "memory", "wechat_memory_candidate:1")
    _seed_runtime_operation_parent(store, "memory", "wechat_memory_import_job:1")

    event_attempt = store.claim_runtime_operation_attempt(
        "memory", "memory_write_event:1", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    candidate_attempt = store.claim_runtime_operation_attempt(
        "memory", "wechat_memory_candidate:1", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    import_attempt = store.claim_runtime_operation_attempt(
        "memory", "wechat_memory_import_job:1", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    assert len({
        event_attempt.workload_key,
        candidate_attempt.workload_key,
        import_attempt.workload_key,
    }) == 3
    assert {
        event_attempt.attempt_number,
        candidate_attempt.attempt_number,
        import_attempt.attempt_number,
    } == {1}


def test_runtime_attempt_requires_parent_to_be_in_runnable_state(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-parent-state.sqlite3")
    _seed_runtime_operation_parent(store, "meeting", "13")
    with store._connect() as db:
        db.execute("update runs set status='failed' where id=13")

    with pytest.raises(ValueError, match="parent does not exist or is not running"):
        store.claim_runtime_operation_attempt(
            "meeting", "13", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
        )


def test_runtime_attempt_correction_lineage_is_sealed_and_immutable(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-correction-lineage.sqlite3")
    _seed_runtime_operation_parent(store, "structured", "91")

    normal = store.claim_runtime_operation_attempt(
        "structured", "91", "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    assert (
        normal.attempt_purpose,
        normal.validation_retry_policy_id,
        normal.validation_result_schema_id,
    ) == ("normal", "", "")
    store.fail_agent_runtime_attempt(
        normal.id,
        "result",
        "runtime_result_validation_failed",
        False,
    )
    correction = store.claim_runtime_operation_attempt(
        "structured",
        "91",
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.5",
        attempt_purpose="result_validation_correction",
        validation_retry_policy_id="result_validation_retry.v1:test",
        validation_result_schema_id="test.integer.v1",
    )
    assert correction.attempt_purpose == "result_validation_correction"

    with store._connect() as db, pytest.raises(
        sqlite3.IntegrityError, match="lineage is immutable"
    ):
        db.execute(
            "update runtime_attempts set attempt_purpose='normal', validation_retry_policy_id='', validation_result_schema_id='' where id=?",
            (correction.id,),
        )
    with pytest.raises(ValueError, match="cannot carry correction lineage"):
        store.claim_runtime_operation_attempt(
            "structured",
            "91",
            "codex_api",
            "codex_cli",
            "service_api",
            "gpt-5.5",
            validation_retry_policy_id="forged",
        )


def test_task_memory_backfill_parent_is_work_project_not_summary_input(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task-memory-parent.sqlite3")
    summary_input_id = store.enqueue_work_summary_input("local_file", "ref", "{}")

    with pytest.raises(ValueError, match="parent does not exist"):
        store.claim_runtime_operation_attempt(
            "task", f"{summary_input_id}:memory_backfill", "codex_oauth", "codex_cli",
            "local_oauth", "gpt-5.5",
        )




def test_weekly_okr_report_run_lease_is_single_owner_and_recoverable(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "weekly-report-lease.sqlite3")
    started = datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)

    assert store.claim_weekly_okr_report_run(
        owner="owner-a", now=started, lease_seconds=120
    )
    assert not store.claim_weekly_okr_report_run(
        owner="owner-b", now=started + timedelta(seconds=1), lease_seconds=120
    )
    assert store.renew_weekly_okr_report_run(
        owner="owner-a", now=started + timedelta(seconds=60), lease_seconds=120
    )
    assert not store.release_weekly_okr_report_run(owner="owner-b")
    assert store.release_weekly_okr_report_run(owner="owner-a")
    assert store.claim_weekly_okr_report_run(
        owner="owner-b", now=started + timedelta(seconds=61), lease_seconds=120
    )


def test_weekly_okr_report_run_lease_can_be_reclaimed_after_expiry(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "weekly-report-expired-lease.sqlite3")
    started = datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)

    assert store.claim_weekly_okr_report_run(
        owner="crashed-owner", now=started, lease_seconds=60
    )

    assert store.claim_weekly_okr_report_run(
        owner="recovery-owner",
        now=started + timedelta(seconds=61),
        lease_seconds=60,
    )


















def _completed_projection_run(store):
    input_id = store.enqueue_work_summary_input("local_file", "receipt-source", "{}")
    store.claim_work_summary_inputs(1)
    run_id = store.claim_professional_run(input_id)
    store.finish_professional_run(run_id, status="completed", session_id="receipt-session")
    return input_id, run_id


def _projection_receipt_json():
    from app.task_models import TaskAttentionProjectionOutcome, TaskAttentionProjectionReceipt

    return TaskAttentionProjectionReceipt(
        status="partial", source_type="local_file", task_decision_count=2,
        project_link_count=1, registry_row_count=None, proposal_count=2,
        applied_count=1, outcomes=[
            TaskAttentionProjectionOutcome(task_id=1, anchor_id=3, attention_id=5, status="applied"),
            TaskAttentionProjectionOutcome(task_id=2, status="rejected", reason="Missing linked evidence"),
        ],
    ).model_dump_json()


def test_task_agent_projection_receipt_preserves_terminal_run(tmp_path: Path):
    from app.task_models import TaskAgentRun

    store = AutoReplyStore(tmp_path / "projection-receipt.sqlite3")
    _, run_id = _completed_projection_run(store)
    with store._connect() as db:
        before = dict(db.execute(f"select * from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run_id,)).fetchone())
    assert TaskAgentRun.model_validate(before).projection_json == "{}"
    receipt = _projection_receipt_json()
    store.record_task_agent_projection(run_id, receipt)
    with store._connect() as db:
        after = dict(db.execute(f"select * from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run_id,)).fetchone())
    assert TaskAgentRun.model_validate(after).projection_json == receipt
    assert after.pop("projection_json") == receipt
    assert before.pop("projection_json") == "{}"
    assert after == before


def test_task_agent_projection_receipt_rejects_invalid_and_absent_run(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "invalid-receipt.sqlite3")
    _, run_id = _completed_projection_run(store)
    with pytest.raises(ValueError):
        store.record_task_agent_projection(run_id, '{"status":"completed"}')
    with pytest.raises(ValueError, match="Task Agent Run does not exist"):
        store.record_task_agent_projection(999999, _projection_receipt_json())


def test_task_agent_projection_receipt_uses_existing_transaction(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "rollback-receipt.sqlite3")
    _, run_id = _completed_projection_run(store)
    with pytest.raises(RuntimeError, match="rollback receipt"):
        with store.business_task_transaction() as db:
            store.record_task_agent_projection(run_id, _projection_receipt_json(), _db=db)
            assert db.execute(f"select projection_json from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run_id,)).fetchone()[0] != "{}"
            raise RuntimeError("rollback receipt")
    with store._connect() as db:
        assert db.execute(f"select projection_json from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run_id,)).fetchone()[0] == "{}"


def _create_receipt_attention(store, assessment_json=None):
    signal_id = store.create_business_task_signal(
        source_type="message", source_ref="receipt-message", evidence_text="Delivery is blocked",
        dedupe_key="receipt-message",
    )
    with store.business_task_transaction() as db:
        anchor_id = store.create_business_anchor_in_transaction(
            anchor_type="customer", anchor_ref="receipt-customer", title="Receipt customer", _db=db,
        )
        extra = {} if assessment_json is None else {"assessment_json": assessment_json}
        return store.create_business_attention_item_in_transaction(
            stable_key="receipt-attention", category="watch", title="Delivery blocked",
            business_area="Delivery", why_attention="Customer delivery risk", current_state="Blocked",
            ceo_action="Review delivery options", anchor_id=anchor_id, evidence_signal_id=signal_id,
            now="2026-10-01T12:00:00+00:00", _db=db, **extra,
        )


def test_business_attention_assessment_json_create_and_update(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "assessment.sqlite3")
    assessment = '{"material_trigger":"delivery_risk","inference":"Customer impact","evidence":[{"signal_id":1,"source_ref":"receipt-message","source_excerpt":"Delivery is blocked","source_time":"","link":""}]}'
    item_id = _create_receipt_attention(store, assessment)
    item = store.get_business_attention_item(item_id)
    assert item.assessment_json == assessment
    updated = item.model_copy(update={"assessment_json": '{"inference":"Updated assessment"}'})
    with store.business_task_transaction() as db:
        store.update_business_attention_item_in_transaction(item=updated, _db=db)
    assert store.get_business_attention_item(item_id).assessment_json == updated.assessment_json


def test_task_attention_receipt_reopen_does_not_claim_historical_success(tmp_path: Path):
    path = tmp_path / "receipts.sqlite3"
    store = AutoReplyStore(path)
    _, run_id = _completed_projection_run(store)
    item_id = _create_receipt_attention(store)
    with store._connect() as db:
        before_run = dict(db.execute(f"select * from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run_id,)).fetchone())
        before_item = dict(db.execute(f"select * from ({candidate_projection('attention_item')}) where id=?", (item_id,)).fetchone())
    store_module._INITIALIZED_STORE_PATHS.discard(path.resolve())
    reopened = AutoReplyStore(path)
    with reopened._connect() as db:
        run = dict(db.execute(f"select * from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run_id,)).fetchone())
        item = dict(db.execute(f"select * from ({candidate_projection('attention_item')}) where id=?", (item_id,)).fetchone())
    assert run == before_run
    assert item == before_item
    assert run["projection_json"] == "{}"
    assert item["assessment_json"] == "{}"


def test_task_agent_run_begin_is_concurrent_and_finish_is_idempotent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "task-run-lifecycle.sqlite3")
    summary_id = store.enqueue_work_summary_input("local_file", "source", "{}")
    claimed = store.claim_work_summary_inputs(1)[0]
    assert claimed.id == summary_id
    barrier = Barrier(3)
    run_ids: list[int] = []

    def begin() -> None:
        barrier.wait(timeout=5)
        run_ids.append(store.claim_professional_run(summary_id))

    threads = [Thread(target=begin) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert len(run_ids) == 2
    assert len(set(run_ids)) == 1
    run_id = run_ids[0]
    store.finish_professional_run(run_id, status="completed", session_id="session-task")
    store.finish_professional_run(run_id, status="completed", session_id="session-task")
    with store._connect() as db:
        row = db.execute(f"select * from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run_id,)).fetchone()
    assert row["status"] == "completed"
    assert row["finished_at"]


def test_expired_runtime_attempt_is_closed_after_its_task_run_is_terminal(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "terminal-task-runtime.sqlite3")
    summary_id = store.enqueue_work_summary_input("local_file", "source", "{}")
    store.claim_work_summary_inputs(1)
    run_id = store.claim_professional_run(summary_id)
    attempt = store.claim_runtime_operation_attempt(
        "task",
        str(run_id),
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.6-sol",
        owner="crashed-task-runtime",
        lease_seconds=5,
        now="2026-08-22 16:01:19",
    )
    store.mark_agent_runtime_attempt_running_once(
        attempt.id,
        owner="crashed-task-runtime",
        lease_seconds=5,
        now="2026-08-22 16:01:19",
    )
    store.finish_professional_run(run_id, status="failed", error="runtime_attempt_active")

    assert store.recover_expired_terminal_task_runtime_attempts(
        now="2026-08-22 16:01:25"
    ) == 1

    recovered = store.get_agent_runtime_attempt(attempt.id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.failure_code == "runtime_lease_expired"
    assert recovered.first_effect_started_at == ""
    assert recovered.lease_owner == ""
    assert recovered.lease_expires_at == ""


def test_expired_terminal_task_runtime_with_effect_evidence_is_not_closed(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "effectful-terminal-task-runtime.sqlite3")
    summary_id = store.enqueue_work_summary_input("local_file", "source", "{}")
    store.claim_work_summary_inputs(1)
    run_id = store.claim_professional_run(summary_id)
    attempt = store.claim_runtime_operation_attempt(
        "task",
        str(run_id),
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.6-sol",
        owner="effectful-task-runtime",
        lease_seconds=5,
        now="2026-08-22 16:01:19",
    )
    store.mark_agent_runtime_attempt_running_once(
        attempt.id,
        owner="effectful-task-runtime",
        lease_seconds=5,
        effectful=True,
        now="2026-08-22 16:01:19",
    )
    store.finish_professional_run(run_id, status="failed", error="interrupted")

    assert store.recover_expired_terminal_task_runtime_attempts(
        now="2026-08-22 16:01:25"
    ) == 0

    untouched = store.get_agent_runtime_attempt(attempt.id)
    assert untouched is not None
    assert untouched.status == "running"
    assert untouched.first_effect_started_at == "2026-08-22 16:01:19"


def test_recover_orphaned_task_agent_run_when_parent_input_is_pending(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "orphaned-task-agent-run.sqlite3")
    input_id = store.enqueue_work_summary_input("local_file", "source", "{}")
    assert len(store.claim_work_summary_inputs(1)) == 1
    run_id = store.claim_professional_run(input_id)
    attempt = store.claim_runtime_operation_attempt(
        "task",
        str(run_id),
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.6-sol",
        owner="orphaned-task-agent-run",
        lease_seconds=1800,
    )
    assert len(store.reset_processing_work_summary_inputs()) == 1

    # The reset closes the run and its runtime attempt in its own transaction,
    # so the reaper has nothing left to find. It used to leave both live, and
    # because the reaper only sees an orphan once the parent input is already
    # out of 'processing', service start ran it BEFORE the reset that creates
    # them -- so every restart's wreckage waited for the next restart.
    assert store.recover_orphaned_task_agent_runs() == 0

    with store._connect() as db:
        run = db.execute(
            f"select status, error from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run_id,)
        ).fetchone()
    recovered = store.get_agent_runtime_attempt(attempt.id)
    assert run is not None
    assert tuple(run) == (
        "failed",
        "orphaned_task_agent_run_parent_not_processing",
    )
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.failure_code == "runtime_parent_terminal_no_effect"


def test_meeting_run_begin_is_idempotent_and_finish_closes_running_parent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "meeting-run-lifecycle.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-lifecycle", title="Meeting", source_json="{}",
        participants_json="[]", ended_at="2026-08-20T10:00:00+08:00",
        eligible_at="2026-08-20T10:10:00+08:00", status="pending",
    )
    [job] = store.claim_meeting_alignment_jobs(1, now="2026-08-20T10:11:00+08:00")
    assert job.id == job_id

    run_id = store.claim_professional_run(job_id)
    assert store.claim_professional_run(job_id) == run_id
    store.finish_professional_run(run_id, status="no_action")
    assert store.get_professional_run(run_id)["status"] == "no_action"


@pytest.mark.parametrize(
    "status",
    ["", "running", " running ", "READY_TO_SEND", "ready-to-send", "typo"],
)
def test_meeting_run_finish_rejects_noncanonical_terminal_status(
    tmp_path: Path, status: str
):
    store = AutoReplyStore(tmp_path / f"meeting-invalid-{len(status)}.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id=f"meeting-invalid-{len(status)}", title="Meeting",
        source_json="{}", participants_json="[]",
        ended_at="2026-08-20T10:00:00+08:00",
        eligible_at="2026-08-20T10:10:00+08:00", status="pending",
    )
    store.claim_meeting_alignment_jobs(1, now="2026-08-20T10:11:00+08:00")
    run_id = store.claim_professional_run(job_id)

    with pytest.raises(ValueError, match="terminal status"):
        store.finish_professional_run(run_id, status=status)


@pytest.mark.parametrize(
    "status", ["failed", "retry", "no_action", "ready_to_send"]
)
def test_meeting_run_finish_accepts_production_terminal_statuses(
    tmp_path: Path, status: str
):
    store = AutoReplyStore(tmp_path / f"meeting-valid-{status}.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id=f"meeting-valid-{status}", title="Meeting",
        source_json="{}", participants_json="[]",
        ended_at="2026-08-20T10:00:00+08:00",
        eligible_at="2026-08-20T10:10:00+08:00", status="pending",
    )
    store.claim_meeting_alignment_jobs(1, now="2026-08-20T10:11:00+08:00")
    run_id = store.claim_professional_run(job_id)

    store.finish_professional_run(run_id, status=status)

    assert store.get_professional_run(run_id)["status"] == status


def test_wechat_import_jobs_are_distinct_per_invocation(tmp_path: Path):
    from tests.test_wechat_import_native import AssembledTaskStore

    store = AssembledTaskStore(tmp_path / "wechat-import-job.sqlite3")
    first = store.begin_wechat_memory_import_job(
        import_run_id="wechat-scope", account_id="account-1"
    )
    second = store.begin_wechat_memory_import_job(
        import_run_id="wechat-scope", account_id="account-1"
    )
    assert first != second
    attempt = store.claim_runtime_operation_attempt(
        "memory", f"wechat_memory_import_job:{first}", "codex_oauth",
        "codex_cli", "local_oauth", "gpt-5.5",
    )
    assert attempt.workload_key == f"wechat_memory_import_job:{first}"
    store.finish_wechat_memory_import_job(first, status="completed")


def test_current_schema_rejects_legacy_task_run_table_without_mutation(tmp_path: Path):
    path = tmp_path / "legacy-task-run-shape.sqlite3"
    store = AutoReplyStore(path)
    _completed_projection_run(store)
    with store._connect() as db:
        db.execute(
            "create table task_agent_runs (id integer primary key autoincrement, "
            "summary_input_id integer not null, codex_session_id text not null default '', "
            "decision_json text not null default '{}', audit_summary text not null default '', "
            "memory_recall_used integer not null default 0, "
            "created_at text not null default current_timestamp)"
        )
        db.execute("insert into task_agent_runs(summary_input_id,codex_session_id) values(1,'legacy-session')")
    assert store._schema_is_current() is False
    assert_store_reopen_rejected_without_mutation(path)


def test_current_schema_rejects_missing_runtime_parent_indexes(tmp_path):
    path = tmp_path / "damaged.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute("drop index idx_runtime_attempts_run_order")
        db.execute("drop index idx_runs_parent")
        before = tuple(db.iterdump())
    with pytest.raises(sqlite3.IntegrityError, match="malformed"):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before


def test_current_schema_has_no_legacy_core_trajectory_columns(tmp_path: Path):
    store = AutoReplyStore(tmp_path / 'core-no-trajectory.sqlite3')
    assert store._schema_is_current() is True
    with store._connect() as db:
        assert_no_core_native_storage(db)



def test_current_schema_sentinel_rejects_malformed_duplicate_meeting_source(
    tmp_path: Path,
):
    from tests.test_task_storage import fingerprint, source_database

    db_path = tmp_path / "legacy-meeting-run-shape.sqlite3"
    db = source_database(db_path)
    try:
        db.execute(
            "insert into meeting_alignment_jobs (id, meeting_id) "
            "values (1, 'legacy-meeting')"
        )
        db.execute("drop index idx_meeting_alignment_runs_active_job")
        db.execute(
            "insert into meeting_alignment_runs "
            "(id, job_id, status, error, created_at) values "
            "(7, 1, 'running', '', '2026-08-20 09:00:00'), "
            "(8, 1, 'running', '', '2026-08-20 09:00:00')"
        )
        db.commit()
        before = fingerprint(db)
    finally:
        db.close()
    with pytest.raises((ValueError, sqlite3.IntegrityError), match="unsupported|malformed"):
        AutoReplyStore(db_path)
    with sqlite3.connect(db_path) as db:
        assert fingerprint(db) == before


def test_runtime_attempt_transitions_are_terminal_safe(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    running = store.mark_agent_runtime_attempt_running(attempt.id)
    completed = store.complete_agent_runtime_attempt(
        running.id,
        session_id="session-1",
        transcript_reference="run.jsonl",
        transcript_start=4,
        transcript_end=8,
    )

    assert running.status == "running"
    assert completed.status == "completed"
    with pytest.raises(ValueError, match="completed"):
        store.fail_agent_runtime_attempt(
            completed.id, "process", "codex_process_failed", False
        )
    with pytest.raises(ValueError, match="completed"):
        store.mark_agent_runtime_attempt_superseded(completed.id)


def test_runtime_attempt_completion_allows_missing_session_and_transcript(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    completed = store.complete_agent_runtime_attempt(attempt.id, "", "", 0, 0)

    assert completed.status == "completed"
    assert completed.session_id == ""
    assert completed.transcript_reference == ""


def test_runtime_attempt_and_conversation_session_commit_atomically(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "runtime-attempt-session-atomic.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "claude_api", "claude_cli", "service_api", "claude-sonnet-4-5"
    )
    attempt = store.mark_agent_runtime_attempt_running_once(attempt.id)

    def fail_slot(*args, **kwargs):
        raise RuntimeError("injected_slot_failure")

    monkeypatch.setattr(
        store, "_upsert_conversation_runtime_session_in_connection", fail_slot
    )
    with pytest.raises(RuntimeError, match="injected_slot_failure"):
        store.complete_agent_runtime_attempt(
            attempt.id,
            "claude-session",
            "",
            0,
            3,
            result_schema_id="schema-v1",
            result_envelope_json=json.dumps(
                {"schema_id": "schema-v1", "raw_result": "{}"}
            ),
            conversation_id="cid-runtime-atomic",
            route_name="claude_api",
            conversation_contract_hash="contract-v1",
        )

    persisted = store.get_agent_runtime_attempt(attempt.id)
    assert persisted is not None and persisted.status == "running"
    assert persisted.session_id == ""
    assert persisted.result_schema_id == ""
    assert store.get_conversation_runtime_session(
        "cid-runtime-atomic", "claude_api"
    ) is None


def test_runtime_attempt_rejects_conflicting_terminal_rewrites(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )
    failed = store.fail_agent_runtime_attempt(
        attempt.id, "authentication", "codex_login_required", True
    )

    assert (
        store.fail_agent_runtime_attempt(
            attempt.id, "authentication", "codex_login_required", True
        )
        == failed
    )
    with pytest.raises(ValueError, match="conflicting terminal rewrite"):
        store.fail_agent_runtime_attempt(
            attempt.id, "process", "codex_process_failed", False
        )


def test_runtime_attempt_effect_started_timestamp_is_idempotent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(
        run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5"
    )

    first = store.note_runtime_attempt_effect_started(
        attempt.id, at="2026-08-20 10:00:00"
    )
    repeated = store.note_runtime_attempt_effect_started(
        attempt.id, at="2026-08-20 10:01:00"
    )

    assert first.first_effect_started_at == "2026-08-20 10:00:00"
    assert repeated.first_effect_started_at == first.first_effect_started_at


def test_route_sessions_do_not_overwrite_other_routes(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "route-sessions.sqlite3")

    store.upsert_conversation_runtime_session(
        "cid", "codex_oauth", "oauth-session", "oauth-contract"
    )
    store.upsert_conversation_runtime_session(
        "cid", "codex_api", "api-session", "api-contract"
    )

    assert (
        store.get_conversation_runtime_session("cid", "codex_oauth")
        == "oauth-session"
    )
    assert store.get_conversation_runtime_session("cid", "codex_api") == "api-session"
    assert store.get_conversation_runtime_session(
        "cid", "codex_oauth", required_contract_hash="api-contract"
    ) is None
    assert store.get_conversation_runtime_session(
        "cid", "codex_api", required_contract_hash="api-contract"
    ) == "api-session"


def test_partial_legacy_conversation_source_is_rejected_without_mutation(tmp_path: Path):
    db_path = tmp_path / "legacy-route-sessions.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            create table conversations (
                conversation_id text primary key,
                title text not null,
                single_chat integer not null,
                codex_session_id text
            )
            """
        )
        db.execute(
            """
            insert into conversations (
                conversation_id, title, single_chat, codex_session_id
            ) values ('legacy-cid', 'Legacy', 0, 'legacy-session')
            """
        )

    assert_store_reopen_rejected_without_mutation(db_path)


def test_partial_legacy_route_source_is_rejected_without_mutation(tmp_path: Path):
    db_path = tmp_path / "old-route-session-schema.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            create table conversation_runtime_sessions (
                conversation_id text not null,
                route_name text not null,
                session_id text not null,
                updated_at text not null default current_timestamp,
                primary key(conversation_id, route_name)
            )
            """
        )
        db.execute(
            """
            insert into conversation_runtime_sessions (
                conversation_id, route_name, session_id
            ) values ('cid', 'codex_api', 'old-api-session')
            """
        )

    assert_store_reopen_rejected_without_mutation(db_path)


def test_current_route_session_unknown_contract_survives_reopen(tmp_path: Path):
    db_path = tmp_path / "current-route-unknown-contract.sqlite3"
    store = AutoReplyStore(db_path)
    store.upsert_conversation_runtime_session('cid', 'codex_api', 'old-api-session', '')
    with store._connect() as db:
        before = [tuple(row) for row in db.execute(
            f"select * from ({state_projection('conversation_runtime_sessions')})"
        )]
    assert store._schema_is_current() is True
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    reopened = AutoReplyStore(db_path)
    assert reopened.get_conversation_runtime_session('cid', 'codex_api') == 'old-api-session'
    assert reopened.get_conversation_runtime_session_contract_hash('cid', 'codex_api') == ''
    assert reopened.get_conversation_runtime_session(
        'cid', 'codex_api', required_contract_hash='current-contract',
    ) is None
    with reopened._connect() as db:
        assert [tuple(row) for row in db.execute(
            f"select * from ({state_projection('conversation_runtime_sessions')})"
        )] == before


def test_route_pause_is_independent_and_expires(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "route-pauses.sqlite3")

    assert store.open_runtime_route_pause(
        "codex_oauth", "codex_login_required", retry_at="2026-08-20 10:30:00"
    )
    assert store.active_runtime_route_pause("codex_api", now="2026-08-20 10:00:00") is None
    assert (
        store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:31:00")
        is None
    )


def test_route_pause_open_close_is_independent_and_idempotent(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "route-pauses.sqlite3")

    assert store.open_runtime_route_pause(
        "codex_oauth", "codex_login_required", retry_at="2099-01-01 00:00:00"
    )
    assert not store.open_runtime_route_pause(
        "codex_oauth", "codex_login_required", retry_at="2099-01-01 00:00:00"
    )
    assert store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:00:00") == (
        "codex_login_required"
    )
    assert store.close_runtime_route_pause("codex_oauth")
    assert not store.close_runtime_route_pause("codex_oauth")
    assert store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:00:00") is None


def test_route_pause_reopens_after_expiry_without_prior_read(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "route-pauses.sqlite3")
    assert store.open_runtime_route_pause(
        "codex_oauth", "old_failure", retry_at="2000-01-01 00:00:00"
    )

    assert store.open_runtime_route_pause(
        "codex_oauth", "new_failure", retry_at="2099-01-01 00:00:00"
    )
    assert store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:00:00") == (
        "new_failure"
    )


def test_route_pause_open_and_expiry_cleanup_are_serialized(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "route-pauses.sqlite3")
    assert store.open_runtime_route_pause(
        "codex_oauth", "old_failure", retry_at="2000-01-01 00:00:00"
    )
    barrier = Barrier(2)
    results = Queue()

    def open_new_pause():
        barrier.wait(timeout=5)
        results.put(store.open_runtime_route_pause(
            "codex_oauth", "new_failure", retry_at="2099-01-01 00:00:00"
        ))

    thread = Thread(target=open_new_pause)
    thread.start()
    barrier.wait(timeout=5)
    store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:00:00")
    thread.join(timeout=5)

    assert results.get(timeout=1) is True
    assert store.active_runtime_route_pause("codex_oauth", now="2026-08-20 10:00:00") == "new_failure"


@pytest.mark.parametrize("field, replacement", [
    ("runtime_kind", "claude_cli"),
    ("credential_mode", "service_api"),
    ("model", "gpt-5.6"),
])
def test_runtime_attempt_active_claim_rejects_conflicting_immutable_data(
    tmp_path: Path, field: str, replacement: str
):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    values = {"runtime_kind": "codex_cli", "credential_mode": "local_oauth", "model": "gpt-5.5"}
    store.claim_agent_runtime_attempt(run.id, "codex_oauth", **values)
    values[field] = replacement

    with pytest.raises(ValueError, match="conflicting active runtime attempt"):
        store.claim_agent_runtime_attempt(run.id, "codex_oauth", **values)


def test_runtime_attempt_readback_reflects_effect_start(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "runtime-attempt.sqlite3")
    run = _claimed_runtime_agent_run(store)
    attempt = store.claim_agent_runtime_attempt(run.id, "codex_oauth", "codex_cli", "local_oauth", "gpt-5.5")
    assert store.get_agent_runtime_attempt(attempt.id) == attempt
    assert store.get_agent_runtime_attempt(999999) is None
    store.note_runtime_attempt_effect_started(attempt.id, at="2026-08-20 10:00:00")
    assert store.get_agent_runtime_attempt(attempt.id).first_effect_started_at == "2026-08-20 10:00:00"


def test_agent_run_write_transaction_propagates_post_yield_lock_error(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "transaction.sqlite3")

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        with store._agent_run_write_transaction(None):
            raise sqlite3.OperationalError("database is locked")


def test_agent_run_write_transaction_retries_begin_then_runs_body_once(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "transaction.sqlite3")
    original_connect = store._connect
    attempts = 0
    body_runs = 0

    @contextmanager
    def flaky_connect():
        nonlocal attempts
        attempts += 1
        with original_connect() as db:
            if attempts == 1:
                class BeginLocked:
                    def execute(self, _sql):
                        raise sqlite3.OperationalError("database is locked")
                yield BeginLocked()
            else:
                yield db

    monkeypatch.setattr(store, "_connect", flaky_connect)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    with store._agent_run_write_transaction(None):
        body_runs += 1

    assert attempts == 2
    assert body_runs == 1


def test_agent_run_write_transaction_closes_every_failed_begin_connection(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "transaction.sqlite3")
    closed = 0

    @contextmanager
    def locked_connect():
        nonlocal closed
        try:
            class BeginLocked:
                def execute(self, _sql):
                    raise sqlite3.OperationalError("database is locked")
            yield BeginLocked()
        finally:
            closed += 1

    monkeypatch.setattr(store, "_connect", locked_connect)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        with store._agent_run_write_transaction(None):
            pytest.fail("transaction body must not run")

    assert closed == store_module.AGENT_RUN_WRITE_LOCK_RETRY_ATTEMPTS


def test_list_agent_run_summaries_for_terminal_runs_batches_without_events(
    tmp_path: Path,
):
    statements: list[str] = []

    class TracedStore(AutoReplyStore):
        def _open_connection(self):
            connection = super()._open_connection()
            connection.set_trace_callback(statements.append)
            return connection

    store = TracedStore(tmp_path / "worker.sqlite3")

    def seed_generation(label: str, generation: str):
        store.enqueue_reply_task(
            conversation_id=f"cid-summary-{label}",
            conversation_title=f"Summary {label}",
            single_chat=False,
            trigger_message_id=f"msg-summary-{label}",
            trigger_create_time="2026-08-18 10:00:00",
            trigger_sender="Derek",
            trigger_text="Summarize the agent runs.",
            execution_generation=generation,
        )
        [task] = store.claim_reply_tasks(limit=1)
        consumer = store.claim_agent_run(
            task.id,
            task.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
            turn_attempt=0,
            parent_agent_run_id=None,
            operation_id="",
            owner=f"consumer-{label}",
        ).run
        audit = _claim_audit_run(
            store,
            task.id,
            task.execution_generation,
            owner=f"audit-{label}",
        ).run
        store.append_agent_run_event(
            audit.id,
            {"type": "tool", "name": f"summary-{label}"},
            owner=f"audit-{label}",
        )
        return consumer, audit

    first_consumer, first_terminal = seed_generation("first", "generation-first")
    second_consumer, second_terminal = seed_generation("second", "generation-second")
    method = getattr(store, "list_agent_run_summaries_for_terminal_runs", None)
    assert method is not None

    statements.clear()
    summaries = method(
        [second_terminal.id, first_terminal.id, first_terminal.id, 0, -1, 999999]
    )

    assert set(summaries) == {first_terminal.id, second_terminal.id}
    assert [run.id for run in summaries[first_terminal.id]] == [
        first_consumer.id,
        first_terminal.id,
    ]
    assert [run.id for run in summaries[second_terminal.id]] == [
        second_consumer.id,
        second_terminal.id,
    ]
    assert all(
        run.tool_events == []
        for summary_runs in summaries.values()
        for run in summary_runs
    )
    normalized_statements = [statement.casefold() for statement in statements]
    assert len(
        [statement for statement in normalized_statements if 'with terminal_runs as' in statement]
    ) == 1
    assert not any("agent_run_events" in statement for statement in normalized_statements)
    assert method([]) == {}


def test_finalize_orchestration_preserves_independently_recorded_delivery(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-confirmed-direct",
        conversation_title="Direct chat",
        single_chat=True,
        trigger_message_id="msg-confirmed-direct",
        trigger_create_time="2026-08-12 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please reply",
    )
    [task] = store.claim_reply_tasks(limit=1)
    audit = _claim_audit_run(
        store,
        task.id,
        task.execution_generation,
        owner="audit",
    ).run
    audit = store.complete_agent_run(
        audit.id,
        {"outcome": "executed", "summary": "Readback verified."},
        owner="audit",
    )

    sent_reply_text = (
        "Verified direct reply（by明哥分身）\n\n"
        "反馈：[👍 有帮助](https://feedback.example.com/api/dingtalk-feedback-spike"
        "?feedback_token=spike_1_abcd1234&rating=up)｜"
        "[👎 需改进](https://feedback.example.com/api/dingtalk-feedback-spike"
        "?feedback_token=spike_1_abcd1234&rating=down)"
    )
    store.record_completed_agent_message_delivery(
        agent_run_id=audit.id, external_action_key='confirmed-delivery',
        business_object_key=task.business_object_key, action_identity='reply',
        operation='message.send', target_identifiers={'conversation_id': task.conversation_id},
        conversation_id=task.conversation_id, trigger_message_id=task.trigger_message_id,
        reply_text=sent_reply_text, provider_result={'source': 'test'},
    )
    from app.action_entity_storage import append_action_receipt, create_action
    oa_result = '{"source":"oa-provider-substitute"}'
    with store._connect() as db:
        db.execute("begin immediate")
        oa_action = create_action(db, values={
            "operation": "approve", "status": "done",
            "business_object_key": task.business_object_key,
            "action_identity": "oa-approve",
            "external_action_key": "oa-provider-substitute",
        })
        oa_receipt_id = append_action_receipt(db, oa_action["id"], values={
            "operation": "approve", "target": '{"process_instance_id":"test-proc","task_id":"test-task"}',
            "provider_result_json": oa_result, "result_digest": "oa-test-digest",
            "agent_run_id": audit.id, "observed_at": "2026-10-25T00:00:00+00:00",
        })
    attempt_id = store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=audit.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="Readback verified.",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="Readback verified.",
        send_status="completed",
        send_error="",
        channel="dingtalk",
        oa_process_instance_id="test-proc",
        oa_task_id="test-task",
        oa_action="approve",
        oa_action_result_json=oa_result,
        oa_action_receipt_id=oa_receipt_id,
    )

    sent = store.get_sent_reply(task.conversation_id, task.trigger_message_id)
    attempt = store.get_reply_attempt(attempt_id)

    assert sent is not None
    assert sent.reply_text == sent_reply_text
    assert sent.feedback_token == "spike_1_abcd1234"
    assert attempt is not None
    assert attempt.final_reply_text == ""
    assert attempt.oa_action_result_json == oa_result
    assert store.count_sent_replies() == 1
    with store._connect() as db:
        raw = db.execute(
            "select a.attempt_oa_action_result_json,ref.receipt_id "
            "from actions a join reply_attempt_receipt_refs ref on ref.attempt_id=a.id "
            "where a.record_type='reply_attempts' and cast(a.legacy_key as integer)=?",
            (attempt_id,),
        ).fetchone()
        assert raw[0] == "" and raw[1] == oa_receipt_id


def test_finalize_orchestration_never_projects_failed_current_run_as_done(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-failed-final-run",
        conversation_title="Direct chat",
        single_chat=True,
        trigger_message_id="msg-failed-final-run",
        trigger_create_time="2026-09-14 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please handle this",
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = _claim_audit_run(
        store, task.id, task.execution_generation, owner="audit"
    ).run
    store.fail_agent_run(
        run.id,
        {"code": "runtime_result_validation_failed"},
        owner="audit",
    )

    store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=run.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="incorrect caller projection",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="incorrect caller projection",
        send_status="completed",
        send_error="",
        channel=task.channel,
    )

    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "runtime_result_validation_failed"


def test_finalize_orchestration_keeps_confirmation_boundary_as_failed(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-confirmation-boundary",
        conversation_title="Direct chat",
        single_chat=True,
        trigger_message_id="msg-confirmation-boundary",
        trigger_create_time="2026-09-15 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please handle this",
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = _claim_audit_run(
        store, task.id, task.execution_generation, owner="audit"
    ).run
    store.fail_agent_run(
        run.id,
        {
            "code": "confirmation_required",
            "retryable": True,
            "authorization_required": True,
        },
        owner="audit",
    )
    options = json.dumps(
        [
            {
                "key": "confirm_external_action",
                "instruction": "Confirm the reviewed external action.",
            },
            {
                "key": "stop_without_action",
                "instruction": "Stop without an external action.",
            },
        ]
    )

    attempt_id = store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=run.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="Provider confirmation is required.",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="Provider confirmation is required.",
        human_decision_options_json=options,
        send_status="needs_human",
        send_error="confirmation_required",
        channel=task.channel,
    )

    updated = store.get_reply_task(task.id)
    attempt = store.get_reply_attempt(attempt_id)

    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "confirmation_required"
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "confirmation_required"


def test_reconcile_done_reply_tasks_with_failed_current_run(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-reconcile-failed-run",
        conversation_title="Direct chat",
        single_chat=True,
        trigger_message_id="msg-reconcile-failed-run",
        trigger_create_time="2026-09-14 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please handle this",
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = _claim_audit_run(
        store, task.id, task.execution_generation, owner="audit"
    ).run
    store.fail_agent_run(
        run.id, {"code": "codex_process_failed"}, owner="audit"
    )
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'done', 'error': ''},
            where_sql='and id=?',
            parameters=(task.id,),
        )

    assert store.reconcile_done_reply_tasks_with_failed_current_run() == 1
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "codex_process_failed"


def test_reconciliation_candidate_scan_uses_receipt_lookup_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    statements: list[str] = []
    original_connect = store._connect

    @contextmanager
    def traced_connect():
        with original_connect() as db:
            db.set_trace_callback(statements.append)
            try:
                yield db
            finally:
                db.set_trace_callback(None)

    monkeypatch.setattr(store, "_connect", traced_connect)
    assert store.reconcile_done_reply_tasks_with_failed_current_run() == 0

    [candidate_query] = [
        statement
        for statement in statements
        if statement.lstrip().lower().startswith(
            "select tasks.id, tasks.execution_generation"
        )
    ]
    with store._connect() as db:
        plan = db.execute(f"explain query plan {candidate_query}").fetchall()
    details = " ".join(str(row[3]) for row in plan)
    assert "idx_action_receipts_action" in details
    assert "idx_actions_errors_reply_task_settlement" in details
    assert "SCAN " not in details


def test_reconcile_done_failed_runs_commits_between_bounded_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_ids: list[int] = []
    for index in range(2):
        store.enqueue_reply_task(
            conversation_id=f"cid-reconcile-batch-{index}",
            conversation_title="Direct chat",
            single_chat=True,
            trigger_message_id=f"msg-reconcile-batch-{index}",
            trigger_create_time="2026-09-14 10:00:00",
            trigger_sender="Derek",
            trigger_text="Please handle this",
        )
        [task] = store.claim_reply_tasks(limit=1)
        task_ids.append(task.id)
        run = _claim_audit_run(
            store, task.id, task.execution_generation, owner="audit"
        ).run
        store.fail_agent_run(
            run.id, {"code": "codex_process_failed"}, owner="audit"
        )
        with store._connect() as db:
            _update_current_task8(
                db,
                'reply_tasks',
                {'status': 'done', 'error': ''},
                where_sql='and id=?',
                parameters=(task.id,),
            )

    monkeypatch.setattr(store_module, "DONE_FAILED_RUN_RECONCILE_BATCH_SIZE", 1)
    original_transaction = store._immediate_write_transaction
    committed_batches = 0
    interleaved_writer_acquired = False
    batch_transaction_sql: list[str] = []

    @contextmanager
    def transaction_with_competing_writer():
        nonlocal committed_batches, interleaved_writer_acquired
        with original_transaction() as db:
            statements: list[str] = []
            db.set_trace_callback(statements.append)
            try:
                yield db
            finally:
                db.set_trace_callback(None)
                batch_transaction_sql.extend(statements)
        committed_batches += 1
        if committed_batches == 1:
            with store._connect() as other_writer:
                other_writer.execute("begin immediate")
                _update_current_task8(
                    other_writer,
                    'reply_tasks',
                    {'status': 'skipped'},
                    where_sql='and id=?',
                    parameters=(task_ids[1],),
                )
                other_writer.execute("commit")
            interleaved_writer_acquired = True

    monkeypatch.setattr(
        store, "_immediate_write_transaction", transaction_with_competing_writer
    )

    assert store.reconcile_done_reply_tasks_with_failed_current_run() == 1
    assert committed_batches == 2
    assert interleaved_writer_acquired
    batch_selects = [
        statement.lower()
        for statement in batch_transaction_sql
        if statement.lstrip().lower().startswith(("select", "with"))
    ]
    candidate_selects = [
        statement for statement in batch_selects if statement.lstrip().startswith("with batch_candidates")
    ]
    assert len(candidate_selects) == 2
    # The only other reads in a batch are the native Task update's own bookkeeping.
    assert all(
        statement.strip() == "select current_timestamp"
        or statement.strip().startswith("select json(parameters_json) from tasks where id=")
        for statement in batch_selects if statement not in candidate_selects
    )
    assert all(
        "from (select cast(legacy_key as integer) as id" not in statement
        for statement in batch_selects
    )
    first = store.get_reply_task(task_ids[0])
    second = store.get_reply_task(task_ids[1])
    assert first is not None and first.status == "failed"
    assert second is not None and second.status == "skipped"


def test_generic_store_skips_email_receipt_reconciliation_without_email_schema(
    tmp_path: Path,
) -> None:
    """Core startup can run before the optional Email schema is initialized."""
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.reconcile_failed_email_unsubscribe_tasks_with_terminal_receipts() == 0


def test_reconcile_confirmation_required_run_stays_failed(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-legacy-confirmation",
        conversation_title="Management group",
        single_chat=False,
        trigger_message_id="msg-legacy-confirmation",
        trigger_create_time="2026-09-15 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please confirm the reviewed action.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = _claim_audit_run(
        store, task.id, task.execution_generation, owner="audit"
    ).run
    store.fail_agent_run(
        run.id,
        {
            "code": "confirmation_required",
            "retryable": True,
            "authorization_required": True,
        },
        owner="audit",
    )
    store.fail_reply_task(
        task.id,
        "confirmation_required",
        expected_execution_generation=task.execution_generation,
    )

    assert store.reconcile_failed_reply_tasks_with_confirmation_required_runs() == 0

    updated = store.get_reply_task(task.id)
    attempt = store.get_latest_reply_attempt_for_trigger(
        task.conversation_id,
        task.trigger_message_id,
    )

    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "confirmation_required"
    assert attempt is None


def test_reconcile_repairs_existing_confirmation_projection_to_failed(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-existing-confirmation",
        conversation_title="Management group",
        single_chat=False,
        trigger_message_id="msg-existing-confirmation",
        trigger_create_time="2026-09-15 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please handle this",
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = _claim_audit_run(
        store, task.id, task.execution_generation, owner="audit"
    ).run
    store.fail_agent_run(
        run.id,
        {
            "code": "confirmation_required",
            "retryable": True,
            "authorization_required": True,
        },
        owner="audit",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="needs_human",
        human_decision_options_json='[{"key":"confirm"}]',
        channel=task.channel,
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id,
                      {"agent_run_id": run.id, "send_error": "confirmation_required"})
    store.fail_reply_task(
        task.id,
        "confirmation_required",
        expected_execution_generation=task.execution_generation,
    )

    assert store.reconcile_failed_reply_tasks_with_confirmation_required_runs() == 1
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "failed"
    assert attempt.send_error == "confirmation_required"
    assert attempt.human_decision_options_json == "[]"
    assert "不是业务决策" in attempt.audit_summary


def test_reconcile_preserves_done_task_with_recorded_message_delivery(
    tmp_path: Path,
) -> None:
    """A later audit failure cannot undo an already recorded provider send."""
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-reconcile-delivery",
        conversation_title="Direct chat",
        single_chat=True,
        trigger_message_id="msg-reconcile-delivery",
        trigger_create_time="2026-09-14 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please handle this",
    )
    [task] = store.claim_reply_tasks(limit=1)
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer",
    ).run
    store.complete_agent_run(consumer.id, {"outcome": "proposal"}, owner="consumer")
    store.record_completed_agent_message_delivery(
        agent_run_id=consumer.id,
        external_action_key="recorded-delivery",
        business_object_key=task.business_object_key,
        action_identity="reply",
        operation="message.send",
        target_identifiers={"conversation_id": task.conversation_id},
        conversation_id=task.conversation_id,
        trigger_message_id=task.trigger_message_id,
        reply_text="Already delivered.",
        provider_result={"message_id": "provider-message-1"},
    )
    audit = _claim_audit_run(
        store, task.id, task.execution_generation, owner="audit"
    ).run
    store.fail_agent_run(audit.id, {"code": "audit_validation_failed"}, owner="audit")
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'done', 'error': ''},
            where_sql='and id=?',
            parameters=(task.id,),
        )

    assert store.reconcile_done_reply_tasks_with_failed_current_run() == 0
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "done"


def test_reconcile_recorded_delivery_closes_needs_human_task(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-recorded-needs-human",
        conversation_title="Direct chat",
        single_chat=True,
        trigger_message_id="msg-recorded-needs-human",
        trigger_create_time="2026-09-20 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please handle this",
    )
    [task] = store.claim_reply_tasks(limit=1)
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer",
    ).run
    store.complete_agent_run(consumer.id, {"outcome": "proposal"}, owner="consumer")
    store.record_completed_agent_message_delivery(
        agent_run_id=consumer.id,
        external_action_key="recorded-needs-human",
        business_object_key=task.business_object_key,
        action_identity="reply",
        operation="message.send",
        target_identifiers={"conversation_id": task.conversation_id},
        conversation_id=task.conversation_id,
        trigger_message_id=task.trigger_message_id,
        reply_text="Already delivered.",
        provider_result={"message_id": "provider-message-1"},
    )
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'needs_human', 'error': 'provider_effect_unreconciled'},
            where_sql='and id=?',
            parameters=(task.id,),
        )

    assert store.reconcile_failed_reply_tasks_with_recorded_deliveries() == 1
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "done"
    assert updated.error == ""


@pytest.mark.parametrize("verified,all_effects", [(True, True), (False, True), (True, False)])
def test_reconcile_failed_audit_requires_verified_delivery_and_all_effects(
    tmp_path: Path, monkeypatch, verified, all_effects,
) -> None:
    from app.dingtalk_send_evidence import DingTalkSendEvidenceDriver
    from app.business_identity import external_action_key

    store = AutoReplyStore(tmp_path / "audit-recovery.sqlite3")
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="cid-test", conversation_title="Direct",
        single_chat=True, trigger_message_id="msg-test", trigger_sender="Derek",
        trigger_create_time="2026-10-03 13:00:00", trigger_text="report",
    )
    task = store.claim_reply_tasks(1)[0]
    consumer = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    action = {"action_identity": "send", "operation": "send", "target": {"user_id": "derek"}}
    store.complete_agent_run(
        consumer.id,
        {"outcome": "proposal", "proposal": {"actions": [action]}},
        owner="consumer",
    )
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id="operation", owner="audit",
    ).run
    key = external_action_key(
        business_object_key=task.business_object_key, action_identity="send",
        operation="send", target_identifiers=action["target"],
    )
    historical_sent_delivery(store,
        agent_run_id=audit.id, external_action_key=key,
        conversation_id=task.conversation_id, trigger_message_id=task.trigger_message_id,
        reply_text="Delivered report", provider_result={
            "verification": {"state": "sent" if verified else "unknown"},
        },
    )
    store.fail_agent_run(audit.id, {"code": "codex_result_invalid"}, owner="audit")
    store.fail_reply_task(task.id, "codex_result_invalid", expected_execution_generation=task.execution_generation)
    monkeypatch.setattr(DingTalkSendEvidenceDriver, "audit_run_has_execution_evidence", lambda *a, **k: all_effects)
    if verified and all_effects:
        assert store.reconcile_failed_audit_with_verified_delivery(task.id, audit.id)
        assert store.get_reply_task(task.id).status == "done"
        assert store.reconcile_done_reply_tasks_with_failed_current_run() == 0
    else:
        with pytest.raises(ValueError):
            store.reconcile_failed_audit_with_verified_delivery(task.id, audit.id)
        assert store.get_reply_task(task.id).status == "failed"
    assert store.get_agent_run(audit.id).status == "failed"


def test_complete_reply_task_never_hides_failed_current_run(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-complete-failed-run",
        conversation_title="Direct chat",
        single_chat=True,
        trigger_message_id="msg-complete-failed-run",
        trigger_create_time="2026-09-14 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please handle this",
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = _claim_audit_run(
        store, task.id, task.execution_generation, owner="audit"
    ).run
    store.fail_agent_run(
        run.id,
        {"code": "runtime_result_validation_failed"},
        owner="audit",
    )

    store.complete_reply_task(
        task.id, expected_execution_generation=task.execution_generation
    )

    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "runtime_result_validation_failed"


def test_done_with_failed_latest_run_keeps_exact_input_until_settlement(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "failed-latest.sqlite3")
    task = store.ensure_reply_task(
        conversation_id="cid-failed-latest", conversation_title="Group",
        single_chat=False, trigger_message_id="msg-failed-latest",
        trigger_create_time="2026-10-10 10:00:00", trigger_sender="Sender",
        trigger_text="Recoverable original", trigger_message_json='{"content":"Recoverable original"}',
    )
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    run = _claim_audit_run(store, task.id, claimed.execution_generation, owner="audit").run
    store.fail_agent_run(run.id, {"code": "runtime_result_validation_failed"}, owner="audit")
    store.complete_reply_task(task.id, expected_execution_generation=claimed.execution_generation)
    assert store.get_reply_task(task.id).status == "failed"
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'done'},
            where_sql='and id=?',
            parameters=(task.id,),
        )
        assert not store._compact_settled_reply_task_input(db, task.id)
    unchanged = store.get_reply_task(task.id)
    assert unchanged is not None and unchanged.trigger_text == "Recoverable original"
    assert not unchanged.input_compacted
    assert store.reconcile_done_reply_tasks_with_failed_current_run() == 1
    assert store.get_reply_task(task.id).status == "failed"


def test_reply_input_compaction_rolls_back_with_terminal_transaction(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "rollback-input.sqlite3")
    task = store.ensure_reply_task(
        conversation_id="cid-rollback", conversation_title="Group", single_chat=False,
        trigger_message_id="msg-rollback", trigger_create_time="2026-10-10 10:00:00",
        trigger_sender="Sender", trigger_text="Original before rollback",
        trigger_message_json='{"content":"Original before rollback"}',
    )
    with pytest.raises(RuntimeError, match="abort terminal write"):
        with store._immediate_write_transaction() as db:
            _update_current_task8(
                db,
                'reply_tasks',
                {'status': 'done'},
                where_sql='and id=?',
                parameters=(task.id,),
            )
            assert store._compact_settled_reply_task_input(db, task.id)
            raise RuntimeError("abort terminal write")
    after = store.get_reply_task(task.id)
    assert after is not None and after.status == "pending"
    assert not after.input_compacted and after.trigger_text == "Original before rollback"
    [input_row] = store.list_reply_task_inputs(task.id)
    assert input_row["input_compacted"] == 0


def test_skip_failed_reply_task_superseded_by_terminal_business_object(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for conversation_id, message_id in (
        ("approval-old-scan", "approval-old"),
        ("approval-current-scan", "approval-current"),
    ):
        store.enqueue_reply_task(
            conversation_id=conversation_id,
            conversation_title="审批待办",
            single_chat=True,
            trigger_message_id=message_id,
            trigger_create_time="2026-09-14 10:00:00",
            trigger_sender="Derek OA",
            trigger_text="审批事项",
        )
    claimed = store.claim_reply_tasks(limit=2)
    old_task = next(task for task in claimed if task.trigger_message_id == "approval-old")
    current_task = next(
        task for task in claimed if task.trigger_message_id == "approval-current"
    )
    store.fail_reply_task(
        old_task.id,
        "codex_process_failed",
        expected_execution_generation=old_task.execution_generation,
    )
    store.complete_reply_task(
        current_task.id,
        expected_execution_generation=current_task.execution_generation,
    )
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'business_object_key': 'oa:process-1:task-1'},
            where_sql='and id in (?, ?)',
            parameters=(old_task.id, current_task.id),
        )
        _bind_business_object_key(db, 'oa:process-1:task-1', current_task.id)

    assert store.skip_failed_reply_tasks_superseded_by_terminal_business_object() == 1
    updated = store.get_reply_task(old_task.id)
    assert updated is not None
    assert updated.status == "skipped"
    assert updated.error == "superseded_by_terminal_business_object_task"


@pytest.mark.parametrize(
    ("current_task_status", "expected_reconciled"),
    (("done", 1), ("failed", 0)),
)
def test_skip_obsolete_needs_human_attempt_superseded_by_terminal_business_object(
    tmp_path: Path,
    current_task_status: str,
    expected_reconciled: int,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for conversation_id, message_id in (
        ("approval-old-human-scan", "approval-old-human"),
        ("approval-current-human-scan", "approval-current-human"),
    ):
        store.enqueue_reply_task(
            conversation_id=conversation_id,
            conversation_title="审批待办",
            single_chat=True,
            trigger_message_id=message_id,
            trigger_create_time="2026-09-14 10:00:00",
            trigger_sender="Derek OA",
            trigger_text="审批事项",
        )
    claimed = store.claim_reply_tasks(limit=2)
    old_task = next(
        task for task in claimed if task.trigger_message_id == "approval-old-human"
    )
    current_task = next(
        task
        for task in claimed
        if task.trigger_message_id == "approval-current-human"
    )
    old_run = _claim_audit_run(
        store, old_task.id, old_task.execution_generation, owner="audit"
    ).run
    store.complete_agent_run(old_run.id, {}, owner="audit")
    attempt_id = store.record_reply_attempt(
        conversation_id=old_task.conversation_id,
        conversation_title=old_task.conversation_title,
        trigger_message_id=old_task.trigger_message_id,
        trigger_sender=old_task.trigger_sender,
        trigger_text=old_task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="needs_human",
    )
    if current_task_status == "done":
        store.complete_reply_task(
            current_task.id,
            expected_execution_generation=current_task.execution_generation,
        )
    else:
        store.fail_reply_task(
            current_task.id,
            "current_task_failed",
            expected_execution_generation=current_task.execution_generation,
        )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id, {"agent_run_id": old_run.id})
        _update_current_task8(
            db,
            'reply_tasks',
            {'business_object_key': 'oa:process-2:task-2'},
            where_sql='and id in (?, ?)',
            parameters=(old_task.id, current_task.id),
        )
        _bind_business_object_key(db, 'oa:process-2:task-2', current_task.id)

    assert (
        store.skip_failed_reply_tasks_superseded_by_terminal_business_object()
        == expected_reconciled
    )
    updated_attempt = store.get_reply_attempt(attempt_id)
    assert updated_attempt is not None
    expected_attempt_status = "skipped" if expected_reconciled else "needs_human"
    assert updated_attempt.send_status == expected_attempt_status
    assert updated_attempt.send_error == ""
    with store._connect() as db:
        closure = db.execute(
            f"select resolved_at, resolution from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?",
            (attempt_id,),
        ).fetchone()
    assert closure is not None
    if expected_reconciled:
        assert closure["resolved_at"]
        assert closure["resolution"] == "同一业务对象已由新的终态任务接管；原人工决策不再有效。"
    else:
        assert closure["resolved_at"] == ""
        assert closure["resolution"] == ""


@pytest.mark.parametrize(
    ("latest_status", "expected_reconciled"),
    (("completed", 1), ("failed", 0)),
)
def test_skip_obsolete_needs_human_attempt_with_later_terminal_trigger_attempt(
    tmp_path: Path,
    latest_status: str,
    expected_reconciled: int,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_arguments = {
        "conversation_id": "same-trigger-history",
        "conversation_title": "管理群",
        "trigger_message_id": "same-trigger-message",
        "trigger_sender": "Derek",
        "trigger_text": "处理这件事",
        "action": "agent_run",
        "sensitivity_kind": "general",
    }
    old_attempt_id = store.record_reply_attempt(
        **attempt_arguments,
        send_status="needs_human",
    )
    store.record_reply_attempt(
        **attempt_arguments,
        send_status=latest_status,
    )

    assert (
        store.skip_failed_reply_tasks_superseded_by_terminal_business_object()
        == expected_reconciled
    )
    updated_attempt = store.get_reply_attempt(old_attempt_id)
    assert updated_attempt is not None
    expected_status = "skipped" if expected_reconciled else "needs_human"
    assert updated_attempt.send_status == expected_status


def test_history_hides_legacy_duplicate_attempt_projections_for_one_reply_task(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="legacy-attempt-conversation",
        conversation_title="历史管理群",
        single_chat=False,
        trigger_message_id="legacy-attempt-message",
        trigger_create_time="2026-08-27 04:00:00",
        trigger_sender="Derek",
        trigger_text="同一条触发消息",
    )
    [task] = store.claim_reply_tasks(limit=1)
    attempt_ids = [
        store.record_reply_attempt(
            conversation_id=task.conversation_id,
            conversation_title=task.conversation_title,
            trigger_message_id=task.trigger_message_id,
            trigger_sender=task.trigger_sender,
            trigger_text=task.trigger_text,
            action="agent_run",
            sensitivity_kind="general",
            send_status="skipped",
        )
        for _ in range(5)
    ]
    with store._connect() as db:
        for generation, attempt_id in enumerate(attempt_ids):
            run = _insert_run_row(db, {
                    'task_id': task.id,
                    'execution_generation': task.execution_generation if generation == len(attempt_ids) - 1
                    else f"legacy-generation-{generation}",
                    'role': 'consumer',
                    'operation_id': f"legacy-{generation}",
                    'status': 'completed',
                },
            )
            update_action(db, "reply_attempts", attempt_id,
                          {"agent_run_id": run.lastrowid})

    history_ids = [
        item.source_id
        for item in store.list_history_items(kinds=("reply",))
        if item.source_id in attempt_ids
    ]
    operation_log_total, operation_logs = store.list_operation_logs_with_count(
        limit=20,
        source_tables=("reply_attempts",),
    )
    operation_log_ids = [
        item.source_id
        for item in operation_logs
        if item.source_id in attempt_ids
    ]

    assert history_ids == [attempt_ids[-1]]
    assert operation_log_total == 1
    assert operation_log_ids == [attempt_ids[-1]]


@pytest.mark.parametrize(
    ("attempt_status", "expected_task_status"),
    (
        ("completed", "done"),
        ("decision_selected", "done"),
        ("skipped", "skipped"),
        ("needs_human", "needs_human"),
    ),
)
def test_failed_reply_task_follows_latest_terminal_trigger_attempt(
    tmp_path: Path,
    attempt_status: str,
    expected_task_status: str,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-terminal-attempt",
        conversation_title="Management",
        single_chat=True,
        trigger_message_id="msg-terminal-attempt",
        trigger_create_time="2026-09-15 09:00:00",
        trigger_sender="Derek",
        trigger_text="Handle this.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    store.fail_reply_task(
        task.id,
        "codex_result_missing",
        expected_execution_generation=task.execution_generation,
    )
    store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status=attempt_status,
    )

    assert store.reconcile_unresolved_reply_tasks_with_terminal_attempts() == 1
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == expected_task_status
    # The outcome is settled, but why it failed on the way is kept. Clearing
    # it is what made the weekly report's lost week unreadable: the task ended
    # `skipped` with an empty error and nothing said a provider overload had
    # cut it short.
    assert updated.error == "reconciled_from_failed:codex_result_missing"


def test_needs_human_task_follows_resolved_failed_attempt(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-settled-attempt",
        conversation_title="Management",
        single_chat=True,
        trigger_message_id="msg-settled-attempt",
        trigger_create_time="2026-09-20 09:00:00",
        trigger_sender="Derek",
        trigger_text="Handle this.",
    )
    [task] = store.claim_reply_tasks(limit=1)
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'needs_human', 'error': 'external_action_completed:verified readback'},
            where_sql='and id=?',
            parameters=(task.id,),
        )
        update_action(db, "reply_attempts", attempt_id, {
            "send_error": "agent_reported_failure",
            "resolved_at": db.execute("select current_timestamp").fetchone()[0],
            "resolution": "Provider action was sent and read back.",
        })

    assert store.reconcile_unresolved_reply_tasks_with_terminal_attempts() == 1
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "done"
    assert updated.error.startswith("reconciled_from_needs_human:")


def test_live_readback_can_close_settled_needs_human_task(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-calendar-readback",
        conversation_title="Calendar invite",
        single_chat=True,
        trigger_message_id="msg-calendar-readback",
        trigger_create_time="2026-09-20 09:00:00",
        trigger_sender="Casey",
        trigger_text="Calendar invite",
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = _claim_audit_run(
        store, task.id, task.execution_generation, owner="audit"
    ).run
    store.fail_agent_run(
        run.id,
        {"code": "codex_process_failed"},
        owner="audit",
    )
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'needs_human', 'error': 'calendar_response_not_executed'},
            where_sql='and id=?',
            parameters=(task.id,),
        )

    assert not store.close_unresolved_reply_task_already_settled(
        task.id,
        expected_execution_generation="stale-generation",
        settled_evidence="stale external readback must not close newer work",
    )
    assert store.close_unresolved_reply_task_already_settled(
        task.id,
        expected_execution_generation=task.execution_generation,
        settled_evidence=(
            "Live calendar readback shows the principal attendee accepted event-1."
        ),
    )
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "done"
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'failed'},
            where_sql='and id=?',
            parameters=(task.id,),
        )
    assert store.reconcile_unresolved_reply_tasks_with_settlement_evidence() == 1
    assert store.get_reply_task(task.id).status == "done"
    assert store.reconcile_done_reply_tasks_with_failed_current_run() == 0
    assert store.get_reply_task(task.id).status == "done"
    with store._connect() as db:
        error = db.execute(
            f'select resolved_at, resolution from ({ERROR_SQL}) where conversation_id=? and message_id=? order by id desc limit 1',
            (task.conversation_id, task.trigger_message_id),
        ).fetchone()
    assert error is not None
    assert error[0]
    assert "accepted event-1" in error[1]


def test_expired_reply_task_can_be_skipped_with_generation_bound_evidence(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-calendar-expired",
        conversation_title="Calendar invite",
        single_chat=True,
        trigger_message_id="msg-calendar-expired",
        trigger_create_time="2026-10-15 09:00:00",
        trigger_sender="Casey",
        trigger_text="Calendar invite",
    )
    [task] = store.claim_reply_tasks(limit=1)
    with store._connect() as db:
        _update_current_task8(
            db, "reply_tasks", {"status": "failed", "error": "external_action_uncertain"},
            where_sql="and id=?", parameters=(task.id,),
        )

    assert not store.skip_unresolved_reply_task(
        task.id,
        expected_execution_generation="stale-generation",
        reason="Calendar event has ended; no further response action is required.",
    )
    assert store.skip_unresolved_reply_task(
        task.id,
        expected_execution_generation=task.execution_generation,
        reason="Calendar event has ended; no further response action is required.",
    )
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "skipped"
    assert updated.error == "external_action_uncertain"
    with store._connect() as db:
        error = db.execute(
            f"select kind, detail, resolved_at, resolution from ({store_module.ERROR_SQL}) "
            "where conversation_id=? and message_id=? order by id desc limit 1",
            (task.conversation_id, task.trigger_message_id),
        ).fetchone()
    assert error is not None
    assert error[0] == "reply_task_skipped_by_operator"
    assert error[1] == error[3]
    assert error[2]
    assert "event has ended" in error[3]


def test_uncertain_oa_reply_can_only_be_skipped_for_its_exact_terminal_source(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-oa-terminal",
        conversation_title="OA follow-up",
        single_chat=False,
        trigger_message_id="msg-oa-terminal",
        trigger_create_time="2026-10-16 09:00:00",
        trigger_sender="Derek OA",
        trigger_text="Follow up",
        business_object_key="oa:process-1:task-1",
    )
    [task] = store.claim_reply_tasks(limit=1)
    with store._connect() as db:
        _update_current_task8(
            db, "reply_tasks", {"status": "failed", "error": "external_action_uncertain"},
            where_sql="and id=?", parameters=(task.id,),
        )

    evidence = (
        "Verified live DingTalk OA terminal state for exact reviewed candidate: "
        "process_instance_id=process-1; task_id=task-1; process_status=TERMINATED; "
        "task_status=CANCELED"
    )
    assert not store.skip_unresolved_reply_task_for_terminal_oa_source(
        task.id,
        expected_execution_generation=task.execution_generation,
        process_instance_id="process-2",
        oa_task_id="task-1",
        settled_evidence=evidence,
    )
    assert not store.skip_unresolved_reply_task_for_terminal_oa_source(
        task.id,
        expected_execution_generation="stale-generation",
        process_instance_id="process-1",
        oa_task_id="task-1",
        settled_evidence=evidence,
    )
    assert store.skip_unresolved_reply_task_for_terminal_oa_source(
        task.id,
        expected_execution_generation=task.execution_generation,
        process_instance_id="process-1",
        oa_task_id="task-1",
        settled_evidence=evidence,
    )
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "skipped"
    assert updated.error == "external_action_uncertain"
    assert store.count_current_unresolved_problem_attempts() == 0
    with store._connect() as db:
        error = db.execute(
            f"select kind, detail, resolved_at, resolution from ({store_module.ERROR_SQL}) "
            "where conversation_id=? and message_id=? order by id desc limit 1",
            (task.conversation_id, task.trigger_message_id),
        ).fetchone()
    assert error is not None
    assert error[0] == "reply_task_skipped_terminal_oa"
    assert error[1] == evidence == error[3]
    assert error[2]


def test_settlement_evidence_reconcile_commits_between_bounded_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_ids: list[int] = []
    for index in range(2):
        store.enqueue_reply_task(
            conversation_id=f"cid-settlement-batch-{index}",
            conversation_title="Direct chat",
            single_chat=True,
            trigger_message_id=f"msg-settlement-batch-{index}",
            trigger_create_time="2026-09-20 09:00:00",
            trigger_sender="Derek",
            trigger_text="Please handle this",
        )
        [task] = store.claim_reply_tasks(limit=1)
        task_ids.append(task.id)
        with store._connect() as db:
            _update_current_task8(
                db,
                'reply_tasks',
                {'status': 'failed'},
                where_sql='and id=?',
                parameters=(task.id,),
            )
            insert_error_action(db, {'conversation_id': task.conversation_id, 'message_id': task.trigger_message_id, 'kind': 'reply_task_already_settled', 'detail': 'External settlement was verified.', 'resolution': 'External settlement was verified.'}, resolved_at_now=True)

    monkeypatch.setattr(store_module, "SETTLED_REPLY_RECONCILE_BATCH_SIZE", 1)
    original_transaction = store._immediate_write_transaction
    committed_batches = 0
    interleaved_writer_acquired = False

    @contextmanager
    def transaction_with_competing_writer():
        nonlocal committed_batches, interleaved_writer_acquired
        with original_transaction() as db:
            yield db
        committed_batches += 1
        if committed_batches == 1:
            with store._connect() as other_writer:
                other_writer.execute("begin immediate")
                _update_current_task8(
                    other_writer,
                    'reply_tasks',
                    {'status': 'skipped'},
                    where_sql='and id=?',
                    parameters=(task_ids[1],),
                )
                other_writer.execute("commit")
            interleaved_writer_acquired = True

    monkeypatch.setattr(
        store, "_immediate_write_transaction", transaction_with_competing_writer
    )

    assert store.reconcile_unresolved_reply_tasks_with_settlement_evidence() == 1
    assert committed_batches == 2
    assert interleaved_writer_acquired
    first = store.get_reply_task(task_ids[0])
    second = store.get_reply_task(task_ids[1])
    assert first is not None and first.status == "done"
    assert second is not None and second.status == "skipped"


def test_settlement_reconcile_preserves_uncertain_external_action(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-settlement-uncertain",
        conversation_title="Direct chat",
        single_chat=True,
        trigger_message_id="msg-settlement-uncertain",
        trigger_create_time="2026-09-20 09:00:00",
        trigger_sender="Derek",
        trigger_text="Please handle this",
    )
    [task] = store.claim_reply_tasks(limit=1)
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'failed', 'error': 'external_action_uncertain'},
            where_sql='and id=?',
            parameters=(task.id,),
        )
        insert_error_action(db, {'conversation_id': task.conversation_id, 'message_id': task.trigger_message_id, 'kind': 'reply_task_already_settled', 'detail': 'A stale settlement record must not override uncertain delivery.', 'resolution': 'A stale settlement record must not override uncertain delivery.'}, resolved_at_now=True)

    assert store.reconcile_unresolved_reply_tasks_with_settlement_evidence() == 0
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "external_action_uncertain"


def test_skip_failed_reply_task_with_terminal_no_action_run(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-already-completed",
        conversation_title="Approval scan",
        single_chat=True,
        trigger_message_id="msg-already-completed",
        trigger_create_time="2026-09-14 10:00:00",
        trigger_sender="Derek OA",
        trigger_text="This approval is already complete",
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = _claim_audit_run(
        store, task.id, task.execution_generation, owner="audit"
    ).run
    store.fail_agent_run(
        run.id, {"code": "task_already_completed"}, owner="audit"
    )
    store.fail_reply_task(
        task.id,
        "task_already_completed",
        expected_execution_generation=task.execution_generation,
    )

    assert store.skip_failed_reply_tasks_with_terminal_no_action_run() == 1
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "skipped"
    assert updated.error == "skipped_terminal_no_action:task_already_completed"


def test_earliest_delivery_receipt_records_exact_configured_feedback_token(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv(
        "CEO_FEEDBACK_SPIKE_VERCEL_BASE_URL",
        "https://feedback.example.com",
    )
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    reply_text = (
        "Verified direct reply（by明哥分身）\n\n"
        "反馈：[👍 有帮助](https://feedback.example.com/api/dingtalk-feedback-spike"
        "?feedback_token=spike_1_abcd1234&rating=up)｜"
        "[👎 需改进](https://feedback.example.com/api/dingtalk-feedback-spike"
        "?feedback_token=spike_1_abcd1234&rating=down)"
    )

    store.record_sent_reply("cid-1", "msg-1", reply_text)

    sent = store.get_sent_reply("cid-1", "msg-1")
    assert sent is not None
    assert sent.feedback_token == "spike_1_abcd1234"


def test_finalize_orchestration_inherits_oa_identity_from_reply_task(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    oa_url = (
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"
    )
    store.enqueue_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id="oa-pending:proc-1:revision-1",
        trigger_create_time="2026-08-12 10:00:00",
        trigger_sender="Derek OA",
        trigger_text="吴柯欣提交的录用申请",
        oa_url=oa_url,
    )
    [task] = store.claim_reply_tasks(limit=1)
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer",
    ).run
    consumer = store.complete_agent_run(
        consumer.id,
        {"outcome": "no_action", "summary": "当前审批节点已经完成。"},
        owner="consumer",
    )

    attempt_id = store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=consumer.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="当前审批节点已经完成。",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="当前审批节点已经完成。",
        send_status="skipped",
        send_error="",
        channel="dingtalk",
    )

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_url == oa_url
    assert attempt.oa_action == "review"


def test_finalize_pre_run_failure_inherits_oa_identity_from_reply_task(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    oa_url = (
        "https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1"
    )
    store.enqueue_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id="oa-pending:proc-1:revision-1",
        trigger_create_time="2026-08-12 10:00:00",
        trigger_sender="Derek OA",
        trigger_text="吴柯欣提交的录用申请",
        oa_url=oa_url,
    )
    [task] = store.claim_reply_tasks(limit=1)

    attempt_id = store.finalize_reply_task_without_run(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        task_status="failed",
        task_error="worker_start_failed",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="worker_start_failed",
        audit_summary="worker_start_failed",
        send_status="failed",
        send_error="worker_start_failed",
        channel="dingtalk",
    )

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_url == oa_url
    assert attempt.oa_action == "review"


def test_finalize_orchestration_reuses_current_reply_attempt_projection(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-reuse-attempt",
        conversation_title="Direct chat",
        single_chat=True,
        trigger_message_id="msg-reuse-attempt",
        trigger_create_time="2026-08-12 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please retry",
    )
    [task] = store.claim_reply_tasks(limit=1)
    run = _claim_audit_run(
        store,
        task.id,
        task.execution_generation,
        owner="audit",
    ).run
    store.complete_agent_run(
        run.id,
        {"outcome": "failed", "summary": "temporary provider failure"},
        owner="audit",
    )

    first_id = store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=run.id,
        task_status="pending",
        task_error="temporary provider failure",
        available_at="2026-08-12 10:01:00",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="temporary provider failure",
        codex_session_id="session-1",
        codex_transcript_start_line=1,
        codex_transcript_end_line=2,
        audit_tool_events_json="[]",
        audit_summary="first projection",
        send_status="failed",
        send_error="temporary provider failure",
        channel=task.channel,
    )
    assert store.count_reply_attempts() == 1

    second_id = store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=run.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="provider recovered",
        codex_session_id="session-2",
        codex_transcript_start_line=3,
        codex_transcript_end_line=4,
        audit_tool_events_json="[]",
        audit_summary="final projection",
        send_status="skipped",
        send_error="",
        channel=task.channel,
    )
    assert second_id == first_id
    assert store.count_reply_attempts() == 1
    current = store.get_reply_attempt(first_id)
    assert current is not None
    assert current.codex_reason == "provider recovered"
    assert current.audit_summary == "final projection"
    assert current.send_status == "skipped"


def test_pre_run_failure_projection_is_reused_by_later_agent_run(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-pre-run-reuse",
        conversation_title="Direct chat",
        single_chat=True,
        trigger_message_id="msg-pre-run-reuse",
        trigger_create_time="2026-08-12 10:00:00",
        trigger_sender="Derek",
        trigger_text="Please retry",
    )
    [task] = store.claim_reply_tasks(limit=1)
    first_id = store.finalize_reply_task_without_run(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        task_status="pending",
        task_error="worker_start_failed",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="worker_start_failed",
        audit_summary="worker_start_failed",
        send_status="failed",
        send_error="worker_start_failed",
        channel=task.channel,
    )
    assert store.count_reply_attempts() == 1

    [retry_task] = store.claim_reply_tasks(limit=1)
    run = _claim_audit_run(
        store,
        retry_task.id,
        retry_task.execution_generation,
        owner="audit",
    ).run
    store.complete_agent_run(
        run.id,
        {"outcome": "no_action", "summary": "recovered"},
        owner="audit",
    )
    second_id = store.finalize_orchestrated_reply_task(
        task_id=retry_task.id,
        expected_execution_generation=retry_task.execution_generation,
        run_id=run.id,
        task_status="done",
        task_error="",
        available_at="",
        conversation_id=retry_task.conversation_id,
        conversation_title=retry_task.conversation_title,
        trigger_message_id=retry_task.trigger_message_id,
        trigger_sender=retry_task.trigger_sender,
        trigger_text=retry_task.trigger_text,
        codex_reason="recovered",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="recovered",
        send_status="skipped",
        send_error="",
        channel=retry_task.channel,
    )
    assert second_id == first_id
    assert store.count_reply_attempts() == 1
    assert store.get_reply_attempt(first_id).codex_reason == "recovered"


def test_store_indexes_and_searches_codex_sessions_with_fts_and_embeddings(
    tmp_path: Path, monkeypatch,
):
    from app import native_trajectory
    from app.meeting_alignment_models import MeetingSource
    from tests.test_meeting_alignment import summary_decision

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    decisions = [
        summary_decision().model_copy(update={"final_message": "先定义上线风险预算与故障面"}),
        summary_decision().model_copy(update={"final_message": "客服解释口径"}),
    ]
    sources = [MeetingSource.model_validate({
        "meeting_id": f"meeting-{index}", "title": title,
        "status": "ended", "started_at": "2026-07-14T07:00:00+08:00",
        "ended_at": "2026-07-14T08:00:00+08:00",
        "participants": [{"name": "Derek", "user_id": "derek"}],
        "attendee_evidence": "calendar", "attendee_roster_complete": True,
        "current_user_id": "derek", "summary": "会议摘要", "transcript": [],
    }) for index, title in enumerate(("上线评审", "客服话术"))]

    def native_lines():
        lines = []
        for source, decision in zip(sources, decisions):
            lines.append(json.dumps({"type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [{
                    "type": "input_text", "text": "完整会议来源 JSON：\n" + source.model_dump_json()
                }]
            }}, ensure_ascii=False))
            lines.append(json.dumps({"type": "response_item", "payload": {
                "type": "message", "role": "assistant", "content": [{
                    "type": "output_text", "text": decision.model_dump_json()
                }]
            }}, ensure_ascii=False))
        return "\n".join(lines) + "\n"

    native = tmp_path / "meetings.jsonl"
    native.write_text(native_lines())
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *args, **kwargs: native)
    for index, (session_id, title) in enumerate((
        ("session-risk-budget", "上线评审"),
        ("session-customer-script", "客服话术"),
    )):
        job_id = store.upsert_meeting_alignment_job(
            meeting_id=f"meeting-{index}", title=title,
            source_json='{"minutes_info":{"summary":"数据库旧摘要"}}',
            participants_json='[{"name":"Derek"}]',
            ended_at="2026-07-14T08:00:00+08:00",
            eligible_at="2026-07-14T08:10:00+08:00", status="no_action",
        )
        run_id = store.create_professional_run(job_id, session_id=session_id, status="ready_to_send", error="", transcript_start=index * 2, transcript_end=index * 2 + 2)
        with store._connect() as db:
            _insert_runtime_attempt_row(db, {
                    'workload_kind': 'meeting',
                    'workload_key': str(run_id),
                    'attempt_number': 1,
                    'route_name': 'test',
                    'runtime_kind': 'codex_cli',
                    'credential_mode': 'local_oauth',
                    'model': 'test',
                    'session_id': session_id,
                    'status': 'completed',
                    'transcript_start': index * 2,
                    'transcript_end': index * 2 + 2,
                },
            )
        store.upsert_codex_session_search_index(
            session_id=session_id, source_type="meeting_alignment",
            source_id=str(run_id), title=title,
        )
        with pytest.raises(TypeError, match="unexpected keyword"):
            store.upsert_codex_session_search_index(
                session_id=session_id, source_type="meeting_alignment",
                source_id=str(run_id), title=title,
                summary_text="private summary", fts_text="private token",
                embedding=[1.0, 0.0],
            )

    calls = []

    class Embed:
        model = "test-model-1"
        base_url = "test://embedding"

        def __call__(self, texts):
            calls.append(tuple(texts))
            return [[1.0, 0.0] if "风险" in text else [0.0, 1.0] for text in texts]

    embed = Embed()
    results = store.search_codex_sessions(
        fts_query="上线 OR 风险",
        query_embedding=[1.0, 0.0],
        embedding_client=embed,
        limit=2,
    )

    assert [result.session_id for result in results] == [
        "session-risk-budget",
        "session-customer-script",
    ]
    assert results[0].embedding_score > results[1].embedding_score
    assert results[0].bm25_score is not None
    assert "数据库旧摘要" not in results[0].summary_text
    store.search_codex_sessions(
        fts_query="上线 OR 风险", query_embedding=[1.0, 0.0],
        embedding_client=embed, limit=2,
    )
    assert len(calls) == 1
    decisions[0] = decisions[0].model_copy(
        update={"final_message": "更新的上线风险预算与故障面"}
    )
    native.write_text(native_lines())
    refreshed = store.search_codex_sessions(
        fts_query="上线 OR 风险", query_embedding=[1.0, 0.0],
        embedding_client=embed, limit=2,
    )
    assert "更新的上线风险预算" in refreshed[0].summary_text
    assert len(calls) == 2
    embed.model = "test-model-2"
    store.search_codex_sessions(
        fts_query="上线 OR 风险", query_embedding=[1.0, 0.0],
        embedding_client=embed, limit=2,
    )
    assert len(calls) == 3
    with store._connect() as db:
        _update_current_task8(
            db,
            'meeting_alignment_jobs',
            {'source_json': '{"minutes_info":{"summary":"业务补充摘要"}}'},
            where_sql='and id=1',
        )
    refreshed_source = store.search_codex_sessions(
        fts_query="上线 OR 风险", query_embedding=[1.0, 0.0],
        embedding_client=embed, limit=2,
    )
    assert "会议摘要" in refreshed_source[0].summary_text
    assert "业务补充摘要" not in refreshed_source[0].summary_text
    assert len(calls) == 3
    lines = native_lines().splitlines()
    lines[0] = json.dumps({"type": "event_msg", "payload": {"type": "task_started"}})
    native.write_text("\n".join(lines) + "\n")
    missing_source = store.search_codex_sessions(
        fts_query="上线 OR 风险", query_embedding=[1.0, 0.0],
        embedding_client=embed, limit=2,
    )
    by_session = {item.session_id: item for item in missing_source}
    assert by_session["session-risk-budget"].summary_text == ""
    assert by_session["session-risk-budget"].native_available is False
    assert by_session["session-risk-budget"].native_reason == "native_input_unavailable"
    assert by_session["session-risk-budget"].embedding_score == 0.0
    with store._connect() as db:
        columns = {column[0] for column in db.execute(
            "select * from codex_session_search_index limit 0"
        ).description}
        assert columns == {"id", "session_id", "source_type", "source_id", "title", "created_at", "updated_at"}
        assert not columns.intersection({
            'summary_text', 'fts_text', 'embedding_json', 'embedding_model', 'embedding_updated_at',
        })
        rows = db.execute(
            "select session_id,title from codex_session_search_index order by session_id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ('session-customer-script', '客服话术'),
            ('session-risk-budget', '上线评审'),
        ]
        assert db.execute(
            "select count(*) from sqlite_schema "
            "where name='codex_session_search_fts'"
        ).fetchone()[0] == 0


def test_store_connections_enable_sqlite_concurrency_pragmas(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with store._connect() as db:
        journal_mode = db.execute("pragma journal_mode").fetchone()[0]
        busy_timeout = db.execute("pragma busy_timeout").fetchone()[0]
        synchronous = db.execute("pragma synchronous").fetchone()[0]
        foreign_keys = db.execute("pragma foreign_keys").fetchone()[0]

    assert journal_mode == "wal"
    assert busy_timeout >= 30_000
    assert synchronous == 1
    assert foreign_keys == 1


def test_store_restores_wal_for_current_schema_database(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    AutoReplyStore(db_path)
    store_module._INITIALIZED_STORE_PATHS.clear()
    # Leaving WAL needs this to be the only connection to the file.
    store_module._close_idle_connections()
    with sqlite3.connect(db_path) as db:
        assert db.execute("pragma journal_mode = delete").fetchone()[0] == "delete"

    AutoReplyStore(db_path)

    with sqlite3.connect(db_path) as db:
        assert db.execute("pragma journal_mode").fetchone()[0] == "wal"






def test_skip_unstarted_service_tasks_requires_exact_legacy_source(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-service",
        conversation_title="Service",
        single_chat=True,
        trigger_message_id="legacy-service",
        trigger_create_time="2026-08-11 10:00:00",
        trigger_sender="Service",
        trigger_text="synthetic recovery",
        trigger_message_json=json.dumps(
            {"raw_payload": {"source": "oa_pending_scan"}}
        ),
    )
    task = store.claim_reply_tasks(limit=1)[0]

    with pytest.raises(ValueError, match="not an explicit service task"):
        store.skip_unstarted_service_tasks(
            [task.id],
            reason="The synthetic recovery source was invalid.",
            expected_source="wrong_source",
        )

    skipped = store.skip_unstarted_service_tasks(
        [task.id],
        reason="The synthetic recovery source was invalid.",
        expected_source="oa_pending_scan",
    )
    assert skipped[0].status == "done"


def test_store_connections_can_use_short_busy_timeout(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3", busy_timeout_seconds=2)

    with store._connect() as db:
        busy_timeout = db.execute("pragma busy_timeout").fetchone()[0]

    assert busy_timeout == 2_000


def test_immediate_write_transaction_retries_begin_without_repeating_body(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    original_connect = store._connect
    begin_attempts = 0
    body_runs = 0

    @contextmanager
    def flaky_connect():
        nonlocal begin_attempts
        with original_connect() as db:
            begin_attempts += 1
            if begin_attempts == 1:
                class BeginLocked:
                    def execute(self, _sql):
                        raise sqlite3.OperationalError("database is locked")

                yield BeginLocked()
            else:
                yield db

    monkeypatch.setattr(store, "_connect", flaky_connect)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    with store._immediate_write_transaction() as db:
        body_runs += 1
        insert_error_action(db, {'conversation_id': 'cid', 'message_id': 'msg', 'kind': 'test', 'detail': 'lock retry'})

    assert begin_attempts == 2
    assert body_runs == 1
    assert len(store.list_errors()) == 1


def test_immediate_write_transaction_preserves_body_lock_error(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    error = sqlite3.OperationalError("database is locked")
    sleeps = []
    real_sleep = time.sleep
    caller = get_ident()

    def record_sleep(seconds):
        # The test-memory watchdog thread polls with time.sleep as well.
        if get_ident() == caller:
            sleeps.append(seconds)
        else:
            real_sleep(seconds)

    monkeypatch.setattr(store_module.time, "sleep", record_sleep)

    with pytest.raises(sqlite3.OperationalError) as caught:
        with store._immediate_write_transaction() as db:
            store_module.insert_error_action(db, {
                "conversation_id": "cid", "message_id": "msg",
                "kind": "test", "detail": "must roll back",
            })
            raise error

    assert caught.value is error
    assert sleeps == []
    assert store.list_errors() == []


def test_slow_sqlite_context_names_caller_without_local_values(tmp_path: Path, monkeypatch, capsys):
    from app import store as module
    store = AutoReplyStore(tmp_path / "slow-context.sqlite3")
    ticks = iter((0.0, 3.0, 4.0, 5.0))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    private_value = "private-context-body"
    with store._connect() as db:
        db.execute("select ?", (private_value,)).fetchone()
    output = capsys.readouterr().err
    assert "elapsed_seconds=5.000" in output
    assert "body_seconds=3.000 finish_seconds=1.000 close_seconds=1.000" in output
    assert "test_slow_sqlite_context_names_caller" in output
    assert private_value not in output


def test_slow_transaction_names_business_caller_through_wrapper(tmp_path: Path, capsys):
    import time

    store = AutoReplyStore(tmp_path / "wrapped-context.sqlite3")
    with store._immediate_write_transaction() as db:
        db.execute("select 1").fetchone()
        time.sleep(1.01)
    output = capsys.readouterr().err
    assert "lock_wait_seconds=0.000" in output
    assert "hold_seconds=1." in output
    assert "test_slow_transaction_names_business_caller_through_wrapper" in output


def test_sqlite_error_diagnostic_names_caller_without_local_values(tmp_path: Path, capsys):
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorname = "SQLITE_BUSY"
    error.sqlite_errorcode = 5
    private_value = "private-message-body"
    store_module._name_sqlite_extended_error(tmp_path / "worker.sqlite3", error)
    diagnostic = capsys.readouterr().err
    assert "test_sqlite_error_diagnostic_names_caller_without_local_values" in diagnostic
    assert "thread=" in diagnostic
    assert private_value not in diagnostic


def test_set_service_state_retries_a_transient_write_lock(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "service-state-lock.sqlite3")
    original_connect = store._connect
    connect_attempts = 0

    @contextmanager
    def flaky_connect():
        nonlocal connect_attempts
        connect_attempts += 1
        with original_connect() as db:
            if connect_attempts == 1:

                class WriteLockedConnection:
                    @property
                    def in_transaction(self):
                        return db.in_transaction

                    def execute(self, sql, parameters=()):
                        if sql.lstrip().lower().startswith("begin immediate"):
                            return db.execute(sql, parameters)
                        raise sqlite3.OperationalError("database is locked")

                yield WriteLockedConnection()
                return
            yield db

    monkeypatch.setattr(store, "_connect", flaky_connect)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    store.set_service_state("component:email-worker", '{"status":"ready"}')

    assert connect_attempts == 2
    assert store.get_service_state("component:email-worker") == '{"status":"ready"}'


def test_store_connections_close_after_context_exit(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with store._connect() as db:
        db.execute("select 1").fetchone()

    # The connection now waits in this thread's idle slot for the next call;
    # closing the slot closes it.
    store_module._close_idle_connections()
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute("select 1").fetchone()


def test_store_initializes_same_path_once_per_process(tmp_path: Path, monkeypatch):
    calls: list[Path] = []
    original_initialize = AutoReplyStore._initialize

    def counted_initialize(self: AutoReplyStore) -> None:
        calls.append(self.path)
        original_initialize(self)

    monkeypatch.setattr(AutoReplyStore, "_initialize", counted_initialize)
    db_path = tmp_path / "worker.sqlite3"

    AutoReplyStore(db_path)
    AutoReplyStore(db_path)

    assert calls == [db_path]


def test_store_skips_schema_work_when_another_process_finished_it(
    tmp_path: Path, monkeypatch
):
    db_path = tmp_path / "worker.sqlite3"
    AutoReplyStore(db_path)


def test_store_rejects_missing_action_index_without_recreating_retired_table(tmp_path):
    path = tmp_path / "damaged.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute("drop index idx_actions_sync_follow_up_send_attempts_0")
        before = tuple(db.iterdump())
    with pytest.raises(sqlite3.IntegrityError, match="malformed"):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before


def test_store_rechecks_schema_after_transient_database_lock(tmp_path, monkeypatch):
    db_path = tmp_path / "worker.sqlite3"
    AutoReplyStore(db_path)
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    original_schema_check = AutoReplyStore._schema_is_current
    checks = 0

    def flaky_schema_check(self: AutoReplyStore) -> bool:
        nonlocal checks
        checks += 1
        if checks == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_schema_check(self)

    def unexpected_initialize(_self: AutoReplyStore) -> None:
        raise AssertionError("transient database lock must not trigger migration")

    monkeypatch.setattr(AutoReplyStore, "_schema_is_current", flaky_schema_check)
    monkeypatch.setattr(AutoReplyStore, "_initialize", unexpected_initialize)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    AutoReplyStore(db_path)

    assert checks == 2
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    def unexpected_initialize(_self: AutoReplyStore) -> None:
        raise AssertionError("schema work should not repeat after another process")

    monkeypatch.setattr(AutoReplyStore, "_initialize", unexpected_initialize)

    AutoReplyStore(db_path)


@pytest.mark.parametrize(
    ("error_type", "error_code", "error_name", "message"),
    [
        (
            sqlite3.OperationalError,
            sqlite3.SQLITE_IOERR | (2 << 8),
            "SQLITE_IOERR_SHORT_READ",
            "disk I/O error",
        ),
        (
            sqlite3.DatabaseError,
            sqlite3.SQLITE_CORRUPT,
            "SQLITE_CORRUPT",
            "database disk image is malformed",
        ),
        (
            sqlite3.DatabaseError,
            sqlite3.SQLITE_NOTADB,
            "SQLITE_NOTADB",
            "file is not a database",
        ),
        (
            sqlite3.OperationalError,
            sqlite3.SQLITE_CANTOPEN,
            "SQLITE_CANTOPEN",
            "unable to open database file",
        ),
    ],
)
@pytest.mark.parametrize(
    "schema_check_name",
    ["_schema_is_current", "_schema_manifest_is_current"],
)
def test_schema_checks_propagate_non_schema_sqlite_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error_type: type[sqlite3.Error],
    error_code: int,
    error_name: str,
    message: str,
    schema_check_name: str,
) -> None:
    store = AutoReplyStore(tmp_path / "schema-failure.sqlite3")
    capsys.readouterr()
    error = _sqlite_failure(
        message,
        code=error_code,
        name=error_name,
        error_type=error_type,
    )

    def fail_manifest_check(
        _db: sqlite3.Connection,
        *,
        include_run_snapshots: bool,
    ) -> bool:
        del include_run_snapshots
        raise error

    monkeypatch.setattr(
        store,
        "_schema_manifest_is_current_in_connection",
        fail_manifest_check,
    )

    with pytest.raises(sqlite3.Error) as raised:
        getattr(store, schema_check_name)()

    assert raised.value is error
    assert capsys.readouterr().err.count(error_name) == 1


def test_schema_io_error_does_not_start_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "schema-io-error.sqlite3"
    AutoReplyStore(db_path)
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    error = _sqlite_failure(
        "disk I/O error",
        code=sqlite3.SQLITE_IOERR | (2 << 8),
        name="SQLITE_IOERR_SHORT_READ",
    )
    initialize_calls = 0

    def fail_schema_check(_self: AutoReplyStore) -> bool:
        raise error

    def count_initialize(_self: AutoReplyStore) -> None:
        nonlocal initialize_calls
        initialize_calls += 1

    monkeypatch.setattr(AutoReplyStore, "_schema_is_current", fail_schema_check)
    monkeypatch.setattr(AutoReplyStore, "_initialize", count_initialize)

    with pytest.raises(sqlite3.Error) as raised:
        AutoReplyStore(db_path)

    assert raised.value is error
    assert initialize_calls == 0


def test_schema_check_treats_missing_service_state_as_uninitialized(
    tmp_path: Path,
) -> None:
    store = _store_without_initialization(tmp_path / "uninitialized.sqlite3")

    assert store._schema_is_current() is False


def test_schema_manifest_treats_missing_column_as_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AutoReplyStore(tmp_path / "incomplete-schema.sqlite3")
    error = _sqlite_failure(
        "no such column: round_number",
        code=sqlite3.SQLITE_ERROR,
        name="SQLITE_ERROR",
    )

    def fail_manifest_check(
        _db: sqlite3.Connection,
        *,
        include_run_snapshots: bool,
    ) -> bool:
        del include_run_snapshots
        raise error

    monkeypatch.setattr(
        store,
        "_schema_manifest_is_current_in_connection",
        fail_manifest_check,
    )

    assert store._schema_manifest_is_current() is False


def test_partial_legacy_follow_up_source_requires_explicit_migration(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    db = sqlite3.connect(db_path)
    try:
        db.execute(
            """
            create table follow_up_drafts (
                id integer primary key autoincrement,
                project_id integer not null,
                todo_id integer not null default 0,
                owner_user_id text not null default '',
                owner_name text not null default '',
                target_conversation_id text not null default '',
                target_kind text not null default '',
                question_text text not null default '',
                risk_check_json text not null default '{}',
                status text not null default 'draft',
                send_result_json text not null default '{}',
                scheduled_at text not null default '',
                sent_at text not null default '',
                created_at text not null default current_timestamp
            )
            """
        )
        db.execute(
            """
            insert into follow_up_drafts (
                project_id, todo_id, owner_user_id, owner_name,
                target_conversation_id, target_kind, question_text,
                risk_check_json, status, send_result_json, scheduled_at, sent_at
            ) values (
                1, 1, 'owner-1', 'Alex',
                'cid-1', 'group', '请同步进展。',
                '{}', 'draft', '{}', '2026-06-26 09:00:00', ''
            )
            """
        )
        db.commit()
    finally:
        db.close()

    assert_store_reopen_rejected_without_mutation(db_path)


def test_store_writer_can_commit_while_reader_transaction_is_open(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    reader = sqlite3.connect(db_path)

    try:
        reader.execute("begin")
        reader.execute(f'select count(*) from ({ERROR_SQL})').fetchone()

        store.record_error("cid-1", "msg-1", "producer", "database is locked")
    finally:
        reader.rollback()
        reader.close()

    assert store.list_errors(limit=1)[0].kind == "producer"


def test_conversation_session_persists(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation(
        conversation_id="cid-1",
        title="Friday",
        single_chat=False,
        codex_session_id="session-1",
    )

    loaded = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert loaded.get_codex_session_id("cid-1") == "session-1"


def test_codex_session_lock_is_exclusive(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "okr:1") is True
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False

    store.release_codex_session_lock("cid-1", "okr:1")
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True


def test_codex_session_lock_retries_resource_deadlock(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    original_connect = store._connect
    attempts = 0

    @contextmanager
    def flaky_connect():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError(errno.EDEADLK, "Resource deadlock avoided")
        with original_connect() as db:
            yield db

    monkeypatch.setattr(store, "_connect", flaky_connect)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True
    assert attempts == 3


def test_retry_failed_pre_agent_reply_task_requires_no_run_or_sent_reply(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = store.ensure_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-12 10:00:00",
        trigger_sender="Alex",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    store.fail_reply_task(
        task.id,
        "pre_agent_lock_failure",
        expected_execution_generation=claimed.execution_generation,
    )

    recovered = store.retry_failed_pre_agent_reply_task(
        task.id,
        reason="operator_retry_after_lock_recovery",
    )

    assert recovered.status == "pending"
    assert recovered.attempts == 0
    assert recovered.error == "operator_retry_after_lock_recovery"

    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    store.fail_reply_task(
        task.id,
        "second_failure",
        expected_execution_generation=claimed.execution_generation,
    )
    run = store.claim_agent_run(
        task.id,
        claimed.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="test-run",
    ).run

    with pytest.raises(ValueError, match="already has an agent run"):
        store.retry_failed_pre_agent_reply_task(
            task.id,
            reason="must_not_retry",
        )
    assert run.id > 0


def test_codex_session_lock_replaces_stale_lock(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "okr:1") is True
    with store._immediate_write_transaction() as db:
        locked_at = db.execute("select datetime('now', '-21 minutes')").fetchone()[0]
        update_state_rows(db, 'codex_session_locks', {'locked_at': locked_at},
                          where_sql="conversation_id=?", parameters=('cid-1',))

    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True
    with store._immediate_write_transaction() as db:
        rows = db.execute(
            f"select owner from ({state_projection('codex_session_locks')}) where conversation_id='cid-1'"
        ).fetchall()
    assert [row["owner"] for row in rows] == ["reply:msg-1"]


def test_codex_session_lock_release_requires_owner(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "okr:1") is True
    assert store.release_codex_session_lock("cid-1", "other") is False
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False
    assert store.release_codex_session_lock("cid-1", "okr:1") is True


def test_codex_session_lock_renewal_requires_current_owner(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.acquire_codex_session_lock("cid-1", "consumer:1") is True
    with store._immediate_write_transaction() as db:
        locked_at = db.execute("select datetime('now', '-19 minutes')").fetchone()[0]
        update_state_rows(db, 'codex_session_locks', {'locked_at': locked_at},
                          where_sql="conversation_id=?", parameters=('cid-1',))

    assert store.renew_codex_session_lock("cid-1", "other") is False
    assert store.renew_codex_session_lock("cid-1", "consumer:1") is True
    assert store.acquire_codex_session_lock("cid-1", "other") is False

    with store._immediate_write_transaction() as db:
        locked_at = db.execute("select datetime('now', '-21 minutes')").fetchone()[0]
        update_state_rows(db, 'codex_session_locks', {'locked_at': locked_at},
                          where_sql="conversation_id=?", parameters=('cid-1',))
    assert store.renew_codex_session_lock("cid-1", "consumer:1") is False


def test_codex_session_lock_context_manager_releases_without_swallowing(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with store.codex_session_lock("cid-1", "okr:1"):
        assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is False

    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1") is True


def test_reply_task_queue_dedupes_by_conversation_and_message(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    first_inserted = store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 看一下",
    )
    second_inserted = store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 看一下",
    )

    assert first_inserted is True
    assert second_inserted is False
    assert store.count_reply_tasks(status="pending") == 1


def test_retry_failed_effect_free_email_unsubscribe_tasks_requeues_only_safe_rows(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    action_identity = "email-action:unsubscribe-safe"
    payload = {
        "schema": "email_agent_action.v1",
        "action_type": "unsubscribe",
        "lifecycle_version": "email_unsubscribe_audited_v2",
        "action_identity": action_identity,
    }
    assert store.enqueue_reply_task(
        conversation_id="email-thread:1",
        conversation_title="Email unsubscribe",
        single_chat=False,
        trigger_message_id=action_identity,
        trigger_create_time="2026-09-28 09:00:00",
        trigger_sender="sender@example.com",
        trigger_text="Immutable ActionPlan authorizes unsubscribe.",
        channel="email",
    )
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'failed', 'error': 'email_unsubscribe_browser_failed: category=operation_failed'},
            where_sql="and id in (select task_id from task_inputs where trigger_message_id=?)",
            parameters=(action_identity,),
        )

    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_task_inputs',
            {'trigger_message_json': json.dumps(payload)},
            where_sql="and trigger_message_id=?",
            parameters=(action_identity,),
        )

    assert store.retry_failed_effect_free_email_unsubscribe_tasks() == [1]
    task = store.list_reply_tasks(limit=1)[0]
    assert task.status == "pending"
    assert task.attempts == 0
    assert task.recovery_code == "effect_free_unsubscribe_retry"
    assert task.execution_generation != "initial"

    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'failed', 'error': 'email_unsubscribe_browser_timeout'},
            where_sql='and id=1',
        )
        insert_state(db, "service_state", {
            "key": f"email_unsubscribe_execution_pending:v1:{action_identity}",
            "value": json.dumps({"action_identity": action_identity}),
        })
    assert store.retry_failed_effect_free_email_unsubscribe_tasks() == []
    task = store.list_reply_tasks(limit=1)[0]
    assert task.status == "failed"
    assert task.error == "email_unsubscribe_browser_timeout"


def test_reply_task_execution_generation_defaults_and_survives_requeue(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claimed = store.list_reply_tasks(limit=1)[0]

    assert claimed.id == task_id
    assert claimed.execution_generation == "initial"

    store.requeue_reply_task(
        task_id,
        "retry",
        expected_execution_generation=claimed.execution_generation,
    )
    reclaimed = store.claim_reply_tasks(limit=1)[0]

    assert reclaimed.execution_generation == "initial"




def test_enqueue_reply_task_rejects_empty_execution_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(ValueError, match="execution_generation must be non-empty"):
        store.enqueue_reply_task(
            conversation_id="cid-1",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id="msg-1",
            trigger_create_time="2026-07-20 10:00:00",
            trigger_sender="Derek",
            trigger_text="Handle this task",
            execution_generation="   ",
        )








def test_enqueue_manual_rerun_reply_task_requeues_existing_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 看一下",
        trigger_message_json='{"open_message_id":"msg-1","content":"old"}',
    )
    task = store.claim_reply_tasks(limit=1)[0]
    store.fail_reply_task(
        task.id,
        "old failure",
        expected_execution_generation=task.execution_generation,
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 看一下",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )

    rerun = store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:01:00",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 重新看",
        trigger_message_json='{"open_message_id":"msg-1","content":"new"}',
        oa_url="https://oa.example/process",
        attempt_id=attempt_id,
    )

    assert rerun.id == task.id
    assert rerun.status == "pending"
    assert rerun.attempts == 0
    assert rerun.locked_at is None
    assert rerun.force_new_decision is True
    assert rerun.oa_url == "https://oa.example/process"
    assert rerun.manual_rerun_attempt_id == attempt_id
    assert rerun.error == f"manual_rerun_from_attempt:{attempt_id}"
    assert rerun.trigger_text == "@Alex Chen 重新看"
    claimed = store.claim_reply_tasks(limit=1)
    assert [claimed_task.id for claimed_task in claimed] == [task.id]


def test_sent_wechat_rerun_requires_exact_explicit_resend_at_store_boundary(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    source = dict(
        channel="wechat", conversation_id="melody115", conversation_title="Melody",
        single_chat=True, trigger_message_id="wx-1",
        trigger_create_time="2026-09-26T14:12:52-07:00",
        trigger_sender="Melody", trigger_text="他说 memory 不仅是 data",
        trigger_message_json='{"message_id":"wx-1","text":"他说 memory 不仅是 data"}',
    )
    store.enqueue_reply_task(**source)
    attempt_id = store.record_reply_attempt(
        channel="wechat", conversation_id=source["conversation_id"],
        conversation_title="Melody", trigger_message_id=source["trigger_message_id"],
        trigger_sender="Melody", trigger_text=source["trigger_text"],
        action="send_reply", sensitivity_kind="normal", send_status="sent",
    )
    original = store.get_reply_task_for_message("melody115", "wx-1", channel="wechat")
    assert original is not None

    with pytest.raises(store_module.ManualRerunConflict, match="explicit resend"):
        store.enqueue_manual_rerun_reply_task(**source, attempt_id=attempt_id)
    with pytest.raises(store_module.ManualRerunConflict, match="explicit resend"):
        store.enqueue_manual_rerun_reply_task(
            **source, attempt_id=attempt_id, explicit_resend_message_id="wx-other",
        )
    assert store.get_reply_task(original.id).execution_generation == original.execution_generation

    resent = store.enqueue_manual_rerun_reply_task(
        **source, attempt_id=attempt_id, explicit_resend_message_id="wx-1",
    )
    assert resent.execution_generation != original.execution_generation
    assert resent.manual_rerun_attempt_id == attempt_id
    repeated_pending = store.enqueue_manual_rerun_reply_task(
        **source, attempt_id=attempt_id, explicit_resend_message_id="wx-1",
    )
    assert repeated_pending.execution_generation == resent.execution_generation
    with store._connect() as db:
        _update_current_task8(
            db, "reply_tasks", {"status": "done"}, where_sql="and id=?", parameters=(resent.id,),
        )
    with pytest.raises(store_module.ManualRerunConflict, match="already resent"):
        store.enqueue_manual_rerun_reply_task(
            **source, attempt_id=attempt_id, explicit_resend_message_id="wx-1",
        )
    store.record_reply_attempt(
        channel="wechat", conversation_id=source["conversation_id"],
        conversation_title="Melody", trigger_message_id=source["trigger_message_id"],
        trigger_sender="Melody", trigger_text=source["trigger_text"],
        action="send_reply", sensitivity_kind="normal", send_status="sent",
    )
    with pytest.raises(store_module.ManualRerunConflict, match="newer WeChat Attempt"):
        store.enqueue_manual_rerun_reply_task(
            **source, attempt_id=attempt_id, explicit_resend_message_id="wx-1",
        )


def test_manual_rerun_dedupes_same_pending_source_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    _enqueue_universal_reply_task(store)
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-universal",
        conversation_title="Universal",
        trigger_message_id="msg-universal",
        trigger_sender="Derek",
        trigger_text="Handle this task",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )

    rerun_args = {
        "conversation_id": "cid-universal",
        "conversation_title": "Universal",
        "single_chat": False,
        "trigger_message_id": "msg-universal",
        "trigger_create_time": "2026-07-20 10:01:00",
        "trigger_sender": "Derek",
        "trigger_text": "Run it again",
        "trigger_message_json": "{}",
        "attempt_id": attempt_id,
    }
    first = store.enqueue_manual_rerun_reply_task(**rerun_args)
    second = store.enqueue_manual_rerun_reply_task(**rerun_args)

    assert first.execution_generation
    assert second.execution_generation
    assert first.execution_generation != "initial"
    assert second.execution_generation != "initial"
    assert first.execution_generation == second.execution_generation
    assert first.id == second.id


def test_forced_manual_rerun_rotates_failed_pending_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original = store.get_reply_task(task_id)
    assert original is not None
    run = _claim_audit_run(store,
        task_id,
        original.execution_generation,
        owner="worker-1",
    ).run
    store.fail_agent_run(
        run.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="worker-1",
    )
    store.defer_reply_task(
        task_id,
        "codex_process_failed",
        expected_execution_generation=original.execution_generation,
    )

    rerun = store.enqueue_manual_rerun_reply_task(
        conversation_id="cid-universal",
        conversation_title="Universal",
        single_chat=False,
        trigger_message_id="msg-universal",
        trigger_create_time="2026-07-20 10:01:00",
        trigger_sender="Derek",
        trigger_text="Run it again",
        trigger_message_json="{}",
        force_rotation=True,
    )

    assert rerun.id == task_id
    assert rerun.execution_generation != original.execution_generation
    assert rerun.status == "pending"


def test_authorization_defer_preserves_claim_attempt_count(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original = store.get_reply_task(task_id)
    assert original is not None
    assert original.status == "processing" and original.attempts == 1

    store.defer_reply_task_for_authorization(
        task_id,
        "dws_forbidden_accessDenied",
        expected_execution_generation=original.execution_generation,
        available_at="2026-08-13 16:00:00",
    )

    deferred = store.get_reply_task(task_id)
    assert deferred is not None
    assert deferred.status == "pending"
    assert deferred.attempts == 1


def test_forced_manual_rerun_does_not_supersede_active_agent_run(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    original = store.get_reply_task(task_id)
    assert original is not None
    _claim_audit_run(store,
        task_id,
        original.execution_generation,
        owner="worker-1",
    )

    with pytest.raises(ValueError, match="active agent run must finish"):
        store.enqueue_manual_rerun_reply_task(
            conversation_id="cid-universal",
            conversation_title="Universal",
            single_chat=False,
            trigger_message_id="msg-universal",
            trigger_create_time="2026-07-20 10:01:00",
            trigger_sender="Derek",
            trigger_text="Run it again",
            trigger_message_json="{}",
            force_rotation=True,
        )

    unchanged = store.get_reply_task(task_id)
    assert unchanged is not None
    assert unchanged.execution_generation == original.execution_generation


def test_manual_rerun_dedupes_same_attempt_across_processes(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.enqueue_reply_task(
        conversation_id="cid-process-rerun",
        conversation_title="Process rerun",
        single_chat=False,
        trigger_message_id="msg-process-rerun",
        trigger_create_time="2026-07-29 10:59:00",
        trigger_sender="ET",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-process-rerun",
        conversation_title="Process rerun",
        trigger_message_id="msg-process-rerun",
        trigger_sender="ET",
        trigger_text="请处理",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    context = get_context("spawn")
    barrier = context.Barrier(8)
    results = context.Queue()
    processes = [
        context.Process(
            target=_enqueue_manual_rerun_in_process,
                args=(str(db_path), attempt_id, barrier, results),
        )
        for _ in range(8)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=2) for _ in processes]
    assert len({task_id for task_id, _ in outcomes}) == 1
    assert len({generation for _, generation in outcomes}) == 1


def test_manual_rerun_new_source_attempt_rotates_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    common = {
        "conversation_id": "cid-corrected-rerun",
        "conversation_title": "Corrected rerun",
        "single_chat": False,
        "trigger_message_id": "msg-corrected-rerun",
        "trigger_create_time": "2026-07-29 11:00:00",
        "trigger_sender": "ET",
        "trigger_text": "请重新处理",
        "trigger_message_json": "{}",
    }
    attempt_ids = [
        store.record_reply_attempt(
            conversation_id=common["conversation_id"],
            conversation_title=common["conversation_title"],
            trigger_message_id=common["trigger_message_id"],
            trigger_sender=common["trigger_sender"],
            trigger_text=common["trigger_text"],
            action="send_reply",
            sensitivity_kind="general",
            send_status="failed",
        )
        for _ in range(2)
    ]

    first = store.enqueue_manual_rerun_reply_task(
        **common, attempt_id=attempt_ids[0]
    )
    corrected = store.enqueue_manual_rerun_reply_task(
        **common, attempt_id=attempt_ids[1]
    )

    assert corrected.id == first.id
    assert corrected.execution_generation != first.execution_generation
    assert corrected.manual_rerun_attempt_id == attempt_ids[1]


def test_manual_rerun_changed_attempt_revision_rotates_processing_generation(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-revised-attempt",
        conversation_title="Revised attempt",
        trigger_message_id="msg-revised-attempt",
        trigger_sender="ET",
        trigger_text="请重新处理",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    common = {
        "conversation_id": "cid-revised-attempt",
        "conversation_title": "Revised attempt",
        "single_chat": False,
        "trigger_message_id": "msg-revised-attempt",
        "trigger_create_time": "2026-07-29 11:00:00",
        "trigger_sender": "ET",
        "trigger_text": "请重新处理",
        "trigger_message_json": "{}",
        "attempt_id": attempt_id,
    }

    first = store.enqueue_manual_rerun_reply_task(**common)
    claimed = store.claim_reply_tasks(limit=1)
    assert claimed[0].execution_generation == first.execution_generation
    assert store.record_reply_feedback(
        attempt_id,
        feedback="请根据审核意见重新处理",
        corrected_reply_text="这是修正版回复。",
    )

    revised = store.enqueue_manual_rerun_reply_task(**common)
    repeated = store.enqueue_manual_rerun_reply_task(**common)

    assert revised.execution_generation != first.execution_generation
    assert revised.status == "pending"
    assert revised.execution_generation == repeated.execution_generation












def test_reviewed_reply_and_rerun_roll_back_together(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-atomic-review",
        conversation_title="Review",
        single_chat=False,
        trigger_message_id="msg-atomic-review",
        trigger_create_time="2026-07-29 10:00:00",
        trigger_sender="ET",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    with store._connect() as db:
        db.executescript(
            """
            create trigger reject_review_rerun before update on tasks
            when new.kind='reply'
              and coalesce(json_extract(new.parameters_json,'$.manual_rerun_attempt_id'),0) > 0
            begin
                select raise(abort, 'forced review rerun failure');
            end;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced review rerun failure"):
        store.record_reviewed_reply_rerun(
            conversation_id="cid-atomic-review",
            conversation_title="Review",
            single_chat=False,
            trigger_message_id="msg-atomic-review",
            trigger_create_time="2026-07-29 10:00:00",
            trigger_sender="ET",
            trigger_text="请处理",
            trigger_message_json="{}",
            suggested_reply_text="建议内容",
            reviewer_feedback="审核意见",
        )

    attempts = store.list_reply_attempts(limit=20)
    task = store.get_reply_task_for_message(
        "cid-atomic-review", "msg-atomic-review"
    )
    assert attempts == []
    assert task is not None and task.manual_rerun_attempt_id == 0


def test_reviewed_reply_rerun_is_idempotent_across_concurrent_connections(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    store.enqueue_reply_task(
        conversation_id="cid-concurrent-review",
        conversation_title="Review",
        single_chat=False,
        trigger_message_id="msg-concurrent-review",
        trigger_create_time="2026-07-29 10:00:00",
        trigger_sender="ET",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    barrier = Barrier(12)
    results: Queue = Queue()

    def enqueue_review() -> None:
        thread_store = AutoReplyStore(db_path)
        try:
            barrier.wait(timeout=5)
            results.put(
                thread_store.record_reviewed_reply_rerun(
                    conversation_id="cid-concurrent-review",
                    conversation_title="Review",
                    single_chat=False,
                    trigger_message_id="msg-concurrent-review",
                    trigger_create_time="2026-07-29 10:00:00",
                    trigger_sender="ET",
                    trigger_text="请处理",
                    trigger_message_json="{}",
                    suggested_reply_text="建议内容",
                    reviewer_feedback="审核意见",
                )
            )
        except Exception as exc:  # pragma: no cover - surfaced below
            results.put(exc)

    threads = [Thread(target=enqueue_review) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    outcomes = [results.get_nowait() for _ in threads]
    errors = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert errors == []
    attempt_ids = {outcome[0] for outcome in outcomes}
    generations = {outcome[1].execution_generation for outcome in outcomes}
    assert len(attempt_ids) == 1
    assert len(generations) == 1

    matching_attempts = [
        attempt
        for attempt in store.list_reply_attempts(limit=20)
        if attempt.codex_reason == "reviewed_message_reply"
    ]
    assert [attempt.id for attempt in matching_attempts] == list(attempt_ids)


def test_reviewed_reply_rerun_allows_changed_feedback_to_rotate_generation(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    common = {
        "conversation_id": "cid-revised-review",
        "conversation_title": "Review",
        "single_chat": False,
        "trigger_message_id": "msg-revised-review",
        "trigger_create_time": "2026-07-29 10:00:00",
        "trigger_sender": "ET",
        "trigger_text": "请处理",
        "trigger_message_json": "{}",
        "suggested_reply_text": "建议内容",
    }

    first_attempt_id, first_task = store.record_reviewed_reply_rerun(
        **common,
        reviewer_feedback="审核意见",
    )
    revised_attempt_id, revised_task = store.record_reviewed_reply_rerun(
        **common,
        reviewer_feedback="补充后的审核意见",
    )

    assert revised_attempt_id != first_attempt_id
    assert revised_task.id == first_task.id
    assert revised_task.execution_generation != first_task.execution_generation


def test_reviewed_reply_rerun_keeps_review_metadata_without_process_events(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-history-preserve",
        conversation_title="Review",
        trigger_message_id="msg-history-preserve",
        trigger_sender="ET",
        trigger_text="请处理",
        action="agent_run",
        sensitivity_kind="general",
        audit_tool_events_json='[{"tool":"original_read","result":"ok"}]',
        send_status="failed",
    )

    store.record_reviewed_reply_rerun(
        conversation_id="cid-history-preserve",
        conversation_title="Review",
        single_chat=False,
        trigger_message_id="msg-history-preserve",
        trigger_create_time="2026-07-29 10:00:00",
        trigger_sender="ET",
        trigger_text="请处理",
        trigger_message_json="{}",
        suggested_reply_text="建议内容",
        reviewer_feedback="审核意见",
        source_attempt_id=attempt_id,
    )

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert json.loads(attempt.audit_tool_events_json) == [
        {"tool": "audit_review", "result": "queued"},
    ]


def test_actionable_attempt_decision_resolves_source_and_requeues_same_task(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-actionable-decision",
        conversation_title="HR",
        single_chat=False,
        trigger_message_id="msg-actionable-decision",
        trigger_create_time="2026-08-11 05:00:00",
        trigger_sender="Avery",
        trigger_text="Please decide.",
        trigger_message_json="{}",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    store.fail_reply_task(
        task.id,
        "decision required",
        expected_execution_generation=task.execution_generation,
    )
    source_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="A manager decision is required.",
        send_status="failed",
    )
    feedback = f"Human decision for source attempt #{source_id}: 暂不处理"

    selected_id, requeued = store.record_actionable_attempt_decision(
        source_id,
        reviewer_feedback=feedback,
        conversation_title=task.conversation_title,
        single_chat=task.single_chat,
        trigger_create_time=task.trigger_create_time,
        trigger_message_json=task.trigger_message_json,
    )
    repeated_id, repeated_task = store.record_actionable_attempt_decision(
        source_id,
        reviewer_feedback=feedback,
        conversation_title=task.conversation_title,
        single_chat=task.single_chat,
        trigger_create_time=task.trigger_create_time,
        trigger_message_json=task.trigger_message_json,
    )

    source = store.get_reply_attempt(source_id)
    selected = store.get_reply_attempt(selected_id)
    assert requeued.id == task.id
    assert requeued.status == "pending"
    assert source is not None and source.send_status == "pending"
    assert source.reviewer_feedback == feedback
    assert selected_id == source_id
    assert selected is not None and selected.id == source_id
    assert repeated_id == source_id
    assert repeated_task.execution_generation == requeued.execution_generation


def test_actionable_attempt_decision_rolls_back_queue_when_source_update_fails(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-actionable-rollback",
        conversation_title="HR",
        single_chat=False,
        trigger_message_id="msg-actionable-rollback",
        trigger_create_time="2026-08-11 05:00:00",
        trigger_sender="Avery",
        trigger_text="Please decide.",
        trigger_message_json="{}",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    store.fail_reply_task(
        task.id,
        "decision required",
        expected_execution_generation=task.execution_generation,
    )
    source_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
    )
    with store._connect() as db:
        db.executescript(
            f"""
            create trigger reject_actionable_resolution before update on actions
                when old.record_type='reply_attempts'
                and old.legacy_key='{source_id}'
                and new.attempt_send_status='pending'
            begin
                select raise(abort, 'forced actionable resolution failure');
            end;
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="forced actionable resolution failure",
    ):
        store.record_actionable_attempt_decision(
            source_id,
            reviewer_feedback="Human decision: 暂不处理",
            conversation_title=task.conversation_title,
            single_chat=task.single_chat,
            trigger_create_time=task.trigger_create_time,
            trigger_message_json=task.trigger_message_json,
        )

    source = store.get_reply_attempt(source_id)
    unchanged_task = store.get_reply_task(task.id)
    assert source is not None and source.send_status == "failed"
    assert unchanged_task is not None and unchanged_task.status == "failed"
    assert unchanged_task.execution_generation == task.execution_generation
    assert store.count_reply_attempts() == 1


def test_agent_run_is_unique_per_task_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)

    first = _claim_audit_run(store, task_id, "initial", owner="worker-1")
    second = _claim_audit_run(store, task_id, "initial", owner="worker-2")

    assert first.claimed is True
    assert second.claimed is False
    assert second.run.id == first.run.id
    assert second.run.lease_owner == "worker-1"






def test_agent_run_concurrent_claims_choose_exactly_one_owner(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    first_store = AutoReplyStore(db_path)
    second_store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(first_store)
    barrier = Barrier(2)
    results: Queue = Queue()

    def claim(store: AutoReplyStore, owner: str) -> None:
        try:
            barrier.wait(timeout=5)
            results.put((_claim_audit_run(store, task_id, "initial", owner=owner), None))
        except BaseException as exc:
            results.put((None, exc))

    threads = [
        Thread(target=claim, args=(first_store, "worker-1")),
        Thread(target=claim, args=(second_store, "worker-2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    outcomes = [results.get_nowait(), results.get_nowait()]
    errors = [error for _, error in outcomes if error is not None]
    assert errors == []
    claims = [claim for claim, _ in outcomes]
    assert sum(claim.claimed for claim in claims) == 1
    assert len({claim.run.id for claim in claims}) == 1
    winner = next(claim for claim in claims if claim.claimed)
    loser = next(claim for claim in claims if not claim.claimed)
    assert loser.run.lease_owner == winner.run.lease_owner


def test_agent_run_fresh_lease_cannot_be_stolen_but_expired_lease_recovers(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        lease_seconds=1800,
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-1",
        transcript_start_line=8,
        now="2026-07-29 00:01:00",
    )

    fresh = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-2",
        now="2026-07-29 00:29:59",
    )
    expired = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-2",
        now="2026-07-29 00:30:01",
    )

    assert fresh.claimed is False
    assert expired.claimed is True
    assert expired.run.id == first.run.id
    assert expired.run.codex_session_id == "session-1"
    assert expired.run.transcript_start_line == 8
    assert expired.run.lease_owner == "worker-2"


def test_expired_sessionless_no_effect_agent_run_can_be_reclaimed(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        lease_seconds=1800,
        now="2026-07-29 00:00:00",
    )

    expired = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-2",
        now="2026-07-29 00:30:01",
    )

    assert expired.claimed is True
    assert expired.run.id == first.run.id
    assert expired.run.codex_session_id == ""
    assert expired.run.lease_owner == "worker-2"
    assert expired.run.lease_expires_at > first.run.lease_expires_at


def test_agent_run_claim_rejects_generation_not_owned_by_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)

    with pytest.raises(ValueError, match="execution generation mismatch"):
        _claim_audit_run(store, task_id, "other-generation", owner="worker-1")


def test_agent_run_lease_renewal_requires_current_owner(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claim = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 00:00:00",
    )

    renewed = store.renew_agent_run_lease(
        claim.run.id,
        owner="worker-1",
        lease_seconds=900,
        now="2026-07-29 00:10:00",
    )

    assert renewed.lease_expires_at == "2026-07-29 00:25:00"
    with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
        store.renew_agent_run_lease(
            claim.run.id,
            owner="worker-2",
            now="2026-07-29 00:11:00",
        )


def test_agent_run_lease_renewal_treats_terminal_run_as_lost(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claim = _claim_audit_run(
        store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 00:00:00",
    )
    store.fail_agent_run(
        claim.run.id,
        {"code": "stale_agent_turn_recovery"},
        owner="worker-1",
        now="2026-07-29 00:01:00",
    )

    with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
        store.renew_agent_run_lease(
            claim.run.id,
            owner="worker-1",
            now="2026-07-29 00:02:00",
        )


def test_agent_run_lease_renewal_retries_transient_database_lock(
    tmp_path: Path, monkeypatch
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claim = _claim_audit_run(
        store,
        task_id,
        "initial",
        owner="worker-1",
        now="2026-07-29 00:00:00",
    )
    original_connect = store._connect
    attempts = 0

    @contextmanager
    def flaky_connect():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("database is locked")
        with original_connect() as db:
            yield db

    monkeypatch.setattr(store, "_connect", flaky_connect)
    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)

    renewed = store.renew_agent_run_lease(
        claim.run.id,
        owner="worker-1",
        lease_seconds=900,
        now="2026-07-29 00:10:00",
    )

    assert attempts == 2
    assert renewed.lease_expires_at == "2026-07-29 00:25:00"


def test_reclaimed_agent_run_rejects_every_stale_owner_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    from app import native_trajectory

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-a",
        now="2026-07-29 00:01:00",
    )
    reclaimed = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-b",
        now="2026-07-29 00:30:01",
    )
    assert reclaimed.claimed is True
    before = store.get_agent_run(first.run.id)
    assert before is not None

    stale_mutations = [
        lambda: store.set_agent_run_session(
            first.run.id,
            "session-1",
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.append_agent_run_event(
            first.run.id,
            {"type": "item.started", "call_id": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.complete_agent_run(
            first.run.id,
            {"outcome": "completed", "summary": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.fail_agent_run(
            first.run.id,
            {"code": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.fail_agent_run(
            first.run.id,
            {"code": "stale"},
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
        lambda: store.renew_agent_run_lease(
            first.run.id,
            owner="worker-a",
            now="2026-07-29 00:30:02",
        ),
    ]
    for mutate in stale_mutations:
        with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
            mutate()
        assert store.get_agent_run(first.run.id) == before

    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-b",
        now="2026-07-29 00:30:02",
    )
    owned_event = {
        "type": "item.completed",
        "item": {
            "type": "command_execution", "id": "owned", "command": "echo owned",
            "exit_code": 0, "status": "completed", "aggregated_output": "owned",
        },
    }
    store.append_agent_run_event(
        first.run.id,
        owned_event,
        owner="worker-b",
        now="2026-07-29 00:30:02",
    )
    native_path = tmp_path / "session-1.jsonl"
    native_path.write_text("\n".join(json.dumps(record) for record in (
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "event_msg", "payload": {
            "type": "item_completed",
            "item": {**owned_event["item"], "type": "CommandExecution"},
        }},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    )) + "\n")
    monkeypatch.setattr(
        native_trajectory, "find_codex_session_path",
        lambda session_id, **kwargs: native_path if session_id == "session-1" else None,
    )
    renewed = store.renew_agent_run_lease(
        first.run.id,
        owner="worker-b",
        now="2026-07-29 00:31:00",
    )
    assert store.get_agent_run(first.run.id).tool_events == [owned_event]
    completed = store.complete_agent_run(
        first.run.id,
        {"outcome": "completed", "summary": "owned"},
        owner="worker-b",
        transcript_end_line=3,
        now="2026-07-29 00:31:01",
    )

    assert renewed.lease_owner == "worker-b"
    assert completed.status == "completed"
    assert completed.tool_events == [owned_event]
    assert completed.codex_session_id == "session-1"
    assert completed.transcript_start_line == 0
    assert completed.transcript_end_line == 3


def test_expired_lease_blocks_writes_until_session_recovery(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    first = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-a",
        now="2026-07-29 00:00:00",
    )
    store.set_agent_run_session(
        first.run.id,
        "session-1",
        owner="worker-a",
        now="2026-07-29 00:01:00",
    )

    before = store.get_agent_run(first.run.id)
    assert before is not None
    expired_mutations = [
        lambda: store.set_agent_run_session(
            first.run.id,
            "session-1",
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.append_agent_run_event(
            first.run.id,
            {"type": "item.started", "call_id": "blocked"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.complete_agent_run(
            first.run.id,
            {"outcome": "completed", "summary": "expired"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.fail_agent_run(
            first.run.id,
            {"code": "expired"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.fail_agent_run(
            first.run.id,
            {"code": "expired"},
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
        lambda: store.renew_agent_run_lease(
            first.run.id,
            owner="worker-a",
            now="2026-07-29 00:30:01",
        ),
    ]
    for mutate in expired_mutations:
        with pytest.raises(AgentRunLeaseLostError, match="agent run lease lost"):
            mutate()
        assert store.get_agent_run(first.run.id) == before

    recovered = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-b",
        now="2026-07-29 00:30:02",
    )
    appended = store.append_agent_run_event(
        first.run.id,
        {"type": "item.started", "call_id": "recovered"},
        owner="worker-b",
        now="2026-07-29 00:30:03",
    )

    assert recovered.claimed is True
    assert appended.tool_events == []
    persisted = store.get_agent_run(first.run.id)
    assert persisted is not None
    assert [event["call_id"] for event in persisted.tool_events] == ["recovered"]






def test_running_agent_events_are_visible_incrementally(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    started = {
        "type": "item.started",
        "call_id": "c1",
        "effect": {"kind": "write", "provider": "dws"},
    }
    completed = {
        "type": "item.completed",
        "call_id": "c1",
        "receipt": {"accepted": True},
    }

    store.append_agent_run_event(run.id, started, owner="worker-1")
    store.append_agent_run_event(run.id, completed, owner="worker-1")

    loaded = store.get_agent_run(run.id)
    assert loaded is not None
    assert loaded.tool_events == [started, completed]
    assert loaded.transcript_end_line == 2






def test_agent_run_events_are_not_copied_to_sqlite(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    first = {"type": "item.started", "call_id": "c1"}
    second = {"type": "item.completed", "call_id": "c1"}

    store.append_agent_run_event(run.id, first, owner="worker-1")
    store.append_agent_run_event(run.id, second, owner="worker-1")

    assert store.get_agent_run(run.id).tool_events == [first, second]
    with sqlite3.connect(store.path) as db:
        assert_no_core_native_storage(db)



def test_append_agent_events_does_not_reparse_prior_json(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    original_loads = store_module.json.loads
    calls = 0

    def counted_loads(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_loads(*args, **kwargs)

    monkeypatch.setattr(store_module.json, "loads", counted_loads)
    appended = None
    for index in range(50):
        appended = store.append_agent_run_event(
            run.id,
            {
                "type": "item.completed",
                "item": {
                    "id": f"read-{index}",
                    "metadata": {"effect": "read_only"},
                },
            },
            owner="worker-1",
        )

    assert appended is not None and appended.tool_events == []
    assert calls <= 300


def test_current_source_with_retired_native_event_body_is_rejected_without_mutation(tmp_path: Path):
    path = tmp_path / "nonempty-retired-events.sqlite3"
    AutoReplyStore(path)
    inventory = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-11-database-redesign-phase4-inventory.json").read_text())
    with sqlite3.connect(path) as db:
        db.execute(inventory["tables"]["agent_run_events"]["sql"])
        db.execute("insert into agent_run_events(agent_run_id,sequence,event_json) values(1,1,?)",
                   (json.dumps({"type": "item.started", "call_id": "legacy-1"}),))
    assert_store_reopen_rejected_without_mutation(path)







def _effect_intent_authorization(
    authorization_id: str,
    *,
    action_index: int = 0,
    external_action_key: str | None = None,
) -> dict[str, object]:
    return {
        "authorization_id": authorization_id,
        "external_action_key": external_action_key or f"external-{authorization_id}",
        "business_object_key": "message:dingtalk:cid-1:msg-1",
        "action_identity": "send-result",
        "action_index": action_index,
        "receipt_operation_id": f"operation-action-{action_index}",
        "capability": "agent_cli.dws",
        "operation": "chat +dm",
        "operation_digest": "operation-digest",
        "arguments_digest": "arguments-digest",
        "target_identifiers": {"to": "recipient"},
    }


def test_message_delivery_history_is_single_projection_with_run_observers(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_task_id = _enqueue_universal_reply_task(store)
    first_run = _claim_audit_run(
        store, first_task_id, "initial", owner="worker-1"
    ).run
    first = historical_sent_delivery(store,
        agent_run_id=first_run.id,
        external_action_key="stable-message-action",
        conversation_id="cid-1",
        trigger_message_id="msg-1",
        reply_text="已审批并通知。",
        provider_result={"messageId": "sent-once"},
    )
    store.enqueue_reply_task(
        conversation_id="cid-2",
        conversation_title="Second trigger",
        single_chat=False,
        trigger_message_id="msg-2",
        trigger_create_time="2026-08-21 00:01:00",
        trigger_sender="Derek",
        trigger_text="retry",
    )
    second_task = store.claim_reply_tasks(limit=1)[0]
    second_run = _claim_audit_run(
        store, second_task.id, second_task.execution_generation,
        owner="worker-2",
    ).run
    repeated = historical_sent_delivery(store,
        agent_run_id=second_run.id,
        external_action_key="stable-message-action",
        conversation_id="cid-2",
        trigger_message_id="msg-2",
        reply_text="已审批并通知。",
        provider_result={"messageId": "sent-once", "reused": True},
    )

    assert repeated.id == first.id
    with store._connect() as db:
        assert db.execute(f"select count(*) from ({SENT_REPLY_SQL})").fetchone()[0] == 1
        assert db.execute(
            f"select count(*) from ({SENT_REPLY_OBSERVER_SQL}) where sent_reply_id=?",
            (first.id,),
        ).fetchone()[0] == 2


def test_completed_message_delivery_persists_action_result_and_history_atomically(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    audit = _claim_audit_run(
        store, task_id, task.execution_generation, owner="worker-1"
    ).run
    store.complete_agent_run(
        audit.id,
        {"outcome": "executed"},
        owner="worker-1",
    )

    sent = store.record_completed_agent_message_delivery(
        agent_run_id=audit.id,
        external_action_key="external-send-result",
        business_object_key=task.business_object_key,
        action_identity="send-result",
        operation="chat +send-to-group",
        target_identifiers={"group": "cid-1"},
        conversation_id=task.conversation_id,
        trigger_message_id=task.trigger_message_id,
        reply_text="已发送。",
        provider_result={"message_id": "message-1"},
    )

    assert sent.external_action_key == "external-send-result"
    with store._connect() as db:
        assert db.execute(
            f"select count(*) from ({EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?",
            ("external-send-result",),
        ).fetchone()[0] == 1
        assert db.execute(
            f"select count(*) from ({SENT_REPLY_OBSERVER_SQL}) where agent_run_id=?",
            (audit.id,),
        ).fetchone()[0] == 1


@pytest.mark.parametrize("payload_field", ["content", "text"])
@pytest.mark.parametrize(
    ("reply_text", "sent_text", "readback_text", "receipt_message_id"),
    [
        ("Delivered text.", "Delivered text.", "Delivered text.", False),
        (
            "Delivered text.\n\n反馈：[有帮助](https://example.com/api/"
            "dingtalk-feedback-spike?q=%E6%AD%A3)",
            "Delivered text.\n\n反馈：[有帮助](https://example.com/api/"
            "dingtalk-feedback-spike?q=%E6%损坏)",
            "Delivered text.\n\n反馈：[有帮助](https://example.com/api/"
            "dingtalk-feedback-spike?q=%E6%损坏)",
            False,
        ),
        (
            "Delivered 12:00–12:30.",
            "Delivered 12:00–12:30.",
            "Delivered 12:00-12:30.",
            True,
        ),
    ],
)
def test_reconcile_failed_agent_message_requires_send_receipt_and_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload_field: str,
    reply_text: str,
    sent_text: str,
    readback_text: str,
    receipt_message_id: bool,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    native_paths: dict[str, Path] = {}
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer",
    ).run
    store.complete_agent_run(
        consumer.id,
        {
            "outcome": "proposal",
            "proposal": {
                "actions": [
                    {
                        "action_identity": "reply",
                        "operation": "send_group_message",
                        "target": {"conversation_id": task.conversation_id},
                        "payload": {payload_field: reply_text},
                    }
                ]
            },
        },
        owner="consumer",
    )
    send_run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id="audit-send",
        owner="audit",
    ).run
    store.append_agent_run_event(
        send_run.id,
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": (
                    "/bin/zsh -lc \"dws chat +send-to-group --group cid-1 "
                    f"--content '{sent_text}' --yes --format json\""
                ),
                "exit_code": 0,
                "status": "completed",
                "aggregated_output": json.dumps(
                    {
                        "success": True,
                        "result": {
                            "openTaskId": "receipt-1",
                            **(
                                {"openMessageId": "message-1"}
                                if receipt_message_id
                                else {}
                            ),
                        },
                    }
                ),
            },
        },
        owner="audit",
    )
    if sent_text != reply_text:
        alternate = sent_text.replace("%损坏", "%另一损坏")
        store.append_agent_run_event(
            send_run.id,
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": (
                        "/bin/zsh -lc \"dws chat +send-to-group --group cid-1 "
                        f"--content '{alternate}' --yes --format json\""
                    ),
                    "exit_code": 0,
                    "status": "completed",
                    "aggregated_output": json.dumps(
                        {"success": True, "result": {"openTaskId": "receipt-2"}}
                    ),
                },
            },
            owner="audit",
        )
    _write_native_run_events(store, send_run.id, tmp_path, monkeypatch, native_paths)
    store.fail_agent_run(send_run.id, {"code": "result_invalid"}, owner="audit")
    readback_run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=1,
        parent_agent_run_id=consumer.id,
        operation_id="audit-readback",
        owner="audit-readback",
    ).run
    store.append_agent_run_event(
        readback_run.id,
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "dws chat +chat-messages --group cid-1 --format json",
                "exit_code": 0,
                "status": "completed",
                "aggregated_output": json.dumps(
                    {
                        "messages": [
                            {
                                "messageId": "message-1",
                                "text": (
                                    readback_text
                                    if receipt_message_id
                                    else "@Riley  "
                                    + readback_text.replace("\n\n", "  \n")
                                ),
                            }
                        ]
                    }
                ),
            },
        },
        owner="audit-readback",
    )
    store.append_agent_run_event(
        readback_run.id,
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": json.dumps(
                    {
                        "outcome": "executed",
                        "external_result": {
                            "live_result_reference": {
                                "external_action_key": "external-1",
                                "message_id": "message-1",
                            }
                        },
                    }
                ),
            },
        },
        owner="audit-readback",
    )
    _write_native_run_events(store, readback_run.id, tmp_path, monkeypatch, native_paths)
    store.fail_agent_run(
        readback_run.id, {"code": "provider_receipt_missing"}, owner="audit-readback"
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
        channel=task.channel,
        oa_process_instance_id="process-authorization",
        oa_task_id="task-authorization",
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id,
                      {"agent_run_id": readback_run.id})
    store.fail_reply_task(
        task.id,
        "provider_receipt_missing",
        expected_execution_generation=task.execution_generation,
    )

    sent = store.reconcile_failed_agent_message_delivery(
        send_run_id=send_run.id,
        readback_run_id=readback_run.id,
        external_action_key="external-1",
        readback_message_id="message-1",
    )

    expected_text = readback_text if receipt_message_id else sent_text
    assert sent.reply_text == expected_text
    assert store.get_reply_task(task.id).status == "done"
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "completed"
    assert attempt.send_error == ""
    with store._connect() as db:
        assert db.execute(
            f"select count(*) from ({EXTERNAL_ACTION_RESULT_SQL}) where external_action_key='external-1'"
        ).fetchone()[0] == 1


def test_reconcile_failed_agent_message_accepts_send_and_readback_in_same_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    native_paths: dict[str, Path] = {}
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer",
    ).run
    store.complete_agent_run(
        consumer.id,
        {
            "outcome": "proposal",
            "proposal": {
                "actions": [
                    {
                        "action_identity": "reply",
                        "operation": "send_direct_message",
                        "target": {"open_dingtalk_id": "open-recipient"},
                        "payload": {"content": "Delivered text."},
                    }
                ]
            },
        },
        owner="consumer",
    )
    audit = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=consumer.id,
        operation_id="audit-send",
        owner="audit",
    ).run
    action_key = "same-run-external-key"
    store.append_agent_run_event(
        audit.id,
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": (
                    "/bin/zsh -lc \"dws chat +messages-send --open-dingtalk-id "
                    "open-recipient --content 'Delivered text.' --uuid "
                    f"{action_key} --yes --format json\""
                ),
                "exit_code": 0,
                "status": "completed",
                "aggregated_output": json.dumps(
                    {"success": True, "result": {"openTaskId": "receipt-1"}}
                ),
            },
        },
        owner="audit",
    )
    store.append_agent_run_event(
        audit.id,
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "dws chat +messages-mget --message-ids message-1",
                "exit_code": 0,
                "status": "completed",
                "aggregated_output": json.dumps(
                    {
                        "external_action_key": action_key,
                        "messages": [
                            {"messageId": "message-1", "text": "Delivered text."}
                        ],
                    }
                ),
            },
        },
        owner="audit",
    )
    _write_native_run_events(store, audit.id, tmp_path, monkeypatch, native_paths)
    store.fail_agent_run(audit.id, {"code": "result_invalid"}, owner="audit")
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
        channel=task.channel,
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id, {"agent_run_id": audit.id})
    store.fail_reply_task(
        task.id,
        "result_invalid",
        expected_execution_generation=task.execution_generation,
    )

    sent = store.reconcile_failed_agent_message_delivery(
        send_run_id=audit.id,
        readback_run_id=audit.id,
        external_action_key=action_key,
        readback_message_id="message-1",
    )

    assert sent.reply_text == "Delivered text."
    assert store.get_reply_task(task.id).status == "done"
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "completed"


def test_reconcile_preserves_legacy_authorization_projection(tmp_path: Path, monkeypatch):
    """An unreviewed old choice remains recorded but is not made actionable."""
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="needs_human",
        channel=task.channel,
    )
    result = {
        "outcome": "needs_human",
        "risk": "high",
        "confidence": 0.2,
        "rule_coverage": 1.0,
        "information_completeness": 1.0,
        "decision_options": [
            {
                "key": "retry",
                "label": "retry",
                "instruction": "retry this action",
                "consequence": "a provider action may happen",
            },
            {
                "key": "stop",
                "label": "stop",
                "instruction": "do not retry",
                "consequence": "the task remains incomplete",
            },
        ],
        "error_code": "external_action_authorization_required",
        "error_retryable": False,
        "error_authorization_required": True,
    }
    with store._connect() as db:
        run = insert_native_run_fixture(
            db, {
                'task_id': task.id,
                'execution_generation': task.execution_generation,
                'role': 'consumer',
                'status': 'completed',
            },
                    result=json.dumps(result), tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        update_action(db, "reply_attempts", attempt_id,
                      {"agent_run_id": run.lastrowid})
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'needs_human'},
            where_sql='and id=?',
            parameters=(task.id,),
        )

    assert store.reconcile_invalid_needs_human_projections() == 0
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "needs_human"
    assert store.get_reply_task(task.id).status == "needs_human"

    # A later queue transition is not rewritten from this unreviewed legacy row.
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'pending', 'error': 'orphaned_before_agent_start'},
            where_sql='and id=?',
            parameters=(task.id,),
        )
    assert store.reconcile_invalid_needs_human_projections() == 0
    assert store.get_reply_task(task.id).status == "pending"



def test_agent_run_concurrent_event_writers_do_not_drop_events(tmp_path: Path):
    db_path = tmp_path / "worker.sqlite3"
    first_store = AutoReplyStore(db_path)
    second_store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(first_store)
    run = _claim_audit_run(first_store, task_id, "initial", owner="worker-1").run
    barrier = Barrier(2)
    results: Queue = Queue()

    def append(store: AutoReplyStore, call_id: str) -> None:
        try:
            barrier.wait(timeout=5)
            store.append_agent_run_event(
                run.id,
                {"type": "item.completed", "call_id": call_id},
                owner="worker-1",
            )
            results.put(None)
        except BaseException as exc:
            results.put(exc)

    threads = [
        Thread(target=append, args=(first_store, "c1")),
        Thread(target=append, args=(second_store, "c2")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert [results.get_nowait(), results.get_nowait()] == [None, None]

    loaded = first_store.get_agent_run(run.id)
    assert loaded is not None
    assert len(loaded.tool_events) == 2
    assert {event["call_id"] for event in loaded.tool_events} == {"c1", "c2"}


def test_list_agent_runs_for_generation_batches_events_and_preserves_order(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "agent-run-batch.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    first = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    second = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=first.id,
        operation_id="audit", owner="audit",
    ).run
    store.append_agent_run_event(first.id, {"call_id": "first-1"}, owner="consumer")
    store.append_agent_run_event(first.id, {"call_id": "first-2"}, owner="consumer")
    store.append_agent_run_event(second.id, {"call_id": "second-1"}, owner="audit")

    runs = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)

    assert [run.id for run in runs] == [first.id, second.id]
    assert [event["call_id"] for event in runs[0].tool_events] == ["first-1", "first-2"]
    assert [event["call_id"] for event in runs[1].tool_events] == ["second-1"]

    status_runs = store.list_agent_runs_for_task_generation(
        task.id, task.execution_generation, load_events=False
    )
    assert [run.id for run in status_runs] == [first.id, second.id]
    assert [run.tool_events for run in status_runs] == [[], []]


def test_list_agent_runs_reads_native_events_after_connection_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from app import native_trajectory

    store = AutoReplyStore(tmp_path / "agent-run-hydration.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    audit_run = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=run.id,
        operation_id="audit", owner="audit",
    ).run
    result = {"outcome": "no_action", "summary": "native result"}
    native_paths = {}
    for session_id in ("native-session", "native-audit-session"):
        native_path = tmp_path / f"{session_id}.jsonl"
        native_path.write_text("\n".join((
            json.dumps({
                "type": "event_msg",
                "payload": {"type": "item_completed", "item": {
                    "type": "CommandExecution", "id": f"{session_id}-event",
                    "command": "true", "aggregated_output": "",
                }},
            }),
            json.dumps({
                "type": "response_item",
                "payload": {"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": json.dumps(result)}
                ]},
            }),
        )) + "\n")
        native_paths[session_id] = native_path
    monkeypatch.setattr(
        native_trajectory,
        "find_codex_session_path",
        lambda session_id, **kwargs: native_paths.get(session_id),
    )
    for agent_run, session_id, owner in (
        (run, "native-session", "consumer"),
        (audit_run, "native-audit-session", "audit"),
    ):
        runtime = store.claim_agent_runtime_attempt(
            agent_run.id, "codex_oauth", "codex_cli", "local_oauth", "test-model"
        )
        store.complete_agent_runtime_attempt(
            runtime.id, session_id, f"codex_session:{session_id}", 0, 2
        )
        if agent_run.id == run.id:
            store.complete_agent_run(
                agent_run.id, result, owner=owner, transcript_end_line=2
            )
        else:
            store.fail_agent_run(
                agent_run.id, {"code": "test_failure"}, owner=owner,
                transcript_end_line=2,
            )

    original_connect = store._connect
    connection_state = {"open": False}

    @contextmanager
    def tracked_connect():
        try:
            with original_connect() as db:
                db.set_trace_callback(trace_query)
                connection_state["open"] = True
                yield db
        finally:
            connection_state["open"] = False

    original_hydrate = native_trajectory.hydrate_run_events_from_attempts
    original_read_events = native_trajectory.read_codex_events
    original_read_result = native_trajectory.read_native_result_stream
    reference_query_count = {"value": 0}

    def trace_query(statement):
        if "from runtime_attempts" in statement.casefold():
            reference_query_count["value"] += 1

    def assert_connection_closed(row, attempts, db_path):
        assert connection_state["open"] is False
        return original_hydrate(row, attempts, db_path)

    def assert_read_events_connection_closed(*args, **kwargs):
        assert connection_state["open"] is False
        return original_read_events(*args, **kwargs)

    def assert_read_result_connection_closed(*args, **kwargs):
        assert connection_state["open"] is False
        return original_read_result(*args, **kwargs)

    monkeypatch.setattr(store, "_connect", tracked_connect)
    monkeypatch.setattr(
        native_trajectory,
        "hydrate_run_events_from_attempts",
        assert_connection_closed,
    )
    monkeypatch.setattr(
        native_trajectory, "read_codex_events", assert_read_events_connection_closed
    )
    monkeypatch.setattr(
        native_trajectory,
        "read_native_result_stream",
        assert_read_result_connection_closed,
    )

    runs = store.list_agent_runs_for_task_generation(
        task.id, task.execution_generation
    )

    assert [agent_run.id for agent_run in runs] == [run.id, audit_run.id]
    assert all(agent_run.tool_events for agent_run in runs)
    assert json.loads(runs[0].final_result_json) == result
    assert runs[1].final_result_json == ""
    assert reference_query_count["value"] == 2


def test_reply_attempt_status_projection_is_scoped_to_requested_ids(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "history-status-scope.sqlite3")
    selected = _attempt(store, trigger="selected", status="failed")
    unrelated = _attempt(store, trigger="unrelated", status="pending")

    assert store.list_reply_attempt_operation_statuses([selected, selected, 0, -1]) == {
        selected: "failed"
    }
    assert store.list_reply_attempt_operation_statuses([]) == {}
    assert store.list_reply_attempt_operation_statuses([unrelated + 10_000]) == {}


def test_append_rechecks_default_time_after_waiting_for_write_lock(
    tmp_path: Path,
    monkeypatch,
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store,
        task_id,
        "initial",
        owner="worker-1",
        lease_seconds=2,
    ).run
    original_utc_store_time = store_module._utc_store_time
    clock_called = Event()

    def observed_utc_store_time(now=None):
        if now is None:
            clock_called.set()
        return original_utc_store_time(now)

    monkeypatch.setattr(store_module, "_utc_store_time", observed_utc_store_time)
    lock_db = sqlite3.connect(db_path, timeout=5)
    lock_db.execute("begin immediate")
    started = Event()
    outcomes: Queue = Queue()

    def append() -> None:
        started.set()
        try:
            result = store.append_agent_run_event(
                run.id,
                {"type": "item.started", "call_id": "expired-while-waiting"},
                owner="worker-1",
            )
            outcomes.put(result)
        except BaseException as exc:
            outcomes.put(exc)

    thread = Thread(target=append)
    thread.start()
    try:
        assert started.wait(timeout=2)
        clock_called_before_release = clock_called.wait(timeout=0.2)
        expires_at = datetime.strptime(
            run.lease_expires_at,
            "%Y-%m-%d %H:%M:%S",
        ).replace(tzinfo=timezone.utc)
        wait_seconds = max(
            0.0,
            (expires_at - datetime.now(timezone.utc)).total_seconds(),
        ) + 0.2
        assert wait_seconds < 3
        time.sleep(wait_seconds)
    finally:
        lock_db.rollback()
        lock_db.close()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert clock_called_before_release is False
    outcome = outcomes.get_nowait()
    assert isinstance(outcome, AgentRunLeaseLostError)
    loaded = store.get_agent_run(run.id)
    assert loaded is not None
    assert loaded.tool_events == []


@pytest.mark.parametrize("event", [[], "event", 1, None])
def test_agent_run_event_must_be_a_json_object(tmp_path: Path, event):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run

    with pytest.raises(ValueError, match="event must be a JSON object"):
        store.append_agent_run_event(run.id, event, owner="worker-1")


def test_agent_run_event_rejects_non_json_object_values(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run

    with pytest.raises(ValueError, match="event must be a JSON object"):
        store.append_agent_run_event(
            run.id,
            {"value": object()},
            owner="worker-1",
        )


def test_agent_run_terminal_transitions_are_strict_and_exactly_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from app import native_trajectory

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    final_result = {"outcome": "completed", "summary": "sent"}
    native_path = tmp_path / "terminal-session.jsonl"
    native_path.write_text(json.dumps({
        "type": "response_item",
        "payload": {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": json.dumps(final_result)}
        ]},
    }) + "\n")
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *a, **k: native_path)
    with store._connect() as db:
        db.execute("update runs set session_id='terminal-session' where id=?", (run.id,))

    completed = store.complete_agent_run(
        run.id,
        final_result,
        owner="worker-1",
        transcript_end_line=1,
    )
    repeated = store.complete_agent_run(
        run.id,
        final_result,
        owner="worker-1",
        transcript_end_line=1,
    )

    assert completed.status == "completed"
    assert repeated == completed
    assert completed.lease_owner == ""
    assert completed.lease_expires_at == ""
    with pytest.raises(ValueError, match="conflicting terminal rewrite"):
        store.complete_agent_run(
            run.id,
            {"outcome": "completed", "summary": "different"},
            owner="worker-1",
            transcript_end_line=1,
        )
    with pytest.raises(ValueError, match="transition from completed"):
        store.fail_agent_run(
            run.id,
            {"code": "late_failure"},
            owner="worker-1",
        )
    with pytest.raises(ValueError, match="terminal agent run"):
        store.append_agent_run_event(
            run.id,
            {"type": "late"},
            owner="worker-1",
        )


def test_oa_triggers_share_one_current_reply_task_and_preserve_inputs(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first = store.ensure_reply_task(
        channel="dingtalk",
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=False,
        trigger_message_id="oa-pending:proc-1:first",
        trigger_create_time="2026-09-07 10:00:00",
        trigger_sender="Derek OA",
        trigger_text="首次扫描",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )
    second = store.ensure_reply_task(
        channel="dingtalk",
        conversation_id="work-notice",
        conversation_title="工作通知",
        single_chat=True,
        trigger_message_id="message-2",
        trigger_create_time="2026-09-07 10:05:00",
        trigger_sender="OA审批",
        trigger_text="申请人补充了说明",
        oa_url="https://aflow.dingtalk.com/detail?taskId=task-1&processInstanceId=proc-1",
    )

    assert second.id == first.id
    assert second.business_object_key == "oa:proc-1:task-1"
    assert second.trigger_message_id == "message-2"
    assert [item["trigger_message_id"] for item in store.list_reply_task_inputs(first.id)] == [
        "oa-pending:proc-1:first",
        "message-2",
    ]


def test_oa_comment_without_task_id_reuses_the_known_approval_task(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    pending = store.ensure_reply_task(
        channel="dingtalk",
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=False,
        trigger_message_id="oa-pending:proc-1:first",
        trigger_create_time="2026-09-07 10:00:00",
        trigger_sender="Derek OA",
        trigger_text="首次扫描",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )

    comment = store.ensure_reply_task(
        channel="dingtalk",
        conversation_id="work-notice",
        conversation_title="工作通知",
        single_chat=True,
        trigger_message_id="message-2",
        trigger_create_time="2026-09-07 10:05:00",
        trigger_sender="OA审批",
        trigger_text=(
            "申请人补充了说明 "
            "https://aflow.dingtalk.com/dingtalk/mobile/homepage.htm?"
            "procInstId=proc-1&taskId=&swfrom=comment"
        ),
    )

    assert comment.id == pending.id
    assert comment.business_object_key == "oa:proc-1:task-1"
    assert [item["trigger_message_id"] for item in store.list_reply_task_inputs(pending.id)] == [
        "oa-pending:proc-1:first",
        "message-2",
    ]


def test_oa_comment_aliases_the_known_node_on_current_schema(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "oa-alias.sqlite3")
    common = dict(channel="dingtalk", single_chat=True, trigger_create_time="2026-09-07 10:00:00", trigger_sender="OA审批")
    original = store.ensure_reply_task(
        **common, conversation_id="work-notice", conversation_title="工作通知",
        trigger_message_id="message-1",
        trigger_text="补充说明 https://aflow.dingtalk.com/mobile?procInstId=proc-1&taskId=",
    )
    known = store.ensure_reply_task(
        **common, conversation_id="oa_pending_scan", conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-1:task-1", trigger_text="待办扫描",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )
    follow_up = store.ensure_reply_task(
        **common, conversation_id="work-notice", conversation_title="工作通知",
        trigger_message_id="message-2",
        trigger_text="再次补充 https://aflow.dingtalk.com/mobile?procInstId=proc-1&taskId=",
    )
    assert original.id == known.id == follow_up.id
    assert store.get_reply_task(original.id).business_object_key == "oa:proc-1:task-1"
    assert len(store.list_reply_task_inputs(original.id)) == 3
    with store._connect() as db:
        assert [tuple(row) for row in db.execute(f"select business_object_key,reply_task_id from ({store_module.NATIVE_REPLY_BUSINESS_KEY_SQL})")] == [("oa:proc-1:task-1", original.id)]


def test_schema_upgrade_preserves_unchanged_business_object_mapping_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    for message_id in ("first", "second"):
        store.ensure_reply_task(
            channel="dingtalk", conversation_id="cid", conversation_title="Chat",
            single_chat=False, trigger_message_id=message_id,
            trigger_create_time="2026-10-10 00:00:00", trigger_sender="Sender",
            trigger_text=f"Input {message_id}",
        )
    with store._connect() as db:
        db.execute(
            "update task_business_keys set created_at='2020-01-02 03:04:05', "
            "updated_at='2021-02-03 04:05:06'"
        )
        before = [tuple(row) for row in db.execute(
            "select business_key, task_id, created_at, updated_at "
            "from task_business_keys order by business_key"
        )]
        update_state_rows(db, "service_state", {"value": store_module.STORE_SCHEMA_VERSION},
                          where_sql="key=?", parameters=(store_module.STORE_SCHEMA_VERSION_KEY,))
    # The core is current; exercise the retained business migration directly.
    store._initialize()
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())

    upgraded = AutoReplyStore(db_path)
    with upgraded._connect() as db:
        after = [tuple(row) for row in db.execute(
            "select business_key, task_id, created_at, updated_at "
            "from task_business_keys order by business_key"
        )]
    assert after == before


def test_current_business_object_mapping_keeps_latest_input(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "mapping.sqlite3")
    common = dict(channel="dingtalk", conversation_id="cid", conversation_title="Chat",
                  single_chat=False, trigger_create_time="2026-10-10", trigger_sender="Sender",
                  business_object_key="object:current")
    first = store.ensure_reply_task(**common, trigger_message_id="first", trigger_text="First input")
    second = store.ensure_reply_task(**common, trigger_message_id="second", trigger_text="Second input")
    assert first.id == second.id
    assert second.trigger_text == "Second input"
    assert second.input_version == first.input_version + 1
    with store._connect() as db:
        before = [tuple(row) for row in db.execute(f"select * from ({store_module.NATIVE_REPLY_BUSINESS_KEY_SQL})")]
    store_module._INITIALIZED_STORE_PATHS.discard(store.path.resolve())
    reopened = AutoReplyStore(store.path)
    assert reopened.get_reply_task(first.id).trigger_text == "Second input"
    assert [row["trigger_text"] for row in reopened.list_reply_task_inputs(first.id)] == ["First input", "Second input"]
    with reopened._connect() as db:
        assert [tuple(row) for row in db.execute(f"select * from ({store_module.NATIVE_REPLY_BUSINESS_KEY_SQL})")] == before


def test_different_oa_nodes_keep_separate_current_reply_tasks(tmp_path: Path) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first = store.ensure_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=False,
        trigger_message_id="oa-1",
        trigger_create_time="2026-09-07 10:00:00",
        trigger_sender="Derek OA",
        trigger_text="审批一",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )
    second = store.ensure_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=False,
        trigger_message_id="oa-2",
        trigger_create_time="2026-09-07 10:01:00",
        trigger_sender="Derek OA",
        trigger_text="审批二",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-2",
    )

    assert second.id != first.id
    assert second.business_object_key != first.business_object_key


def test_new_business_input_during_processing_requeues_same_task(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = store.ensure_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=False,
        trigger_message_id="oa-1",
        trigger_create_time="2026-09-07 10:00:00",
        trigger_sender="Derek OA",
        trigger_text="首次扫描",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None

    refreshed = store.ensure_reply_task(
        conversation_id="work-notice",
        conversation_title="工作通知",
        single_chat=True,
        trigger_message_id="message-2",
        trigger_create_time="2026-09-07 10:05:00",
        trigger_sender="OA审批",
        trigger_text="申请人补充了说明",
        oa_url="https://aflow.dingtalk.com/detail?procInstId=proc-1&taskId=task-1",
    )
    assert refreshed.id == task.id

    store.complete_reply_task(
        task.id,
        expected_execution_generation=claimed.execution_generation,
    )
    current = store.get_reply_task(task.id)
    assert current is not None
    assert current.status == "pending"
    assert current.execution_generation != claimed.execution_generation
    assert current.trigger_message_id == "message-2"












def test_failed_agent_run_rejects_conflicting_terminal_rewrite(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    run = _claim_audit_run(store, task_id, "initial", owner="worker-1").run
    error = {"code": "command_failed", "retryable": True}

    failed = store.fail_agent_run(
        run.id,
        error,
        owner="worker-1",
        transcript_end_line=5,
    )
    repeated = store.fail_agent_run(
        run.id,
        error,
        owner="worker-1",
        transcript_end_line=5,
    )

    assert failed == repeated
    with pytest.raises(ValueError, match="conflicting terminal rewrite"):
        store.fail_agent_run(
            run.id,
            {"code": "different_failure"},
            owner="worker-1",
            transcript_end_line=5,
        )


def test_retry_failed_reply_task_creates_a_new_generation_and_keeps_consumer_session(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    store.upsert_conversation_runtime_session(
        task.conversation_id,
        "codex_oauth",
        "stale-consumer-session",
        "service-contract",
    )
    claim = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-1",
    )
    store.fail_agent_run(
        claim.run.id,
        {"code": "codex_process_failed", "retryable": True},
        owner="worker-1",
    )
    store.fail_reply_task(
        task_id,
        "codex_process_failed",
        expected_execution_generation=task.execution_generation,
    )

    recovered = store.retry_failed_reply_task(
        task_id,
        claim.run.id,
        reason="operator_retry_after_runtime_fix",
    )

    assert recovered.status == "pending"
    assert recovered.execution_generation != task.execution_generation
    assert recovered.error == "operator_retry_after_runtime_fix"
    assert store.get_conversation_runtime_session(
        task.conversation_id,
        "codex_oauth",
    ) == "stale-consumer-session"
    retry_claim = store.claim_reply_task(task_id)
    assert retry_claim is not None
    same_turn = store.claim_agent_run(
        task_id,
        recovered.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-2",
    )
    assert same_turn.claimed is True
    duplicate_turn = store.claim_agent_run(
        task_id,
        recovered.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-2",
    )
    assert duplicate_turn.claimed is False
    next_turn = store.claim_agent_run(
        task_id,
        recovered.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=store.next_agent_run_turn_attempt(
            task_id,
            recovered.execution_generation,
            role=AgentRole.CONSUMER,
            proposal_revision=0,
        ),
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-2",
    )
    assert next_turn.claimed is True
    assert next_turn.run.id != claim.run.id
    assert next_turn.run.turn_attempt == 1
    assert store.get_agent_run(claim.run.id).status == "failed"









def test_recover_failed_native_codex_auth_task_requires_no_effect_or_delivery(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    claim = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-1",
    )
    store.fail_agent_run(
        claim.run.id,
        {"code": "codex_provider_auth_failed: native login unavailable", "retryable": False},
        owner="worker-1",
    )
    store.fail_reply_task(
        task_id,
        "codex_provider_auth_failed: native login unavailable",
        expected_execution_generation=task.execution_generation,
    )

    assert store.has_failed_native_codex_auth_tasks(channel="dingtalk") is True
    assert store.recover_failed_native_codex_auth_tasks(
        channel="dingtalk", reason="codex_auth_recovered"
    ) == [task_id]
    recovered = store.get_reply_task(task_id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.attempts == 0
    assert recovered.error == "codex_auth_recovered"
    assert recovered.execution_generation != task.execution_generation

    rerun = store.claim_agent_run(
        task_id,
        recovered.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-2",
    )
    assert rerun.claimed is True

    claimed = store.claim_reply_task(task_id)
    assert claimed is not None
    store.fail_reply_task(
        task_id,
        "codex_provider_auth_failed: native login unavailable",
        expected_execution_generation=claimed.execution_generation,
    )
    store.record_sent_reply(claimed.conversation_id, claimed.trigger_message_id, "done")
    assert store.recover_failed_native_codex_auth_tasks(
        channel="dingtalk", reason="codex_auth_recovered"
    ) == []


def test_recover_native_codex_auth_resumes_orphaned_recovery_lock(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'processing', 'error': 'codex_auth_recovered'},
            update_sql={'locked_at': "datetime('now', '-2 minutes')"},
            where_sql='and id=?',
            parameters=(task_id,),
        )

    assert store.has_failed_native_codex_auth_tasks(channel="dingtalk") is True
    assert store.recover_failed_native_codex_auth_tasks(
        channel="dingtalk", reason="codex_auth_recovered"
    ) == [task_id]
    recovered = store.get_reply_task(task_id)
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.execution_generation != task.execution_generation


def test_retry_failed_reply_task_reopens_effect_free_failure_without_retryable_flag(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    claim = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="worker-1",
    )
    store.fail_agent_run(
        claim.run.id,
        {"code": "runtime_result_validation_failed"},
        owner="worker-1",
    )
    store.fail_reply_task(
        task_id,
        "runtime_result_validation_failed",
        expected_execution_generation=task.execution_generation,
    )

    recovered = store.retry_failed_reply_task(
        task_id,
        claim.run.id,
        reason="automatic_effect_free_consumer_retry",
    )

    assert recovered.status == "pending"
    assert recovered.execution_generation != task.execution_generation


def test_retry_failed_reply_task_uses_durable_delivery_for_idempotency(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = _claim_audit_run(
        store,
        task_id,
        task.execution_generation,
        owner="worker-1",
    ).run
    store.fail_agent_run(
        run.id,
        {"code": "runtime_failure", "retryable": True},
        owner="worker-1",
    )
    store.fail_reply_task(
        task_id,
        "runtime_failure",
        expected_execution_generation=task.execution_generation,
    )
    store.record_sent_reply(
        task.conversation_id,
        task.trigger_message_id,
        "delivered",
        send_result_json=json.dumps(
            {"agent_run_id": run.id, "operation_id": run.operation_id},
            separators=(",", ":"),
        ),
    )

    recovered = store.retry_failed_reply_task(
        task_id,
        run.id,
        reason="operator_retry_after_runtime_fix",
    )

    assert recovered.status == "pending"
    assert store.get_sent_reply(task.conversation_id, task.trigger_message_id) is not None




def test_retry_failed_reply_task_rejects_older_run_and_reopens_safe_latest_audit(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    older = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="audit-attempt-0",
        owner="worker-1",
    )
    store.fail_agent_run(
        older.run.id,
        {"code": "runtime_failure", "retryable": True},
        owner="worker-1",
    )
    latest = store.claim_agent_run(
        task_id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=1,
        parent_agent_run_id=None,
        operation_id="audit-attempt-1",
        owner="worker-1",
    )
    store.fail_agent_run(
        latest.run.id,
        {"code": "runtime_failure", "retryable": True},
        owner="worker-1",
    )
    store.fail_reply_task(
        task_id,
        "runtime_failure",
        expected_execution_generation=task.execution_generation,
    )

    with pytest.raises(ValueError, match="not retryable"):
        store.retry_failed_reply_task(
            task_id,
            older.run.id,
            reason="operator_retry_after_runtime_fix",
        )

    recovered = store.retry_failed_reply_task(
        task_id,
        latest.run.id,
        reason="operator_retry_after_runtime_fix",
    )

    assert recovered.status == "pending"
    assert recovered.execution_generation != task.execution_generation
    assert recovered.error == "operator_retry_after_runtime_fix"






























def test_reply_task_state_writes_reject_stale_generation_before_run_creation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    new_generation = store.rotate_reply_task_execution_generation(task_id)

    for operation in (
        lambda: store.requeue_reply_task(
            task_id, "old context failure", expected_execution_generation="initial"
        ),
        lambda: store.fail_reply_task(
            task_id, "old authorization failure", expected_execution_generation="initial"
        ),
        lambda: store.defer_reply_task(
            task_id, "old active run", expected_execution_generation="initial"
        ),
        lambda: store.complete_reply_task(
            task_id, expected_execution_generation="initial"
        ),
    ):
        with pytest.raises(AgentRunLeaseLostError):
            operation()

    task = store.get_reply_task(task_id)
    assert task is not None
    assert task.execution_generation == new_generation
    assert task.status == "pending"
    assert task.locked_at is None
    assert task.error == "execution_generation_rotated"






def test_generation_switch_revokes_old_run_write_access_and_only_new_run_claims(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    old = _claim_audit_run(store,
        task_id,
        "initial",
        owner="old-worker",
        now="2026-07-29 09:00:00",
    ).run

    new_generation = store.rotate_reply_task_execution_generation(task_id)

    with pytest.raises(AgentRunLeaseLostError):
        store.append_agent_run_event(
            old.id,
            {
                "type": "item.completed",
                "item": {
                    "id": "send-1",
                    "type": "command_execution",
                    "metadata": {"effect": "effectful"},
                },
            },
            owner="old-worker",
            now="2026-07-29 09:00:01",
        )
    with pytest.raises(AgentRunLeaseLostError):
        store.complete_agent_run(
            old.id,
            {"outcome": "completed"},
            owner="old-worker",
                now="2026-07-29 09:00:01",
        )

    superseded = store.get_agent_run(old.id)
    assert superseded is not None
    assert superseded.status == "failed"
    assert "superseded" in superseded.structured_error_json
    assert superseded.lease_owner == ""
    assert superseded.lease_expires_at == ""

    claimed_task = store.claim_reply_task(
        task_id,
        now="2026-07-29 09:00:01",
    )
    assert claimed_task is not None
    new_claim = _claim_audit_run(store,
        task_id,
        new_generation,
        owner="new-worker",
        now="2026-07-29 09:00:01",
    )
    assert new_claim.claimed is True
    assert new_claim.run.execution_generation == new_generation














def test_reopening_current_core_keeps_native_events_in_ram_only(tmp_path: Path):
    db_path = tmp_path / 'core-native-ram.sqlite3'
    store = AutoReplyStore(db_path)
    run = _claimed_runtime_agent_run(store)
    event = {'type':'item.completed','call_id':'native-1'}
    store.append_agent_run_event(run.id, event, owner='runtime-attempt')
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    reopened = AutoReplyStore(db_path)
    assert reopened.get_agent_run(run.id).tool_events == [event]
    with reopened._connect() as db:
        assert_no_core_native_storage(db)



def test_get_agent_run_for_turn_returns_exact_row(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    claimed = _claim_audit_run(store, task_id, "initial", owner="worker-1")

    loaded = _get_audit_run(store, task_id, "initial")

    assert loaded == claimed.run
    assert _get_audit_run(store, task_id, "missing") is None


def test_claim_reply_tasks_marks_tasks_processing_atomically(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 看一下",
    )

    claimed = store.claim_reply_tasks(limit=1)
    second_claim = store.claim_reply_tasks(limit=1)

    assert len(claimed) == 1
    assert claimed[0].conversation_id == "cid-1"
    assert claimed[0].trigger_message_id == "msg-1"
    assert claimed[0].status == "processing"
    assert claimed[0].attempts == 1
    assert second_claim == []
    assert store.count_reply_tasks(status="pending") == 0
    assert store.count_reply_tasks(status="processing") == 1


def test_peek_reply_tasks_does_not_claim_or_increment_attempts(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Derek",
        trigger_text="read this",
        trigger_message_json="{}",
    )

    peeked = store.peek_reply_tasks(limit=1, now="2026-05-13 18:01:00")
    task_id = peeked[0].id

    task = store.get_reply_task(task_id)
    assert task is not None
    assert task.status == "pending"
    assert task.attempts == 0








def test_peek_reply_tasks_pages_after_id_without_claiming(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for index in range(3):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time=f"2026-05-13 18:00:0{index}",
            trigger_sender="Derek",
            trigger_text=str(index),
            trigger_message_json="{}",
        )

    first_page = store.peek_reply_tasks(
        limit=2, now="2026-05-13 18:01:00"
    )
    second_page = store.peek_reply_tasks(
        limit=2,
        now="2026-05-13 18:01:00",
        after_id=first_page[-1].id,
    )

    assert [task.trigger_message_id for task in first_page] == ["msg-0", "msg-1"]
    assert [task.trigger_message_id for task in second_page] == ["msg-2"]
    assert store.count_reply_tasks(status="pending") == 3


def test_peek_reply_tasks_respects_pending_snapshot_upper_bound(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for index in range(3):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time=f"2026-05-13 18:00:0{index}",
            trigger_sender="Derek",
            trigger_text=str(index),
            trigger_message_json="{}",
        )
    max_id = store.max_pending_reply_task_id(
        now="2026-05-13 18:01:00",
        channel="dingtalk",
    )
    assert max_id is not None
    store.enqueue_reply_task(
        conversation_id="cid-new",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-new",
        trigger_create_time="2026-05-13 18:00:03",
        trigger_sender="Derek",
        trigger_text="new",
        trigger_message_json="{}",
    )

    snapshot = store.peek_reply_tasks(
        limit=10,
        now="2026-05-13 18:01:00",
        channel="dingtalk",
        max_id=max_id,
    )

    assert [task.trigger_message_id for task in snapshot] == [
        "msg-0",
        "msg-1",
        "msg-2",
    ]


def test_claim_reply_task_claims_only_requested_pending_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for index in (1, 2):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title="Friday",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time=f"2026-05-13 18:00:0{index}",
            trigger_sender="Derek",
            trigger_text=str(index),
            trigger_message_json="{}",
        )
    first, second = store.peek_reply_tasks(limit=2, now="2026-05-13 18:01:00")

    claimed = store.claim_reply_task(second.id, now="2026-05-13 18:01:00")

    assert claimed is not None
    assert claimed.id == second.id
    assert claimed.status == "processing"
    assert claimed.attempts == 1
    unchanged = store.get_reply_task(first.id)
    assert unchanged is not None
    assert unchanged.status == "pending"
    assert unchanged.attempts == 0


def test_claim_reply_tasks_waits_until_available_at(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 看一下",
        available_at="2026-05-13 17:05:00",
        error="waiting_fast_path_unread_backoff",
    )

    before = store.claim_reply_tasks(limit=1, now="2026-05-13 17:04:59")
    after = store.claim_reply_tasks(limit=1, now="2026-05-13 17:05:00")

    assert before == []
    assert len(after) == 1
    assert after[0].status == "processing"
    assert after[0].available_at == ""
    assert after[0].error == "waiting_fast_path_unread_backoff"


def test_claim_reply_tasks_accepts_timezone_aware_available_at(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-iso-time",
        conversation_title="Morgan",
        single_chat=True,
        trigger_message_id="msg-iso-time",
        trigger_create_time="2026-09-08T01:00:00-07:00",
        trigger_sender="Morgan",
        trigger_text="hello",
        available_at="2026-09-08T01:04:34-07:00",
        channel="wechat",
    )

    [claimed] = store.claim_reply_tasks(
        limit=1,
        channel="wechat",
        now="2026-09-08 08:05:00",
    )

    assert claimed.status == "processing"
    assert claimed.available_at == ""


def test_requeue_reply_task_can_delay_next_claim(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1, now="2026-05-13 17:00:00")

    store.requeue_reply_task(
        claimed[0].id,
        "temporary failure",
        expected_execution_generation=claimed[0].execution_generation,
        available_at="2026-05-13 17:01:00",
    )

    before = store.claim_reply_tasks(limit=1, now="2026-05-13 17:00:59")
    after = store.claim_reply_tasks(limit=1, now="2026-05-13 17:01:00")

    assert before == []
    assert len(after) == 1
    assert after[0].attempts == 2
    assert after[0].available_at == ""
    assert after[0].error == "temporary failure"


def test_requeue_reply_task_records_stale_run_terminal_failure_event(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="stale-worker",
        lease_seconds=1,
        now="2026-05-13 17:00:00",
    ).run

    store.requeue_reply_task(
        task.id,
        "stale_agent_turn_recovery",
        expected_execution_generation=task.execution_generation,
    )

    failed = store.get_agent_run(run.id)
    assert failed is not None
    assert failed.status == "failed"
    with store._connect() as db:
        events = db.execute(
            f"select phase, structured_error_json from ({RUN_STATE_EVENT_SQL}) "
            "where agent_run_id=? order by id",
            (run.id,),
        ).fetchall()
    assert [(row["phase"], json.loads(row["structured_error_json"])) for row in events] == [
        (
            "terminal_failure",
            {
                "authorization_required": False,
                "code": "stale_agent_turn_recovery",
                "retryable": True,
            },
        )
    ]


def test_finalize_orchestrated_reply_task_has_no_unknown_run_compatibility():
    source = inspect.getsource(AutoReplyStore.finalize_orchestrated_reply_task)

    assert '"unknown"' not in source


def test_complete_reply_task_marks_generation_bound_task_done(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)[0]
    store.complete_reply_task(
        claimed.id,
        expected_execution_generation=claimed.execution_generation,
    )

    tasks = store.list_reply_tasks(limit=1)
    assert tasks[0].status == "done"
    assert tasks[0].error == ""


def test_settled_reply_input_compacts_exact_copies_and_keeps_identity(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "reply-input.sqlite3")
    original = json.dumps({"schema": "email_agent_action.v1",
                           "lifecycle_version": "email_unsubscribe_audited_v2",
                           "action_type": "unsubscribe", "action_identity": "action-1",
                           "classification_id": 42, "body": "long copied input"})
    spec = dict(channel="email", conversation_id="email:thread-1",
                conversation_title="Mail", single_chat=True,
                trigger_message_id="action-1", trigger_create_time="2026-10-10 10:00:00",
                trigger_sender="Sender", trigger_text="long copied input",
                trigger_message_json=original)
    task = store.ensure_reply_task(**spec)
    attempt_id = store.record_reply_attempt(
        channel="email", conversation_id=spec["conversation_id"],
        conversation_title="Mail", trigger_message_id="action-1",
        trigger_sender="Sender", trigger_text="an earlier rendering",
        action="agent_run", sensitivity_kind="normal", send_status="completed",
    )
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    store.complete_reply_task(task.id, expected_execution_generation=claimed.execution_generation)
    settled = store.get_reply_task(task.id)
    assert settled is not None and settled.status == "done"
    assert settled.input_compacted and settled.trigger_text == ""
    assert json.loads(settled.input_provenance_json)["trigger_message_json"] == {
        "sha256": hashlib.sha256(original.encode()).hexdigest(),
        "bytes": len(original.encode()),
    }
    marker = json.loads(settled.trigger_message_json)
    assert marker["input_compacted"] is True
    assert (marker["schema"], marker["lifecycle_version"], marker["action_type"],
            marker["classification_id"]) == (
                "email_agent_action.v1", "email_unsubscribe_audited_v2", "unsubscribe", 42)
    [input_row] = store.list_reply_task_inputs(task.id)
    assert input_row["input_compacted"] == 1 and input_row["trigger_text"] == ""
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None and attempt.trigger_text == ""
    assert json.loads(attempt.trigger_text_provenance_json)["sha256"] == hashlib.sha256(
        "an earlier rendering".encode()).hexdigest()
    assert store.ensure_reply_tasks([spec])[0].id == task.id
    with pytest.raises(store_module.ReplyTaskIdentityConflict):
        store.ensure_reply_tasks([{**spec, "trigger_text": "changed original"}])
    assert store.get_reply_task(task.id).input_compacted


def test_compacted_reply_input_revised_card_starts_new_exact_generation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "revised.sqlite3")
    task = store.ensure_reply_task(
        conversation_id="cid-card", conversation_title="Card", single_chat=False,
        trigger_message_id="msg-card", trigger_create_time="2026-10-10 10:00:00",
        trigger_sender="Sender", trigger_text="First card",
        trigger_message_json=(
            '{"content":"First card","scheduled_consumer":'
            '{"scheduled_task_run_id":1}}'
        ),
    )
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    store.complete_reply_task(task.id, expected_execution_generation=claimed.execution_generation)
    assert store.get_reply_task(task.id).input_compacted
    assert not store.requeue_terminal_reply_task_for_revised_trigger(
        conversation_id="cid-card", conversation_title="Card", single_chat=False,
        trigger_message_id="msg-card", trigger_create_time="2026-10-10 10:00:00",
        trigger_sender="Sender", trigger_text="First card",
        trigger_message_json=(
            '{"content":"First card","scheduled_consumer":'
            '{"scheduled_task_run_id":2}}'
        ),
    )
    assert store.get_reply_task(task.id).input_compacted
    assert len(store.list_reply_task_inputs(task.id)) == 1
    assert store.requeue_terminal_reply_task_for_revised_trigger(
        conversation_id="cid-card", conversation_title="Card", single_chat=False,
        trigger_message_id="msg-card", trigger_create_time="2026-10-10 10:01:00",
        trigger_sender="Sender", trigger_text="Updated card",
        trigger_message_json='{"content":"Updated card"}',
    )
    revised = store.get_reply_task(task.id)
    assert revised is not None and revised.status == "pending"
    assert not revised.input_compacted and revised.input_provenance_json == "{}"
    assert revised.trigger_text == "Updated card"
    assert revised.execution_generation != claimed.execution_generation
    assert len(store.list_reply_task_inputs(task.id)) == 2


def test_failed_reply_task_requeues_once_for_new_source_revision(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "source-revision-rerun.sqlite3")
    task = store.ensure_reply_task(
        conversation_id="cid-source-revision",
        conversation_title="Source revision",
        single_chat=False,
        trigger_message_id="msg-original",
        trigger_create_time="2026-10-13 10:00:00",
        trigger_sender="Sender",
        trigger_text="Please handle this after checking the current context.",
    )
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    store.fail_reply_task(
        task.id,
        "audit_revision_exhausted",
        expected_execution_generation=claimed.execution_generation,
    )

    assert store.requeue_failed_reply_task_for_source_revision(
        task.id,
        expected_execution_generation=claimed.execution_generation,
        source_revision_key="messages:sha256:later-context",
    )
    rerun = store.get_reply_task(task.id)
    assert rerun is not None
    assert rerun.status == "pending"
    assert rerun.error == "automatic_source_revision_rerun"
    assert rerun.force_new_decision is True
    assert rerun.execution_generation != task.execution_generation
    assert not store.requeue_failed_reply_task_for_source_revision(
        task.id,
        expected_execution_generation=rerun.execution_generation,
        source_revision_key="messages:sha256:later-context",
    )


def test_failed_reply_task_source_revision_rerun_stops_after_delivery_receipt(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "source-revision-receipt.sqlite3")
    task = store.ensure_reply_task(
        conversation_id="cid-source-revision-receipt",
        conversation_title="Source revision receipt",
        single_chat=False,
        trigger_message_id="msg-original",
        trigger_create_time="2026-10-13 10:00:00",
        trigger_sender="Sender",
        trigger_text="Please handle this after checking the current context.",
    )
    claimed = store.claim_reply_task(task.id)
    assert claimed is not None
    store.fail_reply_task(
        task.id,
        "audit_revision_exhausted",
        expected_execution_generation=claimed.execution_generation,
    )
    store.record_sent_reply(task.conversation_id, task.trigger_message_id, "Already delivered")

    assert not store.requeue_failed_reply_task_for_source_revision(
        task.id,
        expected_execution_generation=claimed.execution_generation,
        source_revision_key="messages:sha256:later-context",
    )
    unchanged = store.get_reply_task(task.id)
    assert unchanged is not None
    assert unchanged.status == "failed"
    assert unchanged.execution_generation == claimed.execution_generation


def test_scheduled_reply_compaction_keeps_run_and_frozen_skill_identity():
    payload = json.dumps({
        "schema": "scheduled_agent_execution.v1",
        "context": {"trigger_raw_payload": {
            "scheduled_task_id": 5, "scheduled_task_version": 3,
            "scheduled_task_run_id": 17,
            "skills": [
                {"source": "managed", "name": "daily", "skill_id": 8,
                 "revision_id": 11, "sha256": "a" * 64},
                {"source": "operation", "name": "ops", "path": "/skills/ops/SKILL.md",
                 "sha256": "b" * 64},
            ],
        }},
        "skill_names": ["daily", "ops"],
        "skill_materials": [{"name": "daily", "content": "frozen body"}],
    })
    provenance = AutoReplyStore._reply_input_provenance("prompt", payload)
    compact = json.loads(AutoReplyStore._compact_reply_input_json(provenance))
    assert compact["input_compacted"] is True
    assert compact["context"]["trigger_raw_payload"]["scheduled_task_run_id"] == 17
    assert compact["skills"][0]["revision_id"] == 11
    assert compact["skills"][1]["path"] == "/skills/ops/SKILL.md"
    assert "frozen body" not in json.dumps(compact)
    assert json.loads(provenance)["source"]["skill_materials"][0]["content"] == {
        "sha256": hashlib.sha256(b"frozen body").hexdigest(), "bytes": 11,
    }








def test_list_reply_tasks_filters_statuses_newest_first(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 看一下",
    )
    store.enqueue_reply_task(
        conversation_id="cid-2",
        conversation_title="HR管理",
        single_chat=False,
        trigger_message_id="msg-2",
        trigger_create_time="2026-05-13 18:01:00",
        trigger_sender="Phina",
        trigger_text="@Alex Chen 再看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)
    store.complete_reply_task(
        claimed[0].id,
        expected_execution_generation=claimed[0].execution_generation,
    )

    tasks = store.list_reply_tasks(statuses=("pending", "processing", "failed"))

    assert [task.trigger_message_id for task in tasks] == ["msg-2"]


def test_requeue_reply_task_keeps_attempt_count_for_retry(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)

    store.requeue_reply_task(
        claimed[0].id,
        "temporary dws auth failure",
        expected_execution_generation=claimed[0].execution_generation,
    )
    reclaimed = store.claim_reply_tasks(limit=1)

    assert reclaimed[0].id == claimed[0].id
    assert reclaimed[0].attempts == 2
    assert reclaimed[0].error == "temporary dws auth failure"


def test_defer_reply_task_for_authorization_preserves_claim_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Friday",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-05-13 18:00:00",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 看一下",
    )
    claimed = store.claim_reply_tasks(limit=1)

    store.defer_reply_task_for_authorization(
        claimed[0].id,
        "authorization required",
        expected_execution_generation=claimed[0].execution_generation,
    )
    reclaimed = store.claim_reply_tasks(limit=1)

    assert reclaimed[0].id == claimed[0].id
    assert reclaimed[0].attempts == 2
    assert reclaimed[0].error == "authorization required"


def test_create_and_claim_okr_review_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="吴婷",
        trigger_message_id="msg-1",
        trigger_sender="吴婷",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )

    claimed = store.claim_okr_review_requests(limit=1)

    assert [item.id for item in claimed] == [request_id]
    assert claimed[0].status == "processing"


def test_recreating_okr_review_request_requeues_failed_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="吴婷",
        trigger_message_id="msg-1",
        trigger_sender="吴婷",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_failed(request_id, "source unavailable")

    recreated_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="吴婷",
        trigger_message_id="msg-1",
        trigger_sender="吴婷",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"processed":{"okrRows":[]}}',
    )

    assert recreated_id == request_id
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "pending"
    assert loaded.error == ""
    assert loaded.codex_session_id == ""
    assert json.loads(loaded.okr_source_json)["processed"]["okrRows"] == []


def test_okr_review_request_is_failed_for_feedback_instead_of_skipped(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="吴婷",
        trigger_message_id="msg-1",
        trigger_sender="吴婷",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )

    store.mark_okr_review_request_failed(request_id, "reviewer feedback required")

    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "failed"
    assert loaded.error == "reviewer feedback required"
    assert store.claim_okr_review_requests(limit=1) == []


def test_recreating_okr_review_request_does_not_requeue_done_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="吴婷",
        trigger_message_id="msg-1",
        trigger_sender="吴婷",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_done(request_id, codex_session_id="session-1")

    recreated_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="吴婷",
        trigger_message_id="msg-1",
        trigger_sender="吴婷",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"processed":{"okrRows":[]}}',
    )

    assert recreated_id == request_id
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "done"
    assert loaded.codex_session_id == "session-1"
    assert json.loads(loaded.okr_source_json)["objectives"] == []


def test_recreating_okr_review_request_does_not_reset_processing_request(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="吴婷",
        trigger_message_id="msg-1",
        trigger_sender="吴婷",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    claimed = store.claim_okr_review_requests(limit=1)

    recreated_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="吴婷",
        trigger_message_id="msg-1",
        trigger_sender="吴婷",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"processed":{"okrRows":[]}}',
    )

    assert [item.id for item in claimed] == [request_id]
    assert recreated_id == request_id
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "processing"
    assert json.loads(loaded.okr_source_json)["objectives"] == []


def test_reset_recoverable_okr_review_requests_requeues_stale_processing(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    claimed = store.claim_okr_review_requests(limit=1)[0]
    assert store.acquire_codex_session_lock("cid-1", f"okr_review:{request_id}")
    with sqlite3.connect(db_path) as db:
        _update_current_task8(
            db,
            'okr_review_requests',
            {},
            update_sql={'updated_at': "datetime('now', '-31 minutes')"},
            where_sql='and id=?',
            parameters=(request_id,),
        )

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert [request.id for request in recovered] == [claimed.id]
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "pending"
    assert loaded.error == ""
    assert store.acquire_codex_session_lock("cid-1", "reply:msg-1")


def test_reset_recoverable_okr_review_requests_keeps_fresh_processing(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    store.claim_okr_review_requests(limit=1)

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert recovered == []
    assert store.get_okr_review_request(request_id).status == "processing"


def test_reset_recoverable_okr_review_requests_requeues_stale_lock_failure(
    tmp_path: Path,
):
    db_path = tmp_path / "worker.sqlite3"
    store = AutoReplyStore(db_path)
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="再查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_failed(request_id, "codex session locked: cid-1")

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert [request.id for request in recovered] == [request_id]
    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "pending"
    assert loaded.error == ""


def test_reset_recoverable_okr_review_requests_keeps_fresh_lock_failure(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="卢鑫",
        trigger_message_id="msg-1",
        trigger_sender="卢鑫",
        trigger_sender_user_id="user-1",
        trigger_text="再查一下我的评分",
        period_label="2026 Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        okr_source_json='{"objectives":[]}',
    )
    store.mark_okr_review_request_failed(request_id, "codex session locked: cid-1")
    assert store.acquire_codex_session_lock("cid-1", "okr_review:other")

    recovered = store.reset_recoverable_okr_review_requests(
        processing_max_age_seconds=30 * 60
    )

    assert recovered == []
    assert store.get_okr_review_request(request_id).status == "failed"


def test_record_okr_review_run_and_items(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    request_id = store.create_okr_review_request(
        conversation_id="cid-1",
        conversation_title="吴婷",
        trigger_message_id="msg-1",
        trigger_sender="吴婷",
        trigger_sender_user_id="user-1",
        trigger_text="帮我审核 OKR",
        period_label="2026 Q2",
        period_start="2026-04-01",
        period_end="2026-06-30",
        okr_source_json='{"objectives":[]}',
    )
    run_id = store.create_professional_run(request_id, session_id="session-1", transcript_start=1, transcript_end=10)
    item_id = store.record_okr_review_item(
        request_id=request_id,
        objective_title="O",
        objective_weight=1.0,
        kr_title="KR",
        kr_weight=0.5,
        item_json='{"kr_title":"KR"}',
    )
    store.mark_okr_review_request_done(request_id, codex_session_id="session-1")

    loaded = store.get_okr_review_request(request_id)
    assert loaded.status == "done"
    assert run_id > 0
    assert item_id > 0


def test_create_okr_review_request_requires_source_json(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(TypeError):
        store.create_okr_review_request(
            conversation_id="cid-1",
            conversation_title="吴婷",
            trigger_message_id="msg-1",
            trigger_sender="吴婷",
            trigger_sender_user_id="user-1",
            trigger_text="帮我审核 OKR",
            period_label="2026 Q2",
            period_start="2026-04-01",
            period_end="2026-06-30",
        )


def test_logical_okr_run_rejects_retired_output_parameters(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(TypeError):
        store.create_professional_run(1,
            codex_session_id="session-1", codex_transcript_start_line=1,
            codex_transcript_end_line=10, envelope_json="{}",
            audit_tool_events_json="[]", audit_summary="retired")


def test_record_okr_review_item_requires_item_json(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    with pytest.raises(TypeError):
        store.record_okr_review_item(
            request_id=1,
            objective_title="O",
            objective_weight=1.0,
            kr_title="KR",
            kr_weight=0.5,
        )


def test_reset_codex_sessions_clears_conversation_mapping_only(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_conversation("cid-1", "Friday", False, "session-1")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_session_id="session-1",
        codex_transcript_start_line=3,
        codex_transcript_end_line=9,
    )

    cleared = store.reset_codex_sessions()

    assert cleared == 1
    assert store.get_codex_session_id("cid-1") is None
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.codex_session_id == "session-1"
    assert attempt.codex_transcript_start_line == 3
    assert attempt.codex_transcript_end_line == 9


def test_record_reply_attempt_extracts_memory_write_events(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    memory_output = {
        "structured_content": {
            "result": json.dumps(
                {
                    "ok": True,
                    "episode_uuid": "episode-1",
                    "processing_status": "completed",
                }
            )
        }
    }

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="记一下这个项目口径",
        action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "memory_write",
                    "call_id": "call-1",
                    "input": json.dumps({"data": "stable fact"}),
                    "output": json.dumps(memory_output),
                }
            ]
        ),
    )

    events = store.list_memory_write_events_for_attempt(attempt_id)

    assert len(events) == 1
    assert events[0].status == "written"
    assert events[0].memory_episode_id == "episode-1"


def test_record_reply_attempt_extracts_memory_write_output_from_tool_output(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    memory_output = {
        "result": json.dumps(
            {
                "ok": True,
                "episode_uuid": "episode-2",
                "processing_status": "pending",
            }
        )
    }

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="记一下这个项目口径",
        action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "memory_write",
                    "call_id": "call-1",
                    "input": json.dumps({"data": "stable fact"}),
                },
                {
                    "event_type": "response_item",
                    "tool": "tool_output",
                    "call_id": "call-1",
                    "output": "Wall time: 1.1 seconds\nOutput:\n"
                    + json.dumps(memory_output),
                },
            ]
        ),
    )

    events = store.list_memory_write_events_for_attempt(attempt_id)

    assert len(events) == 1
    assert events[0].status == "written"
    assert events[0].memory_episode_id == "episode-2"


def test_record_reply_attempt_ignores_tool_search_memory_write_mentions(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="查一下记忆",
        action="send_reply",
        sensitivity_kind="general",
        audit_tool_events_json=json.dumps(
            [
                {
                    "event_type": "response_item",
                    "tool": "tool_search_call",
                    "call_id": "call-1",
                    "input": json.dumps({"query": "memory_connector memory_write"}),
                }
            ]
        ),
    )

    assert store.list_memory_write_events_for_attempt(attempt_id) == []






def test_seen_messages_are_deduplicated(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.has_seen("msg-1") is False
    assert store.mark_seen("msg-1", "cid-1") is True
    assert store.has_seen("msg-1") is True
    assert store.mark_seen("msg-1", "cid-1") is False


def test_records_sent_reply_and_error(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.record_sent_reply(
        "cid-1",
        "msg-1",
        "收到（by明哥分身）",
        send_result_json='{"result":{"processQueryKey":"key-1"}}',
        recall_key="key-1",
    )
    store.record_error("cid-1", "msg-2", "codex_json", "invalid json")
    sent_reply = store.get_sent_reply("cid-1", "msg-1")

    assert store.count_sent_replies() == 1
    assert sent_reply is not None
    assert sent_reply.recall_key == "key-1"
    assert sent_reply.recall_status == ""
    assert store.count_errors() == 1


def test_records_sent_reply_recall_result(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "收到（by明哥分身）", recall_key="key-1")
    sent_reply = store.get_sent_reply("cid-1", "msg-1")

    assert sent_reply is not None

    store.update_sent_reply_recall(
        sent_reply.id,
        recall_status="recalled",
        recall_error="",
    )
    updated = store.get_sent_reply("cid-1", "msg-1")

    assert updated is not None
    assert updated.recall_status == "recalled"
    assert updated.recalled_at is not None


def test_feedback_pressure_counts_unanswered_replies_since_last_feedback(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply(
        "cid-1",
        "old-before-feedback",
        "旧回复",
        feedback_token="token-old",
    )
    store.record_sent_reply(
        "cid-1",
        "old-unanswered",
        "旧回复",
        feedback_token="token-1",
    )
    store.record_sent_reply(
        "cid-1",
        "recent-unanswered",
        "近回复",
        feedback_token="token-2",
    )
    store.record_sent_reply(
        "cid-2",
        "other-conversation",
        "其他会话",
        feedback_token="token-3",
    )
    store.upsert_feedback_event(
        key="event-old",
        feedback_token="token-old",
        rating="up",
        received_at="2026-06-01 12:00:00",
    )
    with sqlite3.connect(store.path) as db:
        for sent_at, trigger_id in (
            ("2026-05-30 12:00:00", "old-before-feedback"),
            ("2026-06-02 12:00:00", "old-unanswered"),
            ("2026-06-09 12:00:00", "recent-unanswered"),
            ("2026-06-02 12:00:00", "other-conversation"),
        ):
            for row in db.execute(f"select id from ({SENT_REPLY_SQL}) where trigger_message_id=?", (trigger_id,)).fetchall():
                db.execute(
                    "update action_receipts set observed_at=? where id="
                    "(select action_delivery_receipt_id from actions where id=?)",
                    (sent_at, row[0]),
                )

    stats = store.feedback_pressure_stats(
        "cid-1",
        now_utc="2026-06-12 12:00:00",
    )

    assert stats.unanswered_since_last_feedback == 2
    assert stats.unanswered_older_than_7_days == 1
    assert stats.unanswered_older_than_10_days == 1


def test_list_sent_replies_with_feedback_tokens_for_conversation(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "无反馈")
    store.record_sent_reply("cid-1", "msg-2", "旧回复", feedback_token="token-1")
    store.record_sent_reply("cid-2", "msg-3", "其他会话", feedback_token="token-2")
    store.record_sent_reply("cid-1", "msg-4", "新回复", feedback_token="token-3")

    replies = store.list_sent_replies_with_feedback_tokens_for_conversation(
        "cid-1",
        limit=10,
    )

    assert [reply.trigger_message_id for reply in replies] == ["msg-4", "msg-2"]


def test_list_sent_replies_waiting_for_feedback_events_filters_answered_tokens(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "无反馈")
    store.record_sent_reply("cid-1", "msg-2", "已有本地反馈", feedback_token="token-1")
    store.record_sent_reply("cid-1", "msg-3", "等待反馈同步", feedback_token="token-2")
    store.upsert_feedback_event(
        key="event-1",
        feedback_token="token-1",
        rating="useful",
        received_at="2026-06-18T08:00:00.000Z",
    )

    replies = store.list_sent_replies_waiting_for_feedback_events(limit=10)

    assert [reply.trigger_message_id for reply in replies] == ["msg-3"]


def test_reply_attempt_tracing_and_feedback_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
        draft_reply_text="先收敛问题",
        codex_session_id="session-1",
        codex_transcript_start_line=2,
        codex_transcript_end_line=7,
        audit_documents_json='[{"path":"面试/岗位画像.md"}]',
        audit_tool_events_json='[{"tool":"exec_command","command":"rg 岗位"}]',
        audit_summary="查看岗位画像后判断需要先收敛问题。",
    )
    store.update_reply_attempt(
        attempt_id,
        final_reply_text="先收敛问题（by明哥分身）",
        permission_action="allow",
        permission_reason="",
        send_status="sent",
        retry_count=1,
    )
    store.record_reply_feedback(
        attempt_id,
        feedback="语气可以，但需要更具体",
        corrected_reply_text="先明确负责人和时间点。",
    )

    attempt = store.get_reply_attempt(attempt_id)

    assert store.count_reply_attempts() == 1
    assert attempt is not None
    assert attempt.conversation_title == "技术部"
    assert attempt.trigger_message_id == "msg-1"
    assert attempt.action == "send_reply"
    assert attempt.audit_documents_json == '[{"path":"面试/岗位画像.md"}]'
    assert attempt.audit_tool_events_json == '[]'
    assert attempt.audit_summary == "查看岗位画像后判断需要先收敛问题。"
    assert attempt.codex_session_id == "session-1"
    assert attempt.codex_transcript_start_line == 2
    assert attempt.codex_transcript_end_line == 7
    assert attempt.final_reply_text == "先收敛问题（by明哥分身）"
    assert attempt.send_status == "sent"
    assert attempt.retry_count == 1
    assert attempt.reviewed_at is not None
    assert attempt.reviewer_feedback == "语气可以，但需要更具体"
    assert attempt.corrected_reply_text == "先明确负责人和时间点。"


def test_reply_attempt_records_oa_metadata(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="审批通知",
        trigger_message_id="msg-1",
        trigger_sender="工作通知",
        trigger_text="[Ding]张静提醒您审批他的录用申请",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="oa approval handled by dingtalk-oa-approval skill",
        codex_session_id="session-1",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_url="https://aflow.dingtalk.com/dingtalk/mobile/query/formService#/detail?procInstId=proc-1",
        oa_action="退回",
        oa_remark="请补充试用期考核标准和完整面试记录后再提交。",
        oa_action_result_json='{"errcode":0,"errmsg":"ok"}',
        send_status="skipped",
    )

    loaded = store.get_reply_attempt(attempt_id)

    assert loaded is not None
    assert loaded.action == "oa_approval"
    assert loaded.oa_process_instance_id == "proc-1"
    assert loaded.oa_task_id == "task-1"
    assert loaded.oa_url.startswith("https://aflow.dingtalk.com/")
    assert loaded.oa_action == "退回"
    assert loaded.oa_remark == "请补充试用期考核标准和完整面试记录后再提交。"
    assert loaded.oa_action_result_json == '{"errcode":0,"errmsg":"ok"}'


def test_reply_attempt_records_calendar_response_metadata(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Avery",
        trigger_message_id="msg-1",
        trigger_sender="Avery",
        trigger_text="[日程]",
        action="no_reply",
        sensitivity_kind="general",
        codex_reason="calendar invite handled",
        calendar_event_id="event-1",
        calendar_response_status="accepted",
        calendar_response_result_json='{"success":true}',
        send_status="skipped",
    )

    loaded = store.get_reply_attempt(attempt_id)

    assert loaded is not None
    assert loaded.calendar_event_id == "event-1"
    assert loaded.calendar_response_status == "accepted"
    assert loaded.calendar_response_result_json == '{"success":true}'


def test_get_latest_reply_attempt_for_trigger(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="failed",
    )
    second_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="dry_run",
    )

    attempt = store.get_latest_reply_attempt_for_trigger("cid-1", "msg-1")

    assert first_id != second_id
    assert attempt is not None
    assert attempt.id == second_id
    assert store.get_latest_reply_attempt_for_trigger("cid-1", "missing") is None


def test_history_query_skips_search_text_materialization_without_search():
    query, args = AutoReplyStore._history_items_query(
        send_statuses=None,
        query_text="",
        kinds=None,
        reply_channels=None,
        object_types=("reply", "meeting", "task"),
        created_since="",
    )

    assert query.count("iif(?1,") == 5
    assert args == [False, "reply", "meeting", "task"]


def test_history_query_has_indexes_for_correlated_reply_lookups(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with sqlite3.connect(store.path) as db:
        index_names = {
            row[0]
            for row in db.execute(
                "select name from sqlite_master where type='index'"
            ).fetchall()
        }
        query, args = store._history_items_query(
            send_statuses=None,
            query_text="",
            kinds=None,
            reply_channels=None,
            object_types=("replay", "wechat", "approval", "task", "meeting"),
            created_since="",
        )
        plan = [
            str(row[3])
            for row in db.execute(
                f"explain query plan {query} order by created_at desc, source_id desc, kind desc limit 1",
                args,
            ).fetchall()
        ]

    assert {
        "idx_actions_reply_oa_history",
        "idx_actions_reply_trigger_history",
        "idx_actions_reply_current_trigger",
        "idx_actions_reply_run_recovery",
        "idx_actions_history_public_id",
        "idx_tasks_claim_due",
    } <= index_names
    assert not any("scan process_attempts" in detail.lower() for detail in plan)
    assert not any("scan sent" in detail.lower() for detail in plan)


def test_history_treats_superseded_blocked_reply_as_skipped(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    blocked_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="stop_with_error",
        sensitivity_kind="general",
        send_status="blocked",
    )
    sent_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="sent",
    )

    blocked_items = store.list_history_items(send_statuses=("blocked",))
    skipped_items = store.list_history_items(send_statuses=("skipped",))

    assert blocked_id != sent_id
    assert [item.source_id for item in blocked_items] == []
    assert [item.source_id for item in skipped_items] == [blocked_id]


def test_history_groups_approval_retries_under_latest_attempt_for_filters_and_counts(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    reviewed_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-1:first",
        trigger_sender="Derek OA",
        trigger_text="付款申请",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="review",
        send_status="needs_human",
    )
    failed_retry_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-1:own-remark",
        trigger_sender="Derek OA",
        trigger_text="付款申请",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="review",
        send_status="failed",
    )
    completed_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-2:first",
        trigger_sender="Derek OA",
        trigger_text="合同申请",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-2",
        oa_task_id="task-2",
        oa_action="review",
        send_status="completed",
    )

    items = store.list_history_items(object_types=("approval",))
    failed_items = store.list_history_items(
        object_types=("approval",), send_statuses=("failed",)
    )
    needs_human_items = store.list_history_items(
        object_types=("approval",), send_statuses=("needs_human",)
    )

    assert len({reviewed_id, failed_retry_id, completed_id}) == 3
    assert [item.source_id for item in items] == [completed_id, failed_retry_id]
    assert [item.status for item in items] == ["completed", "failed"]
    assert [item.source_id for item in failed_items] == [failed_retry_id]
    assert needs_human_items == []
    assert store.count_history_items(object_types=("approval",)) == 2
    assert (
        store.count_history_items(
            object_types=("approval",), send_statuses=("failed",)
        )
        == 1
    )
    assert [
        item.source_id
        for item in store.list_history_items(
            limit=1, offset=0, object_types=("approval",)
        )
    ] == [completed_id]
    assert [
        item.source_id
        for item in store.list_history_items(
            limit=1, offset=1, object_types=("approval",)
        )
    ] == [failed_retry_id]


def test_history_approval_group_breaks_created_at_ties_with_larger_id(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    older_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-tie:first",
        trigger_sender="Derek OA",
        trigger_text="采购申请",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="proc-tie",
        send_status="commented",
    )
    newer_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-tie:retry",
        trigger_sender="Derek OA",
        trigger_text="采购申请",
        action="oa_approval",
        sensitivity_kind="general",
        oa_process_instance_id="proc-tie",
        send_status="failed",
    )
    with store._connect() as db:
        for attempt_id in (older_id, newer_id):
            update_action(db, "reply_attempts", attempt_id,
                          {"created_at": "2026-08-19 00:00:00"})

    items = store.list_history_items(object_types=("approval",))

    assert [item.source_id for item in items] == [newer_id]
    assert items[0].status == "failed"


def test_history_keeps_blocked_side_effects_visible_after_terminal_reply(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-memory", "msg-memory", "reply delivered")
    memory_id = store.record_reply_attempt(
        conversation_id="cid-memory",
        conversation_title="Strategy",
        trigger_message_id="msg-memory",
        trigger_sender="Derek",
        trigger_text="Remember this",
        action="memory_write",
        sensitivity_kind="general",
        send_status="blocked",
    )
    store.update_reply_attempt(memory_id, send_error="memory backend unavailable")
    oa_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="Approvals",
        trigger_message_id="msg-oa",
        trigger_sender="System",
        trigger_text="Pending approval",
        action="oa_approval",
        sensitivity_kind="general",
        send_status="blocked",
    )
    store.update_reply_attempt(oa_id, send_error="oa_task_not_current_user")
    store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="Approvals",
        trigger_message_id="msg-oa",
        trigger_sender="System",
        trigger_text="Pending approval",
        action="no_reply",
        sensitivity_kind="general",
        send_status="skipped",
    )

    blocked_items = store.list_history_items(send_statuses=("blocked",))

    assert {item.source_id for item in blocked_items} == {memory_id, oa_id}
    assert store.count_recoverable_blocked_reply_attempts() == 2


def test_history_preserves_superseded_meeting_failure(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="meeting-1",
        title="招聘站会",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-07-22T10:12:44+00:00",
        eligible_at="2026-07-22T10:22:44+00:00",
        status="pending",
    )
    failed_run_id = store.create_professional_run(job_id, session_id="meeting-session-failed", status="retry", error="Codex did not return a valid MeetingAlignmentDecision")
    sent_run_id = store.create_professional_run(job_id, session_id="meeting-session-sent", status="ready_to_send", error="")
    store.update_meeting_alignment_job(
        job_id,
        status="sent",
        target_kind="group",
        target_title="HR",
        final_message="会后对齐已发送。",
        send_result_json='{"status":"sent"}',
    )

    failed_items = store.list_history_items(send_statuses=("failed",))
    skipped_items = store.list_history_items(send_statuses=("skipped",))
    sent_items = store.list_history_items(send_statuses=("sent",))

    assert failed_run_id != sent_run_id
    assert [item.source_id for item in failed_items] == [failed_run_id]
    assert [item.source_id for item in skipped_items] == []
    assert [item.source_id for item in sent_items] == [sent_run_id]


def test_lists_reply_attempts_newest_first_with_limit(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
        codex_reason="direct ask",
    )
    second_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="HR",
        trigger_message_id="msg-2",
        trigger_sender="HR",
        trigger_text="张三转正怎么看？",
        action="no_reply",
        sensitivity_kind="internal_personnel",
        codex_reason="privacy",
    )

    all_attempts = store.list_reply_attempts()
    attempts = store.list_reply_attempts(limit=1)
    offset_attempts = store.list_reply_attempts(limit=1, offset=1)

    assert [attempt.id for attempt in all_attempts] == [second_id, first_id]
    assert [attempt.id for attempt in attempts] == [second_id]
    assert [attempt.id for attempt in offset_attempts] == [first_id]
    assert attempts[0].conversation_title == "HR"
    assert attempts[0].send_status == "pending"
    assert first_id != second_id


def test_lists_reply_attempts_since_timestamp(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    old_id = store.record_reply_attempt(
        conversation_id="cid-old",
        conversation_title="Old",
        trigger_message_id="msg-old",
        trigger_sender="Old",
        trigger_text="old",
        action="send_reply",
        sensitivity_kind="general",
    )
    new_id = store.record_reply_attempt(
        conversation_id="cid-new",
        conversation_title="New",
        trigger_message_id="msg-new",
        trigger_sender="New",
        trigger_text="new",
        action="send_reply",
        sensitivity_kind="general",
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", old_id,
                      {"created_at": "2026-06-04 00:00:00"})
        update_action(db, "reply_attempts", new_id,
                      {"created_at": "2026-06-05 00:00:00"})

    attempts = store.list_reply_attempts_since("2026-06-04 12:00:00")

    assert [attempt.id for attempt in attempts] == [new_id]


def test_lists_reviewed_reply_attempts_for_optimization(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    unreviewed_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="技术部",
        trigger_message_id="msg-1",
        trigger_sender="Xiaomin",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="send_reply",
        sensitivity_kind="general",
    )
    reviewed_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="Casey",
        trigger_message_id="msg-2",
        trigger_sender="Casey",
        trigger_text="明哥上会啦",
        action="send_reply",
        sensitivity_kind="general",
        draft_reply_text="收到，我现在进会。",
    )
    store.update_reply_attempt(
        reviewed_id,
        final_reply_text="收到，我现在进会。（by明哥分身）",
        send_status="sent",
    )
    store.record_reply_feedback(
        reviewed_id,
        feedback="不能代 Alex 声称正在进会",
        corrected_reply_text="我让明哥本人看一下。（by明哥分身）",
    )

    attempts = store.list_reviewed_reply_attempts()

    assert [attempt.id for attempt in attempts] == [reviewed_id]
    assert attempts[0].reviewer_feedback == "不能代 Alex 声称正在进会"
    assert attempts[0].corrected_reply_text == "我让明哥本人看一下。（by明哥分身）"
    assert unreviewed_id != reviewed_id


def test_lists_errors_newest_first_with_limit(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "codex", "invalid json")
    store.record_error("cid-2", "msg-2", "send", "authorization required")

    all_errors = store.list_errors()
    errors = store.list_errors(limit=1)
    offset_errors = store.list_errors(limit=1, offset=1)

    assert [error.kind for error in all_errors] == ["send", "codex"]
    assert len(errors) == 1
    assert errors[0].conversation_id == "cid-2"
    assert errors[0].message_id == "msg-2"
    assert errors[0].kind == "send"
    assert errors[0].detail == "authorization required"
    assert errors[0].created_at
    assert len(offset_errors) == 1
    assert offset_errors[0].kind == "codex"


def test_lists_run_delta_records_after_ids(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Friday",
        trigger_message_id="msg-1",
        trigger_sender="Avery",
        trigger_text="@Alex Chen 这个怎么处理？",
        action="no_reply",
        sensitivity_kind="general",
        send_status="skipped",
    )
    store.record_sent_reply("cid-1", "msg-1", "收到（by明哥分身）")
    store.record_error("cid-1", "msg-1", "codex", "invalid json")

    baseline_attempt_id = store.max_reply_attempt_id()
    baseline_sent_reply_id = store.max_sent_reply_id()
    baseline_error_id = store.max_error_id()

    second_attempt_id = store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="BA",
        trigger_message_id="msg-2",
        trigger_sender="Phina",
        trigger_text="@Alex Chen 需要看一下吗？",
        action="send_reply",
        sensitivity_kind="general",
        send_status="pending",
    )
    store.record_sent_reply("cid-2", "msg-2", "可以（by明哥分身）")
    store.record_error("cid-2", "msg-2", "read_messages", "dws timeout")

    assert baseline_attempt_id == first_attempt_id
    assert baseline_sent_reply_id > 0  # native Action IDs are shared across kinds
    assert baseline_error_id > 0
    assert [attempt.id for attempt in store.list_reply_attempts_after(baseline_attempt_id)] == [
        second_attempt_id
    ]
    assert [
        sent.trigger_message_id for sent in store.list_sent_replies_after(baseline_sent_reply_id)
    ] == ["msg-2"]
    assert [error.kind for error in store.list_errors_after(baseline_error_id)] == [
        "read_messages"
    ]


def test_org_user_profile_cache_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.upsert_org_user_profile(
        user_id="user-1",
        name="张三",
        open_dingtalk_id="open-1",
        manager_user_id="manager-1",
        department_ids={"dept-1", "dept-2"},
        title="产品负责人",
        manager_name="李四",
        department_names={"产品部", "售前解决方案部"},
        org_labels=["职务: 产品负责人", "岗位: 管理层"],
        has_subordinate=True,
    )

    profile = store.get_org_user_profile("user-1")

    assert profile is not None
    assert profile.user_id == "user-1"
    assert profile.name == "张三"
    assert profile.open_dingtalk_id == "open-1"
    assert profile.manager_user_id == "manager-1"
    assert profile.manager_name == "李四"
    assert profile.department_ids == {"dept-1", "dept-2"}
    assert profile.department_names == {"产品部", "售前解决方案部"}
    assert profile.title == "产品负责人"
    assert profile.org_labels == ["职务: 产品负责人", "岗位: 管理层"]
    assert profile.has_subordinate is True
    assert store.find_org_user_by_open_dingtalk_id("open-1").user_id == "user-1"
    assert [user.user_id for user in store.find_org_users_by_name("张三")] == ["user-1"]
    assert store.list_org_user_ids() == ["user-1"]


def test_org_cache_metadata_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.set_current_user_id("principal-user-1")
    store.set_hr_department_ids({"hr-dept-1"})

    assert store.get_current_user_id() == "principal-user-1"
    assert store.get_hr_department_ids() == {"hr-dept-1"}


def test_service_state_round_trip(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.set_service_state("dws_upgrade_checked_date", "2026-05-25")
    loaded = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert loaded.get_service_state("dws_upgrade_checked_date") == "2026-05-25"


def test_codex_capacity_pause_is_shared_and_expires(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    now = datetime.fromisoformat("2026-08-12T10:00:00+00:00")
    retry_at = "2026-08-12T10:30:00+00:00"

    assert store.open_codex_capacity_pause(retry_at=retry_at, now=now) is True
    assert store.open_codex_capacity_pause(retry_at=retry_at, now=now) is False
    assert store.active_codex_capacity_pause(now=now) == retry_at
    assert (
        store.active_codex_capacity_pause(
            now=datetime.fromisoformat("2026-08-12T10:30:00+00:00")
        )
        == ""
    )


def test_codex_capacity_failure_count_persists_until_successful_clear(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first = datetime.fromisoformat("2026-08-12T10:00:00+00:00")

    assert store.codex_capacity_failure_count() == 0
    assert store.open_codex_capacity_pause(
        retry_at="2026-08-12T10:30:00+00:00",
        now=first,
    )
    assert store.codex_capacity_failure_count() == 1
    assert store.open_codex_capacity_pause(
        retry_at="2026-08-12T11:31:00+00:00",
        now=datetime.fromisoformat("2026-08-12T10:31:00+00:00"),
    )
    assert store.codex_capacity_failure_count() == 2

    store.clear_codex_capacity_pause()

    assert store.codex_capacity_failure_count() == 0
    assert store.active_codex_capacity_pause(
        now=datetime.fromisoformat("2026-08-12T10:32:00+00:00")
    ) == ""


def test_resolve_errors_keeps_history_with_a_resolution(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error(None, None, "consumer", "temporary failure")
    [error] = store.list_errors()

    assert store.resolve_errors([error.id], resolution="recovered by queue retry") == 1
    resolved = store.list_errors()[0]

    assert resolved.resolved_at
    assert resolved.resolution == "recovered by queue retry"


def test_resolve_errors_recovered_by_wechat_reader_keeps_unrelated_errors_open(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("wechat", "", "wechat_reader_unavailable", "reader timed out")
    store.record_error("cid-1", "msg-1", "reply_task", "temporary failure")

    assert store.resolve_errors_recovered_by_wechat_reader() == 1

    errors = store.list_errors()
    recovered = next(error for error in errors if error.kind == "wechat_reader_unavailable")
    unrelated = next(error for error in errors if error.kind == "reply_task")
    assert recovered.resolved_at
    assert recovered.resolution == "recovered by successful WeChat reader cycle"
    assert unrelated.resolved_at == ""


def test_scheduled_service_incident_resolution_uses_task_history_index(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    statements = []
    original_open = store._open_connection

    def traced_connection():
        db = original_open()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, "_open_connection", traced_connection)
    store.resolve_errors_recovered_by_scheduled_service_command()
    statement = next(sql for sql in statements if "update actions as error_event" in sql)
    with store._connect() as db:
        plan = [row[3] for row in db.execute("explain query plan " + statement)]
    assert any("SEARCH run" in step and "scheduled_task_id=?" in step for step in plan), plan


def test_resolve_errors_recovered_by_scheduled_service_command(tmp_path: Path):
    from app.scheduled_config_storage import intern_scheduled_config
    from app.agent_cron.models import ScheduledTaskSnapshot
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = store.create_scheduled_task(
        name="Check DingTalk meetings",
        command="scan-meetings-once",
        cron_expression="* * * * *",
        timezone_name="UTC",
    )
    store.record_error(
        f"scheduled-task:{task.id}",
        "event-failed",
        "scheduled_task_service_command_failed",
        "scan-meetings-once failed",
    )
    store.record_error(
        "scheduled-task:3",
        "event-other",
        "scheduled_task_service_command_failed",
        "other task still failing",
    )
    with store._immediate_write_transaction() as db:
        snapshot_id = intern_scheduled_config(db, ScheduledTaskSnapshot.from_task(task).to_json())
        parts, parameters = error_set_parts({"created_at": "2026-09-10 12:00:00"})
        db.execute(
            f"update actions set {parts} where record_type='errors' and error_message_id='event-failed'",
            parameters,
        )
        _insert_scheduled_occurrence(db, {
            'event_id': 'event-success',
            'scheduled_task_id': task.id,
            'trigger_kind': 'scheduled',
            'scheduled_for': '2026-09-10T12:01:00+00:00',
            'first_scheduled_for': '2026-09-10T12:01:00+00:00',
            'dispatch_status': 'dispatched',
            'snapshot_id': snapshot_id,
            'execution_kind': 'service_command',
            'execution_id': 'scan-meetings-once',
            'dispatched_at': '2026-09-10 12:01:00',
        })

    assert store.resolve_errors_recovered_by_scheduled_service_command() == 1

    errors = store.list_errors()
    recovered = next(error for error in errors if error.message_id == "event-failed")
    unrelated = next(error for error in errors if error.message_id == "event-other")
    assert recovered.resolved_at
    assert recovered.resolution == (
        "recovered by later successful scheduled service command"
    )
    assert unrelated.resolved_at == ""


def test_resolve_errors_recovered_by_scheduled_reply_task(tmp_path: Path):
    from app.scheduled_config_storage import intern_scheduled_config
    from app.agent_cron.models import ScheduledTaskSnapshot
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task = store.create_scheduled_task(
        name="Sync meeting notes",
        prompt="$ceo-minutes-sync",
        cron_expression="* * * * *",
        timezone_name="UTC",
        runtime_id="codex_api",
    )
    assert store.enqueue_reply_task(
        conversation_id=f"scheduled-task:{task.id}",
        conversation_title="Sync meeting notes",
        single_chat=True,
        trigger_message_id="event-success",
        trigger_create_time="2026-09-18T12:00:00Z",
        trigger_sender="scheduler",
        trigger_text="sync",
        channel="scheduled",
    )
    with store._immediate_write_transaction() as db:
        snapshot_id = intern_scheduled_config(db, ScheduledTaskSnapshot.from_task(task).to_json())
        reply_task_id = db.execute(
            f"select id from ({store_module.REPLY_TASK_SQL}) where trigger_message_id='event-success'"
        ).fetchone()[0]
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'done', 'updated_at': '2026-09-18 12:02:00'},
            where_sql='and id=?',
            parameters=(reply_task_id,),
        )
        _insert_scheduled_occurrence(db, {
            'event_id': 'event-success',
            'scheduled_task_id': task.id,
            'trigger_kind': 'scheduled',
            'scheduled_for': '2026-09-18T12:00:00Z',
            'first_scheduled_for': '2026-09-18T12:00:00Z',
            'dispatch_status': 'dispatched',
            'snapshot_id': snapshot_id,
            'execution_kind': 'reply_task',
            'execution_id': str(reply_task_id),
            'dispatched_at': '2026-09-18 12:00:32',
        })
    store.record_error(
        f"scheduled-task:{task.id}",
        "event-success",
        "scheduled_task_execution_unavailable",
        "runtime route paused",
    )
    with store._connect() as db:
        parts, parameters = error_set_parts({"created_at": '2026-09-18 11:59:00'})
        db.execute(f"update actions set {parts} where record_type='errors' and error_message_id='event-success'", parameters)

    assert store.resolve_errors_recovered_by_scheduled_reply_task() == 1
    [error] = store.list_errors()
    assert error.resolved_at
    assert error.resolution == "recovered by later successful scheduled reply task"


def test_resolve_errors_recovered_by_scheduled_task_seed(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error(
        "",
        "",
        "scheduled_task_seed_failed",
        "scheduled consumer managed Skill unavailable: ceo-weekly-report",
    )
    store.record_error("", "", "reply_task", "unrelated failure")

    assert store.resolve_errors_recovered_by_scheduled_task_seed() == 1

    errors = store.list_errors()
    recovered = next(
        error for error in errors if error.kind == "scheduled_task_seed_failed"
    )
    unrelated = next(error for error in errors if error.kind == "reply_task")
    assert recovered.resolved_at
    assert recovered.resolution == (
        "recovered by later successful scheduled task seed"
    )
    assert unrelated.resolved_at == ""


def test_service_health_components_hold_current_component_state(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_service_health_component(
        "dws.read_robot_direct_messages",
        state="degraded",
        detail="DWS read failed with code PARAM_ERROR.",
    )

    [component] = store.list_service_health_components()
    assert component["component"] == "dws.read_robot_direct_messages"
    assert component["state"] == "degraded"

    store.set_service_health_component(
        "dws.read_robot_direct_messages",
        state="healthy",
    )
    assert store.list_service_health_components()[0]["state"] == "healthy"


def test_scheduler_health_keeps_recent_error_when_a_new_tick_succeeds(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.set_service_health_component(
        "agent-cron-scheduler", state="degraded", status="failed",
        detail="scheduler scan failed", latest_error="scheduler scan failed",
        latest_error_at="2026-09-08T12:00:00+00:00",
    )
    store.set_service_health_component(
        "agent-cron-scheduler", state="healthy", status="running",
        latest_tick_at="2026-09-08T12:01:00+00:00",
    )

    [component] = store.list_service_health_components()
    assert component["state"] == "healthy"
    assert component["status"] == "running"
    assert component["latest_tick_at"] == "2026-09-08T12:01:00+00:00"
    assert component["latest_error"] == "scheduler scan failed"
    assert component["latest_error_at"] == "2026-09-08T12:00:00+00:00"


def test_resolve_unresolved_errors_by_kind_after_successful_service_cycle(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("", "", "task_maintenance_weekly_okr_report", "session expired")
    store.record_error("", "", "task_maintenance_weekly_okr_report", "session expired")
    store.record_error("", "", "meeting-memory-write", "disk I/O error")
    store.record_error("", "", "other_component", "still failing")

    assert store.resolve_unresolved_errors_by_kind(
        "task_maintenance_weekly_okr_report",
        resolution="recovered by a later successful maintenance cycle",
    ) == 2

    errors = store.list_errors()
    okr_errors = [item for item in errors if item.kind == "task_maintenance_weekly_okr_report"]
    assert all(item.resolved_at for item in okr_errors)
    assert store.resolve_unresolved_errors_by_kind(
        "meeting_memory_write",
        resolution="recovered by a later Memory write cycle",
    ) == 1
    assert next(item for item in errors if item.kind == "other_component").resolved_at == ""


def test_redact_and_resolve_error_replaces_unsafe_historical_detail(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error(None, None, "follow_up", "outbound message body")
    [error] = store.list_errors()

    assert store.redact_and_resolve_error(
        error.id,
        detail="direct recipient rejected; message not sent",
        resolution="target is no longer available",
    )

    [resolved] = store.list_errors()
    assert resolved.detail == "direct recipient rejected; message not sent"
    assert resolved.resolved_at
    assert resolved.resolution == "target is no longer available"


def test_resolve_errors_recovered_by_later_terminal_reply_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "reply_task", "temporary failure")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Management",
        trigger_message_id="msg-1",
        trigger_sender="Avery",
        trigger_text="Please handle this.",
        action="agent_run",
        sensitivity_kind="general",
        send_status="completed",
    )
    store.update_reply_attempt(attempt_id, send_status="completed")

    assert store.resolve_errors_recovered_by_reply_attempts() == 1
    [resolved] = store.list_errors()
    assert resolved.resolved_at
    assert resolved.resolution == "recovered by later terminal reply attempt"


def test_resolve_errors_recovered_by_reply_attempts_keeps_unrelated_errors_open(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_error("cid-1", "msg-1", "reply_task", "temporary failure")
    store.record_reply_attempt(
        conversation_id="cid-2",
        conversation_title="Management",
        trigger_message_id="msg-2",
        trigger_sender="Avery",
        trigger_text="Please handle this.",
        action="agent_run",
        sensitivity_kind="general",
        send_status="completed",
    )

    assert store.resolve_errors_recovered_by_reply_attempts() == 0
    [error] = store.list_errors()
    assert error.resolved_at == ""


def test_completed_reply_task_resolves_trigger_error_and_closed_blocked_attempt(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Management",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-08-12 12:00:00",
        trigger_sender="Avery",
        trigger_text="Please handle this.",
    )
    task = store.claim_reply_task(1)
    assert task is not None
    store.record_error("cid-1", "msg-1", "reply_task", "temporary failure")
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="Management",
        trigger_message_id="msg-1",
        trigger_sender="Avery",
        trigger_text="Please handle this.",
        action="agent_run",
        sensitivity_kind="general",
        send_status="blocked",
    )
    store.update_reply_attempt(attempt_id, send_error="external material unavailable")
    store.complete_reply_task(
        task.id,
        expected_execution_generation=task.execution_generation,
    )

    assert store.resolve_errors_recovered_by_completed_reply_tasks() == 1
    assert store.resolve_closed_blocked_reply_attempts() == 1

    [error] = store.list_errors()
    attempt = store.get_reply_attempt(attempt_id)
    assert error.resolution == "recovered by completed reply task"
    assert attempt is not None
    assert attempt.send_status == "skipped"
    assert attempt.send_error == ""
    assert attempt.permission_action == REPLY_ATTEMPT_CLOSED_AFTER_REVIEW


def test_missing_service_state_returns_none(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    assert store.get_service_state("missing") is None


def test_list_oa_attempt_history_returns_newest_first(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    first_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="OA 群",
        trigger_message_id="msg-1",
        trigger_sender="Derek",
        trigger_text="审批 1",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="退回",
        draft_reply_text="请补材料",
        oa_process_instance_id="proc-1",
        oa_task_id="task-1",
        oa_action="退回",
        oa_remark="请补材料",
        send_status="commented",
    )
    second_id = store.record_reply_attempt(
        conversation_id="cid-oa",
        conversation_title="OA 群",
        trigger_message_id="msg-2",
        trigger_sender="Derek",
        trigger_text="审批 2",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="同意",
        draft_reply_text="同意",
        oa_process_instance_id="proc-1",
        oa_task_id="task-2",
        oa_action="同意",
        oa_remark="同意",
        send_status="skipped",
    )
    store.record_reply_attempt(
        conversation_id="cid-other",
        conversation_title="其他",
        trigger_message_id="msg-3",
        trigger_sender="Derek",
        trigger_text="审批 3",
        action="oa_approval",
        sensitivity_kind="internal_personnel",
        codex_reason="同意",
        draft_reply_text="同意",
        oa_process_instance_id="proc-2",
        send_status="skipped",
    )

    history = store.list_oa_attempt_history("proc-1")
    histories = store.list_oa_attempt_histories(
        ["proc-1", "proc-2", "proc-1", "missing", ""]
    )

    assert [attempt.id for attempt in history] == [second_id, first_id]
    assert store.list_oa_attempt_history("") == []
    assert {
        process_id: [attempt.id for attempt in attempts]
        for process_id, attempts in histories.items()
    } == {
        "proc-1": [second_id, first_id],
        "proc-2": [second_id + 1],
        "missing": [],
    }


def test_backfill_oa_audit_metadata_recovers_completed_agent_scan_attempt(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    trigger_message_id = "oa-pending:proc-1:revision-1"
    assert store.enqueue_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id=trigger_message_id,
        trigger_create_time="2026-08-06T05:41:00+00:00",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        oa_url=(
            "https://aflow.dingtalk.com/dingtalk/mobile/query/formService"
            "#/detail?procInstId=proc-1&taskId=task-1"
        ),
    )
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'oa_url': ''},
            where_sql="and id in (select task_id from task_inputs where trigger_message_id=?)",
            parameters=(trigger_message_id,),
        )
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id=trigger_message_id,
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        audit_summary="已审阅并评论要求补充材料。",
        send_status="needs_human",
    )

    assert store.backfill_oa_audit_metadata() == 1

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.oa_process_instance_id == "proc-1"
    assert attempt.oa_task_id == "task-1"
    assert attempt.oa_url.endswith("procInstId=proc-1&taskId=task-1")
    assert attempt.oa_action == "review"


def test_backfill_oa_audit_metadata_scans_before_opening_write_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    for number in range(2):
        trigger_message_id = f"oa-pending:proc-{number}:revision-1"
        assert store.enqueue_reply_task(
            conversation_id="oa_pending_scan",
            conversation_title="审批待办",
            single_chat=True,
            trigger_message_id=trigger_message_id,
            trigger_create_time="2026-08-06T05:41:00+00:00",
            trigger_sender="Derek OA",
            trigger_text="审批待办扫描",
            oa_url=(
                "https://aflow.dingtalk.com/dingtalk/mobile/query/formService"
                f"#/detail?procInstId=proc-{number}&taskId=task-{number}"
            ),
        )
        with store._connect() as db:
            _update_current_task8(
                db,
                'reply_tasks',
                {'oa_url': ''},
                where_sql="and id in (select task_id from task_inputs where trigger_message_id=?)",
                parameters=(trigger_message_id,),
            )
        store.record_reply_attempt(
            conversation_id="oa_pending_scan",
            conversation_title="审批待办",
            trigger_message_id=trigger_message_id,
            trigger_sender="Derek OA",
            trigger_text="审批待办扫描",
            action="agent_run",
            sensitivity_kind="general",
            send_status="needs_human",
        )

    read_transaction_states: list[bool] = []
    statements: list[str] = []
    original_connect = store._connect
    monkeypatch.setattr(store_module, "OA_AUDIT_METADATA_BACKFILL_BATCH_SIZE", 1)

    @contextmanager
    def traced_connect():
        with original_connect() as db:
            def trace(statement: str) -> None:
                statements.append(statement)
                if statement.lstrip().lower().startswith(
                    "select reply_attempts.id, reply_attempts.oa_action"
                ):
                    read_transaction_states.append(db.in_transaction)

            db.set_trace_callback(trace)
            try:
                yield db
            finally:
                db.set_trace_callback(None)

    monkeypatch.setattr(store, "_connect", traced_connect)

    assert store.backfill_oa_audit_metadata() == 2
    assert read_transaction_states == [False]
    assert sum(
        statement.strip().upper() == "BEGIN IMMEDIATE" for statement in statements
    ) == 2


def test_completed_oa_needs_human_attempt_is_closed_as_skipped(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-completed:revision-1",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-completed",
        send_status="needs_human",
    )

    assert store.resolve_completed_oa_needs_human_attempt(
        attempt_id,
        process_instance_id="proc-completed",
        process_result="agree",
    )
    assert not store.resolve_completed_oa_needs_human_attempt(
        attempt_id,
        process_instance_id="proc-completed",
        process_result="agree",
    )

    with store._connect() as db:
        row = db.execute(
            "select send_status, send_error, resolved_at, resolution "
            f"from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?",
            (attempt_id,),
        ).fetchone()
    assert row["send_status"] == "skipped"
    assert row["send_error"] == ""
    assert row["resolved_at"]
    assert "agree" in row["resolution"]
    assert "OA task reached terminal state" in row["resolution"]


@pytest.mark.parametrize("task_status", ["needs_human", "failed"])
def test_completed_oa_failed_attempt_closes_stale_task(
    tmp_path: Path,
    task_status: str,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    assert store.enqueue_reply_task(
        conversation_id="oa_pending_scan:proc-completed",
        conversation_title="审批待办",
        single_chat=True,
        trigger_message_id="oa-pending:proc-completed:revision-2",
        trigger_create_time="2026-09-21 09:00:00",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        execution_generation="initial",
        business_object_key="oa:proc-completed:task-completed",
    )
    task = store.claim_reply_task(1)
    assert task is not None
    with store._immediate_write_transaction() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': task_status, 'error': 'codex_result_invalid'},
            where_sql='and id=?',
            parameters=(task.id,),
        )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-completed",
        oa_task_id="task-completed",
        send_status="failed",
    )
    with store._immediate_write_transaction() as db:
        update_action(db, "reply_attempts", attempt_id,
                      {"send_error": "codex_result_invalid"})

    assert [row.id for row in store.list_open_oa_needs_human_attempts()] == [
        attempt_id
    ]
    assert store.resolve_completed_oa_needs_human_attempt(
        attempt_id,
        process_instance_id="proc-completed",
        process_result="agree",
    )

    assert store.get_reply_attempt(attempt_id).send_status == "skipped"
    assert store.get_reply_task(task.id).status == "done"
    assert store.get_reply_task(task.id).error == ""


def test_completed_oa_closure_requires_matching_process_identity(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    attempt_id = store.record_reply_attempt(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        trigger_message_id="oa-pending:proc-expected:revision-1",
        trigger_sender="Derek OA",
        trigger_text="审批待办扫描",
        action="agent_run",
        sensitivity_kind="general",
        oa_process_instance_id="proc-expected",
        send_status="needs_human",
    )

    assert not store.resolve_completed_oa_needs_human_attempt(
        attempt_id,
        process_instance_id="proc-other",
        process_result="agree",
    )
    assert store.get_reply_attempt(attempt_id).send_status == "needs_human"


def test_setup_wizard_step_state_round_trips(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.upsert_setup_wizard_step(
        step_id="mcp",
        status="done",
        summary="Codex config contains memory_connector",
        manual_confirmed_by="",
    )
    row = store.get_setup_wizard_step("mcp")

    assert row["step_id"] == "mcp"
    assert row["status"] == "done"
    assert row["summary"] == "Codex config contains memory_connector"
    assert row["manual_confirmed_by"] == ""
    assert row["updated_at"]


def test_setup_wizard_event_history_round_trips(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    event_id = store.record_setup_wizard_event(
        step_id="mcp",
        action_id="setup_mcp",
        status="done",
        summary="wrote config",
        evidence_json='{"codex_config": "/tmp/config.toml"}',
        stdout_excerpt="setup-memory-connector codex_config=/tmp/config.toml",
        stderr_excerpt="",
    )
    events = store.list_setup_wizard_events("mcp")

    assert event_id > 0
    assert len(events) == 1
    assert events[0]["step_id"] == "mcp"
    assert events[0]["action_id"] == "setup_mcp"
    assert events[0]["evidence_json"] == '{"codex_config": "/tmp/config.toml"}'


def test_setup_wizard_event_history_filters_exact_action_id(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_setup_wizard_event(
        step_id="wechat_connection",
        action_id="connect_wechat",
        status="done",
        evidence_json='{"full_disk_access_prompted": true}',
    )
    store.record_setup_wizard_event(
        step_id="wechat_connection",
        action_id="check_wechat_connection",
        status="done",
        evidence_json='{"reader_ready": true}',
    )

    events = store.list_setup_wizard_events(
        "wechat_connection",
        action_id="connect_wechat",
        limit=1,
    )

    assert len(events) == 1
    assert events[0]["action_id"] == "connect_wechat"
    assert json.loads(events[0]["evidence_json"])["full_disk_access_prompted"] is True


def test_setup_wizard_running_event_is_not_finished(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    store.record_setup_wizard_event(
        step_id="mcp",
        action_id="setup_mcp",
        status="running",
    )
    events = store.list_setup_wizard_events("mcp")

    assert events[0]["started_at"]
    assert events[0]["finished_at"] == ""


def test_partial_legacy_setup_event_source_requires_explicit_migration(tmp_path):
    db_path = tmp_path / "worker.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            create table setup_wizard_events (
                id integer primary key autoincrement,
                step_id text not null,
                action_id text not null,
                status text not null,
                summary text not null default '',
                evidence_json text not null default '{}',
                stdout_excerpt text not null default '',
                stderr_excerpt text not null default '',
                started_at text not null default current_timestamp,
                finished_at text not null default current_timestamp
            );
            """
        )
    assert_store_reopen_rejected_without_mutation(db_path)


def test_setup_wizard_steps_list_has_stable_tie_breaker(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.upsert_setup_wizard_step(step_id="mcp", status="done", summary="ok")
    store.upsert_setup_wizard_step(step_id="preflight", status="done", summary="ok")
    with store._immediate_write_transaction() as db:
        update_state_rows(db, 'setup_wizard_steps', {'updated_at': '2026-06-12 12:00:00'})

    rows = store.list_setup_wizard_steps()

    assert [row["step_id"] for row in rows] == ["mcp", "preflight"]


def test_reply_attempt_round_trips_mail_action_state(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")

    attempt_id = store.record_reply_attempt(
        conversation_id="cid-1",
        conversation_title="HR",
        trigger_message_id="msg-1",
        trigger_sender="Alan",
        trigger_text="审批并回复邮件",
        action="send_reply",
        sensitivity_kind="general",
        mail_mailbox="derek@example.com",
        mail_message_id="mail-1",
        mail_subject="Re: 评奖结果",
        mail_reply_text="确认无误，可以发布。",
    )
    store.update_reply_attempt(
        attempt_id,
        mail_action_result_json='{"success": true}',
    )

    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.mail_mailbox == "derek@example.com"
    assert attempt.mail_message_id == "mail-1"
    assert attempt.mail_subject == "Re: 评奖结果"
    assert attempt.mail_reply_text == "确认无误，可以发布。"
    assert attempt.mail_action_result_json == '{"success": true}'


def test_sent_reply_exists_matches_exact_conversation_and_trigger(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.record_sent_reply("cid-1", "msg-1", "Sent")

    assert store.sent_reply_exists("cid-1", "msg-1") is True
    assert store.sent_reply_exists("cid-1", "msg-other") is False
    assert store.sent_reply_exists("cid-other", "msg-1") is False


def test_channel_login_claim_requires_owner_to_finalize_and_persists_safe_state(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    now = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)

    claimed, reserved = store.claim_channel_login_request(
        channel="dingtalk",
        reason_code="live_probe_auth_failed",
        now=now,
        suppression_seconds=3600,
        reservation_owner="owner-1",
    )

    assert claimed is True
    assert reserved["status"] == "starting"
    assert (
        store.update_claimed_channel_login_request(
            channel="dingtalk",
            reservation_owner="owner-2",
            state={"status": "running", "pid": 99},
        )
        is False
    )
    assert store.update_claimed_channel_login_request(
        channel="dingtalk",
        reservation_owner="owner-1",
        state={"status": "failed", "exited_at": now.isoformat()},
    )
    state = json.loads(
        store.get_service_state("channel_login_request:dingtalk") or "{}"
    )
    assert state == {
        "checked_at": now.isoformat(),
        "exited_at": now.isoformat(),
        "reason_code": "live_probe_auth_failed",
        "started_at": now.isoformat(),
        "status": "failed",
    }


def test_removed_runtime_tables_modules_and_apis_are_absent(tmp_path: Path) -> None:
    from app.worker import DingTalkAutoReplyWorker

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    with store._connect() as db:
        tables = {
            str(row[0])
            for row in db.execute(
                "select name from sqlite_master where type='table'"
            ).fetchall()
        }

    assert "runs" in tables
    assert "agent_runs" not in tables
    assert "universal_plan_executions" not in tables
    assert "universal_action_executions" not in tables
    assert store.get_service_state("dws_auth_backup") is None
    assert not hasattr(store, "create_universal_plan_execution")
    assert not hasattr(store, "claim_universal_action_execution")
    assert not hasattr(DingTalkAutoReplyWorker, "execute_universal_send_reply")
    assert importlib.util.find_spec("app.universal_consumer") is None
    assert importlib.util.find_spec("app.universal_plan") is None




_LEGACY_EFFECT_SUCCESS_CASES: dict[str, tuple[str, dict[str, object]]] = {
    "send_reply": (
        "sent",
        {"action_kind": "send_reply", "outcome": "delivered"},
    ),
    "ask_clarifying_question": (
        "sent",
        {"action_kind": "ask_clarifying_question", "outcome": "delivered"},
    ),
    "oa_approval": (
        "completed",
        {
            "action": "同意",
            "outcome": "applied",
            "process_instance_id": "process-1",
            "task_id": "task-1",
        },
    ),
    "mail_reply": ("sent", {"success": True}),
    "calendar_response": ("calendar", {"success": True}),
    "dws_markdown_document_reply": (
        "document",
        {
            "node_id": "node-1",
            "url": "https://alidocs.dingtalk.com/i/nodes/node-1",
            "delivery": {"messageId": "message-1"},
        },
    ),
    "dws_message_reaction": ("reacted", {"reactionId": "reaction-1"}),
    "queue_okr_review": (
        "completed",
        {
            "action_kind": "queue_okr_review",
            "outcome": "okr_review_queued_and_acknowledged",
        },
    ),
    "memory_write": (
        "completed",
        {
            "episode_uuid": "episode-1",
            "processing_status": "completed",
            "duplicate": False,
        },
    ),
}
















def test_recover_orphaned_processing_reply_tasks_is_generation_aware(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_ids = []
    for index in range(3):
        store.enqueue_reply_task(
            conversation_id=f"cid-{index}",
            conversation_title=f"Conversation {index}",
            single_chat=False,
            trigger_message_id=f"msg-{index}",
            trigger_create_time="2026-07-30 09:00:00",
            trigger_sender="Derek",
            trigger_text="handle this",
        )
        task_ids.append(store.claim_reply_tasks(1)[0].id)

    running_task = store.get_reply_task(task_ids[1])
    unknown_task = store.get_reply_task(task_ids[2])
    assert running_task is not None and unknown_task is not None
    _claim_audit_run(store,
        running_task.id,
        running_task.execution_generation,
        owner="running-worker",
    )
    unknown_run = _claim_audit_run(store,
        unknown_task.id,
        unknown_task.execution_generation,
        owner="unknown-worker",
    ).run
    store.fail_agent_run(
        unknown_run.id,
        {"code": "effect_completion_missing"},
        owner="unknown-worker",
    )

    recovered = store.recover_orphaned_processing_reply_tasks(limit=10)

    assert [task.id for task in recovered] == [task_ids[0]]
    assert store.get_reply_task(task_ids[0]).status == "pending"
    assert store.get_reply_task(task_ids[0]).attempts == 0
    assert store.get_reply_task(task_ids[1]).status == "processing"
    assert store.get_reply_task(task_ids[2]).status == "processing"


def test_stale_processing_task_ignores_future_lease_without_recent_heartbeat(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    _claim_audit_run(store, task.id, task.execution_generation, owner="stale-worker")

    with sqlite3.connect(store.path) as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'locked_at': '2026-07-20 10:00:00', 'updated_at': '2026-07-20 10:00:00'},
            where_sql='and id=?',
            parameters=(task.id,),
        )
        db.execute(
            "update runs set updated_at='2026-07-20 10:00:00', lease_expires_at='2099-01-01 00:00:00' where task_id=?",
            (task.id,),
        )

    stale = store.list_stale_processing_reply_tasks(60)

    assert [item.id for item in stale] == [task.id]

    store.requeue_reply_task(
        task.id,
        "stale_agent_turn_recovery",
        expected_execution_generation=task.execution_generation,
    )

    assert store.get_reply_task(task.id).status == "pending"
    assert store.get_agent_run_for_turn(
        task.id,
        task.execution_generation,
        role=AgentRole.AUDIT,
        proposal_revision=0,
        turn_attempt=0,
    ).status == "failed"


def test_stale_processing_task_with_completed_turn_is_recoverable(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="active-worker",
    ).run
    store.complete_agent_run(
        run.id,
        {"outcome": "proposal", "summary": "Audit is the next turn."},
        owner="active-worker",
    )
    with sqlite3.connect(store.path) as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {},
            update_sql={'locked_at': "datetime('now', '-11 minutes')"},
            where_sql='and id=?',
            parameters=(task.id,),
        )

    stale = store.list_stale_processing_reply_tasks(600)
    assert [item.id for item in stale] == [task.id]










def test_service_restart_resumes_completed_turn_without_replaying_it(
    tmp_path: Path,
) -> None:
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    run = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="stopped-worker",
    ).run
    store.complete_agent_run(
        run.id,
        {"outcome": "no_action", "summary": "Nothing remains."},
        owner="stopped-worker",
    )

    recovered = store.resume_completed_agent_turns_after_service_restart()

    assert [item.id for item in recovered] == [task.id]
    recovered_task = store.get_reply_task(task.id)
    recovered_run = store.get_agent_run(run.id)
    assert recovered_task is not None and recovered_task.status == "pending"
    assert recovered_task.execution_generation == task.execution_generation
    assert recovered_task.error == "service_restart_after_completed_turn"
    assert recovered_run is not None and recovered_run.status == "completed"


def test_requeue_failed_work_summary_input_is_scoped_to_failed_record(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    input_id = store.enqueue_work_summary_input(
        "local_file",
        "file:reference",
        "{}",
    )
    [claimed] = store.claim_work_summary_inputs(1)
    store.mark_work_summary_input_failed(claimed.id, "validation failed")

    assert store.requeue_failed_work_summary_input(
        input_id,
        "retry_after_reviewed_root_cause_fix",
    )
    with store._connect() as db:
        row = db.execute(
            f"select status, attempts, error, available_at from ({WORK_SUMMARY_INPUT_SQL}) where id=?",
            (input_id,),
        ).fetchone()
    assert dict(row) == {
        "status": "pending",
        "attempts": 0,
        "error": "retry_after_reviewed_root_cause_fix",
        "available_at": "",
    }
    assert not store.requeue_failed_work_summary_input(input_id, "again")


def test_terminal_work_summary_input_resolves_its_own_error(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    input_id = store.enqueue_work_summary_input("local_file", "file:reference", "{}")
    [claimed] = store.claim_work_summary_inputs(1)
    store.record_error(
        "work_summary_input",
        str(input_id),
        "task_agent",
        "validation failed",
    )
    store.mark_work_summary_input_skipped(claimed.id, "no usable material")

    assert store.resolve_errors_recovered_by_terminal_work_summary_inputs() == 1
    with store._connect() as db:
        row = db.execute(f"select resolved_at from ({store_module.ERROR_SQL})").fetchone()
    assert row is not None
    assert row["resolved_at"]


@pytest.mark.parametrize(
    "incident_time, expected_count",
    [
        ("2026-10-15 06:59:59", 1),
        ("2026-10-15 07:00:00", 1),
        ("2026-10-15 07:00:01", 0),
    ],
)
def test_terminal_work_summary_incident_recovery_uses_incident_time(
    tmp_path, incident_time, expected_count
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    input_id = store.enqueue_work_summary_input("local_file", "file:reference", "{}")
    [claimed] = store.claim_work_summary_inputs(1)
    store.record_error("work_summary_input", str(input_id), "task_agent", "validation failed")
    store.mark_work_summary_input_skipped(claimed.id, "no usable material")
    with store._connect() as db:
        _update_current_task8(
            db,
            'work_summary_inputs',
            {'updated_at': '2026-10-15 07:00:00'},
            where_sql='and id=?',
            parameters=(input_id,),
        )
        parts, parameters = error_set_parts({"created_at": incident_time})
        db.execute(f"update actions set {parts} where record_type='errors'", parameters)

    assert store.resolve_errors_recovered_by_terminal_work_summary_inputs() == expected_count
    with store._connect() as db:
        row = db.execute(f'select resolved_at from ({ERROR_SQL})').fetchone()
    assert bool(row["resolved_at"]) is (expected_count == 1)


def test_terminal_work_input_compacts_atomically_with_adopted_project(tmp_path: Path):
    import hashlib

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    payload = json.dumps({"source": {"type": "local_file", "ref": "file#v1",
                                     "created_at": "2026-01-01T09:00:00Z"},
                          "summary": "private source body"})
    input_id = store.enqueue_work_summary_input("local_file", "file#v1", payload)
    [claimed] = store.claim_work_summary_inputs(1)
    assert store.enqueue_work_summary_input(
        "local_file", "file#v1", '{"summary":"later scan while processing"}'
    ) == input_id
    assert store.get_work_summary_input(input_id).payload_json == payload
    with pytest.raises(RuntimeError, match="rollback"):
        with store.task_agent_domain_apply_transaction() as db:
            insert_business(db, 'work_project', {'title': 'adopted', 'goal': ''})
            store.mark_work_summary_input_done(claimed.id, _db=db)
            raise RuntimeError("rollback")
    assert store.get_work_summary_input(input_id).payload_json == payload
    with store._connect() as db:
        assert db.execute("select count(*) from business where record_type='work_project'").fetchone()[0] == 0

    with store.task_agent_domain_apply_transaction() as db:
        insert_business(db, 'work_project', {'title': 'adopted', 'goal': ''})
        store.mark_work_summary_input_done(claimed.id, _db=db)
    done = store.get_work_summary_input(input_id)
    assert done.status == "done"
    assert done.payload_json == "{}"
    assert done.source_created_at == "2026-01-01T09:00:00Z"
    assert done.body_sha256 == hashlib.sha256(payload.encode()).hexdigest()
    assert done.body_bytes == len(payload.encode())
    with store._connect() as db:
        assert db.execute("select count(*) from business where record_type='work_project'").fetchone()[0] == 1

    duplicate = store.enqueue_work_summary_input("local_file", "file#v1", '{"summary":"later"}')
    assert duplicate == input_id
    assert store.get_work_summary_input(input_id) == done
    log = next(log for log in store.list_operation_logs(source_tables=("work_summary_inputs",))
               if log.source_id == input_id)
    assert "private source body" not in log.summary
    assert "file#v1" in log.summary
    assert done.body_sha256 in log.summary


def test_skipped_compacts_but_failed_and_requeued_keep_exact_input(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    payload = '{"source":{"type":"local_file","ref":"retry"},"summary":"exact"}'
    failed_id = store.enqueue_work_summary_input("local_file", "retry", payload)
    skipped_id = store.enqueue_work_summary_input("local_file", "skip", payload)
    [failed, skipped] = store.claim_work_summary_inputs(2)
    store.mark_work_summary_input_failed(failed.id, "invalid")
    store.mark_work_summary_input_skipped(skipped.id, "irrelevant")
    assert store.get_work_summary_input(failed_id).payload_json == payload
    assert store.get_work_summary_input(skipped_id).payload_json == "{}"
    assert store.requeue_failed_work_summary_input(failed_id, "reviewed")
    assert store.get_work_summary_input(failed_id).payload_json == payload


def test_work_input_provenance_schema_is_checked_on_store_reopen(tmp_path: Path):
    path = tmp_path / "malformed-provenance.sqlite3"
    store = AutoReplyStore(path)
    store.enqueue_work_summary_input("local_file", "source", "{}")
    with store._connect() as db:
        db.execute("alter table work_summary_inputs rename column body_compacted to malformed_body_compacted")
    assert store._schema_is_current() is False
    assert_store_reopen_rejected_without_mutation(path)


@pytest.mark.parametrize("terminal_status", ["done", "skipped"])
def test_late_work_input_failure_or_retry_cannot_reopen_compacted_input(
    tmp_path: Path, terminal_status: str
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    payload = '{"source":{"type":"local_file","ref":"file#v1"},"summary":"original"}'
    input_id = store.enqueue_work_summary_input("local_file", "file#v1", payload)
    [claimed] = store.claim_work_summary_inputs(1)
    if terminal_status == "done":
        store.mark_work_summary_input_done(claimed.id)
    else:
        store.mark_work_summary_input_skipped(claimed.id, "no actionable task")
    terminal = store.get_work_summary_input(input_id)
    assert terminal.payload_json == "{}"
    assert terminal.body_compacted == 1

    store.mark_work_summary_input_failed(input_id, "late failure")
    store.schedule_work_summary_input_retry(
        input_id, "late retry", available_at="2026-12-01 00:00:00"
    )
    store.defer_work_summary_input_for_capacity(
        input_id, "late capacity", available_at="2026-12-01 00:00:00"
    )
    assert not store.requeue_failed_work_summary_input(input_id, "late manual requeue")
    assert store.get_work_summary_input(input_id) == terminal
    assert store.claim_work_summary_inputs(1) == []


def test_reopening_current_core_never_recreates_a_second_trajectory_copy(tmp_path: Path):
    db_path = tmp_path / 'core-native-only.sqlite3'
    store = AutoReplyStore(db_path)
    run = _claimed_runtime_agent_run(store)
    store_module._INITIALIZED_STORE_PATHS.discard(db_path.resolve())
    reopened = AutoReplyStore(db_path)
    assert reopened.get_agent_run(run.id).tool_events == []
    with reopened._connect() as db:
        assert_no_core_native_storage(db)



def test_current_source_with_retired_result_body_is_rejected_without_erasing_payload(tmp_path: Path):
    path = tmp_path / "undrained-retired-result.sqlite3"
    AutoReplyStore(path)
    inventory = json.loads((Path(__file__).parents[1] / "docs/superpowers/specs/2026-10-11-database-redesign-phase4-inventory.json").read_text())
    with sqlite3.connect(path) as db:
        db.execute(inventory["tables"]["agent_runs"]["sql"])
        db.execute("insert into agent_runs(reply_task_id,execution_generation,role,status,final_result_json) values(1,'original','consumer','completed',?)",
                   (json.dumps({"type": "item.completed", "call_id": "kept"}),))
    assert_store_reopen_rejected_without_mutation(path)



def test_current_runtime_keeps_execution_shape_without_result_cache(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "execution-shape.sqlite3")
    with store._connect() as db:
        run_columns = {row["name"] for row in db.execute("pragma table_info(runs)")}
        attempt_columns = {row["name"] for row in db.execute("pragma table_info(runtime_attempts)")}
        assert {"lease_owner", "lease_expires_at"} <= run_columns
        assert {"attempt_purpose", "validation_retry_policy_id",
                "validation_result_schema_id", "result_schema_id"} <= attempt_columns
        assert_no_core_native_storage(db)
    assert store._schema_is_current() is True



def test_current_schema_rejects_meeting_memory_lease_index(tmp_path):
    path = tmp_path / "damaged.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute("drop index idx_actions_td_meeting_lease")
        before = tuple(db.iterdump())
    with pytest.raises(sqlite3.IntegrityError, match="malformed"):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before


@pytest.mark.parametrize('column', ['td_lease_owner', 'td_lease_expires_at', 'td_started_at'])
def test_current_meeting_memory_lease_declaration_rejects_malformed_column(tmp_path: Path, column):
    path = tmp_path / 'meeting-memory-malformed-lease.sqlite3'
    store = AutoReplyStore(path)
    with store._connect() as db:
        db.execute(f'alter table actions rename column "{column}" to "malformed_{column}"')
    assert store._schema_is_current() is False
    assert_store_reopen_rejected_without_mutation(path)


def test_terminalize_exhausted_pending_reply_tasks_closes_restart_retry_loop(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "retry-deadline.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-retry-deadline",
        conversation_title="Operations",
        single_chat=False,
        trigger_message_id="msg-retry-deadline",
        trigger_create_time="2026-08-11 05:00:00",
        trigger_sender="Avery",
        trigger_text="Please handle.",
        trigger_message_json="{}",
    )
    task = store.get_reply_task_for_message("cid-retry-deadline", "msg-retry-deadline")
    assert task is not None
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'pending', 'attempts': 3, 'error': 'consumer_retry_exhausted'},
            where_sql='and id=?',
            parameters=(task.id,),
        )

    closed = store.terminalize_exhausted_pending_reply_tasks(max_attempts=3)

    assert closed == [task.id]
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error.endswith("retry_deadline_exhausted")


def test_recover_stale_runtime_attempts_closes_terminal_parent_and_expired_lease(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "stale-runtime-attempts.sqlite3")
    store.enqueue_reply_task(
        conversation_id="stale-runtime-conversation",
        conversation_title="Operations",
        single_chat=False,
        trigger_message_id="stale-runtime-message",
        trigger_create_time="2026-08-27 05:00:00",
        trigger_sender="Derek",
        trigger_text="stale runtime test",
        trigger_message_json="{}",
    )
    task = store.get_reply_task_for_message(
        "stale-runtime-conversation", "stale-runtime-message"
    )
    assert task is not None
    with store._connect() as db:
        _insert_run_row(db, {
                'task_id': task.id,
                'execution_generation': 'generation',
                'role': 'consumer',
                'status': 'completed',
            },
        )
        parent_id = int(db.execute("select last_insert_rowid()").fetchone()[0])
        _insert_runtime_attempt_row(db, {
                'parent_id': parent_id,
                'workload_kind': 'agent_run',
                'workload_key': str(parent_id),
                'attempt_number': 1,
                'route_name': 'codex_oauth',
                'runtime_kind': 'codex_cli',
                'credential_mode': 'oauth',
                'model': 'model',
                'status': 'running',
                'lease_expires_at': '',
                'updated_at': '2020-01-01 00:00:00',
            },
        )
        _insert_runtime_attempt_row(db, {
                'workload_kind': 'meeting',
                'workload_key': 'stale-meeting',
                'attempt_number': 1,
                'route_name': 'codex_oauth',
                'runtime_kind': 'codex_cli',
                'credential_mode': 'oauth',
                'model': 'model',
                'status': 'running',
                'lease_owner': 'stale-owner',
                'lease_expires_at': '2020-01-01 00:00:00',
            },
        )

    closed = store.recover_stale_runtime_attempts(
        stale_after_seconds=60,
        now="2026-08-27 06:00:00",
    )

    assert closed == 2
    with store._connect() as db:
        rows = db.execute(
            f'select status, failure_code from ({RUNTIME_ATTEMPT_SQL}) order by id'
        ).fetchall()
    assert [(row["status"], row["failure_code"]) for row in rows] == [
        ("failed", "runtime_lease_expired"),
        ("failed", "runtime_lease_expired"),
    ]


def test_recover_unstarted_runtime_operation_attempts_releases_short_stale_claim(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "unstarted-runtime-attempt.sqlite3")
    workload_key = "wechat_memory_import_job:51"
    _seed_runtime_operation_parent(store, "memory", workload_key)
    claimed = store.claim_runtime_operation_attempt(
        "memory",
        workload_key,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.6-sol",
        owner="routed-codex-owner",
        lease_seconds=1800,
        now="2026-09-14T23:00:00+00:00",
    )
    store.mark_agent_runtime_attempt_running_once(
        claimed.id,
        owner="routed-codex-owner",
        lease_seconds=1800,
        now="2026-09-14T23:00:00+00:00",
    )

    assert store.recover_unstarted_runtime_operation_attempts(
        stale_after_seconds=60,
        now="2026-09-14T23:01:01+00:00",
    ) == 1

    recovered = store.get_agent_runtime_attempt(claimed.id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.failure_code == "runtime_start_stalled"
    assert recovered.lease_owner == ""
    assert recovered.lease_expires_at == ""


def test_recover_unstarted_runtime_operation_attempts_keeps_session_evidence(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "started-runtime-attempt.sqlite3")
    workload_key = "wechat_memory_import_job:52"
    _seed_runtime_operation_parent(store, "memory", workload_key)
    claimed = store.claim_runtime_operation_attempt(
        "memory",
        workload_key,
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.6-sol",
        owner="routed-codex-owner",
        lease_seconds=1800,
        now="2026-09-14T23:00:00+00:00",
    )
    store.mark_agent_runtime_attempt_running_once(
        claimed.id,
        owner="routed-codex-owner",
        lease_seconds=1800,
        now="2026-09-14T23:00:00+00:00",
    )
    store.set_agent_runtime_attempt_session(
        claimed.id,
        "session-52",
        "codex_session:session-52",
        owner="routed-codex-owner",
        now="2026-09-14T23:00:01+00:00",
    )

    assert store.recover_unstarted_runtime_operation_attempts(
        stale_after_seconds=60,
        now="2026-09-14T23:01:01+00:00",
    ) == 0
    assert store.get_agent_runtime_attempt(claimed.id).status == "running"


def test_recover_stale_runtime_attempts_immediately_closes_terminal_meeting_parent(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "terminal-meeting-runtime.sqlite3")
    job_id = store.upsert_meeting_alignment_job(
        meeting_id="terminal-meeting",
        title="Meeting",
        source_json="{}",
        participants_json="[]",
        ended_at="2026-08-27T05:00:00+00:00",
        eligible_at="2026-08-27T05:01:00+00:00",
        status="pending",
    )
    store.claim_meeting_alignment_jobs(limit=1, now="2026-08-27T05:01:00+00:00")
    run_id = store.claim_professional_run(job_id)
    attempt = store.claim_runtime_operation_attempt(
        "meeting",
        str(run_id),
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.6-sol",
        owner="meeting-owner",
        lease_seconds=3600,
        now="2026-08-27T05:01:00+00:00",
    )
    store.mark_agent_runtime_attempt_running_once(
        attempt.id,
        owner="meeting-owner",
        lease_seconds=3600,
        now="2026-08-27T05:01:00+00:00",
    )
    store.finish_professional_run(run_id, status="retry", error='{"kind":"runtime_session_conflict"}')

    assert store.recover_stale_runtime_attempts(
        stale_after_seconds=3600,
        now="2026-08-27T05:02:00+00:00",
    ) == 1
    recovered = store.get_agent_runtime_attempt(attempt.id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.failure_code == "runtime_parent_requeued"






def test_current_schema_rejects_missing_agent_run_recovery_index(tmp_path):
    path = tmp_path / "damaged.sqlite3"
    AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        db.execute("drop index idx_actions_reply_run_recovery")
        before = tuple(db.iterdump())
    with pytest.raises(sqlite3.IntegrityError, match="malformed"):
        AutoReplyStore(path)
    with sqlite3.connect(path) as db:
        assert tuple(db.iterdump()) == before


def test_discard_unstarted_agent_run_removes_only_a_fresh_claim(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    run = _claimed_runtime_agent_run(store)
    task_id, generation = run.reply_task_id, run.execution_generation

    # Another owner cannot discard someone else's claim.
    assert store.discard_unstarted_agent_run(run.id, owner="someone-else") is False
    assert store.get_agent_run(run.id) is not None

    assert store.discard_unstarted_agent_run(run.id, owner="runtime-attempt") is True
    assert store.get_agent_run(run.id) is None
    assert store.list_agent_runs_for_task_generation(task_id, generation) == []
    # The turn number is handed back: the next claim reuses attempt 0.
    assert (
        store.next_agent_run_turn_attempt(
            task_id, generation, role=AgentRole.AUDIT, proposal_revision=0
        )
        == 0
    )
    with store._connect() as db:
        assert (
            db.execute(
                f"select count(*) from ({RUN_STATE_EVENT_SQL}) where agent_run_id=?",
                (run.id,),
            ).fetchone()[0]
            == 0
        )

    # The freed attempt number is reused; a run that already reached a
    # terminal state is never discarded.
    terminal = _claim_audit_run(store, task_id, generation, owner="runtime-attempt").run
    assert terminal.turn_attempt == 0
    store.fail_agent_run(
        terminal.id,
        {"code": "codex_process_failed", "retryable": True, "authorization_required": False},
        owner="runtime-attempt",
    )
    assert store.discard_unstarted_agent_run(terminal.id, owner="runtime-attempt") is False
    assert store.get_agent_run(terminal.id) is not None




def test_schema_currency_check_reads_no_more_than_the_first_stale_snapshot(tmp_path: Path):
    """The check runs in every subprocess; it must not scan the whole table."""
    from app.scheduled_config_storage import intern_scheduled_config
    store = AutoReplyStore(tmp_path / "snapshots.sqlite3")
    task = store.create_scheduled_task(
        name="消息检查",
        command="produce-once",
        cron_expression="0 * * * * *",
        timezone_name="Asia/Shanghai",
    )
    current = json.dumps(
        {"command": "produce-once", "required_runtime_capabilities": [], "task_id": 1}
    )
    stale = json.dumps({"task_id": 2})
    with store._immediate_write_transaction() as db:
        assert store._scheduled_task_run_snapshots_are_current(db) is True
        cols = {column[0] for column in db.execute("select * from schedule_occurrences limit 0").description}
        assert "snapshot_id" in cols
        # A JSON null value for command is a present key, not an absent one.
        for index, payload in enumerate(
            (current, json.dumps({"command": None, "required_runtime_capabilities": None}))
        ):
            _insert_scheduled_occurrence(db, {
                'event_id': f"evt-{index}",
                'scheduled_task_id': task.id,
                'trigger_kind': 'scheduled',
                'scheduled_for': f"2026-09-11T0{index}:00:00+00:00",
                'first_scheduled_for': f"2026-09-11T0{index}:00:00+00:00",
                'dispatch_status': 'pending',
                'snapshot_id': intern_scheduled_config(db, payload),
            })
        assert store._scheduled_task_run_snapshots_are_current(db) is True

        # Unparseable JSON is corrupt data, not a stale schema.
        _insert_scheduled_occurrence(db, {
            'event_id': 'evt-bad',
            'scheduled_task_id': task.id,
            'trigger_kind': 'scheduled',
            'scheduled_for': '2026-09-11T05:00:00+00:00',
            'first_scheduled_for': '2026-09-11T05:00:00+00:00',
            'dispatch_status': 'pending',
            'snapshot_id': intern_scheduled_config(db, "{not json"),
        })
        assert store._scheduled_task_run_snapshots_are_current(db) is True

        # A snapshot missing the command keys is what the check must catch.
        _insert_scheduled_occurrence(db, {
            'event_id': 'evt-stale',
            'scheduled_task_id': task.id,
            'trigger_kind': 'scheduled',
            'scheduled_for': '2026-09-11T06:00:00+00:00',
            'first_scheduled_for': '2026-09-11T06:00:00+00:00',
            'dispatch_status': 'pending',
            'snapshot_id': intern_scheduled_config(db, stale),
        })
        assert store._scheduled_task_run_snapshots_are_current(db) is False


def test_open_connection_names_and_reraises_connect_io_error_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = _store_without_initialization(tmp_path / "connect-io-error.sqlite3")
    error = _sqlite_failure(
        "disk I/O error",
        code=sqlite3.SQLITE_IOERR | (2 << 8),
        name="SQLITE_IOERR_SHORT_READ",
    )

    def fail_connect(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(store_module.sqlite3, "connect", fail_connect)

    with pytest.raises(sqlite3.Error) as raised:
        with store._connect():
            pytest.fail("connection setup failure must not yield")

    assert raised.value is error
    captured = capsys.readouterr().err
    assert captured.count("SQLITE_IOERR_SHORT_READ") == 1
    assert str(store.path) in captured


def test_open_connection_disables_mmap_for_mutable_service_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store_without_initialization(tmp_path / "mutable-service.sqlite3")
    executed: list[str] = []

    class TrackingConnection(sqlite3.Connection):
        def execute(self, sql: str):
            executed.append(sql)
            return self

        def close(self) -> None:
            super().close()

    monkeypatch.setattr(
        store_module.sqlite3,
        "connect",
        lambda *_args, **_kwargs: TrackingConnection(":memory:"),
    )

    store._open_connection().close()

    assert "pragma mmap_size = 0" in executed


@pytest.mark.parametrize(
    "failed_pragma",
    [
        "pragma busy_timeout = 30000",
        "pragma synchronous = normal",
        f"pragma cache_size = {store_module.SQLITE_READ_CACHE_SIZE}",
        f"pragma mmap_size = {store_module.SQLITE_MMAP_SIZE_BYTES}",
        "pragma foreign_keys = on",
    ],
)
def test_open_connection_names_and_reraises_each_pragma_io_error_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed_pragma: str,
) -> None:
    store = _store_without_initialization(tmp_path / "pragma-io-error.sqlite3")
    error = _sqlite_failure(
        "disk I/O error",
        code=sqlite3.SQLITE_IOERR | (2 << 8),
        name="SQLITE_IOERR_SHORT_READ",
    )

    class SetupConnection(sqlite3.Connection):
        def __init__(self) -> None:
            super().__init__(":memory:")
            self.closed = False

        def execute(self, sql: str):
            if sql == failed_pragma:
                raise error
            return self

        def close(self) -> None:
            super().close()
            self.closed = True

    connection = SetupConnection()
    monkeypatch.setattr(
        store_module.sqlite3,
        "connect",
        lambda *_args, **_kwargs: connection,
    )

    with pytest.raises(sqlite3.Error) as raised:
        with store._connect():
            pytest.fail("connection setup failure must not yield")

    assert raised.value is error
    assert connection.closed is True
    captured = capsys.readouterr().err
    assert captured.count("SQLITE_IOERR_SHORT_READ") == 1
    assert str(store.path) in captured


def test_open_connection_preserves_setup_error_when_close_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = _store_without_initialization(tmp_path / "setup-and-close-error.sqlite3")
    setup_error = _sqlite_failure(
        "disk I/O error",
        code=sqlite3.SQLITE_IOERR | (2 << 8),
        name="SQLITE_IOERR_SHORT_READ",
    )
    close_error = _sqlite_failure(
        "cannot close",
        code=sqlite3.SQLITE_IOERR,
        name="SQLITE_IOERR",
    )

    class FailingConnection(sqlite3.Connection):
        def execute(self, _sql: str):
            raise setup_error

        def close(self) -> None:
            super().close()
            raise close_error

    monkeypatch.setattr(
        store_module.sqlite3,
        "connect",
        lambda *_args, **_kwargs: FailingConnection(":memory:"),
    )

    with pytest.raises(sqlite3.Error) as raised:
        store._open_connection()

    assert raised.value is setup_error
    assert capsys.readouterr().err.count("SQLITE_IOERR_SHORT_READ") == 1


def test_sqlite_failures_name_their_extended_result_code(tmp_path: Path, capsys):
    """`disk I/O error` alone cannot tell a bad page from a locking problem."""
    store = AutoReplyStore(tmp_path / "named-errors.sqlite3")

    with pytest.raises(sqlite3.Error):
        with store._connect() as db:
            db.execute("select * from a_table_that_does_not_exist")

    captured = capsys.readouterr().err
    assert "SQLITE_ERROR" in captured
    assert str(store.path) in captured


def test_naming_an_error_does_not_swallow_it(tmp_path: Path, capsys):
    """The failure must reach the caller unchanged; this is not a fallback."""
    store = AutoReplyStore(tmp_path / "reraise.sqlite3")

    with pytest.raises(sqlite3.OperationalError) as raised:
        with store._connect() as db:
            db.execute("select * from a_table_that_does_not_exist")

    assert "a_table_that_does_not_exist" in str(raised.value)


def _failed_attempt(store, *, trigger: str = "msg-settled-outside") -> int:
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-settled-outside",
        conversation_title="胡明",
        trigger_message_id=trigger,
        trigger_sender="胡明",
        trigger_text="[Ding]磊哥，请审批工资。",
        action="agent_run",
        sensitivity_kind="general",
        codex_reason="runtime_provider_unreachable",
        send_status="failed",
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id,
                      {"send_error": "runtime_provider_unreachable"})
    return attempt_id


def _history_status(store, attempt_id: int) -> str:
    _total, rows = store.list_operation_logs_with_count(
        limit=200,
        source_tables=("reply_attempts",),
    )
    row = next(
        row
        for row in rows
        if row.source_table == "reply_attempts" and row.source_id == attempt_id
    )
    return row.status


def test_an_attempt_the_principal_settled_himself_stops_reading_as_failed(
    tmp_path,
):
    """The only way a failed attempt could recover was a later attempt on the
    same trigger. When the principal just does the work himself in DingTalk,
    no such attempt ever exists, so the row read as an open failure forever
    while nothing was open. `errors` has had resolved_at/resolution all along.
    """

    store = store_module.AutoReplyStore(tmp_path / "settled.sqlite3")
    attempt_id = _failed_attempt(store)
    assert _history_status(store, attempt_id) == "failed"

    assert store.resolve_failed_reply_attempt_already_settled(
        attempt_id,
        resolution="approval completed 15:45:52 by the principal in DingTalk",
    )

    assert _history_status(store, attempt_id) == "recovered"


def test_the_resolution_appends_and_never_rewrites_the_failure(tmp_path):
    store = store_module.AutoReplyStore(tmp_path / "settled-detail.sqlite3")
    attempt_id = _failed_attempt(store)

    store.resolve_failed_reply_attempt_already_settled(
        attempt_id,
        resolution="approval completed 15:45:52",
    )

    _total, rows = store.list_operation_logs_with_count(
        limit=200,
        source_tables=("reply_attempts",),
    )
    row = next(row for row in rows if row.source_id == attempt_id)
    assert "runtime_provider_unreachable" in row.detail
    assert "Resolved: approval completed 15:45:52" in row.detail
    with store._connect() as db:
        stored = db.execute(
            f"select send_status, send_error from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?",
            (attempt_id,),
        ).fetchone()
    assert stored["send_status"] == "failed"
    assert stored["send_error"] == "runtime_provider_unreachable"


def test_settling_an_attempt_requires_live_evidence(tmp_path):
    store = store_module.AutoReplyStore(tmp_path / "settled-evidence.sqlite3")
    attempt_id = _failed_attempt(store)

    with pytest.raises(ValueError):
        store.resolve_failed_reply_attempt_already_settled(
            attempt_id,
            resolution="   ",
        )


def test_only_a_failed_attempt_can_be_settled_and_only_once(tmp_path):
    store = store_module.AutoReplyStore(tmp_path / "settled-once.sqlite3")
    attempt_id = _failed_attempt(store)
    sent = store.record_reply_attempt(
        conversation_id="cid-settled-outside",
        conversation_title="胡明",
        trigger_message_id="msg-already-sent",
        trigger_sender="胡明",
        trigger_text="hello",
        action="agent_run",
        sensitivity_kind="general",
        send_status="sent",
    )


    assert store.resolve_failed_reply_attempt_already_settled(
        attempt_id, resolution="settled"
    )
    assert not store.resolve_failed_reply_attempt_already_settled(
        attempt_id, resolution="settled again"
    )
    assert not store.resolve_failed_reply_attempt_already_settled(
        sent, resolution="not a failure"
    )


def _attempt(store, *, trigger: str, status: str, error: str = "") -> int:
    attempt_id = store.record_reply_attempt(
        conversation_id="cid-supersede",
        conversation_title="Supersession",
        trigger_message_id=trigger,
        trigger_sender="Sender",
        trigger_text="trigger",
        action="agent_run",
        sensitivity_kind="general",
        send_status=status,
    )
    if error:
        with store._connect() as db:
            update_action(db, "reply_attempts", attempt_id, {"send_error": error})
    return attempt_id


def _projected(store, attempt_id: int) -> str:
    _total, rows = store.list_operation_logs_with_count(
        limit=500, source_tables=("reply_attempts",)
    )
    return next(r.status for r in rows if r.source_id == attempt_id)


def test_a_superseded_attempt_retires_whatever_status_it_stopped_on(tmp_path):
    """The recovered collapse only ever applied to send_status='failed'.

    68 blocked attempts from a runtime that no longer exists, 37 needs_human
    rows nobody can act on, and 7 stranded pending placeholders all had a
    strictly newer terminal attempt on the same trigger and still read as
    open work, some for 46 days.
    """

    store = store_module.AutoReplyStore(tmp_path / "supersede.sqlite3")
    for status in ("blocked", "needs_human", "pending"):
        trigger = f"msg-{status}"
        stale = _attempt(store, trigger=trigger, status=status)
        assert _projected(store, stale) == status
        _attempt(store, trigger=trigger, status="sent")
        assert _projected(store, stale) == "recovered", status


def test_an_unanswered_question_is_never_retired_by_its_own_closed_task(tmp_path):
    """A needs_human attempt always sits on a task the service closed as done.

    Collapsing on the terminal-task branch would therefore retire every open
    question — including a payroll approval the Agent refused to self-authorize
    and that had been waiting 44 hours.
    """

    store = store_module.AutoReplyStore(tmp_path / "open-question.sqlite3")
    pending_question = _attempt(
        store, trigger="msg-payroll", status="needs_human", error="needs_human"
    )
    store.ensure_reply_task(
        channel="dingtalk",
        conversation_id="cid-supersede",
        conversation_title="Supersession",
        single_chat=True,
        trigger_message_id="msg-payroll",
        trigger_create_time="2026-09-10T05:47:00+00:00",
        trigger_sender="Sender",
        trigger_text="trigger",
        trigger_message_json="{}",
    )
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'done'},
            where_sql="and id in (select task_id from task_inputs where trigger_message_id=?)",
            parameters=('msg-payroll',),
        )

    assert _projected(store, pending_question) == "needs_human"


def test_skipped_task_retires_its_failed_attempt_from_current_problems(tmp_path):
    store = store_module.AutoReplyStore(tmp_path / "skipped-current.sqlite3")
    failed = _attempt(store, trigger="msg-skipped", status="failed")
    store.ensure_reply_task(
        channel="dingtalk",
        conversation_id="cid-supersede",
        conversation_title="Supersession",
        single_chat=True,
        trigger_message_id="msg-skipped",
        trigger_create_time="2026-09-10T05:47:00+00:00",
        trigger_sender="Sender",
        trigger_text="trigger",
        trigger_message_json="{}",
    )
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'skipped'},
            where_sql="and id in (select task_id from task_inputs where trigger_message_id=?)",
            parameters=('msg-skipped',),
        )

    assert all(
        attempt.id != failed
        for attempt in store.list_current_unresolved_problem_attempts()
    )


def test_an_explicit_resolution_still_retires_any_status(tmp_path):
    store = store_module.AutoReplyStore(tmp_path / "explicit.sqlite3")
    blocked = _attempt(store, trigger="msg-blocked-resolved", status="blocked")

    with store._connect() as db:
        update_action(db, "reply_attempts", blocked, {
            "resolved_at": db.execute("select current_timestamp").fetchone()[0],
            "resolution": "settled by the principal",
        })

    assert _projected(store, blocked) == "recovered"


def _input_with_live_run(store):
    """One work item mid-flight: claimed, with a running run and a leased attempt."""

    input_id = store.enqueue_work_summary_input("local_file", "source", "{}")
    assert len(store.claim_work_summary_inputs(1)) == 1
    run_id = store.claim_professional_run(input_id)
    attempt = store.claim_runtime_operation_attempt(
        "task",
        str(run_id),
        "codex_oauth",
        "codex_cli",
        "local_oauth",
        "gpt-5.6-sol",
        owner="leaving-processing",
        lease_seconds=1800,
    )
    return input_id, run_id, attempt


def _live_counts(store, run_id: int, attempt_id: int) -> tuple[str, str]:
    with store._connect() as db:
        run = db.execute(
            f"select status from ({TASK_LOGICAL_RUN_SQL}) where id=?", (run_id,)
        ).fetchone()
    attempt = store.get_agent_runtime_attempt(attempt_id)
    return run["status"], attempt.status


def test_an_input_never_leaves_processing_with_a_live_run_behind_it(tmp_path: Path):
    """The reaper could only ever see an orphan the reset had already made.

    recover_orphaned_task_agent_runs joins on `input.status <> 'processing'`,
    and service start ran it BEFORE reset_processing_work_summary_inputs. So
    every restart's reaper found nothing, the reset then orphaned that
    restart's runs, and the wreckage waited for the NEXT restart -- 6 attempts
    closed that way on 2026-09-12 and 36 on 2026-09-08. A runtime attempt left
    leased that way is what a re-claimed input then collides with, which is
    where runtime_attempt_active came from.
    """

    store = AutoReplyStore(tmp_path / "leaving-processing.sqlite3")
    _input_id, run_id, attempt = _input_with_live_run(store)
    assert _live_counts(store, run_id, attempt.id) == ("running", "starting")

    store.reset_processing_work_summary_inputs()

    assert _live_counts(store, run_id, attempt.id) == ("failed", "failed")
    recovered = store.get_agent_runtime_attempt(attempt.id)
    assert recovered.failure_code == "runtime_parent_terminal_no_effect"
    assert recovered.lease_expires_at == ""


def test_the_stale_reset_also_closes_what_it_orphans(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "stale-leaving.sqlite3")
    input_id, run_id, attempt = _input_with_live_run(store)
    with store._connect() as db:
        _update_current_task8(db, 'work_summary_inputs', {},
                              update_sql={'updated_at': "datetime('now','-3600 seconds')"},
                              where_sql='and id=?', parameters=(input_id,))

    assert store.reset_stale_processing_work_summary_inputs(1200) == 1

    assert _live_counts(store, run_id, attempt.id) == ("failed", "failed")


def test_a_capacity_deferral_closes_what_it_orphans(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "capacity-leaving.sqlite3")
    input_id, run_id, attempt = _input_with_live_run(store)

    store.defer_work_summary_input_for_capacity(
        input_id, "runtime_provider_unreachable", available_at=""
    )

    assert _live_counts(store, run_id, attempt.id) == ("failed", "failed")


def test_an_attempt_that_started_a_provider_effect_is_left_alone(tmp_path: Path):
    """Only an attempt that did nothing outside is safe to close from here."""

    store = AutoReplyStore(tmp_path / "effectful-leaving.sqlite3")
    _input_id, run_id, attempt = _input_with_live_run(store)
    with store._connect() as db:
        db.execute(
            "update runtime_attempts set first_effect_started_at=current_timestamp where id=?",
            (attempt.id,),
        )

    store.reset_processing_work_summary_inputs()

    run_status, attempt_status = _live_counts(store, run_id, attempt.id)
    assert run_status == "failed"
    assert attempt_status == "starting"


def test_a_task_whose_approval_action_completed_still_ends_failed(tmp_path):
    """A completed action cannot turn a later technical failure into a decision."""
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=False,
        trigger_message_id="oa-pending:INST:1",
        trigger_create_time="2026-09-17 23:00:00",
        trigger_sender="Derek OA",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    task = store.get_reply_task_for_message("oa_pending_scan", "oa-pending:INST:1")
    assert task is not None
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'processing', 'business_object_key': 'oa:INST:T1'},
            update_sql={'locked_at': 'current_timestamp', 'claimed_input_version': 'input_version'},
            where_sql='and id=?',
            parameters=(task.id,),
        )
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="effect-test-owner",
    )
    reject = {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "status": "completed",
            "exit_code": 0,
            "command": (
                "/bin/zsh -lc 'dws oa approval reject --instance-id INST "
                "--task-id T1 --remark 材料不足 --format json'"
            ),
            "aggregated_output": '{"success":true}',
        },
    }
    store.append_agent_run_event(claim.run.id, reject, owner='effect-test-owner')
    with store._connect() as db:
        assert_no_core_native_storage(db)

    store.fail_reply_task(
        task.id,
        "audit_revision_exhausted",
        expected_execution_generation=task.execution_generation,
    )

    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "audit_revision_exhausted"


def test_a_task_that_wrote_nothing_still_fails(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="oa_pending_scan",
        conversation_title="审批待办",
        single_chat=False,
        trigger_message_id="oa-pending:INST2:1",
        trigger_create_time="2026-09-17 23:00:00",
        trigger_sender="Derek OA",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    task = store.get_reply_task_for_message("oa_pending_scan", "oa-pending:INST2:1")
    assert task is not None
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'processing', 'business_object_key': 'oa:INST2:T1'},
            update_sql={'locked_at': 'current_timestamp', 'claimed_input_version': 'input_version'},
            where_sql='and id=?',
            parameters=(task.id,),
        )

    store.fail_reply_task(
        task.id,
        "audit_revision_exhausted",
        expected_execution_generation=task.execution_generation,
    )

    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error == "audit_revision_exhausted"


def test_a_task_holding_a_lock_with_no_live_run_is_requeued(tmp_path):
    """Reply task 384388 held its lock for 49 minutes after its Audit run failed.

    Nothing between restarts picked it up, and Attention shows errors rather
    than states, so only the quality gate's `processing_stale` named it.
    """
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-stale",
        conversation_title="产研管理群",
        single_chat=False,
        trigger_message_id="msg-stale",
        trigger_create_time="2026-09-18 00:58:00",
        trigger_sender="张晓民",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    task = store.get_reply_task_for_message("cid-stale", "msg-stale")
    assert task is not None
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'processing'},
            update_sql={'locked_at': "datetime('now','-45 minutes')"},
            where_sql='and id=?',
            parameters=(task.id,),
        )
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="stale-test-owner",
    )
    with store._connect() as db:
        db.execute("update runs set status='failed' where id=?", (claim.run.id,))
        expired_at = db.execute("select datetime('now','-1 minute')").fetchone()[0]
        task_claims.acquire_claim(
            db, adapter_name='reply', source_id=task.id, owner='live-dispatcher',
            owner_pid=1, lease_expires_at=expired_at, now='2000-01-01 00:00:00',
        )

    recovered = store.recover_stale_processing_reply_tasks(stale_after_seconds=900)

    assert [item.id for item in recovered] == [task.id]
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "pending"
    assert updated.error == "stale_lease_recovered"
    with store._connect() as db:
        lease = db.execute(
            f"select owner, lease_expires_at from ({task_claims.claim_projection()}) "
            "where adapter_name='reply' and source_id=?",
            (str(task.id),),
        ).fetchone()
    assert tuple(lease) == ("", "")


def test_a_task_with_a_live_run_keeps_its_lock(tmp_path):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-live",
        conversation_title="产研管理群",
        single_chat=False,
        trigger_message_id="msg-live",
        trigger_create_time="2026-09-18 00:58:00",
        trigger_sender="张晓民",
        trigger_text="请处理",
        trigger_message_json="{}",
    )
    task = store.get_reply_task_for_message("cid-live", "msg-live")
    assert task is not None
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'processing'},
            update_sql={'locked_at': "datetime('now','-45 minutes')"},
            where_sql='and id=?',
            parameters=(task.id,),
        )
    store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="live-test-owner",
    )

    assert store.recover_stale_processing_reply_tasks(stale_after_seconds=900) == []
    updated = store.get_reply_task(task.id)
    assert updated is not None
    assert updated.status == "processing"


def test_a_busy_provider_does_not_end_the_task(tmp_path: Path):
    """The weekly report lost a week to a provider that was merely busy.

    Its first occurrence, 2026-09-19, spent seven minutes gathering evidence,
    then its third turn hit `codex_provider_overloaded` -- retryable, and the
    provider itself reported the session could continue. The task was closed
    on that turn, and since it runs once a week there was nothing to retry.
    """
    store = AutoReplyStore(tmp_path / "resumable.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Scheduled",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-09-19 12:00:00",
        trigger_sender="Derek",
        trigger_text="weekly report",
        execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="test",
    )
    store.fail_agent_run(
        claim.run.id,
        {
            "code": "codex_provider_overloaded",
            "retryable": True,
            "authorization_required": False,
            "detail": "Selected model is at capacity.",
            "session_continuable": True,
        },
        owner="test",
    )

    store.complete_reply_task(
        task.id, expected_execution_generation=task.execution_generation
    )

    reopened = store.get_reply_task(task.id)
    assert reopened.status == "pending"
    # The generation is kept, so the next claim resumes the same session
    # rather than repeating the work already done in it.
    assert reopened.execution_generation == task.execution_generation


def test_a_failure_that_cannot_be_resumed_still_fails_the_task(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "not-resumable.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1",
        conversation_title="Scheduled",
        single_chat=False,
        trigger_message_id="msg-1",
        trigger_create_time="2026-09-19 12:00:00",
        trigger_sender="Derek",
        trigger_text="weekly report",
        execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="test",
    )
    store.fail_agent_run(
        claim.run.id,
        {
            "code": "codex_result_invalid",
            "retryable": True,
            "authorization_required": False,
            "detail": "bad result",
            "session_continuable": False,
        },
        owner="test",
    )

    store.complete_reply_task(
        task.id, expected_execution_generation=task.execution_generation
    )

    assert store.get_reply_task(task.id).status == "failed"


def _fail_task_turn(store: AutoReplyStore, task, payload: dict) -> None:
    claim = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=store.next_agent_run_turn_attempt(
            task.id, task.execution_generation, role=AgentRole.CONSUMER,
            proposal_revision=0,
        ),
        parent_agent_run_id=None,
        operation_id="",
        owner="test",
    )
    store.fail_agent_run(claim.run.id, payload, owner="test")


def test_a_turn_that_never_ran_resumes_too(tmp_path: Path):
    """No route was available, so nothing was consumed and nothing is lost.

    These report `session_continuable: False` only because no new session was
    opened; the generation's earlier session is still there. Over thirty days
    they are the two largest failure classes by far -- 7597
    `runtime_provider_unreachable` and 417 `runtime_route_unavailable`,
    against 92 provider overloads.
    """
    store = AutoReplyStore(tmp_path / "nothing-ran.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1", conversation_title="Scheduled", single_chat=False,
        trigger_message_id="msg-1", trigger_create_time="2026-09-19 12:00:00",
        trigger_sender="Derek", trigger_text="weekly report",
        execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    _fail_task_turn(
        store,
        task,
        {
            "code": "runtime_provider_unreachable",
            "retryable": True,
            "authorization_required": False,
            "detail": "no_eligible_route",
            "session_continuable": False,
        },
    )

    store.complete_reply_task(
        task.id, expected_execution_generation=task.execution_generation
    )

    assert store.get_reply_task(task.id).status == "pending"


def test_a_resume_that_never_stops_is_allowed_to_fail(tmp_path: Path):
    """A permanent fault must surface, not hide behind an endless queue."""
    store = AutoReplyStore(tmp_path / "resume-ceiling.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1", conversation_title="Scheduled", single_chat=False,
        trigger_message_id="msg-1", trigger_create_time="2026-09-19 12:00:00",
        trigger_sender="Derek", trigger_text="weekly report",
        execution_generation="gen-1",
    )
    overloaded = {
        "code": "codex_provider_overloaded",
        "retryable": True,
        "authorization_required": False,
        "detail": "Selected model is at capacity.",
        "session_continuable": True,
    }
    statuses = []
    for _ in range(AutoReplyStore.RESUMABLE_MAX_ATTEMPTS + 1):
        with store._connect() as db:
            _update_current_task8(
                db,
                'reply_tasks',
                {'available_at': ''},
            )
        claimed = store.claim_reply_tasks(limit=1)
        if not claimed:
            break
        task = claimed[0]
        _fail_task_turn(store, task, overloaded)
        store.complete_reply_task(
            task.id, expected_execution_generation=task.execution_generation
        )
        statuses.append(store.get_reply_task(task.id).status)

    assert statuses[0] == "pending"
    assert statuses[-1] == "failed"


def test_a_shell_send_failure_stays_failed_not_needs_human(tmp_path: Path):
    """Unverified shell output is a technical failure, never a rule decision.

    A non-governed shell command cannot prove a provider delivery receipt.
    Its later runtime failure must retain the concrete failure state; otherwise
    the service creates opaque human work merely because an action *might*
    have happened.
    """
    store = AutoReplyStore(tmp_path / "shell-send-needs-human.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1", conversation_title="Group", single_chat=False,
        trigger_message_id="msg-1", trigger_create_time="2026-09-19 10:00:00",
        trigger_sender="Derek", trigger_text="handle this",
        execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    claim = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="test",
    )
    store.append_agent_run_event(
        claim.run.id,
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "exit_code": 0,
                "status": "completed",
                "command": (
                    "/bin/zsh -lc 'dws chat +messages-send --group cid-1 "
                    "--text \"已处理\"'"
                ),
                "aggregated_output": '{"success":true}',
            },
        },
        owner="test",
    )
    store.fail_agent_run(
        claim.run.id,
        {
            "code": "agent_reported_failure",
            "retryable": True,
            "authorization_required": False,
            "detail": "invalid_execution_path",
            "session_continuable": False,
        },
        owner="test",
    )

    store.fail_reply_task(
        task.id,
        "agent_reported_failure",
        expected_execution_generation=task.execution_generation,
    )

    assert store.get_reply_task(task.id).status == "failed"


def test_reconcile_preserves_invalid_legacy_needs_human_projection(
    tmp_path: Path,
) -> None:
    """A provider error remains in History without turning into a decision."""
    store = AutoReplyStore(tmp_path / "invalid-human-projection.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1", conversation_title="Group", single_chat=False,
        trigger_message_id="msg-1", trigger_create_time="2026-09-19 10:00:00",
        trigger_sender="Derek", trigger_text="handle this",
        execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    claim = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="test",
    )
    store.fail_agent_run(
        claim.run.id,
        {
            "code": "agent_reported_failure",
            "source_code": "provider_receipt_missing",
            "retryable": True,
        },
        owner="test",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="needs_human",
        human_decision_options_json='[{"key":"confirm"}]',
        channel=task.channel,
    )
    with store._connect() as db:
        update_action(db, "reply_attempts", attempt_id, {
            "agent_run_id": claim.run.id,
            "send_error": "provider_receipt_missing",
        })
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'needs_human', 'error': ''},
            where_sql='and id=?',
            parameters=(task.id,),
        )

    assert all(
        attempt.id != attempt_id
        for attempt in store.list_current_unresolved_problem_attempts()
    )
    assert store.reconcile_invalid_needs_human_projections() == 0
    attempt = store.get_reply_attempt(attempt_id)
    assert attempt is not None
    assert attempt.send_status == "needs_human"
    assert attempt.send_error == "provider_receipt_missing"
    assert store.get_reply_task(task.id).status == "needs_human"
    assert store.reconcile_invalid_needs_human_projections() == 0


def test_reconcile_preserves_failed_attempt_and_stale_task_for_investigation(
    tmp_path: Path,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    consumer = store.claim_agent_run(
        task.id,
        task.execution_generation,
        role=AgentRole.CONSUMER,
        proposal_revision=0,
        turn_attempt=0,
        parent_agent_run_id=None,
        operation_id="",
        owner="consumer",
    ).run
    consumer = store.fail_agent_run(
        consumer.id,
        {"code": "codex_result_invalid"},
        owner="consumer",
    )
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
        channel=task.channel,
    )
    with store._immediate_write_transaction() as db:
        update_action(db, "reply_attempts", attempt_id, {
            "agent_run_id": consumer.id,
            "send_error": "codex_result_invalid",
        })
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'needs_human', 'error': 'codex_result_invalid'},
            where_sql='and id=?',
            parameters=(task.id,),
        )

    assert store.reconcile_invalid_needs_human_projections() == 0
    assert store.get_reply_attempt(attempt_id).send_status == "failed"
    assert store.get_reply_task(task.id).status == "needs_human"
    assert store.get_reply_task(task.id).error == "codex_result_invalid"


def test_failed_task_without_a_current_human_projection_is_unchanged(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "never-acted.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1", conversation_title="Group", single_chat=False,
        trigger_message_id="msg-1", trigger_create_time="2026-09-19 10:00:00",
        trigger_sender="Derek", trigger_text="handle this",
        execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'failed', 'error': 'codex_result_missing'},
        )

    assert store.reconcile_invalid_needs_human_projections() == 0
    assert store.get_reply_task(task.id).status == "failed"


def test_old_generation_attempt_cannot_fail_or_surface_current_task(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "generation-guard.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
        channel=task.channel,
    )
    with store._connect() as db:
        run = insert_native_run_fixture(
            db, {
                'task_id': task.id,
                'execution_generation': task.execution_generation,
                'role': 'consumer',
                'status': 'completed',
            },
                    result='{}', tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        update_action(db, "reply_attempts", attempt_id, {
            "agent_run_id": run.lastrowid,
            "send_error": "needs_human",
        })
        _update_current_task8(
            db,
            'reply_tasks',
            {'execution_generation': 'current-gen', 'status': 'pending'},
            where_sql='and id=?',
            parameters=(task.id,),
        )

    assert store.reconcile_invalid_needs_human_projections() == 0
    assert store.get_reply_task(task.id).status == "pending"
    assert store.list_current_unresolved_problem_attempt_summaries() == []
    assert store.list_current_unresolved_problem_attempts() == []


def test_current_generation_failed_attempt_remains_visible_while_task_retries(tmp_path: Path, monkeypatch):
    store = AutoReplyStore(tmp_path / "retry-attention.sqlite3")
    task_id = _enqueue_universal_reply_task(store)
    task = store.get_reply_task(task_id)
    assert task is not None
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        action="agent_run",
        sensitivity_kind="general",
        send_status="failed",
        channel=task.channel,
    )
    with store._connect() as db:
        run = insert_native_run_fixture(
            db, {
                'task_id': task.id,
                'execution_generation': task.execution_generation,
                'role': 'consumer',
                'status': 'failed',
            },
                    result='{}', tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        update_action(db, "reply_attempts", attempt_id, {
            "agent_run_id": run.lastrowid,
            "send_error": "codex_process_failed",
        })
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'pending'},
            where_sql='and id=?',
            parameters=(task.id,),
        )
    assert store.get_reply_task(task.id).status == "pending"
    assert [item.id for item in store.list_current_unresolved_problem_attempts()] == [attempt_id]
    assert [item["id"] for item in store.list_current_unresolved_problem_attempt_summaries()] == [str(attempt_id)]
    assert store.count_current_unresolved_problem_attempts() == 1


def test_the_second_failure_path_keeps_receipt_failure_failed(tmp_path: Path):
    """A missing receipt stays a recoverable technical failure on every path."""
    store = AutoReplyStore(tmp_path / "second-path.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1", conversation_title="Group", single_chat=False,
        trigger_message_id="msg-1", trigger_create_time="2026-09-19 10:00:00",
        trigger_sender="Derek", trigger_text="handle this",
        execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    claim = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="test",
    )
    store.append_agent_run_event(
        claim.run.id,
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "exit_code": 0,
                "status": "completed",
                "command": (
                    "/bin/zsh -lc 'dws chat +messages-send --group cid-1 "
                    "--text \"已处理\"'"
                ),
                "aggregated_output": '{"success":true,"messageId":"m-1"}',
            },
        },
        owner="test",
    )
    store.fail_agent_run(
        claim.run.id,
        {
            "code": "agent_reported_failure",
            "retryable": True,
            "authorization_required": False,
            "detail": "provider_receipt_missing",
            "session_continuable": False,
        },
        owner="test",
    )

    store.complete_reply_task(
        task.id, expected_execution_generation=task.execution_generation
    )

    assert store.get_reply_task(task.id).status == "failed"


def test_the_oa_finalize_path_keeps_technical_failure_failed(tmp_path: Path):
    """Finalization cannot bypass the decision-quality contract either."""
    store = AutoReplyStore(tmp_path / "oa-finalize.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1", conversation_title="审批待办", single_chat=False,
        trigger_message_id="msg-1", trigger_create_time="2026-09-20 10:00:00",
        trigger_sender="Derek", trigger_text="leave approval",
        execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    claim = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="test",
    )
    store.append_agent_run_event(
        claim.run.id,
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "exit_code": 0,
                "status": "completed",
                "command": (
                    "/bin/zsh -lc \"dws chat +messages-send --open-dingtalk-id "
                    "DVPP --text '你的年假申请已同意'\""
                ),
                "aggregated_output": '{"ok":true,"result":{"errorCode":null}}',
            },
        },
        owner="test",
    )

    store.fail_agent_run(
        claim.run.id,
        {
            "code": "codex_result_invalid",
            "retryable": True,
            "authorization_required": False,
            "detail": "sent from its own shell",
            "session_continuable": False,
        },
        owner="test",
    )

    store.finalize_orchestrated_reply_task(
        task_id=task.id,
        expected_execution_generation=task.execution_generation,
        run_id=claim.run.id,
        task_status="failed",
        task_error="codex_result_invalid",
        available_at="",
        conversation_id=task.conversation_id,
        conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id,
        trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text,
        codex_reason="",
        codex_session_id="",
        codex_transcript_start_line=0,
        codex_transcript_end_line=0,
        audit_tool_events_json="[]",
        audit_summary="",
        send_status="failed",
        send_error="codex_result_invalid",
        channel=task.channel,
    )

    assert store.get_reply_task(task.id).status == "failed"


def test_an_answered_question_can_close(tmp_path: Path):
    """A needs_human task has no way back to a terminal state on its own.

    The retry path refuses it, which is right: re-running something handed to
    a person can repeat an irreversible action. But an answered question has
    to be able to close, or the page fills with questions that were settled in
    conversation and nowhere else.
    """
    store = AutoReplyStore(tmp_path / "answered.sqlite3")
    store.enqueue_reply_task(
        conversation_id="cid-1", conversation_title="Group", single_chat=False,
        trigger_message_id="msg-1", trigger_create_time="2026-09-20 10:00:00",
        trigger_sender="Derek", trigger_text="decide this",
        execution_generation="gen-1",
    )
    task = store.claim_reply_tasks(limit=1)[0]
    with store._connect() as db:
        _update_current_task8(
            db,
            'reply_tasks',
            {'status': 'needs_human'},
        )

    assert store.close_needs_human_task_with_decision(
        task.id, decision="认可已发出的内容，不补发。"
    )

    closed = store.get_reply_task(task.id)
    assert closed.status == "done"
    assert "human_decision:" in closed.error
    # The attempt has to leave `failed` too, or the next reconciliation sweep
    # projects the task back from it: 384446 and 384447 reopened one minute
    # after they were answered.
    assert store.reconcile_unresolved_reply_tasks_with_terminal_attempts() == 0
    assert store.get_reply_task(task.id).status == "done"
    # A task that is not waiting on anyone is not closed this way.
    assert not store.close_needs_human_task_with_decision(task.id, decision="again")


def test_scheduled_task_ids_for_reply_tasks_maps_only_reply_task_runs(tmp_path: Path):
    from app.agent_cron.models import ScheduledTaskSnapshot

    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    from app.schedules_storage import insert_schedule

    with store._immediate_write_transaction() as db:
        insert_schedule(db, {
            'id': 13, 'name': '准备 CEO 管理周报', 'prompt': 'p', 'command': '',
            'cron_expression': '0 12 * * 6', 'timezone': 'America/Los_Angeles',
            'runtime_id': '', 'enabled': 1,
        })
    task = store.get_scheduled_task(13)
    assert task is not None
    snapshot_json = ScheduledTaskSnapshot.from_task(task).to_json()
    with store._immediate_write_transaction() as db:
        from app.scheduled_config_storage import intern_scheduled_config
        snapshot_id = intern_scheduled_config(db, snapshot_json)
        for event_id, scheduled_for, execution_kind, execution_id in (
            ('e1', '2026-10-01T12:00:00+00:00', 'reply_task', '9001'),
            ('e2', '2026-10-01T13:00:00+00:00', 'service_command', 'produce-once'),
        ):
            _insert_scheduled_occurrence(db, {
                'event_id': event_id,
                'scheduled_task_id': 13,
                'trigger_kind': 'scheduled',
                'scheduled_for': scheduled_for,
                'first_scheduled_for': scheduled_for,
                'dispatch_status': 'dispatched',
                'execution_kind': execution_kind,
                'execution_id': execution_id,
                'created_at': scheduled_for,
                'snapshot_id': snapshot_id,
            })

    mapped = store.scheduled_task_ids_for_reply_tasks((9001, 9002))

    assert mapped == {9001: 13}
    assert store.scheduled_task_ids_for_reply_tasks(()) == {}


@pytest.mark.parametrize("effect_receipt", [False, True])
def test_manual_rerun_can_reopen_only_feedback_backed_skipped_pending_oa_review(
    tmp_path: Path,
    effect_receipt: bool,
):
    store = AutoReplyStore(tmp_path / "worker.sqlite3")
    process_id = "proc-review"
    oa_task_id = "task-review"
    conversation_id = f"oa_pending_scan:{process_id}"
    trigger_id = "oa-pending:proc-review:revision-1:2026-10-19"
    oa_url = (
        "https://aflow.dingtalk.com/detail?"
        f"procInstId={process_id}&taskId={oa_task_id}"
    )
    trigger_json = json.dumps({
        "message_id": trigger_id,
        "raw_payload": {"source": "oa_pending_scan", "processInstanceId": process_id,
                        "taskId": oa_task_id},
    })
    task = store.ensure_reply_task(
        conversation_id=conversation_id, conversation_title="审批待办",
        single_chat=True, trigger_message_id=trigger_id,
        trigger_create_time="2026-10-19T10:00:00Z", trigger_sender="Derek OA",
        trigger_text="Review this OA process.", trigger_message_json=trigger_json,
        channel="dingtalk", oa_url=oa_url,
        business_object_key=f"oa:{process_id}:{oa_task_id}",
    )
    if effect_receipt:
        # An external effect already recorded for this Task's run forbids a new review.
        claimed = store.claim_reply_task(task.id)
        assert claimed is not None
        audit = _claim_audit_run(
            store, task.id, task.execution_generation, owner="worker-1"
        ).run
        store.complete_agent_run(audit.id, {"outcome": "executed"}, owner="worker-1")
        store.record_completed_agent_message_delivery(
            agent_run_id=audit.id, external_action_key="external-oa-effect",
            business_object_key=task.business_object_key, action_identity="oa-effect",
            operation="chat +send-to-group", target_identifiers={"group": "cid-1"},
            conversation_id=task.conversation_id, trigger_message_id=task.trigger_message_id,
            reply_text="done", provider_result={"message_id": "message-1"},
        )
    with store._immediate_write_transaction() as db:
        _update_current_task8(
            db, "reply_tasks", {},
            update_sql={"status": "'done'", "updated_at": "current_timestamp"},
            where_sql="and id=?", parameters=(task.id,),
        )
    attempt_id = store.record_reply_attempt(
        conversation_id=conversation_id, conversation_title="审批待办",
        trigger_message_id=trigger_id, trigger_sender="Derek OA",
        trigger_text="", action="agent_run", sensitivity_kind="general",
        oa_process_instance_id=process_id, oa_task_id=oa_task_id,
        oa_url=oa_url, oa_action="review", oa_action_result_json="{}",
        send_status="skipped", channel="dingtalk",
    )
    store.record_reply_feedback(
        attempt_id, feedback="Re-review based on the business evidence.",
    )
    rerun_args = dict(
        conversation_id=conversation_id, conversation_title="审批待办",
        single_chat=True, trigger_message_id=trigger_id,
        trigger_create_time="2026-10-19T10:00:00Z", trigger_sender="Derek OA",
        trigger_text="Review this OA process.", trigger_message_json=trigger_json,
        oa_url=oa_url, attempt_id=attempt_id, channel="dingtalk",
        expected_execution_generation=task.execution_generation,
    )
    with pytest.raises(store_module.ManualRerunConflict):
        store.enqueue_manual_rerun_reply_task(**rerun_args)

    if effect_receipt:
        with pytest.raises(store_module.ManualRerunConflict):
            store.enqueue_manual_rerun_reply_task(**rerun_args, allow_skipped_oa_review=True)
        return

    rerun = store.enqueue_manual_rerun_reply_task(
        **rerun_args, allow_skipped_oa_review=True,
    )

    assert rerun.status == "pending"
    assert rerun.execution_generation != task.execution_generation
    assert rerun.manual_rerun_attempt_id == attempt_id


def test_requeue_reply_task_fails_stale_run_and_records_terminal_event(tmp_path):
    store = AutoReplyStore(tmp_path / "requeue-source.sqlite3")
    with store._connect() as db:
        db.row_factory = sqlite3.Row
        task_id = _insert_current_task8(db, "reply_tasks", {
            "channel": "dingtalk", "conversation_id": "cid", "conversation_title": "Chat",
            "single_chat": 0, "trigger_message_id": "msg", "trigger_create_time": "",
            "trigger_sender": "Sender", "status": "processing",
            "execution_generation": "generation-1",
        }).lastrowid
        run_id = _insert_native_run(db, {
            "task_id": task_id,
            "execution_generation": "generation-1",
            "role": "consumer",
            "status": "running",
            "lease_expires_at": "2000-01-01 00:00:00",
            "updated_at": "2000-01-01 00:00:00",
        })

    store.requeue_reply_task(
        task_id, "stale_agent_turn_recovery",
        expected_execution_generation="generation-1",
        available_at="2026-10-23 17:00:00",
    )

    with store._connect() as db:
        task_row = db.execute(
            f"select status,available_at,error,force_new_decision from ({store_module.REPLY_TASK_SQL}) where id=?",
            (task_id,),
        ).fetchone()
        run = db.execute(
            f"select status,structured_error_json,lease_owner,lease_expires_at from ({AGENT_RUN_SQL}) where id=?",
            (run_id,),
        ).fetchone()
        events = db.execute(
            f"select phase,structured_error_json from ({RUN_STATE_EVENT_SQL}) where agent_run_id=? order by id",
            (run_id,),
        ).fetchall()
    assert tuple(task_row) == ("pending", "2026-10-23 17:00:00", "stale_agent_turn_recovery", 1)
    assert tuple(run[:1]) == ("failed",)
    assert json.loads(run["structured_error_json"]) == {
        "authorization_required": False,
        "code": "stale_agent_turn_recovery",
        "retryable": True,
    }
    assert run["lease_owner"] == ""
    assert run["lease_expires_at"] == ""
    assert [(row["phase"], json.loads(row["structured_error_json"])) for row in events] == [
        ("terminal_failure", {
            "authorization_required": False,
            "code": "stale_agent_turn_recovery",
            "retryable": True,
        })
    ]
