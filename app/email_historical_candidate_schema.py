"""Current-data transformation for historical candidate locator ownership."""
from __future__ import annotations

import json
import sqlite3


SOURCE_VERSION = 53
TARGET_VERSION = 54


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(raw: str) -> dict:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("historical candidate provider message must be a JSON object")
    return value


def _matching_locator_keys(row: sqlite3.Row, payload: dict) -> tuple[str, ...]:
    expected = {
        "accountId": (str, row["account_id"]),
        "folder": (str, row["folder"]),
        "stableMessageIdentity": (str, row["stable_message_identity"]),
        "uidValidity": (int, row["uidvalidity"]),
        "uid": (int, row["uid"]),
    }
    matches = []
    for key, (kind, scalar) in expected.items():
        value = payload.get(key)
        if type(value) is kind and value == scalar:
            matches.append(key)
    return tuple(matches)


def compact_projection(payload: dict, *, account_id: str, folder: str,
                       stable_message_identity: str, uidvalidity: int, uid: int) -> dict:
    """Return a compact payload using the same typed same-row ownership rule."""
    scalar_values = {
        "accountId": (str, account_id), "folder": (str, folder),
        "stableMessageIdentity": (str, stable_message_identity),
        "uidValidity": (int, uidvalidity), "uid": (int, uid),
    }
    compact = dict(payload)
    for key, (kind, scalar) in scalar_values.items():
        value = compact.get(key)
        if type(value) is kind and value == scalar:
            del compact[key]
    return compact


def compact_candidate_locators(db: sqlite3.Connection) -> int:
    """Remove only exact typed locator duplicates represented by same-row columns."""
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "select account_id,folder,model_id,stable_message_identity,provider_message_json,"
        "uidvalidity,uid from email_historical_candidates"
    ).fetchall()
    updates = []
    for row in rows:
        payload = _load(row["provider_message_json"])
        required = {"accountId", "folder", "stableMessageIdentity", "uidValidity", "uid"}
        missing = required - payload.keys()
        if missing:
            raise ValueError(
                "Email 53 candidate locator inverse would be ambiguous: missing "
                + ", ".join(sorted(missing))
            )
        keys = _matching_locator_keys(row, payload)
        if not keys:
            continue
        for key in keys:
            del payload[key]
        updates.append((_dump(payload), row["account_id"], row["folder"], row["model_id"],
                         row["stable_message_identity"]))
    db.executemany(
        "update email_historical_candidates set provider_message_json=? "
        "where account_id=? and folder=? and model_id=? and stable_message_identity=?",
        updates,
    )
    return len(updates)


def restore_candidate_locators(db: sqlite3.Connection) -> int:
    """Rebuild the compacted locator keys from their exact scalar owners."""
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "select account_id,folder,model_id,stable_message_identity,provider_message_json,"
        "uidvalidity,uid from email_historical_candidates"
    ).fetchall()
    changed = 0
    for row in rows:
        payload = _load(row["provider_message_json"])
        values = {
            "accountId": row["account_id"], "folder": row["folder"],
            "stableMessageIdentity": row["stable_message_identity"],
            "uidValidity": row["uidvalidity"], "uid": row["uid"],
        }
        added = False
        for key, value in values.items():
            if key not in payload:
                payload[key] = value
                added = True
        if not added:
            continue
        db.execute(
            "update email_historical_candidates set provider_message_json=? "
            "where account_id=? and folder=? and model_id=? and stable_message_identity=?",
            (_dump(payload), row["account_id"], row["folder"], row["model_id"],
             row["stable_message_identity"]),
        )
        changed += 1
    return changed


def reconstruct_projection(row: sqlite3.Row) -> dict:
    """Rebuild the public current provider-message projection from scalar owners."""
    payload = _load(row["provider_message_json"])
    values = {
        "accountId": row["account_id"], "folder": row["folder"],
        "stableMessageIdentity": row["stable_message_identity"],
        "uidValidity": row["uidvalidity"], "uid": row["uid"],
    }
    for key, value in values.items():
        payload.setdefault(key, value)
    return payload
