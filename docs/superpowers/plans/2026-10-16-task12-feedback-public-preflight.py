#!/usr/bin/env python3
"""Real Feedback4 public APIs against synthetic source/prepared target fixtures.

The target deliberately bypasses initialization after an empty Tasks20 fixture
and exact retained-child FK rebind. This proves neither migration nor a cold
constructor, production ancestry, service health, provider or outbound behavior.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
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
SOURCE_SHA = 'ce66a4dd7302080126fb178b4dc8e3983acbc68b2f70c9107bd3e584c4207d5f'
TARGET_SHA = '13dbf3724919fbeac89b7d2aca895ca2651339b3fcb35a322e56ef10d108d55b'
HELPER_SHA = '7174fc6da84370fda1eda1733fd4e3337cae0bd0a7c776a665964efcc79c8833'
PATCHES = (
    ('2026-10-16-task8-store-caller-proposal.patch', '691cc165fdbaaa4e56a04b907dc6c76cbbc717c4e0c3fc9f3c31a95857cc873c'),
    ('2026-10-16-task12-store-misc-caller-proposal.patch', 'aeb1ffd38995428d61f9f01bf4d0a58dfdd223b510b358ff6a7c2e01976706c4'),
    ('2026-10-16-task12-feedback-caller-proposal.patch', '9bf3323406b39fc06fff84496bd6fb2bcda6c3eb990e96a550b878e433a8105d'),
)
FAMILIES = ('feedback_iteration_decisions', 'feedback_processing_batches', 'feedback_processing_items', 'feedback_processing_rounds')
RETAINED = ('feedback_iteration_decision_items', 'feedback_processing_transitions', 'feedback_events')
OWNERS = ('upsert_feedback_event', 'record_feedback_iteration_decision', 'list_feedback_iteration_decisions', 'create_feedback_processing_batch', 'claim_feedback_processing_items', 'reopen_feedback_processing_item', 'list_feedback_processing_rounds', 'list_feedback_processing_rounds_for_batch', 'associate_feedback_processing_turn', 'get_feedback_processing_batch', 'get_feedback_processing_item', 'patch_feedback_processing_item_evidence', '_validate_current_feedback_processing_batch_lineage', '_validate_resolved_feedback_processing_batch', '_resolution_feedback_iteration_decision', 'resolve_feedback_processing_batch', 'list_user_feedback_items', 'list_user_feedback_items_by_keys', 'count_pending_user_feedback_items', 'resolve_feedback_event')
CLOCK = datetime(2040, 1, 1)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value):
    if hasattr(value, 'model_dump'):
        return encoded(value.model_dump(mode='json'))
    if isinstance(value, dict):
        return {str(k): encoded(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encoded(v) for v in value]
    if isinstance(value, bytes):
        return {'blob_hex': value.hex()}
    return value


def worker(args):
    sys.path.insert(0, str(args.code))
    import app.store as sm
    from app.store import AutoReplyStore
    from app.feedback_processing import FeedbackIterationDecision, ResolutionEvidence
    from app.task20_definitions import TASK20_FIELDS, create_task20_schema, initialize_task8_sequence, task20_column
    from app.task12_storage import task12_projection
    assert Path(sm.__file__).resolve() == (args.code / 'app/store.py').resolve()
    assert digest(args.code / 'app/task12_storage.py') == HELPER_SHA

    if args.worker == 'bootstrap':
        store = AutoReplyStore(args.db)
        with store._connect() as db:
            assert db.execute('pragma foreign_keys').fetchone()[0] == 1
            counts = {kind: db.execute(f'select count(*) from {kind}').fetchone()[0] for kind in ('tasks', *FAMILIES, *RETAINED)}
            assert not any(counts.values()), counts
            assert not db.execute('pragma foreign_key_check').fetchall()
            fields = {kind: [r[1] for r in db.execute(f'pragma table_info({kind})')] for kind in FAMILIES}
            assert sum(map(len, fields.values())) == 51
            assert {k: set(v) for k, v in fields.items()} == {k: set(TASK20_FIELDS[k]) for k in FAMILIES}
        args.result.write_text(json.dumps({'constructor': 'original complete AutoReplyStore fresh constructor', 'counts': counts, 'source_fields': fields, 'foreign_keys': 1}))
        return
    if args.worker == 'prepare':
        with sqlite3.connect(args.db) as db:
            assert all(db.execute(f'select count(*) from {k}').fetchone()[0] == 0 for k in ('tasks', *FAMILIES, *RETAINED))
            before = dict(db.execute("select name,sql from sqlite_schema where type='table'"))
            child = 'feedback_iteration_decision_items'
            ddl = before[child]
            replacements = {
                'references feedback_iteration_decisions(id)': 'references tasks("feedback_iteration_decisions__id")',
                'references feedback_processing_items(feedback_key)': 'references tasks("feedback_processing_items__feedback_key")',
                'references feedback_processing_rounds(id)': 'references tasks("feedback_processing_rounds__id")',
            }
            translated = ddl
            for old, new in replacements.items():
                assert translated.count(old) == 1
                translated = translated.replace(old, new)
            child_objects = list(db.execute("select type,name,sql from sqlite_schema where tbl_name=? and type in ('index','trigger') and sql is not null order by type,name", (child,)))
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            db.execute(f'drop table {child}')
            for kind in FAMILIES:
                db.execute(f'drop table {kind}')
            db.execute('drop table tasks')
            create_task20_schema(db)
            initialize_task8_sequence(db, 0)
            db.execute(translated)
            for _, _, sql in child_objects:
                db.execute(sql)
            after = dict(db.execute("select name,sql from sqlite_schema where type='table'"))
            assert set(before) - set(after) == set(FAMILIES)
            assert {k: v for k, v in before.items() if k not in ('tasks', child, *FAMILIES)} == {k: v for k, v in after.items() if k not in ('tasks', child)}
            assert after[child] == translated
            after_objects = list(db.execute("select type,name,sql from sqlite_schema where tbl_name=? and type in ('index','trigger') and sql is not null order by type,name", (child,)))
            assert after_objects == child_objects
        with sqlite3.connect(args.db) as db:
            db.execute('pragma foreign_keys=on')
            assert db.execute('pragma foreign_keys').fetchone()[0] == 1
            assert not db.execute('pragma foreign_key_check').fetchall()
            fks = [list(r) for r in db.execute(f'pragma foreign_key_list({child})')]
        args.result.write_text(json.dumps({'boundary': 'fixture-only empty replacement and exact retained-child3FK rebind; no migration/cold constructor proof', 'empty_feedback_sources_retired': list(FAMILIES), 'other_table_ddl_exact': True, 'child_ddl_only_references_translated': replacements, 'child_index_trigger_sql_exact': True, 'rebound_child_fks': fks, 'observed_methods_foreign_keys': 1}))
        return

    clock = [CLOCK.strftime('%Y-%m-%d %H:%M:%S')]
    original_open = AutoReplyStore._open_connection

    def fixture_open(self):
        db = original_open(self)
        assert db.execute('pragma foreign_keys').fetchone()[0] == 1
        db.create_function('current_timestamp', 0, lambda: clock[0])
        if args.target:
            from app.task8_storage import register_task8_functions
            register_task8_functions(db)
        return db

    AutoReplyStore._open_connection = fixture_open
    if args.target:
        AutoReplyStore._ensure_initialized = lambda self: None
    sequence = iter(range(1, 10000))
    sm.uuid4 = lambda: UUID(int=next(sequence))
    store = AutoReplyStore(args.db)
    records, witnesses, trace, fixture_actions = [], [], [], []
    methods, entered = set(), Counter()

    def query(kind):
        return task12_projection(kind, include_rowid=True) if args.target else f'select rowid as rowid,* from {kind}'

    def facts():
        with store._connect() as db:
            assert db.execute('pragma foreign_keys').fetchone()[0] == 1
            state = {}
            for kind in FAMILIES:
                projection = query(kind)
                fields = ('rowid', *TASK20_FIELDS[kind])
                rows = db.execute(f'select * from ({projection}) order by rowid').fetchall()
                state[kind] = [[[f, encoded(r[f]), type(r[f]).__name__] for f in fields] for r in rows]
                types = ','.join('typeof("' + f + '")' for f in fields)
                state[kind + ':sqlite_types'] = [list(r) for r in db.execute(f'select {types} from ({projection}) order by rowid')]
            for kind in RETAINED:
                order = 'decision_id,feedback_key' if kind == RETAINED[0] else 'id' if kind == RETAINED[1] else 'key'
                state[kind] = [encoded(dict(r)) for r in db.execute(f'select * from {kind} order by {order}')]
            for kind in ('feedback_iteration_decisions', 'feedback_processing_rounds', 'feedback_processing_transitions'):
                name = 'tasks:' + kind if args.target and kind in FAMILIES else kind
                seq = db.execute('select seq from sqlite_sequence where name=?', (name,)).fetchone()
                state[kind + ':water'] = 0 if seq is None else seq[0]
            state['fk_findings'] = [list(r) for r in db.execute('pragma foreign_key_check')]
            assert not state['fk_findings']
            # Verify all three independently keyed retained child endpoints.
            child_rows = db.execute(f'select child.*,decision.batch_id,round.feedback_key as round_feedback_key from feedback_iteration_decision_items child join ({query(FAMILIES[0])}) decision on decision.id=child.decision_id join ({query(FAMILIES[2])}) item on item.feedback_key=child.feedback_key join ({query(FAMILIES[3])}) round on round.id=child.round_id order by child.decision_id,child.feedback_key').fetchall()
            assert len(child_rows) == len(state[RETAINED[0]])
            assert all(r['feedback_key'] == r['round_feedback_key'] for r in child_rows)
            state['three_endpoint_join'] = [encoded(dict(r)) for r in child_rows]
            if args.target:
                structural = [list(r) for r in db.execute('select id,record_type,legacy_key,parent_id from tasks order by id')]
                for row in db.execute("select d.parent_id,b.id from tasks d left join tasks b on b.id=d.parent_id and b.record_type='feedback_processing_batches' and b.\"feedback_processing_batches__batch_id\"=d.\"feedback_iteration_decisions__batch_id\" where d.record_type='feedback_iteration_decisions'"):
                    assert row[0] == row[1] and row[1] is not None
            else:
                structural = []
            global_water = db.execute("select seq from sqlite_sequence where name='tasks'").fetchone()
            witnesses.append({'checkpoint': len(records) + 1, 'target_physical_rows': structural, 'global_tasks_water': 0 if global_water is None else global_water[0]})
            return state

    def call(label, method, *pos, expected_error=None, **kw):
        methods.add(method)
        assert label not in {r['label'] for r in records}
        clock[0] = (CLOCK + timedelta(seconds=len(records) + 1)).strftime('%Y-%m-%d %H:%M:%S')
        trace.clear()
        previous = AutoReplyStore._open_connection

        def traced(self):
            db = previous(self)
            db.set_trace_callback(trace.append)
            return db

        def observe(frame, event, arg):
            if event == 'call' and frame.f_code.co_filename == sm.__file__ and frame.f_code.co_name in OWNERS:
                entered[frame.f_code.co_name] += 1

        AutoReplyStore._open_connection = traced
        assert sys.getprofile() is None
        sys.setprofile(observe)
        try:
            result = getattr(store, method)(*pos, **kw)
            error = None
        except Exception as exc:
            result = None
            error = {'type': type(exc).__name__, 'message': str(exc), 'error_code': getattr(exc, 'error_code', None)}
        finally:
            sys.setprofile(None)
            AutoReplyStore._open_connection = previous
        assert (error is not None) == (expected_error is not None), (label, error)
        if expected_error:
            assert (error['type'], error['message']) == expected_error, (label, error, expected_error)
        txns = [s.strip().upper() for s in trace if s.strip().upper() in ('BEGIN', 'BEGIN IMMEDIATE', 'COMMIT', 'ROLLBACK')]
        current = facts()
        records.append({'label': label, 'method': method, 'outcome': {'error': error} if error else {'return': encoded(result)}, 'transactions': txns, 'facts': current})
        return result

    def fixture_sql(label, sql, params=(), expected_error=None):
        before = facts()
        try:
            with store._connect() as db:
                db.execute(sql, params)
            error = None
        except sqlite3.IntegrityError as exc:
            error = str(exc)
        assert error == expected_error, (label, error, expected_error)
        after = facts()
        if expected_error:
            assert before == after, label
        record = {'label': label, 'error': error, 'facts_unchanged': before == after}
        fixture_actions.append(record)
        return record

    def event(label, key, **kw):
        return call(label, 'upsert_feedback_event', key=key, feedback_token='token-' + key, comment='synthetic original feedback', source='fixture', received_at='2040-01-01', **kw)

    def receipt():
        return ResolutionEvidence(commit_sha='a' * 40, test_evidence={'pytest': {'exit_code': 0}}, restart_evidence={'launchd_label': 'com.ceo-agent-service.main', 'before_pid': 10, 'after_pid': 11}, health_evidence={'url': 'http://127.0.0.1:8765/healthz', 'status_code': 200, 'ok': True}, backlog_evidence={'processing': 0, 'failed': 0, 'retryable': 0})

    def decision(keys, root='Synthetic code boundary needs an existing-column repair.'):
        return FeedbackIterationDecision(scope='code', root_cause=root, feedback_keys=keys, source_references=['fixture:feedback'], why_not_code='Code scope is selected explicitly for this synthetic receipt.', acceptance={'scenario': 'synthetic feedback', 'expected_behavior': 'Source and target return the same persisted round and receipt.', 'verification': ['public API parity']})

    if args.worker == 'negative':
        event('negative_seed_event', 'negative')
        # Prepared legacy-gap fixture: source event exists, mutable projection absent.
        sql = "delete from tasks where record_type='feedback_processing_items' and \"feedback_processing_items__feedback_key\"='negative'" if args.target else "delete from feedback_processing_items where feedback_key='negative'"
        fixture_sql('legacy-gap-remove-empty-projection', sql)
        assert call('negative_control_resolve', 'resolve_feedback_event', 'negative') is True
        item = call('negative_control_readback', 'get_feedback_processing_item', 'negative')
        args.result.write_text(json.dumps({'resolved_at': item.resolved_at, 'expected_clock': records[-2]['facts'][RETAINED[2]][0]['resolved_at'], 'status': item.status, 'records': records, 'fixture': 'public event followed by explicit fixture deletion of only its unreferenced item; FK stays ON'}))
        return

    # Public source-event INSERT, repeated pending UPSERT and source completion.
    assert call('missing_item', 'get_feedback_processing_item', 'absent') is None
    assert call('missing_event_resolve', 'resolve_feedback_event', 'absent') is False
    event('pending_insert', 'standalone')
    event('pending_conflict', 'standalone', rating='useful')
    assert call('standalone_pending', 'get_feedback_processing_item', 'standalone').status == 'pending'
    assert call('standalone_resolve', 'resolve_feedback_event', 'standalone') is True
    standalone_time = call('standalone_resolved_read', 'get_feedback_processing_item', 'standalone').resolved_at
    event('resolved_event_conflict_preserves_time', 'standalone')
    assert call('standalone_time_preserved', 'get_feedback_processing_item', 'standalone').resolved_at == standalone_time
    assert call('resolved_event_repeat_native_clock', 'resolve_feedback_event', 'standalone') is True
    standalone_latest = call('resolved_event_clock_rewritten', 'get_feedback_processing_item', 'standalone').resolved_at
    assert standalone_latest != standalone_time
    gap_delete = "delete from tasks where record_type='feedback_processing_items' and \"feedback_processing_items__feedback_key\"=?" if args.target else 'delete from feedback_processing_items where feedback_key=?'
    fixture_sql('resolved_source_missing_projection_fixture', gap_delete, ('standalone',))
    event('resolved_source_native_insert_missing_projection', 'standalone')
    assert call('resolved_source_native_insert_readback', 'get_feedback_processing_item', 'standalone').resolved_at == standalone_latest
    call('batch_missing_source_rejected', 'create_feedback_processing_batch', ['absent'], batch_id='bad-missing', expected_error=('FeedbackProcessingBatchError', 'feedback processing batch definition conflict'))
    assert call('resolved_batch_source_rejected', 'create_feedback_processing_batch', ['standalone'], batch_id='bad-resolved', expected_error=('FeedbackProcessingBatchError', 'feedback processing batch definition conflict')) is None

    for key in ('alpha', 'beta'):
        event('seed_' + key, key)
    fixture_sql('pending_source_missing_projection_fixture', gap_delete, ('beta',))
    batch = call('batch_create', 'create_feedback_processing_batch', ['alpha', 'beta', 'alpha'], batch_id='main')
    assert batch.status == 'pending' and batch.requested_count == 2
    assert call('batch_create_idempotent', 'create_feedback_processing_batch', ['beta', 'alpha'], batch_id='main') == batch
    call('batch_membership_conflict', 'create_feedback_processing_batch', ['alpha'], batch_id='main', expected_error=('FeedbackProcessingBatchError', 'feedback processing batch definition conflict'))
    assert call('pending_no_receipt_close', 'resolve_feedback_processing_batch', 'main', commit_is_ancestor=True) is False
    claims = call('batch_claim', 'claim_feedback_processing_items', 'main', ['alpha', 'beta'])
    assert [i.current_round_id for i in claims] == [1, 2]
    assert all(i.status == 'processing' for i in claims)
    assert call('same_claim_retry', 'claim_feedback_processing_items', 'main', ['alpha', 'beta']) == claims
    call('claim_membership_conflict', 'claim_feedback_processing_items', 'main', ['alpha'], expected_error=('FeedbackProcessingBatchError', 'feedback processing batch definition conflict'))
    event('processing_event_conflict_keeps_projection', 'alpha')
    assert call('processing_projection_unchanged', 'get_feedback_processing_item', 'alpha').status == 'processing'
    call('reopen_processing_rejected', 'reopen_feedback_processing_item', 'alpha', reason='new evidence', expected_error=('FeedbackProcessingReopenError', 'feedback is currently processing'))
    for key in ('alpha', 'beta'):
        item = call('associate_' + key, 'associate_feedback_processing_turn', key, expected_batch_id='main', workbench_task_id='task-shared', workbench_turn_id='turn-shared', attempt_id=1 if key == 'alpha' else 2, agent_run_id=101 if key == 'alpha' else 102)
        assert item.status == 'processing'
        evidence = receipt()
        call('evidence_' + key, 'patch_feedback_processing_item_evidence', key, commit_sha=evidence.commit_sha, test_evidence=evidence.test_evidence, restart_evidence=evidence.restart_evidence, health_evidence=evidence.health_evidence, note='adopted synthetic fix')
    prior = facts()
    evidence = receipt()
    call('evidence_noop', 'patch_feedback_processing_item_evidence', 'alpha', commit_sha=evidence.commit_sha, test_evidence=evidence.test_evidence, restart_evidence=evidence.restart_evidence, health_evidence=evidence.health_evidence, note='adopted synthetic fix')
    assert facts() == prior
    call('evidence_cannot_resolve', 'patch_feedback_processing_item_evidence', 'alpha', status='resolved', expected_error=('ValueError', 'only batch resolution may mark feedback resolved'))
    first = call('decision_first', 'record_feedback_iteration_decision', 'main', decision(['alpha', 'beta']), workbench_task_id='task-shared', workbench_turn_id='turn-shared')
    second = call('decision_second_append', 'record_feedback_iteration_decision', 'main', decision(['alpha', 'beta'], 'Second adopted code classification.'), workbench_task_id='task-shared', workbench_turn_id='turn-shared')
    assert (first.id, second.id) == (1, 2) and first.round_ids == [1, 2]
    assert len(call('decision_readback', 'list_feedback_iteration_decisions', 'main')) == 2
    call('decision_wrong_association', 'record_feedback_iteration_decision', 'main', decision(['alpha', 'beta']), workbench_task_id='wrong', workbench_turn_id='wrong', expected_error=('FeedbackIterationAssociationMismatchError', 'feedback iteration decision must match the current processing round association'))
    call('resolution_bad_ancestry_rollback', 'resolve_feedback_processing_batch', 'main', receipt(), commit_is_ancestor=False, expected_error=('ValueError', 'resolution commit is not an ancestor of local main'))
    assert call('batch_resolve', 'resolve_feedback_processing_batch', 'main', receipt(), commit_is_ancestor=True) is True
    assert call('batch_resolved_read', 'get_feedback_processing_batch', 'main').status == 'resolved'
    assert call('resolved_idempotent_receipt', 'resolve_feedback_processing_batch', 'main', receipt(), commit_is_ancestor=True) is True
    assert call('resolved_idempotent_no_receipt', 'resolve_feedback_processing_batch', 'main', commit_is_ancestor=True) is True
    completed = call('rounds_for_batch', 'list_feedback_processing_rounds_for_batch', 'main')
    assert len(completed) == 2 and all(r.receipt_version == 2 and r.status == 'resolved' for r in completed)
    assert call('batch_recreate_resolved_idempotent', 'create_feedback_processing_batch', ['alpha', 'beta'], batch_id='main').status == 'resolved'
    reopened = call('reopen_alpha', 'reopen_feedback_processing_item', 'alpha', reason='a distinct synthetic follow-up')
    assert reopened.status == 'pending' and reopened.current_round_id == 0 and reopened.batch_id == ''
    assert call('reopen_pending_idempotent', 'reopen_feedback_processing_item', 'alpha', reason='same request') == reopened
    history = call('alpha_history_after_reopen', 'list_feedback_processing_rounds', 'alpha')
    assert history[0].id == 1 and history[0].status == 'resolved' and history[0].reopened_at
    call('new_batch', 'create_feedback_processing_batch', ['alpha'], batch_id='follow-up')
    claims = call('new_round_claim', 'claim_feedback_processing_items', 'follow-up', ['alpha'])
    assert claims[0].current_round_id == 3
    call('new_round_associate', 'associate_feedback_processing_turn', 'alpha', expected_batch_id='follow-up', workbench_task_id='task-follow-up', workbench_turn_id='turn-follow-up', attempt_id=3, agent_run_id=103)
    evidence = receipt()
    call('new_round_evidence', 'patch_feedback_processing_item_evidence', 'alpha', commit_sha=evidence.commit_sha, test_evidence=evidence.test_evidence, restart_evidence=evidence.restart_evidence, health_evidence=evidence.health_evidence, note='second round receipt')
    third = call('new_round_decision', 'record_feedback_iteration_decision', 'follow-up', decision(['alpha']), workbench_task_id='task-follow-up', workbench_turn_id='turn-follow-up')
    assert third.id == 3 and third.round_ids == [3]
    assert call('new_round_resolve', 'resolve_feedback_processing_batch', 'follow-up', receipt(), commit_is_ancestor=True) is True
    assert call('old_batch_new_lineage_readback', 'resolve_feedback_processing_batch', 'main', commit_is_ancestor=True) is True
    assert [r.round_number for r in call('alpha_two_round_history', 'list_feedback_processing_rounds', 'alpha')] == [2, 1]
    transitions = call('alpha_transitions', 'list_feedback_processing_transitions', 'alpha')
    assert [(t.from_status, t.to_status) for t in transitions] == [('processing', 'resolved'), ('pending', 'processing'), ('resolved', 'pending'), ('processing', 'resolved'), ('pending', 'processing')]
    assert call('native_source_readback', 'get_feedback_event', 'alpha').resolved_at
    assert call('source_token_readback', 'list_feedback_events_for_token', 'token-alpha')
    assert len(call('source_tokens_bulk', 'list_feedback_events_for_tokens', ['token-alpha', 'token-beta'])) == 2
    assert len(call('user_feedback_list', 'list_user_feedback_items')) == 3
    assert len(call('user_feedback_by_key', 'list_user_feedback_items_by_keys', ['alpha', 'beta'])) == 2
    assert call('pending_feedback_count', 'count_pending_user_feedback_items') == 0
    beta_before = call('beta_resolved_projection', 'get_feedback_processing_item', 'beta')
    event('resolved_batch_event_upsert', 'beta')
    beta_after = call('resolved_batch_and_time_preserved', 'get_feedback_processing_item', 'beta')
    assert (beta_after.batch_id, beta_after.resolved_at) == ('main', beta_before.resolved_at)
    call('reopen_after_source_edit_rejected', 'reopen_feedback_processing_item', 'beta', reason='source changed after receipt', expected_error=('FeedbackProcessingReopenError', 'feedback processing history is incomplete'))

    constraints = []
    # Observed constraint calls keep FK ON and must leave all source facts unchanged.
    decision_update = f'update tasks set payload_json=payload_json where record_type={FAMILIES[0]!r}' if args.target else 'update feedback_iteration_decisions set decision_json=decision_json'
    decision_delete = f'delete from tasks where record_type={FAMILIES[0]!r}' if args.target else 'delete from feedback_iteration_decisions'
    for label, sql in (('decision_append_only_update', decision_update), ('decision_append_only_delete', decision_delete)):
        constraints.append(fixture_sql(label, sql, expected_error='feedback iteration decisions are append-only'))
    constraints.append(fixture_sql('decision_item_append_only_update', 'update feedback_iteration_decision_items set round_id=round_id', expected_error='feedback iteration decision items are append-only'))
    constraints.append(fixture_sql('decision_item_append_only_delete', 'delete from feedback_iteration_decision_items', expected_error='feedback iteration decision items are append-only'))
    for label, values in (('decision_fk', (9999, 'alpha', 1)), ('item_fk', (1, 'absent', 1)), ('round_fk', (1, 'standalone', 9999))):
        constraints.append(fixture_sql(label, 'insert into feedback_iteration_decision_items(decision_id,feedback_key,round_id) values(?,?,?)', values, expected_error='FOREIGN KEY constraint failed'))

    # Abort after the first claimed item/round/transition was written. Rollback
    # must also restore the native/public AUTO sequence and target allocator.
    for key in ('rollback-a', 'rollback-b'):
        event('seed_' + key, key)
    call('rollback_batch_create', 'create_feedback_processing_batch', ['rollback-a', 'rollback-b'], batch_id='rollback')
    before = facts()
    before_physical = {k: witnesses[-1][k] for k in ('target_physical_rows', 'global_tasks_water')}
    table = 'tasks' if args.target else 'feedback_processing_rounds'
    condition = f"new.record_type='feedback_processing_rounds' and new.{task20_column(FAMILIES[3], 'feedback_key')}='rollback-b'" if args.target else "new.feedback_key='rollback-b'"
    fixture_sql('install_native_abort', f"create trigger fixture_abort_round before insert on {table} when {condition} begin select raise(abort,'fixture round write abort'); end")
    call('native_partial_claim_rollback', 'claim_feedback_processing_items', 'rollback', ['rollback-a', 'rollback-b'], expected_error=('IntegrityError', 'fixture round write abort'))
    assert facts() == before
    assert {k: witnesses[-1][k] for k in before_physical} == before_physical
    assert records[-1]['transactions'] == ['BEGIN IMMEDIATE', 'ROLLBACK']
    fixture_sql('remove_native_abort', 'drop trigger fixture_abort_round')
    assert [i.current_round_id for i in call('claim_after_native_rollback', 'claim_feedback_processing_items', 'rollback', ['rollback-a', 'rollback-b'])] == [4, 5]

    # A native zero-row CAS must trigger the existing caller error/rollback.
    event('seed_cas_zero', 'cas-zero')
    call('cas_batch_create', 'create_feedback_processing_batch', ['cas-zero'], batch_id='cas-zero')
    before = facts()
    before_physical = {k: witnesses[-1][k] for k in ('target_physical_rows', 'global_tasks_water')}
    table = 'tasks' if args.target else 'feedback_processing_items'
    condition = f"new.record_type='feedback_processing_items' and new.{task20_column(FAMILIES[2], 'feedback_key')}='cas-zero' and new.{task20_column(FAMILIES[2], 'status')}='processing'" if args.target else "new.feedback_key='cas-zero' and new.status='processing'"
    fixture_sql('install_zero_cas', f'create trigger fixture_zero_cas before update on {table} when {condition} begin select raise(ignore); end')
    call('native_zero_cas_rollback', 'claim_feedback_processing_items', 'cas-zero', ['cas-zero'], expected_error=('FeedbackProcessingClaimError', 'feedback processing claim rejected'))
    assert facts() == before
    assert {k: witnesses[-1][k] for k in before_physical} == before_physical
    fixture_sql('remove_zero_cas', 'drop trigger fixture_zero_cas')
    assert call('claim_after_cas_rollback', 'claim_feedback_processing_items', 'cas-zero', ['cas-zero'])[0].current_round_id == 6
    final = facts()
    assert len(final[RETAINED[0]]) == 5 and len(final[FAMILIES[3]]) == 6 and len(final[FAMILIES[0]]) == 3
    assert set(entered) == set(OWNERS), set(OWNERS) - set(entered)
    if args.target:
        divergent = [r for r in witnesses[-1]['target_physical_rows'] if r[1] in (FAMILIES[0], FAMILIES[3]) and r[0] != int(r[2])]
        assert len(divergent) == 9
    args.result.write_text(json.dumps({'records': records, 'public_methods': sorted(methods), 'owner_entry_counts': dict(entered), 'constraints': constraints, 'physical_witness': witnesses[-1], 'fixture_actions': fixture_actions, 'clock_end': clock[0]}, ensure_ascii=False))


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
    assert Path(sys.executable).resolve() == PYTHON.resolve(), 'run pinned Python -B'
    script = Path(__file__).resolve()
    assert digest(args.repo / 'app/store.py') == SOURCE_SHA
    helper_bytes = subprocess.check_output(['git', 'show', 'ef26ceadf:app/task12_storage.py'], cwd=args.repo)
    assert hashlib.sha256(helper_bytes).hexdigest() == HELPER_SHA
    patches = [args.repo / 'docs/superpowers/plans' / name for name, _ in PATCHES]
    for patch, (_, expected) in zip(patches, PATCHES, strict=True):
        assert digest(patch) == expected
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    evidence = {'harness_sha256': digest(script), 'source_store_sha256': SOURCE_SHA, 'target_store_sha256': TARGET_SHA, 'canonical_helper_sha256': HELPER_SHA, 'helper_git_pin': 'ef26ceadf:app/task12_storage.py', 'patches': dict(PATCHES), 'scope': 'real Feedback4 public methods on normal fresh source and constructor-bypassed explicitly empty Tasks20+retained3FK target fixture', 'normalization': ['Synthetic SQL current_timestamp increments one second per recorded public call identically on both sides.', 'Deterministic Store uuid4 integer stream; explicitly supplied batch IDs.', 'No feedback public IDs, field values, storage classes, rowcounts or statuses are normalized. Absent source AUTO water and target zero both mean initial effective water zero.'], 'limitations': ['Target is prepared from an EMPTY synthetic source; no nonempty forward copy, preserved captured rows, inverse/current facts, predecessor/reforward or cold constructor proof.', 'Exactly three retained decision-items FK REFERENCES are rebound in the fixture; production migration companion and final child Actions conversion remain unimplemented by this proof.', 'Only source code scope decisions and synthetic prevalidated commit ancestry/receipt values are exercised; no Git ancestry, actual launchd/health/network/native CLI/provider/outbound action occurs.', 'Two temporary abort/IGNORE triggers are explicit fault-injection fixtures; two unreferenced item deletions create pending/resolved source events with absent projections; original methods and all observed connections keep foreign_keys=ON.', 'The helper is frozen from ef26ceadf at7174fc6d; a later separate REPLACE+expression-NULL-default repair is outside these actual plain/UPSERT feedback statements.'], 'no_app_test_live_db_provider_native_cli_network_main_push_deploy_changes': True}
    with tempfile.TemporaryDirectory(prefix='task12-feedback-public-', dir='/tmp') as temp:
        root = Path(temp)
        source, target = root / 'source', root / 'target'
        for code in (source, target):
            shutil.copytree(args.repo / 'app', code / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            (code / 'app/task12_storage.py').write_bytes(helper_bytes)
        source_db, target_db = source / 'fixture.sqlite3', target / 'fixture.sqlite3'

        def run(stage, code, db, target_side=False, label=''):
            result = root / f'{stage}-{label or ("target" if target_side else "source")}.json'
            cmd = [str(PYTHON), '-B', str(script), '--worker', stage, '--code', str(code), '--db', str(db), '--result', str(result)]
            if target_side:
                cmd.append('--target')
            proc = subprocess.run(cmd, cwd=code, env=env, capture_output=True, text=True, timeout=120)
            if proc.returncode:
                raise RuntimeError(f'{stage} {label} target={target_side}: {proc.stderr[-9000:]}')
            return json.loads(result.read_text())

        evidence['bootstrap'] = run('bootstrap', source, source_db)
        with sqlite3.connect(source_db) as src, sqlite3.connect(target_db) as dst:
            src.backup(dst)
        for patch in patches:
            subprocess.run(['git', 'apply', '--check', str(patch)], cwd=target, capture_output=True, text=True, check=True)
            subprocess.run(['git', 'apply', str(patch)], cwd=target, capture_output=True, text=True, check=True)
        assert digest(target / 'app/store.py') == TARGET_SHA
        evidence['prepared_fixture'] = run('prepare', target, target_db, True)
        source_negative, target_control, mutant = root / 'source-control.sqlite3', root / 'target-control.sqlite3', root / 'mutant'
        shutil.copytree(target / 'app', mutant / 'app')
        mutant_db = mutant / 'fixture.sqlite3'
        for origin, clone in ((source_db, source_negative), (target_db, target_control), (target_db, mutant_db)):
            with sqlite3.connect(origin) as src, sqlite3.connect(clone) as dst:
                src.backup(dst)
        old = "insert_sql={'resolved_at': 'current_timestamp'}, insert_parameters=())"
        new = 'insert_sql={\'resolved_at\': "\'\'"}, insert_parameters=())'
        mod = mutant / 'app/store.py'
        text = mod.read_text()
        assert text.count(old) == 1
        mod.write_text(text.replace(old, new))
        # Passing complete normal differential precedes every negative control.
        native = run('scenario', source, source_db)
        proposed = run('scenario', target, target_db, True)
        assert native['public_methods'] == proposed['public_methods']
        assert native['owner_entry_counts'] == proposed['owner_entry_counts']
        assert native['constraints'] == proposed['constraints']
        assert native['fixture_actions'] == proposed['fixture_actions']
        nr, tr = native['records'], proposed['records']
        assert len(nr) == len(tr) == 75 and len({r['label'] for r in nr}) == 75
        assert len(native['public_methods']) == len(proposed['public_methods']) == 21
        assert len(native['owner_entry_counts']) == len(proposed['owner_entry_counts']) == 20
        assert sum('error' in r['outcome'] for r in nr) == 11
        differences = [{'label': a['label'], 'fields': [key for key in a if a[key] != b[key]]} for a, b in zip(nr, tr, strict=True) if a != b]
        assert not differences, differences
        control_source = run('negative', source, source_negative, label='source-control')
        control_target = run('negative', target, target_control, True, 'target-control')
        broken = run('negative', mutant, mutant_db, True, 'wrong-insert-expression')
        assert control_source['records'] == control_target['records']
        assert control_source['resolved_at'] == control_target['resolved_at'] == control_source['expected_clock']
        assert broken['status'] == 'resolved' and broken['resolved_at'] == ''
        assert broken['resolved_at'] != control_target['resolved_at']
        evidence.update({'fixed_coverage_assertions': {'paired_checkpoints':75,'public_methods':21,'entered_scoped_owners':20,'expected_public_errors':11,'constraint_probes':7,'independent_source_public_auto_ids':True,'physical_row_and_water_rollback_asserted':True,'missing_item_source_expression_fixtures':2,'no_equal_error_as_success':True}, 'checkpoint_count': len(nr), 'equal_checkpoints': len(nr), 'differences': differences, 'public_methods': native['public_methods'], 'owner_entry_counts': native['owner_entry_counts'], 'expected_error_count': sum('error' in r['outcome'] for r in nr), 'transactions': dict(Counter(','.join(r['transactions']) for r in nr)), 'checkpoint_labels': [r['label'] for r in nr], 'constraint_probes': native['constraints'], 'explicit_fixture_actions': native['fixture_actions'], 'final_three_endpoint_join': nr[-1]['facts']['three_endpoint_join'], 'record_sha256': {side: hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest() for side, records in (('source', nr), ('target', tr))}, 'final_row_counts': {k: len(nr[-1]['facts'][k]) for k in (*FAMILIES, *RETAINED)}, 'raw_target_physical_witness': proposed['physical_witness'], 'final_fk_findings': {'source': nr[-1]['facts']['fk_findings'], 'target': tr[-1]['facts']['fk_findings']}, 'negative_control': {'mutation': 'only explicit resolve_feedback_event native INSERT resolved_at changes current_timestamp to empty SQL string; UPSERT tail stays original', 'passing_source_resolved_at': control_source['resolved_at'], 'passing_target_resolved_at': control_target['resolved_at'], 'mutant_resolved_at': broken['resolved_at'], 'mutant_status_and_boolean_still_success': broken['status'] == 'resolved' and broken['records'][-2]['outcome'] == {'return': True}, 'detected_by_readback': True}, 'decisive_outcomes': {r['label']: r['outcome'] for r in nr if r['label'] in ('batch_claim', 'same_claim_retry', 'resolved_source_native_insert_readback', 'rounds_for_batch', 'alpha_two_round_history', 'decision_first', 'decision_second_append', 'batch_resolve', 'resolved_batch_and_time_preserved', 'reopen_alpha', 'new_round_claim', 'old_batch_new_lineage_readback', 'native_partial_claim_rollback', 'native_zero_cas_rollback', 'claim_after_native_rollback', 'claim_after_cas_rollback')}})
    evidence['owned_temp_removed'] = not root.exists()
    if args.output:
        args.output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: evidence[k] for k in ('checkpoint_count', 'equal_checkpoints', 'differences', 'expected_error_count', 'owned_temp_removed')}))


if __name__ == '__main__':
    main()
