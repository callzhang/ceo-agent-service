#!/usr/bin/env python3
"""Actual Workbench child methods/files, synthetic fresh paired fixture only.

The target is an explicitly prepared EMPTY Tasks20 fixture. Its initialization
check alone is bypassed after preparation; original methods/FS helpers execute.
No real database, provider, native CLI, migration or cold-target claim.
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
import stat
import subprocess
import sys
import tempfile
from uuid import UUID

PYTHON = Path('/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python')
CLOCK = '2040-01-01 00:00:00'
NAME = '2026-10-16-task12-workbench-children-public'
PINNED = {
    'app/workbench/store.py': '4a3bb9287dfea1f2e13e31be5ff2a9ac536a458ac079f30ccb1519848870e18e',
    'app/task12_storage.py': '8a7a84db53006065697ae44a2e39dc27a2008ca2687b6ddfc54e23ff9cc1729a',
    'app/task20_definitions.py': 'cf58403bcc9dc7bf4c3ef5b9a777ff941c7a6de210e04b29e4cb7abb1e0df4d0',
}
PATCHES = {
    '2026-10-16-task8-store-caller-proposal.patch': '691cc165fdbaaa4e56a04b907dc6c76cbbc717c4e0c3fc9f3c31a95857cc873c',
    '2026-10-16-task8-workbench-caller-proposal.patch': '66d1c19cfee4fd636d596400b228b0926f9fca46aee177d180a6701b33386c26',
    '2026-10-16-task12-workbench-children-caller-proposal.patch': 'f9e64c543a9aec532184a99ee9832d691635bdbaf4dfade9c515e25e3f268cc7',
}
FS_HELPERS = {
    '_attachment_lock', '_managed_directory', '_attachment_task_directory',
    '_write_attachment_temp', '_after_attachment_temp_created', '_fsync_directory',
    '_fsync_attachment_file', '_safe_attachment_unlink', '_commit_attachment_metadata',
}
CHILD_METHODS = {
    'save_attachment', 'list_attachments', 'append_artifact_event', 'list_artifacts',
    'get_artifact', 'timeline_snapshot', 'workbench_stats',
    'attachment_paths_for_executor', '_reconcile_attachments',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def worker(args):
    sys.path.insert(0, str(args.code))
    import app.workbench.store as wb
    from app.store import AutoReplyStore

    if args.worker == 'bootstrap':
        store = wb.WorkbenchStore(args.db)
        with store._connect() as db:
            assert db.execute('select count(*) from tasks').fetchone()[0] == 0
            assert all(db.execute('select count(*) from '+k).fetchone()[0] == 0 for k in ('workbench_artifacts', 'workbench_attachments'))
            answer = {'empty': True, 'table_entries': db.execute("select count(*) from sqlite_schema where type='table'").fetchone()[0], 'fk': [list(r) for r in db.execute('pragma foreign_key_check')]}
        args.result.write_text(json.dumps(answer))
        return
    if args.worker == 'prepare':
        from app.task20_definitions import create_task20_schema, initialize_task8_sequence
        with sqlite3.connect(args.db) as db:
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            for name in ('workbench_artifacts', 'workbench_attachments', 'tasks'):
                assert db.execute('select count(*) from '+name).fetchone()[0] == 0
                db.execute('drop table '+name)
            create_task20_schema(db)
            initialize_task8_sequence(db, 0)
        args.result.write_text(json.dumps({'empty_tables_replaced': ['tasks', 'workbench_artifacts', 'workbench_attachments'], 'published_water': 0}))
        return

    from app.task12_storage import task12_projection, task12_set_parts
    from app.task20_definitions import task20_column
    original_open = AutoReplyStore._open_connection
    trace = []
    insert_files = []

    def norm(value):
        if hasattr(value, 'model_dump'):
            return norm(value.model_dump(mode='json'))
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str):
            return value.replace(str(args.db.parent), '<FIXTURE>')
        if isinstance(value, bytes):
            return {'hex': value.hex()}
        if isinstance(value, dict):
            return {str(k): norm(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [norm(v) for v in value]
        return value

    def files():
        directory = args.db.parent / 'workbench'
        found = []
        if not directory.exists():
            return found
        for parent, dirs, names in os.walk(directory, followlinks=False):
            for name in sorted(dirs+names):
                p = Path(parent)/name
                mode = os.lstat(p).st_mode
                entry = {'path': str(p.relative_to(directory)), 'mode': stat.S_IMODE(mode)}
                if stat.S_ISLNK(mode):
                    entry.update(kind='symlink', target=norm(os.readlink(p)))
                elif stat.S_ISDIR(mode):
                    entry.update(kind='directory')
                elif stat.S_ISREG(mode):
                    content = p.read_bytes()
                    entry.update(kind='file', size=len(content), sha256=hashlib.sha256(content).hexdigest(), hex=content.hex())
                else:
                    entry.update(kind='other')
                found.append(entry)
        return sorted(found, key=lambda x: x['path'])

    def traced(sql):
        trace.append(sql)
        text = sql.lstrip().lower()
        if text.startswith('insert into workbench_attachments') or (text.startswith('insert into tasks') and "'workbench_attachments'" in text):
            current = files()
            if not insert_files or insert_files[-1] != current:
                insert_files.append(current)

    def fixture_open(self):
        db = original_open(self)
        db.create_function('current_timestamp', 0, lambda: CLOCK)
        if args.target:
            from app.task8_storage import register_task8_functions
            register_task8_functions(db)
        db.set_trace_callback(traced)
        return db

    AutoReplyStore._open_connection = fixture_open
    if args.target:
        AutoReplyStore._ensure_initialized = lambda self: None
    counter = iter(range(1, 10000))
    wb.uuid4 = lambda: UUID(int=next(counter))
    store = wb.WorkbenchStore(args.db)
    records = []
    calls = set()
    covered = Counter()
    lines = {name: set() for name in CHILD_METHODS | FS_HELPERS}

    def profile(frame, event, arg):
        if frame.f_code.co_filename == wb.__file__ and frame.f_code.co_name in lines:
            name = frame.f_code.co_name
            if event == 'call':
                covered[name] += 1
            if event == 'line':
                lines[name].add(frame.f_lineno)
        return profile

    def facts():
        with store._connect() as db:
            data = {}
            for kind in ('workbench_artifacts', 'workbench_attachments'):
                query = task12_projection(kind, include_rowid=True) if args.target else 'select rowid,* from '+kind
                rows = db.execute('select * from ('+query+') order by rowid').fetchall()
                columns = list(rows[0].keys()) if rows else []
                types = db.execute('select '+','.join('typeof("'+c+'")' for c in columns)+' from ('+query+') order by rowid').fetchall() if columns else []
                data[kind] = [[[k, norm(r[k]), typ[i]] for i, k in enumerate(columns)] for r, typ in zip(rows, types, strict=True)]
            for name, query in (('tasks', wb.WORKBENCH_TASK_SQL), ('turns', wb.WORKBENCH_TURN_SQL)):
                data[name] = [norm(dict(r)) for r in db.execute('select * from ('+query+') order by id')]
            data['old8_physical'] = [list(r) for r in db.execute("select id,record_type,legacy_key,parent_id from tasks where record_type in ('workbench_tasks','workbench_turns') order by id")]
            data['runs'] = [norm(dict(r)) for r in db.execute('select * from runs order by id')]
            data['fk'] = [list(r) for r in db.execute('pragma foreign_key_check')]
            return data

    def call(checkpoint, method, *pos, error=None, **kw):
        assert checkpoint not in {r['label'] for r in records}
        trace.clear()
        insert_files.clear()
        calls.add(method)
        sys.settrace(profile)
        try:
            result = getattr(store, method)(*pos, **kw)
            outcome = {'return': norm(result)}
        except Exception as exc:
            result = None
            outcome = {'error': {'type': type(exc).__name__, 'message': norm(str(exc)), 'code': getattr(exc, 'code', None)}}
        finally:
            sys.settrace(None)
        assert ('error' in outcome) == (error is not None), (checkpoint, outcome)
        if error is not None:
            assert outcome['error']['type'] == error, (checkpoint, outcome)
        tx = [s.strip().upper() for s in trace if s.strip().upper() in {'BEGIN', 'BEGIN IMMEDIATE', 'COMMIT', 'ROLLBACK'}]
        record = {'label': checkpoint, 'method': method, 'outcome': outcome, 'transactions': tx, 'insert_attempt_files': list(insert_files), 'files': files(), 'facts': facts()}
        records.append(record)
        return result

    def change_attachment(key, values):
        with store._connect() as db:
            if args.target:
                part = task12_set_parts('workbench_attachments', values)
                db.execute('update tasks set '+part.sql+" where record_type='workbench_attachments' and "+task20_column('workbench_attachments', 'id')+'=?', (*part.parameters, key))
            else:
                db.execute('update workbench_attachments set '+','.join('"'+k+'"=?' for k in values)+' where id=?', (*values.values(), key))

    def failure_trigger(kind, field, value, message):
        with store._connect() as db:
            table = 'tasks' if args.target else kind
            guard = "new.record_type='"+kind+"' and " if args.target else ''
            column = task20_column(kind, field) if args.target else field
            db.execute('create trigger synthetic_child_insert_failure before insert on '+table+' when '+guard+'new.'+column+'='+repr(value)+" begin select raise(abort,'"+message+"'); end")

    def clear_trigger():
        with store._connect() as db:
            db.execute('drop trigger synthetic_child_insert_failure')

    def attachment(label, task, request, content=b'body', filename=None):
        return call(label, 'save_attachment', task.id, client_request_id=str(UUID(int=request)), filename=filename or label+'.bin', media_type='application/octet-stream', content=content)

    call('reconcile_no_root', '_reconcile_attachments')
    task = call('task_one', 'create_task', title='二进制附件', runtime_kind='codex')
    other = call('task_two', 'create_task', title='other', runtime_kind='codex')
    turn = call('turn_one', 'create_turn', task.id, user_text='file proof', client_request_id='turn-one')
    call('turn_two', 'create_turn', other.id, user_text='other', client_request_id='turn-two')
    assert call('claim_one', 'claim_next_turn', owner='executor', execution_run_id='run-one', now=CLOCK).id == turn.id
    call('text_live', 'append_event', turn.id, sequence=2, event_type='text_delta', payload={'text': 'local synthetic cache'}, owner='executor', now=CLOCK)
    call('read_live_events', 'events_after', turn.id)
    binary = b'\x00\xff\x01binary\n'+ '星尘'.encode()
    stable = attachment('binary_attachment', task, 1000, binary, ' 报告.bin ')
    stable_path = args.db.parent/'workbench/attachments'/task.id/stable.id
    assert stable_path.read_bytes() == binary
    assert stable.size_bytes == len(binary)
    assert 'content_sha256' not in stable.model_dump() and 'storage_path' not in stable.model_dump()
    with store._connect() as db:
        assert db.execute('select content_sha256 from ('+wb.WORKBENCH_ATTACHMENT_SQL+') where id=?', (stable.id,)).fetchone()[0] == hashlib.sha256(binary).hexdigest()
    zero = attachment('zero_attachment', task, 1001, b'')
    assert call('replay', 'save_attachment', task.id, client_request_id=str(UUID(int=1000)), filename='报告.bin', media_type='application/octet-stream', content=binary).id == stable.id
    for label, values in (
        ('conflict_filename', {'filename': 'different'}),
        ('conflict_media', {'media_type': 'text/plain'}),
        ('conflict_size', {'content': binary+b'x'}),
        ('conflict_hash', {'content': bytes([1])+binary[1:]}),
    ):
        kw = dict(client_request_id=str(UUID(int=1000)), filename='报告.bin', media_type='application/octet-stream', content=binary)
        kw.update(values)
        call(label, 'save_attachment', task.id, error='WorkbenchConflictError', **kw)
    for label, parent, values in (
        ('invalid_content', task.id, {'content': 'not bytes'}),
        ('blank_filename', task.id, {'filename': ' '}),
        ('blank_media', task.id, {'media_type': ' '}),
        ('invalid_task_uuid', 'not-a-uuid', {}),
        ('invalid_request_uuid', task.id, {'client_request_id': 'bad'}),
        ('missing_task', str(UUID(int=9998)), {}),
    ):
        kw = dict(client_request_id=str(UUID(int=1500)), filename='reject.bin', media_type='text/plain', content=b'x')
        kw.update(values)
        call(label, 'save_attachment', parent, error='ValueError', **kw)
    call('list_attachments', 'list_attachments', task.id)
    call('missing_list', 'list_attachments', str(UUID(int=9998)), error='ValueError')
    call('executor_paths', 'attachment_paths_for_executor', turn.id, owner='executor', now=CLOCK)
    call('wrong_executor_paths', 'attachment_paths_for_executor', turn.id, owner='wrong', now=CLOCK, error='ValueError')
    call('expired_executor_paths', 'attachment_paths_for_executor', turn.id, owner='executor', now='2040-01-01 00:05:00', error='ValueError')

    before = facts()
    before_files = files()
    failure_trigger('workbench_attachments', 'filename', 'insert-failure.bin', 'synthetic_attachment_insert_failure')
    call('attachment_insert_failure', 'save_attachment', task.id, client_request_id=str(UUID(int=1600)), filename='insert-failure.bin', media_type='text/plain', content=b'failed temp bytes', error='IntegrityError')
    assert facts() == before and files() == before_files
    assert records[-1]['transactions'] == ['BEGIN IMMEDIATE', 'ROLLBACK']
    assert any(p['path'].endswith('.tmp') and p.get('hex') == b'failed temp bytes'.hex() for snap in records[-1]['insert_attempt_files'] for p in snap)
    clear_trigger()
    call('attachment_failure_list', 'list_attachments', task.id)

    artifact = call('artifact_one', 'append_artifact_event', turn.id, sequence=3, label=' 报告 ', path=str(args.db.parent/'artifacts/report.bin'), media_type=' application/octet-stream ', owner='executor', now=CLOCK)[0]
    call('artifact_get', 'get_artifact', artifact.id)
    call('artifact_get_missing', 'get_artifact', str(UUID(int=9998)))
    call('artifact_list', 'list_artifacts', task.id)
    call('artifact_wrong_owner', 'append_artifact_event', turn.id, sequence=4, label='wrong', path='wrong.bin', media_type='text/plain', owner='wrong', now=CLOCK, error='ValueError')
    call('artifact_wrong_sequence', 'append_artifact_event', turn.id, sequence=44, label='wrong', path='wrong.bin', media_type='text/plain', owner='executor', now=CLOCK, error='ValueError')
    call('artifact_bad_metadata', 'append_artifact_event', turn.id, sequence=4, label=' ', path='x', media_type='text/plain', owner='executor', now=CLOCK, error='ValueError')
    before = facts()
    failure_trigger('workbench_artifacts', 'label', 'insert-failure', 'synthetic_artifact_insert_failure')
    call('artifact_insert_failure', 'append_artifact_event', turn.id, sequence=4, label='insert-failure', path='x', media_type='text/plain', owner='executor', now=CLOCK, error='IntegrityError')
    assert facts() == before
    clear_trigger()
    with store._connect() as db:
        db.execute("create trigger synthetic_child_insert_failure before insert on runs when new.record_type='workbench_events' and new.event_type='artifact_created' begin select raise(abort,'synthetic_event_insert_failure'); end")
    call('artifact_event_failure', 'append_artifact_event', turn.id, sequence=4, label='event-failure', path='x', media_type='text/plain', owner='executor', now=CLOCK, error='IntegrityError')
    assert facts() == before
    clear_trigger()
    call('artifact_two', 'append_artifact_event', turn.id, sequence=4, label='second', path='relative/report.txt', media_type='text/plain', owner='executor', now=CLOCK)
    call('child_timeline', 'timeline_snapshot', task.id)
    call('child_cursor_boundary', 'timeline_snapshot', task.id, artifact_after=(CLOCK, artifact.id), attachment_after=(CLOCK, stable.id))
    call('child_stats', 'workbench_stats')

    directory = stable_path.parent
    orphan = directory/str(UUID(int=8000))
    orphan.write_bytes(b'orphan')
    temporary = directory/'.generated.tmp'
    temporary.write_bytes(b'temporary')
    keep = directory/'keep.txt'
    keep.write_bytes(b'keep nongenerated')
    noncanonical = args.db.parent/'workbench/attachments/non-uuid-directory'
    noncanonical.mkdir()
    (noncanonical/'keep').write_bytes(b'untouched')
    call('reconcile_orphans', '_reconcile_attachments')
    assert not orphan.exists() and not temporary.exists() and keep.read_bytes() == b'keep nongenerated'
    assert stable_path.read_bytes() == binary
    call('reconcile_repeat', '_reconcile_attachments')
    outside = args.db.parent/'outside-owned'
    outside.mkdir()
    symlink = args.db.parent/'workbench/attachments/symlink-directory'
    symlink.symlink_to(outside, target_is_directory=True)
    call('reconcile_symlink', '_reconcile_attachments', error='ValueError')
    symlink.unlink()
    outside.rmdir()

    missing = attachment('missing_file', task, 2000)
    wrongpath = attachment('wrong_storage_path', task, 2001)
    baduuid = attachment('invalid_metadata_uuid', task, 2002)
    nonregular = attachment('nonregular_file', task, 2003)
    absentdir = attachment('missing_directory', other, 2004)
    (directory/missing.id).unlink()
    change_attachment(wrongpath.id, {'storage_path': str(directory/'wrong.bin')})
    change_attachment(baduuid.id, {'id': 'invalid-uuid'})
    (directory/nonregular.id).unlink()
    (directory/nonregular.id).mkdir()
    absentpath = args.db.parent/'workbench/attachments'/other.id/absentdir.id
    absentpath.unlink()
    absentpath.parent.rmdir()
    # The existing source reconciliation checks paths/file kind, not body hash.
    # Exercise that unchanged policy explicitly without modifying any helper.
    (directory/zero.id).write_bytes(b'body changed after original zero-byte save')
    call('reconcile_invalid_metadata', '_reconcile_attachments')
    if not args.mutant:
        with store._connect() as db:
            remaining = db.execute('select id from ('+wb.WORKBENCH_ATTACHMENT_SQL+') order by id').fetchall()
        assert {r[0] for r in remaining} == {stable.id, zero.id}
        assert stable_path.read_bytes() == binary
        assert (directory/zero.id).read_bytes() == b'body changed after original zero-byte save'
        assert not (directory/wrongpath.id).exists() and not (directory/baduuid.id).exists()
        assert (directory/nonregular.id).is_dir()
    with store._connect() as db:
        if args.target:
            physical = [list(r) for r in db.execute("select id,record_type,cast(legacy_key as integer),parent_id from tasks where record_type in ('workbench_artifacts','workbench_attachments') order by id")]
        else:
            physical = {k: [list(r) for r in db.execute('select rowid,id,'+parent+' from '+k+' order by rowid')] for k, parent in (('workbench_artifacts', 'turn_id'), ('workbench_attachments', 'task_id'))}
    args.result.write_text(json.dumps({'records': records, 'methods': sorted(calls), 'entered': dict(covered), 'executed_source_lines': {k: sorted(v) for k, v in lines.items()}, 'physical_witness': physical}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', choices=('bootstrap', 'prepare', 'scenario'))
    parser.add_argument('--code', type=Path)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--target', action='store_true')
    parser.add_argument('--mutant', action='store_true')
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    assert Path(sys.executable).resolve() == PYTHON.resolve(), 'use pinned Python'
    plan = args.repo/'docs/superpowers/plans'
    frozen_helper = subprocess.run(['git', 'show', '2302361dd:app/task12_storage.py'], cwd=args.repo, capture_output=True, check=True).stdout
    assert hashlib.sha256(frozen_helper).hexdigest() == PINNED['app/task12_storage.py']
    for name, expected in PINNED.items():
        if name != 'app/task12_storage.py':
            assert digest(args.repo/name) == expected, name
    for name, expected in PATCHES.items():
        assert digest(plan/name) == expected, name
    sources = {f: digest(args.repo/f) for f in (*PINNED, 'app/store.py', 'app/task8_storage.py', 'app/workbench/native_events.py', 'app/workbench/models.py')}
    application_snapshot = dict(sources)
    sources['app/task12_storage.py'] = PINNED['app/task12_storage.py']
    receipt = {'frozen_helper': {'git_ref': '2302361dd', 'sha256': PINNED['app/task12_storage.py'], 'reason': 'exclude later unrelated INSERT-expression extension; copied dependencies only'}, 'application_snapshot': application_snapshot, 'scope': 'actual source methods and attachment filesystem, full synthetic fresh native schema versus explicitly prepared EMPTY Tasks20 target; no migration/cold-target/actual-profile claim', 'source_files': sources, 'patches': PATCHES, 'python': str(PYTHON), 'clock': CLOCK, 'uuid_stream': 'Workbench UUID integers1..9999', 'normalization': 'only task-owned fixture absolute path prefix becomes <FIXTURE>; clocks/UUIDs controlled identically', 'constructor_boundary': 'full unmodified fresh source constructor; target _ensure_initialized bypass only after explicit empty table replacement, original constructor/reconciliation otherwise retained'}
    with tempfile.TemporaryDirectory(prefix='task12-workbench-children-public-', dir='/tmp') as temp:
        root = Path(temp)
        source_code, target_code = root/'source', root/'target'
        for code in (source_code, target_code):
            shutil.copytree(args.repo/'app', code/'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            (code/'app/task12_storage.py').write_bytes(frozen_helper)
        assert {f: digest(source_code/f) for f in sources} == sources
        source_db, target_db = source_code/'fixture.sqlite3', target_code/'fixture.sqlite3'

        def run(stage, code, db, target=False, mutant=False):
            result = root/(stage+'-'+code.name+'.json')
            command = [str(PYTHON), '-B', str(Path(__file__).resolve()), '--worker', stage, '--code', str(code), '--db', str(db), '--result', str(result)]
            if target:
                command.append('--target')
            if mutant:
                command.append('--mutant')
            done = subprocess.run(command, cwd=code, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True)
            if done.returncode:
                raise RuntimeError(stage+' '+code.name+' failed: '+done.stdout+done.stderr)
            return json.loads(result.read_text())

        receipt['bootstrap'] = run('bootstrap', source_code, source_db)
        for patch in PATCHES:
            subprocess.run(['git', 'apply', '--check', str(plan/patch)], cwd=target_code, check=True, capture_output=True)
            subprocess.run(['git', 'apply', str(plan/patch)], cwd=target_code, check=True, capture_output=True)
        assert digest(target_code/'app/workbench/store.py') == 'b9027194eb4f9f277630935bf2d00a36792047f626e6b0242667d22a7de070db'
        companion = target_code/'app/workbench/native_events.py'
        text = companion.read_text()
        old = 'from app.task_storage import WORKBENCH_TURN_SQL, task_id_expression'
        assert text.count(old) == 1
        companion.write_text(text.replace(old, "from app.task_storage import task_id_expression\nfrom app.task20_definitions import task20_projection\nWORKBENCH_TURN_SQL = task20_projection('workbench_turns')"))
        receipt['native_events_companion_sha256'] = digest(companion)
        mutant_code = root/'mutant'
        shutil.copytree(target_code/'app', mutant_code/'app')
        f = mutant_code/'app/workbench/store.py'
        text = f.read_text()
        old = "{task20_column('workbench_attachments', 'id')}=?"
        assert text.count(old) == 3
        f.write_text(text.replace(old, 'id=?'))
        for code, db in ((target_code, target_db), (mutant_code, mutant_code/'fixture.sqlite3')):
            with sqlite3.connect(source_db) as src, sqlite3.connect(db) as dst:
                src.backup(dst)
            prepared = run('prepare', code, db, True)
            if code == target_code:
                receipt['preparation'] = prepared
        source = run('scenario', source_code, source_db)
        target = run('scenario', target_code, target_db, True)
        mutant = run('scenario', mutant_code, mutant_code/'fixture.sqlite3', True, True)
        assert source['methods'] == target['methods']
        assert set(CHILD_METHODS) <= set(source['entered']) & set(target['entered'])
        assert FS_HELPERS <= set(source['entered']) & set(target['entered'])
        assert len(source['records']) == len(target['records']) == len(mutant['records']) == 50
        assert source['entered'] == target['entered']
        assert any(row[0] != row[2] and row[2] != row[3] and row[0] != row[3] for row in target['physical_witness'])
        differences = []
        for left, right in zip(source['records'], target['records'], strict=True):
            if left != right:
                differences.append({'label': left['label'], 'fields': [k for k in left if left[k] != right[k]], 'source_outcome': left['outcome'], 'target_outcome': right['outcome']})
        mutant_differences = [left['label'] for left, right in zip(source['records'], mutant['records'], strict=True) if left != right]
        assert mutant_differences == ['reconcile_invalid_metadata'], mutant_differences
        chosen = {'binary_attachment', 'zero_attachment', 'replay', 'attachment_insert_failure', 'artifact_one', 'artifact_insert_failure', 'artifact_event_failure', 'reconcile_orphans', 'reconcile_symlink', 'reconcile_invalid_metadata'}
        receipt.update(checkpoints=len(source['records']), equal_checkpoints=len(source['records'])-len(differences), differences=differences, public_and_reconcile_methods=source['methods'], entered_methods={'source': source['entered'], 'target': target['entered']}, executed_lines={'source': source['executed_source_lines'], 'target': target['executed_source_lines']}, comparison_digests={k: hashlib.sha256(json.dumps(v['records'], sort_keys=True, ensure_ascii=False).encode()).hexdigest() for k, v in (('source', source), ('target', target))}, errors={r['label']: r['outcome']['error'] for r in source['records'] if 'error' in r['outcome']}, observations={r['label']: {k: r[k] for k in ('outcome', 'transactions', 'insert_attempt_files', 'files')} for r in source['records'] if r['label'] in chosen}, physical_witness={'source': source['physical_witness'], 'target': target['physical_witness']}, final_fk={'source': source['records'][-1]['facts']['fk'], 'target': target['records'][-1]['facts']['fk']}, negative_control={'mutation': 'replace only the three typed attachment UUID DELETE predicates with structural Tasks id; source UUID bound values retained', 'different_checkpoints': mutant_differences, 'source_final_child_facts': source['records'][-1]['facts']['workbench_attachments'], 'mutant_final_child_facts': mutant['records'][-1]['facts']['workbench_attachments']})
        assert not differences, differences
    receipt['preflight_sha256'] = digest(Path(__file__))
    receipt['coverage_contract'] = {'fixed_checkpoints': 50, 'all_nine_scoped_runtime_owners_entered': True, 'all_nine_original_filesystem_helpers_entered': True, 'source_target_trace_call_event_counts_equal': True, 'public_uuid_hidden_rowid_physical_child_parent_distinct_witness': True}
    receipt['branch_limits'] = ['synthetic fresh only; no frozen actual-profile or nonempty copy/migration/current-fact inverse', 'target initializer bypass after explicit empty target preparation; no cold-target/schema manifest acceptance', 'no concurrent lock/race/process crash/power loss or fsync/rename/commit failure injection', 'no native CLI/provider/network/UI or physical storage durability acceptance', 'only one real symlink-directory rejection case; no complete directory/path permission/O_NOFOLLOW platform error matrix', 'parents are created before child interleaving; no subsequent old-eight allocation check in this bounded proof', 'native events companion installed and real live cache exercised; native source records absent/no native read claimed']
    receipt['owned_temp_removed'] = not root.exists()
    assert receipt['owned_temp_removed']
    receipt['application_files_unchanged'] = {f: digest(args.repo/f) for f in application_snapshot} == application_snapshot
    assert receipt['application_files_unchanged']
    if args.output:
        args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: receipt[k] for k in ('checkpoints', 'equal_checkpoints', 'differences', 'owned_temp_removed', 'application_files_unchanged')}))


if __name__ == '__main__':
    main()
