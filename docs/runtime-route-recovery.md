# Runtime-route recovery contract

## Scope

This document covers retryable `runtime_route_unavailable` failures before an
Agent turn starts external work. It is separate from Audit `unknown` recovery:
an unknown Audit run may have dispatched an external action and is reconciled
read-only, whereas a runtime-route failure with `side_effect_state=none` has
not crossed the effect boundary.

## Invariant

The first route-unavailable result is deferred so a single worker pass cannot
spin through new runtime processes. On a later worker pass, a task may start
one fresh Consumer or Audit turn only when either the task still records the
same deferred error or it was explicitly reopened by the effect-free recovery
API. The fresh turn must independently pass current route health and capability
selection; it is not an authorization to ignore a route pause.

`reply_tasks.error` records the operator-visible recovery reason after an
explicit reopen. `reply_tasks.recovery_code` records that the reopen was
intentional. Scheduling must use that structured recovery fact rather than
mistaking the displayed reason for evidence that the route has not yet waited.

## Verification

Regression coverage exercises both Consumer and Audit paths: a safely reopened
route-unavailable run creates one new turn and then completes. The recovery API
rejects any generation with a running or unknown run, non-`none` side-effect
state, completed execution receipt, or recorded send.
