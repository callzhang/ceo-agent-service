"""Exact semantic-reference translation used by the unactivated Task migration.

These helpers deliberately operate on identities collected by the enclosing
caller-owned migration. They do not infer an owner from a numeric-looking key.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import json


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


def _feedback_scope_receipt(value: object) -> tuple[object, bool]:
    """Decode only source-admitted JSON text; leave other SQLite values opaque."""
    if isinstance(value, str):
        raw = value
    elif isinstance(value, (bytes, bytearray, memoryview)):
        try:
            raw = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return value, False
    else:
        return value, False
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value, False
    if not isinstance(parsed, dict):
        return value, False
    return parsed, True


def translate_feedback_scope_receipt(
    value: object, indexes: Mapping, *, unresolved: list[dict] | None = None,
) -> object:
    """Translate the three typed owner leaves embedded in a scope receipt."""
    receipt, decoded = _feedback_scope_receipt(value)
    if not decoded:
        return value
    original = value
    is_blob = isinstance(value, (bytes, bytearray, memoryview))
    changed = False
    decision_id = receipt.get("decision_id")
    if type(decision_id) is int:
        target = indexes["public"].get((
            "feedback_iteration_decisions", ("integer", decision_id),
        ))
        if target is not None:
            receipt["decision_id"] = target
            changed = True
        elif unresolved is not None:
            unresolved.append({"field": "scope_receipt_json.decision_id",
                               "value": decision_id, "type": "integer"})
    evidence = receipt.get("evidence")
    associations = evidence.get("associations") if isinstance(evidence, dict) else None
    if isinstance(associations, dict):
        for key, association in associations.items():
            if not isinstance(association, dict):
                continue
            for field, kind in (("workbench_task_id", "workbench_tasks"),
                                ("workbench_turn_id", "workbench_turns")):
                ref = association.get(field)
                if not isinstance(ref, str):
                    continue
                target = indexes["public"].get((kind, ("text", ref)))
                marker_field = f"scope_receipt_json.associations.{key}.{field}"
                if target is None:
                    if ref and unresolved is not None:
                        unresolved.append({"field": marker_field, "value": ref,
                                           "type": "text"})
                else:
                    association[field] = str(target)
                    changed = True
    if not changed:
        return original
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    return encoded.encode("utf-8") if is_blob else encoded


def restore_feedback_scope_receipt(
    value: object, indexes: Mapping, *, unresolved: Mapping[str, Mapping] | None = None,
) -> object:
    """Restore only current typed owner leaves, preserving captured unresolved facts."""
    receipt, decoded = _feedback_scope_receipt(value)
    if not decoded:
        return value
    original = value
    is_blob = isinstance(value, (bytes, bytearray, memoryview))
    changed = False
    decision_id = receipt.get("decision_id")
    if type(decision_id) is int:
        marker = (unresolved or {}).get("scope_receipt_json.decision_id")
        if marker is not None and marker.get("value") == decision_id:
            source_id = marker["value"]
            if source_id != decision_id:
                receipt["decision_id"] = source_id
                changed = True
        else:
            refs = [key for (kind, key), target in indexes["public"].items()
                    if kind == "feedback_iteration_decisions" and target == decision_id]
            if len(refs) > 1:
                raise ValueError("ambiguous native Feedback decision identity during inverse")
            if refs and refs[0][0] == "integer":
                receipt["decision_id"] = refs[0][1]
                changed = True
    evidence = receipt.get("evidence")
    associations = evidence.get("associations") if isinstance(evidence, dict) else None
    if isinstance(associations, dict):
        for key, association in associations.items():
            if not isinstance(association, dict):
                continue
            for field, kind in (("workbench_task_id", "workbench_tasks"),
                                ("workbench_turn_id", "workbench_turns")):
                ref = association.get(field)
                if not isinstance(ref, str):
                    continue
                marker_field = f"scope_receipt_json.associations.{key}.{field}"
                marker = (unresolved or {}).get(marker_field)
                if marker is not None and marker.get("value") == ref:
                    continue
                if not (ref.isascii() and ref.isdecimal() and str(int(ref)) == ref):
                    continue
                target_id = int(ref)
                refs = [public_key for (owner_kind, public_key), target
                        in indexes["public"].items()
                        if owner_kind == kind and target == target_id]
                if len(refs) > 1:
                    raise ValueError("ambiguous native Workbench identity during inverse")
                if refs and refs[0][0] == "text":
                    association[field] = str(refs[0][1])
                    changed = True
    if not changed:
        return original
    encoded = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    return encoded.encode("utf-8") if is_blob else encoded


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
