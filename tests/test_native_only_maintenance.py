import json
import sqlite3
from contextlib import closing

import pytest

from app import storage_maintenance
from app.store import AutoReplyStore
from tests.test_agent_turn_store import _task, _claim_consumer
from app.run_storage import AGENT_RUN_SQL, MEETING_RUN_SQL, OKR_RUN_SQL, RUNTIME_ATTEMPT_SQL, TASK_AGENT_RUN_SQL, WORKBENCH_EVENT_SQL, insert_run
from app.task_storage import MEETING_JOB_SQL, REPLY_TASK_SQL, WORKBENCH_TURN_SQL, insert_task, task_physical_id
from tests.run_reader_fixtures import assert_no_core_native_storage

from app.action_storage import CANDIDATE_REVIEW_SQL, REVIEW_CANDIDATE_SQL, REPLY_ATTEMPT_COMMON_SQL, update_action


def test_native_only_cleanup_removes_missing_native_copies_preserves_service_state(tmp_path):
    store = AutoReplyStore(tmp_path / 'service.sqlite3')
    task = _task(store)
    run = _claim_consumer(store, task).run
    runtime = store.claim_agent_runtime_attempt(run.id, 'codex_oauth', 'codex_cli', 'local_oauth', 'gpt-5.5')
    from tests.test_store import _attempt
    attempt_id = _attempt(store, trigger='cleanup-regression', status='sent')
    marker = '[{"tool":"audit_review","result":"queued"}]'
    with sqlite3.connect(store.path) as db:
        db.execute('pragma journal_mode=wal')
        db.execute("update runs set status='completed',session_id='missing-session' where record_type='agent_runs' and legacy_key=?", (str(run.id),))
        update_action(db, 'reply_attempts', attempt_id, {'audit_tool_events_json': marker})
        insert_run(db, 'okr_review_runs', {'request_id': 1})
        job_id = insert_task(db, 'meeting_alignment_jobs', {'meeting_id': 'cleanup-meeting', 'final_message': 'adopted business output'}).lastrowid
        insert_run(db, 'meeting_alignment_runs', {'task_id': job_id, 'status': 'sent'})
        index_id = db.execute("insert into codex_session_search_index(session_id,title) values('missing-session','service title')").lastrowid
        db.execute("insert into codex_session_search_fts(rowid,title) values(?,'service title')", (index_id,))
        cache_columns = {row[1] for row in db.execute('pragma table_info(codex_session_search_index)')}
        assert not cache_columns.intersection({
            'summary_text', 'fts_text', 'embedding_json', 'embedding_model', 'embedding_updated_at',
        })
        assert db.execute("select count(*) from codex_session_search_fts where codex_session_search_fts match 'service'").fetchone()[0] == 1
        before = db.execute(f'select id,status,execution_generation from ({REPLY_TASK_SQL})').fetchall()
    counts = storage_maintenance.compact_native_duplicates(store.path)
    assert counts['event_runs_cleared'] == 0
    assert counts['payload_fields_cleared'] == 0
    with sqlite3.connect(store.path) as db:
        assert_no_core_native_storage(db)
        assert db.execute(f'select audit_tool_events_json,send_status from ({REPLY_ATTEMPT_COMMON_SQL}) where id=?',(attempt_id,)).fetchone() == (marker,'sent')
        assert db.execute(f'select id,status,execution_generation from ({REPLY_TASK_SQL})').fetchall() == before
        assert db.execute(f'select codex_session_id,status from ({AGENT_RUN_SQL}) where id=?',(run.id,)).fetchone() == ('missing-session','completed')
        assert db.execute(f'select result_envelope_json,runtime_kind from ({RUNTIME_ATTEMPT_SQL}) where id=?', (runtime.id,)).fetchone() == ('','codex_cli')
        assert db.execute(f'select audit_tool_events_json from ({OKR_RUN_SQL})').fetchone()[0] == '[]'
        assert db.execute(f'select audit_tool_events_json,status from ({MEETING_RUN_SQL})').fetchone() == ('[]','sent')
        assert db.execute(f'select final_message from ({MEETING_JOB_SQL}) where id=?', (job_id,)).fetchone()[0] == 'adopted business output'
        assert db.execute('select session_id,title from codex_session_search_index').fetchone() == ('missing-session','service title')
        assert {row[1] for row in db.execute('pragma table_info(codex_session_search_index)')} == cache_columns
        assert db.execute("select count(*) from codex_session_search_fts where codex_session_search_fts match 'oldtrajectorysearch'").fetchone()[0] == 0
        assert db.execute("select count(*) from codex_session_search_fts where codex_session_search_fts match 'service'").fetchone()[0] == 1
        assert db.execute('pragma quick_check').fetchone()[0] == 'ok'
    assert storage_maintenance.compact_native_duplicates(store.path)['payload_bytes_removed'] == 0
    wal = store.path.with_name(store.path.name + '-wal')
    assert not wal.exists() or wal.stat().st_size == 0


def test_cleanup_reports_unreclaimed_wal_when_reader_holds_old_snapshot(tmp_path, monkeypatch):
    from app.workbench.store import WorkbenchStore
    store = WorkbenchStore(tmp_path / 'service.sqlite3')
    task = store.create_task(title='Held reader', runtime_kind='codex')
    turn = store.create_turn(task.id, user_text='Input', client_request_id='held-reader')
    with store._connect() as db:
        turn_id = task_physical_id(db, 'workbench_turns', turn.id)
    connect = sqlite3.connect
    with closing(connect(store.path)) as writer:
        writer.execute('pragma journal_mode=wal')
        writer.execute('update tasks set error_detail=? where record_type=\'workbench_turns\' and id=?',
                       ('old-trajectory-body', turn_id))
        writer.commit()
        with closing(connect(store.path)) as reader:
            reader.execute('begin')
            assert 'old-trajectory-body' in reader.execute(
                'select error_detail from tasks where record_type=\'workbench_turns\' and id=?', (turn_id,)
            ).fetchone()[0]
            monkeypatch.setattr(storage_maintenance.sqlite3, 'connect',
                                lambda path, **kwargs: connect(path, timeout=0.01))
            with pytest.raises(RuntimeError, match='WAL reclamation incomplete'):
                storage_maintenance.compact_native_duplicates(store.path)
            assert reader.execute('select error_detail from tasks where record_type=\'workbench_turns\' and id=?',
                                  (turn_id,)).fetchone()[0] == 'old-trajectory-body'
    storage_maintenance.compact_native_duplicates(store.path)
    wal = store.path.with_name(store.path.name + '-wal')
    assert not wal.exists() or wal.stat().st_size == 0


def test_cleanup_clears_standalone_bodies_and_workbench_keeps_only_source_references(tmp_path):
    from app.workbench.store import WorkbenchStore

    store = WorkbenchStore(tmp_path / 'service.sqlite3')
    task = store.create_task(title='Service task', runtime_kind='codex')
    turn = store.create_turn(task.id, user_text='Service input', client_request_id='cleanup')
    from tests.test_reviewed_candidate_store import _reviewed
    _, candidate, review, _ = _reviewed(store)
    bodies = [
        ('tool_started', {'tool_call_id': 'b', 'command': 'private b'}),
        ('tool_started', {'tool_call_id': 'a', 'command': 'private a'}),
        ('tool_completed', {'tool_call_id': 'a', 'output': 'private output a'}),
        ('tool_completed', {'tool_call_id': 'b', 'output': 'private output b'}),
        ('tool_started', {'tool_call_id': 'unfinished', 'arguments': 'private arguments'}),
        ('text_delta', {'text': 'private text'}),
        ('text_delta', {'native_ordinal': 2}),
        ('thinking_summary', {'text': 'private reasoning'}),
        ('file_changed', {'path': 'private path'}),
        ('status_changed', {'status': 'completed'}),
    ]
    with sqlite3.connect(store.path) as db:
        job = insert_task(db, 'meeting_alignment_jobs', {'meeting_id': 'cleanup', 'decision_json': '{"adopted":true}', 'final_message': 'Business message'}).lastrowid
        insert_run(db, 'meeting_alignment_runs', {'task_id': job, 'status': 'sent'})
        insert_run(db, 'task_agent_runs', {'summary_input_id': 1, 'projection_json': '{"applied":true}'})
        insert_run(db, 'okr_review_runs', {'request_id': 1})
        physical_turn = task_physical_id(db, 'workbench_turns', turn.id)
        assert 'final_text' not in {row[1] for row in db.execute('pragma table_info(tasks)')}
        db.execute("update tasks set status='failed', error_detail='private provider stderr', error_code='runtime_failed' where record_type='workbench_turns' and id=?", (physical_turn,))
        for sequence, (kind, payload) in enumerate(bodies, 100):
            insert_run(db, 'workbench_events', {
                'task_id': physical_turn, 'sequence': sequence,
                'event_type': kind, 'payload_json': json.dumps(payload),
            })
        before = db.execute(f'select id,turn_id,sequence,event_type,created_at from ({WORKBENCH_EVENT_SQL})').fetchall()
        candidate_before = db.execute(f'select candidate_json,candidate_digest from ({REVIEW_CANDIDATE_SQL}) where id=?', (candidate['id'],)).fetchone()
        review_before = db.execute(f'select result_json from ({CANDIDATE_REVIEW_SQL}) where id=?', (review['id'],)).fetchone()
    storage_maintenance.compact_native_duplicates(store.path)
    with sqlite3.connect(store.path) as db:
        assert db.execute(f'select candidate_json,candidate_digest from ({REVIEW_CANDIDATE_SQL}) where id=?', (candidate['id'],)).fetchone() == candidate_before
        assert db.execute(f'select result_json from ({CANDIDATE_REVIEW_SQL}) where id=?', (review['id'],)).fetchone() == review_before
        assert db.execute(f'select decision_json,audit_summary,audit_tool_events_json from ({MEETING_RUN_SQL})').fetchone() == ('{}','','[]')
        assert db.execute(f'select decision_json,audit_summary,memory_recall_used,projection_json from ({TASK_AGENT_RUN_SQL})').fetchone() == ('{}','',0,'{"applied":true}')
        assert db.execute(f'select envelope_json,audit_summary from ({OKR_RUN_SQL})').fetchone() == ('{}','')
        assert db.execute(f'select decision_json,final_message from ({MEETING_JOB_SQL})').fetchone() == ('{"adopted":true}','Business message')
        assert db.execute(f'select status,final_text,error_detail,error_code,user_text from ({WORKBENCH_TURN_SQL}) where id=?',(turn.id,)).fetchone() == ('failed','','','runtime_failed','Service input')
        assert db.execute(f'select id,turn_id,sequence,event_type,created_at from ({WORKBENCH_EVENT_SQL})').fetchall() == before
        payloads = [json.loads(row[0]) for row in db.execute(f'select payload_json from ({WORKBENCH_EVENT_SQL}) where turn_id=? and sequence>=100 order by sequence',(turn.id,))]
        assert payloads == [{'native_ordinal':2},{'native_ordinal':1},{'native_ordinal':1},{'native_ordinal':2},{},{},{'native_ordinal':2},{},{},{'status':'completed'}]
        assert db.execute('pragma quick_check').fetchone()[0] == 'ok'
    assert storage_maintenance.compact_native_duplicates(store.path)['payload_bytes_removed'] == 0
