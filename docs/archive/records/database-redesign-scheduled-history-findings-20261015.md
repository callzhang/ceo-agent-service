# Scheduled history: bounded read-only findings, 2026-10-15

Inspected candidate cfc71b52276d387ec0195117ab46ba6caed7c9d9 and immutable
source f264d32e4aff13d2e7e0a91424d8ab2422857948. Source observations are
historical aggregates, not current live counts. No retention change or deletion.

167930 scheduled rows comprise151505 dispatched service commands,69 Agent
dispatches,15350 skips and1006 failures. For service commands, execution_id
is the command name, not an Agent session/trajectory ID (consumer.py:105-159).
Routine successes persist separately; frontend loaded-page idle coalescing
does not coalesce database rows (ScheduledTasksPage.tsx:262-289). Existing
DB coalescing is only consecutive overlap skips, using first/last/count
(store.py:7757-7801). Global History hides routine successes but per-task
History retains them (history_types.py:129-182).

A source-only classification identifies83610 exact known zero-work summaries;
this is not a safe deletion count or a byte estimate. Compacted reply input
provenance alone references1379 distinct scheduled runs through1679 input
rows. Two referenced runs have queued=0 (calendar and OA). Other source
shapes, Email inputs and business references are additional closure inputs.

The9828 scheduled Dispatcher claim rows include9795 terminal and33 nonterminal
with owners, despite zero pending scheduled source rows in the source aggregate.
That mismatch needs separate reconciliation; no claim retirement is inferred.

Dependencies to preserve: public/event IDs and scheduling uniqueness, active
claim/execution/overlap state, direct JSON source references, prior delivered
daily-report date, failure-streak success boundaries and later-success incident
recovery, frozen snapshot identity. Existing scheduled_tasks has no replacement
report/success watermark; do not invent one as though it already exists.

Future optional cohort: unreferenced completed routine scheduled checks with
no unique business result and no necessary state role. Full reference closure,
current-fact inverse and explicit history/identity semantics must precede any
retirement. First/last/count coalescing would change per-occurrence identities
and deduplication/history behavior; no 30-day or other age cutoff is approved.

This research does not replace the remaining Email copy/native/CI/publication
gates or complete the ten-core-table-plus-ledger design.
