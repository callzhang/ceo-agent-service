# B3A Task Identity Fix 1 Report

## Change

The inverse migration now writes the current new Email Task's canonical `target_id` into its restored `legacy_key`. The prior code read a stale `canonical_id` value left from an earlier loop, so separate Email owners could receive a different owner's key.

The regression now creates two native Email Tasks with distinct stable message identities. One uses canonical ID `1`, forcing its source physical ID above the captured Task sequence watermark; the other exercises the ordinary newly allocated canonical ID path. It verifies for both owners that the restored legacy key exactly equals that owner's canonical ID, the public predecessor task key is the SHA-256 derived from that owner's stable identity, and the predecessor runtime validator accepts that public key. It also verifies the low-ID owner's source physical allocation is explicitly present in inverse identity handoff.

## Verification

- `pytest tests/test_task_entity_schema.py -k new_native_email_task_inverse --basetemp=/tmp/task-run-entity-b3-fix1-child.J3OWYr` — 1 passed, 24 deselected.
- `pytest tests/test_task_entity_schema.py tests/test_run_entity_schema.py --basetemp=/tmp/task-run-entity-b3-fix1-child.oYSDXz` — 42 passed.
- `ruff check app/task_entity_schema.py tests/test_task_entity_schema.py` — passed.
- `python -m py_compile app/task_entity_schema.py tests/test_task_entity_schema.py` — passed.
- `git diff --check` — passed.

## Scope and status

This fixes the P1 in the independent B3A review. The broader Run allocation, Task-first Run inverse context, complete new-fact vector, and full Phase 2 acceptance remain pending; this report makes no full-B3 claim. No production database, provider, CLI, push, or deploy action was performed.
