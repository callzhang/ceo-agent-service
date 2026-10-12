# Attention follow-up — 2026-10-11

This follows the SQLite repair readback. The user requested documentation updates
and investigation/repair of the four remaining Attention entries. All live state
changes use the existing Console bound supplement/rerun APIs. Historical runs,
candidates, reviews and provider outcomes remain intact.

## Initial state and refreshed evidence

| Entry | Initial state | Verified finding | Service outcome |
| --- | --- | --- | --- |
| Reply task 386349 / Attempt 15269 | `failed`, `audit_revision_exhausted` | Complete current-generation native runs 25757–25765 were read. Audit 25765 claimed a numeric target ID although its actual input contains a string. Prior comment action 72 is already provider-verified. During the new review, the original OA task became canceled and ceased to belong to the principal. | Current task `done`; Consumer 25807 `no_action`, Audit 25808 approve. The obsolete task was not replayed. |
| Attempt 15196 / task 386297 | `needs_human`, missing explanation | Exact later message `msge2EGAQkgQ7B/p1D7QUDz/A==` from the principal supplies the reason and current acceptable approach. A bound supplement requested source verification. | Current task `done`; Consumer 25798 `no_action`, Audit 25801 approve. No new chat action. |
| Attempt 15203 / task 386305 | `needs_human`, financial scope/metric | Complete subsequent chat range and both later PDF reports were inspected. Local PDF extraction confirms the reports' period and accounting definitions. The native online-document reader cannot read these PDF file IDs. Audit 25805 still required confirmation of business scope and the requested metric rather than substituting a report's net-profit ratio. | Current task `done`; Audit 25809 approved one clarification to the original source, action 85 verified and message read back. The business scope/metric answer remains pending from that source. |
| Attempt 15201 / task 386304 | `needs_human`, compensation boundary | Original message and complete later conversation were read. No later principal decision supplies a cap and its compensation basis. | Remains a genuine reviewed human question. The principal was asked for the cap/basis or an explicit instruction that no cap is set. No number or compensation commitment was invented. |

Message queries before the clarification completed without missing pages or
failures: 57 messages for the compensation conversation, 25 for the product
conversation and 12 for the financial conversation. Exact original-message
retrieval found all three triggers. The later financial send was read back by
its exact provider message ID `msg58fAgX27a6GNoxznCgehNg==`; this targeted send
readback is not an assertion that the entire later conversation was paginated.
Original unqualified provider message timestamps are not assigned a timezone
in this record.

## OA recovery boundaries

The original successful comment remains action attempt 72 / execution 71.
Its exact content hash, principal identity and operation timestamp are retained
in the existing verified receipt. The historical note describing an uncertain
comment is an older checkpoint, not the current persisted result.

The new approved candidate was rechecked by the System Executor. Action attempt
84 returned `oa_task_state_conflict` / `business_state_changed`: its target task
was canceled and a different task belonged to another approver. No successful
new OA action is claimed. The ordinary feedback loop reread the changed source,
produced `no_action` and obtained Audit approval before completing the task.

## Console rerun performance repair

Commit `3195cd3b` changes only the manual-rerun terminal-run lookup to
`load_events=False`. The existing refusal check uses structured error metadata;
native session discovery and event/result hydration were unnecessary on this
queueing path. Refusal behavior, original-input checks and appended execution
generation are preserved.

The live rerun HTTP request timed out after 30 seconds, but authoritative state
confirmed its new generation was already queued. It was not submitted twice.
The unnecessary native read is a confirmed code-path issue; this record does
not attribute every second of that request to file I/O without a timing profile.

Regression `tests/test_rerun_metadata_read.py` failed in both normal and refused
cases before the change. Afterward, that file plus the existing rerun/provider
refusal selection in `tests/test_audit_web.py` passed all 10 cases. Architecture
and runtime documentation were updated in the same commit. No new audit,
authorization, refusal or effect-reconciliation policy was added.

## Latest checkpoint

The refreshed Attention endpoint has one remaining item: Attempt 15201. The
other three service tasks are terminal `done`. Financial clarification delivery
is verified, but its requested business answer is not yet received. The missing
compensation input is required to continue the remaining item through its
original bound supplement, Consumer, Audit and provider readback flow.

Production deployment completed through `python -m app.deploy --skip-quiet-wait`,
as previously authorized by the user. The preceding normal deploy was
interrupted only while its own CLI was waiting for quiet, before production
advanced. The formal deploy advanced `29e01567` to `3195cd3b`; supervisor PID
changed from `53962` to `34205`. No manual launchd restart was used.

Post-deploy health, Status, Attention and History data endpoints returned 200.
Observed Status latency was 0.937 seconds, Attention 0.125 seconds and History
0.853 seconds. Status had zero processing, failed and retryable entries; no
Agent run remained running at that snapshot. The three handled tasks remained
`done`, and the compensation question remained the single Attention item.
There was no new `SQLITE_BUSY` or `database is locked` log after web PID `34209`
started during this readback. Two scheduled items were normally due; this is not
a claim that all background queues were empty.

Private chat
exports, PDF originals/extracted text and native trajectories are temporary
task-owned working artifacts and are removed after the operational record is
saved; their full content is not copied into this repository.
