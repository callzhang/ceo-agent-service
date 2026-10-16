#!/usr/bin/env python3
"""Synthetic fresh two-queue public differential proof; no migration acceptance.

Copies app into owned temporary trees. Source runs ordinary constructors. Target
replaces EMPTY Tasks/native queues only, then uses EmailStore.__new__ with its
actual constructor attributes and original connection methods. No DAO is faked.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

PYTHON = Path('/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python')
CLASSIFICATION = 'email_agent_classification_tasks'
HISTORICAL = 'email_historical_candidates'
KINDS = (CLASSIFICATION, HISTORICAL)
CLOCK = datetime(2040, 1, 1, tzinfo=timezone.utc)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value):
    if is_dataclass(value):
        return encoded(asdict(value))
    if hasattr(value, 'model_dump'):
        return encoded(value.model_dump(mode='json'))
    if isinstance(value, dict):
        return {k: encoded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encoded(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(encoded(v) for v in value)
    if isinstance(value, bytes):
        return {'blob_hex': value.hex()}
    return value


def worker(args):
    sys.path.insert(0, str(args.code))
    from app.store import AutoReplyStore
    from app.email_store import EmailStore
    from app.email_task_adapter import EmailClassificationTaskAdapter, EmailClassificationTaskInput
    from app.email_historical_classifier import HistoricalClassificationCandidate
    from app.task12_storage import task12_projection
    from app.state_definitions import state_projection

    if args.worker == 'bootstrap':
        AutoReplyStore(args.db)
        email = EmailStore(args.db)
        with email._connect() as db:
            counts = {name: db.execute(f'select count(*) from {name}').fetchone()[0] for name in ('tasks', *KINDS)}
            assert set(counts.values()) == {0}
            receipt = {'empty_counts': counts, 'sqlite_schema_table_entries': db.execute("select count(*) from sqlite_schema where type='table'").fetchone()[0], 'native_queue_ddl_sha256': {name: hashlib.sha256(db.execute('select sql from sqlite_schema where name=?', (name,)).fetchone()[0].encode()).hexdigest() for name in KINDS}, 'fk_findings': [list(row) for row in db.execute('pragma foreign_key_check')]}
        args.result.write_text(json.dumps(receipt))
        return
    if args.worker == 'prepare':
        from app.task20_definitions import create_task20_schema, initialize_task8_sequence
        with sqlite3.connect(args.db) as db:
            for name in ('tasks', *KINDS):
                assert db.execute(f'select count(*) from {name}').fetchone()[0] == 0
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            for name in KINDS:
                db.execute(f'drop table {name}')
            db.execute('drop table tasks')
            create_task20_schema(db)
            initialize_task8_sequence(db, 0)
        args.result.write_text(json.dumps({'empty_tasks_replaced': True, 'empty_native_queues_removed': list(KINDS), 'published_water': 0}))
        return

    clock = [CLOCK]
    touches = []
    sql_trace = []
    original_open = EmailStore._open_connection

    def fixture_open(self):
        db = original_open(self)
        db.create_function('current_timestamp', 0, lambda: clock[0].strftime('%Y-%m-%d %H:%M:%S'))
        db.create_function('fixture_touch', 1, lambda value: touches.append(value) or 0)
        if args.target:
            from app.task8_storage import register_task8_functions
            register_task8_functions(db)
        db.set_trace_callback(sql_trace.append)
        return db

    EmailStore._open_connection = fixture_open
    EmailStore._now = staticmethod(lambda: clock[0].isoformat(timespec='seconds'))
    if args.target:
        email = EmailStore.__new__(EmailStore)
        email.path = args.db
        email.training_run_id = None
    else:
        email = EmailStore(args.db)
    adapter = EmailClassificationTaskAdapter(email, now=lambda: clock[0], lease_seconds=10, retry_base_seconds=2)

    def classification_input(uid, text='Synthetic classification body'):
        return EmailClassificationTaskInput.from_message(
            {'accountId': 'account-synthetic', 'folder': 'INBOX', 'uidValidity': 42, 'uid': uid, 'messageId': f'<synthetic-{uid}@example.invalid>', 'providerUnread': True, 'subject': f'Synthetic {uid}', 'textBody': text},
            allowed_category_keys=('work', 'junk'), category_descriptions={'work': {'core': 'Business.'}, 'junk': {'core': 'Unwanted.'}}, folder_targets={'work': 'Work'}, config_version='config-synthetic-v1', unsubscribe_candidates=(),
        )

    if args.worker == 'compaction-control':
        task_input = classification_input(999)
        adapter.ensure_task(task_input)
        claimed = adapter.claim_next(owner='control')
        adapter.complete(claimed, {'synthetic': True})
        persisted = adapter.get_task(claimed.task_id)
        payload = json.loads(persisted.input_json)
        args.result.write_text(json.dumps({'method': 'EmailClassificationTaskAdapter.complete', 'status': persisted.status, 'input_compacted': payload.get('input_compacted') is True, 'message_retained': 'message' in payload, 'identity_sha256': payload.get('identity_sha256'), 'input_bytes': len(persisted.input_json.encode())}))
        return

    def logical_facts():
        with email._connect() as db:
            facts = {}
            for kind in KINDS:
                query = task12_projection(kind, include_rowid=True) if args.target else f'select rowid,* from {kind}'
                rows = db.execute(f'select * from ({query}) order by rowid').fetchall()
                facts[kind] = [{key: {'value': encoded(row[key]), 'scalar_type': type(row[key]).__name__} for key in row.keys()} for row in rows]
            query = state_projection('email_historical_traversals')
            facts['traversals'] = [encoded(dict(row)) for row in db.execute(f'select * from ({query}) order by account_id,folder,model_id')]
            facts['fk_findings'] = [list(row) for row in db.execute('pragma foreign_key_check')]
        return facts

    records = []
    methods = set()
    expected_errors = {
        'immutable_changed', 'claim_blank_owner', 'fail_wrong_owner', 'retry_empty',
        'complete_stale_generation', 'done_replay_changed', 'stale_expired_complete',
        'bulk_retry_abort', 'historical_limit_bool', 'historical_bad_state',
        'historical_missing_state', 'historical_page_second_invalid',
    }

    def call(label, receiver, method, *pos, **kw):
        methods.add(type(receiver).__name__ + '.' + method)
        before = logical_facts()
        sql_trace.clear()
        touches.clear()
        try:
            result = getattr(receiver, method)(*pos, **kw)
            outcome = {'return': encoded(result)}
        except Exception as exc:
            result = None
            outcome = {'error': {'type': type(exc).__name__, 'message': str(exc)}}
        transactions = [stmt.strip().upper() for stmt in sql_trace if stmt.strip().upper() in {'BEGIN', 'BEGIN IMMEDIATE', 'COMMIT', 'ROLLBACK'}]
        after = logical_facts()
        records.append({'label': label, 'method': type(receiver).__name__ + '.' + method, 'outcome': outcome, 'transactions': transactions, 'fault_rows_touched': list(touches), 'facts': after})
        if ('error' in outcome) != (label in expected_errors):
            raise AssertionError(f'unexpected public outcome {label}: {outcome}')
        if label in expected_errors:
            assert before == after, f'failed public call changed committed facts: {label}'
        return result

    def section(label, fn):
        try:
            fn()
        except Exception as exc:
            records.append({'label': label + ':fixture_abort', 'error': {'type': type(exc).__name__, 'message': str(exc)}})

    def classifications():
        task_input = classification_input(41)
        first = call('ensure', adapter, 'ensure_task', task_input)
        assert first.status == 'pending'
        assert call('ensure_replay', adapter, 'ensure_task', task_input) == first
        assert call('consumer_context_replay', adapter, 'ensure_task', replace(task_input, scheduled_consumer={'prompt': 'synthetic changed context'})) == first
        call('immutable_changed', adapter, 'ensure_task', replace(task_input, config_version='changed'))
        assert call('has_record', adapter, 'has_stable_record', task_input.stable_message_identity)
        assert not call('missing_record', adapter, 'has_stable_record', 'account-synthetic:missing')
        assert call('missing_task', adapter, 'get_task', 'missing') is None
        assert call('uids_pending', adapter, 'stable_provider_uids', account_id='account-synthetic', folder='INBOX', uidvalidity=42) == frozenset({41})
        call('claim_blank_owner', adapter, 'claim_next', owner=' ')
        claimed = call('claim_initial', adapter, 'claim_next', owner=' first ')
        assert claimed.task_id == first.task_id and claimed.generation == claimed.attempt_count == 1
        call('fail_wrong_owner', adapter, 'fail', replace(claimed, owner='wrong'), error='synthetic', retryable=False)
        call('defer', adapter, 'fail', claimed, error='synthetic retry', retryable=True)
        deferred = call('get_deferred', adapter, 'get_task', first.task_id)
        assert deferred.status == 'pending' and json.loads(deferred.input_json).get('input_compacted') is not True
        assert call('claim_before_due', adapter, 'claim_next', owner='early') is None
        clock[0] += timedelta(seconds=2)
        second = call('claim_due', adapter, 'claim_next', owner='second')
        assert second.generation == second.attempt_count == 2
        call('fail_terminal', adapter, 'fail', second, error='synthetic final', retryable=False)
        assert call('uids_failed', adapter, 'stable_provider_uids', account_id='account-synthetic', folder='INBOX', uidvalidity=42) == frozenset()
        call('retry_empty', adapter, 'retry_failed_tasks', [])
        assert call('retry_reviewed', adapter, 'retry_failed_tasks', [first.task_id, first.task_id, 'missing']) == 1
        third = call('claim_reviewed', adapter, 'claim_next', owner='third')
        assert third.generation == 3 and third.attempt_count == 1
        call('complete_stale_generation', adapter, 'complete', replace(third, generation=2), {'synthetic': True})
        call('complete_done', adapter, 'complete', third, {'synthetic': True})
        done = call('get_done', adapter, 'get_task', first.task_id)
        compact = json.loads(done.input_json)
        assert done.status == 'done' and compact['input_compacted'] is True and 'message' not in compact
        assert call('ensure_compacted_replay', adapter, 'ensure_task', task_input) == done
        call('done_replay_changed', adapter, 'ensure_task', replace(task_input, config_version='changed-after-done'))
        assert call('uids_done', adapter, 'stable_provider_uids', account_id='account-synthetic', folder='INBOX', uidvalidity=42) == frozenset({41})
        call('close_guarded_done', adapter, 'close_offline_task', first.task_id)
        assert call('get_guarded_done', adapter, 'get_task', first.task_id) == done

    def recovery():
        first = call('recovery_ensure', adapter, 'ensure_task', classification_input(42))
        old = call('recovery_claim', adapter, 'claim_next', owner='old')
        assert old.task_id == first.task_id
        assert call('recover_live', adapter, 'recover_running_tasks') == 0
        clock[0] += timedelta(seconds=10)
        assert call('recover_equal_expiry', adapter, 'recover_running_tasks') == 1
        assert call('recover_repeat', adapter, 'recover_running_tasks') == 0
        call('stale_expired_complete', adapter, 'complete', old, {'synthetic': True})
        new = call('recovered_claim', adapter, 'claim_next', owner='new')
        assert new.generation == new.attempt_count == 2
        call('recovered_complete', adapter, 'complete', new, {'synthetic': True})

    def offline():
        call('offline_open', adapter, 'open_offline_task', task_id='offline-A', owner='batch-one')
        call('offline_replace', adapter, 'open_offline_task', task_id='offline-A', owner='batch-two')
        replaced = call('offline_get', adapter, 'get_task', 'offline-A')
        assert replaced.owner == 'batch-two' and replaced.input_json == '{}'
        call('offline_second', adapter, 'open_offline_task', task_id='offline-B', owner='batch-two')
        call('offline_other', adapter, 'open_offline_task', task_id='offline-C', owner='other')
        assert call('offline_purge_multirow', adapter, 'purge_offline_tasks', 'batch-two') == 2
        call('offline_close', adapter, 'close_offline_task', 'offline-C')
        call('offline_close_repeat', adapter, 'close_offline_task', 'offline-C')
        assert call('offline_purge_empty', adapter, 'purge_offline_tasks', 'batch-two') == 0

    def bulk_failure():
        failed = []
        for uid in (43, 44):
            call(f'bulk_ensure_{uid}', adapter, 'ensure_task', classification_input(uid))
            claimed = call(f'bulk_claim_{uid}', adapter, 'claim_next', owner='bulk')
            call(f'bulk_fail_{uid}', adapter, 'fail', claimed, error='bulk synthetic', retryable=False)
            failed.append(claimed)
        # Native multirow statement must attempt both rows but roll back all of
        # them when the second trigger aborts. UDF observation is test-only.
        table = 'tasks' if args.target else CLASSIFICATION
        if args.target:
            from app.task20_definitions import task20_column
            task_field = 'old.' + task20_column(CLASSIFICATION, 'task_id')
            state_field = 'new.' + task20_column(CLASSIFICATION, 'status')
            kind_guard = f"new.record_type='{CLASSIFICATION}' and "
        else:
            task_field, state_field, kind_guard = 'old.task_id', 'new.status', ''
        first_id, abort_id = (item.task_id for item in failed)
        assert all("'" not in value for value in (first_id, abort_id))
        trigger = f"create trigger fixture_bulk_retry before update on {table} when {kind_guard}{state_field}='pending' and {task_field} in ('{first_id}','{abort_id}') begin select fixture_touch({task_field}); select case when {task_field}='{abort_id}' then raise(abort,'fixture bulk retry abort') end; end"
        with email._connect() as db:
            db.execute(trigger)
        call('bulk_retry_abort', adapter, 'retry_failed_tasks', [first_id, abort_id])
        assert records[-1]['outcome']['error'] == {'type': 'IntegrityError', 'message': 'fixture bulk retry abort'}
        assert records[-1]['transactions'] == ['BEGIN IMMEDIATE', 'ROLLBACK']
        assert records[-1]['fault_rows_touched'] == [first_id, abort_id]
        with email._connect() as db:
            db.execute('drop trigger fixture_bulk_retry')
        assert call('bulk_retry_success', adapter, 'retry_failed_tasks', [first_id, abort_id, first_id]) == 2
        for index in range(2):
            claimed = call(f'bulk_finish_claim_{index}', adapter, 'claim_next', owner='finisher')
            call(f'bulk_complete_{index}', adapter, 'complete', claimed, {'synthetic': True})

    def historical():
        key = {'account_id': 'account-synthetic', 'folder': 'Archive', 'model_id': 'model-synthetic'}

        def candidate(number, uid=None, text=None, folder='Archive', validity=42):
            identity = f'account-synthetic:historical-{number}'
            return HistoricalClassificationCandidate(identity, text or f'synthetic normalized {number}', {'accountId': key['account_id'], 'folder': folder, 'uidValidity': validity, 'uid': uid or number, 'messageId': f'<history-{number}@example.invalid>', 'stableMessageIdentity': identity})

        def state(number, value, reason):
            return call('historical_' + reason, email, 'set_historical_candidate_state', **key, stable_message_identity=candidate(number).stable_message_identity, state=value, reason=reason)

        assert call('historical_cursor_empty', email, 'historical_traversal_cursor', **key) == {'uidvalidity': None, 'last_seen_uid': 0}
        call('historical_empty_page', email, 'enqueue_historical_page', **key, uidvalidity=42, last_seen_uid=10, candidates=[])
        call('historical_page', email, 'enqueue_historical_page', **key, uidvalidity=42, last_seen_uid=20, candidates=[candidate(1, 5), candidate(2, 3), candidate(3, 8)])
        listed = call('historical_list', email, 'list_historical_candidates', **key, limit=3)
        assert [row['uid'] for row in listed] == [3, 5, 8]
        call('historical_limit_bool', email, 'list_historical_candidates', **key, limit=True)
        state(1, 'deferred', 'wait')
        state(2, 'terminal', 'terminal')
        assert len(call('historical_before_due', email, 'list_historical_candidates', **key)) == 1
        call('historical_lower_cursor_page', email, 'enqueue_historical_page', **key, uidvalidity=42, last_seen_uid=9, candidates=[])
        assert call('historical_monotone_cursor', email, 'historical_traversal_cursor', **key) == {'uidvalidity': 42, 'last_seen_uid': 20}
        before = logical_facts()[HISTORICAL][0]
        clock[0] += timedelta(seconds=1)
        call('historical_upsert_existing', email, 'enqueue_historical_page', **key, uidvalidity=43, last_seen_uid=1, candidates=[candidate(1, 9, 'changed synthetic normalized text', validity=43)])
        after = logical_facts()[HISTORICAL][0]
        changed = {field for field in before if before[field] != after[field]}
        assert changed == {'normalized_text', 'provider_message_json', 'uidvalidity', 'uid', 'updated_at'}
        assert call('historical_uidvalidity_reset', email, 'historical_traversal_cursor', **key) == {'uidvalidity': 43, 'last_seen_uid': 1}
        call('historical_more_page', email, 'enqueue_historical_page', **key, uidvalidity=43, last_seen_uid=6, candidates=[candidate(4, 6), candidate(5, 2), candidate(6, 1)])
        for number in (4, 5, 6):
            state(number, 'deferred', f'defer_{number}')
        clock[0] += timedelta(seconds=60)
        due = call('historical_deferred_quota', email, 'list_historical_candidates', **key, limit=3)
        assert [row['stable_message_identity'] for row in due] == [candidate(1).stable_message_identity, candidate(6).stable_message_identity, candidate(3).stable_message_identity]
        call('historical_bad_state', email, 'set_historical_candidate_state', **key, stable_message_identity='missing', state='bad', reason='invalid')
        call('historical_missing_state', email, 'set_historical_candidate_state', **key, stable_message_identity='missing', state='terminal', reason='missing')
        assert records[-1]['transactions'] == ['BEGIN', 'COMMIT']
        call('historical_page_second_invalid', email, 'enqueue_historical_page', **key, uidvalidity=99, last_seen_uid=999, candidates=[candidate(7), candidate(8, folder='wrong')])
        assert records[-1]['outcome']['error'] == {'type': 'ValueError', 'message': 'historical candidate folder does not match queue'}
        assert records[-1]['transactions'] == ['BEGIN IMMEDIATE', 'ROLLBACK']
        state(1, 'pending', 'reset_pending')
        call('historical_final_list', email, 'list_historical_candidates', **key, limit=50)

    for name, function in (('classifications', classifications), ('recovery', recovery), ('offline', offline), ('bulk', bulk_failure), ('historical', historical)):
        section(name, function)
    labels = [record['label'] for record in records]
    assert len(labels) == len(set(labels)), 'checkpoint labels must be unique'
    with email._connect() as db:
        if args.target:
            physical = [dict(row) for row in db.execute('select id as physical_id,record_type,cast(legacy_key as integer) as source_rowid from tasks order by id')]
            assert len({row['physical_id'] for row in physical}) == len(physical)
            assert any(row['physical_id'] != row['source_rowid'] for row in physical), 'physical/global identity must differ from native ROWID in mixed fixture'
        else:
            physical = [{'record_type': kind, 'source_rowid': row[0]} for kind in KINDS for row in db.execute(f'select rowid from {kind} order by rowid')]
    args.result.write_text(json.dumps({'records': records, 'methods': sorted(methods), 'physical_coordinates': physical}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', choices=('bootstrap', 'prepare', 'scenario', 'compaction-control'))
    parser.add_argument('--code', type=Path)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--target', action='store_true')
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    script = Path(__file__).resolve()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    patch = args.repo / 'docs/superpowers/plans/2026-10-16-task12-email-native-caller-proposal.patch'
    files = ('app/store.py', 'app/email_store.py', 'app/email_task_adapter.py', 'app/task12_storage.py', 'app/task20_definitions.py')
    receipt = {'scope': 'synthetic full fresh source, two native queues only, empty Tasks20 fixture', 'constructor': 'source ordinary AutoReplyStore and EmailStore; target EmailStore.__new__ with actual path/training_run_id attributes after explicit template preparation', 'patch_sha256': sha(patch), 'source_files': {name: sha(args.repo / name) for name in files}, 'clock': CLOCK.isoformat(), 'clock_controls': 'same current_timestamp UDF, EmailStore._now seconds and adapter aware datetime callback; no output time normalization', 'companions': 'none; only exact Email proposal applied; progress/readers using old Store/Email REPLY projection are outside this two-queue proof', 'providers_native_cli_network': 'not invoked'}
    with tempfile.TemporaryDirectory(prefix='task12-email-public-', dir='/tmp') as temporary:
        root = Path(temporary)
        source_code, target_code, negative_code = (root / name for name in ('source', 'target', 'target-without-compaction'))
        for code in (source_code, target_code):
            shutil.copytree(args.repo / 'app', code / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        receipt['copied_source_files'] = {name: sha(source_code / name) for name in files}
        assert receipt['source_files'] == receipt['copied_source_files'], 'inputs changed during copy'
        source_db, target_db = source_code / 'fixture.sqlite3', target_code / 'fixture.sqlite3'

        def run(stage, code, db, target=False):
            result = root / (stage + '-' + code.name + '.json')
            cmd = [str(PYTHON), '-B', str(script), '--worker', stage, '--code', str(code), '--db', str(db), '--result', str(result)]
            if target:
                cmd.append('--target')
            completed = subprocess.run(cmd, cwd=code, env=env, text=True, capture_output=True, timeout=180)
            if completed.returncode:
                raise RuntimeError(f'{stage} {code.name}: {completed.stderr[-5000:]}')
            return json.loads(result.read_text())

        def clone(destination):
            with sqlite3.connect(source_db) as src, sqlite3.connect(destination) as dst:
                src.backup(dst)

        receipt['bootstrap'] = run('bootstrap', source_code, source_db)
        clone(target_db)
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)], cwd=target_code, check=True, text=True, capture_output=True)
        shutil.copytree(target_code / 'app', negative_code / 'app')
        source_control_db, negative_db = source_code / 'control.sqlite3', negative_code / 'control.sqlite3'
        clone(source_control_db)
        clone(negative_db)
        negative_adapter = negative_code / 'app/email_task_adapter.py'
        text = negative_adapter.read_text()
        before = "'input_json': compact, 'updated_at': self._timestamp(),"
        assert text.count(before) == 1
        negative_adapter.write_text(text.replace(before, "'input_json': row['input_json'], 'updated_at': self._timestamp(),"))
        receipt['preparation'] = run('prepare', target_code, target_db, True)
        run('prepare', negative_code, negative_db, True)
        source_control = run('compaction-control', source_code, source_control_db)
        target_control = run('compaction-control', negative_code, negative_db, True)
        assert source_control['input_compacted'] is True and source_control['message_retained'] is False
        assert target_control['input_compacted'] is False and target_control['message_retained'] is True
        receipt['negative_control'] = {'mutation': 'omit only done-branch input compaction assignment in copied target caller', 'source': source_control, 'mutated_target': target_control}
        source, target = run('scenario', source_code, source_db), run('scenario', target_code, target_db, True)
        assert source['methods'] == target['methods'], 'public method coverage differs'
        assert len(source['records']) == len(target['records']) == 79, 'expected fixed 79 checkpoints per worker'
        receipt['coverage_assertions'] = {'unique_worker_labels': True, 'same_public_method_set': True, 'fixed_checkpoints_per_worker': 79}
        sr, tr = ({row['label']: row for row in result['records']} for result in (source, target))
        differences = []
        for label in sorted(sr.keys() | tr.keys()):
            left, right = sr.get(label), tr.get(label)
            if left != right:
                fields = sorted(field for field in (left or {}).keys() | (right or {}).keys() if (left or {}).get(field) != (right or {}).get(field))
                differences.append({'label': label, 'different_fields': fields, 'source_outcome': (left or {}).get('outcome', (left or {}).get('error')), 'target_outcome': (right or {}).get('outcome', (right or {}).get('error'))})
        receipt.update({'source_checkpoints': len(sr), 'target_checkpoints': len(tr), 'equal_checkpoints': sum(sr.get(label) == tr.get(label) for label in sr.keys() | tr.keys()), 'public_methods': source['methods'], 'differences': differences, 'fixture_aborts': [row for result in (source, target) for row in result['records'] if row['label'].endswith(':fixture_abort')], 'comparison_digests': {name: hashlib.sha256(json.dumps(result['records'], sort_keys=True, ensure_ascii=False).encode()).hexdigest() for name, result in (('source', source), ('target', target))}, 'source_errors': {label: row['outcome']['error'] for label, row in sr.items() if 'error' in row.get('outcome', {})}, 'physical_coordinates': {'source': source['physical_coordinates'], 'target': target['physical_coordinates']}})
        receipt['final_fk_findings'] = {name: result['records'][-1]['facts']['fk_findings'] for name, result in (('source', source), ('target', target))}
        receipt['source_rowid_observations'] = {label: [{'source_rowid': row['rowid']['value'], 'task_id': row['task_id']['value']} for row in sr[label]['facts'][CLASSIFICATION]] for label in ('offline_open', 'offline_replace', 'offline_purge_multirow')}
        decisive = ('get_done', 'offline_get', 'offline_purge_multirow', 'bulk_retry_abort', 'bulk_retry_success', 'historical_upsert_existing', 'historical_missing_state', 'historical_page_second_invalid', 'historical_deferred_quota')
        receipt['observations'] = {label: {'equal': sr.get(label) == tr.get(label), 'source_outcome': sr.get(label, {}).get('outcome'), 'transactions': sr.get(label, {}).get('transactions'), 'fault_rows_touched': sr.get(label, {}).get('fault_rows_touched')} for label in decisive}
    receipt['owned_temp_removed'] = not root.exists()
    output = json.dumps(receipt, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(output)
    print(json.dumps({key: receipt[key] for key in ('source_checkpoints', 'target_checkpoints', 'equal_checkpoints', 'differences', 'fixture_aborts', 'owned_temp_removed')}, ensure_ascii=False))
    if receipt['differences'] or receipt['fixture_aborts']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
