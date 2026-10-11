import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.agent_contracts import ProposedAction
from app.system_action_handlers import OaCommentHandler


OBSERVED_AT = int((datetime.now(timezone.utc) - timedelta(seconds=10)).timestamp() * 1000)
BOUNDARY = datetime.fromtimestamp((OBSERVED_AT - 2000) / 1000, timezone.utc).isoformat()


class CommentSource:
    def __init__(self):
        self.detail = {
            "success": True,
            "result": {
                "processInstanceId": "original-process",
                "operationRecords": [{
                    "type": "ADD_REMARK", "userId": "principal",
                    "date": OBSERVED_AT, "remark": "Exact approved comment",
                    "result": "NONE", "activityId": None, "taskId": None,
                }],
            },
        }
        self.reads = []
        self.comments = 0

    def get_current_user_id(self):
        return "principal"

    def read_oa_approval_detail(self, process_id):
        self.reads.append(("detail", process_id))
        return self.detail

    def read_oa_approval_records(self, process_id):
        self.reads.append(("records", process_id))
        return {"success": True, "result": {"operationRecords": [{
            "operationType": "ADD_REMARK", "userId": "principal",
            "operationTime": OBSERVED_AT, "operationResult": "NONE",
        }]}}

    def comment_oa_approval(self, *args):
        self.comments += 1
        raise AssertionError("Unknown comment effects must never be redispatched")


def action():
    return ProposedAction.model_validate({
        "description": "Request missing material", "action_identity": "clarify-material",
        "capability": "dingtalk-oa", "operation": "comment",
        "target": {"process_instance_id": "original-process"},
        "payload": {"content": "Exact approved comment"},
    })


def candidate():
    return {"action_attempt": {
        "status": "uncertain", "created_at": BOUNDARY,
        "external_action_key": "original-key",
    }}


def test_unknown_oa_comment_is_verified_from_exact_native_detail_without_resend():
    source = CommentSource()
    result = OaCommentHandler(source).reconcile(
        action(), action_key="original-key", candidate=candidate(),
    )
    assert result.status == "verified"
    assert source.reads == [("detail", "original-process")]
    assert source.comments == 0
    record = result.provider_result["operation_record"]
    assert record == {"type": "ADD_REMARK", "user_id": "principal", "date": OBSERVED_AT}
    assert result.provider_result["process_instance_id"] == "original-process"
    assert "comment_id" not in result.provider_result
    assert "remark" not in result.provider_result


@pytest.mark.parametrize("fault", [
    "wrong_process", "wrong_actor", "wrong_text", "wrong_type", "old_record",
    "invalid_date", "boolean_date", "future_record", "duplicate", "provider_failure",
    "missing_records", "missing_actor", "missing_boundary", "naive_boundary",
    "wrong_action_key", "wrong_attempt_status", "root_has_more", "detail_has_more",
])
def test_oa_comment_unknown_effects_remain_unknown_when_evidence_is_not_exact(fault):
    source = CommentSource()
    selected = candidate()
    detail = source.detail["result"]
    record = detail["operationRecords"][0]
    if fault == "wrong_process":
        detail["processInstanceId"] = "another-process"
    elif fault == "wrong_actor":
        record["userId"] = "another-person"
    elif fault == "wrong_text":
        record["remark"] += " changed"
    elif fault == "wrong_type":
        record["type"] = "EXECUTE_TASK_NORMAL"
    elif fault == "old_record":
        record["date"] -= 10000
    elif fault == "invalid_date":
        record["date"] = "unavailable"
    elif fault == "boolean_date":
        record["date"] = True
    elif fault == "future_record":
        record["date"] = 32503680000000
    elif fault == "duplicate":
        detail["operationRecords"].append(copy.deepcopy(record))
    elif fault == "provider_failure":
        source.detail["success"] = False
    elif fault == "missing_records":
        del detail["operationRecords"]
    elif fault == "missing_actor":
        source.get_current_user_id = lambda: ""
    elif fault == "missing_boundary":
        selected["action_attempt"].pop("created_at")
    elif fault == "naive_boundary":
        selected["action_attempt"]["created_at"] = "2026-10-11 03:08:18"
    elif fault == "wrong_action_key":
        selected["action_attempt"]["external_action_key"] = "different-key"
    elif fault == "wrong_attempt_status":
        selected["action_attempt"]["status"] = "dispatched"
    elif fault == "root_has_more":
        source.detail["hasMore"] = True
    elif fault == "detail_has_more":
        detail["hasMore"] = True
    result = OaCommentHandler(source).reconcile(
        action(), action_key="original-key", candidate=selected,
    )
    assert result.status == "uncertain"
    assert source.comments == 0


def test_identical_oa_comment_before_dispatch_does_not_cover_an_unknown_action():
    source = CommentSource()
    record = source.detail["result"]["operationRecords"][0]
    old = copy.deepcopy(record)
    old["date"] -= 10000
    source.detail["result"]["operationRecords"].insert(0, old)
    result = OaCommentHandler(source).reconcile(
        action(), action_key="original-key", candidate=candidate(),
    )
    assert result.status == "verified"
    assert result.provider_result["operation_record"]["date"] == OBSERVED_AT
    assert source.comments == 0


def test_system_retry_adopts_exact_comment_record_without_second_dispatch():
    from app.system_executor import SystemExecutor
    from tests.test_system_executor import FakeStore, _candidate

    class ReceiptWithoutId(CommentSource):
        def comment_oa_approval(self, process_id, content):
            assert process_id == "original-process" and content == "Exact approved comment"
            self.comments += 1
            return {"success": True}

    source = ReceiptWithoutId()
    store = FakeStore(_candidate(actions=[action().model_dump(mode="json")]))
    begin = store.begin_candidate_action

    def begin_with_original_boundary(execution_id, owner, index, action_key):
        attempt = begin(execution_id, owner, index, action_key)
        attempt.setdefault("created_at", BOUNDARY)
        return attempt

    store.begin_candidate_action = begin_with_original_boundary
    executor = SystemExecutor(store, {("dingtalk-oa", "comment"): OaCommentHandler(source)}, owner="worker")
    task = store.get_reply_task(1)
    first = executor.execute(task, 1, 2)
    assert first.error.code == "external_action_uncertain"
    key = store.attempts[0]["external_action_key"]
    second = executor.execute(task, 1, 2)
    assert second.outcome == "executed"
    assert source.comments == 1
    assert store.attempts[0]["status"] == "verified"
    assert store.ledger["external_action_key"] == key
    assert json.loads(store.ledger["provider_result_json"])["operation_record"]["date"] == OBSERVED_AT


@pytest.mark.parametrize("missing", ["userId", "remark", "date"])
def test_incomplete_competing_remark_does_not_prove_unique_oa_comment(missing):
    source = CommentSource()
    records = source.detail["result"]["operationRecords"]
    competitor = copy.deepcopy(records[0])
    del competitor[missing]
    records.append(competitor)
    result = OaCommentHandler(source).reconcile(
        action(), action_key="original-key", candidate=candidate(),
    )
    assert result.status == "uncertain"
    assert source.comments == 0


@pytest.mark.parametrize("exclusion", ["older", "other_author", "different_body"])
def test_demonstrably_unrelated_incomplete_remark_does_not_hide_exact_receipt(exclusion):
    source = CommentSource()
    records = source.detail["result"]["operationRecords"]
    competitor = copy.deepcopy(records[0])
    if exclusion == "older":
        del competitor["userId"]
        competitor["date"] -= 10000
    elif exclusion == "other_author":
        del competitor["remark"]
        competitor["userId"] = "another-person"
    else:
        del competitor["userId"]
        competitor["remark"] = "Different comment"
    records.append(competitor)
    result = OaCommentHandler(source).reconcile(
        action(), action_key="original-key", candidate=candidate(),
    )
    assert result.status == "verified"
    assert source.comments == 0


def test_oa_comment_boundary_uses_instant_not_timezone_wall_clock():
    source = CommentSource()
    selected = candidate()
    selected["action_attempt"]["created_at"] = datetime.fromisoformat(BOUNDARY).astimezone(
        timezone(timedelta(hours=8)),
    ).isoformat()
    result = OaCommentHandler(source).reconcile(
        action(), action_key="original-key", candidate=selected,
    )
    assert result.status == "verified"
    assert source.comments == 0


@pytest.mark.parametrize("change", ["unchanged", "missing_context", "changed_source"])
def test_oa_comment_recovery_after_store_reopen_preserves_source_and_dispatch_fences(tmp_path, change):
    from app.reviewed_sources import capture_candidate_sources
    from app.store import AgentRole, AutoReplyStore
    from app.system_executor import SystemExecutor
    from tests.test_reviewed_orchestration import setup
    from tests.test_system_executor import _candidate

    class NativeSource(CommentSource):
        def __init__(self):
            super().__init__()
            self.detail["result"]["operationRecords"] = []
            self.detail["result"]["formValueVOS"] = [{"id": "amount", "value": "100"}]

        def comment_oa_approval(self, process_id, content):
            self.comments += 1
            self.detail["result"]["operationRecords"].append({
                "type": "ADD_REMARK", "userId": "principal", "remark": content,
                "date": int(datetime.now(timezone.utc).timestamp() * 1000),
            })
            return {"success": True}

    store, task, context = setup(tmp_path)
    source = NativeSource()
    proposal = capture_candidate_sources(
        _candidate(actions=[action().model_dump(mode="json")]), context, source,
    )
    consumer = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    ).run
    store.complete_agent_run(consumer.id, proposal.model_dump(mode="json"), owner="consumer")
    selected = store.persist_review_candidate(task, consumer, proposal)
    audit = store.claim_agent_run(
        task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id,
        operation_id="review", owner="audit",
    ).run
    approval = {"outcome": "approve", "candidate_digest": selected["candidate_digest"]}
    store.complete_agent_run(audit.id, approval, owner="audit")
    review = store.record_candidate_review(selected["id"], audit.id, approval)
    first = SystemExecutor(store, dws=source).execute(
        task, selected["id"], review["id"], context=context,
    )
    assert first.error.code == "external_action_uncertain"
    execution = store.get_candidate_execution(selected["id"])
    [original] = store.list_candidate_action_attempts(execution["id"])
    reopened = AutoReplyStore(store.path)
    if change == "changed_source":
        source.detail["result"]["formValueVOS"][0]["value"] = "900"
    recovered = SystemExecutor(reopened, dws=source).execute(
        task, selected["id"], review["id"],
        context=None if change == "missing_context" else context,
    )
    [after] = reopened.list_candidate_action_attempts(execution["id"])
    assert after["created_at"] == original["created_at"]
    assert after["external_action_key"] == original["external_action_key"]
    assert source.comments == 1
    receipt = reopened.get_candidate_external_action(original["external_action_key"])
    if change == "unchanged":
        assert recovered.outcome == "executed"
        assert after["status"] == "verified"
        assert receipt is not None
        result = json.loads(receipt["provider_result_json"])
        assert result["operation_record"]["user_id"] == "principal"
        assert "comment_id" not in result and "readback" not in result
    else:
        assert recovered.error.code == (
            "reviewed_source_unavailable" if change == "missing_context" else "business_state_changed"
        )
        assert after["status"] == "uncertain"
        assert receipt is None
