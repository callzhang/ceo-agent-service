import json

import pytest

from app.dws_client import DwsClient
from app.processing_reaction import ProcessingReaction, TEMPLATE_KEY
from app.store import AutoReplyStore


class Provider:
    def __init__(self):
        self.calls = []
        self.reject = False

    def create_message_text_emotion(self, **kwargs):
        self.calls.append(('create', kwargs))
        return {'success': True, 'result': {'emotionId': 'e1', 'backgroundId': 'b1'}}

    def add_message_text_emotion(self, cid, mid, **kwargs):
        self.calls.append(('add', mid))
        return {'success': not self.reject}

    def remove_message_text_emotion(self, cid, mid, **kwargs):
        self.calls.append(('remove', mid))
        return {'success': not self.reject}


@pytest.fixture
def setup(tmp_path):
    store = AutoReplyStore(tmp_path / 'state.sqlite3')
    store.enqueue_reply_task(conversation_id='c1', conversation_title='chat', single_chat=True,
                             trigger_message_id='m1', trigger_create_time='2026-10-07 12:00:00',
                             trigger_sender='sender', trigger_text='hello', channel='dingtalk')
    provider = Provider()
    return store, provider, ProcessingReaction(store, provider)


def test_start_duplicate_and_restart_finish(setup):
    store, provider, progress = setup
    progress.start('c1', 'm1')
    progress.start('c1', 'm1')
    assert [c[0] for c in provider.calls] == ['create', 'add']
    assert json.loads(store.get_service_state(TEMPLATE_KEY))['emotion_id'] == 'e1'
    restarted = ProcessingReaction(AutoReplyStore(store.path), provider)
    restarted.finish('c1', 'm1')
    restarted.finish('c1', 'm1')
    assert [c[0] for c in provider.calls] == ['create', 'add', 'remove']


def test_pending_retry_retains_and_terminal_removes(setup):
    store, provider, progress = setup
    progress.start('c1', 'm1')
    progress.sync()
    assert provider.calls[-1] == ('add', 'm1')
    with store._connect() as db:
        db.execute("update reply_tasks set status='done'")
    progress.sync()
    assert provider.calls[-1] == ('remove', 'm1')


def test_replaced_trigger_cleans_old_message(setup):
    store, provider, progress = setup
    progress.start('c1', 'm1')
    with store._connect() as db:
        db.execute("update reply_tasks set trigger_message_id='m2'")
    progress.sync()
    assert provider.calls[-1] == ('remove', 'm1')
    progress.start('c1', 'm2')
    assert provider.calls[-1] == ('add', 'm2')


def test_failed_remove_survives_restart_for_retry(setup):
    store, provider, progress = setup
    progress.start('c1', 'm1')
    provider.reject = True
    progress.finish('c1', 'm1')
    provider.reject = False
    ProcessingReaction(AutoReplyStore(store.path), provider).sync()
    assert provider.calls[-1] == ('remove', 'm1')


def test_interrupted_add_intent_resumes(setup):
    store, provider, progress = setup
    provider.reject = True
    progress.start('c1', 'm1')
    provider.reject = False
    ProcessingReaction(AutoReplyStore(store.path), provider).sync()
    assert provider.calls[-1] == ('add', 'm1')
    assert len([c for c in provider.calls if c[0] == 'create']) == 1


def test_terminal_input_does_not_add(setup):
    store, provider, progress = setup
    with store._connect() as db:
        db.execute("update reply_tasks set status='failed'")
    progress.start('c1', 'm1')
    assert provider.calls == []


def test_native_remove_text_emotion_command():
    calls = []
    client = DwsClient()
    client.run_json = lambda argv: calls.append(argv) or {'success': True}
    client.remove_message_text_emotion('c1', 'm1', text='处理中', emotion_id='e1',
                                     emotion_name='处理中', background_id='b1')
    command = calls[0]
    assert command[1:3] == ['chat', '+messages-remove-text-emotion']
    assert command[command.index('--conversation-id') + 1] == 'c1'
    assert command[command.index('--msg-id') + 1] == 'm1'
    assert command[command.index('--emotion-id') + 1] == 'e1'


def test_worker_intake_adds_reaction_without_running_agent(tmp_path, monkeypatch):
    from tests.test_worker import FakeCodex, FakeDws, conversation, make_worker, message
    dws = FakeDws([], {})
    provider = Provider()
    dws.create_message_text_emotion = provider.create_message_text_emotion
    dws.add_message_text_emotion = provider.add_message_text_emotion
    dws.remove_message_text_emotion = provider.remove_message_text_emotion
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    assert worker._enqueue_reply_task(conversation(), message('please respond'))
    assert [c[0] for c in provider.calls] == ['create', 'add']
    assert worker.store.count_reply_tasks(status='pending') == 1


def test_synthetic_intake_has_no_reaction(tmp_path, monkeypatch):
    from tests.test_worker import FakeCodex, FakeDws, conversation, make_worker, message
    dws = FakeDws([], {})
    provider = Provider()
    dws.create_message_text_emotion = provider.create_message_text_emotion
    worker = make_worker(tmp_path, dws, FakeCodex([]), monkeypatch)
    trigger = message('service work').model_copy(update={'raw_payload': {'service_task': True}})
    assert worker._enqueue_reply_task(conversation(), trigger)
    assert provider.calls == []
