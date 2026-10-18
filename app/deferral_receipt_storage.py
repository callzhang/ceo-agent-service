"""Existing operator deferral receipts in namespaced State; explicit native inverse."""
from contextlib import closing
from functools import lru_cache
import sqlite3

from app.action_storage import _native_payload, _objects
from app.action_email_definitions import _encoded_value
from app.action_email_storage import _external_dependencies
from app.state_definitions import state_projection
from app.state_storage import insert_state, update_state_rows, delete_state

NAME = 'reply_task_deferral_receipts'
PREFIX = NAME + ':'
FIELDS = ('id', 'request_id', 'task_id', 'business_object_key', 'execution_generation', 'input_version', 'latest_failed_run_id', 'latest_failed_run_fingerprint', 'original_status', 'original_available_at', 'deferred_until', 'duration_seconds', 'rationale', 'backup_path', 'backup_integrity', 'status', 'resume_request_id', 'created_at', 'resumed_at')
NATIVE_DDL = "\ncreate table if not exists reply_task_deferral_receipts (\n id integer primary key,\n request_id text not null unique,\n task_id integer not null,\n business_object_key text not null,\n execution_generation text not null,\n input_version integer not null,\n latest_failed_run_id integer not null,\n latest_failed_run_fingerprint text not null,\n original_status text not null,\n original_available_at text not null,\n deferred_until text not null,\n duration_seconds integer not null,\n rationale text not null,\n backup_path text not null,\n backup_integrity text not null,\n status text not null check(status in ('paused','resumed')),\n resume_request_id text not null default '',\n created_at text not null default current_timestamp,\n resumed_at text not null default ''\n);\ncreate unique index if not exists idx_reply_task_one_active_deferral\n on reply_task_deferral_receipts(task_id) where status='paused';\n"


def receipt_projection():
    columns = ["cast(substr(key," + str(len(PREFIX)+1) + ") as integer) as id"]
    columns += [_encoded_value('value', '$.' + name) + ' as "' + name + '"' for name in FIELDS[1:]]
    return ('select ' + ','.join(columns) + ' from (' + state_projection('service_state') +
            ') where key>=' + repr(PREFIX) + ' and key<' + repr(PREFIX[:-1]+';'))


def _one(db, clause, parameters):
    cursor = db.execute('select * from (' + receipt_projection() + ') where ' + clause, parameters)
    row = cursor.fetchone()
    return dict(zip((item[0] for item in cursor.description), row, strict=True)) if row is not None else None


def find_receipt(db, *, receipt_id=None, request_id=None):
    return _one(db, 'id=?', (receipt_id,)) if receipt_id is not None else _one(db, 'request_id=?', (request_id,))


def _next_id(db):
    maximum = db.execute('select max(id) from (' + receipt_projection() + ')').fetchone()[0]
    if maximum != 2**63-1:
        return 1 if maximum is None else maximum+1
    with closing(sqlite3.connect(':memory:')) as allocator:
        allocator.execute('create table ids(id integer primary key)')
        allocator.executemany('insert into ids values(?)', db.execute('select id from (' + receipt_projection() + ')'))
        return allocator.execute('insert into ids default values').lastrowid


def insert_receipt(db, values):
    # The existing operator always holds BEGIN IMMEDIATE, including its request
    # and active-task checks. IDs stay in this receipt namespace.
    row = dict(values)
    identity = row.pop('id') if 'id' in row else _next_id(db)
    row.setdefault('resume_request_id', '')
    row.setdefault('resumed_at', '')
    row.setdefault('created_at', db.execute('select current_timestamp').fetchone()[0])
    if set(row) != set(FIELDS[1:]):
        raise ValueError('unsupported deferral receipt fields')
    insert_state(db, 'service_state', {'key': PREFIX+str(identity), 'value': _native_payload(row)})
    return identity


def update_receipt(db, receipt_id, changes):
    row = find_receipt(db, receipt_id=receipt_id)
    row.pop('id')
    row.update(changes)
    return update_state_rows(db, 'service_state', {'value': _native_payload(row)},
                             where_sql='key=?', parameters=(PREFIX+str(receipt_id),))


@lru_cache(maxsize=1)
def _native_objects():
    with closing(sqlite3.connect(':memory:')) as db:
        db.executescript(NATIVE_DDL)
        return _objects(db, NAME)


def capture_native_receipts(db):
    actual = _objects(db, NAME)
    if not actual:
        return None
    if actual != _native_objects():
        raise ValueError('unsupported native deferral receipt declaration')
    return actual


def migrate_native_receipts(db):
    if capture_native_receipts(db) is None:
        return
    _external_dependencies(db, targets=(NAME,))
    cursor = db.execute('select * from main.' + NAME + ' order by id')
    names = tuple(item[0] for item in cursor.description)
    for values in cursor.fetchall():
        insert_receipt(db, dict(zip(names, values, strict=True)))
    db.execute('drop table main.' + NAME)


def restore_native_receipts(db, *, preserve_empty):
    cursor = db.execute(receipt_projection() + ' order by id')
    rows = cursor.fetchall()
    if not rows and not preserve_empty:
        return None
    for statement in NATIVE_DDL.split(';'):
        if statement.strip():
            db.execute(statement)
    db.executemany('insert into main.' + NAME + '(' + ','.join(FIELDS) + ') values(' +
                   ','.join('?' for _ in FIELDS) + ')', rows)
    identities = db.execute('select rowid from (' + state_projection('service_state', include_rowid=True) +
                            ') where key>=? and key<?', (PREFIX, PREFIX[:-1]+';')).fetchall()
    for (identity,) in identities:
        delete_state(db, 'service_state', identity)
    return capture_native_receipts(db)
