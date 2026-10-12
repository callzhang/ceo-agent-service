"""Assertions for the current unified core run schema."""


def assert_no_core_native_storage(db):
    retired = ('agent_runs', 'agent_runtime_attempts', 'agent_run_state_events', 'agent_run_events')
    assert db.execute(
        "select name from sqlite_master where name in (?,?,?,?)", retired,
    ).fetchall() == []
    columns = {row[1] for row in db.execute('pragma table_info(runs)')}
    assert {'final_result_json', 'result_envelope_json', 'tool_events_json'}.isdisjoint(columns)


def insert_native_run_fixture(db, values, *, result, tmp_path, monkeypatch):
    """Persist a logical reference and put the fixture's raw final in native JSONL."""
    import json
    from app.run_storage import insert_run

    cursor = insert_run(db, 'agent_runs', values)
    session = f'phase4-fixture-{cursor.lastrowid}'
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
        "update runs set session_id=?, transcript_start=0, transcript_end=1 "
        "where record_type='agent_runs' and cast(legacy_key as integer)=?",
        (session, cursor.lastrowid),
    )
    return cursor
