"""Unselected caller-transactional schedules storage."""
import sqlite3
from app.schedules_definitions import SCHEDULE_DDL, SCHEDULE_FIELDS, SCHEDULE_INDEXES

def create_schedules_schema(db: sqlite3.Connection) -> None:
    db.execute(SCHEDULE_DDL)
    for sql in SCHEDULE_INDEXES:
        db.execute(sql)

def _write_ready(db: sqlite3.Connection, values: dict) -> None:
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("schedule writes require caller transaction")
    if set(values) - set(SCHEDULE_FIELDS):
        raise ValueError("unknown schedule field")

def insert_schedule(db: sqlite3.Connection, values: dict) -> int:
    _write_ready(db, values)
    keys = tuple(values)
    sql = ("insert into schedules(" + ",".join(keys) + ") values(" +
           ",".join("?" for _ in keys) + ")")
    return db.execute(sql, tuple(values.values())).lastrowid

def update_schedule(db: sqlite3.Connection, identity: int, expected_version: int,
                    values: dict) -> int:
    _write_ready(db, values)
    if not values or "id" in values or "version" in values:
        raise ValueError("CAS update needs mutable fields only")
    keys = tuple(values)
    sql = ("update schedules set " + ",".join(k + "=?" for k in keys) +
           ",version=version+1 where id=? and version=? and deleted_at is null")
    return db.execute(sql, (*values.values(), identity, expected_version)).rowcount

def delete_schedule(db: sqlite3.Connection, identity: int, expected_version: int,
                    deleted_at: str) -> int:
    _write_ready(db, {"deleted_at": deleted_at})
    return db.execute("update schedules set updated_at=?,deleted_at=?,enabled=0,version=version+1 "
                      "where id=? and version=? and deleted_at is null",
                      (deleted_at, deleted_at, identity, expected_version)).rowcount
