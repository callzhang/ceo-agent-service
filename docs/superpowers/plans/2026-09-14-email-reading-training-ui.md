# Email Reading and Model Training UI Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task. Track completion with the checkboxes below.

**Goal:** Deliver the user-approved email reading panel and model training overview with accurate statuses and working actions.

**Architecture:** Keep EmailPage as route/data owner. Separate reading, evidence, training setup and promotion presentation into focused local components; reuse existing provider actions, training APIs, and model registry validation.

**Tech Stack:** React, TypeScript, React Router, Recharts, Vitest, Python API, SQLite.

**Approved design:** `docs/superpowers/specs/2026-09-14-email-reading-training-ui-design.md`.

## 1. Confirm data contracts before presentation changes

Files: `frontend/src/api/console.ts`, `app/web_api/email.py`, `app/email_store.py`, `tests/test_email_web_api.py`, `frontend/src/api/email.test.ts`.

- [ ] Trace training_sources and pending_examples to their real sources; record why collected data may be ineligible. Count selectable unique samples using the same eligibility/deduplication rules as training submission. Do not sum overlapping source counts.
- [ ] Add structured unsubscribe outcome and URL availability/reason to normal detail when absent. Normal detail still excludes the private URL. Reuse the explicit entry route validation for availability.
- [ ] Trace important writes through the existing action execution path. Add the missing UI-facing operation only if absent, preserving configured Audit handling and provider readback.
- [ ] Add focused contract tests: terminal/no_reliable_entry is not success; unavailable URL has a reason; available URL fetch still validates linkage; unreadable training sources are errors, not zero examples; unknown important remains unknown.
- [ ] Run `pytest tests/test_email_web_api.py -q` and `npm --prefix frontend test -- --run src/api/email.test.ts` before committing this contract change.

## 2. Implement email reading layout

Files: `frontend/src/pages/email/EmailList.tsx`, new `frontend/src/pages/email/EmailReadingPanel.tsx`, `frontend/src/pages/email/email.css`, `frontend/src/pages/EmailPage.test.tsx`.

- [ ] Move selected-email presentation into EmailReadingPanel, receiving detail, loading/error, controlled category/important values, save state, previous/next callbacks and close/expand callbacks. Keep route parameters and fetching in EmailList.
- [ ] Replace the mail-specific overlay with in-flow double columns; use full-width reading below 1100px. Keep the generic EmailDrawer available for settings and model dialogs.
- [ ] Place header/actions before body; add original/processing tabs and collapsed classification evidence. Preserve paragraphs, quoted text and attachment metadata; inspect the original text extraction path before changing HTML handling.
- [ ] Preserve URL-selected mail, page/page_size/filter, focus restoration and page-bound previous/next navigation. Discard stale responses when switching mail.
- [ ] Fix save behavior so processed/all views refresh and retain matching records. Remove a row only after it no longer matches the selected filter. Keep errors adjacent to Save.
- [ ] Add tests for reading tab navigation, processed reclassification retained under All, pending record removed under pending-only, preserving URL state, save error retaining edits, stale detail requests and keyboard navigation.
- [ ] Run `npm --prefix frontend test -- --run src/pages/EmailPage.test.tsx` and commit the reading change.

## 3. Present action outcomes and entry links

Files: `frontend/src/pages/email/Evidence.tsx`, `frontend/src/pages/email/shared.ts`, `frontend/src/pages/email/email.css`, `frontend/src/pages/EmailPage.test.tsx`.

- [ ] Translate structured outcomes into result text and status icon, using neutral unknown when no result exists. Show provider folder names and important state as normal fields.
- [ ] Show the associated Attempt link within each unsubscribe action. Place hashes, raw logs, ActionPlan and receipt metadata under technical details.
- [ ] Show URL availability before offering reveal; implement reveal loading, inline failure/retry, copy success/failure feedback. Switching messages must discard an earlier revealed address.
- [ ] Test a completed but unsuccessful unsubscribe, genuinely successful unsubscribe, unknown result, unavailable entry, invalidated entry at reveal time, clipboard failure, and Attempt target.
- [ ] Run the focused frontend suite and commit this presentation change.

## 4. Reorganize training overview and setup

Files: `frontend/src/pages/email/ModelTraining.tsx`, new `frontend/src/pages/email/TrainingSetup.tsx`, new `frontend/src/pages/email/PromotionPanel.tsx`, `frontend/src/pages/email/email.css`, `frontend/src/pages/EmailPage.test.tsx`.

- [ ] Keep ModelTraining as state owner for the actual runtime/candidate, training request and version selection. Extract training controls and threshold editing into the two panels.
- [ ] Render mode/action header, eligible sample count and a single candidate's F1/P95. Candidate selection must follow service-provided candidate identity, never a client-side best-score override.
- [ ] Display no-model and unmeasured states compactly; group category precision/support checks into one row. Keep system-integrity errors visible even when check details are collapsed.
- [ ] Move source/category/model-family selection into New Training, with source availability and exact selected count. Preserve user selections during background refresh when still valid; remove invalid selections with a visible reason.
- [ ] Retain existing mode confirmation, optimistic version checks and server readback. Red dot and toggle depend on verified server readiness.
- [ ] After submission, refresh durable training state and display real stage/per-family status until terminal. Disable repeat submissions during an active submission; surface API failures and resumable state after refresh.
- [ ] Test zero data versus source error, valid subset selection, multi-family payload, running/completed/failed training, missing candidate, readiness toggle and failed switch recovery.
- [ ] Run focused frontend/backend tests and commit training overview/setup.

## 5. Trends and model history

Files: `frontend/src/pages/email/modelTrend.ts`, `frontend/src/pages/email/modelTrend.test.ts`, `frontend/src/pages/email/ModelTraining.tsx`, `frontend/src/pages/email/email.css`.

- [ ] Group series by family and evaluation comparability; exclude no metric from plotted values while preserving empty-state explanations. Use shared metric values for table, chart and tooltip.
- [ ] Display isolated points without requiring two comparable models; never connect incomparable protocols/data/category sets. Keep threshold reference lines and readable metric labels.
- [ ] Add family/status filtering to version history. Separate runtime active status from historical registration; full model ID remains copyable in details.
- [ ] Organize version details into Effect, Data/Parameters and Technical Details. Collapse mode-transition history.
- [ ] Test mixed-family series, incomparable evaluation boundaries, isolated point, missing metric, historical active registration and exact model-ID detail lookup.
- [ ] Run `npm --prefix frontend test -- --run src/pages/email/modelTrend.test.ts src/pages/EmailPage.test.tsx` and commit model history/trends.

## 6. Visual and release acceptance

- [ ] Run `npm --prefix frontend test -- --run`, `npm --prefix frontend run build`, `pytest tests/test_email_web_api.py -q`, and any newly affected provider/training suites. Record actual results.
- [ ] Inspect screenshots at 1440×900, 1280×800, 768×900 and 600×900 against both approved images; verify details, processing tab, new-training panel, promotion settings and version details.
- [ ] Exercise all states listed in the spec with isolated fixtures. Live UI checks are read-only: do not submit real training, move mail, mark important or promote a model solely for screenshot validation.
- [ ] Check actual current selected mail `3203915087564382400`: original text readable, unsuccessful unsubscribe stated accurately, Attempt #9203 reachable, private URL availability accurate.
- [ ] Update CHANGELOG and user guide with new navigation. Commit task-owned files only, preserving unrelated graphify output or other work.
- [ ] Before authorized runtime restart, verify durable resumability/idempotency under the canonical project rules. After restart inspect health, queue recovery, live assets and API/browser readback.
- [ ] Report final commits, tests, visible runtime result, and any remaining real data limitations; do not claim sample quality or model improvement from this UI change.

## Coverage review

Spec reading/navigation → task 2; body and evidence/outcomes → tasks 1–3; mode, sources, controls → tasks 1 and 4; trends/version traceability → task 5; responsive/error/keyboard/runtime acceptance → task 6. These tasks describe implementation work; no product code has changed in this planning phase.
