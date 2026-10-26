"""Historical repair uses saved identities and never sends a second OA action."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.store import AgentRole, AutoReplyStore
from app.system_executor import SystemExecutor
from scripts.repair_attempt_review_states import (
    ReturnRepair,
    close_settled_trigger,
    inspect_recorded_return,
    inspect_settled_trigger,
    reconcile_recorded_return,
)
from tests.test_oa_return_readback import Source, action
from tests.test_reviewed_orchestration import setup
from tests.test_system_executor import _candidate


def _uncertain_return(tmp_path, monkeypatch):
    class Clock(datetime):
        offset = timedelta()

        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + cls.offset

    monkeypatch.setattr("app.system_action_handlers.datetime", Clock)

    class ReturningSource(Source):
        def revert_oa_approval_task(self, **kwargs):
            self.calls.append(kwargs)
            self.returned = True
            event = datetime.now(timezone.utc) + timedelta(seconds=2)
            self.records[0]["date"] = event.isoformat()
            self.destination["create_time"] = (event + timedelta(seconds=1)).isoformat()
            Clock.offset = timedelta(seconds=4)
            return {"success": True, "dingOpenErrcode": 0, "result": True}

    store, task, context = setup(tmp_path)
    source = ReturningSource()
    proposal = _candidate(actions=[action().model_dump(mode="json")])
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
    first = SystemExecutor(store, dws=source).execute(task, selected["id"], review["id"], context=context)
    assert first.error.code == "external_action_uncertain"
    store.complete_reply_task(task.id, expected_execution_generation=task.execution_generation)
    execution = store.get_candidate_execution(selected["id"])
    [attempt] = store.list_candidate_action_attempts(execution["id"])
    return store, source, ReturnRepair(task.id, selected["id"], review["id"], execution["id"], attempt["id"])


def test_repair_reconciles_recorded_return_without_provider_dispatch(tmp_path, monkeypatch):
    store, source, repair = _uncertain_return(tmp_path, monkeypatch)
    before = store.list_candidate_action_attempts(repair.execution_id)

    evidence = inspect_recorded_return(store, source, repair)
    assert evidence["destination_task_ids"] == ["destination"]
    assert store.get_candidate_execution(repair.candidate_id)["status"] == "uncertain"
    assert len(source.calls) == 1

    reconciled = reconcile_recorded_return(store, source, repair)
    [after] = store.list_candidate_action_attempts(repair.execution_id)
    assert reconciled == evidence
    assert after["id"] == before[0]["id"]
    assert after["external_action_key"] == before[0]["external_action_key"]
    assert after["status"] == "verified"
    assert store.get_candidate_execution(repair.candidate_id)["status"] == "done"
    assert len(source.calls) == 1


def test_repair_refuses_wrong_action_identity_before_provider_read(tmp_path, monkeypatch):
    store, source, repair = _uncertain_return(tmp_path, monkeypatch)
    reads = len(source.calls)
    mismatched = ReturnRepair(
        repair.task_id, repair.candidate_id, repair.review_id,
        repair.execution_id, repair.action_attempt_id + 1,
    )

    with pytest.raises(ValueError, match="uncertain action mismatch"):
        reconcile_recorded_return(store, source, mismatched)

    assert len(source.calls) == reads
    assert store.get_candidate_execution(repair.candidate_id)["status"] == "uncertain"


class SettledSource:
    def get_current_user_id(self):
        return "principal"

    def read_oa_approval_detail(self, process_id):
        return {"success": True, "result": {
            "processInstanceId": process_id, "businessId": "business",
        }}

    def read_oa_process_instance_openapi(self, process_id):
        return {"errcode": 0, "process_instance": {
            "business_id": "business", "status": "RUNNING",
            "tasks": [
                {"taskid": "old-node", "userid": "principal", "task_status": "COMPLETED", "task_result": "AGREE"},
                {"taskid": "new-node", "userid": "other-approver", "task_status": "PAUSED", "task_result": "NONE"},
                {"taskid": "running-node", "userid": "other-approver", "task_status": "RUNNING", "task_result": "NONE"},
            ],
        }}


def test_settled_trigger_closes_only_the_saved_old_node(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="conversation", conversation_title="Review",
        single_chat=True, trigger_message_id="original-message", trigger_sender="Derek",
        trigger_create_time="2026-10-14 14:41:07", trigger_text="Card for old-node",
    )
    task = store.claim_reply_tasks(1)[0]
    store.fail_reply_task(task.id, "audit_revision_exhausted", expected_execution_generation=task.execution_generation)
    source = SettledSource()

    evidence = inspect_settled_trigger(
        store, source, task_id=task.id, process_id="process",
        original_oa_task_id="old-node", changed_card_task_id="new-node",
    )
    assert json.loads(evidence)["changed_card_task_status"] == "PAUSED"
    assert store.get_reply_task(task.id).status == "failed"

    assert close_settled_trigger(
        store, source, task_id=task.id, process_id="process",
        original_oa_task_id="old-node", changed_card_task_id="new-node",
    )
    assert store.get_reply_task(task.id).status == "done"
    [saved] = [
        error for error in store.list_errors()
        if error.message_id == "original-message" and error.kind == "reply_task_already_settled"
    ]
    assert json.loads(saved.resolution)["original_task_result"] == "AGREE"


def test_settled_trigger_refuses_current_principal_node(tmp_path):
    store = AutoReplyStore(tmp_path / "state.sqlite3")
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="conversation", conversation_title="Review",
        single_chat=True, trigger_message_id="original-message", trigger_sender="Derek",
        trigger_create_time="2026-10-14 14:41:07", trigger_text="Card for old-node",
    )
    task = store.claim_reply_tasks(1)[0]
    store.fail_reply_task(task.id, "audit_revision_exhausted", expected_execution_generation=task.execution_generation)
    source = SettledSource()
    actual = source.read_oa_process_instance_openapi

    def still_owned(process_id):
        payload = actual(process_id)
        payload["process_instance"]["tasks"][2]["userid"] = "principal"
        return payload

    source.read_oa_process_instance_openapi = still_owned
    with pytest.raises(ValueError, match="not settled or current ownership is ambiguous"):
        close_settled_trigger(
            store, source, task_id=task.id, process_id="process",
            original_oa_task_id="old-node", changed_card_task_id="new-node",
        )
    assert store.get_reply_task(task.id).status == "failed"
