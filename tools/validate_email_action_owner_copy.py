"""Validate a disposable 29.2 copy with real predecessor cold read and write.

No provider, model, worker or native CLI is invoked. Output contains only counts,
identities and hashes. Action JSON object order is not a business fact; all other
stored columns are compared as typed SQLite values and opaque blobs.
"""
from contextlib import closing
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.action_storage import email_action_projection  # noqa: E402
from app.current_schema_manifest import schema_objects  # noqa: E402
from app.email_action_owner_release import prepare_email_action_owner_release, source_objects  # noqa: E402
from app.task8_storage import register_task8_functions  # noqa: E402
from tools.native_copy_validation import _identities, _scalar  # noqa: E402


def fingerprint(path, identities=None):
    result = {}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        register_task8_functions(db)
        db.execute('begin')
        plans = {row[0]: row[1:] for row in db.execute('select action_plan_id,classification_id,account_id,action_parameters_json from (' + email_action_projection('email_action_plans') + ')')}
        for schema, table, kind, _, without_rowid, _ in db.execute('pragma table_list').fetchall():
            if schema != 'main' or kind != 'table' or table.startswith('sqlite_'):
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            columns = db.execute(f'pragma table_xinfo({quoted})').fetchall()
            fields = [row[1] for row in columns if row[6] == 0]
            primary = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
            projection = ','.join('json(action_data)' if table == 'actions' and field == 'action_data' else '"' + field + '"' for field in fields)
            order = ','.join('"' + key + '"' for key in primary) if without_rowid else 'rowid'
            query = f'select {projection}' if without_rowid else f'select rowid,{projection}'
            query += f' from {quoted}'
            if table == 'state':
                query += " where not(record_type='service_state' and json_extract(payload_json,'$.key')='store_schema_version')"
            query += ' order by ' + order
            digest = hashlib.sha256(json.dumps(fields).encode())
            count = 0
            for row in db.execute(query):
                row = list(row)
                offset = 0 if without_rowid else 1
                identity = tuple(row[fields.index(name)] for name in primary) if without_rowid else row[0]
                if identities is not None and identity not in identities[table]:
                    continue
                if table == 'actions' and row[offset + fields.index('_kind')] == 70:
                    index = offset + fields.index('action_data')
                    data = json.loads(row[index])
                    operation, plan_id = data.get('operation'), data.get('action_plan_id')
                    direct = operation in ('archive','flag_important','mark_read','move','trash','label') and plan_id is not None
                    if direct:
                        classification, account, encoded = plans[plan_id]
                        params = db.execute("select coalesce(json_extract(?, '$.'||?), '{}')", (encoded, operation)).fetchone()[0]
                        values = (classification, account, params)
                    else:
                        values = (None, None, None)
                    for name, value in zip(('classification_id','account_id','parameters_json'), values, strict=True):
                        if name not in data:
                            data[name] = value
                    row[index] = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                digest.update(json.dumps([_scalar(v) for v in row], ensure_ascii=False, separators=(',', ':')).encode() + b'\n')
                count += 1
            result[table] = {'rows': count, 'sha256': digest.hexdigest()}
    return result


def water(path):
    with closing(sqlite3.connect(path)) as db:
        return list(db.execute('select rowid,name,seq from sqlite_sequence order by rowid'))


def require_cohort(path, identities, facts, *, exact_identities):
    if exact_identities and _identities(path) != identities:
        raise ValueError('validation cohort identities changed')
    if fingerprint(path, identities) != facts:
        raise ValueError('validation cohort typed facts changed')


def seed(path):
    from app.action_entity_storage import append_action_receipt
    from app.shared_action_lifecycle import create_direct_email_action, persist_email_plan
    tag = 'owner-validation-' + uuid.uuid4().hex
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        register_task8_functions(db)
        db.execute('begin')
        cursor = db.execute('select p.* from (' + email_action_projection('email_action_plans') + ") p where exists(select 1 from json_each(p.actions_json) where value in ('archive','flag_important','mark_read','move','trash','label')) limit 1")
        row = cursor.fetchone()
        if row is None:
            raise ValueError('copy has no existing Plan to use as synthetic fixture parent')
        plan = dict(row)
        plan['action_plan_id'] = tag
        plan['action_plan_version'] = db.execute('select max(action_plan_version)+1 from (' + email_action_projection('email_action_plans') + ') where classification_id=?', (plan['classification_id'],)).fetchone()[0]
        persist_email_plan(db, plan)
        direct = [value for value in json.loads(plan['actions_json']) if value in ('archive','flag_important','mark_read','move','trash','label')]
        actions = [create_direct_email_action(db, {'action_plan_id': tag, 'action_type': value, 'status': 'pending', 'created_at': 'synthetic', 'updated_at': 'synthetic'}) for value in direct]
        action = actions[0]
        receipt = append_action_receipt(db, action['id'], values={'status': 'failed', 'error_text': 'synthetic failure'})
        db.commit()
        parameters = db.execute("select coalesce(json_extract(?, '$.'||?), '{}')", (plan['action_parameters_json'], direct[0])).fetchone()[0]
        return {'plan_id': tag, 'action_id': action['id'], 'receipt_id': receipt, 'parameters': parameters}


OLD_WRITE = r'''
import json,socket,sqlite3,sys
socket.socket.connect=lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('network forbidden'))
from pathlib import Path
from app.email_store import DIRECT_EMAIL_ACTION_SQL,EmailStore
from app.action_entity_storage import create_action,append_action_receipt
from app.task8_storage import register_task8_functions
from app.store import AutoReplyStore,_close_idle_connections
path=sys.argv[1]; seed=json.loads(sys.argv[2]); store=AutoReplyStore(Path(path)); email=EmailStore(Path(path)); email.list_action_attempts(seed['action_id'])
db=sqlite3.connect(path); db.row_factory=sqlite3.Row; register_task8_functions(db)
row=db.execute('select parameters_json from ('+DIRECT_EMAIL_ACTION_SQL+') where action_id=?',(seed['action_id'],)).fetchone(); assert row[0]==seed['parameters']
assert db.execute('select error_text from action_receipts where id=?',(seed['receipt_id'],)).fetchone()[0]=='synthetic failure'
db.execute('begin'); a=create_action(db,values={'operation':'synthetic','status':'failed'}); r=append_action_receipt(db,a['id'],values={'status':'failed','error_text':'cold failure'}); db.commit()
assert db.execute('select error_text from action_receipts where id=?',(r,)).fetchone()[0]=='cold failure'
_close_idle_connections(); print(json.dumps({'action_id':a['id'],'receipt_id':r,'cold_read_write_passed':True,'store_and_email_constructors':True}))
'''


def validate(path, old_code):
    old_sha = subprocess.run(['git', '-C', str(old_code), 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True).stdout.strip()
    if old_sha != '57c75b0c05b12153027fbd1ef9d384cec5385ed1':
        raise ValueError('predecessor checkout is not the approved Unit1 commit')
    socket.socket.connect = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('network forbidden'))
    identities = _identities(path)
    before = fingerprint(path, identities)
    initial_water = water(path)
    with closing(sqlite3.connect(path)) as db:
        initial_fk = list(db.execute('pragma foreign_key_check'))
    release = prepare_email_action_owner_release(path)
    release.publish()
    release.verify_loaded()
    assert fingerprint(path, identities) == before and water(path) == initial_water
    synthetic = seed(path)
    seeded_ids = _identities(path)
    seeded_facts = fingerprint(path, seeded_ids)
    target_water = water(path)
    release.rollback()
    assert fingerprint(path, identities) == before and water(path) == target_water
    require_cohort(path, seeded_ids, seeded_facts, exact_identities=True)
    with closing(sqlite3.connect(path)) as db:
        assert schema_objects(db) == source_objects()
        assert list(db.execute('pragma foreign_key_check')) == initial_fk
    env = {**os.environ, 'PYTHONPATH': str(old_code), 'CEO_ENV_FILE': '/private/tmp/no-email-owner-validation.env'}
    before_cold = _identities(path)
    result = subprocess.run([sys.executable, '-c', OLD_WRITE, str(path), json.dumps(synthetic)], cwd=old_code, env=env, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('predecessor cold read/write failed: ' + result.stderr)
    cold = json.loads(result.stdout.strip().splitlines()[-1])
    after_cold = _identities(path)
    for table in before_cold:
        added = after_cold[table] - before_cold[table]
        expected = {cold['action_id']} if table == 'actions' else {cold['receipt_id']} if table == 'action_receipts' else set()
        if added != expected or before_cold[table] - after_cold[table]:
            raise ValueError('predecessor constructor or cold write changed unexpected row identities in ' + table)
    require_cohort(path, seeded_ids, seeded_facts, exact_identities=False)
    cold_facts = fingerprint(path, after_cold)
    cold_water = water(path)
    again = prepare_email_action_owner_release(path)
    again.publish()
    again.finalize()
    assert fingerprint(path, identities) == before and water(path) == cold_water
    require_cohort(path, after_cold, cold_facts, exact_identities=True)
    with closing(sqlite3.connect(path)) as db:
        assert list(db.execute('pragma foreign_key_check')) == initial_fk
        assert db.execute('select error_text from action_receipts where id=?', (cold['receipt_id'],)).fetchone()[0] == 'cold failure'
    return {'original_tables': len(before), 'original_rows': sum(v['rows'] for v in before.values()),
            'forward_inverse_reforward_facts_equal': True, 'watermarks_preserved': True,
            'foreign_key_findings': initial_fk, 'synthetic': synthetic, 'old_code': cold, 'old_code_sha': old_sha,
            'real_sends': 0, 'workers_started': False, 'native_cli_invoked': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='disposable copy; modified in place')
    parser.add_argument('--old-code', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.database.resolve(), args.old_code.resolve()), ensure_ascii=False, sort_keys=True))
