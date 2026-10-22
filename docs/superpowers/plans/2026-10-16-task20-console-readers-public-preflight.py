#!/usr/bin/env python3
"""Real console reader parity on synthetic fresh source and empty Tasks20 target.

No cold-target/migration acceptance. Native-result read is an explicit synthetic
receipt seam for one retirement run only. No external provider, CLI or server.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
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
CLOCK = '2040-01-01 00:00:00'
OLD = '2039-12-31 22:00:00'
CLASS = 'email_agent_classification_tasks'
PATCHES = (
    '2026-10-16-task8-store-caller-proposal.patch',
    '2026-10-16-task12-store-misc-caller-proposal.patch',
    '2026-10-16-task12-feedback-caller-proposal.patch',
    '2026-10-16-task12-email-native-caller-proposal.patch',
    '2026-10-16-task8-workbench-caller-proposal.patch',
    '2026-10-16-task12-workbench-children-caller-proposal.patch',
    '2026-10-16-task20-console-readers-caller-proposal.patch',
)
EXPECTED = {
    'app/audit_web.py': '036f4f46259485c5e341cab8171071b732377d2cee685d90f41eceb8d4dc77e7',
    'app/quality_gate.py': 'c9888b13f4b4965a62793150d030cdbe3e85fe7ed8d45178ff9604eb9475da3f',
    'app/rule_question_retirement.py': 'e2bf5b4fd78dd40fd557480b3c6c6211f56eb6d40cebb209b582fdc58f3cac8e',
    'app/task12_storage.py': '127311aa2ed303066fc3dd325a5642c0e61725a2d8db3b5bedfc218e804e013f',
    'app/task20_definitions.py': 'cf58403bcc9dc7bf4c3ef5b9a777ff941c7a6de210e04b29e4cb7abb1e0df4d0',
}
HELPERS = {'_sqlite_table_exists', '_sqlite_table_columns', '_queue_status_counts', '_queue_count_for', '_queue_retryable_count', '_queue_latest_value', '_queue_latest_error'}
OWNERS = HELPERS | {'_queue_status_snapshots', '_work_summary_input_queue_snapshot', '_meeting_job_queue_snapshot', '_okr_review_queue_snapshot', '_reply_task_queue_snapshot', '_reply_attempt_queue_snapshot', '_queue_attention_rows', '_human_decision_attention_rows', 'scan_hourly_quality', '_check_email_classification_tasks', '_check_structured_needs_human', '_retire_in_connection', 'retire_rule_question'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encode(value):
    if hasattr(value, 'to_dict'):
        return encode(value.to_dict())
    if hasattr(value, 'model_dump'):
        return encode(value.model_dump(mode='json'))
    if is_dataclass(value):
        return encode(asdict(value))
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(encode(v) for v in value)
    if isinstance(value, bytes):
        return {'hex': value.hex()}
    return value


def worker(args):
    sys.path.insert(0, str(args.code))
    import app.audit_web as audit
    import app.quality_gate as quality
    import app.rule_question_retirement as retire
    import app.store as sm
    from app.email_store import EmailStore
    from app.store import AutoReplyStore
    from app.task20_definitions import TASK20_FIELDS, TASK20_NEW_KINDS, create_task20_schema, initialize_task8_sequence, task20_column
    from app.task12_storage import insert_task12, task12_projection, task12_set_parts
    from app.task8_storage import register_task8_functions, task8_insert_parts, task8_set_parts
    from app.task_storage import insert_task
    from app.run_storage import insert_run
    from app.action_storage import update_action

    if args.worker == 'bootstrap':
        store = AutoReplyStore(args.db)
        EmailStore(args.db)
        with store._connect() as db:
            counts = {k: db.execute('select count(*) from '+k).fetchone()[0] for k in ('tasks', *TASK20_NEW_KINDS)}
            assert not any(counts.values())
            fields = {k: [r[1] for r in db.execute('pragma table_info('+k+')')] for k in TASK20_NEW_KINDS}
            assert all(set(fields[k]) == set(TASK20_FIELDS[k]) for k in TASK20_NEW_KINDS)
            result = {'constructors': 'ordinary full-fresh AutoReplyStore then EmailStore', 'counts': counts, 'source_fields': fields, 'table_entries': db.execute("select count(*) from sqlite_schema where type='table'").fetchone()[0], 'fk': [list(r) for r in db.execute('pragma foreign_key_check')]}
        args.result.write_text(json.dumps(result))
        return
    if args.worker == 'prepare':
        with sqlite3.connect(args.db) as db:
            before = dict(db.execute("select name,sql from sqlite_schema where type='table'"))
            assert all(db.execute('select count(*) from '+k).fetchone()[0] == 0 for k in ('tasks', *TASK20_NEW_KINDS, 'feedback_iteration_decision_items'))
            child = 'feedback_iteration_decision_items'
            ddl = before[child]
            replacements = {'references feedback_iteration_decisions(id)': 'references tasks("feedback_iteration_decisions__id")', 'references feedback_processing_items(feedback_key)': 'references tasks("feedback_processing_items__feedback_key")', 'references feedback_processing_rounds(id)': 'references tasks("feedback_processing_rounds__id")'}
            for old, new in replacements.items():
                assert ddl.count(old) == 1
                ddl = ddl.replace(old, new)
            objects = list(db.execute("select type,name,sql from sqlite_schema where tbl_name=? and sql is not null and type in ('index','trigger') order by type,name", (child,)))
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            db.execute('drop table '+child)
            for k in TASK20_NEW_KINDS:
                db.execute('drop table '+k)
            db.execute('drop table tasks')
            create_task20_schema(db)
            initialize_task8_sequence(db, 0)
            db.execute(ddl)
            for _, _, sql in objects:
                db.execute(sql)
            after = dict(db.execute("select name,sql from sqlite_schema where type='table'"))
            assert set(before)-set(after) == set(TASK20_NEW_KINDS)
            assert {k: v for k, v in before.items() if k not in ('tasks', child, *TASK20_NEW_KINDS)} == {k: v for k, v in after.items() if k not in ('tasks', child)}
        args.result.write_text(json.dumps({'fixture_only': True, 'empty_native_sources_retired': list(TASK20_NEW_KINDS), 'retained_empty_child_three_fk_rebinding': replacements, 'all_other_table_ddl_exact': True}))
        return

    trace = []
    previous_open = AutoReplyStore._open_connection

    def fixture_open(self):
        db = previous_open(self)
        db.create_function('current_timestamp', 0, lambda: CLOCK)
        if args.target:
            register_task8_functions(db)
        db.set_trace_callback(trace.append)
        return db

    AutoReplyStore._open_connection = fixture_open
    previous_email_open = EmailStore._open_connection

    def email_open(self):
        db = previous_email_open(self)
        db.create_function('current_timestamp', 0, lambda: CLOCK)
        if args.target:
            register_task8_functions(db)
        db.set_trace_callback(trace.append)
        return db

    EmailStore._open_connection = email_open
    if args.target:
        AutoReplyStore._ensure_initialized = lambda self: None
    store = AutoReplyStore(args.db)
    if args.target:
        email = EmailStore.__new__(EmailStore)
        email.path, email.training_run_id = args.db, None
    else:
        email = EmailStore(args.db)
    records = []
    tie_witness = []
    replacement_witness = []
    entered = Counter()
    called = set()
    module_paths = {str(args.code/'app'/f) for f in ('audit_web.py', 'quality_gate.py', 'rule_question_retirement.py')}

    def profile(frame, event, arg):
        if event == 'call' and frame.f_code.co_filename in module_paths and frame.f_code.co_name in OWNERS:
            entered[frame.f_code.co_name] += 1
        return profile

    def facts():
        with store._connect() as db:
            result = {}
            for k, query in [('reply_tasks', sm.REPLY_TASK_SQL), ('reply_task_inputs', sm.REPLY_TASK_INPUT_SQL), ('work_summary_inputs', sm.WORK_SUMMARY_INPUT_SQL), ('meeting_alignment_jobs', sm.MEETING_JOB_SQL), ('okr_review_requests', sm.OKR_REQUEST_SQL)]:
                result[k] = [dict(r) for r in db.execute('select * from ('+query+') order by id')]
            for k in (CLASS, 'business_object_tasks'):
                query = task12_projection(k, include_rowid=True) if args.target else 'select rowid,* from '+k
                rows = db.execute('select * from ('+query+') order by rowid').fetchall()
                result[k] = [dict(r) for r in rows]
                fields = ['rowid', *TASK20_FIELDS[k]]
                result[k+':types'] = [list(r) for r in db.execute('select '+','.join('typeof("'+f+'")' for f in fields)+' from ('+query+') order by rowid')]
            for k in ('runs', 'actions'):
                result[k] = [dict(r) for r in db.execute('select * from '+k+' order by id')]
            from app.state_definitions import state_projection
            result['retirement_receipts'] = [dict(r) for r in db.execute("select key,value from ("+state_projection('service_state')+") where key like 'rule_question_retirement:%' order by key")]
            result['fk'] = [list(r) for r in db.execute('pragma foreign_key_check')]
            assert not result['fk']
            return encode(result)

    def call(checkpoint, fn, *pos, error=None, **kw):
        assert checkpoint not in {r['label'] for r in records}
        trace.clear()
        called.add(fn.__module__+'.'+fn.__name__)
        sys.settrace(profile)
        try:
            value = fn(*pos, **kw)
            outcome = {'return': encode(value)}
        except Exception as exc:
            value = None
            outcome = {'error': {'type': type(exc).__name__, 'message': str(exc)}}
        finally:
            sys.settrace(None)
        assert ('error' in outcome) == (error is not None), (checkpoint, outcome)
        if error:
            assert outcome['error']['type'] == error
        transactions = [s.strip().upper() for s in trace if s.strip().upper() in {'BEGIN', 'BEGIN IMMEDIATE', 'COMMIT', 'ROLLBACK'}]
        current = facts()
        eligible = [r for r in current[CLASS] if str(r['status']).lower() == 'failed' and str(r['error']).strip()]
        latest = max((r['updated_at'] for r in eligible), default=None)
        candidates = sorted({r['error'] for r in eligible if r['updated_at'] == latest})
        if len(candidates) > 1 and 'return' in outcome:
            if fn is audit._queue_latest_error and pos[1] == CLASS:
                assert value in candidates
                tie_witness.append({'label':checkpoint, 'raw_selected':value, 'max_timestamp':latest, 'eligible_errors':candidates})
                outcome = {'return': {'unspecified_tie_eligible_errors':candidates}}
            elif fn is audit._queue_status_snapshots:
                for item in outcome['return']:
                    if item['table'] == CLASS:
                        assert item['latest_error'] in candidates
                        tie_witness.append({'label':checkpoint, 'raw_selected':item['latest_error'], 'max_timestamp':latest, 'eligible_errors':candidates})
                        item['latest_error'] = {'unspecified_tie_eligible_errors':candidates}
        records.append({'label': checkpoint, 'outcome': outcome, 'transactions': transactions, 'facts': current})
        return value

    def dbcall(checkpoint, fn, *pos, **kw):
        with store._connect() as db:
            return call(checkpoint, fn, db, *pos, **kw)

    def pub(kind, identity, values):
        values = {'legacy_key': str(identity), **values}
        with store._connect() as db:
            if args.target:
                part = task8_insert_parts(kind, values)
                db.execute('insert into tasks(id,'+part.columns+') values(?,'+part.values+')', (identity, *part.parameters))
            else:
                insert_task(db, kind, {'id': identity, **values})

    def update_pub(kind, identity, values):
        with store._connect() as db:
            if args.target:
                part = task8_set_parts(kind, values)
                db.execute('update tasks set '+part.sql+' where id=?', (*part.parameters, identity))
            else:
                db.execute('update tasks set '+','.join(k+'=?' for k in values)+' where id=?', (*values.values(), identity))

    def native(kind, values, rowid):
        with store._connect() as db:
            if args.target:
                insert_task12(db, kind, values, source_rowid=rowid)
            else:
                fields=['rowid', *values]
                db.execute('insert into '+kind+'('+','.join(fields)+') values('+','.join('?' for _ in fields)+')', (rowid, *values.values()))

    call('empty_selected_snapshots', audit._queue_status_snapshots, store)
    call('empty_quality', quality.scan_hourly_quality, args.db, now=datetime(2040, 1, 1, tzinfo=timezone.utc))
    call('missing_required_quality', quality.scan_hourly_quality, args.db.parent/'missing.sqlite3', now=datetime(2040,1,1,tzinfo=timezone.utc))
    dbcall('class_exists_empty', audit._sqlite_table_exists, CLASS)
    assert dbcall('class_columns', audit._sqlite_table_columns, CLASS) == set(TASK20_FIELDS[CLASS])
    assert 'id' not in records[-1]['outcome']['return']
    for label, function, extra in [('empty_counts', audit._queue_status_counts, ('status',)), ('empty_latest', audit._queue_latest_value, ('updated_at',)), ('empty_error', audit._queue_latest_error, ('status', 'error')), ('classification_retryable_zero', audit._queue_retryable_count, ('status', 'error'))]:
        dbcall(label, function, CLASS, *extra)
    call('count_helper', audit._queue_count_for, {'failed': 2, 'done': 9}, {'failed'})
    dbcall('native_columns', audit._sqlite_table_columns, 'work_todo_dingtalk_links')
    dbcall('native_retryable', audit._queue_retryable_count, 'work_todo_dingtalk_links', 'status', 'last_error')
    with sqlite3.connect(':memory:') as missing:
        missing.row_factory = sqlite3.Row
        call('missing_class_presence', audit._sqlite_table_exists, missing, CLASS)
        call('missing_class_columns', audit._sqlite_table_columns, missing, CLASS)

    # Explicit original source coordinates seed parents before all new-kind rows.
    for identity, conv, trigger, key, status in [(101, 'old', 'old', 'message:dingtalk:old:old', 'failed'), (102, 'replacement', 'new', 'message:dingtalk:old:old', 'done'), (103, 'current', 'current', 'current-map', 'failed'), (104, 'retire', 'question', 'retire-map', 'done')]:
        pub('reply_tasks', identity, {'channel':'dingtalk', 'conversation_id':conv, 'conversation_title':'Context '+conv, 'single_chat':0, 'trigger_message_id':trigger, 'trigger_create_time':'1', 'trigger_sender':'Synthetic', 'business_object_key':key, 'status':status, 'error':'synthetic '+conv if status=='failed' else '', 'updated_at':OLD})
        input_id=identity+100
        pub('reply_task_inputs', input_id, {'parent_id':identity, 'channel':'dingtalk', 'conversation_id':conv, 'conversation_title':'Context '+conv, 'single_chat':0, 'trigger_message_id':trigger, 'trigger_create_time':'1', 'trigger_sender':'Synthetic', 'trigger_text':'Canonical '+conv, 'trigger_message_json':json.dumps({'text':'canonical '+conv}), 'business_object_key':key})
        update_pub('reply_tasks', identity, {'current_input_id': input_id})
    for identity, status in [(301,'failed'), (302,'processing')]:
        pub('work_summary_inputs', identity, {'source_type':'local_file', 'source_ref':'file:'+str(identity), 'payload_json':json.dumps({'source':{'title':'Source title'}, 'summary':'Synthetic summary '+str(identity)}), 'status':status, 'error':'work failure' if status=='failed' else '', 'updated_at':OLD})
    for identity, status in [(401,'failed'), (402,'processing')]:
        pub('meeting_alignment_jobs', identity, {'meeting_id':'meeting-'+str(identity), 'title':'Meeting '+str(identity), 'status':status, 'error':'meeting failure' if status=='failed' else '', 'updated_at':OLD})
    for identity, status in [(501,'failed'), (502,'processing')]:
        pub('okr_review_requests', identity, {'conversation_id':'okr'+str(identity), 'conversation_title':'OKR '+str(identity), 'trigger_message_id':'okr'+str(identity), 'trigger_sender':'Synthetic', 'trigger_text':'OKR review', 'period_label':'week', 'period_start':'2039-12-24', 'period_end':'2039-12-31', 'status':status, 'error':'okr failure' if status=='failed' else '', 'updated_at':OLD})
    for rowid, key, parent in [(1,'message:dingtalk:old:old',102),(2,'current-map',103),(3,'retire-map',104),(4,'alternate-current-alias',103)]:
        native('business_object_tasks', {'business_object_key':key, 'reply_task_id':parent}, rowid)
    for rowid, task_id, status, error in [(7,'a-failed','failed','first tied failure'),(90,'z-failed','failed','second tied failure'),(91,'blank-failed','failed',' '),(92,'pending','pending',''),(93,'running','running',''),(94,None,'failed','')]:
        native(CLASS, {'task_id':task_id,'channel':'email','stable_message_identity':'stable-'+str(task_id),'status':status,'input_json':json.dumps({'message':{'sender':{'email':'sender@example.invalid'},'subject':'Subject '+str(task_id)}}),'error':error,'updated_at':OLD}, rowid)
    dbcall('nonempty_class_counts', audit._queue_status_counts, CLASS, 'status')
    dbcall('nonempty_class_latest', audit._queue_latest_value, CLASS, 'updated_at')
    dbcall('tied_class_latest_error', audit._queue_latest_error, CLASS, 'status', 'error')
    call('nonempty_snapshots', audit._queue_status_snapshots, store)
    with store._connect() as db:
        if args.target:
            part = task12_set_parts(CLASS, {'updated_at':CLOCK})
            db.execute('update tasks set '+part.sql+" where record_type='email_agent_classification_tasks' and cast(legacy_key as integer)=90",part.parameters)
        else:
            db.execute('update email_agent_classification_tasks set updated_at=? where rowid=90',(CLOCK,))
    assert dbcall('unique_class_latest_error', audit._queue_latest_error, CLASS, 'status', 'error') == 'second tied failure'
    call('unique_class_snapshots', audit._queue_status_snapshots, store)
    for label, fn in [('summary_snapshot', audit._work_summary_input_queue_snapshot), ('meeting_snapshot', audit._meeting_job_queue_snapshot), ('okr_snapshot', audit._okr_review_queue_snapshot), ('reply_physical_snapshot', audit._reply_task_queue_snapshot), ('attempt_empty_snapshot', audit._reply_attempt_queue_snapshot)]:
        dbcall(label, fn)
    call('attention_nonempty', audit._queue_attention_rows, store)
    call('attention_limited', audit._queue_attention_rows, store, limit=2)
    call('human_empty', audit._human_decision_attention_rows, store)
    call('nonempty_quality', quality.scan_hourly_quality, args.db, now=datetime(2040, 1, 1, tzinfo=timezone.utc))
    call('live_channels', quality.required_live_channels, args.db)

    attempts=[]
    for conv, trigger, status in [('old','old','failed'), ('current','current','failed'), ('retire','question','needs_human')]:
        attempts.append(store.record_reply_attempt(conversation_id=conv,conversation_title='Context '+conv,trigger_message_id=trigger,trigger_sender='Synthetic',trigger_text='Canonical '+conv,action='agent_run',sensitivity_kind='general',send_status=status))
    with store._connect() as db:
        result=insert_run(db,'agent_runs',{'task_id':104,'execution_generation':'initial','role':'consumer','status':'completed','session_id':'synthetic-retirement','transcript_start':1,'transcript_end':2})
        run_id=result.lastrowid
        update_action(db,'reply_attempts',attempts[-1],{'agent_run_id':run_id})
        db.execute("insert into meeting_memory_write_events(meeting_job_id,execution_generation,payload_json,status,updated_at,error) values(401,'fixture','{}','failed',?,'memory failure')",(OLD,))
    fact={'assertion':'Synthetic checked fact','references':['synthetic:fixture']}
    result={'outcome':'needs_human','needs_human_reason':'Choose reusable rule','error':{'code':'','retryable':False,'authorization_required':False},'decision_options':[{'key':k,'label':k,'instruction':k,'consequence':k,'applies_to':'task_class'} for k in ('allow','stop')],'decision_basis':{'verified_facts':[fact],'rule_evidence':[fact],'quality_explanation':'Synthetic explanation','no_external_action_evidence':[fact],'conclusion':'Rule question'}}
    native_json=json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    import app.native_trajectory as trajectory
    native_reads=[]

    def synthetic_native(db, row, *pos, **kw):
        assert row['id'] == run_id and row['role'] == 'consumer' and row['codex_session_id'] == 'synthetic-retirement'
        native_reads.append(row['id'])
        return native_json

    trajectory.read_native_result_json=synthetic_native
    dbcall('attempt_map_supersession', audit._reply_attempt_queue_snapshot)
    call('attention_attempts_memory', audit._queue_attention_rows, store)
    call('quality_before_retirement', quality.scan_hourly_quality, args.db, now=datetime(2040,1,1,tzinfo=timezone.utc))
    call('retirement_missing', retire.retire_rule_question, store, 9999, authority='fixture-authority', error='ValueError')
    call('retirement_wrong_attempt', retire.retire_rule_question, store, attempts[0], authority='fixture-authority', error='ValueError')
    preview=call('retirement_preview', retire.retire_rule_question, store, attempts[-1], authority='fixture-authority')
    assert preview['applied'] is False
    receipt=call('retirement_apply', retire.retire_rule_question, store, attempts[-1], authority='fixture-authority', apply=True)
    assert receipt['applied'] is True
    assert call('retirement_replay', retire.retire_rule_question, store, attempts[-1], authority='fixture-authority', apply=True) == receipt
    call('retirement_authority_conflict', retire.retire_rule_question, store, attempts[-1], authority='changed-authority', apply=True, error='ValueError')
    call('quality_after_retirement', quality.scan_hourly_quality, args.db, now=datetime(2040,1,1,tzinfo=timezone.utc))

    # Use the real input codec and done compactor, then source-shaped seed its
    # already-compacted row: the console must tolerate the source's missing body.
    from app.email_task_adapter import EmailClassificationTaskAdapter, EmailClassificationTaskInput
    value=EmailClassificationTaskInput.from_message({'accountId':'synthetic-account','folder':'INBOX','uidValidity':42,'uid':55,'providerUnread':True,'messageId':'<55@example.invalid>','subject':'Body removed after done','textBody':'synthetic body'},allowed_category_keys=('work',),category_descriptions={'work':{'core':'Business'}},folder_targets={'work':'Work'},config_version='fixture-v1',unsubscribe_candidates=())
    compact=EmailClassificationTaskAdapter.compact_done_input_json(json.dumps(value.payload(),ensure_ascii=False,sort_keys=True,separators=(',',':')))
    assert json.loads(compact)['input_compacted'] is True and 'message' not in json.loads(compact)
    native(CLASS,{'task_id':'compacted-done','channel':'email','stable_message_identity':'stable-compacted','status':'done','input_json':compact,'updated_at':CLOCK},100)
    adapter = EmailClassificationTaskAdapter(email, now=lambda: datetime(2040,1,1,tzinfo=timezone.utc))
    for step in ('first','replace'):
        call('offline_'+step, adapter.open_offline_task, task_id='offline-probe', owner=step)
        with store._connect() as db:
            query = ("select id,cast(legacy_key as integer) as rowid from tasks where record_type='email_agent_classification_tasks' and "+task20_column(CLASS,'task_id')+"='offline-probe'") if args.target else "select rowid from email_agent_classification_tasks where task_id='offline-probe'"
            replacement_witness.append({'step':step, **dict(db.execute(query).fetchone())})
    assert replacement_witness[0]['rowid'] != replacement_witness[1]['rowid']
    if args.target:
        assert replacement_witness[0]['id'] == replacement_witness[1]['id']
    with store._connect() as db:
        if args.target:
            part=task12_set_parts(CLASS,{'status':'failed'})
            db.execute('update tasks set '+part.sql+" where record_type='email_agent_classification_tasks' and cast(legacy_key as integer)=?",(*part.parameters,replacement_witness[-1]['rowid']))
        else:
            db.execute("update email_agent_classification_tasks set status='failed' where task_id='offline-probe'")
    call('offline_replaced_attention', audit._queue_attention_rows, store)
    call('compacted_done_snapshot', audit._queue_status_snapshots, store)
    call('compacted_done_attention', audit._queue_attention_rows, store)
    call('compacted_done_quality', quality.scan_hourly_quality, args.db, now=datetime(2040,1,1,tzinfo=timezone.utc))
    call('offline_close', adapter.close_offline_task, 'offline-probe')
    with store._connect() as db:
        coordinates=[list(r) for r in db.execute("select id,record_type,cast(legacy_key as integer),parent_id from tasks where record_type in ('email_agent_classification_tasks','business_object_tasks') order by id")] if args.target else {k:[list(r) for r in db.execute('select rowid from '+k+' order by rowid')] for k in (CLASS,'business_object_tasks')}
    assert HELPERS <= set(entered)
    args.result.write_text(json.dumps({'records':records,'tie_witness':tie_witness,'replacement_witness':replacement_witness,'called':sorted(called),'entered':dict(entered),'coordinates':coordinates,'native_receipt_seam':{'only_run_id':run_id,'read_count':len(native_reads),'canonical_sha256':hashlib.sha256(native_json.encode()).hexdigest()},'compacted_input':{'input_compacted':True,'message_present':False,'sha256':hashlib.sha256(compact.encode()).hexdigest()}},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[3])
    parser.add_argument('--output',type=Path)
    parser.add_argument('--worker',choices=('bootstrap','prepare','scenario'))
    parser.add_argument('--code',type=Path)
    parser.add_argument('--db',type=Path)
    parser.add_argument('--result',type=Path)
    parser.add_argument('--target',action='store_true')
    args=parser.parse_args()
    if args.worker:
        worker(args)
        return
    assert Path(sys.executable).resolve() == PYTHON.resolve()
    for path, expected in EXPECTED.items():
        assert digest(args.repo/path)==expected,path
    plan=args.repo/'docs/superpowers/plans'
    assert digest(plan/PATCHES[-1]) == 'a823ff570d1d8b6045f1289853831f7893531f78badc1281134bb80389f717df'
    receipt={'scope':'actual console/quality/retirement methods, full synthetic fresh native source versus explicitly prepared EMPTY Tasks20 with12native sources removed; no actual-profile/nonempty migration/cold-target acceptance','patches':{k:digest(plan/k) for k in PATCHES},'source_files':{p:digest(args.repo/p) for p in (*EXPECTED,'app/store.py','app/email_store.py','app/email_task_adapter.py','app/workbench/store.py','app/workbench/native_events.py')},'clock':CLOCK,'python':str(PYTHON),'fixture_seams':['AutoReplyStore target _ensure_initialized bypass only after empty template preparation','original Store/Email connection wrappers add accepted scalar registration and deterministic SQL clock/trace','one native-result function returns canonical synthetic obsolete-question result for a single asserted consumer run; no native provenance/read claim'],'normalization':'only unspecified equal-update classification latest-error ties: selected value must belong actual max-timestamp eligible error set; raw choices retained separately. Explicit old-eight coordinates/fields/sourceROWIDs otherwise exact; physical new-kind IDs retained separately'}
    with tempfile.TemporaryDirectory(prefix='task20-console-readers-public-',dir='/tmp') as temp:
        root=Path(temp)
        source_code,target_code=root/'source',root/'target'
        for code in (source_code,target_code):
            shutil.copytree(args.repo/'app',code/'app',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        assert {p:digest(source_code/p) for p in receipt['source_files']} == receipt['source_files']
        source_db,target_db=source_code/'fixture.sqlite3',target_code/'fixture.sqlite3'

        def run(stage,code,db,target=False):
            output=root/(stage+'-'+code.name+'.json')
            command=[str(PYTHON),'-B',str(Path(__file__).resolve()),'--worker',stage,'--code',str(code),'--db',str(db),'--result',str(output)]
            if target:
                command.append('--target')
            result=subprocess.run(command,cwd=code,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True)
            if result.returncode:
                raise RuntimeError(stage+' '+code.name+': '+result.stdout+result.stderr)
            return json.loads(output.read_text())

        receipt['bootstrap']=run('bootstrap',source_code,source_db)
        for patch in PATCHES:
            subprocess.run(['git','apply','--check',str(plan/patch)],cwd=target_code,check=True,capture_output=True)
            subprocess.run(['git','apply',str(plan/patch)],cwd=target_code,check=True,capture_output=True)
        native=target_code/'app/workbench/native_events.py'
        text=native.read_text()
        old='from app.task_storage import WORKBENCH_TURN_SQL, task_id_expression'
        assert text.count(old)==1
        native.write_text(text.replace(old,"from app.task_storage import task_id_expression\nfrom app.task20_definitions import task20_projection\nWORKBENCH_TURN_SQL=task20_projection('workbench_turns')"))
        receipt['target_files']={p:digest(target_code/p) for p in receipt['source_files']}
        mutant_code=root/'mutant'
        shutil.copytree(target_code/'app',mutant_code/'app')
        audit_path=mutant_code/'app/audit_web.py'
        text=audit_path.read_text()
        old="select rowid from ({task12_projection('email_agent_classification_tasks', include_rowid=True)})\n                    where lower(status)='failed'"
        new="select id from tasks\n                    where record_type='email_agent_classification_tasks' and lower({task20_column('email_agent_classification_tasks', 'status')})='failed'"
        assert text.count(old)==1
        audit_path.write_text(text.replace(old,new))
        mutant_db=mutant_code/'fixture.sqlite3'
        with sqlite3.connect(source_db) as src,sqlite3.connect(mutant_db) as dst:
            src.backup(dst)
        run('prepare',mutant_code,mutant_db,True)
        with sqlite3.connect(source_db) as src,sqlite3.connect(target_db) as dst:
            src.backup(dst)
        receipt['preparation']=run('prepare',target_code,target_db,True)
        source=run('scenario',source_code,source_db)
        target=run('scenario',target_code,target_db,True)
        mutant=run('scenario',mutant_code,mutant_db,True)
        assert source['called']==target['called']
        assert len(source['records'])==len(target['records'])==len(mutant['records'])==47
        assert OWNERS <= set(source['entered']) & set(target['entered'])
        differences=[]
        for left,right in zip(source['records'],target['records'],strict=True):
            if left != right:
                differences.append({'label':left['label'],'fields':[k for k in left if left[k]!=right[k]],'source_outcome':left['outcome'],'target_outcome':right['outcome']})
        mutant_differences=[a['label'] for a,b in zip(source['records'],mutant['records'],strict=True) if a!=b]
        assert 'attention_nonempty' in mutant_differences
        sr={r['label']:r for r in source['records']}
        mr={r['label']:r for r in mutant['records']}
        source_class=[r for r in sr['attention_nonempty']['outcome']['return'] if r['category']=='Email classification']
        mutant_class=[r for r in mr['attention_nonempty']['outcome']['return'] if r['category']=='Email classification']
        assert len(source_class)==4 and not mutant_class
        assert sr['class_exists_empty']['outcome']['return'] is True
        assert sr['missing_class_presence']['outcome']['return'] is False
        assert sr['missing_class_columns']['outcome']['return'] == []
        assert sr['missing_required_quality']['outcome']['return']['missing_sources']
        assert sr['reply_physical_snapshot']['outcome']['return']['counts'] == {'done':2,'failed':1}
        assert sr['attempt_map_supersession']['outcome']['return']['counts'] == {'failed':1,'needs_human':1}
        before_issues=sr['quality_before_retirement']['outcome']['return']['violations']
        after_issues=sr['quality_after_retirement']['outcome']['return']['violations']
        assert sum(i['count'] for i in before_issues if i['code']=='invalid_needs_human_result')==1
        assert not any(i['code']=='invalid_needs_human_result' for i in after_issues)
        class_issues=[i for i in sr['nonempty_quality']['outcome']['return']['violations'] if i['source']==CLASS]
        assert {i['code']:i['count'] for i in class_issues} == {'failed':4,'running_stale':1,'pending_stale':1}
        assert source['native_receipt_seam']==target['native_receipt_seam']
        assert source['compacted_input']==target['compacted_input']
        receipt['negative_control']={'mutation':'only classification Attention inner candidate IDs use global Tasks.id; outer source ROWID filter and all predicates unchanged','different_checkpoints':mutant_differences,'source_classification_attention':source_class,'mutant_classification_attention':mutant_class}
        receipt.update(checkpoints=len(source['records']),equal_checkpoints=len(source['records'])-len(differences),differences=differences,called=source['called'],entered={'source':source['entered'],'target':target['entered']},coordinates={'source':source['coordinates'],'target':target['coordinates']},comparison_digests={k:hashlib.sha256(json.dumps(v['records'],sort_keys=True,ensure_ascii=False).encode()).hexdigest() for k,v in [('source',source),('target',target)]},observations={r['label']:r['outcome'] for r in source['records']},native_receipt_seam=source['native_receipt_seam'],compacted_input=source['compacted_input'],tie_witness={'source':source['tie_witness'],'target':target['tie_witness']},replacement_witness={'source':source['replacement_witness'],'target':target['replacement_witness']})
    receipt['preflight_sha256']=digest(Path(__file__))
    receipt['coverage_contract']={'fixed_checkpoints':47,'all20instrumented_owners_entered':True,'all7generic_helpers_entered':True,'source_statement_parameter_expressions':'exact original52execute/36_count tuples in pinned reader proposal static receipt; reader patch unchanged','eligible_human_decision_attention':'not seeded; actual SQL executes with zero eligible rows','tie_boundary':'two classification latest-error checkpoints compare actual maximum-timestamp eligible set and retain raw choices; no tie-break added','retirement_native_provenance':'unproved; one explicit canonical synthetic native-result function seam only'}
    receipt['branch_limits']=['synthetic fresh only; no frozen actual profile/nonempty migration/copy/inverse/cold target/complete schema manifests','eligible approved human-decision candidate/selection and all seven business-map join branch combinations not proved; owner entry does not establish all branches','no concurrent readers/writers, provider/send, native CLI/source provenance, HTTP/browser/server/UI, full eleven-table acceptance or deploy','compacted input created by actual pure input codec/compactor; public classifier completion lifecycle is separate earlier Email proof','only declared equal-time classification latest-error tie-set normalization; all other compared outputs/facts/transactions exact']
    receipt['owned_temp_removed']=not root.exists()
    receipt['application_files_unchanged']={p:digest(args.repo/p) for p in receipt['source_files']}==receipt['source_files']
    assert receipt['owned_temp_removed'] and receipt['application_files_unchanged']
    if args.output:
        args.output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:receipt[k] for k in ('checkpoints','equal_checkpoints','differences','owned_temp_removed','application_files_unchanged')}))
    assert not differences,differences


if __name__=='__main__':
    main()
