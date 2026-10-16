# Tasks8 allocator provenance implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: subagent-driven-development for exact transfer, then fresh SPEC and fresh QUALITY.

**Goal:** Retain the original published-eight source allocator water independently from the twelve new source kinds sharing physical Tasks IDs, so the future inverse can restore current facts.

**Architecture:** Add one internal sqlite_sequence row named tasks:published and one finite BEFORE INSERT trigger for the eight published kinds. Existing IDs and allocation remain native. An explicit initializer runs only in caller-owned migration/fresh constructor transactions, after bulk transfer, with captured original source water. This component is unselected; no constructors, callers or migration selector changes. No ordinary table, business field, history record, transaction wrapper or SQL parsing is added.

**Tech Stack:** pinned Python3.13.12/SQLite3.50.4, frozen actual/fresh source8 DDL, existing Tasks20 declarations and exact Task8 parts.

## Confirmed source contract and evidence boundary

Two histories can leave identical seven stored Tasks columns and the same global Tasks water3: new12 hidden-ROWID source duplicate IGNORE after physical2 should leave original-eight water1; old8 duplicate IGNORE must retain its observed allocation3. Existing source IDs remain physical IDs, including gaps. One internal source counter retains that otherwise lost fact.

Native current8 callers use single VALUES automatic physical allocation with plain INSERT, IGNORE and UPSERT. Trigger predicts max(global sqlite_sequence, actual max(id),0)+1 only when NEW.id=-1; explicit IDs use NEW.id. At MAX it updates no higher water and lets native SQLITE_FULL run. Frozen source BEFORE triggers preserve the caller transaction and earlier writes on MAX statement failure. Arbitrary multi-row INSERT with ignored candidates and OR FAIL are outside the observed runtime contract; do not advertise them as supported. Migration explicit -1 can be ambiguous in BEFORE INSERT, so reset the counter from captured source water AFTER bulk transfer. Original absence versus zero remains in migration metadata. Runtime insert compiler does not expose physical id. No helper/compiler change is needed.

## Complete canonical artifacts

- `docs/superpowers/plans/2026-10-16-task8-allocator-candidate.py` -> `app/task20_definitions.py`; SHA256 `cf58403bcc9dc7bf4c3ef5b9a777ff941c7a6de210e04b29e4cb7abb1e0df4d0`.
- `docs/superpowers/plans/2026-10-16-task8-allocator-regression.py` -> `tests/test_task8_allocator.py`; SHA256 `c27823e6d9de6028e962cce1adfa7f9520a6b61797702fb658ab5d17cbe72752`.

Preflight: `docs/superpowers/plans/2026-10-16-task8-allocator-preflight.py`; it copies app/tests into a task-owned /tmp tree and removes it. `--applied` uses installed app/test; default uses complete canonical files. `--before-repair` disables only new trigger in the copied module, leaving initializer/fixtures intact to prove missing state retention.

### Task 1: source differential before application edits

- [x] Complete candidate/test/preflight before app edits; original app is unchanged.
- [x] Bounded read-only research18 actual/fresh source cases passes.
- [x] Permanent RED108 fail/13 controls pass8.36s after removing only new trigger; no import/fixture setup failures counted as RED.
- [x] Complete candidate plus existing declarations/Task8/Task12 focused matrix961pass64.98s (121new+325declarations+276Task8+239Task12), OWNED_TEMP_REMOVED True. Initial preflight concatenation/unused fixture import and missing copied dependency were corrected before final RED/GREEN; setup errors are not regression evidence.

### Task 2: exact mechanical transfer

- [x] Claim only app/task20_definitions.py, tests/test_task8_allocator.py, own row and this plan checkboxes.
- [x] Verify canonical hashes above; copy exact files. Do not edit previous source snapshots, stored fields, indexes, source defaults, existing trigger messages, helpers or callers.
- [x] Run pinned applied preflight with declarations: 961 passed in 68.82s; `OWNED_TEMP_REMOVED True`.
- [x] Ruff on exact app/new test and `git diff --check` passed; committed only claimed changes. No production, provider, network, native CLI, main, push or deploy.

### Task 3: independent review then root acceptance

- [ ] Fresh SPEC: source actual/fresh DDL authority; all8 automatic/IGNORE/UPSERT/ABORT/FULL, current/cursor transaction semantics, internal row and no schema install DML; source absence/zero/deleted-high, migration explicit negative reset, caller/savepoint rollback, mixed-history ambiguity.
- [ ] Fresh QUALITY: meaningful controls kill missing tracking, incorrectly tracking all20, skipping ignored/UPSERT attempted allocation, MAX overflow, eager initializer commit, and lost explicit source-water reset. Compare valid native controls before mutations; own temp only and cleanup.
- [ ] Root records exact reviewed hashes and scope. New trigger count31; prior30-trigger acceptance remains historical. This is allocator foundation only, not actual-copy migration or full inverse acceptance.

## Remaining full cutover

Initializer must be selected explicitly inside both complete paired migration and fresh constructor transaction, reset AFTER all bulk inserts. Inverse reads tasks:published with original metadata for absence/zero, and checks surviving old8 IDs against restored water. Current source-eight counter cannot simply copy global Tasks water after new12 writes. Complete all20 caller/construction/feedback endpoints first, then copy current writes/current-fact inverse/genuine predecessor writes/reforward. Native CLI fake outbound/UI/business result/receipt, all remaining families and final10core+Emailledger acceptance remain open. Last integrated copy67ordinary tables is not final11. No production publication without final human approval.

QUALITY regression supplement: four actual/fresh cases persist earlier caller-write rollback and caller-savepoint survival across initializer. App SHA unchanged. Independent control4pass; commit-before-DML mutant4fail (persisted sentinel/no-such-savepoint), no setup failures. Final focused125 installed run/root commit below supersedes121-case regression hash; original961 full application proof remains unchanged-source evidence.
