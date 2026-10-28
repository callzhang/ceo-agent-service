"""Native Run and RuntimeAttempt storage over the assembled Task/Run schema.

Callers supply and own the SQLite connection and transaction. This module stores
only native owner columns and the explicitly typed Run child tables.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from app.run_entity_definitions import RUN_FIELDS


_RUN_FIELDS = frozenset(name for name, _type, _required, _default in RUN_FIELDS) - {"id"}
_ATTEMPT_FIELDS = frozenset({
    "parent_id", "workload_kind", "workload_key", "attempt_number", "route_name",
    "runtime_kind", "credential_mode", "model", "session_mode", "source_session_id",
    "attempt_purpose", "validation_retry_policy_id", "validation_result_schema_id",
    "session_id", "transcript_reference", "transcript_start", "transcript_end", "status",
    "failure_class", "failure_code", "failover_permitted", "lease_owner", "lease_expires_at",
    "first_effect_started_at", "result_schema_id", "started_at", "finished_at", "created_at",
    "updated_at",
})


def _values(values: Mapping[str, object], allowed: frozenset[str], owner: str) -> dict[str, object]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{owner} values must be a mapping")
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"unknown {owner} fields: {', '.join(sorted(unknown))}")
    return dict(values)


def _row(row: sqlite3.Row | tuple | None) -> dict[str, object] | None:
    return None if row is None else dict(row)


def _create(db: sqlite3.Connection, table: str, values: dict[str, object]) -> dict[str, object]:
    if not values:
        raise ValueError(f"{table} requires values")
    columns = list(values)
    row = db.execute(
        f"insert into {table} ({','.join(columns)}) values ({','.join('?' for _ in columns)}) returning *",
        tuple(values.values()),
    ).fetchone()
    return _row(row)  # type: ignore[return-value]


def _get(db: sqlite3.Connection, table: str, identity: str, value: int) -> dict[str, object] | None:
    return _row(db.execute(f"select * from {table} where {identity}=?", (value,)).fetchone())


def _cas(db: sqlite3.Connection, table: str, identity: str, value: int,
         expected: Mapping[str, object], changes: Mapping[str, object],
         allowed: frozenset[str], owner: str) -> dict[str, object] | None:
    predicates = _values(expected, allowed, f"{owner} expectation")
    updates = _values(changes, allowed, f"{owner} update")
    if not predicates or not updates:
        raise ValueError(f"{owner} CAS requires expected values and changes")
    setters = [f'"{field}"=?' for field in updates]
    where = [f'"{identity}"=?', *(f'"{field}" IS ?' for field in predicates)]
    args = [*updates.values(), value, *predicates.values()]
    return _row(db.execute(
        f"update {table} set {','.join(setters)} where {' and '.join(where)} returning *", args
    ).fetchone())


def create_run(db: sqlite3.Connection, *, values: Mapping[str, object]) -> dict[str, object]:
    """Insert a Run; SQLite assigns its ordinary native identity and defaults."""
    return _create(db, "runs", _values(values, _RUN_FIELDS, "Run"))


def get_run(db: sqlite3.Connection, run_id: int) -> dict[str, object] | None:
    return _get(db, "runs", "id", run_id)


def update_run_cas(db: sqlite3.Connection, *, run_id: int, expected: Mapping[str, object],
                   changes: Mapping[str, object]) -> dict[str, object] | None:
    """Compare caller-selected Run facts and update exactly supplied fields."""
    return _cas(db, "runs", "id", run_id, expected, changes, _RUN_FIELDS, "Run")


def create_runtime_attempt(db: sqlite3.Connection, *, values: Mapping[str, object]) -> dict[str, object]:
    """Insert one explicitly observed RuntimeAttempt without synthesizing facts."""
    return _create(db, "runtime_attempts", _values(values, _ATTEMPT_FIELDS, "RuntimeAttempt"))


def get_runtime_attempt(db: sqlite3.Connection, attempt_id: int) -> dict[str, object] | None:
    return _get(db, "runtime_attempts", "id", attempt_id)


def update_runtime_attempt_cas(
    db: sqlite3.Connection, *, attempt_id: int, expected: Mapping[str, object],
    changes: Mapping[str, object],
) -> dict[str, object] | None:
    """CAS observed attempt facts; DDL owns correction-lineage immutability."""
    return _cas(db, "runtime_attempts", "id", attempt_id, expected, changes,
                _ATTEMPT_FIELDS, "RuntimeAttempt")


def set_run_provenance(db: sqlite3.Connection, *, run_id: int, source_type: str,
                       job_id: int | None = None) -> dict[str, object]:
    """Store explicit source provenance in its typed child row."""
    row = db.execute(
        "insert into run_provenance(run_id,source_type,job_id) values(?,?,?) "
        "on conflict(run_id) do update set source_type=excluded.source_type,job_id=excluded.job_id "
        "returning *", (run_id, source_type, job_id),
    ).fetchone()
    return _row(row)  # type: ignore[return-value]


def get_run_provenance(db: sqlite3.Connection, run_id: int) -> dict[str, object] | None:
    return _row(db.execute("select * from run_provenance where run_id=?", (run_id,)).fetchone())


def set_run_projection(db: sqlite3.Connection, *, run_id: int,
                       projection_json: object) -> dict[str, object]:
    """Store the Task Agent projection, separately from adopted candidate data."""
    row = db.execute(
        "insert into run_projections(run_id,projection_json) values(?,?) "
        "on conflict(run_id) do update set projection_json=excluded.projection_json returning *",
        (run_id, projection_json),
    ).fetchone()
    return _row(row)  # type: ignore[return-value]




def append_run_failure(db: sqlite3.Connection, *, run_id: int, phase: str,
                       structured_error_json: str, created_at: str) -> dict[str, object]:
    """Append one server failure fact; it does not represent CLI output."""
    return _create(db, "run_failures", {
        "run_id": run_id, "phase": phase, "structured_error_json": structured_error_json,
        "created_at": created_at,
    })
