"""Unselected exact-source conversion of native errors to typed Actions."""

import sqlite3
from contextlib import closing
from functools import lru_cache

from app import action_storage as storage
from app.action_email_storage import _columns, _insert_rows, _set_sequence, _create_main_index, _restore_triggers
from app.action_error_definitions import ERROR_FIELDS, error_projection
from app.action_followup_sync_storage import (
    _require_fk_off, _configs_action_triggers, _drop_configs_triggers,
    _restore_configs_triggers, _exact_email_blockers,
)
from app.action_task_delivery_storage import _source_declaration, _quote, _exact_sync_legacy_triggers


SOURCE_TABLE_SQL = (
    """CREATE TABLE errors (\n                    id integer primary key autoincrement,\n                    conversation_id text,\n                    message_id text,\n                    kind text not null,\n                    detail text not null,\n                    created_at text not null default current_timestamp\n                , resolved_at text not null default '', resolution text not null default '')""",
    """CREATE TABLE errors (\n                    id integer primary key autoincrement,\n                    conversation_id text,\n                    message_id text,\n                    kind text not null,\n                    detail text not null,\n                    created_at text not null default current_timestamp,\n                    resolved_at text not null default '',\n                    resolution text not null default ''\n                )""",
)
SOURCE_INDEX_SQL = (
    """CREATE INDEX idx_errors_unresolved\n                    on errors(conversation_id, kind, message_id, id)\n                    where coalesce(resolved_at, '')=''\n                """,
    """CREATE INDEX idx_errors_reply_task_settlement\n                    on errors(conversation_id, message_id, kind)\n                """,
)


@lru_cache(maxsize=1)
def _source_declarations():
    expected = []
    for table in SOURCE_TABLE_SQL:
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute(table)
            for index in SOURCE_INDEX_SQL:
                db.execute(index)
            expected.append(_source_declaration(db, "errors"))
    return tuple(expected)


def _action_objects(*, variant, receipts, errors):
    return storage._current_objects(
        include_reply=True, reply_variant=variant, include_email=True,
        include_followup_sync=True, include_business_sync=True,
        include_skill_config_receipts=receipts, include_task_delivery=True,
        include_errors=errors,
    )


def _declared_action_columns(objects):
    table = next(item["sql"] for item in objects if item["type"] == "table")
    with closing(sqlite3.connect(":memory:")) as probe:
        probe.execute(table)
        return _columns(probe, "actions")


def _sequence(db, name):
    rows = [row[0] for row in db.execute("select seq from main.sqlite_sequence where name=?", (name,))]
    if len(rows) > 1 or rows and (type(rows[0]) is not int or not 0 <= rows[0] < 2**63):
        raise ValueError("unsupported error sequence")
    return rows[0] if rows else None


def capture_action_error_source(db: sqlite3.Connection) -> dict:
    declaration = _source_declaration(db, "errors")
    if declaration not in _source_declarations():
        raise ValueError("unsupported error source declaration")
    actions = storage._objects(db, "actions")
    candidate = next((
        (variant, receipts) for variant in ("actual_no_memory_fk", "fresh_memory_fk")
        for receipts in (False, True)
        if actions == _action_objects(variant=variant, receipts=receipts, errors=False)
    ), None)
    if candidate is None:
        raise ValueError("unsupported current Actions source declaration")
    variant, receipts = candidate
    storage._no_inbound(db, {"errors"})
    configs = _configs_action_triggers(db, variant, include_followup_sync=True,
                                       include_business_sync=True, include_task_delivery=True)
    from app.action_email_storage import _external_dependencies
    _external_dependencies(db, targets=("errors",))
    _external_dependencies(db, targets=("actions",), allowed_trigger_names=(
        *_exact_email_blockers(db), *(name for name, _ in configs),
        *_exact_sync_legacy_triggers(db),
    ))
    target = _action_objects(variant=variant, receipts=receipts, errors=True)
    old_names = {storage.sqlite_schema_name(item["name"]) for item in actions}
    for item in target:
        if storage.sqlite_schema_name(item["name"]) not in old_names and db.execute(
            "select 1 from main.sqlite_schema where name=? collate nocase", (item["name"],)
        ).fetchone():
            raise ValueError("unsupported error target name collision")
    _sequence(db, "errors")
    if _sequence(db, "actions:errors") is not None:
        raise ValueError("unsupported existing error action sequence")
    return {"source_table": declaration, "source_actions": actions,
            "reply_variant": variant, "skill_config_receipts": receipts,
            "configs_triggers": configs, "action_columns": _columns(db, "actions")}


def _facts(db, columns, *, errors):
    where = "record_type<>'errors'" if errors else "1"
    names = ",".join(map(_quote, columns))
    return (
        [tuple(row) for row in db.execute(f"select {names} from main.actions where {where} order by id")],
        [tuple(row) for row in db.execute(
            f"select {','.join('typeof(' + _quote(c) + ')' for c in columns)} "
            f"from main.actions where {where} order by id")],
    )


def _error_facts(db):
    names = ",".join(map(_quote, ERROR_FIELDS))
    return (
        [tuple(row) for row in db.execute(f"select {names} from main.errors order by id")],
        [tuple(row) for row in db.execute(
            f"select {','.join('typeof(' + _quote(c) + ')' for c in ERROR_FIELDS)} "
            "from main.errors order by id")],
    )


def _projected_facts(db):
    query = error_projection().replace("from actions a", "from main.actions a")
    return (
        [tuple(row) for row in db.execute(f"select * from ({query}) order by id")],
        [tuple(row) for row in db.execute(
            f"select {','.join('typeof(' + _quote(c) + ')' for c in ERROR_FIELDS)} "
            f"from ({query}) order by id")],
    )


def _create_actions(db, objects):
    db.execute(next(item["sql"] for item in objects if item["type"] == "table"))
    for item in objects:
        if item["type"] == "index":
            _create_main_index(db, item["sql"])
    return [item for item in objects if item["type"] == "trigger"]


def migrate_action_error_schema(db: sqlite3.Connection, source: dict) -> dict:
    _require_fk_off(db)
    if capture_action_error_source(db) != source:
        raise ValueError("error source changed after capture")
    columns = tuple(source["action_columns"])
    retained = _facts(db, columns, errors=False)
    originals = _error_facts(db)
    action_seq, error_seq = _sequence(db, "actions"), _sequence(db, "errors")
    _drop_configs_triggers(db, source)
    db.execute("drop table main.errors")
    db.execute("drop table main.actions")
    triggers = _create_actions(db, _action_objects(
        variant=source["reply_variant"], receipts=source["skill_config_receipts"], errors=True))
    _insert_rows(db, "actions", columns, retained[0])
    _set_sequence(db, "actions", action_seq)
    _set_sequence(db, "actions:errors", error_seq)
    for row in originals[0]:
        values = dict(zip(ERROR_FIELDS, row, strict=True))
        db.execute(
            "insert into main.actions(record_type,legacy_key,payload_json) values('errors',?,?)",
            (str(values.pop("id")), storage._native_payload(values)),
        )
    if _projected_facts(db) != originals:
        raise ValueError("error migrated facts differ")
    _restore_triggers(db, triggers)
    _restore_configs_triggers(db, source)
    if _facts(db, columns, errors=True) != retained:
        raise ValueError("retained Actions changed")
    return source


def restore_action_error_schema(db: sqlite3.Connection, state: dict) -> dict:
    _require_fk_off(db)
    if type(state) is not dict or set(state) != {
        "source_table", "source_actions", "reply_variant", "skill_config_receipts",
        "configs_triggers", "action_columns",
    } or state["source_table"] not in _source_declarations() or state["source_actions"] != _action_objects(
        variant=state["reply_variant"], receipts=state["skill_config_receipts"], errors=False):
        raise ValueError("unsupported error inverse metadata")
    if tuple(state["action_columns"]) != _declared_action_columns(state["source_actions"]):
        raise ValueError("unsupported error inverse metadata Action columns")
    if storage._objects(db, "actions") != _action_objects(
        variant=state["reply_variant"], receipts=state["skill_config_receipts"], errors=True):
        raise ValueError("unsupported error target Actions declaration")
    if db.execute("select 1 from main.sqlite_schema where name='errors' collate nocase").fetchone():
        raise ValueError("unsupported error source name collision")
    current_configs = tuple(db.execute(
        "select name,sql from main.sqlite_schema where type='trigger' and tbl_name='configs' order by name"))
    if current_configs != tuple(tuple(pair) for pair in state["configs_triggers"]):
        raise ValueError("unsupported inverse Config trigger declarations")
    from app.action_email_storage import _external_dependencies
    _external_dependencies(db, targets=("actions",), allowed_trigger_names=(
        *_exact_email_blockers(db), *(name for name, _ in current_configs),
        *_exact_sync_legacy_triggers(db),
    ))
    columns = tuple(state["action_columns"])
    retained = _facts(db, columns, errors=True)
    current = _projected_facts(db)
    action_seq = _sequence(db, "actions")
    error_seq = _sequence(db, "actions:errors")
    _drop_configs_triggers(db, {"configs_triggers": current_configs})
    db.execute("drop table main.actions")
    triggers = _create_actions(db, state["source_actions"])
    _insert_rows(db, "actions", columns, retained[0])
    table = next(item for item in state["source_table"]["objects"] if item[0] == "table")
    db.execute(table[3])
    for item in reversed(state["source_table"]["objects"]):
        if item[0] == "index" and item[3] is not None:
            _create_main_index(db, item[3])
    _insert_rows(db, "errors", ERROR_FIELDS, current[0])
    if _error_facts(db) != current:
        raise ValueError("error inverse facts differ")
    _set_sequence(db, "actions", action_seq)
    _set_sequence(db, "errors", error_seq)
    _set_sequence(db, "actions:errors", None)
    _restore_triggers(db, triggers)
    _restore_configs_triggers(db, {"configs_triggers": current_configs})
    if _facts(db, columns, errors=False) != retained:
        raise ValueError("retained Actions inverse differs")
    if _source_declaration(db, "errors") != state["source_table"]:
        raise ValueError("error inverse declaration differs")
    return {"count": len(current[0])}
