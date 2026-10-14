"""Frozen source binding and disabled follow-up/Todo action conversion.

These hashes freeze the reviewed actual/fresh declarations. A published
predecessor must be observed and rebound separately before any release.
"""

import hashlib
import json
import sqlite3
from contextlib import closing

from app import action_storage as storage
from app.action_email_definitions import EMAIL_AUTOINCREMENT_TYPES
from app.action_followup_sync_definitions import SYNC_TYPES_ORDER, sync_action_projection
from app.action_email_storage import (
    _columns, _rows, _types, _set_sequence, _insert_rows, _create_main_index,
    _restore_triggers, _external_dependencies, _target_external_sql,
    EXTERNAL_TRIGGER_NAMES,
)


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


def _validate_allocator_names(db, *, action_names, table_names, context):
    reserved_tables = {storage.sqlite_schema_name(name) for name in
                       ("actions", *SOURCE_TABLES, "business_legacy_links")}
    for (name,) in db.execute("select name from main.sqlite_sequence"):
        if (name.casefold().startswith("actions:") and name not in action_names or
                storage.sqlite_schema_name(name) in reserved_tables and name not in table_names):
            raise ValueError(f"unsupported {context} allocator alias")


def _legacy_objects(db):
    return [dict(zip(("type", "name", "tbl_name", "sql"), tuple(row), strict=True)) for row in db.execute(
        "select type,name,tbl_name,sql from main.sqlite_schema where tbl_name='business_legacy_links' order by type,name")]


def _exact_email_blockers(db):
    rows = {name: sql for name, sql in db.execute(
        "select name,sql from main.sqlite_schema where type='trigger' and name in (" +
        ",".join("?" for _ in EXTERNAL_TRIGGER_NAMES) + ")", EXTERNAL_TRIGGER_NAMES)}
    if not rows:
        return ()
    if rows != _target_external_sql():
        raise ValueError("unsupported Email blocker declaration")
    return EXTERNAL_TRIGGER_NAMES


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
    _external_dependencies(db, targets=targets, allowed_trigger_names=_exact_email_blockers(db))
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
    _validate_allocator_names(db, action_names=action_allocator_names,
                              table_names={"actions", *SOURCE_TABLES, "business_legacy_links"},
                              context="source")
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


def _action_rows(db, columns, *, sync):
    excluded = SYNC_TYPES_ORDER if sync else ()
    predicate = "record_type not in (" + ",".join("?" for _ in excluded) + ")" if excluded else "1"
    names = ",".join(map(_quote, columns))
    rows = [tuple(row) for row in db.execute(
        f"select {names} from main.actions where {predicate} order by id", excluded)]
    types = [tuple(row) for row in db.execute(
        f"select {','.join('typeof(' + _quote(c) + ')' for c in columns)} "
        f"from main.actions where {predicate} order by id", excluded)]
    return rows, types


def _create_actions(db, variant, *, sync):
    objects = storage._current_objects(include_reply=True, reply_variant=variant,
                                       include_email=True, include_followup_sync=sync)
    db.execute(next(obj["sql"] for obj in objects if obj["type"] == "table"))
    for obj in objects:
        if obj["type"] == "index":
            _create_main_index(db, obj["sql"])
    return [obj for obj in objects if obj["type"] == "trigger"]


def _projection(db, kind, variant):
    sql = (sync_action_projection(kind, source_variant=variant)
           .replace("from actions a", "from main.actions a")
           .replace("join actions p", "join main.actions p"))
    columns = tuple(item[0] for item in db.execute(f"select * from ({sql}) limit 0").description)
    names = ",".join(map(_quote, columns))
    rows = [tuple(row) for row in db.execute(f"select {names} from ({sql}) order by id")]
    types = [tuple(row) for row in db.execute(
        f"select {','.join('typeof(' + _quote(c) + ')' for c in columns)} from ({sql}) order by id")]
    return columns, rows, types


def _legacy_rows(db):
    columns = _columns(db, "business_legacy_links")
    return columns, _rows(db, "business_legacy_links", columns), _types(db, "business_legacy_links", columns)


def _legacy_target_sql(source_sql):
    old = "references work_updates(id)"
    if source_sql.count(old) != 1:
        raise ValueError("unsupported legacy link source FK")
    return source_sql.replace(old, "references actions(id)")


LINK_TRIGGER_SQL = (
    "create trigger trg_sync_legacy_link_parent_insert after insert on business_legacy_links "
    "when new.work_update_id is not null and not exists(select 1 from actions a where "
    "a.id=new.work_update_id and a.record_type='work_updates') "
    "begin select raise(abort,'work update parent type mismatch'); end",
    "create trigger trg_sync_legacy_link_parent_update after update of work_update_id on business_legacy_links "
    "when new.work_update_id is not null and not exists(select 1 from actions a where "
    "a.id=new.work_update_id and a.record_type='work_updates') "
    "begin select raise(abort,'work update parent type mismatch'); end",
    "create trigger trg_sync_legacy_update_public_id before update of legacy_key on actions "
    "when old.record_type='work_updates' and new.legacy_key<>old.legacy_key and "
    "exists(select 1 from business_legacy_links l where l.work_update_id=old.id) "
    "begin select raise(abort,'FOREIGN KEY constraint failed'); end",
)


def _main_trigger_sql(sql):
    return sql.replace("create trigger ", "create trigger main.", 1)


def _target_legacy_declaration(source):
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.execute(_legacy_target_sql(source["sql"]))
        for index in reversed(source["indexes"]):
            if index["sql"]:
                scratch.execute(index["sql"])
        for sql in LINK_TRIGGER_SQL[:2]:
            scratch.execute(_main_trigger_sql(sql))
        return _declaration(scratch, "business_legacy_links")


def _rebuild_legacy(db, declaration, rows, *, target):
    db.execute(_legacy_target_sql(declaration["sql"]) if target else declaration["sql"])
    for index in reversed(declaration["indexes"]):
        if index["sql"]:
            _create_main_index(db, index["sql"])
    _insert_rows(db, "business_legacy_links", tuple(col["name"] for col in declaration["columns"]), rows)


def _map_legacy_refs(db, columns, rows, *, forward):
    slot = columns.index("work_update_id")
    mapped = []
    for row in rows:
        row = list(row)
        if row[slot] is not None:
            if forward:
                parent = db.execute("select id from main.actions where record_type='work_updates' "
                                    "and cast(legacy_key as integer)=?", (row[slot],)).fetchone()
            else:
                parent = db.execute("select cast(legacy_key as integer) from main.actions "
                                    "where id=? and record_type='work_updates'", (row[slot],)).fetchone()
            if parent is None:
                raise ValueError("unsupported legacy link parent")
            row[slot] = parent[0]
        mapped.append(tuple(row))
    return mapped


def _require_fk_off(db):
    storage._require_transaction(db)
    if db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("follow-up migration requires caller FK-off transaction")


def migrate_followup_sync_schema(db: sqlite3.Connection, source: dict) -> dict:
    """Copy the reviewed nine families inside the caller's FK-off transaction."""
    _require_fk_off(db)
    current = capture_followup_sync_source(db)
    if current != source:
        raise ValueError("follow-up source changed after capture")
    variant = source["action_variant"]
    action_columns = _columns(db, "actions")
    retained, retained_types = _action_rows(db, action_columns, sync=False)
    family = {kind: (_columns(db, kind), _rows(db, kind, _columns(db, kind)),
                     _types(db, kind, _columns(db, kind))) for kind in SOURCE_TABLES}
    link_columns, link_rows, link_types = _legacy_rows(db)
    db.execute("drop table main.business_legacy_links")
    for kind in reversed(SOURCE_TABLES):
        db.execute(f"drop table main.{_quote(kind)}")
    db.execute("drop table main.actions")
    triggers = _create_actions(db, variant, sync=True)
    _insert_rows(db, "actions", action_columns, retained)
    for name, water in source["sequences"].items():
        if name not in SOURCE_TABLES and name != "business_legacy_links":
            _set_sequence(db, name, water)
    for kind in SOURCE_TABLES:
        columns, rows, expected_types = family[kind]
        for row in rows:
            values = dict(zip(columns, row, strict=True))
            public_id = values.pop("id")
            parent_ref = None
            if kind == "business_task_follow_up_send_attempts":
                draft_id = values.pop("draft_id")
                parent = db.execute("select id from main.actions where record_type='business_task_follow_ups' "
                                    "and cast(legacy_key as integer)=?", (draft_id,)).fetchone()
                if parent is None:
                    raise ValueError("unsupported business follow-up parent")
                parent_ref = parent[0]
            db.execute("insert into main.actions(record_type,legacy_key,payload_json,sync_draft_action_id) "
                       "values(?,?,?,?)", (kind, str(public_id), storage._reply_payload(values), parent_ref))
        if _projection(db, kind, source["source_variant"]) != (columns, rows, expected_types):
            raise ValueError(f"{kind} migrated facts differ")
        _set_sequence(db, "actions:" + kind, source["sequences"][kind])
    mapped_links = _map_legacy_refs(db, link_columns, link_rows, forward=True)
    _rebuild_legacy(db, source["external"]["business_legacy_links"], mapped_links, target=True)
    _set_sequence(db, "business_legacy_links", source["sequences"]["business_legacy_links"])
    _restore_triggers(db, triggers)
    for sql in LINK_TRIGGER_SQL:
        db.execute(_main_trigger_sql(sql))
    if _action_rows(db, action_columns, sync=True) != (retained, retained_types):
        raise ValueError("retained actions changed")
    if _legacy_rows(db)[2] != link_types:
        raise ValueError("legacy link storage classes changed")
    return {key: source[key] for key in ("action_variant", "source_variant", "source_actions",
                                         "source_tables", "external")}


def restore_followup_sync_schema(db: sqlite3.Connection, state: dict) -> dict:
    """Restore literal source declarations using current target business facts."""
    _require_fk_off(db)
    if type(state) is not dict or set(state) != {"action_variant", "source_variant", "source_actions",
                                                 "source_tables", "external"}:
        raise ValueError("unsupported follow-up inverse metadata")
    variant = state["action_variant"]
    source_variant = state["source_variant"]
    expected_actions = storage._current_objects(include_reply=True, reply_variant=variant,
                                                include_email=True, include_followup_sync=True)
    current_actions = [obj for obj in storage._objects(db, "actions")
                       if obj["name"] != "trg_sync_legacy_update_public_id"]
    if ((variant == "actual_no_memory_fk") != (source_variant == "actual") or
            current_actions != expected_actions or
            state["source_actions"] != storage._current_objects(include_reply=True,
                reply_variant=variant, include_email=True) or
            tuple(_hash(state["source_tables"][kind]) for kind in SOURCE_TABLES) != SOURCE_HASHES[source_variant]):
        raise ValueError("unsupported follow-up target or inverse source")
    legacy_decl = state["external"]["business_legacy_links"]
    trigger_row = db.execute("select sql from main.sqlite_schema where name='trg_sync_legacy_update_public_id'").fetchone()
    if (_declaration(db, "business_legacy_links") != _target_legacy_declaration(legacy_decl) or
            (tuple(trigger_row) if trigger_row else None) !=
            (LINK_TRIGGER_SQL[2].replace("create trigger ", "CREATE TRIGGER ", 1),)):
        raise ValueError("unsupported target legacy links")
    for kind in SOURCE_TABLES:
        if db.execute("select 1 from main.sqlite_schema where name=? collate nocase", (kind,)).fetchone():
            raise ValueError("unsupported inverse source name collision")
    target_action_names = (set(storage.ACTION_SEQUENCE_NAMES.values()) |
                           {storage.DELIVERY_SEQUENCE_NAME} |
                           set(storage.REPLY_SEQUENCE_NAMES.values()) |
                           {"actions:" + kind for kind in EMAIL_AUTOINCREMENT_TYPES} |
                           {"actions:" + kind for kind in SOURCE_TABLES})
    _validate_allocator_names(db, action_names=target_action_names,
                              table_names={"actions", "business_legacy_links"},
                              context="inverse action")
    target_names = {storage.sqlite_schema_name(name) for name in
                    ("actions", *SOURCE_TABLES, "business_legacy_links")}
    for (name,) in db.execute("select name from main.sqlite_schema where type='table' "
                              "and name not glob 'sqlite_*'"):
        if name in ("actions", "business_legacy_links"):
            continue
        if any(storage.sqlite_schema_name(fk[2]) in target_names
               for fk in db.execute(f"pragma main.foreign_key_list({_quote(name)})")):
            raise ValueError("unsupported inverse inbound foreign key")
    _external_dependencies(db, targets=("actions", "business_legacy_links"),
                           allowed_trigger_names=_exact_email_blockers(db))
    action_columns = _source_action_columns(state)
    retained, retained_types = _action_rows(db, action_columns, sync=True)
    family = {kind: _projection(db, kind, source_variant) for kind in SOURCE_TABLES}
    waters = {kind: _sequence(db, "actions:" + kind) for kind in SOURCE_TABLES}
    retained_waters = {name: _sequence(db, name) for name in
                       ("actions", *storage.ACTION_SEQUENCE_NAMES.values(), storage.DELIVERY_SEQUENCE_NAME,
                        *storage.REPLY_SEQUENCE_NAMES.values(),
                        *("actions:" + kind for kind in EMAIL_AUTOINCREMENT_TYPES))}
    link_columns, link_rows, _ = _legacy_rows(db)
    link_water = _sequence(db, "business_legacy_links")
    links = _map_legacy_refs(db, link_columns, link_rows, forward=False)
    for sql in LINK_TRIGGER_SQL:
        name = sql.split(" ")[2]
        db.execute(f"drop trigger main.{_quote(name)}")
    db.execute("drop table main.business_legacy_links")
    db.execute("drop table main.actions")
    triggers = _create_actions(db, variant, sync=False)
    _insert_rows(db, "actions", action_columns, retained)
    for name, water in retained_waters.items():
        _set_sequence(db, name, water)
    _restore_triggers(db, triggers)
    for kind in SOURCE_TABLES:
        definition = state["source_tables"][kind]
        db.execute(definition["sql"])
        for index in reversed(definition["indexes"]):
            if index["sql"]:
                _create_main_index(db, index["sql"])
        columns, rows, types = family[kind]
        _insert_rows(db, kind, columns, rows)
        _set_sequence(db, kind, waters[kind])
        if (_rows(db, kind, columns), _types(db, kind, columns)) != (rows, types):
            raise ValueError(f"{kind} inverse facts differ")
    _rebuild_legacy(db, legacy_decl, links, target=False)
    _set_sequence(db, "business_legacy_links", link_water)
    for kind in SOURCE_TABLES:
        _set_sequence(db, "actions:" + kind, None)
    if _action_rows(db, action_columns, sync=False) != (retained, retained_types):
        raise ValueError("retained actions inverse differs")
    return {"counts": {kind: len(family[kind][1]) for kind in SOURCE_TABLES}}


def _source_action_columns(state):
    table = next(item for item in state["source_actions"] if item["type"] == "table")
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.execute(table["sql"])
        return _columns(scratch, "actions")
