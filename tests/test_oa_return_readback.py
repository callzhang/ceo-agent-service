from hashlib import sha256
import json

import pytest

from app.agent_contracts import ProposedAction
from app.system_action_handlers import OaRevertHandler


def action():
    return ProposedAction(description="Return missing material", action_identity="return-node",
        capability="dingtalk-oa", operation="revert_task",
        target={"process_instance_id": "process", "task_id": "original", "target_activity_id": "handover"},
        payload={"remark": "Need exact handover facts", "revert_action": "REVERT_FOR_APPROVAL"})


class Source:
    def __init__(self):
        self.calls = []
        self.read_calls = 0
        self.returned = False
        self.records = [{"operation_type": "REDIRECT_PROCESS", "userid": "principal",
            "date": "2026-10-13 18:06:28", "remark": "Need exact handover facts"}]
        self.destination = {"taskid": "destination", "activity_id": "handover",
            "create_time": "2026-10-13 18:06:28", "task_status": "RUNNING", "userid": "handover-owner"}
        self.business_id = "business"
        self.source_status = "CANCELED"
        self.extra_tasks = []

    def get_current_user_id(self):
        return "principal"

    def read_oa_approval_detail(self, process_id):
        self.read_calls += 1
        return {"success": True, "result": {"processInstanceId": process_id,
            "businessId": "business", "tasks": [{"taskId": "original",
                "userId": "retired-owner" if self.returned else "principal",
                "taskStatus": "CANCELED" if self.returned else "RUNNING", "taskResult": "NONE"}]}}

    def revert_oa_approval_task(self, **kwargs):
        self.calls.append(kwargs)
        self.returned = True
        return {"success": True, "dingOpenErrcode": 0, "result": True}

    def read_oa_process_instance_openapi(self, process_id):
        self.read_calls += 1
        assert process_id == "process"
        return {"errcode": 0, "process_instance": {"business_id": self.business_id,
            "tasks": [{"taskid": "original", "task_status": self.source_status}, self.destination, *self.extra_tasks],
            "operation_records": self.records}}


def attempt(status="dispatched", result=None):
    return {"action_attempt": {"status": status, "external_action_key": "key",
        "result_json": json.dumps(result or {})}}


def acceptance():
    return {"provider_response": {"success": True, "dingOpenErrcode": 0, "result": True},
        "dispatch_principal_user_id": "principal",
        "accepted_action_digest": sha256(action().model_dump_json().encode()).hexdigest()}


def test_return_acceptance_is_reconciled_after_persisted_dispatch(tmp_path):
    from app.reviewed_sources import capture_candidate_sources
    from app.store import AgentRole
    from app.system_executor import SystemExecutor
    from tests.test_reviewed_orchestration import setup
    from tests.test_system_executor import _candidate

    class ReturningSource(Source):
        def read_oa_approval_detail(self, process_id):
            detail = super().read_oa_approval_detail(process_id)
            detail["result"]["formValueVOS"] = []
            return detail

    store, task, context = setup(tmp_path)
    source = ReturningSource()
    proposal = capture_candidate_sources(_candidate(actions=[action().model_dump(mode="json")]), context, source)
    consumer = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=None, operation_id="", owner="consumer").run
    store.complete_agent_run(consumer.id, proposal.model_dump(mode="json"), owner="consumer")
    selected = store.persist_review_candidate(task, consumer, proposal)
    audit = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=0, turn_attempt=0, parent_agent_run_id=consumer.id, operation_id="review", owner="audit").run
    approval = {"outcome": "approve", "candidate_digest": selected["candidate_digest"]}
    store.complete_agent_run(audit.id, approval, owner="audit")
    review = store.record_candidate_review(selected["id"], audit.id, approval)
    first = SystemExecutor(store, dws=source).execute(task, selected["id"], review["id"], context=context)
    assert first.outcome == "executed", first.model_dump(mode="json")
    execution = store.get_candidate_execution(selected["id"])
    [original] = store.list_candidate_action_attempts(execution["id"])
    assert original["status"] == "verified"
    assert json.loads(original["result_json"])["provider_response"]["result"] is True
    assert len(source.calls) == 1
    assert source.read_calls >= 4


def test_return_acceptance_is_preserved_for_readonly_reconciliation():
    source = Source()
    handler = OaRevertHandler(source)
    accepted = handler.dispatch(action(), action_key="key", candidate=attempt())
    assert accepted.status == "uncertain"
    assert accepted.provider_result["provider_response"]["success"] is True
    assert source.calls[0]["target_activity_id"] == "handover"
    verified = handler.reconcile(action(), action_key="key", candidate=attempt("uncertain", accepted.provider_result))
    assert verified.status == "verified"
    assert verified.provider_result["destination_activity_id"] == "handover"
    assert len(source.calls) == 1


@pytest.mark.parametrize("timestamps", ["absent", "inconsistent"])
def test_accepted_return_matches_provider_evidence_independent_of_time(timestamps):
    source = Source()
    source.returned = True
    proof = acceptance()
    if timestamps == "absent":
        source.records[0].pop("date")
        source.destination.pop("create_time")
    else:
        proof["dispatch_started_at"] = "2026-10-13T10:06:25.641Z"
        proof["accepted_at"] = "2026-10-13T10:06:29.001Z"
        source.records[0]["date"] = "2026-10-13T10:06:29.081180+00:00"
        source.destination["create_time"] = "2026-10-13T10:06:25+00:00"
        source.extra_tasks = [{"taskid": "earlier-visit", "activity_id": "handover"}]
    candidate = attempt("uncertain", proof)
    if timestamps == "inconsistent":
        candidate["action_attempt"]["created_at"] = "2026-10-13T10:06:25Z"

    result = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=candidate)

    assert result.status == "verified"
    assert result.provider_result["destination_activity_id"] == "handover"
    assert "destination_task_ids" not in result.provider_result
    assert source.calls == []


@pytest.mark.parametrize("response", [
    {"success": True, "result": True, "errorCode": 0},
    {"success": True, "result": True, "dingOpenErrcode": 1},
    {"success": False, "result": True, "dingOpenErrcode": 0},
])
def test_only_dingtalk_success_envelope_is_accepted(response):
    assert OaRevertHandler._accepted(response) is False


@pytest.mark.parametrize("change", [
    "missing_record", "wrong_actor", "wrong_remark", "ambiguous_event",
    "wrong_destination", "source_still_running", "different_process", "wrong_key",
    "wrong_status", "wrong_action_digest",
])
def test_cancelled_node_alone_never_verifies_a_return(change):
    source = Source()
    source.returned = True
    candidate = attempt("uncertain", acceptance())
    if change == "missing_record":
        source.records = []
    elif change == "wrong_actor":
        source.records[0]["userid"] = "other"
    elif change == "wrong_remark":
        source.records[0]["remark"] = "Another request"
    elif change == "ambiguous_event":
        source.records.append(dict(source.records[0]))
    elif change == "wrong_destination":
        source.destination["activity_id"] = "different"
    elif change == "source_still_running":
        source.source_status = "RUNNING"
    elif change == "different_process":
        source.business_id = "different"
    elif change == "wrong_key":
        candidate["action_attempt"]["external_action_key"] = "other"
    elif change == "wrong_status":
        candidate["action_attempt"]["status"] = "prepared"
    elif change == "wrong_action_digest":
        candidate = attempt("uncertain", {**acceptance(), "accepted_action_digest": "different"})
    outcome = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=candidate)
    assert outcome.status == "uncertain"
    assert source.calls == []


def test_return_is_not_dispatched_without_exact_journal_binding():
    source = Source()
    result = OaRevertHandler(source).dispatch(action(), action_key="key", candidate={})
    assert result.status == "uncertain"
    assert source.calls == []


def test_other_operations_do_not_make_unique_return_ambiguous():
    source = Source()
    source.returned = True
    source.records.extend([
        {"operation_type": "PROCESS_CC"},
        {"operation_type": "EXECUTE_TASK_NORMAL"},
        {"operation_type": "REDIRECT_PROCESS", "userid": "principal", "remark": "Different request"},
    ])

    outcome = OaRevertHandler(source).reconcile(
        action(), action_key="key", candidate=attempt("uncertain", acceptance()))

    assert outcome.status == "verified"
    assert source.calls == []


@pytest.mark.parametrize("field", ["userid", "remark", "operation_type"])
def test_incomplete_potential_return_does_not_prove_uniqueness(field):
    source = Source()
    source.returned = True
    partial = dict(source.records[0])
    partial.pop(field)
    source.records.append(partial)
    assert OaRevertHandler(source).reconcile(action(), action_key="key",
        candidate=attempt("uncertain", acceptance())).status == "uncertain"


@pytest.mark.parametrize("response", [{"success": True, "errorCode": 0, "result": False},
    {"success": True, "errorCode": 0}, {"success": True, "result": True, "errorCode": 7}])
def test_negative_or_incomplete_operation_ack_is_not_acceptance(response):
    source = Source()
    source.returned = True
    proof = acceptance()
    proof["provider_response"] = response
    assert OaRevertHandler(source).reconcile(action(), action_key="key",
        candidate=attempt("uncertain", proof)).status == "uncertain"


def test_same_remark_in_two_returns_stays_uncertain_even_with_distinct_timestamps():
    source = Source()
    source.returned = True
    source.records.append({**source.records[0], "date": "2026-10-13 18:06:40"})
    assert OaRevertHandler(source).reconcile(action(), action_key="key",
        candidate=attempt("uncertain", acceptance())).status == "uncertain"
