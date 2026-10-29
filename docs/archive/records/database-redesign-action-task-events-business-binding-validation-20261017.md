# Task Event and follow-up Business binding validation — 2026-10-17

This is a prerequisite check for the unselected Task Event/follow-up action migration. It does not select a Store schema or alter the production database.

## Finding

The Task Event/follow-up candidate currently describes `task_id` and `business_task_id` as references to the physical `business_tasks` table. The paired release order migrates Business parents first. On that target, the existing retained-child migration rewrites those exact references to `business(task_public_id)`. Therefore the candidate's current FK targets are transitional only and cannot be used by the final paired action migration.

The source facts already provide the correct direct-reference mapping: `business_task_events.task_id`, `business_task_follow_ups.business_task_id`, `business_task_todo_sync_outbox.business_task_id`, and `business_task_dingtalk_links.business_task_id` all reference `business(task_public_id)` after Business migration. Event `signal_id` and follow-up `source_signal_id` still reference `business_task_signals(id)` because signal facts have not yet moved. In `business_legacy_links`, task/project/cluster/anchor/work-project/work-todo references are rewritten to the corresponding `business` public-ID columns, while `work_update_id` still references `work_updates(id)` and candidate/attention references still target their retained source tables.

## Verification

`tests/test_action_followup_sync_storage.py::test_task_action_binding_after_business_parent_migration` runs the existing Business source fixture for both `actual` and `fresh`, performs the Business migration in a caller-owned transaction, and reads the resulting SQLite foreign-key declarations. It asserts the task references target `business(task_public_id)`, the retired `business_tasks` target is absent from the affected child declarations, and signal/work-update references retain their current exact parents. Result: **2 tests passed** in the focused file.

The exact post-Business declarations for the five affected families are frozen in [`2026-10-17-action-task-events-post-business-source.json`](superpowers/specs/2026-10-17-action-task-events-post-business-source.json). The actual and fresh fixtures produce identical DDL, columns, indexes, and FK metadata for these children. The metadata-only comparison test verifies that equality after running the migration rather than assuming it. The focused file now passes **5 tests**.

The opt-in action schema generator now accepts `include_business_sync=True`. Its native SQLite declaration points `sync_task_id` and `sync_business_task_id` at `business(task_public_id)`, while `sync_signal_id` and `sync_source_signal_id` remain bound to `business_task_signals(id)`. The existing default still points to the pre-Business Email scaffold and remains byte-for-byte behaviorally unchanged. The full action storage, follow-up storage/migration/write regression set passes **176 tests**.

The follow-up/Task Event source capture now recognizes the exact pre-Business and post-Business declaration pairs. For the post-Business pair it selects the typed action FK target and retains the signal table target. The forward migration returns inverse metadata containing the declaration variant and binding; the inverse restores the corresponding exact source DDL.

`test_source_capture_accepts_exact_business_migrated_pair` exercises both actual and fresh variants through source capture, Business migration, typed action conversion, inverse restoration, and an outer transaction rollback. It checks the migrated Task Event row and typed task/signal FK declarations, writes a new Task Event through `insert_sync_action`, and confirms the current write returns to the source table on inverse. After rolling back the caller transaction, it recaptures and compares the full original source metadata. The combined action/follow-up storage, migration, and write regression set passes **176 tests**; the paired typed current-write/inverse test passes for both variants; Ruff, compileall, and `git diff --check` pass.

## Current actual database copy rehearsal

A fresh SQLite online backup of the active database at `/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3` was used as a disposable copy. The source readback was **496,676,864 bytes**, **121 ordinary tables**, Email schema **51**, and `quick_check=ok`. On the copy only, Email Actions migrated first, Business migrated second, and post-Business capture selected `after_business_actual` / `typed`. Follow-up, TODO-sync, legacy-link, and Task Event conversion then passed with source counts of 5,468 work updates, 2,477 drafts, 65 send attempts, 242 TODO outbox rows, 875 TODO links, and 971 Task Event rows; the other four included families were empty. Target action counts matched all captured source counts.

The copy committed that forward sequence, then committed inverse restoration in reverse order. `quick_check` remained `ok`; the original single foreign-key finding, all 48 allocator rows, and all 449 non-internal schema objects matched the pre-migration copy exactly. An unaffected-table digest comparison found only the approved retirement of `work_projects.memory_context_json`: 975 work-project rows and every non-cache value matched, while the field declined from 992,510 source bytes to 1,950 bytes after the schema inverse. That cache omission is the only data exception in this scoped rehearsal. The production source was opened read-only and never changed; the temporary copy was removed automatically. This exercises only Email + Business + follow-up/Task Event on a copy, not the entire 116-to-11 release or its production selector.

## Remaining work

The release coordinator remains owned by the existing paired-release scope, so this change does not edit it. The full 116-to-11 proof, integrated coordinator forward/inverse sequence, selector activation, publication, and deployment remain open. This copy rehearsal is limited to Email + Business + follow-up/Task Event; it is not full redesign or production release acceptance.
