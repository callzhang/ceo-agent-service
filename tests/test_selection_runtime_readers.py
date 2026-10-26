"""Console and Worker project the selected immutable candidate's own option."""

import json
from types import SimpleNamespace

from tests.test_system_executor import FakeStore, _candidate


def _question():
    plan = _candidate(actions=[{
        "description": "Original approval", "action_identity": "old-action",
        "capability": "dingtalk-oa", "operation": "approve",
        "target": {"process_instance_id": "old-process", "task_id": "old-task"},
        "payload": {"remark": "Original reviewed remark"},
    }]).proposal.model_dump(mode="json")
    return _candidate(outcome="needs_human", options=[
        {"key": "approve", "label": "Approve", "instruction": "Approve", "consequence": "Approved", "plan": plan},
        {"key": "stop", "label": "Stop", "instruction": "Stop", "consequence": "Stopped", "terminal_outcome": "skipped", "reason": "Stop"},
    ])


def test_attempt_projection_derives_branch_from_exact_candidate_and_option():
    from app.web_api.attempts import _system_execution_payload

    candidate = _question()
    store = FakeStore(candidate, selected=candidate.decision_options[0].model_dump(mode="json"))
    reviewed = store.current_reviewed_candidate(1, "generation")
    # Even a supplied response copy cannot replace the actual selected option.
    reviewed["branch_json"] = candidate.decision_options[1].model_dump_json()
    result = _system_execution_payload(store, reviewed, is_current=True)
    assert result["total_actions"] == 1
    assert result["actions"][0]["action_identity"] == "old-action"


def test_worker_oa_history_reads_selection_of_result_candidate():
    from app.worker import DingTalkAutoReplyWorker

    candidate = _question()
    worker = DingTalkAutoReplyWorker.__new__(DingTalkAutoReplyWorker)
    selected_candidates = []

    def selection(candidate_id):
        selected_candidates.append(candidate_id)
        return {"id": 3, "candidate_id": 1, "option_key": "approve"}

    worker.store = SimpleNamespace(
        get_review_candidate=lambda candidate_id: {"id": 1, "candidate_json": candidate.model_dump_json()},
        get_candidate_selection=selection,
        current_reviewed_candidate=lambda *args: {
            "id": 99, "selection_id": 100, "branch_json": '{"key":"stop"}',
        },
        list_verified_candidate_actions=lambda task_id: [{
            "action_identity": "old-action", "target_identifiers_json": json.dumps({
                "process_instance_id": "old-process", "task_id": "old-task",
            }), "provider_result_json": '{"receipt":"old-receipt"}',
            "provider_receipt_id": 7,
        }],
    )
    result = worker._orchestration_oa_metadata(SimpleNamespace(id=1, oa_url="old-url"),
        SimpleNamespace(candidate_id=1, execution_result=SimpleNamespace(external_result=object())))
    assert selected_candidates == [1]
    assert result["oa_process_instance_id"] == "old-process"
    assert result["oa_remark"] == "Original reviewed remark"
    assert result["oa_action_result_json"] == '{"receipt":"old-receipt"}'
    assert result["oa_action_receipt_id"] == 7
