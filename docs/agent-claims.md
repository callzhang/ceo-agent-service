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
| Codex session `attention-reconciliation` | `app/audit_web.py`, `tests/test_console_web_api.py` | show every unresolved service error in Attention while keeping the four-hour window only in system health | 2026-09-11 |
| Codex session `consumer-email-minutes-repair` | `app/email_worker.py`, `tests/test_email_worker.py`, `app/task_scanners.py`, `tests/test_task_scanners.py`, `app/minutes_sync.py`, `tests/test_minutes_sync.py`, `app/quality_gate.py`, `tests/test_quality_gate.py` | repair technical needs_human projection, runtime confirmation quality projection, and avoidable minute scanner pagination failures | 2026-09-11 |
| Codex session `meeting-target-repair` | `app/meeting_alignment_agent.py`, `app/meeting_alignment_delivery.py`, `app/meeting_alignment_source.py`, `tests/test_meeting_alignment_agent.py`, `tests/test_meeting_alignment_delivery.py`, `tests/test_meeting_alignment_source.py` | retry source-aware meeting target validation failures and resolve organizer fallback before marking meeting jobs failed | 2026-09-11 |


## Recent overlaps worth knowing

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
