"""Current meeting-claim identity with a bounded SQLite program, no real timeouts."""

from app.action_storage import task_delivery_projection
from app.current_schema_manifest import schema_objects
from app.store import AutoReplyStore, _close_idle_connections
from app.task_entity_storage import update_task_cas


NOW = '2026-10-29T12:00:00+00:00'


def job(store, meeting_id):
    return store.upsert_meeting_alignment_job(
        meeting_id=meeting_id, title='Synthetic meeting', source_json='{}',
        participants_json='[]', ended_at=NOW, eligible_at=NOW, status='ready_to_send',
    )


def test_meeting_claim_sql_program_is_bounded(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / 'worker.sqlite3')
    job(store, 'synthetic')
    _close_idle_connections()
    statements = []
    original = store._open_connection

    def traced(*args, **kwargs):
        db = original(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, '_open_connection', traced)
    assert len(store.claim_ready_to_send_meeting_alignment_jobs(limit=1, now=NOW)) == 1
    writes = {sql for sql in statements if sql.lower().startswith('insert into')
              and 'td_claim_job_id' in sql}
    assert len(writes) == 1
    with store._connect() as db:
        # Stream EXPLAIN: collecting 14 million rows would itself exceed the
        # fixed memory budget. This bound catches the untargeted-UPSERT blowup
        # without an unstable wall-clock assertion or removing any constraint.
        original_text_factory = db.text_factory
        try:
            # EXPLAIN's p4 may contain a JSONB literal, not UTF-8 text.
            db.text_factory = bytes
            instructions = sum(1 for _ in db.execute('explain ' + writes.pop()))
        finally:
            db.text_factory = original_text_factory
    assert instructions < 1_000_000


def test_both_claim_paths_keep_one_owner_and_replace_only_its_token(tmp_path):
    store = AutoReplyStore(tmp_path / 'worker.sqlite3')
    first_id, second_id = job(store, 'first'), job(store, 'second')
    first, second = store.claim_ready_to_send_meeting_alignment_jobs(limit=2, now=NOW)
    assert (first.id, second.id) == (first_id, second_id)
    with store._connect() as db:
        schema = schema_objects(db)
        before = {row['td_claim_job_id']: dict(row) for row in db.execute(
            "select id,td_claim_job_id,payload_json from actions "
            "where record_type='meeting_alignment_delivery_claims'")}
        assert update_task_cas(db, task_id=first_id,
            expected={'locked_at': first.locked_at}, changes={'locked_at': None}) is not None
    replacement = store.claim_ready_to_send_meeting_alignment_job(first_id, now=NOW)
    assert replacement is not None
    assert replacement.delivery_claim_token != first.delivery_claim_token
    with store._connect() as db:
        after = {row['td_claim_job_id']: dict(row) for row in db.execute(
            "select id,td_claim_job_id,payload_json from actions "
            "where record_type='meeting_alignment_delivery_claims'")}
        assert after.keys() == before.keys()
        assert after[first_id]['id'] == before[first_id]['id']
        assert after[second_id] == before[second_id]
        row = db.execute('select * from (' + task_delivery_projection(
            'meeting_alignment_delivery_claims') + ') where job_id=?', (first_id,)).fetchone()
        assert row['claim_token'] == replacement.delivery_claim_token
        assert schema_objects(db) == schema
        assert db.execute('pragma foreign_key_check').fetchall() == []
