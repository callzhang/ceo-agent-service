"""Read-only preflight for the exact published Attempt and Memory sources.

The source fingerprints come from the frozen Store16 actual/fresh declaration
inventory. A later migration must call this before any schema mutation.
"""

import hashlib
import json
import sqlite3

from app import action_storage as storage


SOURCE_TABLES = ("reply_attempts", "memory_write_events")
PUBLISHED_STORE_VERSION = "2026-10-13.4"
PUBLISHED_EMAIL_VERSION = 51
PUBLISHED_ACTIONS_FINGERPRINT = "2b2e7e5c152906c77938192856e3958b4c86c82df32727d1c845ca09301d13e0"
PUBLISHED_ACTION_TYPES = (
    "review_candidates", "candidate_reviews", "candidate_selections",
    "candidate_supplements", "candidate_executions", "candidate_action_attempts",
    "external_action_results", "agent_effect_intents", "agent_execution_receipts",
    "outbound_postfixes", "outbound_postfix_receipts", "native_reply_dispatches",
    "sent_replies", "sent_reply_observers",
)
SOURCE_FINGERPRINTS = {
    "actual": {
        "reply_attempts": "5ec10b773b04a2a9c09d43fc8d9c01873c36b90c5c842b0ecb5e73857ea9f199",
        "memory_write_events": "4f236a93d6cba856697474a73f9541a8bcaf720e691139868d0e940f41490ec5",
    },
    "fresh_candidate17": {
        "reply_attempts": "3184d334f955b4e0b7be98f9cd22d149dd04b435976915c45461d4d5771c8fe1",
        "memory_write_events": "392cfe3a94e1a985f202a64a68bfdba92083e063ffb59929fb0f945cbf5220a2",
    },
}


def _source_declaration(db: sqlite3.Connection, table: str) -> dict:
    sql_row = db.execute("select sql from sqlite_schema where type='table' and name=? collate nocase",
                         (table,)).fetchone()
    quoted = '"' + table.replace('"', '""') + '"'
    columns = [dict(zip(("cid", "name", "type", "notnull", "dflt_value", "pk", "hidden"), row,
                        strict=True)) for row in db.execute(f"pragma table_xinfo({quoted})")]
    foreign_keys = [dict(zip(("id", "seq", "table", "from", "to", "on_update", "on_delete", "match"),
                             row, strict=True)) for row in db.execute(f"pragma foreign_key_list({quoted})")]
    indexes = []
    for seq, name, unique, origin, partial in db.execute(f"pragma index_list({quoted})"):
        index_quoted = '"' + name.replace('"', '""') + '"'
        index_sql = db.execute("select sql from sqlite_schema where type='index' and name=?", (name,)).fetchone()
        indexes.append({
            "seq": seq, "name": name, "unique": unique, "origin": origin, "partial": partial,
            "columns": [dict(zip(("seqno", "cid", "name", "desc", "coll", "key"), row,
                                 strict=True)) for row in db.execute(f"pragma index_xinfo({index_quoted})")],
            "sql": index_sql[0] if index_sql else None,
        })
    triggers = [dict(zip(("name", "sql"), row, strict=True)) for row in db.execute(
        "select name,sql from sqlite_schema where type='trigger' and tbl_name=? collate nocase "
        "order by name", (table,))]
    return {"sql": sql_row[0] if sql_row else None, "columns": columns,
            "foreign_keys": foreign_keys, "indexes": indexes, "triggers": triggers}


def _fingerprint(declaration: object) -> str:
    encoded = json.dumps(declaration, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def capture_reply_source(db: sqlite3.Connection) -> dict:
    """Validate and capture only source metadata inside the caller's read transaction."""
    marker = db.execute("select value from service_state where key='store_schema_version'").fetchone()
    if marker is None or marker[0] != PUBLISHED_STORE_VERSION:
        raise ValueError("unsupported published Store source marker")
    email = db.execute("select max(version) from email_schema_migrations").fetchone()[0]
    if email != PUBLISHED_EMAIL_VERSION:
        raise ValueError("unsupported published Email source marker")
    actions = storage._objects(db, "actions")
    legacy_names = db.execute(
        "select name from sqlite_schema where name collate nocase in ("
        + ",".join("?" for _ in PUBLISHED_ACTION_TYPES) + ") limit 1",
        PUBLISHED_ACTION_TYPES,
    ).fetchone()
    if _fingerprint(actions) != PUBLISHED_ACTIONS_FINGERPRINT or legacy_names:
        raise ValueError("unsupported fourteen-type actions source declaration")
    if db.execute("select 1 from actions where record_type in ('reply_attempts','memory_write_events') "
                  "limit 1").fetchone():
        raise ValueError("unsupported mixed Attempt/Memory actions source")
    if db.execute("select 1 from sqlite_sequence where name in "
                  "('actions:reply_attempts','actions:memory_write_events') limit 1").fetchone():
        raise ValueError("unsupported mixed Attempt/Memory allocator source")
    fingerprints = {table: _fingerprint(_source_declaration(db, table)) for table in SOURCE_TABLES}
    variant = next((name for name, expected in SOURCE_FINGERPRINTS.items()
                    if fingerprints == expected), None)
    if variant is None:
        raise ValueError("unsupported Attempt/Memory source declaration")
    source_tables = {"actions", *SOURCE_TABLES}
    storage._no_inbound(db, source_tables)
    storage._no_sql_dependencies(db, source_tables)
    sequences = {table: (row[0] if (row := db.execute(
        "select seq from sqlite_sequence where name=?", (table,)).fetchone()) else None)
                 for table in SOURCE_TABLES}
    counts = {table: db.execute(f"select count(*) from {table}").fetchone()[0]
              for table in SOURCE_TABLES}
    return {"variant": variant, "store_version": marker[0], "email_version": email,
            "fingerprints": fingerprints, "sequences": sequences, "counts": counts,
            "actions": actions}
