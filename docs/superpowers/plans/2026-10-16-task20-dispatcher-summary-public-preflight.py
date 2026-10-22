#!/usr/bin/env python3
"""Bounded real adapter/summary proof on synthetic source and EMPTY target.

Target construction is bypassed only after explicit empty Tasks20 preparation.
Original public methods and their connection/transaction implementations run.
Fixture connections observe rowcount and inject labeled faults; no SQL rewrite.
"""
from __future__ import annotations

import argparse
from collections import Counter
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
CLOCK = datetime(2040, 1, 1, tzinfo=timezone.utc)
KINDS = ('reply_tasks', 'reply_task_inputs', 'meeting_alignment_jobs', 'okr_review_requests', 'work_summary_inputs')
PATCHES = (
    ('2026-10-16-task8-store-caller-proposal.patch', '691cc165fdbaaa4e56a04b907dc6c76cbbc717c4e0c3fc9f3c31a95857cc873c'),
    ('2026-10-16-task12-store-misc-caller-proposal.patch', 'aeb1ffd38995428d61f9f01bf4d0a58dfdd223b510b358ff6a7c2e01976706c4'),
    ('2026-10-16-task12-feedback-caller-proposal.patch', '9bf3323406b39fc06fff84496bd6fb2bcda6c3eb990e96a550b878e433a8105d'),
    ('2026-10-16-task20-external-writers-caller-proposal.patch', 'c52007327727e670dd55f434375b8aba3416112f369732717eba998102423415'),
)
CASES = ('reply', 'scheduled', 'meeting', 'okr', 'summary-adapter', 'channels', 'compression', 'stale-leaf', 'competing-writer', 'batch-abort')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value):
    if is_dataclass(value):
        return encoded(asdict(value))
    if hasattr(value, 'model_dump'):
        return encoded(value.model_dump(mode='json'))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return {'blob_hex': value.hex()}
    if isinstance(value, dict):
        return {str(k): encoded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encoded(v) for v in value]
    return value


def worker(args):
    external = []

    def forbid(event, details):
        if event in ('socket.connect', 'subprocess.Popen', 'os.system'):
            external.append(event)
            raise AssertionError('unexpected external action: ' + event)

    sys.path.insert(0, str(args.code))
    import app.store as sm
    import app.dispatcher.adapters as adapters
    import app.work_summary_payload as payloads
    from app.state_definitions import state_projection
    from app.task20_definitions import create_task20_schema, initialize_task8_sequence, task20_column, task20_projection
    from app.task8_storage import register_task8_functions, task8_insert_parts, task8_set_parts
    from app.task_storage import TASK_PROJECTIONS, insert_task

    # Import-time platform.architecture may run the local file utility. Guard
    # constructor/public execution after dependencies load; no Agent CLI runs.
    sys.addaudithook(forbid)

    assert Path(sm.__file__).resolve() == (args.code / 'app/store.py').resolve()
    assert Path(adapters.__file__).resolve() == (args.code / 'app/dispatcher/adapters.py').resolve()
    assert Path(payloads.__file__).resolve() == (args.code / 'app/work_summary_payload.py').resolve()
    if args.worker == 'bootstrap':
        store = sm.AutoReplyStore(args.db)
        with store._connect() as db:
            assert db.execute('select count(*) from tasks').fetchone()[0] == 0
            assert not db.execute('pragma foreign_key_check').fetchall()
            receipt = {'normal_complete_fresh_constructor': True, 'empty_tasks': 0, 'tasks_ddl_sha256': hashlib.sha256(db.execute("select sql from sqlite_schema where name='tasks'").fetchone()[0].encode()).hexdigest(), 'tables': db.execute("select count(*) from sqlite_schema where type='table'").fetchone()[0], 'foreign_keys': db.execute('pragma foreign_keys').fetchone()[0], 'external_calls': external}
        args.result.write_text(json.dumps(receipt))
        return
    if args.worker == 'prepare':
        with sqlite3.connect(args.db) as db:
            assert db.execute('select count(*) from tasks').fetchone()[0] == 0
            before = dict(db.execute("select name,sql from sqlite_schema where type='table'"))
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            db.execute('drop table tasks')
            create_task20_schema(db)
            initialize_task8_sequence(db, 0)
            after = dict(db.execute("select name,sql from sqlite_schema where type='table'"))
            assert set(before) == set(after)
            assert {k: v for k, v in before.items() if k != 'tasks'} == {k: v for k, v in after.items() if k != 'tasks'}
        args.result.write_text(json.dumps({'empty_tasks_replaced': True, 'all_other_table_ddl_exact': True, 'source_families_not_retired': True, 'task8_source_water_initialized': 0, 'migration_and_cold_constructor': 'not tested'}))
        return

    native_connect = sqlite3.connect
    trace = []
    rowcounts = []
    active = [False]
    injections = []
    injected = [False]
    current_time = [CLOCK]
    entered = Counter()
    raw_target = []
    fixture_actions = []

    def projection(kind):
        return task20_projection(kind) if args.target else TASK_PROJECTIONS[kind]

    def leaf_set(db, row_id, value):
        if args.target:
            parts = task8_set_parts('work_summary_inputs', {'payload_json': value})
            return sqlite3.Connection.execute(db, f'update tasks set {parts.sql} where id=?', (*parts.parameters, row_id))
        return sqlite3.Connection.execute(db, 'update tasks set payload_json=? where id=?', (value, row_id))

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            native_update = sql.lstrip().lower().startswith('update tasks ')
            if active[0] and args.case in ('stale-leaf', 'competing-writer') and native_update and "record_type='work_summary_inputs'" in sql and not injected[0]:
                injected[0] = True
                row_id = parameters[1]
                if args.case == 'stale-leaf':
                    changed = json.dumps({'body': 'different intervening value ' * 100}, separators=(',', ':'))
                    leaf_set(self, row_id, changed)
                    injections.append({'mode': 'same-transaction stale-leaf seam before original CAS', 'row_id': row_id, 'changed_leaf_sha256': hashlib.sha256(changed.encode()).hexdigest()})
                else:
                    other = native_connect(args.db, timeout=0)
                    if args.target:
                        register_task8_functions(other)
                    try:
                        leaf_set(other, row_id, 'competing writer value')
                    except sqlite3.OperationalError as exc:
                        assert str(exc) == 'database is locked'
                        injections.append({'mode': 'actual second writer inside BEGIN IMMEDIATE', 'error': type(exc).__name__, 'message': str(exc)})
                    else:
                        raise AssertionError('competing writer unexpectedly acquired write lock')
                    finally:
                        other.close()
            cursor = super().execute(sql, parameters)
            if active[0] and native_update:
                rowcounts.append({'table': 'tasks', 'rowcount': cursor.rowcount})
            return cursor

    def observed_connect(*pos, **kw):
        assert 'factory' not in kw
        connection = native_connect(*pos, factory=ObservedConnection, **kw)
        connection.set_trace_callback(lambda sql: trace.append(sql) if active[0] else None)
        return connection

    sqlite3.connect = observed_connect
    original_open = sm.AutoReplyStore._open_connection

    def fixture_open(self):
        db = original_open(self)
        db.create_function('current_timestamp', 0, lambda: current_time[0].strftime('%Y-%m-%d %H:%M:%S'))
        if args.target:
            register_task8_functions(db)
        return db

    sm.AutoReplyStore._open_connection = fixture_open
    if args.target:
        sm.AutoReplyStore._ensure_initialized = lambda self: None
    store = sm.AutoReplyStore(args.db)

    def profile(frame, event, _arg):
        if event == 'call' and frame.f_code.co_filename in (adapters.__file__, payloads.__file__):
            entered[frame.f_code.co_qualname] += 1

    sys.setprofile(profile)

    def seed(kind, values):
        with store._immediate_write_transaction() as db:
            if args.target:
                parts = task8_insert_parts(kind, values)
                cursor = db.execute(f'insert into tasks ({parts.columns}) values ({parts.values})', parts.parameters)
            else:
                cursor = insert_task(db, kind, values)
            result = cursor.lastrowid
        fixture_actions.append({'action': 'seed source-shaped finite synthetic row', 'kind': kind, 'physical_id': result})
        return result

    def set_fields(kind, row_id, values):
        with store._immediate_write_transaction() as db:
            if args.target:
                parts = task8_set_parts(kind, values)
                db.execute(f'update tasks set {parts.sql} where id=?', (*parts.parameters, row_id))
            else:
                db.execute('update tasks set ' + ','.join(key + '=?' for key in values) + ' where id=?', (*values.values(), row_id))
        fixture_actions.append({'action': 'explicit fixture fact change', 'kind': kind, 'physical_id': row_id, 'values': encoded(values)})

    def seed_reply(channel, key):
        common = {'channel': channel, 'conversation_id': key, 'conversation_title': 'Synthetic conversation', 'single_chat': 0, 'trigger_message_id': key + ':message', 'trigger_create_time': CLOCK.isoformat(), 'trigger_sender': 'synthetic-sender', 'business_object_key': key}
        row = seed('reply_tasks', common | {'attempts': 3, 'input_version': 7, 'claimed_input_version': 2, 'execution_generation': 'fixture-generation', 'created_at': CLOCK.strftime('%Y-%m-%d %H:%M:%S'), 'updated_at': CLOCK.strftime('%Y-%m-%d %H:%M:%S')})
        first = seed('reply_task_inputs', common | {'parent_id': row, 'trigger_text': 'old snapshot', 'input_revision_key': 'old'})
        second = seed('reply_task_inputs', common | {'parent_id': row, 'trigger_text': 'current snapshot', 'input_revision_key': 'current'})
        set_fields('reply_tasks', row, {'current_input_id': second, 'claimed_input_id': first})
        return row

    def leaf_meta(value):
        raw = value.encode() if isinstance(value, str) else bytes(value)
        result = {'type': type(value).__name__, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        try:
            result['decoded_sha256'] = hashlib.sha256(payloads.decode_work_summary_payload(value).encode()).hexdigest()
        except Exception as exc:
            result['decode_error'] = type(exc).__name__
        return result

    def facts():
        with store._connect() as db:
            result = {}
            for kind in KINDS:
                rows = db.execute(f'select * from ({projection(kind)}) order by id').fetchall()
                result[kind] = [{key: {'value': leaf_meta(row[key]) if kind == 'work_summary_inputs' and key == 'payload_json' else encoded(row[key]), 'scalar_type': type(row[key]).__name__} for key in row.keys()} for row in rows]
            result['physical_tasks'] = [list(row) for row in db.execute('select id,record_type,legacy_key,parent_id,current_input_id,claimed_input_id from tasks order by id')]
            result['leases'] = [encoded(dict(row)) for row in db.execute('select * from (' + state_projection('dispatcher_claim_leases', include_rowid=True) + ') order by adapter_name,source_id')]
            result['fk_findings'] = [list(row) for row in db.execute('pragma foreign_key_check')]
            assert not result['fk_findings']
            if args.target:
                for row in db.execute("select id,payload_json from tasks where record_type='work_summary_inputs' order by id"):
                    value = json.loads(row['payload_json'])
                    assert isinstance(value, dict)
                    raw_target.append({'id': row['id'], 'outer_type': type(row['payload_json']).__name__, 'outer_json_valid': True, 'siblings_sha256': hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != 'payload_json'}, sort_keys=True).encode()).hexdigest(), 'business_leaf_json_type': type(value['payload_json']).__name__, 'business_leaf_json_sha256': hashlib.sha256(json.dumps(value['payload_json'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()})
            return result

    records = []
    expected_errors = set()

    def call(label, receiver, method, *pos, expect_error=False, **kw):
        if expect_error:
            expected_errors.add(label)
        before = facts()
        trace.clear()
        rowcounts.clear()
        active[0] = True
        try:
            result = getattr(receiver, method)(*pos, **kw)
            outcome = {'return': encoded(result)}
        except Exception as exc:
            result = None
            outcome = {'error': {'type': type(exc).__name__, 'message': str(exc)}}
        finally:
            active[0] = False
        transactions = [sql.strip().upper() for sql in trace if sql.strip().upper() in ('BEGIN', 'BEGIN IMMEDIATE', 'COMMIT', 'ROLLBACK')]
        after = facts()
        name = getattr(receiver, '__name__', type(receiver).__name__) + '.' + method
        records.append({'label': label, 'method': name, 'outcome': outcome, 'transactions': transactions, 'native_task_rowcounts': list(rowcounts), 'committed_before_sha256': hashlib.sha256(json.dumps(before, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), 'committed_after_sha256': hashlib.sha256(json.dumps(after, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), 'facts': after})
        assert ('error' in outcome) == expect_error, (label, outcome)
        if label in ('claim_before_expiry', 'expired_live_owner', 'native_ignore_zero_claim', 'handler_backoff'):
            assert before == after, (label, 'empty claim changed committed facts')
        if expect_error:
            if args.case != 'batch-abort':
                assert before == after, (label, 'failed call changed committed facts')
            assert not transactions or transactions[-1] == 'ROLLBACK'
        return result

    def field(kind, key):
        return records[-1]['facts'][kind][0][key]['value']

    def stamp(seconds):
        return CLOCK + timedelta(seconds=seconds)

    if args.case in ('reply', 'scheduled', 'meeting', 'okr', 'summary-adapter'):
        if args.case in ('reply', 'scheduled'):
            kind = 'reply_tasks'
            seed_reply('email', 'excluded-email')
            seed_reply('dingtalk' if args.case == 'scheduled' else 'scheduled', 'excluded-other-adapter')
            row_id = seed_reply('scheduled' if args.case == 'scheduled' else 'dingtalk', 'eligible')
            cls = adapters.ScheduledExecutionQueueAdapter if args.case == 'scheduled' else adapters.ReplyQueueAdapter
        elif args.case == 'meeting':
            kind = 'meeting_alignment_jobs'
            row_id = seed(kind, {'meeting_id': 'fixture-meeting', 'title': 'Synthetic', 'source_json': '{}', 'participants_json': '[]', 'ended_at': '2039-12-31T23:00:00+00:00', 'eligible_at': '2039-12-31 23:30:00', 'status': 'pending', 'attempts': 3})
            cls = adapters.MeetingQueueAdapter
        elif args.case == 'okr':
            kind = 'okr_review_requests'
            row_id = seed(kind, {'conversation_id': 'okr', 'conversation_title': 'Synthetic', 'trigger_message_id': 'okr-message', 'trigger_sender': 'synthetic', 'trigger_sender_user_id': 'synthetic', 'trigger_text': 'Synthetic request', 'period_label': 'Q', 'period_start': '2039-10-01', 'period_end': '2039-12-31', 'okr_source_json': '{}'})
            cls = adapters.OkrReviewQueueAdapter
        else:
            kind = 'work_summary_inputs'
            row_id = seed(kind, {'source_type': 'local_file', 'source_ref': 'adapter-summary', 'payload_json': '{}', 'attempts': 3})
            cls = adapters.WorkSummaryQueueAdapter
        alive = set()
        owner_probes = []

        def owner_alive(pid):
            assert pid in (41, 42, 43, 44), pid
            owner_probes.append(pid)
            return pid in alive

        adapter = cls(store, owner_alive=owner_alive)
        call('metrics_initial', adapter, 'metrics', CLOCK)
        call('invalid_owner', adapter, 'claim', CLOCK, owner='', owner_pid=41, lease=timedelta(seconds=10), expect_error=True)
        first = call('claim_first', adapter, 'claim', CLOCK, owner='A', owner_pid=41, lease=timedelta(seconds=10))
        assert first is not None and first.source_id == str(row_id)
        assert first.attempt == (1 if args.case == 'okr' else 4)
        if kind == 'reply_tasks':
            selected = next(row for row in records[-1]['facts'][kind] if row['id']['value'] == row_id)
            assert selected['claimed_input_version']['value'] == selected['input_version']['value'] == 7
            assert selected['claimed_input_id']['value'] == selected['current_input_id']['value']
        call('wrong_owner_assert', adapter, 'assert_current', first, owner='wrong', now=stamp(1), expect_error=True)
        call('wrong_owner_release', adapter, 'release', first, owner='wrong', now=stamp(1), expect_error=True)
        call('renew', adapter, 'renew', first, owner='A', now=stamp(2), lease=timedelta(seconds=10))
        assert call('claim_before_expiry', adapter, 'claim', stamp(11), owner='B', owner_pid=42, lease=timedelta(seconds=10)) is None
        call('renew_at_expiry', adapter, 'renew', first, owner='A', now=stamp(12), lease=timedelta(seconds=10), expect_error=True)
        alive.add(41)
        assert call('expired_live_owner', adapter, 'claim', stamp(12), owner='B', owner_pid=42, lease=timedelta(seconds=10)) is None
        alive.clear()
        second = call('expired_dead_owner_reclaim', adapter, 'claim', stamp(12), owner='B', owner_pid=42, lease=timedelta(seconds=10))
        assert second is not None and second.generation == 2
        assert second.attempt == (2 if args.case == 'okr' else 5)
        call('stale_generation_release', adapter, 'release', first, owner='A', now=stamp(13), expect_error=True)
        call('forged_generation_release', adapter, 'release', replace(second, generation=99), owner='B', now=stamp(13), expect_error=True)
        set_fields(kind, row_id, {'status': 'pending'})
        call('status_zero_release_rolls_back_lease', adapter, 'release', second, owner='B', now=stamp(13), expect_error=True)
        assert records[-1]['native_task_rowcounts'] == [{'table': 'tasks', 'rowcount': 0}]
        assert records[-1]['transactions'] == ['BEGIN IMMEDIATE', 'ROLLBACK']
        set_fields(kind, row_id, {'status': 'processing'})
        call('release', adapter, 'release', second, owner='B', now=stamp(13))
        third = call('claim_after_release', adapter, 'claim', stamp(14), owner='C', owner_pid=43, lease=timedelta(seconds=10))
        assert third is not None and third.generation == 3
        if args.case != 'okr':
            set_fields(kind, row_id, {'attempts': 0})
        call('release_zero_attempt_floor', adapter, 'release', third, owner='C', now=stamp(15))
        if args.case != 'okr':
            selected = next(row for row in records[-1]['facts'][kind] if row['id']['value'] == row_id)
            assert selected['attempts']['value'] == 0
        new_status = 'new.' + (task20_column(kind, 'status') if args.target else 'status')
        with store._connect() as db:
            db.execute(f"create trigger fixture_ignore_claim before update on tasks when new.id={row_id} and {new_status}='processing' begin select raise(ignore); end")
        assert call('native_ignore_zero_claim', adapter, 'claim', stamp(16), owner='D', owner_pid=44, lease=timedelta(seconds=10)) is None
        assert records[-1]['native_task_rowcounts'] == [{'table': 'tasks', 'rowcount': 0}]
        with store._connect() as db:
            db.execute('drop trigger fixture_ignore_claim')
            db.execute("create trigger fixture_abort_lease before update on state when new.record_type='dispatcher_claim_leases' begin select raise(abort,'fixture lease abort'); end")
        call('lease_abort_rolls_back_task_update', adapter, 'claim', stamp(16), owner='D', owner_pid=44, lease=timedelta(seconds=10), expect_error=True)
        assert records[-1]['native_task_rowcounts'] == [{'table': 'tasks', 'rowcount': 1}]
        assert records[-1]['transactions'] == ['BEGIN IMMEDIATE', 'ROLLBACK']
        with store._connect() as db:
            db.execute('drop trigger fixture_abort_lease')
        final = call('claim_after_abort', adapter, 'claim', stamp(16), owner='D', owner_pid=44, lease=timedelta(seconds=10))
        assert final is not None and final.generation == 4
        call('final_assert_current', adapter, 'assert_current', final, owner='D', now=stamp(17))
        if args.case in ('reply', 'scheduled'):
            # Public handler failure path retains its current attempt budget.
            call('handler_retry', adapter, 'handle_handler_error', final, owner='D', now=stamp(17), error='synthetic handler failure')
            assert call('handler_backoff', adapter, 'claim', stamp(18), owner='D', owner_pid=44, lease=timedelta(seconds=10)) is None
            again = call('handler_second_attempt', adapter, 'claim', stamp(77), owner='D', owner_pid=44, lease=timedelta(seconds=10))
            assert again is not None and again.attempt == 2
            call('handler_retry_twice', adapter, 'handle_handler_error', again, owner='D', now=stamp(78), error='synthetic handler failure')
            last = call('handler_third_attempt', adapter, 'claim', stamp(198), owner='D', owner_pid=44, lease=timedelta(seconds=10))
            assert last is not None and last.attempt == 3
            call('handler_terminal', adapter, 'handle_handler_error', last, owner='D', now=stamp(199), error='synthetic handler failure')
            selected = next(row for row in records[-1]['facts'][kind] if row['id']['value'] == row_id)
            assert selected['status']['value'] == 'failed' and selected['attempts']['value'] == 3
        extra = {'owner_liveness_probes': owner_probes}
    elif args.case == 'channels':
        expected = {channel: seed_reply(channel, channel) for channel in ('email', 'scheduled', 'dingtalk', 'wechat')}
        reply = adapters.ReplyQueueAdapter(store, owner_alive=lambda pid: False)
        scheduled = adapters.ScheduledExecutionQueueAdapter(store, owner_alive=lambda pid: False)
        first = call('channel_dingtalk', reply, 'claim', CLOCK, owner='A', owner_pid=41, lease=timedelta(seconds=10))
        second = call('channel_wechat', reply, 'claim', CLOCK, owner='A', owner_pid=41, lease=timedelta(seconds=10))
        third = call('channel_scheduled_override', scheduled, 'claim', CLOCK, owner='B', owner_pid=42, lease=timedelta(seconds=10))
        assert [int(v.source_id) for v in (first, second, third)] == [expected[c] for c in ('dingtalk', 'wechat', 'scheduled')]
        assert call('channel_email_excluded', reply, 'claim', CLOCK, owner='A', owner_pid=41, lease=timedelta(seconds=10)) is None
        extra = {'channel_expected_ids': expected}
    else:
        large = json.dumps({'body': 'synthetic unicode 重复正文 ' * 500}, ensure_ascii=False, separators=(',', ':'))
        for index, body, status in ((1, large, 'pending'), (2, '{}', 'pending'), (3, large, 'done'), (4, payloads.encode_work_summary_payload(large), 'processing'), (5, large, 'pending')) if args.case == 'compression' else ((1, large, 'pending'), (2, large, 'pending')) if args.case == 'batch-abort' else ((1, large, 'pending'),):
            seed('work_summary_inputs', {'source_type': 'local_file', 'source_ref': f'compression:{index}', 'payload_json': body, 'status': status, 'error': 'retain sibling sentinel', 'body_sha256': 'fixture-sha-sentinel', 'body_bytes': 54321, 'source_created_at': '2039-12-30T12:00:00Z'})
        initial = facts()
        if args.case == 'batch-abort':
            with store._connect() as db:
                db.execute("create trigger fixture_abort_second before update on tasks when old.id=2 begin select raise(abort,'fixture second batch abort'); end")
        result = call('compression_first' if args.case == 'compression' else args.case, payloads, 'migrate_work_summary_payloads', args.db, batch_size=1, expect_error=args.case == 'batch-abort')
        if args.case == 'compression':
            assert result['compressed'] == 2 and result['scanned'] == 4
            assert [r['rowcount'] for r in records[-1]['native_task_rowcounts']] == [1, 1]
            call('compression_idempotent', payloads, 'migrate_work_summary_payloads', args.db, batch_size=2)
            assert records[-1]['outcome']['return']['compressed'] == 0
            call('compression_invalid_batch', payloads, 'migrate_work_summary_payloads', args.db, batch_size=0, expect_error=True)
        elif args.case == 'stale-leaf':
            assert injected[0] and result['compressed'] == 0
            assert records[-1]['native_task_rowcounts'] == [{'table': 'tasks', 'rowcount': 0}]
        elif args.case == 'competing-writer':
            assert injected[0] and result['compressed'] == 1
            assert injections[0]['message'] == 'database is locked'
        elif args.case == 'batch-abort':
            assert records[-1]['facts']['work_summary_inputs'][0]['payload_json']['value']['type'] == 'bytes'
            assert records[-1]['facts']['work_summary_inputs'][1]['payload_json']['value']['type'] == 'str'
            assert records[-1]['transactions'] == ['BEGIN IMMEDIATE', 'COMMIT', 'BEGIN IMMEDIATE', 'ROLLBACK']
        elif args.case == 'negative':
            assert result['compressed'] == (0 if args.mutant else 1)
        final = facts()
        for before, after in zip(initial['work_summary_inputs'], final['work_summary_inputs'], strict=True):
            assert {k: v for k, v in before.items() if k != 'payload_json'} == {k: v for k, v in after.items() if k != 'payload_json'}
        if args.target:
            siblings = {}
            for witness in raw_target:
                assert siblings.setdefault(witness['id'], witness['siblings_sha256']) == witness['siblings_sha256']
        extra = {'injections': injections, 'sibling_fields_exact': True, 'original_body_bytes': len(large.encode())}
    sys.setprofile(None)
    assert not external
    args.result.write_text(json.dumps({'records': records, 'entered_actual_owners': dict(entered), 'raw_target_wrapper_witnesses': raw_target, 'fixture_actions': fixture_actions, 'external_calls': external, **extra}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', choices=('bootstrap', 'prepare', 'scenario'))
    parser.add_argument('--case', choices=(*CASES, 'negative'))
    parser.add_argument('--code', type=Path)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--target', action='store_true')
    parser.add_argument('--mutant', action='store_true')
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    assert Path(sys.executable).resolve() == PYTHON.resolve()
    script = Path(__file__).resolve()
    proposal = json.loads((args.repo / 'docs/superpowers/plans/2026-10-16-task20-external-writers-caller-proposal-analysis.json').read_text())
    source_files = {f: digest(args.repo / f) for f in ('app/store.py', 'app/task_storage.py', 'app/task8_storage.py', 'app/task20_definitions.py', 'app/task12_storage.py', *proposal['scope'])}
    assert source_files['app/store.py'] == 'ce66a4dd7302080126fb178b4dc8e3983acbc68b2f70c9107bd3e584c4207d5f'
    for f, value in proposal['files'].items():
        assert source_files[f] == value['source_sha256']
    patch_paths = [args.repo / 'docs/superpowers/plans' / name for name, _ in PATCHES]
    for patch, (_, expected) in zip(patch_paths, PATCHES, strict=True):
        assert digest(patch) == expected
    evidence = {'harness_sha256': digest(script), 'source_files': source_files, 'patches': dict(PATCHES), 'scope': 'five real Tasks8 queue adapters plus real work_summary_payload migration, synthetic fresh source and prepared EMPTY Tasks20 target only', 'constructor_boundary': 'Original full fresh source constructor; target only _ensure_initialized bypass after explicit empty Tasks replacement; original constructor attributes, _open_connection, _connect and immediate transaction bodies retained', 'external_action_guard_boundary': 'Python audit hook rejects socket connections/subprocesses/os.system during constructors and public calls. Dependencies import first because croniter platform.architecture may invoke local file metadata utility; no Agent/provider CLI is invoked.', 'fixture_connection_instrumentation': 'Native sqlite3.Connection subclass records actual execute.rowcount and trace; existing connect args/PRAGMAs remain. Same-connection stale-leaf seam injects only an explicit fixture write immediately before original CAS SQL; separate actual competing-writer lock control. No application method body or SQL is reimplemented.', 'normalization': ['Deterministic SQLite current_timestamp only.', 'No new Task12 rows seeded: published-eight row identities, physical pointers, values/types and State lease facts compare exactly without ID normalization.', 'Payload bodies represented by exact stored-byte SHA256/type/length and decoded SHA256; no semantic-only equality.'], 'limitations': ['No nonempty migration, actual production profile, copied preserved rows, inverse/current-fact/predecessor/reforward/cold target constructor/native/UI/final11 proof.', 'Other six external-writer modules and native scheduled-definition queue are not public-tested; this covers five adapter classes over four relevant Tasks kinds and their input children.', 'Explicit fixture triggers force native UPDATE IGNORE and lease-write ABORT. Readonly/provider/native/worker behavior is outside scope; no worker, provider or CLI is started.', 'BEGIN IMMEDIATE prevents a concurrent committed change during a batch. Stale-leaf CAS uses a disclosed same-transaction injection, not a claimed successful concurrent writer.'], 'no_app_tests_live_db_network_provider_worker_native_cli_main_push_deploy': True}
    env = os.environ | {'PYTHONDONTWRITEBYTECODE': '1'}
    with tempfile.TemporaryDirectory(prefix='task20-dispatcher-summary-public-', dir='/tmp') as temp:
        root = Path(temp)
        source, target = root / 'source', root / 'target'
        for code in (source, target):
            shutil.copytree(args.repo / 'app', code / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))

        def run(stage, code, db, target_side=False, case=None, mutant=False):
            result = root / f'{stage}-{case}-{code.name}-{db.stem}.json'
            cmd = [str(PYTHON), '-B', str(script), '--worker', stage, '--code', str(code), '--db', str(db), '--result', str(result)]
            if case:
                cmd.extend(('--case', case))
            if target_side:
                cmd.append('--target')
            if mutant:
                cmd.append('--mutant')
            proc = subprocess.run(cmd, cwd=code, env=env, capture_output=True, text=True, timeout=120)
            if proc.returncode:
                raise RuntimeError(f'{stage}/{case}/{code.name}: {proc.stderr[-9000:]}')
            return json.loads(result.read_text())

        def clone(origin, dest):
            with sqlite3.connect(origin) as src, sqlite3.connect(dest) as dst:
                src.backup(dst)

        source_template, target_template = source / 'empty.sqlite3', target / 'empty.sqlite3'
        evidence['bootstrap'] = run('bootstrap', source, source_template)
        clone(source_template, target_template)
        for patch in patch_paths:
            subprocess.run(['git', 'apply', '--check', str(patch)], cwd=target, capture_output=True, check=True)
            subprocess.run(['git', 'apply', str(patch)], cwd=target, capture_output=True, check=True)
        assert digest(target / 'app/store.py') == '13dbf3724919fbeac89b7d2aca895ca2651339b3fcb35a322e56ef10d108d55b'
        for f, value in proposal['files'].items():
            assert digest(target / f) == value['candidate_sha256']
        evidence['prepared_target'] = run('prepare', target, target_template, True)
        all_records = {'source': [], 'target': []}
        case_receipts = {}
        for case in CASES:
            paired = {}
            for side, code, template in (('source', source, source_template), ('target', target, target_template)):
                db = code / f'{case}.sqlite3'
                clone(template, db)
                paired[side] = run('scenario', code, db, side == 'target', case)
                all_records[side].extend([{'case': case, **record} for record in paired[side]['records']])
            assert paired['source']['records'] == paired['target']['records'], (case, [(a['label'], [k for k in a if a[k] != b[k]]) for a, b in zip(paired['source']['records'], paired['target']['records'], strict=True) if a != b])
            assert paired['source']['fixture_actions'] == paired['target']['fixture_actions']
            case_receipts[case] = {'equal_checkpoints': len(paired['source']['records']), 'labels': [r['label'] for r in paired['source']['records']], 'entered_actual_owners': paired['target']['entered_actual_owners'], 'expected_errors': {r['label']: r['outcome']['error'] for r in paired['source']['records'] if 'error' in r['outcome']}, 'decisive': {r['label']: {k: r[k] for k in ('outcome', 'transactions', 'native_task_rowcounts', 'committed_before_sha256', 'committed_after_sha256')} for r in paired['target']['records'] if r['label'] in ('claim_first', 'expired_dead_owner_reclaim', 'status_zero_release_rolls_back_lease', 'native_ignore_zero_claim', 'lease_abort_rolls_back_task_update', 'handler_terminal', 'compression_first', 'stale-leaf', 'competing-writer', 'batch-abort')}, 'injections': paired['target'].get('injections', []), 'raw_target_wrapper_witnesses': paired['target']['raw_target_wrapper_witnesses'] if case in ('compression', 'stale-leaf', 'competing-writer', 'batch-abort') else []}
        # Retain compact old/new headers, native physical pointers and lease
        # facts for the decisive real method transitions; no body copies.
        for case, receipt in case_receipts.items():
            wanted = {'metrics_initial', 'claim_first', 'claim_before_expiry', 'renew_at_expiry', 'expired_live_owner', 'expired_dead_owner_reclaim', 'status_zero_release_rolls_back_lease', 'release', 'release_zero_attempt_floor', 'native_ignore_zero_claim', 'lease_abort_rolls_back_task_update', 'claim_after_abort', 'handler_terminal'}
            transitions = []
            focus_id = next((int(record['outcome']['return']['source_id']) for record in all_records['target'] if record['case'] == case and record['label'] == 'claim_first'), None)
            for record in all_records['target']:
                if record['case'] != case or record['label'] not in wanted:
                    continue
                facts = record['facts']
                keys = {'id', 'channel', 'status', 'attempts', 'input_version', 'claimed_input_version', 'current_input_id', 'claimed_input_id', 'locked_at', 'available_at', 'execution_generation'}
                headers = {kind: [{k: v['value'] for k, v in row.items() if k in keys} for row in facts[kind] if row['id']['value'] == focus_id] for kind in KINDS if kind != 'reply_task_inputs' and facts[kind]}
                headers = {kind: rows for kind, rows in headers.items() if rows}
                physical = [row for row in facts['physical_tasks'] if row[0] == focus_id or row[3] == focus_id]
                transitions.append({'label': record['label'], 'headers_sha256': hashlib.sha256(json.dumps(headers, sort_keys=True, ensure_ascii=False).encode()).hexdigest(), 'physical_pointer_rows_sha256': hashlib.sha256(json.dumps(physical, sort_keys=True).encode()).hexdigest(), 'leases_sha256': hashlib.sha256(json.dumps(facts['leases'], sort_keys=True, ensure_ascii=False).encode()).hexdigest(), 'committed_before_sha256': record['committed_before_sha256'], 'committed_after_sha256': record['committed_after_sha256']})
            receipt['transition_readback_witnesses'] = transitions
            raw = receipt['raw_target_wrapper_witnesses']
            receipt['raw_target_wrapper_witnesses'] = list({json.dumps(row, sort_keys=True): row for row in raw}.values())
        # Run successful native controls before the single finite caller mutant.
        controls = {}
        for side, code, template in (('source', source, source_template), ('target', target, target_template)):
            db = code / 'control.sqlite3'
            clone(template, db)
            controls[side] = run('scenario', code, db, side == 'target', 'negative')
        assert controls['source']['records'] == controls['target']['records']
        mutant = root / 'mutant'
        shutil.copytree(target / 'app', mutant / 'app')
        mod = mutant / 'app/work_summary_payload.py'
        before = "{task20_column('work_summary_inputs', 'payload_json')}=?"
        text = mod.read_text()
        assert text.count(before) == 1
        mod.write_text(text.replace(before, 'payload_json=?'))
        mutant_db = mutant / 'control.sqlite3'
        clone(target_template, mutant_db)
        broken = run('scenario', mutant, mutant_db, True, 'negative', True)
        assert controls['source']['records'][0]['outcome']['return']['compressed'] == controls['target']['records'][0]['outcome']['return']['compressed'] == 1
        assert broken['records'][0]['outcome']['return']['compressed'] == 0
        assert broken['records'][0]['facts'] != controls['target']['records'][0]['facts']
        evidence['negative_control'] = {'mutation': 'Only original summary CAS RHS column becomes outer structural payload_json instead of generated business leaf; SET and actual routine unchanged', 'successful_source': controls['source']['records'][0]['outcome'], 'successful_target': controls['target']['records'][0]['outcome'], 'mutant': broken['records'][0]['outcome'], 'mutant_native_rowcounts': broken['records'][0]['native_task_rowcounts'], 'caught_by_counter_and_stored_leaf_readback': True}
        evidence['cases'] = case_receipts
        evidence['equal_checkpoints'] = len(all_records['source'])
        evidence['public_methods'] = sorted({r['method'] for r in all_records['source']})
        evidence['record_sha256'] = {side: hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest() for side, records in all_records.items()}
        assert evidence['record_sha256']['source'] == evidence['record_sha256']['target']
        assert evidence['equal_checkpoints'] == 122
        assert len(evidence['public_methods']) == 28
        assert {case: receipt['equal_checkpoints'] for case, receipt in case_receipts.items()} == dict(zip(CASES, (26, 26, 20, 20, 20, 4, 3, 1, 1, 1), strict=True))
        evidence['coverage_assertions'] = {'fixed_equal_checkpoints': 122, 'fixed_public_methods': 28, 'five_adapter_classes': True, 'source_and_target_native_compression_controls': True, 'single_wrong_outer_CAS_mutant_killed': True, 'no_equal_errors_as_success': True, 'all_error_committed_facts_exact_except_explicit_prior_batch_commit': True}
        evidence['differences'] = []
        evidence['unexpected_external_calls'] = []
    evidence['owned_temp_removed'] = not root.exists()
    if args.output:
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: evidence[k] for k in ('equal_checkpoints', 'differences', 'owned_temp_removed')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
