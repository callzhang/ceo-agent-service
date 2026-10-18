"""Input retirement follows durable completion and released provider leases."""
import json
from types import SimpleNamespace

import pytest

from app import email_worker as worker


@pytest.mark.parametrize('status', ['done', 'failed'])
def test_direct_completion_retires_only_after_successful_receipt_and_released_lease(status):
    events = []
    action = SimpleNamespace(account_id='a', classification_id=101)
    outcome = SimpleNamespace(status=status, provider_operation='operation', provider_target='target',
                              provider_result_id='receipt', error='')
    class Store:
        def claim_next_direct_action(self, **kwargs):
            return action
        def complete_direct_action_attempt(self, claimed, **kwargs):
            assert claimed is action
            assert events == ['executed', 'lease_released']
            events.append('receipt_committed')
    class Executor:
        def execute(self, claimed):
            try:
                events.append('executed')
                return outcome
            finally:
                events.append('lease_released')
    def retire(identity):
        assert identity == 101 and events[-1] == 'receipt_committed'
        events.append('original_retired')
    assert worker._run_next_direct_action(Store(), lambda account: Executor(), retire_original=retire) is outcome
    assert events == ['executed', 'lease_released', 'receipt_committed'] + (['original_retired'] if status == 'done' else [])


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_completed_classification_cleanup_cannot_reclassify_a_completed_task_as_failed(cleanup_fails):
    events = []
    task = SimpleNamespace(stable_message_identity='stable', input_json='{}')
    adapter = SimpleNamespace(claim_next=lambda **kwargs: task,
        complete=lambda *args: events.append('classification_committed'),
        fail=lambda *args, **kwargs: pytest.fail('completed decision cannot be turned into a failed classification'))
    store = SimpleNamespace(get_classification_by_stable_identity=lambda stable: {
        'id': 101, 'agent_result': {'category': 'work', 'important': False, 'certainty': 'certain',
        'confidence': 0.9, 'reason': 'business'}, 'action_plan': None})
    def retire(identity):
        assert identity == 101 and events == ['classification_committed']
        events.append('original_retired')
        if cleanup_fails:
            raise RuntimeError('synthetic retirement error')
    def run():
        return worker.run_email_classification_task_once(adapter, object(), store, owner='test',
            provider_readback=lambda *args: pytest.fail('no source read needed for durable outcome replay'),
            action_task_producer=object(), retire_original=retire)
    if cleanup_fails:
        with pytest.raises(RuntimeError, match='retirement error'):
            run()
    else:
        assert run()['classification_id'] == 101
    assert events == ['classification_committed', 'original_retired']


def _task():
    return SimpleNamespace(id=7, execution_generation='generation', attempts=1,
        conversation_id='conversation', conversation_title='title', trigger_message_id='trigger',
        trigger_sender='sender', trigger_text='metadata',
        trigger_message_json=json.dumps({'classification_id': 101}))


def test_direct_unsubscribe_finalization_retires_after_task_and_receipt():
    events = []
    store = SimpleNamespace(record_reply_attempt=lambda **kwargs: events.append('receipt'),
        complete_reply_task=lambda *args, **kwargs: events.append('task_done'))
    worker._finalize_direct_email_unsubscribe_task(store, _task(), {'status': 'done', 'outcome': 'done'},
        retire_original=lambda identity: events.append(('retired', identity)))
    assert events == ['receipt', 'task_done', ('retired', 101)]


def test_audited_finalization_retires_after_task_result_and_artifacts(monkeypatch):
    events = []
    monkeypatch.setattr('app.task_artifacts.cleanup_terminal_task_artifacts',
                        lambda *args: events.append('artifacts'))
    run = SimpleNamespace(id=8, codex_session_id='native-session', transcript_start_line=0,
                          transcript_end_line=1, tool_events=[])
    store = SimpleNamespace(get_agent_run=lambda identity: run,
        finalize_orchestrated_reply_task=lambda **kwargs: events.append('task_result_committed'))
    result = SimpleNamespace(status='executed', error=SimpleNamespace(code='', authorization_required=False),
        final_run_id=8, summary='completed', human_decision=None)
    worker._finalize_email_task(store, _task(), result,
        retire_original=lambda identity: events.append(('retired', identity)))
    assert events == ['task_result_committed', 'artifacts', ('retired', 101)]


def test_real_store_retires_after_last_action_and_reopen_does_not_reexecute(tmp_path):
    from functools import partial
    from app.email_store import EmailStore
    from app.email_provider_actions import ProviderActionResult
    from tests.test_email_worker import _historical_junk_fixture, _accepted_model_prediction

    store = EmailStore(tmp_path / 'lifecycle.sqlite3')
    message, context = _historical_junk_fixture()
    accepted = worker.persist_model_primary_classification(store, SimpleNamespace(produce=lambda *args: None),
        message=message, prediction=_accepted_model_prediction(), context=context, model_id='test-model',
        model_text='retained model features', unsubscribe_entries=())
    classification_id = accepted.persisted['id']
    with store._connect() as db:
        original_model_input = db.execute('select class_model_text from emails').fetchone()[0]
    effects, reads = [], []
    class Executor:
        def execute(self, action):
            effects.append(action.action_id)
            return ProviderActionResult(status='done', provider_operation=action.action_type.value,
                provider_target=action.locator.stable_message_identity, provider_result_id='synthetic-receipt')
    def read(account, identity):
        reads.append((account, identity))
        return message
    retire = partial(store.compact_email_message_body, source_reader=SimpleNamespace(read=read))
    assert worker._run_next_direct_action(store, lambda account: Executor(), retire_original=retire).status == 'done'
    with store._connect() as db:
        first = db.execute('select message_normalized_text,message_source_reference_json from emails').fetchone()
    assert first[0] and first[1] is None and reads == []
    assert worker._run_next_direct_action(store, lambda account: Executor(), retire_original=retire).status == 'done'
    with store._connect() as db:
        last = db.execute('select message_normalized_text,message_source_reference_json,class_model_text from emails').fetchone()
    assert last[0] == '' and json.loads(last[1])['kind'] == 'provider'
    assert last[2] == original_model_input
    assert reads == [('account-1', accepted.persisted['stable_message_identity'])]
    reopened = EmailStore(store.path)
    assert worker._run_next_direct_action(reopened, lambda account: pytest.fail('completed actions must not replay'), retire_original=retire) is None
    statuses = reopened.direct_action_statuses_for_plan(accepted.persisted['action_plan']['action_plan_id'])
    assert len(effects) == len(statuses) == 2 and set(statuses.values()) == {'done'}
    assert classification_id > 0


def test_production_dependency_builder_shares_one_retirement_reader(tmp_path, monkeypatch):
    from functools import partial
    from app.email_account_connector import ConnectorPriority
    monkeypatch.setattr(worker, '_build_agent_orchestrator', lambda *args, **kwargs: object())
    monkeypatch.setattr('app.agent_runtime_production.build_production_routed_codex_execution', lambda **kwargs: object())
    leases, registries = [], []
    class Connector:
        def checkout(self, mode, priority, **kwargs):
            leases.append(('checkout', mode, priority))
            return object(), object()
        def checkin(self, session, *, keep):
            leases.append(('checkin', keep))
    connector = Connector()
    registry = SimpleNamespace(connector_for=lambda account: connector)
    def build_registry(store):
        registries.append(store)
        return registry
    monkeypatch.setattr(worker, '_build_email_connector_registry', build_registry)
    settings = SimpleNamespace(db_path=tmp_path / 'worker.sqlite3', workspace=tmp_path, dry_run=False)
    bootstrap = worker.build_email_worker_dependencies(settings)
    deps = bootstrap.build_dependencies(({'account_id': 'a', 'enabled': True},),
        SimpleNamespace(loaded=SimpleNamespace(model_id='test', classifier=object()), tick=lambda: None))
    callbacks = [getattr(deps, name).keywords['retire_original'] for name in (
        'run_classification_once', 'run_direct_actions_once', 'finalize_task', 'finalize_direct_unsubscribe_task')]
    assert all(callback is callbacks[0] for callback in callbacks)
    assert isinstance(callbacks[0], partial) and callbacks[0].func.__self__ is bootstrap.email_store
    source_reader = callbacks[0].keywords['source_reader']
    assert source_reader.store is bootstrap.email_store
    source = source_reader.source_factory({'account_id': 'a'})
    source.logout()
    assert leases == [('checkout', 'readonly', ConnectorPriority.LOW), ('checkin', False)]
    assert registries == [bootstrap.email_store]
    historical_callbacks = []
    monkeypatch.setattr(worker, 'reconcile_historical_operations',
        lambda *args, **kwargs: historical_callbacks.append(kwargs['retire_original']) or {'repaired': 0})
    assert deps.reconcile_historical_operations_once() == {'repaired': 0}
    assert historical_callbacks == [callbacks[0]]


def test_synchronous_historical_receipt_reuse_retries_input_cleanup_without_reexecution(monkeypatch):
    persisted = {'id': 101, 'stable_message_identity': 'stable',
                 'action_plan': {'action_plan_id': 'plan', 'actions': ['mark_read']}}
    monkeypatch.setattr(worker, 'persist_model_primary_classification',
        lambda *args, **kwargs: SimpleNamespace(persisted=persisted))
    store = SimpleNamespace(direct_action_ids_for_plan=lambda plan: ('action',),
        begin_historical_operation=lambda **kwargs: None,
        direct_action_statuses_for_plan=lambda plan: {'action': 'done'},
        get_classification_by_stable_identity=lambda identity: persisted,
        direct_action_receipts_for_plan=lambda plan: [{'action_type': 'mark_read'}])
    retired = []
    result = worker.execute_historical_model_actions(store,
        lambda account: pytest.fail('durable actions must not execute again'), object(),
        message={'accountId': 'a'}, prediction=SimpleNamespace(category='work', category_probability=0.9, important=False),
        context=object(), model_id='model', model_text='input', unsubscribe_entries=(),
        read_after=lambda: SimpleNamespace(is_read=True), retire_original=retired.append)
    assert result.outcome == 'read_preserved'
    assert retired == [101]
