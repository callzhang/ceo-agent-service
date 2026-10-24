"""Adding an approver (加签) to the principal's own OA task.

Derek 2026-10-24: when an approval lacks a person the applicable Skill requires
(a new supervisor, a salesperson), the system adds them to the approval rather
than returning it. Only ``before`` and ``parallel`` are registered because both
create the appended person's running task at once, which the readback needs.
"""

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from app.agent_contracts import ProposedAction
from app.consumer_purpose import purpose_output_model
from app.dws_client import DwsClient
from app.system_action_handlers import OaAppendApproverHandler
from tests.test_system_executor import _candidate


class FakeDws:
    def __init__(self, *, source_owner="derek", source_status="RUNNING", appender_running=False,
                 appears=True, success=True):
        self.source_owner = source_owner
        self.source_status = source_status
        self.appender_running = appender_running
        self.appears = appears
        self.success = success
        self.appended = False
        self.calls = []

    def get_current_user_id(self):
        return "derek"

    def read_oa_approval_detail(self, process_id):
        tasks = [{"taskId": "task-1", "userid": self.source_owner, "taskStatus": self.source_status}]
        if self.appender_running or (self.appended and self.appears):
            tasks.append({"taskId": "task-2", "userid": "supervisor", "taskStatus": "RUNNING"})
        return {"result": {"processInstanceId": process_id, "tasks": tasks}}

    def append_oa_approval_task(self, **kwargs):
        self.calls.append(kwargs)
        self.appended = self.success
        return {"success": self.success}


def _action(kind="before", **target):
    return _candidate(actions=[{
        "description": "Add the new supervisor", "action_identity": "append-supervisor",
        "capability": "dingtalk-oa", "operation": "append_task",
        "target": {"process_instance_id": "p1", "task_id": "task-1",
                   "appender_user_id": "supervisor", **target},
        "payload": {"type": kind},
    }]).proposal.actions[0]


def test_append_is_verified_by_a_new_running_task_owned_by_the_appended_person():
    dws = FakeDws()
    outcome = OaAppendApproverHandler(dws).dispatch(_action(), action_key="key", candidate={})
    assert outcome.status == "verified"
    assert dws.calls == [{"process_instance_id": "p1", "task_id": "task-1",
                          "appender_user_id": "supervisor", "kind": "before"}]
    assert outcome.provider_result["new_tasks"][0]["taskId"] == "task-2"


def test_append_is_refused_when_the_person_already_has_a_running_task():
    dws = FakeDws(appender_running=True)
    outcome = OaAppendApproverHandler(dws).dispatch(_action(), action_key="key", candidate={})
    assert outcome.status == "business_state_changed"
    assert dws.calls == []


@pytest.mark.parametrize("fake", [
    FakeDws(source_owner="someone-else"),
    FakeDws(source_status="COMPLETED"),
])
def test_append_only_touches_the_principals_own_running_task(fake):
    outcome = OaAppendApproverHandler(fake).dispatch(_action(), action_key="key", candidate={})
    assert outcome.status == "business_state_changed"
    assert fake.calls == []


def test_a_task_that_does_not_appear_is_uncertain_and_reconciles_later():
    dws = FakeDws(appears=False)
    handler = OaAppendApproverHandler(dws)
    assert handler.dispatch(_action(), action_key="key", candidate={}).status == "uncertain"
    assert handler.reconcile(_action(), action_key="key", candidate={}).status == "confirmed_no_effect"
    dws.appears = True
    assert handler.reconcile(_action(), action_key="key", candidate={}).status == "verified"


def test_a_provider_failure_is_failed():
    dws = FakeDws(success=False)
    assert OaAppendApproverHandler(dws).dispatch(_action(), action_key="key", candidate={}).status == "failed"


@pytest.mark.parametrize("change", [
    {"target": {"process_instance_id": "p", "task_id": "t"}},
    {"target": {"process_instance_id": "p", "task_id": "t", "appender_user_id": " "}},
    {"payload": {}},
    {"payload": {"type": "after"}},
])
def test_append_task_contract_is_strict(change):
    value = {"description": "Add", "action_identity": "x", "capability": "dingtalk-oa",
             "operation": "append_task", "effect": "external",
             "target": {"process_instance_id": "p", "task_id": "t", "appender_user_id": "u"},
             "payload": {"type": "before"}}
    ProposedAction.model_validate(value)
    value.update(change)
    with pytest.raises(ValidationError):
        ProposedAction.model_validate(value)
    if "target" in change:
        assert list(Draft202012Validator(ProposedAction.model_json_schema()).iter_errors(value))


def test_oa_purpose_offers_add_approver_and_general_does_not():
    model = purpose_output_model("oa")
    output = model.model_validate({
        "action": "add_approver", "reason": "The new supervisor has not agreed",
        "target": {"process_instance_id": "p", "task_id": "t", "appender_user_id": "u"},
        "payload": {"type": "before"}, "evidence_refs": [],
    })
    action = output.to_proposal().actions[0]
    assert (action.capability, action.operation) == ("dingtalk-oa", "append_task")
    assert "add_approver" not in purpose_output_model("general").model_json_schema()["properties"]["action"]["enum"]
    with pytest.raises(ValidationError):
        model.model_validate({"action": "add_approver", "reason": "r", "evidence_refs": [],
                              "target": {"process_instance_id": "p", "task_id": "t", "appender_user_id": "u"},
                              "payload": {"type": "after"}})


def test_dws_command_adds_one_required_approver_in_order(monkeypatch):
    client = DwsClient()
    seen = []
    monkeypatch.setattr(client, "run_json", lambda command: seen.append(command) or {"success": True})
    client.append_oa_approval_task(process_instance_id="p", task_id="t", appender_user_id="u", kind="parallel")
    command = seen[0]
    assert command[1:4] == ["oa", "approval", "append-task"]
    assert command[command.index("--type") + 1] == "Parallel"
    assert command[command.index("--appender-user-ids") + 1] == "u"
    assert command[command.index("--activate-type") + 1] == "ONE_BY_ONE"
    assert command[command.index("--agree-all") + 1] == "true"
    assert "--yes" in command
    with pytest.raises(ValueError):
        client.append_oa_approval_task(process_instance_id="p", task_id="t", appender_user_id="u", kind="after")
