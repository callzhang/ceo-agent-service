import test_reviewed_human_decision as fixtures

from app.agent_contracts import ConsumerAgentResult
from app.action_storage import CANDIDATE_ACTION_ATTEMPT_SQL, update_action
from app.web_api.attempts import build_attempt_detail


def _two_action_question(tmp_path, monkeypatch):
    body = fixtures._question().model_dump(mode="json")
    actions = body["decision_options"][0]["plan"]["actions"]
    actions.append({**actions[0], "action_identity": "followup", "description": "Notify applicant",
                    "payload": {"content": "Follow-up notice"}})
    question = ConsumerAgentResult.model_validate(body)
    monkeypatch.setattr(fixtures, "_question", lambda: question)
    return fixtures._approved_question(tmp_path / "projection.sqlite3")


def test_current_question_keeps_decision_status_after_earlier_sent_stage(tmp_path):
    store, task, _candidate, _review, attempt_id = fixtures._approved_question(tmp_path / "question.sqlite3")
    store.record_sent_reply(task.conversation_id, task.trigger_message_id, "Earlier material request")
    _, detail = build_attempt_detail(store, attempt_id)
    assert detail["status"]["requires_decision"] is True
    assert "等待你的决策" in detail["status"]["message"]
    assert detail["system_execution"] is None


def test_stale_historical_action_without_execution_is_not_projected_as_pending(tmp_path, monkeypatch):
    body = {
        "outcome": "needs_human", "summary": "Choose a current response", "proposal": None,
        "decision_options": [
            {"key": "send", "label": "Send reply", "instruction": "Send the exact reply",
             "consequence": "Applicant receives it", "applies_to": "task_class",
             "plan": {"objective": "Reply to applicant", "actions": [{
                 "description": "Send exact approved reply", "action_identity": "reply",
                 "capability": "dingtalk-chat", "operation": "send_direct_message",
                 "target": {"open_dingtalk_id": "applicant"},
                 "payload": {"content": "The exact reply"},
             }], "sourced_facts": [{"assertion": "Applicant asked for a reply", "references": ["message:1"]}],
                 "authored_judgment": "One choice remains"}},
            {"key": "stop", "label": "Stop", "instruction": "Do not reply",
             "consequence": "No message", "applies_to": "task_class",
             "terminal_outcome": "skipped", "reason": "Already resolved"},
        ],
        "error": {"code": "", "retryable": False, "authorization_required": False},
        "risk": "medium", "confidence": 0.8, "rule_coverage": 0.4,
        "information_completeness": 1.0,
        "needs_human_reason": "Only Derek can choose this instance's reply",
        "decision_basis": {"verified_facts": [{"assertion": "Applicant asked for a reply", "references": ["message:1"]}],
                           "rule_evidence": [{"assertion": "Applicant asked for a reply", "references": ["message:1"]}],
                           "quality_explanation": "No existing rule selects one option",
                           "no_external_action_evidence": [{"assertion": "Applicant asked for a reply", "references": ["message:1"]}],
                           "conclusion": "Ask Derek"},
        "requested_input": None, "stage_index": 0, "predecessor_review_id": None,
        "continue_after_execution": False, "durable_memories": [],
    }
    body = ConsumerAgentResult.model_validate(body).model_dump(mode="json")
    body["outcome"] = "proposal"
    body["proposal"] = body["decision_options"][0]["plan"]
    body["decision_options"] = []
    body["needs_human_reason"] = None
    body["decision_basis"] = None
    body["requested_input"] = None
    candidate_result = ConsumerAgentResult.model_validate(body)
    monkeypatch.setattr(fixtures, "_question", lambda: candidate_result)
    store, _task, candidate, _review, attempt_id = fixtures._approved_question(
        tmp_path / "stale-history.sqlite3"
    )

    store.invalidate_review_candidate(candidate["id"], "a later review superseded it")

    _, detail = build_attempt_detail(store, attempt_id)

    assert detail["system_execution"] is None
    assert store.get_candidate_execution(candidate["id"]) is None


def test_selected_plan_projects_pending_actions_without_inferred_receipts(tmp_path, monkeypatch):
    store, task, candidate, review, attempt_id = _two_action_question(tmp_path, monkeypatch)
    store.select_candidate_option(candidate["id"], review["id"], "send")
    store.wake_selected_candidate_execution(candidate["id"], review["id"])
    _, detail = build_attempt_detail(store, attempt_id)
    execution = detail["system_execution"]
    assert execution["status"] == "pending"
    assert execution["candidate_id"] == candidate["id"]
    assert execution["verified_actions"] == 0
    assert [action["status"] for action in execution["actions"]] == ["pending", "pending"]
    assert all(action["receipt"] is None for action in execution["actions"])


def test_partial_execution_exposes_only_verified_canonical_receipt(tmp_path, monkeypatch):
    store, task, candidate, review, attempt_id = _two_action_question(tmp_path, monkeypatch)
    store.select_candidate_option(candidate["id"], review["id"], "send")
    store.wake_selected_candidate_execution(candidate["id"], review["id"])
    claimed = store.claim_candidate_execution(candidate["id"], review["id"], "executor", 60)
    store.begin_candidate_action(claimed["id"], "executor", 0, "verified-send")
    store.record_candidate_external_action(claimed["id"], "executor", 0, "verified-send",
        "send_direct_message", {"open_dingtalk_id": "applicant"}, {"message_id": "original-provider-message"})
    store.begin_candidate_action(claimed["id"], "executor", 1, "uncertain-send")
    store.record_candidate_action_outcome(claimed["id"], "executor", 1, "uncertain",
        {"reason": "provider_exception", "source_code": "TIMEOUT"})
    store.finish_candidate_execution(claimed["id"], "executor", "uncertain",
        {"summary": "Notification unverified", "error": {"code": "external_action_uncertain"}})
    store.record_sent_reply(task.conversation_id, task.trigger_message_id, "Earlier material request")
    with store._connect() as db:
        db.execute("update tasks set status='failed' where record_type='reply_tasks' and cast(legacy_key as integer)=?", (task.id,))
    _, detail = build_attempt_detail(store, attempt_id)
    execution = detail["system_execution"]
    assert execution["status"] == "uncertain"
    assert execution["verified_actions"] == 1
    assert execution["total_actions"] == 2
    assert execution["error"]["code"] == "external_action_uncertain"
    assert execution["actions"][0]["receipt"]["external_action_key"] == "verified-send"
    assert execution["actions"][0]["receipt"]["provider_result"] == {"message_id": "original-provider-message"}
    assert execution["actions"][1]["receipt"] is None
    assert execution["actions"][1]["result"]["source_code"] == "TIMEOUT"
    assert "没有全部核验完成" in detail["status"]["message"]

    store.invalidate_review_candidate(candidate["id"], "new relevant source fact")
    _, historical = build_attempt_detail(store, attempt_id)
    assert historical["system_execution"]["candidate_id"] == candidate["id"]
    assert historical["system_execution"]["is_current"] is False
    assert historical["system_execution"]["actions"][0]["receipt"] is not None


def test_verified_action_without_success_ledger_is_not_projected_as_completed(tmp_path, monkeypatch):
    store, _task, candidate, review, attempt_id = _two_action_question(tmp_path, monkeypatch)
    store.select_candidate_option(candidate["id"], review["id"], "send")
    claimed = store.claim_candidate_execution(candidate["id"], review["id"], "executor", 60)
    store.begin_candidate_action(claimed["id"], "executor", 0, "missing-receipt")
    # A historical inconsistent row must not invent a canonical success receipt.
    with store._connect() as db:
        for (public_id,) in db.execute(f"select id from ({CANDIDATE_ACTION_ATTEMPT_SQL}) where execution_id=?", (claimed["id"],)).fetchall():
            update_action(db, 'candidate_action_attempts', public_id, {'status': 'verified'})
    _, detail = build_attempt_detail(store, attempt_id)
    assert detail["system_execution"]["verified_actions"] == 0
    assert detail["system_execution"]["actions"][0]["receipt"] is None
    assert detail["system_execution"]["actions"][0]["status"] == "uncertain"
