# Reply worker reliability

## Failure visibility

`produce-once` and `consume-once` record top-level failures in the `errors` table
and raise a local macOS notification before exiting non-zero. Launchd keeps the
five-minute producer schedule, so transient producer failures are visible locally
and retried by the next scheduled run.

Per-conversation read failures are recorded and notified without blocking other
conversations in the same producer pass.

## Consumer retry behavior

Reply tasks move from `pending` to `processing` when claimed. If task processing
raises an exception, the consumer records a retry error, sends a local
notification, and moves the task back to `pending` until the task reaches the
maximum attempt count. The default maximum is three claimed attempts.

When the maximum is reached, the task is marked `failed`, the final error is
recorded, and a local notification is sent.

Processing tasks older than the stale-task threshold are also moved back to
`pending`; this recovery path sends a local notification so the operator can see
that an interrupted task was retried.
