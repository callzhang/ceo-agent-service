"""Opt-in ten-family Business Actions conversion inside a caller-owned transaction."""

import sqlite3
from contextlib import closing
from dataclasses import dataclass

from app import action_storage as actions
from app.action_business_definitions import (
    BUSINESS_ACTION_AUTO, BUSINESS_ACTION_FIELDS, BUSINESS_ACTION_KINDS,
    BUSINESS_ACTION_DEFAULTS, BUSINESS_ACTION_KEYS, business_action_column,
    business_action_projection,
)
from app.action_email_definitions import EMAIL_NUMERIC_SCOPE
from app.action_email_storage import (
    _create_main_index, _external_dependencies, _sequence, _set_sequence,
)
from app.action_followup_sync_storage import (
    _configs_action_triggers, _drop_configs_triggers, _restore_configs_triggers,
    _exact_email_blockers, _require_fk_off,
)
from app.action_task_delivery_storage import (
    _exact_sync_legacy_triggers, _hash, _quote, _source_declaration,
)
from app.business_candidate_storage import _collision
from app.business_definitions import _definitions as business_definitions
from app.business_schema_release import _snapshot, _restore_rows
from app.task_schema_release import _foreign_keys


SOURCE_HASHES = {
    "actual": (
        "a3b426ea107a4ca0670682060ce02c33eeb6afafce7c1cf3fb58ba9e6a8888c4",
        "17cc71b31096fc774b59d655ea1359eb1b65ef10b34f0e72493fd822ac63e532",
        "993f747b0774da0681d711fc42dc5cb3c92eca97f5ab2d12899a9fd7392bac9b",
        "33b9324c7a665b2e3d690636e7c3e5d51a3872587449a461319771dca3b56351",
        "9811f37fbcbcc438b9f94fbf60f08fe55269592f38e11a79767617d72cf311f2",
        "aa313a8cea8b39f5cc1ced8509fdd2996f50ff902c95df211d4dbb8c3b0e0cc2",
        "650401abbc3d8180fdd1f07f4632fe38dcac5a0ba9746069831e4156c3ec9ae5",
        "d419f89868a3b989fa1f81cfd65de64e81a7d8697766ac5ab2fccee86718d064",
        "3e1accb6eb936bac52420d2c6677c9562336c392f4b351c511d677bd5df13f6c",
        "2ff89770eebde771d3b4aa4082d6fc587964fc438c16b71260f6faebfa357249",
    ),
    "fresh": (
        "a3b426ea107a4ca0670682060ce02c33eeb6afafce7c1cf3fb58ba9e6a8888c4",
        "17cc71b31096fc774b59d655ea1359eb1b65ef10b34f0e72493fd822ac63e532",
        "993f747b0774da0681d711fc42dc5cb3c92eca97f5ab2d12899a9fd7392bac9b",
        "33b9324c7a665b2e3d690636e7c3e5d51a3872587449a461319771dca3b56351",
        "9811f37fbcbcc438b9f94fbf60f08fe55269592f38e11a79767617d72cf311f2",
        "aa313a8cea8b39f5cc1ced8509fdd2996f50ff902c95df211d4dbb8c3b0e0cc2",
        "650401abbc3d8180fdd1f07f4632fe38dcac5a0ba9746069831e4156c3ec9ae5",
        "d419f89868a3b989fa1f81cfd65de64e81a7d8697766ac5ab2fccee86718d064",
        "52efd920f7685bf6b255ac01315fb6b19703e5ac291a5b7db58c7643c92c7de0",
        "2ff89770eebde771d3b4aa4082d6fc587964fc438c16b71260f6faebfa357249",
    ),
}
LEGACY_HASH = "439e8f0da1b1a63a7d6f8da8ab6594eab1bbafcabcee9fd7d715a6669da6ce67"
SOURCE_OPTIONS = dict(
    include_receipts=True, include_reply=True, include_email=True,
    include_followup_sync=True, include_skill_config_receipts=True,
    include_business_sync=True, include_task_delivery=True,
    include_errors=True, include_feedback=True,
)
REBUILT = ("actions", "business", "business_legacy_links", *BUSINESS_ACTION_KINDS)
OWNED_ALIASES = frozenset("actions:" + kind for kind in BUSINESS_ACTION_AUTO)
LEGACY_SIGNAL_FK = "foreign key(signal_id) references business_task_signals(id)"
TARGET_SIGNAL_FK = "foreign key(signal_id) references actions(business_signal_public_id)"


def _action_objects(variant, *, target):
    return actions._current_objects(reply_variant=variant, include_business_actions=target,
                                    **SOURCE_OPTIONS)


def _business_declaration(*, target):
    with closing(sqlite3.connect(":memory:")) as probe:
        table, indexes, triggers = business_definitions(
            include_candidates=True, include_business_actions=target)
        for sql in (table, *indexes, *triggers):
            probe.execute(sql)
        return _source_declaration(probe, "business")


def _legacy_sql(sql, *, target):
    old, new = (LEGACY_SIGNAL_FK, TARGET_SIGNAL_FK) if target else (TARGET_SIGNAL_FK, LEGACY_SIGNAL_FK)
    if sql.count(old) != 1:
        raise ValueError("unsupported Business legacy signal FK")
    return sql.replace(old, new, 1)


def _legacy_declaration(db):
    return _source_declaration(db, "business_legacy_links")


def _source_variant(declarations):
    hashes = tuple(_hash(declarations[kind]) for kind in BUSINESS_ACTION_KINDS)
    for name, expected in SOURCE_HASHES.items():
        if hashes == expected:
            return name
    raise ValueError("unsupported Business action source declarations")


def _existing_names(db, tables):
    marks = ",".join("?" for _ in tables)
    return {actions.sqlite_schema_name(name) for (name,) in db.execute(
        "select name from main.sqlite_schema where tbl_name in (" + marks + ")", tuple(tables))}


def _target_names(db, variant):
    objects = _action_objects(variant, target=True)
    with closing(sqlite3.connect(":memory:")) as probe:
        table, indexes, triggers = business_definitions(
            include_candidates=True, include_business_actions=True)
        for sql in (table, *indexes, *triggers):
            probe.execute(sql)
        business_names = {row[0] for row in probe.execute(
            "select name from main.sqlite_schema where tbl_name='business'")}
    return {actions.sqlite_schema_name(obj["name"]) for obj in objects} | {
        actions.sqlite_schema_name(name) for name in business_names}


def _collision_preflight(db, names, replaced):
    _collision(db, names)
    existing = {actions.sqlite_schema_name(row[0]) for row in db.execute(
        "select name from main.sqlite_schema")}
    if {actions.sqlite_schema_name(name) for name in names} & (existing - _existing_names(db, replaced)):
        raise ValueError("Business action target name collision")


def _external(db, configs):
    present = tuple(name for name in REBUILT if db.execute(
        "select 1 from main.sqlite_schema where type='table' and name=? collate nocase",
        (name,)).fetchone())
    _external_dependencies(
        db, targets=present,
        allowed_trigger_names=(*_exact_email_blockers(db),
                               *(name for name, _ in configs),
                               *_exact_sync_legacy_triggers(db)),
    )


def capture_business_action_source(db: sqlite3.Connection) -> dict:
    """Capture exact actual/fresh source declarations and waters, never row backups."""
    _require_fk_off(db)
    _collision(db, REBUILT)
    declarations = {kind: _source_declaration(db, kind) for kind in BUSINESS_ACTION_KINDS}
    variant = _source_variant(declarations)
    source_actions = actions._objects(db, "actions")
    reply_variant = next((candidate for candidate in
                          ("actual_no_memory_fk", "fresh_memory_fk")
                          if source_actions == _action_objects(candidate, target=False)), None)
    if reply_variant is None:
        raise ValueError("unsupported Business action source Actions declaration")
    business = _source_declaration(db, "business")
    if business != _business_declaration(target=False):
        raise ValueError("unsupported Business action source Business declaration")
    legacy = _legacy_declaration(db)
    if _hash(legacy) != LEGACY_HASH:
        raise ValueError("unsupported Business action legacy declaration")
    configs = _configs_action_triggers(
        db, reply_variant, include_followup_sync=True, include_business_sync=True,
        include_task_delivery=True, include_errors=True, include_feedback=True)
    _external(db, configs)
    _collision_preflight(db, _target_names(db, reply_variant), REBUILT)
    if any(_sequence(db, name) is not None for name in OWNED_ALIASES):
        raise ValueError("existing Business action allocator alias")
    findings = [list(row) for row in _foreign_keys(db)]
    if any(table in REBUILT or parent in BUSINESS_ACTION_KINDS
           for table, _rowid, parent, _id in findings):
        raise ValueError("broken Business action source FK")
    return {
        "variant": variant, "reply_variant": reply_variant,
        "source_tables": declarations, "source_actions": source_actions,
        "source_business": business, "source_legacy": legacy,
        "configs_triggers": [list(row) for row in configs],
        "action_columns": list(_snapshot(db, "actions")["columns"]),
        "business_columns": list(_snapshot(db, "business")["columns"]),
        "source_waters": {kind: _sequence(db, kind) for kind in BUSINESS_ACTION_KINDS},
        "fk_findings": findings,
    }


def _validate_state(state):
    if type(state) is not dict or set(state) != {
        "variant", "reply_variant", "source_tables", "source_actions",
        "source_business", "source_legacy", "configs_triggers", "action_columns",
        "business_columns", "source_waters", "fk_findings",
    }:
        raise ValueError("unsupported Business action inverse metadata")
    if state["variant"] not in SOURCE_HASHES or _source_variant(state["source_tables"]) != state["variant"]:
        raise ValueError("unsupported Business action source profile")
    if state["reply_variant"] not in ("actual_no_memory_fk", "fresh_memory_fk"):
        raise ValueError("unsupported Business action reply variant")
    if (state["source_actions"] != _action_objects(state["reply_variant"], target=False)
            or state["source_business"] != _business_declaration(target=False)
            or _hash(state["source_legacy"]) != LEGACY_HASH):
        raise ValueError("unsupported Business action source parent declarations")
    if set(state["source_waters"]) != set(BUSINESS_ACTION_KINDS):
        raise ValueError("unsupported Business action source waters")


def _create_actions(db, objects):
    db.execute(next(obj["sql"] for obj in objects if obj["type"] == "table"))
    for obj in objects:
        if obj["type"] == "index":
            _create_main_index(db, obj["sql"])
    return [obj["sql"] for obj in objects if obj["type"] == "trigger"]


def _create_business(db, *, target):
    table, indexes, triggers = business_definitions(
        include_candidates=True, include_business_actions=target)
    db.execute(table)
    for sql in indexes:
        db.execute(sql)
    return triggers


def _create_declaration(db, declaration, *, table_sql=None):
    db.execute(table_sql or next(obj[3] for obj in declaration["objects"] if obj[0] == "table"))
    index_sql = {obj[1]: obj[3] for obj in declaration["objects"]
                 if obj[0] == "index" and obj[3]}
    for item in reversed(declaration["indexes"]):
        sql = index_sql.get(item["list"][1])
        if sql:
            _create_main_index(db, sql)
    return [obj[3] for obj in declaration["objects"] if obj[0] == "trigger"]


def _drop_rebuilt(db, *, source):
    db.execute("drop table main.business_legacy_links")
    if source:
        for kind in BUSINESS_ACTION_KINDS:
            db.execute("drop table main." + _quote(kind))
    db.execute("drop table main.business")
    db.execute("drop table main.actions")


def _projected_snapshot(db, kind, variant):
    hidden = kind not in BUSINESS_ACTION_AUTO
    query = business_action_projection(kind, source_variant=variant, include_rowid=hidden)
    columns = BUSINESS_ACTION_FIELDS[kind]
    selected = (("_source_rowid",) if hidden else ()) + columns
    cols = ",".join(_quote(column) for column in selected)
    rows = tuple(tuple(row) for row in db.execute(
        "select " + cols + " from (" + query + ") order by " +
        ("_source_rowid" if hidden else "id")))
    classes = tuple(tuple(row) for row in db.execute(
        "select " + ",".join("typeof(" + _quote(column) + ")" for column in selected)
        + " from (" + query + ") order by " + ("_source_rowid" if hidden else "id")))
    return {"columns": columns, "rows": rows, "types": classes, "hidden_rowid": hidden}


def _insert_source_action(db, kind, row, hidden):
    fields = BUSINESS_ACTION_FIELDS[kind]
    if hidden:
        source_rowid, *values = row
        legacy_key = str(source_rowid)
    else:
        values = row
        legacy_key = str(row[0])
    payload = dict(zip(fields if hidden else fields[1:], values if hidden else values[1:], strict=True))
    db.execute("insert into main.actions(record_type,legacy_key,payload_json) values(?,?,?)",
               (kind, legacy_key, actions._native_payload(payload)))


def _restore_source_action(db, kind, snap):
    columns = (("rowid",) if snap["hidden_rowid"] else ()) + snap["columns"]
    sql = ("insert into main." + _quote(kind) + "("
           + ",".join(_quote(column) for column in columns) + ") values("
           + ",".join("?" for _ in columns) + ")")
    db.executemany(sql, snap["rows"])
    if _snapshot(db, kind, hidden_rowid=snap["hidden_rowid"]) != snap:
        raise ValueError(f"{kind} restored facts differ")


def _all_sequences(db):
    return {name: seq for name, seq in db.execute("select name,seq from main.sqlite_sequence")}


def migrate_business_action_schema(db: sqlite3.Connection, metadata: dict) -> dict:
    """Move all ten source families into Actions, preserving current source facts."""
    _require_fk_off(db)
    if capture_business_action_source(db) != metadata:
        raise ValueError("Business action source changed after capture")
    family = {kind: _snapshot(db, kind, hidden_rowid=kind not in BUSINESS_ACTION_AUTO)
              for kind in BUSINESS_ACTION_KINDS}
    old_actions = _snapshot(db, "actions")
    old_business = _snapshot(db, "business")
    old_legacy = _snapshot(db, "business_legacy_links")
    waters = _all_sequences(db)
    _drop_configs_triggers(db, metadata)
    _drop_rebuilt(db, source=True)
    action_triggers = _create_actions(db, _action_objects(metadata["reply_variant"], target=True))
    business_triggers = _create_business(db, target=True)
    legacy_sql = next(obj[3] for obj in metadata["source_legacy"]["objects"] if obj[0] == "table")
    legacy_triggers = _create_declaration(db, metadata["source_legacy"],
                                          table_sql=_legacy_sql(legacy_sql, target=True))
    _restore_rows(db, "actions", old_actions)
    _restore_rows(db, "business", old_business)
    _restore_rows(db, "business_legacy_links", old_legacy)
    for kind, snap in family.items():
        for row in snap["rows"]:
            _insert_source_action(db, kind, row, snap["hidden_rowid"])
        if _projected_snapshot(db, kind, metadata["variant"]) != snap:
            raise ValueError(f"{kind} migrated facts differ")
    for name, water in waters.items():
        if name not in BUSINESS_ACTION_KINDS:
            _set_sequence(db, name, water)
    for kind in BUSINESS_ACTION_AUTO:
        _set_sequence(db, "actions:" + kind, metadata["source_waters"][kind])
    for sql in (*action_triggers, *business_triggers, *legacy_triggers):
        db.execute(sql)
    _restore_configs_triggers(db, metadata)
    action_snap = _snapshot(db, "actions")
    record_type_column = action_snap["columns"].index("record_type")
    retained = {**action_snap,
                "rows": tuple(row for row in action_snap["rows"]
                              if row[record_type_column] not in BUSINESS_ACTION_KINDS),
                "types": tuple(types for row, types in zip(action_snap["rows"], action_snap["types"], strict=True)
                               if row[record_type_column] not in BUSINESS_ACTION_KINDS)}
    if (retained != old_actions or _snapshot(db, "business") != old_business
            or _snapshot(db, "business_legacy_links") != old_legacy):
        raise ValueError("Business action retained facts differ")
    return metadata


def restore_business_action_schema(db: sqlite3.Connection, metadata: dict) -> dict:
    """Rebuild exact source declarations from current Actions rows, not source backups."""
    _require_fk_off(db)
    _validate_state(metadata)
    if (actions._objects(db, "actions") != _action_objects(metadata["reply_variant"], target=True)
            or _source_declaration(db, "business") != _business_declaration(target=True)):
        raise ValueError("unsupported Business action target declarations")
    legacy = _legacy_declaration(db)
    source_legacy_sql = next(obj[3] for obj in metadata["source_legacy"]["objects"] if obj[0] == "table")
    if next(obj[3] for obj in legacy["objects"] if obj[0] == "table") != _legacy_sql(source_legacy_sql, target=True):
        raise ValueError("unsupported Business action target legacy declaration")
    source_names = {obj[1] for declaration in metadata["source_tables"].values()
                    for obj in declaration["objects"] if obj[3] is not None}
    _collision_preflight(db, source_names, ("actions", "business", "business_legacy_links"))
    configs = tuple(db.execute(
        "select name,sql from main.sqlite_schema where type='trigger' and tbl_name='configs' order by name"))
    if [list(row) for row in configs] != [list(row) for row in metadata["configs_triggers"]]:
        raise ValueError("unsupported Business action target Config triggers")
    _external(db, configs)
    family = {kind: _projected_snapshot(db, kind, metadata["variant"])
              for kind in BUSINESS_ACTION_KINDS}
    raw_actions = _snapshot(db, "actions")
    i = raw_actions["columns"].index("record_type")
    retained = {**raw_actions,
                "rows": tuple(row for row in raw_actions["rows"] if row[i] not in BUSINESS_ACTION_KINDS),
                "types": tuple(types for row, types in zip(raw_actions["rows"], raw_actions["types"], strict=True)
                               if row[i] not in BUSINESS_ACTION_KINDS)}
    business = _snapshot(db, "business")
    legacy = _snapshot(db, "business_legacy_links")
    waters = _all_sequences(db)
    _drop_configs_triggers(db, {"configs_triggers": configs})
    _drop_rebuilt(db, source=False)
    action_triggers = _create_actions(db, metadata["source_actions"])
    business_triggers = _create_business(db, target=False)
    source_triggers = []
    for kind in BUSINESS_ACTION_KINDS:
        source_triggers.extend(_create_declaration(db, metadata["source_tables"][kind]))
    legacy_triggers = _create_declaration(db, metadata["source_legacy"])
    _restore_rows(db, "actions", retained)
    _restore_rows(db, "business", business)
    _restore_rows(db, "business_legacy_links", legacy)
    for kind, snap in family.items():
        _restore_source_action(db, kind, snap)
    for name, water in waters.items():
        if name not in OWNED_ALIASES:
            _set_sequence(db, name, water)
    for kind in BUSINESS_ACTION_AUTO:
        _set_sequence(db, kind, waters.get("actions:" + kind))
        _set_sequence(db, "actions:" + kind, None)
    for sql in (*action_triggers, *business_triggers, *source_triggers, *legacy_triggers):
        db.execute(sql)
    _restore_configs_triggers(db, {"configs_triggers": configs})
    if (_snapshot(db, "actions") != retained or _snapshot(db, "business") != business
            or _snapshot(db, "business_legacy_links") != legacy):
        raise ValueError("Business action inverse retained facts differ")
    if any(_source_declaration(db, kind) != metadata["source_tables"][kind]
           for kind in BUSINESS_ACTION_KINDS):
        raise ValueError("Business action inverse source declarations differ")
    return {"counts": {kind: len(family[kind]["rows"]) for kind in BUSINESS_ACTION_KINDS}}


@dataclass(frozen=True)
class BusinessActionWriteResult:
    lastrowid: int | None
    rowcount: int


def _validate_fields(kind, values):
    if kind not in BUSINESS_ACTION_KINDS or set(values) - set(BUSINESS_ACTION_FIELDS[kind]):
        raise ValueError("unsupported Business action source fields")
    for value in values.values():
        actions._encoded_native_sqlite_value(value)


def _complete(db, kind, values):
    fields = BUSINESS_ACTION_FIELDS[kind]
    fields = fields[1:] if kind in BUSINESS_ACTION_AUTO else fields
    missing = [field for field in fields if field not in values
               and BUSINESS_ACTION_DEFAULTS[kind].get(field) != "current_timestamp"]
    result = dict(values)
    if missing:
        defaults = db.execute("select " + ",".join(
            BUSINESS_ACTION_DEFAULTS[kind].get(field, "null") for field in missing)).fetchone()
        result.update(zip(missing, defaults, strict=True))
    return result


def _next_source_rowid(db, kind):
    maximum = db.execute(
        "select max(cast(legacy_key as integer)) from main.actions where record_type=? and "
        + EMAIL_NUMERIC_SCOPE,
        (kind,)).fetchone()[0]
    if kind not in BUSINESS_ACTION_AUTO:
        if maximum != 2**63 - 1:
            return (maximum or 0) + 1
        with closing(sqlite3.connect(":memory:")) as allocator:
            allocator.execute("create table ids(id integer primary key)")
            allocator.executemany("insert into ids(id) values(?)", db.execute(
                "select cast(legacy_key as integer) from main.actions where record_type=? and "
                + EMAIL_NUMERIC_SCOPE,
                (kind,)))
            return allocator.execute("insert into ids default values").lastrowid
    water = _sequence(db, "actions:" + kind) or 0
    high = max(water, maximum or 0)
    if high == 2**63 - 1:
        # SQLite's immutable BEFORE triggers on these two native tables turn
        # SQLITE_FULL into a statement failure; the other AUTO tables roll
        # back the caller transaction when their allocator is exhausted.
        if kind not in ("business_task_date_evidence", "business_task_signals"):
            db.rollback()
        actions._raise_autoincrement_full()
    return high + 1


def insert_business_action(db: sqlite3.Connection, kind: str, values: dict,
                           *, source_rowid=None, ignore=False,
                           on_conflict_key_do_nothing=False) -> BusinessActionWriteResult:
    """Apply one native-shaped INSERT; composite records keep their hidden ROWID."""
    _validate_fields(kind, values)
    if on_conflict_key_do_nothing and (kind not in BUSINESS_ACTION_KEYS or ignore):
        raise ValueError("unsupported Business action conflict clause")
    actions._email_lock(db)
    supplied = values.get("id") if kind in BUSINESS_ACTION_AUTO else source_rowid
    identity = (_next_source_rowid(db, kind) if actions._source_bound_null(supplied)
                else actions._native_integer_primary_key(supplied))
    complete = _complete(db, kind, values)
    if kind in BUSINESS_ACTION_AUTO:
        complete.pop("id", None)
    omitted_created_at = "created_at" not in values
    payload_sql = ("jsonb_set(?, " + repr(actions._action_path(kind, "created_at")) + ", current_timestamp)"
                   if omitted_created_at else "?")
    conflict = (" on conflict(" + ",".join(business_action_column(kind, field)
                 for field in BUSINESS_ACTION_KEYS[kind]) + f") where record_type='{kind}' do nothing"
                if on_conflict_key_do_nothing else "")
    cursor = db.execute("insert " + ("or ignore " if ignore else "")
                        + "into main.actions(_kind,legacy_key,payload_json) values(?,?," + payload_sql + ")"
                        + conflict,
                        (actions.ACTION_RECORD_TYPES.code(kind), str(identity), actions._action_payload(db, kind, complete)))
    return BusinessActionWriteResult(identity if cursor.rowcount else None, cursor.rowcount)


def copy_business_action_date_evidence(
    db: sqlite3.Connection, *, source_task_id: int, target_task_id: int,
) -> int:
    """Copy nonduplicate date facts with the source's single INSERT SELECT."""
    kind = "business_task_date_evidence"
    actions._email_lock(db)
    fields = (
        "date_type", "value_at", "raw_phrase", "source_signal_id",
        "actor_kind", "actor_user_id", "actor_name", "created_at",
    )
    equal = " and ".join(
        f"target.{business_action_column(kind, field)}="
        f"source.{business_action_column(kind, field)}" for field in fields
    )
    candidates = (
        "from main.actions source where source.record_type=? "
        f"and source.{business_action_column(kind, 'task_id')}=? "
        "and not exists (select 1 from main.actions target "
        "where target.record_type=? "
        f"and target.{business_action_column(kind, 'task_id')}=? and {equal})"
    )
    parameters = (kind, source_task_id, kind, target_task_id)
    count = db.execute("select count(*) " + candidates, parameters).fetchone()[0]
    if not count:
        return 0
    base = _next_source_rowid(db, kind) - 1
    if count > 2**63 - 1 - base:
        # Native AUTO raises SQLITE_FULL and ABORTs the whole INSERT SELECT.
        # Actions.id is an ordinary IPK, so its overflow would otherwise
        # surface as a CHECK failure after numeric promotion to REAL.
        actions._raise_autoincrement_full()
    cursor = db.execute(
        "insert into main.actions(_kind,legacy_key,payload_json) "
        "select ?,cast(?+row_number() over (order by cast(source.legacy_key as integer)) as text),"
        "jsonb_set(source.payload_json," + repr(actions._action_path(kind, "task_id")) + ",?) " + candidates
        + " order by cast(source.legacy_key as integer)",
        (actions.ACTION_RECORD_TYPES.code(kind), base, target_task_id, *parameters),
    )
    return cursor.rowcount


def upsert_business_action_anchor_link(
    db: sqlite3.Connection, *, task_id: int, anchor_id: int, status: str,
    active: int, evidence_signal_id: int, reason: str,
) -> int:
    """Use the native link key to update four fields without changing its identity."""
    kind = "business_task_anchor_links"
    actions._email_lock(db)
    identity = _next_source_rowid(db, kind)
    payload = actions._action_payload(db, kind, {
        "task_id": task_id, "anchor_id": anchor_id, "status": status,
        "active": active, "evidence_signal_id": evidence_signal_id,
        "reason": reason,
    })
    updates = ",".join(
        repr(actions._action_path(kind, field)) + ",json_extract(excluded.payload_json," + repr(actions._action_path(kind, field)) + ")"
        for field in ("status", "active", "evidence_signal_id", "reason")
    )
    task_column = business_action_column(kind, "task_id")
    anchor_column = business_action_column(kind, "anchor_id")
    db.execute(
        "insert into main.actions(_kind,legacy_key,payload_json) "
        "values(?,?,jsonb_set(?," + repr(actions._action_path(kind, "created_at")) + ",current_timestamp)) "
        f"on conflict({task_column},{anchor_column}) where record_type='{kind}' "
        f"do update set payload_json=jsonb_set(actions.payload_json,{updates})",
        (actions.ACTION_RECORD_TYPES.code(kind), str(identity), payload),
    )
    row = db.execute(
        f"select cast(legacy_key as integer) from main.actions where record_type=? "
        f"and {task_column}=? and {anchor_column}=?",
        (kind, task_id, anchor_id),
    ).fetchone()
    assert row is not None
    return int(row[0])


def update_business_action(db: sqlite3.Connection, kind: str, source_rowid: int,
                           values: dict, *, ignore=False) -> int:
    """Update source columns while retaining composite hidden ROWID."""
    _validate_fields(kind, values)
    if not values:
        raise ValueError("empty Business action update")
    actions._email_lock(db)
    projection = business_action_projection(kind, include_rowid=True, public_id_index=True)
    fields = BUSINESS_ACTION_FIELDS[kind]
    row = db.execute("select " + ",".join(_quote(field) for field in fields)
                     + " from (" + projection + ") where _source_rowid=?",
                     (source_rowid,)).fetchone()
    if row is None:
        return 0
    current = dict(zip(fields, row, strict=True))
    current.update(values)
    new_id = (actions._native_integer_primary_key(current.pop("id"), update=True)
              if kind in BUSINESS_ACTION_AUTO else source_rowid)
    cursor = db.execute("update " + ("or ignore " if ignore else "")
                        + "main.actions set legacy_key=?,payload_json=? "
                        "where record_type=? and legacy_key=?",
                        (str(new_id), actions._action_payload(db, kind, current), kind, str(source_rowid)))
    return cursor.rowcount


def delete_business_action(db: sqlite3.Connection, kind: str, source_rowid: int) -> int:
    """Delete the exact source row subject to existing FK and immutable triggers."""
    _validate_fields(kind, {})
    actions._email_lock(db)
    return db.execute("delete from main.actions where record_type=? and legacy_key=?",
                      (kind, str(source_rowid))).rowcount
