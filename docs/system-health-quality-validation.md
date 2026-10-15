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

## Production recovery readback

Deployment of `7301f9a6` completed through `app.deploy --skip-quiet-wait`,
replacing supervisor PID with 58973. The explicit recovery CLI requeued only
input 404912. The normal worker completed new Task run 360429 at
2026-10-15 05:45:33 UTC, retaining failed runs 357692 and 357883. Source input
404912 is `done` after attempt 3 and was compacted by normal terminal cleanup.
The adopted projection updated existing Task 471 and recorded signal 833 for
the exact original source. Its `no_proposal` projection status describes no
new Attention proposal; it does not negate the persisted Task update.

A production-code, SQLite `mode=ro` quality scan checked at
2026-10-15T05:53:49.988851Z returned no missing sources and no violations.
The system-health projection was `healthy`, with zero violations; all 25
persisted components were healthy, and Meeting Memory had no delayed,
processing, failed or ghost work. Native delivery coverage remained explicit:
39 readable results and 475 unavailable results. Active work and a genuine
human decision remained visible; they were not relabelled as failures or
completed to obtain this result.

This confirms degraded recovery independently of HTTP responsiveness. Fresh
Status HTTP requests still exceeded 20 seconds, including one 90-second
zero-byte timeout. A read-only Status build plus the exact HTTP outer
validation/serialization completed in 5.157 seconds; the outer stages took
only milliseconds. A current all-history runtime metadata check performed
167,041 credential checks and 2,338,574 regular-expression searches for
23,863 rows, taking 8.218 seconds. These measured costs establish optimization
work, but do not by themselves explain the entire observed HTTP delay.

The bounded optimization reuses the unchanged credential/path predicate for
identical strings only inside one runtime-invariant check. All historical
rows and all seven fields remain checked; violations still count per row and
the next scan rereads path configuration. On the same production read-only
snapshot the original and new functions returned identical empty issues:
8.460s / 167,048 credential calls / 2,338,672 regex searches versus
2.365s / 25,456 calls / 356,384 searches. RED duplicate-value coverage failed
before implementation; all 83 scan-cost/quality tests passed afterward, with
18 scan-cost cases independently rerun. No global health-result cache or
check policy changed.

The subsequent complete Quality run on `832da02e` (38685363962) exposed 33
failures: the same 15 Email doubles and 18 prompt assertions depending on
pretty-printed JSON whitespace. The latter span Worker, context provenance,
and trace-compaction tests. All 18 reproduced RED, then passed after parsing
the exact named JSON sections and asserting the original business fields.
Timestamp checks now bind their values to the exact message ID. Negative kind,
empty read-command, source provenance and raw-payload assertions remain.
The trace unit fixtures also use repository seed prompt configuration instead
of the operator's saved local prompt, and their Audit wire valid sample follows
the current transport schema while retaining compact/raw validation equivalence
and extra-field rejection. All ten trace/provenance cases passed; independent
review accepted the semantic assertions. No runtime contract, timeout, skip or
expected business outcome changed in this test-only repair.

The completed `ff4d3623` Quality log additionally exposed six specialized
prompt-format assertions: one Meeting audience case, one Task Skill guidance
case, and four read-only Project evaluation variants. All six reproduced RED
and passed after asserting the exact JSON participant ID or original
`source.ref`. Original audience, combined-message content, read-only transport,
source payload and side-effect assertions remain. Independent review accepted
the three-file test-only change; Ruff and diff checks passed.

## Latest-trigger quality query cost

The four trigger-latest checks repeated full-history window sorting. Attempt
fields are VIRTUAL generated columns over `actions.payload_json`, so metadata
selection can still read JSON. The finite source now reads candidate statuses
through the existing status index before resolving identity fields, groups
candidate keys once and fixes those keys as the outer lookup. The latest lookup
still includes every status and retains `datetime(updated_at), id` order and
NULL partitions. OA's separate process-identity window and all consumer quality
predicates remain unchanged. No schema, index or persistent cache was added.

Cost regressions first failed for terminal history, repeated candidates and
many candidate keys. The final 87 focused tests passed. On the same production
read-only snapshot, the original full Reply Attempt check used10.634M VM steps
and3.846s; the candidate used2.300M /1.388s and2.302M /1.119s. Complete violations
and Attention issue fields matched, and99 current ten-field candidate rows
matched exactly. EXPLAIN and bytecode verified status rejection before payload
column reads, candidate-key outer iteration and indexed public-ID/trigger
lookups. This proves the query's improvement, not complete HTTP acceptance.

## Audit wire fixture follow-up

Complete Quality run 38690491856 on `ae08b7c7` exposed 38 failures after the
service-owned Audit revision/digest transport change; the preceding 24
format assertions no longer failed. Synthetic native-output fixtures still
emitted `proposal_revision` and `candidate_digest`, which the current strict
wire schema rejects. The fixture adapters now omit only those transport echoes;
the domain result retains its service-provided binding. The instruction test
asserts the same ownership. Two explicit controls verify each forbidden echo
still raises validation failure and service conversion restores the binding.

All 38 affected scenarios plus those two controls passed in168.11s using
temporary copies of repository seed prompts. An earlier local run using the
operator's old saved prompt was isolated as configuration pollution and is not
counted as CI evidence. Ruff and diff checks passed, and independent review
accepted the test-only change. Lifecycle, delivery, receipt, retry and side-effect
assertions remain; no validator, timeout, skip or expected business result changed.

## Subsequent inline-Skill adapter fixture

Both27575931 and92420eb0 complete Quality runs passed all steps:13237 Python
tests,729 frontend tests, production frontend build and2 browser tests. A
concurrent subsequent release633f5e59 exposed six new failures. Four semantic
repair cases stopped at their stale FakeAdapter signature after native
inlined_skill_names composition was added. The adapter now explicitly accepts
that published keyword, and RecordingAdapter asserts the exact Task Skill
tuple. All four original repair/replay scenarios passed on the real shared
633 HEAD in7.73s; strict lifecycle, bounded repair and receipt assertions remain.
Independent review accepted the two-line test-only change. The other two
failures concern redaction normalization and are repaired separately.
