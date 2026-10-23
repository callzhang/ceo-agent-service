# Task 2D8R — Feedback semantic reference completion

Date: 2026-10-23
Base: `8738b7c129f0359689941fb729fe5a8e6167e127`
Scope: explicit converter and inverse work only. Constructor activation, runtime cutover, provider/CLI use, production, main, push and deploy were not performed.

## Finding and repair

The frozen probe at `feedback-reference-probe.md` established the first Task-conversion divergence: Feedback item and round Workbench refs followed the typed Workbench owner handoff, while `feedback_iteration_decisions.workbench_task_id` and `.workbench_turn_id` stayed at source strings. It also showed an item `current_round_id` retained its source public value after the target round owner had a different native ID. The existing adoption path already translates decision-item `fb_decision_id` and `fb_round_id`; this work leaves that path intact.

Task forward conversion now applies the typed Workbench public-owner translator to decisions as well as items and rounds. It translates item `current_round_id` only through the exact typed Feedback round public index. NULL and integer zero pass through; unresolved integer pointers receive sparse per-row inverse markers, preventing a coincident native round ID from changing their meaning. Decision/item/round Workbench NULL and exact unresolved text values are retained by the existing sparse exception path. The frozen source declaration admits BLOB and integer inputs for decision Workbench reference fields while rejecting NULL for those required fields. Conversion maps only actual TEXT values; BLOB and other non-TEXT SQLite facts pass through unchanged, and inverse does not coerce them into owner lookups. Python input type alone does not establish stored SQLite type because TEXT affinity can normalize integer input.

The pointer conversion checks the actual SQLite value type before lookup. A noninteger raw value is left as stored even if its text resembles a native round ID; inverse likewise resolves only an exact integer native value. A focused helper regression covers nullable Workbench references and a noninteger pointer string.

The composed adoption converter showed Feedback transition facts are stored as a positional `payload_json` array. It now translates `round_id` through the exact typed round owner map and translates the two Workbench string entries using the frozen `FEEDBACK_FIELDS` declaration and Task public-owner map. Known source strings become canonical native owner IDs encoded as JSON strings. Unresolved strings and integer round IDs remain unchanged and receive sparse exception metadata keyed by the physical Action row and field, so a numeric-looking source string such as `"1202"` cannot be mistaken for a mapped native owner on inverse. Empty association strings and non-TEXT JSON facts pass through unchanged. The adoption inverse accepts this exact optional handoff and restores only these references. Other payload members, JSON typing, decision-item mapping, Action IDs, sequences and unrelated Actions remain on their existing paths.

`attempt_id` and `agent_run_id` remain independent association facts. The confirmed processing-item/round writer evidence is route input passthrough; frontend projections use attempt identity for the ReplyAttempt link and agent-run values as labels. No typed Run owner was established for these processing association fields, so the converter does not remap them or synthesize Run/RuntimeAttempt owners.

## Verification

The fail-first probe output is preserved in `feedback-reference-probe.md`. The new Task regression now passes two portable Task forward/inverse cycles with Workbench task 202→1202, Workbench turn 203→1203 and round public ID 229→native 1229; it asserts exact decision/item references and source values after inverse.

The composed adoption regression uses the real Task→Run→adoption converter order with Workbench and round owner remaps. It verifies transition round `229→1229`, mapped and unresolved Workbench payload fields, including unresolved numeric-looking `"1202"` and empty strings; writes a new native transition through the existing `insert_feedback_action` API; runs the actual inverse; and performs a fresh portable forward. Both captured and newly written transition refs return to the proper source/canonical identities.

Commands and results:

- `pytest -q tests/test_task_entity_schema.py::test_feedback_workbench_and_current_round_refs_follow_typed_handoff_two_cycles --disable-warnings --maxfail=1` — passed.
- `pytest -q tests/test_run_adoption_inverse_rows.py::test_adopted_feedback_transition_workbench_refs_roundtrip_with_typed_task_handoff --disable-warnings --maxfail=1` — passed.
- `pytest -q tests/test_task_entity_schema.py tests/test_run_adoption_schema.py tests/test_run_adoption_inverse_rows.py -k 'not test_new_native_email_task_inverse_uses_predecessor_runtime_identity' --disable-warnings --maxfail=1` — 57 passed, 1 deselected.
- The same selected files without the exclusion stopped at `test_new_native_email_task_inverse_uses_predecessor_runtime_identity`: unchanged `app/store.py` rejects its hashed Email workload key as not a persisted task ID. That method/file is outside this change; no Task2D8R assertion failed in that run.
- `python -m compileall -q app/task_entity_migration.py app/task_entity_schema.py app/run_adoption_schema.py app/run_adoption_inverse.py` — passed.
- `ruff check app/task_entity_migration.py app/task_entity_schema.py app/run_adoption_schema.py app/run_adoption_inverse.py tests/test_task_entity_schema.py tests/test_run_adoption_schema.py tests/test_run_adoption_inverse_rows.py` — passed.
- `git diff --check` — passed.

The full service suite, real native CLI/providers, schema constructor activation, runtime callers, production readback and release gates remain unrun by design and outside this task.

## Files and handoff

Changed: `app/task_entity_migration.py`, `app/task_entity_schema.py`, `app/run_adoption_schema.py`, `app/run_adoption_inverse.py`, the three focused converter test files, `docs/architecture.md`, `docs/runtime-mechanism.md`, this report, and the Task 2D8R claim row in `docs/agent-claims.md`.

The unrelated pre-existing dirty reports `task-2d0-reply-claim-input-report.md` and `task-3c2a-adoption-forward-report.md` were preserved. The code implementation commit and final worktree HEAD are supplied in the parent handoff; the report-file commit is not embedded as a self-reference.
