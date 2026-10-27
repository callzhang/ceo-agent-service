# Email classification retry API validation

## Production case

Production task `417571` failed after the bounded four-attempt classification
budget with `IMAP4.abort: CAPABILITY => socket error: EOF`. It had no saved
classification result, no generated Agent action, and no unsubscribe candidate.
Its stable message identity and immutable task input remain available for a
selected retry after the transport error.

## Change

`POST /api/console/email/classification-tasks/{task_id}/retry` exposes the
existing `EmailClassificationTaskAdapter.retry_failed_tasks` operation. It
requires a current failed classification task with no saved result. It returns
404 for an unknown or non-classification task, and 409 for a task that is not
failed, has a non-null result, or loses the failed-state compare-and-set. After
requeue, it reads the task back and confirms the stable identity and exact input
are unchanged. The ordinary worker still performs the next mailbox read and
classification under the existing bounded automatic retry budget.

The endpoint cannot retry pending/running/done work or create a provider action
directly. Existing unsubscribe guards and delivery flows remain unchanged.

## Verification

Before implementation, both endpoint regressions failed with HTTP 404. The
dedicated test file covers a successful requeue with preserved identity/input,
rejection of a nonfailed task, and rejection of a failed task that already has
a saved result. All three pass in 31.99 seconds. The related existing Email Web
API file passed 184 tests in 153.34 seconds. Ruff and `git diff --check` pass.

## Deployment and production readback

Pending the standard gated `python -m app.deploy` after the fix is committed and
pushed. After deployment, use the endpoint once for task `417571`, then verify
the same task's latest state and history, its provider readback/classification
result, health, Attention and queue counts. Do not replay unsubscribe or treat a
pending state as success.
