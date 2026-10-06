"""Selected reviewed choices retain identity when source facts change."""
from app.agent_orchestrator import AgentOrchestrator
from app.reviewed_sources import capture_candidate_sources
from app.store import AutoReplyStore
from app.system_executor import ActionOutcome, SystemExecutor
from tests.test_reviewed_orchestration import Audit, Consumer, candidate, setup


class Source:
    amount = "100"

    def read_oa_approval_detail(self, process):
        assert process == "process"
        return {"success": True, "result": {
            "processInstanceId": process,
            "formValueVOS": [{"id": "amount", "value": self.amount}],
        }}


class Handler:
    def __init__(self):
        self.calls = []

    def dispatch(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return ActionOutcome("verified", {"provider_result": {"taskId": "task", "result": "agree"}})


def question(context, source):
    result = candidate(human=True)
    option = result.decision_options[0]
    action = option.plan.actions[0].model_copy(update={
        "capability": "dingtalk-oa", "operation": "approve",
        "target": {"process_instance_id": "process", "task_id": "task"},
        "payload": {"remark": "Approved"},
    })
    option = option.model_copy(update={"plan": option.plan.model_copy(update={"actions": (action,)})})
    result = result.model_copy(update={"decision_options": (option, result.decision_options[1])})
    return capture_candidate_sources(result, context, source)


def test_selected_question_with_changed_native_form_reopens_same_stage_and_keeps_answer(tmp_path):
    store, task, context = setup(tmp_path)
    source = Source()
    original = question(context, source)
    first = AgentOrchestrator(store=store, consumer=Consumer(store, original), audit=Audit(store, "approve"))
    assert first.process(task, context, refresh_context=lambda: context).status == "needs_human"
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    original_json = reviewed["candidate_json"]
    selection = store.select_candidate_option(reviewed["id"], reviewed["review_id"], "send")
    with store._connect() as db:
        db.execute("update reply_tasks set status='needs_human' where id=?", (task.id,))
    store.wake_selected_candidate_execution(reviewed["id"], reviewed["review_id"])
    source.amount = "900"
    reopened = AutoReplyStore(store.path)
    handler = Handler()
    consumer = Consumer(reopened, lambda fresh: question(fresh, source))
    audit = Audit(reopened, "approve")
    result = AgentOrchestrator(
        store=reopened, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(reopened, {("dingtalk-oa", "approve"): handler}, dws=source),
    ).process(task, context, refresh_context=lambda: context)
    assert result.status == "needs_human"
    assert handler.calls == []
    fresh = reopened.current_reviewed_candidate(task.id, task.execution_generation)
    assert fresh["id"] != reviewed["id"] and fresh["proposal_revision"] == 1
    assert fresh["stage_index"] == 0 and fresh["selection_id"] is None
    captured = consumer.calls[0][0]
    assert captured.prior_human_decisions
    assert captured.business_state_changes
    assert audit.calls[0].candidate.stage_index == 0
    with reopened._connect() as db:
        old = db.execute("select candidate_json,invalidated_at from review_candidates where id=?", (reviewed["id"],)).fetchone()
        assert old["candidate_json"] == original_json and old["invalidated_at"]
        saved = db.execute("select * from candidate_selections where id=?", (selection["id"],)).fetchone()
        assert saved["option_key"] == "send" and saved["candidate_id"] == reviewed["id"]


def test_selected_question_after_restart_executes_exact_branch_without_new_agent_turn(tmp_path):
    store, task, context = setup(tmp_path)
    source = Source()
    original = question(context, source)
    assert AgentOrchestrator(
        store=store, consumer=Consumer(store, original), audit=Audit(store, "approve"),
    ).process(task, context, refresh_context=lambda: context).status == "needs_human"
    reviewed = store.current_reviewed_candidate(task.id, task.execution_generation)
    with store._connect() as db:
        db.execute("update reply_tasks set status='needs_human' where id=?", (task.id,))
    store.select_candidate_option(reviewed["id"], reviewed["review_id"], "send")
    store.wake_selected_candidate_execution(reviewed["id"], reviewed["review_id"])
    reopened = AutoReplyStore(store.path)
    handler = Handler()
    consumer, audit = Consumer(reopened), Audit(reopened)
    orchestrator = AgentOrchestrator(
        store=reopened, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(reopened, {("dingtalk-oa", "approve"): handler}, dws=source),
    )
    result = orchestrator.process(task, context, refresh_context=lambda: context)
    assert result.status == "executed" and len(handler.calls) == 1
    assert handler.calls[0][0].model_dump() == original.decision_options[0].plan.actions[0].model_dump()
    assert consumer.calls == [] and audit.calls == []
    again = orchestrator.process(task, context, refresh_context=lambda: context)
    assert again.status == "executed" and len(handler.calls) == 1


def test_context_refresh_preserves_native_dependency_error_without_business_question(tmp_path):
    from app.dws_client import DwsError
    store, task, context = setup(tmp_path)
    consumer, audit = Consumer(store), Audit(store)
    def unavailable():
        raise DwsError("This private group cannot be read", code="1001", server_key="dingtalk-chat", retryable_external_dependency=False)
    result = AgentOrchestrator(store=store, consumer=consumer, audit=audit).process(
        task, context, refresh_context=unavailable,
    )
    assert result.status == "failed_terminal"
    assert result.error.source_code == "1001" and result.error.source == "dingtalk-chat"
    assert result.error.retryable is False and result.error.authorization_required is False
    assert "private group" in result.summary
    assert consumer.calls == [] and audit.calls == []
