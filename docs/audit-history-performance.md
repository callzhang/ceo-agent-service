# Audit History Performance

The audit history combines reply, approval, meeting, and task records. Its
reply branch performs correlated lookups for the latest approval attempt and
for later delivery evidence.

`AutoReplyStore` maintains three supporting indexes after schema migrations:

- approval instance and recency for the latest approval attempt;
- conversation, trigger, action, and attempt id for later-attempt checks;
- conversation, trigger, and send time for sent-reply checks.

The approval lookup explicitly excludes an empty approval instance so SQLite
can use its partial index. Keep the query-plan regression test when changing
the history query.

The unfiltered history page also keeps one in-process HTML snapshot for two
seconds. This prevents concurrent browser navigations from repeatedly running
the same aggregate query while workers are writing audit records. Search,
filters, pagination, and every action route bypass the snapshot and query the
current database state.

## Event Chart Semantics

The 24-hour chart groups reply attempts by channel, conversation, and trigger,
and meeting runs by their stable meeting job id. It shows the latest state for
each grouped operation rather than every retry. An operation that failed and
later reached a completed delivery state is displayed as `Recovered`; only an
operation whose latest state is failed is red. The chart headline shows both
operation count and raw reply-attempt count so retry volume remains auditable.
