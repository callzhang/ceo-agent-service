"""Unselected, exact-source Feedback conversion; caller owns FK-off transaction."""

import sqlite3
from contextlib import closing
from functools import lru_cache

from app import action_storage as storage
from app.action_feedback_definitions import FEEDBACK_FIELDS, FEEDBACK_KINDS, feedback_projection
from app.action_historical_projections import FEEDBACK_FRESH_EVENTS, historical_feedback_projection
from app.action_email_storage import _columns, _insert_rows, _set_sequence, _create_main_index, _restore_triggers, _external_dependencies
from app.action_followup_sync_storage import _require_fk_off, _configs_action_triggers, _drop_configs_triggers, _restore_configs_triggers, _exact_email_blockers
from app.action_task_delivery_storage import _source_declaration, _quote, _exact_sync_legacy_triggers

SOURCE_SQL = {'actual': {'feedback_events': 'CREATE TABLE feedback_events (\n'
                               '                    key text primary key,\n'
                               '                    feedback_token text not null,\n'
                               "                    rating text not null default '',\n"
                               "                    rating_label text not null default '',\n"
                               "                    comment text not null default '',\n"
                               "                    original_text text not null default '',\n"
                               "                    reply_text text not null default '',\n"
                               "                    source text not null default '',\n"
                               "                    received_at text not null default '',\n"
                               "                    raw_json text not null default '{}',\n"
                               '                    created_at text not null default '
                               'current_timestamp,\n'
                               '                    updated_at text not null default '
                               'current_timestamp\n'
                               "                , resolved_at text not null default '')",
            'feedback_iteration_decision_items': 'CREATE TABLE feedback_iteration_decision_items '
                                                 '(\n'
                                                 '                    decision_id integer not '
                                                 'null,\n'
                                                 '                    feedback_key text not null,\n'
                                                 '                    round_id integer not null,\n'
                                                 '                    primary key(decision_id, '
                                                 'feedback_key),\n'
                                                 '                    foreign key(decision_id) '
                                                 'references '
                                                 'tasks("feedback_iteration_decisions__id"),\n'
                                                 '                    foreign key(feedback_key) '
                                                 'references '
                                                 'tasks("feedback_processing_items__feedback_key"),\n'
                                                 '                    foreign key(round_id) '
                                                 'references '
                                                 'tasks("feedback_processing_rounds__id")\n'
                                                 '                )',
            'feedback_processing_transitions': 'CREATE TABLE feedback_processing_transitions (\n'
                                               '                    id integer primary key '
                                               'autoincrement,\n'
                                               '                    feedback_key text not null,\n'
                                               '                    round_id integer not null '
                                               'default 0,\n'
                                               '                    batch_id text not null default '
                                               "'',\n"
                                               '                    from_status text not null '
                                               "default ''\n"
                                               "                        check (from_status in ('', "
                                               "'pending', 'processing', 'resolved')),\n"
                                               '                    to_status text not null\n'
                                               '                        check (to_status in '
                                               "('pending', 'processing', 'resolved')),\n"
                                               '                    reason text not null default '
                                               "'',\n"
                                               '                    workbench_task_id text not '
                                               "null default '',\n"
                                               '                    workbench_turn_id text not '
                                               "null default '',\n"
                                               '                    created_at text not null '
                                               'default current_timestamp\n'
                                               '                )'},
 'fresh': {'feedback_events': 'CREATE TABLE feedback_events (\n'
                              '                    key text primary key,\n'
                              '                    feedback_token text not null,\n'
                              "                    rating text not null default '',\n"
                              "                    rating_label text not null default '',\n"
                              "                    comment text not null default '',\n"
                              "                    original_text text not null default '',\n"
                              "                    reply_text text not null default '',\n"
                              "                    source text not null default '',\n"
                              "                    received_at text not null default '',\n"
                              "                    resolved_at text not null default '',\n"
                              "                    raw_json text not null default '{}',\n"
                              '                    created_at text not null default '
                              'current_timestamp,\n'
                              '                    updated_at text not null default '
                              'current_timestamp\n'
                              '                )',
           'feedback_iteration_decision_items': 'CREATE TABLE feedback_iteration_decision_items (\n'
                                                '                    decision_id integer not '
                                                'null,\n'
                                                '                    feedback_key text not null,\n'
                                                '                    round_id integer not null,\n'
                                                '                    primary key(decision_id, '
                                                'feedback_key),\n'
                                                '                    foreign key(decision_id) '
                                                'references '
                                                'tasks("feedback_iteration_decisions__id"),\n'
                                                '                    foreign key(feedback_key) '
                                                'references '
                                                'tasks("feedback_processing_items__feedback_key"),\n'
                                                '                    foreign key(round_id) '
                                                'references '
                                                'tasks("feedback_processing_rounds__id")\n'
                                                '                )',
           'feedback_processing_transitions': 'CREATE TABLE feedback_processing_transitions (\n'
                                              '                    id integer primary key '
                                              'autoincrement,\n'
                                              '                    feedback_key text not null,\n'
                                              '                    round_id integer not null '
                                              'default 0,\n'
                                              '                    batch_id text not null default '
                                              "'',\n"
                                              '                    from_status text not null '
                                              "default ''\n"
                                              "                        check (from_status in ('', "
                                              "'pending', 'processing', 'resolved')),\n"
                                              '                    to_status text not null\n'
                                              '                        check (to_status in '
                                              "('pending', 'processing', 'resolved')),\n"
                                              '                    reason text not null default '
                                              "'',\n"
                                              '                    workbench_task_id text not null '
                                              "default '',\n"
                                              '                    workbench_turn_id text not null '
                                              "default '',\n"
                                              '                    created_at text not null '
                                              'default current_timestamp\n'
                                              '                )'}}
SOURCE_INDEX_SQL = {'feedback_events': ('CREATE INDEX idx_feedback_events_token\n'
                     '                    on feedback_events(feedback_token, received_at)',),
 'feedback_iteration_decision_items': ('CREATE INDEX '
                                       'idx_feedback_iteration_decision_items_feedback_round\n'
                                       '                    on '
                                       'feedback_iteration_decision_items(feedback_key, round_id, '
                                       'decision_id)',),
 'feedback_processing_transitions': ('CREATE INDEX idx_feedback_processing_transitions_feedback\n'
                                     '                    on '
                                     'feedback_processing_transitions(feedback_key, id desc)',
                                     'CREATE INDEX '
                                     'idx_feedback_processing_transitions_batch_round\n'
                                     '                    on '
                                     'feedback_processing_transitions(batch_id, round_id, id)',
                                     'CREATE INDEX idx_feedback_processing_transitions_round\n'
                                     '                    on '
                                     'feedback_processing_transitions(round_id, id)')}
SOURCE_TRIGGER_SQL = ('CREATE TRIGGER trg_feedback_iteration_decision_items_immutable_delete\n'
 'before delete on feedback_iteration_decision_items\n'
 'begin\n'
 "    select raise(abort, 'feedback iteration decision items are append-only');\n"
 'end',
 'CREATE TRIGGER trg_feedback_iteration_decision_items_immutable_update\n'
 'before update on feedback_iteration_decision_items\n'
 'begin\n'
 "    select raise(abort, 'feedback iteration decision items are append-only');\n"
 'end')


@lru_cache(maxsize=2)
def _expected_sources(variant):
    with closing(sqlite3.connect(":memory:")) as db:
        for kind in FEEDBACK_KINDS:
            db.execute(SOURCE_SQL[variant][kind])
            for sql in SOURCE_INDEX_SQL[kind]:
                db.execute(sql)
            if kind == "feedback_iteration_decision_items":
                for sql in SOURCE_TRIGGER_SQL:
                    db.execute(sql)
        return {kind: _source_declaration(db, kind) for kind in FEEDBACK_KINDS}


def _action_objects(variant, receipts, *, feedback):
    return storage._current_objects(
        include_reply=True, reply_variant=variant, include_email=True,
        include_followup_sync=True, include_business_sync=True,
        include_skill_config_receipts=receipts, include_task_delivery=True,
        include_errors=True, include_feedback=feedback,
    )


def _sequence(db, name):
    rows = db.execute("select seq from main.sqlite_sequence where name=?", (name,)).fetchall()
    if len(rows) > 1 or rows and (type(rows[0][0]) is not int or not 0 <= rows[0][0] < 2**63):
        raise ValueError("unsupported Feedback sequence")
    return rows[0][0] if rows else None


def _facts(db, table, columns, *, where="1"):
    names = ",".join(map(_quote, columns))
    order = "id" if table == "actions" else "rowid"
    return (
        [tuple(row) for row in db.execute(f"select {names} from main.{_quote(table)} where {where} order by {order}")],
        [tuple(row) for row in db.execute(
            f"select {','.join('typeof(' + _quote(c) + ')' for c in columns)} "
            f"from main.{_quote(table)} where {where} order by {order}")],
    )


def _source_facts(db, kind, variant):
    columns = FEEDBACK_FRESH_EVENTS if kind == "feedback_events" and variant == "fresh" else FEEDBACK_FIELDS[kind]
    return _facts(db, kind, ("rowid", *columns))


def _projected_facts(db, kind, variant):
    query = historical_feedback_projection(kind, source_variant=variant, with_rowid=True).replace(
        "from actions a", "from main.actions a")
    columns = ("_source_rowid", *(FEEDBACK_FRESH_EVENTS if kind == "feedback_events" and variant == "fresh" else FEEDBACK_FIELDS[kind]))
    names = ",".join(map(_quote, columns))
    return (
        [tuple(row) for row in db.execute(f"select {names} from ({query}) order by _source_rowid")],
        [tuple(row) for row in db.execute(
            f"select {','.join('typeof(' + _quote(c) + ')' for c in columns)} from ({query}) order by _source_rowid")],
    )


def _retained(db, columns, *, feedback):
    where = ("record_type not in ('feedback_events','feedback_processing_transitions',"
             "'feedback_iteration_decision_items')" if feedback else "1")
    return _facts(db, "actions", columns, where=where)


def _create_actions(db, objects):
    db.execute(next(obj["sql"] for obj in objects if obj["type"] == "table"))
    for obj in objects:
        if obj["type"] == "index":
            _create_main_index(db, obj["sql"])
    return [obj for obj in objects if obj["type"] == "trigger"]


def capture_action_feedback_source(db: sqlite3.Connection) -> dict:
    declarations = {kind: _source_declaration(db, kind) for kind in FEEDBACK_KINDS}
    variant = next((name for name in ("actual", "fresh")
                    if declarations == _expected_sources(name)), None)
    if variant is None:
        raise ValueError("unsupported Feedback source declaration")
    reply_variant = "actual_no_memory_fk" if variant == "actual" else "fresh_memory_fk"
    actions = storage._objects(db, "actions")
    receipts = next((value for value in (False, True)
                     if actions == _action_objects(reply_variant, value, feedback=False)), None)
    if receipts is None:
        raise ValueError("unsupported Feedback source Actions declaration")
    storage._no_inbound(db, set(FEEDBACK_KINDS))
    configs = _configs_action_triggers(
        db, reply_variant, include_followup_sync=True, include_business_sync=True,
        include_task_delivery=True, include_errors=True)
    _external_dependencies(db, targets=FEEDBACK_KINDS)
    _external_dependencies(db, targets=("actions",), allowed_trigger_names=(
        *_exact_email_blockers(db), *(name for name, _ in configs),
        *_exact_sync_legacy_triggers(db),
    ))
    old_names = {storage.sqlite_schema_name(obj["name"]) for obj in actions}
    for obj in _action_objects(reply_variant, receipts, feedback=True):
        if storage.sqlite_schema_name(obj["name"]) not in old_names and db.execute(
            "select 1 from main.sqlite_schema where name=? collate nocase", (obj["name"],)
        ).fetchone():
            raise ValueError("unsupported Feedback target name collision")
    if any(_sequence(db, "actions:" + kind) is not None for kind in FEEDBACK_KINDS):
        raise ValueError("unsupported existing Feedback action sequence")
    return {"source_variant": variant, "reply_variant": reply_variant,
            "skill_config_receipts": receipts, "source_tables": declarations,
            "source_actions": actions, "configs_triggers": configs,
            "action_columns": _columns(db, "actions"),
            "transition_sequence": _sequence(db, "feedback_processing_transitions")}


def _insert_feedback_row(db, kind, row, variant):
    columns = FEEDBACK_FRESH_EVENTS if kind == "feedback_events" and variant == "fresh" else FEEDBACK_FIELDS[kind]
    source_rowid, *fields = row
    values = dict(zip(columns, fields, strict=True))
    if kind == "feedback_iteration_decision_items":
        db.execute("insert into main.actions(record_type,legacy_key,payload_json,fb_decision_id,fb_feedback_key,fb_round_id) values(?,?,?,?,?,?)",
                   (kind, str(source_rowid), "{}", values["decision_id"], values["feedback_key"], values["round_id"]))
    else:
        if kind == "feedback_processing_transitions":
            values.pop("id")
        db.execute("insert into main.actions(record_type,legacy_key,payload_json) values(?,?,?)",
                   (kind, str(source_rowid), storage._native_payload(values)))


def migrate_action_feedback_schema(db: sqlite3.Connection, source: dict) -> dict:
    _require_fk_off(db)
    if capture_action_feedback_source(db) != source:
        raise ValueError("Feedback source changed after capture")
    columns = tuple(source["action_columns"])
    retained = _retained(db, columns, feedback=False)
    family = {kind: _source_facts(db, kind, source["source_variant"]) for kind in FEEDBACK_KINDS}
    action_sequence = _sequence(db, "actions")
    _drop_configs_triggers(db, source)
    for kind in reversed(FEEDBACK_KINDS):
        db.execute(f"drop table main.{_quote(kind)}")
    db.execute("drop table main.actions")
    triggers = _create_actions(db, _action_objects(source["reply_variant"],
                                                  source["skill_config_receipts"], feedback=True))
    _insert_rows(db, "actions", columns, retained[0])
    _set_sequence(db, "actions", action_sequence)
    for kind in FEEDBACK_KINDS:
        for row in family[kind][0]:
            _insert_feedback_row(db, kind, row, source["source_variant"])
        if _projected_facts(db, kind, source["source_variant"]) != family[kind]:
            raise ValueError(f"{kind} migrated facts differ")
    _set_sequence(db, "actions:feedback_processing_transitions", source["transition_sequence"])
    _restore_triggers(db, triggers)
    _restore_configs_triggers(db, source)
    if _retained(db, columns, feedback=True) != retained:
        raise ValueError("retained Actions changed")
    return source


def restore_action_feedback_schema(db: sqlite3.Connection, state: dict) -> dict:
    _require_fk_off(db)
    if type(state) is not dict or set(state) != {
        "source_variant", "reply_variant", "skill_config_receipts", "source_tables",
        "source_actions", "configs_triggers", "action_columns", "transition_sequence",
    } or state["source_variant"] not in ("actual", "fresh") or state["source_tables"] != _expected_sources(state["source_variant"]):
        raise ValueError("unsupported Feedback inverse metadata")
    if state["reply_variant"] != ("actual_no_memory_fk" if state["source_variant"] == "actual" else "fresh_memory_fk") or state["source_actions"] != _action_objects(
        state["reply_variant"], state["skill_config_receipts"], feedback=False):
        raise ValueError("unsupported Feedback inverse Actions source")
    with closing(sqlite3.connect(":memory:")) as probe:
        probe.execute(next(obj["sql"] for obj in state["source_actions"] if obj["type"] == "table"))
        if tuple(state["action_columns"]) != _columns(probe, "actions"):
            raise ValueError("unsupported Feedback inverse Action columns")
    if storage._objects(db, "actions") != _action_objects(
        state["reply_variant"], state["skill_config_receipts"], feedback=True):
        raise ValueError("unsupported Feedback target Actions declaration")
    if any(db.execute("select 1 from main.sqlite_schema where name=? collate nocase", (kind,)).fetchone()
           for kind in FEEDBACK_KINDS):
        raise ValueError("unsupported Feedback source name collision")
    configs = tuple(db.execute("select name,sql from main.sqlite_schema where type='trigger' and tbl_name='configs' order by name"))
    if configs != tuple(tuple(row) for row in state["configs_triggers"]):
        raise ValueError("unsupported Feedback inverse Config triggers")
    _external_dependencies(db, targets=("actions",), allowed_trigger_names=(
        *_exact_email_blockers(db), *(name for name, _ in configs),
        *_exact_sync_legacy_triggers(db),
    ))
    columns = tuple(state["action_columns"])
    retained = _retained(db, columns, feedback=True)
    family = {kind: _projected_facts(db, kind, state["source_variant"]) for kind in FEEDBACK_KINDS}
    action_sequence = _sequence(db, "actions")
    transition_sequence = _sequence(db, "actions:feedback_processing_transitions")
    _drop_configs_triggers(db, {"configs_triggers": configs})
    db.execute("drop table main.actions")
    action_triggers = _create_actions(db, state["source_actions"])
    _insert_rows(db, "actions", columns, retained[0])
    source_triggers = []
    for kind in FEEDBACK_KINDS:
        definition = state["source_tables"][kind]
        db.execute(next(obj[3] for obj in definition["objects"] if obj[0] == "table"))
        index_sql = {obj[1]: obj[3] for obj in definition["objects"]
                     if obj[0] == "index" and obj[3] is not None}
        for index in reversed(definition["indexes"]):
            sql = index_sql.get(index["list"][1])
            if sql is not None:
                _create_main_index(db, sql)
        for obj in definition["objects"]:
            if obj[0] == "trigger":
                source_triggers.append(obj[3])
        original_columns = FEEDBACK_FRESH_EVENTS if kind == "feedback_events" and state["source_variant"] == "fresh" else FEEDBACK_FIELDS[kind]
        _insert_rows(db, kind, ("rowid", *original_columns), family[kind][0])
        if _source_facts(db, kind, state["source_variant"]) != family[kind]:
            raise ValueError(f"{kind} inverse facts differ")
    _set_sequence(db, "actions", action_sequence)
    _set_sequence(db, "feedback_processing_transitions", transition_sequence)
    _set_sequence(db, "actions:feedback_processing_transitions", None)
    _restore_triggers(db, action_triggers)
    for sql in source_triggers:
        db.execute(sql)
    _restore_configs_triggers(db, {"configs_triggers": configs})
    if _retained(db, columns, feedback=False) != retained:
        raise ValueError("retained Actions inverse differs")
    if any(_source_declaration(db, kind) != state["source_tables"][kind] for kind in FEEDBACK_KINDS):
        raise ValueError("Feedback inverse declaration differs")
    return {"counts": {kind: len(family[kind][0]) for kind in FEEDBACK_KINDS}}


FEEDBACK_DEFAULTS = {
    "feedback_events": {"rating": "''", "rating_label": "''", "comment": "''",
                        "original_text": "''", "reply_text": "''", "source": "''",
                        "received_at": "''", "raw_json": "'{}'",
                        "created_at": "current_timestamp", "updated_at": "current_timestamp",
                        "resolved_at": "''"},
    "feedback_processing_transitions": {"round_id": "0", "batch_id": "''",
                                        "from_status": "''", "reason": "''",
                                        "workbench_task_id": "''", "workbench_turn_id": "''",
                                        "created_at": "current_timestamp"},
    "feedback_iteration_decision_items": {},
}


def _materialize(db, kind, values):
    columns = FEEDBACK_FIELDS[kind]
    if set(values) - set(columns):
        raise ValueError("unknown Feedback field")
    result = dict(values)
    missing = [field for field in columns
               if field not in result and field in FEEDBACK_DEFAULTS[kind]]
    if missing:
        defaults = db.execute("select " + ",".join(FEEDBACK_DEFAULTS[kind][field]
                                                    for field in missing)).fetchone()
        result.update(zip(missing, defaults, strict=True))
    if kind == "feedback_events" and "key" not in result:
        result["key"] = None
    return result


def _next_feedback_id(db, kind):
    if kind != "feedback_processing_transitions":
        return storage._email_rowid_next(db, kind)
    maximum = db.execute("select max(cast(legacy_key as integer)) from main.actions where record_type=?",
                         (kind,)).fetchone()[0]
    high = max(maximum or 0, _sequence(db, "actions:" + kind) or 0)
    if high == 2**63 - 1:
        if db.in_transaction:
            db.execute("rollback")
        storage._raise_autoincrement_full()
    return high + 1


def insert_feedback_action(db: sqlite3.Connection, kind: str, values: dict,
                           *, source_rowid=None):
    """Insert one native-shaped Feedback row and return its source ROWID.

    A nullable Event key is independent of its ROWID. Decision items carry
    their three parent endpoints in physical columns, with an empty payload.
    """
    if kind not in FEEDBACK_KINDS:
        raise ValueError("unknown Feedback kind")
    storage._email_lock(db)
    fields = _materialize(db, kind, values)
    supplied_id = (source_rowid if source_rowid is not None else
                   fields.get("id") if kind == "feedback_processing_transitions" else None)
    identity = (_next_feedback_id(db, kind) if db.execute(
        "select typeof(?)", (supplied_id,)).fetchone()[0] == "null"
                else storage._native_integer_primary_key(supplied_id))
    if kind == "feedback_processing_transitions":
        fields.pop("id", None)
    if kind == "feedback_iteration_decision_items":
        db.execute("insert into main.actions(_kind,legacy_key,payload_json,"
                   "fb_decision_id,fb_feedback_key,fb_round_id) values(?,?,?,?,?,?)",
                   (storage.ACTION_RECORD_TYPES.code(kind), str(identity), storage._action_payload(db, kind, {}), fields["decision_id"], fields["feedback_key"],
                    fields["round_id"]))
    else:
        db.execute("insert into main.actions(_kind,legacy_key,payload_json) values(?,?,?)",
                   (storage.ACTION_RECORD_TYPES.code(kind), str(identity), storage._action_payload(db, kind, fields)))
    return identity


def upsert_feedback_event(db: sqlite3.Connection, values: dict):
    """Apply the source Event key conflict behavior in one caller transaction."""
    if "feedback_token" not in values or set(values) - set(FEEDBACK_FIELDS["feedback_events"]):
        raise ValueError("invalid Feedback Event upsert")
    storage._email_lock(db)
    key = values.get("key")
    row = (db.execute("select cast(legacy_key as integer) from main.actions "
                      "where record_type='feedback_events' and fb_event_key=?", (key,)).fetchone()
           if key is not None else None)
    if row is None:
        return insert_feedback_action(db, "feedback_events", values)
    materialized = _materialize(db, "feedback_events", values)
    updates = {field: materialized[field] for field in FEEDBACK_FIELDS["feedback_events"][1:10]}
    set_feedback_action(db, "feedback_events", row[0], updates,
                        update_timestamp=True)
    return row[0]


def insert_feedback_decision_items(db: sqlite3.Connection, items):
    """Insert an ordered decision-item batch within the caller's transaction."""
    return [insert_feedback_action(db, "feedback_iteration_decision_items", item)
            for item in items]


def set_feedback_action(db: sqlite3.Connection, kind: str, source_rowid: int,
                        values: dict, *, update_timestamp=False):
    """Apply native-shaped scalar changes to an Event or Transition payload."""
    if kind not in FEEDBACK_KINDS[:2] or (not values and not update_timestamp) or set(values) - set(FEEDBACK_FIELDS[kind]):
        raise ValueError("unsupported Feedback update")
    if kind == "feedback_processing_transitions" and "id" in values:
        raise ValueError("Transition ID update requires a separate source operation")
    query = feedback_projection(kind, with_rowid=True)
    fields = FEEDBACK_FIELDS[kind]
    row = db.execute("select " + ",".join(map(_quote, fields)) + " from (" + query +
                     ") where _source_rowid=?",
                     (source_rowid,)).fetchone()
    if row is None:
        return 0
    current = dict(zip(fields, row, strict=True))
    current.update(values)
    if kind == "feedback_processing_transitions":
        current.pop("id")
    expression = ("jsonb_set(?," + repr(storage._action_path(kind, "updated_at")) + ",current_timestamp)" if update_timestamp
                  else "?")
    result = db.execute("update main.actions set payload_json=" + expression +
                        " where record_type=? and legacy_key=?",
                        (storage._action_payload(db, kind, current), kind, str(source_rowid)))
    return result.rowcount


def resolve_feedback_events(db: sqlite3.Connection, keys, timestamp: str) -> int:
    """Resolve the selected source events with one native-equivalent UPDATE."""
    placeholders = ",".join("?" for _ in keys)
    result = db.execute(
        "update main.actions set payload_json=jsonb_set(payload_json,"
        + repr(storage._action_path("feedback_events", "resolved_at")) + ",?,"
        + repr(storage._action_path("feedback_events", "updated_at")) + ",?) "
        "where record_type='feedback_events' and fb_event_key in (" + placeholders + ") "
        "and trim(fb_event_resolved_at)=''",
        (timestamp, timestamp, *keys),
    )
    return result.rowcount


def reopen_feedback_event(db: sqlite3.Connection, key: str) -> int:
    """Clear one resolved source event with the original statement clock."""
    result = db.execute(
        "update main.actions set payload_json=jsonb_set(payload_json,"
        + repr(storage._action_path("feedback_events", "resolved_at")) + ",'',"
        + repr(storage._action_path("feedback_events", "updated_at")) + ",current_timestamp) "
        "where record_type='feedback_events' and fb_event_key=? "
        "and trim(fb_event_resolved_at)<>''",
        (key,),
    )
    return result.rowcount
