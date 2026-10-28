# Approved storage simplification

1. Move training snapshot bodies/observations out of SQLite. Keep only the latest complete baseline per distinct dataset type; keep small metadata/evaluation records in DB. Pin the selected external file with small run-bound metadata for active training. Migrate the newest valid snapshot of each dataset type before removing old body tables.
2. Use Codex/Claude native session and Friday operation history as the sole trajectory source. Do not persist full or compact events, agent result bodies, runtime result envelopes, audit tool bodies or derived search bodies in SQLite or archive files. Persist service-origin task state and minimal native run references. Remove historical copies even where native records have disappeared; those details become explicitly unavailable.
3. Store each exact scheduled configuration once, referenced by trigger rows. Preserve queued input and historical configuration.
4. Keep current and previous runnable model, plus latest candidate under evaluation. Remove other artifacts and unreferenced temp files only when no training is in flight.

Validation: test-first focused regressions, native readers and runtime receipt tests, real DB-copy migration and integrity/foreign-key checks, compare business object counts and pending references; deploy through app.deploy; verify PID, health, queues, Attention/History and disk sizes. No unrelated safety or audit policy changes.

> 方案已实施并部署（2026-10-10）。各阶段验收记录见 [archive/storage-slimming-plan-acceptance-log.md](archive/storage-slimming-plan-acceptance-log.md)。
