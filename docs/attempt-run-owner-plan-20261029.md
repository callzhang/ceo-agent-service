# Attempt / Run provenance and summary ownership

User-approved scope: unify existing facts, preserve orphan and distinct facts exactly, no old-runtime compatibility, no invented business fields or policy changes. Prior no-DDL batch is complete. Source597da9f0e Store29.1/Email54; target Store29.2/Email54.

## Task1: Freeze data and field ownership
Existing Attempt JSONB57cells: reason7, summary21, session22, start52,end53, Run55. Summary21 is final business outcome, not universally a model Run result. Equal raw reason cell7 is represented by NULL and resolves to summary21. Distinct reason including empty string remains literal. When all three provenance rawcells exactly match the canonical TEXT/INTEGER values of the actual linked terminal Run (completed/failed), set cells22/52/53 NULL together to reference that Run. Otherwise retain full original cells. No RuntimeAttempt inference or new fields. Preserve rawcell differences even when projected values compare equal. Freeze source schema catalog.

## Task2: Current runtime single-owner writes and reads
Current-only projection resolves NULL provenance triplet from the exact linked Run; explicit local triplets retain their own fact. Reason generated column resolves NULL cell7 to summary21. Do not add old-layout/schema branches. Normalize inserts and relevant updates; partial updates and rebinding must preserve the prior logical values and distinct facts. Finalizer accepts only Run reference for provenance and one existing final summary from orchestration, remove repeated worker tuple and summary inputs after switching all actual callers/tests. No Audit, authority, retry or execution semantics change.

## Task3: Explicit forward and current-data inverse
Rebuild actions with exact target declarations, preserving all stored columns/types, IDs/high-watermarks, other-kind payload bytes, all tables and preexisting FK findings. Only compact raw exact matches; inverse reconstructs current referenced values and equal reason from their current owners, preserving untouched rawcells/header widths. Preserve padded/noncanonical cells as local facts if canonical reference would not roundtrip. Do not snapshot business values in permanent mapping/backups. Exact source/target fingerprints only. This replaces the formerly supported28.5→29.1 publication transition: switch current drain/receipt tests to29.1→29.2, retire the obsolete converter and its dedicated fixture/tool/tests after all callers switch; do not add a chain of old runtime versions. New runtime rejects old source; standard release performs explicit transition.

## Task4: Validation and release
RED/GREEN focused tests; latest production-derived copy forward; create new native tasks/runs/attempts and updates with substitute sends; inverse then real old-code cold start read/write, forward again; compare all required facts/types/references/sequences/FK. Standard fixed10min/2GB gate and coordinated deploy, read health/PID/queues/Attention/History/Attempt/session/results/receipts; validate then remove workflow backup and owned temporary artifacts, update docs/claim.

## Completion

All four tasks completed; standard release `1e8d793ab6ca7c1cd78147e648afa6f320de01b6`, natural quiet and Store29.2/Email54 production readback passed. Current-data inverse and real previous-code cold read/write passed on a production-derived copy. Workflow backup and task-owned temporary artifacts removed after verification. Exact evidence and test/release failures retained in [validation](attempt-run-ownership-validation-20261029.md). No full-library optimality claim or native-model/outbound acceptance claim.
