# Stale WeChat Delivery Closure Design

## Problem

Feedback batch resolution requires a fresh global backlog receipt with zero
`processing`, `failed`, and `retryable` work. Production feedback
`manual:8308` is otherwise complete, but WeChat delivery `#81` remains
`failed` after two safe pre-action retries. Its failure is
`target_open_failed`, `pre_action_failure=1`, and no message was sent.

Requeueing it above the existing retry limit could send a stale reply. Reusing
the legacy `user_rejected` normalization would falsely claim that the user
rejected the draft. Direct SQLite updates are forbidden by the repository
Feedback Skill.

## Decision

Add one explicit operator operation that terminally skips an exhausted,
stale, pre-action WeChat delivery without invoking a sender. The operation is
available through the existing local Python CLI; it does not add a Console UI,
Feedback API route, Agent workflow, or automatic cleanup loop.

The Store operation accepts an exact delivery ID, its expected execution
generation, a non-empty factual reason, an inactivity cutoff, and the retry
limit used by the normal recovery path. It succeeds only when all of the
following remain true inside one write transaction:

- the delivery belongs to the current reply-task execution generation;
- status is `failed`;
- `pre_action_failure=1`;
- the failure code is `target_open_failed`;
- `action_started_at` is non-empty and no later than the supplied cutoff;
- the latest matching WeChat reply attempt has `retry_count` greater than or
  equal to the supplied retry limit;
- there is no uncertain or confirmed delivery outcome to reconcile.

On success it changes only the current projection:

- delivery status becomes `skipped`;
- `pre_action_failure` becomes `0`;
- the factual operator reason is retained in `error`;
- the associated reply attempt becomes `skipped` with the same reason.

Existing task, attempt, delivery, and runtime rows remain available as audit
history. A second call is rejected without changing state; it never reports a
second successful close.

## CLI Contract

Add the top-level command:

```text
python -m app.cli skip-stale-wechat-delivery \
  --delivery-id <positive-int> \
  --inactive-before <ISO-8601 timestamp> \
  --reason <non-empty factual reason> \
  [--max-retries 2]
```

The command reads the current delivery to obtain its execution generation,
then calls the guarded Store operation. Success prints exactly:

```text
wechat-delivery skipped=<delivery-id>
```

Missing, changed, non-exhausted, non-pre-action, non-stale, or already-terminal
records fail without mutation and without sending.

## Boundaries

- No sender, WeChat UI automation, target lookup, generation rotation, model,
  Audit Agent, or delivery retry is called.
- No direct SQLite maintenance command is documented or used.
- No generic “dismiss any failure” operation is introduced.
- The operation does not infer staleness; the operator supplies an explicit
  cutoff and reason after inspecting the record.
- This change does not weaken Feedback resolution evidence or global backlog
  gates.

## Verification

Tests must prove the Store transition and attempt synchronization, and must
reject every missing guard. A CLI test must prove exact argument forwarding and
the stable success receipt. The regression test must be observed failing before
implementation, then passing after implementation. Focused WeChat Store/CLI
tests, the Feedback processing suites, Ruff, and `git diff --check` must pass
before integration.
