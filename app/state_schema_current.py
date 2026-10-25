"""Current-declaration check for the State table."""

import sqlite3
from app.action_reply_storage import _fingerprint, _source_declaration
from app.state_definitions import STATE_FIELDS, STATE_INDEX_DDL


SOURCE_TABLES = (*STATE_FIELDS, "corpus_sources")


TARGET_HASH = "f867c00d08bbf1cc66d479b25e471644944329485065e21cd30c18f571772c88"


TARGET_INDEXES = tuple(sql.split(" on state(", 1)[0].split()[-1].strip('"')
                       for sql in STATE_INDEX_DDL)


def _present(db, schema, name):
    return db.execute("select 1 from " + schema + ".sqlite_schema "
                      "where name=? collate nocase", (name,)).fetchone() is not None


def _no_temp(db, names):
    if any(_present(db, "temp", name) for name in names):
        raise ValueError("unsupported State TEMP name collision")


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
