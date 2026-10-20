"""Finite SQL parts for the existing eight Tasks kinds; no transaction owner.

Callers retain the native statement verb, CTE/FROM/WHERE/RETURNING and cursor.
These builders do not access a connection or select a runtime schema.
Current Store/Email/Workbench callers register the codec on their existing
connections and select these parts with the paired Tasks20 release.
"""

from dataclasses import dataclass
import json

from app.action_storage import _encoded_native_sqlite_value
from app.storage_payload import TASK_RECORD_TYPES
from app.task20_definitions import (
    TASK20_COLUMNS,
    TASK20_NEW_KINDS,
    TASK20_PAYLOAD_FIELDS,
    task20_column,
)
from app.task_lifecycle_storage import source_assignments, source_insert

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


def _scalar_json(value):
    return json.dumps(
        _encoded_native_sqlite_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def register_task8_functions(db):
    """Install the exact scalar codec; never begin, commit or select a schema."""
    from app.task_lifecycle_jsonb import register
    db.create_function("ceo_native_json", 1, _scalar_json, deterministic=True)
    register(db)


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
    insert_sql=None,
    insert_parameters=(),
    structural_sql=None,
    structural_parameters=(),
):
    """Compile native scalar INSERT parts; expression parameters follow payload binding."""
    expressions = {} if insert_sql is None else insert_sql
    physical_sql = {} if structural_sql is None else structural_sql
    payload, physical = _partition(kind, values, expressions, physical_sql)
    owned_columns, owned_values, owned_parameters = source_insert(
        kind, payload, expressions, insert_parameters,
    )
    columns = ["_kind", *physical, *physical_sql, *owned_columns]
    scalar_sql = [str(TASK_RECORD_TYPES.code(kind)), *("?" for _ in physical), *physical_sql.values()]
    scalar_sql.extend(owned_values)
    return Task8InsertParts(
        ",".join(map(_quote, columns)),
        ",".join(scalar_sql),
        (
            *physical.values(),
            *structural_parameters,
            *owned_parameters,
        ),
    )


def task8_set_parts(
    kind,
    values,
    *,
    update_sql=None,
    update_parameters=(),
    structural_sql=None,
    structural_parameters=(),
    payload_source="tasks.payload_json",
    source_alias="",
):
    """Compile native scalar SET; each RHS is evaluated once on the original row."""
    expressions = {} if update_sql is None else update_sql
    physical_sql = {} if structural_sql is None else structural_sql
    payload, physical = _partition(kind, values, expressions, physical_sql)
    owned = source_assignments(
        kind, payload, expressions,
        payload_source=payload_source, source_alias=source_alias,
    )
    assignments = []
    if owned.sql:
        assignments.append(owned.sql)
    assignments.extend(_quote(field) + "=?" for field in physical)
    assignments.extend(
        _quote(field) + "=" + expression for field, expression in physical_sql.items()
    )
    if not assignments:
        raise ValueError("existing Tasks SET requires fields")
    return Task8SetParts(
        ",".join(assignments),
        (*owned.parameters, *update_parameters, *physical.values(), *structural_parameters),
    )


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
