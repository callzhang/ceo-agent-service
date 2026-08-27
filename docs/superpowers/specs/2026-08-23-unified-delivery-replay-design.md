# Unified Delivery Replay Design

> **Historical specification.** The application-owned replay and
> reconciliation state described below is retained as historical context only.
> Current runs use the ordinary failed/retry contract documented in
> `docs/runtime-mechanism.md`.

**Date:** 2026-08-23  
**Status:** Implemented  
**Runtime commits:** `b0515b2`, `ebc970a`, `f7e4b27`, `911d57d`, `b6e8331`

## Problem

Older service versions could finish a reply task after Audit lost its Codex
session.  Those records have a completed Consumer proposal, a terminal Audit
result with `audit_recovery_session_missing`, and no completed delivery flow.
The former recovery code only replayed selected chat actions and separately
deferred group-policy-like content.  That made a delivery failure depend on
business wording rather than on evidence of delivery.

The recovery decision must instead answer one question: has the original
trigger already been delivered successfully?

## Scope

This design applies only to legacy terminal records selected by
`AutoReplyStore.recover_terminal_sessionless_audit_deliveries`:

- the reply task is `done`;
- the task belongs to the requested channel;
- a completed Audit recorded `audit_recovery_session_missing`;
- its completed Consumer parent contains a durable `proposal`.

It does not alter ordinary in-progress unknown-effect reconciliation.  A run
that is still `running`, `unknown`, or awaiting reconciliation continues to use
its existing state machine.  This narrowly scopes the replay to historical
session-loss failures rather than treating a possibly live external write as a
new delivery request.

## Delivery Rule

The replay eligibility check is independent of action type and message content.
It does not classify a proposal as a group announcement, policy, responsibility
change, calendar action, or ordinary reply.

The saved Consumer proposal is replayed when all scope conditions hold and
neither success ledger below exists for the same trigger:

1. `sent_replies` contains the task's `conversation_id` and
   `trigger_message_id`; or
2. an Audit run for the task has an execution receipt with
   `completed=1`, `persisted=1`, and `safe_to_confirm=1`.

Either ledger is sufficient proof that the delivery succeeded.  It prevents a
second external delivery.  A historical `side_effect_state='confirmed'` value
without either durable receipt is not delivery proof and therefore does not
block a retry.

## Replay State Transition

For an eligible record, the store performs one transaction that:

1. replaces the task's execution generation with a new UUID;
2. returns the task to `pending` with recovery code
   `legacy_sessionless_audit_delivery_replay`;
3. clones the original completed Consumer result into that generation;
4. leaves the original generation and its failure evidence immutable.

`AgentOrchestrator` recognizes this recovery code.  It starts Audit from the
saved Consumer proposal and does not request a new Consumer decision.  If Audit
requests a revision, it retries Audit against the same saved proposal.  The
replay is therefore a delivery retry, not a new content decision.

## Delayed Replay Annotation

When the replay begins more than 30 minutes after `trigger_create_time`, the
service appends this standalone paragraph to each textual action payload:

```
原消息生成于 YYYY-M-D HH:MM
```

The store updates all coupled representations of that text:

- `payload.content` when present;
- `payload.text` when present;
- matching command-line argument values in `payload.argv`;
- `expected_verification` text.

If the action has `--idempotency-key`, the replay replaces it with a digest
derived from the old key and the new execution generation.  This preserves
deduplication within the replay while making the recovered delivery an explicit
new attempt.

## Non-Goals

- Do not manufacture a new business proposal or change recipients/content.
- Do not mark an unproven send as successful.
- Do not replay a trigger with a successful delivery ledger.
- Do not change the read-only reconciliation policy for non-terminal unknown
  external effects.
- Do not use action keywords or content categories as replay gates.

## Acceptance Criteria

The implementation is accepted when the following remain true:

1. Chat, group, calendar, and other saved proposal actions all use the same
   legacy replay path.
2. A delayed replay updates both `content` and `text` payload contracts, argv,
   expected verification text, and the idempotency key.
3. A `sent_replies` record for the same trigger prevents replay.
4. A safe persisted execution receipt for the task prevents replay.
5. A missing delivery ledger allows replay even when an old Audit state says
   `confirmed`.
6. Frozen delivery replay never reopens Consumer because Audit requests a
   revision.

## Verification

Focused coverage lives in:

- `tests/test_store.py`: `sessionless_delivery_replay` and
  `recover_terminal_sessionless_audit` cases;
- `tests/test_agent_orchestrator.py`: `frozen_delivery_retry` cases.

The service must be restarted after runtime changes and the live History page
must remain available before this behavior is reported as deployed.
