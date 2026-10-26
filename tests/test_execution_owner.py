"""The Consumer Run is the one current owner of execution progress."""


def test_runtime_keeps_execution_identity_on_consumer_run(tmp_path):
    from app.store import AutoReplyStore
    from tests.test_reviewed_candidate_store import _reviewed
    store = AutoReplyStore(tmp_path / 'runtime.sqlite3')
    task, candidate, review, _ = _reviewed(store, options=False)
    execution = store.claim_candidate_execution(candidate['id'], review['id'], 'owner', 60)
    assert execution['id'] == candidate['id']
    assert store.get_candidate_execution(candidate['id']) == execution
    with store._connect() as connection:
        assert connection.execute("select count(*) from actions where record_type='candidate_executions'").fetchone()[0] == 0
        run = connection.execute(
            "select id,task_id,role from runs where id=?", (execution['id'],),
        ).fetchone()
        assert tuple(run) == (execution['id'], task.id, 'consumer')
        adopted = connection.execute(
            "select consumer_run_id from run_adopted_results where consumer_run_id=?",
            (execution['id'],),
        ).fetchone()
        assert adopted['consumer_run_id'] == execution['id'] == candidate['id']
        progress = connection.execute(
            "select consumer_run_id,approved_audit_run_id,selection_id,status,"
            "lease_owner,lease_expires_at,result_json from run_execution_progress"
        ).fetchone()
        assert tuple(progress[:5]) == (
            candidate['id'], review['audit_run_id'], None, 'running', 'owner',
        )
        assert progress['lease_expires_at'] == execution['lease_expires_at']
        assert progress['result_json'] == '{}'
        assert connection.execute(
            "select count(*) from actions where record_type='candidate_execution_alias'"
        ).fetchone()[0] == 0
    renewed = store.renew_candidate_execution_claim(execution['id'], 'owner', 120)
    assert renewed['id'] == execution['id'] == candidate['id']
    assert renewed['audit_run_id'] == review['audit_run_id']
    assert renewed['lease_expires_at'] >= execution['lease_expires_at']
    attempt = store.begin_candidate_action(execution['id'], 'owner', 0, 'receipt')
    assert attempt['execution_id'] == execution['id']
    store.record_candidate_external_action(execution['id'], 'owner', 0, 'receipt', 'send', {'user_id': 'x'}, {'receipt': 'substitute'})
    with store._connect() as connection:
        owned = connection.execute(
            "select execution_run_id from action_receipts "
            "where action_index=0 and execution_run_id=?",
            (execution['id'],),
        ).fetchone()
        assert owned['execution_run_id'] == execution['id']
    finished = store.finish_candidate_execution(execution['id'], 'owner', 'done', {'done': True})
    assert finished['id'] == execution['id']
    assert finished['status'] == 'done'
    assert finished['result_json'] == '{"done":true}'
    assert finished['lease_owner'] == ''
    assert finished['lease_expires_at'] == ''
    assert store.claim_candidate_execution(candidate['id'], review['id'], 'owner', 60) is None
    with store._connect() as connection:
        assert connection.execute(
            "select count(*) from actions where record_type='candidate_execution_alias'"
        ).fetchone()[0] == 0
        assert connection.execute('pragma foreign_key_check').fetchall() == []


