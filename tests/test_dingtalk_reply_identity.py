"""Dispatch and readback bind the same original native trigger identity."""
from types import SimpleNamespace

import pytest

from app.dingtalk_models import DingTalkMessage
from app.outbound_postfix import PreparedOutboundMessage
from app.system_action_handlers import DingTalkMessageHandler
from tests.test_system_executor import _candidate


@pytest.mark.parametrize('entrypoint', ['dispatch', 'reconcile'])
@pytest.mark.parametrize('changed', ['target_conversation', 'target_message', 'source_message', 'source_type'])
def test_native_reply_rejects_identity_mismatch_before_provider(entrypoint, changed):
    trigger = DingTalkMessage(open_conversation_id='native-chat', open_message_id='original-message',
        conversation_title='Example', single_chat=True, sender_name='Example',
        create_time='2026-11-18 12:00:00', content='Please confirm.')
    if changed == 'source_message':
        trigger = trigger.model_copy(update={'open_message_id': 'another-message'})
    if changed == 'source_type':
        trigger = trigger.model_copy(update={'single_chat': False})
    target = {'conversation_id': trigger.open_conversation_id, 'message_id': trigger.open_message_id}
    if changed == 'target_conversation':
        target['conversation_id'] = 'another-chat'
    if changed == 'target_message':
        target['message_id'] = 'another-message'
    task = SimpleNamespace(business_object_key='object:1', execution_generation='gen',
        conversation_id='queue-alias', conversation_title='Example', single_chat=True,
        trigger_message_id='original-message', trigger_message_json=trigger.model_dump_json())

    class Store:
        def get_reply_task_for_execution(self, *_): return task
        def get_outbound_postfix(self, *_):
            return PreparedOutboundMessage('dingtalk', 'prepared', 'Prepared reply', '', 'v1')
        def get_outbound_postfix_receipt(self, *_): return None

    class ForbiddenProvider:
        def __getattr__(self, name):
            pytest.fail('identity mismatch must not call provider or Sender: ' + name)

    handler = DingTalkMessageHandler(Store(), ForbiddenProvider(), sender=ForbiddenProvider())
    action = _candidate(actions=[{
        'description': 'Reply', 'action_identity': 'reply', 'capability': 'dingtalk-chat',
        'operation': 'reply_to_message', 'target': target, 'payload': {'content': 'Prepared reply'},
    }]).proposal.actions[0]
    result = getattr(handler, entrypoint)(action, action_key='key', candidate={
        'task_id': 1, 'proposal_revision': 0, 'execution_generation': 'gen',
        'action_attempt': {'created_at': '2026-11-18T12:00:00Z'}})
    assert result.status in ('failed', 'uncertain')
    assert result.provider_result['reason'] in ('reply_target_mismatch', 'reply_trigger_mismatch')
