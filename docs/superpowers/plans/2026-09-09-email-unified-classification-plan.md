# Email Unified Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge processed and pending email views into one original-text-first workflow that supports filtering, reclassification, and immediate provider actions.

**Architecture:** Keep the existing Email API and provider action boundaries. Extend the list endpoint with a unified status filter, use one detail drawer for model/Agent/user classification, and route every saved classification through the existing provider-aware action executor. The UI will never synthesize a summary or use preview text as the full body.

**Tech Stack:** Python existing web API/store, React + TypeScript + Vite, Vitest + Testing Library, existing IMAP/provider adapters.

---

### Task 1: Define unified list contract

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/web_api/email.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/api/console.ts`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_email_web_api.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/pages/EmailPage.test.tsx`

- [ ] **Step 1: Write failing API tests** for `status=all`, `status=pending_feedback`, and `status=processed`, asserting the response retains `category`, `classification_source`, `message_text` only in detail, and provider observations.
- [ ] **Step 2: Run `pytest tests/test_email_web_api.py -q` and confirm the unified filter is absent or fails.
- [ ] **Step 3: Implement the smallest query/response extension, preserving current status-specific behavior and string IDs.
- [ ] **Step 4: Add typed frontend request parameters for the unified filter.
- [ ] **Step 5: Run the focused API and frontend tests; expect all existing tests plus the new contract tests to pass.
- [ ] **Step 6: Commit only the API/type/test files with `git commit -m "feat: add unified email classification list contract"`.

### Task 2: Replace separate tabs with unified filters

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/pages/EmailPage.tsx`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/pages/email/EmailList.tsx`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/pages/email/email.css`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/pages/EmailPage.test.tsx`

- [ ] **Step 1: Add failing tests** for `全部`, `待确认`, and `已处理` filters, URL persistence, and one shared list component.
- [ ] **Step 2: Run `npm test -- --run src/pages/EmailPage.test.tsx` and verify the new filter assertions fail.
- [ ] **Step 3: Implement the three-filter navigation and preserve page/page_size/selected URL state.
- [ ] **Step 4: Make each row render original body text truncated by CSS only; remove any generated-summary field from the visible list.
- [ ] **Step 5: Add regression coverage for null category displaying `未分类（留在收件箱）`.
- [ ] **Step 6: Run focused frontend tests and build with `npm run build`.
- [ ] **Step 7: Commit the unified UI with `git commit -m "feat: unify email processed and pending views"`.

### Task 3: Enable reclassification for processed mail

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/web_api/email.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/store.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/pages/email/EmailList.tsx`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_email_store.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_email_web_api.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/pages/EmailPage.test.tsx`

- [ ] **Step 1: Write failing tests** showing a processed message can be changed from `work` to `legal`, the new provider action is issued, and an action failure is persisted without claiming success.
- [ ] **Step 2: Run the focused tests and confirm processed feedback is rejected or unavailable.
- [ ] **Step 3: Reuse the existing idempotency key and provider action path for both pending and processed records; do not add a second action executor.
- [ ] **Step 4: Render the same category chooser in the processed drawer, with current category and source visible.
- [ ] **Step 5: Add tests for `junk → Trash`, important Star/Flag independence, null category remaining in Inbox, and retry after provider failure.
- [ ] **Step 6: Run focused tests, then the email API/store suites.
- [ ] **Step 7: Commit with `git commit -m "feat: support email reclassification"`.

### Task 4: Implement direct Codex API Agent client

**Files:**
- Create: `/Users/derek/Documents/Projects/ceo-agent-service/app/email_agent_api.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/consumer_agent.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/email_worker.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/settings.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_email_agent_api.py`

- [ ] **Step 1: Write failing client tests** for valid JSON classification, unknown category rejection, timeout, bounded retry, and token/cost event recording.
- [ ] **Step 2: Run `pytest tests/test_email_agent_api.py -q` and confirm the client does not exist.
- [ ] **Step 3: Implement a small HTTP client using the configured Codex API/fallback settings, request timeout, max retry count, and strict response validation.
- [ ] **Step 4: Build the request from subject, sender, recipients, body, quoted body, attachment metadata, and versioned category descriptions; never include attachment content or generated summaries.
- [ ] **Step 5: Connect cold-start labeling and low-confidence fallback to the client while preserving `agent_result` provenance and no CLI task creation.
- [ ] **Step 6: Run client and worker tests; verify no Codex CLI task path is invoked.
- [ ] **Step 7: Commit with `git commit -m "feat: call email classification agent through API"`.

### Task 5: Add multi-source training selection

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/email_classifier_training.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/email_training_snapshot.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/web_api/email.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/pages/email/ModelTraining.tsx`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/api/console.ts`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_email_classifier_training.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/pages/EmailPage.test.tsx`

- [ ] **Step 1: Write failing tests** for default-all source selection, source/category exclusions, and inclusion of user, Agent, folder, Star/Flag, and Trash labels with provenance.
- [ ] **Step 2: Run focused tests and confirm training currently ignores the new selection contract.
- [ ] **Step 3: Add a versioned training snapshot selection object; retain message IDs, source, label, description version, and digest.
- [ ] **Step 4: Add a training request endpoint and UI controls that default all sources/categories to selected and allow unchecking them.
- [ ] **Step 5: Verify training snapshots never write back to user feedback and exclude records with missing body where body is required.
- [ ] **Step 6: Run training and frontend tests plus TypeScript build.
- [ ] **Step 7: Commit with `git commit -m "feat: select email training data sources"`.

### Task 6: Train and compare selectable model families

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/email_classifier_training.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/email_classifier_model.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/email_worker.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/web_api/email.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/pages/email/ModelTraining.tsx`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_email_classifier_training.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_email_classifier_model.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/frontend/src/pages/EmailPage.test.tsx`

- [ ] **Step 1: Write failing tests** for selecting any subset of `tfidf_lr`, `fasttext`, and `embedding_mlp`, creating one independent candidate version per selected family, and preserving each artifact/metric record.
- [ ] **Step 2: Run focused tests and confirm only the current model path is supported.
- [ ] **Step 3: Implement the family registry and shared training request, keeping family-specific preprocessing and artifacts isolated.
- [ ] **Step 4: Add manual start action, progress state, failure state, and candidate-only persistence; no automatic primary promotion.
- [ ] **Step 5: Add model detail and comparison UI for training time, samples, per-category metrics, latency, parameters, and historical trend.
- [ ] **Step 6: Test save/reload prediction consistency and primary toggle server readback.
- [ ] **Step 7: Run all classifier, API, worker, and frontend tests plus production build.
- [ ] **Step 8: Commit with `git commit -m "feat: train selectable email model families"`.

### Task 7: Provider folder and label action integration

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/email_provider.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/email_worker.py`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/app/web_api/email.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_email_worker.py`
- Test: `/Users/derek/Documents/Projects/ceo-agent-service/tests/test_email_provider.py`

- [ ] **Step 1: Write failing tests** for account-specific folder creation/binding, category move, junk-to-system-Trash, and provider-aware Star/Flag union signals.
- [ ] **Step 2: Run focused provider/worker tests and confirm missing adapter behavior.
- [ ] **Step 3: Implement one-way service-to-mail-server folder synchronization and idempotent action receipts.
- [ ] **Step 4: Keep server folders as training observations only; never create business categories from arbitrary server folders.
- [ ] **Step 5: Run provider, worker, and API integration tests with mocked provider boundaries.
- [ ] **Step 6: Commit with `git commit -m "feat: apply unified email folder and label actions"`.

### Task 8: Full verification and runtime delivery

**Files:**
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/docs/superpowers/plans/2026-09-09-email-unified-classification-plan.md`
- Modify: `/Users/derek/Documents/Projects/ceo-agent-service/docs/superpowers/specs/2026-09-09-email-unified-classification-and-api-agent-design.md`

- [ ] **Step 1: Run `pytest -q` and record the complete result, including skips and existing unrelated failures.
- [ ] **Step 2: Run `npm test -- --run` and `npm run build`.
- [ ] **Step 3: Restart `com.ceo-agent-service.main`, verify the new process and `/healthz`.
- [ ] **Step 4: Browser-check unified filters, original text, drawer reclassification, probability distribution, training source/model selection, candidate records, and no CLI task creation.
- [ ] **Step 5: Check the database for new failed/processing backlog and verify no production model promotion occurred.
- [ ] **Step 6: Record exact commits, test results, runtime state, and any user-owned uncommitted files without staging them.
- [ ] **Step 7: Commit only verification documentation with `git commit -m "docs: verify unified email classification delivery"`.
