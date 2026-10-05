"""Actual native message handler + SQLite recovery after acceptance without receipt."""
from datetime import UTC, datetime

import pytest

from app.agent_contracts import ConsumerAgentResult
from app.dws_client import DwsClient
from app.service_message_sender import ServiceMessageSender, agent_message_delivery_key
from app.store import AgentRole, AutoReplyStore
from app.system_executor import SystemExecutor


class InterruptedNativeClient(DwsClient):
    def __init__(self):
        super().__init__()
        self.commands = []
        self.messages = []
        self.visible = False
        self.sends = 0

    def get_current_user_id(self):
        return 'principal'

    def run_json(self, command):
        self.commands.append(command)
        if '+messages-send' in command:
            self.sends += 1
            self.messages.append({
                'openConversationId': 'target-cid', 'openMessageId': 'accepted-message',
                'sender': 'Principal', 'senderUserId': 'principal',
                'createTime': datetime.now(UTC).isoformat(),
                'content': command[command.index('--markdown') + 1],
            })
            raise RuntimeError('simulated interruption after provider acceptance')
        assert command[1:3] == ['chat', 'message']
        assert command[3] in ('list', 'list-direct')
        return {'result': {'messages': self.messages if self.visible else []}}


@pytest.mark.parametrize('legacy_timestamp', [False, True])
@pytest.mark.parametrize('operation,target,read_target', [
    ('send_group_message', {'conversation_id': 'target-cid'}, ['--group', 'target-cid']),
    ('send_direct_message', {'user_id': 'exact-user'}, ['--user', 'exact-user']),
    ('send_direct_message', {'open_dingtalk_id': 'exact-open'},
     ['--open-dingtalk-id', 'exact-open']),
])
def test_native_missing_receipt_reads_exact_target_after_restart_without_resend(
    tmp_path, operation, target, read_target, legacy_timestamp,
):
    path = tmp_path / 'native-recovery.sqlite3'
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        channel='dingtalk', conversation_id='source-cid', conversation_title='Source',
        single_chat=False, trigger_message_id='trigger', trigger_sender='Derek',
        trigger_create_time='2026-10-04 00:00:00', trigger_text='Send the exact notice',
    )
    task = store.claim_reply_tasks(1)[0]
    key = agent_message_delivery_key(business_object_key=task.business_object_key,
        action_identity='notice', execution_generation=task.execution_generation,
        proposal_revision=0)
    client = InterruptedNativeClient()
    prepared = ServiceMessageSender(store=store, dingtalk=client).prepare(
        channel='dingtalk', delivery_key=key, body='Exact reviewed notice',
        feedback_base_url='',
    )
    result = ConsumerAgentResult.model_validate({
        'outcome': 'proposal', 'summary': 'Notify the exact recipient',
        'proposal': {'objective': 'Notify', 'actions': [{
            'description': 'Notice', 'action_identity': 'notice',
            'capability': 'dingtalk-chat', 'operation': operation,
            'target': target, 'payload': {'content': prepared.final_body},
        }], 'sourced_facts': [], 'authored_judgment': 'Verified target and body'},
        'error': {'code': '', 'retryable': False, 'authorization_required': False},
        'risk': 'low', 'confidence': 1.0, 'rule_coverage': 1.0,
        'information_completeness': 1.0,
    })
    consumer = store.claim_agent_run(task.id, task.execution_generation,
        role=AgentRole.CONSUMER, proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=None, operation_id='', owner='consumer').run
    store.complete_agent_run(consumer.id, result.model_dump(mode='json'), owner='consumer')
    candidate = store.persist_review_candidate(task, consumer, result)
    audit = store.claim_agent_run(task.id, task.execution_generation,
        role=AgentRole.AUDIT, proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=consumer.id, operation_id='review', owner='audit').run
    approval = {'outcome': 'approve', 'candidate_digest': candidate['candidate_digest']}
    store.complete_agent_run(audit.id, approval, owner='audit')
    review = store.record_candidate_review(candidate['id'], audit.id, approval)
    first = SystemExecutor(store, dws=client).execute(task, candidate['id'], review['id'])
    assert first.outcome == 'failed'
    assert client.sends == 1
    assert store.get_outbound_postfix_receipt('dingtalk', key) is None

    if legacy_timestamp:
        import sqlite3

        with sqlite3.connect(path) as db:
            db.execute("update candidate_action_attempts set created_at=?",
                       ('2026-10-05 00:00:00',))

    reopened = AutoReplyStore(path)
    still_unknown = SystemExecutor(reopened, dws=client).execute(
        task, candidate['id'], review['id'])
    assert still_unknown.outcome == 'failed'
    assert client.sends == 1
    reads = [command for command in client.commands if command[1:3] == ['chat', 'message']]
    if not legacy_timestamp:
        assert reads, 'uncertain native send must inspect its original provider target'
        assert reads[-1][reads[-1].index(read_target[0]):][:2] == read_target
    client.visible = True
    recovered = SystemExecutor(reopened, dws=client).execute(
        task, candidate['id'], review['id'])
    if legacy_timestamp:
        assert recovered.outcome == 'failed'
        assert reopened.get_outbound_postfix_receipt('dingtalk', key) is None
        assert client.sends == 1
        return
    assert recovered.outcome == 'executed'
    assert client.sends == 1
    assert reopened.get_outbound_postfix_receipt('dingtalk', key)['result']['openMessageId'] == 'accepted-message'
    assert SystemExecutor(AutoReplyStore(path), dws=client).execute(
        task, candidate['id'], review['id']).outcome == 'executed'
    assert client.sends == 1


@pytest.mark.parametrize('fault', [
    'old', 'body', 'conversation', 'sender', 'recalled', 'duplicate',
])
def test_positive_readback_cannot_use_wrong_or_ambiguous_message(monkeypatch, fault):
    from datetime import timedelta
    from app.dingtalk_models import DingTalkConversation, DingTalkMessage

    boundary = datetime(2026, 10, 5, 11, 0, 0, 123000, tzinfo=UTC)
    message = DingTalkMessage(
        open_conversation_id='exact-cid', open_message_id='message',
        conversation_title='Group', single_chat=False, sender_name='Principal',
        sender_user_id='principal', create_time=(boundary + timedelta(milliseconds=1)).isoformat(),
        content='Exact body',
    )
    updates = {
        'old': {'create_time': (boundary - timedelta(milliseconds=1)).isoformat()},
        'body': {'content': 'Different body'},
        'conversation': {'open_conversation_id': 'other-cid'},
        'sender': {'sender_user_id': 'another-user'},
        'recalled': {'raw_payload': {'messageStatus': 'recalled'}},
        'duplicate': {},
    }
    messages = [message.model_copy(update=updates[fault])]
    if fault == 'duplicate':
        messages.append(message.model_copy(update={'open_message_id': 'another-message'}))
    client = InterruptedNativeClient()
    monkeypatch.setattr(client, 'read_recent_messages', lambda conversation: messages)
    conversation = DingTalkConversation(open_conversation_id='exact-cid', title='Group',
                                        single_chat=False, unread_point=0)
    assert client.reconcile_message_send(conversation, 'Exact body', not_before=boundary) is None
    assert client.sends == 0


@pytest.mark.parametrize('capability,operation,target,expected_path', [
    ('dingtalk-doc', 'create_doc_comment', {'node_id': 'exact-node'},
     ['doc', '+comment-list', '--node', 'exact-node', '--limit', '50', '--format', 'json']),
    ('dingtalk-oa', 'comment', {'process_instance_id': 'exact-process'},
     ['oa', 'approval', 'records', '--instance-id', 'exact-process', '--format', 'json']),
])
def test_comment_uncertainty_reads_original_object_without_inferred_identity(
    capability, operation, target, expected_path,
):
    from app.agent_contracts import ProposedAction
    from app.system_action_handlers import DocumentCommentHandler, OaCommentHandler

    class ReadOnlyClient(DwsClient):
        def __init__(self):
            super().__init__()
            self.commands = []

        def run_json(self, command):
            self.commands.append(command)
            assert command[1:] == expected_path
            return {'result': {'observations': ['unattributed existing comment']}}

    client = ReadOnlyClient()
    handler = DocumentCommentHandler(client) if capability == 'dingtalk-doc' else OaCommentHandler(client)
    action = ProposedAction.model_validate({
        'description': 'Comment', 'action_identity': 'original-comment',
        'capability': capability, 'operation': operation, 'target': target,
        'payload': {'content': 'Exact comment'},
    })
    outcome = handler.reconcile(action, action_key='original-key', candidate={})
    assert outcome is not None and outcome.status == 'uncertain'
    assert len(client.commands) == 1
    assert outcome.provider_result['readback'] == {
        'result': {'observations': ['unattributed existing comment']}}


@pytest.mark.parametrize('wire_time,verified', [
    ('2026-10-05 19:00:00', False),
    ('2026-10-05 19:00:01', True),
])
def test_native_second_precision_message_time_keeps_ambiguous_same_second_unknown(
    monkeypatch, wire_time, verified,
):
    from app.dingtalk_models import DingTalkConversation, DingTalkMessage

    client = InterruptedNativeClient()
    message = DingTalkMessage(
        open_conversation_id='exact-cid', open_message_id='message',
        conversation_title='Group', single_chat=False, sender_name='Principal',
        sender_user_id='principal', create_time=wire_time, content='Exact body',
    )
    monkeypatch.setattr(client, 'read_recent_messages', lambda conversation: [message])
    conversation = DingTalkConversation(open_conversation_id='exact-cid', title='Group',
                                        single_chat=False, unread_point=0)
    receipt = client.reconcile_message_send(
        conversation, 'Exact body',
        not_before=datetime(2026, 10, 5, 11, 0, 0, 123000, tzinfo=UTC),
    )
    assert (receipt is not None) is verified
    assert client.sends == 0
