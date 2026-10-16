# Task12 native INSERT scalar-expression implementation

Required workflow: complete canonical source and regression before application edits; mechanical exact transfer; fresh SPEC review then fresh QUALITY review. This remains an unselected component of the approved database redesign.

## Source and scope

The frozen source statements in `../specs/2026-10-16-feedback-insert-expression-source.json` are the actual `upsert_feedback_event`, `create_feedback_processing_batch` and `resolve_feedback_event` feedback item INSERTs. They query retained feedback_events or evaluate current_timestamp in the original INSERT. Preparing scalar values with a separate SELECT would change execution timing. The existing INSERT-parts interface accepts bound values only.

Extend `task12_insert_parts` with `insert_sql` and ordered `insert_parameters`, using the existing exact native scalar codec. `_payload` includes those expressions directly in its final JSON construction and omits their defaults. Expressions cannot replace source identity or a public-key dependency used to resolve a physical parent. This restriction comes from existing finite field/parent metadata; it adds no business field or execution policy. Caller INSERT/UPSERT and commit ownership remain intact. Existing no-expression callers retain their SQL/parameters and point-writer behavior. The final SQL-expression option is limited to existing non-AUTOINCREMENT source kinds; the five independent source AUTO kinds retain bound inputs.

Only two existing functions change: `_payload` and `task12_insert_parts`, plus the existing TASK20_PARENTS import. No caller, constructor, schema, migration, codec, allocator, current connection hook or production behavior changes.

## Complete transfer artifacts

- Candidate `2026-10-16-task12-insert-expression-candidate.py` → `app/task12_storage.py`, SHA256 `127311aa2ed303066fc3dd325a5642c0e61725a2d8db3b5bedfc218e804e013f`.
- Regression `2026-10-16-task12-insert-expression-regression.py` → `tests/test_task12_insert_expression.py`, SHA256 `0835a92ed79aa51e581e3f31753018f21cd9b258569ae86966d5d8a33b13dbd4`.
- Preflight `2026-10-16-task12-insert-expression-preflight.py`, SHA256 `8e95153781d57846ca61a6f7c9c4fbe913c491d71c3f3732683215460e40989d`.

The preflight copies source/tests to an owned temporary directory, substitutes the candidate by default, or installed source with `--applied`, and removes its copies. `--with-declarations` includes the existing Task12/native-parts, Task8/allocator and Tasks20 focused suites. It uses the pinned Python3.13 runtime. No full service suite or source database is used.

## Verification and implementation

- [x] Freeze actual three source SQL values expressions and owners without source row bodies.
- [x] Complete candidate, regression and preflight before any application edit.
- [x] Full candidate scoped preflight1053passed/64.50s, including42new cases; Ruff clean; owned temp removed. Initial fixture errors (invented weight field corrected to actual objective_weight, existing NOTNULL-to-CHECK extended code distinction) corrected before acceptance; no application defect asserted.
- [x] Mechanical exact-copy of the two canonical files, own claim and implementation checkboxes only; applied full scoped preflight and Ruff; commit.
- [ ] Fresh SPEC: verify real source statements, native execution timing, lazy CASE/default suppression, scalar types, identity dependency representation and unchanged no-expression path; no runtime selection.
- [ ] Fresh QUALITY after SPEC: meaningful mutation controls for eager evaluation, duplicate evaluation, exact scalar encoding and omitted expression/default; independent restored control. Do not count missing API or collection failures as semantic kills.
- [ ] Root records acceptance and current integration boundary.

The final 64-case new regression compares actual/fresh source declarations. It executes the actual three INSERT VALUES clauses, checks preparing before feedback_events exists and observing facts inserted later, lazy CASE, once-only exact REAL arithmetic, constraint failure preserving caller work, and identity/duplicate validation before write. Full UPSERT clauses and real feedback public-method behavior remain a separate caller proof. Native NOT NULL failures are expressed by existing target CHECK constraints: both reject and preserve caller work, but their extended SQLite error codes intentionally differ; this test does not claim extended-code equality.

No main merge, push, production DB write, restart or deploy. Complete caller/constructor/migration/current-fact inverse/predecessor/native CLI/UI and final ten core tables plus Email ledger remain required.


## Independent SPEC correction before acceptance

Initial applied ef26ceadf passed1053/65.92s and fresh SPEC42/2.94s, but SPEC reproduced a P2 accepted-write mismatch: REPLACE plus a NULL scalar for required/defaulted status failed target CHECK while native SQLite inserted its pending default. This is distinct from the documented error-code representation difference for two rejecting writes. Candidate now wraps only required/defaulted expression fields under REPLACE in native coalesce(expression, captured default), using existing metadata. Once-only expression evaluation and lazy default evaluation are covered; a required expression without a default still rejects. Point/bound behavior remains unchanged.

The 8 added actual/fresh cases reproduce original application RED through --before-default-repair -k replace:4failed/4passed/42deselected1.17s, native accepted controls then target constraint errors. Corrected candidate50/4.08s PASS. No app edit occurred before complete canonical code/regression/preflight correction. Full corrected preflight and mechanical correction pending.

- [x] Corrected full scoped preflight1061/66.10s PASS; owned temporary copies removed.
- [x] Mechanical correction copies exact current candidate/regression, applied50focused rerun, hashes/Ruff/scope/commit.
- [ ] SPEC rereview resolves P2; fresh QUALITY follows.


## QUALITY contract narrowing: existing source AUTO preparation

Fresh QUALITY found a second P2 in the overly broad internal API: an expression-valued round_number=1 at its source AUTO stream maximum raised the positive-round diagnostic instead of native SQLITE_FULL, because the existing allocator requires bound inputs before the final INSERT. Independent actual/fresh diagnostic2failed/6passed0.64s proved this; the actual three business INSERT expressions all belong to non-AUTOINCREMENT feedback_processing_items.

The final API deliberately supports SQL INSERT expressions only for existing non-AUTO source kinds. Existing five independent AUTO kinds continue using bound values exactly as current callers do. Reject their nonempty insert_sql before any SQL, without changing allocator, source-trigger order, point writers or business behavior. This does not claim generic AUTO expression support or introduce new allocator machinery. The boundary derives from existing TASK12_AUTOINCREMENT_KINDS, no new field or source policy.

Four actual/fresh available/exhausted-round cases assert unsupported expression rejection before any SQL, then verify the bound round path still inserts or produces native SQLITE_FULL with prior caller work preserved. Ten cases cover all five source AUTO kinds followed by successful native bound controls. Exact-scalar diagnostics now use the existing non-AUTO feedback item attempt_id INTEGER field (which natively retains nonintegral REAL); the no-default NULL diagnostic uses actual workbench_artifacts.label. Candidate64/5.00s passes; final scoped1075 pending.

- [x] Final scoped1075/71.50s PASS, owned temp removed. Previous API boundary regression14failed/50deselected0.90s reproduces unsupported admission; this is API contract rejection evidence, not14native equivalence failures. The independent native mismatch was2/8cases above.
- [ ] Exact final app/test transfer and applied64, with mechanical receipt correction (previous writer8/42deselected was not50; separate SPEC50/4.09s).
- [ ] Sequential SPEC rereview and QUALITY rereview of narrowed contract.
