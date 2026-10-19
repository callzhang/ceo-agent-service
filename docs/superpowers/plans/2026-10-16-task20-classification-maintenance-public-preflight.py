#!/usr/bin/env python3
"""Real two-method proof; synthetic full fresh source / explicit EMPTY Tasks20.

All nine proposal patches are applied only to a temporary copied app tree.
The source runs both normal constructors. Target construction is bypassed after
empty schema preparation. Maintenance opens its own original raw connection;
its observer records native SQL/rowcounts/close and never rewrites SQL/results.
"""
from __future__ import annotations

import argparse
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
KIND = 'email_agent_classification_tasks'
PREFIX = '2026-10-16-task20-classification-maintenance-public-'
PATCH_NAMES = ('task8-store', 'task8-workbench', 'task12-email-native', 'task12-store-misc', 'task12-feedback', 'task12-workbench-children', 'task20-external-writers', 'task20-console-readers', 'task20-remaining-readers')
STAMP = '2040-01-01 00:00:00'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def value_digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def worker(args):
    sys.path.insert(0, str(args.code))
    import app.store as sm
    import app.storage_maintenance as maintenance
    from app.email_store import EmailStore
    from app.task12_storage import insert_task12, task12_projection, task12_set_parts
    from app.task20_definitions import create_task20_schema, initialize_task8_sequence, task20_column
    from app.task8_storage import register_task8_functions

    assert Path(sm.__file__).resolve() == (args.code / 'app/store.py').resolve()
    assert Path(maintenance.__file__).resolve() == (args.code / 'app/storage_maintenance.py').resolve()
    external = []

    def forbid(event, _details):
        if event in ('socket.connect', 'subprocess.Popen', 'os.system'):
            external.append(event)
            raise AssertionError('unexpected external action: ' + event)

    # Imported dependencies may invoke platform's file utility; only the
    # constructor/method execution phase is guarded, as in the adjacent proof.
    sys.addaudithook(forbid)
    if args.worker == 'bootstrap':
        store = sm.AutoReplyStore(args.db)
        email = EmailStore(args.db)
        with store._connect() as db, email._connect() as edb:
            counts = {name: db.execute(f'select count(*) from {name}').fetchone()[0] for name in ('tasks', KIND)}
            assert set(counts.values()) == {0}
            assert not db.execute('pragma foreign_key_check').fetchall()
            fields = [row[1] for row in edb.execute(f'pragma table_info({KIND})')]
            assert 'task_id' in fields and 'id' not in fields
            result = {'normal_full_fresh_store_constructor': True, 'normal_full_fresh_email_constructor': True, 'empty_counts': counts, 'classification_fields': fields, 'table_count': db.execute("select count(*) from sqlite_schema where type='table'").fetchone()[0], 'fk_findings': [], 'source_ddl_sha256': {name: value_digest(db.execute('select sql from sqlite_schema where name=?', (name,)).fetchone()[0]) for name in ('tasks', KIND)}}
        args.result.write_text(json.dumps(result))
        return
    if args.worker == 'prepare':
        with sqlite3.connect(args.db) as db:
            assert all(db.execute(f'select count(*) from {name}').fetchone()[0] == 0 for name in ('tasks', KIND))
            before = dict(db.execute("select name,sql from sqlite_schema where type='table'"))
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            db.execute(f'drop table {KIND}')
            db.execute('drop table tasks')
            create_task20_schema(db)
            initialize_task8_sequence(db, 0)
            after = dict(db.execute("select name,sql from sqlite_schema where type='table'"))
            assert set(after) == set(before) - {KIND}
            assert {k: v for k, v in before.items() if k not in ('tasks', KIND)} == {k: v for k, v in after.items() if k != 'tasks'}
        args.result.write_text(json.dumps({'explicit_empty_target_preparation': True, 'removed_empty_native_queue': KIND, 'all_other_table_ddl_exact': True, 'target_constructor': 'bypassed; not tested', 'migration': 'not tested', 'target_tasks_ddl_sha256': value_digest(after['tasks'])}))
        return

    native_connect = sqlite3.connect
    active = [False]
    trace, updates, closes = [], [], []
    entered = {}

    class ObservedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            updating = sql.lstrip().lower().startswith(('update tasks ', f'update {KIND} '))
            try:
                cursor = super().execute(sql, parameters)
            except Exception as exc:
                if active[0] and updating:
                    updates.append({'rowcount': None, 'error': type(exc).__name__, 'message': str(exc)})
                raise
            if active[0] and updating:
                updates.append({'rowcount': cursor.rowcount})
            return cursor

        def close(self):
            if active[0]:
                closes.append({'transaction_open_before_native_close': self.in_transaction})
            return super().close()

    def observed_connect(*pos, **kw):
        assert 'factory' not in kw
        db = native_connect(*pos, factory=ObservedConnection, **kw)

        def traced(sql):
            command = sql.strip().upper()
            if active[0] and (command.startswith('BEGIN') or command in ('COMMIT', 'ROLLBACK')):
                trace.append(command)
        db.set_trace_callback(traced)
        return db

    sqlite3.connect = observed_connect

    def profile(frame, event, _arg):
        if event == 'call' and frame.f_code.co_filename in (sm.__file__, maintenance.__file__):
            owner = frame.f_code.co_qualname
            if owner in ('AutoReplyStore._runtime_operation_parent_exists', 'compact_done_email_classification_inputs'):
                entered[owner] = entered.get(owner, 0) + 1

    sys.setprofile(profile)
    coordinates, records = [], []

    def connect(path):
        db = native_connect(path)
        db.row_factory = sqlite3.Row
        db.execute('pragma foreign_keys=on')
        if args.target:
            register_task8_functions(db)
        return db

    def copy_case(name):
        path = args.db.parent / ('case-' + name + '.sqlite3')
        with native_connect(args.db) as source, native_connect(path) as target:
            source.backup(target)
        return path

    def raw_input(key):
        return json.dumps({'stable_message_identity': key, 'provider_locator': {'account_id': 'synthetic', 'folder': 'INBOX', 'uidvalidity': 73, 'uid': 41}, 'config_version': 'fixture-v1', 'unsubscribe_candidates': [], 'subject': 'Synthetic subject', 'message': 'Synthetic Unicode 正文 ' * 300, 'scheduled_consumer': {'schema': 'fixture-scheduled-v1', 'scheduled_task_id': 7, 'scheduled_task_run_id': 8, 'skill_names': ['fixture'], 'skill_protocol_source': 'fixture', 'prompt': 'Synthetic prompt ' * 80, 'skill_protocol': 'fixture protocol', 'skill_materials': [{'name': 'fixture', 'content': 'Synthetic material ' * 100}]}}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

    def seed(path, rows):
        with connect(path) as db:
            for rowid, key, status, body in rows:
                values = {'task_id': key, 'channel': 'email', 'stable_message_identity': 'identity:' + str(rowid), 'status': status, 'owner': 'fixture-owner', 'generation': 3, 'attempt_count': 4, 'lease_expires_at': '2040-01-02', 'available_at': '2040-01-03', 'input_json': body, 'result_json': '{"sentinel":"result"}', 'error': 'preserve error sentinel', 'created_at': STAMP, 'updated_at': STAMP}
                if args.target:
                    inserted = insert_task12(db, KIND, values, source_rowid=rowid)
                    physical = inserted.physical_id
                    assert physical != rowid
                else:
                    fields = ','.join(values)
                    db.execute(f'insert into {KIND} (rowid,{fields}) values (?,' + ','.join('?' for _ in values) + ')', (rowid, *values.values()))
                    physical = None
                coordinates.append({'case': path.stem, 'source_rowid': rowid, 'task_id': key, 'physical_tasks_id': physical})
        assert len(facts(path)) == len(rows), 'fixture seed failed'

    def facts(path):
        with connect(path) as db:
            query = task12_projection(KIND, include_rowid=True) if args.target else f'select rowid,* from {KIND}'
            rows = [dict(row) for row in db.execute(f'select * from ({query}) order by rowid')]
            assert all('id' not in row and 'rowid' in row and 'task_id' in row for row in rows)
            assert not db.execute('pragma foreign_key_check').fetchall(), 'fixture FK failure'
            # Store every finite field, including the original JSON text and scalar
            # class, for equality. Only synthetic fixtures occur in this report.
            return [{k: {'value': v, 'scalar_type': type(v).__name__} for k, v in row.items()} for row in rows]

    def parent(path, label, key, expected):
        with connect(path) as db:
            before_changes = db.total_changes
            result = sm.AutoReplyStore._runtime_operation_parent_exists(db, 'email_classification', key)
            assert db.total_changes == before_changes and not db.in_transaction
        records.append({'label': label, 'return': result, 'expected': expected, 'read_only': True})
        if not args.mutant:
            assert result is expected, (label, result, expected)
        return result

    def compact(path, label, expected=None, error=None):
        before = facts(path)
        trace.clear()
        updates.clear()
        closes.clear()
        active[0] = True
        try:
            result = maintenance.compact_done_email_classification_inputs(path)
            outcome = {'return': result}
        except Exception as exc:
            outcome = {'error': type(exc).__name__, 'message': str(exc)}
        finally:
            active[0] = False
        after = facts(path)
        record = {'label': label, 'outcome': outcome, 'transactions': list(trace), 'native_update_calls': list(updates), 'native_close': list(closes), 'before': before, 'after': after}
        records.append(record)
        if error:
            assert outcome == {'error': 'IntegrityError', 'message': error}, outcome
        elif not args.mutant and expected is not None:
            assert outcome == {'return': expected}, (label, outcome, expected)
        return record

    def body(row):
        return row['input_json']['value']

    def check_compact(original, updated):
        old, new = json.loads(original), json.loads(updated)
        assert new['input_compacted'] is True
        assert new['input_sha256'] == value_digest(original) and new['input_bytes'] == len(original.encode('utf-8'))
        assert all(new[key] == old[key] for key in ('stable_message_identity', 'provider_locator', 'config_version', 'unsubscribe_candidates'))
        identity = {key: value for key, value in old.items() if key != 'scheduled_consumer'}
        assert new['identity_sha256'] == value_digest(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        ref, scheduled = new['scheduled_consumer_reference'], old['scheduled_consumer']
        assert all(ref[key] == scheduled[key] for key in ('schema', 'scheduled_task_id', 'scheduled_task_run_id', 'skill_names', 'skill_protocol_source'))
        assert ref['prompt_sha256'] == value_digest(scheduled['prompt']) and ref['skill_protocol_sha256'] == value_digest(scheduled['skill_protocol'])
        assert ref['skill_materials'] == [{'name': 'fixture', 'content_sha256': value_digest(scheduled['skill_materials'][0]['content'])}]
        assert 'message' not in new and 'prompt' not in ref and 'skill_protocol' not in ref

    path = copy_case('parent')
    parent_rows = [(101, 'key-running', 'running', '{}'), (205, 'key-done', 'done', '{}'), (309, 'key-pending', 'pending', '{}'), (411, 'key-failed', 'failed', '{}'), (513, None, 'running', '{}'), (615, '007', 'running', '{}'), (717, '7', 'done', '{}'), (819, '键:unicode', 'running', '{}')]
    seed(path, parent_rows)
    for key, expected in [('key-running', True), ('key-done', False), ('key-pending', False), ('key-failed', False), ('missing', False), ('007', True), ('7', False), ('键:unicode', True), (None, False), ('101', False), ('1', False)]:
        parent(path, 'parent:' + repr(key), key, expected)
    if args.mutant == 'parent':
        args.result.write_text(json.dumps({'records': records, 'fixture_seed_verified': True, 'entered_actual_owners': entered}))
        return
    # Caller-owned transaction, actual method read, then explicit caller rollback.
    initial = facts(path)
    with connect(path) as db:
        db.execute('begin immediate')
        if args.target:
            parts = task12_set_parts(KIND, {'status': 'running'})
            db.execute(f'update tasks set {parts.sql} where record_type=? and cast(legacy_key as integer)=205', (*parts.parameters, KIND))
        else:
            db.execute(f"update {KIND} set status='running' where rowid=205")
        assert db.in_transaction
        assert sm.AutoReplyStore._runtime_operation_parent_exists(db, 'email_classification', 'key-done') is True
        assert db.in_transaction
        db.rollback()
    assert facts(path) == initial
    records.append({'label': 'parent:caller_transaction', 'before_equals_after_explicit_rollback': True, 'method_preserves_open_transaction': True, 'before': initial, 'after': facts(path)})

    path = copy_case('empty')
    record = compact(path, 'compact:empty', {'tasks_compacted': 0, 'logical_bytes_removed': 0})
    assert record['transactions'] == ['BEGIN IMMEDIATE', 'COMMIT'] and record['native_update_calls'] == []

    path = copy_case('mixed')
    rows = [(101, None, 'done', raw_input('null')), (205, 'a-done', 'done', raw_input('a')), (309, 'b-done', 'done', raw_input('b')), (411, 'already', 'done', '{"input_compacted":true,"sentinel":"unchanged"}'), (513, 'flag-one', 'done', '{"input_compacted":1,"sentinel":"unchanged"}'), (615, 'failed', 'failed', '{}'), (717, 'pending', 'pending', '{}'), (819, 'running', 'running', '{}')]
    seed(path, rows)
    before = facts(path)
    # Exact expected compact bytes are read from the actual results below; an
    # independent provenance check validates their hashes/fields, not helper output.
    record = compact(path, 'compact:mixed', None)
    if args.mutant == 'compactor':
        args.result.write_text(json.dumps({'records': records, 'fixture_seed_verified': True, 'entered_actual_owners': entered}))
        return
    after = record['after']
    removed = 0
    for left, right in zip(before, after, strict=True):
        assert {k: v for k, v in left.items() if k != 'input_json'} == {k: v for k, v in right.items() if k != 'input_json'}
        if left['task_id']['value'] in ('a-done', 'b-done'):
            check_compact(body(left), body(right))
            removed += max(0, len(body(left).encode()) - len(body(right).encode()))
        else:
            assert left == right
    assert record['outcome'] == {'return': {'tasks_compacted': 2, 'logical_bytes_removed': removed}}
    assert record['native_update_calls'] == [{'rowcount': 0}, {'rowcount': 1}, {'rowcount': 1}]
    assert record['transactions'] == ['BEGIN IMMEDIATE', 'COMMIT'] and record['native_close'] == [{'transaction_open_before_native_close': False}]
    record = compact(path, 'compact:idempotent', {'tasks_compacted': 0, 'logical_bytes_removed': 0})
    assert record['before'] == record['after'] and record['native_update_calls'] == [{'rowcount': 0}]

    for mode in ('ignore', 'abort'):
        path = copy_case(mode)
        seed(path, [(101, 'a-done', 'done', raw_input('a')), (205, 'b-done', 'done', raw_input('b'))])
        with connect(path) as db:
            table = 'tasks' if args.target else KIND
            key = task20_column(KIND, 'task_id') if args.target else 'task_id'
            guard = f"old.{key}='b-done'" + (f" and old.record_type='{KIND}'" if args.target else '')
            action = "raise(abort,'fixture second classifier abort')" if mode == 'abort' else 'raise(ignore)'
            db.execute(f'create trigger fixture_native_{mode} before update on {table} when {guard} begin select {action}; end')
        record = compact(path, 'compact:' + mode, None, 'fixture second classifier abort' if mode == 'abort' else None)
        if mode == 'abort':
            assert record['native_update_calls'] == [{'rowcount': 1}, {'rowcount': None, 'error': 'IntegrityError', 'message': 'fixture second classifier abort'}]
            assert record['transactions'] == ['BEGIN IMMEDIATE']
            assert record['native_close'] == [{'transaction_open_before_native_close': True}]
            assert record['before'] == record['after'], 'native close did not roll back first successful UPDATE'
        else:
            assert record['native_update_calls'] == [{'rowcount': 1}, {'rowcount': 0}]
            assert record['transactions'] == ['BEGIN IMMEDIATE', 'COMMIT']
            left, right = record['before'][0], record['after'][0]
            check_compact(body(left), body(right))
            removed = max(0, len(body(left).encode()) - len(body(right).encode()))
            assert record['outcome'] == {'return': {'tasks_compacted': 1, 'logical_bytes_removed': removed}}
            assert record['before'][1] == record['after'][1]
    sys.setprofile(None)
    assert not external
    args.result.write_text(json.dumps({'records': records, 'entered_actual_owners': entered, 'coordinates': coordinates, 'fixture_seed_verified': True, 'external_calls': external}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', choices=('bootstrap', 'prepare', 'scenario'))
    parser.add_argument('--code', type=Path)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--target', action='store_true')
    parser.add_argument('--mutant', choices=('parent', 'compactor'))
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    script, repo = Path(__file__).resolve(), args.repo.resolve()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    previous = json.loads((repo / 'docs/superpowers/plans/2026-10-16-task20-remaining-readers-caller-proposal-analysis.json').read_text())
    patches = [repo / 'docs/superpowers/plans' / ('2026-10-16-' + name + '-caller-proposal.patch') for name in PATCH_NAMES]
    pins = {item['path']: item['sha256'] for item in previous['prerequisite_patches_in_order']}
    pins[str(patches[-1].relative_to(repo))] = previous['patch_sha256']
    for patch in patches:
        assert sha(patch) == pins[str(patch.relative_to(repo))], patch
    assert sha(repo / 'app/task12_storage.py') == '127311aa2ed303066fc3dd325a5642c0e61725a2d8db3b5bedfc218e804e013f'
    files = ('app/store.py', 'app/storage_maintenance.py', 'app/email_task_adapter.py', 'app/email_store.py', 'app/task12_storage.py', 'app/task8_storage.py', 'app/task20_definitions.py')
    report = {'scope': 'two actual methods only; full fresh source and explicit EMPTY Tasks20 fixture; no migration/cold target constructor', 'script_sha256': sha(script), 'source_sha256': {name: sha(repo / name) for name in files}, 'patches_in_order': [{'path': str(p.relative_to(repo)), 'sha256': sha(p)} for p in patches]}
    with tempfile.TemporaryDirectory(prefix='task20-classification-maintenance-', dir='/tmp') as temporary:
        root = Path(temporary)
        source, target = root / 'source', root / 'target'
        for code in (source, target):
            shutil.copytree(repo / 'app', code / 'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        assert report['source_sha256'] == {name: sha(source / name) for name in files}
        for patch in patches:
            subprocess.run(['git', 'apply', '--check', str(patch)], cwd=target, check=True, capture_output=True, text=True)
            subprocess.run(['git', 'apply', str(patch)], cwd=target, check=True, capture_output=True, text=True)
        report['candidate_sha256'] = {name: sha(target / name) for name in files}

        def run(stage, code, db, selected=False, mutant=None):
            result = root / (stage + '-' + code.name + '.json')
            cmd = [str(PYTHON), '-B', str(script), '--worker', stage, '--code', str(code), '--db', str(db), '--result', str(result)]
            if selected:
                cmd.append('--target')
            if mutant:
                cmd.extend(('--mutant', mutant))
            done = subprocess.run(cmd, cwd=code, env=env, text=True, capture_output=True, timeout=180)
            assert done.returncode == 0, {'stage': stage, 'code': code.name, 'stderr': done.stderr[-6000:]}
            return json.loads(result.read_text())

        source_db, target_db = source / 'fixture.sqlite3', target / 'fixture.sqlite3'
        report['bootstrap'] = run('bootstrap', source, source_db)
        with sqlite3.connect(source_db) as src, sqlite3.connect(target_db) as dst:
            src.backup(dst)
        report['target_preparation'] = run('prepare', target, target_db, True)
        left, right = run('scenario', source, source_db), run('scenario', target, target_db, True)
        assert left['fixture_seed_verified'] and right['fixture_seed_verified']
        assert left['entered_actual_owners'] == right['entered_actual_owners']
        assert left['records'] == right['records'], 'two-method source/target differential failed'
        report.update({'source_checkpoints': len(left['records']), 'target_checkpoints': len(right['records']), 'equal_checkpoints': len(left['records']), 'entered_actual_owners': left['entered_actual_owners'], 'records': left['records'], 'physical_coordinates': {'source': left['coordinates'], 'target': right['coordinates']}, 'external_calls': {'source': left['external_calls'], 'target': right['external_calls']}})
        report['negative_controls'] = []
        for mutant, filename in (('parent', 'app/store.py'), ('compactor', 'app/storage_maintenance.py')):
            code = root / ('mutant-' + mutant)
            shutil.copytree(target / 'app', code / 'app')
            database = code / 'fixture.sqlite3'
            with sqlite3.connect(target_db) as src, sqlite3.connect(database) as dst:
                src.backup(dst)
            file = code / filename
            text = file.read_text()
            if mutant == 'parent':
                original = 'f"select 1 from ({task12_projection(\'email_agent_classification_tasks\')}) "\n                "where task_id=? and status=\'running\'"'
                changed = original.replace("status='running'", "status='done'")
            else:
                original = 'f"and {task20_column(\'email_agent_classification_tasks\', \'status\')}=\'done\'",'
                changed = original.replace("='done'", "='failed'")
            assert text.count(original) == 1, (mutant, original)
            file.write_text(text.replace(original, changed))
            observation = run('scenario', code, database, True, mutant)
            assert observation['fixture_seed_verified']
            if mutant == 'parent':
                checkpoint = observation['records'][0]
                assert checkpoint['label'] == "parent:'key-running'" and checkpoint['expected'] is True and checkpoint['return'] is False
            else:
                checkpoint = next(row for row in observation['records'] if row['label'] == 'compact:mixed')
                assert checkpoint['outcome']['return'] == {'tasks_compacted': 0, 'logical_bytes_removed': 0}
                assert checkpoint['before'] == checkpoint['after']
                assert checkpoint['native_update_calls'] == [{'rowcount': 0}] * 3
            report['negative_controls'].append({'mutation': mutant, 'fixture_seed_verified': True, 'actual_owner_entered': observation['entered_actual_owners'], 'distinguishing_checkpoint': checkpoint})
    report['owned_temp_removed'] = not root.exists()
    assert report['source_sha256'] == {name: sha(repo / name) for name in files}
    # Exact full-field/full-text comparison above precedes bounded report output.
    # Evidence retains typed logical fields and JSON hashes/bytes/provenance,
    # avoiding repetition of large synthetic input bodies.
    def bounded(value):
        if isinstance(value, list):
            return [bounded(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == 'input_json' and isinstance(item, dict) and isinstance(item.get('value'), str):
                text = item['value']
                payload = json.loads(text)
                summary = {'scalar_type': item['scalar_type'], 'utf8_bytes': len(text.encode('utf-8')), 'sha256': value_digest(text), 'input_compacted': payload.get('input_compacted')}
                if payload.get('input_compacted') is True and 'input_sha256' in payload:
                    summary['compacted_provenance'] = payload
                result[key] = summary
            else:
                result[key] = bounded(item)
        return result
    report = bounded(report)
    report['facts_report'] = 'full typed raw-field/text equality asserted before output; input_json evidence stores type/bytes/hash and compacted provenance'
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('source_checkpoints', 'target_checkpoints', 'equal_checkpoints', 'entered_actual_owners', 'owned_temp_removed')}))


if __name__ == '__main__':
    main()
