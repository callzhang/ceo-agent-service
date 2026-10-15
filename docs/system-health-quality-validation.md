# System health and Quality repair

## Confirmed production causes

On 2026-10-14, `/api/console/status` reported `degraded` with three quality
violations. The direct scanner confirmed required-source coverage was complete:

| Violation | Exact source | Finding |
| --- | --- | --- |
| `reply_attempts.unresolved_latest_attempt` | Attempt 15438 | Failed WeChat send candidate, no later delivery/recovery |
| `wechat_deliveries.failed` | Delivery 137 / task 404718 | Same current-generation candidate, `wechat_not_running`, two exhausted retries |
| `work_summary_inputs.failed` | Input 404912 | Task Agent structured-result validation failed twice, without applied projection |

These were real unresolved objects, not stale-generation false positives. The
38 readable and 475 unavailable native delivery results were coverage metadata;
unavailable native evidence was not the cause of these three violations.

## Authorized WeChat settlement

The configured Reader read the exact Melody conversation after trigger
6783111560372195766. It exposed only the original inbound message, with no later
principal reply or outbound delivery in its current local data. Sender IPC was
ready but its passive readiness check still reported `wechat_not_running`.
The original send failed before any visible action. Phone-side content that
had not synchronized to the stopped desktop client could not be ruled out.

Derek reviewed the exact old reply and explicitly chose to skip the expired
candidate while keeping its failure history. The existing
`skip-stale-wechat-delivery` CLI settled only delivery 137 in generation
1918a88195194475b98b60329783e081, with the inactivity and exhausted-retry checks.
Readback confirmed delivery 137 and Attempt 15438 became `skipped`. Their close
reason retains `original_status=failed`, `original_error=wechat_not_running`,
`original_pre_action_failure=1`, retry count 2, failure time and `no_message_sent`.
The candidate, generation, original timestamps and original run remain; no
message was sent, replayed or labelled delivered. The scan then contained only
the failed Work input. The existing skip/CLI regression selections passed 22
and 21 tests respectively.

## Task Agent output-contract repair

Input 404912 retained its original AI Minutes body and had two failed runs,
357692 and 357883. Local validation of the four exact native outputs reproduced:

1. Both initial outputs put existing Task 471 in `target_task_id` and left
   `task_id` null, invalid for `update_task`.
2. The first correction omitted the Project selector for a new supporting Task.
3. The second correction proposed merging Task 471 into itself.

Both bounded correction cycles exhausted before applying a business projection.
The native ranges contained no tool calls, runtime effect-start markers were
empty, and no business signal or TODO outbox receipt existed for this input.

The validation model and checked-in native strict schema now explain the
existing roles of `task_id`, merge destination/identity fields, and supporting
Task Project selectors. Ordinary scope updates use the existing `task_id` and
`update_fields`, with no identity merge. Validators, schema shapes, business
rules, correction budget and prompts are unchanged. The original native Task
input exposed general identity rules but did not name these merge-only JSON
fields. Native session metadata did not retain a schema snapshot; schema
binding is established by the runtime's checked-in-schema path, not a claim
that the old schema text was present in the session transcript.

Focused RED reproduced the absent field guidance; malformed ID/self-merge and
Project-selector controls continue to reject invalid outputs. The final new
contract plus existing Task Agent/runtime-repair selection passed 277 tests;
independent contract/schema-snapshot checks passed 40 tests; the final clue-only
control brought the focused contract set to 15 passing cases. Removing only
descriptions makes old/new native schemas identical, and validator methods are
unchanged. Ruff and `git diff --check` passed.

The existing `retry-work-summary-input --input-id 404912` entrypoint is the
recovery path after deployment: it conditionally requeues a failed uncompacted
input, preserves its failed runs and attempts, and lets the normal worker
produce a new run. Actual recovery success requires a terminal source readback
and adopted projection; queue acceptance alone is insufficient.

## Quality CI repair

The latest completed failing Quality baseline, run 38677293656 on `bdc98bb3`,
reported 15 failures, 13,159 passes and 118 skips. All 15 failures had one cause:
capture-only Email test Store doubles lacked `get_reply_task`, which terminal
artifact cleanup now calls after finalization. The focused 15 reproduced RED;
six local doubles now implement the exact read protocol without changing their
business assertions. New integration cases exercise the real Email finalizer
and file cleanup for four terminal statuses and one retryable status, proving
that pending artifacts remain and cleanup follows finalization. Both affected
test files passed 261 tests; independent relevant cases passed 25 tests.

No production cleanup logic, failure expectation, retry limit or timeout was
weakened. An exploratory local frontend run hit multiple concurrent timeouts
and was stopped; those observations are not treated as code failures. Final
acceptance follows the GitHub Quality run for the published repair revision,
including any additional failures exposed by that complete run.
