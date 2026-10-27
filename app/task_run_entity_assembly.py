"""Assemble the current Task, Run, Action and retained-owner declarations."""
from __future__ import annotations

from app.run_adoption_definitions import (
    INDEXES as ADOPTION_INDEXES,
    TABLES as ADOPTION_TABLES,
    TRIGGERS as ADOPTION_TRIGGERS,
    receipt_schema_objects,
)
from app.run_entity_definitions import run_entity_objects
from app.task_run_entity_definitions import task_run_entity_objects



def _object_name(sql: str) -> str:
    """Read a CREATE object's declared name and validate it through SQLite."""
    tokens = sql.strip().split()
    upper = [token.upper() for token in tokens]
    if upper[:2] == ["CREATE", "TABLE"] or upper[:2] == ["CREATE", "TRIGGER"]:
        return tokens[2]
    if upper[:3] == ["CREATE", "UNIQUE", "INDEX"] or upper[:2] == ["CREATE", "INDEX"]:
        return tokens[3] if upper[:2] == ["CREATE", "UNIQUE"] else tokens[2]
    raise ValueError(f"unsupported schema declaration: {tokens[:3]}")


def _unique_declared_objects(families):
    """Build target declarations without hiding duplicate actual SQLite names."""
    declarations = {}
    for family in families:
        entries = family.items() if hasattr(family, "items") else family
        for declared_name, sql in entries:
            actual_name = _object_name(sql)
            if declared_name != actual_name:
                raise ValueError(f"declared object name mismatch: {declared_name} != {actual_name}")
            if actual_name in declarations:
                raise ValueError(f"duplicate target schema object: {actual_name}")
            declarations[actual_name] = sql
    return declarations


def _action_objects():
    from app.current_schema_declarations import ACTION_OBJECTS
    return dict(ACTION_OBJECTS)


def task_run_entity_assembly_objects(*, email_ledger=False):
    """Return target DDL plus actual retained-owner declarations.

    The scratch schema uses only target Task/Run declarations and the native
    individual creators for unchanged owners. It deliberately does not call
    the current whole-database factory or source manifest, both of which
    instantiate the old Task/Run factories.
    """
    import sqlite3
    from app.action_entity_definitions import ACTION_IDENTITY_MAP_DDL
    from app.reply_receipt_reference import DDL as REPLY_RECEIPT_REFERENCE_DDL
    from app.business_legacy_fields import current_business_declaration
    from app.business_schema_helpers import _create_auxiliary
    from app.configs_storage import create_configs_schema
    from app.email_canonical_storage import _create_emails
    from app.current_schema_manifest import EMAIL_LEDGER_SQL
    from app.provenance_storage import create_provenance_schema
    from app.provider_state_storage import create_provider_state_schema
    from app.schedules_storage import create_schedules_schema
    from app.state_storage import create_state_schema
    from app.task8_storage import register_task8_functions

    declarations = _unique_declared_objects((
        task_run_entity_objects(), run_entity_objects(), ADOPTION_TABLES,
        ADOPTION_INDEXES, ADOPTION_TRIGGERS, receipt_schema_objects(), _action_objects(),
        ((_object_name(sql), sql) for sql in ACTION_IDENTITY_MAP_DDL),
        ((_object_name(sql), sql) for sql in REPLY_RECEIPT_REFERENCE_DDL),
    ))
    db = sqlite3.connect(":memory:")
    register_task8_functions(db)
    # Install target-owned tables first so retained native creators can keep
    # their real FK declarations without substituting fake owner tables.
    for name, sql in declarations.items():
        if sql.lstrip().lower().startswith("create table "):
            db.execute(sql)
    # Retained native owner factories from current_database_schema.py, with
    # only its Task/Run factories and their child guards intentionally omitted.
    create_schedules_schema(db)
    create_configs_schema(db)
    create_state_schema(db)
    create_provider_state_schema(db)
    create_provenance_schema(db)
    _create_emails(db, metadata_owner=True, action_owner=True)
    business = current_business_declaration()
    db.execute(business["sql"])
    _create_auxiliary(db, business)
    if email_ledger:
        db.execute(EMAIL_LEDGER_SQL)
    # Install the target-owned indexes and triggers after every owner exists.
    for name, sql in declarations.items():
        if not sql.lstrip().lower().startswith("create table "):
            db.execute(sql)
    rows = db.execute(
        "SELECT name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name"
    ).fetchall()
    result = {name: sql for name, sql in rows}
    db.close()
    return result


def validate_assembled_object_names(db):
    actual = {name for (name,) in db.execute(
        "SELECT name FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'")}
    expected = set(task_run_entity_assembly_objects())
    if actual != expected:
        raise ValueError("assembled DDL object names differ from executed SQLite schema")
