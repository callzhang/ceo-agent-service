"""Isolated operator deferral of an existing reply task."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.reply_task_deferral import apply_deferral, main, preview_deferral, resume_deferral
from app.store import AgentRole, AutoReplyStore

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def ready(tmp_path: Path):
    path = tmp_path / 'source.sqlite3'
    store = AutoReplyStore(path)
    store.enqueue_reply_task(
        conversation_id='cid-deferral', conversation_title='Test', single_chat=True,
        trigger_message_id='mid-deferral', trigger_create_time='2026-10-05 10:00:00',
        trigger_sender='Derek', trigger_text='input',
    )
    task = store.claim_reply_tasks(1)[0]
    claim = store.claim_agent_run(task.id, task.execution_generation,
        role=AgentRole.CONSUMER, proposal_revision=0, turn_attempt=0,
        parent_agent_run_id=None, operation_id='', owner='agent')
    store.fail_agent_run(claim.run.id, {'code': 'origin_validation_failed', 'retryable': False}, owner='agent')
    with store._connect() as db:
        db.execute("update reply_tasks set status='pending', locked_at=null where id=?", (task.id,))
    backup = tmp_path / 'verified-backup.sqlite3'
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    return path, task.id, backup


def apply_ready(path, task_id, backup, **overrides):
    preview = preview_deferral(path, task_id, now=NOW)
    args = dict(expected_business_object_key=preview['business_object_key'],
        expected_generation=preview['execution_generation'],
        expected_input_version=preview['input_version'],
        expected_run_id=preview['latest_failed_run_id'],
        expected_run_fingerprint=preview['latest_failed_run_fingerprint'],
        backup_path=backup, rationale='bounded deployment drain', duration_seconds=3600,
        request_id='pause-one', now=NOW)
    args.update(overrides)
    return apply_deferral(path, task_id, **args)


def test_preview_read_only_and_apply_resume_preserve_task_and_run(tmp_path):
    path, task_id, backup = ready(tmp_path)
    with sqlite3.connect(path) as db:
        before = db.execute('select status, attempts, execution_generation, input_version, error from reply_tasks where id=?', (task_id,)).fetchone()
        runs_before = db.execute('select id, final_result_json, structured_error_json from agent_runs where reply_task_id=?', (task_id,)).fetchall()
        schema_before = db.execute("select name from sqlite_master where type='table'").fetchall()
    preview = preview_deferral(path, task_id, now=NOW)
    assert preview['eligible'] is True
    with sqlite3.connect(path) as db:
        assert db.execute("select name from sqlite_master where type='table'").fetchall() == schema_before
    receipt = apply_ready(path, task_id, backup)
    assert receipt['status'] == 'paused'
    assert apply_ready(path, task_id, backup)['id'] == receipt['id']
    assert AutoReplyStore(path).claim_reply_tasks(1) == []
    with sqlite3.connect(path) as db:
        task = db.execute('select status, attempts, execution_generation, input_version, error, available_at from reply_tasks where id=?', (task_id,)).fetchone()
        assert task[:5] == before
        assert task[5] > '2026-10-05 12:00:00'
        assert db.execute('select id, final_result_json, structured_error_json from agent_runs where reply_task_id=?', (task_id,)).fetchall() == runs_before
    restored = resume_deferral(path, task_id, receipt_id=receipt['id'], request_id='resume-one', now=NOW)
    assert restored['status'] == 'resumed'
    assert resume_deferral(path, task_id, receipt_id=receipt['id'], request_id='resume-one', now=NOW)['id'] == receipt['id']
    with sqlite3.connect(path) as db:
        assert db.execute('select available_at from reply_tasks where id=?', (task_id,)).fetchone()[0] == ''
    assert AutoReplyStore(path).claim_reply_tasks(1)[0].id == task_id


def test_cli_defaults_to_read_only_preview(tmp_path, capsys):
    path, task_id, _backup = ready(tmp_path)
    with sqlite3.connect(path) as db:
        before = db.execute("select name from sqlite_master where type='table'").fetchall()
    assert main(['--db', str(path), '--task-id', str(task_id)]) == 0
    assert 'latest_failed_run_fingerprint' in capsys.readouterr().out
    with sqlite3.connect(path) as db:
        assert db.execute("select name from sqlite_master where type='table'").fetchall() == before


def test_expired_dispatcher_lease_with_live_pid_is_still_owned(tmp_path):
    import os

    path, task_id, _backup = ready(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("""insert into dispatcher_claim_leases(adapter_name,source_id,owner,owner_pid,lease_expires_at)
            values ('reply',?,?,?,'2026-10-05 11:00:00')""", (str(task_id), 'worker', os.getpid()))
    assert 'active_dispatcher_owner' in preview_deferral(path, task_id, now=NOW)['blockers']


def test_unrelated_message_delivery_does_not_block_this_task(tmp_path):
    path, task_id, _backup = ready(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("insert into sent_replies(conversation_id,trigger_message_id,reply_text) values ('cid-deferral','other-message','sent')")
    assert preview_deferral(path, task_id, now=NOW)['eligible'] is True


def test_native_sent_reply_for_exact_trigger_blocks_apply(tmp_path):
    path, task_id, backup = ready(tmp_path)
    AutoReplyStore(path).record_sent_reply('cid-deferral', 'mid-deferral', 'delivered')
    assert 'sent_reply_projection' in preview_deferral(path, task_id, now=NOW)['blockers']
    with pytest.raises(ValueError, match='deferral blocked'):
        apply_ready(path, task_id, backup)


def test_native_sent_reply_after_pause_blocks_resume_without_overwrite(tmp_path):
    path, task_id, backup = ready(tmp_path)
    receipt = apply_ready(path, task_id, backup)
    AutoReplyStore(path).record_sent_reply('cid-deferral', 'mid-deferral', 'delivered')
    with pytest.raises(ValueError, match='resume blocked: sent_reply_projection'):
        resume_deferral(path, task_id, receipt_id=receipt['id'], request_id='resume-one', now=NOW)
    with sqlite3.connect(path) as db:
        assert db.execute('select available_at from reply_tasks where id=?', (task_id,)).fetchone()[0] == receipt['deferred_until']
        assert db.execute('select status from reply_task_deferral_receipts where id=?', (receipt['id'],)).fetchone()[0] == 'paused'


def test_active_deferral_previews_blocked_and_distinct_apply_is_explicit_conflict(tmp_path):
    path, task_id, backup = ready(tmp_path)
    receipt = apply_ready(path, task_id, backup)
    assert 'already_paused' in preview_deferral(path, task_id, now=NOW)['blockers']
    with pytest.raises(ValueError, match='already_paused'):
        apply_ready(path, task_id, backup, request_id='different-pause')
    assert apply_ready(path, task_id, backup)['id'] == receipt['id']
    assert resume_deferral(path, task_id, receipt_id=receipt['id'], request_id='resume-one', now=NOW)['status'] == 'resumed'


@pytest.mark.parametrize('mutation', [
    "update reply_tasks set input_version=input_version+1",
    "update reply_tasks set execution_generation='new-gen'",
    "update agent_runs set structured_error_json='changed'",
    "update agent_runs set status='running'",
    "insert into candidate_selections(candidate_id,review_id,option_key,branch_json) values (1,1,'x','{}')",
])
def test_apply_refuses_changed_identity_or_live_work(tmp_path, mutation):
    path, task_id, backup = ready(tmp_path)
    preview = preview_deferral(path, task_id, now=NOW)
    with sqlite3.connect(path) as db:
        if mutation.startswith('insert into candidate'):
            db.execute("insert into review_candidates(task_id,execution_generation,consumer_run_id,stage_index,proposal_revision,candidate_digest,candidate_json) select ?,execution_generation,id,0,0,?,? from agent_runs where reply_task_id=?", (task_id, 'd'*64, '{}', task_id))
            db.execute("insert into candidate_reviews(candidate_id,audit_run_id,decision,candidate_digest,result_json) values (1,1,'approve',?,'{}')", ('d'*64,))
        db.execute(mutation + (' where id=?' if mutation.startswith('update reply_tasks') else (' where reply_task_id=?' if mutation.startswith('update agent_runs') else '')), ((task_id,) if mutation.startswith('update ') else ()))
    with pytest.raises(ValueError):
        apply_deferral(path, task_id, expected_business_object_key=preview['business_object_key'],
            expected_generation=preview['execution_generation'],
            expected_input_version=preview['input_version'], expected_run_id=preview['latest_failed_run_id'],
            expected_run_fingerprint=preview['latest_failed_run_fingerprint'], backup_path=backup,
            rationale='bounded drain', duration_seconds=3600, request_id='pause-one', now=NOW)


@pytest.mark.parametrize(('statement', 'values'), [
    ("update agent_runs set lease_owner='worker', lease_expires_at='2026-10-05 13:00:00'", ()),
    ("update agent_runs set lease_owner='worker', lease_expires_at='2026-10-05T13:00:00Z'", ()),
    ("insert into dispatcher_claim_leases(adapter_name,source_id,owner,lease_expires_at) values ('reply',?,'worker','2026-10-05 13:00:00')", ('TASK',)),
    ("insert into agent_runtime_attempts(agent_run_id,workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status) values (?,'agent_run',?,1,'r','cli','native','m','running')", ('RUN', 'RUN_TEXT')),
    ("insert into agent_runtime_attempts(agent_run_id,workload_kind,workload_key,attempt_number,route_name,runtime_kind,credential_mode,model,status,first_effect_started_at) values (?,'agent_run',?,1,'r','cli','native','m','failed','2026-10-05 11:00:00')", ('RUN', 'RUN_TEXT')),
    ("insert into agent_effect_intents(agent_run_id,authorization_id,action_index,receipt_operation_id,capability,operation,operation_digest,arguments_digest,target_identifiers_json,state) values (?,'auth',0,'op','c','o','d','a','{}','dispatched')", ('RUN',)),
    ("insert into agent_effect_intents(agent_run_id,authorization_id,action_index,receipt_operation_id,capability,operation,operation_digest,arguments_digest,target_identifiers_json,state) values (?,'auth',0,'op','c','o','d','a','{}','prepared')", ('RUN',)),
    ("insert into agent_run_events(agent_run_id,sequence,event_json,effect_kind) values (?,1,'{}','unreviewed')", ('RUN',)),
    ("insert into agent_run_events(agent_run_id,sequence,event_json,effect_kind) values (?,1,'{\"item\":{\"metadata\":{\"effect\":\"effectful\"}}}','')", ('RUN',)),
    ("insert into agent_execution_receipts(agent_run_id,receipt_id,operation_id,cli,command_path,command_digest,exit_code,completed,persisted,safe_to_confirm) values (?,'r','op','cli','cmd','d',1,0,0,0)", ('RUN',)),
    ("insert into sent_replies(conversation_id,trigger_message_id,reply_text) values ('cid-deferral','mid-deferral','sent')", ()),
    ("insert into external_action_results(external_action_key,business_object_key,action_identity,operation,target_identifiers_json,provider_result_json,result_digest,first_agent_run_id) values ('k',?,'a','o','{}','{}','d',?)", ('BUSINESS', 'RUN')),
])
def test_preview_and_apply_refuse_live_or_possible_effect(tmp_path, statement, values):
    path, task_id, backup = ready(tmp_path)
    original = preview_deferral(path, task_id, now=NOW)
    actual = tuple({'TASK': str(task_id), 'RUN': original['latest_failed_run_id'],
                    'RUN_TEXT': str(original['latest_failed_run_id']),
                    'BUSINESS': original['business_object_key']}.get(value, value) for value in values)
    with sqlite3.connect(path) as db:
        db.execute(statement + (' where reply_task_id=?' if statement.startswith('update agent_runs') else ''),
                   actual + ((task_id,) if statement.startswith('update agent_runs') else ()))
    assert not preview_deferral(path, task_id, now=NOW)['eligible']
    with pytest.raises(ValueError, match='deferral blocked'):
        apply_ready(path, task_id, backup)
    with sqlite3.connect(path) as db:
        assert db.execute("select name from sqlite_master where name='reply_task_deferral_receipts'").fetchone() is None


def test_backup_must_be_separate_intact_and_exact(tmp_path):
    path, task_id, backup = ready(tmp_path)
    with pytest.raises(ValueError, match='separate'):
        apply_ready(path, task_id, path)
    with sqlite3.connect(backup) as db:
        db.execute("update reply_tasks set business_object_key='changed' where id=?", (task_id,))
    with pytest.raises(ValueError, match='exact'):
        apply_ready(path, task_id, backup)
    backup.write_bytes(b'not a sqlite database')
    with pytest.raises(sqlite3.DatabaseError):
        apply_ready(path, task_id, backup)


@pytest.mark.parametrize('change', [
    "update reply_tasks set input_version=input_version+1",
    "update reply_tasks set available_at='2026-10-05 15:00:00'",
    "update agent_runs set structured_error_json='changed'",
    "insert into dispatcher_claim_leases(adapter_name,source_id,owner,lease_expires_at) values ('reply','TASK','worker','2026-10-05 13:00:00')",
])
def test_resume_refuses_intervening_change(tmp_path, change):
    path, task_id, backup = ready(tmp_path)
    receipt = apply_ready(path, task_id, backup)
    statement = change.replace('TASK', str(task_id))
    with sqlite3.connect(path) as db:
        db.execute(statement + (' where id=?' if statement.startswith('update reply_tasks') else
                                (' where reply_task_id=?' if statement.startswith('update agent_runs') else '')),
                   ((task_id,) if statement.startswith('update ') else ()))
    with pytest.raises(ValueError):
        resume_deferral(path, task_id, receipt_id=receipt['id'], request_id='resume-one', now=NOW)
    with sqlite3.connect(path) as db:
        assert db.execute('select status from reply_task_deferral_receipts where id=?', (receipt['id'],)).fetchone()[0] == 'paused'


def test_dispatcher_claim_committed_before_deferral_lock_wins(tmp_path):
    path, task_id, backup = ready(tmp_path)
    with sqlite3.connect(path, isolation_level=None) as claiming:
        claiming.execute('begin immediate')
        with ThreadPoolExecutor(max_workers=1) as pool:
            deferred = pool.submit(apply_ready, path, task_id, backup)
            claiming.execute("""insert into dispatcher_claim_leases
                (adapter_name,source_id,owner,lease_expires_at)
                values ('reply',?,'worker','2026-10-05 13:00:00')""", (str(task_id),))
            claiming.commit()
            with pytest.raises(ValueError, match='deferral blocked'):
                deferred.result(timeout=5)
    with sqlite3.connect(path) as db:
        assert db.execute("select name from sqlite_master where name='reply_task_deferral_receipts'").fetchone() is None
