"""Read the frozen source through backup API; mutate only owned disposable copies.

This proves the pure Runs11 component after the already selected54-table release.
Current callers/constructors remain on54; this is not a coupled51-table release.
"""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import os
import pickle
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
PYTHON = '/Users/derek/Projects/ceo-agent-service/.worktrees/task-first-tasks/.venv/bin/python'
SOURCE = Path('/private/tmp/ceo-business-combined-actual-u6i3ldg0/source.sqlite3')
SOURCE_SHA = '970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320'
MODULES = {'runs11_definitions.py': 'definition-canonical.py',
           'runs11_storage.py': 'storage-canonical.py',
           'runs11_schema_release.py': 'release-canonical.py'}


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ordinary_count(db):
    return sum(row[0] == 'main' and row[2] == 'table' and not row[1].startswith('sqlite_')
               for row in db.execute('pragma main.table_list'))


def snapshot(db):
    tables = {row[1]: row[4] for row in db.execute('pragma main.table_list')
              if row[2] == 'table' and not row[1].startswith('sqlite_')}
    facts = {}
    for name, without_rowid in tables.items():
        fields = [row[1] for row in db.execute('pragma main.table_xinfo("'+name+'")') if row[6] == 0]
        quoted = ','.join('"'+field+'"' for field in fields)
        typed = ','.join('typeof("'+field+'")' for field in fields)
        identity = '' if without_rowid else 'rowid,'
        order = quoted if without_rowid else 'rowid'
        digest = hashlib.sha256()
        count = 0
        for row in db.execute('select '+identity+quoted+','+typed+' from main."'+name+'" order by '+order):
            digest.update(pickle.dumps(tuple(row), protocol=4))
            count += 1
        facts[name] = [count, digest.hexdigest()]
    return {
        'facts': facts,
        'declarations': [tuple(row) for row in db.execute(
            "select type,name,tbl_name,sql from main.sqlite_schema where name not glob 'sqlite_*' order by type,name")],
        'counters': dict(db.execute('select name,seq from main.sqlite_sequence')),
        'foreign_keys': [tuple(row) for row in db.execute('pragma foreign_key_check')],
    }


def inside(owned):
    from app.store_schema_release import prepare_database_schema_release
    from app.configs_run_schema import _objects, _declarations
    from app.run_storage import insert_run
    from app.runs11_definitions import RUN11_NEW_KINDS, RUN11_FIELDS, RUN11_BASE_COLUMNS, run11_projection
    from app.runs11_storage import insert_run11_fact
    from app.runs11_schema_release import capture_run11_source, migrate_run11_schema, restore_run11_schema, run11_schema_is_current
    from app.task20_schema_release import _sequence

    copy = owned / 'copy.sqlite3'
    start = time.monotonic()
    with closing(sqlite3.connect(SOURCE.as_uri()+'?mode=ro&immutable=1', uri=True)) as original:
        with closing(sqlite3.connect(copy)) as destination:
            original.backup(destination)
            assert ordinary_count(destination) == 116
    release = prepare_database_schema_release(owned, copy)
    assert release is not None
    release.publish()
    print(json.dumps({'phase': 'selected_baseline54_ready'}), flush=True)
    with closing(sqlite3.connect(copy)) as db:
        db.execute('pragma foreign_keys=off')
        db.execute('begin')
        before = snapshot(db)
        assert len(before['facts']) == 54
        metadata = capture_run11_source(db, mapped=True)
        original_counts = migrate_run11_schema(db, metadata)
        assert run11_schema_is_current(db)
        assert ordinary_count(db) == 51
        payloads = {
            'codex_session_search_index': dict(session_id='runs11-copy-only-session', source_type='copy-only', source_id='unavailable-native', title='runs11copyuniquetitle'),
            'email_classifier_runtime_samples': dict(model_id='runs11-copy-only-model', outcome='success', cache_hit=0, runtime_warm=1, queue_ms=1, http_ms=2, embedding_ms=3, head_ms=4, total_ms=10, recorded_at='copy-only-time'),
            'setup_wizard_events': dict(step_id='runs11-copy-only-step', action_id='copy-only-action', status='copy-only-custom', evidence_json='literal non-JSON evidence'),
        }
        public_ids = {}
        for name, payload in payloads.items():
            public_ids[name] = insert_run11_fact(db, name, payload).lastrowid
        old = dict(workload_kind='other', workload_key='runs11-copy-only-runtime', attempt_number=1, route_name='copy-only', runtime_kind='copy-only', credential_mode='copy-only', model='copy-only', status='completed')
        old_id = insert_run(db, 'agent_runtime_attempts', old).lastrowid
        expected = {}
        for name in ('runs', *RUN11_NEW_KINDS):
            fields = RUN11_BASE_COLUMNS if name == 'runs' else RUN11_FIELDS[name]
            columns = ','.join('"'+field+'"' for field in fields)
            typed = ','.join('typeof("'+field+'")' for field in fields)
            source = ('select * from runs where record_type not in ('+','.join(map(repr, RUN11_NEW_KINDS))+')') if name == 'runs' else run11_projection(name)
            digest = hashlib.sha256()
            for row in db.execute('select '+columns+','+typed+' from ('+source+') order by id'):
                digest.update(pickle.dumps(tuple(row), protocol=4))
            expected[name] = digest.hexdigest()
        print(json.dumps({'phase': 'pure51_with_current_writes', 'source_counts': original_counts, 'new_public_ids': public_ids, 'new_old8_physical_id': old_id}), flush=True)
        restore_run11_schema(db, metadata)
        after = snapshot(db)
        assert len(after['facts']) == 54
        assert after['declarations'] == before['declarations']
        assert after['foreign_keys'] == before['foreign_keys']
        changed = {name for name in before['facts'] if before['facts'][name] != after['facts'][name]}
        assert changed == {'runs', *RUN11_NEW_KINDS}, changed
        for name, expected_digest in expected.items():
            fields = RUN11_BASE_COLUMNS if name == 'runs' else RUN11_FIELDS[name]
            columns = ','.join('"'+field+'"' for field in fields)
            typed = ','.join('typeof("'+field+'")' for field in fields)
            digest = hashlib.sha256()
            for row in db.execute('select '+columns+','+typed+' from main."'+name+'" order by id'):
                digest.update(pickle.dumps(tuple(row), protocol=4))
            assert digest.hexdigest() == expected_digest, name
        for name, public_id in public_ids.items():
            assert _sequence(db, name) == public_id
        assert _sequence(db, 'runs') == old_id
        assert db.execute("select rowid from codex_session_search_fts where codex_session_search_fts match 'runs11copyuniquetitle'").fetchall() == [(public_ids['codex_session_search_index'],)]
        assert _objects(db) == _declarations(mapped=True)
        print(json.dumps({'phase': 'pure54_inverse_facts_counters_declarations_verified'}), flush=True)
        migrate_run11_schema(db, metadata)
        assert run11_schema_is_current(db)
        assert db.execute('pragma integrity_check').fetchall() == [('ok',)]
        assert [tuple(row) for row in db.execute('pragma foreign_key_check')] == before['foreign_keys']
        db.commit()
        print(json.dumps({'phase': 'pure51_reforward_integrity_verified'}), flush=True)
    receipt = {'status': 'passed', 'scope': 'Frozen source116 through selected baseline54, pure Runs11 51/current writes/54 inverse/51 reforward. Current51 callers, constructors, paired inverse, genuine predecessor writes and publication remain unproved.',
               'source_sha256': SOURCE_SHA, 'ordinary_table_sequence': [116, 54, 51, 54, 51],
               'original_source_counts': original_counts, 'unchanged_baseline54_tables': 50,
               'baseline_foreign_key_findings_preserved': before['foreign_keys'],
               'native_public_ids': public_ids, 'new_old8_physical_id': old_id,
               'candidate_hashes': {name: file_hash(owned/'app'/name) for name in MODULES},
               'checker_sha256': file_hash(Path(__file__)),
               'elapsed_seconds': round(time.monotonic()-start, 3)}
    (owned / 'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')


if len(sys.argv) == 3 and sys.argv[1] == '--inside':
    inside(Path(sys.argv[2]))
else:
    assert file_hash(SOURCE) == SOURCE_SHA
    with tempfile.TemporaryDirectory(prefix='runs11-actual-copy-', dir='/tmp') as directory:
        owned = Path(directory)
        for name in ('app', 'data/config', 'docs/superpowers/specs'):
            shutil.copytree(ROOT/name, owned/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for target, source in MODULES.items():
            shutil.copyfile(HERE/('2026-10-16-runs11-'+source), owned/'app'/target)
        checker = owned / 'docs/superpowers/plans/2026-10-16-runs11-copy-preflight.py'
        checker.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).resolve(), checker)
        env = os.environ | {'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(owned),
                            'CEO_WORKER_DB': str(owned/'copy.sqlite3'), 'CEO_ENV_FILE': str(owned/'absent.env'),
                            'CEO_SERVICE_ROOT': str(owned/'absent-service')}
        subprocess.run([PYTHON, '-B', str(checker), '--inside', str(owned)], cwd=owned, env=env, check=True)
        assert file_hash(SOURCE) == SOURCE_SHA
        shutil.copyfile(owned/'receipt.json', HERE/'2026-10-16-runs11-copy-evidence.json')
