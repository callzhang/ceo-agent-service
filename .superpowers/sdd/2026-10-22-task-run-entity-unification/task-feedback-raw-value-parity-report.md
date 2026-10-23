# Feedback resolved_at raw-value parity

Base: `e754dad8252840225df0cd5dd8a04c6bf4447140`.

## Scope and source facts

The accepted source Feedback Event projection and native `AutoReplyStore.upsert_feedback_event` both admit SQLite `resolved_at` values with their SQLite storage classes. The current source DDL does not enforce TEXT at runtime: binding `sqlite3.Binary(b"2026-10-23 10:00:00")` through the actual `actions` / `FEEDBACK_EVENT_SQL` path reads back as `(typeof='blob', value=b"2026-10-23 10:00:00")`. The previous native intake converted this to Python's bytes representation, stored it as TEXT, and used Python `.strip()` for the status decision. The source contract uses SQLite `trim(value) <> ''` classification.

Expected: preserve the resulting source value as a bound SQLite value. For a new item, source SQL's conditional projection copies a nonblank timestamp and defaults a blank timestamp to `''`; status is `resolved` only for a source value classified nonblank by SQLite. On repeat, a nonblank source value updates status and detail value; a blank source value leaves the existing status and detail resolution value untouched. Association, evidence, rounds, and source-event upsert behavior stay as before.

Actual before the repair: a nonblank BLOB was stored as its `b'...'` representation in TEXT; a tab-only TEXT value was treated as blank by Python although SQLite `trim()` classifies it as nonblank. The new-item empty BLOB path also needed the source CASE/default result (`''`) rather than a direct BLOB copy.

## Repair

`AutoReplyStore.upsert_feedback_event` now selects the raw source value and `trim(resolved_at) <> ''` result in SQLite. New detail rows copy the raw value only when the source classification is nonblank and otherwise use the source default `''`. Existing detail rows copy the raw value only on the existing nonblank branch; the blank branch still changes only `tasks.updated_at`.

Added actual native Store/source DDL regressions for BLOB timestamp, empty BLOB, ordinary TEXT timestamp, blank TEXT, and tab-only TEXT for both new and existing feedback items. The tests compare `typeof()` plus value for the source event and native detail, and derive status classification through SQLite's source expression. Extended the real Task entity conversion test to read the BLOB from the native source event, verify the resolved Task/detail, inverse to the source public projection, and reforward while preserving the source event and detail BLOB values.

Updated `docs/architecture.md` and `docs/runtime-mechanism.md` in the same change to describe SQLite value preservation and blank classification.

## Verification

RED on the unmodified source-value projection:

```text
python -m pytest -q tests/test_store_task_entity_creator.py -k 'intake_preserves_source_resolved_value_and_sqlite_classification' --tb=short
4 failed, 6 passed, 31 deselected
```

The failures showed BLOB-to-TEXT representation conversion and the TAB classification difference. The empty-BLOB new-item expectation also confirmed source CASE/default behavior.

GREEN after the repair and inverse/reforward assertion:

```text
python -m pytest -q tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py -k 'intake_preserves_source_resolved_value_and_sqlite_classification or feedback_intake_task_survives_source_inverse_and_reforward or feedback_event_repeat_projects_resolved_source_and_preserves_blank_source_facts' --tb=short
12 passed, 66 deselected in 16.07s
```

Scoped static checks:

```text
ruff check app/store.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py
All checks passed!
python -m py_compile app/store.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py
git diff --check
```

No source fixture factory, provider, constructor, production checkout, native CLI, runtime, main branch, push, or deployment was changed or exercised. This verifies the bounded Store intake behavior and its Task converter inverse/reforward case; it does not claim whole-phase acceptance or runtime activation.
