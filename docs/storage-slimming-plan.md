# Approved storage simplification

1. Move training snapshot bodies/observations out of SQLite. Keep only latest complete data file; keep small metadata/evaluation records in DB. Pin data in memory for active training. Migrate newest old snapshot before removing old body tables.
2. Stop saving complete runtime trajectory in SQLite or new archive files. Read Codex/Claude native session and Friday operation history; retain service status/result/provider receipts. Migration may remove historical duplicate trajectories only after native coverage is measured and sole historical copies identified.
3. Store each exact scheduled configuration once, referenced by trigger rows. Preserve queued input and historical configuration.
4. Keep current and previous runnable model, plus latest candidate under evaluation. Remove other artifacts and unreferenced temp files only when no training is in flight.

Validation: test-first focused regressions, native readers and runtime receipt tests, real DB-copy migration and integrity/foreign-key checks, compare business object counts and pending references; deploy through app.deploy; verify PID, health, queues, Attention/History and disk sizes. No unrelated safety or audit policy changes.
