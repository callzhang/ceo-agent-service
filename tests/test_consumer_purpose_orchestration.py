from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest

from test_agent_orchestrator import (
    ScriptedConsumer, ScriptedAudit, VerifiedHandler, task_and_context,
)

from app.agent_orchestrator import AgentOrchestrator
from app.agent_orchestrator import MAX_CONSECUTIVE_FAILED_TURNS
from app.agent_wire_contracts import consumer_wire_model
from app.consumer_purpose_retry import (
    MAX_PURPOSE_RETRIES,
    RETRY_REQUESTED,
    RETRY_EXHAUSTED,
    PurposeRetryBudget,
)
from app.system_executor import SystemExecutor
from app.agent_result import ResultParseError
from app.agent_result import AgentError
from app.store import AgentRole
from app.consumer_agent import _prepare_reviewable_candidate


def output(action, *, target=None, payload=None, refs=None):
    return consumer_wire_model('oa').model_validate({
        'action': action, 'reason': action, 'target': target or {},
        'payload': payload or {}, 'evidence_refs': refs or [],
        'summary': action, 'error_code': '', 'error_retryable': False,
        'error_authorization_required': False, 'risk': 'low', 'confidence': 1.0,
        'rule_coverage': 1.0, 'information_completeness': 1.0, 
    }).to_result()


def test_request_wait_retry_and_business_return_without_replaying_request(tmp_path):
    store, task, context = task_and_context(tmp_path)
    context = replace(context, skill_names=('dingtalk-oa-approval',))
    clock = [datetime.now(timezone.utc) + timedelta(minutes=5)]
    target = {'process_instance_id': 'p', 'file_id': 'file-a'}
    request = output('request_access', target=target, payload={'content': 'Please grant read access'})

    def still_waiting(fresh):
        receipt = fresh.prior_receipts[0].receipt_id
        result = output('retry', refs=[receipt])
        return result.model_copy(update={'stage_index': fresh.stage_index,
                                         'predecessor_review_id': fresh.predecessor_review_id})

    def give_back(fresh):
        result = output('return', target={'process_instance_id': target['process_instance_id'],
                                         'task_id': 't', 'target_activity_id': 'owner'},
                        payload={'remark': 'Access denied and request could not be completed',
                                 'revert_action': 'REVERT_FOR_RESUBMIT'}, refs=['current-read:denied'])
        return result.model_copy(update={'stage_index': fresh.stage_index,
                                         'predecessor_review_id': fresh.predecessor_review_id})

    consumer = ScriptedConsumer(store, request, still_waiting, give_back)
    audit = ScriptedAudit(store, 'approve', 'approve', 'approve')
    handler = VerifiedHandler()
    runtime = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {
            ('dingtalk-oa', 'comment'): handler, ('dingtalk-oa', 'revert_task'): handler,
        }), now=lambda: clock[0])
    first = runtime.process(task, context, refresh_context=lambda: context)
    assert first.error.code == RETRY_REQUESTED
    assert len(consumer.calls) == len(handler.calls) == 1
    budget = PurposeRetryBudget(store, task.business_object_key or f'reply-task:{task.id}', 'oa')
    initial = budget.state()
    assert initial['count'] == 0
    with pytest.raises(ResultParseError, match='already has a verified access request'):
        _prepare_reviewable_candidate(request, store=store, task=task, context=context,
                                       proposal_revision=1, source_client=None)
    with pytest.raises(ResultParseError, match='exactly one verified'):
        _prepare_reviewable_candidate(output('retry', refs=['invented']), store=store,
            task=task, context=context, proposal_revision=1, source_client=None)
    assert (runtime.process(task, context, refresh_context=lambda: context)).error.code == RETRY_REQUESTED
    assert len(consumer.calls) == 1

    clock[0] += timedelta(seconds=first.retry_after_seconds + 1)
    second = runtime.process(task, context, refresh_context=lambda: context)
    assert second.error.code == RETRY_REQUESTED
    assert budget.state()['count'] == 1
    assert len(handler.calls) == 1
    clock[0] += timedelta(seconds=second.retry_after_seconds + 1)
    third = runtime.process(task, context, refresh_context=lambda: context)
    assert third.status == 'executed'
    assert budget.state()['count'] == 2
    assert len(consumer.calls) == 3
    assert len(handler.calls) == 2  # One permission request and one real OA return.
    assert len(audit.calls) == 1  # Only the actual OA return is audited.
    assert budget.state()['closed'] is True


def test_permission_request_and_retry_do_not_invoke_audit(tmp_path):
    store, task, context = task_and_context(tmp_path)
    context = replace(context, skill_names=('dingtalk-oa-approval',))
    now = datetime.now(timezone.utc) + timedelta(minutes=5)
    target = {'process_instance_id': 'p', 'file_id': 'file-a'}
    request = output('request_access', target=target,
                     payload={'content': 'Please grant read access'})
    consumer = ScriptedConsumer(store, request)
    audit = ScriptedAudit(store, 'approve')
    handler = VerifiedHandler()
    runtime = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {
            ('dingtalk-oa', 'comment'): handler,
        }), now=lambda: now)

    result = runtime.process(task, context, refresh_context=lambda: context)

    assert result.error.code == RETRY_REQUESTED
    assert len(consumer.calls) == 1
    assert audit.calls == []
    assert len(handler.calls) == 1


def test_verified_legacy_permission_request_is_not_sent_again(tmp_path):
    store, task, context = task_and_context(tmp_path)
    context = replace(context, skill_names=('dingtalk-oa-approval',))
    now = datetime.now(timezone.utc) + timedelta(minutes=5)
    request = output('request_access',
                     target={'process_instance_id': 'p', 'file_id': 'file-a'},
                     payload={'content': 'Please grant read access'})
    consumer = ScriptedConsumer(store, request)
    run = consumer.run(task, context, proposal_revision=0, parent_agent_run_id=None)
    candidate = store.persist_review_candidate(task, store.get_agent_run(run.run_id), request)
    budget = PurposeRetryBudget(
        store, task.business_object_key or f'reply-task:{task.id}', 'oa'
    )
    budget.begin_access_request(file_id='file-a', action_key='legacy-request-key',
                                candidate_id=candidate['id'], now=now - timedelta(minutes=1))
    budget.finish_access_request(file_id='file-a', action_key='legacy-request-key',
                                 status='verified', result={'provider_receipt_id': 'old'},
                                 now=now - timedelta(minutes=1))
    # A newer Consumer candidate for the same file must not bypass the old
    # reservation and send a second comment after this version is deployed.
    consumer.steps.append(request)
    newer_run = consumer.run(task, context, proposal_revision=0, parent_agent_run_id=None)
    newer = store.persist_review_candidate(
        task, store.get_agent_run(newer_run.run_id), request
    )
    assert newer['id'] != candidate['id']
    audit = ScriptedAudit(store, 'approve')
    handler = VerifiedHandler()
    runtime = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {
            ('dingtalk-oa', 'comment'): handler,
        }), now=lambda: now)

    result = runtime.process(task, context, refresh_context=lambda: context)

    assert result.error.code == RETRY_REQUESTED
    assert audit.calls == []
    assert handler.calls == []


def test_permission_retry_exhaustion_forms_audited_oa_return(tmp_path):
    store, task, context = task_and_context(tmp_path)
    context = replace(context, skill_names=('dingtalk-oa-approval',))
    clock = [datetime.now(timezone.utc) + timedelta(minutes=5)]
    target = {'process_instance_id': 'p', 'file_id': 'file-a'}
    request = output('request_access', target=target,
                     payload={'content': 'Please grant read access'})

    def still_waiting(fresh):
        receipt = fresh.prior_receipts[0].receipt_id
        result = output('retry', refs=[receipt])
        return result.model_copy(update={'stage_index': fresh.stage_index,
                                         'predecessor_review_id': fresh.predecessor_review_id})

    def return_after_exhaustion(fresh):
        assert fresh.permission_return_required is True
        result = output('return', target={'process_instance_id': 'p', 'task_id': 't',
                                          'target_activity_id': 'owner'},
                        payload={'remark': '请提供审批账号可访问的材料，以便继续核对。',
                                 'revert_action': 'REVERT_FOR_RESUBMIT'},
                        refs=[fresh.prior_receipts[0].receipt_id])
        return result.model_copy(update={'stage_index': fresh.stage_index,
                                         'predecessor_review_id': fresh.predecessor_review_id})

    consumer = ScriptedConsumer(store, request, still_waiting, still_waiting,
                                still_waiting, return_after_exhaustion)
    audit = ScriptedAudit(store, 'approve')
    handler = VerifiedHandler()
    runtime = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {
            ('dingtalk-oa', 'comment'): handler,
            ('dingtalk-oa', 'revert_task'): handler,
        }),
        now=lambda: clock[0])
    result = runtime.process(task, context, refresh_context=lambda: context)
    assert result.error.code == RETRY_REQUESTED

    for _ in range(2):
        clock[0] += timedelta(seconds=result.retry_after_seconds + 1)
        result = runtime.process(task, context, refresh_context=lambda: context)
        assert result.error.code == RETRY_REQUESTED

    clock[0] += timedelta(seconds=result.retry_after_seconds + 1)
    result = runtime.process(task, context, refresh_context=lambda: context)

    budget = PurposeRetryBudget(store, task.business_object_key or f'reply-task:{task.id}', 'oa')
    assert result.status == 'executed', (result.error.code, result.summary)
    assert budget.state()['count'] == 3
    assert len(consumer.calls) == 5  # Request + three rereads + one non-reading return proposal.
    assert len(handler.calls) == 2  # One request comment and one audited OA return.
    assert len(audit.calls) == 1  # Only the OA return proposal is audited.


def test_exhausted_permission_without_saved_consumer_run_still_proposes_oa_return(tmp_path):
    store, task, context = task_and_context(tmp_path)
    context = replace(context, skill_names=('dingtalk-oa-approval',))
    clock = [datetime.now(timezone.utc) + timedelta(minutes=5)]
    budget = PurposeRetryBudget(store, task.business_object_key or f'reply-task:{task.id}', 'oa')
    requested_at = clock[0] - timedelta(days=1)
    budget.begin_access_request(file_id='file-a', action_key='permission-request-1',
                                candidate_id=1, now=requested_at)
    budget.finish_access_request(file_id='file-a', action_key='permission-request-1',
                                 status='verified', result={'provider_receipt_id': 'receipt-1'},
                                 now=requested_at)
    budget.schedule('receipt-1', requested_at, 'review-1', requested_at)
    for index in range(MAX_PURPOSE_RETRIES):
        clock[0] = datetime.fromisoformat(budget.state()['next_at']) + timedelta(seconds=1)
        budget.reserve(f'prior-turn-{index}', clock[0])
    assert budget.state()['count'] == MAX_PURPOSE_RETRIES

    def return_after_exhaustion(fresh):
        assert fresh.permission_return_required is True
        return output('return', target={'process_instance_id': 'p', 'task_id': 't',
                                        'target_activity_id': 'owner'},
                      payload={'remark': '请提供审批账号可访问的材料。',
                               'revert_action': 'REVERT_FOR_RESUBMIT'})

    consumer = ScriptedConsumer(store, return_after_exhaustion)
    audit = ScriptedAudit(store, 'approve')
    handler = VerifiedHandler()
    runtime = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {
            ('dingtalk-oa', 'revert_task'): handler,
        }), now=lambda: clock[0])

    result = runtime.process(task, context, refresh_context=lambda: context)

    assert result.status == 'executed'
    assert len(consumer.calls) == 1
    assert len(audit.calls) == 1
    assert len(handler.calls) == 1
    assert budget.state()['count'] == MAX_PURPOSE_RETRIES


def test_definitive_permission_request_denial_proposes_audited_oa_return(tmp_path):
    from app.system_executor import ActionOutcome

    class DeniedPermissionHandler:
        def __init__(self):
            self.calls = []

        def dispatch(self, action, *, action_key, candidate):
            self.calls.append(action_key)
            return ActionOutcome("failed", {"reason": "forbidden.accessDenied"})

        def reconcile(self, action, *, action_key, candidate):
            return None

    store, task, context = task_and_context(tmp_path)
    context = replace(context, skill_names=('dingtalk-oa-approval',))
    now = datetime.now(timezone.utc) + timedelta(minutes=5)
    request = output('request_access',
                     target={'process_instance_id': 'p', 'file_id': 'file-a'},
                     payload={'content': 'Please grant read access'})

    def return_for_denied_access(fresh):
        assert fresh.permission_return_required is True
        result = output('return', target={'process_instance_id': 'p', 'task_id': 't',
                                          'target_activity_id': 'owner'},
                        payload={'remark': '审批账号无法读取该材料，请提供可访问版本。',
                                 'revert_action': 'REVERT_FOR_RESUBMIT'})
        return result.model_copy(update={'stage_index': fresh.stage_index,
                                         'predecessor_review_id': fresh.predecessor_review_id})

    consumer = ScriptedConsumer(store, request, return_for_denied_access)
    audit = ScriptedAudit(store, 'approve')
    denied_handler = DeniedPermissionHandler()
    return_handler = VerifiedHandler()
    runtime = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {
            ('dingtalk-oa', 'comment'): denied_handler,
            ('dingtalk-oa', 'revert_task'): return_handler,
        }), now=lambda: now)

    result = runtime.process(task, context, refresh_context=lambda: context)

    budget = PurposeRetryBudget(store, task.business_object_key or f'reply-task:{task.id}', 'oa')
    assert result.status == 'executed'
    assert budget.access_request_for_file('file-a')['status'] == 'failed'
    assert len(consumer.calls) == 2
    assert len(denied_handler.calls) == 1
    assert len(audit.calls) == 1
    assert len(return_handler.calls) == 1


def test_manual_rerun_after_permission_exhaustion_allows_one_unbudgeted_access_check(
    tmp_path, monkeypatch
):
    store, task, context = task_and_context(tmp_path)
    runtime = AgentOrchestrator(store=store, consumer=ScriptedConsumer(store),
                                audit=ScriptedAudit(store))
    purpose = "oa"
    now = datetime.now(timezone.utc)
    budget = PurposeRetryBudget(store, task.business_object_key or f"reply-task:{task.id}", purpose)
    budget.begin_access_request(file_id="file-a", action_key="access-request", candidate_id=1, now=now)
    budget.finish_access_request(
        file_id="file-a", action_key="access-request", status="verified",
        result={"receipt": "verified"}, now=now,
    )
    budget.schedule("verified-receipt", now, "review", now)
    state = budget.state()
    for index in range(MAX_PURPOSE_RETRIES):
        retry_at = now + timedelta(hours=12 * (index + 1), seconds=1)
        budget.reserve(f"old-generation:{index}", retry_at)

    monkeypatch.setattr(store, "get_reply_attempt", lambda _attempt_id: type(
        "Attempt", (), {"send_status": "blocked", "send_error": RETRY_EXHAUSTED,
                        "agent_run_id": 91}
    )())
    monkeypatch.setattr(store, "get_agent_run", lambda *_args, **_kwargs: type(
        "Run", (), {"role": AgentRole.CONSUMER,
                    "execution_generation": 0}
    )())
    monkeypatch.setattr(store, "list_agent_runs_for_task_generation", lambda *_args, **_kwargs: [])
    resumed_task = task.model_copy(update={
        "execution_generation": "1",
        "manual_rerun_attempt_id": 77,
    })

    assert runtime._manual_permission_access_check(resumed_task, purpose, budget.state()) is True
    assert runtime._manual_permission_access_check(resumed_task, purpose, budget.state()) is False
    assert budget.state()["count"] == state["count"] + MAX_PURPOSE_RETRIES
    assert budget.state()["manual_access_check"]["attempt_id"] == 77
    assert MAX_CONSECUTIVE_FAILED_TURNS == 4  # Initial call plus exactly three retries per role.


def test_manual_access_check_accepts_legacy_verified_comment_receipt_without_resetting_budget(
    tmp_path, monkeypatch
):
    store, task, _context = task_and_context(tmp_path)
    runtime = AgentOrchestrator(store=store, consumer=ScriptedConsumer(store),
                                audit=ScriptedAudit(store))
    now = datetime.now(timezone.utc)
    budget = PurposeRetryBudget(store, task.business_object_key or f"reply-task:{task.id}", "oa")
    # Historical OA access requests persisted a verified comment receipt but
    # did not create the newer access_requests reservation entry.
    budget.schedule("legacy-verified-comment-receipt", now, "review", now)
    for index in range(MAX_PURPOSE_RETRIES):
        budget.reserve(f"legacy-reread:{index}", now + timedelta(hours=12 * (index + 1), seconds=1))

    monkeypatch.setattr(store, "get_reply_attempt", lambda _attempt_id: type(
        "Attempt", (), {"send_status": "blocked", "send_error": RETRY_EXHAUSTED,
                        "agent_run_id": 91}
    )())
    monkeypatch.setattr(store, "get_agent_run", lambda *_args, **_kwargs: type(
        "Run", (), {"role": AgentRole.CONSUMER, "execution_generation": 0}
    )())
    monkeypatch.setattr(store, "list_agent_runs_for_task_generation", lambda *_args, **_kwargs: [])
    resumed_task = task.model_copy(update={
        "execution_generation": "1", "manual_rerun_attempt_id": 78,
    })

    before = budget.state()["count"]
    assert runtime._manual_permission_access_check(resumed_task, "oa", budget.state()) is True
    assert runtime._manual_permission_access_check(resumed_task, "oa", budget.state()) is False
    assert budget.state()["count"] == before == MAX_PURPOSE_RETRIES


def test_worker_pass_limit_does_not_reserve_a_third_unstarted_retry(tmp_path):
    store, task, context = task_and_context(tmp_path)
    now = datetime.now(timezone.utc)
    budget = PurposeRetryBudget(store, task.business_object_key or f'reply-task:{task.id}', 'general')
    budget.schedule('verified-request', now - timedelta(hours=13), 'review', now - timedelta(hours=13))
    consumer = ScriptedConsumer(store, *(AgentError(code='temporary_read_error', retryable=True) for _ in range(2)))
    runtime = AgentOrchestrator(store=store, consumer=consumer, audit=ScriptedAudit(store), now=lambda: now)
    result = runtime.process(task, context, refresh_context=lambda: context)
    assert result.status == 'failed_retryable'
    assert len(consumer.calls) == budget.state()['count'] == 2


def test_fresh_trigger_on_exhausted_permission_wait_proposes_audited_oa_return(tmp_path):
    store, _, context = task_and_context(tmp_path)
    key = "oa-node:instance:task"
    store.enqueue_reply_task(
        channel="dingtalk", conversation_id="conversation", conversation_title="Test",
        single_chat=True, trigger_message_id="oa", trigger_sender="Derek",
        trigger_create_time="2026-10-04 00:00:00", trigger_text="Review",
        business_object_key=key,
    )
    first = next(task for task in store.claim_reply_tasks(2) if task.business_object_key == key)
    claim = store.claim_agent_run(
        first.id, first.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=0, turn_attempt=1, parent_agent_run_id=None,
        operation_id="", owner="consumer",
    )
    store.complete_agent_run(claim.run.id, output("no_action").model_dump(mode="json"), owner="consumer")
    store.complete_reply_task(
        first.id, expected_execution_generation=first.execution_generation
    )
    fresh = store.ensure_reply_task(
        channel="dingtalk", conversation_id="conversation", conversation_title="Test",
        single_chat=True, trigger_message_id="oa-next", trigger_sender="Derek",
        trigger_create_time="2026-10-04 00:01:00", trigger_text="Review again",
        business_object_key=key, execution_generation="new-trigger",
    )

    now = datetime.now(timezone.utc)
    budget = PurposeRetryBudget(store, key, "oa")
    budget.begin_access_request(file_id="file-a", action_key="request", candidate_id=1, now=now)
    budget.finish_access_request(file_id="file-a", action_key="request", status="verified",
                                 result={}, now=now)
    budget.schedule("receipt", now, "review", now)
    for index in range(MAX_PURPOSE_RETRIES):
        budget.reserve(f"turn:{index}", now + timedelta(hours=12 * (index + 1), seconds=1))
    assert fresh.business_object_key == key
    assert budget.state()["count"] == MAX_PURPOSE_RETRIES
    assert budget.claim_oa_return_turn(
        source_consumer_run_id=0, now=now + timedelta(days=2),
        bootstrap_without_source=True, proposal_revision=0, parent_run_id=None,
        stage_index=0, predecessor_review_id=None, reply_task_id=fresh.id,
        execution_generation=fresh.execution_generation,
    )
    assert not budget.claim_oa_return_turn(
        source_consumer_run_id=0, now=now + timedelta(days=2),
        bootstrap_without_source=True, proposal_revision=0, parent_run_id=None,
        stage_index=0, predecessor_review_id=None, reply_task_id=fresh.id + 1,
        execution_generation="duplicate-trigger",
    )

    def return_after_exhaustion(fresh):
        assert fresh.permission_return_required is True
        return output('return', target={'process_instance_id': 'p', 'task_id': 't',
                                       'target_activity_id': 'owner'},
                      payload={'remark': '请提供审批账号可访问的材料。',
                               'revert_action': 'REVERT_FOR_RESUBMIT'})

    consumer = ScriptedConsumer(store, return_after_exhaustion, return_after_exhaustion)
    audit = ScriptedAudit(store, 'return', 'approve')
    handler = VerifiedHandler()
    runtime = AgentOrchestrator(
        store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {
            ('dingtalk-oa', 'revert_task'): handler,
        }),
        now=lambda: now + timedelta(days=2),
    )
    fresh_context = replace(context, task_id=fresh.id, channel=fresh.channel,
                            conversation_id=fresh.conversation_id,
                            conversation_title=fresh.conversation_title,
                            trigger_message_id=fresh.trigger_message_id,
                            trigger_text=fresh.trigger_text,
                            skill_names=('dingtalk-oa-approval',))
    result = runtime.process(fresh, fresh_context, refresh_context=lambda: fresh_context)

    assert result.status == 'executed', (result.error.code, result.summary, len(consumer.calls))
    assert result.error.code == ''
    assert len(consumer.calls) == 2
    assert len(audit.calls) == 2
    assert len(handler.calls) == 1
