# Task2D7B Workbench Turn native execution report

Base: `b63b5f45b6a71a837fd3c97d1222b4875028badc`
Head: `49acb2c9c` (local commit).
Scope: native Workbench Turn lifecycle, event/artifact readers and writers, exact RuntimeAttempt workload validation, public API identity parsing, and the exact typed `artifact_created.payload.artifact_id` converter handoff. No constructor activation, full-suite/provider execution, production database, push, or deploy.

## Owner and identity decisions

- Workbench parent identity remains its native `workbench_tasks.id`; Workbench Turn public identity is canonical decimal text for `tasks.id`, whose kind is `workbench_turn`. Turn-specific fields remain on `workbench_turns.task_id`; common status and lease facts remain on `tasks`.
- Workbench Turn events are native `workbench_events` keyed by its Task FK. Artifact rows are native `workbench_artifacts` keyed by their integer PK and native Turn FK. Artifact DTO and event `artifact_id` use the canonical decimal artifact ID at runtime.
- Runtime history reads only `runtime_attempts` rows with `workload_kind='workbench'` and exact canonical Turn Task ID as `workload_key`; no professional/logical Run or UUID runtime alias is created. Existing source-session/transcript hydration remains selected-attempt based.
- The actual source/current artifact reference is translated only for `artifact_created.payload.artifact_id`. Forward uses the Task typed public identity map; inverse uses explicit `workbench_artifact_identities` handoff, validates the native artifact and its Turn owner, and restores the actual source public ID. Other payload values pass through unchanged. Existing opaque source public IDs are preserved; UUIDs are allocated only for new native artifacts during explicit inverse.
- Feedback item association callers accept optional empty Workbench references as before. Nonempty public references must be canonical decimal strings. Routes that already require both references remain strict.

## Changed files

- `app/workbench/store.py`: native Task/Turn lifecycle, lease and recovery operations, native event/artifact CRUD, timeline and aggregate readers.
- `app/workbench/native_events.py`: native Turn and Workbench RuntimeAttempt hydration.
- `app/workbench/api.py`, `app/store.py`: canonical route/cursor IDs and runtime workload key/parent validation.
- `app/task_entity_schema.py`, `app/run_entity_schema.py`: explicit artifact identity inverse handoff and typed payload reference conversion.
- `app/web_api/registration.py`: canonical feedback references while preserving optional empty associations.
- `app/workbench/models.py`: representable historical `waiting_confirmation` state.
- `frontend/src/types.ts`, `frontend/src/api.ts`, `frontend/src/app.tsx`, `frontend/src/presentation.ts`: parse, count, and label historical `waiting_confirmation` rows as read-only status.
- `tests/test_workbench_native_parent.py`, `tests/test_task_entity_schema.py`, `tests/test_run_entity_schema.py`, `tests/test_console_feedback_iteration_api.py`: assembled target, identity, and feedback compatibility coverage.
- `docs/architecture.md`, `docs/runtime-mechanism.md`: current native runtime ownership and artifact reference behavior.

## Verification

Passed:

```text
python -m pytest -q tests/test_workbench_native_parent.py tests/test_console_feedback_iteration_api.py tests/test_task_entity_schema.py::test_native_input_new_fact_below_global_water_keeps_canonical_id_on_reforward tests/test_run_entity_schema.py::test_task_first_new_run_facts_survive_old_write_and_two_json_cycles tests/test_run_entity_schema.py::test_artifact_created_uuid_reference_survives_native_forward_and_inverse
18 passed in 17.12s
```

```text
python -m compileall -q app/workbench/store.py app/workbench/native_events.py app/workbench/api.py app/store.py app/task_entity_schema.py app/run_entity_schema.py app/web_api/registration.py
```

```text
git diff --check
```

`cd frontend && npm ci` installed the locked local frontend dependencies. `cd frontend && npm test -- --run src/api.test.ts` passed (1 test file, 10 tests). `cd frontend && npm run build` passed TypeScript checking and Vite build. Generated `app/static/workbench/` output is ignored and not included. The native Workbench test also calls the canonical-ID artifact download route and checks the returned bytes; the migration test starts with a genuine UUID source `artifact_created` reference, checks the native canonical artifact link and same Turn owner, then verifies inverse restores that UUID and unrelated JSON payload value.

A broader focused run of `tests/test_workbench_native_parent.py`, `tests/test_task_entity_schema.py`, `tests/test_run_entity_schema.py`, and `tests/test_console_feedback_iteration_api.py` initially reported 110 passed and 3 failed. Two failures were expectations that needed updating for the newly explicit artifact identity handoff and the native canonical Workbench runtime key; both corresponding targeted tests pass after those corrections. The remaining failure is `tests/test_task_entity_schema.py::test_new_native_email_task_inverse_uses_predecessor_runtime_identity`, which asserts the Email classification hash key is accepted by the current runtime validator; it is outside this Workbench scope and remains unmodified.

Not run: full test suite, provider/CLI effects, actual constructor activation, production database, parent-owned Task5 CLI gate, deploy, push, and independent full `BASE..HEAD` SPEC/QUALITY review.

## Handoff

The change is committed locally for parent review. The parent owns full-range independent SPEC/QUALITY review and any release decision. The checkout's two pre-existing unrelated dirty SDD report files were preserved and are not included in this commit.
