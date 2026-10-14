"""Opt-in writes against an owned SQLite snapshot; no model/provider access.

Set CEO_COPY_LIFECYCLE_SOURCE to a temporary, integrity-verified SQLite backup.
Every case gets a disposable copy, never the live database. Scripted roles stand
in for model output; real stores, worker, review and execution code remain in use.
"""
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
from uuid import uuid4

import pytest

from app.agent_context import AgentTaskContext
from app.agent_contracts import ConsumerAgentResult
from app.agent_orchestrator import AgentOrchestrator
from app.agent_result import AgentError
from app.consumer_agent import _prepare_reviewable_candidate
from app.dingtalk_models import DingTalkMessage
from app.service_message_sender import agent_message_delivery_key
from app.run_storage import TASK_AGENT_RUN_SQL
from app.store import AutoReplyStore
from app.system_action_handlers import native_dingtalk_message_handlers
from app.system_executor import SystemExecutor
from app.worker import DingTalkAutoReplyWorker
from test_agent_orchestrator import ScriptedAudit, ScriptedConsumer

from app.action_storage import CANDIDATE_REVIEW_SQL, EXTERNAL_ACTION_RESULT_SQL, REPLY_ATTEMPT_COMMON_SQL, SENT_REPLY_SQL

SOURCE = os.environ.get("CEO_COPY_LIFECYCLE_SOURCE", "")
pytestmark = pytest.mark.skipif(not SOURCE, reason="explicit owned database snapshot required")


@pytest.fixture
def copied_store(tmp_path, monkeypatch):
    source = Path(SOURCE).resolve()
    assert source.name == "source.sqlite3" and source.parent.name.startswith("ceo-copy-lifecycle.")
    assert source.parent.parent == Path("/private/tmp")
    expected = (source.parent / "snapshot.sha256").read_text()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
    target = tmp_path / "copy.sqlite3"
    shutil.copyfile(source, target)

    def refuse(*args, **kwargs):
        raise AssertionError("copy acceptance attempted a real network or subprocess operation")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(subprocess.Popen, "__init__", refuse)
    store = AutoReplyStore(target)
    with store._connect() as db:
        assert db.execute("pragma quick_check").fetchall()[0][0] == "ok"
        original_fks = {tuple(row) for row in db.execute("pragma foreign_key_check")}
    native_home = tmp_path / "native-home"
    sessions = native_home / "sessions"
    sessions.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(native_home))
    original_complete = store.complete_agent_run

    def complete_with_native(run_id, result, *, owner):
        session_id = str(uuid4())
        records = [
            {"type": "session_meta", "payload": {"id": session_id, "cwd": str(tmp_path)}},
            {"type": "event_msg", "payload": {"type": "task_started"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(result)}]}},
            {"type": "event_msg", "payload": {"type": "task_complete"}},
        ]
        native = sessions / ("rollout-" + session_id + ".jsonl")
        native.write_text("\n".join(json.dumps(row) for row in records) + "\n")
        from app.codex_history import _append_session_path_index
        _append_session_path_index(native_home, session_id, native, line_count=len(records))
        store.set_agent_run_session(run_id, session_id, owner=owner)
        attempt = store.claim_agent_runtime_attempt(run_id, "copy-scripted", "codex_cli", "local_oauth", "scripted-output")
        store.mark_agent_runtime_attempt_running(attempt.id)
        store.complete_agent_runtime_attempt(attempt.id, session_id, "codex_session:" + session_id,
            0, len(records), owner=owner)
        return original_complete(run_id, result, owner=owner)

    monkeypatch.setattr(store, "complete_agent_run", complete_with_native)
    yield store, original_fks
    with store._connect() as db:
        assert {tuple(row) for row in db.execute("pragma foreign_key_check")} == original_fks
        assert db.execute("pragma quick_check").fetchall()[0][0] == "ok"
    with store._connect() as db:
        db.execute("attach database ? as baseline", (str(source),))
        virtual = [row[0] for row in db.execute("select name from baseline.sqlite_master where type='table' and sql like 'CREATE VIRTUAL TABLE%'")]
        tables = [row[0] for row in db.execute("select name from baseline.sqlite_master where type='table' and name not like 'sqlite_%'")
            if not any(row[0] == name or row[0].startswith(name + "_") for name in virtual)]
        changed = {}
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            count = db.execute(f"select count(*) from (select * from baseline.{quoted} except select * from main.{quoted})").fetchone()[0]
            if count:
                changed[table] = count
        assert not changed, f"pre-existing application rows changed: {changed}"
    # The fixture owns this entire pytest temporary directory and all sidecars.
    shutil.rmtree(tmp_path)


class Provider:
    """In-memory provider, with assertions that dispatch follows current approval."""
    def __init__(self, store, state="sent"):
        self.store, self.state, self.calls = store, state, []
        self.verifications = []

    def send_message(self, conversation_id, body, **target):
        assert conversation_id.startswith("copy-acceptance:")
        with self.store._connect() as db:
            review = db.execute(f"select decision from ({CANDIDATE_REVIEW_SQL}) order by id desc limit 1").fetchone()
        assert review[0] == "approve"
        self.calls.append((conversation_id, body, target))
        return {"success": self.state != "failed", "result": {
            "openMessageId": "copy-provider-message", "openConversationId": conversation_id}}

    def verify_message_send_result(self, result):
        self.verifications.append(result)
        return {"state": self.state, "message_id": "copy-provider-message"}

    def reconcile_message_send(self, *args, **kwargs):
        return None


class PreparedConsumer(ScriptedConsumer):
    def run(self, task, context, *, proposal_revision, **kwargs):
        step = self.steps[0]
        if isinstance(step, ConsumerAgentResult):
            self.steps[0] = _prepare_reviewable_candidate(
                step, store=self.store, task=task, context=context,
                proposal_revision=proposal_revision, source_client=None,
            )
        return super().run(task, context, proposal_revision=proposal_revision, **kwargs)


def new_task(store):
    key = "copy-acceptance:" + str(uuid4())
    trigger = DingTalkMessage(
        open_conversation_id=key, open_message_id=key + ":trigger",
        conversation_title="Isolated copy acceptance", single_chat=False,
        sender_name="Test principal", create_time="2026-10-12T00:00:00Z",
        content="Create an isolated acceptance result.",
    )
    values = dict(
        conversation_id=key, conversation_title=trigger.conversation_title,
        single_chat=False, trigger_message_id=trigger.open_message_id,
        trigger_sender=trigger.sender_name, trigger_create_time=trigger.create_time,
        trigger_text=trigger.content, trigger_message_json=trigger.model_dump_json(),
    )
    pending = store.ensure_reply_task(**values)
    assert pending.status == "pending"
    assert store.ensure_reply_task(**values).id == pending.id
    task = store.claim_reply_task(pending.id)
    assert task.status == "processing" and task.attempts == 1
    assert store.claim_reply_task(pending.id) is None
    context = AgentTaskContext(
        task_id=task.id, channel=task.channel, conversation_id=task.conversation_id,
        conversation_title=task.conversation_title, single_chat=False,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, trigger_create_time=task.trigger_create_time,
        messages=(), materials=(), prior_receipts=(),
    )
    return task, context


def proposal(task, text="copy result", outcome="proposal"):
    return ConsumerAgentResult.model_validate({
        "outcome": outcome, "summary": text,
        "proposal": {"objective": "Copy acceptance", "actions": [{
            "description": text, "action_identity": "copy-notice", "capability": "dingtalk-chat",
            "operation": "send_group_message", "target": {"conversation_id": task.conversation_id},
            "payload": {"content": text},
        }], "sourced_facts": [], "authored_judgment": text} if outcome == "proposal" else None,
        "error": {"code": "", "retryable": False}, "risk": "low",
        "confidence": 1, "rule_coverage": 1, "information_completeness": 1,
    })


def worker_for(store, driver, context, monkeypatch):
    # These seams prevent desktop notices, reactions and external context reads.
    worker = DingTalkAutoReplyWorker(store, object(), object(), agent_orchestrator=driver)
    monkeypatch.setattr(worker, "_queued_task_prompt_context_messages", lambda *a, **kw: ([], []))
    monkeypatch.setattr(worker, "_build_agent_task_context", lambda **kw: context)
    monkeypatch.setattr(worker, "_sync_processing_reactions", lambda: None)
    monkeypatch.setattr(worker, "_dismiss_problem_notification", lambda task: None)
    monkeypatch.setattr(worker, "_notify_problem_attempt", lambda *a, **kw: None)
    return worker


@pytest.mark.parametrize("case", ["approve", "return", "reject", "no_action", "technical_retry", "provider_failed", "provider_uncertain"])
def test_reply_creation_review_execution_business_receipt_and_reopen(copied_store, monkeypatch, case):
    store, _ = copied_store
    task, context = new_task(store)
    candidates = [proposal(task)]
    reviews = ["approve"]
    expected_runs = 2
    if case in {"return", "reject"}:
        candidates.append(proposal(task, "corrected result"))
        reviews = [case, "approve"]
        expected_runs = 4
    elif case == "no_action":
        candidates = [proposal(task, outcome="no_action")]
    elif case == "technical_retry":
        candidates.insert(0, AgentError(code="temporary_read_error", retryable=True))
        expected_runs = 3
    provider = Provider(store, {"provider_failed": "failed", "provider_uncertain": "unknown"}.get(case, "sent"))
    consumer, audit = PreparedConsumer(store, *candidates), ScriptedAudit(store, *reviews)
    executor = SystemExecutor(store, native_dingtalk_message_handlers(store, provider), owner="copy-acceptance")
    driver = AgentOrchestrator(store=store, consumer=consumer, audit=audit, system_executor=executor)
    worker = worker_for(store, driver, context, monkeypatch)
    completed = worker.process_claimed_reply_task(task)
    current = store.get_reply_task(task.id)
    runs = store.list_agent_runs_for_task_generation(task.id, task.execution_generation)
    assert len(runs) == expected_runs
    completed_runs = [run for run in runs if run.status == "completed"]
    for run in completed_runs:
        attempts = store.list_agent_runtime_attempts(run.id)
        assert len(attempts) == 1 and attempts[0].session_id == run.codex_session_id
        assert attempts[0].transcript_start == 0 and attempts[0].transcript_end == 4
        assert run.final_result_json
    with store._connect() as db:
        assert "final_result_json" not in {row[1] for row in db.execute("pragma table_info(runs)")}
    if case == "technical_retry":
        assert [run.parent_agent_run_id for run in runs] == [None, None, runs[1].id]
        assert [run.turn_attempt for run in runs[:2]] == [0, 1]
        assert [run.proposal_revision for run in runs] == [0, 0, 0]
    else:
        assert [run.parent_agent_run_id for run in runs][1:] == [run.id for run in runs[:-1]]
    with store._connect() as db:
        attempts = db.execute(f"select send_status,agent_run_id from ({REPLY_ATTEMPT_COMMON_SQL}) where conversation_id=?", (task.conversation_id,)).fetchall()
        receipts = db.execute(f"select * from ({EXTERNAL_ACTION_RESULT_SQL}) where business_object_key=?", (task.business_object_key,)).fetchall()
        deliveries = db.execute(f"select * from ({SENT_REPLY_SQL}) where conversation_id=?", (task.conversation_id,)).fetchall()
        input_row = db.execute("select trigger_text,trigger_message_json,input_compacted from tasks where id=?", (current.current_input_id,)).fetchone()
    assert attempts
    if case in {"provider_failed", "provider_uncertain"}:
        assert not completed and current.status in {"failed", "pending"}
        assert not receipts and not deliveries
        assert attempts[-1][0] == "failed"
        assert input_row[2] == 0
    else:
        assert completed and current.status == "done"
        assert input_row[0:2] == ("", "") and input_row[2] == 1
        assert current.input_compacted and current.input_provenance_json
        if case == "no_action":
            assert not provider.calls and not receipts and not deliveries
            assert attempts[-1][0] == "skipped"
        else:
            assert len(provider.calls) == len(receipts) == len(deliveries) == 1
            assert provider.verifications
            assert attempts[-1][0] in {"sent", "completed"}
            assert "corrected result" in provider.calls[0][1] if case in {"return", "reject"} else True
            assert store.get_outbound_postfix_receipt("dingtalk", agent_message_delivery_key(
                business_object_key=task.business_object_key, action_identity="copy-notice",
                execution_generation=task.execution_generation, proposal_revision=(1 if case in {"return", "reject"} else 0),
            )) is not None
        reopened = AutoReplyStore(store.path)
        replay = AgentOrchestrator(store=reopened, consumer=PreparedConsumer(reopened),
            audit=ScriptedAudit(reopened), system_executor=SystemExecutor(reopened, {}, owner="reopen"))
        terminal = replay.persisted_terminal_result(reopened.get_reply_task(task.id))
        assert terminal is not None and terminal.status in {"executed", "no_action"}
        assert len(provider.calls) == (0 if case == "no_action" else 1)


def test_scheduled_create_dispatch_review_and_receipt(copied_store, tmp_path, monkeypatch):
    import test_scheduled_agent_consumer as cron
    from app.agent_cron.consumer import ScheduledAgentConsumer
    from app.dispatcher.adapters import ScheduledExecutionQueueAdapter

    store, _ = copied_store
    monkeypatch.setattr(cron, "AutoReplyStore", lambda path: store)
    # All pre-existing scheduled triggers are later than this isolated clock.
    clock = datetime(1990, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(cron, "NOW", clock)
    (tmp_path / "scheduled").mkdir()
    _, source_run, options = cron.fixture(tmp_path / "scheduled")
    dispatched = cron.dispatch(store, source_run, options, now=clock)
    assert dispatched.dispatch_status == "dispatched"
    task = store.get_reply_task(int(dispatched.execution_id))
    assert task.channel == "scheduled" and task.status == "pending"
    provider = Provider(store)
    driver = AgentOrchestrator(store=store,
        consumer=PreparedConsumer(store, proposal(task)), audit=ScriptedAudit(store, "approve"),
        system_executor=SystemExecutor(store, native_dingtalk_message_handlers(store, provider), owner="cron-copy"))
    # The scheduled envelope has a service-owned conversation; target remains a synthetic destination.
    def scheduled_proposal(context):
        parsed = proposal(task)
        action = parsed.proposal.actions[0].model_copy(update={"target": {"conversation_id": "copy-acceptance:scheduled"}})
        parsed = parsed.model_copy(update={"proposal": parsed.proposal.model_copy(update={"actions": (action,)})})
        return _prepare_reviewable_candidate(parsed, store=store, task=task, context=context,
            proposal_revision=0, source_client=None)
    driver.consumer.steps.clear()
    driver.consumer.steps.append(scheduled_proposal)
    adapter = ScheduledExecutionQueueAdapter(store, owner_alive=lambda pid: True)
    envelope, guard = cron.claim(adapter, task.id, "copy-scheduled-owner", now=clock)
    ScheduledAgentConsumer(store=store, option_service=options,
        orchestrator_factory=lambda built: driver, now=lambda: clock)(envelope, guard)
    guard.complete(clock)
    current = store.get_reply_task(task.id)
    assert current.status == "done" and current.input_compacted
    assert len(provider.calls) == 1
    assert store.get_scheduled_task_run(source_run.id).execution_id == str(task.id)
    assert len(store.list_agent_runs_for_task_generation(task.id, task.execution_generation)) == 2
    reopened = AutoReplyStore(store.path)
    assert reopened.get_scheduled_task_run(source_run.id).execution_id == str(task.id)
    assert driver.persisted_terminal_result(current).status == "executed"
    assert len(provider.calls) == 1


def test_work_summary_input_applies_business_result_and_keeps_provenance(copied_store, monkeypatch):
    from app.task_agent import process_work_item, TaskAgentRunner
    from app.task_models import WorkItem
    from test_task_agent import FakeCodexWithAuditEvents

    store, _ = copied_store
    monkeypatch.setattr("app.task_agent.memory_connector_config_issue", lambda: "offline copy acceptance")
    source_ref = "copy-acceptance:" + str(uuid4())
    item = WorkItem.model_validate({"source": {"type": "reply_attempt", "ref": source_ref,
        "title": "Copy-only business source", "created_at": "2026-10-12T00:00:00Z"},
        "summary": "补齐来源链接。", "context": {"source_conversation_kind": "group"}})
    input_id = store.enqueue_work_summary_input(item.source.type.value, source_ref, item.model_dump_json())
    assert store.enqueue_work_summary_input(item.source.type.value, source_ref, item.model_dump_json()) == input_id
    pending = store.get_work_summary_input(input_id)
    # The production snapshot has no pending work-summary inputs; refuse otherwise.
    claimed = store.claim_work_summary_inputs(limit=1)[0]
    assert claimed.id == input_id
    payload = {"project_decisions": [], "project_assessments": [], "update_summary": "Copy-only result",
        "task_decisions": [{"action": "record_candidate", "transition": "none",
            "source_excerpt": "补齐来源链接", "source_ref": source_ref,
            "title": source_ref, "missing_evidence": ["owner"]}]}
    process_work_item(store, TaskAgentRunner(FakeCodexWithAuditEvents(payload, [])), claimed)
    done = store.get_work_summary_input(input_id)
    assert done.status.value == "done"
    with store._connect() as db:
        business = db.execute("select id from business_tasks where title=?", (source_ref,)).fetchall()
        row = db.execute("select payload_json,body_compacted,body_sha256,body_bytes,source_ref from tasks where record_type='work_summary_inputs' and id=?", (input_id,)).fetchone()
        run = db.execute(f"select status from ({TASK_AGENT_RUN_SQL}) where summary_input_id=?", (input_id,)).fetchone()
    assert len(business) == 1 and run[0] == "completed"
    assert row[0] == "{}" and row[1] == 1 and row[2] and row[3] > 0 and row[4] == source_ref
    assert store.enqueue_work_summary_input(item.source.type.value, source_ref, item.model_dump_json()) == input_id
    reopened = AutoReplyStore(store.path)
    assert reopened.get_work_summary_input(input_id).status.value == "done"
    assert pending.id == done.id


def test_settlement_interruption_rolls_back_and_reopen_reuses_receipt(copied_store, monkeypatch):
    store, _ = copied_store
    task, context = new_task(store)
    provider = Provider(store)
    driver = AgentOrchestrator(store=store, consumer=PreparedConsumer(store, proposal(task)),
        audit=ScriptedAudit(store, "approve"),
        system_executor=SystemExecutor(store, native_dingtalk_message_handlers(store, provider), owner="interrupted-copy"))
    worker = worker_for(store, driver, context, monkeypatch)
    with store._connect() as db:
        db.execute(f"create trigger copy_acceptance_interrupt before update of status on tasks when old.id={task.id} and new.status='done' begin select raise(abort,'copy settlement interrupted'); end")
    with pytest.raises(sqlite3.IntegrityError, match="copy settlement interrupted"):
        worker.process_claimed_reply_task(task)
    current = store.get_reply_task(task.id)
    assert current.status == "processing" and not current.input_compacted
    assert len(provider.calls) == 1
    with store._connect() as db:
        assert db.execute(f"select count(*) from ({EXTERNAL_ACTION_RESULT_SQL}) where business_object_key=?", (task.business_object_key,)).fetchone()[0] == 1
        assert db.execute(f"select count(*) from ({REPLY_ATTEMPT_COMMON_SQL}) where conversation_id=?", (task.conversation_id,)).fetchone()[0] == 0
        db.execute("drop trigger copy_acceptance_interrupt")
    reopened = AutoReplyStore(store.path)
    recovered_driver = AgentOrchestrator(store=reopened, consumer=PreparedConsumer(reopened),
        audit=ScriptedAudit(reopened), system_executor=SystemExecutor(reopened, {}, owner="recovered-copy"))
    recovered_worker = worker_for(reopened, recovered_driver, context, monkeypatch)
    assert recovered_worker.process_claimed_reply_task(reopened.get_reply_task(task.id))
    assert reopened.get_reply_task(task.id).status == "done"
    assert reopened.get_reply_task(task.id).input_compacted
    assert len(provider.calls) == 1
    with reopened._connect() as db:
        assert db.execute(f"select count(*) from ({REPLY_ATTEMPT_COMMON_SQL}) where conversation_id=?", (task.conversation_id,)).fetchone()[0] == 1
        assert db.execute(f"select count(*) from ({EXTERNAL_ACTION_RESULT_SQL}) where business_object_key=?", (task.business_object_key,)).fetchone()[0] == 1


def test_workbench_creation_api_persists_task_turn_and_idempotent_request(copied_store, tmp_path):
    from fastapi.testclient import TestClient
    from app.audit_web import create_audit_app
    from app.workbench.store import WorkbenchStore

    store, _ = copied_store
    WorkbenchStore(store.path)

    class NonExecutingExecutor:
        workspace = tmp_path
        def recover(self): return 0
        def run_once(self): return []
        def close(self): return True

    app = create_audit_app(store.path, workbench_asset_dir=tmp_path / "missing-assets",
        workbench_workspace=tmp_path, workbench_executor=NonExecutingExecutor())
    with TestClient(app, client=("127.0.0.1", 50000), headers={"Host": "127.0.0.1:8765"}) as client:
        created = client.post("/api/workbench/tasks", json={"title": "Copy-only API task", "runtime_kind": "codex"})
        assert created.status_code == 201
        task = created.json()
        payload = {"text": "Copy-only turn", "client_request_id": str(uuid4())}
        path = f"/api/workbench/tasks/{task['id']}/turns"
        first = client.post(path, json=payload)
        assert first.status_code == 201
        second = client.post(path, json=payload)
        assert second.status_code in {200, 201} and second.json()["id"] == first.json()["id"]
        timeline = client.get(f"/api/workbench/tasks/{task['id']}/timeline")
        assert timeline.status_code == 200
    reopened = WorkbenchStore(store.path)
    assert reopened.get_task(task["id"]).title == "Copy-only API task"
    assert reopened.get_turn(first.json()["id"]).user_text == "Copy-only turn"
    with store._connect() as db:
        row = db.execute("select id from tasks where record_type='workbench_tasks' and legacy_key=?", (task["id"],)).fetchone()
        turns = db.execute("select count(*) from tasks where record_type='workbench_turns' and parent_id=?", (row[0],)).fetchone()[0]
    assert turns == 1
