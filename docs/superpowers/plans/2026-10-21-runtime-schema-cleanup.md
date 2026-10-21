# Runtime Current Schema Implementation Plan

> **For agentic workers:** Use subagent-driven-development for bounded implementation and sequential spec/quality review. Main owns release and production verification.

**Goal:** Remove runtime historical declaration selection and automatic schema repair, preserving only explicit migration and inverse tools.

**Architecture:** Store 19.12 / Email 53 uses a single Actions declaration combining the four existing actual-source Attempt fields with the existing fresh-source physical Memory parent FK. No new business fields or record types. Blank databases install this declaration; existing databases must exactly match it. Release tools explicitly convert 19.11 and invert to its captured source declaration.

**Tech Stack:** Python 3.12, SQLite JSONB, existing paired schema deploy coordinator.

## Confirmed ownership

Production 19.11 retains conversation_single_chat, trigger_create_time, universal_execution_id, universal_execution_scope_id; nonneutral values are 1/1/735/735 respectively. Do not remove these facts. The fresh declaration owns a physical memory_attempt_ref FK, while actual production stores the public attempt_id in its payload. All 476 production references currently resolve. Explicit conversion preserves IDs, ROWIDs, typed values, provenance, receipts, allocator waters and unaffected tables. Existing unrelated FK findings remain original findings. Task-delivery defaults must be chosen once and documented; preserve existing row values. Workbench attachment reconciliation is business file maintenance, outside this schema cleanup.

## Task 1: Canonical current Actions

Files: app/action_storage.py, new app/current_action_schema.py, tests/test_current_action_schema.py.

- [x] Add regression tests for one declaration containing all four real Attempt fields and physical Memory FK; verify typed values, insert/update and parent references.
- [x] Run focused tests to establish failure on baseline.
- [x] Keep historical factories immutable for explicit release/inverse; build one current declaration and payload layout. Runtime writers/projections use that definition without inspecting physical variants. Do not weaken the existing fresh FK or lose actual facts.
- [x] Run focused current Action and reply write tests.

## Task 2: Explicit paired release

Files: new app/current_action_schema_release.py, app/store_schema_release.py, tests/test_current_action_schema_release.py.

- [x] Test exact source variants, failed conversion transaction rollback, current-data inverse and allocator preservation.
- [x] Implement explicit 19.11-to-19.12 rebuild. Public Memory parent IDs map to existing physical Actions IDs. Inverse restores the captured source declaration and marker/clock. Refuse unrepresentable inverse values; never silently erase newly added facts.
- [x] Integrate release coordinator; no runtime migration.
- [x] Forward/inverse/reforward on synthetic data with external effects replaced.

## Task 3: Strict runtime bootstrap

Files: app/store.py, app/email_store.py, app/current_database_schema.py, app/workbench_storage.py, current task/run/schema validators; focused constructor tests.

- [x] Replace repair-expecting tests with rejection/no-mutation checks. Cover missing and malformed indexes, triggers, partial current markers, old declarations and both constructor orders.
- [x] Remove startup repair branches and OA metadata backfill call. Remove uncalled private migration helpers and obsolete tests; retain genuinely called explicit release/inverse helpers behind release modules.
- [x] Validate one exact current manifest. Blank construction only; preserve Email fresh ledger/seed boundary. Current Workbench construction uses final declaration, without historical source rewrite.
- [x] Run focused Store/Email/Workbench constructor, writes and explicit historical release tests.

## Task 4: Copy proof and release

Files: docs/architecture.md, docs/runtime-mechanism.md, concise validation evidence.

- [x] SQLite backup production into task temporary directory. Forward; compare all typed semantic facts, references, allocator waters and unaffected rows. Inverse current data; old code cold read; reforward. Preserve baseline FK findings.
- [x] Exercise affected task/review/business result/receipt writes with fake outbound providers. Never trigger real test sending.
- [x] Update behavior documents, perform spec review then quality review, normal merge upstream increments and rerun affected checks.
- [x] Commit/push; deploy via app.deploy after copy/inverse checks pass. Read live PID/version/schema, health, queues, Attention, History and verified newest backup. Remove own claim and temporary artifacts.

Acceptance: one current declaration for blank and migrated databases; no current-schema startup repair; exact-current opens do not rewrite facts; malformed current opens reject without mutation; explicit forward/inverse is transactional and proven on production copy before deployment.

Current deployment readers now use current_publication_sources: exact current19.12 or immediate explicit source19.11 only. Runtime Email availability, quality shape checks and Memory queue projection use one current schema. Historical publication tools remain separate.
