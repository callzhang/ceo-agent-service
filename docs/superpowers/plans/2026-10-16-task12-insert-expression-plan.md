# Task12 native INSERT scalar-expression implementation

Required workflow: complete canonical source and regression before application edits; mechanical exact transfer; fresh SPEC review then fresh QUALITY review. This remains an unselected component of the approved database redesign.

## Source and scope

The frozen source statements in `../specs/2026-10-16-feedback-insert-expression-source.json` are the actual `upsert_feedback_event`, `create_feedback_processing_batch` and `resolve_feedback_event` feedback item INSERTs. They query retained feedback_events or evaluate current_timestamp in the original INSERT. Preparing scalar values with a separate SELECT would change execution timing. The existing INSERT-parts interface accepts bound values only.

Extend `task12_insert_parts` with `insert_sql` and ordered `insert_parameters`, using the existing exact native scalar codec. `_payload` includes those expressions directly in its final JSON construction and omits their defaults. Expressions cannot replace source identity or a public-key dependency used to resolve a physical parent. This restriction comes from existing finite field/parent metadata; it adds no business field or execution policy. Caller INSERT/UPSERT and commit ownership remain intact. Existing no-expression callers retain their SQL/parameters and point-writer behavior.

Only two existing functions change: `_payload` and `task12_insert_parts`, plus the existing TASK20_PARENTS import. No caller, constructor, schema, migration, codec, allocator, current connection hook or production behavior changes.

## Complete transfer artifacts

- Candidate `2026-10-16-task12-insert-expression-candidate.py` → `app/task12_storage.py`, SHA256 `7174fc6da84370fda1eda1733fd4e3337cae0bd0a7c776a665964efcc79c8833`.
- Regression `2026-10-16-task12-insert-expression-regression.py` → `tests/test_task12_insert_expression.py`, SHA256 `1b621ea878d5be564eb0c1e85b8d4d9c69296b7fb3cab7dfa8443d823695f8c2`.
- Preflight `2026-10-16-task12-insert-expression-preflight.py`, SHA256 `ce7570a7ccb776332f1e6fd21cf8a7357df86e432aa8b8b0f9dc823ce5809ed2`.

The preflight copies source/tests to an owned temporary directory, substitutes the candidate by default, or installed source with `--applied`, and removes its copies. `--with-declarations` includes the existing Task12/native-parts, Task8/allocator and Tasks20 focused suites. It uses the pinned Python3.13 runtime. No full service suite or source database is used.

## Verification and implementation

- [x] Freeze actual three source SQL values expressions and owners without source row bodies.
- [x] Complete candidate, regression and preflight before any application edit.
- [x] Full candidate scoped preflight1053passed/64.50s, including42new cases; Ruff clean; owned temp removed. Initial fixture errors (invented weight field corrected to actual objective_weight, existing NOTNULL-to-CHECK extended code distinction) corrected before acceptance; no application defect asserted.
- [ ] Mechanical exact-copy of the two canonical files, own claim and implementation checkboxes only; applied full scoped preflight and Ruff; commit.
- [ ] Fresh SPEC: verify real source statements, native execution timing, lazy CASE/default suppression, scalar types, identity dependency representation and unchanged no-expression path; no runtime selection.
- [ ] Fresh QUALITY after SPEC: meaningful mutation controls for eager evaluation, duplicate evaluation, exact scalar encoding and omitted expression/default; independent restored control. Do not count missing API or collection failures as semantic kills.
- [ ] Root records acceptance and current integration boundary.

The 42-case new regression compares actual/fresh source declarations. It executes the actual three INSERT VALUES clauses, checks preparing before feedback_events exists and observing facts inserted later, lazy CASE, once-only exact REAL arithmetic, constraint failure preserving caller work, and identity/duplicate validation before write. Full UPSERT clauses and real feedback public-method behavior remain a separate caller proof. Native NOT NULL failures are expressed by existing target CHECK constraints: both reject and preserve caller work, but their extended SQLite error codes intentionally differ; this test does not claim extended-code equality.

No main merge, push, production DB write, restart or deploy. Complete caller/constructor/migration/current-fact inverse/predecessor/native CLI/UI and final ten core tables plus Email ledger remain required.
