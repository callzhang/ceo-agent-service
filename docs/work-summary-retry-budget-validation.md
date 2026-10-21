# Work Summary retry budget repair

## Expected and observed

Expected: automatic processing has a three-attempt failure budget; a worker
waiting for the shared Task Agent session has not made a processing attempt.

Read-only production inspection on 2026-10-20 Pacific time found Work Summary
input 416319 pending at 46 attempts, with no task_agent_runs for that input.
Its Task claim history had reached generation 48. Input 416320 was pending with
`Task Agent shared session is owned by another worker`. A service restart had
cleared the older input's current error, so the retained rows do not establish
every historical reason for its 46 claims.

## Mechanism and repair

- `_TaskQueueAdapter.claim` increments Work Summary attempts on every claim.
- `_process_claimed_work_summary_input` formerly returned a busy-session claim
  using the ordinary retry operation, without refunding the claim or checking
  the failure budget. Repeated contention alone could therefore produce an
  unbounded displayed retry count without an Agent run. It now uses the existing
  deferred-capacity transition, refunding only the current unexecuted claim.
- `WorkSummaryQueueAdapter` lacked the Reply adapter's handler-error transition.
  An exception before ordinary Task processing left the source processing and
  reclaimable repeatedly. It now shares the existing fenced failure transition:
  delayed pending after failures one and two, failed after failure three.
- Admission rejects an already exhausted Work Summary input before another
  handler executes. It retains the accumulated count, source and run history;
  the closing claim remains counted. This also closes old over-budget inputs.
- The explicit root-cause-repair retry starts a new three-attempt budget. Ordinary
  automatic retries do not reset the budget. Task Agent runs are not rewritten.

No Audit policy, provider authorization, external write, schema or migration is
part of this repair. The exact historical root cause of each old claim is not
inferred from the count alone.

## Verification

All three original regressions failed before the repair: busy-session attempts
increased, an exhausted item entered WorkItem parsing, and a pre-Agent handler
failure did not persist its error or apply delayed/terminal handling. An extra
regression first demonstrated that explicit retry inherited the exhausted budget.

The final regression file exercises real SQLite task transitions and the real
ConsumerDispatcher; it covers repeated contention, the 46-claim legacy case,
three escaping handler failures, and explicit repair retry. The initial four
regressions and eight related CLI shared-session cases passed (12 checks).
Final focused checks: 12 passed (four budget regressions plus existing CLI
shared-session cases); 11 passed in the budget/dispatcher handler-error,
release/cancellation and stale-claim selection. Ruff, git diff --check and imports
of app.cli, app.worker, app.email_worker and app.service_supervisor passed.

A wider dispatcher run had 76 passes and one timing failure:
`tests/test_consumer_dispatcher.py::test_task_todo_outbox_enqueue_wake_is_not_lost_after_empty_scan`.
Its unrelated Todo worker exceeded the two-second wait while a SQLite claim
transaction took more than two seconds. A baseline single run passed; candidate
single runs still hit the deadline, so this check is not claimed green.
The failing test leaves a non-daemon dispatcher thread; only the exact task-owned
pytest processes were interrupted after their completed reports.

A pristine origin/main archive (d812e0439) reproduced 18 failures and 15 passes
in the selected CLI Work Summary tests. Their fake runners use the old decide
signature (one inspected current error was unexpected keyword friday_prompt).
The failures present in that baseline are:

- test_process_work_items_command_processes_claimed_input
- test_process_work_items_command_processes_existing_input_without_feature_gate
- test_process_work_items_command_reclaims_stale_processing_input
- test_process_work_items_command_does_not_batch_claim_after_failure
- test_process_work_items_command_backoffs_transient_codex_failure
- test_process_work_items_command_fails_native_codex_missing_auth_header
- test_process_work_items_command_keeps_native_missing_header_terminal_after_limit
- test_process_work_items_command_fails_codex_transport_failure_after_limit
- test_process_work_items_pauses_after_codex_capacity_exhaustion
- test_work_summary_process_failure_continues_prior_capacity_wait
- test_process_work_items_command_fails_typed_external_failure_after_limit
- test_process_work_items_command_fails_missing_memory_recall_tool_event
- test_process_work_items_command_discards_cross_project_follow_up_draft
- test_process_work_items_command_uses_task_agent_timeouts
- test_process_work_items_waits_for_paused_routes_without_spending_attempts
- test_process_work_items_waits_for_provider_outage_after_failed_attempt_without_spending_attempts[CAPACITY]
- test_process_work_items_waits_for_provider_outage_after_failed_attempt_without_spending_attempts[TRANSPORT]
- test_process_work_items_keeps_execution_failures_bounded

An intermediate placement of the budget check at the shared CLI entry also
broke the four minimal retired-source fixtures. The final check is at dispatcher
admission, preserving their existing CLI routing; all four pass in the final
12-check selection. The baseline's 18 failed tests were not changed or hidden.
No full test suite was run against the live service.

## Release

The normal deployment drains claims and active Agent work, verifies or creates
its database backup, fast-forwards only pushed origin/main code, and restarts
through launchd. Existing startup recovery refunds interrupted Work Summary
claims, preserves Reply execution generation and external-action identities,
and recovers Meeting work from persisted state. SystemExecutor reuses verified
candidate action attempts and reconciles dispatched/uncertain attempts. This
repair adds no provider calls or external-action replay.

Commit and production readback are pending.
