# Automatic error repair

Derek approved implementing the previous pilot as a standing service feature. New failed Attempt notifications and recorded service errors enqueue a repair using existing service_state coordination facts. Existing historical errors are not mass-replayed. A single coordinator in the existing Workbench scheduler creates an atomic source-to-conversation association and idempotent turns. All turns use the same native ServiceWorkbenchRuntime and existing routing, leases and recovery.

The Agent reads the exact persisted source and native trajectory, works in a development worktree, writes a failing regression, fixes code, runs focused tests, and pushes a PR. It stops at PR, never merges, deploys, or replays business actions. Each repair uses a stable branch. On terminal turns the coordinator reads that branch's actual GitHub PR; missing delivery continues the same conversation up to MAX_AUTOMATIC_ATTEMPTS. An explicit Workbench stop stays stopped. No-code and blocked diagnoses may terminate explicitly. A PR receipt means PR created, never deployed or original business outcome fixed.

Attempt and generic Service error details read the persisted repair status, Workbench URL, PR URL and unresolved reason. Pending work is durable across restart; transactionally enqueued events cannot be lost between error recording and task creation. No schema migration, new task kind, runtime-route branch, audit change or new approval policy.

Acceptance: focused atomic enqueue/idempotency/restart/continuation/PR readback tests, API and UI coverage, standard deployment gate, live process/queues readback, then feed a real currently failing diagnostic into record_error and observe automatic Agent work through remote PR and browser links.
