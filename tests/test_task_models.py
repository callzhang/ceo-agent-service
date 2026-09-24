import pytest
from pydantic import ValidationError

from app.task_models import TaskAgentDecision, WorkItem


def _decision(**changes):
    return {
        "action": "record_candidate",
        "transition": "none",
        "source_excerpt": "美国报价可以研究一下",
        "source_ref": "message:42",
        "title": "研究美国报价",
        "missing_evidence": ["deliverable", "authority"],
        **changes,
    }


def test_work_item_does_not_require_project_name():
    item = WorkItem.model_validate({
        "source": {"type": "reply_attempt", "ref": "42"},
        "summary": "美国报价可以研究一下",
        "context": {"source_conversation_kind": "group"},
    })
    assert "project_name" not in item.model_dump()


def test_vague_source_is_candidate_with_missing_evidence():
    decision = TaskAgentDecision.model_validate({"task_decisions": [_decision()]})
    assert decision.task_decisions[0].action == "record_candidate"
    assert decision.task_decisions[0].formal_basis is None


def test_one_task_agent_contract_supports_task_and_completion_transitions():
    decision = TaskAgentDecision.model_validate(
        {
            "task_decisions": [_decision()],
            "todo_changes": [
                {
                    "action": "close",
                    "todo_id": 17,
                    "completion_evidence": {
                        "source": "message:42",
                        "reason": "Owner confirmed delivery",
                        "description": "The requested quote was submitted.",
                        "completed_at": "2026-09-23T10:00:00+08:00",
                        "checked_at": "2026-09-23T10:05:00+08:00",
                    },
                }
            ],
            "search_trace": [
                {
                    "source_kind": "dingtalk_message",
                    "result": "Owner confirmed delivery",
                    "source_ref": "message:42",
                    "reason": "Direct completion confirmation",
                    "source_created_at": "2026-09-23T10:00:00+08:00",
                    "retrieved_at": "2026-09-23T10:05:00+08:00",
                    "audit_call_ids": ["call-17"],
                }
            ],
            "update_summary": "Recorded a candidate and confirmed an existing TODO.",
        }
    )

    assert decision.task_decisions[0].action == "record_candidate"
    assert decision.todo_changes[0].todo_id == 17
    assert decision.search_trace[0].audit_call_ids == ["call-17"]


def test_completion_operation_targets_one_business_task_without_legacy_todo_id():
    decision = TaskAgentDecision.model_validate({
        "todo_changes": [{
            "action": "close", "business_task_id": 42,
            "completion_evidence": {"source": "message:42", "reason": "done"},
        }],
    })
    assert decision.todo_changes[0].business_task_id == 42
    assert decision.todo_changes[0].todo_id is None


def test_one_source_can_emit_several_source_grounded_tasks():
    result = TaskAgentDecision.model_validate({"task_decisions": [
        _decision(source_excerpt="请王明提交报价", title="提交报价",
                  action="create_task", formal_basis="explicit_assignment",
                  owner_user_id="wang", owner_name="王明",
                  owner_evidence={"source_ref": "message:42", "excerpt": "请王明提交报价"},
                  missing_evidence=[],
                  date_evidence=[{"kind": "requested_deadline_at", "value": "2026-09-25T18:00:00+08:00",
                                  "source_ref": "message:42", "source_excerpt": "周五前", "actor_name": "指派人"}]),
        _decision(source_excerpt="安排客户演示", title="安排演示"),
    ]})
    assert len(result.task_decisions) == 2
    assert result.task_decisions[0].formal_basis.value == "explicit_assignment"
    assert result.task_decisions[0].date_evidence[0].kind == "requested_deadline_at"
    assert "project" not in type(result.task_decisions[0]).model_fields


def test_acceptance_has_dedicated_transition_and_existing_task_id():
    accepted = TaskAgentDecision.model_validate({"task_decisions": [
        _decision(action="update_task", transition="apply_acceptance", task_id=12,
                  source_excerpt="我接受报价任务，周五交第一版", missing_evidence=[],
                  acceptance_polarity="accepted", acceptance_target_signal_id=55)
    ]}).task_decisions[0]
    assert accepted.task_id == 12
    assert accepted.transition == "apply_acceptance"


@pytest.mark.parametrize("polarity", ["declined", "ambiguous", None])
def test_nonaccepted_polarity_cannot_transition_commitment(polarity):
    with pytest.raises(ValidationError, match="apply_acceptance requires explicit accepted polarity"):
        TaskAgentDecision.model_validate({"task_decisions": [
            _decision(action="update_task", transition="apply_acceptance", task_id=12,
                      source_excerpt="我不确定", missing_evidence=[],
                      acceptance_polarity=polarity)
        ]})


@pytest.mark.parametrize("payload", [
    {"action": "create_project"},
    {"action": "create_task", "commitment_status": "accepted"},
    {"action": "create_task", "deadline_at": "2026-09-25"},
    {"action": "create_task", "project": {"title": "美国客户"}},
    {"action": "create_task", "project_name": "美国客户"},
])
def test_old_project_and_model_authored_commitment_fields_are_rejected(payload):
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate({"task_decisions": [_decision(**payload)]})


def test_merge_proposal_requires_ids_and_structured_identity_evidence():
    valid = _decision(action="update_task", transition="merge_identity", task_id=8,
                      target_task_id=9,
                      identity_proposal={"source_task_id": 8, "target_task_id": 9,
                                         "identity_evidence": {
                                             "basis": "same_external_task_id",
                                             "source_signal_id": 21,
                                             "target_signal_id": 22,
                                         }})
    TaskAgentDecision.model_validate({"task_decisions": [valid]})
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate({"task_decisions": [
            _decision(action="update_task", transition="merge_identity", task_id=8,
                      identity_proposal={"reason": "sounds similar"})
        ]})


def test_project_candidate_requires_existing_cluster_id_and_cannot_create_project():
    TaskAgentDecision.model_validate({"task_decisions": [
        _decision(project_candidate_proposal={"cluster_id": 3, "title": "美国客户成交", "reason": "相关任务持续"})
    ]})
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate({"task_decisions": [
            _decision(project_candidate_proposal={"title": "美国客户成交", "reason": "相关任务持续"})
        ]})


def test_attention_proposal_requires_material_trigger_and_confirmed_anchor():
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate({"task_decisions": [
            _decision(action="create_task", formal_basis="external_todo", missing_evidence=[],
                      attention_proposal={"category": "fyi", "title": "买办公用品", "why_attention": "有日期"})
        ]})
    TaskAgentDecision.model_validate({"task_decisions": [
        _decision(action="create_task", formal_basis="external_todo", missing_evidence=[])
    ]})


def test_required_and_optional_null_contract():
    model = TaskAgentDecision.model_validate({"task_decisions": [
        _decision(owner_evidence=None, date_evidence=None)
    ]})
    assert model.task_decisions[0].owner_evidence == {}
    assert model.task_decisions[0].date_evidence == []
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate({"task_decisions": [_decision(action=None)]})


@pytest.mark.parametrize("change", [
    {"source_excerpt": ""},
    {"source_ref": ""},
    {"action": "create_task", "formal_basis": None},
    {"action": "record_candidate", "formal_basis": "explicit_assignment"},
    {"action": "create_task", "formal_basis": "explicit_assignment", "owner_name": "王明",
     "owner_evidence": {}},
])
def test_source_grounding_and_formality_shape(change):
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate({"task_decisions": [_decision(**change)]})


def test_typed_date_requires_provenance_and_merge_target_must_match():
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate({"task_decisions": [
            _decision(date_evidence=[{"kind": "estimated_deadline_at", "value": "2026-09-30"}])
        ]})
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate({"task_decisions": [
            _decision(action="update_task", transition="merge_identity", task_id=8,
                      target_task_id=10,
                      identity_proposal={"source_task_id": 8, "target_task_id": 9,
                                         "identity_evidence": {
                                             "basis": "same_external_task_id",
                                             "source_signal_id": 21,
                                             "target_signal_id": 22,
                                         }})
        ]})


@pytest.mark.parametrize("identity_evidence", [
    {"same_external_task_id": True},
    {"same_deliverable": True, "same_owner": True, "same_context": True,
     "compatible_time_window": True},
    {"basis": "same_external_task_id", "source_signal_id": 21},
    {"basis": "same_external_task_id", "source_signal_id": 0, "target_signal_id": 22},
])
def test_merge_identity_cannot_use_unverifiable_agent_assertions(identity_evidence):
    with pytest.raises(ValidationError):
        TaskAgentDecision.model_validate({"task_decisions": [
            _decision(action="update_task", transition="merge_identity", task_id=8,
                      target_task_id=9, identity_proposal={
                          "source_task_id": 8, "target_task_id": 9,
                          "identity_evidence": identity_evidence,
                      })
        ]})


@pytest.mark.parametrize("actor", [{}, {"actor_name": "  ", "actor_user_id": ""}])
def test_committed_deadline_requires_nonblank_actor_provenance(actor):
    with pytest.raises(ValidationError, match="committed deadline"):
        TaskAgentDecision.model_validate({"task_decisions": [
            _decision(date_evidence=[{
                "kind": "committed_deadline_at", "value": "2026-09-30T18:00:00+08:00",
                "source_ref": "message:42", "source_excerpt": "我周三交付",
                **actor,
            }])
        ]})
