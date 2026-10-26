"""Frozen predecessor candidate writer for migration source fixtures only."""
from contextlib import closing
import sqlite3

from app import action_storage as storage
from app.action_email_definitions import EMAIL_NUMERIC_SCOPE
from app.shared_action_definitions import SPECIALIZED_RECORD_TYPES


def _layout(kind):
    return storage.action_payloads('actual_no_memory_fk', selection_references=True,
        execution_owner=True, shared_lifecycle=True, specialized_claims=True).layouts[kind]


def _allocate(db, kind, supplied=None):
    """Preserve each old namespace and its ordinary/AUTOINCREMENT semantics."""
    if supplied is not None:
        return storage._native_integer_primary_key(supplied)
    maximum = db.execute('select max(cast(legacy_key as integer)) from actions where record_type=? and ' + EMAIL_NUMERIC_SCOPE, (kind,)).fetchone()[0]
    if maximum == 2**63 - 1:
        with closing(sqlite3.connect(':memory:')) as allocator:
            allocator.execute('create table ids(id integer primary key)')
            allocator.executemany('insert into ids values(?)', db.execute(
                'select cast(legacy_key as integer) from actions where record_type=?', (kind,)))
            return allocator.execute('insert into ids default values').lastrowid
    return (maximum or 0) + 1


def insert_candidate_source(db, kind, values):
    if kind not in {"review_candidates", "candidate_reviews", "candidate_selections", "candidate_supplements"}:
        raise ValueError("not a predecessor candidate fixture kind")
    storage._check_fields(kind, values)
    storage._email_lock(db, tuple(values.values()))
    layout = _layout(kind)
    clock = db.execute('select current_timestamp').fetchone()[0]
    defaults = {**storage.DEFAULTS[kind], **{field: clock for field in ('created_at', 'updated_at') if field in layout.fields}}
    references = storage._kind_references(kind, execution_owner=True)
    payload = dict.fromkeys(layout.fields) | defaults | {field: value for field, value in values.items() if field in layout.fields}
    public = _allocate(db, kind, values.get('id'))
    physical = {field: storage._parent_id(db, field, values.get(field)) for field in references}
    columns = ('_kind', 'legacy_key', 'payload_json', *physical)
    packed = db.execute('select jsonb(?)', (layout.pack(payload, scalar_codec=storage._encoded_native_sqlite_value),)).fetchone()[0]
    cursor = db.execute('insert into actions('
        + ','.join(columns) + ') values(' + ','.join('?' for _ in columns) + ')'
        + ' returning cast(legacy_key as integer)',
        (SPECIALIZED_RECORD_TYPES.code(kind), str(public), packed, *physical.values()))
    row = cursor.fetchone()
    cursor.fetchall()
    return storage.ActionWriteResult(row[0] if row else None, cursor.rowcount)

