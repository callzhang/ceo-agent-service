"""Retire the empty canceled Workbench feature in the caller's transaction.

Inverse metadata contains its original declarations only. No business rows,
receipts, task bodies or historical counter values are copied.
"""
import sqlite3

from app.action_reply_storage import _fingerprint
from app.run_storage import WORKBENCH_EVENT_SQL
from app.task_storage import WORKBENCH_TURN_SQL
from app.task20_definitions import task20_projection
from app.task20_schema_release import _declaration

TABLE = "workbench_confirmations"
SOURCE_HASH = "e215afb029d2993691a8585b07a614667aca8cddedd8dd21b9b0a3f3335d982d"


def _transaction(db):
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise sqlite3.ProgrammingError("confirmation retirement requires caller FK-off transaction")
    for name in (TABLE, "tasks", "runs"):
        if db.execute("select 1 from temp.sqlite_schema where name=? collate nocase", (name,)).fetchone():
            raise ValueError("confirmation retirement TEMP relation collision")


def _metadata(metadata):
    if not isinstance(metadata, dict) or set(metadata) != {"declaration"} or \
            _fingerprint(metadata["declaration"]) != SOURCE_HASH:
        raise ValueError("unsupported confirmation source metadata")


def _empty_closure(db, *, current):
    turns = task20_projection("workbench_turns") if current else WORKBENCH_TURN_SQL
    if db.execute("select 1 from main.workbench_confirmations limit 1").fetchone() or \
            db.execute("select 1 from (" + turns + ") where status='waiting_confirmation' limit 1").fetchone() or \
            db.execute("select 1 from (" + WORKBENCH_EVENT_SQL + ") where event_type='confirmation_required' limit 1").fetchone():
        raise ValueError("canceled confirmation source is nonempty; explicit disposition required")


def capture_confirmation_source(db):
    """Bind the genuine native declaration and empty feature before conversion."""
    _transaction(db)
    metadata = {"declaration": _declaration(db, TABLE)}
    _metadata(metadata)
    _empty_closure(db, current=False)
    return metadata


def retire_confirmation_schema(db, metadata):
    """Remove only the original empty table, including its own indexes/guards."""
    _transaction(db)
    _metadata(metadata)
    if _declaration(db, TABLE) != metadata["declaration"]:
        raise ValueError("confirmation source declaration changed")
    _empty_closure(db, current=True)
    db.execute("drop table main.workbench_confirmations")
    verify_confirmation_retired(db)


def verify_confirmation_retired(db):
    if db.execute("select 1 from main.sqlite_schema where name=? collate nocase", (TABLE,)).fetchone():
        raise ValueError("retired confirmation relation remains")


def restore_confirmation_schema(db, metadata):
    """Restore the empty native declarations for the genuine predecessor."""
    _transaction(db)
    _metadata(metadata)
    verify_confirmation_retired(db)
    definition = metadata["declaration"]
    db.execute(definition["sql"])
    for index in definition["indexes"]:
        if index["sql"] is not None:
            db.execute(index["sql"])
    for trigger in definition["triggers"]:
        db.execute(trigger["sql"])
    if _declaration(db, TABLE) != definition:
        raise ValueError("confirmation inverse declaration differs")
