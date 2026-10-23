# Task2C creator Fix 1 report

Status: implemented and locally verified; awaiting the controller's independent SPEC and QUALITY re-review. Implementation commit: `66e9911f2`. Report commit: `a857f59a7`.

## Source-parity correction

The independent review identified that the source OA provisional-key adoption updates `business_object_key` on every existing reply input for that Task before associating the refined key. The initial native creator only replaced the relation and put the refined key on the appended input, changing the key returned for an already-claimed provisional input. The creator now performs the same all-input key update, relation replacement, and append in its existing immediate transaction. It leaves trigger text, message JSON, provenance, current/claimed input IDs, input versions, and execution generation untouched by refinement.

`get_reply_task_for_execution` now reads the actual native Task and its current/claimed inputs through `get_task_with_inputs`. It returns `None` for a missing Task, a non-reply Task, a generation mismatch, or a Task without a claimed input. Its returned `ReplyTask` is assembled by the shared `_reply_task_from_native(..., claimed=True)` mapping, so message/context fields come from the claimed input while Task status and current/claimed IDs and versions remain Task-owned. The normal creator projection still selects the current input.

## Regression and verification

The new test claims the provisional input before OA refinement, appends the refined trigger, then verifies the returned execution DTO shows the old claimed trigger/body and refined key. It also checks that every TaskInput and the TaskBusinessKey relation use the refined key, both source text and JSON hashes remain the same, provenance is unchanged, the claimed version/pointer remains old, the current pointer/version advances, and a wrong execution generation returns `None`.

RED on base `9df8b191a`: `python -m pytest -xq tests/test_store_task_entity_creator.py -k oa_refinement_updates_claimed` failed at `get_reply_task_for_execution` with `sqlite3.OperationalError: no such column: legacy_key`, because that execution reader still used the source projection. GREEN after Fix 1: `python -m pytest -q tests/test_store_task_entity_creator.py tests/test_task_entity_storage.py` — 24 passed. `python -m ruff check app/store.py tests/test_store_task_entity_creator.py`, `python -m compileall -q app/store.py tests/test_store_task_entity_creator.py`, and `git diff --check` passed.

Only this OA source-parity behavior and its required claimed-input reader were changed. No Run, general claim/queue lifecycle, retry, authorization/safety policy, provider/CLI, production database, push, or deployment work was done. Ten other named downstream source-backed methods and the broader lifecycle/constructor cutover remain as described in `task-2c-creator-report.md`.
