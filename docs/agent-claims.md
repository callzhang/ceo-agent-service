# Concurrent agent file claims

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
6. **Release the claim** by deleting your row when the work is committed.

## Current claims

| Owner | Files | What | Since |
| --- | --- | --- | --- |
| codex-agent-health-metrics | app/store.py, app/audit_web.py, app/cli.py, frontend/src/api/console.ts, frontend/src/pages/StatusPage.tsx, tests/test_store.py, tests/test_audit_web.py, tests/test_cli.py, tests/test_console_status_response.py, frontend/src/api/console.test.ts, frontend/src/pages/StatusPage.test.tsx, README.md, CHANGELOG.md | Meeting Memory lease-backed health payload, degraded health gate, components and status UI | 2026-09-15 |
| codex-attempt-detail-readable-history | app/web_api/attempts.py, frontend/src/pages/AttemptDetailPage.tsx, frontend/src/pages/AttemptDetailPage.test.tsx, frontend/src/styles.css, tests/test_console_attempt_detail_api.py, docs/agent-claims.md | Group Attempt runtime history and render audit explanations in user-facing language | 2026-09-15 |
| codex-session-readable-history | frontend/src/pages/CodexPages.tsx, frontend/src/pages/CodexPages.test.tsx, frontend/src/components/status/StatusBadge.tsx, frontend/src/components/status/StatusBadge.test.tsx, frontend/src/styles.css, docs/agent-claims.md | Explain unavailable/reused Codex sessions and group related Attempt history | 2026-09-15 |
| codex-session-history-docs | docs/architecture.md, docs/agent-claims.md | Document the user-visible meaning of unavailable Codex transcripts and related Attempt indexes | 2026-09-15 |
| claude-retired-category-save | app/web_api/email.py, frontend/src/pages/email/EmailList.tsx, frontend/src/pages/email/EmailReadingPanel.tsx, frontend/src/pages/EmailPage.test.tsx, tests/test_email_web_api.py, docs/agent-claims.md | Stop the console offering a retired category as a saveable value, and refuse one at the API boundary instead of as a 500 | 2026-09-15 |
| claude-todo-sync-skipped | app/todo_sync.py, app/store.py (task_todo_sync_outbox region and STORE_SCHEMA_VERSION), app/dispatcher/adapters.py (TaskTodoSyncOutboxQueueAdapter only), tests/test_todo_sync.py, tests/test_store.py (pinned schema version literal only), docs/agent-claims.md | Record a Todo that never qualified for a DingTalk mirror as `skipped` instead of a failed external delivery | 2026-09-16 |
| claude-email-action-attention | app/audit_web.py (_queue_attention_rows only), tests/test_audit_web.py (new Email action cases only), docs/agent-claims.md | Surface an exhausted failed email provider action in Attention, so it agrees with the status page's own failed count | 2026-09-16 |
| claude-email-action-skipped | app/email_provider_actions.py, app/email_store.py (direct action status sets, DDL, _migrate_v39_to_v40, EMAIL_SCHEMA_VERSION), tests/test_email_provider_actions.py, tests/test_email_store.py, docs/agent-claims.md | Let a direct email action end as `skipped` when its message has left the account, and let an interrupted claim retry (Derek: 消失的邮件就算 skip) | 2026-09-16 |
| claude-schema-version-test-rot | tests/test_email_store.py (migration-ladder assertions only), docs/agent-claims.md | Tie the schema-ladder tests to EMAIL_SCHEMA_VERSION so a version bump stops breaking nine unowned tests | 2026-09-16 |
| claude-memory-write-ceiling | app/meeting_memory_write.py, tests/test_meeting_memory_write.py, docs/agent-claims.md | Bound meeting Memory write retries so a dependency the model calls retryable cannot churn forever in `pending` | 2026-09-16 |
| claude-outbox-quality-gate | app/quality_gate.py, tests/test_quality_gate.py, docs/agent-claims.md | Cover `task_todo_sync_outbox` in the hourly quality gate so an exhausted or unreconciled DingTalk Todo effect is a violation | 2026-09-16 |
| claude-friday-turn-text | app/agent_runtime_router.py (_friday_turn_text, _friday_result_events and the Friday execute call only), tests/test_routed_codex_execution.py, tests/e2e/test_friday_runtime_fallback.py (parsers only), docs/agent-claims.md | Carry the developer instructions, business Skills and output schema into Friday Runtime's single message, and hand its final message back to parsers as runtime events | 2026-09-17 |
| claude-todo-deadline-backfill | app/task_deadline_backfill.py, app/schemas/todo_deadline_decision.schema.json, app/cli.py (backfill-todo-deadlines only), app/store.py (_validate_runtime_operation_workload task suffix only), tests/test_task_deadline_backfill.py, docs/agent-claims.md | Backfill a deadline onto every open TODO created without one and mirror owned ones to DingTalk Todo (Derek: 必须有截止日期 / 开始) | 2026-09-17 |
| claude-agent-error-service-owned | app/agent_reported_error.py, app/agent_wire_contracts.py (error_payload only), tests/test_agent_reported_error.py, tests/test_agent_contracts.py, tests/test_consumer_agent.py, tests/test_agent_runtime_worker.py (error-code assertions only), docs/agent-claims.md | The service, not the Agent turn, decides what a reported error code means and whether it retries (Derek 2026-09-17: 一律由系统定性) | 2026-09-17 |
| claude-capacity-same-route-retry | app/agent_runtime_router.py (capacity retry before route pause/failover only), tests/test_routed_codex_execution.py, docs/agent-claims.md | Retry a provider-full (429) failure three times on the same route with growing waits, then pause the route and switch runtime (Derek 2026-09-17) | 2026-09-17 |
| claude-one-runtime-path | app/agent_runtime_router.py (Claude branch and turn text), app/agent_runtime_production.py (adapter wiring), app/claude_runtime_adapter.py (input contract), app/agent_turn_runner.py (contract import), tests/test_routed_codex_execution.py, tests/test_agent_runtime_production.py, docs/agent-claims.md | Every workload reaches every configured runtime through one path (Derek 2026-09-17: 统一 runtime fallback 路径) | 2026-09-17 |
| claude-runtime-settings-ui | frontend/src/pages/SettingsPage.tsx (RuntimePanel only), frontend/src/pages/SettingsPage.test.tsx (Agent Runtime cases only), frontend/src/styles.css (.runtime-* rules only), docs/agent-claims.md | Make Settings / Agent Runtime legible: the configured failover order, equal route cards, one label style | 2026-09-17 |
| claude-meeting-rejected-target | app/meeting_alignment.py (MeetingAlignmentTargetError handler only), tests/test_meeting_alignment.py (rejected-target case only), docs/agent-claims.md | Keep the delivery target a roster rule rejected, so the failure says who the turn picked | 2026-09-17 |
| claude-unified-runtime-fallback | app/runtime_fallback.py, app/agent_runtime_router.py (fallback call site only), app/agent_turn_runner.py (fallback call site and sleeper only), tests/test_runtime_fallback.py, docs/agent-claims.md | One fallback decision for both runtime loops (Derek 2026-09-17: 统一 runtime fallback 路径，不要有多个) | 2026-09-17 |
| claude-todo-deadline-required | app/task_agent.py (todo create validation and prompt rule), tests/test_task_agent.py, docs/agent-claims.md | Require a concrete deadline on every TODO the task agent creates (Derek: 必须有截止日期) | 2026-09-17 |
| claude-self-agent-echo | app/worker.py (candidate filtering only), tests/test_worker.py, docs/agent-claims.md | Identify the service's own DWS delivery by the provider's AI-send marker so a renumbered read-back stops opening a run on our own message | 2026-09-16 |
| claude-evidence-gate-wiring | app/audit_agent.py, tests/test_audit_agent.py, docs/agent-claims.md | `989ed829` shipped `DingTalkSendEvidenceDriver` but `_parse_evidenced_result` only wrapped `parse_result` when `email_unsubscribe_tools` was truthy, so the new driver never actually ran for a DingTalk task; wire the wrap unconditionally (each driver already no-ops when out of scope) | 2026-09-16 |
| claude-oa-pending-page-size | app/dws_client.py (list-pending page size only), app/task_scanners.py (OA scan page size only), app/org_cache.py (list-pending default only), tests/test_dws_client.py, tests/test_task_scanners.py, docs/agent-claims.md | DingTalk now rejects `dws oa approval list-pending --limit` above 20 with 400002 参数错误 (21 fails, 20 works, verified live 2026-09-17), so every OA pending scan since ~11:00 UTC failed. Cap the page size at 20. Shipped `6b170c1b`; scan succeeded 15:42Z. | done |
| claude-owner-evidence-repair | app/task_agent.py (`_require_supported_owner` only), tests/test_task_agent.py, docs/agent-claims.md | Work summary 24736 failed terminally on `project.owner_evidence.source is required`: missing evidence fields raised a plain ValueError, so the existing owner-repair turn never ran. Raise OwnerResolutionRequired instead. Shipped `76750909`; 24736 done after the repair turn (run 9244). | done |
| claude-evidence-gate-failed-pipe | app/dingtalk_send_evidence.py (`_completed_native_command` only), tests/test_dingtalk_send_evidence.py, docs/agent-claims.md | Bug in the existing gate, not a new gate: audit run 20012 reported OA 341173 approved; the only write-shaped call was `dws oa approval oa-comments ... \| head -150`, which printed `--content is required` but exited 0 through `head`, so it counted and task closed done with no approval. A call that printed DWS's failure envelope no longer counts. Shipped `5fd9fa23`; replaying run 20012 now rejects it. Task 341173 is still marked done though the approval never ran: left for Derek (approve in DingTalk, or authorize reopening the task). | done |
| claude-todo-mirror-abandoned-link | app/store.py (`reconcile_unknown_task_todo_sync_outbox` only), tests/test_todo_sync.py, docs/agent-claims.md | The half-written link closed by unknown-create reconciliation was marked `failed`, so link 796 (TODO 1238) stayed a History failure after link 873 created DingTalk task 57249450733. Mark it `cancelled`, and move row 796. Shipped `38aa8e2b`; row 796 moved (backup kept). | done |
| claude-permission-mode-auto | app/claude_runtime_adapter.py (`--permission-mode` only), tests/test_claude_runtime_adapter.py, docs/agent-claims.md | Derek approved running the service's Claude turns in `auto` permission mode; under `default` every tool call was denied (341173: `skill_and_oa_read_permission_denied`). | active |
| claude-claim-without-tools | app/agent_effect_claim.py (new), app/consumer_agent.py (result parse wrapper only), app/audit_agent.py (`_parse_evidenced_result` only), tests/test_agent_effect_claim.py, docs/agent-claims.md | Derek approved 2026-09-17: a result claiming a completed external action (executed/已执行/已发送/已通过…) from a turn that made zero tool calls is a result-contract violation, so it gets the ordinary correction turn. Consumer run 20016 reported the 江淮 650k POC approved with no tool call at all. Shipped `aa252e9d`, then narrowed: judged per run it flagged 33 of 1095 completed runs in 7 days, 31 of them true statements about a sibling turn's work, so the check now spans the task's whole execution generation and drops the third-party phrases 已提交/已通过 — 1 hit in the same week, run 20016. | active |
| claude-provider-duplicate-final | app/agent_reported_error.py (one policy entry), tests/test_agent_reported_error.py, docs/agent-claims.md | Reply task 135612 failed four runs in a row: the turn notifies the applicant, DingTalk suppresses the repeat as a duplicate, no receipt returns, the bounded retry walks into the same suppression. Duplicate suppression means the effect already exists, so the code no longer retries. | active |
| claude-oa-authority-in-consumer-prompt | app/consumer_agent.py (OA paragraph only), tests/test_consumer_agent.py (that one case), docs/agent-claims.md | Same defect the OA session fixed in the scan prompt (`84a301c3`): the Consumer instructions named the vendor `dingtalk-misc/references/oa.md` as the OA authority, so turns read it instead of `dingtalk-oa-approval`, and `dws upgrade` overwrites anything written there. | active |
| claude-prepared-text-only | app/outbound_text_authority.py (new), app/audit_agent.py (`_parse_evidenced_result` only), tests/test_outbound_text_authority.py, docs/agent-claims.md | Derek approved the hard cut 2026-09-17: a turn may only send text the service prepared for the accepted action. Audit run 20020 composed Wayne's DM itself and corrupted the feedback links transcribing them. Replayed over 14 days, 15 of 57 sending runs used a prepared body and 42 composed their own. | active |
| claude-orphan-processing-task | app/store.py (`recover_interrupted_agent_runs_after_service_restart` only), tests/test_store.py, docs/agent-claims.md | Reply task 135612 sat `processing` for 33 minutes with no live run: its last Audit run had already failed, so restart recovery, which selected only tasks with a `running` run, skipped it, and Attention shows errors rather than states. At startup nothing of ours executes, so every `processing` task is an orphan and is requeued. | active |
| claude-handtyped-feedback-links | app/consumer_agent.py (`_prepare_outgoing_dingtalk_action` only), tests/test_consumer_agent.py, docs/agent-claims.md | Consumer run 20064 failed the whole turn with `feedback_callback_pair_invalid`: the proposed body carried feedback links the model typed itself, which the service rejects because it appends them. Failing the turn only sends the same body back, so it now takes a correction naming the rule. | active |
| claude-runtime-probe-command | app/agent_runtime_probe.py (timeout constants only), app/agent_runtime_production.py (`build_production_runtime_probe` only), app/cli.py (`probe-runtime-route` only), tests/test_agent_runtime_probe.py, tests/test_agent_runtime_production.py, tests/test_cli.py, docs/agent-claims.md | Raise the probe timeout to match a Friday turn's real latency and add an on-demand route self-test command (Derek 2026-09-17: 超时放大 / 加一个测试功能) | 2026-09-17 |
| claude-runtime-claude-api-card | app/web_api/registration.py (agent-runtime section only), app/audit_web.py (`handle_agent_runtime_config_post` only), frontend/src/pages/SettingsPage.tsx (Claude API card only), frontend/src/pages/SettingsPage.test.tsx (Claude API case only), tests/test_audit_web.py, tests/test_console_web_api.py, CHANGELOG.md, docs/agent-claims.md | Give Claude API the card the page already named, and refuse a save that names no route instead of silently disabling every optional one (Derek 2026-09-17) | 2026-09-17 |
| claude-added-runtime-routes | app/agent_runtime_config.py, app/agent_runtime_contracts.py (RuntimeRoute.base_url only), app/codex_runtime_adapter.py (provider settings only), app/audit_web.py (`handle_agent_runtime_config_post` helpers only), app/web_api/registration.py (agent-runtime section only), app/cli.py (`--route` only), frontend/src/pages/SettingsPage.tsx (added-runtime cards, add form, order controls), frontend/src/styles.css (.runtime-order-move/.runtime-move-button/.runtime-add-* only), frontend/src/pages/SettingsPage.test.tsx (added-runtime cases only), tests/test_agent_runtime_config.py, tests/test_audit_web.py, tests/test_console_web_api.py, tests/test_cli.py, CHANGELOG.md, docs/agent-claims.md | Let an operator add runtime routes by name (several of one kind, each with its own endpoint/model/token) and arrange the failover order (Derek 2026-09-17) | 2026-09-17 |
| claude-friday-auth-switch | frontend/src/pages/SettingsPage.tsx (FridayAuthFields only), frontend/src/pages/SettingsPage.test.tsx (Friday credential cases only), frontend/src/styles.css (.runtime-card-grid column count and .runtime-switch-inline only), CHANGELOG.md, docs/agent-claims.md | Ask for a Friday credential only when its authentication is on, one type at a time, and stop runtime cards pairing up per row (Derek 2026-09-17) | 2026-09-17 |
| claude-runtime-kinds-and-layout | app/agent_runtime_config.py (added-route kinds), app/audit_web.py (added-route validation and Friday project provisioning), app/friday_runtime_adapter.py (`ensure_friday_project` only), frontend/src/pages/SettingsPage.tsx, frontend/src/pages/SettingsPage.test.tsx, frontend/src/styles.css (.runtime-fields columns and .runtime-field-group only), tests/test_audit_web.py, CHANGELOG.md, docs/agent-claims.md | Four addable runtime kinds with per-kind fields, three fields per row, Claude API's shared model fields, a grouped Friday card and an auto-provisioned Friday project (Derek 2026-09-17) | 2026-09-17 |
| claude-runtime-card-lifecycle | app/friday_runtime_adapter.py (`bundled_friday_cli` only), app/audit_web.py (hidden-route handling and Friday CLI gate), app/web_api/registration.py (agent-runtime section only), frontend/src/pages/SettingsPage.tsx, frontend/src/pages/SettingsPage.test.tsx, frontend/src/styles.css (.runtime-card-actions/.runtime-card-unavailable/.runtime-restore-row/.runtime-fieldset only), tests/test_audit_web.py, tests/test_console_web_api.py, CHANGELOG.md, docs/agent-claims.md | Delete a runtime card as distinct from switching it off, restore a deleted built-in, and gate Friday on the desktop app that ships its CLI (Derek 2026-09-17) | 2026-09-17 |


## Recent overlaps worth knowing

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
