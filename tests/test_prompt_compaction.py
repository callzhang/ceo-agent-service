import json

from app.agent_context import AgentContextMessage, AgentTaskContext, AuditTurnContext
from app.agent_contracts import ConsumerAgentResult, ReviewedSourceBinding
from app.reviewed_sources import context_source


def task_context():
    return AgentTaskContext(task_id=7, channel='dingtalk', conversation_id='conversation',
        conversation_title='Group', single_chat=False, trigger_message_id='trigger',
        trigger_sender='Source', trigger_text='unique-trigger-facts', trigger_create_time='2026-10-06T09:00:00-07:00',
        messages=(AgentContextMessage('earlier', 'Source', 'unique-long-history-facts', '2026-10-06T08:00:00-07:00'),),
        materials=(), prior_receipts=(), trigger_raw_payload={'body': 'unique-raw-provider-facts'})


def candidate(context, *, matching=True):
    value = context_source(context)
    if not matching:
        value = {**value, 'trigger_text': 'stale-trigger-facts'}
    return ConsumerAgentResult.model_validate(dict(outcome='no_action', summary='No external action needed.',
        proposal=None, decision_options=[], error={'code':'','retryable':False,'authorization_required':False},
        risk='low', confidence=1, rule_coverage=1, information_completeness=1,
        source_bindings=[ReviewedSourceBinding(provider='task_context', object_ref=context.trigger_message_id, value=value)]))


def test_audit_keeps_complete_candidate_but_serializes_bound_source_facts_once():
    context = task_context()
    result = candidate(context)
    audit = AuditTurnContext(context, 0, 'operation', result, 'digest', 'configured audit rule')
    body = audit.render(current_time='2026-10-06T10:00:00-07:00')
    for fact in ('unique-trigger-facts', 'unique-long-history-facts', 'unique-raw-provider-facts'):
        assert body.count(fact) == 1
    assert '2026-10-06T08:00:00-07:00' in body
    saved = json.loads(body.partition('Candidate revision\n')[2])['candidate']
    assert saved == result.model_dump(mode='json')
    assert 'source_bindings[0].value' in body


def test_audit_keeps_current_and_different_bound_source_values_visible():
    context = task_context()
    body = AuditTurnContext(context, 0, 'operation', candidate(context, matching=False), 'digest', 'rule').render()
    assert 'unique-trigger-facts' in body and 'stale-trigger-facts' in body
    assert 'source_bindings[0].value.trigger_text' not in body


def test_consumer_rules_and_each_developer_contract_avoid_repeated_copies():
    from app.consumer_agent import consumer_developer_instructions, audit_developer_instructions
    from app.agent_context import _CONSUMER_AGENT_RULES, _AUDIT_AGENT_RULES
    consumer = consumer_developer_instructions(runtime_context='', work_profile='') + task_context().render()
    audit = audit_developer_instructions('configured audit rule', runtime_context='', work_profile='')
    assert consumer.count(_CONSUMER_AGENT_RULES) == 1
    assert audit.count(_AUDIT_AGENT_RULES) == 1


def test_audit_task_retains_existing_review_contract_at_the_task_boundary():
    from app.agent_context import _AUDIT_AGENT_RULES
    context = task_context()
    body = AuditTurnContext(context, 0, 'operation', candidate(context), 'digest', 'configured audit rule').render(developer_audit_rules='configured audit rule')
    assert body.startswith(_AUDIT_AGENT_RULES + '\n\n')
    assert body.count('configured audit rule') == 0


def test_audit_does_not_collapse_distinct_json_boolean_and_number_sources():
    from dataclasses import replace
    context = replace(task_context(), trigger_raw_payload={'flag': True})
    result = candidate(context)
    source = result.source_bindings[0]
    source = source.model_copy(update={'value': {**source.value, 'trigger_raw_payload': {'flag': 1}}})
    result = result.model_copy(update={'source_bindings': (source,)})
    body = AuditTurnContext(context, 0, 'operation', result, 'digest', '').render(developer_audit_rules='')
    trigger, _ = json.JSONDecoder().raw_decode(body.partition('Original trigger\n')[2])
    assert trigger['raw_payload']['flag'] is True
    persisted, _ = json.JSONDecoder().raw_decode(body.partition('Candidate revision\n')[2])
    assert type(persisted['candidate']['source_bindings'][0]['value']['trigger_raw_payload']['flag']) is int
