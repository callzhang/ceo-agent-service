"""Disabled, exact-source conversion of four task delivery tables to Actions."""

import hashlib
import json
import sqlite3

from app import action_storage as storage
from app.action_task_delivery_definitions import TASK_DELIVERY_KINDS
from app.action_task_delivery_definitions import TASK_DELIVERY_PARENTS
from app.action_historical_projections import historical_task_delivery_projection
from app.action_email_storage import _columns, _insert_rows, _set_sequence, _create_main_index, _restore_triggers
from app.action_followup_sync_storage import (
    _require_fk_off, _configs_action_triggers, _drop_configs_triggers,
    _restore_configs_triggers, _exact_email_blockers, LINK_TRIGGER_SQL,
)


SOURCE_HASHES = {
    "actual": (
        "ee2091a69fd2fc3f36a8c11902c9ebea7f77c8bd333afadc1edb961058fce48d",
        "3c12e34cc5f440ba7a5bbfcbbadcd3699e47fcb0ddd8e2383dcdfe2111a6ac8c",
        "d1663ce322a54632963f6a9c28ff70619a9de918f21adccc9be7de6c58e6bc1c",
        "08247e9ea788a09541a8d3d8c35c3635271851f249f2dd002dc93289ca4acb3a",
    ),
    "fresh": (
        "ee2091a69fd2fc3f36a8c11902c9ebea7f77c8bd333afadc1edb961058fce48d",
        "342fccaa71ed296df137c3d0a4b7acae1f3cc6af7a410d1e53b36dc05a109def",
        "d1663ce322a54632963f6a9c28ff70619a9de918f21adccc9be7de6c58e6bc1c",
        "a2a6ac17c794e8632c5c6be3b67ebba42f28a2f5d945b46f7214ca693349a2bb",
    ),
}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _source_declaration(db, name):
    objects = [list(row) for row in db.execute(
        "select type,name,tbl_name,sql from main.sqlite_schema where tbl_name=? collate nocase order by type,name",
        (name,),
    )]
    columns = [list(row) for row in db.execute(f"pragma main.table_xinfo({_quote(name)})")]
    foreign_keys = [list(row) for row in db.execute(f"pragma main.foreign_key_list({_quote(name)})")]
    indexes = [
        {"list": list(row), "xinfo": [list(item) for item in db.execute(
            f"pragma main.index_xinfo({_quote(row[1])})")]} for row in db.execute(
                f"pragma main.index_list({_quote(name)})")
    ]
    return {"objects": objects, "columns": columns,
            "foreign_keys": foreign_keys, "indexes": indexes}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _source_variant(declarations):
    hashes = tuple(_hash(declarations[kind]) for kind in TASK_DELIVERY_KINDS)
    return next((name for name, expected in SOURCE_HASHES.items() if hashes == expected), None)


def _exact_sync_legacy_triggers(db):
    names = tuple(sql.split(" ")[2] for sql in LINK_TRIGGER_SQL[:2])
    actual = dict(db.execute(
        "select name,sql from main.sqlite_schema where type='trigger' and name in (?,?)", names))
    if any(actual.get(name) != sql.replace("create trigger", "CREATE TRIGGER", 1)
           for name, sql in zip(names, LINK_TRIGGER_SQL[:2], strict=True)):
        raise ValueError("unsupported sync legacy link trigger declarations")
    return names


def capture_task_delivery_source(db: sqlite3.Connection) -> dict:
    """Read metadata and counters only; reject every unsupported source shape."""
    declarations = {kind: _source_declaration(db, kind) for kind in TASK_DELIVERY_KINDS}
    variant = _source_variant(declarations)
    if variant is None:
        raise ValueError("unsupported task delivery source declaration")
    reply_variant = "actual_no_memory_fk" if variant == "actual" else "fresh_memory_fk"
    actions = storage._objects(db, "actions")
    scaffold = None
    for skill_receipts in (False, True):
        if actions == storage._current_objects(
            include_reply=True, reply_variant=reply_variant, include_email=True,
            include_followup_sync=True, include_business_sync=True,
            include_skill_config_receipts=skill_receipts,
        ):
            scaffold = skill_receipts
            break
    if scaffold is None:
        raise ValueError("unsupported task delivery source Actions declaration")
    configs_triggers = _configs_action_triggers(
        db, reply_variant, include_followup_sync=True, include_business_sync=True)
    for kind in TASK_DELIVERY_KINDS:
        if db.execute("select 1 from main.actions where record_type=? limit 1", (kind,)).fetchone():
            raise ValueError("task delivery type already present in source Actions")
    names = {storage.sqlite_schema_name(name) for name in TASK_DELIVERY_KINDS}
    for (name,) in db.execute("select name from main.sqlite_schema where type='table' and name not glob 'sqlite_*'"):
        if storage.sqlite_schema_name(name) in names or name == "actions":
            continue
        if any(storage.sqlite_schema_name(row[2]) in names for row in db.execute(
                f"pragma main.foreign_key_list({_quote(name)})")):
            raise ValueError("unsupported inbound task delivery foreign key")
    from app.action_email_storage import _external_dependencies
    _external_dependencies(db, targets=TASK_DELIVERY_KINDS)
    _external_dependencies(db, targets=("actions",),
                           allowed_trigger_names=(*_exact_email_blockers(db),
                                                  *(name for name, _ in configs_triggers),
                                                  *_exact_sync_legacy_triggers(db)))
    target = storage._current_objects(
        include_reply=True, reply_variant=reply_variant, include_email=True,
        include_followup_sync=True, include_business_sync=True,
        include_skill_config_receipts=scaffold, include_task_delivery=True,
    )
    old_names = {storage.sqlite_schema_name(obj["name"]) for obj in actions}
    for obj in target:
        if storage.sqlite_schema_name(obj["name"]) not in old_names and db.execute(
            "select 1 from main.sqlite_schema where name=? collate nocase", (obj["name"],)
        ).fetchone():
            raise ValueError("unsupported task delivery target name collision")
    sequences = {}
    for name in (*TASK_DELIVERY_KINDS, *("actions:" + kind for kind in TASK_DELIVERY_KINDS)):
        rows = [tuple(row) for row in db.execute(
            "select seq from main.sqlite_sequence where name=?", (name,))]
        if len(rows) > 1 or rows and (type(rows[0][0]) is not int or not 0 <= rows[0][0] < 2**63):
            raise ValueError("unsupported task delivery sequence")
        sequences[name] = rows[0][0] if rows else None
    return {"source_variant": variant, "reply_variant": reply_variant,
            "skill_config_receipts": scaffold, "source_tables": declarations,
            "source_actions": actions, "sequences": sequences,
            "configs_triggers": configs_triggers,
            "action_columns": _columns(db, "actions")}


def _schema_objects(db, *, variant, skill_receipts, delivery):
    return storage._current_objects(
        include_reply=True, reply_variant=variant, include_email=True,
        include_followup_sync=True, include_business_sync=True,
        include_skill_config_receipts=skill_receipts,
        include_task_delivery=delivery,
    )


def _create_actions(db, objects):
    db.execute(next(obj["sql"] for obj in objects if obj["type"] == "table"))
    for obj in objects:
        if obj["type"] == "index":
            _create_main_index(db, obj["sql"])
    return [obj for obj in objects if obj["type"] == "trigger"]


def _restore_source_family(db, definition):
    table = next(obj for obj in definition["objects"] if obj[0] == "table")
    db.execute(table[3])
    for obj in definition["objects"]:
        if obj[0] == "index" and obj[3] is not None:
            _create_main_index(db, obj[3])
    return [obj[3] for obj in definition["objects"] if obj[0] == "trigger"]


def _rows(db, table, columns):
    key = "job_id" if table == "meeting_alignment_delivery_claims" else "id"
    return [tuple(row) for row in db.execute(
        f"select {','.join(map(_quote, columns))} from main.{_quote(table)} order by {_quote(key)}")]


def _types(db, table, columns):
    key = "job_id" if table == "meeting_alignment_delivery_claims" else "id"
    return [tuple(row) for row in db.execute(
        f"select {','.join('typeof(' + _quote(c) + ')' for c in columns)} "
        f"from main.{_quote(table)} order by {_quote(key)}")]


def _projection(db, kind, variant):
    query = historical_task_delivery_projection(kind, source_variant=variant).replace(
        "from actions a", "from main.actions a")
    columns = tuple(item[0] for item in db.execute(f"select * from ({query}) limit 0").description)
    names = ",".join(map(_quote, columns))
    key = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
    rows = [tuple(row) for row in db.execute(f"select {names} from ({query}) order by {_quote(key)}")]
    types = [tuple(row) for row in db.execute(
        f"select {','.join('typeof(' + _quote(c) + ')' for c in columns)} "
        f"from ({query}) order by {_quote(key)}")]
    return columns, rows, types


def _action_rows(db, columns, *, delivery):
    where = ("record_type not in (" + ",".join("?" for _ in TASK_DELIVERY_KINDS) + ")"
             if delivery else "1")
    args = TASK_DELIVERY_KINDS if delivery else ()
    rows = [tuple(row) for row in db.execute(
        f"select {','.join(map(_quote, columns))} from main.actions where {where} order by id", args)]
    types = [tuple(row) for row in db.execute(
        f"select {','.join('typeof(' + _quote(c) + ')' for c in columns)} "
        f"from main.actions where {where} order by id", args)]
    return rows, types


def _all_sequences(db):
    return {str(name): seq for name, seq in db.execute("select name,seq from main.sqlite_sequence")}


def _restore_sequences(db, sequences, names):
    for name in names:
        _set_sequence(db, name, sequences.get(name))


def _require_state(state):
    if type(state) is not dict or set(state) != {
        "source_variant", "reply_variant", "skill_config_receipts", "source_tables",
        "source_actions", "configs_triggers", "action_columns",
    }:
        raise ValueError("unsupported task delivery inverse metadata")
    variant = state["source_variant"]
    if variant not in SOURCE_HASHES or state["reply_variant"] != (
            "actual_no_memory_fk" if variant == "actual" else "fresh_memory_fk"):
        raise ValueError("unsupported task delivery inverse variant")
    if tuple(_hash(state["source_tables"][kind]) for kind in TASK_DELIVERY_KINDS) != SOURCE_HASHES[variant]:
        raise ValueError("unsupported task delivery inverse source declaration")
    if state["source_actions"] != _schema_objects(
            None, variant=state["reply_variant"],
            skill_receipts=state["skill_config_receipts"], delivery=False):
        raise ValueError("unsupported task delivery inverse Actions source")


def migrate_task_delivery_schema(db: sqlite3.Connection, source: dict) -> dict:
    """Replace the exact four source tables inside the caller's FK-off transaction."""
    _require_fk_off(db)
    if capture_task_delivery_source(db) != source:
        raise ValueError("task delivery source changed after capture")
    action_columns = source["action_columns"]
    retained, retained_types = _action_rows(db, action_columns, delivery=False)
    family = {kind: (_columns(db, kind), _rows(db, kind, _columns(db, kind)),
                     _types(db, kind, _columns(db, kind))) for kind in TASK_DELIVERY_KINDS}
    sequences = _all_sequences(db)
    _drop_configs_triggers(db, {"configs_triggers": source["configs_triggers"]})
    for kind in reversed(TASK_DELIVERY_KINDS):
        db.execute(f"drop table main.{_quote(kind)}")
    db.execute("drop table main.actions")
    triggers = _create_actions(db, _schema_objects(
        db, variant=source["reply_variant"],
        skill_receipts=source["skill_config_receipts"], delivery=True))
    _insert_rows(db, "actions", action_columns, retained)
    _restore_sequences(db, sequences,
                       ("actions", *("actions:" + kind for kind in TASK_DELIVERY_KINDS)))
    for kind, (columns, rows, types) in family.items():
        parent, target, _ = TASK_DELIVERY_PARENTS[kind]
        identity = "job_id" if kind == "meeting_alignment_delivery_claims" else "id"
        for row in rows:
            values = dict(zip(columns, row, strict=True))
            public_id = values.pop(identity)
            parent_id = values.pop(parent) if parent != identity else public_id
            db.execute(
                f"insert into main.actions(record_type,legacy_key,payload_json,{target}) values(?,?,?,?)",
                (kind, str(public_id), storage._native_payload(values), parent_id),
            )
        if _projection(db, kind, source["source_variant"]) != (columns, rows, types):
            raise ValueError(f"{kind} migrated facts differ")
        if kind != "meeting_alignment_delivery_claims":
            _set_sequence(db, "actions:" + kind, source["sequences"][kind])
    _restore_triggers(db, triggers)
    _restore_configs_triggers(db, {"configs_triggers": source["configs_triggers"]})
    if _action_rows(db, action_columns, delivery=True) != (retained, retained_types):
        raise ValueError("retained Actions changed")
    return {key: source[key] for key in (
        "source_variant", "reply_variant", "skill_config_receipts", "source_tables",
        "source_actions", "configs_triggers", "action_columns",
    )}


def restore_task_delivery_schema(db: sqlite3.Connection, state: dict) -> dict:
    """Rebuild original physical declarations from current typed Action facts."""
    _require_fk_off(db)
    _require_state(state)
    if storage._objects(db, "actions") != _schema_objects(
            db, variant=state["reply_variant"],
            skill_receipts=state["skill_config_receipts"], delivery=True):
        raise ValueError("unsupported task delivery target Actions declaration")
    for kind in TASK_DELIVERY_KINDS:
        if db.execute("select 1 from main.sqlite_schema where name=? collate nocase", (kind,)).fetchone():
            raise ValueError("unsupported inverse source name collision")
    current_config_triggers = tuple(db.execute(
        "select name,sql from main.sqlite_schema "
        "where type='trigger' and tbl_name='configs' order by name"
    ))
    if current_config_triggers != tuple(tuple(pair) for pair in state["configs_triggers"]):
        raise ValueError("unsupported inverse Config trigger declarations")
    from app.action_email_storage import _external_dependencies
    _external_dependencies(db, targets=("actions",),
                           allowed_trigger_names=(*_exact_email_blockers(db),
                                                  *(name for name, _ in current_config_triggers),
                                                  *_exact_sync_legacy_triggers(db)))
    action_columns = tuple(state["action_columns"])
    retained, retained_types = _action_rows(db, action_columns, delivery=True)
    family = {kind: _projection(db, kind, state["source_variant"])
              for kind in TASK_DELIVERY_KINDS}
    sequences = _all_sequences(db)
    _drop_configs_triggers(db, {"configs_triggers": current_config_triggers})
    db.execute("drop table main.actions")
    action_triggers = _create_actions(db, state["source_actions"])
    _insert_rows(db, "actions", action_columns, retained)
    source_triggers = []
    for kind in TASK_DELIVERY_KINDS:
        source_triggers.extend(_restore_source_family(db, state["source_tables"][kind]))
        columns, rows, types = family[kind]
        _insert_rows(db, kind, columns, rows)
        if (_rows(db, kind, columns), _types(db, kind, columns)) != (rows, types):
            raise ValueError(f"{kind} inverse facts differ")
    for kind in TASK_DELIVERY_KINDS[1:]:
        sequences[kind] = sequences.pop("actions:" + kind, None)
    _restore_sequences(db, sequences,
                       ("actions", *TASK_DELIVERY_KINDS,
                        *("actions:" + kind for kind in TASK_DELIVERY_KINDS)))
    _restore_triggers(db, action_triggers)
    for sql in source_triggers:
        db.execute(sql)
    _restore_configs_triggers(db, {"configs_triggers": current_config_triggers})
    if _action_rows(db, action_columns, delivery=False) != (retained, retained_types):
        raise ValueError("retained Actions inverse differs")
    if any(_source_declaration(db, kind) != state["source_tables"][kind]
           for kind in TASK_DELIVERY_KINDS):
        raise ValueError("task delivery inverse declaration differs")
    return {"counts": {kind: len(family[kind][1]) for kind in TASK_DELIVERY_KINDS}}
