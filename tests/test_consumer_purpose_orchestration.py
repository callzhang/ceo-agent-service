from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest

from test_agent_orchestrator import (
    ScriptedConsumer, ScriptedAudit, VerifiedHandler, task_and_context,
)

from app.agent_orchestrator import AgentOrchestrator
from app.agent_wire_contracts import consumer_wire_model
from app.consumer_purpose_retry import RETRY_REQUESTED, RETRY_EXHAUSTED, PurposeRetryBudget
from app.system_executor import SystemExecutor
from app.agent_result import ResultParseError
from app.agent_result import AgentError
from app.consumer_agent import _prepare_reviewable_candidate


def output(action, *, target=None, payload=None, refs=None):
    return consumer_wire_model('oa').model_validate({
        'action': action, 'reason': action, 'target': target or {},
        'payload': payload or {}, 'evidence_refs': refs or [],
        'summary': action, 'error_code': '', 'error_retryable': False,
        'error_authorization_required': False, 'risk': 'low', 'confidence': 1.0,
        'rule_coverage': 1.0, 'information_completeness': 1.0, 'durable_memories': [],
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
    assert len(audit.calls) == 2  # Permission comment and OA return are each audited.
    assert budget.state()['closed'] is True


def test_permission_request_requires_audit_before_comment(tmp_path):
    store, task, context = task_and_context(tmp_path)
    context = replace(context, skill_names=('dingtalk-oa-approval',))
    now = datetime.now(timezone.utc) + timedelta(minutes=5)
    target = {'process_instance_id': 'p', 'file_id': 'file-a'}
    request = output('request_access', target=target,
                     payload={'content': 'Please grant read access'})
    consumer = ScriptedConsumer(store, request)
    audit = ScriptedAudit(store, 'approve')
    handler = VerifiedHandler()
    original_dispatch = handler.dispatch

    def dispatch_after_audit(*args, **kwargs):
        assert len(audit.calls) == 1
        return original_dispatch(*args, **kwargs)

    handler.dispatch = dispatch_after_audit
    runtime = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {
            ('dingtalk-oa', 'comment'): handler,
        }), now=lambda: now)

    result = runtime.process(task, context, refresh_context=lambda: context)

    assert result.error.code == RETRY_REQUESTED
    assert len(consumer.calls) == 1
    assert len(audit.calls) == 1
    assert len(handler.calls) == 1


def test_rejected_permission_request_does_not_comment(tmp_path):
    store, task, context = task_and_context(tmp_path)
    context = replace(context, skill_names=('dingtalk-oa-approval',))
    now = datetime.now(timezone.utc) + timedelta(minutes=5)
    request = output('request_access',
                     target={'process_instance_id': 'p', 'file_id': 'file-a'},
                     payload={'content': 'Please grant read access'})
    consumer = ScriptedConsumer(store, request,
                                AgentError(code='revised_source_unavailable', retryable=False))
    audit = ScriptedAudit(store, 'return')
    handler = VerifiedHandler()
    runtime = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
        system_executor=SystemExecutor(store, {
            ('dingtalk-oa', 'comment'): handler,
        }), now=lambda: now)

    result = runtime.process(task, context, refresh_context=lambda: context)

    assert result.status == 'failed_terminal'
    assert len(audit.calls) == 1
    assert handler.calls == []


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


def test_expired_budget_blocks_source_refresh_and_consumer_call(tmp_path):
    store, task, context = task_and_context(tmp_path)
    clock = datetime.now(timezone.utc)
    budget = PurposeRetryBudget(store, task.business_object_key or f'reply-task:{task.id}', 'general')
    budget.schedule('verified-request', clock - timedelta(days=2), 'review', clock - timedelta(days=2))
    consumer, audit = ScriptedConsumer(store), ScriptedAudit(store)
    runtime = AgentOrchestrator(store=store, consumer=consumer, audit=audit,
                                now=lambda: clock + timedelta(days=1))
    def must_not_read():
        raise AssertionError('expired retry must not refresh sources')
    result = runtime.process(task, context, refresh_context=must_not_read)
    assert result.status == 'failed_terminal'
    assert result.error.code == RETRY_EXHAUSTED
    assert not consumer.calls and not audit.calls


def test_worker_pass_limit_does_not_reserve_a_third_unstarted_retry(tmp_path):
    store, task, context = task_and_context(tmp_path)
    now = datetime.now(timezone.utc)
    budget = PurposeRetryBudget(store, task.business_object_key or f'reply-task:{task.id}', 'general')
    budget.schedule('verified-request', now - timedelta(hours=1), 'review', now - timedelta(hours=1))
    consumer = ScriptedConsumer(store, *(AgentError(code='temporary_read_error', retryable=True) for _ in range(2)))
    runtime = AgentOrchestrator(store=store, consumer=consumer, audit=ScriptedAudit(store), now=lambda: now)
    result = runtime.process(task, context, refresh_context=lambda: context)
    assert result.status == 'failed_retryable'
    assert len(consumer.calls) == budget.state()['count'] == 2
