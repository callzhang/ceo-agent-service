---
name: ceo-work-tracking
description: Use when a message, meeting, or decision creates trackable work that needs extraction, assignment, project or TODO creation, follow-up, completion evidence, or closure. Use ceo-message-triage when no durable work item is needed and ceo-meeting-work for meeting synthesis before actions are confirmed. Load the relevant task operation Skill before changing tracked work.
metadata:
  managed_by: ceo-agent-service
  version: 1
---

# CEO Work Tracking

Treat extraction, creation, follow-up, replies, completion verification, and
closure as one lifecycle. Preserve identity, intent, evidence, and links across
every state change. Do not use keyword routers, hardcoded business terms,
person names, or static routing branches to make work decisions.

Load `dingtalk-todo` for DingTalk TODO operations, `task-management` for local task records, and `dingtalk-chat` before requesting updates or reporting closure.

## Lifecycle Decision

1. Decide whether the input deserves durable tracking. Discard ordinary process
   motion and low-consequence coordination. Track a commitment only when losing
   it would materially affect an established goal, delivery, decision, risk, or
   obligation supported by the current evidence.
2. Choose a one-time action, TODO, or project. Use a project only for durable
   multi-step work, a TODO for one owned deliverable that needs state, and a
   one-time action when no persistent follow-up is needed.
3. Require stable owner identity and owner evidence before assigning work or
   drafting a follow-up. Record the evidence source, why it proves ownership,
   and the supporting fact. A participant, speaker, sender, reporter, group
   member, contact lookup, or name match proves identity or presence only; it
   does not prove responsibility. Never infer an owner from those roles.
4. For tracked work, preserve the deliverable, deadline, completion standard,
   priority, source context, and the consequence of non-completion. Do not make
   a vague title stand in for missing scope.
5. Every follow-up must bind to a TODO, either by an existing `todo_id` or by the
   same `todo_ref` as a TODO created in the decision. Do not create a reminder
   that exists independently from owned work.
6. Select a schedule during local work hours and select the actual audience.
   Use a verified source group only when that group is appropriate and includes
   the intended owner. Use a verified direct identity for sensitive content.
   If owner or target evidence is missing, repair the existing work item or ask
   for that evidence; do not ask the service to guess or reroute it.
7. Read current project, TODO, and external status before following up. Load the
   operation Skills needed to read live status. Historical summaries and an old
   draft do not establish current state.
8. Close the TODO and suppress its open follow-ups when current evidence proves
   completion or cancellation. Preserve the evidence source, reason,
   description, and completion time. Do not close from a status-like phrase
   without evidence tied to the deliverable.
9. If the TODO is still open after current-state reads, ask one concise,
   contextual progress question. Mention the source and request the missing
   result, blocker, or date; do not reassign the work in reminder wording.
10. Apply replies to the existing work item. Update, close, reschedule,
    reassign, or suppress the matched TODO and follow-up; do not create a second
    project, TODO, or follow-up for the same commitment.

## Lifecycle Cases

- `routine_process_is_discarded`: discard routine process motion unless the
  evidence establishes a material tracked commitment or risk.
- `important_commitment_creates_todo_with_owner_evidence`: create a TODO only
  with a concrete deliverable and stable owner evidence.
- `follow_up_cannot_exist_without_todo`: bind the follow-up to its open TODO.
- `participant_or_speaker_is_not_owner_evidence`: do not assign or contact a
  person merely because they participated, spoke, sent, reported, or appeared.
- `due_follow_up_refreshes_live_todo_before_send`: require current TODO and
  external-status reads before deciding that a due reminder remains useful.
- `completed_todo_suppresses_follow_up`: close or suppress all pending reminders
  when supported completion evidence exists.
- `owner_correction_updates_todo_and_suppresses_old_draft`: update the existing
  TODO owner using the correction evidence and suppress the draft aimed at the
  previous owner before considering a new draft.
- `follow_up_reply_updates_existing_work_item_instead_of_creating_duplicate`:
  match the reply to the existing TODO and follow-up and mutate those records.
- `stale_follow_up_is_skipped`: when an old draft is presented for
  reevaluation, read its current project, TODO, external status, and replies;
  suppress it only when those facts show the old question is no longer
  appropriate. The service may enqueue reevaluation but cannot decide the
  semantic outcome. If the decision keeps the follow-up open, provide a new
  future work-hours schedule; the revised draft is a new repair revision.
- `sensitive_follow_up_uses_verified_direct_target`: use only the verified
  direct identity selected in the decision; never convert a group target to a
  direct target in service code.

## Memory And Evidence

Memory is optional context, not completion evidence. When Memory is available,
call `memory_recall` before creating or updating a project and record the query
and useful result in `project.memory_context`. Only an actual successful recall
sets `memory_recall_used=true`. If the tool is absent or fails, continue from
current supplied and live evidence, set `memory_recall_used=false`, and record
the unavailable status and substitute evidence in `project.memory_context`.

Use current source material and live systems as authority for owner, target, and
completion state. Load a specialist Skill when a tracked item belongs to a
specialized workflow instead of copying that workflow here.

## Service Boundary

The service owns persistence, scheduled wake-up, due-time and local-work-hours
guards, bound-TODO existence, live DingTalk Todo completion refresh, local
completion-evidence checks, exact-message idempotency, and sent-result or retry
state. The service does not decide importance, ownership, audience, sensitivity,
schedule meaning, completion meaning, or semantic similarity to older work.
Preserve exact-message idempotency. A corrected or materially changed message
is a new revision and is not blocked merely because an older message was stored
or sent.
