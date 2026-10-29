# Attempt review repairs, 2026-10-14

Scope: 15452, 15446 and 15436. These are deterministic repairs to source provenance,
message chronology, existing project accounting scope and scanned-document reading.
The first-curve threshold, original reply target, source digest, bounded revision
cycle and authority to perform OA actions remain unchanged.

## Actual failure evidence

- 15452: the queued approval card points to Derek's original node. A later read of
  the same message renders a subsequent approver's node. Consumer output has no
  writable `source_bindings`; the service captures those bindings after the result.
  Asking Consumer to replace a historical source binding cannot repair the candidate.
- 15446: the successful native return envelope uses `dingOpenErrcode=0`, not
  `errorCode`. The original response timestamp and exact matching native return
  record permit read-only verification; the separately owned OA receipt fix is
  included through commits 7fb82e2b and 369edadb. The financial issue is independent:
  current revenue 955000 and total cost 713060 imply 25.334%, while the prior trial's
  8200/7100 imply 13.415%. Trial costs 1500+5600 are already included in the other
  cost component. They cannot be added again or classify the whole application.
- 15436: a reply at 14:46:26 follows the 14:41:07 trigger by 319 seconds; the
  14:47:48 follow-up is a separate request. The scanned settlement attachment is
  one actual PDF page with no text layer. A technical OCR gap is not missing
  applicant material.

## Focused verification

The new provenance regression and scanned PDF tests fail on the pre-change
implementation and pass on the repaired implementation. Focused checks also cover
mixed PDF pages, OCR failure, actual attachment identity, material bounds, native
message recovery, meeting recovery, scheduled recovery and claim contention.

The real settlement PDF, Drive node 237492518210, was downloaded and read through
exactly the repaired reader and the installed OCR client: one page, 495 characters,
confidence 0.9543, no truncation; source SHA-256
`20fb02aee28b7807edc813654609ecc06663fc836c9fccbd3c66e8f23c1c5cf6`.
The working PDF and OCR export were deleted after this verification.

The development checkout's saved User Prompt uses retired slots. Production's
saved User Prompt is `{{task_context}}`. Tests that load configured prompt text
explicitly select the repository default matching production; the unrelated
saved development template was not changed.

## Fixed native comparison boundary

`context.v1.json` preserves the first comparison including the candidate regression
that treated prior replies as unable to settle the original request. The authority
sentence was clarified, and `context.v2.json` records a new fixed comparison:
four cases, identical baseline/candidate facts and subjects, gpt-5.6-luna/medium,
no tools or provider writes. All eight v2 outputs are parser-valid, digest-bound
and meet their outcome criteria. Manual semantic review confirms that the current
card does not replace the old node, prior replies can settle the old request,
unsupported completion is returned, and a changed reply target is rejected.
Both baseline and candidate satisfy the v2 semantic controls; this comparison
proves those controls survive, not a statistical improvement rate.

Financial Audit v1 preserves the baseline's incorrect repeated cost accounting
and the candidate's correct 25.33% current-project calculation. Its main-case
outputs and a single bounded digest correction failed strict wire validation;
they are diagnostic reasoning evidence, not accepted Audit results. The genuine
13.4% low-margin control retained the special-approval branch. The unknown-scope
control correctly asked for project facts, with an additional confidence adjustment
that the frozen expected outcome did not allow; the v1 score remains unchanged.
Consumer v2 preserves one parser-valid baseline reproducing the repeated-cost
error and five CLI failures without result events. The exact candidate-A diagnostic then produced a parser-valid response that still
added the component twice. The initial Skill candidate therefore failed the
business criterion; no financial acceptance is inferred from that version. All raw outcomes and case versions
are kept for review; production contracts are not relaxed to make an eval pass.

## Historical maintenance boundary

Historical runs and final human OA approval remain immutable. Receipt recovery
uses the existing journal and native read-only reconciliation. Settled-task closure
uses the existing Store operation with explicit current provider evidence; it does
not manufacture a successful Audit or replay an external action. Final live
receipt, task state, runtime revision and deployment readbacks are recorded below.

Financial Consumer v3 freezes a revised financial basis ledger and the same three
case facts. All six one-shot baseline/candidate outputs exited 0, used no tools
and passed the production Consumer wire parser. The baseline main case repeated
the extra subtraction and false special-approval request; the revised candidate
computed 25.33% and ended only this unsupported low-margin question, explicitly
leaving other project materials for review. Both genuine-current-low-margin sides
retained the special-approval return. The revised unknown-scope candidate requested
project facts without inventing a first-curve classification or asking the
applicant for company policy. Two successful candidate calls carry a nonfatal
native system-Skill installation warning, retained in stderr; project Skill bodies
were exact inline frozen sources, so the warning does not establish live installed
Skill loading. Runtime publication is verified independently.

Final project Skill SHA-256:
`950d752ae159737752dc46285c6896924b45b17a7b2119c12e98bcec7f24ba66`.

## Live readback

Formal `python -m app.deploy --skip-quiet-wait` completed from d9d34093 to
5691f6c7. launchd PID was 34642 and `/healthz` returned ok. Runtime capture
published project Skill revision 159 (revision number 5), matching the final
950d752a source hash. No scheduled-task or runtime-config binding referenced
this project Skill, so no frozen historical input was replaced.

A fresh download read through the deployed production `app/agent_cli.py` returned
the same original settlement PDF digest, one complete OCR page, 495 characters
and confidence 0.9543. Maintenance then used the existing normal Store and
SystemExecutor paths: 15446 execution179 became done and action175 verified from
its existing native return proof; 15452 settled the original Derek node, leaving
the process's other-approver nodes untouched; 15436 settled only the explicit
request to repair scanned PDF reading, without approving materials or payment.
All three task rows are done. The 26 original run rows had identical pre/post
SHA-256 a79f969bc7a42c349f26ebcb9a618309c3139c5567df6e343bbb21f2483deccb.

Normal settled-input compaction clears the Attempt preview text and updates
its provenance/payload. It does not rewrite original runs or review decisions.
15452's full original trigger remains byte-identical in candidate660 source
bindings; the compacted input points to input404820 and records the same 1673-byte
source digest 20ea1490bd1ceeea7c309b6a24f8f86ac00d25204e23fed2e1db4efeeefdf841.
The Console details show 15436/15452 done without a human decision, and 15446
System execution done with one of one actions verified. No new message or OA
write was dispatched during this maintenance.

Recovery readback identified two old-process failures. The old-table reply query
was from the pre-deployment loaded process: task404869 automatically retried with
the deployed actions projection, completed Consumer356308/Audit356315 and became
done. No redundant SQL patch was added. Meeting job404821 also recovered through
new completed runtime356313 and became sent. Its original persistence failure
was a prompt/codec mismatch: the prompt required an absolute profile source path
that the existing codec correctly refused. A follow-up input repair uses a
SHA-256 reference to exactly the same one-time injected profile text; it leaves
the codec unchanged and passed 91 focused tests including a RED/GREEN regression
and a final-message path rejection control.

Another interrupted task404777 resumed Consumer/Audit and reached System execution.
The initially read provider receipt was classified ambiguous and an exact
message search returned no evidence. Further inspection found the accepted native
send-task identity under the successful `result.result` response and the registered
`sendReceipt`. The exact native send-status query returned SUCCESS. A separate
receipt parser fix and read-only journal reconciliation then recorded the verified
effect: execution184 is done, action180 verified, and Task404777 done. No send was
replayed. Its seven original run rows retained SHA-256
386e123107fc3c8bac827d457bc707c8ae093a6be9b18c88d62d7afd46238064. Unknown/PENDING
and conflicting send-task identities continue to remain unverified. Health status still
includes unrelated historical warnings; endpoint health alone is not a claim
that every historical task in the service is resolved.

The shared backup workflow retained the newest verified database backup at
`~/Library/Application Support/ceo-agent-service/backups/auto-reply-before-send-reconcile-2026-10-14.sqlite3`;
temporary rehearsal databases and downloaded files are removed at task completion.


Second formal deployment installed eb797e6b, containing the independently committed
meeting-input, send-receipt/reconciliation and stop-completion fixes. launchd PID
78336 started at 2026-10-14T10:01:20Z; the old supervisor and worker PIDs had exited,
and healthz returned ok. At 10:06:16Z no Agent/runtime failed run had been created
since that restart. All five inspected recovery tasks were done with cleared task
errors. Console Attention and individual History detail reads completed, including
15446 System done and one verified action. Broad Status/History list polls exceeded
their request budgets; current queues were independently read from Store18 and
individual affected details were verified. These list latency limits do not prove
that unrelated historical warnings are resolved.

Focused verification of the follow-up changes: 91 meeting tests, 63 delivery/
executor/source-revision tests, and 68 updater/schema tests passed. Each repaired
mechanism has a regression that failed before its change; no full-suite run was
started while the service was live.
