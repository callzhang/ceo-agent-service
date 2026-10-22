"""Email52 -> Email53 metadata ownership and inverse from current Email facts.

Both directions run inside the caller's FK-off transaction. TEMP staging exists
only during the rebuild; inverse metadata never archives business rows or bodies.
"""

from app import action_storage as actions
from app.action_task_delivery_storage import _hash, _quote, _source_declaration
from app.email_canonical_definitions import MESSAGE_METADATA_FIELDS, email_metadata_declaration
from app.model_result_schema import result_action_objects

_STAGING = "_email_metadata_rows"
_WATER = "_email_metadata_water"


def _require_source(db, *, metadata_owner):
    from app.business_candidate_storage import _collision
    from app.email_canonical_storage import _external_email_dependencies, _target_hash

    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise ValueError("Email metadata transition requires caller-owned FK-off transaction")
    _collision(db, ("emails", "actions", "sqlite_sequence", _STAGING, _WATER))
    declaration = _source_declaration(db, "emails")
    _collision(db, (obj[1] for obj in declaration["objects"]))
    if _hash(declaration) != _target_hash(metadata_owner=metadata_owner):
        raise ValueError("unsupported Email metadata source declaration")
    variant = actions._reply_variant_for(db)
    if actions._objects(db, "actions") != result_action_objects(variant):
        raise ValueError("unsupported Email metadata Actions declaration")
    _external_email_dependencies(db, target=True)
    for schema in ("main", "temp"):
        for (table,) in db.execute(f"select name from {schema}.sqlite_schema where type='table'").fetchall():
            for fk in db.execute(f"pragma {schema}.foreign_key_list({_quote(table)})"):
                if actions.sqlite_schema_name(fk[2]) == "emails" and (schema, table) != ("main", "actions"):
                    raise ValueError("unsupported inbound Email metadata dependency")
    return {"reply_variant": variant}


def _require_equal_metadata(db):
    pairs = [f"(class_{field} is not message_{field} or "
             f"typeof(class_{field})<>typeof(message_{field}))"
             for field in sorted(MESSAGE_METADATA_FIELDS - {"preview"})]
    if db.execute("select 1 from main.emails where classification_id is not null and ("
                  + " or ".join(pairs) + ") limit 1").fetchone():
        raise ValueError("Email metadata differs in SQLite value or storage type")
    if db.execute("""select 1 from main.emails where
        (classification_id is not null and
          (typeof(message_preview)<>'text' or message_preview<>'' or typeof(class_preview)<>'text'
           or length(cast(unhex(replace(hex(class_preview),'00','01')) as text))>1000))
        or (classification_id is null and
          (typeof(message_preview)<>'text' or length(cast(unhex(replace(hex(message_preview),'00','01')) as text))>1000))
        limit 1""").fetchone():
        raise ValueError("Email preview has no single bounded text owner")
    # The source CHECK permits stray classification slots on message-only rows.
    # Dropping them would lose facts; the inverse can reconstruct only NULL.
    if db.execute("select 1 from main.emails where classification_id is null and ("
                  + " or ".join("class_" + field + " is not null" for field in sorted(MESSAGE_METADATA_FIELDS))
                  + ") limit 1").fetchone():
        raise ValueError("Email metadata exists without a classification owner")


def capture_email_metadata_source(db):
    metadata = _require_source(db, metadata_owner=False)
    _require_equal_metadata(db)
    return metadata


def _rebuild(db, *, metadata_owner):
    from app.email_canonical_storage import _target_hash

    table, indexes, triggers = email_metadata_declaration(metadata_owner=metadata_owner)
    columns = [row[1] for row in db.execute("pragma main.table_xinfo(emails)") if row[6] == 0]
    if metadata_owner:
        columns = [name for name in columns if name not in {"class_" + field for field in MESSAGE_METADATA_FIELDS}]
        expressions = ["case when classification_id is not null then class_preview else message_preview end"
                       if name == "message_preview" else _quote(name) for name in columns]
    else:
        # Recreate the frozen column order from its declaration, independent of
        # which classified/message-only rows existed when publish ran.
        from contextlib import closing
        import sqlite3
        with closing(sqlite3.connect(":memory:")) as scratch:
            scratch.execute(table)
            columns = [row[1] for row in scratch.execute("pragma table_xinfo(emails)") if row[6] == 0]
        expressions = []
        for name in columns:
            if name in {"class_" + field for field in MESSAGE_METADATA_FIELDS}:
                expressions.append("case when classification_id is not null then message_" + name[6:] + " else null end")
            elif name == "message_preview":
                expressions.append("case when classification_id is not null then '' else message_preview end")
            else:
                expressions.append(_quote(name))
    names = ",".join(map(_quote, columns))
    foreign_keys = sorted(tuple(row) for row in db.execute("pragma foreign_key_check"))
    db.execute(f"create temp table {_STAGING} as select " + ",".join(
        expression + " as " + _quote(name) for name, expression in zip(columns, expressions, strict=True)) + " from main.emails")
    db.execute(f"create temp table {_WATER} as select rowid,name,seq from main.sqlite_sequence")
    db.execute("drop table main.emails")
    db.execute(table)
    db.execute(f"insert into main.emails({names}) select {names} from temp.{_STAGING} order by id")
    for sql in (*indexes, *triggers):
        db.execute(sql)
    db.execute("delete from main.sqlite_sequence")
    db.execute(f"insert into main.sqlite_sequence(rowid,name,seq) select rowid,name,seq from temp.{_WATER}")
    values = names + "," + ",".join("typeof(" + _quote(name) + ")" for name in columns)
    for left, right in (("main.emails", "temp." + _STAGING), ("temp." + _STAGING, "main.emails")):
        if db.execute(f"select {values} from {left} except select {values} from {right} limit 1").fetchone():
            raise ValueError("Email metadata transition changed SQLite values or storage types")
    if (_hash(_source_declaration(db, "emails")) != _target_hash(metadata_owner=metadata_owner)
            or sorted(tuple(row) for row in db.execute("pragma foreign_key_check")) != foreign_keys):
        raise ValueError("Email metadata transition changed declaration or foreign keys")
    db.execute(f"drop table temp.{_STAGING}")
    db.execute(f"drop table temp.{_WATER}")


def migrate_email_metadata(db):
    metadata = capture_email_metadata_source(db)
    _rebuild(db, metadata_owner=True)
    return metadata


def restore_email_metadata(db, metadata):
    if metadata != _require_source(db, metadata_owner=True):
        raise ValueError("unsupported Email metadata inverse metadata")
    _rebuild(db, metadata_owner=False)
