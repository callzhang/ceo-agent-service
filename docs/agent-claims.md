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
| Claude session `ceo-agent-service-f6` (Agent Cron) | `app/agent_cron/*`, `app/cli.py` (scheduled-task subcommands and `_service_command_registry`), `app/web_api/scheduled_tasks.py`, `frontend/src/api/scheduledTasks.ts`, `frontend/src/pages/ScheduledTasksPage.tsx` + their tests | scheduled-task execution forms (Agent task vs service command), the downstream-consumer descriptor, and the hourly recovery split | 2026-09-10 19:45Z |
| Claude session `ceo-agent-service-f6` (execution boundary) | `app/agent_effect_guard.py`, `app/consumer_agent.py` + `tests/test_agent_effect_guard.py` | detecting provider effects produced by a Consumer proposal turn (task 383537) | 2026-09-11 01:40Z |
| Codex session `attention-reconciliation` | `app/audit_web.py`, `tests/test_console_web_api.py` | show every unresolved service error in Attention while keeping the four-hour window only in system health | 2026-09-11 |

## Recent overlaps worth knowing

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

  The open work is that `sync-minutes-once` does not yet perform the sync: it
  binds `scan_ai_minutes`, which only pages the list and enqueues work-summary
  inputs. `grep -rn "export-pack\|apply-permission" app/ scripts/` finds
  nothing, and `~/Documents/memory/AI听记` has had no new content since
  2026-09-03, so local archiving has in fact stopped. Making the command do
  the real sync (list → export-pack → archive → content cursor, with
  apply-permission on denial) belongs to the Agent Cron owner. Do not convert
  this task back to an Agent task.
| Codex session `contract-quality` | `app/agent_contracts.py`, `app/agent_wire_contracts.py`, `app/schemas/consumer_agent_result.schema.json`, `app/schemas/audit_agent_result.schema.json`, `tests/test_agent_contracts.py` + related wire tests | decision quality fields and strict wire/schema contracts | 2026-09-10
| Codex session `dispatcher-health-fix` (takeover authorized by `/root`) | `app/cli.py` dispatcher construction hunk, `tests/test_cli.py` dispatcher wiring regression | Wire Dispatcher heartbeat to service health; preserve Agent Cron owner's other changes | 2026-09-11 |
