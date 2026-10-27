# Business entrypoint cleanup: no-DDL batch 1 — 2026-10-26

Approved scope: remove20 unused/test-only Store entrypoints, RecentFollowUpCandidate,
its exclusive helpers, the Attempt transcript-owner wrapper and optional single-call
runtime lookup branch. Tests use current transaction/domain/read owners and
test-only historical seeds. No compatibility alias, runtime fallback, schema change,
historical data deletion or outbound/Audit/retry policy change.

Source baseline:442a5951c95076a0402a1a9e8c2063912d5a6269. Production baseline is
read independently; source push, installed runtime and business completion are separate.

## Execution

1. Store owner deletion and current/historical fixture conversion: SPEC and QUALITY approved.
2. Attempt reader simplification:53 focused cases pass; SPEC and QUALITY approved.
3. Both units independently SPEC then QUALITY approved; exact476 schema comparison
   passed. Combined19case integration and final review approved.
4. Standard exact-target deployment, actual UI/API readback and verified backup/owned
   temporary-artifact cleanup: complete.

Keep all historical facts/provenance/results/receipts and actual current batch readers.
The3 terminal rejected Todo evidence candidates,242 terminal legacy sync outbox rows,
2477 old follow-ups and65 send-attempt facts are not approved for deletion.
This batch does not retire Project title/anchor_type, claim fields or current DDL factories.

Evidence and exact test scope will be appended after verification.

## Approved Store entrypoint retirement

- `_business_source_document_id`
- `_projection_failure_code`
- `get_todo_evidence_candidate_by_work_summary_input`
- `create_follow_up_draft`
- `update_follow_up_draft`
- `get_follow_up_draft`
- `list_recent_follow_up_candidates`
- `count_sent_follow_ups_for_owner_since`
- `count_sent_follow_ups_for_conversation_since`
- `upsert_todo_evidence_candidate`
- `get_todo_evidence_candidate`
- `mark_todo_evidence_candidate_enqueued`
- `mark_todo_evidence_candidate`
- `list_task_todo_sync_outbox`
- `get_task_todo_sync_outbox`
- `create_business_task_signal`
- `list_business_task_signals`
- `create_business_task`
- `link_business_task_evidence`
- `get_business_project_candidate`

RecentFollowUpCandidate and the three exclusive helpers also leave current code.
Existing raw historical records and batch/detail/History readers remain. One real
script caller was found during refreshed inspection and switched to the existing
transaction owner before deletion; no production replay was performed. Three
predecessor-code literal calls are intentionally retained only in the frozen
old-checkout subprocess test.

Current test setup uses three thin transactional seeds, a typed Signal snapshot
and current Project-candidate read snapshot. Legacy history seed functions write
only synthetic frozen records. They do not implement old deduplication, lifecycle,
retry or outbound policies and are not application compatibility APIs.

Store baseline471, after core500 and selected192, candidate16, restoredHistory1
and exact affectedHistory14 are overlapping groups, not an additive full-suite
count. The original broad History run reached the fixed300s limit and is not
green. One removed mixed old-workflow test contained current History assertions;
SPEC required their restoration using an accepted historical fixture, preserving
source, record, project, Todo, input, decision, category, status and title.

[Machine-readable evidence](validation/2026-10-26-business-entrypoint-batch1.json)
records each scope and the production fact/schema baseline. Final publication and cleanup are recorded below.

## Attempt reader contract

`_agent_sessions` reads Run session IDs directly, preserving role order, trimming,
deduplication, available/unavailable behavior and a legitimate session owned by the
Attempt itself. The unused old Attempt-shaped SimpleNamespace, empty tool-event
placeholder and unused range conversion leave the reader. Runtime calls require
the existing batch method; tests already provide the current interface. No extra
fallback is supplied for incomplete fake Stores. The real missing agent_run_id to
Task-owned Run lookup remains unchanged. Baseline and after are53 cases; neither
review launched extra tests or broadened behavior.

Root integration19passed8.48s; full changed-file Ruff(50Python files) and diff check
passed. Final independent integration review approved the bounded runtime diff.
Standard exact-target publication and post-deploy API/UI/fact readback completed as below.

## Production release and closeout — 2026-10-26 local time

Source1e6db7a871e02a1dc8c6de1b501957e265b6dc09 was pushed after both units'
SPEC/QUALITY reviews and final integration approval. Standard `python -m app.deploy
--target-commit 1e6db7a871e02a1dc8c6de1b501957e265b6dc09` passed its mandatory
exact-target gate under unchanged2GB/300s limits, used the normal quiet window,
verified backup, import/health checks and deployed successfully. There was no
skip-quiet flag: in-flight work was0 at launch. Another task had already installed
442a5951c during development; the final pre-release snapshot was rebound to that
version/PID30325 before this release. Operationdeploy-b38ec509-a056-4925-89a3-4a9adb465daa
records successful target installation; newPID34587.

Three deployed code files match the reviewed worktree byte-for-byte. All20Store
methods, DTO and Attempt adapter are absent; RuntimeAttempt lookup is the required
batch method. All476 schema declarations and the same8historical groups remain
exactly equal across initial/ready/after snapshots, withFK[] throughout. This
includes3Todo evidence candidates,65historical follow-up attempts and242terminal
legacy outbox records. No database field/index/trigger/schema or business record is retired.

Production `/healthz`,Status,Attention,History and Project APIs return200. Project6
JSON/HTML retains13Todos,24Updates and3rejected evidence candidates; the evidence
skip decision, source and Todo identity and existing DingTalk receipt were expanded
in the browser. Current Tasks/attention cards, Attempt16137, Consumer/Audit detail,
two role sessions and exact ranges0..89/0..69 are readable. History's filtered
second page renders21–40 of6383records; actual Action/receipt source is available.
No send, recall, rerun or feedback-save action was clicked, and no production test
Task was created. The owned browser tab is closed.

Failed summary remains25before/after this release; runtimeAttention1 and the prior
system-health findings (including nonretryable Email classification) remain, not a
new regression or a claim of a fully green service. Current service healthz isok.

The startup daily backup replaced the operation backup. The new02:44:53UTC daily
file was independently rechecked with fullintegrity_check=ok andCEOB completion
marker; after readback it was deleted under the workflow directory lock after
matchingmtime/size. Its exact514969600byte path is absent. The gate clone and its
pytest4557root were already absent; this task's explicit temp root was removed
without following121nested symlinks. Production DB/native trajectories and concise
committed evidence remain. Cleanup is not a database-size reduction claim.

Current declaration factory simplification and Project/claim-field retirement are
separate next batches and have not been implemented here.
