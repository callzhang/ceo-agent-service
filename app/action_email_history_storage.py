"""Reversible two-family Email history conversion inside a caller transaction."""

import sqlite3
from contextlib import closing

from app import action_storage as actions
from app.action_email_history_definitions import HISTORY_FIELDS, HISTORY_KINDS, history_projection
from app.action_email_storage import (
    _create_main_index, _external_dependencies,
    _temp_create_sql, _sequence, _set_sequence,
)
from app.action_business_storage import SOURCE_OPTIONS
from app.action_followup_sync_storage import _exact_email_blockers, _require_fk_off
from app.action_task_delivery_storage import _exact_sync_legacy_triggers, _hash, _quote, _source_declaration
from app.business_schema_helpers import _restore_rows, _snapshot
from app.result_schema_helpers import _foreign_keys


SOURCE_HASHES = {
    "email_historical_classification_history": "77af2546065da3a40e79f69c9b5b883b82b7ffdd99d06cccf32fd1b14b54b1a3",
    "email_historical_operations": "2cf9b1103b170b04e639676f0f0ad9569d7510274ff8986b2af7555771faba6b",
}
BUSINESS_HASH = "4229a95e964301e62b7e8fdd5ca7a8e3c0d72d9fc99252227047f7e3cc58b56b"
LEGACY_HASH = "d3f6cb2d64af459929b71f5955dd9eb91d7671377ffdc4e1042505bef86e69ec"
KNOWN_PARENTS = ("business", "business_legacy_links")


def _action_objects(variant, *, target):
    return actions._current_objects(reply_variant=variant,
                                    include_business_actions=True,
                                    include_email_history=target, **SOURCE_OPTIONS)


def _external_trigger_rows(db):
    # These declarations remain present throughout an Actions-only rebuild.
    names = (*_exact_email_blockers(db), *_exact_sync_legacy_triggers(db))
    rows = tuple(db.execute(
        "select name,sql from main.sqlite_schema where type='trigger' and "
        "tbl_name='configs' order by name"))
    return rows, names


def _external_dependencies_for_actions(db, known, allowed):
    """Run the existing SQLite rename/drop probe with only five known FK edges masked.

    Exact Business and Legacy declarations have already been checked. The mask
    exists only in this disposable parser; live schema and generic dependency
    validation stay unchanged.
    """
    objects = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from main.sqlite_schema "
        "where type in ('table','view','trigger') and sql is not null "
        "and name not glob 'sqlite_*' "
        "order by case type when 'table' then 0 when 'view' then 1 else 2 end,name"
    )]
    temp = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from temp.sqlite_schema "
        "where type in ('table','view','index','trigger') and sql is not null "
        "and name not glob 'sqlite_*' order by rowid"
    )]
    virtual = {(schema, actions.sqlite_schema_name(row[1]))
               for schema in ("main", "temp")
               for row in db.execute(f"pragma {schema}.table_list") if row[2] == "virtual"}
    allowed_names = {actions.sqlite_schema_name(name) for name in allowed}
    with closing(sqlite3.connect(":memory:")) as parser:
        for kind, name, owner, sql in objects:
            if kind == "trigger" and (actions.sqlite_schema_name(name) in allowed_names
                                      or actions.sqlite_schema_name(owner) == "actions"):
                continue
            if kind == "table" and name in KNOWN_PARENTS:
                expected = next(obj[3] for obj in known[name]["objects"] if obj[0] == "table")
                if sql != expected or sql.count("references actions(") != (3 if name == "business" else 2):
                    raise ValueError("unsupported known Email history parent FK")
                sql = sql.replace("references actions(", "references _history_known_actions(")
            if kind == "table" and ("main", actions.sqlite_schema_name(name)) in virtual:
                columns = [row[1] for row in db.execute(f"pragma main.table_xinfo({_quote(name)})")]
                sql = f"create table {_quote(name)} (" + ",".join(map(_quote, columns)) + ")"
            parser.execute(sql)
        for kind, name, _owner, sql in temp:
            if kind == "table" and ("temp", actions.sqlite_schema_name(name)) in virtual:
                columns = [row[1] for row in db.execute(f"pragma temp.table_xinfo({_quote(name)})")]
                sql = f"CREATE TABLE {_quote(name)} (" + ",".join(map(_quote, columns)) + ")"
            parser.execute(_temp_create_sql(kind, sql))
        # Preserve generic probe's MAIN and TEMP reference detection, now with
        # the exact unchanged Business/Legacy edges isolated from the rename.
        _external_dependencies(parser, targets=("actions",), allowed_trigger_names=())


def _capture(db, *, target):
    _require_fk_off(db)
    expected = {name: BUSINESS_HASH if name == "business" else LEGACY_HASH
                for name in KNOWN_PARENTS}
    parents = {name: _source_declaration(db, name) for name in KNOWN_PARENTS}
    if any(_hash(parents[name]) != expected[name] for name in KNOWN_PARENTS):
        raise ValueError("unsupported unchanged Email history Business/Legacy declaration")
    actual = actions._objects(db, "actions")
    variant = next((name for name in ("actual_no_memory_fk", "fresh_memory_fk")
                    if actual == _action_objects(name, target=target)), None)
    if variant is None:
        raise ValueError("unsupported Email history Actions declaration")
    declarations = {kind: _source_declaration(db, kind) for kind in HISTORY_KINDS}
    if target:
        if any(decl["objects"] for decl in declarations.values()):
            raise ValueError("native Email history table remains")
    elif any(_hash(declarations[kind]) != SOURCE_HASHES[kind] for kind in HISTORY_KINDS):
        raise ValueError("unsupported Email history source declaration")
    if any(_sequence(db, "actions:" + kind) is not None for kind in HISTORY_KINDS):
        raise ValueError("unsupported Email history allocator alias")
    configs, external = _external_trigger_rows(db)
    from app.configs_schema_current import configs_content_schema_is_current
    if not configs_content_schema_is_current(
        db, action_variant=variant, include_followup_sync=True,
        include_business_sync=True, include_task_delivery=True,
        include_errors=True, include_feedback=True,
        include_business_actions=True, include_email_history=target,
    ):
        raise ValueError("unsupported Email history Config/Actions declaration")
    if not target:
        _external_dependencies(db, targets=HISTORY_KINDS,
                               allowed_trigger_names=(*external, *(name for name, _ in configs)))
    _external_dependencies_for_actions(db, parents,
                                       (*external, *(name for name, _ in configs)))
    return variant, declarations, parents, [list(row) for row in configs], list(external)


def capture_email_history_source(db: sqlite3.Connection) -> dict:
    """Return declarations and waters only, for JSON-roundtrippable inverse."""
    variant, declarations, parents, configs, external = _capture(db, target=False)
    waters = {kind: _sequence(db, kind) for kind in HISTORY_KINDS}
    if any(water is not None for water in waters.values()):
        raise ValueError("unexpected Email history sqlite_sequence entry")
    findings = [list(row) for row in _foreign_keys(db)]
    return {
        "variant": variant, "source_actions": _action_objects(variant, target=False),
        "source_tables": declarations, "source_waters": waters,
        "parents": parents, "configs_triggers": configs,
        "external_triggers": external, "fk_findings": findings,
    }


def _validate_state(state):
    if type(state) is not dict or set(state) != {
        "variant", "source_actions", "source_tables", "source_waters",
        "parents", "configs_triggers", "external_triggers", "fk_findings",
    }:
        raise ValueError("unsupported Email history inverse metadata")
    if state["variant"] not in ("actual_no_memory_fk", "fresh_memory_fk"):
        raise ValueError("unsupported Email history reply variant")
    if (state["source_actions"] != _action_objects(state["variant"], target=False)
            or set(state["source_tables"]) != set(HISTORY_KINDS)
            or any(_hash(state["source_tables"][kind]) != SOURCE_HASHES[kind]
                   for kind in HISTORY_KINDS)
            or set(state["source_waters"]) != set(HISTORY_KINDS)
            or any(value is not None for value in state["source_waters"].values())
            or set(state["parents"]) != set(KNOWN_PARENTS)
            or _hash(state["parents"]["business"]) != BUSINESS_HASH
            or _hash(state["parents"]["business_legacy_links"]) != LEGACY_HASH):
        raise ValueError("unsupported Email history source metadata")


def _create_actions(db, objects):
    for category in ("table", "index"):
        for obj in objects:
            if obj["type"] == category:
                if category == "index":
                    _create_main_index(db, obj["sql"])
                else:
                    db.execute(obj["sql"])
    return tuple(obj["sql"] for obj in objects if obj["type"] == "trigger")


def _projected_snapshot(db, kind):
    fields = HISTORY_FIELDS[kind]
    query = history_projection(kind, include_rowid=True)
    names = ("_source_rowid", *fields)
    return {
        "columns": fields, "hidden_rowid": True,
        "rows": tuple(tuple(row) for row in db.execute(
            "select " + ",".join(names) + " from (" + query + ") order by _source_rowid")),
        "types": tuple(tuple(row) for row in db.execute(
            "select " + ",".join("typeof(" + name + ")" for name in names)
            + " from (" + query + ") order by _source_rowid")),
    }


def _source_rows_into_actions(db, kind, snap):
    for rowid, *values in snap["rows"]:
        db.execute("insert into main.actions(record_type,legacy_key,payload_json) values(?,?,?)",
                   (kind, str(rowid), actions._native_payload(dict(zip(HISTORY_FIELDS[kind], values, strict=True)))))


def _source_table_from_declaration(db, declaration):
    db.execute(next(obj[3] for obj in declaration["objects"] if obj[0] == "table"))
    for obj in declaration["objects"]:
        if obj[0] == "index" and obj[3] is not None:
            _create_main_index(db, obj[3])
    for obj in declaration["objects"]:
        if obj[0] == "trigger":
            db.execute(obj[3])


def migrate_email_history_schema(db: sqlite3.Connection, metadata: dict) -> dict:
    if capture_email_history_source(db) != metadata:
        raise ValueError("Email history source changed after capture")
    family = {kind: _snapshot(db, kind, hidden_rowid=True) for kind in HISTORY_KINDS}
    retained = _snapshot(db, "actions")
    parents = {kind: _snapshot(db, kind) for kind in KNOWN_PARENTS}
    waters = dict(db.execute("select name,seq from main.sqlite_sequence"))
    for kind in HISTORY_KINDS:
        db.execute("drop table main." + _quote(kind))
    db.execute("drop table main.actions")
    action_triggers = _create_actions(db, _action_objects(metadata["variant"], target=True))
    _restore_rows(db, "actions", retained)
    for kind, snap in family.items():
        _source_rows_into_actions(db, kind, snap)
        if _projected_snapshot(db, kind) != snap:
            raise ValueError("Email history source facts changed in Actions")
    for name, water in waters.items():
        _set_sequence(db, name, water)
    for sql in action_triggers:
        db.execute(sql)
    if any(_snapshot(db, kind) != parents[kind] for kind in KNOWN_PARENTS):
        raise ValueError("Email history retained parent changed")
    return metadata


def restore_email_history_schema(db: sqlite3.Connection, metadata: dict) -> dict:
    _validate_state(metadata)
    variant, declarations, parents, configs, external = _capture(db, target=True)
    if (variant != metadata["variant"] or parents != metadata["parents"]
            or configs != metadata["configs_triggers"]
            or external != metadata["external_triggers"]):
        raise ValueError("Email history target declaration changed")
    family = {kind: _projected_snapshot(db, kind) for kind in HISTORY_KINDS}
    old_actions = _snapshot(db, "actions")
    record_type_at = old_actions["columns"].index("record_type")
    retained = {**old_actions,
                "rows": tuple(row for row in old_actions["rows"] if row[record_type_at] not in HISTORY_KINDS),
                "types": tuple(types for row, types in zip(old_actions["rows"], old_actions["types"], strict=True)
                               if row[record_type_at] not in HISTORY_KINDS)}
    parent_rows = {kind: _snapshot(db, kind) for kind in KNOWN_PARENTS}
    waters = dict(db.execute("select name,seq from main.sqlite_sequence"))
    db.execute("drop table main.actions")
    action_triggers = _create_actions(db, metadata["source_actions"])
    for kind in HISTORY_KINDS:
        _source_table_from_declaration(db, metadata["source_tables"][kind])
    _restore_rows(db, "actions", retained)
    for kind, snap in family.items():
        _restore_rows(db, kind, snap)
        _set_sequence(db, kind, metadata["source_waters"][kind])
    for name, water in waters.items():
        _set_sequence(db, name, water)
    for sql in action_triggers:
        db.execute(sql)
    if any(_snapshot(db, kind) != parent_rows[kind] for kind in KNOWN_PARENTS):
        raise ValueError("Email history inverse changed retained parent")
    return metadata


def insert_email_history(db: sqlite3.Connection, kind: str, values: dict,
                         *, ignore_public_duplicate: bool = False) -> int:
    """Insert one ordinary hidden-ROWID history record in the caller's unit."""
    if kind not in HISTORY_KINDS or set(values) - set(HISTORY_FIELDS[kind]):
        raise ValueError("unsupported Email history insert fields")
    fields = HISTORY_FIELDS[kind]
    data = {field: values.get(field, "" if kind == HISTORY_KINDS[1]
                                        and field == "action_outcome" else None)
            for field in fields}

    def operation():
        actions._email_lock(db, tuple(data.values()))
        rowid = actions._email_rowid_next(db, kind)
        sql = ("insert or ignore " if ignore_public_duplicate else "insert ")
        sql += "into main.actions(_kind,legacy_key,payload_json) values(?,?,?)"
        return db.execute(sql, (actions.ACTION_RECORD_TYPES.code(kind), str(rowid), actions._action_payload(db, kind, data))).rowcount

    return actions._email_unit(db, "email_history_insert", operation)


def email_history_rows(db: sqlite3.Connection, kind: str, *, where: str = "",
                       parameters=(), order: str = ""):
    if kind not in HISTORY_KINDS:
        raise ValueError("unsupported Email history kind")
    return db.execute("select * from (" + history_projection(kind) + ")"
                      + (" where " + where if where else "")
                      + (" order by " + order if order else ""), parameters).fetchall()


def update_email_operation(db: sqlite3.Connection, *, model_id: str,
                           stable_message_identity: str, updated_at: str,
                           outcome: str | None = None) -> int:
    kind = "email_historical_operations"
    assignments = [repr(actions._action_path(kind, "updated_at")) + ",?"]
    parameters = [updated_at]
    if outcome is not None:
        assignments.extend((repr(actions._action_path(kind, "status")) + ",'terminal'",
                            repr(actions._action_path(kind, "action_outcome")) + ",?"))
        parameters.append(outcome)
    return db.execute(
        "update main.actions set payload_json=jsonb_set(payload_json,"
        + ",".join(assignments) + ") where record_type='email_historical_operations' "
        "and email_operation_model_id=? and email_operation_stable_message_identity=? "
        "and email_operation_status='processing'",
        (*parameters, model_id, stable_message_identity),
    ).rowcount
