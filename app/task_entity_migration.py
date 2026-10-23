"""Exact semantic-reference translation used by the unactivated Task migration.

These helpers deliberately operate on identities collected by the enclosing
caller-owned migration. They do not infer an owner from a numeric-looking key.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping


def _typed_key(value: object) -> tuple[str, object]:
    if value is None:
        return "null", None
    if isinstance(value, bool):
        return "integer", int(value)
    if isinstance(value, int):
        return "integer", value
    if isinstance(value, str):
        return "text", value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "blob", bytes(value)
    raise TypeError(f"unsupported SQLite identity value: {type(value).__name__}")


def owner_indexes(rows: Iterable[Mapping[str, object]]) -> dict:
    """Build kind-qualified physical and typed public identity indexes."""
    physical: dict[tuple[str, int], int] = {}
    public: dict[tuple[str, tuple[str, object]], int] = {}
    for row in rows:
        kind = row["source_kind"]
        source_id = row["physical_id"]
        target_id = row["target_id"]
        if not isinstance(kind, str) or type(source_id) is not int or type(target_id) is not int:
            raise ValueError("invalid Task owner identity row")
        identity = (kind, source_id)
        if identity in physical:
            raise ValueError("duplicate source physical identity")
        physical[identity] = target_id
        key = (kind, _typed_key(row.get("public_key")))
        if key in public:
            raise ValueError("ambiguous typed source public identity")
        public[key] = target_id
    return {"physical": physical, "public": public}


def translate_feedback_workbench_refs(
    *, task_ref: object, turn_ref: object, task_indexes: Mapping, turn_indexes: Mapping,
    unresolved: list[dict] | None = None,
) -> tuple[object, object]:
    """Translate only exact, currently resolved Workbench public TEXT refs.

    Unresolved historical values are preserved byte-for-byte as text. Resolved
    values become canonical native owner IDs encoded as TEXT; the sparse marker
    records only enough information to reverse that translation.
    """
    markers: list[dict] = [] if unresolved is None else unresolved
    task_target = (None if not isinstance(task_ref, str) else task_indexes["public"].get(
        ("workbench_tasks", ("text", task_ref))))
    turn_target = (None if not isinstance(turn_ref, str) else turn_indexes["public"].get(
        ("workbench_turns", ("text", turn_ref))))
    original_task, original_turn = task_ref, turn_ref
    if task_target is None and isinstance(original_task, str) and original_task:
        markers.append({"field": "workbench_task_id", "value": original_task, "type": "text"})
    if turn_target is None and isinstance(original_turn, str) and original_turn:
        markers.append({"field": "workbench_turn_id", "value": original_turn, "type": "text"})
    return (original_task if task_target is None else str(task_target),
            original_turn if turn_target is None else str(turn_target))


def restore_feedback_workbench_ref(
    *, field: str, current_value: object, indexes: Mapping,
    unresolved_marker: Mapping | None = None,
) -> object:
    """Restore a canonical Workbench integer-text reference to its old key."""
    if field not in {"workbench_task_id", "workbench_turn_id"}:
        raise ValueError("unsupported feedback Workbench reference field")
    if not isinstance(current_value, str):
        return current_value
    if unresolved_marker is not None:
        if (set(unresolved_marker) != {"field", "value", "type"}
                or unresolved_marker["field"] != field
                or unresolved_marker["type"] != "text"
                or not isinstance(unresolved_marker["value"], str)
                or unresolved_marker["value"] == ""):
            raise ValueError("invalid unresolved Workbench identity marker")
        return unresolved_marker["value"]
    kind = "workbench_tasks" if field == "workbench_task_id" else "workbench_turns"
    if not (current_value.isascii() and current_value.isdecimal()
            and str(int(current_value)) == current_value):
        return current_value
    target_id = int(current_value)
    matches = [key for (owner_kind, key), target in indexes["public"].items()
               if owner_kind == kind and target == target_id]
    if not matches:
        return current_value
    if len(matches) != 1 or matches[0][0] != "text":
        raise ValueError("ambiguous native Workbench identity during inverse")
    return str(matches[0][1])


def translate_feedback_round_pointer(value: object, indexes: Mapping,
                                     unresolved: list[dict] | None = None) -> object:
    """Map a captured item pointer only when it is an exact typed round ID."""
    if value is None or type(value) is not int or value == 0:
        return value
    target = indexes["public"].get(("feedback_processing_rounds", ("integer", value)))
    if target is None:
        if unresolved is not None:
            unresolved.append({"field": "current_round_id", "value": value, "type": "integer"})
        return value
    return target


def restore_feedback_round_pointer(value: object, indexes: Mapping,
                                   unresolved_marker: Mapping | None = None) -> object:
    """Restore a mapped round ID, preserving captured zero and unresolved IDs."""
    if unresolved_marker is not None:
        if (set(unresolved_marker) != {"field", "value", "type"}
                or unresolved_marker["field"] != "current_round_id"
                or unresolved_marker["type"] != "integer"
                or type(unresolved_marker["value"]) is not int
                or unresolved_marker["value"] == 0):
            raise ValueError("invalid unresolved Feedback round identity marker")
        return unresolved_marker["value"]
    if value is None or type(value) is not int or value == 0:
        return value
    matches = [key for (kind, key), target in indexes["public"].items()
               if kind == "feedback_processing_rounds" and target == value]
    if not matches:
        return value
    if len(matches) != 1 or matches[0][0] != "integer":
        raise ValueError("ambiguous native Feedback round identity during inverse")
    return matches[0][1]


def translate_action_feedback_parent(
    *, source_kind: str, source_public_id: object, indexes: Mapping,
) -> int | None:
    """Map an Action FK's logical public ID to its actual native parent ID."""
    if source_kind not in {
        "feedback_iteration_decisions", "feedback_processing_rounds",
    }:
        raise ValueError("unsupported Action feedback parent kind")
    key = (source_kind, _typed_key(source_public_id))
    try:
        return indexes["public"][key]
    except KeyError as exc:
        raise ValueError(f"missing {source_kind} owner for Action parent") from exc
