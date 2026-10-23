# Task2C Reply creator implementation report

Status: implemented and locally verified on the unpublished Task/Run Phase2 worktree. Commit: pending.

## Converted closure

The existing `AutoReplyStore` class and its transaction boundaries remain. The creator path now calls `task_entity_storage.create_task`, `append_task_input`, `bind_current_input`, `get_task_with_inputs`, and `associate_business_key` against the assembled target. Converted methods are `_append_reply_input_snapshot`, `_ensure_business_reply_task`, `enqueue_reply_task`, `ensure_reply_task`, `ensure_reply_tasks`, `ensure_authorized_email_reply_tasks`, and `_ensure_reply_tasks_in_transaction`. `_reply_task_from_native` projects the canonical Task plus its bound current input for creator returns; it does not decode old and new schemas together. Shared `REPLY_TASK_*` source projections remain for caller groups that have not moved.

## Preserved behavior and evidence

- Triple `(channel, conversation_id, trigger_message_id)` dedupe returns the original owner and its current-input projection. `enqueue_reply_task` reports whether the immutable input was newly inserted.
- Input identity remains the full four-part key including `input_revision_key`. Same-owner reuse compares all existing source fields, and owner or byte conflicts preserve `ReplyTaskIdentityConflict`.
- A business key reuses its Task. New messages append immutable inputs and advance the current binding/version; the claimed input and a nonterminal execution generation stay fixed. A new input on a `done` or `failed` Task resets its queue state, retry count, lock, error, availability, and generation.
- OA provisional-key refinement moves the key relation to the refined key without mutating the immutable prior input. Multiple keys remain rows in `task_business_keys`.
- The Task parameters contain only `force_new_decision`, `manual_rerun_attempt_id`, `manual_rerun_revision_key`, and `recovery_code`. Reply context fields come from the bound current TaskInput; numeric input versions remain numeric.
- Authorized email batching reads current classification/message ownership from the assembled Email tables and rejects a stale plan before inserting reply Tasks.

The new Store tests create the actual assembled target DDL with foreign keys enabled, then use genuine `AutoReplyStore._connect` and `_immediate_write_transaction`. RED on base `456549add`: `python -m pytest -xq tests/test_store_task_entity_creator.py` failed in the original creator with `sqlite3.OperationalError: no such column: legacy_key` while querying `REPLY_TASK_INPUT_SQL`. GREEN after conversion: `python -m pytest -q tests/test_store_task_entity_creator.py tests/test_task_entity_storage.py` — 23 passed. `python -m ruff check app/store.py tests/test_store_task_entity_creator.py`, `python -m compileall -q app/store.py tests/test_store_task_entity_creator.py`, and `git diff --check` passed.

## Remaining caller boundary

Eleven named downstream methods remain outside this creator unit: scheduled reply dispatch; manual rerun; input refresh; trigger update; pending-trigger update; revised-trigger requeue; single-chat coalescing; current task read; claimed execution read; single claim; and batch claim. The broader reply lifecycle, attempt, Run, delivery, History, reconciliation, and constructor/read projections also remain on their existing source representations. These call sites must move before constructor or manifest activation and the final atomic cutover. No Run plumbing, source-to-target database migration, provider/native CLI, production database, push, or deploy work was performed.
