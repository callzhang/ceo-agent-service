"""Offline Phase5B proof using an online backup and current-fact schema inverse.

Only constructors and storage run; no worker, model or provider is invoked.
Disposable copies remain in the printed task temporary root for inspection.
"""
import argparse
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import pickle
import sqlite3
import subprocess
import tarfile
import tempfile

from app.action_receipt_schema_release import prepare_action_receipt_schema_release
from app.action_storage import (
    ACTION_PROJECTIONS, SOURCE_COLUMNS, ACTION_SEQUENCE_NAMES,
    insert_action, update_action, delete_action,
)
from app.action_receipt_storage import capture_action_receipt_schema
from app.store import STORE_SCHEMA_VERSION


TYPES = ('external_action_results', 'agent_effect_intents', 'agent_execution_receipts')


def fingerprint(rows):
    values = sorted(pickle.dumps(tuple(row)) for row in rows)
    return {'rows': len(values), 'sha256_values_types': hashlib.sha256(b''.join(values)).hexdigest()}


def other_tables(db):
    return {name: fingerprint(db.execute('select * from "' + name.replace('"', '""') + '"'))
            for (name,) in db.execute("select name from sqlite_schema where type='table' and name not like 'sqlite_%'")
            if name not in {*TYPES, 'actions', 'service_state'}}


def validate(source, source_ref, repo):
    root = Path(tempfile.mkdtemp(prefix='ceo-phase5b-proof-', dir='/private/tmp'))
    original, target = root / 'source.sqlite3', root / 'target.sqlite3'
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as db, closing(sqlite3.connect(original)) as copy:
        db.backup(copy)
    with closing(sqlite3.connect(original)) as db, closing(sqlite3.connect(target)) as copy:
        db.backup(copy)
    with closing(sqlite3.connect(original)) as db:
        marker = db.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone()
        count = db.execute("select count(*) from sqlite_schema where type='table'").fetchone()[0]
        unchanged = other_tables(db)
        source_receipts = {kind: fingerprint(db.execute('select ' + ','.join(SOURCE_COLUMNS[kind]) + ' from ' + kind)) for kind in TYPES}
        action_columns = [row[1] for row in db.execute('pragma table_info(actions)')]
        action_sql = ','.join('"' + col + '"' for col in action_columns)
        six_rows = fingerprint(db.execute('select ' + action_sql + ' from actions'))
    release = prepare_action_receipt_schema_release(repo, target, marker)
    release.publish()
    with closing(sqlite3.connect(target)) as db:
        assert other_tables(db) == unchanged
        assert db.execute("select count(*) from sqlite_schema where type='table'").fetchone()[0] == count - 3
        for kind in TYPES:
            assert fingerprint(db.execute(ACTION_PROJECTIONS[kind])) == source_receipts[kind], kind
        assert fingerprint(db.execute('select ' + action_sql + " from actions where record_type not in ('external_action_results','agent_effect_intents','agent_execution_receipts')")) == six_rows
        run = db.execute("select id from runs where record_type='agent_runs' order by id limit 1").fetchone()[0]
        insert_action(db, 'external_action_results', dict(
            external_action_key='phase5b-copy-owned-receipt', business_object_key='copy-owned',
            action_identity='copy-owned', operation='copy-storage', target_identifiers_json='{}',
            provider_result_json=' {"copy_only":true} ', result_digest='copy-owned', first_agent_run_id=run,
        ))
        keys = [row[0] for row in db.execute(f"select external_action_key from ({ACTION_PROJECTIONS['external_action_results']}) order by external_action_key limit 2")]
        assert len(keys) == 2
        update_action(db, 'external_action_results', keys[0], {'provider_result_json': ' {"copy_updated": [2,1]} '})
        delete_action(db, 'external_action_results', keys[1])
        for kind in ('agent_effect_intents', 'agent_execution_receipts'):
            row = db.execute(ACTION_PROJECTIONS[kind]).fetchone()
            assert row is not None
            values = dict(zip(SOURCE_COLUMNS[kind], row, strict=True))
            values.pop('id')
            if kind == 'agent_effect_intents':
                values.update(authorization_id='phase5b-copy-owned', receipt_operation_id='phase5b-copy-owned')
            else:
                values.update(receipt_id='phase5b-copy-owned', operation_id='phase5b-copy-owned')
            insert_action(db, kind, values)
        current_receipts = {kind: fingerprint(db.execute(ACTION_PROJECTIONS[kind])) for kind in TYPES}
        current_sequences = {kind: db.execute('select seq from sqlite_sequence where name=?', (name,)).fetchone()[0]
                             for kind, name in ACTION_SEQUENCE_NAMES.items()}
        db.commit()
    release.rollback()
    release.rollback()
    with closing(sqlite3.connect(target)) as db:
        assert other_tables(db) == unchanged
        assert fingerprint(db.execute('select ' + action_sql + ' from actions')) == six_rows
        assert db.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone() == marker
        assert db.execute("select count(*) from external_action_results where external_action_key='phase5b-copy-owned-receipt'").fetchone() == (1,)
        assert db.execute('pragma integrity_check').fetchone() == ('ok',)

    def verify_inverse():
        with closing(sqlite3.connect(target)) as db:
            assert other_tables(db) == unchanged
            assert fingerprint(db.execute('select ' + action_sql + ' from actions')) == six_rows
            assert db.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone() == marker
            state = capture_action_receipt_schema(db)
            assert state['actions'] == release.source_schema['actions']
            assert state['tables'] == release.source_schema['tables']
            assert state['sequences'] == current_sequences
            for kind in TYPES:
                assert fingerprint(db.execute('select ' + ','.join(SOURCE_COLUMNS[kind]) + ' from ' + kind)) == current_receipts[kind], kind

    verify_inverse()
    oldroot = root / 'old-code'
    oldroot.mkdir()
    archive = subprocess.check_output(['git', '-C', str(repo), 'archive', source_ref, 'app'])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(oldroot, filter='data')
    subprocess.run(['python', '-c', 'from pathlib import Path; from app.store import AutoReplyStore; import sys; AutoReplyStore(Path(sys.argv[1]))', str(target)], cwd=oldroot, check=True)
    verify_inverse()
    release.publish()
    with closing(sqlite3.connect(target)) as db:
        assert other_tables(db) == unchanged
        assert fingerprint(db.execute('select ' + action_sql + " from actions where record_type not in ('external_action_results','agent_effect_intents','agent_execution_receipts')")) == six_rows
        for kind in TYPES:
            assert fingerprint(db.execute(ACTION_PROJECTIONS[kind])) == current_receipts[kind], kind
        assert {kind: db.execute('select seq from sqlite_sequence where name=?', (name,)).fetchone()[0]
                for kind, name in ACTION_SEQUENCE_NAMES.items()} == current_sequences
    paths = ['app/action_storage.py', 'app/action_receipt_storage.py', 'app/action_receipt_schema_release.py',
             'app/store.py', 'scripts/validate_action_receipt_copy.py']
    proof = dict(source_version=marker[0], target_version=STORE_SCHEMA_VERSION,
                 actual_old_commit=source_ref, source_physical_tables=count, target_physical_tables=count-3,
                 unchanged_tables=unchanged, source_receipts=source_receipts, six_action_facts=six_rows,
                 current_fact_inverse=True, old_constructor=True, forward_inverse_forward=True,
                 current_receipts=current_receipts, current_sequences=current_sequences,
                 old_constructor_and_re_forward_values_types_sequences_equal=True,
                 new_update_delete_exercised=True,
                 source_fk_findings=len(release.source_fk), real_sends=0,
                 code_sha256={p: hashlib.sha256((repo/p).read_bytes()).hexdigest() for p in paths})
    (root/'proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n')
    return root, proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-ref', required=True)
    args = parser.parse_args()
    root, proof = validate(args.source.resolve(), args.source_ref, Path(__file__).resolve().parents[1])
    print(json.dumps({'temporary_root': str(root), 'source_physical_tables': proof['source_physical_tables'],
                      'target_physical_tables': proof['target_physical_tables'], 'unchanged_tables': len(proof['unchanged_tables']),
                      'source_version': proof['source_version'], 'target_version': proof['target_version']}))
