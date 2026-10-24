# Reply terminal and receipt closure — Unit B

BASE: `7cfc57baea25ca23aa5eb14db63848ec3c46405c`

This is a local, unactivated native-store implementation. It does not select the assembled constructor, publish a manifest, modify production, or claim full runtime acceptance.

## Native owner changes

`app/store.py` adds a native current Reply projection whose message and business-object facts are read from the Task's `current_input_id` TaskInput. The reached writers/readers below now use native Task/TaskInput/Run ownership and Task generation/status CAS rather than Task8/Task20 or logical-run projections:

- `complete_reply_task`, `fail_reply_task`, `skip_unstarted_service_tasks`, `resume_completed_agent_turns_after_service_restart`, `skip_scheduled_reply_task`, `finalize_reply_task_without_run`, and `reply_task_is_done`.
- `_enqueue_manual_rerun_reply_task_in_connection`, `enqueue_manual_rerun_reply_task`'s generation/risk/effect checks, and `requeue_failed_reply_task_for_source_revision`. New input snapshots and Task parameters use native owners; a source revision rerun remains keyed by its persisted revision, rejects a running current-generation consumer/audit Run, provider-risk rejection, delivery, or native external-action receipt, and CASes the resulting Task error.
- `finalize_orchestrated_reply_task` and `_enqueue_task_memory_write_in_connection`; adopted results stay bound to exact native Run identity, and memory-write event identity remains Task plus execution generation.
- `reconcile_failed_reply_tasks_with_recorded_deliveries`, `reconcile_done_reply_tasks_with_failed_current_run`, `reconcile_failed_reply_tasks_with_confirmation_required_runs`, `reconcile_invalid_needs_human_projections`, `reconcile_valid_needs_human_projections`, `reconcile_unresolved_reply_tasks_with_terminal_attempts`, `reconcile_unresolved_reply_tasks_with_settlement_evidence`, `skip_failed_reply_tasks_superseded_by_terminal_business_object`, and `skip_failed_reply_tasks_with_terminal_no_action_run`.
- `resolve_errors_recovered_by_completed_reply_tasks` and `resolve_errors_recovered_by_scheduled_reply_task` use native Reply Task facts for recovery evidence.

The port retains the source predicates: status/generation fences and clock comparisons, latest Consumer/Audit Run within the Task's current generation, current-versus-claimed Input/version settlement checks, exact current TaskInput business key (not any historical TaskBusinessKey), receipt/action identity, terminal/no-action code allowlist, confirmation-required classification, exact candidate/review/run binding for `needs_human`, and existing ReplyAttempt status/ACK/update guards. Action and ReplyAttempt facts continue to use their own native Action/Receipt owners. Attempts and completed Runs remain immutable; Task changes use CAS. A retryable failed current Run keeps the existing pending/revision semantics. Memory-write enqueue remains duplicate-safe.

The Email unsubscribe receipt method is deliberately unchanged: `reconcile_failed_email_unsubscribe_tasks_with_terminal_receipts` still checks the whole current manifest through `current_email_actions_available`. In this isolated phase the manifest is still source `Store2026-10-21.1` / `Email53`; public positive reconciliation is not reachable until global publication. No gate or validator was stubbed or weakened.

## Validation

Native assembled-target tests were added to `tests/test_store_task_entity_creator.py` for:

- current TaskInput key matching versus a different historical TaskBusinessKey for failed-run settlement and recorded Action/Receipt delivery;
- completion plus native memory-write enqueue idempotence, and preservation of a retryable failed Audit Run;
- terminal no-action reconciliation requiring the latest native Run's allowlisted code;
- manual rerun rotating the native Task generation, appending the current TaskInput, clearing claim ownership and retaining one TaskBusinessKey;
- source-revision rerun generation binding and idempotence.

Checks run (all in the isolated worktree):

- `python -m compileall -q app/store.py tests/test_store_task_entity_creator.py` — passed.
- `ruff check app/store.py tests/test_store_task_entity_creator.py` — passed after cleanup of the unused legacy insert helper import.
- `pytest -q tests/test_store_task_entity_creator.py --maxfail=1` — 87 passed in 114.53s after adding the native no-action, manual-rerun, and source-revision fixtures.
- `pytest -q tests/test_store_task_entity_creator.py -k 'native_manual_rerun or terminal_no_action_reconciliation or native_recorded_delivery_reconciliation or native_complete_reply_task_' --maxfail=1` — 5 passed.
- `pytest -q tests/test_store_task_entity_creator.py -k 'native_source_revision_rerun' --maxfail=1` — 1 passed after correcting the native external-action lookup to the current Input's exact business key.
- `pytest -q tests/test_run_adoption_inverse.py -k reply_provenance_task_and_input_references_survive_two_portable_cycles --maxfail=1` — 1 passed; this is the existing Task/Input/ReplyAttempt provenance cycle check, including a Task and Input created after the initial forward conversion.
- `pytest -q tests/test_run_adoption_inverse_rows.py -k adoption_inverse_consumes_real_current_task_inverse_handoff --maxfail=1` — 2 passed; validates native Task inverse identity handoff consumed by Action adoption inverse for both remapped and non-remapped Task fixtures.

The historical full-predecessor parity fixture `tests/test_error_actions_native_parity.py -k full_predecessor_done_task_with_failed_current_run` stopped during fixture initialization (`trg_configs_direct_account_delete: no such column: a.action_account_id`) before reaching any API. It is unrelated to this code path; it is not semantic parity evidence and was not repeatedly repaired. Direct native assembled-target checks provide the local evidence above.

## Remaining gates and source exits

- `replace_pending_single_chat_reply_task_trigger` still has source Task8/Task20 writes and source TaskInput/BusinessObject Task merge logic. It is a reached merge writer and was not claimed converted here; it requires a dedicated native merge preserving its duplicate cleanup/ownership semantics.
- Shared History/Attention/WeChat and broader Reply list/detail readers remain source-shaped outside this closure. The current implementation does not activate the constructor or add legacy source/native fallback.
- Public Email receipt reconciliation remains blocked by the global manifest availability gate stated above.
- A single composed inverse/forward of newly created native Task/Input/Run/adopted-result/Action/Receipt facts is not established by the two existing converter checks above: one covers new Task/Input and ReplyAttempt references over two cycles, and the other covers the actual Task inverse handoff consumed by the Action adoption inverse. Full current-owner composition must still verify new native Run/adopted result/receipt rows survive the full pipeline.
- Full copied database acceptance, native process restart/fake-ACK recovery chain, publication/cold-process validation, browser acceptance, and global source-consumer removal remain later gates.

## Review and commit

Before committing, review this report and every changed hunk against the base for source-predicate parity, native owner correctness, current/claimed input identity, rollback behavior, and unsupported source SQL. Commit only this unit's claimed hunks and this report; release the claim immediately afterward. Parent performs independent full `BASE..HEAD` SPEC and QUALITY review.
