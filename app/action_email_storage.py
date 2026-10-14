"""Read-only source binding for the unselected Email action migration candidate.

The fingerprints are from the frozen thirteen-table actual/fresh source pair
and the reviewed sixteen-type E action declarations. They are candidate
contracts; publication must bind its actual immediate predecessor separately.
"""

import hashlib
import json
import sqlite3
from contextlib import closing

from app import action_storage as storage
from app.action_reply_storage import _source_declaration
from app.action_email_definitions import EMAIL_AUTOINCREMENT_TYPES


SOURCE_TABLES = (
    "email_action_plans", "email_actions", "email_action_attempts",
    "email_unsubscribe_cancellations", "email_unsubscribe_claims",
    "email_unsubscribe_continuations", "email_unsubscribe_effects",
    "email_unsubscribe_receipts", "email_unsubscribe_steps",
    "email_direct_retry_requests", "email_feedback_requests",
    "email_reply_dispatch_claims", "email_reply_receipts",
)
EXTERNAL_TRIGGER_NAMES = (
    "trg_email_direct_action_blocks_account_delete",
    "trg_email_direct_action_blocks_account_update",
    "trg_email_direct_action_blocks_plan_switch",
    "trg_email_unsubscribe_blocks_account_delete",
    "trg_email_unsubscribe_blocks_account_update",
    "trg_email_unsubscribe_blocks_message_delete",
    "trg_email_unsubscribe_blocks_message_update",
    "trg_email_unsubscribe_blocks_plan_switch",
    "trg_email_reply_dispatch_blocks_account_delete",
    "trg_email_reply_dispatch_blocks_account_update",
    "trg_email_reply_dispatch_blocks_message_delete",
    "trg_email_reply_dispatch_blocks_plan_switch",
    "trg_email_reply_dispatch_blocks_thread_update",
)
SOURCE_PAIRS = {
    ("actual_no_memory_fk", "actual"): (
        "69691ed77f99cf5209e9e78086595cc20a25bd01aee6770a6c35091a7887ef0d",
        "fc8e0f26e3d063444193752ce6933f0febda68a60b11ed572829a74a8fe88f61",
    ),
    ("fresh_memory_fk", "fresh_candidate"): (
        "d741764c7ac77bce2c774e8518788044cea3b3a56aac3d09096a7cc07f4e7663",
        "4d05ce37c15dc8ceeea07980e1b58437c2a2ac2b3eae07014797b16dee794e78",
    ),
}
EXTERNAL_TRIGGERS_FINGERPRINT = "f16284f5ddc64ffdd9966bd1083479dc6770ef1247ab8e5318425f27cb2bdaf4"


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _sequence(db, name):
    rows = db.execute("select seq from sqlite_sequence where name=?", (name,)).fetchall()
    if len(rows) > 1 or rows and (type(rows[0][0]) is not int or not 0 <= rows[0][0] < 2**63):
        raise ValueError("unsupported Email sequence metadata")
    return rows[0][0] if rows else None


def _external_dependencies(db):
    """Apply SQLite's own rename parser after omitting the thirteen known blockers."""
    objects = [tuple(row) for row in db.execute(
        "select type,name,tbl_name,sql from sqlite_schema "
        "where type in ('table','view','trigger') and sql is not null "
        "and name not glob 'sqlite_*' "
        "order by case type when 'table' then 0 when 'view' then 1 else 2 end,name"
    )]
    virtual = {storage.sqlite_schema_name(row[1]) for row in db.execute("pragma table_list")
               if row[0] == "main" and row[2] == "virtual"}
    allowed = {storage.sqlite_schema_name(name) for name in EXTERNAL_TRIGGER_NAMES}
    with closing(sqlite3.connect(":memory:")) as parser:
        for kind, name, owner, sql in objects:
            if kind == "trigger" and (storage.sqlite_schema_name(name) in allowed or
                                      storage.sqlite_schema_name(owner) in
                                      {"actions", *(storage.sqlite_schema_name(t) for t in SOURCE_TABLES)}):
                continue
            if kind == "table" and storage.sqlite_schema_name(name) in virtual:
                columns = [row[1] for row in db.execute(f"pragma table_xinfo({_quote(name)})")]
                sql = f"create table {_quote(name)} (" + ",".join(map(_quote, columns)) + ")"
            parser.execute(sql)
        storage._no_sql_dependencies(parser, set(SOURCE_TABLES))


def capture_email_action_source(db: sqlite3.Connection) -> dict:
    """Capture only supported candidate source metadata in the caller's read transaction."""
    actions = storage._objects(db, "actions")
    action_fingerprint = _fingerprint(actions)
    declarations = {table: _source_declaration(db, table) for table in SOURCE_TABLES}
    email_fingerprint = _fingerprint(declarations)
    pair = next((pair for pair, hashes in SOURCE_PAIRS.items()
                 if hashes == (action_fingerprint, email_fingerprint)), None)
    if pair is None:
        raise ValueError("unsupported sixteen-type actions or Email source declaration pair")
    if db.execute("select 1 from actions where record_type not in (" +
                  ",".join("?" for _ in storage.ALL_ACTION_TYPES_WITH_REPLY) +
                  ") limit 1", storage.ALL_ACTION_TYPES_WITH_REPLY).fetchone():
        raise ValueError("unsupported mixed Email or unknown action record type")
    allowed_names = {storage.sqlite_schema_name(item["name"]) for item in actions}
    allowed_names.update(storage.sqlite_schema_name(name) for name in SOURCE_TABLES)
    allowed_names.update(storage.sqlite_schema_name(name) for name in EXTERNAL_TRIGGER_NAMES)
    for obj in storage._current_objects(include_reply=True, reply_variant=pair[0], include_email=True):
        name = storage.sqlite_schema_name(obj["name"])
        if name not in allowed_names and db.execute(
            "select 1 from sqlite_schema where name=? collate nocase", (obj["name"],)
        ).fetchone():
            raise ValueError("unsupported Email replacement name collision")
    external = [dict(zip(("type", "name", "tbl_name", "sql"), row, strict=True)) for row in db.execute(
        "select type,name,tbl_name,sql from sqlite_schema where type='trigger' and name in (" +
        ",".join("?" for _ in EXTERNAL_TRIGGER_NAMES) + ") order by name",
        EXTERNAL_TRIGGER_NAMES,
    )]
    if _fingerprint(external) != EXTERNAL_TRIGGERS_FINGERPRINT:
        raise ValueError("unsupported external Email trigger declaration")
    storage._no_inbound(db, set(SOURCE_TABLES))
    _external_dependencies(db)
    allowed_sequences = set(storage.ACTION_SEQUENCE_NAMES.values()) | {
        storage.DELIVERY_SEQUENCE_NAME, *storage.REPLY_SEQUENCE_NAMES.values(),
    }
    if db.execute("select 1 from sqlite_sequence where name like 'actions:%' and name not in (" +
                  ",".join("?" for _ in allowed_sequences) + ") limit 1",
                  tuple(sorted(allowed_sequences))).fetchone():
        raise ValueError("unsupported action allocator alias")
    sequences = {name: _sequence(db, name) for name in (
        *SOURCE_TABLES, *sorted(allowed_sequences))}
    if any(sequences[name] is not None for name in SOURCE_TABLES
           if name not in EMAIL_AUTOINCREMENT_TYPES):
        raise ValueError("unsupported Email non-allocator sequence metadata")
    counts = {name: db.execute(f"select count(*) from {_quote(name)}").fetchone()[0]
              for name in SOURCE_TABLES}
    return {
        "action_variant": pair[0], "email_variant": pair[1],
        "source_actions": actions, "source_tables": declarations,
        "external_triggers": external, "sequences": sequences, "counts": counts,
        "locator_modes": {name: ("integer_id" if name in EMAIL_AUTOINCREMENT_TYPES else
                                  "composite" if name in storage.EMAIL_COMPOSITE_KEYS else "rowid")
                          for name in SOURCE_TABLES},
    }
