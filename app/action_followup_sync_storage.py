"""Read-only binding for the disabled nine-family follow-up/Todo source.

These hashes freeze the reviewed actual/fresh declarations. A published
predecessor must be observed and rebound separately before any release.
"""

import hashlib
import json
import sqlite3

from app import action_storage as storage
from app.action_email_storage import _external_dependencies
from app.action_email_definitions import EMAIL_AUTOINCREMENT_TYPES


SOURCE_HASHES = {
    "actual": (
        "dad4d1fd5648ec32c2f0621ee4522ddb1e1b78d921ec20d88bdca56124a22b15",
        "84b322a80062225d69ba3e08f22dc07f4c56ea6bf75319ee6c92695e1872b847",
        "14529237b333260b0163acd09f8cc53072c3ac3353a7d115b6f32e87391b0358",
        "980945493144c7e56b241792036d988e6bad5a2e9569726afdbdb74086d3365b",
        "669add70a1fc31bba498f0e5ca43f3c426f923cde338b528b930658dc79f27f8",
        "3351d499daa46feabdb9fc82f6aebaf784c19d77e22195375d3359dd3c4223f7",
        "dc53a76139f9d21bb5cb52be56f4e33bee16c49791f598cecc744cee4d02ffab",
        "a09cb08836b7e137eba29a51358e16ac200ab630f786a06a27e4801f8aadaabd",
        "55bb2dd9a6dffa7b63ba22bc150d343a7bd6afdfabf54354816062d1842153d6",
    ),
    "fresh_candidate": (
        "dad4d1fd5648ec32c2f0621ee4522ddb1e1b78d921ec20d88bdca56124a22b15",
        "5073ef39419032ca39c33bce7a621a5ad9e099873da5ef21418437fbc4b0ae9a",
        "14529237b333260b0163acd09f8cc53072c3ac3353a7d115b6f32e87391b0358",
        "980945493144c7e56b241792036d988e6bad5a2e9569726afdbdb74086d3365b",
        "669add70a1fc31bba498f0e5ca43f3c426f923cde338b528b930658dc79f27f8",
        "616e05fef18ea099e01017f28a6682e2b0f2ccb099cd49b14e408ae43f03802b",
        "dc53a76139f9d21bb5cb52be56f4e33bee16c49791f598cecc744cee4d02ffab",
        "20240f68ac50d261bf818de98af402caa2de3cee1e774daf0f76d900dda28f43",
        "55bb2dd9a6dffa7b63ba22bc150d343a7bd6afdfabf54354816062d1842153d6",
    ),
}
SOURCE_TABLES = (
    "work_updates", "follow_up_drafts", "follow_up_send_attempts",
    "business_task_follow_ups", "business_task_follow_up_send_attempts",
    "task_todo_sync_outbox", "business_task_todo_sync_outbox",
    "work_todo_dingtalk_links", "business_task_dingtalk_links",
)
LEGACY_OBJECTS_HASH = "e8f00099dc0facc42abeccc162de798fa947bba4a81e4bb5748c1ab6d5ebcb05"
LEGACY_FKS_HASH = "e0ececea26419a7ff11a5d5c80677267b4ffa35ee8ec94e67e70fa97da4c7697"
LEGACY_COLUMNS_HASH = "0b9342bf08ee800ed73c835313575103a514facc293eb35a2a3f27925bc99956"
LEGACY_INDEXES_HASH = "36110e304246c48cae285dd5cdc0117d66f8de8b2462e4e8d61a40382a336110"


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _declaration(db, table):
    row = db.execute("select sql from main.sqlite_schema where type='table' and name=? collate nocase", (table,)).fetchone()
    columns = [dict(zip(("cid", "name", "type", "notnull", "dflt_value", "pk", "hidden"), tuple(item), strict=True))
               for item in db.execute(f"pragma main.table_xinfo({_quote(table)})")]
    foreign_keys = [dict(zip(("id", "seq", "table", "from", "to", "on_update", "on_delete", "match"), tuple(item), strict=True))
                    for item in db.execute(f"pragma main.foreign_key_list({_quote(table)})")]
    indexes = []
    for seq, name, unique, origin, partial in db.execute(f"pragma main.index_list({_quote(table)})"):
        index_row = db.execute("select sql from main.sqlite_schema where type='index' and name=?", (name,)).fetchone()
        indexes.append({"seq": seq, "name": name, "unique": unique, "origin": origin, "partial": partial,
                        "columns": [dict(zip(("seqno", "cid", "name", "desc", "coll", "key"), tuple(item), strict=True))
                                    for item in db.execute(f"pragma main.index_xinfo({_quote(name)})")],
                        "sql": index_row[0] if index_row else None})
    triggers = [dict(zip(("name", "sql"), tuple(item), strict=True)) for item in db.execute(
        "select name,sql from main.sqlite_schema where type='trigger' and tbl_name=? collate nocase order by name", (table,))]
    return {"sql": row[0] if row else None, "columns": columns, "foreign_keys": foreign_keys,
            "indexes": indexes, "triggers": triggers}


def _sequence(db, table):
    rows = [tuple(row) for row in db.execute("select seq from main.sqlite_sequence where name=?", (table,))]
    if len(rows) > 1 or rows and (type(rows[0][0]) is not int or not 0 <= rows[0][0] < 2**63):
        raise ValueError("unsupported follow-up source sequence")
    return rows[0][0] if rows else None


def _legacy_objects(db):
    return [dict(zip(("type", "name", "tbl_name", "sql"), tuple(row), strict=True)) for row in db.execute(
        "select type,name,tbl_name,sql from main.sqlite_schema where tbl_name='business_legacy_links' order by type,name")]


def capture_followup_sync_source(db: sqlite3.Connection) -> dict:
    """Return exact source metadata; caller owns the read transaction."""
    actions = [dict(zip(("type", "name", "sql"), tuple(row), strict=True)) for row in db.execute(
        "select type,name,sql from main.sqlite_schema where tbl_name='actions' collate nocase and sql is not null order by type,name")]
    action_variant = next((variant for variant in ("actual_no_memory_fk", "fresh_memory_fk")
                           if actions == storage._current_objects(include_reply=True, reply_variant=variant, include_email=True)), None)
    if action_variant is None:
        raise ValueError("unsupported Email29 actions declaration")
    allowed = (*storage.ALL_ACTION_TYPES_WITH_REPLY, *storage.EMAIL_TYPES_ORDER)
    if db.execute("select 1 from main.actions where record_type not in (" + ",".join("?" for _ in allowed) + ") limit 1", allowed).fetchone():
        raise ValueError("unsupported mixed actions")
    declarations = {name: _declaration(db, name) for name in SOURCE_TABLES}
    hashes = tuple(_hash(declarations[name]) for name in SOURCE_TABLES)
    source_variant = next((name for name, expected in SOURCE_HASHES.items() if hashes == expected), None)
    if source_variant is None:
        raise ValueError("unsupported nine-family source declaration")
    if (action_variant == "actual_no_memory_fk") != (source_variant == "actual"):
        raise ValueError("unsupported action/source variant pair")
    legacy = _declaration(db, "business_legacy_links")
    if (_hash(_legacy_objects(db)) != LEGACY_OBJECTS_HASH or
            _hash(legacy["foreign_keys"]) != LEGACY_FKS_HASH or
            _hash(legacy["columns"]) != LEGACY_COLUMNS_HASH or
            _hash([{key: index[key] for key in ("seq", "name", "unique", "origin", "partial")} |
                   {"xinfo": index["columns"]} for index in legacy["indexes"]]) != LEGACY_INDEXES_HASH):
        raise ValueError("unsupported business legacy links declaration")
    targets = ("actions", *SOURCE_TABLES, "business_legacy_links")
    target_names = {storage.sqlite_schema_name(name) for name in targets}
    for (name,) in db.execute("select name from main.sqlite_schema where type='table' and name not glob 'sqlite_*'"):
        if storage.sqlite_schema_name(name) in target_names:
            continue
        if any(storage.sqlite_schema_name(fk[2]) in target_names for fk in db.execute(f"pragma main.foreign_key_list({_quote(name)})")):
            raise ValueError("unsupported inbound source foreign key")
    _external_dependencies(db, targets=targets)
    candidate_objects = storage._current_objects(include_reply=True, reply_variant=action_variant,
                                                 include_email=True, include_followup_sync=True)
    source_names = {storage.sqlite_schema_name(obj["name"]) for obj in actions}
    source_names.update(storage.sqlite_schema_name(name) for name in targets)
    for obj in candidate_objects:
        if storage.sqlite_schema_name(obj["name"]) not in source_names and db.execute(
            "select 1 from main.sqlite_schema where name=? collate nocase", (obj["name"],)).fetchone():
            raise ValueError("unsupported target name collision")
    action_allocator_names = (set(storage.ACTION_SEQUENCE_NAMES.values()) |
                              {storage.DELIVERY_SEQUENCE_NAME} |
                              set(storage.REPLY_SEQUENCE_NAMES.values()) |
                              {"actions:" + kind for kind in EMAIL_AUTOINCREMENT_TYPES})
    for (name,) in db.execute("select name from main.sqlite_sequence"):
        if (name.casefold().startswith("actions:") and name not in action_allocator_names or
                name.casefold() in {table.casefold() for table in (*SOURCE_TABLES, "business_legacy_links", "actions")}
                and name not in (*SOURCE_TABLES, "business_legacy_links", "actions")):
            raise ValueError("unsupported source allocator alias")
    sequences = {name: _sequence(db, name) for name in (
        *SOURCE_TABLES, "business_legacy_links", "actions", *sorted(action_allocator_names))}
    counts = {name: db.execute(f"select count(*) from main.{_quote(name)}").fetchone()[0]
              for name in (*SOURCE_TABLES, "business_legacy_links")}
    action_counts = {str(row[0]): row[1] for row in db.execute(
        "select record_type,count(*) from main.actions group by record_type")}
    parent_declarations = {name: _declaration(db, name) for name in ("business_tasks", "business_task_signals")}
    return {"action_variant": action_variant, "source_variant": source_variant,
            "source_actions": actions, "source_tables": declarations,
            "external": {"business_legacy_links": legacy}, "retained_parents": parent_declarations,
            "sequences": sequences, "counts": counts, "action_counts": action_counts,
            "locator_modes": {name: "integer_id" for name in SOURCE_TABLES}}
