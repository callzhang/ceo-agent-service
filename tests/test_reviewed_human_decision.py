import json
from pathlib import Path
from urllib.parse import urlencode

from app.agent_contracts import ConsumerAgentResult
from app.audit_web import handle_needs_human_decision_post, _is_valid_rerun_trigger_json
from app.audit_web import _human_decision_attention_rows
from app.quality_gate import scan_hourly_quality
from app.reviewed_candidates import candidate_digest
from app.store import AgentRole, AutoReplyStore
from app.web_api.attempts import build_attempt_detail


def _question():
    fact = {"assertion": "Applicant asked for a reply", "references": ["message:1"]}
    plan = {"objective": "Reply to applicant", "actions": [{
        "description": "Send exact approved reply", "action_identity": "reply",
        "capability": "dingtalk-chat", "operation": "send_direct_message",
        "target": {"open_dingtalk_id": "applicant"},
        "payload": {"content": "The exact reply"},
    }], "sourced_facts": [fact], "authored_judgment": "One choice remains"}
    return ConsumerAgentResult.model_validate({
        "outcome": "needs_human", "summary": "Choose a current response", "proposal": None,
        "decision_options": [
            {"key": "send", "label": "Send reply", "instruction": "Send the exact reply",
             "consequence": "Applicant receives it", "plan": plan},
            {"key": "stop", "label": "Stop", "instruction": "Do not reply",
             "consequence": "No message", "terminal_outcome": "skipped", "reason": "Already resolved"},
        ],
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "medium", "confidence": 0.8, "rule_coverage": 0.5,
        "information_completeness": 1.0,
        "needs_human_reason": "Only Derek can choose this instance's reply",
        "decision_basis": {"verified_facts": [fact], "rule_evidence": [fact],
                           "quality_explanation": "No existing rule selects one option",
                           "no_external_action_evidence": [fact], "conclusion": "Ask Derek"},
        "requested_input": None, "stage_index": 0, "predecessor_review_id": None,
        "continue_after_execution": False, "durable_memories": [],
    })


def _approved_question(path: Path):
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        conversation_id="cid-choice", conversation_title="Applicant", single_chat=True,
        trigger_message_id="msg-choice", trigger_create_time="2026-10-04 10:00:00",
        trigger_sender="Applicant", trigger_text="Please choose",
    )
    task = store.claim_reply_tasks(1)[0]
    question = _question()
    consumer = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    store.complete_agent_run(consumer.id, question.model_dump(mode="json"), owner="consumer")
    candidate = store.persist_review_candidate(task, consumer, question)
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id="audit-choice", owner="audit",
    ).run
    audit_result = {"outcome": "approve", "candidate_digest": candidate_digest(question)}
    store.complete_agent_run(audit.id, audit_result, owner="audit")
    review = store.record_candidate_review(candidate["id"], audit.id, audit_result)
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id, conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, action="agent_run", sensitivity_kind="general",
        audit_summary="Reviewed current choice", send_status="needs_human",
    )
    with store._connect() as db:
        db.execute("update reply_attempts set agent_run_id=? where id=?", (audit.id, attempt_id))
        db.execute("update reply_tasks set status='needs_human' where id=?", (task.id,))
    return store, task, candidate, review, attempt_id


def _submit(store, attempt_id, **fields):
    return handle_needs_human_decision_post(store, attempt_id, urlencode(fields).encode())


def test_only_current_approved_option_is_actionable_and_wakes_same_generation(tmp_path):
    store, task, candidate, review, attempt_id = _approved_question(tmp_path / "choice.sqlite3")
    status, detail = build_attempt_detail(store, attempt_id)
    assert status == 200
    assert detail["status"]["requires_decision"] is True
    assert [option["key"] for option in detail["decision_options"]] == ["send", "stop"]
    assert detail["decision_options"][0]["plan"]["actions"][0]["payload"]["content"] == "The exact reply"
    assert store.reconcile_invalid_needs_human_projections() == 0
    assert store.reconcile_valid_needs_human_projections() == 0
    assert store.get_reply_attempt(attempt_id).send_status == "needs_human"
    assert [row["id"] for row in _human_decision_attention_rows(store)] == [str(attempt_id)]
    report = scan_hourly_quality(store.path)
    assert any(item.source == "reply_attempts" and item.code == "needs_human" for item in report.attention)
    status, _, _ = _submit(store, attempt_id, kind="select", candidate_id=candidate["id"],
                           review_id=review["id"], candidate_digest=candidate["candidate_digest"], option_key="send")
    assert status == 303
    assert store.get_reply_task(task.id).status == "pending"
    assert store.get_reply_task(task.id).execution_generation == task.execution_generation
    assert store.current_reviewed_candidate(task.id, task.execution_generation)["option_key"] == "send"
    assert _human_decision_attention_rows(store) == []
    assert _submit(store, attempt_id, kind="select", candidate_id=candidate["id"],
                   review_id=review["id"], option_key="send")[0] == 303
    assert _submit(store, attempt_id, kind="select", candidate_id=candidate["id"],
                   review_id=review["id"], option_key="stop")[0] == 409
    _, selected_detail = build_attempt_detail(store, attempt_id)
    assert selected_detail["decision_options"] == []
    assert selected_detail["human_decision"]["selection"]["option_key"] == "send"


def test_forged_payload_and_old_instruction_only_request_rejected(tmp_path):
    store, _task, candidate, review, attempt_id = _approved_question(tmp_path / "choice.sqlite3")
    assert _submit(store, attempt_id, instruction="send something else")[0] == 400
    assert _submit(store, attempt_id, kind="select", candidate_id=candidate["id"],
                   review_id=review["id"], option_key="send", payload="forged")[0] == 400
    assert _submit(store, attempt_id, kind="select", candidate_id=candidate["id"],
                   review_id=review["id"], option_key="send", feedback_scope="reusable_policy")[0] == 400
    assert _submit(store, attempt_id, kind="select", candidate_id=candidate["id"],
                   review_id=review["id"], option_key="send", candidate_digest="0" * 64)[0] == 409
    assert store.get_candidate_execution(candidate["id"]) is None


def test_historical_question_stays_readable_but_cannot_be_selected(tmp_path):
    store, task, candidate, review, attempt_id = _approved_question(tmp_path / "choice.sqlite3")
    store.invalidate_review_candidate(candidate["id"], "new source fact")
    _, detail = build_attempt_detail(store, attempt_id)
    assert detail["decision_options"] == []
    assert detail["human_decision"]["reason"] == "Only Derek can choose this instance's reply"
    assert detail["status"]["requires_decision"] is False
    assert _submit(store, attempt_id, kind="select", candidate_id=candidate["id"],
                   review_id=review["id"], option_key="send")[0] == 409
    assert store.get_reply_task(task.id).status == "needs_human"


def test_unbound_legacy_task_class_choices_survive_restart_without_becoming_executable(tmp_path):
    path = tmp_path / "legacy-choice.sqlite3"
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        conversation_id="cid-legacy", conversation_title="Legacy request", single_chat=True,
        trigger_message_id="msg-legacy", trigger_create_time="2026-10-04 13:42:55",
        trigger_sender="Applicant", trigger_text="Allocate this request",
    )
    task = store.claim_reply_tasks(1)[0]
    options = [
        {"key": "initial", "label": "Prepare initial allocation", "instruction": "For future requests use an initial proportional allocation", "consequence": "A standing rule changes", "applies_to": "task_class"},
        {"key": "evidence", "label": "Get evidence first", "instruction": "For future requests obtain evidence before confirming", "consequence": "A standing rule changes", "applies_to": "task_class"},
    ]
    run = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    body = {"outcome": "needs_human", "summary": "Choose a standing rule", "decision_options": options}
    store.complete_agent_run(run.id, body, owner="consumer")
    attempt_id = store.record_reply_attempt(
        conversation_id=task.conversation_id, conversation_title=task.conversation_title,
        trigger_message_id=task.trigger_message_id, trigger_sender=task.trigger_sender,
        trigger_text=task.trigger_text, action="agent_run", sensitivity_kind="general",
        audit_summary="Historical rule question", send_status="needs_human",
        human_decision_options_json=json.dumps(options),
    )
    with store._connect() as db:
        db.execute("update reply_attempts set agent_run_id=? where id=?", (run.id, attempt_id))
        db.execute("update reply_tasks set status='needs_human' where id=?", (task.id,))
    reopened = AutoReplyStore(path)
    assert reopened.reconcile_invalid_needs_human_projections() == 0
    assert reopened.reconcile_valid_needs_human_projections() == 0
    _, detail = build_attempt_detail(reopened, attempt_id)
    assert detail["decision_options"] == []
    assert detail["status"]["requires_decision"] is False
    assert _submit(reopened, attempt_id, instruction=options[0]["instruction"])[0] == 400
    assert _submit(reopened, attempt_id, kind="select", candidate_id=1, review_id=1, option_key="initial")[0] == 409
    assert reopened.get_reply_attempt(attempt_id).send_status == "needs_human"
    assert json.loads(reopened.get_reply_attempt(attempt_id).human_decision_options_json) == options
    assert reopened.get_reply_task(task.id).status == "needs_human"
    assert json.loads(reopened.get_agent_run(run.id).final_result_json) == body
    with reopened._connect() as db:
        assert db.execute("select count(*) from candidate_selections").fetchone()[0] == 0
        assert db.execute("select count(*) from candidate_executions").fetchone()[0] == 0


def test_supplement_and_generation_rerun_commit_together(tmp_path, monkeypatch):
    store, task, candidate, review, attempt_id = _approved_question(tmp_path / "choice.sqlite3")
    original = store._enqueue_manual_rerun_reply_task_in_connection

    def fail_after_supplement(*args, **kwargs):
        raise ValueError("rerun unavailable")

    monkeypatch.setattr(store, "_enqueue_manual_rerun_reply_task_in_connection", fail_after_supplement)
    fields = dict(kind="supplement", candidate_id=candidate["id"],
                  review_id=review["id"], instruction="One new fact")
    assert _submit(store, attempt_id, **fields)[0] == 409
    assert store.get_review_candidate(candidate["id"])["invalidated_at"] == ""
    assert store.get_reply_task(task.id).execution_generation == task.execution_generation
    with store._connect() as db:
        assert db.execute("select count(*) from candidate_supplements").fetchone()[0] == 0

    monkeypatch.setattr(store, "_enqueue_manual_rerun_reply_task_in_connection", original)
    assert _submit(store, attempt_id, **fields)[0] == 303
    assert store.get_review_candidate(candidate["id"])["invalidated_at"]
    assert store.get_reply_task(task.id).execution_generation != task.execution_generation
    with store._connect() as db:
        assert db.execute("select instruction from candidate_supplements").fetchone()[0] == "One new fact"


def test_scheduled_reviewed_question_accepts_exact_selection_and_new_facts(tmp_path):
    store, task, candidate, review, attempt_id = _approved_question(tmp_path / "scheduled.sqlite3")
    saved_execution = {
        "schema": "scheduled_agent_execution.v1",
        "context": {
            "conversation_id": task.conversation_id, "conversation_title": task.conversation_title,
            "single_chat": True, "trigger_message_id": task.trigger_message_id,
            "trigger_sender": task.trigger_sender, "trigger_text": task.trigger_text,
            "trigger_create_time": task.trigger_create_time, "trigger_raw_payload": {},
        },
        "route": {"name": "test", "runtime_kind": "codex_cli", "credential_mode": "local_oauth", "model": "test"},
        "workspace": str(tmp_path), "reasoning_effort": "medium", "skill_protocol": "test",
    }
    with store._connect() as db:
        db.execute("update reply_tasks set channel='scheduled', trigger_message_json=? where id=?",
                   (json.dumps(saved_execution), task.id))
        db.execute("update reply_attempts set channel='scheduled' where id=?", (attempt_id,))
    scheduled_task = store.get_reply_task_for_message(task.conversation_id, task.trigger_message_id, channel="scheduled")
    assert scheduled_task is not None
    assert _is_valid_rerun_trigger_json(scheduled_task.trigger_message_json, channel="scheduled")
    response = _submit(store, attempt_id, kind="supplement", candidate_id=candidate["id"],
                       review_id=review["id"], instruction="New report date")
    assert response[0] == 303, response
    assert store.get_review_candidate(candidate["id"])["invalidated_at"]

    second, _task, candidate, review, attempt_id = _approved_question(tmp_path / "scheduled-choice.sqlite3")
    with second._connect() as db:
        db.execute("update reply_tasks set channel='scheduled' where id=?", (_task.id,))
        db.execute("update reply_attempts set channel='scheduled' where id=?", (attempt_id,))
    assert _submit(second, attempt_id, kind="select", candidate_id=candidate["id"],
                   review_id=review["id"], option_key="stop")[0] == 303
