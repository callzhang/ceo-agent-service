# Task1 assembly report — 2026-10-22

## Implemented

- Added `app/task_run_entity_assembly.py` to deterministically join Task, Run, adopted-result, and revised receipt declarations, rejecting duplicate schema-object names.
- Preserved `run_projections` as the professional Run projection owner while adoption rows live in `run_adopted_results` and related typed tables.
- Added the OKR item parent FK to `tasks(id)` plus kind guards for `okr_review`; this follows the verified public-to-physical mapping (source request public IDs 1, 5, 6, 8–24 map to Task physical IDs 404036–404055), not numerical coincidence.
- Added target query indexes for source email-classification, feedback-processing, WeChat-import, and Workbench access paths, plus Task-kind immutability.
- Fixed `tools/inspect_task_run_entities.py` to report `PRAGMA index_list` names (`row[1]`) and added a multiple-index regression.
- Replaced the impossible kind-mutation in the Run provenance test with a valid wrong-kind provenance rejection.
- Updated dated Task/Run and production-observation wording in the validation, architecture, runtime and business-progress documents. Corrected the historical `runs(190042) -> tasks(1)` note: `1` is the FK ordinal from `foreign_key_list`, not evidence of a missing parent.

## Focused verification

`python -m pytest -q tests/test_task_run_entity_assembly.py tests/test_task_run_entity_definitions.py tests/test_run_entity_definitions.py tests/test_run_adoption_definitions.py` — 32 passed.

New tests cover duplicate-free assembly names, DDL execution with prerequisite table shapes, correct and wrong-kind OKR parents, immutable Task kind with mutable status, and inspector index names. The assembler test's Action/config/schedule prerequisites are deliberately only structural placeholders; that test proves the composed contracts parse and create under those shapes, not full Phase 1 source parity.

## Still open; no completion claim

This is not complete Task1 final acceptance. The exact retained Action guards from `task-1c-report.md` still need translation and positive/negative execution against the assembled native Action and Task/Run owners. The full assembled DDL has not yet been exercised with actual current Phase1 Action, config, State, Email, provenance and native prerequisite declarations. Task-domain index/default/nullability coverage still needs source-paired validation beyond the called-out access paths. The deterministic complete field map, retirement artifact and current inspector comparison also need final integrated validation. The known three retired `first_agent_run_id` fixture failures remain Task3 work and were not skipped or counted as green.

No migration, runtime activation, provider operation, production access/write, push, or deploy was performed.
