# Task2C creator Fix 2 report

Status: implemented and locally verified; awaiting independent SPEC and QUALITY re-review. Implementation commit: `a7327abf6`. Report commit: `f84dfb586`.

## Corrected DTO behavior

`_reply_task_from_native` no longer eagerly stringifies business fields or coerces `attempts` through `int(value or 0)`. It passes stored values into the existing `ReplyTask` Pydantic model, matching `_reply_task_from_row`: UTF-8 BLOB `trigger_text` and `trigger_message_json` decode to readable strings, while fractional REAL `attempts=1.25` raises the same `ValidationError` on both paths. The fix preserves source values and validation behavior; it does not add a BLOB-specific conversion, REAL-specific conditional, fallback, or default.

The existing explicit `bool()` conversions, numeric input-version conversions, explicit provenance string conversion, native ID normalization, and compacted-input hydration remain. Current and claimed projections still select their corresponding TaskInput; OA business-key refinement remains unchanged.

## Regression evidence

The tests use `_source()`'s actual Task20 source schema and projections, write BLOB values through `task8_set_parts`, read source DTOs through `_reply_task_from_row`, migrate using `capture_task_entities` and `migrate_task_entities`, and compare actual native current/claimed DTOs. They assert raw BLOB bytes and SQLite storage types before and after migration. Canonical identity is asserted separately: source public input IDs are 5/6, native physical IDs are 205/206.

RED on base `6a408c856`: both focused tests failed. Current and claimed native DTOs returned `b'typed source text'` instead of `typed source text`; the fractional-attempt test showed the source reader raising Pydantic `ValidationError` while the native reader accepted the value after truncation.

GREEN after Fix 2: `python -m pytest -q tests/test_task_entity_schema.py -k native_reply_dto_preserves_source_pydantic` — 2 passed. Combined `tests/test_store_task_entity_creator.py`, `tests/test_task_entity_storage.py`, and `tests/test_task_entity_schema.py` — 42 passed. `python -m ruff check app/store.py tests/test_task_entity_schema.py`, `python -m compileall -q app/store.py tests/test_task_entity_schema.py`, and `git diff --check` passed.

## Scope

Only the native ReplyTask DTO's business-value mapping, paired actual-source migration regressions, and the creator validation documentation changed. No source schema/writer, migration implementation, Run, claim lifecycle, constructor, provider/native CLI, production database, push, or deployment work was performed. The ten downstream source-backed methods remain as listed in `task-2c-creator-report.md`.
