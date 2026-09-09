# Email Console implementation verification

Status: implementation and review in progress; not deployed.

## Scope

Implements the approved Email density and model-training design. No automatic
model promotion, mailbox mutation, reply, or unsubscribe is performed by the
verification workflow. Browser interactions use an in-memory fixture.

## Implementation notes

- Frontend modules live under `frontend/src/pages/email/`, beside their page
  owner, rather than a second global component hierarchy.
- Running mode and its transition history are committed in one atomic Registry
  manifest replacement. Configuration and category-description versions live in
  the existing Email SQLite store.
- Missing historical metrics remain unmeasured. Classifier-head timings cannot
  stand in for resident end-to-end timings.
- Read-only Registry inventory preserves readable versions when a separate
  artifact is corrupt; promotion still requires the complete integrity checks.

## Browser checks performed

Local fixture: `http://127.0.0.1:5181/workbench-assets/email-fixture.html`.
Fixture messages and models are synthetic; every API operation is in memory.

- Desktop list: 44-pixel single rows; 50 messages on page one, five on page two.
- Pending review: complete text and quoted text visible in the drawer;
  attachments shown as metadata only.
- Confirmation: count changed from 55 to 54 and selection advanced.
- Ready candidate: explicit confirmation changed the fixture to model-primary,
  checked the toggle, and removed the ready red dot.
- 390-pixel viewport: model overview wraps. Rechecked the footer correction: all
  nine choices, including fixed Trash, are visible and wrap inside the drawer.
- Category editor displays core definition, inclusion examples, exclusions,
  description/config versions, and provider-folder bindings.

## Remaining verification

- Resolve independent persistence, runtime/API, and frontend review findings.
- Confirm training/online input equivalence before accepting benchmark evidence.
- Finish full Python suite, frontend suite/build, and affected regression reruns.
- Reconcile concurrent main-branch changes without overwriting unrelated work.
- Verify database backup before migration, deploy, and check live mode and queue
  state. Do not claim that the fixture proves production behavior.

## Intermediate test evidence

- Persistence at `f202763e`: main-agent rerun, 373 passed in 77.37 seconds;
  independent reviewer also verified 373 passing tests and closed prior findings.
- Frontend at `cfe6661a`: main-agent rerun, 36 focused tests passed; independent
  re-review closed stale-request, keyboard disclosure, and failed-switch readback
  findings. The implementer also completed TypeScript and production build checks.
- Frozen runtime/API/training slice: main-agent combined run, 580 passed in
  31.24 seconds with four file-scoped test workers; three existing deprecation
  warnings. Scoped Ruff and staged whitespace checks passed. This is a test-suite
  execution setting, not a measurement of model performance.
- Independent training review verified exact input/cache identity, metadata-only
  attachments, no folder-label leakage, separate remote/cache measurements, and
  healthy-row preservation. It did not call the live GPU or mailbox.
- Final runtime/API review closed active-model corrupt-inventory handling and
  required API-level category CAS. Store-level callers may omit CAS; HTTP edits
  must carry a nonblank current version. All named P1/P2 review findings are closed.
- Production-database rehearsal: verified schema-33 backup, upgraded only its
  second copy to schema 34, and checked integrity plus preserved counts of 325
  classifications, 325 message rows, and 5,107 tasks. Actual production migration
  remains a separate deployment step.
- First full Python run overlapped active TDD edits: 6,984 passed, 24 failed,
  86 skipped, 44 deselected in 1,108.80 seconds. Twenty-three failures exercised
  the earlier in-memory persistence implementation before the fixes above. The
  remaining managed-thread test used a one-second event timeout and requires
  deterministic synchronization review. This run is not final release evidence.
- Read-only live body availability: all three sampled pending-feedback details
  returned text (323, 3,662, and 15,143 characters). One of three processed samples
  had neither stored body nor preview; the UI must not fabricate missing content.

Feedback `ok=true` acknowledges the persisted classification feedback, not the
completion of asynchronously executed mailbox actions. The page may advance to
the next classification after that acknowledgement; actual folder/flag/action
outcomes remain separately visible in the existing observability/detail evidence.
No synchronous action executor or new authorization rule is introduced here.
