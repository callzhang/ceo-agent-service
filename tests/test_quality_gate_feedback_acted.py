"""Manual feedback that was rerun and verified is attention, not an error.

Derek 2026-10-24: closing feedback needs code, Skill or config evidence, which
an operational rerun of an approval does not produce, so such feedback stayed
unresolved and kept the service degraded. The gate now lists it as attention
only when the rerun happened after the feedback and finished with every
approved action verified; anything else is still counted as an error.
"""

import test_reviewed_human_decision as fixtures

from app.quality_gate import _check_feedback

BEFORE_EXECUTION = "2000-01-01T00:00:00+00:00"
AFTER_EXECUTION = "2099-01-01T00:00:00+00:00"


def _executed(tmp_path, *, verify_all=True):
    store, _task, candidate, review, attempt_id = fixtures._approved_question(
        tmp_path / "acted.sqlite3"
    )
    store.select_candidate_option(candidate["id"], review["id"], "send")
    store.wake_selected_candidate_execution(candidate["id"], review["id"])
    claimed = store.claim_candidate_execution(candidate["id"], review["id"], "executor", 60)
    store.begin_candidate_action(claimed["id"], "executor", 0, "verified-send")
    if verify_all:
        store.record_candidate_external_action(
            claimed["id"], "executor", 0, "verified-send", "send_direct_message",
            {"open_dingtalk_id": "applicant"}, {"message_id": "provider-message"},
        )
        store.finish_candidate_execution(
            claimed["id"], "executor", "done", {"summary": "All approved actions verified"}
        )
    else:
        store.record_candidate_action_outcome(
            claimed["id"], "executor", 0, "uncertain", {"reason": "provider_exception"}
        )
        store.finish_candidate_execution(
            claimed["id"], "executor", "uncertain",
            {"summary": "unverified", "error": {"code": "external_action_uncertain"}},
        )
    return store, attempt_id


def _gate(store):
    violations, attention = [], []
    with store._connect() as db:
        _check_feedback(db, violations, attention)
    return (
        [(item.code, item.count) for item in violations],
        [(item.code, item.count) for item in attention],
    )


def test_rerun_after_feedback_with_every_action_verified_is_attention(tmp_path):
    store, attempt_id = _executed(tmp_path)
    store.upsert_feedback_event(
        key=f"manual:{attempt_id}", feedback_token="", received_at=BEFORE_EXECUTION
    )
    assert _gate(store) == ([], [("acted_unresolved", 1)])


def test_feedback_that_arrived_after_the_execution_is_still_an_error(tmp_path):
    store, attempt_id = _executed(tmp_path)
    store.upsert_feedback_event(
        key=f"manual:{attempt_id}", feedback_token="", received_at=AFTER_EXECUTION
    )
    assert _gate(store) == ([("unresolved", 1)], [])


def test_execution_without_every_action_verified_is_still_an_error(tmp_path):
    store, attempt_id = _executed(tmp_path, verify_all=False)
    store.upsert_feedback_event(
        key=f"manual:{attempt_id}", feedback_token="", received_at=BEFORE_EXECUTION
    )
    assert _gate(store) == ([("unresolved", 1)], [])


def test_feedback_that_is_not_a_manual_attempt_item_is_still_an_error(tmp_path):
    store, attempt_id = _executed(tmp_path)
    store.upsert_feedback_event(
        key=f"spike:{attempt_id}", feedback_token="token", received_at=BEFORE_EXECUTION
    )
    assert _gate(store) == ([("unresolved", 1)], [])


def test_reopened_feedback_is_an_error_again(tmp_path):
    store, attempt_id = _executed(tmp_path)
    key = f"manual:{attempt_id}"
    store.upsert_feedback_event(key=key, feedback_token="", received_at=BEFORE_EXECUTION)
    with store._connect() as db:
        db.execute(
            "insert into feedback_processing_rounds(feedback_key,round_number,batch_id,status,"
            "workbench_task_id,workbench_turn_id,attempt_id,agent_run_id,commit_sha,"
            "test_evidence_json,restart_evidence_json,health_evidence_json,backlog_evidence_json,"
            "receipt_version,note,started_at,resolved_at,reopened_at,reopen_reason,created_at,"
            "updated_at,scope_receipt_json) values(?,1,'batch-1','resolved','','',0,0,'','{}','{}','{}','{}',"
            "1,'','','','2026-10-24 09:00:00','still wrong','2026-10-24 08:00:00','2026-10-24 09:00:00','{}')",
            (key,),
        )
    assert _gate(store) == ([("unresolved", 1)], [])
