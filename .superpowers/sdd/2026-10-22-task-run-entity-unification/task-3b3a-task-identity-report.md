# Task 3B3A: Task source-membership and Email identity

Status: bounded implementation complete; this is not overall Task 3B3 completion.

## Change

Task2B forward metadata now adds `source_id_ranges`, a JSON-serializable mapping from every Task source kind to sorted, coalesced `[first_physical_id, last_physical_id]` integer ranges. It stores only exact source physical-ID membership. The inverse consults membership instead of treating every canonical ID at or below a per-kind high-water as a retained source owner. This distinguishes a current native owner at a source gap from an original row even when its ID is below that kind's maximum. The Run converter's Task-metadata validator requires and validates this exact mapping before resolving any Task edge.

For a new native Email classification Task, Task inverse preserves the source table's independent numeric `legacy_key` allocator and derives the source `task_id` public identity from the retained native `stable_message_identity` using the existing producer formula `email-classification:` plus lowercase SHA-256 hex. Existing Email Task identities continue to use their captured mapping. The test calls the predecessor's `_validate_runtime_operation_workload` with the restored identity.

## Evidence

Focused tests run with unique pytest `--basetemp` directories:

- `pytest tests/test_task_entity_schema.py tests/test_run_entity_schema.py`: **42 passed** (26.90 seconds).
- The new source-gap case inserts native `reply_task_inputs.id=204`, absent from the source membership but below the source owner high-water 206; inverse allocates a source physical ID above the captured global Task sequence and emits the canonical identity handoff.
- The new Email case verifies the exact SHA-256 public identity in the restored source projection, preserves numeric `legacy_key`, and passes the predecessor workload-key validator.
- `python -m ruff check app/task_entity_schema.py app/run_entity_schema.py tests/test_task_entity_schema.py tests/test_run_entity_schema.py`: passed.
- `git diff --check`: passed.

## Limits and next work

The focused source-gap fixture establishes the allocator behavior but does not yet compose the frozen missing `runs(190042) -> tasks(404225)` anomaly with the full source database. The parent will verify that case before accepting this subunit.

This commit does not implement native new-fact support for all eight Run owner families, Run/global-versus-owner allocator preservation, Run `source_context`/`inverse_identity`, Task-first Run inverse composition, Run old-writer/reforward cycles, or Run insert-delete water preservation. It does not claim frozen full-database, cold-process, CLI, runtime, capacity, migration, or release acceptance. Those remain follow-on B3 units under the approved scope.
