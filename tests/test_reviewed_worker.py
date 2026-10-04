"""Approved questions remain pending human input, choices execute without models."""
import json
from types import SimpleNamespace

from app.agent_orchestrator import AgentOrchestrator
from app.store import AutoReplyStore
from app.system_action_handlers import OaDecisionHandler
from app.system_executor import SystemExecutor
from app.worker import DingTalkAutoReplyWorker
from tests.test_reviewed_orchestration import Audit, Consumer, candidate, setup
from tests.test_reviewed_source_revisions import Source, question


class NoAgent:
    def run(self, *args, **kwargs):
        raise AssertionError('An approved selected branch must not run an Agent')


def test_worker_question_status_and_selected_stop_are_distinct(tmp_path, monkeypatch):
    store,task,context=setup(tmp_path)
    result=AgentOrchestrator(store=store,consumer=Consumer(store,candidate(human=True)),audit=Audit(store,'approve')).process(task,context,refresh_context=lambda:context)
    worker=DingTalkAutoReplyWorker(store=store,dws=SimpleNamespace(dws_bin='dws'),codex=SimpleNamespace(workspace=tmp_path))
    notifications=[]
    monkeypatch.setattr(worker,'_notify_problem_attempt',lambda *a,**kw:notifications.append(kw['send_status']))
    monkeypatch.setattr(worker,'_dismiss_problem_notification',lambda *a:None)
    assert worker._apply_orchestration_result(task,result) is False
    assert store.get_reply_task(task.id).status=='needs_human'
    assert notifications==['needs_human']
    store.select_candidate_option(result.candidate_id,result.review_id,'stop')
    store.wake_selected_candidate_execution(result.candidate_id,result.review_id)
    selected=store.claim_reply_tasks(1)[0]
    assert selected.execution_generation==task.execution_generation
    resumed=AgentOrchestrator(store=store,consumer=NoAgent(),audit=NoAgent(),system_executor=SystemExecutor(store=store,handlers={})).process(selected,context,refresh_context=lambda:context)
    assert resumed.status=='skipped'
    worker._apply_orchestration_result(selected,resumed)
    assert store.get_reply_task(task.id).status=='skipped'
    assert store.get_candidate_execution(result.candidate_id)['status']=='skipped'


def test_worker_selected_oa_option_executes_after_restart_and_finalizes_verified_receipt(
    tmp_path, monkeypatch,
):
    class OaProvider(Source):
        dws_bin = "dws"

        def __init__(self):
            self.approved = False
            self.dispatches = 0

        def read_oa_approval_detail(self, process):
            detail = super().read_oa_approval_detail(process)
            detail["result"]["tasks"] = [{
                "taskId": "task", "userid": "derek",
                "taskStatus": "COMPLETED" if self.approved else "RUNNING",
                "taskResult": "AGREE" if self.approved else "",
            }]
            return detail

        def get_current_user_id(self):
            return "derek"

        def execute_oa_approval_action(self, process, task_id, action, remark):
            assert (process, task_id, action, remark) == (
                "process", "task", "通过", "Approved",
            )
            self.dispatches += 1
            self.approved = True
            return {"success": True}

    store, task, context = setup(tmp_path)
    provider = OaProvider()
    original = question(context, provider)
    reviewed = AgentOrchestrator(
        store=store, consumer=Consumer(store, original), audit=Audit(store, "approve"),
    ).process(task, context, refresh_context=lambda: context)
    assert reviewed.status == "needs_human"
    worker = DingTalkAutoReplyWorker(
        store=store, dws=provider, codex=SimpleNamespace(workspace=tmp_path),
    )
    monkeypatch.setattr(worker, "_notify_problem_attempt", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "_dismiss_problem_notification", lambda *a: None)
    assert worker._apply_orchestration_result(task, reviewed) is False
    first_attempt = store.get_latest_reply_attempt_for_trigger(
        task.conversation_id, task.trigger_message_id,
    )
    assert first_attempt.send_status == "needs_human"
    assert store.get_reply_task(task.id).status == "needs_human"

    selected = store.select_candidate_option(reviewed.candidate_id, reviewed.review_id, "send")
    store.wake_selected_candidate_execution(reviewed.candidate_id, reviewed.review_id)
    reopened = AutoReplyStore(store.path)
    resumed_task = reopened.claim_reply_tasks(1)[0]
    assert resumed_task.execution_generation == task.execution_generation
    executor = SystemExecutor(
        reopened, {("dingtalk-oa", "approve"): OaDecisionHandler(provider)},
        dws=provider,
    )
    resumed = AgentOrchestrator(
        store=reopened, consumer=NoAgent(), audit=NoAgent(), system_executor=executor,
    ).process(resumed_task, context, refresh_context=lambda: context)
    assert resumed.status == "executed"
    assert resumed.execution_result.outcome == "executed"
    assert resumed.execution_result.completed_action_keys == ("notice",)
    assert provider.dispatches == 1 and provider.amount == "100"

    verified = reopened.list_verified_candidate_actions(task.id)
    assert len(verified) == 1
    receipt = verified[0]
    assert receipt["action_identity"] == "notice"
    assert json.loads(receipt["target_identifiers_json"]) == {
        "process_instance_id": "process", "task_id": "task",
    }
    assert json.loads(receipt["provider_result_json"])["readback"]["taskResult"] == "AGREE"
    assert reopened.get_candidate_external_action(receipt["external_action_key"])

    resumed_worker = DingTalkAutoReplyWorker(
        store=reopened, dws=provider, codex=SimpleNamespace(workspace=tmp_path),
    )
    monkeypatch.setattr(resumed_worker, "_dismiss_problem_notification", lambda *a: None)
    assert resumed_worker._apply_orchestration_result(resumed_task, resumed) is True
    assert reopened.get_reply_task(task.id).status == "done"
    final_attempt = reopened.get_latest_reply_attempt_for_trigger(
        task.conversation_id, task.trigger_message_id,
    )
    assert final_attempt.send_status == "completed"
    assert final_attempt.oa_process_instance_id == "process"
    assert final_attempt.oa_task_id == "task"
    assert final_attempt.oa_action == "approve"
    assert final_attempt.oa_action_result_json == receipt["provider_result_json"]
    assert reopened.get_candidate_execution(reviewed.candidate_id)["status"] == "done"
    assert reopened.list_human_decision_evidence(task.id)[0]["source_record_id"] == selected["id"]
    assert reopened.list_human_decision_evidence(task.id)[0]["option_key"] == "send"
