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
| Claude session `ceo-agent-service-76` (failed-item repair) | `app/email_unsubscribe.py`, `app/email_unsubscribe_audit.py`, `app/audit_agent.py`, `app/email_worker.py`, `app/email_task_adapter.py`, `app/meeting_alignment_agent.py`, `app/meeting_alignment_models.py` + their tests | closing the live `failed` reply tasks (skip outcomes projected as failures, unsubscribe browser page state, meeting schema guidance) | 2026-09-10 19:00Z |

## Recent overlaps worth knowing

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
  and `+apply-permission` to request access automatically when a minute is
  denied. No Agent judgement is involved, so the Agent form was the wrong
  shape and the claim above is withdrawn.

  The open work is that `sync-minutes-once` does not yet perform the sync: it
  binds `scan_ai_minutes`, which only pages the list and enqueues work-summary
  inputs. `grep -rn "export-pack\|apply-permission" app/ scripts/` finds
  nothing, and `~/Documents/memory/AI听记` has had no new content since
  2026-09-03, so local archiving has in fact stopped. Making the command do
  the real sync (list → export-pack → archive → content cursor, with
  apply-permission on denial) belongs to the Agent Cron owner. Do not convert
  this task back to an Agent task.
