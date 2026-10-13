from hashlib import sha256
from datetime import datetime
import json

import pytest

from app.agent_contracts import ProposedAction
from app.system_action_handlers import OaRevertHandler


def install_provider_clock(monkeypatch):
    class Clock(datetime):
        current = datetime.fromisoformat("2026-10-13T10:07:00+00:00")
        @classmethod
        def now(cls, tz=None):
            value = cls.current
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr("app.system_action_handlers.datetime", Clock)
    return Clock


@pytest.fixture(autouse=True)
def provider_read_clock(monkeypatch):
    return install_provider_clock(monkeypatch)


def action():
    return ProposedAction(description="Return missing material", action_identity="return-node",
        capability="dingtalk-oa", operation="revert_task",
        target={"process_instance_id": "process", "task_id": "original", "target_activity_id": "handover"},
        payload={"remark": "Need exact handover facts", "revert_action": "REVERT_FOR_APPROVAL"})


class Source:
    def __init__(self):
        self.clock = None
        self.calls = []
        self.returned = False
        self.records = [{"operation_type": "REDIRECT_PROCESS", "userid": "principal",
            "date": "2026-10-13 18:06:28", "remark": "Need exact handover facts"}]
        self.destination = {"taskid": "destination", "activity_id": "handover",
            "create_time": "2026-10-13 18:06:28", "task_status": "RUNNING", "userid": "handover-owner"}
        self.business_id = "business"

    def get_current_user_id(self):
        return "principal"

    def read_oa_approval_detail(self, process_id):
        return {"success": True, "result": {"processInstanceId": process_id,
            "businessId": "business", "tasks": [{"taskId": "original",
                "userId": "retired-owner" if self.returned else "principal",
                "taskStatus": "CANCELED" if self.returned else "RUNNING", "taskResult": "NONE"}]}}

    def revert_oa_approval_task(self, **kwargs):
        self.calls.append(kwargs)
        self.returned = True
        if self.clock:
            self.clock.current = datetime.fromisoformat("2026-10-13T10:06:29.001+00:00")
        return {"success": True, "errorCode": 0, "result": True}

    def read_oa_process_instance_openapi(self, process_id):
        assert process_id == "process"
        return {"errcode": 0, "process_instance": {"business_id": self.business_id,
            "tasks": [{"taskid": "original", "task_status": "CANCELED"}, self.destination],
            "operation_records": self.records}}


def attempt(status="dispatched", result=None):
    return {"action_attempt": {"status": status, "external_action_key": "key",
        "created_at": "2026-10-13T10:06:25.641Z", "result_json": json.dumps(result or {})}}


def acceptance():
    return {"provider_response": {"success": True, "errorCode": 0, "result": True},
        "accepted_at": "2026-10-13T10:06:29.001Z", "dispatch_started_at": "2026-10-13T10:06:25.641Z",
        "dispatch_principal_user_id": "principal",
        "accepted_action_digest": sha256(action().model_dump_json().encode()).hexdigest()}


def test_return_acceptance_is_preserved_for_readonly_reconciliation(provider_read_clock):
    source = Source()
    source.clock = provider_read_clock
    source.clock.current = datetime.fromisoformat("2026-10-13T10:06:25.641+00:00")
    handler = OaRevertHandler(source)
    accepted = handler.dispatch(action(), action_key="key", candidate=attempt())
    assert accepted.status == "uncertain"
    assert accepted.provider_result["provider_response"]["success"] is True
    assert source.calls[0]["target_activity_id"] == "handover"
    verified = handler.reconcile(action(), action_key="key", candidate=attempt("uncertain", accepted.provider_result))
    assert verified.status == "verified"
    assert verified.provider_result["destination_activity_id"] == "handover"
    assert verified.provider_result["destination_task_ids"] == ["destination"]
    assert len(source.calls) == 1


@pytest.mark.parametrize("change", [
    "missing_record", "wrong_actor", "wrong_remark", "old_event", "ambiguous_event",
    "wrong_destination", "old_destination", "different_process", "missing_boundary", "wrong_key",
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
    elif change == "old_event":
        source.records[0]["date"] = "2026-10-13 18:06:25"
    elif change == "ambiguous_event":
        source.records.append(dict(source.records[0]))
    elif change == "wrong_destination":
        source.destination["activity_id"] = "different"
    elif change == "old_destination":
        source.destination["create_time"] = "2026-10-13 18:06:24"
    elif change == "different_process":
        source.business_id = "different"
    elif change == "missing_boundary":
        candidate["action_attempt"]["created_at"] = "2026-10-13 10:06:25"
    elif change == "wrong_key":
        candidate["action_attempt"]["external_action_key"] = "other"
    outcome = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=candidate)
    assert outcome.status == "uncertain"
    assert source.calls == []


def test_seconds_only_event_in_dispatch_second_is_not_positive_evidence():
    source = Source()
    source.returned = True
    source.records[0]["date"] = "2026-10-13 18:06:25"
    source.destination["create_time"] = source.records[0]["date"]
    outcome = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=attempt("uncertain", acceptance()))
    assert outcome.status == "uncertain"


def test_return_is_not_dispatched_without_exact_journal_binding():
    source = Source()
    result = OaRevertHandler(source).dispatch(action(), action_key="key", candidate={})
    assert result.status == "uncertain"
    assert source.calls == []


@pytest.mark.parametrize("field", ["userid", "remark", "date", "operation_type"])
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


def test_destination_can_activate_after_return_before_next_provider_transition():
    source = Source()
    source.returned = True
    source.destination["create_time"] = "2026-10-13 18:06:29"
    source.records.append({"operation_type": "EXECUTE_TASK_NORMAL", "date": "2026-10-13 18:06:35"})
    assert OaRevertHandler(source).reconcile(action(), action_key="key",
        candidate=attempt("uncertain", acceptance())).status == "verified"


def test_later_same_remark_return_is_not_assigned_to_original_accepted_request():
    source = Source()
    source.returned = True
    later = dict(source.records[0])
    later["date"] = "2026-10-13 18:06:40"
    source.records.append(later)
    assert OaRevertHandler(source).reconcile(action(), action_key="key",
        candidate=attempt("uncertain", acceptance())).status == "verified"


@pytest.mark.parametrize("kind", ["tied", "untimed_actor", "untimed_remark"])
def test_unorderable_transition_cannot_bind_a_later_destination(kind):
    source = Source()
    source.returned = True
    other = dict(source.records[0])
    if kind == "untimed_remark":
        other["remark"] = "Different request"
    else:
        other["userid"] = "different"
    if kind != "tied":
        other.pop("date")
    source.records.append(other)
    source.destination["create_time"] = "2026-10-13 18:06:50"
    assert OaRevertHandler(source).reconcile(action(), action_key="key",
        candidate=attempt("uncertain", acceptance())).status == "uncertain"
