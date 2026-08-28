# Runtime-route recovery contract

## Scope

This document covers retryable runtime execution failures before an Agent turn
starts. Route recovery is ordinary retry scheduling; it is not an
Audit state-check flow and does not inspect tool names or external-effect
state. If an Agent turn was interrupted after an external call, the next turn
uses the current business Skill and any persisted provider identifier to decide
what to do. The service does not create a separate unknown-outcome state.

## Invariant

The first runtime execution failure is deferred so a single worker pass cannot
spin through new runtime processes. On a later worker pass, a task may start
one fresh Consumer or Audit turn only when either the task still records the
same deferred error or it was explicitly reopened by the retry API. The fresh turn must independently pass current route health and capability
selection; it is not an authorization to ignore a route pause.

`reply_tasks.error` records the operator-visible recovery reason after an
explicit reopen. `reply_tasks.recovery_code` records that the reopen was
intentional. Scheduling must use that structured recovery fact rather than
mistaking the displayed reason for evidence that the route has not yet waited.

## Health-snapshot renewal

The runtime probe loop must renew a healthy snapshot shortly before its expiry.
Its cadence can otherwise wake a few milliseconds before the exact expiry,
skip the still-current snapshot, and leave a following Audit turn without an
eligible route for a full additional probe interval. Early renewal changes only
the health evidence; route selection still rejects unhealthy, paused, or
capability-incomplete routes.

## Verification

Regression coverage exercises both Consumer and Audit paths: a safely reopened
runtime failure creates one new turn and then completes. The retry API only
checks lease ownership, generation, and whether the requested retry is still
current. A provider result identifier, when present, is carried with the same
operation and target so the Agent can avoid duplicate work; it is not converted
into an application-level state.
