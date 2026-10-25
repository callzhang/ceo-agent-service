"""Retire nine legacy controls, with a neutral-default current-fact inverse.

No original assessment values are captured in metadata or durable rows.
TEMP transfer contains the transformed current payloads and is removed before
commit. All retained scalar cells, identities, links and allocator rows survive.
"""
import json

from app import action_storage as actions
from app.action_email_storage import _columns, _quote
from app.action_reply_storage import _source_declaration
from app.business_definitions import business_column
from app.business_legacy_owner_storage import _business_declaration
from app.business_legacy_fields import (
    CURRENT_BUSINESS_FIELDS, CURRENT_SYNC_FIELDS, RETIRED_DEFAULTS,
    current_action_objects, current_business_declaration,
)
from app.logical_run_definitions import logical_action_objects
from app.task_lifecycle_jsonb import _array, _framed

STAGES = {"business": "_legacy_field_business", "actions": "_legacy_field_actions"}
WATER = "_legacy_field_water"


def _require_source(db, *, retired):
    from app.business_candidate_storage import _collision
    from app.model_action_schema import _dependencies
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("legacy field conversion requires caller-owned FK-off transaction")
    _collision(db, (*STAGES, *STAGES.values(), WATER))
    business = current_business_declaration() if retired else _business_declaration(target=True)
    factory = current_action_objects if retired else logical_action_objects
    variant = next((variant for variant in actions.REPLY_VARIANTS
                    if actions._objects(db, "actions") == factory(variant)), None)
    if variant is None or _source_declaration(db, "business") != business:
        raise ValueError("unsupported legacy field source declaration")
    if _source_declaration(db, "business_legacy_links")["sql"] is not None:
        raise ValueError("retired legacy links cannot be present")
    _dependencies(db)
    from app.action_email_storage import _external_dependencies
    _external_dependencies(db, targets=("business",), allowed_trigger_names=tuple(
        obj["name"] for obj in actions._objects(db, "actions") if obj["type"] == "trigger"))
    if db.execute("pragma quick_check").fetchone()[0] != "ok":
        raise ValueError("legacy field source integrity is invalid")
    return variant


def _retained_facts(db):
    facts = {}
    for kind in RETIRED_DEFAULTS:
        fields = CURRENT_SYNC_FIELDS[kind] if kind == "work_updates" else CURRENT_BUSINESS_FIELDS[kind]
        columns = (["cast(legacy_key as integer)" if field == "id" else "sync_" + field
                    for field in fields] if kind == "work_updates" else
                   [business_column(kind, field) for field in fields] + ["import_task_id"])
        columns = ["id", *columns]
        values = ",".join(columns) + "," + ",".join(f"typeof({column})" for column in columns)
        table = "actions" if kind == "work_updates" else "business"
        facts[kind] = tuple(db.execute(f"select {values} from main.{table} where record_type=? order by id", (kind,)))
    return facts


def _business_payload(*, inverse):
    clauses, parameters = [], []
    for kind in ("work_project", "work_todo"):
        defaults = RETIRED_DEFAULTS[kind]
        operation = "json_set" if inverse else "json_remove"
        arguments = []
        for field, value in defaults.items():
            arguments.append("?")
            parameters.append("$." + field)
            if inverse:
                arguments.append("?")
                parameters.append(value)
        clauses.append(f"when record_type='{kind}' then {operation}(payload_json,{','.join(arguments)})")
    return "case " + " ".join(clauses) + " else payload_json end", parameters


def _update_payload(db, *, inverse):
    from app.action_followup_sync_definitions import SYNC_FIELDS
    defaults = {field: db.execute("select jsonb(?)", (json.dumps(value),)).fetchone()[0]
                for field, value in RETIRED_DEFAULTS["work_updates"].items()}
    old = SYNC_FIELDS["work_updates"][1:]
    current = CURRENT_SYNC_FIELDS["work_updates"][1:]

    def convert(blob):
        width, cells = _array(blob)
        fields = current if inverse else old
        if len(cells) != len(fields):
            raise ValueError("legacy update payload field count differs")
        values = dict(zip(fields, cells, strict=True))
        target = old if inverse else current
        return _framed((defaults[field] if field in defaults else values[field]
                        for field in target), width)
    return convert


def _rebuild(db, variant, *, inverse):
    from app.business_schema_helpers import _create_auxiliary
    before = _retained_facts(db)
    findings = tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))
    columns = {table: _columns(db, table) for table in STAGES}
    db.create_function("ceo_legacy_update_fields", 1, _update_payload(db, inverse=inverse))
    for table, stage in STAGES.items():
        parameters = []
        payload = "case when record_type='work_updates' then ceo_legacy_update_fields(payload_json) else payload_json end"
        if table == "business":
            payload, parameters = _business_payload(inverse=inverse)
        selection = ",".join(payload + " as payload_json" if column == "payload_json" else _quote(column)
                             for column in columns[table])
        db.execute(f"create temp table {stage} as select {selection} from main.{table}", parameters)
    db.execute(f"create temp table {WATER} as select rowid,name,seq from main.sqlite_sequence")
    business = _business_declaration(target=True) if inverse else current_business_declaration()
    action_objects = logical_action_objects(variant) if inverse else current_action_objects(variant)
    db.execute("drop table main.actions")
    db.execute("drop table main.business")
    db.execute(business["sql"])
    db.execute(next(obj["sql"] for obj in action_objects if obj["type"] == "table"))
    for table, stage in STAGES.items():
        names = ",".join(map(_quote, columns[table]))
        db.execute(f"insert into main.{table}({names}) select {names} from temp.{stage} order by id")
    _create_auxiliary(db, business)
    for category in ("index", "trigger"):
        for obj in action_objects:
            if obj["type"] == category:
                db.execute(obj["sql"])
    db.execute("delete from main.sqlite_sequence")
    db.execute(f"insert into main.sqlite_sequence(rowid,name,seq) select rowid,name,seq from temp.{WATER}")
    for table, stage in STAGES.items():
        names = ",".join(map(_quote, columns[table]))
        values = names + "," + ",".join("typeof(" + _quote(column) + ")" for column in columns[table])
        for left, right in ((f"main.{table}", f"temp.{stage}"), (f"temp.{stage}", f"main.{table}")):
            if db.execute(f"select {values} from {left} except select {values} from {right} limit 1").fetchone():
                raise ValueError("legacy field conversion changed stored values or types")
    if tuple(db.execute("select rowid,name,seq,typeof(seq) from main.sqlite_sequence order by rowid")) != tuple(
            db.execute(f"select rowid,name,seq,typeof(seq) from temp.{WATER} order by rowid")):
        raise ValueError("legacy field conversion changed allocator rows")
    if (before != _retained_facts(db)
            or findings != tuple(sorted(tuple(row) for row in db.execute("pragma foreign_key_check")))
            or _source_declaration(db, "business") != business
            or actions._objects(db, "actions") != action_objects
            or db.execute("pragma quick_check").fetchone()[0] != "ok"):
        raise ValueError("legacy field conversion changed retained facts or declarations")
    for stage in (*STAGES.values(), WATER):
        db.execute(f"drop table temp.{stage}")


def migrate_legacy_business_fields(db):
    variant = _require_source(db, retired=False)
    _rebuild(db, variant, inverse=False)
    return {"reply_variant": variant}


def restore_legacy_business_fields(db, metadata):
    variant = _require_source(db, retired=True)
    if metadata != {"reply_variant": variant}:
        raise ValueError("unsupported legacy field inverse metadata")
    _rebuild(db, variant, inverse=True)
