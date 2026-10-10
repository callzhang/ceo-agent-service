import json
import sqlite3
from pathlib import Path

import pytest

from app.store import AgentRole, AutoReplyStore, STORE_SCHEMA_VERSION_KEY
from app import store as store_module


@pytest.mark.parametrize("channel", ["wechat", "dingtalk"])
def test_legacy_wechat_decision_does_not_become_audited_candidate(tmp_path, channel):
    from app.dingtalk_models import CodexAction, CodexDecision

    store = AutoReplyStore(tmp_path / "channel-contract.sqlite3")
    store.enqueue_reply_task(
        channel=channel, conversation_id="contract", conversation_title="Test",
        single_chat=True, trigger_message_id="m1", trigger_sender="Test",
        trigger_create_time="2026-10-10T00:00:00Z", trigger_text="test",
    )
    task = store.claim_reply_tasks(1, channel=channel)[0]
    run = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="contract",
    ).run
    decision = CodexDecision(action=CodexAction.NO_REPLY, audit_summary="not needed")
    if channel == "dingtalk":
        with pytest.raises(ValueError, match="not a reviewable candidate"):
            store.complete_agent_run(run.id, decision.model_dump(mode="json"), owner="contract")
        assert store.get_agent_run(run.id).status == "running"
    else:
        completed = store.complete_agent_run(run.id, decision.model_dump(mode="json"), owner="contract")
        assert completed.status == "completed"
        assert completed.final_result_json == ""
    assert store.adopted_candidate_for_consumer_run(run.id) is None


@pytest.mark.parametrize("body", [
    {"action": "not_a_decision"},
    {"action": "no_reply", "outcome": "executed"},
])
def test_wechat_completion_rejects_invalid_contract_without_adoption(tmp_path, body):
    store = AutoReplyStore(tmp_path / "invalid-wechat.sqlite3")
    store.enqueue_reply_task(
        channel="wechat", conversation_id="contract", conversation_title="Test",
        single_chat=True, trigger_message_id="m1", trigger_sender="Test",
        trigger_create_time="2026-10-10T00:00:00Z", trigger_text="test",
    )
    task = store.claim_reply_tasks(1, channel="wechat")[0]
    run = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="contract",
    ).run
    with pytest.raises(ValueError):
        store.complete_agent_run(run.id, body, owner="contract")
    assert store.get_agent_run(run.id).status == "running"
    assert store.adopted_candidate_for_consumer_run(run.id) is None


def _native_wechat_run(tmp_path, monkeypatch, *, probe_writer=False, with_attempt=False, failed_read=False, with_draft=False):
    from app import native_trajectory
    from app.agent_envelope import AgentEnvelope
    from app.codex_decision import CodexDecisionRunner, parse_codex_json
    from app.codex_history import extract_codex_audit_events_from_session

    store = AutoReplyStore(tmp_path / "native-wechat.sqlite3")
    store.enqueue_reply_task(
        channel="wechat", conversation_id="contract", conversation_title="Test",
        single_chat=True, trigger_message_id="m1", trigger_sender="Test",
        trigger_create_time="2026-10-10T00:00:00Z", trigger_text="test",
    )
    task = store.claim_reply_tasks(1, channel="wechat")[0]
    run = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="contract",
    ).run
    envelope = AgentEnvelope.model_validate({
        "kind": "no_action", "user_response": {
            "mode": "no_reply", "text": "", "sensitivity_kind": "general",
        }, "system_actions": [], "domain_payload": {},
        "audit": {"summary": "not needed", "documents": [], "confidence": 1},
    })
    native = tmp_path / "wechat.jsonl"
    records = []
    if failed_read:
        records.append({"type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "CommandExecution", "id": "failed-read", "command": "dws minutes list",
            "aggregated_output": "Process exited with code 6\nrequest_failed",
        }}})
    native_text = envelope.model_dump_json()
    if with_draft:
        draft = envelope.model_copy(update={"audit": envelope.audit.model_copy(update={"summary": "changed"})})
        native_text = draft.model_dump_json() + "\n" + native_text
    records.append({"type": "response_item", "payload": {
        "type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": native_text},
        ],
    }})
    native.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    writer_probes = []

    def native_path(*args, **kwargs):
        if probe_writer:
            with sqlite3.connect(store.path, timeout=0) as writer:
                writer.execute("BEGIN IMMEDIATE")
                writer.rollback()
            writer_probes.append(True)
        return native

    monkeypatch.setattr(native_trajectory, "find_codex_session_path", native_path)
    monkeypatch.setattr("app.codex_history.find_codex_session_path", native_path)
    store.set_agent_run_session(run.id, "fixture-wechat", owner="contract")
    if with_attempt:
        attempt = store.claim_agent_runtime_attempt(run.id, "fixture", "codex_cli", "local_oauth", "fixture")
        store.mark_agent_runtime_attempt_running(attempt.id)
        store.complete_agent_runtime_attempt(
            attempt.id, "fixture-wechat", "codex_session:fixture-wechat", 0, len(records), owner="contract",
        )
    runner = CodexDecisionRunner.__new__(CodexDecisionRunner)
    runner.last_audit_tool_events = extract_codex_audit_events_from_session(
        "fixture-wechat", start_line=0, end_line=len(records),
    )
    decision = runner._finalize_decision(parse_codex_json(native_text), [], remember_events=False)
    return store, run, decision, writer_probes


@pytest.mark.parametrize("with_attempt", [False, True])
@pytest.mark.parametrize("with_draft", [False, True])
def test_wechat_native_envelope_completion_is_exactly_idempotent(tmp_path, monkeypatch, with_attempt, with_draft):
    store, run, decision, _ = _native_wechat_run(tmp_path, monkeypatch, with_attempt=with_attempt, with_draft=with_draft)
    for _ in range(2):
        assert store.complete_agent_run(
            run.id, decision.model_dump(mode="json"), owner="contract", transcript_end_line=1,
        ).status == "completed"
    assert store.adopted_candidate_for_consumer_run(run.id) is None
    with pytest.raises(ValueError, match="conflicting terminal rewrite"):
        store.complete_agent_run(
            run.id, {**decision.model_dump(mode="json"), "audit_summary": "changed"},
            owner="contract", transcript_end_line=1,
        )


def test_wechat_native_completion_does_not_hold_sqlite_writer_lock(tmp_path, monkeypatch):
    store, run, decision, probes = _native_wechat_run(tmp_path, monkeypatch, probe_writer=True)
    for _ in range(2):
        store.complete_agent_run(run.id, decision.model_dump(mode="json"),
                                 owner="contract", transcript_end_line=1)
    assert probes


def test_wechat_native_replay_preserves_runner_dependency_finalization(tmp_path, monkeypatch):
    store, run, decision, _ = _native_wechat_run(tmp_path, monkeypatch, with_attempt=True, failed_read=True)
    assert decision.action.value == "stop_with_error"
    assert decision.external_dependency_failed
    for _ in range(2):
        store.complete_agent_run(run.id, decision.model_dump(mode="json"),
                                 owner="contract", transcript_end_line=2)


def test_wechat_native_replay_rejects_missing_source(tmp_path, monkeypatch):
    store, run, decision, _ = _native_wechat_run(tmp_path, monkeypatch, with_attempt=True)
    store.complete_agent_run(run.id, decision.model_dump(mode="json"), owner="contract", transcript_end_line=1)
    monkeypatch.setattr("app.native_trajectory.find_codex_session_path", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.codex_history.find_codex_session_path", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="conflicting terminal rewrite"):
        store.complete_agent_run(run.id, decision.model_dump(mode="json"), owner="contract", transcript_end_line=1)


def test_wechat_native_replay_rechecks_exact_reference_after_io(tmp_path, monkeypatch):
    from app import native_trajectory

    store, run, decision, _ = _native_wechat_run(tmp_path, monkeypatch, with_attempt=True)
    store.complete_agent_run(run.id, decision.model_dump(mode="json"), owner="contract", transcript_end_line=1)
    original = native_trajectory.read_native_result_stream

    def rotate_reference(*args):
        result = original(*args)
        with store._connect() as db:
            db.execute("update agent_runtime_attempts set session_id='rotated' where agent_run_id=? and status='completed'", (run.id,))
        return result

    monkeypatch.setattr(native_trajectory, "read_native_result_stream", rotate_reference)
    with pytest.raises(ValueError, match="native terminal reference changed"):
        store.complete_agent_run(run.id, decision.model_dump(mode="json"), owner="contract", transcript_end_line=1)


@pytest.mark.parametrize("same_result", [True, False])
def test_native_audit_completion_race_rechecks_outside_writer(tmp_path, monkeypatch, same_result):
    from app import native_trajectory

    store = AutoReplyStore(tmp_path / "raced-audit.sqlite3")
    other = AutoReplyStore(store.path)
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="contract", conversation_title="Test",
        single_chat=True, trigger_message_id="m1", trigger_sender="Test",
        trigger_create_time="2026-10-10T00:00:00Z", trigger_text="test",
    )
    task = store.claim_reply_tasks(1)[0]
    run = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id=f"direct-agent:{task.id}:{task.execution_generation}", owner="contract",
    ).run
    body = {"outcome": "completed", "summary": "done"}
    native = tmp_path / "audit.jsonl"
    native.write_text(json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": json.dumps(body)},
        ],
    }}) + "\n")

    def native_path(*args, **kwargs):
        with sqlite3.connect(store.path, timeout=0) as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.rollback()
        return native

    monkeypatch.setattr(native_trajectory, "find_codex_session_path", native_path)
    store.set_agent_run_session(run.id, "fixture-audit", owner="contract")
    original_snapshot = store._agent_run_terminal_native_snapshot
    completed_between_reads = False

    def raced_snapshot(run_id):
        nonlocal completed_between_reads
        snapshot = original_snapshot(run_id)
        if not completed_between_reads:
            completed_between_reads = True
            assert snapshot is None
            other.complete_agent_run(run.id, body, owner="contract", transcript_end_line=1)
        return snapshot

    monkeypatch.setattr(store, "_agent_run_terminal_native_snapshot", raced_snapshot)
    if same_result:
        assert store.complete_agent_run(run.id, body, owner="contract", transcript_end_line=1).status == "completed"
    else:
        with pytest.raises(ValueError, match="conflicting terminal rewrite"):
            store.complete_agent_run(run.id, {**body, "summary": "different"},
                                     owner="contract", transcript_end_line=1)


def _reviewed(store: AutoReplyStore, *, options=True):
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="candidate-test", conversation_title="Test",
        single_chat=True, trigger_message_id="candidate-1", trigger_sender="Derek",
        trigger_create_time="2026-10-04 00:00:00", trigger_text="test",
    )
    task = store.claim_reply_tasks(1)[0]
    consumer = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    body = {"outcome": "needs_human" if options else "proposal", "summary": "review me",
            "stage_index": 0, "predecessor_review_id": None,
            "decision_options": ([
                {"key": "execute", "plan": {"actions": [{"action_identity": "a", "operation": "send", "target": {"user_id": "x"}}]}},
                {"key": "stop", "terminal_outcome": "skipped", "reason": "not needed"},
            ] if options else []),
            "proposal": (None if options else {"actions": [{"action_identity": "a", "operation": "send", "target": {"user_id": "x"}}]})}
    store.complete_agent_run(consumer.id, body, owner="consumer")
    candidate = store.persist_review_candidate(task, consumer, body)
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id="audit-test", owner="audit",
    ).run
    store.complete_agent_run(audit.id, {"outcome": "approve", "candidate_digest": candidate["candidate_digest"]}, owner="audit")
    review = store.record_candidate_review(candidate["id"], audit.id, {"outcome": "approve", "candidate_digest": candidate["candidate_digest"]})
    return task, candidate, review, body


def test_review_binding_and_restart(tmp_path: Path):
    path = tmp_path / "candidate.sqlite3"
    store = AutoReplyStore(path)
    task, candidate, review, body = _reviewed(store)
    current = AutoReplyStore(path).current_reviewed_candidate(task.id, task.execution_generation)
    assert current["id"] == candidate["id"]
    assert current["review_id"] == review["id"]
    assert json.loads(candidate["candidate_json"]) == body
    with pytest.raises(ValueError):
        store.persist_review_candidate(task, store.get_agent_run(candidate["consumer_run_id"]), {**body, "summary": "tampered"})
    with pytest.raises(ValueError):
        store.record_candidate_review(candidate["id"], review["audit_run_id"], {"outcome": "approve", "candidate_digest": "wrong"})


def test_choice_conflict_and_execution_lease(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    _, candidate, review, _ = _reviewed(store)
    assert store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60) is None
    selected = store.select_candidate_option(candidate["id"], review["id"], "execute")
    assert store.select_candidate_option(candidate["id"], review["id"], "execute")["id"] == selected["id"]
    with pytest.raises(ValueError, match="selection_conflict"):
        store.select_candidate_option(candidate["id"], review["id"], "stop")
    claim = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    assert claim["status"] == "running"
    assert store.claim_candidate_execution(candidate["id"], review["id"], "other", 60) is None
    attempt = store.begin_candidate_action(claim["id"], "worker", 0, "action-key")
    assert attempt["status"] == "dispatched"
    store.record_candidate_action_outcome(claim["id"], "worker", 0, "uncertain", {"reason": "timeout"})
    assert store.list_candidate_action_attempts(claim["id"])[0]["status"] == "uncertain"
    store.finish_candidate_execution(claim["id"], "worker", "uncertain", {"reason": "timeout"})
    resumed = store.claim_candidate_execution(candidate["id"], review["id"], "reconciler", 60)
    assert resumed["id"] == claim["id"]
    assert store.begin_candidate_action(resumed["id"], "reconciler", 0, "action-key")["status"] == "uncertain"


def test_supplement_invalidates_without_erasing_choice(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    task, candidate, review, _ = _reviewed(store)
    store.select_candidate_option(candidate["id"], review["id"], "execute")
    supplement = store.record_candidate_supplement(candidate["id"], "new fact")
    assert supplement["instruction"] == "new fact"
    assert store.current_reviewed_candidate(task.id, task.execution_generation) is None
    assert store.get_review_candidate(candidate["id"])["invalidated_at"]
    assert store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60) is None


def test_additive_schema_reopen_preserves_receipt(tmp_path: Path):
    path = tmp_path / "candidate.sqlite3"
    store = AutoReplyStore(path)
    _, candidate, review, _ = _reviewed(store, options=False)
    claim = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    store.begin_candidate_action(claim["id"], "worker", 0, "action-key")
    receipt = store.record_candidate_external_action(
        claim["id"], "worker", 0, "action-key", "send", {"user_id": "x"}, {"receipt": "r1"},
    )
    assert receipt["external_action_key"] == "action-key"
    assert store.get_candidate_external_action("action-key")["provider_result_json"] == '{"receipt":"r1"}'
    for _ in range(2):
        reopened = AutoReplyStore(path)
        assert reopened.get_candidate_external_action("action-key")["external_action_key"] == receipt["external_action_key"]
        assert reopened.get_review_candidate(candidate["id"])["id"] == candidate["id"]
    with sqlite3.connect(path) as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"


def test_provider_receipt_survives_candidate_invalidation_after_dispatch(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    task, candidate, review, _ = _reviewed(store, options=False)
    claim = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    store.begin_candidate_action(claim["id"], "worker", 0, "effect-key")
    with store._connect() as db:
        db.execute("""update review_candidates set invalidated_at=current_timestamp,
            invalidation_reason='new source fact' where id=?""", (candidate["id"],))
        db.execute("""update reply_tasks set execution_generation='next-generation',
            status='pending' where id=?""", (task.id,))
    with pytest.raises(ValueError, match="generation mismatch"):
        store.begin_candidate_action(claim["id"], "worker", 0, "effect-key")
    receipt = store.record_candidate_external_action(
        claim["id"], "worker", 0, "effect-key", "send", {"user_id": "x"},
        {"receipt": "late-success"},
    )
    assert receipt["external_action_key"] == "effect-key"
    store.record_candidate_action_outcome(claim["id"], "worker", 0, "verified", {"receipt": "late-success"})
    assert store.finish_candidate_execution(claim["id"], "worker", "done", {})["status"] == "done"
    assert store.get_reply_task(task.id).status == "pending"
    assert store.get_candidate_external_action("effect-key")["provider_result_json"] == '{"receipt":"late-success"}'


def test_failed_business_state_change_finishes_and_invalidates_atomically(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    task, candidate, review, _ = _reviewed(store, options=False)
    claim = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    store.begin_candidate_action(claim["id"], "worker", 0, "state-key")
    store.record_candidate_action_outcome(
        claim["id"], "worker", 0, "failed", {"evidence": "provider state changed"},
    )
    result = {"error": {"code": "business_state_changed"}, "evidence": "provider state changed"}
    finished = store.finish_candidate_execution(
        claim["id"], "worker", "failed", result,
        invalidate_reason="business_state_changed",
    )
    assert finished["status"] == "failed"
    assert store.get_review_candidate(candidate["id"])["invalidation_reason"] == "business_state_changed"
    assert store.current_reviewed_candidate(task.id, task.execution_generation) is None
    with pytest.raises(ValueError):
        store.begin_candidate_action(claim["id"], "worker", 0, "state-key")


@pytest.mark.parametrize("error", [
    {"code": "agent_reported_failure", "source_code": "provider_risk_rejected", "retryable": False},
    {"code": "provider_risk_rejected", "source": "agent", "source_code": "provider_risk_rejected", "retryable": False},
])
@pytest.mark.parametrize("options", [False, True])
def test_historical_hard_refusal_blocks_execution_without_erasing_review(tmp_path: Path, error, options):
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    task, candidate, review, _ = _reviewed(store, options=options)
    if options:
        store.select_candidate_option(candidate["id"], review["id"], "execute")
    with store._connect() as db:
        db.execute("""insert into agent_runs
            (reply_task_id,execution_generation,role,proposal_revision,turn_attempt,status,structured_error_json)
            values (?,?,'audit',99,0,'failed',?)""", (
                task.id, "older-generation",
                json.dumps(error),
            ))
    # Restarting into the System executor cannot make an approved proposal or
    # previously selected human branch bypass the original provider refusal.
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    with pytest.raises(ValueError, match="historical_runtime_risk_refusal"):
        store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    assert store.get_review_candidate(candidate["id"])["invalidated_at"] == ""
    with store._connect() as db:
        preserved = db.execute("select status,structured_error_json from agent_runs where reply_task_id=? and proposal_revision=99", (task.id,)).fetchone()
        assert preserved["status"] == "failed"
        assert json.loads(preserved["structured_error_json"]) == error
        assert db.execute("select count(*) from candidate_executions").fetchone()[0] == 0


def test_generation_rotation_prevents_old_approved_plan(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    task, candidate, review, _ = _reviewed(store, options=False)
    with store._connect() as db:
        db.execute("update reply_tasks set execution_generation='new-generation' where id=?", (task.id,))
    assert store.current_reviewed_candidate(task.id, task.execution_generation) is None
    with pytest.raises(ValueError, match="generation mismatch"):
        store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)


def test_unknown_dispatch_requires_reconciliation_after_restart(tmp_path: Path):
    path = tmp_path / "candidate.sqlite3"
    store = AutoReplyStore(path)
    _, candidate, review, _ = _reviewed(store, options=False)
    claim = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    store.begin_candidate_action(claim["id"], "worker", 0, "action-key")
    reopened = AutoReplyStore(path)
    assert reopened.begin_candidate_action(claim["id"], "worker", 0, "action-key")["status"] == "uncertain"
    assert reopened.list_candidate_action_attempts(claim["id"])[0]["status"] == "uncertain"


def test_repeated_additive_migration_preserves_existing_history(tmp_path: Path):
    path = tmp_path / "candidate.sqlite3"
    store = AutoReplyStore(path)
    task, candidate, review, _ = _reviewed(store)
    store.select_candidate_option(candidate["id"], review["id"], "stop")
    migration_copy = tmp_path / "pre-migration-copy.sqlite3"
    with sqlite3.connect(path) as source, sqlite3.connect(migration_copy) as target:
        source.backup(target)
        assert target.execute("pragma integrity_check").fetchone()[0] == "ok"
        target.execute("update service_state set value='2026-09-25.1' where key=?", (STORE_SCHEMA_VERSION_KEY,))
    reopened = AutoReplyStore(migration_copy)
    assert reopened.current_reviewed_candidate(task.id, task.execution_generation)["option_key"] == "stop"
    store_module._INITIALIZED_STORE_PATHS.discard(migration_copy.resolve())
    second = AutoReplyStore(migration_copy)
    assert second.get_review_candidate(candidate["id"])["candidate_json"] == candidate["candidate_json"]
    with second._connect() as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"


def test_done_requires_success_ledger_and_stop_branch_skips(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    _, candidate, review, _ = _reviewed(store)
    store.select_candidate_option(candidate["id"], review["id"], "stop")
    claim = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    with pytest.raises(ValueError, match="not verified"):
        store.finish_candidate_execution(claim["id"], "worker", "done", {})
    assert store.finish_candidate_execution(claim["id"], "worker", "skipped", {"reason": "not needed"})["status"] == "skipped"


def test_later_stage_requires_verified_predecessor(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    task, candidate, review, body = _reviewed(store, options=False)
    next_body = {**body, "stage_index": 1, "predecessor_review_id": review["id"]}
    later = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=1, turn_attempt=0, parent_agent_run_id=review["audit_run_id"],
        operation_id="", owner="later",
    ).run
    with pytest.raises(ValueError, match="predecessor mismatch"):
        store.complete_agent_run(later.id, next_body, owner="later")
    assert store.get_agent_run(later.id).status == "running"
    assert store.adopted_candidate_for_consumer_run(later.id) is None
    first = store.claim_candidate_execution(candidate["id"], review["id"], "worker", 60)
    store.begin_candidate_action(first["id"], "worker", 0, "stage-action-key")
    store.record_candidate_external_action(
        first["id"], "worker", 0, "stage-action-key", "send", {"user_id": "x"},
        {"receipt": "verified"},
    )
    store.finish_candidate_execution(first["id"], "worker", "done", {})
    store.complete_agent_run(later.id, next_body, owner="later")
    saved = store.persist_review_candidate(task, later, next_body)
    assert saved["predecessor_review_id"] == review["id"]


def test_selection_wakes_same_generation_without_rewriting_question(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    task, candidate, review, _ = _reviewed(store)
    with store._connect() as db:
        db.execute("update reply_tasks set status='needs_human' where id=?", (task.id,))
    with pytest.raises(ValueError, match="choice is missing"):
        store.wake_selected_candidate_execution(candidate["id"], review["id"])
    store.select_candidate_option(candidate["id"], review["id"], "execute")
    awakened = store.wake_selected_candidate_execution(candidate["id"], review["id"])
    assert awakened["status"] == "pending"
    assert awakened["execution_generation"] == task.execution_generation
    assert store.wake_selected_candidate_execution(candidate["id"], review["id"])["status"] == "pending"
    assert store.get_review_candidate(candidate["id"])["candidate_json"] == candidate["candidate_json"]
    assert store.current_reviewed_candidate(task.id, task.execution_generation)["audit_run_id"] == review["audit_run_id"]


def test_preserved_human_evidence_survives_invalidation_and_generation_change(tmp_path: Path):
    store = AutoReplyStore(tmp_path / "candidate.sqlite3")
    task, candidate, review, _ = _reviewed(store)
    store.select_candidate_option(candidate["id"], review["id"], "execute")
    store.record_candidate_supplement(candidate["id"], "Applicant changed the due date")
    with store._connect() as db:
        db.execute("update reply_tasks set execution_generation='new-facts-generation' where id=?", (task.id,))

    events = store.list_human_decision_evidence(task.id)
    assert [event["kind"] for event in events] == ["selection", "supplement"]
    selected, supplement = events
    assert selected["candidate_id"] == candidate["id"]
    assert selected["review_id"] == review["id"]
    assert selected["audit_run_id"] == review["audit_run_id"]
    assert selected["candidate_digest"] == candidate["candidate_digest"]
    assert selected["execution_generation"] == task.execution_generation
    assert selected["option_key"] == "execute"
    assert selected["selected_branch"]["plan"]["actions"][0]["target"] == {"user_id": "x"}
    assert supplement["instruction"] == "Applicant changed the due date"
    assert selected["invalidation_reason"] == supplement["invalidation_reason"] == "supplement"
