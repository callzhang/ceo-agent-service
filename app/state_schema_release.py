"""Source-bound State migration and current-fact inverse.

The caller owns one FK-off transaction and its rollback. Metadata contains only
original declarations; no source row image, schema marker or runtime selection.
"""
import sqlite3

from app.action_email_storage import _external_dependencies
from app.action_reply_storage import _fingerprint, _source_declaration
from app.action_storage import _no_inbound
from app.state_definitions import STATE_FIELDS, STATE_INDEX_DDL, _quote
from app.state_storage import create_state_schema, insert_state

SOURCE_TABLES = (*STATE_FIELDS, "corpus_sources")
SOURCE_HASHES = {
    "actual": "b7e149e432369018ba105f9e1ac12b052f38bfe245ee7e6d7b7b7b8b3ab9caa6",
    "fresh": "48e1608e66d2a35fdb467708e24fd6bef1f2f4ecd58e8a6e45c4af9fb6449a70",
}
TARGET_HASH = "f867c00d08bbf1cc66d479b25e471644944329485065e21cd30c18f571772c88"
TARGET_INDEXES = tuple(sql.split(" on state(", 1)[0].split()[-1].strip('"')
                       for sql in STATE_INDEX_DDL)


def _transaction(db):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise sqlite3.ProgrammingError("State release requires caller FK-off transaction")


def _present(db, schema, name):
    return db.execute("select 1 from " + schema + ".sqlite_schema "
                      "where name=? collate nocase", (name,)).fetchone() is not None


def _no_temp(db, names):
    if any(_present(db, "temp", name) for name in names):
        raise ValueError("unsupported State TEMP name collision")


def _metadata(metadata):
    if (not isinstance(metadata, dict) or set(metadata) != {"variant", "tables"}
            or metadata["variant"] not in SOURCE_HASHES
            or not isinstance(metadata["tables"], dict)
            or set(metadata["tables"]) != set(SOURCE_TABLES)
            or _fingerprint(metadata["tables"]) != SOURCE_HASHES[metadata["variant"]]):
        raise ValueError("unsupported State source declaration metadata")


def _eligible(db, variant):
    if db.execute("select 1 from main.corpus_sources limit 1").fetchone():
        raise ValueError("nonempty corpus_sources cannot be retired")
    if variant == "actual" and db.execute(
        "select 1 from main.conversations where typeof(in_handoff)<>'integer' "
        "or in_handoff is not 0 or handoff_message_id is not null "
        "or handoff_message_create_time is not null "
        "or manual_clear_message_id is not null limit 1"
    ).fetchone():
        raise ValueError("nondefault unused conversation fields cannot be retired")


def capture_state_source(db):
    """Capture exact native declarations after empty/default eligibility checks."""
    _transaction(db)
    _no_temp(db, (*SOURCE_TABLES, "state", *TARGET_INDEXES))
    if any(_present(db, "main", name) for name in ("state", *TARGET_INDEXES)):
        raise ValueError("State target name already exists")
    tables = {name: _source_declaration(db, name) for name in SOURCE_TABLES}
    fingerprint = _fingerprint(tables)
    variant = next((name for name, expected in SOURCE_HASHES.items()
                    if fingerprint == expected), None)
    if variant is None:
        raise ValueError("unsupported State source declarations")
    _eligible(db, variant)
    _no_inbound(db, SOURCE_TABLES)
    _external_dependencies(db, targets=SOURCE_TABLES, allowed_trigger_names=())
    return {"variant": variant, "tables": tables}


def _target_shape(db):
    _no_temp(db, (*SOURCE_TABLES, "state", *TARGET_INDEXES))
    if any(_present(db, "main", name) for name in SOURCE_TABLES):
        raise ValueError("native State source name remains")
    declaration = _source_declaration(db, "state")
    # PRAGMA index_list sequence/order reflects creation history, not index shape.
    # Preserve each ordered index column, SQL, uniqueness, origin and predicate.
    declaration["indexes"] = sorted(
        ({key: value for key, value in index.items() if key != "seq"}
         for index in declaration["indexes"]),
        key=lambda index: index["name"],
    )
    if _fingerprint(declaration) != TARGET_HASH:
        raise ValueError("unsupported State target declaration")


def state_schema_is_current(db):
    """Recognize only the final selected declaration without changing data."""
    try:
        _target_shape(db)
    except (ValueError, sqlite3.Error):
        return False
    return True


def _same(db, kind):
    fields = STATE_FIELDS[kind]
    source_fields = ("rowid", *fields)
    native = "select " + ",".join(map(_quote, source_fields))
    native += "," + ",".join("typeof(" + _quote(f) + ")" for f in source_fields)
    native += " from main." + _quote(kind)
    target = "select cast(legacy_key as integer)," + ",".join(map(_quote, fields))
    target += ",typeof(cast(legacy_key as integer))," + ",".join(
        "typeof(" + _quote(f) + ")" for f in fields)
    target += " from main.state where record_type=" + repr(kind)
    for left, right in ((native, target), (target, native)):
        if db.execute("select 1 from (" + left + " except " + right + ") limit 1").fetchone():
            raise ValueError("State source identity, values or storage classes differ: " + kind)


def migrate_state_schema(db, metadata):
    """Verify native facts before removing sixteen original physical tables."""
    _transaction(db)
    _metadata(metadata)
    if capture_state_source(db) != metadata:
        raise ValueError("State source declaration changed")
    create_state_schema(db)
    counts = {}
    for kind, fields in STATE_FIELDS.items():
        selected = ",".join(map(_quote, ("rowid", *fields)))
        counts[kind] = 0
        for row in db.execute("select " + selected + " from main." + _quote(kind) + " order by rowid"):
            insert_state(db, kind, dict(zip(fields, tuple(row)[1:], strict=True)),
                         source_rowid=row[0])
            counts[kind] += 1
        _same(db, kind)
    counts["corpus_sources"] = 0
    for kind in SOURCE_TABLES:
        db.execute("drop table main." + _quote(kind))
    _target_shape(db)
    return counts


def restore_state_schema(db, metadata):
    """Rebuild exact old declarations using current target facts, never a snapshot."""
    _transaction(db)
    _metadata(metadata)
    _target_shape(db)
    _no_inbound(db, ("state",))
    _external_dependencies(db, targets=("state",), allowed_trigger_names=())
    index_names = tuple(index["name"] for spec in metadata["tables"].values()
                        for index in spec["indexes"] if index["sql"] is not None)
    _no_temp(db, index_names)
    if any(_present(db, "main", name) for name in index_names):
        raise ValueError("native State index name already exists")
    for kind, definition in metadata["tables"].items():
        db.execute(definition["sql"])
        for index in reversed(definition["indexes"]):
            if index["sql"] is not None:
                db.execute(index["sql"])
    counts = {}
    for kind, fields in STATE_FIELDS.items():
        columns = ",".join(map(_quote, ("rowid", *fields)))
        selected = "cast(legacy_key as integer)," + ",".join(map(_quote, fields))
        cursor = db.execute("insert into main." + _quote(kind) + "(" + columns + ") "
                            "select " + selected + " from main.state where record_type=?", (kind,))
        counts[kind] = cursor.rowcount
        _same(db, kind)
    counts["corpus_sources"] = 0
    _eligible(db, metadata["variant"])
    if {kind: _source_declaration(db, kind) for kind in SOURCE_TABLES} != metadata["tables"]:
        raise ValueError("restored State source declarations differ")
    db.execute("drop table main.state")
    return counts
