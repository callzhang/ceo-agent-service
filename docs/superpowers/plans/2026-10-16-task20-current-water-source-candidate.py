"""Source-bound Tasks20 conversion; caller owns the FK-off transaction.

Unselected canonical implementation. Only declarations and allocator-presence
bits survive as inverse metadata; restoration always reads current task facts.
"""
import sqlite3

from app.action_reply_storage import _fingerprint, _source_declaration
from app.action_storage import _native_payload
from app.task_storage import COLUMN_MAP, WORKBENCH_TASK_COLUMN_MAP
from app.task8_storage import TASK8_KINDS
from app.task12_storage import insert_task12, task12_projection
from app.task20_definitions import (
    TASK12_AUTOINCREMENT_KINDS, TASK20_NEW_KINDS,
    TASK20_PAYLOAD_FIELDS, create_task20_schema, initialize_task8_sequence,
    task20_column,
)

CHILD = "feedback_iteration_decision_items"
SOURCE_TABLES = ("tasks", *TASK20_NEW_KINDS, CHILD)
SOURCE_COUNTERS = ("tasks", *TASK12_AUTOINCREMENT_KINDS)
# Frozen exact actual/fresh declarations, independent of index enumeration order.
SOURCE_HASHES = {'actual': '928065cc65749194e5411af25bff547fa54b1209dcfd4608250b074aa27c4e7f', 'fresh': 'bef04f8f21d824003714e8ec06a30ce2ce40690fdec947fd86a38db2414cc4bb'}
TARGET_TASKS_HASH = '7606f44ab526f4216537199be9f4ca4be6f4ae1eaf0ff67f6adb432710b18e2e'
FK_REPLACEMENTS = (
    ("references feedback_iteration_decisions(id)",
     'references tasks("feedback_iteration_decisions__id")'),
    ("references feedback_processing_items(feedback_key)",
     'references tasks("feedback_processing_items__feedback_key")'),
    ("references feedback_processing_rounds(id)",
     'references tasks("feedback_processing_rounds__id")'),
)


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _declaration(db, table):
    result = _source_declaration(db, table)
    result["indexes"] = sorted(
        ({key: value for key, value in index.items() if key != "seq"}
         for index in result["indexes"]), key=lambda index: index["name"],
    )
    return result


def _transaction(db):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise sqlite3.ProgrammingError("Tasks20 release requires caller FK-off transaction")


def _no_temp(db):
    names = (*SOURCE_TABLES, *("task20_release_" + name for name in SOURCE_TABLES))
    if any(db.execute("select 1 from temp.sqlite_schema where name=? collate nocase",
                      (name,)).fetchone() for name in names):
        raise ValueError("Tasks20 TEMP relation or staging name collision")


def _sequence(db, name):
    row = db.execute("select seq from main.sqlite_sequence where name=?", (name,)).fetchone()
    return None if row is None else row[0]


def _set_sequence(db, name, water):
    db.execute("delete from main.sqlite_sequence where name=?", (name,))
    if water is not None:
        db.execute("insert into main.sqlite_sequence(name,seq) values(?,?)", (name, water))


def _metadata(metadata):
    if (not isinstance(metadata, dict)
            or set(metadata) != {"variant", "tables", "sequence_present"}
            or metadata["variant"] not in SOURCE_HASHES
            or not isinstance(metadata["tables"], dict)
            or set(metadata["tables"]) != set(SOURCE_TABLES)
            or _fingerprint(metadata["tables"]) != SOURCE_HASHES[metadata["variant"]]
            or not isinstance(metadata["sequence_present"], dict)
            or set(metadata["sequence_present"]) != set(SOURCE_COUNTERS)
            or any(type(value) is not bool for value in metadata["sequence_present"].values())):
        raise ValueError("unsupported Tasks20 source metadata")


def capture_task20_source(db):
    """Capture declarations and presence only, never bodies or counter values."""
    _transaction(db)
    _no_temp(db)
    tables = {name: _declaration(db, name) for name in SOURCE_TABLES}
    fingerprint = _fingerprint(tables)
    variant = next((key for key, value in SOURCE_HASHES.items() if value == fingerprint), None)
    if variant is None:
        raise ValueError("unsupported Tasks20 source declarations")
    return {"variant": variant, "tables": tables,
            "sequence_present": {name: _sequence(db, name) is not None for name in SOURCE_COUNTERS}}


def _child_target(source):
    sql = source["sql"]
    for old, new in FK_REPLACEMENTS:
        if sql.count(old) != 1:
            raise ValueError("unsupported Tasks20 retained child source")
        sql = sql.replace(old, new)
    return sql


def _create(db, definition, *, table_sql=None, triggers=True):
    db.execute(definition["sql"] if table_sql is None else table_sql)
    for index in definition["indexes"]:
        if index["sql"] is not None:
            db.execute(index["sql"])
    if triggers:
        for trigger in definition["triggers"]:
            db.execute(trigger["sql"])


def _stage(db, table, fields, *, query=None):
    name = "task20_release_" + table
    if db.execute("select 1 from temp.sqlite_schema where name=? collate nocase", (name,)).fetchone():
        raise ValueError("Tasks20 TEMP staging name collision")
    columns = ",".join(map(_quote, fields))
    # No affinities: staging must not change SQLite scalar storage classes.
    db.execute("create temp table " + _quote(name) + "(" + columns + ")")
    db.execute("insert into temp." + _quote(name) + "(" + columns + ") "
               + ("select " + columns + " from main." + _quote(table) if query is None else "select " + columns + " from (" + query + ")"))
    return name


def _fields(definition):
    return tuple(column["name"] for column in definition["columns"] if column["hidden"] == 0)


def _same(db, source, target, fields):
    columns = ",".join(map(_quote, fields))
    typed = columns + "," + ",".join("typeof(" + _quote(field) + ")" for field in fields)
    left = "select " + typed + " from (" + source + ")"
    right = "select " + typed + " from (" + target + ")"
    if db.execute("select count(*) from (" + source + ")").fetchone()[0] != db.execute(
            "select count(*) from (" + target + ")").fetchone()[0]:
        raise ValueError("Tasks20 fact count differs")
    for a, b in ((left, right), (right, left)):
        if db.execute("select 1 from (" + a + " except " + b + ") limit 1").fetchone():
            raise ValueError("Tasks20 source identity, value or storage class differs")


def _raw_task8(fields):
    result = []
    structural = {"id", "record_type", "legacy_key", "parent_id", "current_input_id", "claimed_input_id"}
    for field in fields:
        if field in structural:
            expression = _quote(field)
        else:
            cases = []
            for kind in TASK8_KINDS:
                mapping = WORKBENCH_TASK_COLUMN_MAP if kind.startswith("workbench_") else COLUMN_MAP
                for logical in TASK20_PAYLOAD_FIELDS[kind]:
                    if mapping.get(logical, logical) == field:
                        cases.append("when record_type=" + repr(kind) + " then " + task20_column(kind, logical))
            expression = "case " + " ".join(cases) + " else null end" if cases else "null"
        result.append(expression + " as " + _quote(field))
    return "select " + ",".join(result) + " from main.tasks where record_type in (" + ",".join(map(repr, TASK8_KINDS)) + ")"


def _target_shape(db, metadata):
    _no_temp(db)
    if _fingerprint(_declaration(db, "tasks")) != TARGET_TASKS_HASH:
        raise ValueError("unsupported Tasks20 target declaration")
    expected = dict(metadata["tables"][CHILD])
    expected["sql"] = _child_target(expected)
    expected["foreign_keys"] = [
        dict(fk, table="tasks", to={
            "decision_id": "feedback_iteration_decisions__id",
            "feedback_key": "feedback_processing_items__feedback_key",
            "round_id": "feedback_processing_rounds__id",
        }[fk["from"]]) for fk in expected["foreign_keys"]
    ]
    if _declaration(db, CHILD) != expected:
        raise ValueError("unsupported Tasks20 retained child declaration")
    if any(db.execute("select 1 from main.sqlite_schema where name=? collate nocase", (name,)).fetchone()
           for name in TASK20_NEW_KINDS):
        raise ValueError("Tasks20 native source remains")


def migrate_task20_schema(db, metadata):
    """Transfer current source facts and retain every original-eight physical ID."""
    _transaction(db)
    _metadata(metadata)
    current = capture_task20_source(db)
    # Presence is allocator state, not a declaration. Supported native writes
    # may consume an initially absent stream after preparation. Transfer reads
    # current waters; a now-positive published water supersedes its absence bit.
    if any(current[key] != metadata[key] for key in ("variant", "tables")):
        raise ValueError("Tasks20 source declarations changed")
    waters = {name: _sequence(db, name) for name in SOURCE_COUNTERS}
    fields = {name: _fields(definition) for name, definition in metadata["tables"].items()}
    staged = {}
    for name in SOURCE_TABLES:
        columns = fields[name] if name == "tasks" else ("rowid", *fields[name])
        staged[name] = _stage(db, name, columns)
    db.execute("drop table main." + _quote(CHILD))
    db.execute("drop table main.tasks")
    create_task20_schema(db)
    raw_fields = fields["tasks"]
    counts = {}
    for row in db.execute("select * from temp." + _quote(staged["tasks"]) + " order by id"):
        values = dict(zip(raw_fields, tuple(row), strict=True))
        kind = values["record_type"]
        mapping = WORKBENCH_TASK_COLUMN_MAP if kind.startswith("workbench_") else COLUMN_MAP
        payload = {field: values[mapping.get(field, field)] for field in TASK20_PAYLOAD_FIELDS[kind]}
        db.execute("insert into main.tasks(id,record_type,legacy_key,parent_id,current_input_id,claimed_input_id,payload_json) values(?,?,?,?,?,?,?)",
                   (*(values[field] for field in ("id", "record_type", "legacy_key", "parent_id", "current_input_id", "claimed_input_id")), _native_payload(payload)))
    _same(db, "select * from temp." + _quote(staged["tasks"]), _raw_task8(raw_fields), raw_fields)
    counts["tasks"] = db.execute("select count(*) from main.tasks").fetchone()[0]
    # Preserve deleted-high source water before allocating any new-kind IDs.
    _set_sequence(db, "tasks", waters["tasks"])
    initialize_task8_sequence(db, 0 if waters["tasks"] is None else waters["tasks"])
    order = ("feedback_processing_batches", *(kind for kind in TASK20_NEW_KINDS if kind != "feedback_processing_batches"))
    for kind in order:
        for row in db.execute("select * from temp." + _quote(staged[kind]) + " order by rowid"):
            values = dict(zip(fields[kind], tuple(row)[1:], strict=True))
            if kind in TASK12_AUTOINCREMENT_KINDS:
                insert_task12(db, kind, values)
            else:
                insert_task12(db, kind, values, source_rowid=row[0])
        _same(db, "select * from temp." + _quote(staged[kind]), task12_projection(kind, include_rowid=True), ("rowid", *fields[kind]))
        counts[kind] = db.execute("select count(*) from temp." + _quote(staged[kind])).fetchone()[0]
    for kind in TASK12_AUTOINCREMENT_KINDS:
        _set_sequence(db, "tasks:" + kind, waters[kind])
    for kind in TASK20_NEW_KINDS:
        db.execute("drop table main." + _quote(kind))
    _create(db, metadata["tables"][CHILD], table_sql=_child_target(metadata["tables"][CHILD]))
    columns = ",".join(map(_quote, ("rowid", *fields[CHILD])))
    db.execute("insert into main." + _quote(CHILD) + "(" + columns + ") select " + columns + " from temp." + _quote(staged[CHILD]))
    _same(db, "select * from temp." + _quote(staged[CHILD]), "select rowid,* from main." + _quote(CHILD), ("rowid", *fields[CHILD]))
    for name in staged.values():
        db.execute("drop table temp." + _quote(name))
    _target_shape(db, metadata)
    return counts


def restore_task20_schema(db, metadata):
    """Restore native schema from current facts, including post-migration writes."""
    _transaction(db)
    _metadata(metadata)
    _target_shape(db, metadata)
    waters = {name: _sequence(db, "tasks:published" if name == "tasks" else "tasks:" + name) for name in SOURCE_COUNTERS}
    fields = {name: _fields(definition) for name, definition in metadata["tables"].items()}
    staged = {"tasks": _stage(db, "tasks", fields["tasks"], query=_raw_task8(fields["tasks"]))}
    for kind in TASK20_NEW_KINDS:
        staged[kind] = _stage(db, kind, ("rowid", *fields[kind]), query=task12_projection(kind, include_rowid=True))
    staged[CHILD] = _stage(db, CHILD, ("rowid", *fields[CHILD]))
    db.execute("drop table main." + _quote(CHILD))
    db.execute("drop table main.tasks")
    for name in SOURCE_TABLES:
        _create(db, metadata["tables"][name], triggers=False)
    counts = {}
    for name in SOURCE_TABLES:
        columns = fields[name] if name == "tasks" or name in TASK12_AUTOINCREMENT_KINDS else ("rowid", *fields[name])
        selected = ",".join(map(_quote, columns))
        cursor = db.execute("insert into main." + _quote(name) + "(" + selected + ") select " + selected + " from temp." + _quote(staged[name]))
        counts[name] = cursor.rowcount
        compared = fields[name] if name == "tasks" else ("rowid", *fields[name])
        query = "select * from main.tasks" if name == "tasks" else "select rowid,* from main." + _quote(name)
        _same(db, "select * from temp." + _quote(staged[name]), query, compared)
    for name in SOURCE_TABLES:
        for trigger in metadata["tables"][name]["triggers"]:
            db.execute(trigger["sql"])
    for name, water in waters.items():
        if name == "tasks" and water == 0 and not metadata["sequence_present"][name]:
            water = None
        _set_sequence(db, name, water)
    for name in ("tasks:published", *("tasks:" + kind for kind in TASK12_AUTOINCREMENT_KINDS)):
        _set_sequence(db, name, None)
    if {name: _declaration(db, name) for name in SOURCE_TABLES} != metadata["tables"]:
        raise ValueError("Tasks20 restored declarations differ")
    for name in staged.values():
        db.execute("drop table temp." + _quote(name))
    return counts
