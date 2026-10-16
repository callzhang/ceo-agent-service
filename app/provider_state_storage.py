"""Caller-transaction-owned storage for the current provider-state table."""

from collections.abc import Mapping, Sequence
import json
import sqlite3

from app.action_storage import _encoded_native_sqlite_value
from app.provider_state_definitions import (
    GENERATION_FIELDS, GENERATION_PROJECTION, OBSERVATION_FIELDS,
    OBSERVATION_PROJECTION, PAYLOAD_FIELDS, PROVIDER_STATE_DDL,
    PROVIDER_STATE_INDEXES,
)


def create_provider_state_schema(db: sqlite3.Connection) -> None:
    db.execute(PROVIDER_STATE_DDL)
    for ddl in PROVIDER_STATE_INDEXES:
        db.execute(ddl)

def _write_transaction(db):
    if not db.in_transaction:
        raise sqlite3.ProgrammingError("provider-state write requires caller transaction")


def _fields(values, allowed):
    unknown = set(values) - set(allowed)
    if unknown:
        raise sqlite3.ProgrammingError(f"unknown provider-state fields: {sorted(unknown)}")


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _payload(values):
    return _json({field: _encoded_native_sqlite_value(values.get(field)) for field in PAYLOAD_FIELDS})


def _observation_bind(values, *, complete=False):
    _fields(values, OBSERVATION_FIELDS)
    if complete and set(values) != set(OBSERVATION_FIELDS):
        raise sqlite3.ProgrammingError("scan observation requires all source fields")
    return (
        values.get("account_id"), values.get("stable_message_identity"),
        values.get("state"), values.get("provider_folder_id"),
        values.get("observed_at"), _payload(values),
    )


def _generation_bind(values):
    _fields(values, GENERATION_FIELDS)
    return tuple(values.get(field) for field in GENERATION_FIELDS)


def _row_dict(cursor):
    row = cursor.fetchone()
    return dict(zip((col[0] for col in cursor.description), row)) if row is not None else None


def get_observation(db: sqlite3.Connection, account_id, stable_message_identity) -> dict | None:
    return _row_dict(db.execute(
        f"SELECT {OBSERVATION_PROJECTION} FROM provider_state "
        "WHERE record_type='observation' AND account_id=? AND stable_message_identity=?",
        (account_id, stable_message_identity),
    ))


def get_folder_generation(db: sqlite3.Connection, account_id, provider_folder_id) -> dict | None:
    return _row_dict(db.execute(
        f"SELECT {GENERATION_PROJECTION} FROM provider_state "
        "WHERE record_type='folder_generation' AND account_id=? AND provider_folder_id=?",
        (account_id, provider_folder_id),
    ))


def insert_observation(
    db: sqlite3.Connection, values: Mapping[str, object], *, ignore: bool = False,
) -> int:
    _write_transaction(db)
    action = "INSERT OR IGNORE" if ignore else "INSERT"
    return db.execute(
        f"{action} INTO provider_state "
        "(record_type,account_id,stable_message_identity,state,provider_folder_id,observed_at,payload_json) "
        "VALUES('observation',?,?,?,?,?,?)",
        _observation_bind(values),
    ).rowcount


def insert_folder_generation(
    db: sqlite3.Connection, values: Mapping[str, object], *, ignore: bool = False,
) -> int:
    _write_transaction(db)
    action = "INSERT OR IGNORE" if ignore else "INSERT"
    return db.execute(
        f"{action} INTO provider_state "
        "(record_type,account_id,provider_folder_id,observed_at,payload_json) "
        "VALUES('folder_generation',?,?,?,'{}')",
        _generation_bind(values),
    ).rowcount


def delete_observation(db: sqlite3.Connection, account_id, stable_message_identity) -> int:
    _write_transaction(db)
    return db.execute(
        "DELETE FROM provider_state WHERE record_type='observation' "
        "AND account_id=? AND stable_message_identity=?",
        (account_id, stable_message_identity),
    ).rowcount


def delete_folder_generation(db: sqlite3.Connection, account_id, provider_folder_id) -> int:
    _write_transaction(db)
    return db.execute(
        "DELETE FROM provider_state WHERE record_type='folder_generation' "
        "AND account_id=? AND provider_folder_id=?",
        (account_id, provider_folder_id),
    ).rowcount

_OBSERVATION_UPSERT = """
INSERT INTO provider_state
(record_type,account_id,stable_message_identity,state,provider_folder_id,observed_at,payload_json)
VALUES('observation',?,?,?,?,?,?)
ON CONFLICT(account_id,stable_message_identity) WHERE record_type='observation'
DO UPDATE SET state=excluded.state,provider_folder_id=excluded.provider_folder_id,
 observed_at=excluded.observed_at,payload_json=excluded.payload_json
WHERE provider_state.state IS NOT excluded.state
 OR provider_state.provider_folder_id IS NOT excluded.provider_folder_id
 OR provider_state.provider_folder_name IS NOT excluded.provider_folder_name
 OR provider_state.category_key IS NOT excluded.category_key
 OR provider_state.important IS NOT excluded.important
 OR provider_state.important_signals_json IS NOT excluded.important_signals_json
"""
_GENERATION_UPSERT = """
INSERT INTO provider_state
(record_type,account_id,provider_folder_id,observed_at,payload_json)
VALUES('folder_generation',?,?,?,'{}')
ON CONFLICT(account_id,provider_folder_id) WHERE record_type='folder_generation'
DO UPDATE SET observed_at=excluded.observed_at
WHERE provider_state.observed_at IS NOT excluded.observed_at
"""
_NULL_PAYLOAD = _payload({})
_CHANGED_TOMBSTONE = """(
 state IS NOT 'unavailable' OR provider_folder_id IS NOT NULL
 OR provider_folder_name IS NOT NULL OR category_key IS NOT NULL
 OR important IS NOT NULL OR important_signals_json IS NOT NULL
 OR observed_at IS NOT ?)"""


def upsert_observations(
    db: sqlite3.Connection, rows: Sequence[Mapping[str, object]],
) -> None:
    _write_transaction(db)
    db.executemany(_OBSERVATION_UPSERT, (_observation_bind(row, complete=True) for row in rows))


def upsert_folder_generations(
    db: sqlite3.Connection, rows: Sequence[Mapping[str, object]],
) -> None:
    _write_transaction(db)
    db.executemany(_GENERATION_UPSERT, (_generation_bind(row) for row in rows))


def mark_authoritative_missing(db: sqlite3.Connection, account_id, folder_id, observed_at) -> int:
    _write_transaction(db)
    return db.execute(
        "UPDATE provider_state AS o SET state='unavailable',provider_folder_id=NULL,"
        "observed_at=?,payload_json=? WHERE record_type='observation' "
        "AND account_id=? AND provider_folder_id=? "
        "AND NOT EXISTS (SELECT 1 FROM current_provider_membership AS m "
        "WHERE m.account_id=o.account_id AND m.provider_folder_id=? "
        "AND m.stable_message_identity=o.stable_message_identity) AND "
        + _CHANGED_TOMBSTONE,
        (observed_at, _NULL_PAYLOAD, account_id, folder_id, folder_id, observed_at),
    ).rowcount


def mark_unavailable_folder(db: sqlite3.Connection, account_id, folder_id, observed_at) -> int:
    _write_transaction(db)
    return db.execute(
        "UPDATE provider_state SET state='unavailable',observed_at=?,"
        "payload_json=json_set(payload_json,'$.category_key',json('null'),"
        "'$.important',json('null'),'$.important_signals_json',json('null')) "
        "WHERE record_type='observation' AND account_id=? AND provider_folder_id=? "
        "AND (state IS NOT 'unavailable' OR category_key IS NOT NULL "
        "OR important IS NOT NULL OR important_signals_json IS NOT NULL "
        "OR observed_at IS NOT ?)",
        (observed_at, account_id, folder_id, observed_at),
    ).rowcount


def mark_inactive_accounts(db: sqlite3.Connection, observed_at) -> int:
    _write_transaction(db)
    return db.execute(
        "UPDATE provider_state AS o SET state='unavailable',provider_folder_id=NULL,"
        "observed_at=?,payload_json=? WHERE record_type='observation' "
        "AND NOT EXISTS (SELECT 1 FROM current_provider_accounts AS a "
        "WHERE a.account_id=o.account_id) AND " + _CHANGED_TOMBSTONE,
        (observed_at, _NULL_PAYLOAD, observed_at),
    ).rowcount


def update_important_signals(
    db: sqlite3.Connection, account_id, stable_message_identity,
    important, important_signals_json,
) -> bool:
    _write_transaction(db)
    return db.execute(
        "UPDATE provider_state SET payload_json=json_set(payload_json,"
        "'$.important',json(?),'$.important_signals_json',json(?)) "
        "WHERE record_type='observation' AND account_id=? "
        "AND stable_message_identity=? AND state='available'",
        (_json(_encoded_native_sqlite_value(important)),
         _json(_encoded_native_sqlite_value(important_signals_json)),
         account_id, stable_message_identity),
    ).rowcount == 1
