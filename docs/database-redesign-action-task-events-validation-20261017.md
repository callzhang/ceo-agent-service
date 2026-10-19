# Action Task Events migration slice — 2026-10-17

This unselected migration slice adds the exact `business_task_events` source declaration to the existing follow-up/Todo action conversion. It preserves the eight source fields and the two current physical parent references (`task_id -> business_tasks.id`, `signal_id -> business_task_signals.id`) while those parent tables remain unmigrated. The generated Action projection uses the same public event ID and preserves source values, SQLite storage classes, the `created_at` default, event-type checks, object-valued JSON checks, nonblank-reason check, and `(task_id, created_at, id)` index.

The actual and fresh source declarations were captured separately in `docs/superpowers/specs/2026-10-17-action-task-events-source-extension.json`. Their declaration fingerprints are equal. The earlier nine-family snapshot remains unchanged and is still used as the historical source oracle for those nine families.

Validation performed in the isolated `database-redesign-provider-state` worktree:

- The new field-mapping test first failed because `business_task_events` was absent from the action declaration.
- The first integrated run exposed that the record-type order tuple was materialized before the new type was appended. Rebuilding that tuple after the extension fixed the issue.
- Focused storage, migration, inverse, and write tests: `152 passed`.
- Ruff passed on the changed Python files; `compileall` and `git diff --check` passed.
- Both actual and fresh candidate variants passed source binding, insert/update/delete parity, and caller-owned forward/inverse migration. A Task Event row with both parent references retained its field values and allocator watermark after the round trip.
- A disposable 524,398,592-byte SQLite backup of the active LaunchAgent database passed the ordered Email51-to-current-Actions plus ten-family forward path, whole-transaction rollback, inverse in reverse order, and reforward. Source and target counts matched for all ten families (`business_task_events`: 971; `work_updates`: 5,468; `follow_up_drafts`: 2,477; `follow_up_send_attempts`: 65; `task_todo_sync_outbox`: 242; `work_todo_dingtalk_links`: 875; the remaining four families: 0). `quick_check` passed; the original one foreign-key finding was unchanged; the copy's original schema declarations and ordinary-table row counts matched after inverse.

This is an intermediate source-family migration proof, not the complete 116-to-final-11-table proof. The Store constructor and Task Event callers still use the existing source table; the migration remains unselected. The temporary copy was removed after the run. No production database was modified, and no production migration, provider call, or external send was performed.
