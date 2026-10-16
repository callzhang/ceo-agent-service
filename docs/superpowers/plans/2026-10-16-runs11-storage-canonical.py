"""Source-shaped writers for the three new Runs11 families.

Store/Email own their implicit or explicit write transaction. Native source IDs
are distinct from structural Runs IDs. No begin, commit, schema selection,
provider invocation or persisted row snapshot is introduced.
"""
from contextlib import closing
from dataclasses import dataclass
import sqlite3

from app.action_storage import (
    _encoded_native_sqlite_value, _native_integer_primary_key,
    _raise_autoincrement_full, _source_bound_null,
)
from app.runs11_definitions import (
    RUN11_NEW_KINDS, RUN11_FIELDS, RUN11_COLUMN_MAP,
    RUN11_DEFAULT_SQL_BY_PROFILE,
)


@dataclass(frozen=True)
class Run11WriteResult:
    lastrowid: int
    rowcount: int
    physical_id: int


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _physical_next(db):
    row = db.execute("select seq from main.sqlite_sequence where name='runs'").fetchone()
    maximum = db.execute('select max(id) from main.runs').fetchone()[0]
    high = max(row[0] if row else 0, maximum or 0, 0)
    if high < 2**63 - 1:
        return high + 1
    # New native sources keep independent identities when the original shared
    # AUTO stream is full. Reuse the existing Tasks12 native ROWID mechanism.
    with closing(sqlite3.connect(':memory:')) as allocator:
        allocator.execute('create table ids(id integer primary key)')
        allocator.executemany('insert into ids values(?)', db.execute('select id from main.runs'))
        return allocator.execute('insert into ids default values').lastrowid


def _public_next(db, kind):
    row = db.execute('select seq from main.sqlite_sequence where name=?', ('runs:' + kind,)).fetchone()
    maximum = db.execute('select max(cast(legacy_key as integer)) from main.runs where record_type=?', (kind,)).fetchone()[0]
    high = max(row[0] if row else 0, maximum or 0, 0)
    if high == 2**63 - 1:
        # These three native sources have no BEFORE triggers. Native AUTO FULL
        # aborts their surrounding transaction, unlike the original Runs8 case.
        db.rollback()
        _raise_autoincrement_full()
    return high + 1


def insert_run11_fact(db, kind, values, *, source_variant='fresh', insert_sql=None,
                      insert_parameters=()):
    """Insert one existing source fact; return its original public cursor ID.

    The setup caller supplies its existing finished_at CASE expression. Native
    actual/fresh defaults remain available for exact source differential; the
    current public caller already supplies that historically differing field.
    """
    expressions = {} if insert_sql is None else insert_sql
    if kind not in RUN11_NEW_KINDS or source_variant not in RUN11_DEFAULT_SQL_BY_PROFILE:
        raise ValueError('unsupported Runs11 source kind or declaration profile')
    if (set(values) | set(expressions)) - set(RUN11_FIELDS[kind]) or 'id' in expressions:
        raise ValueError('unsupported Runs11 source field')
    if set(values) & set(expressions):
        raise ValueError('Runs11 source field supplied twice')
    db.execute('update main.runs set id=id where 0')
    # Match sqlite3's scalar binding errors before native AUTO/FULL execution.
    # Values themselves are bound unchanged into their declared affinities.
    for value in (*values.values(), *insert_parameters):
        _encoded_native_sqlite_value(value)
    public = (_public_next(db, kind) if _source_bound_null(values.get('id'))
              else _native_integer_primary_key(values['id']))
    physical = _physical_next(db)
    bound = {field: value for field, value in values.items() if field != 'id'}
    defaults = RUN11_DEFAULT_SQL_BY_PROFILE[source_variant][kind]
    missing = {field: defaults.get(field, 'NULL') for field in RUN11_FIELDS[kind]
               if field != 'id' and field not in bound and field not in expressions}
    mapping = RUN11_COLUMN_MAP[kind]
    columns = ['id', 'record_type', 'legacy_key',
               *(mapping[field] for field in (*bound, *expressions, *missing))]
    sql = ['?', '?', '?', *('?' for _ in bound), *expressions.values(), *missing.values()]
    cursor = db.execute('insert into main.runs (' + ','.join(map(_quote, columns))
                        + ') values (' + ','.join(sql) + ')',
                        (physical, kind, str(public), *bound.values(), *insert_parameters))
    return Run11WriteResult(public, cursor.rowcount, physical)
