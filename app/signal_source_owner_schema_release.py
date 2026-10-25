"""Exact Store25.2/Email54 -> Store25.3/Email54 Signal source ownership.

The inverse converts current facts, including Signals created after publication;
no source payload archive or runtime compatibility reader is needed.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3

from app import action_storage as actions
from app.current_schema_manifest import current_objects, schema_objects
from app.provenance_definitions import SOURCE_FIELDS, SIGNAL_FIELDS, SIGNAL_FACT_FIELDS, provenance_objects
from app.state_definitions import state_projection
from app.state_storage import update_state_rows
from app.task8_storage import register_task8_functions
from app.task_lifecycle_jsonb import _array, _framed

SOURCE_STORE_VERSION = '2026-10-25.2'
TARGET_STORE_VERSION = '2026-10-25.3'
EMAIL_VERSION = 54
SOURCE_DIGEST = '7b4c91875faf9cf1bc57f912fc692b7c2a3966a03b9b2e384b2d8470ce0c832b'
TARGET_DIGEST = 'acf0537ba1aba7cabb8439599b1a9a1aed7f2b48ec061482a7baf600da72c316'
VERSION_KEY = 'store_schema_version'
KIND = 'business_task_signals'
PREFIX = 'ba_task_signals_'
STAGE = '_signal_owner_actions'


@lru_cache(maxsize=1)
def target_objects():
    result = current_objects(email_ledger=True)
    digest = hashlib.sha256(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if digest != TARGET_DIGEST:
        raise ValueError('frozen Signal target declaration changed')
    return result


@lru_cache(maxsize=1)
def _source_action_objects():
    from app.task_run_entity_assembly import _action_objects
    with closing(sqlite3.connect(':memory:')) as db:
        register_task8_functions(db)
        db.execute('create table _allocator(id integer primary key autoincrement)')
        db.execute('drop table _allocator')
        declarations = _action_objects(signal_source_references=False)
        for sql in declarations.values():
            db.execute(sql)
        return actions._objects(db, 'actions')


@lru_cache(maxsize=1)
def source_objects():
    objects = [obj for obj in target_objects() if obj[2] != 'actions']
    objects.extend((obj['type'], obj['name'], 'actions', obj['sql'])
                   for obj in _source_action_objects())
    result = tuple(sorted(objects, key=lambda obj: (obj[0], obj[1])))
    digest = hashlib.sha256(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    if digest != SOURCE_DIGEST:
        raise ValueError('frozen Signal source declaration changed')
    return result


def _marker(db):
    return db.execute(f"select value,updated_at from ({state_projection('service_state')}) where key=?",
                      (VERSION_KEY,)).fetchone()


def _write_marker(db, version, updated_at):
    update_state_rows(db, 'service_state', {'value': version, 'updated_at': updated_at},
                      where_sql='key=?', parameters=(VERSION_KEY,))


def _foreign_keys(db):
    return tuple(sorted(tuple(row) for row in db.execute('pragma foreign_key_check')))


def _same_values(left, right):
    return len(left) == len(right) and all(type(a) is type(b) and a == b
                                         for a, b in zip(left, right, strict=True))


def _cell(db, value):
    encoded = actions._encoded_native_sqlite_value(value)
    return db.execute('select jsonb(?)', (json.dumps(encoded, ensure_ascii=False, allow_nan=False),)).fetchone()[0]


def _forward_payloads(db):
    parents = {row[8]: (row[0], tuple(row[1:])) for row in db.execute(
        'select id,' + ','.join(SOURCE_FIELDS) + ' from provenance')}
    expressions = ','.join(PREFIX + field for field in SOURCE_FIELDS)
    converted = {}
    for row in db.execute('select id,payload_json,' + PREFIX + 'provenance_id,' + expressions
                          + " from actions where record_type='business_task_signals'").fetchall():
        action_id, blob, parent_id, *values = row
        width, cells = _array(blob)
        if len(cells) != len(SIGNAL_FIELDS):
            raise ValueError('unsupported Signal source payload width')
        if parent_id is None:
            parent = parents.get(values[SOURCE_FIELDS.index('identity_key')])
            if parent is not None:
                if not _same_values(parent[1], values):
                    raise ValueError('Signal identity collides with different source facts')
                parent_id = parent[0]
            else:
                parent_id = db.execute('insert into provenance(' + ','.join(SOURCE_FIELDS)
                    + ') values(' + ','.join('?' for _ in SOURCE_FIELDS) + ')', values).lastrowid
                parents[values[SOURCE_FIELDS.index('identity_key')]] = (parent_id, tuple(values))
        elif not any(parent[0] == parent_id for parent in parents.values()):
            raise ValueError('Signal parent cannot be resolved')
        converted[action_id] = _framed((_cell(db, parent_id), *cells[-len(SIGNAL_FACT_FIELDS):]), width)
    return converted, ()


def _inverse_payloads(db):
    parents = {row[0]: tuple(row[1:]) for row in db.execute(
        'select id,' + ','.join(SOURCE_FIELDS) + ' from provenance')}
    counts = dict(db.execute('select ' + PREFIX + 'provenance_id,count(*) from actions '
                            "where record_type='business_task_signals' group by " + PREFIX + 'provenance_id'))
    converted, retired = {}, set()
    for action_id, blob, parent_id in db.execute('select id,payload_json,' + PREFIX + 'provenance_id '
            "from actions where record_type='business_task_signals'").fetchall():
        if parent_id not in parents:
            raise ValueError('Signal parent cannot be resolved')
        width, cells = _array(blob)
        if len(cells) != 1 + len(SIGNAL_FACT_FIELDS):
            raise ValueError('unsupported Signal target payload width')
        if counts[parent_id] == 1:
            source = tuple(_cell(db, value) for value in parents[parent_id])
            reference = b'\x00'
            retired.add(parent_id)
        else:
            source = (b'\x00',) * len(SOURCE_FIELDS)
            reference = cells[0]
        converted[action_id] = _framed((*source, reference, *cells[1:]), width)
    return converted, tuple(sorted(retired))


def _rebuild(db, *, inverse):
    if not db.in_transaction or db.execute('pragma foreign_keys').fetchone()[0]:
        raise ValueError('Signal conversion requires caller-owned FK-off transaction')
    if db.execute('select 1 from temp.sqlite_schema where name=?', (STAGE,)).fetchone():
        raise ValueError('Signal transfer TEMP collision')
    sequences = db.execute('select rowid,name,seq from main.sqlite_sequence order by rowid').fetchall()
    converted, retired = _inverse_payloads(db) if inverse else _forward_payloads(db)
    objects = _source_action_objects() if inverse else tuple(
        dict(type=kind, name=name, sql=sql) for kind, name, owner, sql
        in target_objects() if owner == 'actions')
    columns = [row[1] for row in db.execute('pragma main.table_xinfo(actions)') if row[6] == 0]
    names = ','.join('"' + name.replace('"', '""') + '"' for name in columns)
    db.create_function('ceo_signal_owner_payload', 2, lambda identity, blob: converted.get(identity, blob))
    try:
        expressions = ','.join('ceo_signal_owner_payload(id,payload_json) as payload_json'
                              if column == 'payload_json' else '"' + column + '"' for column in columns)
        db.execute(f'create temp table {STAGE} as select {expressions} from main.actions')
        db.execute('drop table main.actions')
        db.execute(next(obj['sql'] for obj in objects if obj['type'] == 'table'))
        target_columns = [row[1] for row in db.execute('pragma main.table_xinfo(actions)') if row[6] == 0]
        if target_columns != columns:
            raise ValueError('Signal release changed unrelated stored columns')
        db.execute(f'insert into main.actions({names}) select {names} from temp.{STAGE} order by id')
        for category in ('index', 'trigger'):
            for obj in objects:
                if obj['type'] == category:
                    db.execute(obj['sql'])
        different = ' or '.join(f'a."{column}" is not s."{column}"' for column in columns)
        if (db.execute('select count(*) from main.actions').fetchone()
                != db.execute(f'select count(*) from temp.{STAGE}').fetchone()
                or db.execute(f'select 1 from temp.{STAGE} s left join main.actions a on a.id=s.id '
                              f'where a.id is null or ({different}) limit 1').fetchone()):
            raise ValueError('Signal transfer changed stored facts')
        if retired:
            db.execute('drop trigger trg_provenance_immutable_delete')
            db.executemany('delete from provenance where id=?', ((identity,) for identity in retired))
            db.execute(next(sql for sql in provenance_objects()
                            if sql.startswith('create trigger trg_provenance_immutable_delete ')))
        db.execute('delete from main.sqlite_sequence')
        db.executemany('insert into main.sqlite_sequence(rowid,name,seq) values(?,?,?)', sequences)
        db.execute(f'drop table temp.{STAGE}')
    finally:
        db.create_function('ceo_signal_owner_payload', 2, None)


@dataclass
class SignalSourceOwnerRelease:
    database_path: Path
    source_marker: tuple
    source_fk: tuple
    published: bool = False

    def _verify(self, db, *, target):
        expected = target_objects() if target else source_objects()
        marker = _marker(db)
        valid_marker = marker is not None and marker[0] == TARGET_STORE_VERSION if target else marker == self.source_marker
        if (not valid_marker or schema_objects(db) != expected
                or db.execute('select max(version) from email_schema_migrations').fetchone()[0] != EMAIL_VERSION
                or _foreign_keys(db) != self.source_fk
                or db.execute('pragma quick_check').fetchone()[0] != 'ok'):
            raise ValueError('Signal release schema, marker or integrity mismatch')

    def _convert(self, *, inverse):
        with closing(sqlite3.connect(self.database_path, isolation_level=None)) as db:
            register_task8_functions(db)
            db.execute('pragma foreign_keys=off')
            db.execute('begin immediate')
            try:
                self._verify(db, target=inverse)
                _rebuild(db, inverse=inverse)
                _write_marker(db, *(self.source_marker if inverse else
                    (TARGET_STORE_VERSION, datetime.now(timezone.utc).isoformat())))
                self._verify(db, target=not inverse)
                db.commit()
                self.published = not inverse
            except BaseException:
                db.rollback()
                raise

    def publish(self):
        self._convert(inverse=False)

    def verify_loaded(self):
        from app.current_schema_manifest import require_current_pair
        require_current_pair(self.database_path)
        with closing(sqlite3.connect(self.database_path.as_uri() + '?mode=ro', uri=True)) as db:
            register_task8_functions(db)
            db.execute('begin')
            self._verify(db, target=True)

    def rollback(self):
        if self.published:
            self._convert(inverse=True)
        else:
            with closing(sqlite3.connect(self.database_path.as_uri() + '?mode=ro', uri=True)) as db:
                register_task8_functions(db)
                db.execute('begin')
                self._verify(db, target=False)

    def finalize(self):
        self.verify_loaded()


def prepare_signal_source_owner_release(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        register_task8_functions(db)
        db.execute('begin')
        marker = _marker(db)
        if (marker is None or marker[0] != SOURCE_STORE_VERSION
                or schema_objects(db) != source_objects()
                or db.execute('select max(version) from email_schema_migrations').fetchone()[0] != EMAIL_VERSION):
            raise ValueError('unsupported Store25.2 / Email54 Signal source schema')
        release = SignalSourceOwnerRelease(path, tuple(marker), _foreign_keys(db))
        release._verify(db, target=False)
        return release
