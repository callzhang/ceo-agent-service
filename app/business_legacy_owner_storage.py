"""Unselected direct WorkTodo import owner; caller owns the FK-off transaction."""

import hashlib
import sqlite3
from contextlib import closing

from app.action_reply_storage import _source_declaration
from app.action_email_storage import _external_dependencies, _sequence, _temp_create_sql
from app.action_storage import _no_inbound, sqlite_schema_name
from app.business_definitions import _definitions


OWNER_COLUMN = (
    "import_task_id integer references business(task_public_id) "
    "check(record_type='work_todo' or import_task_id is null)"
)
LEGACY_SQL_HASHES = {
    "business_legacy_links": "6a7647d1c15db38c248a59f0f03ea2e59f45bf2981a6f1d39324c4cc216f590d",
    "idx_business_legacy_links_task": "38e195664acf00fc3cca424f289883d4d2723d6028d2827ad23f2e3137a92def",
    "trg_sync_legacy_link_parent_insert": "9600609b6d6f59859490b8c3f1fec417217e64a94b39fff9a7499d7beedba67f",
    "trg_sync_legacy_link_parent_update": "40a78ee010db7b1bde76d50443271950f9e78b4e8830d2c43e1369200790b711",
}
ACTION_GUARD_HASH = "33bf3777bb6461c9da2465fb8db16c39fdbf2ed2acbb43dbe21a88312b96bee6"


def _transaction(db):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise sqlite3.ProgrammingError("legacy owner conversion needs caller FK-off transaction")


def _business_declaration(*, target):
    with closing(sqlite3.connect(":memory:")) as scratch:
        table, indexes, triggers = _definitions(include_candidates=True, include_business_actions=True)
        for sql in (table, *indexes, *triggers):
            scratch.execute(sql)
        if target:
            scratch.execute("alter table business add column " + OWNER_COLUMN)
        return _source_declaration(scratch, "business")


def _legacy_declaration(db):
    declaration = _source_declaration(db, "business_legacy_links")
    if declaration["sql"] is None:
        raise ValueError("missing legacy link declaration")
    named = db.execute(
        "select type,name,sql from main.sqlite_schema where tbl_name='business_legacy_links' "
        "and sql is not null order by name"
    ).fetchall()
    hashes = {name: hashlib.sha256(sql.encode()).hexdigest() for _, name, sql in named}
    if hashes != LEGACY_SQL_HASHES or any(kind not in ("table", "index", "trigger") for kind, _, _ in named):
        raise ValueError("unsupported legacy link declaration")
    return declaration


def _action_guard(db):
    rows = db.execute("select sql from main.sqlite_schema where name='trg_sync_legacy_update_public_id' "
                      "and type='trigger' and tbl_name='actions'").fetchall()
    if len(rows) != 1 or hashlib.sha256(rows[0][0].encode()).hexdigest() != ACTION_GUARD_HASH:
        raise ValueError("unsupported Actions legacy link guard")
    return rows[0][0]


def _validate_metadata(metadata):
    if type(metadata) is not dict or set(metadata) != {
            "business", "legacy", "action_guard", "legacy_water"}:
        raise ValueError("unsupported legacy owner metadata")
    if metadata["business"] != _business_declaration(target=False):
        raise ValueError("unsupported legacy owner Business metadata")
    water = metadata["legacy_water"]
    if water is not None and (type(water) is not int or not 0 <= water < 2**63):
        raise ValueError("unsupported legacy owner allocator metadata")
    guard = metadata["action_guard"]
    if type(guard) is not str or hashlib.sha256(guard.encode()).hexdigest() != ACTION_GUARD_HASH:
        raise ValueError("unsupported legacy owner Actions metadata")
    legacy = metadata["legacy"]
    if type(legacy) is not dict or set(legacy) != {
            "sql", "columns", "foreign_keys", "indexes", "triggers"}:
        raise ValueError("unsupported legacy owner link metadata")
    try:
        named = {"business_legacy_links": legacy["sql"]}
        named.update((item["name"], item["sql"]) for item in legacy["indexes"] if item["sql"])
        named.update((item["name"], item["sql"]) for item in legacy["triggers"])
        hashes = {name: hashlib.sha256(sql.encode()).hexdigest() for name, sql in named.items()}
        if hashes != LEGACY_SQL_HASHES:
            raise ValueError("unsupported legacy owner link metadata")
        with closing(sqlite3.connect(":memory:")) as scratch:
            scratch.execute(legacy["sql"])
            for item in reversed(legacy["indexes"]):
                if item["sql"]:
                    scratch.execute(item["sql"])
            for item in legacy["triggers"]:
                scratch.execute(item["sql"])
            if _source_declaration(scratch, "business_legacy_links") != legacy:
                raise ValueError("unsupported legacy owner link metadata")
    except (KeyError, TypeError, sqlite3.Error) as exc:
        raise ValueError("unsupported legacy owner link metadata") from exc


def _links(db):
    rows = db.execute("select task_id,work_todo_id,signal_id,cluster_id,anchor_id,project_id,"
                      "project_candidate_id,attention_item_id,work_project_id,work_update_id "
                      "from main.business_legacy_links order by id").fetchall()
    result = []
    for task, todo, *others in rows:
        if type(task) is not int or type(todo) is not int or any(value is not None for value in others):
            raise ValueError("unsupported populated legacy link alternative")
        result.append((todo, task))
    if len({owner for owner, _ in result}) != len(result):
        raise ValueError("conflicting populated legacy owner")
    for owner, task in result:
        if db.execute("select 1 from main.business where record_type='work_todo' and public_id=?",
                      (owner,)).fetchone() is None:
            raise ValueError("dangling populated legacy owner")
        if db.execute("select 1 from main.business where record_type='task' and public_id=?",
                      (task,)).fetchone() is None:
            raise ValueError("dangling populated legacy target")
    return result


def _check_inverse_dependencies(db):
    """Ask SQLite whether the live schema permits dropping this one owner column."""
    objects = db.execute(
        "select sql from main.sqlite_schema where sql is not null and name not glob 'sqlite_*' "
        "order by case type when 'table' then 0 when 'view' then 1 "
        "when 'index' then 2 else 3 end,name"
    ).fetchall()
    temp_objects = db.execute(
        "select type,sql from temp.sqlite_schema where sql is not null and name not glob 'sqlite_*' "
        "order by case type when 'table' then 0 when 'view' then 1 "
        "when 'index' then 2 else 3 end,name"
    ).fetchall()
    with closing(sqlite3.connect(":memory:")) as scratch:
        try:
            for (sql,) in objects:
                scratch.execute(sql)
            for kind, sql in temp_objects:
                scratch.execute(_temp_create_sql(kind, sql))
            scratch.execute("alter table main.business drop column import_task_id")
        except sqlite3.Error as exc:
            raise ValueError("unsupported current owner column dependency") from exc


def capture_legacy_owner_source(db):
    """Capture exact declaration metadata; live mapping rows are read only during conversion."""
    _transaction(db)
    if _source_declaration(db, "business") != _business_declaration(target=False):
        raise ValueError("unsupported Business source declaration")
    legacy = _legacy_declaration(db)
    guard = _action_guard(db)
    _no_inbound(db, ("business_legacy_links",))
    for (table,) in db.execute("select name from temp.sqlite_schema where type='table'"):
        quoted = '"' + table.replace('"', '""') + '"'
        if any(sqlite_schema_name(fk[2]) == "business_legacy_links" for fk in db.execute(
                f"pragma temp.foreign_key_list({quoted})")):
            raise ValueError("unsupported inbound TEMP legacy link reference")
    _external_dependencies(db, targets=("business_legacy_links",),
                           allowed_trigger_names=("trg_sync_legacy_update_public_id",))
    _links(db)
    return {"business": _source_declaration(db, "business"), "legacy": legacy,
            "action_guard": guard, "legacy_water": _sequence(db, "business_legacy_links")}


def migrate_legacy_owner(db, metadata):
    """Translate supported live links, then retire the independent link container."""
    _transaction(db)
    _validate_metadata(metadata)
    current = capture_legacy_owner_source(db)
    if any(current[key] != metadata[key] for key in ("business", "legacy", "action_guard")):
        raise ValueError("legacy owner source changed after capture")
    links = _links(db)
    db.execute("drop trigger main.trg_sync_legacy_update_public_id")
    db.execute("drop table main.business_legacy_links")
    db.execute("alter table main.business add column " + OWNER_COLUMN)
    for owner, task in links:
        if db.execute("update main.business set import_task_id=? where record_type='work_todo' "
                      "and public_id=? and import_task_id is null", (task, owner)).rowcount != 1:
            raise ValueError("legacy owner mapping conflict")
    if _source_declaration(db, "business") != _business_declaration(target=True):
        raise ValueError("legacy owner target declaration differs")
    return {"mapped": len(links)}


def restore_legacy_owner(db, metadata):
    """Restore old declarations using current mappings; old local IDs/times are retired."""
    _transaction(db)
    _validate_metadata(metadata)
    if (_source_declaration(db, "business") != _business_declaration(target=True)
            or db.execute("select 1 from main.sqlite_schema where name='business_legacy_links'").fetchone()
            or db.execute("select 1 from main.sqlite_schema where name='trg_sync_legacy_update_public_id'").fetchone()):
        raise ValueError("unsupported legacy owner inverse metadata or target")
    links = db.execute("select public_id,import_task_id from main.business where import_task_id is not null "
                       "order by id").fetchall()
    if db.execute("select 1 from main.business owner where owner.import_task_id is not null and "
                  "(owner.record_type<>'work_todo' or not exists (select 1 from main.business task "
                  "where task.record_type='task' and task.public_id=owner.import_task_id)) limit 1").fetchone():
        raise ValueError("invalid current import owner or target")
    _check_inverse_dependencies(db)
    db.execute("alter table main.business drop column import_task_id")
    if _source_declaration(db, "business") != metadata["business"]:
        raise ValueError("legacy owner inverse Business declaration differs")
    db.execute(metadata["legacy"]["sql"])
    for index in reversed(metadata["legacy"]["indexes"]):
        if index["sql"]:
            db.execute(index["sql"])
    for trigger in metadata["legacy"]["triggers"]:
        db.execute(trigger["sql"])
    for owner, task in links:
        db.execute("insert into main.business_legacy_links(task_id,work_todo_id) values(?,?)",
                   (task, owner))
    db.execute(metadata["action_guard"])
    if (_source_declaration(db, "business_legacy_links") != metadata["legacy"]
            or (not links and metadata["legacy_water"] is None and
                _sequence(db, "business_legacy_links") is not None)):
        raise ValueError("legacy owner inverse link declaration or empty allocator differs")
    return {"mapped": len(links)}


def read_import_task(db, work_todo_id):
    row = db.execute("select import_task_id from main.business where record_type='work_todo' "
                     "and public_id=?", (work_todo_id,)).fetchone()
    return row[0] if row else None


def assign_import_task(db, work_todo_id, task_id):
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("legacy owner assignment needs caller transaction")
    current = db.execute("select import_task_id from main.business where record_type='work_todo' "
                         "and public_id=?", (work_todo_id,)).fetchone()
    if current is None:
        raise ValueError("missing WorkTodo import owner")
    if type(task_id) is not int or db.execute(
            "select 1 from main.business where record_type='task' and public_id=?", (task_id,)
    ).fetchone() is None:
        raise ValueError("missing formal Task import target")
    if current[0] == task_id:
        return "skipped"
    if current[0] is not None:
        raise ValueError("conflicting WorkTodo import target")
    result = db.execute("update main.business set import_task_id=? where record_type='work_todo' "
                        "and public_id=? and import_task_id is null", (task_id, work_todo_id))
    if result.rowcount != 1:
        raise ValueError("conflicting WorkTodo import target")
    return "created"
