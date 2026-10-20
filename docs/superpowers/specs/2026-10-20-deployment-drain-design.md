# Deployment drain (approved in chat, 2026-10-20)

The user explicitly chose draining currently executing work. On ordinary deploy
and restart, pause all new execution claims, continue admitted work and lease
renewal, wait for in-flight zero, then run the existing stop/backup/update/build/
verify/restart/health sequence. Incoming messages and queued/deferred items stay
persisted and resume after release. Do not require Queued to become zero, change
retry timing, cancel active turns, or repair stale task state as part of this.

Use two stable database-adjacent POSIX flock files. A short admission turnstile
orders entry: each execution claim takes it nonblocking, acquires the shared
drain lock, then releases the turnstile before its existing SQLite transaction.
The shared drain lock lasts through commit. Deployment closes the admission
turnstile first, then waits exclusively for already admitted transactions. This
prevents continuous claim traffic from starving the deployment boundary.
Deployment retains both locks through health verification. Existing execution,
renewal, result writes, intake and recovery do not take these locks. Descriptor
closure releases admission on success, timeout, exception, cancellation or
process exit; no persistent pause bit/TTL or schema migration is needed. Claims
return an empty poll while draining. Restart-only uses the same exclusion.

Cover the dispatcher adapters, email direct reply/classification/provider action claims,
Workbench and meeting delivery/memory claims. Extend in-flight reads for independently
claimed email/Workbench/delivery work before native model calls appear. Preserve
existing backup, upgrade serialization, rollback, health and quiet timeout rules.
A genuine hung job can still time out: restore admission and leave production
unchanged instead of forcing a deployment. The first installation must use the
existing normal release path because old processes do not yet read the lock.

Quiet means current execution. An unleased runtime whose parent generation is
explicitly superseded by the current Task is historical and does not block;
a valid independent native lease still blocks. Preserve historical rows.

Admission/drain flock acquisition is also bounded by30minutes. A stalled
already-admitted claim transaction therefore cannot hold admission indefinitely
before the ordinary in-flight quiet timer starts. Timeout closes both descriptors
and leaves the Service and pending work untouched; the existing in-flight wait
retains its own30-minute timeout after the claim boundary is acquired.
