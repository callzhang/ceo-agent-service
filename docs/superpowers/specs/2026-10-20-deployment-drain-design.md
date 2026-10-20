# Deployment drain (approved in chat, 2026-10-20)

The user explicitly chose draining currently executing work. On ordinary deploy
and restart, pause all new execution claims, continue admitted work and lease
renewal, wait for in-flight zero, then run the existing stop/backup/update/build/
verify/restart/health sequence. Incoming messages and queued/deferred items stay
persisted and resume after release. Do not require Queued to become zero, change
retry timing, cancel active turns, or repair stale task state as part of this.

Use a stable database-adjacent POSIX file lock. Execution claims acquire a shared
lock only for their existing atomic SQLite transaction. The deployment acquires
an exclusive lock before waiting and retains it through health verification.
This orders claim commit before the drain boundary and prevents new claims after
that boundary. Existing execution, renewal, result writes, intake and recovery do
not take this claim lock. File descriptor close releases admission on success,
timeout, exception, cancellation or process exit; no persistent pause bit/TTL or
schema migration is needed. Claims use nonblocking shared acquisition and return
an empty poll while draining. Restart-only uses the same exclusion.

Cover the dispatcher adapters, email classification/provider action claims,
Workbench and meeting delivery/memory claims. Extend in-flight reads for independently
claimed email/Workbench/delivery work before native model calls appear. Preserve
existing backup, upgrade serialization, rollback, health and quiet timeout rules.
A genuine hung job can still time out: restore admission and leave production
unchanged instead of forcing a deployment. The first installation must use the
existing normal release path because old processes do not yet read the lock.
