# Mainline and typed-storage integration plan

Required workflow: subagent-driven-development; one separately claimed author,
fresh SPEC then fresh QUALITY. This is a preservation/integration unit, not
permission to design new audit, safety, authorization, retry or recovery policy.
Carry the already-deployed behavior exactly. Do not edit shared/production
checkouts, overwrite other authors, or merge during another unit's live tests.

## Observed refs and evidence limits

Read-only comparison at2026-10-17: isolatedHEAD8eae51e05, origin/main4f3d52a0,
mergebase44ee956d10;89 upstreamcommits,34changedappfiles. Primary checkout
HEAD4ff8e547 has separate local commits, not the deployed predecessor; preserve
its ownership and do not silently publish or reset it. Refresh allrefs before
implementation, since concurrent main work can advance.

AST navigation counted101 changed/added/removed upstream functions:89 current
matchbase,12 divergefromboth. This is navigation only, no merge correctness
claim. Current13 actual-copy receipt6b8c310c proves exact frozen schema/40family
storage behavior, not complete latest-main runtime behavior.

## Order and ownership

1. Finish current author's unselected canonical component and its separately
   frozen review first; root then selects the next coherent unit boundary.
2. Before integration, preserve all reviewed redesign work as owned commits or
   an explicitly verified patch/snapshot in the existing worktree. Inspect
   claims and unrelated dirty hunks; do not stage them, reset or rebase others.
3. Inventory refreshed upstream full delta including imports/constants/helper
   methods/tests/docs. Bring the whole required helper graph, not just12function
   bodies. Keep unselected/source oracles and selected typed-schema invariants.
4. Integrate overlapping source SQL with existing finite typed projection and
   mutation primitives. Never recreate old native tables or change source
   constructors into fake target oracles solely to pass a current test.
5. Focused native/current and upstream regressions, frozen SPEC then QUALITY.
   Separate database migration/capacity/nativeCLI-fake/UI/release gates remain.

## Twelve overlap functions and exact intent to preserve

| Current scope | Existing upstream behavior | Current storage behavior |
|---|---|---|
| audit_web._queue_attention_rows | Exclude direct-unsubscribe failures from manager Attention; current pending-OA wording | Keep Tasks/Actions/State projections. Translate newly added old status/index query. Needs action field from summary method below. |
| cli.build_parser, cli.main | Existing import-meeting-asr-transcript preview/apply path plus helper | Keep removal of backfill-task-memory-context. |
| meeting_alignment._analyze_meeting_job | Existing persisted ASR override from _stored_asr_transcript_paragraphs | Keep source registration without removed embedding_client. |
| store._enqueue_manual_rerun_reply_task_in_connection | Existing ManualRerunConflict for active run | Keep task8 insert/update projections. |
| store._history_items_query, store._operation_logs_base_query | Existing terminal OA source skipped projection via TERMINAL_OA_SOURCE_SKIP_SQL | Preserve typed Business/Actions/Configs and terminal_id CTE optimization. Carry actual upstream constant. |
| store.list_current_unresolved_problem_attempt_summaries | Existing action field in SELECT/result for direct_unsubscribe filtering | Preserve Business task projection. |
| store.reconcile_valid_needs_human_projections | Existing _is_actionable_needs_human_candidate semantics/helper | Preserve typed Business and task8 update. Do not redesign candidate policy. |
| store.retry_failed_effect_free_email_unsubscribe_tasks | Existing execution-pending marker prevents reopening | Preserve typed Email Actions/Tasks and translate State marker read. |
| store.begin_direct_email_unsubscribe_execution | Existing exact bounded transient-browser retry marker semantics | Translate real State upsert with existing upsert_state source-PK semantics; insert_state alone is not an upsert. Carry record_direct_email_unsubscribe_retryable_failure and UnsubscribeOutcomeUnresolvedError. |
| web_api.registration.register_console_routes | Existing task-state/generation-bound skip endpoints and skill options for History rerun | Preserve Business task transaction for CRM. Carry skip_unresolved_reply_task helper. |

This table counts12 functions because CLI and History each contain two.
It does not enumerate all upstream behavior or authorize ignoring other changed
files. All required helper callers, imports, tests and docs accompany their
existing upstream behavior; no new policy is introduced by shared utilities.

## Focused regression pointers, not passing claims

Refresh exact names in the new upstream tree. Recorded upstream pointers:
History actions test_history_actions.py:102; Attention test_audit_web.py near12080;
retry test_email_unsubscribe_direct.py:462/535; test_store.py:5018/5276/13314;
CLI test_cli.py:791/819; ASR test_meeting_alignment_source.py:387 and
 test_meeting_recovery_safety.py:69; task skip test_console_reply_task_skip.py:7.
Run only relevant test files/groups with isolated temporary/cache roots; no
full serial suite while service is live. Exact schema/default oracle tests and
current/inverse facts must be rerun when integrated code actually affects them.

Final native Agent CLI with fake sends, UI behavior and deploy readback must run
on the integrated release, not on the earlier storage-only frozen candidate.
No release until final11/capacity/whole field disposition/source lifecycle and
current-fact rollback gates also pass.


## Follow-up deployed History attribution commit

Read-only preflight later observed production1fcbddda5fe1e13d7c71cc0462e428d66e336370
(clean tracked checkout), origin/main2adf9bbc1 (deployment evidence successor).
The one runtime commit after4f3d52a0 changes attempt_what_happened.py,
reviewed_candidate_store.py and web_api/attempts.py, with focused tests and
architecture/runtime documentation. It separates new verified external effects
from older reused receipts using the already-existing first_agent_run_id and
provider operation time. Preserve this complete read-only behavior in final
integration; do not regress receipt attribution when selecting typed storage.

Six Store/Email/Actions/source-migration module Git blobs are identical across
the two deployed predecessors. Preliminary baseline metadata is recorded in
2026-10-17-email-canonical-production-baseline-refresh.json (47b27303).
Raw production has116 ordinary tables plus1virtual/4shadow, Store2026-10-13.5
and Email51. Its native Email triggers still reference native action families;
the canonical capsule79f04ccf intentionally describes the *intermediate13*
graph after earlier Action translations. An initial read-only comparison of
those two stages stopped and is retained in the receipt. Do not reinterpret that
stage mismatch as an unknown production declaration or silently waive the
canonical source check. Full fresh-copy coordinator must validate raw source
then match exact intermediate declarations at their actual migration boundary.

The next root copy must archive the refreshed genuine1fcb predecessor and
reverify live HEAD/freeze before source access. This observation is not full
source/caller/copy/release acceptance. Refresh refs again before integration
and include later upstream changes; do not alter active caller-owned files.
