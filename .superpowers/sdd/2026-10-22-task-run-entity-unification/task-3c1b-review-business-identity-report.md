# Task3C1B implementation report

## Result

Implemented the bounded, unactivated Review identity ownership correction on top of `5912b0fcde6b151b95b2dec60000bedf6403a395`.

`run_adopted_reviews.id` is now an ordinary `INTEGER PRIMARY KEY` carrying the source Review business/protocol identity. Its `audit_run_id` is `NOT NULL UNIQUE REFERENCES runs(id)`. `run_adopted_results.predecessor_review_id` references the Review child ID. The predecessor owner trigger resolves the Review row to its Audit Run. The Review source field map carries the preserved child ID through `run_adoption_identity_map.target_child_id`; selection/progress continue to carry Audit Run ownership.

The native Store now uses Review IDs for candidate predecessor reads/writes and Review lookup/selection inputs, returns both `review_id` and `audit_run_id` from current/history/selection/progress projections, and stores newly adopted reviews with SQLite's ordinary rowid allocator. Repeated review adoption still resolves by unique Audit Run owner and validates exact body/candidate binding. Orchestrator stage and revision context now receives the real Review key via the existing Store projections.

The source-backed frozen proof records 23 linked candidate bodies: 23/23 equal source Review public IDs, 23/23 differ from the owning Audit Run IDs, and there are zero unresolved or mismatched IDs. Therefore the earlier `inverse-predecessor-wire-contract-read.md` value `9007` is only a prospective native-writer example; it is not a historical source body requiring repair.

Regenerated the exact source-to-target DDL and field-map artifacts for adoption and the complete Task/Run assembly. Updated the approved design/plan clarification, architecture/runtime behavior notes, and validation record. No source-copy migration, constructor activation, provider operation, production change, push, or deployment was performed.

## Focused validation

Command:

```text
pytest -q tests/test_run_adoption_definitions.py tests/test_native_reviewed_candidate_store.py tests/test_task_run_entity_assembly.py
```

Output: `26 passed in 5.95s`.

This covers the distinct Review child ID/Audit Run owner FK shape, source-compatible predecessor lookup, native review write/idempotence/conflict behavior, current/history/selection projections, orchestrator `_business_revision` and `_stage_for_parent` context, generated DDL parity and catalog execution, and unchanged Task/Run assembly constraints.

Command:

```text
ruff check app/run_adoption_definitions.py app/reviewed_candidate_store.py app/agent_orchestrator.py tests/test_run_adoption_definitions.py tests/test_native_reviewed_candidate_store.py tests/test_task_run_entity_assembly.py
```

Output: `All checks passed!`.

`python -m compileall -q app/run_adoption_definitions.py app/reviewed_candidate_store.py app/agent_orchestrator.py` passed. `git diff --check` passed.

## Additional test boundary

A broader exploratory run of `pytest -q tests/test_agent_orchestrator.py tests/test_agent_orchestrator_feedback_exhaustion.py tests/test_native_reviewed_candidate_store.py` yielded 13 passed and 32 failures. The failing legacy orchestrator fixtures stop during task setup because their initialized database has no `task_inputs` table (`sqlite3.OperationalError: no such table: task_inputs`); those failures occur before the changed Review projection path. The dedicated native Store test does exercise both orchestrator context methods and passes. No full suite was run.

## Remaining gates

This is an unactivated schema/Store slice. Source-copy conversion, source inverse round-trip, genuine old-code cold-open/read-write/reforward acceptance, broader execution/full-History caller closure, independent SPEC/QUALITY review, production publication and deployment remain later gates. The report does not claim those outcomes.
