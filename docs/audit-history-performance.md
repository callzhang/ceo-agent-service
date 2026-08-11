# Audit History Performance

The audit history combines reply, approval, meeting, and task records. Its
reply branch performs correlated lookups for the latest approval attempt and
for later delivery evidence.

`AutoReplyStore` maintains three supporting indexes after schema migrations:

- approval instance and recency for the latest approval attempt;
- conversation, trigger, action, and attempt id for later-attempt checks;
- channel, conversation, trigger, and recency for current-trigger status checks;
- conversation, trigger, and send time for sent-reply checks.

The approval lookup explicitly excludes an empty approval instance so SQLite
can use its partial index. Keep the query-plan regression test when changing
the history query.

The unfiltered history page also keeps one in-process HTML snapshot for two
seconds. This prevents concurrent browser navigations from repeatedly running
the same aggregate query while workers are writing audit records. Search,
filters, pagination, and every action route bypass the snapshot and query the
current database state.

## Current Failure State

The Worker page reports reply attempts by their current trigger state rather
than by every retry record. A failed attempt is shown as recovered once its
reply task is done or a delivery record exists, and as retryable while its
reply task is pending or processing. Historical retries remain available in
the audit trail, but they must not inflate the live failed count or attention
list. A failed state remains visible only when its latest trigger has neither
completion evidence nor active recovery.

WeChat delivery rejection is a completed user decision and is shown as skipped.
An unconfirmed send remains failed until read-only reconciliation can prove the
delivery outcome.

When a historical failed or reconciliation attempt has a later terminal result,
the detail page shows the later result summary inline. Users do not need to open
an internal attempt chain to learn whether the business action completed.
