# Feedback resolved_at raw-value parity

Base: `e754dad8252840225df0cd5dd8a04c6bf4447140`.

## Scope and source facts

The accepted source Feedback Event projection and native `AutoReplyStore.upsert_feedback_event` both admit SQLite `resolved_at` values with their SQLite storage classes. The current source DDL does not enforce TEXT at runtime: binding `sqlite3.Binary(b"2026-10-23 10:00:00")` through the actual `actions` / `FEEDBACK_EVENT_SQL` path reads back as `(typeof='blob', value=b"2026-10-23 10:00:00")`. The previous native intake converted this to Python's bytes representation, stored it as TEXT, and used Python `.strip()` for the status decision. The source contract uses SQLite `trim(value) <> ''` classification.

Expected: preserve the resulting source value as a bound SQLite value. The frozen source Task12 upsert in `d930a2493` inserts `resolved_at` as `coalesce((select resolved_at ...), '')`, independently of its status expression `case when trim((select resolved_at ...)) <> '' then 'resolved' else 'pending' end`. Thus every non-NULL source value is copied to a new detail with its SQLite type intact, including empty BLOB and whitespace-only TEXT; status is classified separately by SQLite `trim()`. On repeat, a nonblank source value updates status and detail value; a blank source value leaves the existing status and detail resolution value untouched. Association, evidence, rounds, and source-event upsert behavior stay as before.

Actual before the repair: a nonblank BLOB was stored as its `b'...'` representation in TEXT; a tab-only TEXT value was treated as blank by Python although SQLite `trim()` classifies it as nonblank. After the first repair, status classification matched SQLite but the new-item projection incorrectly used the classification to replace empty BLOB and whitespace-only TEXT values with `''`. The source SQL copies those non-NULL values unchanged.

## Repair

`AutoReplyStore.upsert_feedback_event` now selects the raw source value and `trim(resolved_at) <> ''` result in SQLite. New detail rows use the source `coalesce(resolved_at, '')` result, independently of status classification. Existing detail rows copy the raw value only on the existing nonblank branch; the blank branch still changes only `tasks.updated_at`.

Added actual native Store/source DDL regressions for BLOB timestamp, empty BLOB, ordinary TEXT timestamp, blank TEXT, space-only TEXT, and tab-only TEXT for both new and existing feedback items. The test keeps an inline SQLite oracle transcribed from the source Task12 insert/upsert SQL at `d930a2493`; committed tests do not need Git history. The source oracle produced the corrected new-item facts, including `b'' -> ('pending', 'blob', b'')`, `'   ' -> ('pending', 'text', '   ')`, `'\t' -> ('resolved', 'text', '\t')`, and the BLOB timestamp -> `('resolved', 'blob', b'2026-10-23 10:00:00')`. Tests compare `typeof()` plus value for the source event and native detail. Extended the real Task entity conversion test to read the BLOB from the native source event, verify the resolved Task/detail, inverse to the source public projection, and reforward while preserving the source event and detail BLOB values.

Updated `docs/architecture.md` and `docs/runtime-mechanism.md` in the same change to describe SQLite value preservation and blank classification.

## Verification

RED on the first repair before correcting the new-item source projection:

```text
python -m pytest -q tests/test_store_task_entity_creator.py -k 'intake_preserves_source_resolved_value_and_sqlite_classification' --tb=short
2 failed, 10 passed, 31 deselected
```

The empty-BLOB case failed because the implementation emitted `('', TEXT)` while the source upsert SQL emitted `(b'', BLOB)`. The space-only TEXT case failed because the implementation emitted `''` while the source upsert SQL retained `'   '`; both remain `pending` under source `trim()` semantics.

GREEN after the repair and inverse/reforward assertion:

```text
python -m pytest -q tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py -k 'intake_preserves_source_resolved_value_and_sqlite_classification or feedback_intake_task_survives_source_inverse_and_reforward or feedback_event_repeat_projects_resolved_source_and_preserves_blank_source_facts' --tb=short
14 passed, 66 deselected
```

Scoped static checks:

```text
ruff check app/store.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py
All checks passed!
python -m py_compile app/store.py tests/test_store_task_entity_creator.py tests/test_task_entity_schema.py
git diff --check
```

No source fixture factory, provider, constructor, production checkout, native CLI, runtime, main branch, push, or deployment was changed or exercised. This verifies the bounded Store intake behavior and its Task converter inverse/reforward case; it does not claim whole-phase acceptance or runtime activation.
