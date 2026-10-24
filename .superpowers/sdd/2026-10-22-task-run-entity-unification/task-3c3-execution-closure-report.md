# Task 3C3 execution closure

Implemented the native runtime writer closure on base `f666b9965519901f471c8332d8aa2a1d1161b66c`. Execution progress is now owned by the Consumer Run in `run_execution_progress.consumer_run_id`; action attempts and their direct receipt references use `action_receipts.execution_run_id`. The existing execution DTO `id` stays the Consumer Run ID. The incoming Review business ID remains the Review ID, progress stores the resolved approved Audit Run ID, and Selection remains its own ID.

## Runtime path

`ReviewedCandidateStoreMixin.claim_candidate_execution`, `renew_candidate_execution_claim`, `_leased_execution`, `_owned_execution`, `finish_candidate_execution`, `begin_candidate_action`, `get_prior_candidate_action_attempt`, `record_prior_candidate_action_reconciliation`, `record_candidate_action_outcome`, `list_candidate_action_attempts`, `record_candidate_external_action`, `get_candidate_external_action`, `get_verified_action_source`, `list_verified_candidate_actions`, and `list_human_decision_evidence` now read/write the native adopted-result, progress, receipt and current-TaskInput tables. Native CRUD is implemented by `create_native_execution` and `update_native_execution` in `app/execution_owner_storage.py`.

The shared receipt lifecycle accepts a declared owner column for reviewed action attempts, outcomes, provider receipt association, and canonical provider metadata. The native reviewed execution caller explicitly passes `execution_run_id`; source Action paths retain their declared `execution_id` association. No runtime schema detection or alias lookup was introduced. `create_plan_execution` and `update_plan_execution` remain in `app/execution_owner_storage.py` only as explicitly documented frozen source-fixture/migration-oracle helpers.

`SystemExecutor` needed no API edits: it passes the claimed row's existing `id` through renew, begin, outcome, provider-receipt, reconciliation, and finish calls. Its native fake-effect integration verifies that the same Consumer Run ID reaches progress and receipt ownership and that a late provider result remains attributable after candidate invalidation during dispatch. The normal runtime execution path no longer creates or queries `candidate_execution_alias` or source `candidate_execution`/`candidate_action_attempts` projections.

Historical provider-refusal matching now joins `runs -> tasks -> task_inputs` using `tasks.current_input_id`, matching each Task's current business key. The test demonstrates that an old key A on a Task now pointing at B does not block a current A candidate, while the current B key is blocked. Selected `skipped` and `no_action` stops continue to bypass this refusal check.

## Verification

Native execution RED/GREEN target command:

```sh
pytest -q tests/test_native_reviewed_candidate_store.py
```

RED was reproduced by copying the new native integration test into a detached temporary worktree at base `f666b9965519901f471c8332d8aa2a1d1161b66c` and running `pytest -q tests/test_native_execution_red.py::test_system_executor_uses_native_run_progress_and_receipt_owner`; it failed before dispatch with `KeyError: 'execution_generation'` from the old source-projection result consumed by `SystemExecutor`. The same integration test is GREEN on this branch. Final result: `8 passed in 2.42s`. Coverage includes native claim and renew in the actual `SystemExecutor` fake-effect path, direct attempt/provider receipt/outcome reads, Consumer Run and Audit Run ownership, no alias row, late receipt after invalidation, historical current-input key behavior, expired-lease reclaim and wrong-owner rejection, and selected-stop/no-action skip behavior with a historical refusal present.

Receipt and source-owner focused command:

```sh
pytest -q tests/test_action_entity_storage.py tests/test_action_storage.py
```

Result: `60 passed, 3 failed in 5.97s`. The three exact nodes are:

- `tests/test_action_entity_storage.py::test_existing_action_schema_with_missing_children_is_rejected_without_repair[action_receipts]`
- `tests/test_action_entity_storage.py::test_existing_action_schema_with_missing_children_is_rejected_without_repair[action_observers]`
- `tests/test_action_entity_storage.py::test_existing_action_schema_with_missing_children_is_rejected_without_repair[both]`

These are confirmed base failures, not regressions: a detached temporary worktree verified top-level `/private/tmp/task3c3-baseline.0zvLWS/repo` at exact HEAD `f666b9965519901f471c8332d8aa2a1d1161b66c`; running the three nodes there gave the same `3 failed` because the test expects `unsupported current Action receipt/observer declaration`, while unchanged `app/current_action_schema.py:83` raises `unsupported current Action child declaration`. The assertion was left untouched.

Also ran `tests/test_reviewed_candidate_store.py tests/test_system_executor.py` as an activation-boundary probe. The broad result was `38 failed, 46 passed`; most failures stop during Store construction because those suites expect `task_inputs` to be installed by constructor activation, which is explicitly deferred to a later unit. The exact unrelated fake-store failure is `tests/test_system_executor.py::test_reply_reconciliation_recovers_only_exact_compacted_trigger_readback`: its `NativeStore.record_outbound_postfix_receipt(self, channel, delivery_key, receipt)` fake rejects `operation='send_reply_to_trigger'` at `app/system_action_handlers.py:152`. A clean one-node comparison at the pinned base produced the same `TypeError` (`1 failed`), confirming it is baseline-existing. No Store constructor, unrelated assertion, or test double was changed here. The focused native fixture avoids that activation boundary.

Static checks passed:

```sh
ruff check app/action_entity_storage.py app/action_storage.py app/execution_owner_storage.py app/reviewed_candidate_store.py app/shared_action_lifecycle.py tests/test_native_reviewed_candidate_store.py
python -m py_compile app/action_entity_storage.py app/action_storage.py app/execution_owner_storage.py app/reviewed_candidate_store.py app/shared_action_lifecycle.py tests/test_native_reviewed_candidate_store.py
git diff --check
```

## Remaining gates and release claim

This closes the bounded runtime writer/receipt unit only. It does not activate constructors or select the new schema in a running Store. The later Task Run constructor/activation unit must exercise the complete initialized Store and retire the constructor-dependent legacy fixture failures before this representation can be claimed as end-to-end active. Independent SPEC/QUALITY review is also still required before the next unit. No provider, production, main, push, or deploy action was performed; this commit is local worktree evidence, not a release claim.

## Fix1 — native retry, ACK and completion acceptance evidence

The missing acceptance coverage was added using `_native_store` and `_approved_native_execution`, which install the actual assembled Task Run schema and invoke public Store claim, begin, receipt, outcome, and finish methods. No product behavior changed; the three source-parity branches already behaved correctly, and the gap was test evidence only.

The retry/uncertain test is parameterized over both statuses. It actually finishes a leased native execution as `retry` or `uncertain`, then claims again and checks the same Consumer Run ID, same single `run_execution_progress` row, no alias row, new owner/lease, `running` state, and retention of the previous result JSON. The ACK test begins a native action, records the same provider ACK twice, and asserts receipt row IDs, provider receipt reference, provider body, digest, lineage (`agent_run_id`), `execution_run_id`, and `action_index` remain stable. It then supplies a different ACK under that action key and asserts the existing exact `conflicting external action result` error and unchanged receipt rows. The completion test uses a two-action plan: `done` is refused with the first action missing, refused again with the second action only dispatched, and accepted only after both native provider receipts/outcomes verify both steps.

Paired focused command:

```sh
pytest -q tests/test_native_reviewed_candidate_store.py -k 'retry_and_uncertain or repeated_provider_ack or done_requires_every_action'
```

At pinned `d1b76cfbb7276c13e92d9f9f13454f8438bd1e06`, I copied the new native test module into a detached temporary worktree and ran the same selection: `4 passed, 8 deselected in 1.47s`. This shows the acceptance cases pass against the already-implemented source-parity behavior; the acceptance gap was absence of these assertions, not a discovered product regression. On the working branch the same selection returned `4 passed, 8 deselected in 1.22s`.

Final changed-file checks:

```sh
pytest -q tests/test_native_reviewed_candidate_store.py
ruff check tests/test_native_reviewed_candidate_store.py
python -m py_compile tests/test_native_reviewed_candidate_store.py
git diff --check
```

Output: `12 passed in 2.81s`; Ruff, compile, and diff checks passed. The original constructor activation, native Store construction, native CLI/UI/full-source regression, and independent scoped SPEC/QUALITY gates remain pending. This evidence commit does not change the no-release claim.
