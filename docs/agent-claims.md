| codex-private-business-skills-20261028 | app/agent_cli.py approved private-script digest verification (explicitly authorized); ci/reviewed-skill-scripts; ci/shared-skills company packages and generic template sanitization; company-policy-only tests/docs; explicit consumer release source list/manifest and related tests; related gate entries; tests/conftest.py public Skill fixture default only; public/private source-dependent integration fixtures; two stale current-DWS documentation assertions after main merge; migration validation/docs; private source package/test snapshot; isolated acceptance instance | Execute authorized private company Skill migration using current installed bytes; keep public service generic and native installer unchanged; no historical force rewrite or OA replay. | Implementation and private acceptance complete; source/fixture ownership released. Exact-target deployment and readback tracked in PR337; no production overwrite or business replay. |
| codex-task6-run-map-retirement-20261028 | app/run_entity_definitions.py; app/run_adoption_definitions.py; app/schema_fixtures/store26_1_email54_catalog.json; app/schema_fixtures/store28_1_retirement_tables.json; app/schema_fixtures/store28_2_session_state.json; tests/test_run_entity_definitions.py; tests/test_run_adoption_definitions.py; tests/test_task_run_entity_assembly.py; tests/test_feedback_owner_schema_release.py; tests/test_current_declarations_independent.py; tests/test_signal_source_owner_storage.py; tests/test_reply_receipt_reference.py; tests/support/store28_session_source.py; tests/support/historical_action_storage.py; tests/support/predecessor_source.py; tests/test_deployment_drain.py unsupported marker case only; tests/test_current_action_schema.py predecessor case only; app/predecessor_database_schema.py; app/scheduled_task_notification_schema_source.py; app/model_retirement_schema_source.py; tests/support/fixtures/store26_configs.json; tests/test_model_retirement_schema_release.py; tests/test_conversation_session_schema_release.py; tests/test_signal_source_owner_schema_release.py; tests/test_store_signal_source_owner_release.py; tests/test_action_reference_trigger_schema_release.py; tests/gate_files.txt; app/model_retirement_schema_release.py; app/conversation_session_schema_release.py; app/scheduled_task_notification_schema_release.py; app/signal_source_owner_schema_release.py; app/action_reference_trigger_schema_release.py; app/reply_receipt_reference_schema_release.py; tools/validate_model_retirement_copy.py; tools/validate_conversation_session_copy.py; scripts/validate_signal_source_owner_copy.py; own claim row | Retire closed source map regeneration and port Feedback release fixture; preserve native SQL, constraints, identity and receipt tests. Root owns integration and release. | Complete locally: 36 Run/assembly tests, 10 Feedback, 16 declaration/Signal/receipt, 3 historical Action, 20 predecessor-port and 33 drain pass; current/source exact 463 hashes and retained function ASTs match, Ruff/reference/gate paths clean. Root owns integration/release. |
| codex-skill-search-install-native-20261028 | app/business_skills.py; app/managed_skills.py; app/store.py repository install method only; new skill_repositories/discovery/installation modules and focused tests; web_api skill installation routes and registration; frontend Skill search/API/installed list/styles; bootstrap/installation and scoped docs/gate; tests/test_agent_cli_wechat_read.py unused-import integration hunk only | Execute approved Native plan through tests, branch review and standard deploy. Preserve peer work and unpushed private OA candidate. | Complete: PR335 merged; standard fixed-commit gate/deploy da5e1585 succeeded 2026-10-28; real UI/private-source API and new-process load receipts verified. 232 focused Python/72 frontend pass, two preexisting skips; full CI retains baseline failures. Private migration/OA candidate excluded. |
| codex-task6-conversion-retirement-20261028 | app/business_legacy_owner_storage.py; app/business_candidate_storage.py; app/business_candidate_definitions.py; app/business_schema_helpers.py; app/final_action_schema.py; tests/test_final_action_schema.py; tests/test_business_legacy_owner_storage.py; tests/test_business_candidate_storage.py; tests/test_business_legacy_field_retirement.py; own claim row | Retire closed Business conversion paths and final Action converter, preserve current owner and candidate storage behavior and port focused tests. No release, deployment or production action. | focused current storage/semantic import 69 passed; final converter pre-retirement 11 passed; Ruff and reference closure clean. Local handoff to root. |

# Concurrent agent file claims

## Current assessment implementation coordination

| Owner | Files | What | Since |
| --- | --- | --- | --- |

Several agents (Claude Code sessions and Codex) edit this working tree at the
same time. This file is the shared claim board: it exists so that two agents
do not rewrite the same file from different assumptions, and so that nobody
reverts committed work they did not author.

## Rules

1. **Claim before you edit.** Add a row below (owner, files, what, since) in
   its own commit or as part of your first commit touching those files.
2. **Read before you edit.** If a file you need is claimed by someone else,
   either wait, or ask through the owner's channel, or edit a different file
   and hand over the exact patch plan.
3. **Never revert a commit you did not author.** If a commit conflicts with
   your change, build on top of it; if you believe it is wrong, say so in the
   claim row and leave it in place.
4. **Stage only your own hunks.** The tree usually contains other agents'
   unfinished work. Use `git add -p`, or stage a blob built from `HEAD` plus
   your own edits; never `git add -A`.
5. **Restarting deploys the whole tree.** `launchctl kickstart -k` (and
   `scripts/install-auto-reply-agents.sh`) put every uncommitted file live.
   Before restarting, verify `python -c "import app.cli, app.worker,
   app.email_worker, app.service_supervisor"` and tell the other owners.
6. **Release the claim yourself.** Delete your row in the push that finishes
   the work, not later. A row with no activity (no edit to the row, no commit
   on its branch) for one day is abandoned: any agent may delete it. Git
   history keeps every deleted row.
7. **Do not develop on this tree's `main`.** Derek, 2026-10-23: this tree's
   `main` is only a copy of `origin/main` and moves by `git pull --ff-only`.
   Develop in a worktree or branch cut from `origin/main`
   (`git worktree add <dir> origin/main`), test there, and push from there. On
   2026-10-23 this tree was 1591 commits behind `origin/main` with two local
   commits that were never pushed: `python -m app.deploy` run from it crashed
   against the live database (`no such table: service_state`), and a merge was
   refused by 15 files holding other agents' uncommitted edits. Deploy from a
   clean `origin/main` worktree too. When this tree has to catch up, a
   temporary `git stash` is allowed for that one merge (Derek, 2026-10-23):
   stash, `git merge origin/main`, `git stash pop --index`, and say so here if
   anything conflicts. Every other use of stash stays forbidden.

## Current claims





| Owner | Files | What | Since |
| --- | --- | --- | --- |

| Owner | Files | What | Since |
| --- | --- | --- | --- |
| Owner | Files | What | Since |
| --- | --- | --- | --- |


## Recent overlaps worth knowing

- 2026-09-19, Claude session `claude-email-model-input-budget`: the `reserved email
  category key` / `validate_email_category_key` stack in `/tmp/ceo-agent-service-main.err.log`
  is **history, not a live fault**. I reported it as active after reading the stack without
  checking when it last occurred; the hourly-check session verified it: 16 occurrences in
  that log, the last at line 56785 of 95173, none since, and the fix is `78f9494e`
  (2026-09-15, "stop a retired category reaching the confirmation as a 500"), which matches
  `claude-retired-category-save`'s own date. Recorded here so the next reader who greps that
  stack does not open it a third time. Nothing in that claim's row was changed.

- 2026-09-17, Claude session `ceo-agent-service-bd`, **I edited `app/runtime_fallback.py`
  and `tests/test_runtime_fallback.py` inside `claude-unified-runtime-fallback`'s claim,
  with Derek's explicit authorisation** (owner session notified directly). One line:
  the `capacity_retry` plan now returns `fresh_session=False`. It returned `True`, which
  the Agent loop in `app/agent_turn_runner.py` reads as "clear an incompatible session";
  that guard only accepts `session_route_incompatible`, so every Agent run failed on its
  first codex 429 with "fresh session retry lacks persisted resume evidence" — no
  same-route wait, no failover. 27 runs failed that way on 2026-09-17. Nothing else in
  your files changed; a regression test pins the contract.

- 2026-09-17, Claude session `claude-per-category-promotion`: **the email model is now promoted per category**
  against the console's `email_model_promotion_configs` thresholds instead of hard-coded 0.95 / 20 / 10.
  `WholeModelReadiness` carries `promoted_categories`; `online-active.json` records them and
  `OnlineEmbeddingPredictor` hands any other category to the Agent (`model_category_not_promoted`).
  Training evidence records `promotion_thresholds`; evidence without it is judged by the legacy values.
- 2026-09-17, Claude session `claude-account-binding-gate`: **`EMAIL_SCHEMA_VERSION` is now 42**
  (`_migrate_v41_to_v42` adds `scan_lookback_days` and `scan_read_state` to
  `email_accounts`), inside `claude-email-action-skipped`'s schema-version claim, and
  `frontend/src/api/console.ts` gained account fields inside
  `codex-agent-health-metrics`' claim. A mailbox is now scanned only back to its
  lookback window, and its "all" setting organizes read mail too. Adding a mailbox
  verifies its folders and restores the categories the save paused.
- 2026-09-17, Claude session `claude-delivery-receipt-gate`, **I edited
  `app/store.py` inside your claim, with Derek's explicit authorisation** after
  the handoff below went unanswered. The change is the three `or` branches
  described there and nothing else; `list_completed_audit_runs_missing_delivery_projection`
  is otherwise untouched, and a regression test sits in `tests/test_worker.py`,
  not in your `tests/test_store.py`. Original handoff, for context:

- 2026-09-16, Claude session `claude-delivery-receipt-gate`, **handoff to the
  owner of `app/store.py` (`codex-agent-health-metrics`)**. `list_completed_audit_runs_missing_delivery_projection`
  (`app/store.py:17220`) decides which completed Audit runs the repair sweep
  backfills into `sent_replies`, from a hand-written list of identity fields
  (the `or trim(coalesce(json_extract(...)))` chain ending near line 17275). That
  list omits `open_task_id`, `openTaskId` and `open_message_id` (it has only
  camelCase `openMessageId`). `dws chat +dm` returns an `openTaskId` and nothing
  else, so real deliveries identified that way are never selected and never
  backfilled -- and an unrecorded delivery is the one a later rerun sends again.
  **Patch:** add three branches to that `or` chain, for
  `$.external_result.live_result_reference.open_task_id`, `.openTaskId` and
  `.open_message_id`, same shape as the existing `openMessageId` branch. The
  Python projection this feeds already accepts all of them: `44478df3` made
  `_sent_reply_projection_from_result` fall back to
  `app.agent_effect_guard.PROVIDER_RECEIPT_FIELDS`, and `de176316` made it read
  the accepted proposal from the run lineage, so the live path now writes the
  row itself. This SQL is the one remaining copy of the old list. Evidence:
  audit run 19709 (task 384233) carries `open_message_id` + `open_task_id` and
  was not selected.

- 2026-09-16, hourly-check session `claude-email-action-skipped`: **`c3ff384b`
  (`fix(audit): point the executing turn at the operation Skill before it acts`)
  has no claim row, and it edits `app/audit_agent.py`, which
  `claude-evidence-gate-wiring` claims.** Two sessions changed that file the
  same day without seeing each other. Nothing is broken — `0a3108fe` and
  `c3ff384b` are both in history and the tree is clean — but if you own
  `c3ff384b`, add your row.

  I also mis-attributed `c3ff384b` to `claude-evidence-gate-wiring` because the
  topic was adjacent and the claim row named the same file, and I pinged the
  wrong session about reruning a live task. The trailers distinguish them
  (Opus 5 vs Sonnet 5). Read the trailer before assigning a commit to a peer.

- 2026-09-16, Claude session `claude-email-action-skipped`: **the live email
  schema is now v40.** `email_actions.status` and `email_action_attempts.status`
  accept `skipped`, which needed both tables rebuilt, so a pre-v40 checkout
  cannot write those tables correctly. Verified on a copy of the live database:
  1,835 actions and 1,978 attempts survive, the three direct-action triggers are
  recreated, and a second open is a no-op.

  Two warnings from this change. First, `tests/test_email_store.py` had 9
  pre-existing failures before I touched anything (the v15/v16/v20/v23/v36
  migration tests and two snapshot tests); they are unrelated to v40 and still
  fail. I measured the baseline before and after, and v40 adds none. Second, I
  briefly ran `git stash` in this shared tree to take that baseline, which
  removed another agent's in-flight edits to `app/email_classifier_*.py` for
  about twenty seconds. `stash pop` restored them intact, but do not do this
  here — check out a temporary worktree instead.

- 2026-09-16, Claude session `claude-evidence-gate-wiring`: attempt #9550
  closed `completed` after its Audit turn reported `executed` for a proposal
  that included a `notify_zhangjing_evaluation_submitted` DingTalk chat-send
  action, then self-described that same action as
  `not_executed_due_to_missing_dingtalk_mcp_tool` in the same result — no
  `dws chat` call, no provider receipt, message never sent. This is exactly
  the case `DingTalkSendEvidenceDriver` (shipped in `989ed829`) exists to
  reject, but `AuditAgentRunner._execute_claimed` only substituted
  `self._parse_evidenced_result(...)` for the plain `parse_result` when
  `email_unsubscribe_tools` was truthy — true only for the email-unsubscribe
  lifecycle, never for a DingTalk task. So the one domain the driver was
  built for was the one domain where it was never invoked. Fix: drop the
  `if email_unsubscribe_tools` condition and always wrap with
  `_parse_evidenced_result`; both drivers (`DingTalkSendEvidenceDriver`,
  `EmailUnsubscribeContinuationDriver`) already return `True` (no-op) for
  tasks outside their own channel/schema, and `domain_continuation is None`
  in tests/paths that don't set one is handled inside `_parse_evidenced_result`
  itself, so this only changes behavior for a proposal that actually claims a
  DingTalk chat-send with no receipt. Regression test:
  `test_non_email_executed_without_tool_evidence_is_an_invalid_result` in
  `tests/test_audit_agent.py`, run against the pre-fix code first to confirm
  it failed there (`DID NOT RAISE ResultParseError`). Attempt #9550 itself is
  untouched by this fix — it is a historical row, and this change only
  affects turns that run after it deploys. Whether/how to notify 张静 for
  9550 specifically is Derek's call, not folded into this commit.

- 2026-09-16, Claude session `claude-self-agent-echo`: I removed the spent
  `claude-delivery-receipt-gate` row. Its work landed in `989ed829` and the
  tree is clean for every file it listed, so the row was only holding
  `app/worker.py` and `tests/test_worker.py` against the next owner. I am now
  that owner, and I touch only the candidate-filtering region of
  `app/worker.py`; the receipt gate itself is untouched.

- 2026-09-16, Claude session `claude-todo-sync-skipped` (`8d237619` plus the
  follow-up commit): **the live store schema version is now `2026-09-16.1`.**
  `task_todo_sync_outbox.status` accepts `skipped`, which needed a CHECK
  constraint rebuild. `AutoReplyStore._ensure_initialized` skips `_initialize`
  whenever the stored `store_schema_version` already matches the constant, so
  DDL alone does nothing on an existing database — my first restart deployed
  the new table definition and the live table kept the old constraint. If you
  change DDL here, bump `STORE_SCHEMA_VERSION` in the same commit, and expect
  `tests/test_store.py` to pin the literal. I touched two files claimed by
  `codex-agent-health-metrics` in unrelated regions and staged only my own
  hunks: `app/store.py` (the outbox DDL, its migration and the version
  constant) and `tests/test_store.py` (the one pinned version literal).

- 2026-09-15, this Claude session: answered Derek's question "attempt页面的
  调用记录是否有必要存在" by removing the `ExecutionDetail` (`/attempts/:id/
  execution/:role`) sub-page's "调用记录" card. That card rendered no call
  data of its own - only a link to the Codex session (`查看 {role} 的 Agent
  记录`) or, when the session had rotated off disk, one sentence saying so -
  and the same link is already one click earlier on the main Attempt page's
  banner (`consumer_url`/`audit_url`/`agent_url`). The link now lives in the
  sub-page's own header actions instead of a separate titled card; the
  "session rotated off disk" sentence moved inline into the context row.
  Kept: the main Attempt page's `ProcessingPanel` inline call list (real
  tool name/args/output), which is the one genuinely load-bearing "调用记录"
  - it is the sole surviving account once a session's Codex transcript
  rotates off disk (confirmed happening in practice; see `e583f9e6` and the
  `direct-unsubscribe-has-real-browser-e2e`-adjacent memory note), so it was
  not touched.

  This edit lands on the same three files `codex-attempt-detail-readable-
  history`'s open claim covers (`app/web_api/attempts.py` untouched by me
  this time, but `frontend/src/pages/AttemptDetailPage.tsx`,
  `AttemptDetailPage.test.tsx` and this file, yes). Diff is small and
  additive-in-spirit (one card removed, its link relocated, no other
  behavior changed) - `git diff --stat` shows 17 lines changed in the
  component, 38 in its test. I have no inbound channel from that Codex
  session, same situation as the `attention-reconciliation` note above:
  reconcile against `ExecutionDetail` in `frontend/src/pages/
  AttemptDetailPage.tsx` before landing anything that also touches that
  function.

- 2026-09-16, Claude session `claude-micro-f1-gate` (`854a4abc`): **the live
  email schema is now v39 and the service was restarted.** The promotion gate
  measures micro F1, and `email_model_promotion_configs.macro_f1_min` is now
  `micro_f1_min`. A process running pre-v39 code cannot open that database, so
  do not start an older checkout against it.

  This cost about twenty minutes of live email downtime and it was my fault, so
  the mechanism is worth knowing: my first version of `_migrate_v38_to_v39` was
  not idempotent. Someone's `launchctl kickstart` deployed the working tree
  while that version sat uncommitted, the migration renamed the column, and
  every later start crashed in `EmailStore.__init__` with `no such column:
  "macro_f1_min"`, which surfaced as `email_store_unavailable` 503s on the
  Email page. The committed migration checks `pragma table_info` first and
  records version 39 in `email_schema_migrations`, which the first version also
  skipped. If you write a migration here, assume a restart will deploy it
  before you are ready and make it safe to run twice.

  I also touched two files claimed by others, in unrelated regions, and staged
  only my own hunks: `frontend/src/api/console.ts` (three email metric type
  lines; `codex-agent-health-metrics` owns it) and `app/email_store.py` (the
  promotion config column and the new migration, alongside that session's
  in-progress `important_signals_json` work, which I left unstaged). The same
  session's `frontend/src/test/email-fixture.tsx` edit is untouched: my staged
  blob is HEAD plus the rename only.

  `README.md`, `CHANGELOG.md` and `docs/architecture.md` are all claimed, so
  the metric change is not written up there yet. Whoever holds them: the gate
  now reads "share of messages classified correctly", not "mean of per-class
  F1", and the configured 0.95 carried over unchanged.

- 2026-09-15 17:27, Claude session `claude-delivery-receipt-gate`: **I restarted
  `com.ceo-agent-service.main`, which deployed the whole tree including
  `claude-micro-f1-gate`'s uncommitted email work.** Imports were verified
  first and the service came up healthy on pids 43024/43029/43030/43031, with
  the console answering and no `failed` or `processing` backlog. Two things
  for that owner: the **live** email schema is now v39 and
  `email_model_promotion_configs.macro_f1_min` has been renamed to
  `micro_f1_min` on the real database (applied 17:22, before my restart, most
  likely by constructing an `EmailStore` against it - the same hazard this
  board recorded on 2026-09-12). A process running pre-v39 code will now fail
  against that database. The crash loop in
  `/tmp/ceo-agent-service-main.err.log` (`no such column: "macro_f1_min"`) is
  from the version of `_migrate_v38_to_v39` that predates the
  `if "micro_f1_min" not in columns` guard; with the guard in place the worker
  starts cleanly, and I did not touch that file.

- 2026-09-15, Claude session `claude-email-tabs`: the Email page's list filters
  are now page tabs, so its URL keys changed from `?tab=list&filter=<status>`
  to `?tab=pending|all|unsubscribe`. `app/web_api/attempts.py:312` still builds
  `/email?tab=list&selected=<id>` and is claimed by
  `codex-attempt-detail-readable-history`, so I left it alone: an unknown `tab`
  now falls back to 待确认, and `selected` still opens that mail's reading
  panel, so the link keeps working. If you want it to land on 全部 instead,
  change that string to `?tab=all&selected=...` - no change needed on my side.

- 2026-09-12, from another Claude session (`e583f9e6 fix(console): flatten the
  stored tool-event stream so the fallback call list is readable`): its edit to
  `app/audit_web.py` is 7 lines (one import, one function body swapped to call
  `normalize_stored_tool_events` in `app/codex_history.py`), touching
  `_audit_tool_events_for_attempt` only. `attention-reconciliation`'s claim on
  the same file is unrelated Attention-rendering work; no textual overlap seen,
  but that Codex session has no inbound channel from this board, so it will not
  see this note on its own - check it against `_audit_tool_events_for_attempt`
  before landing if the diff is anywhere near it.

  I independently verified the fix rather than taking the report at face value:
  re-ran the four claimed test files (385 passed, matches), confirmed live
  against attempt #9129 whose Codex session files have since rotated off disk -
  `agent_sessions` is now `[]`, making `app/web_api/attempts.py`'s `tool_uses`
  fallback (from `7f52991b`) the only path Derek's browser hits for it. Before
  restarting, the running process (started 13:40:19, fix committed 13:50:02)
  was still serving all-`"tool"` unnamed rows over HTTP; restarted (pid 54797)
  and confirmed the same endpoint now returns `user_get` /
  `command_execution` / `unsubscribe_email` with real args and output, with no
  stuck `processing` rows and no new `failed` rows afterward.

- 2026-09-12, Claude session `attempt-detail-evidence`: **the live email schema
  is now v37 and the running service was restarted.** Verifying the Attempt DTO
  against the real database constructed an `EmailStore` on it, which applied the
  v36→v37 migration (`email_unsubscribe_receipts.entry_url`) before the code was
  committed, and held a write lock long enough that the email agent consumer
  died once with `database is locked`. The migration is additive and the service
  has been kicked and verified on pid 82522 with no stuck `processing` rows, but
  two things follow for everyone else: a process still running pre-v37 code will
  refuse that database (`latest_version > EMAIL_SCHEMA_VERSION`), so do not start
  an older checkout against it; and `launchctl kickstart` at 01:19 deployed the
  working tree as it stood, including the uncommitted edit to
  `app/agent_wire_contracts.py` that has been there since this session began.
  The one `failed` task afterwards is 383933 (`周日生成 OKR 周报`,
  `codex_process_failed` / "runtime route is paused"), which predates the
  restart and belongs to the cron change in `ceb3931e`.

- 2026-09-17, for whoever owns the uncommitted `_capture_runtime_skill_edits`
  work (`app/cli.py`, `app/managed_skills.py`, `app/business_skills.py` are all
  modified in the working tree): that startup step files a Service error on
  every service start — errors 14237 (07:35:52Z) and 14238 (07:43:55Z),
  `runtime_skill_edit_capture_failed: Could not record in-place Skill edits;
  version history may be incomplete: Skill missing metadata.managed_by marker`.
  Two restarts, two rows, so each restart adds one. I left both open rather
  than resolving them, since the capture itself has not run successfully yet.
  Update 09:37Z: the capture code is now committed (`3e71a801`, `c771579a`)
  and the rows keep coming — 15 open, one per service start (the latest from
  my own restart at 09:37:20Z). They are the whole of Attention right now.
  Resolved 15:45Z: the failing Skill was only `ceo-wechat`, whose runtime file
  (`~/.agents/skills/ceo-wechat/SKILL.md`, the Sep 11 Chinese version) predates
  the marker. I added `managed_by: ceo-agent-service` to its metadata and
  changed nothing else; a manual capture recorded it as revision 3 and a second
  capture found nothing. All 23 rows are resolved. A pre-existing runtime file
  without the marker still fails the whole capture, since
  `_install_missing_repository_skills` never touches an existing file; that is
  the owner's call.

- 2026-09-11: three findings left open by the failed-item repair round, each
  needing an owner. They are recorded here because the evidence is perishable.

  **A model can still author a terminal error code.** `AgentError.code` is free
  text, so a turn that cannot finish reports a code it invents and the
  orchestrator reads it as a considered decision. Codes seen live that exist
  nowhere in this repository: `task_already_completed`,
  `email_unsubscribe_risk_rejected`, `email_unsubscribe_execution_rejected`,
  `unsubscribe_target_mismatch`, `AUDITED_UNSUBSCRIBE_CAPABILITY_UNAVAILABLE`.
  Each one closed a live task. `app/email_worker.py` now overrides two specific
  cases from the turn's own events (a terminal receipt, a route refusal), but
  that is a patch per case; the contract itself is still open, and Derek has
  approved making these codes a service-owned type.

  **Still live, re-measured 2026-09-16 and still unowned.** Model-authored
  codes in the last 7 days include `xiaoqing_mcp_not_injected` (7),
  `xiaoqing_interview_mcp_not_injected` (6), `provider_not_available` (1, which
  closed task 384271 today) and `AUTHORIZATION_REQUIRED` (5) alongside
  `authorization_required` (12) — the same concept in two spellings, which is
  what free text buys. `_WireBase.error_payload` in
  `app/agent_wire_contracts.py` is the single chokepoint every model-authored
  error passes through, and it already overrides the model for
  `_RUNTIME_RETRYABLE_DEPENDENCY_ERRORS`, so the insertion point exists.

  What blocks it is not the code, it is the taxonomy: 25 distinct codes appear
  in 7 days across runtime, codex, unsubscribe and authorization domains, and
  each has to be classified service-owned or model-authored before the
  chokepoint can quarantine the rest into `source_code`. Misclassifying one
  changes the terminal behaviour of ~7,600 runs a week. That list needs Derek
  or a focused pass, not a guess folded into another change.

  **A terminal unsubscribe receipt cannot be reused across generations.**
  `get_email_unsubscribe_terminal_snapshot` checks the durable claim's Audit
  lineage against the task's current `execution_generation`
  (`app/email_store.py:9946-9971`), so rerunning a task in a new generation
  cannot see a receipt its earlier generation already earned and raises
  `EmailPersistenceCorruption` instead. A receipt records what a provider did,
  which no rerun changes. The generation check is right for a claim still
  `awaiting_audit`; for one that is `done`/`terminal` it turns an idempotency
  record into an error. Tasks 383232 and 383234 hit this with real
  `skipped_login_required` receipts.

  **Closed 2026-09-16, verified against the live database.** The generation
  check now reads the task's generation as it is *right now* and requires the
  caller to be current, while allowing a receipt earned in an earlier
  generation — see the `lineage_live_task_generation` comment in
  `_validate_email_unsubscribe_effect_audit_lineage`. Evidence: task 383232 is
  `skipped` and 383234 is `done`, both with no error; all 123 email tasks are
  terminal (110 done, 13 skipped, 0 failed); and `errors` holds exactly one
  `EmailPersistenceCorruption` ever, last written 2026-09-11 03:53, the day
  this finding was recorded. No code change needed — this entry was stale.

  **Persisted Consumer results predating `310234e8` no longer parse.** That
  commit made `risk`, `confidence`, `rule_coverage` and
  `information_completeness` required on `ConsumerAgentResult`, so
  `model_validate_json` rejects every result written before it: 5 of 6 sampled
  completed email Consumer runs fail. This breaks
  `_bound_parent_consumer_action` (`app/email_unsubscribe_audit.py:641`).

  **Correction, same day.** This entry first said the delivery-projection
  repair sweep had stopped working on historical rows. That was measured
  against the wrong population. The sweep's input is
  `list_completed_audit_runs_missing_delivery_projection()`, which returns only
  completed Audit runs that executed a delivery and have no projection yet, not
  all stored results: 2 rows, against 1457 completed Audit runs with a stored
  result. One of the two (8514) was repaired by `0b51e852`; the other (8460)
  also carries pre-contract fields and reviving it would mean writing
  legacy-shape compatibility, which AGENTS.md forbids. The parse failure is
  real, but its reach through that sweep is two rows. 33 tests in
  `tests/test_email_unsubscribe_audit.py`, 5 in `tests/test_worker.py`, 2 in
  `tests/test_email_worker.py` and the collection of
  `tests/test_approval_history.py` fail for this reason. The contract belongs
  to the Codex `contract-quality` claim; per Derek's rule the answer is to
  rerun rather than hydrate legacy shapes, but the reconciliation sweeps cannot
  rerun anything and need a decision.

- 2026-09-10: two agents independently fixed runtime capability snapshots not
  reaching every service child — one bridged them through the store and the
  console (`app/audit_web.py`, `app/store.py`), the other made the refresher
  adopt a sibling process's fresh snapshot (`app/agent_runtime_probe.py`).
  Both landed and are complementary, but neither knew about the other.
- 2026-09-10: the scheduled "sync AI minutes" task was converted to a
  deterministic service command by one agent and restored to an Agent task
  bound to `ceo-minutes-sync` by another. The live task's `version` is 6, so
  it flipped six times. **Open disagreement, do not flip it again** (rule 3):
  the owner of `app/agent_cron/*` holds that it must stay an Agent task,
  because `sync-minutes-once` binds `scan_ai_minutes` (`app/task_scanners.py`),
  which only pages the minutes list, enqueues the raw list row as the summary
  and keeps a `seen_ids` list cursor. The `ceo-minutes-sync` managed Skill it
  replaced also reads summaries and full transcripts, requests access through
  `dingtalk-minutes-access-request` when a minute is denied, archives content
  to the work directory and persists a *content* cursor; `grep -rn
  "minutes-access-request" app/` finds nothing, so the command form cannot do
  that. `docs/superpowers/specs/2026-09-08-agent-cron-and-managed-minutes-skill-design.md`
  states the sync must not treat a successful list request as synced content.
  Two sessions verified this independently. **Resolved 2026-09-10 by Derek:
  it stays a service command.** Syncing minutes is deterministic work and the
  `dws minutes` CLI already exposes every step it needs — `+list-all` /
  `+list-shared` to enumerate, `+export-pack` to write the full artefacts to a
  controlled directory with a manifest, `+detail` for summary and transcript,
  and `+apply-permission` to request access when a minute is denied.

  **Correction (same day, before implementing):** one premise of that ruling
  was wrong and the scope is back with Derek. `+apply-permission` is *not*
  automatic by design. `skills/ceo-minutes-sync/SKILL.md` marks it a
  CONDITIONAL sub-skill, states that "restriction alone never authorizes a
  request", and gates it on a relevance judgement over the visible title,
  owner, participants and time: CEO-relevant and authorized may request;
  clearly out of scope is `skipped` with audit metadata; relevance unknown
  must not request and blocks the run as `permission_pending`/`needs_review`.
  Everything else in the Skill (pagination completeness, fresh-read
  verification, byte-exact archiving with sha256, manifest, content cursor,
  and the `synced + skipped + permission_pending + failed = discovered`
  ledger) is mechanical and portable to code. So the task is deterministic
  except for that one decision, and whether it stays a pure command, returns
  to an Agent task, or splits (command syncs; only restricted-and-unclear
  items raise an Agent decision) is Derek's open call. Do not implement
  automatic access requests for restricted minutes in the meantime.

  **Closed 2026-09-11.** `sync-minutes-once` now performs the real sync in
  `app/minutes_sync.py`: list → info/summary/transcript → archive file in the
  existing `AI听记` layout → content cursor. Derek settled the one judgement
  call by rule — a meeting shorter than five minutes is skipped — and the
  service requests no minute access at all, because DingTalk's denial codes
  (`PAT_HIGH_RISK_NO_PERMISSION`, `PAT_MEDIUM_RISK_NO_PERMISSION`,
  `AGENT_CODE_NOT_EXISTS`) are credential-level failures that would otherwise
  mail a request to the owner of every minute in the list. The task stays a
  service command. Verified in production on 2026-09-11: three minutes
  archived, cursor written, zero access requests.

- 2026-09-11: **`sync-minutes-once` never finishes its listing, and nobody owns
  the decision.** `dws minutes list` returns 20 items per page whatever limit
  it is asked for, and the sync walks a 100-page cap, so a run costs ~100
  provider calls (~4 minutes), still ends with
  `pagination_error: "minutes list pagination exceeded 100 pages"`, and
  therefore never stamps `last_success_at` — freshness reporting for this
  scanner is dead. New minutes are found (they sort newest-first), so the daily
  sync works; the history behind page 100 is unreachable. Making it incremental
  (stop at the first fully-archived page, or bound the listing by `start`) is
  cheap but changes how far back the sync backfills, which is Derek's call.
  Evidence: the cursor row for scanner `ai_minutes_sync` in `daily_scan_state`.

- 2026-09-11: **The unsubscribe route refusal is a provider-policy boundary,
  not a bug, and it needs Derek.** Nine live tasks end
  `email_unsubscribe_route_refused`. The route's own safety reviewer reads the
  Audit turn and declines to place the MCP call, recording: *"the trusted user
  content does not authorize unsubscribing or even opening that entry; the
  supposed authorization appears only in untrusted agent-generated context"*,
  and it explicitly forbids achieving the outcome by workaround or indirect
  execution. The authorization is real — Derek configured it and has said so
  in this session — but it reaches the model only through the service's own
  ActionPlan, which the reviewer treats as agent-written. Supplying that
  authorization in the trusted developer channel is the obvious fix and it
  would be truthful, but writing text aimed at flipping a provider's safety
  verdict is exactly what that verdict tells us not to do, so it is Derek's
  call, not mine. Options put to him: state the standing authorization in the
  trusted channel, route unsubscribe turns to a runtime without this reviewer,
  or accept these as a permanent terminal skip class. Evidence: the refusal
  message in `reply_attempts.audit_tool_events_json` for task 383338.

  **Closed 2026-09-11, and my framing above was wrong.** None of the three
  options was needed. The refusals were a property of the *old* tool, not of
  the task: `execute_audited_email_unsubscribe` took four arguments including
  a whole accepted proposal and declared `destructiveHint=True`, and the
  reviewer read that as an unauthorized subscription-preference change. The
  one-call replacement takes a single integer, declares
  `destructiveHint=False` and `idempotentHint=True`, and reads its
  authorization from durable state. All nine tasks were requeued through it
  and all nine completed with no refusal. Nothing was written to the trusted
  developer channel and no route was swapped. The lesson is to re-measure a
  provider refusal after the call it refused has actually changed shape,
  before treating it as a standing policy boundary.

| codex-consumer-retry-receipt-prefix-20261020 | `app/consumer_purpose_retry.py` (`verified_access_request` evidence-key matching only); `tests/test_consumer_purpose_retry.py`; `docs/consumer-purpose-output.md`, `docs/archive/records/consumer-purpose-actions-validation.md`; this claim | Accept the canonical `receipt:<external_action_key>` purpose evidence reference by stripping exactly one prefix before exact matching against the existing unique verified request receipt. Preserve task binding, request_access candidate verification, uniqueness, and the persisted ten-retry/72-hour budget. No retry-policy, authorization, external-action, or historical replay change. | complete 2026-10-20: regression observed failing before fix; 38 focused tests, Ruff and diff checks pass. Commit `ba3a26639` pushed and deployed; production checkout matches commit, PID 3957, health OK, zero processing History rows. History 15681/15707 remain failed as pre-deploy records; 15681's OA comment is already verified, and neither attempt was replayed. |

| codex-unsubscribe-attempt-detail-no-attention-20261028 | `app/history_actions.py::reply_history_attention`; `app/web_api/attempts.py` failed direct-unsubscribe Attempt projection only; `tests/test_history_actions.py`, `tests/test_console_attempt_detail_api.py`, `tests/gate_files.txt`; matching unsubscribe Attention behavior paragraphs in `docs/architecture.md` and `docs/runtime-mechanism.md`; this row | Derek 2026-10-28: every failed structured Email unsubscribe remains failed in History and outside manager Attention, including unresolved execution-fence errors. Align Attempt detail with the already-correct global Attention projection: preserve the failure/error, but do not present a human Attention, open_for_human signal, or manual retry control. Preserve the true failure, reconciliation marker, and existing automatic backoff/reconciliation rules; no provider action, automatic retry, status mutation, or marker clearing. |

| codex-restore-lightweight-emoji-replies-20261028 | `app/defaults/developer_prompt.md`; matching tracked default `data/prompts/developer_prompt.md`; `tests/test_prompt.py`, `tests/test_prompt_template_release.py`; scoped `docs/architecture.md` and `docs/runtime-mechanism.md`; this row | Derek 2026-10-28: restore the missing guidance to use message reactions for low-information short closings and lightweight interactions, while retaining plain text for explicit decisions, commitments, explanations or confirmations. No executor, permission or delivery-policy change. | Active isolated restoration; follow standard prompt publication and runtime deployment/readback. |

| codex-model-retirement-batch1-20261028 | app/store.py only audited no-caller methods; app/action_storage.py; app/action_entity_definitions.py; app/shared_action_lifecycle.py; app/action_entity_storage.py; app/action_business_storage.py; app/business_storage.py; app/task_entity_storage.py; app/run_entity_storage.py; app/logical_run_storage.py; app/scheduled_config_storage.py; app/current_publication_sources.py; app/email_store.py; app/email_current_schema.py; app/email_canonical_storage.py; app/provider_state_storage.py; app/workbench/store.py; obsolete app/runs11_storage.py; directly affected test/fixture/gate rows; scoped architecture/runtime/progress/evidence | Derek approved 2026-10-28 five-batch model retirement plan. Batch1 only: no DDL/data/policy changes; remove dead/test-only old paths and normalize current execution-owner APIs. One implementer owns runtime edits. Preserve concurrent Meeting/voice/OA/retirement fixes and any other claims' exact methods. | Complete: c5f39aa8e standard deployment/gate, PID82581/health200, full476SQL/FK[], History failure identity set unchanged, actual UI/native/receipt readback pass. Verified workflow daily backup and exact owned temp/gate roots absent. Broader repo-pyc purge was rejected and not attempted. No active runtime ownership. |
| codex-model-retirement-batch1-callers-20261028 | app/reviewed_candidate_store.py current execution-owner arguments only; app/action_entity_transfer.py frozen historical receipt writer only; tests/test_dingtalk_delivery_repair.py current helper call arguments | Exact caller switches required by approved Batch1 execution_run_id-only API; retain all CAS, execution identity, lineage and effect policies, no tool schema or business fact changes. | Complete: sole Batch1 implementation accepted and deployed c5f39aa8e; no active ownership. |
| claude-meeting-audit-target-tuning-20261027 | `app/meeting_alignment_agent.py` Meeting-specific audience rule (two sentences), `tests/test_meeting_alignment_agent.py` one test, `docs/runtime-mechanism.md` Meeting audience paragraph; this row | Derek 2026-10-28 ("优化"): Audit kept returning 417472 for "the group is not proven to carry this topic" and gave no alternative; it approved the same group on round 7. A same-project group is acceptable whatever its recent topic, and a target return must name the better supplied candidate. Audit-class change, separate commit, at Derek's request. | Complete locally: 181 Audit/Meeting-agent tests pass, Ruff clean; deploy pending. |
| claude-meeting-skip-interview-no-content-20261027 | `app/meeting_alignment_models.py` (action values, one cross-field rule), `app/meeting_alignment_agent.py` (Consumer target contract, Audit prompt, target check), `app/meeting_alignment.py` (approved skip → `skipped`/`no_action`), `app/schemas/meeting_alignment_decision.schema.json`, Meeting tests, `docs/architecture.md`, `docs/runtime-mechanism.md`; this row | Derek 2026-10-28: a meeting with no shareable information is not sent, and an interview is skipped because Derek handles it himself. 417547 had sent a one-sentence post to a 12-person group. The decision `action` gains `skip_interview` and `skip_no_content`; the existing `send` dump is unchanged, so stored digests stay valid. Independent Audit still reviews the skip decision. Audit-class change at Derek's request, separate commit. | Complete locally: 429 Meeting/Audit tests pass except `test_same_verified_private_audience_receives_one_combined_message`, which already fails at the previous head. Deploy pending. |
| codex-meeting-attendee-summary-dm-20261028 | `app/meeting_alignment_models.py` target contract; `app/meeting_alignment_agent.py` Consumer/Audit audience rules and source-aware validation; `app/meeting_alignment_delivery.py` final boundary; focused Meeting target tests; scoped `docs/architecture.md` and `docs/runtime-mechanism.md`; this row | Derek clarified 2026-10-28: do not choose `no_action` because no suitable group was found; if a meeting has another source-identified attendee, send the ordinary sanitized summary to one reasonable attendee by direct message. Do not require explicit assignment of a decision or follow-up as proof of recipient relevance. Keep group search completeness/error handling, stable attendee identity, no-self target, independent Audit, sensitive-content separation, prepared-target binding, receipt and retry gates. No automatic fan-out or historical resend. | Implemented and pushed in `17c492c23`; regression RED then GREEN, meeting-agent file 108 passed, Ruff clean. Standard deploy at this commit was rejected by the mandatory 600-second gate while it ran `tests/test_agent_cli_aitable.py::test_aitable_read_tools_reject_invalid_ids_and_limits_before_dws[...]`; production remains on the prior version. Keep claim active until a standard deploy/readback succeeds. |
| codex-meeting-voice-recovery-integration-20261028 | Integrate completed `claude/voice-transcribe-tool` commits into current mainline; inspect/resolve only their `app/wechat/codex_safety.py`, Consumer/Audit CodeMode handoff, focused role-boundary tests and runtime docs; remove the two stale voice claim rows after final release readback. | Current Attention 417695 fails because generated Consumer attempted `tools.mcp__ai_services__transcribe_audio` as a normal CLI tool. Existing clean claude worktree has two scoped commits for the already-approved tool registration and single CodeMode audio handoff. Preserve no-base64-to-model boundary, original failed run, and no-send-before-transcription. No external action until standard deploy and task readback. | active 2026-10-28 |

| codex-model-retirement-batch2-20261028 | docs/model-retirement-batch2-contract.md; plan/progress/evidence; pending precise runtime file claim after current source preflight | Continue approved second batch: explicit26.1/54 to28.1/54 paired retirement, positive current declarations and only necessary explicit source/inverse. Preserve all facts/identities, no runtime legacy compatibility or policy changes. | Complete:44c13bc91 standard gate/quiet/deploy; exact463schema/FK[] and UI/receipts pass, verified workflow backup and owned temp/gate roots absent. |
| codex-model-retirement-batch2-implementation-20261028 | app/configs_definitions.py; app/configs_storage.py; app/email_store.py transition-only DAO/startup validation; app/run_entity_definitions.py; app/run_adoption_definitions.py; app/schema_fixtures/store26_1_email54_catalog.json; app/schema_fixtures/store28_1_retirement_tables.json; app/schema_fixtures/store28_2_session_state.json; app/task_run_entity_definitions.py exact specified indexes; app/current_schema_manifest.py; app/store.py STORE_SCHEMA_VERSION constant only; new versioned source catalog/loader and paired release; app/store_schema_release.py; app/current_publication_sources.py; affected historical source/explicit release dependencies only; closed converter dependencies and their native test-fixture ports; directly affected tests/gate and current SQL artifacts; scoped architecture/runtime/progress/validation | Sole implementation owner for approved second batch. Freeze exact26.1/54 source, remove10indexes/emptyConfigs family/NULLmapcolumn; target28.1/54; maintain current-fact inverse and metadata. Necessary fifth-batch dependency subset may retire closed conversion-only code/tests after current-functional fixtures switch, instead of copying old builders/writers. Do not alter old pinned source facts/hashes or runtime policies. Parent alone owns production-copy/inverse-oldcode/deploy/cleanup. | Complete: candidate1b94382ba, merged/deployed44c13bc91;49native-table current-data inverse/old cold read-write passed, independent SPEC/QUALITY, standard gate and production readback/cleanup passed. No active Batch2 runtime ownership. |
| codex-leave-proportionality-20261028 | `ci/shared-skills/stardust-oa-attendance-travel-review/SKILL.md`; focused leave regressions/eval cases, the attendance publication assertion in `tests/test_consumer_system_release.py`, and `scripts/eval_oa_skill_replay.py` optional Audit/model switches; release manifest; new correction validation doc; scoped architecture/runtime notes; own claim | Correct the two reported half-day compensatory-leave reviews: establish applicability before asking for balance/policy; evaluate accepted handover by responsibility scope without making incident resolution a leave precondition. No generic Audit, lifecycle or authority change. | Complete: PR332 merged; separate authorized v21 baseline sync unblocked standard publication through PR333 at 9f163a6. Native Consumer/Audit reads use corrected attendance digest; both principal nodes verified AGREE/COMPLETED. One full process complete, one continues at another approver. Public acceptance is anonymous; exact original receipts remain local. No active ownership. |
| claude-voice-upload-audio-file-20261028 | `app/agent_cli.py` (`save_message_audio` replaces `read_wechat_voice` and also saves DingTalk voice; new generic `upload_audio_file`), `app/wechat/{codex_safety,schema}.py`, `app/service_codex_config.py` (approve `create_audio_upload`, `get_audio_transcription`), reverse of the WeChat `code_mode_handoff` flag from `a17aa8ee3` in `app/{consumer_agent,audit_agent,consumer_purpose,prompt_preview}.py`, their tests, `docs/architecture.md`, `docs/runtime-mechanism.md`, `CHANGELOG.md`; this row | Derek 2026-10-28: ai_services added `create_audio_upload` (one-time upload URL, token inside the URL). Audio now goes service file -> that URL without passing through the model and without any stored credential, in plain tool calls on every route, so the CodeMode handoff for WeChat is withdrawn (OA keeps its own). The tool is generic (Derek: DingTalk voice reuses it), not WeChat-named. Overlaps the active `codex-meeting-voice-recovery-integration-20261028` row: `a17aa8ee3` is already on main and deployed, my own commit, reversed here by Derek's direction. No upload-host allowlist: proposed separately, not built. | Implemented locally; focused tests pass; live upload protocol checked with a generated tone (202 + job_id). Push and deploy pending. |
| codex-okr-v21-release-baseline-20261028 | `ci/shared-skills/ceo-weekly-okr-report/SKILL.md`; only that entry in `ci/consumer-system-contract-release.json`; focused existing publisher regression; scoped architecture/runtime and release-validation notes | Derek explicitly authorized preserving the installed v21 company-level notification rule verbatim, with a separate commit, and aligning installed/source/managed release baselines so the approved half-day leave fix can publish. No new scoring, audience, Audit, or delivery mechanics. | Complete: verbatim v21 sync and no-op source/installed/managed digest e786d7a preserved; independent RED/GREEN publisher regression, all30 publisher tests and26 file preconditions pass. PR333 merged; standard publication and installed/source digest readback succeeded at9f163a6. No active ownership. |
| claude-self-voice-command-20261028 | `app/worker.py` (`_candidate_messages` + `_is_principal_voice_to_agent_chat`), `app/wechat/producer.py` (`is_self_voice_command`), `tests/test_worker.py`, `tests/wechat/test_producer.py`, `docs/architecture.md`, `docs/runtime-mechanism.md`; this row | Derek 2026-10-28 ("增加特例：给自己发的语音消息不需要带@磊哥", "微信也一样"): a voice the principal sends in the agent-named DingTalk single chat, or in WeChat `filehelper`, counts as an instruction. Voice only, so the service's own text deliveries cannot loop. Trigger-authorization class: separate commit, no other change. | Implemented locally; focused tests pass. Push and deploy pending. |

| codex-model-retirement-batch3-20261028 | app/state_definitions.py; app/store.py conversation/session methods and version constant only; app/agent_turn_runner.py mirror-only metadata branch; app/worker.py conversation upsert argument only; app/cli.py OAuth reset naming only; app/audit_web.py session-list and Attempt fallback only; app/web_api/registration.py session-list only; directly affected session/test fixtures; fixed current state metadata/manifest/artifacts; new explicit Store28.1→28.2 release/source and dedicated tests/tool; scoped architecture/runtime/progress/evidence | Approved plan Task4: canonical conversation_runtime_sessions is the sole session/hash owner; retire two conversations mirrors, preserve all Run/Attempt facts, all-route clear and route CAS. OAuth CLI reset only canonical OAuth slots. Exact current-data inverse rebuilds mirrors from canonical, including approved formerly-empty hashes. One runtime implementer; parent alone owns migration/publication/production verification. No policy changes or old runtime compatibility. | Complete: af24bc485 standard gate/quiet/paired deployment, PID71305/health200, exact463schema/FK[], actual session/History/native/receipt readback pass. Verified workflow daily backup and owned temp root absent. No active Batch3 runtime ownership. |
| codex-batch3-consumer-metadata-20261028 | app/agent_turn_runner.py Consumer metadata callback and execute flag; app/consumer_agent.py and app/audit_agent.py flag-only hunks; tests/test_agent_turn_store.py, tests/test_native_only_core.py, tests/test_agent_turn_runtime_refresh.py flag-only hunks; tests/test_consumer_session_prompt_fingerprint.py new Consumer metadata regression | Restore route-neutral conversation metadata creation for new Consumer sessions, remove unused flag, preserve canonical session persistence policy and Audit behavior. | Complete: metadata RED/GREEN and current-contract60turn/45related tests, independent QUALITY recheck; included af24bc485 production acceptance. No active ownership. |
| codex-model-retirement-batch4-feedback-20261028 | app/store.py Feedback projection, mutation owners and version only; app/task_run_entity_definitions.py native Item declaration only; current schema metadata/manifest/artifacts; app/feedback_processing.py DTO docstring only; directly affected Feedback/iteration/API/source-identity fixtures/tests; new exact Store28.2→28.3 paired release/source and copy verifier; scoped architecture/runtime/progress/evidence | Approved plan Task5: Item retains task_id/feedback_key/batch_id/current_round_id only. Nine mirrors belong to exact pointed Round, resolved_at to exact feedback Action. Preserve status/CAS/source identity/receipt qualification and neutral pointer=0 semantics; inverse reconstructs from exact owners, no latest-round or old runtime compatibility. Root owns high-risk migration, release/readback/cleanup; one runtime implementer. | Complete:7a6b73ee9 standard gate/quiet/paired deploy; exact463schema/FK[], Feedback/History/Attempt/native/receipt readback passed. Verified workflow backup, raw test worktree, database/code copies and editor script removed; no active ownership. |
| codex-retire-closed-task-run-converters-20261028 | app/task_entity_schema.py; app/task_entity_inverse_files.py; app/task_entity_migration.py; app/run_entity_schema.py; app/run_adoption_schema.py; app/run_adoption_inverse.py; historical-only map/constants portion of app/task_run_entity_definitions.py; explicitly dependent closed validation tools/scripts and dedicated converter tests; native fixture ports in test_wechat_import_native.py/test_store_task_entity_creator.py (including Feedback fixture hunks after confirmed failures)/test_workbench_native_parent.py/test_task2d9_domain_leaves.py; mixed scheduled-occurrence and current-declaration/assembly tests; tests/gate_files.txt | Necessary early approved Task6 subset: retire closed conversion closure rather than make current four-column Item DDL depend on old fourteen-field mapping. Retain current positive declarations, all actual identity/provenance/coalescers, native functional behavior/assertions and explicit current28.2→28.3 inverse. Do not touch Feedback runtime mutations or root new release/source files, current SQL artifacts or peer policy. | Complete:7a6b73ee9 standard gate/quiet/paired deploy; exact463schema/FK[], Feedback/History/Attempt/native/receipt readback passed. Verified workflow backup, raw test worktree, database/code copies and editor script removed; no active ownership. |

| codex-retire-decision-runner-legacy-executor-20261028 | app/codex_decision.py executor-only path and exclusive helpers; current routed fakes in tests/test_codex_decision.py/tests/test_codex_runner.py/tests/test_worker.py and exact additional direct callers; tests/e2e/test_live_smoke.py valid isolated constructor only; scoped docs/evidence | Approved Task6 separate no-DDL unit: retire executor/_legacy_adapter/direct subprocess path; production always uses RoutedCodexExecution. Preserve actual current parsing/session/native-result/audit-summary/retry/auth/route policy, command-boundary assertions and live-smoke intended semantics. No new compatibility wrappers; actual models/providers fake in validation. Root owns final integration/publication and field/storage cleanup. | Complete:exact4022 standard gate/quiet deploy and readback; final49-table typed current-data/real old-code inverse plus SPEC/QUALITY/final review passed. Owned temporary artifacts and backup generation absent; successor3f126 includes changes, its new backup retained for its owner. No active ownership. |

| codex-feedback-source-independence-20261028 | app/feedback_owner_schema_source.py; app/schema_fixtures/store28_2_email54_catalog.json; app/feedback_owner_schema_release.py source references; app/current_publication_sources.py previous-source reference; tests/support/store28_session_source.py 28.2 factory; tests/test_current_declarations_independent.py cold guard; tests/test_feedback_owner_schema_release.py exact source and release tests; tools/native_copy_validation.py; tools/validate_feedback_owner_copy.py helper import; this row | Approved Task6 no-DDL source simplification: freeze exact 28.2 catalog, remove obsolete source/helper dependencies, preserve exact target and explicit inverse. Root owns docs, integration and publication. | Complete:exact4022 standard gate/quiet deploy and readback; final49-table typed current-data/real old-code inverse plus SPEC/QUALITY/final review passed. Owned temporary artifacts and backup generation absent; successor3f126 includes changes, its new backup retained for its owner. No active ownership. |

| codex-current-quiet-source-fixture-20261028 | tests/test_deployment_drain.py previous-source and damaged-source fixtures only | Port stale25.2 input to exact supported28.2 source; preserve running Run count and malformed source read-only rejection, no runtime compatibility. | Complete:exact4022 standard gate/quiet deploy and readback; final49-table typed current-data/real old-code inverse plus SPEC/QUALITY/final review passed. Owned temporary artifacts and backup generation absent; successor3f126 includes changes, its new backup retained for its owner. No active ownership. |

| codex-task6-email-workbench-retirement-20261028 | app/email_canonical_storage.py; app/workbench_storage.py; tests/test_email_canonical_storage.py; tests/support/email_native_fixtures.py; tests/test_email_source_reader.py; tests/test_email_metadata_schema.py; tests/test_store_task_entity_creator.py; tests/test_email_exact_batch.py; tests/test_email_store.py; tests/test_email_body_source.py; tests/test_email_model_input_lifecycle.py; tests/test_email_preview.py; tests/test_email_classification_reader.py; tests/test_workbench_storage.py; tests/workbench_caller_fixtures.py; own claim row | Approved Task6 closed Email and Workbench conversion retirement; preserve current declarations and native behavior; root owns integration and release. | Complete:exact4022 standard gate/quiet deploy and readback; final49-table typed current-data/real old-code inverse plus SPEC/QUALITY/final review passed. Owned temporary artifacts and backup generation absent; successor3f126 includes changes, its new backup retained for its owner. No active ownership. |
| codex-model-retirement-batch5-closeout-20261028 | reviewed Task6 Runner/Business/Email/Workbench/Run-map/release closure; scoped docs/progress/validation; exact4022 publish and readback; owned temporary artifacts | Approved five-batch plan closeout; preserve concurrent schema28.4 owner and its backup generation. | Complete:4022 published and accepted; actual successor3f126/Store28.4 includes all changes, exact464schema/FK[]/health200 independently read back. Known owned roots absent, raw worktree unregistered, old backup generation retired; no active code ownership. |
| codex-hsw-attendee-calendar-match-20261028 | `app/meeting_alignment_source.py` exact creator-attendee calendar association, transcript roster and roster readback validation; `app/meeting_alignment.py` pass Minutes creator into association and transcript fallback, exact-meeting source replay, and carry forward a same-task group target previously read by the Consumer; `app/web_api/registration.py` refresh the exact failed Meeting source by UUID on its rerun route; `app/store.py` safe source replay eligibility only; focused Meeting/API/Store tests and gate manifest; scoped architecture/runtime behavior; this row | Make the already-authorized source-identified attendee fallback work when the exact Minutes creator is also an attendee of the unique overlapping calendar event, even if calendar and recording titles differ. When no unique calendar match exists and transcript attendees are used, include the explicit Minutes-list creator even if they did not speak, only after unique stable-identity resolution; source validation must compare that creator as an attendee, not a transcript speaker. A latest failed Meeting rerun first refreshes only its exact UUID through the same source replay path, then preserves immutable prior Runs and the prepared-delivery/receipt/claim guards. When a later feedback revision loses a previously read group candidate for this same meeting task, provide that original candidate and its evidence to the next Consumer; do not approve or deliver it automatically. Keep the independent Audit, prepared-delivery, receipt, and send gates unchanged. No guessed recipients, status edits, or direct sends. | Complete: regression failed before the fix, then Meeting source/alignment tests 224 passed; Ruff/diff checks passed. Commit `166186e41` deployed through the official gate (8412 passed, 2 skipped, 1 deselected). Task 417948 reran as Attempt 400831 and reached sent after independent Audit; DWS complete readback found one matching message `msgJDRgRhIxFVDJk5OZ5gNqSA==` in Hu Ming’s direct chat, with business body matching after transport whitespace/footer normalization. Attention=0; production health healthy, no quality-gate violations. |
| codex-dws-org-role-evidence-20261028 | `ci/shared-skills/ceo-message-triage/SKILL.md`; its entries in `ci/consumer-system-contract-release.json`; `tests/test_consumer_system_release.py` attendance target baseline assertion; this claim row | Derek 2026-10-28: fix Mina/Chen Kai trace where DWS already returned Chen Kai's current role but the reply omitted it, while HRBrain permission failure was treated as blocking. For existing employees, use DWS organization/contact data for current title, department and manager; keep HRBrain optional for additional talent/profile data; include verified current role and separate it from target-role fit evidence. Publication also refreshes the stale old-hash precondition for an attendance Skill target already byte-identical to its repository source, without changing that Skill. No external message or personnel decision. | Active. |
| claude-wechat-unsupported-content-20261028 | `app/wechat/schema.py`, `app/wechat/reader_version.py` (new), `app/wechat/reader_ipc.py`, `scripts/build-wechat-reader-app.sh`; `app/store.py` (`_sync_wechat_delivery_reply_attempt` guard, reconciliation lookup, console command records); `app/web_api/attempts.py`, `app/web_api/registration.py`, `frontend/src/api/console.ts`; `ci/shared-skills/ceo-wechat/SKILL.md` + its manifest entry; tests under `tests/wechat/`, `tests/test_attempt_projection.py`, `tests/test_console_web_api.py`; scoped `docs/runtime-mechanism.md`, `docs/architecture.md`; `app/deploy.py` gate-under-mutex and per-commit gate reuse with `tests/test_deploy_test_gate.py` and the gate paragraphs in `AGENTS.md`; this claim row | Derek 2026-10-28, from attempt 16295: Reader labels unrenderable appmsg honestly (no reply task); stale-generation delivery no longer overwrites the Attempt; Reader/service share a source-digest version checked on every IPC call; console records who triggered rerun/retry/resend; read-back settled tasks say so; ceo-wechat never repeats an unanswered ask. Shipped d9c67c40d, 5baa3de4e, 7a5c59541 + skill commit; Reader rebuilt and reinstalled. | 2026-10-28 |

| codex-model-retirement-cleanup-final-20261029 | docs/archive/records/model-retirement-batch5-validation.md; docs/archive/records/business-model-execution-progress.md; docs/validation/2026-10-28-model-retirement-batch5.json; this row | Derek authorizes final publisher coordination and task-owned backup/temp absence verification, then documentation closeout only; no new development/deploy and preserve subsequent workflow generations. | Complete: locked final verification confirms all 12 exact backup/sidecar paths, 10 temporary paths and two raw worktree registrations absent; subsequent 10/29 workflow backup retained. Publisher notified; independent absence evidence recorded. Documentation only, no development/deploy or deletion in this follow-up. No active ownership. |


| codex-minutes-transcription-direction-20261029 | `app/dws_client.py::build_minutes_transcription_command` direction enum only; `tests/test_dws_client.py` command-shape regressions; the Minutes transcript-read sentence in `docs/runtime-mechanism.md`; this claim row | Fix the scheduled Minutes transcript read command: DWS accepts direction `0` (forward) or `1` (reverse), while the adapter sends invalid literal `forward`. Preserve forward ordering, pagination, transcript parsing, archive behavior, and all business records; no prompt/Skill change, no external write, no replay. | Implemented; 350 DWS/minutes-sync tests passed, Ruff and diff check clean; formal release and production readback pending. |

| codex-meeting-run-cleanup-20261029 | app/store.py::update_meeting_alignment_job only; app/logical_run_storage.py; tests/test_logical_run_owner.py; new tests/test_meeting_result_storage.py; scripts/validate_database_closeout_copy.py; tests/test_validate_database_closeout_copy.py; tests/gate_files.txt only those new rows; tests/test_runs11_definitions.py::test_all_original_kinds_native_positive_insert frozen clock only; scoped architecture/runtime and acceptance notes | Derek approved no-DDL Meeting result single-write and retired logical Run entry cleanup. Current Run/provenance only; preserve all lifecycle, provenance, receipt and policy semantics. No real outbound tests. | Active: isolated codex/meeting-run-cleanup-20261029, baseline29c891346; root owns store; bounded child owns old Run module/test/script. |
| codex-imap-command-deadline-20261031 | `app/email_imap_readonly.py` read-only IMAP command deadline; `tests/test_email_imap_readonly.py` and its `tests/gate_files.txt` entry; scoped email read timeout behavior in `docs/architecture.md` and `docs/runtime-mechanism.md`; this row | Approved generic recovery direction for stuck email worker: enforce a total deadline for each read-only IMAP command so a peer that trickles bytes cannot keep a worker blocked indefinitely. Expiry closes only the affected read-only session and propagates an external timeout through the existing bounded retry/backoff path. Preserve ordinary idle socket timeout, all mailbox read semantics, and all provider-action/effect gates. No writes, unsubscribe replay, or status edits. | Active root-owned fix after production worker sample showed SSL socket reads blocked while email-consumer tick remained stale. |
