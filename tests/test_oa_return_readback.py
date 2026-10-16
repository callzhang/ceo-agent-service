from hashlib import sha256
from datetime import datetime, timedelta, timezone
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
        return {"success": True, "dingOpenErrcode": 0, "result": True}

    def read_oa_process_instance_openapi(self, process_id):
        assert process_id == "process"
        return {"errcode": 0, "process_instance": {"business_id": self.business_id,
            "tasks": [{"taskid": "original", "task_status": "CANCELED"}, self.destination],
            "operation_records": self.records}}

    def read_oa_approval_records(self, process_id):
        assert process_id == "process"
        native_records = []
        for record in self.records:
            event_time = OaRevertHandler._event_time(record.get("date"))
            native = {"operationType": record.get("operation_type"),
                "userId": record.get("userid")}
            if event_time is not None:
                native["operationTime"] = round(event_time.timestamp() * 1000)
            native_records.append(native)
        return {"success": True, "result": {"operationRecords": native_records}}


def attempt(status="dispatched", result=None):
    return {"action_attempt": {"status": status, "external_action_key": "key",
        "created_at": "2026-10-13T10:06:25.641Z", "result_json": json.dumps(result or {})}}


def acceptance():
    return {"provider_response": {"success": True, "dingOpenErrcode": 0, "result": True},
        "accepted_at": "2026-10-13T10:06:29.001Z", "dispatch_started_at": "2026-10-13T10:06:25.641Z",
        "dispatch_principal_user_id": "principal",
        "accepted_action_digest": sha256(action().model_dump_json().encode()).hexdigest()}


def test_return_acceptance_survives_store_reopen_and_changed_source(tmp_path, monkeypatch):
    from app.dws_client import DINGTALK_MESSAGE_TIME_ZONE
    from app.reviewed_sources import capture_candidate_sources
    from app.store import AgentRole, AutoReplyStore
    from app.system_executor import SystemExecutor
    from tests.test_reviewed_orchestration import setup
    from tests.test_system_executor import _candidate

    class Clock(datetime):
        offset = timedelta()

        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + cls.offset

    monkeypatch.setattr("app.system_action_handlers.datetime", Clock)

    class ReturningSource(Source):
        def read_oa_approval_detail(self, process_id):
            detail = super().read_oa_approval_detail(process_id)
            detail["result"]["formValueVOS"] = [{"id": "amount", "value": "900" if self.returned else "100"}]
            return detail

        def revert_oa_approval_task(self, **kwargs):
            self.calls.append(kwargs)
            self.returned = True
            event = datetime.now(timezone.utc) + timedelta(seconds=2)
            self.records[0]["date"] = event.astimezone(DINGTALK_MESSAGE_TIME_ZONE).strftime("%Y-%m-%d %H:%M:%S")
            self.destination["create_time"] = (event + timedelta(seconds=1)).astimezone(
                DINGTALK_MESSAGE_TIME_ZONE).strftime("%Y-%m-%d %H:%M:%S")
            Clock.offset = timedelta(seconds=4)
            return {"success": True, "dingOpenErrcode": 0, "result": True}

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
    assert first.error.code == "external_action_uncertain"
    execution = store.get_candidate_execution(selected["id"])
    [original] = store.list_candidate_action_attempts(execution["id"])
    assert json.loads(original["result_json"])["provider_response"]["result"] is True

    reopened = AutoReplyStore(store.path)
    recovered = SystemExecutor(reopened, dws=source).execute(task, selected["id"], review["id"], context=context)
    [after] = reopened.list_candidate_action_attempts(execution["id"])
    assert recovered.outcome == "executed"
    assert after["status"] == "verified"
    assert after["created_at"] == original["created_at"]
    assert after["external_action_key"] == original["external_action_key"]
    assert len(source.calls) == 1
    assert reopened.get_candidate_external_action(original["external_action_key"]) is not None


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


def test_dingtalk_success_envelope_and_millisecond_dates_verify_without_replay(provider_read_clock):
    source = Source()
    source.returned = True
    source.records[0]["date"] = 1791939988000  # 2026-10-13 18:06:28 America/Los_Angeles
    source.destination["create_time"] = 1791939988000
    proof = acceptance()
    proof["provider_response"] = {"dingOpenErrcode": 0, "result": True, "success": True}
    proof["dispatch_started_at"] = "2026-10-14T01:06:25.641Z"
    proof["accepted_at"] = "2026-10-14T01:06:29.001Z"
    provider_read_clock.current = datetime.fromisoformat("2026-10-14T01:07:00+00:00")
    candidate = attempt("uncertain", proof)
    candidate["action_attempt"]["created_at"] = "2026-10-14T01:06:25.641Z"
    outcome = OaRevertHandler(source).reconcile(action(), action_key="key",
        candidate=candidate)
    assert outcome.status == "verified"
    assert len(source.calls) == 0


def test_prior_success_response_uses_persisted_response_received_at_when_accepted_at_is_absent(provider_read_clock):
    source = Source()
    source.returned = True
    source.records[0]["date"] = 1791939988000
    source.destination["create_time"] = 1791939988000
    proof = acceptance()
    proof.pop("accepted_at")
    proof["provider_response"] = {"dingOpenErrcode": 0, "result": True, "success": True}
    proof["dispatch_started_at"] = "2026-10-14T01:06:25.641Z"
    proof["response_received_at"] = "2026-10-14T01:06:29.001Z"
    provider_read_clock.current = datetime.fromisoformat("2026-10-14T01:07:00+00:00")
    candidate = attempt("uncertain", proof)
    candidate["action_attempt"]["created_at"] = "2026-10-14T01:06:25.641Z"
    outcome = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=candidate)
    assert outcome.status == "verified"
    assert len(source.calls) == 0


@pytest.mark.parametrize("response", [
    {"success": True, "result": True, "errorCode": 0},
    {"success": True, "result": True, "dingOpenErrcode": 1},
    {"success": False, "result": True, "dingOpenErrcode": 0},
])
def test_only_dingtalk_success_envelope_is_accepted(response):
    assert OaRevertHandler._accepted(response) is False


def test_millisecond_event_time_is_utc_and_boolean_is_not_a_timestamp():
    instant = OaRevertHandler._event_time(1791939988000)
    assert instant == datetime.fromtimestamp(1791939988, timezone.utc)
    assert OaRevertHandler._event_time(True) is None


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


def test_millisecond_dws_record_resolves_openapi_second_truncation_without_weakening_boundary():
    source = Source()
    source.returned = True
    source.records[0]["date"] = "2026-10-13 18:06:25"
    source.destination["create_time"] = "2026-10-13 18:06:27"
    source.read_oa_approval_records = lambda process_id: {
        "success": True,
        "result": {"operationRecords": [{
            "operationType": "REDIRECT_PROCESS",
            "userId": "principal",
            "operationTime": 1791885985900,
        }]},
    }
    proof = acceptance()
    proof["dispatch_started_at"] = "2026-10-13T10:06:25.641Z"
    proof["accepted_at"] = "2026-10-13T10:06:26.200Z"
    proof["response_received_at"] = proof["accepted_at"]
    provider_read_clock.current = datetime.fromisoformat("2026-10-13T10:07:00+00:00")
    candidate = attempt("uncertain", proof)
    candidate["action_attempt"]["created_at"] = "2026-10-13T10:06:25.641Z"

    outcome = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=candidate)

    assert outcome.status == "verified"
    assert len(source.calls) == 0


def test_ambiguous_millisecond_records_in_same_openapi_second_remain_uncertain():
    source = Source()
    source.returned = True
    source.records[0]["date"] = "2026-10-13 18:06:25"
    source.destination["create_time"] = "2026-10-13 18:06:27"
    source.read_oa_approval_records = lambda process_id: {
        "success": True,
        "result": {"operationRecords": [
            {"operationType": "REDIRECT_PROCESS", "userId": "principal",
             "operationTime": 1791885985900},
            {"operationType": "REDIRECT_PROCESS", "userId": "principal",
             "operationTime": 1791885985990},
        ]},
    }
    proof = acceptance()
    proof["dispatch_started_at"] = "2026-10-13T10:06:25.641Z"
    proof["accepted_at"] = "2026-10-13T10:06:26.200Z"
    proof["response_received_at"] = proof["accepted_at"]
    provider_read_clock.current = datetime.fromisoformat("2026-10-13T10:07:00+00:00")
    candidate = attempt("uncertain", proof)
    candidate["action_attempt"]["created_at"] = "2026-10-13T10:06:25.641Z"

    outcome = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=candidate)

    assert outcome.status == "uncertain"
    assert source.calls == []


def test_second_truncated_destination_created_in_redirect_second_is_verified(provider_read_clock):
    source = Source()
    source.returned = True
    source.records[0]["date"] = "2026-10-15 19:21:33"
    source.destination["create_time"] = "2026-10-15 19:21:33"
    source.read_oa_approval_records = lambda process_id: {
        "success": True,
        "result": {"operationRecords": [{
            "operationType": "REDIRECT_PROCESS",
            "userId": "principal",
            "operationTime": 1792063293204,
        }]},
    }
    proof = acceptance()
    proof["dispatch_started_at"] = "2026-10-15T11:21:32.307564Z"
    proof["accepted_at"] = "2026-10-15T11:21:33.480916Z"
    proof["response_received_at"] = proof["accepted_at"]
    provider_read_clock.current = datetime.fromisoformat("2026-10-15T11:22:00+00:00")
    candidate = attempt("uncertain", proof)
    candidate["action_attempt"]["created_at"] = proof["dispatch_started_at"]

    outcome = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=candidate)

    assert outcome.status == "verified"
    assert outcome.provider_result["destination_task_ids"] == ["destination"]
    assert source.calls == []


def test_second_truncated_destination_stays_uncertain_with_same_second_follow_up_event(provider_read_clock):
    source = Source()
    source.returned = True
    source.records = [
        {"operation_type": "REDIRECT_PROCESS", "userid": "principal",
         "date": "2026-10-15 19:21:33", "remark": "Need exact handover facts"},
        {"operation_type": "EXECUTE_TASK_NORMAL", "userid": "handover-owner",
         "date": "2026-10-15 19:21:33", "remark": ""},
    ]
    source.destination["create_time"] = "2026-10-15 19:21:33"
    source.read_oa_approval_records = lambda process_id: {
        "success": True,
        "result": {"operationRecords": [
            {"operationType": "REDIRECT_PROCESS", "userId": "principal",
             "operationTime": 1792063293204},
            {"operationType": "EXECUTE_TASK_NORMAL", "userId": "handover-owner",
             "operationTime": 1792063293800},
        ]},
    }
    proof = acceptance()
    proof["dispatch_started_at"] = "2026-10-15T11:21:32.307564Z"
    proof["accepted_at"] = "2026-10-15T11:21:33.480916Z"
    proof["response_received_at"] = proof["accepted_at"]
    provider_read_clock.current = datetime.fromisoformat("2026-10-15T11:22:00+00:00")
    candidate = attempt("uncertain", proof)
    candidate["action_attempt"]["created_at"] = proof["dispatch_started_at"]

    outcome = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=candidate)

    assert outcome.status == "uncertain"
    assert source.calls == []


def test_second_truncated_destination_stays_uncertain_when_only_precise_read_has_follow_up(provider_read_clock):
    source = Source()
    source.returned = True
    source.records = [
        {"operation_type": "REDIRECT_PROCESS", "userid": "principal",
         "date": "2026-10-15 19:21:33", "remark": "Need exact handover facts"},
    ]
    source.destination["create_time"] = "2026-10-15 19:21:33"
    source.read_oa_approval_records = lambda process_id: {
        "success": True,
        "result": {"operationRecords": [
            {"operationType": "REDIRECT_PROCESS", "userId": "principal",
             "operationTime": 1792063293204},
            {"operationType": "EXECUTE_TASK_NORMAL", "userId": "handover-owner",
             "operationTime": 1792063293800},
        ]},
    }
    proof = acceptance()
    proof["dispatch_started_at"] = "2026-10-15T11:21:32.307564Z"
    proof["accepted_at"] = "2026-10-15T11:21:33.480916Z"
    proof["response_received_at"] = proof["accepted_at"]
    provider_read_clock.current = datetime.fromisoformat("2026-10-15T11:22:00+00:00")
    candidate = attempt("uncertain", proof)
    candidate["action_attempt"]["created_at"] = proof["dispatch_started_at"]

    outcome = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=candidate)

    assert outcome.status == "uncertain"
    assert source.calls == []


def test_second_truncated_destination_stays_uncertain_with_same_second_preceding_event(provider_read_clock):
    source = Source()
    source.returned = True
    source.records = [
        {"operation_type": "REDIRECT_PROCESS", "userid": "principal",
         "date": "2026-10-15 19:21:33", "remark": "Need exact handover facts"},
    ]
    source.destination["create_time"] = "2026-10-15 19:21:33"
    source.read_oa_approval_records = lambda process_id: {
        "success": True,
        "result": {"operationRecords": [
            {"operationType": "EXECUTE_TASK_NORMAL", "userId": "prior-owner",
             "operationTime": 1792063293100},
            {"operationType": "REDIRECT_PROCESS", "userId": "principal",
             "operationTime": 1792063293204},
        ]},
    }
    proof = acceptance()
    proof["dispatch_started_at"] = "2026-10-15T11:21:32.307564Z"
    proof["accepted_at"] = "2026-10-15T11:21:33.480916Z"
    proof["response_received_at"] = proof["accepted_at"]
    provider_read_clock.current = datetime.fromisoformat("2026-10-15T11:22:00+00:00")
    candidate = attempt("uncertain", proof)
    candidate["action_attempt"]["created_at"] = proof["dispatch_started_at"]

    outcome = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=candidate)

    assert outcome.status == "uncertain"
    assert source.calls == []


def test_precise_destination_timestamp_uses_exact_interval_with_same_second_follow_up(provider_read_clock):
    source = Source()
    source.returned = True
    source.records = [
        {"operation_type": "REDIRECT_PROCESS", "userid": "principal",
         "date": "2026-10-15 19:21:33", "remark": "Need exact handover facts"},
        {"operation_type": "EXECUTE_TASK_NORMAL", "userid": "handover-owner",
         "date": "2026-10-15 19:21:33", "remark": ""},
    ]
    source.destination["create_time"] = 1792063293600
    source.read_oa_approval_records = lambda process_id: {
        "success": True,
        "result": {"operationRecords": [
            {"operationType": "REDIRECT_PROCESS", "userId": "principal",
             "operationTime": 1792063293204},
            {"operationType": "EXECUTE_TASK_NORMAL", "userId": "handover-owner",
             "operationTime": 1792063293800},
        ]},
    }
    proof = acceptance()
    proof["dispatch_started_at"] = "2026-10-15T11:21:32.307564Z"
    proof["accepted_at"] = "2026-10-15T11:21:33.480916Z"
    proof["response_received_at"] = proof["accepted_at"]
    provider_read_clock.current = datetime.fromisoformat("2026-10-15T11:22:00+00:00")
    candidate = attempt("uncertain", proof)
    candidate["action_attempt"]["created_at"] = proof["dispatch_started_at"]

    outcome = OaRevertHandler(source).reconcile(action(), action_key="key", candidate=candidate)

    assert outcome.status == "verified"
    assert outcome.provider_result["destination_task_ids"] == ["destination"]
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
