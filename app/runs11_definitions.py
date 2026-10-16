"""Unselected current Runs11 declarations; historical Runs8 factories stay exact.

Source affinities/defaults/constraints are finite native declarations. Source IDs
are public legacy keys; physical Runs identities remain native positive AUTO IDs.
The caller owns every transaction, including allocator initialization.
"""
import sqlite3
from contextlib import closing

from app.configs_run_schema import _declarations

RUN11_NEW_KINDS = ('codex_session_search_index', 'email_classifier_runtime_samples', 'setup_wizard_events')

RUN11_SOURCE_COLUMNS = {'codex_session_search_index': (('id', 'INTEGER', False, None, 'legacy_key'),
                                ('session_id', 'TEXT', True, None, 'session_id'),
                                ('source_type', 'TEXT', True, "''", 'source_type'),
                                ('source_id', 'TEXT', True, "''", 'source_id'),
                                ('title', 'TEXT', True, "''", 'title'),
                                ('created_at', 'TEXT', True, 'current_timestamp', 'created_at'),
                                ('updated_at', 'TEXT', True, 'current_timestamp', 'updated_at')),
 'email_classifier_runtime_samples': (('id', 'INTEGER', False, None, 'legacy_key'),
                                      ('model_id', 'TEXT', True, None, 'model_id'),
                                      ('outcome', 'TEXT', True, None, 'outcome'),
                                      ('fallback_code', 'TEXT', True, "''", 'fallback_code'),
                                      ('cache_hit', 'INTEGER', True, None, 'cache_hit'),
                                      ('runtime_warm', 'INTEGER', True, None, 'runtime_warm'),
                                      ('queue_ms', 'REAL', True, None, 'queue_ms'),
                                      ('http_ms', 'REAL', True, None, 'http_ms'),
                                      ('embedding_ms', 'REAL', True, None, 'embedding_ms'),
                                      ('head_ms', 'REAL', True, None, 'head_ms'),
                                      ('total_ms', 'REAL', True, None, 'total_ms'),
                                      ('recorded_at', 'TEXT', True, None, 'created_at')),
 'setup_wizard_events': (('id', 'INTEGER', False, None, 'legacy_key'),
                         ('step_id', 'TEXT', True, None, 'step_id'),
                         ('action_id', 'TEXT', True, None, 'action_id'),
                         ('status', 'TEXT', True, None, 'status'),
                         ('summary', 'TEXT', True, "''", 'summary'),
                         ('evidence_json', 'TEXT', True, "'{}'", 'evidence_json'),
                         ('stdout_excerpt', 'TEXT', True, "''", 'stdout_excerpt'),
                         ('stderr_excerpt', 'TEXT', True, "''", 'stderr_excerpt'),
                         ('started_at', 'TEXT', True, 'current_timestamp', 'created_at'),
                         ('finished_at', 'TEXT', True, "''", 'finished_at'))}

RUN11_SOURCE_PROFILES = {'actual': {'codex_session_search_index': (('index',
                                            'idx_codex_session_search_source',
                                            'CREATE INDEX idx_codex_session_search_source on '
                                            'codex_session_search_index(source_type,source_id)'),
                                           ('table',
                                            'codex_session_search_index',
                                            'CREATE TABLE codex_session_search_index (\n'
                                            '    id integer primary key autoincrement,\n'
                                            '    session_id text not null unique,\n'
                                            "    source_type text not null default '',\n"
                                            "    source_id text not null default '',\n"
                                            "    title text not null default '',\n"
                                            '    created_at text not null default '
                                            'current_timestamp,\n'
                                            '    updated_at text not null default '
                                            'current_timestamp\n'
                                            ')')),
            'email_classifier_runtime_samples': (('index',
                                                  'idx_email_classifier_runtime_model_id',
                                                  'CREATE INDEX '
                                                  'idx_email_classifier_runtime_model_id\n'
                                                  '            on '
                                                  'email_classifier_runtime_samples(model_id, id '
                                                  'desc)\n'
                                                  '            '),
                                                 ('table',
                                                  'email_classifier_runtime_samples',
                                                  'CREATE TABLE email_classifier_runtime_samples '
                                                  '(\n'
                                                  '                id integer primary key '
                                                  'autoincrement,\n'
                                                  '                model_id text not null '
                                                  "check(trim(model_id) != ''),\n"
                                                  '                outcome text not null\n'
                                                  '                    check(outcome in '
                                                  "('success', 'rejected', 'failure')),\n"
                                                  '                fallback_code text not null '
                                                  "default ''\n"
                                                  '                    '
                                                  'check(length(fallback_code) <= 64),\n'
                                                  '                cache_hit integer not null '
                                                  'check(cache_hit in (0, 1)),\n'
                                                  '                runtime_warm integer not null '
                                                  'check(runtime_warm in (0, 1)),\n'
                                                  '                queue_ms real not null '
                                                  'check(queue_ms >= 0),\n'
                                                  '                http_ms real not null '
                                                  'check(http_ms >= 0),\n'
                                                  '                embedding_ms real not null '
                                                  'check(embedding_ms >= 0),\n'
                                                  '                head_ms real not null '
                                                  'check(head_ms >= 0),\n'
                                                  '                total_ms real not null '
                                                  'check(total_ms >= 0),\n'
                                                  '                recorded_at text not null '
                                                  "check(trim(recorded_at) != '')\n"
                                                  '            )')),
            'setup_wizard_events': (('index',
                                     'idx_setup_wizard_events_step',
                                     'CREATE INDEX idx_setup_wizard_events_step\n'
                                     '                    on setup_wizard_events(step_id, id)'),
                                    ('table',
                                     'setup_wizard_events',
                                     'CREATE TABLE setup_wizard_events (\n'
                                     '                    id integer primary key autoincrement,\n'
                                     '                    step_id text not null,\n'
                                     '                    action_id text not null,\n'
                                     '                    status text not null,\n'
                                     "                    summary text not null default '',\n"
                                     '                    evidence_json text not null default '
                                     "'{}',\n"
                                     '                    stdout_excerpt text not null default '
                                     "'',\n"
                                     '                    stderr_excerpt text not null default '
                                     "'',\n"
                                     '                    started_at text not null default '
                                     'current_timestamp,\n'
                                     '                    finished_at text not null default '
                                     'current_timestamp\n'
                                     '                )'))},
 'fresh': {'codex_session_search_index': (('index',
                                           'idx_codex_session_search_source',
                                           'CREATE INDEX idx_codex_session_search_source on '
                                           'codex_session_search_index(source_type,source_id)'),
                                          ('table',
                                           'codex_session_search_index',
                                           'CREATE TABLE codex_session_search_index (\n'
                                           '    id integer primary key autoincrement,\n'
                                           '    session_id text not null unique,\n'
                                           "    source_type text not null default '',\n"
                                           "    source_id text not null default '',\n"
                                           "    title text not null default '',\n"
                                           '    created_at text not null default '
                                           'current_timestamp,\n'
                                           '    updated_at text not null default '
                                           'current_timestamp\n'
                                           ')')),
           'email_classifier_runtime_samples': (('index',
                                                 'idx_email_classifier_runtime_model_id',
                                                 'CREATE INDEX '
                                                 'idx_email_classifier_runtime_model_id\n'
                                                 '            on '
                                                 'email_classifier_runtime_samples(model_id, id '
                                                 'desc)\n'
                                                 '            '),
                                                ('table',
                                                 'email_classifier_runtime_samples',
                                                 'CREATE TABLE email_classifier_runtime_samples '
                                                 '(\n'
                                                 '                id integer primary key '
                                                 'autoincrement,\n'
                                                 '                model_id text not null '
                                                 "check(trim(model_id) != ''),\n"
                                                 '                outcome text not null\n'
                                                 '                    check(outcome in '
                                                 "('success', 'rejected', 'failure')),\n"
                                                 '                fallback_code text not null '
                                                 "default ''\n"
                                                 '                    check(length(fallback_code) '
                                                 '<= 64),\n'
                                                 '                cache_hit integer not null '
                                                 'check(cache_hit in (0, 1)),\n'
                                                 '                runtime_warm integer not null '
                                                 'check(runtime_warm in (0, 1)),\n'
                                                 '                queue_ms real not null '
                                                 'check(queue_ms >= 0),\n'
                                                 '                http_ms real not null '
                                                 'check(http_ms >= 0),\n'
                                                 '                embedding_ms real not null '
                                                 'check(embedding_ms >= 0),\n'
                                                 '                head_ms real not null '
                                                 'check(head_ms >= 0),\n'
                                                 '                total_ms real not null '
                                                 'check(total_ms >= 0),\n'
                                                 '                recorded_at text not null '
                                                 "check(trim(recorded_at) != '')\n"
                                                 '            )')),
           'setup_wizard_events': (('index',
                                    'idx_setup_wizard_events_step',
                                    'CREATE INDEX idx_setup_wizard_events_step\n'
                                    '                    on setup_wizard_events(step_id, id)'),
                                   ('table',
                                    'setup_wizard_events',
                                    'CREATE TABLE setup_wizard_events (\n'
                                    '                    id integer primary key autoincrement,\n'
                                    '                    step_id text not null,\n'
                                    '                    action_id text not null,\n'
                                    '                    status text not null,\n'
                                    "                    summary text not null default '',\n"
                                    '                    evidence_json text not null default '
                                    "'{}',\n"
                                    '                    stdout_excerpt text not null default '
                                    "'',\n"
                                    '                    stderr_excerpt text not null default '
                                    "'',\n"
                                    '                    started_at text not null default '
                                    'current_timestamp,\n'
                                    "                    finished_at text not null default ''\n"
                                    '                )'))}}

RUN11_FIELDS = {
    kind: tuple(column[0] for column in columns)
    for kind, columns in RUN11_SOURCE_COLUMNS.items()
}
RUN11_COLUMN_MAP = {
    kind: {source: physical for source, _affinity, _nn, _default, physical in columns}
    for kind, columns in RUN11_SOURCE_COLUMNS.items()
}
RUN11_DEFAULT_SQL = {
    kind: {source: default for source, _affinity, _nn, default, _physical in columns
           if default is not None}
    for kind, columns in RUN11_SOURCE_COLUMNS.items()
}
RUN11_ACTUAL_DEFAULT_SQL = {
    **RUN11_DEFAULT_SQL,
    "setup_wizard_events": {
        **RUN11_DEFAULT_SQL["setup_wizard_events"], "finished_at": "current_timestamp",
    },
}
RUN11_DEFAULT_SQL_BY_PROFILE = {
    "actual": RUN11_ACTUAL_DEFAULT_SQL, "fresh": RUN11_DEFAULT_SQL,
}
_BASE_OBJECTS = _declarations(mapped=True)
RUN11_BASE_TABLE_DDL = _BASE_OBJECTS["runs"]
with closing(sqlite3.connect(":memory:")) as _reference:
    _reference.execute(RUN11_BASE_TABLE_DDL)
    RUN11_BASE_COLUMNS = tuple(row[1] for row in _reference.execute("pragma table_info(runs)"))
RUN11_ADDED_COLUMNS = tuple(dict.fromkeys(
    (physical, affinity)
    for columns in RUN11_SOURCE_COLUMNS.values()
    for _source, affinity, _nn, _default, physical in columns
    if physical not in RUN11_BASE_COLUMNS
))


def _definitions():
    old_enum = "'okr_review_runs','scheduled_task_runs','workbench_events'))"
    new_enum = old_enum[:-2] + "," + ",".join(map(repr, RUN11_NEW_KINDS)) + "))"
    table = RUN11_BASE_TABLE_DDL.replace(old_enum, new_enum, 1)
    # Old 62 column declarations and every existing constraint remain byte exact.
    marker = "    foreign key(scheduled_task_id) references schedules(id),"
    table = table.replace(marker, "    " + ",\n    ".join(
        name + " " + affinity.lower() for name, affinity in RUN11_ADDED_COLUMNS
    ) + ",\n" + marker, 1)
    constraints = []
    structural = {"id", "record_type", "legacy_key"}
    all_columns = set(RUN11_BASE_COLUMNS) | {name for name, _ in RUN11_ADDED_COLUMNS}
    for kind, columns in RUN11_SOURCE_COLUMNS.items():
        fields = {physical for _s, _a, _nn, _d, physical in columns}
        checks = [physical + " is not null" for _s, _a, nn, _d, physical in columns if nn]
        checks += [
            "legacy_key is not null",
            "legacy_key=cast(cast(legacy_key as integer) as text)",
        ]
        if kind == "email_classifier_runtime_samples":
            checks += ["trim(model_id)<>''", "outcome in ('success','rejected','failure')",
                       "length(fallback_code)<=64", "cache_hit in (0,1)",
                       "runtime_warm in (0,1)", "trim(created_at)<>''"]
            checks += [name + ">=0" for name in
                       ("queue_ms", "http_ms", "embedding_ms", "head_ms", "total_ms")]
        checks += [name + " is null" for name in sorted(all_columns - fields - structural)]
        constraints.append("check(record_type<>" + repr(kind) + " or ("
                           + " and ".join(checks) + "))")
    constraints.append("check(record_type in (" + ",".join(map(repr, RUN11_NEW_KINDS))
                       + ") or (" + " and ".join(name + " is null" for name, _a in
                                                  RUN11_ADDED_COLUMNS) + "))")
    table = table.removesuffix(")") + "," + ",".join(constraints) + ")"
    indexes = [sql for sql in _BASE_OBJECTS.values() if sql.startswith("CREATE INDEX ")
               or sql.startswith("CREATE UNIQUE INDEX ")]
    indexes += [
        "create unique index idx_runs11_metadata_session on runs(session_id) where record_type='codex_session_search_index'",
        "create index idx_runs11_metadata_source on runs(source_type,source_id) where record_type='codex_session_search_index'",
        "create index idx_runs11_metadata_id on runs(cast(legacy_key as integer) desc,record_type) where record_type='codex_session_search_index'",
        "create index idx_runs11_sample_model_id on runs(model_id,cast(legacy_key as integer) desc) where record_type='email_classifier_runtime_samples'",
        "create index idx_runs11_sample_id on runs(cast(legacy_key as integer) desc) where record_type='email_classifier_runtime_samples'",
        "create index idx_runs11_setup_step_id on runs(step_id,cast(legacy_key as integer)) where record_type='setup_wizard_events'",
        "create index idx_runs11_setup_id on runs(cast(legacy_key as integer) desc) where record_type='setup_wizard_events'",
    ]
    triggers = [sql for sql in _BASE_OBJECTS.values() if sql.startswith("CREATE TRIGGER ")]
    high = ("max(coalesce((select seq from sqlite_sequence where name='runs'),0),"
            "coalesce((select max(id) from runs),0),0)")
    allocated = ("case when new.id<>-1 then new.id when " + high
                 + "<9223372036854775807 then " + high + "+1 else 0 end")
    triggers.append(
        "create trigger trg_runs8_sequence before insert on runs when new.record_type not in ("
        + ",".join(map(repr, RUN11_NEW_KINDS)) + ") begin "
        "insert into sqlite_sequence(name,seq) select 'runs:published',0 "
        "where not exists(select 1 from sqlite_sequence where name='runs:published'); "
        "update sqlite_sequence set seq=max(seq," + allocated + ") "
        "where name='runs:published'; end"
    )
    for kind in RUN11_NEW_KINDS:
        name = repr("runs:" + kind)
        triggers.append(
            "create trigger trg_runs11_sequence_" + kind + " before insert on runs "
            "when new.record_type=" + repr(kind) + " begin "
            "insert into sqlite_sequence(name,seq) select " + name + ",0 "
            "where not exists(select 1 from sqlite_sequence where name=" + name + "); "
            "update sqlite_sequence set seq=max(seq,0,cast(new.legacy_key as integer)) "
            "where name=" + name + "; end"
        )
    return table, tuple(indexes), tuple(triggers)


RUN11_TABLE_DDL, RUN11_INDEX_DDL, RUN11_TRIGGER_DDL = _definitions()


def create_runs11_schema(db: sqlite3.Connection) -> None:
    """Install the exact current target; never select callers or commit."""
    for sql in (RUN11_TABLE_DDL, *RUN11_INDEX_DDL, *RUN11_TRIGGER_DDL):
        db.execute(sql)


def run11_projection(kind: str) -> str:
    """Project exact native source column names/order and public integer ID."""
    fields = []
    for source, _affinity, _nn, _default, physical in RUN11_SOURCE_COLUMNS[kind]:
        expression = "cast(legacy_key as integer)" if source == "id" else physical
        fields.append(expression + " as " + source if expression != source else source)
    return "select " + ",".join(fields) + " from runs where record_type=" + repr(kind)


def initialize_run11_sequences(db: sqlite3.Connection, waters: dict[str, int | None]) -> None:
    """Initialize current source streams after bulk transfer; preserve absent vs0.

    Input keys are runs plus each native source kind. Read the current values at
    conversion, never at preparation. This changes only prefix streams, leaving
    the current physical runs counter to its native allocator and migration.
    """
    if not db.in_transaction:
        raise ValueError("Runs allocator initialization requires a caller transaction")
    for source in ("runs", *RUN11_NEW_KINDS):
        name = "runs:published" if source == "runs" else "runs:" + source
        db.execute("delete from main.sqlite_sequence where name=?", (name,))
        if waters[source] is not None:
            db.execute("insert into main.sqlite_sequence(name,seq) values (?,?)",
                       (name, waters[source]))
