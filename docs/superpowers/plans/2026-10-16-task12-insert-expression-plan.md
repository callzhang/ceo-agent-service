# Task12 native INSERT scalar-expression implementation

Required workflow: complete canonical source and regression before application edits; mechanical exact transfer; fresh SPEC review then fresh QUALITY review. This remains an unselected component of the approved database redesign.

## Source and scope

The frozen source statements in `../specs/2026-10-16-feedback-insert-expression-source.json` are the actual `upsert_feedback_event`, `create_feedback_processing_batch` and `resolve_feedback_event` feedback item INSERTs. They query retained feedback_events or evaluate current_timestamp in the original INSERT. Preparing scalar values with a separate SELECT would change execution timing. The existing INSERT-parts interface accepts bound values only.

Extend `task12_insert_parts` with `insert_sql` and ordered `insert_parameters`, using the existing exact native scalar codec. `_payload` includes those expressions directly in its final JSON construction and omits their defaults. Expressions cannot replace source identity or a public-key dependency used to resolve a physical parent. This restriction comes from existing finite field/parent metadata; it adds no business field or execution policy. Caller INSERT/UPSERT and commit ownership remain intact. Existing no-expression callers retain their SQL/parameters and point-writer behavior.

Only two existing functions change: `_payload` and `task12_insert_parts`, plus the existing TASK20_PARENTS import. No caller, constructor, schema, migration, codec, allocator, current connection hook or production behavior changes.

## Complete transfer artifacts

- Candidate `2026-10-16-task12-insert-expression-candidate.py` → `app/task12_storage.py`, SHA256 `2fbcd6764fa5b8bd6d18f3ac961be56fa74e7d6e1f3f86dcc3e54fd6ec400cae`.
- Regression `2026-10-16-task12-insert-expression-regression.py` → `tests/test_task12_insert_expression.py`, SHA256 `3f7a79d1d03c38d8db3f84c9920bc8f665c6ad772295cda5f87977c82176d09b`.
- Preflight `2026-10-16-task12-insert-expression-preflight.py`, SHA256 `75a6f53083bebdf8a7314b5ece6d2f5aa40c1d11e664ea87373d6940341dc98a`.

The preflight copies source/tests to an owned temporary directory, substitutes the candidate by default, or installed source with `--applied`, and removes its copies. `--with-declarations` includes the existing Task12/native-parts, Task8/allocator and Tasks20 focused suites. It uses the pinned Python3.13 runtime. No full service suite or source database is used.

## Verification and implementation

- [x] Freeze actual three source SQL values expressions and owners without source row bodies.
- [x] Complete candidate, regression and preflight before any application edit.
- [x] Full candidate scoped preflight1053passed/64.50s, including42new cases; Ruff clean; owned temp removed. Initial fixture errors (invented weight field corrected to actual objective_weight, existing NOTNULL-to-CHECK extended code distinction) corrected before acceptance; no application defect asserted.
- [x] Mechanical exact-copy of the two canonical files, own claim and implementation checkboxes only; applied full scoped preflight and Ruff; commit.
- [ ] Fresh SPEC: verify real source statements, native execution timing, lazy CASE/default suppression, scalar types, identity dependency representation and unchanged no-expression path; no runtime selection.
- [ ] Fresh QUALITY after SPEC: meaningful mutation controls for eager evaluation, duplicate evaluation, exact scalar encoding and omitted expression/default; independent restored control. Do not count missing API or collection failures as semantic kills.
- [ ] Root records acceptance and current integration boundary.

The final 50-case new regression compares actual/fresh source declarations. It executes the actual three INSERT VALUES clauses, checks preparing before feedback_events exists and observing facts inserted later, lazy CASE, once-only exact REAL arithmetic, constraint failure preserving caller work, and identity/duplicate validation before write. Full UPSERT clauses and real feedback public-method behavior remain a separate caller proof. Native NOT NULL failures are expressed by existing target CHECK constraints: both reject and preserve caller work, but their extended SQLite error codes intentionally differ; this test does not claim extended-code equality.

No main merge, push, production DB write, restart or deploy. Complete caller/constructor/migration/current-fact inverse/predecessor/native CLI/UI and final ten core tables plus Email ledger remain required.


## Independent SPEC correction before acceptance

Initial applied ef26ceadf passed1053/65.92s and fresh SPEC42/2.94s, but SPEC reproduced a P2 accepted-write mismatch: REPLACE plus a NULL scalar for required/defaulted status failed target CHECK while native SQLite inserted its pending default. This is distinct from the documented error-code representation difference for two rejecting writes. Candidate now wraps only required/defaulted expression fields under REPLACE in native coalesce(expression, captured default), using existing metadata. Once-only expression evaluation and lazy default evaluation are covered; a required expression without a default still rejects. Point/bound behavior remains unchanged.

The 8 added actual/fresh cases reproduce original application RED through --before-default-repair -k replace:4failed/4passed/42deselected1.17s, native accepted controls then target constraint errors. Corrected candidate50/4.08s PASS. No app edit occurred before complete canonical code/regression/preflight correction. Full corrected preflight and mechanical correction pending.

- [x] Corrected full scoped preflight1061/66.10s PASS; owned temporary copies removed.
- [x] Mechanical correction copies exact current candidate/regression, applied50focused rerun, hashes/Ruff/scope/commit.
- [ ] SPEC rereview resolves P2; fresh QUALITY follows.
