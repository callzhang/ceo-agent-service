# Task3C progress Selection parity report

Date: 2026-10-23
Base: `7702c6389` (worktree branch `codex/task-run-entity-phase2-20261022`)

## Finding and change

The prior native progress row stored `selected_audit_run_id`. That value was derived from the selected source Selection's Review and could not identify which Selection business child was selected. The source Action declaration has immediate existence foreign keys for `execution_review_id` and `execution_selection_id`, plus deferred composite owner bindings. Thus a cross-Review pair of existing rows may be temporarily inconsistent inside a transaction, but may not commit; missing Review or Selection rows fail immediately.

`run_execution_progress` now stores the actual Selection business ID in the same existing nullable field position as `selection_id`. The DDL keeps immediate references to the approved Audit Review and actual Selection row, and adds deferred composite references for `(consumer_run_id, approved_audit_run_id)` to the adopted Review and `(selection_id, consumer_run_id, approved_audit_run_id)` to the Selection. The Selection exposes the corresponding unique parent key. The existing unique Consumer Selection constraint remains the source rule; no redundant unique index was added. Status, lease, result, clocks, receipt reference, and persisted field count are unchanged.

Forward adoption translates the source Selection physical reference to its actual business ID. Current-fact inverse resolves that exact current Selection ID back to its planned source physical reference, and rejects a missing or mismatched Consumer/Audit owner before materialization. The current candidate execution reader joins progress to the Selection by `selection_id`. No runtime claim writer or review policy changed.

Deterministic artifacts were regenerated through the existing definition/assembly serializers: `docs/run-adoption-target.sql`, `docs/task-run-entity-assembly-target.sql`, and both field maps. `docs/architecture.md` and `docs/runtime-mechanism.md` now describe the unactivated parity contract.

## Evidence

Regression coverage demonstrates that an actual Selection identity is stored, an existing-but-temporarily-mismatched Audit/Selection pair can be restored before commit, a final mismatch fails at commit, and missing Selection existence remains immediate. Actual-source forward coverage checks source Selection 18 becomes native `selection_id=18`; the current reader test checks the projected Selection ID. Existing source conversion/inverse planner and assembled catalog tests ran in the focused set.

Focused command:

```sh
pytest -q tests/test_run_adoption_definitions.py tests/test_run_adoption_schema.py tests/test_run_adoption_inverse.py tests/test_native_reviewed_candidate_store.py tests/test_task_run_entity_assembly.py
```

Result: **51 passed in 19.42s**. Ruff on the touched application and focused test modules passed. Targeted `compileall` and `git diff --check` passed. The previously accepted 267.43-second full frozen current-inverse checkpoint was not repeated; broader materialization, old-code cold-open/reforward, independent review, runtime activation and release remain outside this bounded change.

## Scope boundary

No constructor activation, policy, runtime writer, migration execution, production checkout, provider, main branch, push or deploy was performed. Root-owned dirty `task-3c2a-adoption-forward-report.md` was preserved and excluded from this change.

## Fix1/5 — actual nonempty Selection inverse/reforward

The actual-source inverse test now continues beyond the existing physical-source restoration check. It captures the restored current Task, Run, and Action adoption declarations; JSON-serializes and deserializes the Task inverse identity and Run handoff; then performs a fresh Task, Run, and adoption forward conversion. The same fixture carries selected source Selection 18, Review 7, Consumer/Audit source Runs 9006/9007, and two current receipts.

The new assertions verify native progress stores `selection_id=18`, Selection 18 and Review 7 still point to the same remapped Consumer/Audit pair, candidate digest/body and Review result remain unchanged, receipt identity/result/digest/status/time fields are preserved, and each source Agent Run's projection and optional provenance fields have the same native projection/provenance readback. The fixture has NULL Task Agent provenance slots, so the roundtrip explicitly verifies that absence remains absent. Both `foreign_key_check` and `integrity_check` are clean after the fresh forward.

Covering command and result:

```sh
pytest -q tests/test_run_adoption_inverse.py::test_actual_task_run_then_adoption_inverse_restores_source_action_catalog_and_facts
# 1 passed in 2.37s
ruff check tests/test_run_adoption_inverse.py
python -m compileall -q tests/test_run_adoption_inverse.py
git diff --check
```

Ruff, targeted test compilation, and diff checks passed. No product code changed in this follow-up. The first version of the provenance assertion expected a `run_provenance` row for a generic Reply Task Agent Run; the actual fixture correctly has no professional provenance row. The final assertion compares the source's exact nullable projection/provenance slots to the native projections and preserves the absence. This was a fixture expectation correction, not a product change.
