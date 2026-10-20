"""Current target setup for the existing frozen native-writer comparisons."""
from app.storage_payload import TASK_RECORD_TYPES
from app.task8_storage import register_task8_functions
from app.task_lifecycle_definitions import TASK20_TABLE_DDL, TASK20_INDEX_DDL, TASK20_TRIGGER_DDL
from app.task_lifecycle_storage import source_insert
from tests.test_task20_definitions import payload


def create_task20_schema(db):
    # DDL-only setup retains the existing tests' explicit allocator initialization.
    for sql in (TASK20_TABLE_DDL, *TASK20_INDEX_DDL, *TASK20_TRIGGER_DDL):
        db.execute(sql)
    register_task8_functions(db)


def insert(db, kind, *, physical_id=None, legacy_key="1", parent_id=None, values=None, verb="insert"):
    fields, expressions, parameters = source_insert(kind, payload(db, kind) if values is None else values, {})
    return db.execute(
        verb + " into tasks(id,_kind,legacy_key,parent_id," + ",".join(fields)
        + ") values(?,?,?,?," + ",".join(expressions) + ")",
        (physical_id, TASK_RECORD_TYPES.code(kind), legacy_key, parent_id, *parameters),
    )
