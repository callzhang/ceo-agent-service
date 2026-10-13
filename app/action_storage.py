"""Finite typed storage for the six existing candidate/review/execution facts.

Public IDs keep each source table's INTEGER PRIMARY KEY namespace. Only physical
IDs participate in FKs. Migration callers own an FK-off transaction; inverse
restoration reads current rows, never a captured copy of business data.
"""

from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
import json
import sqlite3
from string import ascii_lowercase, ascii_uppercase


_SCHEMA_NAME_CASE = str.maketrans(ascii_uppercase, ascii_lowercase)


def sqlite_schema_name(name: str) -> str:
    """Match SQLite identifier NOCASE rules without folding Unicode or data."""
    return name.translate(_SCHEMA_NAME_CASE)


# Frozen from the 2026-10-13 source inventory, including the actual spelling of
# each declaration. This is the supported source and paired-inverse contract.
SOURCE_ACTION_DDL = """
create table review_candidates (
 id integer primary key, task_id integer not null references tasks(id),
 execution_generation text not null, consumer_run_id integer not null unique references runs(id),
 stage_index integer not null, proposal_revision integer not null,
 predecessor_review_id integer references candidate_reviews(id),
 candidate_digest text not null, candidate_json text not null,
 invalidated_at text not null default '', invalidation_reason text not null default '',
 created_at text not null default current_timestamp
);
create index idx_review_candidates_task on review_candidates(task_id,execution_generation,id);
create table candidate_reviews (
 id integer primary key, candidate_id integer not null references review_candidates(id),
 audit_run_id integer not null unique references runs(id),
 decision text not null check(decision in ('approve','return','reject')),
 candidate_digest text not null, result_json text not null,
 created_at text not null default current_timestamp
);
create index idx_candidate_reviews_candidate on candidate_reviews(candidate_id,id);
create table candidate_selections (
 id integer primary key, candidate_id integer not null unique references review_candidates(id),
 review_id integer not null references candidate_reviews(id), option_key text not null,
 branch_json text not null, status text not null default 'selected',
 created_at text not null default current_timestamp
);
create table candidate_supplements (
 id integer primary key, candidate_id integer not null references review_candidates(id),
 instruction text not null, created_at text not null default current_timestamp
);
create table candidate_executions (
 id integer primary key, candidate_id integer not null unique references review_candidates(id),
 review_id integer not null references candidate_reviews(id), selection_id integer references candidate_selections(id),
 status text not null check(status in ('running','done','skipped','failed','retry','uncertain')),
 lease_owner text not null default '', lease_expires_at text not null default '',
 result_json text not null default '{}', created_at text not null default current_timestamp,
 updated_at text not null default current_timestamp
);
create table candidate_action_attempts (
 id integer primary key, execution_id integer not null references candidate_executions(id),
 action_index integer not null, external_action_key text not null,
 status text not null check(status in ('dispatched','verified','confirmed_no_effect','uncertain','failed')),
 result_json text not null default '{}', created_at text not null default current_timestamp,
 updated_at text not null default current_timestamp,
 unique(execution_id,action_index)
);
create index idx_candidate_action_key on candidate_action_attempts(external_action_key);
"""

SOURCE_COLUMNS = {
    "review_candidates": ("id", "task_id", "execution_generation", "consumer_run_id", "stage_index", "proposal_revision", "predecessor_review_id", "candidate_digest", "candidate_json", "invalidated_at", "invalidation_reason", "created_at"),
    "candidate_reviews": ("id", "candidate_id", "audit_run_id", "decision", "candidate_digest", "result_json", "created_at"),
    "candidate_selections": ("id", "candidate_id", "review_id", "option_key", "branch_json", "status", "created_at"),
    "candidate_supplements": ("id", "candidate_id", "instruction", "created_at"),
    "candidate_executions": ("id", "candidate_id", "review_id", "selection_id", "status", "lease_owner", "lease_expires_at", "result_json", "created_at", "updated_at"),
    "candidate_action_attempts": ("id", "execution_id", "action_index", "external_action_key", "status", "result_json", "created_at", "updated_at"),
}
ACTION_TYPES = tuple(SOURCE_COLUMNS)
# source field -> (physical table, exact parent record_type)
REFERENCES = {
    "task_id": ("tasks", "reply_tasks"),
    "consumer_run_id": ("runs", "agent_runs"),
    "audit_run_id": ("runs", "agent_runs"),
    "predecessor_review_id": ("actions", "candidate_reviews"),
    "candidate_id": ("actions", "review_candidates"),
    "review_id": ("actions", "candidate_reviews"),
    "selection_id": ("actions", "candidate_selections"),
    "execution_id": ("actions", "candidate_executions"),
}
NULLABLE = {"predecessor_review_id", "selection_id"}
INTEGER_FIELDS = {"stage_index", "proposal_revision", "action_index"}
PAYLOAD_FIELDS = {
    kind: tuple(c for c in columns if c != "id" and c not in REFERENCES)
    for kind, columns in SOURCE_COLUMNS.items()
}
ENUMS = {
    "candidate_reviews": ("decision", ("approve", "return", "reject")),
    "candidate_executions": ("status", ("running", "done", "skipped", "failed", "retry", "uncertain")),
    "candidate_action_attempts": ("status", ("dispatched", "verified", "confirmed_no_effect", "uncertain", "failed")),
}
DEFAULTS = {
    "review_candidates": {"invalidated_at": "", "invalidation_reason": ""},
    "candidate_reviews": {},
    "candidate_selections": {"status": "selected"},
    "candidate_supplements": {},
    "candidate_executions": {"lease_owner": "", "lease_expires_at": "", "result_json": "{}"},
    "candidate_action_attempts": {"result_json": "{}"},
}


def _objects(db, table):
    return [dict(zip(("type", "name", "sql"), row, strict=True)) for row in db.execute(
        "select type,name,sql from sqlite_master where tbl_name=? collate nocase and sql is not null order by type,name", (table,)
    )]


@lru_cache(maxsize=1)
def _source_schema():
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(SOURCE_ACTION_DDL)
        for table, column, parent, label in (
            ("review_candidates", "task_id", "tasks", "task parent must be a reply task"),
            ("review_candidates", "consumer_run_id", "runs", "run parent must be a logical agent run"),
            ("candidate_reviews", "audit_run_id", "runs", "run parent must be a logical agent run"),
        ):
            kind = "reply_tasks" if parent == "tasks" else "agent_runs"
            for event, suffix in (("insert", "insert"), (f'update of "{column}"', "update")):
                db.execute(f'create trigger trg_{parent}_child_{table}_{column}_{suffix} before {event} on "{table}" when new."{column}" is not null and exists(select 1 from {parent} where id=new."{column}" and record_type<>\'{kind}\') begin select raise(abort,\'{label}\'); end')
        return {kind: {"columns": list(SOURCE_COLUMNS[kind]), "objects": _objects(db, kind)} for kind in ACTION_TYPES}


def _payload_check(kind):
    terms = [f"json_type(payload_json,'$.{field}') is '{'integer' if field in INTEGER_FIELDS else 'text'}'" for field in PAYLOAD_FIELDS[kind]]
    paths = ",".join(repr("$." + field) for field in PAYLOAD_FIELDS[kind])
    terms.append(f"json_remove(payload_json,{paths})='{{}}'")
    for field in REFERENCES:
        if field not in SOURCE_COLUMNS[kind]:
            terms.append(f"{field} is null")
        elif field in NULLABLE:
            terms.append(f"{field} is null or typeof({field})='integer'")
        else:
            terms.append(f"{field} is not null and typeof({field})='integer'")
    return f"check(record_type<>'{kind}' or ({' and '.join('(' + t + ')' for t in terms)}))"


ACTION_TABLE_DDL = (
    "create table actions (id integer primary key, record_type text not null check(record_type in ("
    + ",".join(repr(k) for k in ACTION_TYPES)
    + ")),legacy_key text not null check(legacy_key=cast(cast(legacy_key as integer) as text)),"
    + ",".join(f"{column} integer references {parent}(id)" for column, (parent, _) in REFERENCES.items())
    + ",payload_json text not null check(json_valid(payload_json) and json_type(payload_json)='object'),"
    + ",".join(f"{column} {'integer' if column in INTEGER_FIELDS else 'text'} generated always as (json_extract(payload_json,'$.{column}')) virtual" for column in dict.fromkeys(c for fields in PAYLOAD_FIELDS.values() for c in fields))
    + "," + ",".join(_payload_check(kind) for kind in ACTION_TYPES)
    + "," + ",".join(f"check(record_type<>'{kind}' or {field} in ({','.join(repr(v) for v in options)}))" for kind, (field, options) in ENUMS.items())
    + ")"
)
ACTION_INDEX_DDL = (
    "create unique index idx_actions_public_id on actions(cast(legacy_key as integer),record_type)",
    "create unique index idx_actions_consumer_run on actions(consumer_run_id) where record_type='review_candidates'",
    "create index idx_actions_candidates_task on actions(task_id,execution_generation,cast(legacy_key as integer)) where record_type='review_candidates'",
    "create unique index idx_actions_audit_run on actions(audit_run_id) where record_type='candidate_reviews'",
    "create index idx_actions_reviews_candidate on actions(candidate_id,cast(legacy_key as integer)) where record_type='candidate_reviews'",
    "create unique index idx_actions_selection_candidate on actions(candidate_id) where record_type='candidate_selections'",
    "create unique index idx_actions_execution_candidate on actions(candidate_id) where record_type='candidate_executions'",
    "create unique index idx_actions_attempt_sequence on actions(execution_id,action_index) where record_type='candidate_action_attempts'",
    "create index idx_actions_external_key on actions(external_action_key) where record_type='candidate_action_attempts'",
)
ACTION_TRIGGER_DDL = (
    "create trigger trg_actions_record_type_immutable before update of record_type on actions when new.record_type<>old.record_type begin select raise(abort,'action record type is immutable'); end",
    "create trigger trg_actions_public_reference_update before update of legacy_key on actions when new.legacy_key<>old.legacy_key and exists(select 1 from actions where predecessor_review_id=old.id or candidate_id=old.id or review_id=old.id or selection_id=old.id or execution_id=old.id) begin select raise(abort,'FOREIGN KEY constraint failed'); end",
    *tuple(
        f"create trigger trg_actions_{field}_{suffix} before {event} on actions when new.{field} is not null and exists(select 1 from {parent} where id=new.{field} and record_type<>'{kind}') begin select raise(abort,'action parent type mismatch'); end"
        for field, (parent, kind) in REFERENCES.items()
        for event, suffix in (("insert", "insert"), (f"update of {field}", "update"))
    ),
)


def _projection(kind):
    fields, joins = [], []
    for field in SOURCE_COLUMNS[kind]:
        if field == "id":
            expression = "cast(a.legacy_key as integer)"
        elif field in REFERENCES and REFERENCES[field][0] == "actions":
            alias = "p_" + field
            expression = f"cast({alias}.legacy_key as integer)"
            join = "left join" if field in NULLABLE else "join"
            joins.append(f"{join} actions {alias} on {alias}.id=a.{field} and {alias}.record_type='{REFERENCES[field][1]}'")
        else:
            # The existing logical reply-task and agent-run core schemas enforce
            # public ID == physical ID. Their source FK scalars remain exact.
            expression = "a." + field
        fields.append(expression + " as " + field)
    return f"select {','.join(fields)} from actions a {' '.join(joins)} where a.record_type='{kind}'"


ACTION_PROJECTIONS = {kind: _projection(kind) for kind in ACTION_TYPES}
REVIEW_CANDIDATE_SQL = ACTION_PROJECTIONS["review_candidates"]
CANDIDATE_REVIEW_SQL = ACTION_PROJECTIONS["candidate_reviews"]
CANDIDATE_SELECTION_SQL = ACTION_PROJECTIONS["candidate_selections"]
CANDIDATE_SUPPLEMENT_SQL = ACTION_PROJECTIONS["candidate_supplements"]
CANDIDATE_EXECUTION_SQL = ACTION_PROJECTIONS["candidate_executions"]
CANDIDATE_ACTION_ATTEMPT_SQL = ACTION_PROJECTIONS["candidate_action_attempts"]


def create_action_schema(db: sqlite3.Connection) -> None:
    """Create the declared schema, or repair only its missing auxiliary objects."""
    existing = db.execute("select 1 from sqlite_master where name='actions' collate nocase").fetchone()
    if existing:
        if not action_schema_is_current(db, allow_missing_objects=True):
            raise ValueError("unsupported current actions schema declaration")
        names = {sqlite_schema_name(obj["name"]) for obj in _objects(db, "actions")}
        for obj in _current_objects():
            if sqlite_schema_name(obj["name"]) not in names:
                db.execute(obj["sql"])
    else:
        if any(db.execute("select 1 from sqlite_master where name=? collate nocase", (kind,)).fetchone() for kind in ACTION_TYPES):
            capture_action_schema(db)
        _require_available_names(db, _current_objects())
        _create_action_objects(db)


def _create_action_objects(db):
    for sql in (ACTION_TABLE_DDL, *ACTION_INDEX_DDL, *ACTION_TRIGGER_DDL):
        db.execute(sql)


@lru_cache(maxsize=1)
def _current_objects():
    with closing(sqlite3.connect(":memory:")) as db:
        _create_action_objects(db)
        return _objects(db, "actions")


def action_schema_is_current(db: sqlite3.Connection, *, allow_missing_objects=False) -> bool:
    if any(db.execute("select 1 from sqlite_master where name=? collate nocase", (kind,)).fetchone() for kind in ACTION_TYPES):
        return False
    actual = {sqlite_schema_name(o["name"]): o for o in _objects(db, "actions")}
    expected = {sqlite_schema_name(o["name"]): o for o in _current_objects()}
    if any(db.execute("select 1 from sqlite_master where name=? collate nocase and tbl_name<>'actions' collate nocase", (name,)).fetchone() for name in expected):
        return False
    return not (actual.keys() - expected.keys()) and all(
        actual.get(name) == obj or (allow_missing_objects and name != "actions" and name not in actual)
        for name, obj in expected.items()
    )


def _require_kind(kind):
    if kind not in SOURCE_COLUMNS:
        raise ValueError("unknown action record type")


def action_physical_id(db: sqlite3.Connection, record_type: str, public_id: int | None) -> int | None:
    _require_kind(record_type)
    if public_id is None:
        return None
    row = db.execute("select id from actions where record_type=? and cast(legacy_key as integer)=?", (record_type, public_id)).fetchone()
    if row is None:
        raise sqlite3.IntegrityError(f"missing {record_type} public ID {public_id}")
    return row[0]


def _parent_id(db, field, public_id):
    if public_id is None:
        return None
    table, kind = REFERENCES[field]
    if table == "actions":
        return action_physical_id(db, kind, public_id)
    row = db.execute(f"select id from {table} where record_type=? and cast(legacy_key as integer)=?", (kind, public_id)).fetchone()
    if row is None:
        raise sqlite3.IntegrityError(f"missing {kind} public ID {public_id}")
    return row[0]


def _check_fields(kind, values):
    _require_kind(kind)
    if set(values) - set(SOURCE_COLUMNS[kind]):
        raise ValueError(f"unknown {kind} source fields")
    for field, value in values.items():
        if value is not None and type(value) is not (int if field == "id" or field in REFERENCES or field in INTEGER_FIELDS else str):
            raise ValueError(f"unrepresentable {kind}.{field} storage type")
        if type(value) is int and not -(2**63) <= value < 2**63:
            raise ValueError(f"unrepresentable {kind}.{field} integer")


@dataclass(frozen=True)
class ActionWriteResult:
    lastrowid: int | None
    rowcount: int


def insert_action(db: sqlite3.Connection, record_type: str, values: dict, *, ignore: bool = False) -> ActionWriteResult:
    """Insert source-shaped values; return the original per-type public ID."""
    _check_fields(record_type, values)
    now = db.execute("select current_timestamp").fetchone()[0]
    payload = DEFAULTS[record_type] | {"created_at": now}
    if "updated_at" in SOURCE_COLUMNS[record_type]:
        payload["updated_at"] = now
    payload.update({field: value for field, value in values.items() if field in PAYLOAD_FIELDS[record_type]})
    fields = [field for field in REFERENCES if field in SOURCE_COLUMNS[record_type]]
    arguments = [record_type, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))]
    arguments += [_parent_id(db, field, values.get(field)) for field in fields]
    public_id = values.get("id")
    if public_id is None and db.execute("select 1 from actions where record_type=? and legacy_key='9223372036854775807'", (record_type,)).fetchone():
        # Preserve SQLite's own random-rowid algorithm at INTEGER's upper bound.
        with closing(sqlite3.connect(":memory:")) as allocator:
            allocator.execute("create table ids(id integer primary key)")
            allocator.executemany("insert into ids values(?)", db.execute("select cast(legacy_key as integer) from actions where record_type=?", (record_type,)))
            public_id = allocator.execute("insert into ids default values").lastrowid
    if public_id is None:
        identity = "cast(coalesce((select max(cast(legacy_key as integer))+1 from actions where record_type=?),1) as text)"
        arguments.append(record_type)
    else:
        identity = "?"
        arguments.append(str(public_id))
    sql = f"insert {'or ignore ' if ignore else ''}into actions(record_type,payload_json,{','.join(fields)},legacy_key) values({','.join('?' for _ in range(2 + len(fields)))},{identity}) returning cast(legacy_key as integer)"
    cursor = db.execute(sql, arguments)
    result = cursor.fetchone()
    cursor.fetchall()
    return ActionWriteResult(result[0] if result else None, cursor.rowcount)


def update_action(db: sqlite3.Connection, record_type: str, public_id: int, values: dict, *, where_sql: str = "", parameters=()) -> sqlite3.Cursor:
    """Update explicit source fields; an optional predicate uses the projection."""
    _check_fields(record_type, values)
    assignments, args, paths = [], [], []
    for field, value in values.items():
        if field in REFERENCES or field == "id":
            assignments.append(("legacy_key" if field == "id" else field) + "=?")
            args.append(str(value) if field == "id" else _parent_id(db, field, value))
        else:
            paths.extend(("$." + field, value))
    if paths:
        assignments.append("payload_json=json_set(payload_json," + ",".join("?" for _ in paths) + ")")
        args.extend(paths)
    if not assignments:
        raise ValueError("action update requires fields")
    predicate = "record_type=? and cast(legacy_key as integer)=?"
    args.extend((record_type, public_id))
    if where_sql:
        predicate += f" and cast(legacy_key as integer) in (select id from ({ACTION_PROJECTIONS[record_type]}) where id=? and ({where_sql}))"
        args.extend((public_id, *parameters))
    return db.execute(f"update actions set {','.join(assignments)} where {predicate}", args)


def delete_action(db: sqlite3.Connection, record_type: str, public_id: int) -> sqlite3.Cursor:
    _require_kind(record_type)
    return db.execute("delete from actions where record_type=? and cast(legacy_key as integer)=?", (record_type, public_id))


def _no_inbound(db, target_tables):
    target_names = {sqlite_schema_name(table) for table in target_tables}
    for (table,) in db.execute("select name from sqlite_master where type='table'").fetchall():
        if sqlite_schema_name(table) in target_names:
            continue
        quoted = table.replace('"', '""')
        if any(sqlite_schema_name(fk[2]) in target_names for fk in db.execute(f'pragma foreign_key_list("{quoted}")')):
            raise ValueError(f"unsupported inbound action source reference from {table}")


def _no_sql_dependencies(db, target_tables):
    """Use SQLite's rename parser on schema-only scratch data, never live DDL.

    SQLite rewrites actual table references in views and trigger bodies during
    RENAME TABLE. Comparing the external declarations detects read/write and
    quoted/indirect references without token matching or executing any body.
    Virtual tables need only their declared column shape for this parse; no
    virtual module runs or implicitly creates duplicate shadow tables.
    """
    objects = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from sqlite_master where type in ('table','view','trigger') and sql is not null and name not glob 'sqlite_*' order by case type when 'table' then 0 when 'view' then 1 else 2 end,name"
    )]
    target_names = {sqlite_schema_name(table) for table in target_tables}
    external = {(kind, sqlite_schema_name(name)): sql for kind, name, table, sql in objects if kind in ("view", "trigger") and sqlite_schema_name(table) not in target_names}
    if not external:
        return
    virtual = {sqlite_schema_name(row[1]) for row in db.execute("pragma table_list") if row[0] == "main" and row[2] == "virtual"}
    with closing(sqlite3.connect(":memory:")) as parser:
        for kind, name, _, sql in objects:
            if kind == "table" and sqlite_schema_name(name) in virtual:
                quoted = '"' + name.replace('"', '""') + '"'
                columns = [row[1] for row in db.execute(f"pragma table_xinfo({quoted})")]
                sql = f"create table {quoted} (" + ",".join('"' + c.replace('"', '""') + '"' for c in columns) + ")"
            parser.execute(sql)
        names = {sqlite_schema_name(row[1]) for row in objects}
        for table in sorted(target_tables):
            replacement = "_action_dependency_" + table
            while sqlite_schema_name(replacement) in names:
                replacement += "_"
            names.add(sqlite_schema_name(replacement))
            quoted = '"' + table.replace('"', '""') + '"'
            parser.execute(f'alter table {quoted} rename to "{replacement}"')
            for kind, name, sql in parser.execute("select type,name,sql from sqlite_master where type in ('view','trigger')"):
                key = (kind, sqlite_schema_name(name))
                if key in external and sql != external[key]:
                    raise ValueError(f"unsupported action SQL dependency: {name} references {table}")


def _require_available_names(db, objects):
    for obj in objects:
        if db.execute("select 1 from sqlite_master where name=? collate nocase", (obj["name"],)).fetchone():
            raise ValueError(f"unsupported action source declaration name collision: {obj['name']}")


def capture_action_schema(db: sqlite3.Connection) -> dict:
    """Capture exact declarations only; refuse unsupported sources read-only."""
    if db.execute("select 1 from sqlite_master where name='actions' collate nocase").fetchone():
        raise ValueError("unsupported action source: actions already exists")
    tables = {kind: {"columns": [r[1] for r in db.execute(f"pragma table_info({kind})")], "objects": _objects(db, kind)} for kind in ACTION_TYPES}
    if tables != _source_schema():
        raise ValueError("unsupported action source declaration")
    _require_available_names(db, _current_objects())
    _no_inbound(db, set(ACTION_TYPES))
    _no_sql_dependencies(db, set(ACTION_TYPES))
    return {"tables": tables}


def _require_transaction(db):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("action schema transition requires caller-owned FK-off transaction")


def _source_rows(db):
    rows = {kind: [dict(zip(SOURCE_COLUMNS[kind], row, strict=True)) for row in db.execute(f"select * from {kind} order by id")] for kind in ACTION_TYPES}
    for kind, values in rows.items():
        for row in values:
            _check_fields(kind, row)
            for field, value in row.items():
                if value is None and field not in NULLABLE:
                    raise ValueError(f"invalid source null {kind}.{field}")
            if kind in ENUMS:
                field, options = ENUMS[kind]
                if row[field] not in options:
                    raise ValueError(f"invalid source enum {kind}.{field}")
        if db.execute(f"pragma foreign_key_check({kind})").fetchone():
            raise ValueError(f"invalid source reference in {kind}")
    for kind in ACTION_TYPES:
        for field, (parent, parent_kind) in REFERENCES.items():
            if field in SOURCE_COLUMNS[kind] and parent != "actions" and db.execute(f"select 1 from {kind} s join {parent} p on p.id=s.{field} where p.record_type<>? limit 1", (parent_kind,)).fetchone():
                raise ValueError(f"invalid source parent type {kind}.{field}")
    return rows


def migrate_action_schema(db: sqlite3.Connection) -> dict:
    """Convert all six supported source tables; caller commits or rolls back."""
    _require_transaction(db)
    capture_action_schema(db)
    rows = _source_rows(db)
    mapping = {(kind, row["id"]): number for number, (kind, row) in enumerate(((kind, row) for kind in ACTION_TYPES for row in rows[kind]), 1)}
    _create_action_objects(db)
    for kind, records in rows.items():
        for row in records:
            values = {"id": mapping[kind, row["id"]], "record_type": kind, "legacy_key": str(row["id"])}
            values["payload_json"] = json.dumps({field: row[field] for field in PAYLOAD_FIELDS[kind]}, ensure_ascii=False, separators=(",", ":"))
            for field, (parent, parent_kind) in REFERENCES.items():
                if field in row:
                    values[field] = mapping[parent_kind, row[field]] if parent == "actions" and row[field] is not None else row[field]
            db.execute(f"insert into actions({','.join(values)}) values({','.join('?' for _ in values)})", tuple(values.values()))
    for kind in ACTION_TYPES:
        db.execute(f"drop table {kind}")
    return {"counts": {kind: len(records) for kind, records in rows.items()}}


def restore_action_schema(db: sqlite3.Connection, state: dict) -> dict:
    """Recreate original declarations using all current facts and public IDs."""
    _require_transaction(db)
    if state != {"tables": _source_schema()}:
        raise ValueError("unsupported action inverse source metadata")
    if not action_schema_is_current(db):
        raise ValueError("unsupported current actions schema")
    _require_available_names(db, [obj for schema in state["tables"].values() for obj in schema["objects"]])
    _no_inbound(db, {"actions"})
    _no_sql_dependencies(db, {"actions"})
    if db.execute("pragma foreign_key_check(actions)").fetchone():
        raise ValueError("invalid current actions reference")
    for field, (parent, kind) in REFERENCES.items():
        if db.execute(f"select 1 from actions a join {parent} p on p.id=a.{field} where p.record_type<>? limit 1", (kind,)).fetchone():
            raise ValueError(f"invalid current actions parent type {field}")
    check = db.execute("pragma integrity_check(actions)").fetchall()
    if [row[0] for row in check] != ["ok"]:
        raise ValueError("invalid current actions rows")
    rows = {kind: [tuple(row) for row in db.execute(ACTION_PROJECTIONS[kind])] for kind in ACTION_TYPES}
    for kind, schema in state["tables"].items():
        db.execute(next(obj["sql"] for obj in schema["objects"] if obj["type"] == "table"))
        db.executemany(f"insert into {kind}({','.join(schema['columns'])}) values({','.join('?' for _ in schema['columns'])})", rows[kind])
    for schema in state["tables"].values():
        for obj in schema["objects"]:
            if obj["type"] != "table":
                db.execute(obj["sql"])
    db.execute("drop table actions")
    return {"counts": {kind: len(records) for kind, records in rows.items()}}
