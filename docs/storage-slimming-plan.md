# Approved storage simplification

1. Move training snapshot bodies/observations out of SQLite. Keep only latest complete data file; keep small metadata/evaluation records in DB. Pin the selected external file with small run-bound metadata for active training. Migrate newest old snapshot before removing old body tables.
2. Stop saving complete runtime trajectory in SQLite or new archive files. Read Codex/Claude native session and Friday operation history; retain service status/result/provider receipts. Migration may remove historical duplicate trajectories only after native coverage is measured and sole historical copies identified.
3. Store each exact scheduled configuration once, referenced by trigger rows. Preserve queued input and historical configuration.
4. Keep current and previous runnable model, plus latest candidate under evaluation. Remove other artifacts and unreferenced temp files only when no training is in flight.

Validation: test-first focused regressions, native readers and runtime receipt tests, real DB-copy migration and integrity/foreign-key checks, compare business object counts and pending references; deploy through app.deploy; verify PID, health, queues, Attention/History and disk sizes. No unrelated safety or audit policy changes.

## Verified migration copy (2026-10-09)

A SQLite backup of production was migrated from email schema 45 to 46 and vacuumed. Size changed from 3,482,066,944 to 1,848,934,400 bytes (46.9% reclaimed). quick_check returned ok. Counts were unchanged: reply_tasks 7,175; reply_attempts 14,578; agent_runs 21,219; external_action_results 250; sent_replies 2,268; business_tasks 466; scheduled_task_runs 145,890; email_training_snapshots 122. Scheduled runs reference 66 immutable configurations with zero missing references. Latest external training data is 16,858,401 bytes.

Historical native compaction removed zero runs: unavailable or unmatched native records are retained as sole historical evidence. Both baseline and migrated copy have the same pre-existing foreign-key finding in meeting_alignment_runs row 2298; migration introduced no new finding. Focused cron, training, native trajectory, receipt, audit storage, attention and Workbench regressions passed; spec and code quality reviews approved.
