# Task3C2B0 Task pointer prerequisite

## Finding and correction

The actual Task forward migration first creates the canonical Task ID through its typed inverse-identity handoff, then converts the source `reply_tasks.current_input_id` and `claimed_input_id` public values to target input IDs. The late rebind loop used the source physical Task ID in its `UPDATE tasks ... WHERE id=?`. When Reply Task 201 was remapped to native ID 1201, it therefore updated no row: the canonical Task kept old public input IDs 5 and 6 while its input owners were 205 and 206. `restore_task_entities` then rejected the invalid source projection.

The rebind now targets `target_ids[("reply_tasks", source_physical_id)]`, the same typed owner selected for the rest of that Task conversion. Input resolution and version fields are unchanged. No schema, metadata, fallback, or runtime policy was added.

## Regression evidence

Added `test_remapped_reply_task_rebinds_current_and_claimed_source_input_ids` in `tests/test_task_entity_schema.py`. It uses the existing actual `_source()` database, captures it through `capture_task_entities`, remaps source Reply Task 201 to native 1201, and runs actual forward and inverse converters. It checks current/claimed native input IDs 205/206 and input version values 1/0 before inverse, then checks source current/claimed pointers and returned inverse identity after inverse.

RED, before the pointer rebind correction:

```text
pytest -q --basetemp=<unique /tmp/task3c2b0-red.*> tests/test_task_entity_schema.py::test_remapped_reply_task_rebinds_current_and_claimed_source_input_ids
1 failed: native Task 1201 retained current/claimed IDs 5/6 instead of native input IDs 205/206
```

GREEN, after correction:

```text
pytest -q --basetemp=<unique /tmp/task3c2b0-green.*> tests/test_task_entity_schema.py::test_remapped_reply_task_rebinds_current_and_claimed_source_input_ids
1 passed
ruff check app/task_entity_schema.py tests/test_task_entity_schema.py
All checks passed!
python -m py_compile app/task_entity_schema.py tests/test_task_entity_schema.py
(green)
git diff --check
(green)
```

This verifies the confirmed remapped-owner failure boundary in the actual Task schema fixture. It does not claim the broader C2B inverse or any cold-read/reforward lifecycle acceptance. Runtime readers and constructor remain unactivated.
