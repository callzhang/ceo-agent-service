# Task3C2B2 adopted Action row builder report (2026-10-23)

Status: implemented as an unactivated, in-memory inverse helper. It does not perform Action DDL changes, writes, receipts/observer restoration, cold open, or constructor activation. This report and the source Actions oracle do not claim full-database foreign-key closure or genuine old-code cold-open/reforward acceptance.

## API and source ownership

Added `adopted_action_source_rows(db, source_metadata, forward_metadata, *, run_inverse_metadata, identity_plan=None)` in `app/run_adoption_inverse.py`. It requires the caller-owned FK-off transaction used by the existing inverse helpers. If a plan is supplied it reuses it; otherwise it calls `plan_run_adoption_inverse`. It returns complete source `actions` stored-column dictionaries for only `review_candidates`, `candidate_reviews`, `candidate_selections`, `candidate_supplements`, and `candidate_execution_alias`.

The builder takes the source Actions stored-column set from the captured Actions catalog. It uses current selected `current_action_payloads()` layouts and record-type codes, the existing native SQLite scalar codec, and `jsonb(?)` packing. Candidate run generation, task, proposal revision, and Consumer physical Run ID come from that Candidate's owning source Run through the actual Run inverse handoff. Current adopted result/review/selection/supplement/progress rows supply their current immutable JSON, digests, decisions, instructions, states and timestamps. Source physical references are resolved through the assigned planner IDs: Review points to the Candidate and its source Audit Run; Selection points to Candidate and the Review owned by the same Consumer/Audit pair; predecessor Review and progress references use the same physical owner maps. Execution fields live in the Candidate payload and are paired with the current source execution alias. A missing selected progress Review/Selection reference raises before returning rows.

No source `candidate_executions` or `candidate_action_attempts` row is returned. Selection `branch_json` remains NULL because it is not stored by the selected source declaration. Unrelated nullable source columns are NULL. Source public keys remain the planner's raw value/type, including review `"7"` with source Audit Run physical ID `9007`.

## Evidence

Fail-first evidence: on the task base `96b4a7b3c2fe41602739629afdca3bf235fee17c`, `git show 96b4a7b3c2fe41602739629afdca3bf235fee17c:app/run_adoption_inverse.py | rg adopted_action_source_rows` returned no match (exit 1). The new builder regression exercised the actual C2A source fixture, Task inverse, Run inverse, planner and builder; it checks the native Action and sqlite_sequence snapshots before/after and would fail because the API did not exist on that base.

Green focused command:

```text
python -m pytest -q tests/test_run_adoption_inverse_rows.py tests/test_run_adoption_inverse.py
............                                                             [100%]
15 passed in 17.02s
```

The focused tests cover the actual Review `"7"` / source Audit Run `9007` split, complete source columns, two supplements, current execution alias pairs, physical cross-references, unchanged native Actions/sequences, Task generation advanced while the owning Run keeps its historical generation, a current Action occupying a mapped adopted physical ID, a new Review paired with a Run created through `create_run` before Task/Run inverse, current progress edits, and supplement addition/retirement. The new test Review uses a completed current Audit Run cloned from complete existing Run facts with a distinct native turn attempt; it makes no claim of a real CLI invocation. The source constraint oracle serializes the small in-memory current fixture into an independent in-memory database, translates retained rows with B2A, installs the exact captured source Actions catalog, inserts retained and adopted rows, and passes `PRAGMA foreign_key_check(actions)`. It therefore checks the captured source Actions CHECK/FK contract for this fixture. It deliberately does not assert full-database FK closure or other native-in-transition tables.

Other checks:

```text
python -m ruff check app/run_adoption_inverse.py tests/test_run_adoption_inverse_rows.py
All checks passed!

python -m compileall -q app/run_adoption_inverse.py tests/test_run_adoption_inverse_rows.py
# exit 0

git diff --check -- app/run_adoption_inverse.py tests/test_run_adoption_inverse_rows.py docs/architecture.md docs/runtime-mechanism.md docs/agent-claims.md
# exit 0
```

The API remains unused by runtime constructors. Materialization, receipts/observers, complete C2B/C2C composition, broader database closure, and genuine old-code cold-open/reforward remain separate work.
