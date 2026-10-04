"""Approved questions remain pending human input, choices execute without models."""
from types import SimpleNamespace

from app.agent_orchestrator import AgentOrchestrator
from app.system_executor import SystemExecutor
from app.worker import DingTalkAutoReplyWorker
from tests.test_reviewed_orchestration import Audit, Consumer, candidate, setup


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
