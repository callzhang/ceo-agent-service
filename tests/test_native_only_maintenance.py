import json
import sqlite3
from contextlib import closing

import pytest

from app import storage_maintenance
from app.store import AutoReplyStore
from tests.test_agent_turn_store import _task, _claim_consumer


def test_native_only_cleanup_removes_missing_native_copies_preserves_service_state(tmp_path):
    store = AutoReplyStore(tmp_path / 'service.sqlite3')
    task = _task(store)
    run = _claim_consumer(store, task).run
    runtime = store.claim_agent_runtime_attempt(run.id, 'codex_oauth', 'codex_cli', 'local_oauth', 'gpt-5.5')
    from tests.test_store import _attempt
    attempt_id = _attempt(store, trigger='cleanup-regression', status='sent')
    marker = 'historical-tool-output-only-in-db'
    with sqlite3.connect(store.path) as db:
        db.execute('pragma journal_mode=wal')
        db.execute("update agent_runs set status='completed', final_result_json=?,codex_session_id='missing-session' where id=?", (json.dumps({'output': marker}), run.id))
        db.execute("insert into agent_run_events(agent_run_id,sequence,event_json) values(?,1,?)", (run.id,json.dumps({'type':'item.completed','item':{'aggregated_output':marker}})))
        db.execute('update reply_attempts set audit_tool_events_json=? where id=?', (json.dumps([{'output':marker}]),attempt_id))
        db.execute('update agent_runtime_attempts set result_envelope_json=? where id=?',
                   (json.dumps({'output': marker}), runtime.id))
        db.execute('insert into okr_review_runs(request_id,audit_tool_events_json) values(1,?)',
                   (json.dumps([{'output': marker}]),))
        job_id = db.execute("insert into meeting_alignment_jobs(meeting_id,final_message) values('cleanup-meeting','adopted business output')").lastrowid
        db.execute("insert into meeting_alignment_runs(job_id,status,audit_tool_events_json) values(?,'sent',?)",
                   (job_id, json.dumps([{'output': marker}])))
        index_id = db.execute("insert into codex_session_search_index(session_id,title,summary_text,fts_text,embedding_json,embedding_model,embedding_updated_at) values('missing-session','service title','oldtrajectorysearch','oldtrajectorysearch','[1,0]','old-model','2026-10-09')").lastrowid
        db.execute("insert into codex_session_search_fts(rowid,title,summary_text,fts_text) values(?,'service title','oldtrajectorysearch','oldtrajectorysearch')", (index_id,))
        assert db.execute("select count(*) from codex_session_search_fts where codex_session_search_fts match 'oldtrajectorysearch'").fetchone()[0] == 1
        before = db.execute('select id,status,execution_generation from reply_tasks').fetchall()
    counts = storage_maintenance.compact_native_duplicates(store.path)
    assert counts['event_runs_cleared'] == 1
    assert counts['payload_fields_cleared'] == 10
    with sqlite3.connect(store.path) as db:
        assert db.execute('select count(*) from agent_run_events').fetchone()[0] == 0
        assert db.execute('select final_result_json from agent_runs where id=?',(run.id,)).fetchone()[0] == ''
        assert db.execute('select audit_tool_events_json,send_status from reply_attempts where id=?',(attempt_id,)).fetchone() == ('[]','sent')
        assert db.execute('select id,status,execution_generation from reply_tasks').fetchall() == before
        assert db.execute('select codex_session_id,status from agent_runs where id=?',(run.id,)).fetchone() == ('missing-session','completed')
        assert db.execute('select result_envelope_json,runtime_kind from agent_runtime_attempts where id=?', (runtime.id,)).fetchone() == ('','codex_cli')
        assert db.execute('select audit_tool_events_json from okr_review_runs').fetchone()[0] == '[]'
        assert db.execute('select audit_tool_events_json,status from meeting_alignment_runs').fetchone() == ('[]','sent')
        assert db.execute('select final_message from meeting_alignment_jobs where id=?', (job_id,)).fetchone()[0] == 'adopted business output'
        assert db.execute('select title,summary_text,fts_text,embedding_json,embedding_model,embedding_updated_at from codex_session_search_index').fetchone() == ('service title','','','','','')
        assert db.execute("select count(*) from codex_session_search_fts where codex_session_search_fts match 'oldtrajectorysearch'").fetchone()[0] == 0
        assert db.execute("select count(*) from codex_session_search_fts where codex_session_search_fts match 'service'").fetchone()[0] == 1
        assert db.execute('pragma quick_check').fetchone()[0] == 'ok'
    assert storage_maintenance.compact_native_duplicates(store.path)['payload_bytes_removed'] == 0
    wal = store.path.with_name(store.path.name + '-wal')
    assert not wal.exists() or wal.stat().st_size == 0


def test_cleanup_reports_unreclaimed_wal_when_reader_holds_old_snapshot(tmp_path, monkeypatch):
    store = AutoReplyStore(tmp_path / 'service.sqlite3')
    run = _claim_consumer(store, _task(store)).run
    connect = sqlite3.connect
    with closing(connect(store.path)) as writer:
        writer.execute('pragma journal_mode=wal')
        writer.execute('update agent_runs set final_result_json=? where id=?',
                       (json.dumps({'output': 'old-trajectory-body'}), run.id))
        writer.commit()
        with closing(connect(store.path)) as reader:
            reader.execute('begin')
            assert 'old-trajectory-body' in reader.execute(
                'select final_result_json from agent_runs where id=?', (run.id,)
            ).fetchone()[0]
            monkeypatch.setattr(storage_maintenance.sqlite3, 'connect',
                                lambda path, **kwargs: connect(path, timeout=0.01))
            with pytest.raises(RuntimeError, match='WAL reclamation incomplete'):
                storage_maintenance.compact_native_duplicates(store.path)
            assert reader.execute('select final_result_json from agent_runs where id=?',
                                  (run.id,)).fetchone()[0] != ''
    storage_maintenance.compact_native_duplicates(store.path)
    wal = store.path.with_name(store.path.name + '-wal')
    assert not wal.exists() or wal.stat().st_size == 0
