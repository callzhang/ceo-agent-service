"""Confirmed provider bodies have one owner; inverse uses current receipts."""
import pytest

from app import action_storage as actions
from app.model_action_schema import migrate_selection_references
from tests.test_model_action_schema import _add, _seed, _snapshot
from tests.test_provenance_vector_storage import db as db


SOURCE_HASHES = {
    'actual_no_memory_fk': '317eb42c3c0a24e2f210d5ba52d11888b3c1a1cf74217979e3bce2a240893f04',
    'fresh_memory_fk': 'ad99aad17823c90a058947ba61d261e6da6ffbf9c9290a8a67b8aa825d9190ad',
}
BODY = ' { "receipt": "exact provider bytes α" } '


def _receipt(db, key, body=BODY):
    actions.insert_action(db, 'external_action_results', dict(external_action_key=key,
        business_object_key='object', action_identity=key, operation='send',
        target_identifiers_json='{}', provider_result_json=body, result_digest='digest',
        first_agent_run_id=2))


def _attempt(db, physical, public, status, body, key, index):
    _add(db, 'candidate_action_attempts', physical, public,
         dict(action_index=index, external_action_key=key, status=status,
              result_json=body, created_at='created', updated_at='updated'),
         references={'execution_id': 601}, target=True)


def _source(db):
    _seed(db)
    migrate_selection_references(db)
    _receipt(db, 'equal')
    _receipt(db, 'distinct')
    _attempt(db, 1001, 71, 'verified', BODY, 'equal', 0)
    _attempt(db, 1002, 72, 'verified', '{}', 'distinct', 1)
    _attempt(db, 1003, 73, 'failed', ' { "error": 1 } ', 'failure', 2)
    _attempt(db, 1004, 74, 'uncertain', '{"unknown":true}', 'uncertain', 3)
    _attempt(db, 1005, 75, 'verified', '{}', 'missing-receipt', 4)


def _stored_attempt_outcome(db, attempt_id):
    row = db.execute("select outcome_json from action_receipts where id=?", (attempt_id,)).fetchone()
    return None if row is None else row[0]


def test_writer_stores_reference_but_public_reader_preserves_body(tmp_path):
    from app.store import AutoReplyStore
    from tests.test_reviewed_candidate_store import _reviewed
    store = AutoReplyStore(tmp_path/'runtime.sqlite3')
    task, candidate, review, body = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'worker', 60)
    attempt = store.begin_candidate_action(execution['id'], 'worker', 0, 'receipt')
    receipt = store.record_candidate_external_action(execution['id'], 'worker', 0,
        'receipt', 'send', {'user_id': 'x'}, {'receipt': 'substitute'})
    with store._connect() as connection:
        assert _stored_attempt_outcome(connection, attempt['id']) is None
    assert store.list_candidate_action_attempts(execution['id'])[0]['result_json'] == receipt['provider_result_json']
    assert store.get_prior_candidate_action_attempt('receipt', execution['id'] + 1)['result_json'] == receipt['provider_result_json']
    assert store.list_verified_candidate_actions(task.id)[0]['result_json'] == receipt['provider_result_json']
    saved = store.record_candidate_action_outcome(execution['id'], 'worker', 0, 'verified', {})
    assert saved['result_json'] == '{}'
    with store._connect() as connection:
        assert _stored_attempt_outcome(connection, attempt['id']) == '{}'
    saved = store.record_candidate_action_outcome(execution['id'], 'worker', 0, 'verified', {'receipt': 'substitute'})
    assert saved['result_json'] == receipt['provider_result_json']
    with store._connect() as connection:
        assert _stored_attempt_outcome(connection, attempt['id']) is None
    assert store.list_candidate_action_attempts(execution['id'])[0]['result_json'] == receipt['provider_result_json']
    from types import SimpleNamespace
    from app.attempt_what_happened import _verified_system_receipts
    assert _verified_system_receipts([SimpleNamespace(reply_task_id=task.id,
        execution_generation=task.execution_generation)], store=store)
    assert attempt['id'] == saved['id']


def test_receipt_correction_preserves_other_attempts_original_meaning(tmp_path):
    from app.store import AutoReplyStore
    from tests.test_reviewed_candidate_store import _reviewed
    store = AutoReplyStore(tmp_path/'corrected.sqlite3')
    task, candidate, review, body = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'worker', 60)
    attempt = store.begin_candidate_action(execution['id'], 'worker', 0, 'receipt')
    old = {'provider_result': {'message': 'same'}, 'verification': {'state': 'ambiguous'}}
    receipt = store.record_candidate_external_action(execution['id'], 'worker', 0,
        'receipt', 'send', {'user_id': 'x'}, old)
    # Another approved execution has an exact verified reference to this body.
    later = store.claim_agent_run(task.id, task.execution_generation,
        role='consumer', proposal_revision=1, turn_attempt=0,
        parent_agent_run_id=review['audit_run_id'], operation_id='', owner='later').run
    store.complete_agent_run(later.id, body, owner='later')
    later_candidate = store.persist_review_candidate(task, later, body)
    audit = store.claim_agent_run(task.id, task.execution_generation,
        role='audit', proposal_revision=1, turn_attempt=0, parent_agent_run_id=later.id,
        operation_id='later-audit', owner='audit').run
    verdict = {'outcome': 'approve', 'candidate_digest': later_candidate['candidate_digest']}
    store.complete_agent_run(audit.id, verdict, owner='audit')
    later_review = store.record_candidate_review(later_candidate['id'], audit.id, verdict)
    other_execution = store.claim_candidate_execution(later_candidate['id'], later_review['id'], 'later', 60)
    store.begin_candidate_action(other_execution['id'], 'later', 0, 'receipt')
    other = store.record_candidate_action_outcome(other_execution['id'], 'later', 0,
        'verified', old)
    new = {'provider_result': {'message': 'same'}, 'verification': {'state': 'sent'}}
    changed = store.record_candidate_external_action(execution['id'], 'worker', 0,
        'receipt', 'send', {'user_id': 'x'}, new)
    current_row = store.list_candidate_action_attempts(execution['id'])[0]
    other_row = store.list_candidate_action_attempts(other_execution['id'])[0]
    assert current_row['id'] == attempt['id']
    assert current_row['result_json'] == changed['provider_result_json']
    assert other_row['id'] == other['id']
    assert other_row['result_json'] == receipt['provider_result_json']
    with store._connect() as connection:
        assert _stored_attempt_outcome(connection, attempt['id']) is None
        assert _stored_attempt_outcome(connection, other['id']) is None


def test_unreferenced_correction_wrapper_is_not_retained_as_history(tmp_path):
    from app.store import AutoReplyStore
    from tests.test_reviewed_candidate_store import _reviewed

    store = AutoReplyStore(tmp_path/'wrapper-retention.sqlite3')
    _, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'worker', 60)
    attempt = store.begin_candidate_action(execution['id'], 'worker', 0, 'receipt')
    old = {'provider_result': {'message': 'same'}, 'verification': {'state': 'ambiguous'}}
    store.record_candidate_external_action(execution['id'], 'worker', 0,
        'receipt', 'send', {'user_id': 'x'}, old)
    first = store.get_candidate_external_action('receipt')
    corrected = {'provider_result': {'message': 'same'}, 'verification': {'state': 'sent'}}

    final = store.record_candidate_external_action(execution['id'], 'worker', 0,
        'receipt', 'send', {'user_id': 'x'}, corrected)

    assert final['provider_result_json'] == '{"provider_result":{"message":"same"},"verification":{"state":"sent"}}'
    assert store.list_candidate_action_attempts(execution['id'])[0]['id'] == attempt['id']
    assert store.list_candidate_action_attempts(execution['id'])[0]['result_json'] == final['provider_result_json']
    assert store.get_candidate_external_action('receipt')['created_at'] == first['created_at']
    assert store.get_candidate_external_action('receipt')['first_agent_run_id'] == first['first_agent_run_id']
    with store._connect() as connection:
        action_id = connection.execute(
            "select id from actions where record_type='action' and action_external_action_key='receipt'"
        ).fetchone()[0]
        body_rows = connection.execute(
            "select id,provider_result_json from action_receipts "
            "where action_id=? and provider_result_json is not null order by id",
            (action_id,),
        ).fetchall()
        assert [row['provider_result_json'] for row in body_rows] == [final['provider_result_json']]


def test_receipt_correction_rolls_back_reference_materialization(tmp_path, monkeypatch):
    from app import shared_action_lifecycle
    from app.store import AutoReplyStore
    from tests.test_reviewed_candidate_store import _reviewed
    store = AutoReplyStore(tmp_path/'atomic.sqlite3')
    _, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'worker', 60)
    store.begin_candidate_action(execution['id'], 'worker', 0, 'receipt')
    old = {'provider_result': {'message': 'same'}, 'verification': {'state': 'ambiguous'}}
    store.record_candidate_external_action(execution['id'], 'worker', 0,
        'receipt', 'send', {'user_id': 'x'}, old)
    with store._connect() as connection:
        before = _snapshot(connection)
    reached_after_receipt_write = False
    def fail_before_observation_completion(db, **kwargs):
        nonlocal reached_after_receipt_write
        current = actions.EXTERNAL_ACTION_RESULT_SQL
        row = db.execute(
            f"select provider_result_json from ({current}) where external_action_key=?",
            ('receipt',),
        ).fetchone()
        assert row is not None
        assert row['provider_result_json'] == (
            '{"provider_result":{"message":"same"},"verification":{"state":"sent"}}'
        )
        reached_after_receipt_write = True
        raise RuntimeError('attempt assignment failed')
    monkeypatch.setattr(shared_action_lifecycle, 'record_reviewed_action_outcome',
                        fail_before_observation_completion)
    with pytest.raises(RuntimeError, match='assignment'):
        store.record_candidate_external_action(execution['id'], 'worker', 0,
            'receipt', 'send', {'user_id': 'x'},
            {'provider_result': {'message': 'same'}, 'verification': {'state': 'sent'}})
    assert reached_after_receipt_write
    with store._connect() as connection:
        assert _snapshot(connection) == before


@pytest.mark.parametrize('status', ['failed', 'uncertain', 'confirmed_no_effect'])
def test_nonverified_outcome_retains_body_even_when_equal_to_receipt(tmp_path, status):
    from app.store import AutoReplyStore
    from tests.test_reviewed_candidate_store import _reviewed
    store = AutoReplyStore(tmp_path/'local-fact.sqlite3')
    _, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'worker', 60)
    store.begin_candidate_action(execution['id'], 'worker', 0, 'receipt')
    store.record_candidate_external_action(execution['id'], 'worker', 0,
        'receipt', 'send', {'user_id': 'x'}, {'receipt': 'substitute'})
    saved = store.record_candidate_action_outcome(execution['id'], 'worker', 0, status, {'receipt': 'substitute'})
    assert saved['result_json'] == '{"receipt":"substitute"}'
    with store._connect() as connection:
        assert _stored_attempt_outcome(connection, saved['id']) == saved['result_json']


def test_prior_reconciliation_writes_one_owner_in_same_transaction(tmp_path):
    from app.store import AgentRole, AutoReplyStore
    from tests.test_reviewed_candidate_store import _reviewed
    store = AutoReplyStore(tmp_path/'prior.sqlite3')
    task, candidate, review, body = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'first', 60)
    prior = store.begin_candidate_action(execution['id'], 'first', 0, 'receipt')
    store.record_candidate_action_outcome(execution['id'], 'first', 0, 'failed',
        {'reason': 'reviewed_message_preparation_mismatch', 'independent': 'original failure'})
    store.finish_candidate_execution(execution['id'], 'first', 'failed', {})
    later = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.CONSUMER,
        proposal_revision=1, turn_attempt=0, parent_agent_run_id=review['audit_run_id'],
        operation_id='', owner='later').run
    store.complete_agent_run(later.id, body, owner='later')
    later_candidate = store.persist_review_candidate(task, later, body)
    audit = store.claim_agent_run(task.id, task.execution_generation, role=AgentRole.AUDIT,
        proposal_revision=1, turn_attempt=0, parent_agent_run_id=later.id,
        operation_id='later-audit', owner='audit').run
    verdict = {'outcome':'approve','candidate_digest':later_candidate['candidate_digest']}
    store.complete_agent_run(audit.id, verdict, owner='audit')
    later_review = store.record_candidate_review(later_candidate['id'], audit.id, verdict)
    current = store.claim_candidate_execution(later_candidate['id'], later_review['id'], 'later', 60)
    saved = store.record_prior_candidate_action_reconciliation(current['id'], 'later', 0,
        'receipt', 'verified', {'receipt':'readback substitute'})
    assert saved['id'] == prior['id']
    assert saved['result_json'] == '{"receipt":"readback substitute"}'
    with store._connect() as connection:
        assert _stored_attempt_outcome(connection, prior['id']) is None
        assert connection.execute(
            f"select count(*) from ({actions.EXTERNAL_ACTION_RESULT_SQL})"
        ).fetchone()[0] == 1


def test_prior_reconciliation_keeps_different_existing_canonical_local_to_real_attempt(tmp_path):
    from app.store import AgentRole, AutoReplyStore
    from tests.test_reviewed_candidate_store import _reviewed

    store = AutoReplyStore(tmp_path/'prior-local.sqlite3')
    task, candidate, review, body = _reviewed(store, options=False)
    first = store.claim_candidate_execution(candidate['id'], review['id'], 'first', 60)
    prior = store.begin_candidate_action(first['id'], 'first', 0, 'receipt')
    store.record_candidate_action_outcome(first['id'], 'first', 0, 'failed', {
        'reason': 'reviewed_message_preparation_mismatch',
        'independent': 'original failure',
    })
    store.finish_candidate_execution(first['id'], 'first', 'failed', {})

    later = store.claim_agent_run(task.id, task.execution_generation,
        role=AgentRole.CONSUMER, proposal_revision=1, turn_attempt=0,
        parent_agent_run_id=review['audit_run_id'], operation_id='', owner='later').run
    store.complete_agent_run(later.id, body, owner='later')
    later_candidate = store.persist_review_candidate(task, later, body)
    audit = store.claim_agent_run(task.id, task.execution_generation,
        role=AgentRole.AUDIT, proposal_revision=1, turn_attempt=0,
        parent_agent_run_id=later.id, operation_id='later-audit', owner='audit').run
    verdict = {'outcome': 'approve', 'candidate_digest': later_candidate['candidate_digest']}
    store.complete_agent_run(audit.id, verdict, owner='audit')
    later_review = store.record_candidate_review(later_candidate['id'], audit.id, verdict)
    current = store.claim_candidate_execution(later_candidate['id'], later_review['id'], 'later', 60)
    sent = store.record_completed_agent_message_delivery(
        agent_run_id=later_candidate['consumer_run_id'], external_action_key='receipt',
        business_object_key=task.business_object_key, action_identity='a', operation='send',
        target_identifiers={'user_id': 'x'}, conversation_id=task.conversation_id,
        trigger_message_id=task.trigger_message_id, reply_text='Delivered',
        provider_result={'provider_result': 'current', 'verification': {'state': 'sent'}},
    )

    reconciled = store.record_prior_candidate_action_reconciliation(current['id'], 'later', 0,
        'receipt', 'verified', {'provider_result': 'prior-readback'})

    assert reconciled['id'] == prior['id']
    assert reconciled['result_json'] == '{"provider_result":"prior-readback"}'
    with store._connect() as connection:
        row = connection.execute(
            f"select provider_result_json from ({actions.EXTERNAL_ACTION_RESULT_SQL}) where external_action_key=?",
            ('receipt',),
        ).fetchone()
        assert row[0] == sent.send_result_json
        persisted = connection.execute(
            f"select provider_receipt_id from ({actions.CANDIDATE_ACTION_ATTEMPT_SQL}) where id=?",
            (prior['id'],),
        ).fetchone()
        assert _stored_attempt_outcome(connection, prior['id']) == reconciled['result_json']
        assert persisted[0] is not None


@pytest.mark.parametrize('provider_body', ['{}', '{"receipt":"substitute"}'])
def test_existing_receipt_reuse_preserves_distinct_default_only(tmp_path, provider_body):
    from app.store import AutoReplyStore
    from tests.test_reviewed_candidate_store import _reviewed
    store = AutoReplyStore(tmp_path/'reuse.sqlite3')
    task, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'worker', 60)
    with store._immediate_write_transaction() as connection:
        actions.insert_action(connection, 'external_action_results', dict(external_action_key='receipt',
            business_object_key=task.business_object_key, action_identity='a', operation='send',
            target_identifiers_json='{"user_id":"x"}', provider_result_json=provider_body,
            result_digest='fixture', first_agent_run_id=candidate['consumer_run_id']))
    attempt = store.begin_candidate_action(execution['id'], 'worker', 0, 'receipt')
    assert attempt['status'] == 'verified'
    assert attempt['result_json'] == '{}'
    with store._connect() as connection:
        assert _stored_attempt_outcome(connection, attempt['id']) == (None if provider_body == '{}' else '{}')
    store.record_candidate_action_outcome(execution['id'], 'worker', 0, 'failed', {})
    assert store.begin_candidate_action(execution['id'], 'worker', 0, 'receipt')['result_json'] == '{}'
    with store._connect() as connection:
        assert _stored_attempt_outcome(connection, attempt['id']) == (None if provider_body == '{}' else '{}')
