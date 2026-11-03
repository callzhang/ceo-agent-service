from datetime import datetime, timedelta, timezone

import pytest

from app.agent_contracts import ProposedAction
from app.consumer_purpose import purpose_output_model
from app.dws_client import DwsOaApprovalCandidate
from app.oa_review_request import OaReviewRequestHandler, pending_oa_review_request
from app.store import AutoReplyStore
from app.system_action_handlers import native_dws_handlers
from app.task_scanners import scan_pending_oa_approvals

PROCESS = "proc-1"


class _CommentedApproval:
    """One approval Derek holds and has already commented on."""

    def list_pending_oa_approvals(self, *, page, size, start, end):
        return [DwsOaApprovalCandidate(process_instance_id=PROCESS, title="供应商结算")]

    def get_current_user_id(self):
        return "principal-user-1"

    def read_oa_approval_tasks(self, process_instance_id):
        return {"result": {"tasks": [{"taskId": "task-1", "status": "RUNNING"}]}}

    def read_oa_process_instance_openapi(self, process_instance_id):
        return {"result": {"tasks": [
            {"taskId": "task-1", "userId": "principal-user-1", "status": "RUNNING"}
        ]}}

    def read_oa_approval_records(self, process_instance_id):
        return {"result": {"operationRecords": [
            {"operationType": "ADD_REMARK", "operationTime": 1, "userId": "requester"},
            {"operationType": "ADD_REMARK", "operationTime": 2, "userId": "principal-user-1"},
        ]}}


def _action(reason: str = "申请人已开通结算单查看权限，请继续核验") -> ProposedAction:
    return ProposedAction(
        description="请 OA 重新审阅", action_identity="oa-review-1",
        capability="dingtalk-oa", operation="request_review",
        target={"process_instance_id": PROCESS}, payload={"reason": reason},
    )


def _store_with_scan_schedule(tmp_path) -> AutoReplyStore:
    store = AutoReplyStore(tmp_path / "task.sqlite3")
    store.create_scheduled_task(
        name="处理新的钉钉 OA 审批", command="scan-oa-approvals",
        cron_expression="0 0 * * * *", timezone_name="Asia/Shanghai", enabled=True,
    )
    return store


def test_request_review_is_a_registered_typed_action() -> None:
    assert isinstance(native_dws_handlers(None, None)[("dingtalk-oa", "request_review")], OaReviewRequestHandler)
    for payload in ({}, {"reason": " "}, {"reason": "x", "content": "y"}):
        with pytest.raises(ValueError, match="request_review"):
            ProposedAction(
                description="d", action_identity="i", capability="dingtalk-oa",
                operation="request_review", target={"process_instance_id": PROCESS},
                payload=payload,
            )
    output = purpose_output_model("general").model_validate({
        "action": "request_oa_review", "reason": "对方已补充材料",
        "target": {"process_instance_id": PROCESS}, "payload": {"reason": "对方已补充材料"},
        "evidence_refs": [],
    })
    [action] = output.to_proposal().actions
    assert (action.capability, action.operation) == ("dingtalk-oa", "request_review")


def test_handler_records_the_request_once_and_starts_the_scan_now(tmp_path) -> None:
    store = _store_with_scan_schedule(tmp_path)
    handler = OaReviewRequestHandler(store)

    first = handler.dispatch(_action(), action_key="key-1", candidate={})
    again = handler.dispatch(_action(), action_key="key-1", candidate={})

    assert first.status == again.status == "verified"
    request = pending_oa_review_request(store, PROCESS)
    assert request["request_key"] == "key-1"
    assert request["reason"] == "申请人已开通结算单查看权限，请继续核验"
    run = store.get_scheduled_task_run(first.provider_result["scheduled_task_run_id"])
    assert run.trigger_kind == "manual"
    assert handler.reconcile(_action(), action_key="key-1", candidate={}).status == "verified"
    assert handler.reconcile(_action(), action_key="other", candidate={}) is None


def test_handler_fails_without_an_oa_scan_schedule(tmp_path) -> None:
    store = AutoReplyStore(tmp_path / "task.sqlite3")

    outcome = OaReviewRequestHandler(store).dispatch(_action(), action_key="key-1", candidate={})

    assert outcome.status == "failed"
    assert pending_oa_review_request(store, PROCESS) is None


def test_scan_queues_a_commented_approval_once_when_review_is_requested(tmp_path) -> None:
    store = _store_with_scan_schedule(tmp_path)
    dws = _CommentedApproval()
    day = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
    assert scan_pending_oa_approvals(store, dws, now=day) == 1
    # Derek has commented and nothing changed: the scan waits.
    assert scan_pending_oa_approvals(store, dws, now=day + timedelta(days=1)) == 0

    OaReviewRequestHandler(store).dispatch(_action(), action_key="key-1", candidate={})
    assert scan_pending_oa_approvals(store, dws, now=day + timedelta(days=1, hours=1)) == 1

    [*_, task] = store.list_reply_tasks(statuses=("pending",), channel="dingtalk")
    assert task.conversation_id == f"oa_pending_scan:{PROCESS}"
    assert "申请人已开通结算单查看权限" in task.trigger_text
    assert pending_oa_review_request(store, PROCESS) is None
    # The request is consumed: the next scan waits again.
    assert scan_pending_oa_approvals(store, dws, now=day + timedelta(days=1, hours=2)) == 0
