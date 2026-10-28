"""Native provider-state test fixtures."""
import sqlite3
from collections.abc import Mapping
from app.provider_state_definitions import OBSERVATION_PROJECTION, GENERATION_PROJECTION
from app.provider_state_storage import _write_transaction, _observation_bind, _generation_bind


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
        "(record_type,account_id,stable_message_identity,state,provider_folder_id,"
        "provider_folder_name,category_key,important,observed_at,important_signals_json) "
        "VALUES('observation',?,?,?,?,?,?,?,?,?)",
        _observation_bind(values),
    ).rowcount


def insert_folder_generation(
    db: sqlite3.Connection, values: Mapping[str, object], *, ignore: bool = False,
) -> int:
    _write_transaction(db)
    action = "INSERT OR IGNORE" if ignore else "INSERT"
    return db.execute(
        f"{action} INTO provider_state "
        "(record_type,account_id,provider_folder_id,observed_at) "
        "VALUES('folder_generation',?,?,?)",
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
