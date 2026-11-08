# Automatic error repair implementation plan

> Execute inline using executing-plans; user has approved the trigger/continuation/link flow.

**Goal:** Automatically take new errors through the existing Agent conversation to a verified remote PR and expose both links.
**Architecture:** Transactional service_state outbox, one Workbench coordinator, native runtime, stable branch and idempotent turn request IDs.
**Tech stack:** Python/SQLite, existing Workbench, gh CLI, React/TypeScript.

- [x] Add failing tests for atomic error enqueue, source idempotency, same-conversation continuation, bounded exhaustion, stop, restart and real PR receipt lookup.
- [x] Implement automatic_repair_store.py coordination primitives and automatic_repair.py coordinator and Agent procedure. Wire record_error and failed Attempt notifications.
- [x] Add the coordinator callback to the existing scheduler, preserving its exception boundary, execution and lifecycle.
- [x] Project repair metadata into both detail APIs. Render a shared repair card; verify missing/running/PR/failed states and link targets.
- [x] Run focused backend/frontend tests, Ruff and frontend build, then update architecture/runtime/procedure docs and gate manifest.
- [x] Commit/push/integrate through current release path, deploy the exact pushed commit with the existing gate and read back service state.
- [x] Feed a real failing diagnostic through the live error writer, observe trigger and continuation through actual PR, and inspect links in the browser. Preserve original business error status.

Gate budget evidence: first fixed-budget run reached the final new test at 600 seconds without an earlier assertion failure. All fifteen regressions remain; fast lifecycle/atomicity/remote-delivery contracts live in test_automatic_repair_gate.py, while API/notification and broader recovery integration remain in test_automatic_repair.py and are run separately. Every pre-existing manifest entry remains unchanged; no limit or gate implementation changes.

## Verified live acceptance — 2026-11-08

- Runtime/frontend code installed in `ff340adda` (contains feature commit `19bf0593e`); the later `a2c19fd8f`/`aa3736356` changes only reorganize new tests. Official gate receipt: 455 files, 8,865 passed / 2 skipped / 1 deselected, 569.97 seconds. PID/health, queues, Attention and History read back successfully.
- Controlled input was a real Ruff failure in current source, not a fabricated result: two F401 imports in `app/store.py`. Recording it through normal `record_error` created Service error 16218, and the service itself created Workbench 404263 / turn 418644. No manual task creation or continuation was used for that first delivery.
- The native Agent independently reproduced the failure, changed consumers, ran tests, pushed its branch and opened [PR 343](https://github.com/callzhang/ceo-agent-service/pull/343). The coordinator independently read the remote PR and persisted its links on the original error.
- Final review found two consumers missed by a truncated scan. Acceptance supplied one explicit feedback turn (418646) in that same Workbench conversation; this follow-up was manual acceptance feedback, not an automatically triggered review loop. The Agent reproduced two collection errors, completed the consumer scan, fixed both imports and pushed head `dcbe6ed8b` to the same PR. The coordinator read back the new head correctly.
- Repair verification: repository/tool Ruff checks pass; 1,100 passed across the Agent's focused runs (182 + 832 + 86), plus one confirmed pre-existing `test_message_delivery_history_is_single_projection_with_run_observers` failure (`action_parameters_json`) reproduced on clean `aa3736356`. Root independently confirmed the new head's lint and collection of all 86 formerly blocked cases. Full PR CI was still pending at acceptance, so PR creation does not assert merge readiness.
- Browser acceptance: original Attempt 17022 shows its verified previous Workbench/PR342 links; Service error 16218 shows Workbench404263/PR343 links; clicking the PR link opens the actual public PR. The original error remains failed/unresolved. No repair PR merge, service deployment of that repair, or business replay occurred.
- Incomplete/failed-turn continuation, checkpoint interruption, explicit stop, readback recovery and nonblocking dispatch are covered by the fifteen automated regressions. The live first turn delivered a PR and therefore did not exercise automatic missing-PR continuation.
