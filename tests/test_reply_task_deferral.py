"""Isolated operator deferral of an existing reply task."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.action_storage import insert_action

from app.run_storage import AGENT_RUN_SQL, insert_run
from app.task_storage import REPLY_TASK_SQL
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
        db.execute("update tasks set status='pending', locked_at=null where record_type='reply_tasks' and id=?", (task.id,))
    backup = tmp_path / 'verified-backup.sqlite3'
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    return path, task.id, backup


def native_events(path, task_id, monkeypatch, item, *, missing=False):
    from app import native_trajectory
    native = path.parent / 'native.jsonl'
    records = [
        {'type': 'event_msg', 'payload': {'type': 'task_started'}},
        {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': item}},
        {'type': 'event_msg', 'payload': {'type': 'task_complete'}},
    ]
    if not missing:
        native.write_text('\n'.join(json.dumps(record) for record in records))
    monkeypatch.setattr(native_trajectory, 'find_codex_session_path', lambda *a, **k: native)
    with sqlite3.connect(path) as db:
        db.execute("update runs set session_id='native-test',transcript_start=0,transcript_end=? where record_type='agent_runs' and task_id=?", (len(records), task_id))
    return len(records)


@pytest.mark.parametrize('effect', ['effectful', 'unreviewed'])
def test_native_tool_effect_blocks_deferral_without_event_copy(tmp_path, monkeypatch, effect):
    path, task_id, _ = ready(tmp_path)
    native_events(path, task_id, monkeypatch, {'type': 'UnknownTool', 'metadata': {'effect': effect}})
    assert 'tool_effect_or_unknown_effect' in preview_deferral(path, task_id, now=NOW)['blockers']
    with sqlite3.connect(path) as db:
        assert db.execute("select name from sqlite_master where name='agent_run_events'").fetchone() is None


def test_missing_native_events_cannot_prove_deferral_eligible(tmp_path, monkeypatch):
    path, task_id, _ = ready(tmp_path)
    native_events(path, task_id, monkeypatch, {'type': 'AgentMessage', 'text': 'message'}, missing=True)
    assert 'native_event_source_unavailable' in preview_deferral(path, task_id, now=NOW)['blockers']


def test_complete_native_messages_preserve_deferral_eligibility(tmp_path, monkeypatch):
    path, task_id, _ = ready(tmp_path)
    native_events(path, task_id, monkeypatch, {'type': 'AgentMessage', 'text': 'message'})
    assert preview_deferral(path, task_id, now=NOW)['eligible'] is True


def test_native_tool_without_effect_annotation_preserves_existing_deferral_predicate(tmp_path, monkeypatch):
    path, task_id, _ = ready(tmp_path)
    native_events(path, task_id, monkeypatch, {'type': 'McpToolCall', 'id': 'read', 'server': 'memory_connector', 'tool': 'memory_recall', 'arguments': {}})
    assert preview_deferral(path, task_id, now=NOW)['eligible'] is True


@pytest.mark.parametrize('status', ['failed', 'superseded'])
def test_failed_attempt_before_native_session_remains_eligible(tmp_path, status):
    path, task_id, _ = ready(tmp_path)
    with sqlite3.connect(path) as db:
        run_id = db.execute(f'select id from ({AGENT_RUN_SQL}) where reply_task_id=?', (task_id,)).fetchone()[0]
        insert_run(
            db, 'agent_runtime_attempts', {
                'parent_id': run_id,
                'workload_kind': 'agent_run',
                'workload_key': str(run_id),
                'attempt_number': 1,
                'route_name': 'test',
                'runtime_kind': 'codex_cli',
                'credential_mode': 'oauth',
                'model': 'test',
                'status': status,
            },
        )
    assert preview_deferral(path, task_id, now=NOW)['eligible'] is True


def test_observed_session_without_attempt_or_bounds_is_unavailable(tmp_path):
    from app.deploy_maintenance import check_maintenance
    from app.email_store import EmailStore
    from app.repository_updater import UpgradePreconditionError
    path, task_id, _ = ready(tmp_path)
    EmailStore(path)
    with sqlite3.connect(path) as db:
        db.execute("update runs set session_id='observed-session' where record_type='agent_runs' and task_id=?", (task_id,))
    assert 'native_event_source_unavailable' in preview_deferral(path, task_id, now=NOW)['blockers']
    with pytest.raises(UpgradePreconditionError, match='native event source unavailable'):
        check_maintenance(path, (task_id,))


@pytest.mark.parametrize('kind', ['claude_cli', 'codex_cli'])
def test_complete_native_metadata_preserves_operator_eligibility(tmp_path, monkeypatch, kind):
    from app import native_trajectory
    from app.deploy_maintenance import check_maintenance
    from app.email_store import EmailStore
    path, task_id, _ = ready(tmp_path)
    EmailStore(path)
    if kind == 'claude_cli':
        records = [{'type': kind} for kind in ('ai-title', 'queue-operation', 'attachment', 'atis-latch', 'last-prompt', 'mode')]
        records.extend([
            {'type': 'user', 'message': {'content': 'Plain native user prompt'}},
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'No tool activity.'}]}},
        ])
    else:
        records = [{'type': kind} for kind in ('world_state', 'inter_agent_communication_metadata', 'token_usage_record', 'compacted')]
        records.extend([
            {'type': 'event_msg', 'payload': {'type': 'thread_settings_applied'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'No tool activity.'}]}},
        ])
    native = tmp_path / 'native.jsonl'
    native.write_text('\n'.join(json.dumps(record) for record in records))
    monkeypatch.setattr(native_trajectory, 'claude_session_path', lambda *a, **k: native)
    monkeypatch.setattr(native_trajectory, 'find_codex_session_path', lambda *a, **k: native)
    with sqlite3.connect(path) as db:
        run_id = db.execute(f'select id from ({AGENT_RUN_SQL}) where reply_task_id=?', (task_id,)).fetchone()[0]
        insert_run(
            db, 'agent_runtime_attempts', {
                'parent_id': run_id,
                'workload_kind': 'agent_run',
                'workload_key': str(run_id),
                'attempt_number': 1,
                'route_name': 'test',
                'runtime_kind': kind,
                'credential_mode': 'oauth',
                'model': 'test',
                'status': 'failed',
                'session_id': 'native-test',
                'transcript_reference': 'native-test',
                'transcript_end': len(records),
            },
        )
    assert preview_deferral(path, task_id, now=NOW)['eligible'] is True
    assert check_maintenance(path, (task_id,)).task_bindings[0][0] == task_id


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
        before = db.execute(f'select status, attempts, execution_generation, input_version, error from ({REPLY_TASK_SQL}) where id=?', (task_id,)).fetchone()
        runs_before = db.execute(f'select id, final_result_json, structured_error_json from ({AGENT_RUN_SQL}) where reply_task_id=?', (task_id,)).fetchall()
        schema_before = db.execute("select name from sqlite_master where type='table'").fetchall()
    preview = preview_deferral(path, task_id, now=NOW)
    assert preview['eligible'] is True
    with sqlite3.connect(path) as db:
        assert db.execute("select name from sqlite_master where type='table'").fetchall() == schema_before
    receipt = apply_ready(path, task_id, backup)
    assert receipt['status'] == 'paused'
    assert apply_ready(path, task_id, backup)['id'] == receipt['id']
    assert AutoReplyStore(path).claim_reply_tasks(1, now=NOW.strftime('%Y-%m-%d %H:%M:%S')) == []
    with sqlite3.connect(path) as db:
        task = db.execute(f'select status, attempts, execution_generation, input_version, error, available_at from ({REPLY_TASK_SQL}) where id=?', (task_id,)).fetchone()
        assert task[:5] == before
        assert task[5] > '2026-10-05 12:00:00'
        assert db.execute(f'select id, final_result_json, structured_error_json from ({AGENT_RUN_SQL}) where reply_task_id=?', (task_id,)).fetchall() == runs_before
    restored = resume_deferral(path, task_id, receipt_id=receipt['id'], request_id='resume-one', now=NOW)
    assert restored['status'] == 'resumed'
    assert resume_deferral(path, task_id, receipt_id=receipt['id'], request_id='resume-one', now=NOW)['id'] == receipt['id']
    with sqlite3.connect(path) as db:
        assert db.execute(f'select available_at from ({REPLY_TASK_SQL}) where id=?', (task_id,)).fetchone()[0] == ''
    assert AutoReplyStore(path).claim_reply_tasks(1, now=NOW.strftime('%Y-%m-%d %H:%M:%S'))[0].id == task_id


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
        insert_action(db, 'sent_replies', {'conversation_id':'cid-deferral','trigger_message_id':'other-message','reply_text':'sent'})
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
        assert db.execute(f'select available_at from ({REPLY_TASK_SQL}) where id=?', (task_id,)).fetchone()[0] == receipt['deferred_until']
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
    "update tasks set input_version=input_version+1",
    "update tasks set execution_generation='new-gen'",
    "update runs set structured_error_json='changed' where record_type='agent_runs'",
    "update runs set status='running' where record_type='agent_runs'",
    "candidate_selection",
])
def test_apply_refuses_changed_identity_or_live_work(tmp_path, mutation):
    path, task_id, backup = ready(tmp_path)
    preview = preview_deferral(path, task_id, now=NOW)
    with sqlite3.connect(path) as db:
        if mutation == 'candidate_selection':
            run = db.execute(f"select id,execution_generation from ({AGENT_RUN_SQL}) where reply_task_id=?", (task_id,)).fetchone()
            insert_action(db, 'review_candidates', {
                'id': 1, 'task_id': task_id, 'execution_generation': run[1],
                'consumer_run_id': run[0], 'stage_index': 0, 'proposal_revision': 0,
                'candidate_digest': 'd'*64, 'candidate_json': '{}',
            })
            insert_action(db, 'candidate_reviews', {
                'id': 1, 'candidate_id': 1, 'audit_run_id': 1, 'decision': 'approve',
                'candidate_digest': 'd'*64, 'result_json': '{}',
            })
            insert_action(db, 'candidate_selections', {
                'candidate_id': 1, 'review_id': 1, 'option_key': 'x', 'branch_json': '{}',
            })
        else:
            db.execute(mutation + (" where record_type='reply_tasks' and id=?" if mutation.startswith('update tasks') else (' and task_id=?' if mutation.startswith("update runs ") else '')), ((task_id,) if mutation.startswith('update ') else ()))
    with pytest.raises(ValueError):
        apply_deferral(path, task_id, expected_business_object_key=preview['business_object_key'],
            expected_generation=preview['execution_generation'],
            expected_input_version=preview['input_version'], expected_run_id=preview['latest_failed_run_id'],
            expected_run_fingerprint=preview['latest_failed_run_fingerprint'], backup_path=backup,
            rationale='bounded drain', duration_seconds=3600, request_id='pause-one', now=NOW)


@pytest.mark.parametrize(('statement', 'values'), [
    ("update runs set lease_owner='worker', lease_expires_at='2026-10-05 13:00:00' where record_type='agent_runs'", ()),
    ("update runs set lease_owner='worker', lease_expires_at='2026-10-05T13:00:00Z' where record_type='agent_runs'", ()),
    ("insert into dispatcher_claim_leases(adapter_name,source_id,owner,lease_expires_at) values ('reply',?,'worker','2026-10-05 13:00:00')", ('TASK',)),
    (lambda db, values=(): insert_run(
        db, 'agent_runtime_attempts', {
            'parent_id': values[0],
            'workload_kind': 'agent_run',
            'workload_key': values[1],
            'attempt_number': 1,
            'route_name': 'r',
            'runtime_kind': 'cli',
            'credential_mode': 'native',
            'model': 'm',
            'status': 'running',
        },
    ), ('RUN', 'RUN_TEXT')),
    (lambda db, values=(): insert_run(
        db, 'agent_runtime_attempts', {
            'parent_id': values[0],
            'workload_kind': 'agent_run',
            'workload_key': values[1],
            'attempt_number': 1,
            'route_name': 'r',
            'runtime_kind': 'cli',
            'credential_mode': 'native',
            'model': 'm',
            'status': 'failed',
            'first_effect_started_at': '2026-10-05 11:00:00',
        },
    ), ('RUN', 'RUN_TEXT')),
    (lambda db, values: insert_action(db, 'agent_effect_intents', {
        'agent_run_id': values[0], 'authorization_id': 'auth', 'action_index': 0,
        'receipt_operation_id': 'op', 'capability': 'c', 'operation': 'o',
        'operation_digest': 'd', 'arguments_digest': 'a',
        'target_identifiers_json': '{}', 'state': 'dispatched',
    }), ('RUN',)),
    (lambda db, values: insert_action(db, 'agent_effect_intents', {
        'agent_run_id': values[0], 'authorization_id': 'auth', 'action_index': 0,
        'receipt_operation_id': 'op', 'capability': 'c', 'operation': 'o',
        'operation_digest': 'd', 'arguments_digest': 'a',
        'target_identifiers_json': '{}', 'state': 'prepared',
    }), ('RUN',)),
    (lambda db, values: insert_action(db, 'agent_execution_receipts', {
        'agent_run_id': values[0], 'receipt_id': 'r', 'operation_id': 'op',
        'cli': 'cli', 'command_path': 'cmd', 'command_digest': 'd',
        'exit_code': 1, 'completed': 0, 'persisted': 0, 'safe_to_confirm': 0,
    }), ('RUN',)),
    (lambda db, values: insert_action(db, 'sent_replies', {'conversation_id':'cid-deferral','trigger_message_id':'mid-deferral','reply_text':'sent'}), ()),
    (lambda db, values: insert_action(db, 'external_action_results', {
        'external_action_key': 'k', 'business_object_key': values[0],
        'action_identity': 'a', 'operation': 'o', 'target_identifiers_json': '{}',
        'provider_result_json': '{}', 'result_digest': 'd', 'first_agent_run_id': values[1],
    }), ('BUSINESS', 'RUN')),
])
def test_preview_and_apply_refuse_live_or_possible_effect(tmp_path, statement, values):
    path, task_id, backup = ready(tmp_path)
    original = preview_deferral(path, task_id, now=NOW)
    actual = tuple({'TASK': str(task_id), 'RUN': original['latest_failed_run_id'],
                    'RUN_TEXT': str(original['latest_failed_run_id']),
                    'BUSINESS': original['business_object_key']}.get(value, value) for value in values)
    with sqlite3.connect(path) as db:
        if callable(statement):
            statement(db, actual)
        else:
            db.execute(statement + (' and task_id=?' if statement.startswith("update runs ") else ''),
                       actual + ((task_id,) if statement.startswith("update runs ") else ()))
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
        db.execute("update tasks set business_object_key='changed' where record_type='reply_tasks' and id=?", (task_id,))
    with pytest.raises(ValueError, match='exact'):
        apply_ready(path, task_id, backup)
    backup.write_bytes(b'not a sqlite database')
    with pytest.raises(sqlite3.DatabaseError):
        apply_ready(path, task_id, backup)


@pytest.mark.parametrize('change', [
    "update tasks set input_version=input_version+1",
    "update tasks set available_at='2026-10-05 15:00:00'",
    "update runs set structured_error_json='changed' where record_type='agent_runs'",
    "insert into dispatcher_claim_leases(adapter_name,source_id,owner,lease_expires_at) values ('reply','TASK','worker','2026-10-05 13:00:00')",
])
def test_resume_refuses_intervening_change(tmp_path, change):
    path, task_id, backup = ready(tmp_path)
    receipt = apply_ready(path, task_id, backup)
    statement = change.replace('TASK', str(task_id))
    with sqlite3.connect(path) as db:
        db.execute(statement + (" where record_type='reply_tasks' and id=?" if statement.startswith('update tasks') else
                                (' and task_id=?' if statement.startswith("update runs ") else '')),
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
