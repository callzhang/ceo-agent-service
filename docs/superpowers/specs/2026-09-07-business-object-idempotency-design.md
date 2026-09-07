# Business Object Idempotency Design

## Problem

The queue currently identifies a `reply_task` by channel, conversation, and
trigger message. One real business object can arrive through several sources,
so the same OA node can be processed concurrently as unrelated tasks. External
write identity is also scoped to one Agent run. A later task therefore cannot
reuse a completed approval or notification from an earlier task.

This caused one OA node to produce repeated comments, repeated applicant
messages, and two approval-result notifications.

## Design principle

One real business object has one current queue projection. New triggers append
input facts to that object; they do not create another current workflow.
Execution history stays append-only. External writes have identities that are
stable across task generations and Agent runs, so a completed write is reused
instead of replayed.

The service does not judge shell commands, require read-only recovery, or add
an unknown-effect business state. It persists only the facts needed to route
work and avoid duplicate external writes.

## Stable business object

`reply_tasks.business_object_key` identifies the real item being processed.
For an OA node the canonical value is derived from the process instance and
task identifiers. Other producers may provide their own stable key; existing
message tasks fall back to their current channel/conversation/message identity.

`business_object_tasks` maps one key to its current `reply_task`. Historical
duplicate tasks and all runs remain unchanged. A startup migration chooses the
most recent task as the current projection for an existing key without moving
or deleting older runs.

`reply_task_inputs` is append-only. It records every source trigger and links it
to the current task. The fields shown on `reply_tasks` are the latest input
projection; replacing those fields does not erase the input history.

If another input arrives while the task is processing, the store increments an
input version. Completion compares the claimed version with the current
version. A newer input requeues the same task with a new execution generation,
so stale workers cannot write and the next Agent run sees the latest trigger.

## Stable external action

Each proposed action supplies `action_identity`, a short stable identifier for
the intended external outcome within the business object. Examples are
`oa-node-approved`, `applicant-notified-approved`, and
`applicant-asked-for-required-field`. This separates two legitimate messages
about different states while making two equivalent approval notifications the
same action.

The service combines the business object key, `action_identity`, operation,
and target into an `external_action_key`. Payload wording is not part of this
key; otherwise a rewritten duplicate message would bypass deduplication.

`external_action_results` stores the first completed provider receipt for that
key. Before dispatch, a later run checks this table. If the action is already
complete, it returns the stored provider result without executing the provider
again. For message providers, the same key is also used to derive the provider
idempotency value, protecting the request/response interruption window.

Prepared and failed attempts remain in `agent_effect_intents`. They are
execution history, not current business state.

## Ordered actions

Proposal actions are an ordered transaction boundary. Action N may execute
only after every earlier action has a successful provider result, either from
this run or from `external_action_results`. If an earlier action fails, later
actions are not dispatched. A later run may reuse earlier completed results and
continue with the first incomplete action.

This prevents an applicant notification from being sent when the approval in
the same proposal failed. It also allows a notification to proceed when the
approval was completed by an earlier run and its exact action identity matches.

## Message History projection

Every successful DingTalk message action is recorded through one store method,
regardless of whether it originated from a normal reply or an Audit action.
The stored provider result includes the stable message and conversation
identifiers. Reusing a completed action links the new run to the original
receipt without inserting another outbound message.

History displays the one real outbound message and all runs that reused its
result. It does not infer delivery from a terminal Agent result alone.

## Migration

Schema migration runs through `AutoReplyStore`; no production SQLite file is
edited manually. The migration:

1. adds stable keys and input-version columns;
2. backfills one append-only input row for every existing task;
3. derives OA keys where exact process and task identifiers are available;
4. maps each business key to its most recent task as the current projection;
5. leaves all existing tasks, runs, sessions, events, attempts, and sent replies
   intact.

Historical messages are not retroactively declared idempotent unless their
business object, action meaning, target, and successful provider result can all
be recovered exactly.

## Acceptance criteria

- Distinct OA scan and work-notification triggers for one OA node resolve to
  one current `reply_task`.
- A trigger received during processing causes a later run on the same task,
  with no concurrent second task.
- Equivalent actions across revisions, generations, and historical duplicate
  tasks execute at most once.
- A failed earlier action prevents every dependent later action from running.
- Successful Audit message actions always appear in History with provider
  identifiers.
- Old execution facts remain queryable and no production data is manually
  rewritten.
- Focused agent/store/worker/History tests and the complete suite pass before
  deployment.
