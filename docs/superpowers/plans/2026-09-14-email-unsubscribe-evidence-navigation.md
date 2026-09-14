# Email Unsubscribe Evidence Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a selected Email unsubscribe record safely reveal its stored entry
address on demand and open its verified Attempt history.

**Architecture:** The normal classification-detail projection remains redacted.
`EmailStore` derives safe Attempt IDs only after validating the immutable email
task/receipt lineage. A dedicated, non-cacheable detail endpoint validates the
same receipt before returning its saved address. The drawer fetches that URL
only after an explicit user action.

**Tech Stack:** FastAPI, SQLite, React, TypeScript, Vitest, pytest.

---

### Task 1: Safe receipt and Attempt projection

**Files:**

- Modify: `app/email_store.py:2050-2360, 12380-12610`
- Test: `tests/test_email_web_api.py:1969-2025`

- [x] **Step 1: Write failing store/API regression expectations.**

  Extend the audited unsubscribe fixture test so the terminal event requires
  `attempt_ids: [fixture.attempt.id]`; add a fixture `ReplyAttempt` with the
  same email conversation and action identity. Add a mismatch attempt with a
  different trigger identity and assert it is excluded.

  ```python
  assert event["attempt_ids"] == [fixture.attempt.id]
  assert fixture.unrelated_attempt.id not in event["attempt_ids"]
  ```

- [x] **Step 2: Run the focused test and confirm it fails because
  `attempt_ids` is absent.**

  Run: `pytest tests/test_email_web_api.py::test_email_detail_projects_only_redacted_audited_unsubscribe_lineage -q`

- [x] **Step 3: Implement the minimal verified lookup.**

  In `_audited_unsubscribe_lineage`, query `reply_attempts` by the already
  validated task conversation and action identity, ordered by ID, and project
  only integer IDs into `attempt_ids`. Do not use Consumer/Audit run IDs as
  Attempt IDs. Return no attempt IDs when the table is unavailable.

- [x] **Step 4: Re-run the focused test and confirm it passes.**

  Run: `pytest tests/test_email_web_api.py::test_email_detail_projects_only_redacted_audited_unsubscribe_lineage -q`

### Task 2: Explicit redacted-by-default entry-address endpoint

**Files:**

- Modify: `app/email_store.py:12380-12610`
- Modify: `app/web_api/email.py:1178-1202`
- Test: `tests/test_email_web_api.py:1969-2240`

- [x] **Step 1: Write failing endpoint tests.**

  Persist a terminal receipt with a fixture-only entry URL that hashes to its
  entry reference. Require `GET /api/console/email/classifications/{id}/unsubscribe-entry`
  to return that URL and `Cache-Control: no-store`; require the normal detail
  payload not to contain it. Delete/tamper with receipt lineage and assert a
  404 response with `code == "unsubscribe_entry_unavailable"` that contains
  neither URL nor its token.

  ```python
  response = fixture.client.get(
      f"/api/console/email/classifications/{fixture.classification_id}/unsubscribe-entry"
  )
  assert response.headers["cache-control"] == "no-store"
  assert response.json() == {"ok": True, "entry_url": fixture.entry_url}
  ```

- [x] **Step 2: Run those tests and confirm they fail because the route does
  not exist.**

  Run: `pytest tests/test_email_web_api.py -k 'unsubscribe_entry' -q`

- [x] **Step 3: Implement a narrow `EmailStore` reader and route.**

  Add `get_email_unsubscribe_entry_url(classification_id: int) -> str | None`.
  It selects only the classification's receipt with a nonempty URL, builds the
  existing classification and `_audited_unsubscribe_lineage` context, validates
  the address against `entry_reference`, and returns `None` unless every
  immutable binding validates. The FastAPI route maps `None` to the controlled
  404 envelope and returns `JSONResponse({"ok": True, "entry_url": value},
  headers={"Cache-Control": "no-store"})` on success. It never includes the
  value in an exception or log.

- [x] **Step 4: Re-run the endpoint tests and the existing redaction test.**

  Run: `pytest tests/test_email_web_api.py -k 'unsubscribe_entry or safe_modern_evidence' -q`

### Task 3: Drawer controls and Attempt links

**Files:**

- Modify: `frontend/src/api/console.ts:220-270, 650-675`
- Modify: `frontend/src/pages/email/Evidence.tsx:1-76`
- Modify: `frontend/src/pages/EmailPage.test.tsx:1-310`
- Test: `frontend/src/api/email.test.ts`

- [x] **Step 1: Write failing API and drawer tests.**

  Add `getEmailUnsubscribeEntryUrl(id, signal)` expectations for the exact
  encoded endpoint. In the Email drawer test, provide `attempt_ids: [9205]`,
  assert `href="/attempts/9205"`, assert no address API call before the user
  presses `显示完整地址`, then resolve the call and assert the returned URL is
  rendered with a `复制地址` control. Assert a failed reveal displays the
  controlled error while keeping the drawer open.

- [x] **Step 2: Run the focused frontend tests and confirm they fail because
  the helper/control/link is absent.**

  Run: `npm --prefix frontend test -- --run src/api/email.test.ts src/pages/EmailPage.test.tsx`

- [x] **Step 3: Implement the minimal client and UI.**

  Add `attempt_ids?: number[]` to `EmailObservabilityEvent` and one API helper
  that calls the dedicated endpoint. Extract `UnsubscribeEvidence` inside
  `Evidence.tsx`; it owns reveal loading/error state, calls the helper only
  from the button handler, renders safe `<Link>` elements to `/attempts/{id}`,
  and renders the returned string in a read-only field with a Clipboard API
  copy button. Do not add an automatic external-open action.

- [x] **Step 4: Re-run the focused frontend tests.**

  Run: `npm --prefix frontend test -- --run src/api/email.test.ts src/pages/EmailPage.test.tsx`

### Task 4: Integrated verification and runtime readback

**Files:**

- Modify: `CHANGELOG.md`
- Test: `tests/test_email_web_api.py`, `frontend/src/pages/EmailPage.test.tsx`

- [x] **Step 1: Run backend and frontend feature suites.**

  Run:

  ```bash
  pytest tests/test_email_web_api.py -q
  npm --prefix frontend test -- --run src/api/email.test.ts src/pages/EmailPage.test.tsx
  ```

- [x] **Step 2: Document the user-visible safety boundary.**

  Add one `CHANGELOG.md` entry stating that Email exposes verified Attempt
  navigation and explicitly revealed, non-cacheable unsubscribe entry
  addresses; do not record an actual address.

- [x] **Step 3: Commit only feature-owned files.**

  ```bash
  git add app/email_store.py app/web_api/email.py \
    frontend/src/api/console.ts frontend/src/api/email.test.ts \
    frontend/src/pages/email/Evidence.tsx frontend/src/pages/EmailPage.test.tsx \
    CHANGELOG.md docs/superpowers/plans/2026-09-14-email-unsubscribe-evidence-navigation.md
  git commit -m "feat(email): expose verified unsubscribe evidence"
  ```

- [ ] **Step 4: Restart and read back the live service.**

  First verify the durable task/receipt work is resumable and idempotent, then
  restart `com.ceo-agent-service.main`. Check `/healthz`, reload the selected
  unsubscribe message, verify the initial detail payload has no raw entry URL,
  press the reveal button, and follow the rendered Attempt link. Confirm no
  additional mail action was queued or executed merely by viewing/copying.
