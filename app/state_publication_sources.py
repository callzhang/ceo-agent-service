"""Exact publication marker selection across native source and current State."""
import sqlite3

from app.state_definitions import state_projection
from app.state_schema_release import state_schema_is_current


def publication_store_version(db: sqlite3.Connection, *, current_version: str) -> str | None:
    """Read the selected marker without repairing or falling back across schemas."""
    current = db.execute(
        "select 1 from main.sqlite_schema where name='state' collate nocase"
    ).fetchone() is not None
    if current:
        if not state_schema_is_current(db):
            raise ValueError("unknown or mixed State publication source schema")
        row = db.execute(
            f"select value from ({state_projection('service_state')}) "
            "where key='store_schema_version'"
        ).fetchone()
        version = row[0] if row else None
        if version != current_version:
            raise ValueError("unknown or mixed State publication version")
        return version
    native = db.execute(
        "select 1 from main.sqlite_schema where type='table' "
        "and name='service_state' collate nocase"
    ).fetchone() is not None
    if not native:
        return None
    row = db.execute(
        "select value from main.service_state where key='store_schema_version'"
    ).fetchone()
    version = row[0] if row else None
    if version == current_version:
        raise ValueError("current publication version requires State schema")
    return version


def publication_dispatcher_source(db: sqlite3.Connection, *, current_version: str) -> str:
    """Select the existing dispatcher facts for the same publication role."""
    version = publication_store_version(db, current_version=current_version)
    if version == current_version:
        return state_projection("dispatcher_claim_leases")
    return "select * from main.dispatcher_claim_leases"
