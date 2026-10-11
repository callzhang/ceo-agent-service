import test_reviewed_human_decision as fixtures

from app.agent_contracts import ConsumerAgentResult
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
        db.execute("update reply_tasks set status='failed' where id=?", (task.id,))
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
        db.execute("update candidate_action_attempts set status='verified' where execution_id=?",
                   (claimed["id"],))
    _, detail = build_attempt_detail(store, attempt_id)
    assert detail["system_execution"]["verified_actions"] == 0
    assert detail["system_execution"]["actions"][0]["receipt"] is None
    assert detail["system_execution"]["actions"][0]["status"] == "uncertain"
