# Feedback Reopen Pressure-Test Evidence

This record preserves the bounded application test used while adding the reopen
workflow. It records observed decisions, not a reusable prompt or an
implementation template.

## RED baseline

A fresh agent analyzed a reopen under delivery pressure without seeing this
Skill. It correctly rejected direct SQLite writes and reuse of an old receipt,
but it did not produce a fully safe current-round workflow.

## Observed failures and rationalizations

- It could not name the reopen endpoint or its exact request body.
- It described the next round as “created or selected” after reopen, rather than
  recognizing that reopen itself creates no round.
- It said an existing commit “may avoid another code change”, leaving room to
  resolve without a fresh repair and receipt for the current round.
- It omitted explicit `retryable=0` from the authoritative backlog gate.

## GREEN observable behaviors

With the Skill available, a fresh agent named the exact reopen operation and
body. It stated that reopen creates no round and that claim creates the new batch and round.
It required that old evidence and associations remain historical and required zero `processing`, `failed`, and `retryable`.
It also required API persist and readback before resolution, kept direct SQLite
writeback forbidden, and required code, tests, commit, restart, new PID, and
health evidence for the current round.
