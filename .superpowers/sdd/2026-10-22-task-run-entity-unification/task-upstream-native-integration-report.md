# Upstream native integration report (2026-10-24)

## Merge baseline

The clean Phase 2 candidate started at `82fb511b96456f8bbd8c93234bb1ac6e3ab2b1ee`. The actual fetched `origin/main` at integration was `3a46d14eed6175746bd805f6be3f1b12f10556ca`; the prior preparation had stopped at `b1495f6920103b1e8b802047df4e4954c7e6d918`. A normal `git merge --no-commit --no-ff origin/main` was used. No reset, rebase, push, production checkout, deploy, provider call, or process control occurred.

## Upstream behavior retained

- Meeting delivery identity remains scoped to normalized recipient kind and ID. The old key is reused only when its recorded target agrees; replay, rerun, ASR recovery, and audit recovery recognize prepared and receipt facts under `:target:` keys. Existing receipt IDs and immutable target binding remain intact.
- Failed frozen Meeting candidate recovery keeps the upstream exact SHA-256, latest-attempt, unchanged target/body, no-existing-receipt, independent read-only Audit, and approve-only return-to-worker behavior. Its write path now uses the native Meeting Task and `task_results` CAS; its Audit attempt uses the native professional Run owner and `task_id` parent.
- The new WeChat reader has no conversation selector. It reads channel, conversation ID/type and generation from the claimed native TaskInput so a later current-input refresh cannot change the running execution's scope.
- The upstream document-write `partial_success` evidence/provider identity handling, replay `source_in_input` handling, Store pool `shrink_memory` and idle-test cleanup, Task Agent source-reference guidance, and unrelated main changes were retained by the merge.

## Merge conflicts and resolutions

- `app/meeting_alignment.py`: kept native Meeting delivery/target validation and used the target-scoped resolver for confirmed receipt readback.
- `app/store.py`: kept the native Task implementation for rerun, ASR recovery, replay and delivery ownership. Added target-key-prefix checks to rerun/ASR/replay effect guards. Ported failed-delivery requeue, audit-recovery request, and audit-recovery completion to Task/TaskResult reads and CAS, plus current native Receipt checks and professional Runs. The initial upstream code referred to removed Task20 projection helpers and logical Run APIs; those calls are absent from the final code.
- `app/web_api/registration.py`: the audit-recovery endpoint resolves the native Meeting Run and its native `task_id` parent.
- `docs/agent-claims.md`: retained upstream's active Meeting recovery claim and this worktree's own claim during implementation. The unrelated WeChat scheduled-command test helper conflict retained the helper that reads the native health state shape.

## Verification

Focused affected behavior passed after the final implementation changes:

- `pytest -q tests/test_meeting_recovery_safety.py tests/test_meeting_alignment.py::test_replay_keeps_failed_job_with_prepared_delivery_on_recovery_path tests/test_meeting_alignment.py::test_replay_keeps_failed_job_with_target_scoped_prepared_delivery tests/test_meeting_alignment.py::test_meeting_delivery_identity_is_scoped_to_immutable_recipient tests/test_meeting_alignment.py::test_failed_frozen_meeting_candidate_can_be_reaudited_without_regeneration tests/test_meeting_alignment.py::test_failed_frozen_meeting_audit_return_stays_failed tests/test_meeting_alignment.py::test_failed_frozen_meeting_audit_recovery_requires_exact_digest tests/test_meeting_alignment.py::test_requeue_failed_meeting_delivery_preserves_review_and_key tests/test_meeting_alignment.py::test_requeue_failed_meeting_delivery_rejects_unreviewed_candidate tests/test_meeting_task_runtime_native.py::test_meeting_writer_claim_update_and_delivery_identity_use_canonical_task_id tests/test_meeting_task_runtime_native.py::test_meeting_replay_keeps_saved_public_outbound_identity_at_remapped_task_id tests/test_agent_cli_wechat_read.py tests/test_console_web_api.py::test_meeting_audit_recovery_rejects_nonlatest_attempt tests/test_document_write_recovery.py tests/test_store_thread_connection_reuse.py::test_a_connection_going_idle_releases_its_page_cache tests/test_replay_task_attention_project.py::test_scoped_runner_records_a_source_left_out_of_the_input`: **53 passed**.
- Pytest automatically applies the repository's Darwin physical-memory footprint limit of 3 GiB through `tests/conftest.py`; no cap override was used. No full suite was run.
- `git diff --check` and `python -m compileall -q app` are run again before commit.

During the first diagnostic pass, the target-scoped replay regression exposed a missing `:target:` effect guard; that guard was added. The first native recovery checks also exposed retired Task20 and logical Run API references; they were replaced with native Task/TaskResult and professional Run calls. Several historical tests initially used source-only `create_logical_run`, `dispatcher_claim_leases`, `task_id_expression`, and source receipt signatures. Their assertions were preserved while the fixtures were converted to native Task leases, Runs, RuntimeAttempts and delivery Actions. The stale source-SQL error assertion was adjusted for SQLite's native-qualified column name.

## Remaining gates

The Phase 2 fresh-copy acceptance task, independent review, full-copy/cold-process gates, and any publication decision remain open. This report does not claim those gates or production behavior.
