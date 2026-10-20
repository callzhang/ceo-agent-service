"""Exact publication marker selection across native source and current State."""
import sqlite3

from app.state_definitions import state_projection
from app.state_schema_release import state_schema_is_current


def publication_store_version(db: sqlite3.Connection, *, current_version: str,
                              previous_versions: tuple[str, ...] = ()) -> str | None:
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
        if version not in (current_version, *previous_versions):
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
    if version in (current_version, *previous_versions):
        raise ValueError("current publication version requires State schema")
    return version


def publication_dispatcher_source(db: sqlite3.Connection, *, current_version: str,
                                  previous_versions: tuple[str, ...] = ()) -> str:
    """Select the existing dispatcher facts for the same publication role."""
    version = publication_store_version(db, current_version=current_version, previous_versions=previous_versions)
    if version == "2026-10-19.8":
        from app.task_lifecycle_current_schema import task20_schema_is_current
        from app.task_lifecycle_storage import claim_projection
        from app.task_lifecycle_definitions import DISPATCH_KINDS
        if not task20_schema_is_current(db):
            raise ValueError("unknown Task lifecycle publication declaration")
        fields = ("adapter_name", "source_id", "owner", "owner_pid", "generation",
                  "lease_expires_at", "terminal_at", "last_error", "created_at", "updated_at")
        columns = ",".join(fields)
        return ("select " + columns + ",1 as claim_active from (" + claim_projection() + ") "
                "union all select " + columns + ",coalesce(terminal_at,'')='' as claim_active from ("
                + state_projection("dispatcher_claim_leases") + ") where adapter_name not in ("
                + ",".join(map(repr, DISPATCH_KINDS)) + ")")
    if version in (current_version, *previous_versions):
        return ("select *,coalesce(terminal_at,'')='' as claim_active from ("
                + state_projection("dispatcher_claim_leases") + ")")
    return "select *,coalesce(terminal_at,'')='' as claim_active from main.dispatcher_claim_leases"
