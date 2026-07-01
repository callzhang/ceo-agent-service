# Important Task Boundary Design

## Goal

Reduce noisy task creation by making the task system track only important
business, management, project, risk, and decision items. Routine process steps
should be ignored and should not create projects, TODOs, follow-ups, or DingTalk
Todos.

This design responds to Mina's feedback that some HR flow steps, such as
preparing offer or probation details that must happen before an offer can be
sent, should not become separate reminders.

## Current Problem

The current task-agent prompt already says low-risk one-off items and account or
tool issues should usually be discarded. It does not clearly define routine
business process steps as a negative boundary.

When a person says a generated TODO is unnecessary, the current task-agent path
can discard that feedback because it is not a stable new project update. That
means the complaint is recorded, but the mistaken TODO and pending follow-up can
remain active.

## Scope

In scope:

- Update task-agent behavior so routine process steps are ignored by default.
- Allow feedback about noisy or mistaken TODOs to cancel existing TODOs and
  suppress pending follow-ups.
- Backfill the existing noisy TODOs exposed by Mina's feedback.
- Add tests that protect the boundary between important tasks and process
  steps.

Out of scope:

- Replacing the task agent with deterministic keyword rules.
- Building a new task data model.
- Changing the task table UI.
- Changing the ordinary reply path or message wording.

## Definitions

Important items are work items where failure would materially affect company
goals, key projects, revenue, customer commitments, organization decisions,
critical hiring, compliance, finance, or Derek-level decisions. Important items
may create or update projects, TODOs, follow-ups, and DingTalk Todos.

Routine process steps are normal execution steps inside an already-understood
workflow. Examples include scheduling an interview, preparing an offer document,
collecting standard candidate materials, submitting an approval, adding an
attachment, routing a form, confirming an ordinary calendar detail, or doing a
normal administrative step that must happen before the process can move on.
These should be ignored by the task system unless the work item also contains an
important risk or decision.

## Decision Rules

The task agent should apply this priority order:

1. If the work item is routine process content and contains no important risk,
   output `discard`.
2. If the work item is feedback that an existing TODO or follow-up is noisy,
   wrong, too granular, or should not be tracked, update the related project only
   if needed, cancel the matched TODO, and suppress related unsent follow-ups.
3. If the work item is routine process content but exposes a real risk, system
   fault, cross-owner blocker, deadline risk, or Derek decision, treat only that
   risk as the task.
4. If the work item is an important item, keep the existing task-agent behavior:
   merge into the right project, update facts and state, create or update TODOs
   only when there is an actionable owner, deadline, and follow-up reason.

## Examples

Discard:

- "Candidate arrived at 12:40."
- "Please prepare the offer/probation target one-pager before sending offer."
- "Submit this approval attachment."
- "The interview has been scheduled."

Cancel existing noisy TODO:

- "This kind of thing does not need a TODO; if I do not do it, the offer cannot
  be sent anyway."
- "Do not remind me about these routine HR flow steps."

Create or keep task:

- "The interview system did not create a record, so results cannot be written."
- "This critical CTO offer needs Derek to confirm cash and equity boundaries."
- "The owner is wrong; this should go to another person."
- "A customer commitment is at risk if this is not resolved today."

## Architecture

The primary change belongs in the task-agent decision boundary, not in a
pre-agent keyword filter. The reply path should continue to enqueue plausible
work items. The task agent should decide whether the item is important enough to
track.

Implementation should update the task-agent prompt with explicit routine-process
negative rules and add validation/tests around the expected JSON behavior. The
existing `follow_up_changes` and `todo_changes` models are sufficient for
canceling TODOs and suppressing follow-ups; no new action type is required.

## Data Flow

For new routine process content:

1. Reply or scanner creates a Work Item.
2. Candidate retrieval provides related projects, TODOs, and follow-ups.
3. Task agent classifies the content as routine process content with low
   failure risk.
4. Task agent returns `action=discard`, with `failure_risk_score` near zero.
5. No project, TODO, follow-up, or DingTalk Todo is created.

For feedback about noisy TODOs:

1. Reply path creates a Work Item from the feedback.
2. Candidate retrieval surfaces recent follow-up and matching TODO context.
3. Task agent returns `update_project`.
4. `todo_changes` cancels the noisy TODO.
5. `follow_up_changes` suppresses pending follow-ups for that TODO.
6. `work_updates` records that the item was canceled because it is routine
   process content and should not be tracked.

For important exceptions:

1. Task agent identifies the important risk or decision separately from the
   routine flow.
2. It creates or updates a TODO only for the important risk or decision.
3. Follow-up questions must ask about the risk or decision, not the routine step.

## Backfill

Backfill should be conservative:

- Start from Mina's feedback window and the related HR/recruiting TODOs.
- Cancel existing open TODOs that are only routine process steps.
- Suppress draft follow-ups tied to those canceled TODOs.
- Keep project facts and updates only when they describe useful context.
- Do not cancel TODOs that represent critical hiring decisions, system faults,
  Derek approval boundaries, or cross-owner blockers.

Backfill should produce an auditable list of changed TODO IDs and reasons before
and after applying updates.

## Error Handling

If task-agent context is insufficient to know whether a TODO is routine process
content or important, it should not create a new TODO. It may discard or keep the
existing TODO unchanged, depending on available evidence.

If feedback says a reminder is wrong but the related TODO cannot be confidently
matched, the agent should not cancel unrelated TODOs. It should record no state
change and report low confidence.

If DingTalk Todo sync has already created an external TODO for a noisy internal
TODO, this change should not add a new external-cancel capability. The internal
TODO should still be canceled, related follow-ups should be suppressed, and the
DingTalk link should retain an audit note explaining that external cancellation
is not part of this change.

## Testing

Add or update tests for:

- Routine HR process content is discarded and creates no TODO.
- Mina-style noisy TODO feedback cancels the matched TODO and suppresses pending
  follow-ups.
- A routine process item with a real system fault still updates or creates an
  important TODO.
- A critical hiring decision remains tracked.
- Backfill dry-run classifies routine process TODOs separately from important
  TODOs.

## Acceptance Criteria

- New routine process work items do not create projects, TODOs, follow-ups, or
  DingTalk Todos.
- Feedback that a TODO is too granular can cancel the existing TODO and suppress
  related follow-ups.
- Important risks and decisions inside workflows are still tracked.
- Mina's noisy HR flow TODOs are cleaned up through backfill without removing
  important recruiting or organization items.
- The change is covered by focused tests.
