# Task runtime closure batch report

## Boundary and commits

This report covers only the bounded Task ownership/dispatcher, reached Reply recovery, generation/revised-trigger, and passive-State conversion unit. It does not claim Task/Run runtime closure or global native runtime closure.

- Base: `ed8f063e9bff88e7ee978b8b1e2bb3507d0605dc`
- Implementation commit: `800407697` (`feat(tasks): close native dispatcher recovery lifecycle`)
- Report commit: the documentation-only commit immediately following the implementation commit. The owned claim row was already absent from BASE/HEAD and required no additional removal.
- No production checkout, main branch, push, deploy, global constructor activation, process control, or real outbound action was used.

## Implemented behavior

- Dispatcher claim identity and lease ownership use native `Task.id`, native semantic `Task.kind`, canonical decimal public IDs, and Task CAS fields. Runtime adapter selection no longer routes through source-kind translation or source-ID aliases.
- All five native dispatcher adapters can claim canonical Task IDs. Claim history remains adapter-qualified passive State and is written in the same SQLite transaction as the Task lease update.
- Reached stale/orphan/restart recovery and completed-agent-turn recovery use Task, Run, RuntimeAttempt, and native failure facts. Revised-trigger requeue appends a native TaskInput revision and rotates the Task execution generation using CAS.
- Explicit passive-State conversion maps typed references through migration identity maps, preserves physical row IDs and unrelated payload, preflights mapped-key collisions and unknown-reference-to-native-owner ambiguity before mutation, and supports inverse/forward composition.
- Architecture and runtime-mechanism docs describe this bounded behavior and retain the unconverted terminal and settlement paths as remaining work.

## Verification

Focused checks passed after the final implementation edits:

- Ruff and `compileall` on changed implementation and test files: passed.
- `git diff --check`: passed before implementation commit.
- `pytest -q tests/test_task_entity_schema.py -k 'dispatcher_state_task_references_forward_inverse_and_preserve_unknown_rows or dispatcher_state_unknown_reference_cannot_alias_remapped_native_task_id' --maxfail=1`: **2 passed, 38 deselected**. Covers remapping, preserved State row IDs/payload, unknown-row preservation, collision preflight, and unknown source ID colliding with a remapped native Task owner; the latter fails before source Task DDL mutation.
- `pytest -q tests/test_store_task_entity_creator.py -k 'native_dispatcher_history_write_failure_rolls_back_task_lease or concurrent_native_reply_dispatcher_begin_claims_have_one_winner or native_reply_queue_claim_and_input_refresh_keep_claimed_snapshot or native_bulk_claim_rechecks_after_preview_loses_task or native_task_dispatcher_adapters_claim_canonical_task_ids or revised_terminal_reply_trigger_appends_native_input_and_rotates_task or reply_execution_generation_rotation_uses_native_task_cas or task_dispatcher_claim_history_uses_canonical_task_id or native_reply_dispatcher_claim_uses_task_owner or native_reply_recovery_updates_task_owner or orphaned_native_run_closure_records_run_failure' --maxfail=1`: **16 passed, 62 deselected**. Includes an actual SQLite claim-history insert-abort trigger proving lease rollback, two concurrent independent claim connections with one winner, all five adapters, claimed-input snapshot behavior, candidate-loss recheck, Task CAS generation/requeue, recovery, and Run failure fact.
- `pytest -q tests/test_run_adoption_inverse.py -k 'reply_provenance_task_and_input_references_survive_two_portable_cycles' --maxfail=1`: **1 passed, 10 deselected**. Two portable JSON forward/inverse cycles include historical Reply State plus a newly native Task and claim history; canonical Task claim ID survives inverse and reforward.

An early broad diagnostic on historical source-layout tests reported **41 failed, 120 passed**. It is not acceptance evidence: the first concrete failures use old constructors/source fixtures and the native fixture has no `task_inputs` table. It was not repeated or treated as a blanket expected-failure classification.

## Concrete coverage limits

- Direct current-versus-claimed input snapshot behavior is covered by `test_native_reply_queue_claim_and_input_refresh_keep_claimed_snapshot`.
- Claim owner/generation CAS and two-connection contention are covered, as is transaction rollback when passive State history insertion fails.
- This unit does not directly exercise an adapter switch on the same Task across two independent adapter-qualified histories; PID-zero/empty-expiry let-go behavior; or pruning a stale-generation history row while protecting a newer generation, another adapter's history, and a live Task lease. These are specific missing edge proofs for the changed claim helper and should be covered by the terminal/receipt follow-up or a narrowly targeted claim-state test before publication. The five-adapter claim test proves initial adapter ownership, not switching.

## Remaining immediate runtime closure

The next functional batch must cover terminal settlement/receipt/reconciliation/error paths; this unit intentionally did not modify them. The remaining named `Store` APIs include:

- `reconcile_failed_reply_tasks_with_recorded_deliveries`, `reconcile_failed_email_unsubscribe_tasks_with_terminal_receipts`, `reconcile_done_reply_tasks_with_failed_current_run`, `reconcile_failed_reply_tasks_with_confirmation_required_runs`, `reconcile_invalid_needs_human_projections`, `reconcile_valid_needs_human_projections`, `reconcile_unresolved_reply_tasks_with_terminal_attempts`, and `reconcile_unresolved_reply_tasks_with_settlement_evidence`;
- `skip_failed_reply_tasks_superseded_by_terminal_business_object`, `skip_failed_reply_tasks_with_terminal_no_action_run`, and `resolve_errors_recovered_by_reply_attempts`, `resolve_errors_recovered_by_completed_reply_tasks`, `resolve_errors_recovered_by_scheduled_reply_task`;
- `skip_unstarted_service_tasks`, `resume_completed_agent_turns_after_service_restart`, `complete_reply_task`, `fail_reply_task`, and `skip_scheduled_reply_task`;
- source-shaped current Reply queries, revised-trigger/manual-rerun direct consumers, receipt/reconciliation projections, and other Task/Run consumers not converted by this unit.

No constructor activation is claimed. Other WeChat, Attention, History, memory-write, publication, and unrelated runtime source projections remain outside this bounded unit and require their scheduled ownership batches.
