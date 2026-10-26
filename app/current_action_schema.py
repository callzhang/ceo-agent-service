"""The one current Actions declaration and its source-shaped projections.

The current Attempt keeps the four actual-source facts. Memory uses the
fresh-source physical parent reference. Historical source factories remain
unchanged for explicit release and inverse operations.
"""

from contextlib import closing
from functools import lru_cache
import sqlite3

from app import action_storage as actions
from app.business_legacy_fields import CURRENT_SYNC_FIELDS, retained_action_payloads
from app.action_entity_definitions import RECEIPT_SCHEMA_DDL, ACTION_IDENTITY_MAP_DDL

RETIRED_ACTION_TYPES = frozenset({
    "candidate_action_attempts", "external_action_results", "outbound_postfixes",
    "outbound_postfix_receipts", "native_reply_dispatches", "sent_replies",
    "sent_reply_observers", "email_actions", "email_action_attempts",
})


@lru_cache(maxsize=1)
def current_action_payloads():
    return retained_action_payloads(actions._build_action_payloads(
        actions._REPLY_FIELDS["reply_attempts"],
        tuple(field for field in actions._REPLY_FIELDS["memory_write_events"]
              if field != "attempt_id"),
        physical_provenance=True, selection_references=True,
        execution_owner=True, shared_lifecycle=True,
        specialized_claims=True, postfix_owner=True, action_entity=True,
        signal_source_references=True,
    ))


@lru_cache(maxsize=1)
def _signal_source_action_payloads():
    return retained_action_payloads(actions._build_action_payloads(
        actions._REPLY_FIELDS["reply_attempts"],
        tuple(field for field in actions._REPLY_FIELDS["memory_write_events"]
              if field != "attempt_id"),
        physical_provenance=True, selection_references=True,
        execution_owner=True, shared_lifecycle=True,
        specialized_claims=True, postfix_owner=True, action_entity=True,
    ))


@lru_cache(maxsize=1)
def current_action_objects():
    from app.current_database_schema import FINAL_ACTION_OPTIONS

    options = {key: value for key, value in FINAL_ACTION_OPTIONS.items()
               if key != "canonical_email"}
    options.pop("sent_result_references", None)
    return _build_action_objects(
        options, payloads=current_action_payloads(),
        retired_types=RETIRED_ACTION_TYPES, signal_source_references=True,
    )


@lru_cache(maxsize=1)
def signal_source_action_objects():
    """Frozen Store 2026-10-25.2 current Actions declaration."""
    from app.current_database_schema import FINAL_ACTION_OPTIONS

    options = {key: value for key, value in FINAL_ACTION_OPTIONS.items()
               if key != "canonical_email"}
    options.pop("sent_result_references", None)
    return _build_action_objects(
        options, payloads=_signal_source_action_payloads(),
        retired_types=RETIRED_ACTION_TYPES,
    )


@lru_cache(maxsize=1)
def source_current_action_objects():
    """Explicit pre-entity Actions declaration for migration and inverse tooling."""
    from app.current_database_schema import FINAL_ACTION_OPTIONS

    options = {key: value for key, value in FINAL_ACTION_OPTIONS.items()
               if key not in {"canonical_email", "action_entity"}}
    return _build_action_objects(options, payloads=_signal_source_action_payloads())


def _build_action_objects(options, *, payloads, retired_types=(),
                          signal_source_references=False):
    table, indexes, triggers = actions._action_definitions(
        reply_variant="fresh_memory_fk", **options,
        payloads=payloads, sync_fields=CURRENT_SYNC_FIELDS,
        canonical_attempt_fields=True, retired_types=retired_types,
        signal_source_references=signal_source_references,
    )
    table = table.replace("references email_classifications(id)",
                          "references emails(classification_id)")
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("create table _allocator(id integer primary key autoincrement)")
        db.execute("drop table _allocator")
        for sql in (table, *indexes, *triggers):
            db.execute(sql)
        return actions._objects(db, "actions")


def create_current_action_schema(db: sqlite3.Connection):
    """Install on blank storage or require the exact existing declaration."""
    _create_action_schema(db, current_action_objects())


def create_source_current_action_schema(db: sqlite3.Connection):
    """Install the frozen Store 2026-10-25.2 Actions declaration."""
    _create_action_schema(db, signal_source_action_objects())


def _create_action_schema(db: sqlite3.Connection, expected):
    expected_children = _action_entity_objects()
    actual = actions._objects(db, "actions")
    if actual:
        if actual != expected:
            raise ValueError("unsupported current Actions declaration")
        actual_children = (actions._objects(db, "action_receipts")
                          + actions._objects(db, "action_observers")
                          + actions._objects(db, "action_entity_identity_map"))
        if actual_children != expected_children:
            raise ValueError("unsupported current Action child declaration")
        return
    actions._require_available_names(db, (*expected, *expected_children))
    for obj in sorted(expected, key=lambda item: item["type"] != "table"):
        db.execute(obj["sql"])
    for obj in sorted(expected_children, key=lambda item: item["type"] != "table"):
        db.execute(obj["sql"])


def _action_entity_objects():
    with sqlite3.connect(":memory:") as db:
        db.execute("create table actions(id integer primary key,_kind integer,record_type text)")
        for sql in RECEIPT_SCHEMA_DDL:
            db.execute(sql)
        for sql in ACTION_IDENTITY_MAP_DDL:
            db.execute(sql)
        return (actions._objects(db, "action_receipts") + actions._objects(db, "action_observers")
                + actions._objects(db, "action_entity_identity_map"))


def current_reply_action_projection(kind: str):
    """Project all current public Attempt fields and Memory's parent ID."""
    return actions.reply_action_projection(
        kind, "fresh_memory_fk", canonical_attempt_fields=True,
    )
