# Reply terminal and receipt closure — Unit B

BASE: `7cfc57baea25ca23aa5eb14db63848ec3c46405c`

This is a local, unactivated native-store implementation. It does not select the assembled constructor, publish a manifest, modify production, or claim full runtime acceptance.

## Native owner changes

`app/store.py` adds a native current Reply projection whose message and business-object facts are read from the Task's `current_input_id` TaskInput. The reached writers/readers below now use native Task/TaskInput/Run ownership and Task generation/status CAS rather than Task8/Task20 or logical-run projections:

- `complete_reply_task`, `fail_reply_task`, `skip_unstarted_service_tasks`, `resume_completed_agent_turns_after_service_restart`, `skip_scheduled_reply_task`, `finalize_reply_task_without_run`, and `reply_task_is_done`.
- `requeue_reply_task`, `_enqueue_manual_rerun_reply_task_in_connection`, `enqueue_manual_rerun_reply_task`'s generation/risk/effect checks, and `requeue_failed_reply_task_for_source_revision`. Stale recovery selects only running Consumer/Audit Runs for the expected current Task generation without professional Run provenance and preserves the expired-lease/ten-minute timestamp test. It writes terminal Run status/error natively and appends `run_failures` through `append_run_failure`, then CASes the same Task generation to pending. New manual-rerun input snapshots and Task parameters use native owners; source-revision rerun remains keyed by its persisted revision, rejects a running current-generation Consumer/Audit Run, provider-risk rejection, delivery, or native external-action receipt, and CASes the resulting Task error.
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
- stale Task requeue closes only current-generation stale unprovenanced Consumer/Audit Runs, emits native RunFailure evidence, and leaves old-generation, recent, and other-role Runs running;
- a current source Task created through `_populated_source`, Task/Run/Action migrations, and native `finalize_orchestrated_reply_task`, recorded Action/Receipt, failure-settlement, and memory-write APIs is taken through two complete portable Task→Run→adoption inverse/reforward cycles. The checks retain Task terminal status/error/attempt count and current business key, adopted candidate JSON/digest, a typed ReplyAttempt, and a verified provider receipt. Its exact `agent_run_id` is compared through the typed 9106↔9006 Run mapping rather than as an untyped integer.
- the composed finalizer check first failed with `sqlite3.ProgrammingError: Incorrect number of bindings supplied. The current statement uses 5, and there are 6 supplied.` The native conversion had replaced the source `manual_rerun_attempt_id` subquery with the Task parameter value but retained the source subquery's final Task-ID binding. Removed that extra binding; this defect was introduced in the first Unit B commit and repaired in the follow-up before review.

Checks run (all in the isolated worktree):

- `python -m compileall -q app/store.py tests/test_store_task_entity_creator.py` — passed on the final code tree.
- `ruff check app/store.py tests/test_store_task_entity_creator.py` — passed on the final code tree.
- `git diff --check` — passed on the final code tree.
- `pytest -q tests/test_store_task_entity_creator.py --maxfail=1` — 89 passed in 102.89s on the final code tree.
- `pytest -q tests/test_store_task_entity_creator.py -k 'composed_portable_cycles or native_requeue' --maxfail=1` — 2 passed after exercising the stale-run guard and two complete current-fact Task→Run→adoption inverse/reforward cycles.
- `pytest -q tests/test_store_task_entity_creator.py -k 'native_manual_rerun or terminal_no_action_reconciliation or native_recorded_delivery_reconciliation or native_complete_reply_task_' --maxfail=1` — 5 passed.
- `pytest -q tests/test_store_task_entity_creator.py -k 'native_source_revision_rerun' --maxfail=1` — 1 passed after correcting the native external-action lookup to the current Input's exact business key.
- `pytest -q tests/test_run_adoption_inverse.py -k reply_provenance_task_and_input_references_survive_two_portable_cycles --maxfail=1` — 1 passed; this is the existing Task/Input/ReplyAttempt provenance cycle check, including a Task and Input created after the initial forward conversion.
- `pytest -q tests/test_run_adoption_inverse_rows.py -k adoption_inverse_consumes_real_current_task_inverse_handoff --maxfail=1` — 2 passed; validates native Task inverse identity handoff consumed by Action adoption inverse for both remapped and non-remapped Task fixtures.

The historical full-predecessor parity fixture `tests/test_error_actions_native_parity.py -k full_predecessor_done_task_with_failed_current_run` stopped during fixture initialization (`trg_configs_direct_account_delete: no such column: a.action_account_id`) before reaching any API. It is unrelated to this code path; it is not semantic parity evidence and was not repeatedly repaired. Direct native assembled-target checks provide the local evidence above.

## Remaining gates and source exits

- `replace_pending_single_chat_reply_task_trigger` still has source Task8/Task20 writes and source TaskInput/BusinessObject Task merge logic. It is called by `worker.py` and `wechat/producer.py` and remains a reached merge writer. Its exact selection is the newest pending, zero-attempt single-chat Task for the exact channel/conversation whose current trigger time is no later than the incoming trigger. It checks exact trigger/available/error equality, reparents duplicate input and business-object associations, keeps the selected Task's current business key or derives the prior key, appends the new trigger facts as a current TaskInput, deletes duplicate source Tasks, and updates input version. Port this transaction and its identity conflicts in a separate coherent merge-owner change; it is not part of Unit B's terminal/receipt closure.
- `clear_agent_run_session` retains a raw source-shaped run update and currently has only a test caller; it is a separate reached source exit, not included in this Unit B closure.
- Shared History/Attention/WeChat and broader Reply list/detail readers remain source-shaped outside this closure. The current implementation does not activate the constructor or add legacy source/native fallback.
- Public Email receipt reconciliation remains blocked by the global manifest availability gate stated above.
- Full copied database acceptance, native process restart/fake-ACK recovery chain, publication/cold-process validation, browser acceptance, and global source-consumer removal remain later gates.

## Review and commit

The first Unit B commit is `86ec6a7f937af54e180a07f242528436b0928d02`; the follow-up adds the required current-fact two-cycle proof and native stale-Run requeue, and repairs its newly exposed extra SQLite binding in the converted finalizer query. The parent performs independent full `BASE..HEAD` SPEC and QUALITY review.

## Review fix 1 — canonical summary consistency

The independent review found earlier summaries in `docs/architecture.md` and `docs/runtime-mechanism.md` still described Reply terminal settlement and touched lifecycle/query paths as source-projected and awaiting closure. Updated both summaries to match the current unactivated Task2D10C native terminal/receipt candidate. They explicitly keep `replace_pending_single_chat_reply_task_trigger`, shared History/Attention/WeChat and other Reply list/detail readers, global manifest availability, and public Email receipt reconciliation as open boundaries. No product code, tests, policy, activation, or release behavior changed.

Checks: `git diff --check` — passed. `rg -n 'Reply terminal settlement／error reconciliation 和其余触达|terminal settlement／error reconciliation 与其余触达' docs/architecture.md docs/runtime-mechanism.md` — no matches (exit 1), confirming the two stale summary sentences are gone. `rg -n 'Task2D10C|replace_pending_single_chat_reply_task_trigger|公共 Email|unsubscribe receipt reconciliation' docs/architecture.md docs/runtime-mechanism.md` — both canonical docs show the native candidate and retain the pending merge and Email gates. Focused product tests were not rerun for this documentation-only correction; the previous final-tree Unit B checks remain as recorded above.
