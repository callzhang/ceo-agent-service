# Follow-Up Reply Task Agent Design

Date: 2026-06-29

## Goal

Route replies to CEO Agent follow-up messages through the task agent instead of
using local keyword rules in `follow_up.py`.

The task agent should be the only component that decides whether a new message
updates a work project, TODO, owner, follow-up plan, or DingTalk Todo link.
The ordinary reply agent may notice that a message is task-related, but it must
not decide the project, TODO, follow-up draft, or final task state.

## Problem

`follow_up.py` currently treats short phrases such as "completed" or "already
done" as completion evidence. That creates two risks:

- A message can be incorrectly attached to the wrong follow-up or owner.
- A complaint about an incorrect follow-up can be mistaken for proof that the
  underlying task is complete.

Lily's feedback is the concrete failure case. She said that repeated follow-ups
on already completed items caused anxiety and reduced efficiency, and that one
overseas data compliance P0 follow-up was sent to the wrong owner. The correct
interpretation is not simply "task complete". The system should stop following
up with Lily for that item, update the owner context to Hu Ming and operations,
and keep the project open unless there is separate completion evidence.

## Decisions

- Remove task-state decisions from `follow_up.py` keyword matching.
- Keep follow-up sending, rate limits, stale checks, and DingTalk Todo status
  checks in `follow_up.py`.
- Treat a reply to a follow-up as a Work Item for the task agent.
- The ordinary reply agent can set lightweight routing signals, but cannot set
  `project_id`, `todo_id`, or `follow_up_id`.
- The task agent owns all task association and state changes.
- The task agent output should use the existing task decision JSON model, not a
  new action enum such as `complete_todo` or `correct_owner`.

## Current Context

The service already has:

- `work_projects`, `work_todos`, `work_updates`, and `follow_up_drafts`.
- `work_summary_inputs` as the queue of Work Items consumed by the task agent.
- A reply path that records `reply_attempts`.
- A task agent that can merge Work Items into existing projects and TODOs.
- DingTalk Todo sync that only pushes completion when a TODO is done with
  explicit completion evidence.

The missing piece is the bridge from follow-up replies to task-agent updates.

## Architecture

### Reply Agent Boundary

After ordinary reply processing, the reply path may enqueue a Work Item when the
message appears task-related. The Work Item should include:

- Message summary.
- Sender name and user id when available.
- Conversation id, conversation title, and message time.
- The raw or excerpted trigger text.
- Lightweight signals such as:
  - `possible_task_update`
  - `mentions_follow_up`
  - `progress_claim`
  - `owner_correction`
  - `complaint_about_followup`

These signals are only hints. They do not identify the task row to update.

### Task Agent Boundary

The task agent consumes the Work Item and retrieves candidate context:

- Recently sent follow-ups in the same conversation.
- Recently sent follow-ups for the same sender or owner.
- BM25 project and TODO candidates from the Work Item summary and source text.
- Existing work project background, facts, TODOs, updates, and follow-up drafts.
- DWS conversation context when the Work Item does not contain enough context.
- `memory_recall` when historical project background is likely relevant.

The task agent then outputs a normal task decision JSON. The decision expresses
the new state by updating existing fields:

- A completed TODO is represented by `work_todos.status = done` and non-empty
  completion evidence.
- Partial progress is represented by `work_updates`, while the TODO remains
  open or waiting on an owner.
- A wrong owner is represented by updating the TODO owner, creating a replacement
  TODO, or suppressing the old follow-up plan.
- An irrelevant or ambiguous message produces no task change.

No extra business action enum is needed.

### Follow-Up Sender Boundary

`follow_up.py` should keep deterministic delivery responsibilities:

- Send due follow-ups.
- Respect per-owner and per-group daily limits.
- Skip stale follow-ups.
- Check whether the linked DingTalk Todo is already done before sending.
- Skip follow-ups that already have explicit local completion evidence.
- Record send success, send failure, and suppression state.

It should not infer task completion from plain text.

## Data Flow

1. The service sends a follow-up and stores `follow_up_drafts.status = sent`,
   `sent_at`, target information, owner information, and the sent text.
2. A later DingTalk message is handled by the ordinary reply path.
3. The reply path decides how to respond or not respond as usual.
4. If the message may affect task state, the reply path enqueues a Work Item.
5. The task agent retrieves candidate projects, TODOs, and recent follow-ups.
6. The task agent writes task decision JSON with the updated project/TODO/
   follow-up state.
7. Existing persistence code applies the decision.
8. If a TODO becomes `done` with completion evidence, DingTalk Todo sync marks
   the linked DingTalk Todo done.

## Lily Acceptance Case

Given Lily replies that the bot is repeatedly following up on completed work and
that the overseas data compliance P0 item belongs to Hu Ming and operations:

- The system must not mark the whole overseas data compliance project done.
- The system must not keep following up with Lily for that item.
- The system should update project or TODO context to reflect Hu Ming and
  operations as the owner path.
- The system may create or update a Hu Ming/operations follow-up if the item is
  still open.
- The system should record Lily's complaint as durable task context so future
  follow-ups avoid the same mistake.

## Error Handling

- If the task agent cannot confidently match a reply to a project, TODO, or
  follow-up, it should not update task state.
- If the reply says the owner is wrong but does not identify the correct owner,
  the task agent should stop or suppress the mistaken owner follow-up and create
  a clarification follow-up only when it has a safe target.
- If the reply claims completion but lacks enough context to identify the TODO,
  the task agent may record a work update but must not mark a TODO done.
- If DWS or memory lookup fails, the task agent should proceed only when local
  context is enough; otherwise it should leave the task unchanged or ask for
  clarification.

## Testing

Focused tests should cover:

- Lily feedback: suppress Lily follow-up, correct owner context, keep project
  open.
- Clear completion reply: task agent marks the matched TODO done with completion
  evidence.
- Owner correction reply: task agent changes owner or follow-up plan without
  marking the TODO done.
- Ambiguous completion reply: no TODO is closed.
- Unrelated reply in the same conversation: no task state changes.
- DingTalk Todo sync: only runs when a TODO is actually marked done with
  evidence by the task decision.
- `follow_up.py` no longer closes TODOs from keyword matches.

## Non-Goals

- Do not replace the ordinary reply agent with the task agent.
- Do not make the reply agent own task association or task writes.
- Do not add a parallel follow-up reaction table unless existing
  `follow_up_drafts`, `work_updates`, and task decision data are insufficient.
- Do not treat every reply as a task Work Item; only route plausible task or
  follow-up replies.
