"""Native Task, TaskInput and TaskBusinessKey storage.

The caller supplies the connection and owns its transaction. These functions
operate only on the assembled target schema and never read source-schema rows.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from app.action_storage import _encoded_native_sqlite_value


_TASK_FIELDS = frozenset({
    "kind", "status", "available_at", "attempts", "error", "locked_at",
    "lease_owner", "lease_expires_at", "owner_pid", "generation", "adapter_name",
    "execution_generation", "current_input_id", "claimed_input_id", "input_version",
    "claimed_input_version", "created_at", "updated_at",
})
_INPUT_FIELDS = frozenset({
    "channel", "conversation_id", "conversation_title", "single_chat",
    "trigger_message_id", "trigger_create_time", "trigger_sender", "trigger_text",
    "trigger_message_json", "oa_url", "business_object_key", "input_revision_key",
    "created_at", "input_provenance_json", "input_compacted",
})
_JSON_TEXT_INPUTS = frozenset({"trigger_message_json", "input_provenance_json"})


def _columns(db: sqlite3.Connection, table: str) -> frozenset[str]:
    return frozenset(str(row[1]) for row in db.execute(f'pragma table_info("{table}")'))


def _only_fields(values: Mapping[str, object], allowed: frozenset[str], owner: str) -> dict[str, object]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{owner} values must be a mapping")
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"unknown {owner} fields: {', '.join(sorted(unknown))}")
    return dict(values)


def _jsonb(db: sqlite3.Connection, value: object) -> bytes:
    packed = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return bytes(db.execute("select jsonb(?)", (packed,)).fetchone()[0])


def _parameters_blob(db: sqlite3.Connection, parameters: Mapping[str, object] | None) -> bytes:
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, Mapping):
        raise TypeError("parameters must be a mapping")
    # The schema's kind-specific triggers are the authoritative request contract.
    # The scalar codec preserves the Python SQLite binding type for each value.
    encoded = {
        key: _encoded_native_sqlite_value(int(value) if isinstance(value, bool) else value)
        for key, value in parameters.items()
    }
    return _jsonb(db, encoded)


def _decode_scalar(value: object) -> object:
    if isinstance(value, dict) and value.get("t") == "blob" and set(value) == {"t", "v"}:
        return bytes.fromhex(str(value["v"]))
    if isinstance(value, dict) and value.get("t") == "real" and set(value) == {"t", "m", "e"}:
        return float(value["m"] * (2 ** value["e"]))
    if isinstance(value, dict) and value.get("t") == "inf" and set(value) == {"t"}:
        return float("inf")
    if isinstance(value, dict) and value.get("t") == "-inf" and set(value) == {"t"}:
        return float("-inf")
    return value


def _task_row(db: sqlite3.Connection, row: sqlite3.Row | tuple | None) -> dict[str, object] | None:
    if row is None:
        return None
    result = dict(row)
    raw = db.execute("select json(parameters_json) from tasks where id=?", (result["id"],)).fetchone()[0]
    encoded = json.loads(raw)
    result["parameters"] = {key: _decode_scalar(value) for key, value in encoded.items()}
    result.pop("parameters_json", None)
    return result


def create_task(
    db: sqlite3.Connection, *, values: Mapping[str, object],
    parameters: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Insert one native Task; target defaults and constraints remain SQLite-owned."""
    task = _only_fields(values, _TASK_FIELDS, "Task")
    if "kind" not in task or "status" not in task:
        raise ValueError("Task kind and status are required")
    columns = set(task) | {"parameters_json"}
    missing = columns - _columns(db, "tasks")
    if missing:
        raise ValueError(f"Task target lacks fields: {', '.join(sorted(missing))}")
    ordered = list(task) + ["parameters_json"]
    args = [task[key] for key in task] + [_parameters_blob(db, parameters)]
    cursor = db.execute(
        f"insert into tasks ({','.join(ordered)}) values ({','.join('?' for _ in ordered)}) returning *",
        args,
    )
    return _task_row(db, cursor.fetchone())  # type: ignore[return-value]


def get_task(db: sqlite3.Connection, task_id: int) -> dict[str, object] | None:
    return _task_row(db, db.execute("select * from tasks where id=?", (task_id,)).fetchone())


def update_task_cas(
    db: sqlite3.Connection, *, task_id: int, expected: Mapping[str, object],
    changes: Mapping[str, object], parameters: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    """Update exactly the caller's compare set; return None when the CAS misses."""
    where = _only_fields(expected, _TASK_FIELDS, "Task expectation")
    update = _only_fields(changes, _TASK_FIELDS - {"kind"}, "Task update")
    if not where or not update:
        raise ValueError("Task CAS requires expected values and changes")
    assignments = [f'"{key}"=?' for key in update]
    args: list[object] = list(update.values())
    if parameters is not None:
        current = get_task(db, task_id)
        if current is None:
            return None
        assignments.append("parameters_json=?")
        args.append(_parameters_blob(db, parameters))
    predicates = ["id=?"]
    args.append(task_id)
    for key, value in where.items():
        predicates.append(f'"{key}" IS ?')
        args.append(value)
    row = db.execute(
        f"update tasks set {','.join(assignments)} where {' and '.join(predicates)} returning *", args
    ).fetchone()
    return _task_row(db, row)


def append_task_input(
    db: sqlite3.Connection, *, task_id: int, values: Mapping[str, object],
) -> tuple[dict[str, object], bool]:
    """Append immutable input by its complete source key, preserving original facts."""
    item = _only_fields(values, _INPUT_FIELDS, "TaskInput")
    key_fields = ("channel", "conversation_id", "trigger_message_id", "input_revision_key")
    if any(field not in item for field in key_fields):
        raise ValueError("TaskInput requires the complete source identity")
    if "trigger_text" not in item:
        raise ValueError("TaskInput trigger_text is required")
    target = _columns(db, "task_inputs")
    columns = {"task_id", *item}
    if columns - target:
        raise ValueError("TaskInput target schema mismatch")
    ordered = ["task_id", *item]
    args = [task_id, *item.values()]
    cursor = db.execute(
        f"insert or ignore into task_inputs ({','.join(ordered)}) values ({','.join('?' for _ in ordered)})",
        args,
    )
    inserted = cursor.rowcount == 1
    row = db.execute(
        "select * from task_inputs where channel=? and conversation_id=? and trigger_message_id=? and input_revision_key=?",
        tuple(item[field] for field in key_fields),
    ).fetchone()
    if row is None:
        raise sqlite3.IntegrityError("TaskInput full identity conflicts with an existing target row")
    if int(row["task_id"]) != task_id:
        raise sqlite3.IntegrityError("TaskInput full identity belongs to another Task")
    return dict(row), inserted


def get_task_input(db: sqlite3.Connection, input_id: int) -> dict[str, object] | None:
    row = db.execute("select * from task_inputs where id=?", (input_id,)).fetchone()
    return None if row is None else dict(row)


def bind_current_input(
    db: sqlite3.Connection, *, task_id: int, input_id: int,
    expected_input_version: int | None, input_version: int,
) -> dict[str, object] | None:
    """Advance current input and its caller-supplied version with a version CAS."""
    if not isinstance(input_version, int) or isinstance(input_version, bool):
        raise TypeError("input_version must be an integer supplied by the caller")
    row = db.execute(
        "update tasks set current_input_id=?,input_version=? where id=? and current_input_id is not ? and input_version is ? "
        "and exists(select 1 from task_inputs where id=? and task_id=tasks.id) returning *",
        (input_id, input_version, task_id, input_id, expected_input_version, input_id),
    ).fetchone()
    return _task_row(db, row)


def bind_claimed_input(
    db: sqlite3.Connection, *, task_id: int, input_id: int,
    claimed_input_version: int | None,
) -> dict[str, object] | None:
    """Bind the execution snapshot independently from the current input."""
    row = db.execute(
        "update tasks set claimed_input_id=?,claimed_input_version=? where id=? "
        "and exists(select 1 from task_inputs where id=? and task_id=tasks.id) returning *",
        (input_id, claimed_input_version, task_id, input_id),
    ).fetchone()
    return _task_row(db, row)


def get_task_with_inputs(db: sqlite3.Connection, task_id: int) -> dict[str, object] | None:
    task = get_task(db, task_id)
    if task is None:
        return None
    task["current_input"] = get_task_input(db, task["current_input_id"]) if task["current_input_id"] is not None else None
    task["claimed_input"] = get_task_input(db, task["claimed_input_id"]) if task["claimed_input_id"] is not None else None
    return task


def associate_business_key(
    db: sqlite3.Connection, *, task_id: int, business_key: str,
    created_at: str | None = None, updated_at: str | None = None,
) -> dict[str, object]:
    """Associate one globally unique business key with a Task; never merges Tasks."""
    values: dict[str, object] = {"task_id": task_id, "business_key": business_key}
    if created_at is not None:
        values["created_at"] = created_at
    if updated_at is not None:
        values["updated_at"] = updated_at
    columns = list(values)
    row = db.execute(
        f"insert into task_business_keys ({','.join(columns)}) values ({','.join('?' for _ in columns)}) returning *",
        list(values.values()),
    ).fetchone()
    return dict(row)


def list_task_business_keys(db: sqlite3.Connection, task_id: int) -> list[dict[str, object]]:
    return [dict(row) for row in db.execute(
        "select * from task_business_keys where task_id=? order by id", (task_id,)
    )]


def get_meeting_delivery_key(db: sqlite3.Connection, task_id: int) -> str:
    """Return the exact persisted delivery identity for a native Meeting Task."""
    owner = db.execute(
        "select kind from tasks where id=?", (task_id,),
    ).fetchone()
    if owner is None or owner[0] != "meeting_alignment":
        raise ValueError("Meeting delivery identity requires a Meeting Task")
    rows = db.execute(
        "select business_key from task_business_keys where task_id=? order by id",
        (task_id,),
    ).fetchall()
    if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0]:
        raise ValueError("Meeting Task must own exactly one delivery identity")
    return rows[0][0]
