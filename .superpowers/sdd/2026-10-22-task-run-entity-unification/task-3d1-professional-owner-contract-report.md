# Task3D1 — professional RuntimeAttempt owner contract

## Scope and result

Implemented the bounded, unactivated Task3D1 schema and converter slice. Existing selected-current source declarations and their lease rules are unchanged. The native target permits an optional `runtime_attempts.parent_id` Run FK for non-`agent_run` workloads. The target CHECK still requires a matching non-NULL parent/key for `agent_run`; active non-Agent attempts require a nonempty operation lease regardless of parent; the active-lease index now selects non-Agent attempts by workload kind. Workload/attempt and active-route uniqueness remain keyed by `(workload_kind, workload_key[, route_name])`; no Run/ordinal uniqueness or business column was added.

Forward conversion derives the parent only from the already typed workload identity: Task Agent plain decision and `decision_repair.N` keys map to the canonical Task Agent Run, and Meeting keys map to the canonical Meeting Run. This follows both physical Run remaps and public-to-canonical workload-key conversion. Task memory/deadline backfills and other operation kinds remain unowned. Two repair keys targeting one Run each retain their own ordinal-1 identity.

The inverse leaves source non-Agent `parent_id` NULL and restores the original workload public key. Before any schema mutation it checks that every native typed Task/Meeting key still has its exact derivable parent (including refusing a missing parent), and refuses a non-Agent owner the selected source cannot represent. Tests cover unknown structured ownership and Task key/parent mismatch refusal. This prevents a future native-only link from being silently erased or manufactured by re-forwarding.

The field map now describes `agent_runtime_attempts.parent_id` as owned by the typed Task/Meeting workload key or the source fact. The deterministic Run SQL and assembled Task/Run SQL and assembly field-map artifacts were regenerated; other Run map fields remain byte-unchanged. Architecture and runtime-mechanism docs record the unactivated boundary.

## Producer/runtime boundary

No caller, router, Store lifecycle, constructor, migration activation, provider, production checkout, main branch, push, or deploy was touched. Router and Store still use `agent_run_id`/NULL as an operation discriminator in runtime paths; downstream runtime work must switch those decisions to `workload_kind` before enabling linked operation attempts. Existing source lease triggers and source factories remain frozen migration oracles.

Historical structured keys identify OKR request Tasks, not professional Runs. The current OKR producer retains `result.attempt_id` only inside the routed result and creates its professional Run after result validation. A later producer unit must carry that exact attempt ID through the structured result, retain the current CLI → validation → Run-create ordering, and add portable inverse-owner handoff for the native-only structured parent relationship. D1 does not create or infer such links.

## Verification

Commands run in `/Users/derek/.codex/worktrees/task-run-entity-phase2/ceo-agent-service`:

- `pytest -q tests/test_run_entity_definitions.py tests/test_run_entity_schema.py tests/test_run_entity_storage.py tests/test_task_run_entity_assembly.py` — **69 passed**.
- `ruff check app/run_entity_definitions.py app/run_entity_schema.py tests/test_run_entity_definitions.py tests/test_run_entity_schema.py` — **All checks passed**.
- `python -m compileall -q app/run_entity_definitions.py app/run_entity_schema.py tests/test_run_entity_definitions.py tests/test_run_entity_schema.py` — **passed**.
- `git diff --check` — **passed**.

The focused tests include native DDL lease checks with and without an optional parent, ordinary completed-unowned attempts, unchanged Agent Run parent/key rejection, exact source forward/inverse behavior, Task/Meeting canonical Run remaps, two JSON-portable inverse/reforward cycles, independent repair-key ordinals, a newly created native Task Agent Run plus operation attempt across inverse/reforward, and rejection of unrepresentable current parent facts before schema replacement. Fail-first evidence: running `pytest -q tests/test_run_entity_schema.py::test_professional_attempt_owner_survives_two_json_inverse_reforward_cycles` with `app/run_entity_definitions.py` and `app/run_entity_schema.py` restored from pinned base `bafff3ee849d48f0ef163b62011a7350e57a03c1` produced **1 failed** because the re-forwarded Task attempts had `parent_id=NULL`; the same test passes with this implementation.

The root-owned dirty C2A report was preserved and not staged. Independent SPEC and QUALITY review, full runtime caller conversion, constructor activation, cold-open/source-copy acceptance, and deployment remain subsequent gates.
