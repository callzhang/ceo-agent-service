"""Physical core run storage and exact, paired source-schema conversion.

Public numeric IDs remain scoped by record_type. Native trajectories and adopted
business results stay in their existing sources. The caller owns the transaction
and disables foreign keys while rebuilding declared FK children.
"""

import re
import sqlite3

RUN_TYPES = ("agent_runs", "agent_runtime_attempts", "agent_run_state_events")
RETIRED_RUN_TABLES = (*RUN_TYPES, "agent_run_events")
COLUMN_MAP = {
    "agent_runs": {
        "reply_task_id": "task_id",
        "parent_agent_run_id": "parent_id",
        "codex_session_id": "session_id",
        "transcript_start_line": "transcript_start",
        "transcript_end_line": "transcript_end",
    },
    "agent_runtime_attempts": {"agent_run_id": "parent_id"},
    "agent_run_state_events": {"agent_run_id": "parent_id"},
}
SOURCE_COLUMNS = {
    "agent_runs": (
        "id",
        "reply_task_id",
        "execution_generation",
        "role",
        "proposal_revision",
        "turn_attempt",
        "parent_agent_run_id",
        "operation_id",
        "status",
        "codex_session_id",
        "transcript_start_line",
        "transcript_end_line",
        "final_result_json",
        "structured_error_json",
        "lease_owner",
        "lease_expires_at",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    ),
    "agent_runtime_attempts": (
        "id",
        "agent_run_id",
        "workload_kind",
        "workload_key",
        "attempt_number",
        "route_name",
        "runtime_kind",
        "credential_mode",
        "model",
        "session_mode",
        "source_session_id",
        "attempt_purpose",
        "validation_retry_policy_id",
        "validation_result_schema_id",
        "session_id",
        "status",
        "failure_class",
        "failure_code",
        "failover_permitted",
        "transcript_reference",
        "transcript_start",
        "transcript_end",
        "first_effect_started_at",
        "lease_owner",
        "lease_expires_at",
        "result_schema_id",
        "result_envelope_json",
        "started_at",
        "finished_at",
        "created_at",
        "updated_at",
    ),
    "agent_run_state_events": (
        "id",
        "agent_run_id",
        "phase",
        "structured_error_json",
        "created_at",
    ),
}
RETIRED_COLUMNS = {
    "final_result_json": "",
    "result_envelope_json": "",
    "tool_events_json": "[]",
}


def _projection(record_type: str) -> str:
    fields = []
    for column in SOURCE_COLUMNS[record_type]:
        if column == "id":
            fields.append(
                "id"
                if record_type == "agent_runs"
                else "cast(legacy_key as integer) as id"
            )
        elif column in RETIRED_COLUMNS:
            fields.append(f"'{RETIRED_COLUMNS[column]}' as {column}")
        else:
            physical = COLUMN_MAP[record_type].get(column, column)
            fields.append(f"{physical} as {column}" if physical != column else column)
    return f"select {', '.join(fields)} from runs where record_type='{record_type}'"


AGENT_RUN_SQL = _projection("agent_runs")
RUNTIME_ATTEMPT_SQL = _projection("agent_runtime_attempts")
RUN_STATE_EVENT_SQL = _projection("agent_run_state_events")
RUN_PROJECTIONS = dict(
    zip(
        RUN_TYPES,
        (AGENT_RUN_SQL, RUNTIME_ATTEMPT_SQL, RUN_STATE_EVENT_SQL),
        strict=True,
    )
)

RUN_TABLE_DDL = """
create table runs (
    id integer primary key autoincrement,
    record_type text not null check(record_type in ('agent_runs','agent_runtime_attempts','agent_run_state_events')),
    legacy_key text,
    task_id integer,
    parent_id integer,
    execution_generation text,
    role text,
    proposal_revision integer,
    turn_attempt integer,
    operation_id text,
    status text,
    session_id text,
    transcript_start integer,
    transcript_end integer,
    structured_error_json text,
    lease_owner text,
    lease_expires_at text,
    started_at text,
    completed_at text,
    finished_at text,
    created_at text not null default current_timestamp,
    updated_at text,
    workload_kind text,
    workload_key text,
    attempt_number integer,
    route_name text,
    runtime_kind text,
    credential_mode text,
    model text,
    session_mode text,
    source_session_id text,
    attempt_purpose text,
    validation_retry_policy_id text,
    validation_result_schema_id text,
    failure_class text,
    failure_code text,
    failover_permitted integer,
    transcript_reference text,
    first_effect_started_at text,
    result_schema_id text,
    phase text,
    unique(record_type, legacy_key),
    check(record_type<>'agent_runs' or legacy_key is null or legacy_key=cast(id as text)),
    foreign key(task_id) references reply_tasks(id),
    foreign key(parent_id) references runs(id),
    check(record_type<>'agent_runs' or (
        task_id is not null and execution_generation is not null
        and role is not null and role in ('consumer','audit')
        and proposal_revision is not null and proposal_revision>=0
        and turn_attempt is not null and turn_attempt>=0
        and status is not null and status in ('pending','running','completed','failed'))),
    check(record_type<>'agent_runtime_attempts' or (
        workload_kind is not null and workload_key is not null
        and attempt_number is not null and attempt_number>0
        and route_name is not null and runtime_kind is not null
        and credential_mode is not null and model is not null
        and status is not null and status in ('starting','running','completed','failed','superseded')
        and ((workload_kind='agent_run' and parent_id is not null and workload_key=cast(parent_id as text))
             or (workload_kind<>'agent_run' and parent_id is null))
        and ((session_mode='fresh' and source_session_id='')
             or (session_mode='resume' and trim(source_session_id)<>'')))),
    check(record_type<>'agent_run_state_events' or (parent_id is not null and phase is not null))
)
"""
RUN_REQUIRED_FIELDS = {
    record_type: tuple(
        COLUMN_MAP[record_type].get(column, column)
        for column in columns
        if column not in RETIRED_COLUMNS
        and column != "id"
        and not (record_type == "agent_runs" and column == "parent_agent_run_id")
        and not (record_type == "agent_runtime_attempts" and column == "agent_run_id")
    )
    for record_type, columns in SOURCE_COLUMNS.items()
}
RUN_TABLE_DDL = (
    RUN_TABLE_DDL.rstrip().removesuffix(")")
    + ","
    + ",".join(
        f"check(record_type<>'{record_type}' or ({' and '.join(field + ' is not null' for field in fields)}))"
        for record_type, fields in RUN_REQUIRED_FIELDS.items()
    )
    + ")"
)

# These are source schema defaults, supplied only for a newly inserted applicable type.
RUN_DEFAULT_SQL = {
    "agent_runs": dict(
        operation_id="''",
        status="'pending'",
        session_id="''",
        transcript_start="0",
        transcript_end="0",
        structured_error_json="''",
        lease_owner="''",
        lease_expires_at="''",
        started_at="''",
        completed_at="''",
        created_at="current_timestamp",
        updated_at="current_timestamp",
        proposal_revision="0",
        turn_attempt="0",
    ),
    "agent_runtime_attempts": dict(
        session_mode="'fresh'",
        source_session_id="''",
        attempt_purpose="'normal'",
        validation_retry_policy_id="''",
        validation_result_schema_id="''",
        session_id="''",
        failure_class="''",
        failure_code="''",
        failover_permitted="0",
        transcript_reference="''",
        transcript_start="0",
        transcript_end="0",
        first_effect_started_at="''",
        lease_owner="''",
        lease_expires_at="''",
        result_schema_id="''",
        started_at="current_timestamp",
        finished_at="''",
        created_at="current_timestamp",
        updated_at="current_timestamp",
    ),
    "agent_run_state_events": dict(created_at="current_timestamp"),
}


def insert_run(
    db: sqlite3.Connection, record_type: str, values: dict, *, ignore: bool = False
) -> sqlite3.Cursor:
    """Insert one typed fact using only that original source type's defaults."""
    defaults = {
        field: sql
        for field, sql in RUN_DEFAULT_SQL[record_type].items()
        if field not in values
    }
    columns = ["record_type", *values, *defaults]
    expressions = ["?"] * (1 + len(values)) + list(defaults.values())
    verb = "insert or ignore" if ignore else "insert"
    return db.execute(
        f"{verb} into runs ({','.join(columns)}) values ({','.join(expressions)})",
        (record_type, *values.values()),
    )


RUN_INDEX_DDL = (
    "create index idx_runs_public_id on runs(record_type,cast(legacy_key as integer))",
    "create unique index idx_agent_runs_turn_identity on runs(task_id,execution_generation,role,proposal_revision,turn_attempt) where record_type='agent_runs'",
    "create index idx_agent_runs_status on runs(status,updated_at) where record_type='agent_runs'",
    "create index idx_agent_run_state_events_run on runs(parent_id,cast(legacy_key as integer)) where record_type='agent_run_state_events'",
    "create unique index idx_runtime_attempt_identity on runs(workload_kind,workload_key,attempt_number) where record_type='agent_runtime_attempts'",
    "create unique index idx_runtime_attempt_active_route on runs(workload_kind,workload_key,route_name) where record_type='agent_runtime_attempts' and status in ('starting','running')",
    "create index idx_runtime_attempt_active_lease on runs(status,lease_expires_at) where record_type='agent_runtime_attempts' and parent_id is null and status in ('starting','running')",
    "create index idx_agent_runtime_attempts_run on runs(parent_id,attempt_number) where record_type='agent_runtime_attempts' and parent_id is not null",
)


def _runtime_triggers() -> tuple[str, ...]:
    statements = []
    rules = (
        (
            "session_evidence_trim",
            "session_mode, source_session_id",
            "new.session_mode is null or new.source_session_id is null or not ((new.session_mode='fresh' and new.source_session_id='') or (new.session_mode='resume' and trim(new.source_session_id)<>''))",
            "invalid runtime attempt session evidence",
        ),
        (
            "lineage",
            "attempt_purpose, validation_retry_policy_id, validation_result_schema_id",
            "new.attempt_purpose not in ('normal','result_validation_correction') or (new.attempt_purpose='normal' and (new.validation_retry_policy_id<>'' or new.validation_result_schema_id<>'')) or (new.attempt_purpose='result_validation_correction' and (trim(new.validation_retry_policy_id)='' or trim(new.validation_result_schema_id)=''))",
            "invalid runtime attempt correction lineage",
        ),
        (
            "generalized_lease",
            "status, lease_owner, lease_expires_at",
            "new.parent_id is null and new.status in ('starting','running') and (trim(new.lease_owner)='' or trim(new.lease_expires_at)='')",
            "active generalized attempt requires lease",
        ),
    )
    for name, columns, invalid, error in rules:
        for event, suffix in (("insert", "insert"), (f"update of {columns}", "update")):
            statements.append(
                f"create trigger trg_runtime_attempt_{name}_{suffix} before {event} on runs when new.record_type='agent_runtime_attempts' and ({invalid}) begin select raise(abort,'{error}'); end"
            )
    statements.append(
        "create trigger trg_runtime_attempt_lineage_immutable before update of attempt_purpose,validation_retry_policy_id,validation_result_schema_id on runs when new.record_type='agent_runtime_attempts' and (new.attempt_purpose<>old.attempt_purpose or new.validation_retry_policy_id<>old.validation_retry_policy_id or new.validation_result_schema_id<>old.validation_result_schema_id) begin select raise(abort,'runtime attempt correction lineage is immutable'); end"
    )
    return tuple(statements)


RUN_TRIGGER_DDL = (
    "create trigger trg_runs_record_type_immutable before update of record_type on runs when new.record_type<>old.record_type begin select raise(abort,'run record type is immutable'); end",
    "create trigger trg_runs_public_key after insert on runs when new.legacy_key is null begin update runs set legacy_key=cast(new.id as text) where id=new.id; end",
    *tuple(
        f"create trigger trg_runs_logical_parent_{suffix} before {event} on runs when new.parent_id is not null and exists(select 1 from runs where id=new.parent_id and record_type<>'agent_runs') begin select raise(abort,'run parent must be a logical agent run'); end"
        for event, suffix in (
            ("insert", "insert"),
            ("update of parent_id,record_type", "update"),
        )
    ),
    *_runtime_triggers(),
)


def create_run_schema(db: sqlite3.Connection) -> None:
    db.execute(
        RUN_TABLE_DDL.replace("create table runs", "create table if not exists runs", 1)
    )
    for sql in (*RUN_INDEX_DDL, *RUN_TRIGGER_DDL):
        if sql.startswith("create unique index "):
            sql = sql.replace(
                "create unique index ", "create unique index if not exists ", 1
            )
        elif sql.startswith("create index "):
            sql = sql.replace("create index ", "create index if not exists ", 1)
        else:
            sql = sql.replace("create trigger ", "create trigger if not exists ", 1)
        db.execute(sql)


def run_schema_is_current(
    db: sqlite3.Connection, *, allow_missing_objects: bool = False
) -> bool:
    """Match the current core layout and its declared indexes/triggers.

    Missing objects can be recreated by the existing Store initialization path.
    A changed definition or an old core layout is not that repair case.
    """
    retired_names = ",".join("?" for _ in RETIRED_RUN_TABLES)
    if db.execute(
        f"select 1 from sqlite_master where type in ('table','view') and name in ({retired_names}) limit 1",
        RETIRED_RUN_TABLES,
    ).fetchone():
        return False
    objects = {
        row[0]: str(row[1] or "")
        for row in db.execute(
            "select name,sql from sqlite_master where tbl_name='runs'"
        )
    }

    def normalized(sql: str) -> str:
        return " ".join(sql.strip().rstrip(";").split()).casefold()

    if normalized(objects.get("runs", "")) != normalized(RUN_TABLE_DDL):
        return False
    for sql in (*RUN_INDEX_DDL, *RUN_TRIGGER_DDL):
        words = sql.split()
        name = words[3] if words[1] == "unique" else words[2]
        if name not in objects:
            if allow_missing_objects:
                continue
            return False
        if normalized(objects[name]) != normalized(sql):
            return False
    return True


def _schema_objects(db, table):
    return [
        dict(zip(("type", "name", "sql"), row, strict=True))
        for row in db.execute(
            "select type,name,sql from sqlite_master where tbl_name=? and sql is not null order by type,name",
            (table,),
        )
    ]


def capture_run_schema(db: sqlite3.Connection) -> dict:
    tables = [
        r[0]
        for r in db.execute(
            "select name from sqlite_master where type='table' order by name"
        )
    ]
    children = [
        table
        for table in tables
        if table not in RETIRED_RUN_TABLES
        and any(
            row[2] == "agent_runs"
            for row in db.execute(f'pragma foreign_key_list("{table}")')
        )
    ]
    scoped = [table for table in RETIRED_RUN_TABLES if table in tables] + children
    sequences = (
        dict(db.execute("select name,seq from sqlite_sequence"))
        if "sqlite_sequence" in tables
        else {}
    )
    source_high_water = max(
        [0]
        + [int(sequences.get(table) or 0) for table in scoped]
        + [
            int(db.execute(f'select coalesce(max(id),0) from "{table}"').fetchone()[0])
            for table in RETIRED_RUN_TABLES
            if table in tables
        ]
    )
    migration_high_water = source_high_water + sum(
        int(db.execute(f'select count(*) from "{table}"').fetchone()[0])
        for table in ("agent_runtime_attempts", "agent_run_state_events")
        if table in tables
    )
    return {
        "tables": {
            table: {
                "objects": _schema_objects(db, table),
                "columns": [r[1] for r in db.execute(f'pragma table_info("{table}")')],
                "sequence": sequences.get(table),
            }
            for table in scoped
        },
        "children": children,
        "allocator": {
            "source_high_water": source_high_water,
            "migration_high_water": migration_high_water,
        },
    }


def _replace_parent_ddl(sql, target):
    return re.sub(
        r'(?i)(references\s+)["`\[]?agent_runs["`\]]?(\s*\()',
        lambda m: m[1] + target + m[2],
        sql,
    )


def _rebuild_child(db, table, schema, parent):
    columns = schema["columns"]
    rows = [tuple(row) for row in db.execute(f'select * from "{table}"')]
    sequence = db.execute(
        "select seq from sqlite_sequence where name=?", (table,)
    ).fetchone()
    db.execute(f'drop table "{table}"')
    table_sql = next(obj["sql"] for obj in schema["objects"] if obj["type"] == "table")
    db.execute(_replace_parent_ddl(table_sql, parent))
    names = ",".join(f'"{column}"' for column in columns)
    db.executemany(
        f'insert into "{table}" ({names}) values ({",".join("?" for _ in columns)})',
        rows,
    )
    for obj in schema["objects"]:
        if obj["type"] != "table":
            db.execute(obj["sql"])
    if sequence is not None:
        _set_sequence(db, table, max(int(sequence[0]), int(schema["sequence"] or 0)))
    if parent == "runs":
        _install_child_guards(db, table)


def _install_child_guards(db: sqlite3.Connection, table: str) -> None:
    for fk in db.execute(f'pragma foreign_key_list("{table}")'):
        if fk[2] != "runs":
            continue
        column = fk[3]
        for event, suffix in (
            ("insert", "insert"),
            (f'update of "{column}"', "update"),
        ):
            db.execute(
                f'create trigger if not exists trg_runs_child_{table}_{column}_{suffix} before {event} on "{table}" when new."{column}" is not null and exists(select 1 from runs where id=new."{column}" and record_type<>\'agent_runs\') begin select raise(abort,\'run parent must be a logical agent run\'); end'
            )


def install_run_child_guards(db: sqlite3.Connection) -> None:
    """Retain each old declared FK's logical-run parent namespace."""
    for row in db.execute(
        "select name from sqlite_master where type='table' and name<>'runs'"
    ).fetchall():
        _install_child_guards(db, row[0])


def _drop_explicit_objects(db: sqlite3.Connection, objects: list[dict]) -> None:
    for obj in objects:
        if obj["type"] in ("index", "trigger"):
            db.execute(f'drop {obj["type"]} "{obj["name"]}"')


def _set_sequence(db, table, value):
    db.execute("delete from sqlite_sequence where name=?", (table,))
    db.execute("insert into sqlite_sequence(name,seq) values (?,?)", (table, value))


def migrate_run_schema(db: sqlite3.Connection) -> dict:
    tables = {
        r[0] for r in db.execute("select name from sqlite_master where type='table'")
    }
    if (
        "agent_run_events" in tables
        and db.execute("select 1 from agent_run_events limit 1").fetchone()
    ):
        raise ValueError("agent_run_events contains native events; migration stopped")
    if "runs" in tables:
        raise ValueError("unsupported 11.1 source schema: runs already exists")
    state = capture_run_schema(db)
    for table in RUN_TYPES:
        if table not in state["tables"] or set(
            state["tables"][table]["columns"]
        ) != set(SOURCE_COLUMNS[table]):
            raise ValueError(f"unsupported 11.1 source schema for {table}")
    high = state["allocator"]["source_high_water"]
    for table in RUN_TYPES:
        columns = state["tables"][table]["columns"]
        for column, placeholder in RETIRED_COLUMNS.items():
            if (
                column in columns
                and db.execute(
                    f'select 1 from "{table}" where "{column}" is null or "{column}"<>? limit 1',
                    (placeholder,),
                ).fetchone()
            ):
                raise ValueError(f"{table}.{column} is not a retired empty placeholder")
    for table in RETIRED_RUN_TABLES:
        if table in state["tables"]:
            _drop_explicit_objects(db, state["tables"][table]["objects"])
    create_run_schema(db)
    next_id = high
    counts = {}
    for table in RUN_TYPES:
        columns = state["tables"][table]["columns"]
        counts[table] = 0
        for row in db.execute(f'select * from "{table}" order by id').fetchall():
            source = dict(zip(columns, row, strict=True))
            if table == "agent_runs":
                physical_id = source["id"]
            else:
                next_id += 1
                physical_id = next_id
            values = {
                "id": physical_id,
                "record_type": table,
                "legacy_key": str(source["id"]),
            }
            values.update(
                {
                    COLUMN_MAP[table].get(column, column): value
                    for column, value in source.items()
                    if column not in RETIRED_COLUMNS and column != "id"
                }
            )
            db.execute(
                f"insert into runs ({','.join(values)}) values ({','.join('?' for _ in values)})",
                tuple(values.values()),
            )
            counts[table] += 1
    for table in state["children"]:
        _rebuild_child(db, table, state["tables"][table], "runs")
    for table in RETIRED_RUN_TABLES:
        if table in tables:
            db.execute(f'drop table "{table}"')
    _set_sequence(db, "runs", max(high, next_id))
    return {
        "counts": counts,
        "high_water": max(high, next_id),
        "children": state["children"],
    }


def restore_run_schema(db: sqlite3.Connection, state: dict) -> dict:
    # Rows can have been allocated and legitimately deleted since replacement.
    # Reserve those public IDs in every restored active source namespace. The
    # deterministic migration allocation itself does not advance old sequences.
    sequence = db.execute(
        "select seq from sqlite_sequence where name='runs'"
    ).fetchone()
    current_high_water = int(sequence[0]) if sequence is not None else 0
    replacement_high_water = (
        current_high_water
        if current_high_water > state["allocator"]["migration_high_water"]
        else 0
    )
    _drop_explicit_objects(db, _schema_objects(db, "runs"))
    counts = {}
    for table in RETIRED_RUN_TABLES:
        if table not in state["tables"]:
            continue
        schema = state["tables"][table]
        db.execute(
            next(obj["sql"] for obj in schema["objects"] if obj["type"] == "table")
        )
        rows = (
            db.execute(RUN_PROJECTIONS[table]).fetchall() if table in RUN_TYPES else []
        )
        projection_columns = SOURCE_COLUMNS.get(table, ())
        values = []
        for row in rows:
            source = dict(zip(projection_columns, row, strict=True))
            values.append(
                tuple(
                    source[column] if column in source else RETIRED_COLUMNS[column]
                    for column in schema["columns"]
                )
            )
        names = ",".join(f'"{column}"' for column in schema["columns"])
        db.executemany(
            f'insert into "{table}" ({names}) values ({",".join("?" for _ in schema["columns"])})',
            values,
        )
        for obj in schema["objects"]:
            if obj["type"] != "table":
                db.execute(obj["sql"])
        reserved_high_water = replacement_high_water if table in RUN_TYPES else 0
        if schema["sequence"] is not None or reserved_high_water:
            maximum = db.execute(
                f'select coalesce(max(id),0) from "{table}"'
            ).fetchone()[0]
            _set_sequence(
                db,
                table,
                max(int(schema["sequence"] or 0), maximum, reserved_high_water),
            )
        counts[table] = len(values)
    for table in state["children"]:
        _rebuild_child(db, table, state["tables"][table], "agent_runs")
    db.execute("drop table runs")
    return {"counts": counts, "children": state["children"]}
