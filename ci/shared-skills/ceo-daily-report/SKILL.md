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

- **Service facts**: call the task-bound `agent_cli.daily_report_facts` read
  operation. The service works out the report window:
  from the end of the last report that went out to this trigger, so missed days
  are folded in. Its `report_date` is the document date and its `window_utc` is
  the span every other source is read over. Do not ask anyone to confirm either.
  It also returns the meetings and their follow-up messages, the Tasks that
  changed with their events, every active business attention item, the mail
  the service flagged important, the items the service handled, the items
  waiting on Derek and the principal user id. This is the only required input.
- **Important mail** is what the mailbox flagged, not a verdict: report only the
  messages with management meaning (a request, approval, customer, partner,
  legal or money matter) and count the rest, such as sign-in codes.
- **Group chats**: use the task-bound `agent_cli.recent_dingtalk_conversations`
  and `agent_cli.read_dingtalk_messages_in_window` read operations for accessible
  group conversations inside the report window, paging until the window is
  covered. Keep only messages with management meaning: commitments, changes of
  plan, disagreement, escalation, customer or financing signals, blocked work.
  Count the rest.
- **Meetings**: when a meeting in the facts needs more than its follow-up
  message, load `dingtalk-minutes` and read its summary.

A source that fails, times out, or is only partly read is recorded in the
report's coverage section and the report is still written and delivered.
Missing optional material never turns the run into a question for Derek.

## Report

Title: `CEO 每日总结 YYYY-MM-DD`, using the facts' `report_date`. Sections, in order:

1. **今日要点** — three to five lines Derek should know if he reads nothing else.
2. **重要事项进展** — what moved today, by project or topic.
3. **风险** — fact, impact, suggested action for each.
4. **需要你介入** — the decisions or actions only Derek can take, most urgent
   first; include every item in the facts' `waiting_on_derek` and every active
   `business_attention` item whose category is `decision`, and say what each
   one needs from him.
5. **需关注** — not yet his move, but getting worse or drifting; start from the
   active `business_attention` items of category `push` and `watch`.
6. **管理建议** — judgment drawn from the sections above; mark it as judgment.
7. **覆盖说明** — what was read (counts per source), what failed or was partial.

Every item names its source: meeting title, group name, task or attention id, or attempt
id. Keep fact, participant statement, and your judgment distinct. A section
with nothing in it says `无`; never pad it.

## Deliver

1. Use the task-bound `agent_cli.consumer_document_write` operation. The
   service resolves the wiki `🎯  目标与执行`, the `CEO 每日总结` folder, and the
   exact document title from this scheduled run; it creates or overwrites only
   that report document. Compare the returned readback with the submitted body.
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

**Retrying after an interrupted round.** An earlier round that stopped midway
leaves each write in one of two states, and the lookup above tells you which:
the document (or message) is there, or it is not. There means finish from it:
read it back, and overwrite it if its content differs. Not there means it was
never written, so write it now. A confirmed absence is the answer to "did the
earlier write happen", not a reason to fail the round; only a lookup that
itself fails or comes back incomplete leaves the state unknown.
