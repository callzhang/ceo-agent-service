# Task2D0 Reply queue, claim and input refresh report

## Scope and result

Implemented the existing Reply queue and input-refresh methods directly on the assembled native `tasks`, `task_inputs` and `task_business_keys` owners. The work began at `4d0e01878cffcdc2bbf80488c079aeb414f0a5bc`, after accepted Task creator and TaskInput storage work. The ordinary Store constructor remains on its predecessor schema; this change does not activate the target schema or change constructor, migration, provider, production, main, push, or deploy behavior.

`peek_reply_tasks`, `max_pending_reply_task_id`, and `get_reply_task` read native Task and current TaskInput owners. Queue selectors preserve pending/due predicates, channel filtering through current input, ascending ID order, `after_id`/`max_id`, zero limits, and the bulk claim preview/recheck boundary. `claim_reply_task` and `claim_reply_tasks` update status, attempts, lock/due timestamps, and claimed input ID/version in one existing `claim_connection` transaction. Native DTO reads use `_reply_task_from_native`; execution reads continue to select the separately held claimed input.

`list_reply_task_inputs` now reads the TaskInput FK by `task_id` and exposes the actual native dictionary key `task_id`; no consumer in `app/` reads this method's prior `reply_task_id` alias, so no caller required a key rename. The read still renders compact provenance through the existing compact-input renderer. `update_reply_task_trigger`, `_refresh_reply_input_in_transaction`, and `update_pending_reply_task_trigger_for_message` append a complete native TaskInput identity and advance current pointer/version atomically. They preserve the old pending/attempt guards, digest and revision key, provenance, explicit same-bytes direct refresh behavior, and message-trigger unchanged-bytes no-op. A refresh leaves the claimed input pointer/version untouched.

## Regression evidence

The focused regression was first run at the pinned predecessor Store implementation. It failed on the assembled native file fixture when the predecessor queue reader referenced absent source projection column `legacy_key`, demonstrating the missing native-owner behavior.

The final focused native Store run passed:

```text
pytest -q --basetemp=/tmp/task-run-entity-phase2-tests/task2d0.idbWvY tests/test_store_task_entity_creator.py
11 passed in 8.61s
```

The 3 added native regressions cover queue/channel/due/ID bounds and max ID, single and bulk claim, claim/current/execution input binding after a new current input arrives during processing, empty and lost-preview claims, direct same-byte refresh versus message no-op, pending/attempt guards, atomic rollback when pointer binding fails, compact provenance readback, and native `task_id` dictionary identity.

Additional checks passed:

```text
ruff check app/store.py tests/test_store_task_entity_creator.py
All checks passed!

python -m compileall -q app/store.py tests/test_store_task_entity_creator.py
exit 0

git diff --check
exit 0
```

All pytest basetemp directories were children of `/tmp/task-run-entity-phase2-tests`. Cleanup was attempted for the exact task-owned directories, but the environment rejected deletion because approval is unavailable; these temporary test directories remain under `/tmp/task-run-entity-phase2-tests`.

## Remaining gates

This unit does not convert the remaining predecessor-backed Reply APIs. In particular, stale/orphan recovery and terminal paths remain in `list_stale_processing_reply_tasks`, `recover_stale_processing_reply_tasks`, `recover_orphaned_processing_reply_tasks`, `complete_reply_task`, `fail_reply_task`, `terminalize_exhausted_pending_reply_tasks`, `requeue_reply_task`, retry/defer/authorization methods, and terminal/reconciliation helpers. Queue/summary views such as `count_reply_tasks`, `list_reply_tasks`, and message/business-object lookup are outside this unit. Adjacent input mutations `requeue_terminal_reply_task_for_revised_trigger` and `replace_pending_single_chat_reply_task_trigger`, plus `_compact_settled_reply_task_input`, remain outside this unit. No predecessor-schema Store test suite was run because its fixture lacks the `task_inputs` target owner; the new real assembled target fixture is the evidence for this scoped unit.

The constructor/schema activation, inverse and full current-data migration proof, remaining lifecycle/API conversion, broader public acceptance, and independent SPEC then QUALITY gate remain with the parent Phase2 integration. No production release occurred.
