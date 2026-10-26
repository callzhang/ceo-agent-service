"""A plan owns execution; immutable aliases retain independent public/physical IDs."""



from app import action_storage as actions
from tests.support.historical_action_storage import db as db


SOURCE_HASHES = {
    'actual_no_memory_fk': '77ac6dff94126976212d1cd7bfda3b36531e4ee8a02839a9a73f88bee85401d4',
    'fresh_memory_fk': 'f9ab43a6874aba1cc9fc8d6c19770a4834c6374850e29630a905dcc91379c3be',
}


def test_runtime_keeps_execution_identity_and_mutates_only_plan(tmp_path):
    from app.store import AutoReplyStore
    from tests.test_reviewed_candidate_store import _reviewed
    store = AutoReplyStore(tmp_path / 'runtime.sqlite3')
    _, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'owner', 60)
    assert store.get_candidate_execution(candidate['id']) == execution
    with store._connect() as connection:
        assert connection.execute("select count(*) from actions where record_type='candidate_executions'").fetchone()[0] == 0
        alias = tuple(connection.execute("select id,legacy_key,candidate_id,payload_json,status,result_json,created_at,updated_at from actions where record_type='candidate_execution_alias'").fetchone())
        plan = connection.execute("select id,execution_public_id,execution_review_id,execution_status,execution_lease_owner from actions where record_type='review_candidates'").fetchone()
        assert alias[0] != plan[0]
        assert alias[1:3] == (str(execution['id']), plan[0])
        assert alias[4:] == (None,) * 4
        assert tuple(plan[1:]) == (execution['id'], actions.action_physical_id(connection, 'candidate_reviews', review['id']), 'running', 'owner')
    renewed = store.renew_candidate_execution_claim(execution['id'], 'owner', 120)
    assert renewed['lease_expires_at'] >= execution['lease_expires_at']
    attempt = store.begin_candidate_action(execution['id'], 'owner', 0, 'receipt')
    assert attempt['execution_id'] == execution['id']
    store.record_candidate_external_action(execution['id'], 'owner', 0, 'receipt', 'send', {'user_id': 'x'}, {'receipt': 'substitute'})
    finished = store.finish_candidate_execution(execution['id'], 'owner', 'done', {'done': True})
    assert finished['id'] == execution['id'] and finished['status'] == 'done'
    assert store.claim_candidate_execution(candidate['id'], review['id'], 'owner', 60) is None
    with store._connect() as connection:
        assert tuple(connection.execute("select id,legacy_key,candidate_id,payload_json,status,result_json,created_at,updated_at from actions where record_type='candidate_execution_alias'").fetchone()) == alias
        assert connection.execute('pragma foreign_key_check').fetchall() == []


