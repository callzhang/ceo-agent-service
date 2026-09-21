# Email Dual Lookback Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically process up to a configurable model-history window while limiting Agent classification to its own shorter window and routing uncertain model predictions to the existing pending-feedback workflow.

**Architecture:** The existing scheduled discovery command runs the Agent scan first and a new model-only history scan second. Stable classification identity deduplicates overlapping windows; the model-only route persists accepted results through the existing action pipeline and rejected results as actionless `pending_feedback`, without invoking the Agent producer.

**Tech Stack:** Python 3, SQLite migrations, Pydantic, FastAPI, React/TypeScript, pytest, Vitest.

---

### Task 1: Replace the single account window with explicit Agent and model windows

**Files:**
- Modify: `app/email_connector_config.py`
- Modify: `app/email_store.py`
- Modify: `app/web_api/email.py`
- Modify: `tests/test_email_connector_config.py`
- Modify: `tests/test_email_store.py`
- Modify: `tests/test_email_web_api.py`

- [ ] Write failing request, store, and migration tests asserting `agent_lookback_days=30`, `model_lookback_days=365`, Agent maximum 365, model maximum 3650, and v42 preservation of the old Agent value.
- [ ] Run the focused tests and confirm they fail because the new fields and schema v43 do not exist.
- [ ] Change the strict Pydantic payload, account serialization, DDL contracts, fresh schema, create/update/restore SQL, and v42-to-v43 migration. Rename the old column instead of dual-reading or dual-writing it.
- [ ] Return only the two new window fields from the account API and remove the old response field.
- [ ] Run the focused tests and confirm the new contract and migration pass.

### Task 2: Preserve rejected model evidence for model-only review

**Files:**
- Modify: `app/email_classifier_runtime.py`
- Modify: `tests/test_email_classifier_runtime.py`

- [ ] Write failing tests showing an accepted result remains in `value`, while low-confidence, `others`, and unpromoted results keep `value=None` for realtime fallback and expose the raw prediction in `review_value`.
- [ ] Run the focused tests and confirm `OnlineClassificationResult` has no review evidence.
- [ ] Add the optional immutable `review_value` field and populate it only for non-technical model dispositions. Keep technical exceptions and realtime `SequentialOnlineClassifier` behavior unchanged.
- [ ] Run the focused runtime tests and confirm both old fallback behavior and new review evidence pass.

### Task 3: Persist model uncertainty into the existing pending-feedback list

**Files:**
- Modify: `app/email_worker.py`
- Modify: `tests/test_email_classifier_scan_model.py`

- [ ] Write a failing test for a model prediction saved with source `model`, status `pending_feedback`, full probability evidence, and no action plan or Agent/action task.
- [ ] Run it and confirm no persistence function exists for this case.
- [ ] Add `persist_model_pending_feedback`, using the same stable classification ID and message projection as accepted model persistence, but never constructing or producing an action plan.
- [ ] Test idempotent readback and the existing manual-confirmation training path.

### Task 4: Add the bounded model-only historical scan

**Files:**
- Modify: `app/email_classifier_scan.py`
- Modify: `tests/test_email_classifier_scan_model.py`

- [ ] Write failing tests covering date window, read and unread mail, stable-record exclusion, accepted persistence, rejected/others/unpromoted pending persistence, no Agent producer call, and technical failure retry behavior.
- [ ] Run the focused tests and verify failure before production changes.
- [ ] Implement `scan_model_classification_batch`: search all mail from the model window with a fixed limit, use canonical model input, call one runtime snapshot, persist accepted or review results through callbacks, and advance only after durable persistence.
- [ ] Run the focused tests and confirm model uncertainty never reaches the Agent producer.

### Task 5: Wire both passes into the scheduled command

**Files:**
- Modify: `app/email_scheduled_command.py`
- Modify: `tests/test_email_scheduled_command.py`

- [ ] Write failing command tests asserting Agent and model lookback values are passed independently, Agent runs first, model runs only in `model_primary`, the runtime closes, and summary counts include both paths.
- [ ] Run the tests and confirm the command currently loads no model and performs one pass.
- [ ] Build the promoted runtime and action producer without loading Agent consumers; execute Agent then model for each eligible source folder; close source and runtime on every path.
- [ ] Run scheduled-command tests and verify no-model mode leaves the historical window for a later model run instead of enqueueing Agent work.

### Task 6: Expose the two settings in the non-technical UI

**Files:**
- Modify: `frontend/src/api/console.ts`
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/pages/SettingsPage.test.tsx`

- [ ] Write failing component tests for independent “Agent 回溯” and “模型回溯” controls, the 30/365 defaults, payload values, account summary, and explanatory pending-feedback text.
- [ ] Run the focused Vitest file and confirm the old single-window UI fails the assertions.
- [ ] Replace the old account field in TypeScript types and draft conversion; render two controls and clarify that the read-state toggle belongs to Agent while model uncertainty goes to “待确认”.
- [ ] Run the focused Vitest file and TypeScript build.

### Task 7: Make operational documents match runtime behavior

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/runtime-mechanism.md`

- [ ] Replace the statements that historical processing is manual-only and that every model rejection falls back to Agent.
- [ ] Document the two windows, Agent-first overlap, bounded recurring model backfill, pending-feedback behavior, and technical-failure retry boundary.
- [ ] Search both documents for contradictory historical/manual/fallback statements and resolve them.

### Task 8: Full verification, focused commit, and deployment handoff

**Files:**
- Modify: `docs/agent-claims.md`

- [ ] Run focused backend tests for account schema/API, runtime, scans, scheduled command, pending feedback, and manual confirmation.
- [ ] Run the full backend test suite, frontend test suite, frontend production build, and import check required before service restart.
- [ ] Inspect `git diff --check`, the exact staged diff, and ensure no unrelated concurrent edits are staged.
- [ ] Commit the feature and remove the claim row in the same final commit or a follow-up claim-release commit.
- [ ] Send the commit hash and changed runtime files to the `CEO Agent 每小时修复` task. Do not restart this service from the implementation task; wait for that owner to verify idle queues, import the whole tree, restart, and read back PID, health, queues, Attention, and History.
