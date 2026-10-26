---
name: ceo-daily-report
description: Use when preparing and delivering Derek's end-of-day CEO report of important progress, risks, items needing his intervention, items to watch, and management advice for one Beijing calendar day.
metadata:
  managed_by: ceo-agent-service
  version: 1
---

# CEO Daily Report

Write one evidence-backed report of what mattered today, then publish it as a
DingTalk document and send Derek (磊哥) a short direct message pointing to it.

## Inputs

- **Service facts**: call `agent_cli.read_scheduled_context`, save its exact JSON
  as `context.json` using `consumer_artifact_write`. Run this Skill's
  `scripts/window.py --context context.json` with `consumer_run_skill_script`.
  The primary window is the report date in Beijing, from 00:00 through this
  run's actual `scheduled_for` (normally 21:00). If `catchup_window_utc` is not
  null, keep its facts separate from today's report. Read
  `source_window_utc` once with `read_service_activity(start, end)` and split
  returned items at the primary window's start; use `principal_user_id` from
  scheduled context. This covers both logical windows without duplicate reads.
  The activity contains `meetings`, `tasks`, `attention`, `important_emails`,
  `attempts` and `waiting`. Select completed handled items from `attempts`;
  skipped/no-action records are counts, not handled work. The source timestamps
  and status are facts; judge management relevance yourself.
- **Important mail** is what the mailbox flagged, not a verdict: report only the
  messages with management meaning (a request, approval, customer, partner,
  legal or money matter) and count the rest, such as sign-in codes.
- **Group chats**: use the task-bound
  `agent_cli.recent_dingtalk_conversations` operation once for
  `source_window_utc` to exhaust the inventory of accessible conversations,
  then call `agent_cli.read_dingtalk_messages_in_window` once for every
  accessible group conversation over that same range. An inventory is not a
  message read. Complete
  each conversation's pagination and count even an empty, successfully read
  window as covered. Split messages at the primary window's start and keep
  current-day and catch-up facts separate. Keep only
  messages with management meaning: commitments, changes of plan, disagreement,
  escalation, customer or financing signals, blocked work. Count the rest.
- **Meetings**: when a meeting in the facts needs more than its follow-up
  message, load `dingtalk-minutes` and read its summary.

Try every accessible group conversation. Whatever could not be read (not
attempted, failed, timed out or returned an incomplete page) is a coverage
gap, not a reason to hold the report: write it and deliver it anyway, and list
each such conversation in 覆盖说明 with the requested window and the exact
error, incomplete marker or `not attempted`. This is the general rule for
missing data in the base Developer prompt. Missing material never turns the
run into a question for Derek.

## Report

Title: `CEO 每日总结 YYYY-MM-DD`, using the window script's `report_date`. Sections, in order:

1. **今日要点** — three to five lines Derek should know if he reads nothing else.
2. **重要事项进展** — what moved today, by project or topic.
3. **风险** — fact, impact, suggested action for each.
4. **需要你介入** — the decisions or actions only Derek can take, most urgent
   first; include every item in the facts' `waiting` and every active
   `attention` item whose category is `decision`, and say what each
   one needs from him.
5. **需关注** — not yet his move, but getting worse or drifting; start from the
   active `attention` items of category `push` and `watch`.
6. **管理建议** — judgment drawn from the sections above; mark it as judgment.
7. **覆盖说明** — today's exact Beijing window, counts per source, and what
   was not read, failed or was partial.
8. **补报覆盖** — when a catch-up window exists, identify its exact interval and
   summarize its separately sourced items; otherwise omit this section.

Every item names its source: meeting title, group name, task or attention id, or attempt
id. Keep fact, participant statement, and your judgment distinct. A section
with nothing in it says `无`; never pad it.

## Deliver

1. Save the Markdown as `daily-report.md` with `consumer_artifact_write`.
   Run `scripts/destination.py --date YYYY-MM-DD` with `consumer_run_skill_script`.
   It resolves wiki `🎯  目标与执行` / `CEO 每日总结`, creates that folder if
   missing and returns the exact same-day target. Call `consumer_document_write`
   with artifact_name, title and doc_format="markdown"; use node_id for an
   existing document, or parent_id + workspace_id for creation. Use the returned
   provider receipt to record the write; no automatic content readback is required. Reuse
   the resolved folder URL in the direct message.
2. Send Derek (磊哥) one direct message from his own account, not a bot:
   propose it as a `dingtalk-chat` action with a direct-message operation,
   `target: {"user_id": <principal_user_id>}` and the body in
   `payload.content` — the 今日要点 lines, how many items need his
   intervention, the document title, and the link of the `CEO 每日总结`
   folder. Consumer proposes the direct message but never sends it. Audit
   reviews the exact proposal; the System Executor sends an approved message,
   adds the signature, records delivery and prevents duplicate sends on retry.

These two writes are the run's only external actions. Do not reply in any
group, change Tasks, or message anyone else.

## Audit coverage check

Before approving the document and direct-message proposal, Audit must check the
primary and optional catch-up windows against `scheduled_for`, and verify that
the single `source_window_utc` read covers both logical windows without a gap.
A conversation that was not read, or read only partly, is a disclosed gap, not
a reason to return the proposal: verify that 覆盖说明 names it with the window
and the exact error, incomplete marker or `not attempted` before approving the
otherwise valid report. Do not merge catch-up facts into today's coverage
counts. Do not require a second send or a new report to correct an audit return;
the normal revision flow keeps the original run and action.

**Retrying after an interrupted round.** An earlier round that stopped midway
leaves each write in one of two states, and the lookup above tells you which:
the document (or message) is there, or it is not. There means finish from it:
read it back, and overwrite it if its content differs. Not there means it was
never written, so write it now. A confirmed absence is the answer to "did the
earlier write happen", not a reason to fail the round; only a lookup that
itself fails or comes back incomplete leaves the state unknown.
