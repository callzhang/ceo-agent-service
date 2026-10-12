"""Assertions for the current unified core run schema."""


def assert_no_core_native_storage(db):
    retired = ('agent_runs', 'agent_runtime_attempts', 'agent_run_state_events', 'agent_run_events')
    assert db.execute(
        "select name from sqlite_master where name in (?,?,?,?)", retired,
    ).fetchall() == []
    columns = {row[1] for row in db.execute('pragma table_info(runs)')}
    assert {'final_result_json', 'result_envelope_json', 'tool_events_json'}.isdisjoint(columns)
