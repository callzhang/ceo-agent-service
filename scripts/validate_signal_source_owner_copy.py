"""Validate the Signal owner release on an existing disposable database copy.

Never starts a worker or calls a provider. The old-code path is a frozen checkout
of the immediate predecessor, not a runtime fallback.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import pickle
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.provenance_definitions import SOURCE_FIELDS, SIGNAL_FACT_FIELDS  # noqa: E402
from app.task8_storage import register_task8_functions  # noqa: E402


def connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    register_task8_functions(db)
    db.execute('begin')
    return db


def snapshot(path, *, old):
    result = {}
    with closing(connect(path)) as db:
        for (table,) in db.execute("select name from sqlite_schema where type='table' order by name"):
            if table == 'provenance':
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            fields = [row[1] for row in db.execute(f'pragma table_xinfo({quoted})') if row[6] == 0]
            names = ','.join('"' + name.replace('"', '""') + '"' for name in fields)
            where = " where record_type<>'business_task_signals'" if table == 'actions' else (
                " where not(record_type='service_state' and key='store_schema_version')" if table == 'state' else '')
            # Every current table has its own stable primary identity. Ordering
            # every stored column also handles WITHOUT ROWID owners explicitly.
            digest, count = hashlib.sha256(), 0
            for row in db.execute(f'select {names} from {quoted}{where} order by {names}'):
                data = pickle.dumps(tuple(row), protocol=5)
                digest.update(len(data).to_bytes(8, 'big'))
                digest.update(data)
                count += 1
            result[table] = (count, digest.hexdigest())
        source = ','.join(f'coalesce(p.{field},a.ba_task_signals_{field})' if old else 'p.' + field
                          for field in SOURCE_FIELDS)
        own = ','.join('a.ba_task_signals_' + field for field in SIGNAL_FACT_FIELDS)
        signals = db.execute('select a.id,a.legacy_key,' + source + ',' + own + ' from actions a '
            'left join provenance p on p.id=a.ba_task_signals_provenance_id '
            "where a.record_type='business_task_signals' order by a.id").fetchall()
        parents = dict((row[0], tuple(row[1:])) for row in db.execute('select id,' + ','.join(SOURCE_FIELDS) + ' from provenance'))
        fks = sorted(tuple(row) for row in db.execute('pragma foreign_key_check'))
    return dict(tables=result, signals=signals, parents=parents, fks=fks)


def stable(before, after, *, original_parents):
    assert before['tables'] == after['tables'], 'unrelated stored facts or allocation watermarks changed'
    assert before['signals'] == after['signals'], 'Signal identity/source/own facts changed'
    assert before['fks'] == after['fks'], 'FK finding set changed'
    assert all(after['parents'].get(key) == value for key, value in original_parents.items()), 'original shared source changed'


def add_current_facts(path):
    from app.store import AutoReplyStore
    from app.action_entity_storage import create_action, append_action_receipt
    store = AutoReplyStore(path)
    ids = []
    with store._connect() as db, db:
        db.execute('begin')
        for key, source in [('one', 'sole'), ('two-a', 'shared'), ('two-b', 'shared')]:
            ids.append(store.create_business_task_signal_in_transaction(
                source_type='dingtalk_chat', source_ref='signal-owner-copy-' + source,
                source_time='copy-test-clock', evidence_text='synthetic copy body ' + source,
                conversation_id='copy-only', author_user_id='copy-only', author_name='Copy test', author_kind='human',
                conversation_title='Copy only', context_json='{"copy_only":true}',
                dedupe_key='signal-owner-copy-' + key, _db=db))
        action = create_action(db, values={'operation': 'signal-owner-copy-substitute', 'status': 'done'})
        append_action_receipt(db, action['id'], values={'provider_result_json': '{"substitute":true,"no_provider_call":true}'})
        for identity in ids:
            assert store.get_business_task_signal_in_transaction(signal_id=identity, _db=db) is not None
    return ids


OLD_CODE_PROBE = '''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[3])
import app.store
assert Path(app.store.__file__).resolve().is_relative_to(Path(sys.argv[3]).resolve())
from app.store import AutoReplyStore
from app.provenance_storage import signals_for_source
store=AutoReplyStore(Path(sys.argv[1]))
with store._connect() as db,db:
 db.execute('begin')
 for identity in json.loads(sys.argv[2]):
  assert store.get_business_task_signal_in_transaction(signal_id=identity,_db=db) is not None
 for source,label in (('sole','one'),('shared','shared')):
  before=store.get_business_task_signal_in_transaction(signal_id=json.loads(sys.argv[2])[0 if source=='sole' else 1],_db=db)
  identity=store.create_business_task_signal_in_transaction(source_type='dingtalk_chat',source_ref='signal-owner-copy-'+source,source_time='copy-test-clock',evidence_text='synthetic copy body '+source,conversation_id='copy-only',author_user_id='copy-only',author_name='Copy test',author_kind='human',conversation_title='Copy only',context_json='{"copy_only":true}',dedupe_key='old-cold-continue-'+label,_db=db)
  assert store.get_business_task_signal_in_transaction(signal_id=identity,_db=db).source==before.source
  assert identity in signals_for_source(db,source_type='dingtalk_chat',source_ref='signal-owner-copy-'+source,source_time='copy-test-clock')
 ids=[]
 for suffix in ('a','b'):
  ids.append(store.create_business_task_signal_in_transaction(source_type='dingtalk_chat',source_ref='old-cold-source',source_time='copy-clock',evidence_text='old-code copy-only material',dedupe_key='old-cold-'+suffix,_db=db))
 assert set(signals_for_source(db,source_type='dingtalk_chat',source_ref='old-cold-source',source_time='copy-clock'))==set(ids)
print(json.dumps({'old_cold_read':True,'old_cold_write':True,'new_signals':ids}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--old-code', type=Path, required=True)
    args = parser.parse_args()
    path = args.database.resolve()
    if path == Path('/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3'):
        parser.error('a disposable copy is required')
    started = time.monotonic()
    from app.signal_source_owner_schema_release import prepare_signal_source_owner_release
    original = snapshot(path, old=True)
    print("source typed snapshot complete", flush=True)
    release = prepare_signal_source_owner_release(path)
    release.publish()
    release.verify_loaded()
    print("forward manifest/integrity complete", flush=True)
    stable(original, snapshot(path, old=False), original_parents=original['parents'])
    ids = add_current_facts(path)
    current = snapshot(path, old=False)
    print("new current facts and substitute receipt complete", flush=True)
    release.rollback()
    stable(current, snapshot(path, old=True), original_parents=original['parents'])
    print("current-data inverse typed comparison complete", flush=True)
    result = subprocess.run([sys.executable, '-c', OLD_CODE_PROBE, str(path), json.dumps(ids), str(args.old_code.resolve())],
                            cwd=args.old_code, capture_output=True, text=True, timeout=90, check=True)
    print(result.stdout.strip(), flush=True)
    inverse = snapshot(path, old=True)
    release = prepare_signal_source_owner_release(path)
    release.publish()
    release.finalize()
    final = snapshot(path, old=False)
    stable(inverse, final, original_parents=original['parents'])
    print(json.dumps(dict(pass_validation=True, original_signals=len(original['signals']),
        original_parents=len(original['parents']), final_signals=len(final['signals']),
        preserved_tables=len(final['tables']), original_fk_findings=original['fks'],
        seconds=round(time.monotonic()-started, 2), real_provider_calls=0)), flush=True)


if __name__ == '__main__':
    main()
