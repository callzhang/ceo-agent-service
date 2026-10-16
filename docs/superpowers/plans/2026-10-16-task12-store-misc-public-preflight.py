#!/usr/bin/env python3
"""Real Store public API parity on synthetic fresh source and empty target fixtures.

Explicit fixture-only initialization bypass follows empty target preparation. No
application source is edited; copied processes own all SQLite databases/code.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from uuid import UUID

PYTHON = Path('/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python')
CLOCK = '2040-01-01 00:00:00'
FAMILIES = ('business_object_tasks', 'oa_notification_events', 'okr_review_items', 'wechat_memory_import_jobs')
PUBLISHED = ('reply_tasks', 'reply_task_inputs', 'okr_review_requests')
PATCHES = ('2026-10-16-task8-store-caller-proposal.patch', '2026-10-16-task12-store-misc-caller-proposal.patch')
EXPECTED_OWNERS = ('_ensure_business_reply_task', '_operation_logs_base_query', '_runtime_operation_parent_exists', 'adopt_oa_notification_events', 'begin_wechat_memory_import_job', 'claim_oa_reminder_target', 'count_current_unresolved_problem_attempts', 'finish_wechat_memory_import_job', 'get_reply_task_for_business_object', 'list_current_unresolved_problem_attempt_summaries', 'list_current_unresolved_problem_attempts', 'list_pending_oa_reminder_targets', 'mark_oa_reminder_failed', 'mark_oa_reminder_sent', 'reconcile_valid_needs_human_projections', 'record_oa_notification_event', 'record_okr_review_item', 'replace_pending_single_chat_reply_task_trigger', 'skip_failed_reply_tasks_superseded_by_terminal_business_object')
SOURCE_SHA = 'ce66a4dd7302080126fb178b4dc8e3983acbc68b2f70c9107bd3e584c4207d5f'
TARGET_SHA = '705c8f0e73ff9df17cb5f8118da96e99f0b520f9b0c4b6895e67b05a94cdd849'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value):
    if hasattr(value, 'model_dump'):
        return encoded(value.model_dump(mode='json'))
    if isinstance(value, bytes):
        return {'blob_hex': value.hex()}
    if isinstance(value, dict):
        return {str(k): encoded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encoded(v) for v in value]
    return value


def worker(args):
    sys.path.insert(0, str(args.code))
    import app.store as sm
    from app.store import AutoReplyStore
    from app.task20_definitions import TASK20_FIELDS, create_task20_schema, initialize_task8_sequence
    from app.task12_storage import task12_projection

    if args.worker == 'bootstrap':
        store = AutoReplyStore(args.db)
        with store._connect() as db:
            counts = {kind: db.execute(f'select count(*) from {kind}').fetchone()[0] for kind in ('tasks', *FAMILIES)}
            assert counts == dict.fromkeys(counts, 0), counts
            tables = {r[0]: r[1] for r in db.execute("select name,sql from sqlite_schema where type='table'")}
            source_fields = {kind: [r[1] for r in db.execute(f'pragma table_info({kind})')] for kind in FAMILIES}
            assert {kind: tuple(fields) for kind, fields in source_fields.items()} == {kind: TASK20_FIELDS[kind] for kind in FAMILIES}
            assert sum(map(len, source_fields.values())) == 41
            foreign_keys = [list(r) for r in db.execute('pragma foreign_key_check')]
            assert not foreign_keys
        args.result.write_text(json.dumps({'constructor': 'original AutoReplyStore full fresh normal constructor', 'empty_counts': counts, 'source_fields': source_fields, 'tables': sorted(tables), 'tasks_ddl_sha256': hashlib.sha256(tables['tasks'].encode()).hexdigest(), 'fk_findings': foreign_keys}))
        return
    if args.worker == 'prepare':
        with sqlite3.connect(args.db) as db:
            assert all(db.execute(f'select count(*) from {kind}').fetchone()[0] == 0 for kind in ('tasks', *FAMILIES))
            before = dict(db.execute("select name,sql from sqlite_schema where type='table'"))
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            db.execute('drop table tasks')
            create_task20_schema(db)
            initialize_task8_sequence(db, 0)
            for kind in FAMILIES:
                db.execute(f'drop table {kind}')
            after = dict(db.execute("select name,sql from sqlite_schema where type='table'"))
            assert set(before) - set(after) == set(FAMILIES)
            assert {k: v for k, v in before.items() if k not in (*FAMILIES, 'tasks')} == {k: v for k, v in after.items() if k != 'tasks'}
        args.result.write_text(json.dumps({'empty_tasks_replaced': True, 'dropped_only_empty_families': list(FAMILIES), 'all_other_table_ddl_exact': True, 'published_sequence_initialized': 0, 'constructor_selection': 'not tested: fixture _ensure_initialized bypass only after preparation'}))
        return

    fixture_clock = [CLOCK]
    original_open = AutoReplyStore._open_connection

    def fixture_open(self):
        db = original_open(self)
        db.create_function('current_timestamp', 0, lambda: fixture_clock[0])
        if args.target:
            from app.task8_storage import register_task8_functions
            register_task8_functions(db)
        return db

    AutoReplyStore._open_connection = fixture_open
    if args.target:
        AutoReplyStore._ensure_initialized = lambda self: None
    original_time = sm._utc_store_time
    sm._utc_store_time = lambda now=None: original_time(fixture_clock[0] if now is None else now)
    counter = iter(range(1, 10000))
    sm.uuid4 = lambda: UUID(int=next(counter))
    store = AutoReplyStore(args.db)
    trace = []
    records = []
    methods = set()
    entered_owners = Counter()
    identities = {kind: {} for kind in PUBLISHED}
    physical_witness = []
    issuances = []

    def token(kind, value):
        if value is None:
            return None
        return identities[kind].get(value, value)

    def logical_value(kind, field, value):
        if kind in PUBLISHED and field == 'id':
            return token(kind, value)
        if field == 'reply_task_id' and kind in {'reply_task_inputs', 'business_object_tasks'}:
            return token('reply_tasks', value)
        if kind == 'reply_tasks' and field in {'current_input_id', 'claimed_input_id'}:
            return token('reply_task_inputs', value)
        if kind == 'okr_review_items' and field == 'request_id':
            return token('okr_review_requests', value)
        return encoded(value)

    def facts():
        with store._connect() as db:
            logical = {}
            raw_published = {}
            published_queries = {}
            for kind, query in (('reply_tasks', sm.REPLY_TASK_SQL), ('reply_task_inputs', sm.REPLY_TASK_INPUT_SQL), ('okr_review_requests', sm.OKR_REQUEST_SQL)):
                rows = db.execute(f'select * from ({query}) order by id').fetchall()
                raw_published[kind] = rows
                published_queries[kind] = query
                for row in rows:
                    if row['id'] not in identities[kind]:
                        identities[kind][row['id']] = f'{kind}:{len(identities[kind]) + 1}'
                        issuances.append({'checkpoint': len(records) + 1, 'kind': kind, 'id': row['id'], 'token': identities[kind][row['id']]})
                if args.target:
                    pairs = db.execute('select id,legacy_key from tasks where record_type=? order by id', (kind,)).fetchall()
                    assert all(r[0] == int(r[1]) for r in pairs), (kind, pairs)
            for kind in (*PUBLISHED, *FAMILIES):
                if kind in raw_published:
                    rows = raw_published[kind]
                else:
                    query = task12_projection(kind, include_rowid=True) if args.target else f'select rowid as rowid,* from {kind}'
                    rows = db.execute(f'select * from ({query}) order by rowid').fetchall()
                fields = list(TASK20_FIELDS[kind]) if kind in PUBLISHED else ['rowid', *TASK20_FIELDS[kind]]
                logical[kind] = [[[field, logical_value(kind, field, row[field]), type(row[field]).__name__] for field in fields] for row in rows]
                projection = published_queries[kind] if kind in PUBLISHED else query
                type_sql = ','.join('typeof("' + field + '")' for field in fields)
                order = 'id' if kind in PUBLISHED else 'rowid'
                logical[kind + ':sqlite_types'] = [list(r) for r in db.execute(f'select {type_sql} from ({projection}) order by {order}')]

            for kind in ('oa_notification_events', 'okr_review_items', 'wechat_memory_import_jobs'):
                name = 'tasks:' + kind if args.target else kind
                water = db.execute('select seq from sqlite_sequence where name=?', (name,)).fetchone()
                logical[kind + ':water'] = 0 if water is None else water[0]
            # Every scoped physical reference must resolve to its exact intended kind.
            if args.target:
                for kind, parent_kind in (('business_object_tasks', 'reply_tasks'), ('reply_task_inputs', 'reply_tasks')):
                    assert not db.execute('select 1 from tasks child left join tasks parent on parent.id=child.parent_id where child.record_type=? and (parent.id is null or parent.record_type<>?)', (kind, parent_kind)).fetchall()
                structural = [list(r) for r in db.execute('select id,record_type,legacy_key,parent_id,current_input_id,claimed_input_id from tasks order by id')]
            else:
                structural = [list(r) for r in db.execute('select id,record_type,legacy_key,parent_id,current_input_id,claimed_input_id from tasks order by id')]
            for row in raw_published['reply_tasks']:
                if row['current_input_id'] is not None:
                    parent = next(r for r in raw_published['reply_task_inputs'] if r['id'] == row['current_input_id'])
                    assert parent['reply_task_id'] == row['id']
            logical['actions'] = [encoded(dict(r)) for r in db.execute("select * from actions order by id")]
            logical['runs'] = [encoded(dict(r)) for r in db.execute("select * from runs order by id")]
            logical['fk_findings'] = [list(r) for r in db.execute('pragma foreign_key_check')]
            assert logical['fk_findings'] == []
            seq = db.execute('select seq from sqlite_sequence where name=?', ('tasks:published' if args.target else 'tasks',)).fetchone()
            physical_witness.append({'checkpoint': len(records) + 1, 'physical_tasks': structural, 'published_water': 0 if seq is None else seq[0]})
            return logical

    def normalize_result(method, result):
        result = encoded(result)
        if method == 'list_reply_task_inputs':
            return [{field: logical_value('reply_task_inputs', field, value) for field, value in row.items()} for row in result]
        if method == 'create_okr_review_request':
            return token('okr_review_requests', result)
        def transform(value):
            if isinstance(value, list):
                return [transform(x) for x in value]
            if not isinstance(value, dict):
                return value
            if 'business_object_key' in value and 'execution_generation' in value and 'input_version' in value:
                return {k: logical_value('reply_tasks', k, v) for k, v in value.items()}
            if 'period_label' in value and 'okr_source_json' in value:
                return {k: logical_value('okr_review_requests', k, v) for k, v in value.items()}
            return {k: transform(v) for k, v in value.items()}
        return transform(result)

    def call(label, method, *pos, expected_error=None, **kw):
        assert label not in {r['label'] for r in records}
        methods.add(method)
        trace.clear()
        previous = AutoReplyStore._open_connection

        def traced_open(self):
            db = previous(self)
            db.set_trace_callback(trace.append)
            return db

        def observe(frame, event, arg):
            if event == 'call' and frame.f_code.co_filename == sm.__file__ and frame.f_code.co_name in EXPECTED_OWNERS:
                entered_owners[frame.f_code.co_name] += 1

        AutoReplyStore._open_connection = traced_open
        assert sys.getprofile() is None
        sys.setprofile(observe)
        try:
            result = getattr(store, method)(*pos, **kw)
            error = None
        except Exception as exc:
            result = None
            error = {'type': type(exc).__name__, 'message': str(exc), 'code': getattr(exc, 'code', None)}
        finally:
            sys.setprofile(None)
            AutoReplyStore._open_connection = previous
        assert (error is not None) == (expected_error is not None), (label, error)
        if expected_error is not None:
            assert error['type'] == expected_error[0] and error['message'] == expected_error[1], (label, error)
        txns = [sql.strip().upper() for sql in trace if sql.strip().upper() in {'BEGIN', 'BEGIN IMMEDIATE', 'COMMIT', 'ROLLBACK'}]
        current = facts()
        records.append({'label': label, 'method': method, 'outcome': {'error': error} if error else {'return': normalize_result(method, result)}, 'transactions': txns, 'facts': current})
        return result

    def event(message='oa-1', *, kind='chat_reminder', node='', process='oa-process', requires_reply=None):
        return dict(kind=kind, process_instance_id=process, task_id=node, channel='dingtalk', conversation_id='oa-chat', conversation_title='OA synthetic', trigger_message_id=message, trigger_create_time='100', trigger_sender='owner', trigger_text='adopted reminder', trigger_message_json='{"text":"OA-body","nested":{"x":1}}', oa_url='https://example.invalid/oa', requires_reply=requires_reply)

    if args.worker == 'negative':
        assert call('control_oa', 'record_oa_notification_event', **event()) == 1
        job = call('control_import', 'begin_wechat_memory_import_job', import_run_id='control-import', account_id='synthetic-account')
        assert job == 1
        runnable = call('control_runtime_parent', 'runtime_operation_parent_is_runnable', 'memory', f'wechat_memory_import_job:{job}')
        with store._connect() as db:
            physical = db.execute("select id from tasks where record_type='wechat_memory_import_jobs'").fetchone()[0] if args.target else None
        args.result.write_text(json.dumps({'job_public_id': job, 'job_physical_id': physical, 'runnable': runnable, 'public_outcome': records[-1]['outcome'], 'transactions': records[-1]['transactions']}))
        return

    # OA enqueue/IGNORE natural-key identity, input retention, order and attempt CAS.
    call('oa_bad_kind', 'record_oa_notification_event', **event(kind='invalid'), expected_error=('ValueError', 'invalid OA notification kind'))
    first = call('oa_enqueue', 'record_oa_notification_event', **event())
    assert first == 1
    duplicate = event()
    duplicate.update(trigger_text='ignored replacement body', trigger_message_json='{"wrong":true}')
    assert call('oa_dedup', 'record_oa_notification_event', **duplicate) == first
    ignored = call('oa_system_ignored', 'record_oa_notification_event', **event('oa-system', kind='system_notification'))
    assert ignored == 3
    other_node = call('oa_other_node', 'record_oa_notification_event', **event('oa-other', node='node-other'))
    other_process = call('oa_other_process', 'record_oa_notification_event', **event('oa-process-2', node='node-one', process='other-process'))
    assert (other_node, other_process) == (4, 5)
    pending = call('oa_initial_list', 'list_pending_oa_reminder_targets')
    assert [r['id'] for r in pending] == [1, 4, 5]
    assert pending[0]['trigger_text'] == 'adopted reminder' and json.loads(pending[0]['trigger_message_json'])['text'] == 'OA-body'
    assert call('oa_blank_adopt', 'adopt_oa_notification_events', ' ', 'node-one') == 0
    assert call('oa_adopt', 'adopt_oa_notification_events', ' oa-process ', ' node-one ') == 1
    assert [r['id'] for r in call('oa_exact_targets', 'list_pending_oa_reminder_targets', 'oa-process', 'node-one')] == [1]
    assert call('oa_claim_missing', 'claim_oa_reminder_target', 999, 70) is False
    assert call('oa_claim_ignored', 'claim_oa_reminder_target', ignored, 70) is False
    assert call('oa_claim', 'claim_oa_reminder_target', first, 70) is True
    assert call('oa_claim_again', 'claim_oa_reminder_target', first, 71) is False
    assert call('oa_wrong_sent_attempt', 'mark_oa_reminder_sent', first, 71, 72) is False
    assert call('oa_wrong_fail_attempt', 'mark_oa_reminder_failed', first, 71, 'wrong') is False
    assert call('oa_failed', 'mark_oa_reminder_failed', first, 70, 'synthetic failure') is True
    retry = call('oa_failed_targets', 'list_pending_oa_reminder_targets', 'oa-process', 'node-one')
    assert retry[0]['status'] == 'adopted' and retry[0]['claimed_attempt_id'] == 0 and retry[0]['last_error'] == 'synthetic failure'
    assert call('oa_reclaim', 'claim_oa_reminder_target', first, 71) is True
    assert call('oa_sent', 'mark_oa_reminder_sent', first, 71, 72) is True
    assert call('oa_late_failure', 'mark_oa_reminder_failed', first, 71, 'late') is False
    assert call('oa_sent_not_pending', 'list_pending_oa_reminder_targets', 'oa-process', 'node-one') == []
    assert call('oa_adopt_other_node', 'adopt_oa_notification_events', 'oa-process', 'node-other') == 1

    # Public reply creation creates source mapping aliases. Merge must repoint all.
    def reply(message, *, conv='merge', key='', single=True, stamp='100', text=None):
        return dict(conversation_id=conv, conversation_title='Synthetic reply', single_chat=single, trigger_message_id=message, trigger_create_time=stamp, trigger_sender='sender', trigger_text=text or message, trigger_message_json=json.dumps({'text': text or message}), business_object_key=key)

    fixture_clock[0] = '2040-01-01 00:00:01'
    a = call('reply_first', 'ensure_reply_task', **reply('a', key='alias:first'))
    assert a.business_object_key == 'alias:first' and a.input_version == 1
    assert call('reply_same_identity', 'ensure_reply_task', **reply('a', key='alias:ignored')).id == a.id
    fixture_clock[0] = '2040-01-01 00:00:02'
    b = call('reply_second', 'ensure_reply_task', **reply('b', key='alias:second', stamp='200'))
    assert b.id != a.id
    assert call('reply_current_first_alias', 'get_reply_task_for_business_object', 'alias:first').id == a.id
    fixture_clock[0] = '2040-01-01 00:00:03'
    assert call('reply_merge', 'replace_pending_single_chat_reply_task_trigger', conversation_id='merge', trigger_message_id='c', trigger_create_time='300', trigger_sender='sender-c', trigger_text='merged adopted text', trigger_message_json='{"text":"merged-body"}') == 1
    merged = call('reply_first_alias_repointed', 'get_reply_task_for_business_object', 'alias:first')
    assert merged.id == b.id and merged.trigger_text == 'merged adopted text' and merged.input_version == 3
    mapping_rows = [{field: value for field, value, typ in row} for row in records[-1]['facts']['business_object_tasks']]
    assert {r['business_object_key']: r['created_at'] for r in mapping_rows} == {'alias:first': '2040-01-01 00:00:01', 'alias:second': '2040-01-01 00:00:02', 'message:dingtalk:merge:c': '2040-01-01 00:00:03'}
    merged_inputs = call('reply_merged_inputs', 'list_reply_task_inputs', b.id)
    assert len(merged_inputs) == 3 and merged_inputs[-1]['trigger_text'] == 'merged adopted text'
    queued = call('reply_pending_queue', 'list_reply_tasks', statuses=('pending',), channel='dingtalk')
    assert [r.id for r in queued] == [b.id]
    assert call('reply_second_alias_preserved', 'get_reply_task_for_business_object', 'alias:second').id == b.id
    assert call('reply_new_trigger_alias', 'get_reply_task_for_business_object', 'message:dingtalk:merge:c').id == b.id
    assert call('reply_merge_missing', 'replace_pending_single_chat_reply_task_trigger', conversation_id='absent', trigger_message_id='none', trigger_create_time='300', trigger_sender='sender', trigger_text='none', trigger_message_json='{}') == 0
    assert call('reply_no_mapping', 'get_reply_task_for_business_object', 'alias:missing') is None
    oa_task = call('reply_provisional', 'ensure_reply_task', **reply('provisional', conv='oa-reply', single=False, key='oa:process-reply:'))
    adopted = call('reply_provisional_adopted', 'ensure_reply_task', **reply('identified', conv='oa-reply', single=False, key='oa:process-reply:node'))
    assert adopted.id == oa_task.id and adopted.business_object_key == 'oa:process-reply:node'
    assert call('reply_old_provisional_gone', 'get_reply_task_for_business_object', 'oa:process-reply:') is None
    assert call('reply_node_mapping', 'get_reply_task_for_business_object', 'oa:process-reply:node').id == adopted.id
    assert call('reply_provisional_prefix_finds_node', 'ensure_reply_task', **reply('third-oa', conv='oa-reply', single=False, key='oa:process-reply:')).id == adopted.id

    # Exact adopted item payload and REAL read classes, independent item ID stream.
    request = dict(conversation_id='okr-chat', conversation_title='OKR synthetic', trigger_message_id='okr-trigger', trigger_sender='owner', trigger_sender_user_id='owner-id', trigger_text='review request', period_label='Q4', period_start='2040-10-01', period_end='2040-12-31', okr_source_json='{"objectives":["original"]}')
    request_id = call('okr_request', 'create_okr_review_request', **request)
    assert call('okr_request_replay', 'create_okr_review_request', **request) == request_id
    assert len(call('okr_claim_request', 'claim_okr_review_requests', 1)) == 1
    call('okr_request_failed', 'mark_okr_review_request_failed', request_id, 'source failed')
    request['okr_source_json'] = '{"objectives":["replacement"]}'
    assert call('okr_failed_request_replace', 'create_okr_review_request', **request) == request_id
    assert json.loads(call('okr_replacement_read', 'get_okr_review_request', request_id).okr_source_json)['objectives'] == ['replacement']
    item = dict(request_id=request_id, objective_title='Objective 原文', objective_weight=2, kr_title='KR adopted', kr_weight=0.375, item_json='{"adopted":true,"score":0.375,"text":"保留"}')
    assert call('okr_item', 'record_okr_review_item', **item) == 1
    assert call('okr_repeated_item', 'record_okr_review_item', **item) == 2
    orphan = dict(item, request_id=987654, objective_weight='3.5', kr_weight=4)
    assert call('okr_public_request_no_fk', 'record_okr_review_item', **orphan) == 3
    call('okr_request_done', 'mark_okr_review_request_done', request_id, codex_session_id='synthetic-session')
    assert call('okr_request_done_read', 'get_okr_review_request', request_id).status == 'done'
    item_rows = records[-1]['facts']['okr_review_items']
    for row in item_rows:
        fields = {field: (value, typ) for field, value, typ in row}
        assert fields['objective_weight'][1] == fields['kr_weight'][1] == 'float'
        assert json.loads(fields['item_json'][0])['adopted'] is True
    assert {field: value for field, value, typ in item_rows[0]}['objective_weight'] == 2.0

    # Parent check must use independent source public ID, never physical Tasks ID.
    call('wechat_blank_run', 'begin_wechat_memory_import_job', import_run_id=' ', account_id='account', expected_error=('ValueError', 'import_run_id must be non-empty'))
    job = call('wechat_begin', 'begin_wechat_memory_import_job', import_run_id=' import-one ', account_id=' account ')
    assert job == 1
    assert call('wechat_duplicate_not_deduped', 'begin_wechat_memory_import_job', import_run_id='import-one', account_id='account') == 2
    assert call('wechat_running_parent', 'runtime_operation_parent_is_runnable', 'memory', f'wechat_memory_import_job:{job}') is True
    attempt = call('wechat_runtime_claim', 'claim_runtime_operation_attempt', 'memory', f'wechat_memory_import_job:{job}', 'synthetic-route', 'codex_cli', 'local_oauth', 'synthetic-model', owner='synthetic-owner', now=CLOCK)
    assert attempt.workload_key == 'wechat_memory_import_job:1' and attempt.status == 'starting'
    call('wechat_bad_terminal', 'finish_wechat_memory_import_job', job, status='running', expected_error=('ValueError', 'WeChat Memory import terminal status is invalid'))
    call('wechat_complete', 'finish_wechat_memory_import_job', job, status='completed')
    assert call('wechat_completed_parent', 'runtime_operation_parent_is_runnable', 'memory', f'wechat_memory_import_job:{job}') is False
    call('wechat_terminal_replay', 'finish_wechat_memory_import_job', job, status='completed')
    call('wechat_terminal_conflict', 'finish_wechat_memory_import_job', job, status='failed', error='conflict', expected_error=('ValueError', 'conflicting WeChat Memory import terminal rewrite'))
    call('wechat_missing_job', 'finish_wechat_memory_import_job', 999, status='failed', expected_error=('ValueError', 'WeChat Memory import job does not exist'))
    call('wechat_second_failed', 'finish_wechat_memory_import_job', 2, status='failed', error='kept-error')
    assert call('wechat_failed_parent', 'runtime_operation_parent_is_runnable', 'memory', 'wechat_memory_import_job:2') is False

    # Current indirect queries execute unchanged application projection logic.
    assert call('mapping_reconcile_no_eligible_review', 'reconcile_valid_needs_human_projections') == 0
    assert call('mapping_supersession_no_failed_task', 'skip_failed_reply_tasks_superseded_by_terminal_business_object') == 0
    attempt_id = call('attention_seed_public', 'record_reply_attempt', conversation_id='standalone-failure', conversation_title='Synthetic failure', trigger_message_id='attention-trigger', trigger_sender='owner', trigger_text='source failure body', action='reply', sensitivity_kind='normal', send_status='failed')
    problem = call('attention_full', 'list_current_unresolved_problem_attempts')
    assert [r.id for r in problem] == [attempt_id]
    summaries = call('attention_summaries', 'list_current_unresolved_problem_attempt_summaries')
    assert summaries[0]['trigger_text'] == 'source failure body'
    assert call('attention_count', 'count_current_unresolved_problem_attempts') == 1
    logs = call('history_reply_attempts', 'list_operation_logs', source_tables=('reply_attempts',))
    assert len(logs) == 1 and logs[0].summary == 'source failure body'
    assert len(methods) == 28
    assert len({r['label'] for r in records}) == len(records) == 71
    assert set(entered_owners) == set(EXPECTED_OWNERS), entered_owners
    args.result.write_text(json.dumps({'records': records, 'public_methods': sorted(methods), 'identity_maps': identities, 'raw_identity_witnesses': physical_witness, 'owner_entry_counts': dict(entered_owners), 'issuances': issuances}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', choices=('bootstrap', 'prepare', 'scenario', 'negative'))
    parser.add_argument('--code', type=Path)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--target', action='store_true')
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    assert Path(sys.executable).resolve() == PYTHON.resolve(), 'use pinned Python -B'
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    script = Path(__file__).resolve()
    assert digest(args.repo / 'app/store.py') == SOURCE_SHA
    patches = [args.repo / 'docs/superpowers/plans' / name for name in PATCHES]
    source_files = {name: digest(args.repo / name) for name in ('app/store.py', 'app/task_storage.py', 'app/task12_storage.py', 'app/task8_storage.py', 'app/task20_definitions.py')}
    receipt = {'harness_sha256': digest(script), 'limitations': ['Empty fresh Tasks replacement only; no nonempty migration, preserved captured IDs, current-fact inverse, predecessor/reforward, production cold selection or final table-count proof.', 'Real needs-human reconcile and supersession SQL execute with no eligible approved review or failed task; no nonempty mutation proof for those two policy branches.', 'No external business-map readers, provider delivery, native Agent CLI or whole-release acceptance is covered.'], 'scope': 'author fresh API semantic characterization; original full constructor and cloned empty target only', 'clock': {'default': CLOCK, 'reply_first': '2040-01-01 00:00:01', 'reply_second': '2040-01-01 00:00:02', 'reply_merge_and_later': '2040-01-01 00:00:03'}, 'uuid': 'Store module uuid4 deterministic UUID integer stream 1..9999', 'patches': {p.name: digest(p) for p in patches}, 'source_sha256': SOURCE_SHA, 'target_candidate_sha256': TARGET_SHA, 'constructor_boundary': 'target _ensure_initialized fixture bypass after explicit empty preparation; ContextVar, _open_connection, _connect and constructor otherwise original', 'normalization': ['Declared SQL current_timestamp and _utc_store_time(None) clock.', 'New published8 IDs mapped bijectively by kind and first-observed issuance after exact public call position; raw witnesses retained. Existing captured IDs are not tested by this empty fixture.', 'Four-family independent public IDs/hidden ROWIDs are exact. Only pointers to newly-issued published8 IDs (mapping.reply_task_id and item.request_id) use the declared endpoint maps; all other field values/classes/status/CAS/rowcount remain exact.', 'Absent native AUTO sequence and explicit target zero have effective initial water zero; published8 physical waters retained separately and are not compared numerically.'], 'no_provider_native_cli_network': True}
    with tempfile.TemporaryDirectory(prefix='task12-store-misc-public-', dir='/tmp') as temp:
        root = Path(temp)
        source_code, target_code = root / 'source', root / 'target'
        for code in (source_code, target_code):
            shutil.copytree(args.repo / 'app', code / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        assert {name: digest(source_code / name) for name in source_files} == source_files
        assert {name: digest(target_code / name) for name in source_files} == source_files
        receipt['source_file_sha256'] = source_files
        source_db, target_db = source_code / 'fixture.sqlite3', target_code / 'fixture.sqlite3'

        def run(stage, code, db, target=False, suffix=''):
            result = root / (stage + ('-target' if target else '-source') + suffix + '.json')
            cmd = [str(PYTHON), '-B', str(script), '--worker', stage, '--code', str(code), '--db', str(db), '--result', str(result)]
            if target:
                cmd.append('--target')
            proc = subprocess.run(cmd, cwd=code, env=env, capture_output=True, text=True, timeout=120)
            if proc.returncode:
                raise RuntimeError(f'{stage} target={target}: {proc.stderr[-7000:]}')
            return json.loads(result.read_text())

        receipt['bootstrap'] = run('bootstrap', source_code, source_db)
        with sqlite3.connect(source_db) as src, sqlite3.connect(target_db) as dst:
            src.backup(dst)
        for patch in patches:
            subprocess.run(['git', 'apply', '--check', str(patch)], cwd=target_code, check=True, capture_output=True, text=True)
            subprocess.run(['git', 'apply', str(patch)], cwd=target_code, check=True, capture_output=True, text=True)
        assert digest(target_code / 'app/store.py') == TARGET_SHA
        receipt['preparation'] = run('prepare', target_code, target_db, True)
        source_control, negative_code = root / 'source-control.sqlite3', root / 'negative-code'
        negative_db = negative_code / 'fixture.sqlite3'
        shutil.copytree(target_code / 'app', negative_code / 'app')
        for origin, clone in ((source_db, source_control), (target_db, negative_db)):
            with sqlite3.connect(origin) as src, sqlite3.connect(clone) as dst:
                src.backup(dst)
        before = 'f"select 1 from ({task12_projection(\'wechat_memory_import_jobs\')}) where id=? and status=\'running\'"'
        after = 'f"select 1 from tasks where record_type=\'wechat_memory_import_jobs\' and id=? and {task20_column(\'wechat_memory_import_jobs\', \'status\')}=\'running\'"'
        module = negative_code / 'app/store.py'
        text = module.read_text()
        assert text.count(before) == 1
        module.write_text(text.replace(before, after))
        native_control = run('negative', source_code, source_control, suffix='-control')
        broken_control = run('negative', negative_code, negative_db, True, suffix='-mutant')
        assert native_control['runnable'] is True and broken_control['runnable'] is False
        assert broken_control['job_public_id'] == 1 and broken_control['job_physical_id'] != 1
        receipt['negative_control'] = {'mutation': 'only WeChat runtime-parent native WHERE uses physical tasks.id instead of projected source public ID', 'source': native_control, 'mutant': broken_control, 'caught': True}
        source = run('scenario', source_code, source_db)
        target = run('scenario', target_code, target_db, True)
        sr = {r['label']: r for r in source['records']}
        tr = {r['label']: r for r in target['records']}
        assert len(sr) == len(source['records']) == len(tr) == len(target['records'])
        assert len(sr) == 71
        assert len(source['public_methods']) == len(target['public_methods']) == 28
        assert len(source['owner_entry_counts']) == len(target['owner_entry_counts']) == 19
        assert [{k: v for k, v in r.items() if k != 'id'} for r in source['issuances']] == [{k: v for k, v in r.items() if k != 'id'} for r in target['issuances']]
        receipt['published8_issuance_witnesses'] = {'source': source['issuances'], 'target': target['issuances']}
        assert sr.keys() == tr.keys() and source['public_methods'] == target['public_methods']
        assert source['owner_entry_counts'] == target['owner_entry_counts']
        receipt['actual_scoped_owner_entry_counts'] = source['owner_entry_counts']
        for kind in PUBLISHED:
            assert list(source['identity_maps'][kind].values()) == list(target['identity_maps'][kind].values())
        differences = [{'label': label, 'fields': [key for key in sr[label] if sr[label][key] != tr[label][key]], 'source_outcome': sr[label]['outcome'], 'target_outcome': tr[label]['outcome']} for label in sr if sr[label] != tr[label]]
        receipt.update({'checkpoint_count': len(sr), 'unique_labels': list(sr), 'public_methods': source['public_methods'], 'equal_checkpoints': len(sr) - len(differences), 'differences': differences, 'expected_error_count': sum('error' in r['outcome'] for r in sr.values()), 'source_expected_assertions_passed': True, 'target_expected_assertions_passed': True, 'transaction_histogram': dict(Counter(tuple(r['transactions']) for r in sr.values()))})
        receipt['transaction_histogram'] = {','.join(k): v for k, v in receipt['transaction_histogram'].items()}
        receipt['record_sha256'] = {key: hashlib.sha256(json.dumps(value['records'], sort_keys=True, ensure_ascii=False).encode()).hexdigest() for key, value in (('source', source), ('target', target))}
        receipt['identity_maps'] = {'source': source['identity_maps'], 'target': target['identity_maps']}
        receipt['raw_final_identity_witnesses'] = {'source': source['raw_identity_witnesses'][-1], 'target': target['raw_identity_witnesses'][-1]}
        selected = ('oa_dedup', 'oa_failed_targets', 'reply_first_alias_repointed', 'reply_node_mapping', 'okr_replacement_read', 'wechat_running_parent', 'wechat_completed_parent', 'attention_count', 'history_reply_attempts')
        receipt['decisive_outcomes'] = {k: sr[k]['outcome'] for k in selected}
        receipt['final_scoped_row_counts'] = {kind: len(sr[next(reversed(sr))]['facts'][kind]) for kind in (*PUBLISHED, *FAMILIES)}
        receipt['final_mapping_rows'] = [{field: value for field, value, typ in row} for row in source['records'][-1]['facts']['business_object_tasks']]
        receipt['final_okr_item_sqlite_types'] = source['records'][-1]['facts']['okr_review_items:sqlite_types']
        assert receipt['final_scoped_row_counts'] == {'reply_tasks': 2, 'reply_task_inputs': 6, 'okr_review_requests': 1, 'business_object_tasks': 4, 'oa_notification_events': 4, 'okr_review_items': 3, 'wechat_memory_import_jobs': 2}
        receipt['fixed_coverage_assertions'] = {'checkpoints_each': 71, 'public_methods_each': 28, 'actual_scoped_owners_each': 19, 'source_field_count': 41, 'no_equal_errors_as_success': True, 'exact_expected_final_family_counts': True, 'issuance_call_position_kind_token_equal': True}
        receipt['final_family_field_sha256'] = {kind: hashlib.sha256(json.dumps(sr[next(reversed(sr))]['facts'][kind], ensure_ascii=False, sort_keys=True).encode()).hexdigest() for kind in FAMILIES}
        receipt['final_fk_findings'] = {key: value['records'][-1]['facts']['fk_findings'] for key, value in (('source', source), ('target', target))}
    receipt['owned_temp_removed'] = not root.exists()
    if args.output:
        args.output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: receipt[k] for k in ('checkpoint_count', 'equal_checkpoints', 'differences', 'expected_error_count', 'owned_temp_removed')}, ensure_ascii=False))
    if differences:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
