import contextlib
import hashlib
import json
import sqlite3
from types import SimpleNamespace
import pytest

from app.task_run_entity_assembly import task_run_entity_assembly_objects
from app import run_entity_storage, task_entity_storage
from app.store import (
    AutoReplyStore, AgentRuntimeAttemptLeaseLostError,
    AgentRuntimeAttemptStartConflictError,
)


def _native_store(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("pragma foreign_keys=on")
    objects = task_run_entity_assembly_objects()
    for sql in objects.values():
        if sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    for sql in objects.values():
        if not sql.lstrip().lower().startswith("create table"):
            db.execute(sql)
    store = AutoReplyStore.__new__(AutoReplyStore)

    @contextlib.contextmanager
    def connect():
        with db:
            yield db

    monkeypatch.setattr(store, "_connect", connect, raising=False)
    task = task_entity_storage.create_task(db, values={
        "kind": "reply", "status": "pending", "execution_generation": "gen-1",
        "created_at": "now", "updated_at": "now",
    }, parameters={})
    input_row, _ = task_entity_storage.append_task_input(db, task_id=task["id"], values={
        "channel": "dingtalk", "conversation_id": "native", "conversation_title": "Native",
        "single_chat": 1, "trigger_message_id": "m1", "trigger_create_time": "now",
        "trigger_sender": "Derek", "trigger_text": "review", "business_object_key": "bk-1",
        "input_revision_key": "input-1",
    })
    task_entity_storage.update_task_cas(db, task_id=task["id"], expected={"status": "pending"},
        changes={"current_input_id": input_row["id"]})
    db.execute("insert into task_business_keys(task_id,business_key) values(?,?)", (task["id"], "bk-1"))
    consumer = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "execution_generation": "gen-1", "role": "consumer",
        "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    db.execute("insert into task_business_keys(task_id,business_key) values(?,?)", (task["id"], "bk-1-alternate"))
    db.commit()
    return store, db, task, consumer


def _approved_native_execution(store, db, task, consumer, actions):
    body = {
        "outcome": "proposal", "summary": "Native execution fixture",
        "stage_index": 0, "predecessor_review_id": None,
        "proposal": {"objective": "Native execution", "actions": actions,
            "sourced_facts": [], "authored_judgment": "Supported"},
        "decision_options": [],
    }
    with db:
        candidate = store._adopt_review_candidate_in_connection(db, consumer, body)
        audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"],
            "execution_generation": "gen-1", "role": "audit",
            "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        review = store._adopt_candidate_review_in_connection(db, consumer["id"], audit["id"], {
            "outcome": "approve", "candidate_digest": candidate["candidate_digest"],
        })
    return review


def _native_actions(count):
    return [{
        "description": f"Action {index}", "action_identity": f"native-{index}",
        "capability": "test", "operation": "act",
        "target": {"id": str(index)}, "payload": {"value": str(index)},
    } for index in range(count)]


def test_runtime_attempt_claim_and_agent_history_use_native_parent_owner(monkeypatch):
    store, db, task, _completed_consumer = _native_store(monkeypatch)
    run = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "execution_generation": "gen-1",
        "role": "consumer", "proposal_revision": 0, "turn_attempt": 1,
        "status": "running", "lease_owner": "runtime-owner",
        "lease_expires_at": "2026-10-24 00:00:00",
        "created_at": "now", "updated_at": "now",
    })
    db.commit()

    attempt = store.claim_agent_runtime_attempt(
        run["id"], "codex_oauth", "codex_cli", "local_oauth", "gpt-6",
    )

    assert attempt.parent_id == run["id"]
    assert not hasattr(attempt, "agent_run_id")
    assert not hasattr(attempt, "result_envelope_json")
    assert store.get_agent_runtime_attempt(attempt.id) == attempt
    assert store.list_agent_runtime_attempts(run["id"]) == [attempt]
    assert store.list_runtime_operation_attempts("memory", "memory_write_event:1") == []
    row = db.execute("select parent_id,workload_kind,workload_key from runtime_attempts where id=?",
                     (attempt.id,)).fetchone()
    assert tuple(row) == (run["id"], "agent_run", str(run["id"]))

    started = store.mark_agent_runtime_attempt_running_once(attempt.id, now="2026-10-23 12:00:00")
    sessioned = store.set_agent_runtime_attempt_session(
        started.id, "native-session", "transcript://native", transcript_start=4,
        now="2026-10-23 12:00:01",
    )
    effected = store.note_runtime_attempt_effect_started(
        sessioned.id, "2026-10-23 12:00:02",
    )
    assert (effected.status, effected.session_id, effected.transcript_start,
            effected.transcript_end, effected.first_effect_started_at) == (
        "running", "native-session", 4, 4, "2026-10-23 12:00:02",
    )
    result = {
        "outcome": "no_action", "summary": "No action needed",
        "stage_index": 0, "predecessor_review_id": None,
    }
    result_json = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    envelope = json.dumps({
        "schema_id": "consumer.v1",
        "result_ref": {
            "agent_run_id": run["id"],
            "result_sha256": hashlib.sha256(result_json.encode()).hexdigest(),
        },
    }, separators=(",", ":"))
    completed = store.complete_agent_runtime_attempt(
        effected.id, "native-session", "transcript://native", 4, 9,
        result_schema_id="consumer.v1", result_envelope_json=envelope,
        agent_run_final_result=result, agent_run_transcript_end=9,
        owner="runtime-owner", now="2026-10-23 12:00:03",
    )
    assert completed.status == "completed"
    assert tuple(db.execute("select status,transcript_end from runs where id=?", (run["id"],)).fetchone()) == ("completed", 9)
    assert store.adopted_candidate_for_consumer_run(run["id"])["candidate_json"] == result_json


def test_routed_execution_renews_native_agent_parent_lease(monkeypatch, tmp_path):
    from datetime import UTC, datetime, timedelta

    from app.agent_runtime_config import load_runtime_config
    from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
    from app.agent_runtime_router import AgentRuntimeRouter, RoutedCodexExecution

    store, db, task, _completed_consumer = _native_store(monkeypatch)
    store.path = tmp_path / "native-agent-renewal.sqlite3"
    now = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    run = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "execution_generation": "gen-1",
        "role": "consumer", "proposal_revision": 0, "turn_attempt": 2,
        "status": "running", "lease_owner": "agent-owner",
        "lease_expires_at": "2026-10-23 12:01:00",
        "operation_id": "", "session_id": "", "transcript_start": 0,
        "transcript_end": 0, "structured_error_json": "",
        "started_at": "now", "completed_at": "",
        "created_at": "now", "updated_at": "now",
    })
    db.commit()
    attempt = store.claim_agent_runtime_attempt(
        run["id"], "codex_oauth", "codex_cli", "local_oauth", "gpt-6",
    )
    started = store.mark_agent_runtime_attempt_running_once(attempt.id, now=now)
    assert started.parent_id == run["id"]

    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth"})
    snapshot = RuntimeCapabilitySnapshot(
        route_name="codex_oauth", capabilities=frozenset({"structured_output"}),
        healthy=True, checked_at="2026-10-23T11:59:00+00:00",
        expires_at="2026-10-23T12:10:00+00:00",
    )
    router = AgentRuntimeRouter(
        routes=config.routes, store=store, snapshots={"codex_oauth": snapshot},
        now=lambda: now,
    )
    execution = RoutedCodexExecution(
        store=store, config=config, router=router, adapter=object(), executor=lambda *_a, **_k: None,
        owner="agent-owner", lease_seconds=120, now=lambda: now,
    )

    renewed = execution._renew_attempt_parent_lease(started)

    persisted = store.get_agent_runtime_attempt(attempt.id)
    assert renewed == persisted
    assert renewed is not None and renewed.parent_id == run["id"]
    native_run = db.execute(
        "select lease_owner,lease_expires_at from runs where id=?", (run["id"],)
    ).fetchone()
    assert tuple(native_run) == (
        "agent-owner", (now + timedelta(seconds=execution._lease_seconds)).strftime("%Y-%m-%d %H:%M:%S"),
    )


def test_professional_parent_does_not_change_operation_lease_or_finish_parent(monkeypatch):
    store, db, _reply_task, _consumer = _native_store(monkeypatch)
    task = task_entity_storage.create_task(db, values={
        "kind": "work_summary", "status": "running",
        "execution_generation": "professional-gen",
        "created_at": "now", "updated_at": "now",
    }, parameters={})
    run_entity_storage.create_run(db, values={
        "task_id": None, "status": "completed", "created_at": "now", "updated_at": "now",
    })
    run = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "status": "running",
        "created_at": "now", "updated_at": "now",
    })
    run_entity_storage.set_run_provenance(db, run_id=run["id"], source_type="task_agent_runs")
    newer_run = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    run_entity_storage.set_run_provenance(
        db, run_id=newer_run["id"], source_type="task_agent_runs",
    )
    db.commit()

    attempt = store.claim_runtime_operation_attempt(
        "task", str(run["id"]), "codex_oauth", "codex_cli", "local_oauth", "gpt-6",
        owner="operation-owner", lease_seconds=900, now="2026-10-23 12:00:00",
    )
    assert attempt.parent_id == run["id"]
    assert run["id"] != task["id"] and newer_run["id"] != run["id"]
    assert run["execution_generation"] is None and run["role"] is None
    assert (attempt.lease_owner, attempt.lease_expires_at) == (
        "operation-owner", "2026-10-23 12:15:00",
    )
    assert store.list_agent_runtime_attempts(run["id"]) == []
    assert store.list_runtime_operation_attempts("task", str(run["id"])) == [attempt]

    started = store.mark_agent_runtime_attempt_running_once(
        attempt.id, owner="operation-owner", lease_seconds=900,
        now="2026-10-23 12:00:01",
    )
    sessioned = store.set_agent_runtime_attempt_session(
        started.id, "operation-session", "transcript://operation",
        owner="operation-owner", now="2026-10-23 12:00:02",
    )
    completed = store.complete_agent_runtime_attempt(
        sessioned.id, "operation-session", "transcript://operation", 0, 3,
        owner="operation-owner", now="2026-10-23 12:00:03",
    )
    assert completed.status == "completed"
    assert store.complete_agent_runtime_attempt(
        sessioned.id, "operation-session", "transcript://operation", 0, 3,
        owner="operation-owner", now="2026-10-23 12:00:04",
    ) == completed
    assert tuple(db.execute("select status,session_id from runs where id=?", (run["id"],)).fetchone()) == (
        "running", None,
    )


def test_routed_execution_uses_native_store_for_professional_operation(monkeypatch, tmp_path):
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.agent_runtime_config import load_runtime_config
    from app.agent_runtime_contracts import RuntimeCapabilitySnapshot
    from app.agent_runtime_router import (
        AgentRuntimeRouter, CodexCommandFactory, RoutedCodexExecution,
        RoutedResultCodec,
    )
    from app.process_runner import ProcessRunResult
    from tests.support.native_protocol import install_protocol_native_trajectories

    class SubstituteAdapter:
        def build_command(self, **_kwargs):
            return ["codex-substitute"]

        def build_env(self, _route):
            return {}

    class SubstituteExecutor:
        session_id = str(uuid4())

        def __call__(self, _command, **_kwargs):
            records = [
                {"type": "thread.started", "thread_id": self.session_id},
                {"type": "item.completed", "item": {
                    "type": "agent_message", "text": "7",
                }},
                {"type": "turn.completed"},
            ]
            return ProcessRunResult(0, "\n".join(json.dumps(row) for row in records), "")

    install_protocol_native_trajectories(tmp_path, monkeypatch, SubstituteExecutor)
    store, db, _reply_task, _consumer = _native_store(monkeypatch)
    task = task_entity_storage.create_task(db, values={
        "kind": "work_summary", "status": "running",
        "execution_generation": "professional-gen",
        "created_at": "now", "updated_at": "now",
    }, parameters={})
    run_entity_storage.create_run(db, values={
        "task_id": None, "status": "completed", "created_at": "now", "updated_at": "now",
    })
    run = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "status": "running",
        "created_at": "now", "updated_at": "now",
    })
    run_entity_storage.set_run_provenance(db, run_id=run["id"], source_type="task_agent_runs")
    db.commit()
    assert run["id"] != task["id"]
    meeting_run = run_entity_storage.create_run(db, values={
        "task_id": None, "status": "running", "created_at": "now", "updated_at": "now",
    })
    run_entity_storage.set_run_provenance(
        db, run_id=meeting_run["id"], source_type="meeting_alignment_runs", job_id=4908,
    )
    db.commit()
    config = load_runtime_config({"CEO_AGENT_RUNTIME_ROUTES": "codex_oauth"})
    snapshot = RuntimeCapabilitySnapshot(
        route_name="codex_oauth", capabilities=frozenset({"structured_output"}),
        healthy=True, checked_at="2026-10-23T11:59:00+00:00",
        expires_at="2026-10-23T12:10:00+00:00",
    )
    router = AgentRuntimeRouter(
        routes=config.routes, store=store,
        snapshots={"codex_oauth": snapshot},
        now=lambda: datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
    )
    execution = RoutedCodexExecution(
        store=store, config=config, router=router, adapter=SubstituteAdapter(),
        executor=SubstituteExecutor(), owner="routed-owner", lease_seconds=120,
        session_id_parser=lambda _stdout: "native-routed-session",
        session_line_counter=lambda _session_id: 3,
        now=lambda: datetime(2026, 10, 23, 12, 0, tzinfo=UTC),
    )

    result = execution.execute(
        workload_kind="task", workload_key=str(run["id"]), prompt="Return 7",
        command_factory=CodexCommandFactory.standard(developer_instructions="Return one integer."),
        parser=lambda raw: int(json.loads(raw.splitlines()[1])["item"]["text"]),
        result_codec=RoutedResultCodec.integer(schema_id="test.integer.v1"),
        required_capabilities=frozenset({"structured_output"}),
    )

    assert result.value == 7
    attempt = store.get_agent_runtime_attempt(result.attempt_id)
    assert attempt is not None and attempt.parent_id == run["id"]
    assert attempt.workload_kind == "task" and attempt.status == "completed"
    assert tuple(db.execute("select status,session_id from runs where id=?", (run["id"],)).fetchone()) == (
        "running", None,
    )

    structured_task = task_entity_storage.create_task(db, values={
        "kind": "okr_review", "status": "processing",
        "created_at": "now", "updated_at": "now",
    }, parameters={
        "conversation_id": "native-okr", "conversation_title": "OKR",
        "period_end": "2026-10-23", "period_label": "Q4", "period_start": "2026-10-01",
        "trigger_message_id": "okr-message", "trigger_sender": "Derek",
        "trigger_text": "Review this OKR",
    })
    db.commit()
    assert db.execute(
        "select count(*) from runs where task_id=?", (structured_task["id"],)
    ).fetchone()[0] == 0
    structured_result = execution.execute(
        workload_kind="structured", workload_key=str(structured_task["id"]),
        prompt="Return 7", command_factory=CodexCommandFactory.standard(
            developer_instructions="Return one integer.",
        ), parser=lambda raw: int(json.loads(raw.splitlines()[1])["item"]["text"]),
        result_codec=RoutedResultCodec.integer(schema_id="test.integer.v1"),
        required_capabilities=frozenset({"structured_output"}),
    )
    structured_attempt = store.get_agent_runtime_attempt(structured_result.attempt_id)
    assert structured_result.value == 7
    assert structured_attempt is not None and structured_attempt.parent_id is None
    assert structured_attempt.status == "completed"
    assert db.execute(
        "select count(*) from runs where task_id=?", (structured_task["id"],)
    ).fetchone()[0] == 0
    pending_structured = task_entity_storage.create_task(db, values={
        "kind": "okr_review", "status": "pending",
        "created_at": "now", "updated_at": "now",
    }, parameters={
        "conversation_id": "native-okr-pending", "conversation_title": "OKR",
        "period_end": "2026-10-23", "period_label": "Q4", "period_start": "2026-10-01",
        "trigger_message_id": "okr-message-pending", "trigger_sender": "Derek",
        "trigger_text": "Review this OKR",
    })
    db.commit()
    with pytest.raises(ValueError, match="runtime operation parent"):
        store.claim_runtime_operation_attempt(
            "structured", str(pending_structured["id"]), "codex_oauth", "codex_cli",
            "local_oauth", "gpt-6", owner="operation-owner", lease_seconds=30,
            now="2026-10-23 12:00:04",
        )

    retry = store.claim_runtime_operation_attempt(
        "task", str(run["id"]), "codex_oauth", "codex_cli", "local_oauth", "gpt-6",
        owner="operation-owner", lease_seconds=900, now="2026-10-23 12:00:04",
    )
    decision_repair = store.claim_runtime_operation_attempt(
        "task", f"{run['id']}:decision_repair.1", "codex_oauth", "codex_cli",
        "local_oauth", "gpt-6", owner="operation-owner", lease_seconds=30,
        now="2026-10-23 12:00:05",
    )
    assert (retry.parent_id, retry.attempt_number) == (run["id"], 2)
    assert (decision_repair.parent_id, decision_repair.attempt_number) == (run["id"], 1)
    failed = store.fail_agent_runtime_attempt(
        retry.id, "process", "substitute_failed", True,
        owner="operation-owner", now="2026-10-23 12:00:06",
    )
    assert failed.status == "failed"
    assert store.fail_agent_runtime_attempt(
        retry.id, "process", "substitute_failed", True,
        owner="operation-owner", now="2026-10-23 12:00:07",
    ) == failed
    assert tuple(db.execute("select status,structured_error_json from runs where id=?", (run["id"],)).fetchone()) == (
        "running", None,
    )
    with pytest.raises(AgentRuntimeAttemptLeaseLostError, match="lease lost"):
        store.renew_runtime_operation_attempt_lease(
            decision_repair.id, owner="operation-owner", lease_seconds=30,
            now="2026-10-23 12:01:00",
        )
    reclaimed = store.recover_expired_runtime_operation_attempt(
        "task", f"{run['id']}:decision_repair.1", now="2026-10-23 12:01:00",
    )
    assert reclaimed is not None and reclaimed.failure_code == "runtime_lease_expired"
    successor = store.claim_runtime_operation_attempt(
        "task", f"{run['id']}:decision_repair.1", "codex_oauth", "codex_cli",
        "local_oauth", "gpt-6", owner="operation-owner", lease_seconds=30,
        now="2026-10-23 12:01:01",
    )
    assert (successor.parent_id, successor.attempt_number) == (run["id"], 2)
    meeting_attempt = store.claim_runtime_operation_attempt(
        "meeting", str(meeting_run["id"]), "codex_oauth", "codex_cli", "local_oauth",
        "gpt-6", owner="meeting-owner", lease_seconds=30, now="2026-10-23 12:02:00",
    )
    assert meeting_attempt.parent_id == meeting_run["id"]
    assert meeting_attempt.workload_kind == "meeting"
    with pytest.raises(AgentRuntimeAttemptStartConflictError, match="process start"):
        store.mark_agent_runtime_attempt_running_once(
            meeting_attempt.id, owner="wrong-owner", now="2026-10-23 12:02:01",
        )
    started_meeting_attempt = store.mark_agent_runtime_attempt_running_once(
        meeting_attempt.id, owner="meeting-owner", now="2026-10-23 12:02:01",
    )
    assert started_meeting_attempt.status == "running"
    store.complete_agent_runtime_attempt(
        meeting_attempt.id, "meeting-session", "codex_session:meeting-session", 1, 4,
        owner="meeting-owner", now="2026-10-23 12:02:02",
    )
    assert tuple(db.execute("select status,session_id from runs where id=?", (meeting_run["id"],)).fetchone()) == (
        "running", None,
    )
    assert tuple(db.execute("select status,session_id from runs where id=?", (run["id"],)).fetchone()) == (
        "running", None,
    )


def test_system_executor_uses_native_run_progress_and_receipt_owner(monkeypatch):
    from app.agent_contracts import ConsumerAgentResult
    from app.system_executor import ActionOutcome, SystemExecutor

    store, db, task, consumer = _native_store(monkeypatch)
    candidate_result = ConsumerAgentResult.model_validate({
        "outcome": "proposal", "summary": "Do work",
        "proposal": {"objective": "Do work", "actions": [{
            "description": "Act", "action_identity": "first", "capability": "test",
            "operation": "act", "target": {"id": "1"}, "payload": {"value": "x"},
        }], "sourced_facts": [], "authored_judgment": "Supported"},
        "decision_options": [], "needs_human_reason": None, "decision_basis": None,
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "low", "confidence": 1.0, "rule_coverage": 1.0,
        "information_completeness": 1.0,
    })
    with db:
        adopted = store._adopt_review_candidate_in_connection(
            db, consumer, candidate_result.model_dump(mode="json"))
        audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"],
            "execution_generation": "gen-1", "role": "audit",
            "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        review = store._adopt_candidate_review_in_connection(db, consumer["id"], audit["id"], {
            "outcome": "approve", "candidate_digest": adopted["candidate_digest"],
        })
    store.get_reply_task = lambda _task_id: SimpleNamespace(
        id=task["id"], execution_generation="gen-1", business_object_key="bk-1",
        conversation_id="native", trigger_message_id="m1")

    class FakeEffect:
        calls = 0

        def dispatch(self, _action, *, action_key, candidate):
            self.calls += 1
            store.invalidate_review_candidate(consumer["id"], "fixture_invalidation_during_dispatch")
            return ActionOutcome("verified", {
                "verification": {"state": "sent"},
                "provider_result": {"id": "provider-1"},
            })

        def reconcile(self, *_args, **_kwargs):
            raise AssertionError("unexpected reconciliation")

    effect = FakeEffect()
    result = SystemExecutor(store, {("test", "act"): effect}, owner="native-worker").execute(
        SimpleNamespace(id=task["id"], execution_generation="gen-1"),
        consumer["id"], review["id"])
    assert result.outcome == "executed"
    assert effect.calls == 1
    progress = store.get_candidate_execution(consumer["id"])
    assert progress["id"] == consumer["id"]
    assert progress["status"] == "done"
    assert store.get_review_candidate(consumer["id"])["invalidated_at"]
    receipt = db.execute("select execution_run_id,status,provider_receipt_id from action_receipts "
                         "where execution_run_id=? and action_index=0", (consumer["id"],)).fetchone()
    assert tuple(receipt[:2]) == (consumer["id"], "verified")
    assert receipt["provider_receipt_id"] is not None
    assert db.execute("select count(*) from actions where record_type='candidate_execution_alias'").fetchone()[0] == 0
    attempts = store.list_candidate_action_attempts(consumer["id"])
    assert len(attempts) == 1
    assert attempts[0]["execution_id"] == consumer["id"]
    assert attempts[0]["status"] == "verified"
    external = store.get_candidate_external_action(attempts[0]["external_action_key"])
    assert external["provider_receipt_id"] == receipt["provider_receipt_id"]
    verified_source = store.get_verified_action_source(attempts[0]["external_action_key"])
    assert verified_source["candidate_id"] == consumer["id"]
    assert store.list_verified_candidate_actions(task["id"])[0]["execution_id"] == consumer["id"]


def test_historical_refusal_uses_each_tasks_current_input_only(monkeypatch):
    store, db, _task, _consumer = _native_store(monkeypatch)
    old_task = task_entity_storage.create_task(db, values={
        "kind": "reply", "status": "failed", "execution_generation": "old-gen",
        "created_at": "now", "updated_at": "now",
    }, parameters={})
    task_entity_storage.append_task_input(db, task_id=old_task["id"], values={
        "channel": "dingtalk", "conversation_id": "old", "conversation_title": "Old", "single_chat": 1,
        "trigger_message_id": "a", "trigger_create_time": "now", "trigger_sender": "Derek",
        "trigger_text": "old request", "business_object_key": "business-A",
        "input_revision_key": "old-A",
    })
    input_b, _ = task_entity_storage.append_task_input(db, task_id=old_task["id"], values={
        "channel": "dingtalk", "conversation_id": "old", "conversation_title": "Old", "single_chat": 1,
        "trigger_message_id": "b", "trigger_create_time": "now", "trigger_sender": "Derek",
        "trigger_text": "current request", "business_object_key": "business-B",
        "input_revision_key": "new-B",
    })
    task_entity_storage.update_task_cas(db, task_id=old_task["id"],
        expected={"current_input_id": None}, changes={"current_input_id": input_b["id"]})
    refusal_run = run_entity_storage.create_run(db, values={
        "task_id": old_task["id"], "execution_generation": "old-gen", "role": "consumer",
        "proposal_revision": 0, "turn_attempt": 0, "status": "failed",
        "created_at": "now", "updated_at": "now",
    })
    db.execute("update runs set structured_error_json=? where id=?",
        (json.dumps({"code": "provider_risk_rejected"}), refusal_run["id"]))
    db.commit()

    store._check_historical_runtime_refusal(db, "business-A")
    with pytest.raises(ValueError, match="historical_runtime_risk_refusal"):
        store._check_historical_runtime_refusal(db, "business-B")


def test_native_execution_claim_reclaims_expired_lease_and_finishes_selected_stop(monkeypatch):
    store, db, task, consumer = _native_store(monkeypatch)
    body = {"outcome": "needs_human", "stage_index": 0, "predecessor_review_id": None,
        "decision_options": [{"key": "stop", "terminal_outcome": "skipped", "reason": "stop"}]}
    with db:
        adopted = store._adopt_review_candidate_in_connection(db, consumer, body)
        audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        review = store._adopt_candidate_review_in_connection(db, consumer["id"], audit["id"], {
            "outcome": "approve", "candidate_digest": adopted["candidate_digest"],
        })
        refusal = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "execution_generation": "gen-1", "role": "consumer",
            "proposal_revision": 1, "turn_attempt": 0, "status": "failed",
            "created_at": "now", "updated_at": "now",
        })
        db.execute("update runs set structured_error_json=? where id=?",
            (json.dumps({"code": "provider_risk_rejected"}), refusal["id"]))
    selection = store.select_candidate_option(consumer["id"], review["id"], "stop")
    first = store.claim_candidate_execution(consumer["id"], review["id"], "worker-a", 60)
    assert first["id"] == consumer["id"]
    assert first["selection_id"] == selection["id"]
    with pytest.raises(ValueError, match="execution lease lost"):
        store.renew_candidate_execution_claim(consumer["id"], "worker-b", 60)
    db.execute("update run_execution_progress set lease_expires_at='2000-01-01 00:00:00' where consumer_run_id=?",
        (consumer["id"],))
    db.commit()
    reclaimed = store.claim_candidate_execution(consumer["id"], review["id"], "worker-b", 60)
    assert reclaimed["id"] == consumer["id"]
    assert reclaimed["lease_owner"] == "worker-b"
    finished = store.finish_candidate_execution(consumer["id"], "worker-b", "skipped", {"outcome": "skipped"})
    assert finished["status"] == "skipped"
    assert finished["result_json"] == '{"outcome":"skipped"}'


def test_native_no_action_can_finish_as_skipped_without_selection(monkeypatch):
    store, db, task, consumer = _native_store(monkeypatch)
    body = {"outcome": "no_action", "summary": "Nothing to do", "stage_index": 0,
        "predecessor_review_id": None, "decision_options": []}
    with db:
        adopted = store._adopt_review_candidate_in_connection(db, consumer, body)
        audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        review = store._adopt_candidate_review_in_connection(db, consumer["id"], audit["id"], {
            "outcome": "approve", "candidate_digest": adopted["candidate_digest"],
        })
        refusal = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "execution_generation": "gen-1", "role": "consumer",
            "proposal_revision": 1, "turn_attempt": 0, "status": "failed",
            "created_at": "now", "updated_at": "now",
        })
        db.execute("update runs set structured_error_json=? where id=?",
            (json.dumps({"code": "provider_risk_rejected"}), refusal["id"]))
    execution = store.claim_candidate_execution(consumer["id"], review["id"], "worker", 60)
    assert execution["selection_id"] is None
    finished = store.finish_candidate_execution(consumer["id"], "worker", "skipped", {"outcome": "no_action"})
    assert finished["status"] == "skipped"


@pytest.mark.parametrize("retry_status", ["retry", "uncertain"])
def test_native_retry_and_uncertain_reclaim_same_progress_owner(monkeypatch, retry_status):
    store, db, task, consumer = _native_store(monkeypatch)
    review = _approved_native_execution(store, db, task, consumer, _native_actions(1))
    first = store.claim_candidate_execution(consumer["id"], review["id"], "worker-a", 60)
    prior_result = {"status": retry_status, "reason": "fixture recovery"}
    finished = store.finish_candidate_execution(consumer["id"], "worker-a", retry_status, prior_result)
    assert finished["status"] == retry_status
    assert json.loads(finished["result_json"]) == prior_result

    reclaimed = store.claim_candidate_execution(consumer["id"], review["id"], "worker-b", 60)
    assert reclaimed["id"] == consumer["id"] == first["id"]
    assert reclaimed["status"] == "running"
    assert reclaimed["lease_owner"] == "worker-b"
    assert json.loads(reclaimed["result_json"]) == prior_result
    assert db.execute("select count(*) from run_execution_progress where consumer_run_id=?",
        (consumer["id"],)).fetchone()[0] == 1
    assert db.execute("select count(*) from actions where record_type='candidate_execution_alias'").fetchone()[0] == 0


def test_native_repeated_provider_ack_is_idempotent_and_conflict_is_preserved(monkeypatch):
    store, db, task, consumer = _native_store(monkeypatch)
    review = _approved_native_execution(store, db, task, consumer, _native_actions(1))
    execution = store.claim_candidate_execution(consumer["id"], review["id"], "worker", 60)
    action_key = "native-ack-idempotency"
    attempt = store.begin_candidate_action(execution["id"], "worker", 0, action_key)
    provider_result = {"verification": {"state": "sent"}, "provider_result": {"id": "receipt-1"}}
    first = store.record_candidate_external_action(
        execution["id"], "worker", 0, action_key, "act", {"id": "0"}, provider_result)
    with db:
        first_attempt = db.execute("select id,execution_run_id,action_index,status,provider_receipt_id "
            "from action_receipts where id=?", (attempt["id"],)).fetchone()
        provider_rows = [tuple(row) for row in db.execute(
            "select id,provider_receipt_id,provider_result_json,result_digest,agent_run_id "
            "from action_receipts where action_id=? and provider_result_json is not null order by id",
            (attempt["action_owner_id"],))]
    assert tuple(first_attempt[:4]) == (attempt["id"], consumer["id"], 0, "verified")
    assert first_attempt["provider_receipt_id"] == first["provider_receipt_id"]
    assert len(provider_rows) == 1
    assert provider_rows[0][2] == json.dumps(provider_result, sort_keys=True, separators=(",", ":"))
    assert provider_rows[0][4] == consumer["id"]

    repeated = store.record_candidate_external_action(
        execution["id"], "worker", 0, action_key, "act", {"id": "0"}, provider_result)
    assert repeated["provider_receipt_id"] == first["provider_receipt_id"]
    with db:
        assert [tuple(row) for row in db.execute(
            "select id,provider_receipt_id,provider_result_json,result_digest,agent_run_id "
            "from action_receipts where action_id=? and provider_result_json is not null order by id",
            (attempt["action_owner_id"],))] == provider_rows

    conflicting_ack = {"verification": {"state": "sent"}, "provider_result": {"id": "receipt-2"}}
    with pytest.raises(ValueError, match="conflicting external action result"):
        store.record_candidate_external_action(
            execution["id"], "worker", 0, action_key, "act", {"id": "0"}, conflicting_ack)
    with db:
        assert [tuple(row) for row in db.execute(
            "select id,provider_receipt_id,provider_result_json,result_digest,agent_run_id "
            "from action_receipts where action_id=? and provider_result_json is not null order by id",
            (attempt["action_owner_id"],))] == provider_rows
    assert store.get_candidate_external_action(action_key)["provider_receipt_id"] == first["provider_receipt_id"]


def test_native_done_requires_every_action_verified(monkeypatch):
    store, db, task, consumer = _native_store(monkeypatch)
    review = _approved_native_execution(store, db, task, consumer, _native_actions(2))
    execution = store.claim_candidate_execution(consumer["id"], review["id"], "worker", 60)
    first_key, second_key = "native-done-0", "native-done-1"
    first_attempt = store.begin_candidate_action(execution["id"], "worker", 0, first_key)
    store.record_candidate_external_action(
        execution["id"], "worker", 0, first_key, "act", {"id": "0"},
        {"verification": {"state": "sent"}, "provider_result": {"id": "done-0"}})
    with pytest.raises(ValueError, match="execution actions are not verified"):
        store.finish_candidate_execution(execution["id"], "worker", "done", {"outcome": "done"})

    second_attempt = store.begin_candidate_action(execution["id"], "worker", 1, second_key)
    assert second_attempt["status"] == "dispatched"
    with pytest.raises(ValueError, match="execution actions are not verified"):
        store.finish_candidate_execution(execution["id"], "worker", "done", {"outcome": "done"})
    store.record_candidate_external_action(
        execution["id"], "worker", 1, second_key, "act", {"id": "1"},
        {"verification": {"state": "sent"}, "provider_result": {"id": "done-1"}})
    finished = store.finish_candidate_execution(execution["id"], "worker", "done", {"outcome": "done"})
    assert finished["status"] == "done"
    attempts = store.list_candidate_action_attempts(consumer["id"])
    assert [row["id"] for row in attempts] == [first_attempt["id"], second_attempt["id"]]
    assert [row["status"] for row in attempts] == ["verified", "verified"]


def test_native_store_adopts_candidate_review_selection_and_supplement(monkeypatch, tmp_path):
    from app import native_trajectory

    store, db, task, consumer = _native_store(monkeypatch)
    body = {
        "outcome": "needs_human", "summary": "review",
        "stage_index": 0, "predecessor_review_id": None,
        "decision_options": [
            {"key": "execute", "plan": {"actions": [{"operation": "send", "target": {"user_id": "u1"}}]}},
            {"key": "stop", "terminal_outcome": "skipped", "reason": "stop"},
        ],
    }
    with db:
        adopted = store._adopt_review_candidate_in_connection(db, consumer, body)
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert adopted["id"] == consumer["id"]
    assert adopted["business_object_key"] == "bk-1"
    assert adopted["candidate_json"] == json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert adopted["candidate_digest"] == digest
    assert store.persist_review_candidate(SimpleNamespace(id=task["id"], execution_generation="gen-1"),
        SimpleNamespace(id=consumer["id"]), body)["id"] == consumer["id"]

    with db:
        audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
    review_body = {"outcome": "approve", "candidate_digest": digest}
    native = tmp_path / "audit.jsonl"
    native.write_text(json.dumps({"type": "response_item", "payload": {"type": "message",
        "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(review_body)}]}}) + "\n")
    with db:
        run_entity_storage.create_runtime_attempt(db, values={
        "parent_id": audit["id"], "workload_kind": "agent_run", "workload_key": str(audit["id"]),
        "attempt_number": 1, "route_name": "fixture", "runtime_kind": "codex_cli",
        "credential_mode": "local_oauth", "model": "gpt-6-luna", "session_mode": "fresh",
        "source_session_id": "", "attempt_purpose": "normal", "validation_retry_policy_id": "",
        "validation_result_schema_id": "", "session_id": "native-session", "transcript_reference": "fixture",
        "transcript_start": 0, "transcript_end": 1, "status": "completed", "failure_class": "",
        "failure_code": "", "failover_permitted": 0, "lease_owner": "", "lease_expires_at": "",
        "first_effect_started_at": "", "result_schema_id": "", "started_at": "now",
        "finished_at": "now", "created_at": "now", "updated_at": "now",
        })
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *_args, **_kwargs: native)
    review = store.record_candidate_review(consumer["id"], audit["id"], review_body)
    assert review["id"] != audit["id"]
    assert review["audit_run_id"] == audit["id"]
    assert store.record_candidate_review(consumer["id"], audit["id"], review_body) == review
    assert store.get_candidate_review_for_audit_run(audit["id"])["candidate_id"] == consumer["id"]
    assert store.reviewed_candidate_for_audit_run(audit["id"])["candidate_id"] == consumer["id"]
    assert store.current_reviewed_candidate(task["id"], "gen-1")["review_id"] == review["id"]
    assert store.current_reviewed_candidate(task["id"], "gen-1")["audit_run_id"] == audit["id"]

    later_reviews = []
    for decision in ("return", "reject"):
        with db:
            another_audit = run_entity_storage.create_run(db, values={
                "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
                "role": "audit", "proposal_revision": 0, "turn_attempt": len(later_reviews) + 1,
                "status": "completed", "created_at": "now", "updated_at": "now",
            })
            result = {"outcome": decision, "candidate_digest": digest}
            stored = store._adopt_candidate_review_in_connection(db, consumer["id"], another_audit["id"], result)
        later_reviews.append((another_audit, result, stored))
        assert stored["id"] != another_audit["id"]
        assert stored["audit_run_id"] == another_audit["id"]
        assert stored["candidate_id"] == consumer["id"]
        assert store.get_candidate_review_for_audit_run(another_audit["id"])["decision"] == decision
    with db:
        try:
            store._adopt_candidate_review_in_connection(
                db, consumer["id"], later_reviews[0][0]["id"],
                {"outcome": "reject", "candidate_digest": digest},
            )
        except ValueError as exc:
            assert str(exc) == "review content conflict"
        else:
            raise AssertionError("conflicting Audit review was accepted")

    selected = store.select_candidate_option(consumer["id"], review["id"], "execute")
    assert selected["candidate_id"] == consumer["id"]
    assert selected["review_id"] == review["id"]
    assert selected["audit_run_id"] == audit["id"]
    assert json.loads(selected["branch_json"])["key"] == "execute"
    assert store.select_candidate_option(consumer["id"], review["id"], "execute")["id"] == selected["id"]
    current_selection = store.get_candidate_selection(consumer["id"])
    assert current_selection["review_id"] == review["id"]
    assert current_selection["audit_run_id"] == audit["id"]
    assert store.get_candidate_execution(consumer["id"]) is None

    claimed = store.claim_candidate_execution(consumer["id"], review["id"], "native-worker", 300)
    assert claimed["id"] == consumer["id"]
    assert claimed["review_id"] == review["id"]
    assert claimed["audit_run_id"] == audit["id"]
    assert claimed["selection_id"] == current_selection["id"]
    assert db.execute("select count(*) from run_execution_progress where consumer_run_id=?",
                      (consumer["id"],)).fetchone()[0] == 1
    assert db.execute("select count(*) from actions where record_type='candidate_execution_alias'").fetchone()[0] == 0
    renewed = store.renew_candidate_execution_claim(claimed["id"], "native-worker", 300)
    assert renewed["id"] == consumer["id"]
    db.execute("update run_execution_progress set status='done',lease_owner='',lease_expires_at='' where consumer_run_id=?",
               (consumer["id"],))
    db.commit()
    current_execution = store.get_candidate_execution(consumer["id"])
    assert current_execution["review_id"] == review["id"]
    assert current_execution["audit_run_id"] == audit["id"]
    assert current_execution["selection_id"] == current_selection["id"]
    later = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "parent_id": audit["id"], "execution_generation": "gen-1",
        "role": "consumer", "proposal_revision": 1, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    later_body = {**body, "stage_index": 1, "predecessor_review_id": review["id"]}
    with db:
        later_adoption = store._adopt_review_candidate_in_connection(db, later, later_body)
    assert later_adoption["predecessor_review_id"] == review["id"]
    later_digest = later_adoption["candidate_digest"]
    with db:
        later_audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": later["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 1, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        store._adopt_candidate_review_in_connection(db, later["id"], later_audit["id"],
            {"outcome": "return", "candidate_digest": later_digest})

    historical_body = {**body, "stage_index": 1, "predecessor_review_id": review["id"]}
    historical = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "parent_id": later_audit["id"], "execution_generation": "gen-1",
        "role": "consumer", "proposal_revision": 2, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    historical_json = json.dumps(historical_body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    historical_digest = hashlib.sha256(historical_json.encode()).hexdigest()
    db.execute("""insert into run_adopted_results
        (consumer_run_id,stage_index,predecessor_review_id,candidate_digest,candidate_json,
         invalidated_at,invalidation_reason,created_at)
        values(?,1,?,?,?,'','',current_timestamp)""",
        (historical["id"], review["id"], historical_digest, historical_json))
    db.commit()
    historical_adoption = store.get_review_candidate(historical["id"])
    assert historical_adoption["predecessor_review_id"] == review["id"]
    assert json.loads(historical_adoption["candidate_json"])["predecessor_review_id"] == review["id"]
    assert historical_adoption["candidate_json"] == historical_json
    assert historical_adoption["candidate_digest"] == historical_digest

    with db:
        historical_audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": historical["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 2, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        store._adopt_candidate_review_in_connection(db, historical["id"], historical_audit["id"],
            {"outcome": "return", "candidate_digest": historical_digest})

    # New same-stage revisions name the current relational predecessor, even
    # when an immutable migrated candidate body still contains the old ID.
    revised = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "parent_id": historical_audit["id"], "execution_generation": "gen-1",
        "role": "consumer", "proposal_revision": 3, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    revised_body = {**body, "stage_index": 1, "predecessor_review_id": review["id"]}
    with db:
        revised_adoption = store._adopt_review_candidate_in_connection(db, revised, revised_body)
    assert revised_adoption["stage_index"] == 1
    assert revised_adoption["predecessor_review_id"] == review["id"]
    assert json.loads(revised_adoption["candidate_json"])["predecessor_review_id"] == review["id"]

    bad_revision = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "parent_id": historical_audit["id"], "execution_generation": "gen-1",
        "role": "consumer", "proposal_revision": 4, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    db.commit()
    try:
        with db:
            store._adopt_review_candidate_in_connection(
                db, bad_revision, {**revised_body, "predecessor_review_id": audit["id"]})
    except ValueError as exc:
        assert str(exc) == "candidate predecessor mismatch"
    else:
        raise AssertionError("new revision accepted a migrated predecessor ID")

    from app.agent_orchestrator import AgentOrchestrator, _NextExecution
    from app.agent_contracts import AuditOutcome
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator.store = store
    orchestrator._consumer_result = lambda _run: SimpleNamespace(continue_after_execution=False, purpose_output=None)
    orchestrator._audit_result = lambda _run: SimpleNamespace(outcome=AuditOutcome.RETURN)
    store.get_agent_run = lambda run_id, load_events=False: SimpleNamespace(id=run_id)
    business_revision = orchestrator._business_revision(_NextExecution(
        SimpleNamespace(id=historical["id"], proposal_revision=2),
        SimpleNamespace(id=historical_audit["id"]), None, None, historical["id"], historical_audit["id"], 0))
    assert business_revision.stage_index == 1
    assert business_revision.predecessor_review_id == review["id"]
    stage_context = orchestrator._stage_for_parent(
        SimpleNamespace(id=historical_audit["id"], parent_agent_run_id=historical["id"]))
    assert stage_context == {"stage_index": 1, "predecessor_review_id": review["id"]}
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *_args, **_kwargs: None)
    assert store.get_review_candidate(historical["id"])["candidate_json"] == historical_json

    supplement = store.record_candidate_supplement(consumer["id"], "new fact")
    assert supplement["candidate_id"] == consumer["id"]
    assert store.get_review_candidate(consumer["id"])["invalidation_reason"] == "supplement"
    assert db.execute("select count(*) from actions where record_type in ('review_candidates','candidate_reviews','candidate_selections','candidate_supplements')").fetchone()[0] == 0
    assert [row[0] for row in db.execute("select consumer_run_id from run_adopted_results order by consumer_run_id")] == [consumer["id"], later["id"], historical["id"], revised["id"]]


def test_native_store_rejects_stale_generation_and_rolls_back_child_rowids(monkeypatch):
    store, db, task, consumer = _native_store(monkeypatch)
    body = {"outcome": "needs_human", "stage_index": 0, "predecessor_review_id": None,
        "decision_options": [{"key": "stop", "terminal_outcome": "skipped", "reason": "stop"}]}
    with db:
        candidate = store._adopt_review_candidate_in_connection(db, consumer, body)
        audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        review_body = {"outcome": "approve", "candidate_digest": candidate["candidate_digest"]}
        review = store._adopt_candidate_review_in_connection(db, consumer["id"], audit["id"], review_body)
    db.commit()
    task_entity_storage.update_task_cas(db, task_id=task["id"],
        expected={"execution_generation": "gen-1"}, changes={"execution_generation": "gen-2"})
    db.commit()
    for historical in (store.get_review_candidate(consumer["id"]),
                       store.adopted_candidate_for_consumer_run(consumer["id"])):
        assert historical["candidate_json"] == candidate["candidate_json"]
        assert historical["candidate_digest"] == candidate["candidate_digest"]
        assert historical["execution_generation"] == "gen-1"
    assert store.get_review_candidate(-1) is None
    assert review["id"] != review["audit_run_id"]
    assert store.current_reviewed_candidate(task["id"], "gen-1") is None
    try:
        store.select_candidate_option(consumer["id"], review["id"], "stop")
    except ValueError as exc:
        assert str(exc) == "candidate generation mismatch"
    else:
        raise AssertionError("stale generation candidate remained actionable")

    # The Store's in-transaction adoption write and child rows share the
    # caller's transaction; rollback leaves neither rows nor consumed ROWIDs.
    db.execute("begin")
    revision = run_entity_storage.create_run(db, values={
        "task_id": task["id"], "execution_generation": "gen-2", "role": "consumer",
        "proposal_revision": 1, "turn_attempt": 0, "status": "completed",
        "created_at": "now", "updated_at": "now",
    })
    store._adopt_review_candidate_in_connection(db, revision, body)
    db.execute("insert into run_adopted_supplements(consumer_run_id,instruction,created_at) values(?,?,?)",
        (consumer["id"], "rolled back", "now"))
    db.rollback()
    assert db.execute("select count(*) from run_adopted_results where consumer_run_id=?", (revision["id"],)).fetchone()[0] == 0
    assert db.execute("select count(*) from run_adopted_supplements").fetchone()[0] == 0
    db.execute("insert into run_adopted_supplements(consumer_run_id,instruction,created_at) values(?,?,?)",
        (consumer["id"], "kept", "now"))
    assert db.execute("select id from run_adopted_supplements").fetchone()[0] == 1
    assert db.execute("select count(*) from sqlite_sequence where name like 'run_adopted_%'").fetchone()[0] == 0


def test_native_run_result_reader_uses_native_fields_without_attempt(monkeypatch, tmp_path):
    from app import native_trajectory

    _store, db, _task, run = _native_store(monkeypatch)
    body = {"outcome": "approve", "candidate_digest": "native-digest"}
    transcript = tmp_path / "native.jsonl"
    transcript.write_text(json.dumps({"type": "response_item", "payload": {"type": "message",
        "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(body)}]}}) + "\n")
    run = run_entity_storage.update_run_cas(db, run_id=run["id"], expected={"status": "completed"},
        changes={"session_id": "native-session", "transcript_start": 0, "transcript_end": 1})
    db.commit()
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *_args, **_kwargs: transcript)
    assert json.loads(native_trajectory.read_native_result_json(db, run)) == body
    monkeypatch.setattr(native_trajectory, "find_codex_session_path", lambda *_args, **_kwargs: None)
    assert native_trajectory.read_native_result_json(db, run) == ""


def test_current_review_projection_follows_review_id_when_audit_adoption_is_reversed(monkeypatch):
    store, db, task, consumer = _native_store(monkeypatch)
    body = {"outcome": "proposal", "summary": "review order",
            "stage_index": 0, "predecessor_review_id": None,
            "decision_options": [{"key": "stop", "terminal_outcome": "skipped", "reason": "stop"}]}
    with db:
        store._adopt_review_candidate_in_connection(db, consumer, body)
        first_audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 0, "turn_attempt": 0, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        last_audit = run_entity_storage.create_run(db, values={
            "task_id": task["id"], "parent_id": consumer["id"], "execution_generation": "gen-1",
            "role": "audit", "proposal_revision": 0, "turn_attempt": 1, "status": "completed",
            "created_at": "now", "updated_at": "now",
        })
        digest = store.get_review_candidate(consumer["id"])["candidate_digest"]
        last_review = store._adopt_candidate_review_in_connection(db, consumer["id"], last_audit["id"],
            {"outcome": "approve", "candidate_digest": digest})
        first_review = store._adopt_candidate_review_in_connection(db, consumer["id"], first_audit["id"],
            {"outcome": "approve", "candidate_digest": digest})

    assert first_audit["id"] < last_audit["id"]
    assert last_review["id"] < first_review["id"]
    current = store.current_reviewed_candidate(task["id"], "gen-1")
    assert current["review_id"] == first_review["id"]
    assert current["audit_run_id"] == first_audit["id"]
