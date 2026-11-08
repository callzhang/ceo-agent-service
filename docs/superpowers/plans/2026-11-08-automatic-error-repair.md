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
- [ ] Commit/push/integrate through current release path, deploy the exact pushed commit with the existing gate and read back service state.
- [ ] Feed a real failing diagnostic through the live error writer, observe trigger and continuation through actual PR, and inspect links in the browser. Preserve original business error status.
