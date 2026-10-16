"""Unselected Workbench runs FK translation for the config/schedule release.

The caller owns an active FK-off transaction and all surrounding schema work.
This module changes only two FK targets and never persists a row backup.
"""
import sqlite3

from app.action_email_storage import _sequence, _set_sequence
from app.business_schema_release import _restore_rows, _snapshot
from app.run_storage import _declared_run_objects

_SOURCE_EDGES = (
    "foreign key(scheduled_task_id) references scheduled_tasks(id)",
    "foreign key(snapshot_id) references scheduled_task_config_versions(id)",
)
_TARGET_EDGES = (
    "foreign key(scheduled_task_id) references schedules(id)",
    "foreign key(snapshot_id) references configs(schedule_snapshot_public_id)",
)


def _declarations(*, mapped: bool) -> dict[str, str]:
    expected = _declared_run_objects(True, True).copy()
    table = expected["runs"]
    for source, target in zip(_SOURCE_EDGES, _TARGET_EDGES, strict=True):
        if table.count(source) != 1:
            raise ValueError("unsupported canonical runs declaration")
        if mapped:
            table = table.replace(source, target, 1)
    expected["runs"] = table
    return expected


def _objects(db: sqlite3.Connection) -> dict[str, str]:
    return dict(db.execute(
        "select name,sql from main.sqlite_schema "
        "where tbl_name='runs' and sql is not null"
    ))


def _require_state(db: sqlite3.Connection, *, mapped: bool) -> dict[str, str]:
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0]:
        raise sqlite3.ProgrammingError("runs translation requires caller FK-off transaction")
    if db.execute("select 1 from temp.sqlite_schema where name='runs' collate nocase").fetchone():
        raise ValueError("runs TEMP name collision")
    expected = _declarations(mapped=mapped)
    if _objects(db) != expected:
        raise ValueError("unsupported runs declaration")
    parents = ("schedules", "configs") if not mapped else (
        "scheduled_tasks", "scheduled_task_config_versions")
    if any(not db.execute(
        "select 1 from main.sqlite_schema where type='table' and name=?", (parent,)
    ).fetchone() for parent in parents):
        raise ValueError("required runs FK parent is missing")
    return expected


def _waters(db: sqlite3.Connection) -> dict[str, int | None]:
    names = tuple(row[0] for row in db.execute(
        "select name from main.sqlite_sequence "
        "where name='runs' or name like 'runs:%' order by name"
    ))
    return {name: _sequence(db, name) for name in names}


def _check_edges(db: sqlite3.Connection, *, mapped: bool) -> None:
    targets = _TARGET_EDGES if mapped else _SOURCE_EDGES
    columns = ("scheduled_task_id", "snapshot_id")
    parents = ("schedules", "configs") if mapped else (
        "scheduled_tasks", "scheduled_task_config_versions")
    edge_ids = {
        row[0] for row in db.execute("pragma main.foreign_key_list(runs)")
        if (row[2], row[3], row[4]) in {
            (parents[0], columns[0], "id"),
            (parents[1], columns[1], "schedule_snapshot_public_id" if mapped else "id"),
        }
    }
    if len(edge_ids) != len(targets):
        raise ValueError("unsupported runs FK edges")
    if any(row[3] in edge_ids for row in db.execute("pragma main.foreign_key_check(runs)")):
        raise ValueError("broken translated runs FK")


def _rebuild(db: sqlite3.Connection, *, from_mapped: bool) -> None:
    _require_state(db, mapped=from_mapped)
    rows = _snapshot(db, "runs")
    waters = _waters(db)
    target = _declarations(mapped=not from_mapped)
    db.execute("drop table main.runs")
    db.execute(target["runs"])
    _restore_rows(db, "runs", rows)
    for name, sql in target.items():
        if name != "runs":
            db.execute(sql)
    for name in set(_waters(db)) | set(waters):
        _set_sequence(db, name, waters.get(name))
    if _snapshot(db, "runs") != rows or _waters(db) != waters or _objects(db) != target:
        raise ValueError("runs translation changed facts or declarations")
    _check_edges(db, mapped=not from_mapped)


def map_runs_to_configs(db: sqlite3.Connection) -> None:
    """Rebuild current native Workbench runs with two mapped FK targets."""
    _rebuild(db, from_mapped=False)


def restore_native_run_fks(db: sqlite3.Connection) -> None:
    """Restore native FKs from the current runs facts, including newer writes."""
    _rebuild(db, from_mapped=True)
