"""Finite SQL parts for the existing eight Tasks kinds; no transaction owner.

Callers retain the native statement verb, CTE/FROM/WHERE/RETURNING and cursor.
These builders do not access a connection or select a runtime schema.
Proposal only: native differential tests and complete implementation plan remain
required before application transfer.
"""

from dataclasses import dataclass
import json

from app.action_storage import _encoded_native_sqlite_value, _native_payload
from app.state_storage import _json_assignments
from app.task20_definitions import (
    TASK20_COLUMNS,
    TASK20_DEFAULTS,
    TASK20_NEW_KINDS,
    TASK20_PAYLOAD_FIELDS,
    task20_column,
)

TASK8_KINDS = tuple(kind for kind in TASK20_COLUMNS if kind not in TASK20_NEW_KINDS)


@dataclass(frozen=True)
class Task8InsertParts:
    columns: str
    values: str
    parameters: tuple


@dataclass(frozen=True)
class Task8SetParts:
    sql: str
    parameters: tuple


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _partition(kind, values, expressions, structural_sql):
    if kind not in TASK8_KINDS:
        raise ValueError("unsupported existing Tasks kind")
    physical = {c[4] for c in TASK20_COLUMNS[kind] if c[4] not in ("payload", "none")}
    if set(values) - set(TASK20_PAYLOAD_FIELDS[kind]) - physical:
        raise ValueError("unsupported existing Tasks field")
    if set(expressions) - set(TASK20_PAYLOAD_FIELDS[kind]):
        raise ValueError("unsupported existing Tasks payload field")
    if set(structural_sql) - physical:
        raise ValueError("unsupported existing Tasks physical field")
    if set(values) & (set(expressions) | set(structural_sql)):
        raise ValueError("existing Tasks field supplied twice")
    for value in values.values():
        _encoded_native_sqlite_value(value)
    payload = {f: v for f, v in values.items() if f not in physical}
    structural = {f: v for f, v in values.items() if f in physical}
    return payload, structural


def task8_insert_parts(
    kind,
    values,
    *,
    insert_json=None,
    insert_parameters=(),
    structural_sql=None,
    structural_parameters=(),
):
    """Compile native INSERT parts; expression parameters follow payload binding."""
    expressions = {} if insert_json is None else insert_json
    physical_sql = {} if structural_sql is None else structural_sql
    payload, physical = _partition(kind, values, expressions, physical_sql)
    defaults = {
        field: "json_quote(" + TASK20_DEFAULTS[kind].get(field, "null") + ")"
        for field in TASK20_PAYLOAD_FIELDS[kind]
        if field not in payload and field not in expressions
    }
    columns = ["record_type", *physical, *physical_sql, "payload_json"]
    scalar_sql = [repr(kind), *("?" for _ in physical), *physical_sql.values()]
    scalar_sql.append(_json_assignments("?", defaults | expressions))
    return Task8InsertParts(
        ",".join(map(_quote, columns)),
        ",".join(scalar_sql),
        (
            *physical.values(),
            *structural_parameters,
            _native_payload(payload),
            *insert_parameters,
        ),
    )


def task8_set_parts(
    kind,
    values,
    *,
    update_json=None,
    update_parameters=(),
    structural_sql=None,
    structural_parameters=(),
    payload_source="tasks.payload_json",
):
    """Compile one native SET; all RHS expressions still see the original row."""
    expressions = {} if update_json is None else update_json
    physical_sql = {} if structural_sql is None else structural_sql
    payload, physical = _partition(kind, values, expressions, physical_sql)
    json_expressions = {field: "?" for field in payload} | expressions
    assignments = []
    if json_expressions:
        assignments.append(
            "payload_json=" + _json_assignments(payload_source, json_expressions)
        )
    assignments.extend(_quote(field) + "=?" for field in physical)
    assignments.extend(
        _quote(field) + "=" + expression for field, expression in physical_sql.items()
    )
    if not assignments:
        raise ValueError("existing Tasks SET requires fields")
    encoded = tuple(
        json.dumps(
            _encoded_native_sqlite_value(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        for value in payload.values()
    )
    return Task8SetParts(
        ",".join(assignments),
        (*encoded, *update_parameters, *physical.values(), *structural_parameters),
    )


def task8_json_field(kind, field, source="tasks"):
    """Read an existing payload leaf without losing native scalar codec tags."""
    if kind not in TASK8_KINDS or field not in TASK20_PAYLOAD_FIELDS[kind]:
        raise ValueError("unsupported existing Tasks payload field")
    return _quote(source) + ".payload_json->'$." + field + "'"


def task8_meeting_returning():
    """Keep physical delivery-claim id and public model identity distinct."""
    kind = "meeting_alignment_jobs"
    fields = ["id", "legacy_key"]
    fields.extend(
        task20_column(kind, c[0]) + " as " + _quote(c[0])
        for c in TASK20_COLUMNS[kind]
        if c[0] != "id"
    )
    return ",".join(fields)
