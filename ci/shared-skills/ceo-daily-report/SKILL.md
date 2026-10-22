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
  Its report date and window preserve missed days and same-day reruns. Call
  `read_service_activity(start, end)` using that window. This is the required
  service input; use `principal_user_id` from scheduled context.
  The activity contains `meetings`, `tasks`, `attention`, `important_emails`,
  `attempts` and `waiting`. Select completed handled items from `attempts`;
  skipped/no-action records are counts, not handled work. The source timestamps
  and status are facts; judge management relevance yourself.
- **Important mail** is what the mailbox flagged, not a verdict: report only the
  messages with management meaning (a request, approval, customer, partner,
  legal or money matter) and count the rest, such as sign-in codes.
- **Group chats**: use the task-bound `agent_cli.recent_dingtalk_conversations`
  operation to exhaust the inventory of accessible conversations in the report
  window, then call `agent_cli.read_dingtalk_messages_in_window` for every
  accessible group conversation. An inventory is not a message read. Complete
  each conversation's pagination and count even an empty, successfully read
  window as covered. Keep only messages with management meaning: commitments,
  changes of plan, disagreement, escalation, customer or financing signals,
  blocked work. Count the rest.
- **Meetings**: when a meeting in the facts needs more than its follow-up
  message, load `dingtalk-minutes` and read its summary.

Do not write the report or propose its notification while any required group
conversation has not been read. Not attempted is not a source failure. A group
conversation may be recorded as unavailable or partial only when its read
operation actually fails, times out, or returns an incomplete page; record the
conversation, requested window, and exact error or incomplete marker. In that
case the report is still written and delivered with the limitation disclosed.
Missing optional material never turns the run into a question for Derek.

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
7. **覆盖说明** — what was read (counts per source), what failed or was partial.

Every item names its source: meeting title, group name, task or attention id, or attempt
id. Keep fact, participant statement, and your judgment distinct. A section
with nothing in it says `无`; never pad it.

## Deliver

1. Save the Markdown as `daily-report.md` with `consumer_artifact_write`.
   Run `scripts/destination.py --date YYYY-MM-DD` with `consumer_run_skill_script`.
   It resolves wiki `🎯  目标与执行` / `CEO 每日总结`, creates that folder if
   missing and returns the exact same-day target. Call `consumer_document_write`
   with artifact_name, title and doc_format="markdown"; use node_id for an
   existing document, or parent_id + workspace_id for creation. Read the
   returned readback artifact and compare it with your submitted body. Reuse
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
report window and the group-chat coverage statement against the task's source
evidence. If the inventory contains an accessible conversation with no
`read_dingtalk_messages_in_window` attempt and no provider error, return the
proposal: this is unattempted required coverage, not an allowed partial-source
case. If a read was attempted and the provider returned an error, timeout, or
incomplete page, verify the exact limitation is disclosed before approving the
otherwise valid report. Do not require a second send or a new report to correct
an audit return; the normal revision flow keeps the original run and action.

**Retrying after an interrupted round.** An earlier round that stopped midway
leaves each write in one of two states, and the lookup above tells you which:
the document (or message) is there, or it is not. There means finish from it:
read it back, and overwrite it if its content differs. Not there means it was
never written, so write it now. A confirmed absence is the answer to "did the
earlier write happen", not a reason to fail the round; only a lookup that
itself fails or comes back incomplete leaves the state unknown.
