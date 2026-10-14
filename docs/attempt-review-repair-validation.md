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
