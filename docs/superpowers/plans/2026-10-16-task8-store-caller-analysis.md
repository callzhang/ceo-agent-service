# Existing eight Tasks kinds: complete unselected Store caller proposal

The adjacent patch is a complete **unselected caller proposal** against `app/store.py` at `d618b0646979e50e09fae8f363b1a63046b54410`. It does not change application files, select Tasks20, register connection functions, migrate a database, or demonstrate public-method behavioral acceptance. Apply only as part of the separately reviewed complete paired cutover. The finite companion JSON lists every source anchor, exact changed method and parameter permutation.

Source SHA256: `ce66a4dd7302080126fb178b4dc8e3983acbc68b2f70c9107bd3e584c4207d5f`.
Candidate SHA256: `f4b3b9ddf3341c48b9fe77b6fbfa81fbd376f3549fe4d086f45efe2a37950bec`.
Patch SHA256: `691cc165fdbaaa4e56a04b907dc6c76cbbc717c4e0c3fc9f3c31a95857cc873c`.

The source file remains byte-identical to that pin after adjacent task-parts commits advanced the branch. Original `task_storage` factories, native schema declarations, migration SQL and constructors remain untouched. Six current Store projection aliases now call `task20_projection`; the two exact historical consumers affected by that global change are integration obligations below, not silently supported by a compatibility path.

## Finite coverage and shape preservation

| Store operation | Count | Scope |
| --- | ---: | --- |
| Native UPDATE | 93 | Reply54, input3, summary8, meeting16, OKR4, weekly7, Workbench turn1 |
| Direct INSERT | 6 | Reply2, input2, summary1, OKR1; all three original UPSERT statements remain native |
| Existing insert helper calls | 3 | Input, meeting and weekly; native single INSERT/IGNORE and cursor result remain |
| Business-bearing direct read statements | 12 | Includes the native Tasks SELECT inside the meeting-event INSERT and UPDATE predicates |
| Native DELETE | 1 | Existing reply duplicate cleanup, with translated source predicates |
| Indirect SQL read leaves | 4 | Three runtime-parent branches plus settlement candidate SQL |
| Indirect SQL tails | 2 | Weekly owner suffix and settlement batch generation predicate |
| Native shared fragments | 2 | Explicit native variants of receipt/shared exclusions, preserving the first query over public projection aliases |

The original source analysis's meeting reader anchors16272/16466 select/join only physical identities and record kinds, so their method ASTs stay unchanged. The additional indirect leaves are10350/10361/10425 and19591; tails10357/19614 and shared fragments23115/23125 are also inventoried in JSON. The123 anchor records are not123 mutations. This patch covers102 Store mutations (93UPDATE +6directINSERT +3helper inserts), rather than claiming all116 across both DAOs. Workbench DAO conversion belongs to its separate proposal.

Each UPDATE uses finite `task8_set_parts(update_sql=...)` native scalar expressions with zero helper-bound parameters. Existing payload-only native execute tuples/lists stay byte-for-byte unchanged. Physical assignments stay outside the payload and preserve native UPDATE OF firing. The sole mixed physical/payload binding permutation is original18393: zero-based `[0,1,2,3,5,6,4,7,8]`; claimed_input_version still reads OLD input_version and claimed_input_id reads OLD current_input_id while new input/version assignments execute in the same statement. Explicit finite dynamic meeting SET fields retain the source validation/filter, optional OLD-status lock expression, native args and transaction.

INSERT values are passed through `task8_insert_parts`, preserving source SQL defaults and explicit NULL, original verb/IGNORE, source UPSERT conflict targets/partial-kind predicates, old-row increments, conditional UPSERT WHERE and the native cursor. UPSERT business copies read `excluded.<generated source field>`; in particular the summary body comes from `excluded."work_summary_inputs__payload_json"`, while `tasks.payload_json` remains the target outer container. There is no decode/reencode loop, new transaction, runtime SQL parser, regex rewriting, dual writer, fallback or new policy.

All14 Store RETURNING contracts remain: nine full meeting returns call `task8_meeting_returning()` (physical id as id, legacy_key, explicit source business aliases); meeting rerun17017 keeps `cast(legacy_key as integer)`; reply19332/19395/19628/23422 keep physical id. Leading CTE claimers, UPDATE FROM receipt scope, aliases, native WHERE/CAS, executemany, rowcount/lastrowid/RETURNING consumption and caller transaction boundaries remain in the original statement shape. The native CTE result-column declaration at23183 stays `execution_generation`; only actual task-column expressions translate. Shared receipt exclusions use exact separate finite scalar expressions in the native physical-row query, while the original source-shaped projection query keeps its public aliases.

## Verification actually performed

The patch applies with `git apply --check` to a task-owned temporary copy read directly from the pinned Git object; applying it produces byte-identical candidate source. Python AST parsing, pinned Python3.13 compile, import without any Store constructor, and Ruff pass. All778 original class methods remain;91 method bodies change,687 unrelated method ASTs are exact, and every method signature, decorator and return annotation is exact. A finite source-anchored check proves all93 original UPDATE sites are covered and all96 SET-helper calls (93updates including dynamic, plus3UPSERTs) have no helper-bound parameters. All9 full meeting-returning calls are present.193 rendered native/projected SQL expressions were statically inspected, with zero remaining old business-column reads in known native Tasks scopes; source result aliases and the intentional outer summary payload container are not mistaken for business leaf reads.

This is source/static verification only. No DAO method was exercised against the final paired release, no real SQLite database or native trajectory body was read, and no provider, native CLI, service, UI or outbound action ran. Root must still execute genuine actual/fresh native public-method differential proofs, including source transaction/error priority, CAS/claim/recovery, defaults/UPSERT allocator water, physical/public identities and receipts before application acceptance. The broader copied forward/current-fact inverse/predecessor/reforward/native/UI/final11 release evidence also remains outstanding.

## Exact remaining integration obligations

- `_initialize` source4164 builds `reply_source` from global `REPLY_TASK_CURRENT_SQL`. Its method AST and original schema sequence are deliberately untouched; root must select/order Tasks20 coherently before this consumer and prove fresh/current initialization.
- `_migrate_removed_runtime` source8249 consumes the same global current projection in historical migration SQL. Its method AST and native statements are untouched; root must explicitly prove historical migration ordering against the appropriate source. This proposal adds no old-schema compatibility layer.
- Store and Email connection constructors must register `register_task8_functions` only in the complete paired activation; Workbench inherits Store. Current constructors and runtime schema selection remain untouched here.
- Residual twelve-kind callers, all other DAO files, source retirement, copy/inverse, true predecessor writes and full release selection remain root-owned integration work. The eight-kind pure-parts component and this caller proposal independently remain unselected.

## Exact changed Store methods

- `_append_reply_input_snapshot`
- `_compact_settled_reply_task_input`
- `_compact_work_summary_input`
- `_enqueue_manual_rerun_reply_task_in_connection`
- `_ensure_business_reply_task`
- `_reconcile_legacy_workbench_confirmations`
- `_refresh_reply_input_in_transaction`
- `_runtime_operation_parent_exists`
- `attach_meeting_alignment_segment`
- `baseline_meeting_alignment_jobs_before`
- `begin_meeting_alignment_run`
- `begin_weekly_okr_analysis_job`
- `claim_meeting_alignment_jobs`
- `claim_okr_review_requests`
- `claim_ready_to_send_meeting_alignment_job`
- `claim_ready_to_send_meeting_alignment_jobs`
- `claim_reply_task`
- `claim_reply_tasks`
- `claim_work_summary_inputs`
- `close_needs_human_task_with_decision`
- `close_unresolved_reply_task_already_settled`
- `complete_reply_task`
- `complete_superseded_failed_weekly_okr_analysis_jobs`
- `complete_superseded_stale_weekly_okr_analysis_jobs`
- `create_meeting_memory_write_event`
- `create_okr_review_request`
- `defer_meeting_alignment_job_for_capacity`
- `defer_reply_task`
- `defer_reply_task_for_authorization`
- `defer_work_summary_input_for_capacity`
- `enqueue_sent_meeting_memory_write_events`
- `enqueue_work_summary_input`
- `fail_reply_task`
- `finalize_orchestrated_reply_task`
- `finalize_reply_task_without_run`
- `finalize_wechat_reply_task`
- `finish_weekly_okr_analysis_job`
- `mark_okr_review_request_done`
- `mark_okr_review_request_failed`
- `mark_wechat_read_only_decision_started`
- `mark_work_summary_input_failed`
- `reclaim_weekly_okr_analysis_job_cache_miss`
- `reconcile_done_reply_tasks_with_failed_current_run`
- `reconcile_failed_agent_message_delivery`
- `reconcile_failed_audit_with_verified_delivery`
- `reconcile_failed_email_unsubscribe_tasks_with_terminal_receipts`
- `reconcile_failed_meeting_memory_write_event`
- `reconcile_failed_reply_tasks_with_confirmation_required_runs`
- `reconcile_failed_reply_tasks_with_recorded_deliveries`
- `reconcile_unresolved_reply_tasks_with_settlement_evidence`
- `reconcile_unresolved_reply_tasks_with_terminal_attempts`
- `reconcile_valid_needs_human_projections`
- `recover_failed_native_codex_auth_tasks`
- `recover_interrupted_agent_runs_after_service_restart`
- `recover_orphaned_processing_reply_tasks`
- `recover_stale_processing_reply_tasks`
- `recover_stale_runtime_attempts`
- `release_ready_to_send_meeting_alignment_claims`
- `reopen_meeting_alignment_job_for_replay`
- `replace_pending_single_chat_reply_task_trigger`
- `requeue_failed_meeting_delivery`
- `requeue_failed_meeting_memory_write_event`
- `requeue_failed_reply_task_for_source_revision`
- `requeue_failed_work_summary_input`
- `requeue_reply_task`
- `requeue_terminal_reply_task_for_revised_trigger`
- `rerun_meeting_alignment_jobs`
- `reset_processing_meeting_alignment_jobs`
- `reset_processing_work_summary_inputs`
- `reset_ready_to_send_meeting_alignment_jobs`
- `reset_recoverable_okr_review_requests`
- `reset_stale_processing_work_summary_inputs`
- `resolve_completed_oa_needs_human_attempt`
- `resolve_reconciled_failed_reply_task`
- `restore_confirmed_meeting_delivery`
- `resume_completed_agent_turns_after_service_restart`
- `retry_failed_effect_free_email_unsubscribe_tasks`
- `retry_failed_pre_agent_reply_task`
- `retry_failed_reply_task`
- `rotate_reply_task_execution_generation`
- `schedule_ready_to_send_meeting_alignment_retry`
- `schedule_work_summary_input_retry`
- `skip_failed_legacy_email_unsubscribe_tasks`
- `skip_failed_reply_tasks_superseded_by_terminal_business_object`
- `skip_failed_reply_tasks_with_terminal_no_action_run`
- `skip_scheduled_reply_task`
- `skip_unstarted_service_tasks`
- `terminalize_exhausted_pending_reply_tasks`
- `terminalize_legacy_email_unsubscribe_task`
- `update_meeting_alignment_job`
- `upsert_meeting_alignment_job`
