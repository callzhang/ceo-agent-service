"""Assertions for the current unified core run schema."""


def assert_no_core_native_storage(db):
    retired = ('agent_runs', 'agent_runtime_attempts', 'agent_run_state_events', 'agent_run_events')
    assert db.execute(
        "select name from sqlite_master where name in (?,?,?,?)", retired,
    ).fetchall() == []
    columns = {row[1] for row in db.execute('pragma table_info(runs)')}
    assert {'final_result_json', 'result_envelope_json', 'tool_events_json'}.isdisjoint(columns)


_RUN_DEFAULTS = {
    'proposal_revision': 0, 'turn_attempt': 0, 'operation_id': '', 'status': 'completed',
    'session_id': '', 'transcript_start': 0, 'transcript_end': 0, 'structured_error_json': '',
    'error': '', 'lease_owner': '', 'lease_expires_at': '', 'started_at': '',
    'completed_at': '', 'updated_at': '',
}


def insert_native_run_fixture(db, values, *, result, tmp_path, monkeypatch):
    """Persist a native Run and put the fixture's raw final in native JSONL."""
    import json
    from types import SimpleNamespace
    from app.run_entity_storage import create_run

    run_id = create_run(db, values={**_RUN_DEFAULTS, **values})['id']
    session = f'phase4-fixture-{run_id}'
    native = tmp_path / f'{session}.jsonl'
    native.write_text(json.dumps({'type': 'response_item', 'payload': {
        'type': 'message', 'role': 'assistant', 'content': [
            {'type': 'output_text', 'text': result}
        ],
    }}) + '\n')
    monkeypatch.setattr(
        'app.native_trajectory.find_codex_session_path',
        lambda session_id, **kwargs: tmp_path / f'{session_id}.jsonl',
    )
    db.execute(
        "update runs set session_id=?, transcript_start=0, transcript_end=1 where id=?",
        (session, run_id),
    )
    return SimpleNamespace(lastrowid=run_id)
