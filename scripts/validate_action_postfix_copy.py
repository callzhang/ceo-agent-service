"""Offline standalone Phase5C forward/current-fact inverse proof.

No marker publication, worker, model or provider is invoked. A candidate-source
proof is preliminary until the same source commit has actually been deployed.
The printed owned temporary root is retained for inspection and exact cleanup.
"""
import argparse
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile

from app import action_storage as storage
from app.action_postfix_storage import (
    capture_action_postfix_schema, migrate_action_postfix_schema,
    restore_action_postfix_schema, _nine_rows, _sequences,
)
from scripts.validate_action_receipt_copy import fingerprint


TYPES = storage.POSTFIX_ACTION_TYPES


def _others(db):
    return {
        name: fingerprint(db.execute('select * from "' + name.replace('"', '""') + '"'))
        for (name,) in db.execute("select name from sqlite_schema where type='table' and name not like 'sqlite_%'")
        if name not in {'actions', *TYPES}
    }


def _facts(db, target):
    return {
        kind: fingerprint(db.execute(storage.ACTION_PROJECTIONS[kind] if target else
                                     'select ' + ','.join(storage.SOURCE_COLUMNS[kind]) + ' from ' + kind))
        for kind in TYPES
    }


def _transition(db, operation, *args):
    db.execute('pragma foreign_keys=off')
    db.execute('begin immediate')
    try:
        result = operation(db, *args)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute('pragma foreign_keys=on')
    return result


def validate(source, source_ref, repo):
    root = Path(tempfile.mkdtemp(prefix='ceo-phase5c-proof-', dir='/private/tmp'))
    original, target = root / 'source.sqlite3', root / 'target.sqlite3'
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as db, closing(sqlite3.connect(original)) as copy:
        db.backup(copy)
    with closing(sqlite3.connect(original)) as db, closing(sqlite3.connect(target)) as copy:
        db.backup(copy)
    with closing(sqlite3.connect(target)) as db:
        db.execute('pragma foreign_keys=on')
        state = capture_action_postfix_schema(db)
        source_fk = [tuple(row) for row in db.execute('pragma foreign_key_check')]
        count = db.execute("select count(*) from sqlite_schema where type='table'").fetchone()[0]
        marker = db.execute("select value,updated_at from service_state where key='store_schema_version'").fetchone()
        others, nine = _others(db), fingerprint(_nine_rows(db)[1])
        source_facts = _facts(db, False)
        sequences = _sequences(db)
        _transition(db, migrate_action_postfix_schema)
        assert _others(db) == others
        assert fingerprint(_nine_rows(db)[1]) == nine
        assert _facts(db, True) == source_facts
        assert _sequences(db) == sequences
        assert db.execute("select count(*) from sqlite_schema where type='table'").fetchone()[0] == count - 3

        keys = [('wechat', 'phase5c-copy-owned:甲'), ('dingtalk', 'phase5c-copy-owned:乙')]
        for channel, key in keys:
            storage.insert_action(db, 'outbound_postfixes', dict(
                channel=channel, delivery_key=key, final_body='copy-only\x00原文',
                postfix_version='copy-proof',
            ))
        storage.insert_action(db, 'native_reply_dispatches', dict(zip(('channel', 'delivery_key'), keys[0], strict=True)))
        storage.insert_action(db, 'outbound_postfix_receipts', dict(
            channel=keys[0][0], delivery_key=keys[0][1], provider_result_json=' raw [not JSON] ',
        ))
        storage.update_action(db, 'outbound_postfixes', keys[0], {'final_body': 'copy-only updated'})
        for kind in ('native_reply_dispatches', 'outbound_postfix_receipts'):
            storage.update_action(db, kind, keys[0], dict(zip(('channel', 'delivery_key'), keys[1], strict=True)))
        storage.delete_action(db, 'outbound_postfixes', keys[0])
        renamed = (keys[1][0], keys[1][1] + ':unreferenced')
        storage.insert_action(db, 'outbound_postfixes', dict(
            channel=renamed[0], delivery_key=renamed[1], final_body='unreferenced copy', postfix_version='copy-proof',
        ))
        storage.update_action(db, 'outbound_postfixes', renamed, {'delivery_key': renamed[1] + ':renamed'})
        current_facts = _facts(db, True)
        db.commit()
        _transition(db, restore_action_postfix_schema, state)

    def verify_inverse():
        with closing(sqlite3.connect(target)) as db:
            assert _others(db) == others
            assert fingerprint(_nine_rows(db)[1]) == nine
            assert _facts(db, False) == current_facts
            current = capture_action_postfix_schema(db)
            assert current == state
            assert _sequences(db) == sequences
            assert [tuple(row) for row in db.execute('pragma foreign_key_check')] == source_fk
            assert db.execute('pragma integrity_check').fetchall() == [('ok',)]

    verify_inverse()
    oldroot = root / 'old-code'
    oldroot.mkdir()
    archive = subprocess.check_output(['git', '-C', str(repo), 'archive', source_ref, 'app'])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(oldroot, filter='data')
    subprocess.run(['python', '-c',
                    'from pathlib import Path; from app.store import AutoReplyStore; import sys; AutoReplyStore(Path(sys.argv[1]))',
                    str(target)], cwd=oldroot, check=True)
    verify_inverse()
    with closing(sqlite3.connect(target)) as db:
        _transition(db, migrate_action_postfix_schema)
        assert _others(db) == others
        assert fingerprint(_nine_rows(db)[1]) == nine
        assert _facts(db, True) == current_facts
        assert _sequences(db) == sequences
        assert [tuple(row) for row in db.execute('pragma foreign_key_check')] == source_fk
        assert db.execute('pragma integrity_check').fetchall() == [('ok',)]
    paths = ['app/action_storage.py', 'app/action_postfix_storage.py',
             'scripts/validate_action_postfix_copy.py']
    proof = dict(
        scope='standalone storage migration; no runtime marker publication',
        source_version=marker[0], source_marker_unchanged=True,
        source_constructor_commit=source_ref, actual_source_deployment_verified=False,
        source_physical_tables=count, target_physical_tables=count - 3,
        unchanged_tables=others, nine_action_facts=nine,
        source_postfix_facts=source_facts, current_postfix_facts=current_facts,
        current_sequences=sequences, current_fact_inverse=True,
        source_constructor=True, forward_inverse_forward=True,
        new_update_rebind_key_change_delete_exercised=True,
        source_fk_findings=source_fk, real_sends=0,
        code_sha256={p: hashlib.sha256((repo / p).read_bytes()).hexdigest() for p in paths},
    )
    (root / 'proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
    return root, proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-ref', required=True)
    args = parser.parse_args()
    root, proof = validate(args.source.resolve(), args.source_ref, Path(__file__).resolve().parents[1])
    print(json.dumps({'temporary_root': str(root), 'scope': proof['scope'],
                      'source_physical_tables': proof['source_physical_tables'],
                      'target_physical_tables': proof['target_physical_tables'],
                      'unchanged_tables': len(proof['unchanged_tables']),
                      'actual_source_deployment_verified': False}))
