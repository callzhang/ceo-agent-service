# DingTalk delivery verification repair

## Confirmed mechanism

Attempt15391's native reply response contains success=true, deliveryStatus=unknown,
referencedMessage.messageId (the incoming trigger), and result.openTaskId (the
new send task). The old verifier recursively searched every field for messageId,
found the incoming ID and immediately returned sent with empty task/status
evidence. DingTalkMessageHandler promoted that to verified; SystemExecutor then
recorded a completed action and done execution. This was a deterministic service
bug, not an Audit model decision or evidence of recipient delivery.

Two other acceptance shortcuts were found in the same verification path:
processQueryKey was treated as a sent native reply by the sender, and an accepted
duplicate UUID or generic finished status could be treated as delivery. None
establishes delivery. Outgoing IDs and query outcomes now use their actual native
result scope. Accepted task receipts survive unknown delivery and cold recovery;
no same-key send is repeated. Reconciliation of an explicitly unverified stored
effect remains uncertain rather than reusing the existence of a receipt as proof.

## Real readback and historical impact

The original15391 task was queried read-only. The provider now returns
result.sendStatus=SUCCESS plus the actual outgoing message identity; it is
different from the referenced incoming ID. Its completion is now supported by
real task readback, not by the old shortcut. No message was resent.

Initial live inventory found74 stored reply receipts with the exact false
verification shape: sent, empty open_task_id/status_result, but an incoming
referencedMessage and a real outgoing openTaskId. Original-task queries confirmed
62 deliveries. The other12 (dated September24–October6) returned server1001,
Task not exists; this proves unavailable status, not no effect or successful
delivery. A bounded historical-message read returned no exact match for one;
the next read hit RATE_LIMIT_ERROR and stopped. No delivery assertion or resend
was made for those12.

The explicit repair preserves raw provider output and previous verifier/state
inside the service receipt. Confirmed receipts retain completion with real task
query evidence; unverified receipts retain the accepted action as uncertain and
withdraw same-generation task completion. Original Agent runs and Attempt rows
are unchanged; no new generation is queued. The maintenance script consumes
captured native queries and limits each batch to10. It is never called by startup
or a read API. Existing newer task generations and active tasks are not overwritten.

## Verification and release

Regression tests reproduced the original premature success, task-query bypass,
metadata/status confusion, native process-key shortcut, duplicate-acceptance
shortcut, stale completed display and historical receipt reuse. 454 focused tests
pass, including cold recovery from pending to successful without redispatch and
end-to-end uncertain System execution. Changed files pass Ruff.

A verified online database backup was used for a disposable copy. All74 explicit
corrections applied;12 current closed tasks became failed/external_action_uncertain
and their registered executions became uncertain. All run rows and historical
Attempt rows hash-identically to the original backup, foreign-key findings are
unchanged, integrity_check is ok, the normal Store reopens successfully and a
second application changes0 rows. This is copy evidence; production deployment
and small-batch receipt correction are recorded below after actual readback.

The final recovery regression also reproduces the old immutable-result conflict
when a corrected unknown receipt later gets positive readback. The existing
execution writer now promotes only an ambiguous receipt to sent for the same
action/target and identical raw provider receipt, retaining correction history;
it still rejects changed provider results. A real failed delivery remains failed,
and is not relabeled as unknown. The first deploy was stopped while still waiting
for quiet, before any production checkout change, so this recovery correction
ships with the delivery-verification change.

## Pre-authorization deployment checkpoint (historical)

Code is pushed as411a4f36 plus recovery correctiondec96517. The normal formal
deployment waited30 minutes and exited with “service did not become idle;
nothing was changed” at2026-10-14T05:35:08Z. Production remains9a2a23fc;
historical correction has not been applied live. At that checkpoint, authorization to interrupt in-flight work via
the existing --skip-quiet-wait deployment option was pending. No manual
launchctl/kill, production edits, sends or historical-state writes were performed.

One additional old-code receipt appeared during the quiet wait. Its original
send task also reports SUCCESS. The prepared correction set is now75 receipts:
63 confirmed and12 unverified, with0 task/conversation identity mismatches in
the63 positive queries. The74-row copy proof remains separate from this later
positive readback. Task-owned temporary backup, copy and captured queries remain
available for the authorized release; clean them after actual deployment,
bounded live correction and readback have completed.

Restart regression: the recorded-delivery startup recovery previously completed even explicitly corrected ambiguous/failed receipts. Two regressions reproduced that promotion. The three recorded-delivery completion/projection readers now exclude explicit non-sent verification states, preserving legacy independently verified readback behavior. Live correction waits for this recovery fix to be deployed.

Historical terminal Attempt or settlement projections cannot override a current external_action_uncertain/external_action_failed task error. A later verified provider result uses the normal execution completion writer. Original Attempt and settlement history remains unchanged.

Attempt detail and History attention prioritize the current failed task delivery error over historical Agent success summaries or a stored sent-reply row. Uncertain delivery is displayed as unconfirmed, while provider failure is displayed as failed. Historical Agent explanations and original raw receipts remain available as historical evidence.

## Production acceptance — 2026-10-14

Derek authorized immediate formal deployment with --skip-quiet-wait and recovery
of in-flight work. The initial deployment timed out its health wait and rolled
back to 9a2a23fc without changing these receipts. Subsequent formal deployments
succeeded; final runtime commit is d9d34093 (including d55a21b8 and e23544c4
startup/projection corrections). No manual process control or production source
edit was used.

A final 76-receipt correction set was applied in batches of at most10 after a
fresh verified production backup.64 original send tasks returned SUCCESS, with
matching original task/conversation identities;12 returned Task not exists and
remain unverified, not proved failed deliveries. Their current tasks are
failed/external_action_uncertain and their registered executions/actions are
uncertain. Original raw accepted reply responses are preserved, including their
initial unknown delivery status. Corrected wrappers retain original verifier,
previous execution/task states and the provider query. This maintenance did not
send messages, create new Agent revisions, or enqueue these12 tasks.

The live scope's original run rows and preexisting historical Attempt rows are
unchanged. quick_check is ok; reapplying all76 proofs changes0 rows. A final
inventory finds0 receipts with the original malformed native-reply verification
pattern. The final disposable-copy check applied76, changed0 on repeat, passed
integrity_check and preserved all original run/Attempt rows.

Attempt15391 is now legitimately done: its stored verification names the
original outgoing openTaskId and the native status result contains
sendStatus=SUCCESS with outgoing message msg557iduuezBrwdZS7Yx4zeg==.
All12 uncertain Attempt details were read through the live API after the final
restart: each remains failed, its message and Attention reason say the external
or delivery result is unconfirmed, and requires_decision=false. Historical
Agent success summaries remain historical text and do not override this display.

Validation: initial broad focused set454 passed; latest receipt/History/detail
set71 passed, receipt/caller set29 passed and existing Store recovery selection7
passed. The new failing regressions were reproduced before implementation.
Ruff and diff checks pass. No whole live-tree test suite was run.

Readback at 2026-10-14T07:27:13.212635+00:00: main PID 73677, healthz ok, service running, quality health healthy with 0 violations; Attention 12 groups and History 20 items readable. Reply task failure count15 equals prior3 plus the12 explicit uncertainty corrections. Reply, meeting and scheduled dispatcher jobs are running/requeued through normal recovery; active business jobs have not all finished. Native history availability remains separate (29 readable, 442 unavailable).

The normal backup workflow now retains a verified 2026-10-14 production backup reflecting the corrected receipts. Task-owned temporary copies, captured query files and diagnostic samples have been removed after readback; correction provenance remains in the production receipts and this acceptance record.
