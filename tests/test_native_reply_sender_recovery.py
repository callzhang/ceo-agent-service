"""A native sender ID survives exact-source restoration and never becomes a guessed recipient."""
from unittest.mock import Mock

import pytest

from app.dingtalk_models import DingTalkConversation
from app.dws_client import DwsClient, DwsError, DwsUserProfile


def source():
    return {'contractVersion': 'im.message-list.v1', 'complete': True,
            'messagesComplete': True, 'hasMore': False, 'failures': [], 'messages': [{
                'conversationId': 'native-chat', 'messageId': 'trigger', 'sender': 'Same Name',
                'senderId': 'open-2', 'createTime': '2026-11-20 21:34:39', 'text': 'Question'}]}


def conversation():
    return DingTalkConversation(open_conversation_id='native-chat', title='Example',
                                single_chat=True, unread_point=0)


def profiles():
    return [DwsUserProfile(user_id='user-1', name='Same Name', open_dingtalk_id='open-1'),
            DwsUserProfile(user_id='user-2', name='Same Name', open_dingtalk_id='open-2')]


def test_exact_message_restore_keeps_verified_sender_identity(monkeypatch):
    client = DwsClient()
    monkeypatch.setattr(client, 'run_json', lambda _: source())
    monkeypatch.setattr(client, 'search_user_profiles', lambda _: profiles())
    restored = client.read_message_by_id(conversation(), 'trigger')
    assert restored.sender_open_dingtalk_id == 'open-2'
    assert restored.sender_user_id == 'user-2'
    assert restored.raw_payload['senderId'] == 'open-2'


def test_native_reply_resolves_stable_id_in_existing_uncompacted_source(monkeypatch):
    client = DwsClient()
    message = client.parse_messages(source(), conversation_title='Example', single_chat=True)[0]
    monkeypatch.setattr(client, 'search_user_profiles', lambda _: profiles())
    send = Mock(return_value={'success': True})
    monkeypatch.setattr(client, 'reply_message', send)
    client.send_reply_to_trigger(conversation(), message, 'Reviewed body')
    assert send.call_args.args[:4] == ('native-chat', 'trigger', 'open-2', 'Reviewed body')


@pytest.mark.parametrize('invalid', ['missing', 'unknown', 'ambiguous', 'no-open-id'])
def test_native_reply_refuses_unverified_sender_before_dispatch(monkeypatch, invalid):
    client = DwsClient()
    payload = source()
    matches = profiles()
    if invalid == 'missing':
        payload['messages'][0].pop('senderId')
    elif invalid == 'unknown':
        payload['messages'][0]['senderId'] = 'unrelated'
    elif invalid == 'ambiguous':
        matches.append(DwsUserProfile(user_id='other-user', name='Same Name', open_dingtalk_id='open-2'))
    else:
        payload['messages'][0]['senderId'] = 'user-2'
        matches[1] = matches[1].model_copy(update={'open_dingtalk_id': ''})
    message = client.parse_messages(payload, conversation_title='Example', single_chat=True)[0]
    monkeypatch.setattr(client, 'search_user_profiles', lambda _: matches)
    send = Mock(side_effect=AssertionError('must not dispatch'))
    monkeypatch.setattr(client, 'reply_message', send)
    with pytest.raises(DwsError) as raised:
        client.send_reply_to_trigger(conversation(), message, 'Reviewed body')
    assert send.call_count == 0
    assert raised.value.pre_action_failure is True
    assert raised.value.provider_error['stage'] == 'tool_validation'
    assert str(raised.value)


def test_system_retains_native_error_message_for_reconciliation():
    from app.system_executor import SystemExecutor
    from tests.test_system_executor import FakeStore, _candidate

    class Handler:
        def dispatch(self, *args, **kwargs):
            raise DwsError('native sender field is missing')

    store = FakeStore(_candidate())
    SystemExecutor(store, {('test', 'act'): Handler()}).execute(store.get_reply_task(1), 1, 2)
    import json
    result = json.loads(store.attempts[0]['result_json'])
    assert result['provider_error_message'] == 'native sender field is missing'
