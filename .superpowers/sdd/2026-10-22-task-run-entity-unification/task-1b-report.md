# Task1B implementation report

**Status: DONE**  
**Implementation commit:** `38a8793cc` (`Add unactivated Run entity contract`)

Implemented the Run-side declarations as a pure, unactivated module in `app/run_entity_definitions.py`. It defines logical `runs`, separate `run_provenance` and adopted projection ownership, one-to-many and standalone `runtime_attempts`, service-produced `run_failures`, `schedule_occurrences`, and the four narrow domain record owners. The target uses native SQLite affinity and nullable facts; it adds no synthetic roles, statuses, revisions, or CLI invocations.

The deterministic field map covers all 79 declared source columns for each of the eight source kinds (632 kind/field entries). It records the source SQL type, nullability, default, explicit structural NULL dispositions, typed public identity, and source public-key numeric ordering metadata. The retirement note preserves the inherited `runs.id=190042` missing-Task reference (`task_id=404225`, `job_id=4908`) as a migration exception. It does not manufacture a Task or copy trajectories. The target SQL and identity metadata remain documentation/contracts; no runtime activation or migration was added.

Focused coverage verifies field-map/artifact determinism and metadata against the source projection DDL; natural nullable Run facts; separate source provenance and adopted results; valid Run parent and Task/source-kind relationship; 1:N resumed/fresh attempts and a standalone invocation; attempt ordinal uniqueness, immutable lineage and standalone active lease requirements; pending-only dispatch index versus full-state claim index; scheduled event/instant and Workbench event uniqueness; typed Workbench Task references; and same-number public keys across source kinds.

**Validation:** `/Users/derek/miniforge3/bin/pytest -q tests/test_run_entity_definitions.py` — 5 passed. Scoped Ruff and `git diff --check` passed. No whole-suite run, migration, production write, provider call, or runtime wiring occurred.

**Remaining coordination:** Task1C must own candidate/action and other Run references. Phase2 assembly still needs to combine this module with Task1A and other target-domain prerequisites, review source constraint/index parity and inherited FK handling, and complete the separately approved copy/inverse/old-code acceptance gates. This commit does not claim those phases or live behavior.
