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
