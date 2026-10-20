"""Current Runs11 factories; historical Runs8 factories remain native oracles."""
import sqlite3

from app.configs_run_schema import _objects
from app.configs_schema_release import configs_content_schema_is_current
from app.run_storage import RETIRED_RUN_TABLES
from app.runs11_definitions import RUN11_NEW_KINDS
from app.runs11_schema_release import _target_objects, _fts_objects

RETIRED_RUN11_NAMES = tuple(dict.fromkeys((
    *RETIRED_RUN_TABLES, 'scheduled_task_runs', 'workbench_events',
    *RUN11_NEW_KINDS, *(row[1] for row in _fts_objects()),
)))


def current_run11_objects():
    """Return exact finite current declarations, independent of source factories."""
    return _target_objects().copy()


def create_current_run11_schema(db: sqlite3.Connection):
    """Keep native explicit missing-object repair and caller transaction ownership.

    Creating declarations never resets current source or physical counters.
    Source eligibility is checked by the current constructor before this factory.
    """
    for sql in current_run11_objects().values():
        for prefix in ('CREATE TABLE ', 'CREATE UNIQUE INDEX ', 'CREATE INDEX ', 'CREATE TRIGGER '):
            if sql.startswith(prefix):
                db.execute(prefix + 'IF NOT EXISTS ' + sql[len(prefix):])
                break
        else:
            raise ValueError('unsupported current Runs11 declaration category')


def current_run11_schema_is_current(db: sqlite3.Connection, *, allow_missing_objects=False):
    """Match one current layout, never accept the native Runs8 source as current."""
    if any(db.execute('select 1 from main.sqlite_schema where name=? collate nocase',
                      (name,)).fetchone() for name in RETIRED_RUN11_NAMES):
        return False
    expected = current_run11_objects()
    actual = _objects(db)
    if actual.get('runs') != expected['runs'] or not set(actual) <= set(expected):
        return False
    if allow_missing_objects:
        for name in set(expected) - set(actual):
            if name == 'runs':
                return False
            owner = db.execute(
                'select type,tbl_name from main.sqlite_schema where name=? collate nocase',
                (name,),
            ).fetchone()
            if owner is not None:
                return False
    return all(actual.get(name, sql) == sql if allow_missing_objects
               else actual.get(name) == sql for name, sql in expected.items())


def current_configs_schema_is_current(
    db: sqlite3.Connection,
    *,
    action_variant,
    include_followup_sync: bool = False,
    include_business_sync: bool = False,
    include_task_delivery: bool = False,
    include_errors: bool = False,
    include_feedback: bool = False,
    include_business_actions: bool = False,
    include_email_history: bool = False,
    canonical_email: bool = False,
    inline_legacy_owner: bool = False,
    physical_provenance: bool = False,
    compact_payload: bool = False,
    selection_references: bool = False,
    result_references: bool = False, execution_owner: bool = False,
):
    """Use unchanged config/action facts with the one current Runs11 binding."""
    return (configs_content_schema_is_current(
                db,
                action_variant=action_variant,
                include_followup_sync=include_followup_sync,
                include_business_sync=include_business_sync,
                include_task_delivery=include_task_delivery,
                include_errors=include_errors,
                include_feedback=include_feedback,
                include_business_actions=include_business_actions,
                include_email_history=include_email_history,
                canonical_email=canonical_email,
                inline_legacy_owner=inline_legacy_owner,
                physical_provenance=physical_provenance, compact_payload=compact_payload,
                selection_references=selection_references, result_references=result_references, execution_owner=execution_owner,
            )
            and current_run11_schema_is_current(db))
