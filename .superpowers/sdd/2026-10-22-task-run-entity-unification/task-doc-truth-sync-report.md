# Task/Run documentation truth sync (2026-10-23)

## Scope and evidence

Documentation-only update based on accepted records in `progress.md` through Task2D0 commit `324129193af8dc9920afa3461a4df8fc2cd35c29`. No application source, schema, tests, providers, production checkout, push, or deploy changed. Correction to my prior report: I incorrectly stated that `docs-truth-closeout-preparation.md` was absent. It is present at `.superpowers/sdd/2026-10-22-task-run-entity-unification/docs-truth-closeout-preparation.md`; I have now read it and used it to verify this fix. My earlier lookup statement was wrong; I do not have a substantiated reason for that miss.

The 2026-10-22 Phase 1 production details remain preserved as dated historical readback. No fresh production state was queried. The 2026-10-23 frozen source snapshot facts in the task brief are recorded as snapshot-scoped: runs row 190042 has `task_id=404225`, `source_type='meeting_alignment_runs'`, `legacy_key='2298'`, `job_id=4908`, and Task 404225 is absent. `('runs', 190042, 'tasks', 1)` is the foreign-key-check tuple whose final value is FK ordinal 1. No repair or runtime status is implied.

Accepted evidence recorded with bounded scope:

- Task2D0 `324129193`: native Reply queue/claim/input-refresh scope, 11 focused tests; independent SPEC/QUALITY approved.
- Task3C3 `bafff3ee8`: native execution/progress/receipt owner scope, 12 focused tests after Fix1; independent review clean.
- Task3D1 `d8cc184bc`: professional attempt owner contract/converters, 80 focused tests after Fix1; independent review clean.
- Earlier Task5 records include a frozen 267-second copy and a distinct two-cycle old-DAO cold-write proof. Neither is described as latest full-copy/current-release acceptance.
- The pinned upstream `3875c3796` was integrated locally through merge `4d0e01878`; there is no main push or deployment claim.

## Files updated

- `docs/architecture.md` and `docs/runtime-mechanism.md`: label Phase 1 production state as dated historical evidence; correct the FK tuple interpretation and record the frozen missing-Task fact; summarize accepted components and keep Phase 2 unactivated.
- `docs/task-run-entity-validation.md`: clarify the evolution from initial Task-only contract to assembled Task/Run/adoption/receipt contract; scope the 267/287 Task-only counts separately from assembled counts; cite accepted commit IDs and tracked test paths without linking ignored review scratch files.
- `docs/business-model-execution-progress.md`: replace the stale single status sentence with accepted bounded components, remaining Task1–6 gates, distinctions among prior copy/cold proofs and the latest release gate, the packed capacity wording, integrated migration/current-runtime work still open, and the exact local merge boundary.
- `docs/agent-claims.md`: claimed the six documentation/report paths before editing; this report records the result.

The two pre-existing dirty appendices `task-3c2a-adoption-forward-report.md` and `task-2d0-reply-claim-input-report.md` were left untouched and unstaged.

## Checks and boundaries

`git diff --check` passed. Focused local Markdown-link validation against both filesystem existence and `git ls-files` confirmed every newly referenced test path is committed; ignored review files are no longer linked. Targeted status checks confirmed the dates and identifiers above, that remaining Task4–6 gates are still marked open, and that no production, push, deployment, or full-suite result is claimed. No tests were run because this change is documentation-only.

Next: independent SPEC and QUALITY review of this documentation diff. The prepared `task-3e1-agent-run-lifecycle-brief.md` remains for the parent to resume after review; this task did not execute it.

## Fix1 response

Removed the three ignored review-file links from the validation document. It now cites only accepted commit IDs and tracked test files, and states the bounded review status without linking local scratch artifacts. Corrected the prior report's false statement about the preparation file: I have now read the file at the exact path above; my earlier lookup statement was wrong and I cannot substantiate why I missed it. Corrected the progress matrix to Task6 and Tasks1–6, changed “packed-network” to “packed capacity,” and named integrated migration/current-runtime work as still pending in Task2/Task3.

Checks in the assigned worktree:

- `git diff --check` — exit 0.
- Focused Python link and tracked-reference check across `docs/architecture.md`, `docs/runtime-mechanism.md`, `docs/task-run-entity-validation.md`, and `docs/business-model-execution-progress.md` — output: `local markdown links: 4 files; missing=0`; `new test references checked with git ls-files: 6; untracked=0`.
- Targeted `rg` status-term check — confirmed Task6, packed capacity and integrated migration/current-runtime wording; found no Task7, Task1–7, packed-network, or stale “not present” wording in the scoped files.
- No tests run; this is a documentation-only fix.
