"""A project identifier cannot become an OA binding through a provider mismatch."""

import pytest

from app.agent_orchestrator import AgentOrchestrator
from app.reviewed_sources import ReviewedSourceReadError, capture_candidate_sources
from app.system_action_handlers import OaCommentHandler
from app.system_executor import SystemExecutor
from tests.test_material_request_stage import MaterialSource, material_request
from tests.test_reviewed_orchestration import Audit, Consumer, setup


INVALID_DETAILS = [
    pytest.param({}, id="no-detail"),
    pytest.param({"success": True, "result": {}}, id="empty-detail"),
    pytest.param({"success": True, "result": {
        "processInstanceId": "actual-oa-process", "formValueVOS": [],
    }}, id="canonical-process-mismatch"),
]


@pytest.mark.parametrize("detail", INVALID_DETAILS)
def test_project_id_without_matching_native_oa_detail_cannot_be_bound(tmp_path, detail):
    store, task, context = setup(tmp_path)
    source = MaterialSource()
    original = material_request(context, source)
    action = original.proposal.actions[0].model_copy(update={
        "target": {"process_instance_id": "project-P108"},
    })
    unbound = original.model_copy(update={
        "source_bindings": (),
        "proposal": original.proposal.model_copy(update={"actions": (action,)}),
    })

    class InvalidSource:
        def read_oa_approval_detail(self, process):
            assert process == "project-P108"
            return detail

    with pytest.raises(ReviewedSourceReadError) as raised:
        capture_candidate_sources(unbound, context, InvalidSource())
    assert raised.value.error.code == "provider_read_failed"
    assert not raised.value.error.authorization_required
    assert unbound.source_bindings == ()
    assert store.current_reviewed_candidate(task.id, task.execution_generation) is None


@pytest.mark.parametrize("detail", INVALID_DETAILS)
def test_approved_source_becoming_missing_or_another_process_never_dispatches(tmp_path, detail):
    store, task, context = setup(tmp_path)
    source = MaterialSource()
    prepared = material_request(context, source)
    assert AgentOrchestrator(
        store=store, consumer=Consumer(store, prepared), audit=Audit(store, "approve"),
    ).process(task, context, refresh_context=lambda: context).status == "failed_terminal"
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    assert reviewed is not None
    source.read_oa_approval_detail = lambda process: detail
    result = SystemExecutor(
        store, {("dingtalk-oa", "comment"): OaCommentHandler(source)}, dws=source,
    ).execute(task, reviewed["id"], reviewed["review_id"], context=context)
    assert result.outcome == "failed"
    assert result.error.code == "reviewed_source_unavailable"
    assert not result.error.authorization_required
    assert source.comments == []
    assert store.list_verified_candidate_actions(task.id) == []
    execution = store.get_candidate_execution(reviewed["id"])
    assert store.list_candidate_action_attempts(execution["id"]) == []
