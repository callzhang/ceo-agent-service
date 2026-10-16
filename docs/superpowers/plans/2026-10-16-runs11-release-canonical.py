"""Source-bound Runs11 transfer; the caller owns the FK-off transaction.

Inverse metadata identifies finite declarations only. Current rows and current
source counters are staged in TEMP, restored, checked, and removed in one caller
transaction. No original row image or FTS shadow data is persisted.
"""
from contextlib import closing
from functools import lru_cache
import sqlite3

from app.action_storage import _no_sql_dependencies
from app.configs_run_schema import _declarations, _objects
from app.runs11_definitions import (
    RUN11_BASE_COLUMNS, RUN11_FIELDS, RUN11_NEW_KINDS, RUN11_SOURCE_PROFILES,
    create_runs11_schema, initialize_run11_sequences, run11_projection,
)
from app.runs11_storage import insert_run11_fact
from app.task20_schema_release import _quote, _sequence, _set_sequence
from app.workbench_storage import SEARCH_FTS_DDL

FTS = 'codex_session_search_fts'
SOURCES = ('runs', *RUN11_NEW_KINDS)
STAGES = {name: 'runs11_release_' + name for name in SOURCES}
STREAMS = ('runs:published', *('runs:' + kind for kind in RUN11_NEW_KINDS))


def _transaction(db):
    if not db.in_transaction or db.execute('pragma foreign_keys').fetchone()[0]:
        raise sqlite3.ProgrammingError('Runs11 release requires caller FK-off transaction')
    for name in (*SOURCES, *(row[1] for row in _fts_objects()), *STAGES.values()):
        if db.execute('select 1 from temp.sqlite_schema where name=? collate nocase', (name,)).fetchone():
            raise ValueError('Runs11 TEMP relation or staging collision')


def _metadata(metadata):
    if (type(metadata) is not dict or set(metadata) != {'variant'}
            or metadata['variant'] not in RUN11_SOURCE_PROFILES):
        raise ValueError('unsupported Runs11 source metadata')


def _source_objects(db, table):
    return tuple(sorted(tuple(row) for row in db.execute(
        'select type,name,sql from main.sqlite_schema where tbl_name=? and sql is not null',
        (table,))))


@lru_cache(maxsize=1)
def _target_objects():
    with closing(sqlite3.connect(':memory:')) as db:
        create_runs11_schema(db)
        return _objects(db)


@lru_cache(maxsize=1)
def _fts_objects():
    with closing(sqlite3.connect(':memory:')) as db:
        db.execute(SEARCH_FTS_DDL)
        return tuple(sorted(tuple(row) for row in db.execute(
            'select type,name,tbl_name,sql from main.sqlite_schema where sql is not null')))


def _check_fts(db):
    owners = tuple(row[1] for row in _fts_objects())
    actual = tuple(sorted(tuple(row) for row in db.execute(
        'select type,name,tbl_name,sql from main.sqlite_schema where tbl_name in ('
        + ','.join('?' for _ in owners) + ') and sql is not null', owners)))
    if actual != _fts_objects():
        raise ValueError('unsupported source title FTS declarations')


def _source_profile(db):
    for variant, tables in RUN11_SOURCE_PROFILES.items():
        if all(_source_objects(db, name) == tuple(sorted(objects))
               for name, objects in tables.items()):
            _check_fts(db)
            return variant
    raise ValueError('unsupported Runs11 source declarations')


def _dependencies(db):
    retired = {*RUN11_NEW_KINDS, *(row[1] for row in _fts_objects())}
    for (child,) in db.execute("select name from main.sqlite_schema where type='table'"):
        if any(edge[2].lower() in retired for edge in db.execute(
                'pragma main.foreign_key_list(' + _quote(child) + ')')):
            raise ValueError('unsupported incoming Runs11 source foreign key')
    _no_sql_dependencies(db, retired)


def _inverse_dependencies(db):
    """Compile the exact inverse schema in empty SQLite before retiring columns.

    Existing external triggers over the original62 columns remain supported.
    Views and trigger programs referencing new columns must not survive broken.
    EXPLAIN prepares every program without executing a trigger or external effect.
    """
    objects = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from main.sqlite_schema where sql is not null "
        "and name not glob 'sqlite_*' and tbl_name<>'runs' "
        "order by case type when 'table' then 0 when 'index' then 1 when 'view' then 2 else 3 end,name")]
    with closing(sqlite3.connect(':memory:')) as parser:
        try:
            for sql in _declarations(mapped=True).values():
                parser.execute(sql)
            for _kind, _name, _owner, sql in objects:
                parser.execute(sql)
            for kind, name, _owner, _sql in objects:
                if kind == 'view':
                    parser.execute('explain select * from ' + _quote(name) + ' limit 0')
                elif kind == 'table':
                    fields = [row[1] for row in parser.execute(
                        'pragma table_xinfo(' + _quote(name) + ')') if row[6] == 0]
                    assignment = ','.join(_quote(field) + '=' + _quote(field) for field in fields)
                    for sql in ('insert into ' + _quote(name) + ' default values',
                                'update ' + _quote(name) + ' set ' + assignment + ' where 0',
                                'delete from ' + _quote(name) + ' where 0'):
                        parser.execute('explain ' + sql)
        except sqlite3.Error as error:
            raise ValueError('unsupported Runs11 inverse SQL dependency') from error


def capture_run11_source(db, *, mapped=False):
    """Capture before the paired conversion; retain no facts or water values."""
    _transaction(db)
    if _objects(db) != _declarations(mapped=mapped):
        raise ValueError('unsupported original Runs8 declaration')
    variant = _source_profile(db)
    if any(_sequence(db, name) is not None for name in STREAMS):
        raise ValueError('Runs11 target allocator already exists')
    _dependencies(db)
    return {'variant': variant}


def run11_schema_is_current(db):
    """Require the one target declaration and absence of all retired relations."""
    retired = (*RUN11_NEW_KINDS, *(row[1] for row in _fts_objects()))
    return (_objects(db) == _target_objects()
            and not any(db.execute('select 1 from main.sqlite_schema where name=? collate nocase',
                                   (name,)).fetchone() for name in retired))


def _fields(name):
    return RUN11_BASE_COLUMNS if name == 'runs' else RUN11_FIELDS[name]


def _stage(db, name, query):
    fields = ','.join(map(_quote, _fields(name)))
    stage = 'temp.' + _quote(STAGES[name])
    # No affinity in staging: SQLite scalar storage classes remain observable.
    db.execute('create temp table ' + _quote(STAGES[name]) + '(' + fields + ')')
    db.execute('insert into ' + stage + ' select ' + fields + ' from (' + query + ')')


def _compare(db, name, query):
    fields = _fields(name)
    typed = ','.join(map(_quote, fields)) + ',' + ','.join(
        'typeof(' + _quote(field) + ')' for field in fields)
    left = 'select ' + typed + ' from temp.' + _quote(STAGES[name])
    right = 'select ' + typed + ' from (' + query + ')'
    if db.execute('select count(*) from temp.' + _quote(STAGES[name])).fetchone()[0] != db.execute(
            'select count(*) from (' + query + ')').fetchone()[0]:
        raise ValueError('Runs11 source fact count differs')
    for a, b in ((left, right), (right, left)):
        if db.execute('select 1 from (' + a + ' except ' + b + ') limit 1').fetchone():
            raise ValueError('Runs11 source identity, value or storage class differs')


def _finish(db):
    for name in SOURCES:
        db.execute('drop table temp.' + _quote(STAGES[name]))


def migrate_run11_schema(db, metadata):
    """Fold three native families into Runs after Config mapped its two FKs."""
    _transaction(db)
    _metadata(metadata)
    if capture_run11_source(db, mapped=True) != metadata:
        raise ValueError('Runs11 source declarations changed after capture')
    waters = {name: _sequence(db, name) for name in SOURCES}
    for name in SOURCES:
        _stage(db, name, 'select * from main.' + _quote(name))
        _compare(db, name, 'select * from main.' + _quote(name))
    db.execute('drop table main.' + _quote(FTS))
    for name in SOURCES:
        db.execute('drop table main.' + _quote(name))
    create_runs11_schema(db)
    fields = ','.join(map(_quote, RUN11_BASE_COLUMNS))
    db.execute('insert into main.runs(' + fields + ') select ' + fields
               + ' from temp.' + _quote(STAGES['runs']))
    _set_sequence(db, 'runs', waters['runs'])
    for name in RUN11_NEW_KINDS:
        fields = _fields(name)
        for row in db.execute('select * from temp.' + _quote(STAGES[name]) + ' order by id'):
            insert_run11_fact(db, name, dict(zip(fields, row, strict=True)),
                              source_variant=metadata['variant'])
        _compare(db, name, run11_projection(name))
    old = 'select * from main.runs where record_type not in (' + ','.join(map(repr, RUN11_NEW_KINDS)) + ')'
    _compare(db, 'runs', old)
    initialize_run11_sequences(db, waters)
    if not run11_schema_is_current(db):
        raise ValueError('Runs11 target declaration differs')
    counts = {name: db.execute('select count(*) from temp.' + _quote(STAGES[name])).fetchone()[0]
              for name in SOURCES}
    _finish(db)
    return counts


def restore_run11_schema(db, metadata):
    """Restore current facts/counters before Config restores native Runs FKs."""
    _transaction(db)
    _metadata(metadata)
    if not run11_schema_is_current(db):
        raise ValueError('unsupported Runs11 target declaration')
    _inverse_dependencies(db)
    waters = {'runs': _sequence(db, 'runs:published'),
              **{name: _sequence(db, 'runs:' + name) for name in RUN11_NEW_KINDS}}
    old = 'select * from main.runs where record_type not in (' + ','.join(map(repr, RUN11_NEW_KINDS)) + ')'
    _stage(db, 'runs', old)
    for name in RUN11_NEW_KINDS:
        _stage(db, name, run11_projection(name))
    db.execute('drop table main.runs')
    declarations = _declarations(mapped=True)
    db.execute(declarations['runs'])
    for objects in RUN11_SOURCE_PROFILES[metadata['variant']].values():
        for category in ('table', 'index'):
            for kind, _name, sql in objects:
                if kind == category:
                    db.execute(sql)
    for name in SOURCES:
        fields = ','.join(map(_quote, _fields(name)))
        db.execute('insert into main.' + _quote(name) + '(' + fields + ') select '
                   + fields + ' from temp.' + _quote(STAGES[name]))
        _set_sequence(db, name, waters[name])
        _compare(db, name, 'select * from main.' + _quote(name))
    for name, sql in declarations.items():
        if name != 'runs':
            db.execute(sql)
    for name in STREAMS:
        _set_sequence(db, name, None)
    db.execute(SEARCH_FTS_DDL)
    db.execute('insert into main.' + _quote(FTS) + '(' + _quote(FTS) + ") values('rebuild')")
    if _objects(db) != declarations or _source_profile(db) != metadata['variant']:
        raise ValueError('Runs11 inverse declaration differs')
    if any(_sequence(db, name) != water for name, water in waters.items()):
        raise ValueError('Runs11 inverse current allocator differs')
    _finish(db)
