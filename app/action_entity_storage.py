"""Caller-transaction APIs for the shared external Action owner."""

from collections.abc import Mapping
import json
import sqlite3

from app.action_entity_definitions import (
    ACTION_ALLOWED_FIELDS,
    ACTION_FIELDS,
    ACTION_KIND_CODE,
    RECEIPT_ALLOWED_FIELDS,
)

_PUBLIC_FIELDS = ("id", *ACTION_FIELDS)


def _mapping(values, allowed, label):
    if not isinstance(values, Mapping):
        raise TypeError(f"{label} must be a Mapping")
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"unknown {label} fields: {sorted(unknown)}")
    return dict(values)


def _read_action(db: sqlite3.Connection, action_id: int) -> dict | None:
    fields = ",".join("action_" + name + " as " + name for name in ACTION_FIELDS)
    row = db.execute(
        f"select id,{fields} from actions where id=? and record_type='action'",
        (action_id,),
    ).fetchone()
    return dict(zip(_PUBLIC_FIELDS, row, strict=True)) if row is not None else None


def create_action(db: sqlite3.Connection, *, values: Mapping[str, object]) -> dict:
    """Create one Action; the caller controls commit or rollback."""
    action = {name: None for name in ACTION_FIELDS}
    action.update(_mapping(values, ACTION_ALLOWED_FIELDS, "Action"))
    payload = json.dumps(action, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    row = db.execute(
        "insert into actions(_kind,legacy_key,action_data,payload_json) "
        "values(?,null,jsonb(?),jsonb('[]')) returning id",
        (ACTION_KIND_CODE, payload),
    ).fetchone()
    return _read_action(db, row[0])


def get_action(db: sqlite3.Connection, action_id: int) -> dict | None:
    return _read_action(db, action_id)


def _cas_action(db: sqlite3.Connection, action_id: int, *, expected: Mapping[str, object],
                changes: Mapping[str, object]) -> dict | None:
    expected_values = _mapping(expected, ACTION_ALLOWED_FIELDS, "expected Action")
    changed_values = _mapping(changes, ACTION_ALLOWED_FIELDS, "Action changes")
    if not expected_values:
        raise ValueError("Action compare-and-set requires expected values")
    if not changed_values:
        raise ValueError("Action compare-and-set requires changes")
    expressions = []
    arguments = []
    for name, value in changed_values.items():
        expressions.extend((f"'$.{name}'", "json(?)"))
        arguments.append(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    predicate = " and ".join("action_" + name + " is ?" for name in expected_values)
    arguments.extend(expected_values.values())
    arguments.append(action_id)
    cursor = db.execute(
        "update actions set action_data=jsonb_set(action_data," + ",".join(expressions) + ") "
        "where record_type='action' and " + predicate + " and id=? returning id",
        arguments,
    )
    row = cursor.fetchone()
    return _read_action(db, row[0]) if row is not None else None


def claim_action(db: sqlite3.Connection, action_id: int, *, expected: Mapping[str, object],
                 changes: Mapping[str, object]) -> dict | None:
    return _cas_action(db, action_id, expected=expected, changes=changes)


def record_action_result(db: sqlite3.Connection, action_id: int, *,
                         expected: Mapping[str, object], changes: Mapping[str, object]) -> dict | None:
    return _cas_action(db, action_id, expected=expected, changes=changes)


def add_action_observer(db: sqlite3.Connection, action_id: int, *, agent_run_id: int,
                        task_id: int, created_at: str) -> None:
    db.execute(
        "insert or ignore into action_observers(action_id,agent_run_id,task_id,created_at) "
        "select id,?,?,? from actions where id=? and record_type='action'",
        (agent_run_id, task_id, created_at, action_id),
    )


def append_action_receipt(db: sqlite3.Connection, action_id: int, *,
                          values: Mapping[str, object]) -> int:
    receipt = _mapping(values, RECEIPT_ALLOWED_FIELDS | {"execution_run_id"}, "Action receipt")
    names = ("action_id", *receipt)
    bound = (action_id, *receipt.values())
    placeholders = ",".join("?" for _ in names)
    row = db.execute(
        f"insert into action_receipts({','.join(names)}) "
        f"select {placeholders} where exists(select 1 from actions where id=? and record_type='action') "
        "returning id",
        (*bound, action_id),
    ).fetchone()
    if row is None:
        raise sqlite3.IntegrityError("missing Action receipt owner")
    return row[0]


def update_sent_receipt(db: sqlite3.Connection, action_id: int, *,
                        receipt_id: int, expected: Mapping[str, object],
                        changes: Mapping[str, object],
                        execution_owner_column: str = "execution_id") -> int | None:
    """CAS mutable reference and initially-unknown canonical facts on Sent receipt."""
    mutable = {"provider_receipt_id", "operation", "target", "result_digest",
               "agent_run_id", "created_at", "updated_at"}
    old = _mapping(expected, mutable, "expected Sent receipt")
    new = _mapping(changes, mutable, "Sent receipt changes")
    if not old or not new:
        raise ValueError("Sent receipt update requires expected values and changes")
    if execution_owner_column not in ("execution_id", "execution_run_id"):
        raise ValueError("invalid Sent receipt execution owner column")
    assignments = ",".join(f"{field}=?" for field in new)
    predicates = " and ".join(f"{field} is ?" for field in old)
    row = db.execute(
        f"update action_receipts set {assignments} where id=? and action_id=? "
        f"and {execution_owner_column} is null and action_index is null and {predicates} returning id",
        (*new.values(), receipt_id, action_id, *old.values()),
    ).fetchone()
    return None if row is None else int(row[0])


def update_action_observation(db: sqlite3.Connection, action_id: int, *,
                              execution_id: int | None = None,
                              execution_run_id: int | None = None,
                              action_index: int,
                              expected: Mapping[str, object],
                              changes: Mapping[str, object]) -> int | None:
    """Compare-and-set mutable facts on one execution/action observation."""
    mutable = {"status", "outcome_json", "agent_run_id", "observed_at", "updated_at",
               "provider_receipt_id"}
    old = _mapping(expected, mutable, "expected Action observation")
    new = _mapping(changes, mutable, "Action observation changes")
    if not old or not new:
        raise ValueError("Action observation update requires expected values and changes")
    assignments = ",".join(f"{field}=?" for field in new)
    predicates = " and ".join(f"{field} is ?" for field in old)
    if (execution_id is None) == (execution_run_id is None):
        raise ValueError("Action observation requires exactly one execution owner")
    owner_column, owner_id = (("execution_id", execution_id) if execution_id is not None
                              else ("execution_run_id", execution_run_id))
    row = db.execute(
        f"update action_receipts set {assignments} where action_id=? and {owner_column}=? "
        f"and action_index=? and {predicates} returning id",
        (*new.values(), action_id, owner_id, action_index, *old.values()),
    ).fetchone()
    return None if row is None else int(row[0])
