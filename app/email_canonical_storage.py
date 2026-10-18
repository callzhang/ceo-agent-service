"""Opt-in canonical Email conversion; callers select current13 until a later unit."""

import sqlite3
import json
from contextlib import closing
from functools import lru_cache

from app import action_storage as actions
from app.action_email_storage import (
    _external_dependencies, _sequence, _set_sequence, _temp_create_sql,
)
from app.action_task_delivery_storage import _hash, _quote, _source_declaration
from app.business_schema_release import _snapshot, _restore_rows
from app.email_canonical_definitions import (
    CLASS_FIELDS, MESSAGE_FIELDS, SHARED_FIELDS, EMAILS_TABLE_SQL,
    EMAILS_INDEX_SQL, EMAILS_TRIGGER_SQL, canonical_column, email_projection,
)


SOURCE_HASHES = {
    "actual": {
        "email_classifications": "e351befd931221744005b76b46878e0a763e09834571353947e62e282c29835b",
        "email_messages": "58c36f5a63108f3d77c7097dd9ec353b899a561ac35cff2e1ccc371e989f0a03",
    },
    "fresh": {
        "email_classifications": "0159086d74319a7773bb71a7c8cac614ce2b83e7d54c9c7e08618a8cd5b25a33",
        "email_messages": "fabd6faabc74e471e88523b42262229d99f3acd624d22cd23d4ea128375114b1",
    },
}
ACTION_HASHES = {
    "actual_no_memory_fk": "9b02487d85dab8e2bfef6be1fe61cd1833d7ed73df1e00202fc83b4d09566658",
    "fresh_memory_fk": "b4ff6ce904b6c1d3fb4c73774477c9595cfeb4dd7401e25b758610f74aebaa16",
}
ACTION_OPTIONS = dict(include_receipts=True, include_reply=True, include_email=True,
                      include_followup_sync=True, include_skill_config_receipts=True,
                      include_business_sync=True, include_task_delivery=True,
                      include_errors=True, include_feedback=True,
                      include_business_actions=True, include_email_history=True)
KINDS = ("email_classifications", "email_messages")
INT64_MAX = 2**63 - 1
_UNSET = object()
CLASS_DEFAULTS = {
    "sender": "''", "subject": "''", "preview": "''", "model_text": "''",
    "received_at": "''", "agent_result_json": "'null'",
    "legacy_processed_without_plan": "0", "confirmed_at": "''",
    "created_at": "current_timestamp", "updated_at": "current_timestamp",
}


def email_classification_projection() -> str:
    return email_projection(KINDS[0])


def email_message_projection() -> str:
    return email_projection(KINDS[1])


def _action_objects(variant: str, *, target: bool, inline_legacy_owner: bool = False,
                    physical_provenance: bool = False, compact_payload: bool = False):
    objects = actions._current_objects(
        reply_variant=variant, inline_legacy_owner=inline_legacy_owner,
        physical_provenance=physical_provenance, compact_payload=compact_payload, **ACTION_OPTIONS
    )
    if not target:
        return objects
    return [({**obj, "sql": obj["sql"].replace(
        "references email_classifications(id)", "references emails(classification_id)"
    )} if obj["type"] == "table" else obj) for obj in objects]


def _variant(db, *, target, inline_legacy_owner=False):
    observed = actions._objects(db, "actions")
    return next((variant for variant in ACTION_HASHES
                 if observed == _action_objects(
                     variant, target=target, inline_legacy_owner=inline_legacy_owner
                 )), None)


def _require_fk_off(db):
    if db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("Email conversion requires caller-owned foreign_keys=off")


def _source_declarations(db):
    return {kind: _source_declaration(db, kind) for kind in KINDS}


def _source_profile(declarations):
    hashes = {name: _hash(declarations[name]) for name in KINDS}
    return next((profile for profile, expected in SOURCE_HASHES.items()
                 if hashes == expected), None)


@lru_cache(maxsize=1)
def _target_hash():
    with closing(sqlite3.connect(":memory:")) as scratch:
        _create_emails(scratch)
        return _hash(_source_declaration(scratch, "emails"))


def _external_email_dependencies(db, *, target):
    """Probe unknown MAIN/TEMP references with the exact known Actions FK masked.

    Only the disposable SQLite parser receives the mask. The live Actions FK
    remains active against the source or canonical classification alias.
    """
    owned = {row[0] for row in db.execute(
        "select name from main.sqlite_schema where type='trigger' and tbl_name in ("
        + ("'emails'" if target else "'email_classifications','email_messages'") + ")")}
    objects = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from main.sqlite_schema "
        "where type in ('table','view','trigger') and sql is not null "
        "and name not glob 'sqlite_*' "
        "order by case type when 'table' then 0 when 'view' then 1 else 2 end,name")]
    temp = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from temp.sqlite_schema "
        "where type in ('table','view','index','trigger') and sql is not null "
        "and name not glob 'sqlite_*' order by rowid")]
    virtual = {(schema, actions.sqlite_schema_name(row[1]))
               for schema in ("main", "temp")
               for row in db.execute(f"pragma {schema}.table_list") if row[2] == "virtual"}
    known_fk = ("references emails(classification_id)" if target
                else "references email_classifications(id)")
    with closing(sqlite3.connect(":memory:")) as parser:
        for kind, name, owner, sql in objects:
            if kind == "trigger" and name in owned:
                continue
            if kind == "table" and name == "actions":
                if sql.count(known_fk) != 1:
                    raise ValueError("unsupported canonical Actions FK dependency")
                sql = sql.replace(known_fk, "references _known_email_parent(id)")
            if kind == "table" and ("main", actions.sqlite_schema_name(name)) in virtual:
                columns = [row[1] for row in db.execute(f"pragma main.table_xinfo({_quote(name)})")]
                sql = "create table " + _quote(name) + " (" + ",".join(map(_quote, columns)) + ")"
            parser.execute(sql)
        for kind, name, _owner, sql in temp:
            if kind == "table" and ("temp", actions.sqlite_schema_name(name)) in virtual:
                columns = [row[1] for row in db.execute(f"pragma temp.table_xinfo({_quote(name)})")]
                sql = "create table " + _quote(name) + " (" + ",".join(map(_quote, columns)) + ")"
            parser.execute(_temp_create_sql(kind, sql))
        _external_dependencies(parser, targets=(("emails",) if target else KINDS),
                               allowed_trigger_names=())


def capture_email_canonical_source(
    db: sqlite3.Connection, *, inline_legacy_owner: bool = False
) -> dict:
    """Capture exact declarations and allocator water; never capture bodies."""
    _require_fk_off(db)
    declarations = _source_declarations(db)
    profile = _source_profile(declarations)
    variant = _variant(db, target=False, inline_legacy_owner=inline_legacy_owner)
    if profile not in SOURCE_HASHES or variant not in ACTION_HASHES:
        raise ValueError("unsupported current13 Email/Actions source declaration")
    _external_email_dependencies(db, target=False)
    water = _sequence(db, "email_messages")
    if _sequence(db, "email_classifications") is not None:
        raise ValueError("unexpected classification allocator")
    return {"profile": profile, "variant": variant, "source_tables": declarations,
            "source_actions": _action_objects(
                variant, target=False, inline_legacy_owner=inline_legacy_owner
            ),
            "message_water": water,
            "source_counts": {name: db.execute("select count(*) from main." + _quote(name)).fetchone()[0]
                              for name in (*KINDS, "actions")},
            "source_foreign_key_findings": [list(row) for row in db.execute("pragma foreign_key_check")]}


def _validate_metadata(metadata, *, inline_legacy_owner=False):
    if type(metadata) is not dict or set(metadata) != {
        "profile", "variant", "source_tables", "source_actions",
        "message_water", "source_counts", "source_foreign_key_findings",
    }:
        raise ValueError("unsupported Email inverse metadata")
    profile, variant = metadata["profile"], metadata["variant"]
    if profile not in SOURCE_HASHES or variant not in ACTION_HASHES:
        raise ValueError("unsupported Email inverse metadata")
    declarations = metadata["source_tables"]
    water = metadata["message_water"]
    if (type(declarations) is not dict or set(declarations) != set(KINDS)
            or any(_hash(declarations[kind]) != SOURCE_HASHES[profile][kind] for kind in KINDS)
            or metadata["source_actions"] != _action_objects(
                variant, target=False, inline_legacy_owner=inline_legacy_owner
            )
            or (water is not None and (type(water) is not int or not 0 <= water <= INT64_MAX))
            or type(metadata["source_counts"]) is not dict
            or set(metadata["source_counts"]) != {*KINDS, "actions"}
            or any(type(count) is not int or count < 0 for count in metadata["source_counts"].values())
            or type(metadata["source_foreign_key_findings"]) is not list):
        raise ValueError("unsupported Email inverse metadata")


def _create_source_table(db, declaration):
    for obj in declaration["objects"]:
        if obj[0] == "table":
            db.execute(obj[3])
    by_name = {obj[1]: obj[3] for obj in declaration["objects"]}
    for index in reversed(declaration["indexes"]):
        sql = by_name[index["list"][1]]
        if sql is not None:
            db.execute(sql)
    for obj in declaration["objects"]:
        if obj[0] == "trigger":
            db.execute(obj[3])


def _create_actions(db, objects):
    for kind in ("table", "index", "trigger"):
        for obj in objects:
            if obj["type"] == kind:
                db.execute(obj["sql"])


def _create_emails(db):
    db.execute(EMAILS_TABLE_SQL)
    for sql in EMAILS_INDEX_SQL:
        db.execute(sql)
    for sql in EMAILS_TRIGGER_SQL:
        db.execute(sql)


def _columns(kind):
    return CLASS_FIELDS if kind == KINDS[0] else MESSAGE_FIELDS


def _projection_snapshot(db, kind, *, columns=None):
    fields = columns or _columns(kind)
    query = email_projection(kind)
    return {
        "columns": fields, "hidden_rowid": False,
        "rows": tuple(tuple(row) for row in db.execute(
            "select " + ",".join(map(_quote, fields)) + " from (" + query + ") order by id")),
        "types": tuple(tuple(row) for row in db.execute(
            "select " + ",".join("typeof(" + _quote(f) + ")" for f in fields)
            + " from (" + query + ") order by id")),
    }


def _physical_id(db, preferred):
    if db.execute("select 1 from emails where id=?", (preferred,)).fetchone() is None:
        return preferred
    maximum = db.execute("select max(id) from emails").fetchone()[0]
    if maximum == INT64_MAX:
        # Structural row IDs are private. Native random-at-MAX allocation is
        # kept for the public classification alias, but migration needs a
        # repeatable physical mapping from identical source rows.
        candidate = 1
        for (occupied,) in db.execute("select id from emails where id>=1 order by id"):
            if occupied > candidate:
                break
            candidate += 1
        return candidate
    return (maximum or 0) + 1


def _insert_raw(db, values):
    columns = tuple(values)
    db.execute("insert into emails(" + ",".join(map(_quote, columns)) + ") values("
               + ",".join("?" for _ in columns) + ")", tuple(values.values()))


def _native_class_next(db):
    maximum = db.execute("select max(classification_id) from emails").fetchone()[0]
    if maximum == INT64_MAX:
        with sqlite3.connect(":memory:") as allocator:
            allocator.execute("create table ids(id integer primary key)")
            allocator.executemany("insert into ids values(?)", db.execute(
                "select classification_id from emails where classification_id is not null"))
            return allocator.execute("insert into ids default values").lastrowid
    return (maximum or 0) + 1


def _native_message_next(db):
    water = _sequence(db, "email_messages") or 0
    maximum = db.execute("select max(message_id) from emails").fetchone()[0] or 0
    high = max(water, maximum)
    if high == INT64_MAX:
        if db.in_transaction:
            db.rollback()
        actions._raise_autoincrement_full()
    return high + 1


def _shared_with_native_affinity(values):
    """Bind shared source values through SQLite's TEXT/INTEGER affinities."""
    if not values:
        return {}
    columns = tuple(field for field in ("account_id", "stable_message_identity", "uidvalidity", "uid")
                    if field in values)
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.execute("create table locator(account_id text,stable_message_identity text,"
                        "uidvalidity integer,uid integer)")
        scratch.execute("insert into locator(" + ",".join(columns) + ") values("
                        + ",".join("?" for _ in columns) + ")",
                        tuple(values[field] for field in columns))
        row = scratch.execute("select " + ",".join(
            part for field in columns for part in (field, "typeof(" + field + ")"))
            + " from locator").fetchone()
    return {field: (row[index * 2], row[index * 2 + 1]) for index, field in enumerate(columns)}


def _write_email_message(db, values, *, mode):
    unknown = set(values) - set(MESSAGE_FIELDS)
    if unknown:
        raise ValueError("unsupported Email message fields")
    actions._email_lock(db)
    message_id = values.get("id")
    if message_id is None:
        message_id = _native_message_next(db)
    else:
        message_id = actions._native_integer_primary_key(message_id)
    mapped = {canonical_column(KINDS[1], field): value
              for field, value in values.items() if field != "id"}
    mapped = {"id": _physical_id(db, message_id), "message_id": message_id, **mapped}
    columns = tuple(mapped)
    sql = ("insert " + ("or ignore " if mode == "ignore" else "") + "into emails("
           + ",".join(map(_quote, columns)) + ") values("
           + ",".join("?" for _ in columns) + ")")
    if mode == "upsert":
        updates = ("folder", "uidvalidity", "uid", "in_reply_to", "references_json",
                   "thread_identity", "updated_at")
        sql += " on conflict(stable_message_identity) do update set " + ",".join(
            _quote(canonical_column(KINDS[1], field)) + "=excluded."
            + _quote(canonical_column(KINDS[1], field)) for field in updates)
    db.execute(sql, tuple(mapped.values()))
    _set_sequence(db, "email_messages", max(_sequence(db, "email_messages") or 0, message_id))
    row = db.execute("select message_id from emails where stable_message_identity=?",
                     (values["stable_message_identity"],)).fetchone()
    return row[0] if row is not None else None


def insert_email_message(db: sqlite3.Connection, values: dict) -> int:
    """Insert a message-only row using the independent native AUTO stream."""
    return _write_email_message(db, values, mode="insert")


def upsert_email_message(db: sqlite3.Connection, values: dict) -> int:
    """Match the source's stable-identity conflict update and AUTO burn."""
    return _write_email_message(db, values, mode="upsert")


def ignore_email_message(db: sqlite3.Connection, values: dict) -> int | None:
    """Match native INSERT OR IGNORE, including consumed AUTO identities."""
    return _write_email_message(db, values, mode="ignore")


def insert_email_classification(db: sqlite3.Connection, values: dict) -> int:
    """Fill the optional classification projection on an existing message."""
    unknown = set(values) - set(CLASS_FIELDS)
    if unknown:
        raise ValueError("unsupported Email classification fields")
    actions._email_lock(db)
    classification_id = values.get("id")
    if classification_id is None:
        classification_id = _native_class_next(db)
    else:
        classification_id = actions._native_integer_primary_key(classification_id)
    stable = values["stable_message_identity"]
    target = db.execute("select account_id,uidvalidity,uid,classification_id from emails "
                        "where stable_message_identity=?", (stable,)).fetchone()
    if target is None:
        raise ValueError("classification has no canonical message")
    if target[3] is not None:
        raise sqlite3.IntegrityError("UNIQUE constraint failed: emails.stable_message_identity")
    shared = _shared_with_native_affinity(values)
    target_types = db.execute("select typeof(account_id),typeof(uidvalidity),typeof(uid) "
                              "from emails where stable_message_identity=?", (stable,)).fetchone()
    if any(shared[field] != (target[index], target_types[index])
           for index, field in enumerate(("account_id", "uidvalidity", "uid"))):
        raise ValueError("classification locator differs from message")
    mapped = {canonical_column(KINDS[0], field): value for field, value in values.items()
              if field != "id" and field not in SHARED_FIELDS}
    supplied = ",".join(_quote(col) + "=?" for col in ("classification_id", *mapped))
    defaults = ",".join(_quote(canonical_column(KINDS[0], field)) + "=" + expression
                        for field, expression in CLASS_DEFAULTS.items() if field not in values)
    assignments = ",".join(part for part in (supplied, defaults) if part)
    db.execute("update emails set " + assignments + " where stable_message_identity=?",
               (classification_id, *mapped.values(), stable))
    return classification_id


def update_email_message(db: sqlite3.Connection, message_id: int, values: dict) -> int:
    """Apply source Message UPDATE columns to its exact public alias."""
    if not values:
        return 0
    if set(values) - set(MESSAGE_FIELDS):
        raise ValueError("unsupported Email message fields")
    mapped = {canonical_column(KINDS[1], field):
              actions._native_integer_primary_key(value, update=True) if field == "id" else value
              for field, value in values.items()}
    assignments = [_quote(column) + "=?" for column in mapped]
    reference_parameters = ()
    if 'normalized_text' in values:
        assignments.append("message_source_reference_json=case when message_normalized_text is ? "
            "then message_source_reference_json "
            "when json_type(message_source_reference_json,'$.model_input')='object' "
            "then json_object('model_input',json_extract(message_source_reference_json,'$.model_input')) "
            "else null end")
        reference_parameters = (values['normalized_text'],)
    cursor = db.execute("update emails set " + ",".join(assignments)
                        + " where message_id=?", (*mapped.values(), *reference_parameters, message_id))
    return cursor.rowcount


def update_email_classification(db: sqlite3.Connection, classification_id: int,
                                values: dict) -> int:
    """Apply source Classification UPDATE without changing message-only facts."""
    if not values:
        return 0
    if set(values) - set(CLASS_FIELDS):
        raise ValueError("unsupported Email classification fields")
    if SHARED_FIELDS & set(values):
        actions._email_lock(db)
    mapped = {canonical_column(KINDS[0], field):
              actions._native_integer_primary_key(value, update=True) if field == "id" else value
              for field, value in values.items()}
    shared = _shared_with_native_affinity({field: values[field] for field in SHARED_FIELDS & set(values)})
    for field, converted in shared.items():
        current = db.execute("select " + _quote(field) + ",typeof(" + _quote(field)
                             + ") from emails where classification_id=?", (classification_id,)).fetchone()
        if current is not None and tuple(current) != converted:
            raise ValueError("classification shared fact differs from canonical message")
    assignments = [_quote(column) + "=?" for column in mapped]
    reference_parameters = ()
    if 'model_text' in values:
        assignments.append("message_source_reference_json=case when class_model_text is ? "
                           "then message_source_reference_json else "
                           "json_remove(message_source_reference_json,'$.model_input') end")
        reference_parameters = (values['model_text'],)
    cursor = db.execute("update emails set "
                        + ",".join(assignments)
                        + " where classification_id=?", (*mapped.values(), *reference_parameters, classification_id))
    return cursor.rowcount


def update_email_paired_locator(
    db: sqlite3.Connection, *, classification_id: int, account_id: str,
    stable_message_identity: str, class_folder: str, message_folder: str,
    uidvalidity: int, uid: int, class_thread_id=_UNSET,
    class_updated_at=_UNSET, message_updated_at=_UNSET,
    expected_class_folder=_UNSET, expected_message_folder=_UNSET,
    expected_uidvalidity=_UNSET, expected_uid=_UNSET,
) -> int:
    """Apply the existing class-then-message locator move in one canonical UPDATE.

    Optional old locator values are the caller's existing conditional WHERE
    values. A failed match returns zero for the caller's current conflict path.
    """
    assignments = ["class_folder=?", "message_folder=?", "uidvalidity=?", "uid=?"]
    bindings = [class_folder, message_folder, uidvalidity, uid]
    for column, value in (("class_thread_id", class_thread_id),
                          ("class_updated_at", class_updated_at),
                          ("message_updated_at", message_updated_at)):
        if value is not _UNSET:
            assignments.append(_quote(column) + "=?")
            bindings.append(value)
    where = ["classification_id=?", "account_id=?", "stable_message_identity=?"]
    bindings.extend((classification_id, account_id, stable_message_identity))
    for column, value in (("class_folder", expected_class_folder),
                          ("message_folder", expected_message_folder),
                          ("uidvalidity", expected_uidvalidity), ("uid", expected_uid)):
        if value is not _UNSET:
            where.append(_quote(column) + "=?")
            bindings.append(value)
    cursor = db.execute("update emails set " + ",".join(assignments)
                        + " where " + " and ".join(where), bindings)
    return cursor.rowcount


def delete_email_classification(db: sqlite3.Connection, classification_id: int) -> int:
    """Remove only the optional classification projection."""
    columns = tuple(canonical_column(KINDS[0], field) for field in CLASS_FIELDS
                    if field not in SHARED_FIELDS)
    cursor = db.execute("update emails set "
                        + ",".join(_quote(column) + "=null" for column in columns)
                        + " where classification_id=?", (classification_id,))
    return cursor.rowcount


def delete_email_message(db: sqlite3.Connection, message_id: int) -> int:
    """Delete a message-only row; a paired row has no representable class-only state."""
    actions._email_lock(db)
    row = db.execute("select classification_id from emails where message_id=?", (message_id,)).fetchone()
    if row is not None and row[0] is not None:
        raise ValueError("paired Email must remove classification before message")
    cursor = db.execute("delete from emails where message_id=?", (message_id,))
    return cursor.rowcount


def migrate_email_canonical_schema(
    db: sqlite3.Connection, metadata: dict, *, inline_legacy_owner: bool = False
) -> dict:
    """Convert a source13 DB in its caller's transaction, without commit."""
    _validate_metadata(metadata, inline_legacy_owner=inline_legacy_owner)
    if capture_email_canonical_source(
        db, inline_legacy_owner=inline_legacy_owner
    ) != metadata:
        raise ValueError("Email source changed after capture")
    messages = _snapshot(db, KINDS[1])
    classes = _snapshot(db, KINDS[0])
    retained = _snapshot(db, "actions")
    msg_by_stable = {row[messages["columns"].index("stable_message_identity")]: (row, types)
                     for row, types in zip(messages["rows"], messages["types"], strict=True)}
    if any(row[classes["columns"].index("stable_message_identity")] not in msg_by_stable
           for row in classes["rows"]):
        raise ValueError("classification has no canonical message")
    for row, types in zip(classes["rows"], classes["types"], strict=True):
        message, message_types = msg_by_stable[row[classes["columns"].index("stable_message_identity")]]
        if any((row[classes["columns"].index(field)], types[classes["columns"].index(field)])
               != (message[messages["columns"].index(field)],
                   message_types[messages["columns"].index(field)])
               for field in SHARED_FIELDS):
            raise ValueError("unrepresentable shared Email facts")
    class_by_stable = {row[classes["columns"].index("stable_message_identity")]: row
                       for row in classes["rows"]}
    db.execute("drop table actions")
    for kind in KINDS:
        db.execute("drop table " + kind)
    _create_emails(db)
    for row in classes["rows"]:
        data = dict(zip(classes["columns"], row, strict=True))
        message = dict(zip(messages["columns"], msg_by_stable[data["stable_message_identity"]][0], strict=True))
        mapped = {canonical_column(KINDS[1], field): value for field, value in message.items()}
        mapped.update({canonical_column(KINDS[0], field): value for field, value in data.items()
                       if field not in SHARED_FIELDS})
        mapped["id"] = _physical_id(db, data["id"])
        _insert_raw(db, mapped)
    for row in messages["rows"]:
        message = dict(zip(messages["columns"], row, strict=True))
        if message["stable_message_identity"] in class_by_stable:
            continue
        mapped = {canonical_column(KINDS[1], field): value for field, value in message.items()}
        mapped["id"] = _physical_id(db, message["id"])
        _insert_raw(db, mapped)
    _set_sequence(db, "email_messages", metadata["message_water"])
    _create_actions(db, _action_objects(
        metadata["variant"], target=True, inline_legacy_owner=inline_legacy_owner
    ))
    _restore_rows(db, "actions", retained)
    if (_projection_snapshot(db, KINDS[1], columns=messages["columns"]) != messages
            or _projection_snapshot(db, KINDS[0], columns=classes["columns"]) != classes):
        raise ValueError("Email source facts changed during conversion")
    expected_fk = sorted([row[0], row[1], "emails" if row[2] == KINDS[0] else row[2], row[3]]
                         for row in metadata["source_foreign_key_findings"])
    if sorted(map(list, db.execute("pragma foreign_key_check"))) != expected_fk:
        raise ValueError("Email foreign-key findings changed during conversion")
    return metadata


def restore_email_canonical_schema(db: sqlite3.Connection, metadata: dict, *, read_body=None,
                                   read_model_input=None) -> dict:
    """Restore the exact source profile using current canonical facts."""
    _require_fk_off(db)
    _validate_metadata(metadata)
    if _variant(db, target=True) != metadata["variant"]:
        raise ValueError("unsupported canonical Actions declaration")
    if _hash(_source_declaration(db, "emails")) != _target_hash():
        raise ValueError("unsupported canonical Email declaration")
    _external_email_dependencies(db, target=True)
    from app.email_body_source import verify_original

    cursor = db.execute("select * from emails where message_source_reference_json is not null")
    names = tuple(item[0] for item in cursor.description)
    restored_bodies = []
    restored_models = []
    for values in cursor.fetchall():
        row = dict(zip(names, values, strict=True))
        reference = json.loads(row['message_source_reference_json'])
        if reference.get('kind') is not None:
            if read_body is None:
                raise ValueError("Email original reader required for schema inverse")
            body = read_body(row, reference)
            verify_original(body, reference)
            restored_bodies.append((body, row["id"]))
        model_reference = reference.get('model_input')
        if model_reference is not None and row['class_model_text'] == '':
            if read_model_input is None:
                raise ValueError("Email model input reader required for schema inverse")
            model_text = read_model_input(row, model_reference)
            verify_original(model_text, model_reference)
            restored_models.append((model_text, row['id'], row['class_included_in_model_id']))
    db.executemany("update emails set message_normalized_text=? where id=?", restored_bodies)
    db.executemany("update emails set class_model_text=? where id=?",
                   [(text, identity) for text, identity, _ in restored_models])
    # Restoring identical input bytes must retain the current inclusion fact,
    # despite the old raw-field invalidation trigger firing on representation.
    db.executemany("update emails set class_included_in_model_id=? where id=?",
                   [(included, identity) for _, identity, included in restored_models])
    columns = {kind: tuple(row[1] for row in metadata["source_tables"][kind]["columns"]
                           if row[6] == 0) for kind in KINDS}
    messages = _projection_snapshot(db, KINDS[1], columns=columns[KINDS[1]])
    classes = _projection_snapshot(db, KINDS[0], columns=columns[KINDS[0]])
    retained = _snapshot(db, "actions")
    water = _sequence(db, "email_messages")
    db.execute("drop table actions")
    db.execute("drop table emails")
    for kind in KINDS:
        _create_source_table(db, metadata["source_tables"][kind])
    _restore_rows(db, KINDS[1], messages)
    _restore_rows(db, KINDS[0], classes)
    _set_sequence(db, "email_messages", water)
    _create_actions(db, metadata["source_actions"])
    _restore_rows(db, "actions", retained)
    if _source_profile(_source_declarations(db)) != metadata["profile"]:
        raise ValueError("Email inverse declaration differs")
    if sorted(map(list, db.execute("pragma foreign_key_check"))) != sorted(
            metadata["source_foreign_key_findings"]):
        raise ValueError("Email inverse foreign-key findings changed")
    return metadata
