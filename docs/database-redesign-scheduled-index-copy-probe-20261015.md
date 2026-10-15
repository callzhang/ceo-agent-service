# Scheduled-run index copy experiment, 2026-10-15

This extends the hypothesis in
`database-redesign-follow-up-evidence-20261015.md`. It does not change the
selected schema, production database, retention policy or published code.
All six variants were exercised on a newly owned temporary SQLite backup of
the same frozen input (SHA-256
`970a909a9cfcb4fe604d2111d5572560d4744978df20fb317c4e433059566320`).
The original was opened read-only. Its hash remained unchanged, and the new
temporary directory was removed. No historical rows were retired.

## Result and scope

Each variant returned identical ordered results to the baseline for fourteen
SELECT probes. Eleven reproduce current query branches: four dispatcher
metrics, Store claim candidate selection, dispatcher page and keyset selection,
per-task latest/history cursor, overlap and failure streak. Three additional
diagnostics exercise a wide failed filter, status aggregation and terminal
time range; these are not full public History API acceptance.

Five source-valid manual trigger rows exercise tied times, active and expired
leases, and future work. An earlier fixture attempted duplicate scheduled
instants and correctly hit the native uniqueness constraint; the fixture was
corrected to manual triggers without removing that constraint.

| Variant | Two/three tested indexes, bytes | Finding |
| --- | ---: | --- |
| Original two full indexes | 18,493,440 | Baseline |
| Both pending-only, omit leading status | 8,192 | Unbounded pending metric scans the full per-task latest index; reject this form |
| Pending-only plus full status/ID index | 3,854,336 | Restores status coverage but loses scheduled-time range coverage |
| Pending-only, retain leading status | 8,192 | Pending metric uses the partial index; terminal diagnostics lose coverage |
| Leading-status pending indexes plus full status/ID | 3,854,336 | Terminal time-range diagnostic still loses coverage |
| Partial dispatch, retain full claim | 8,302,592 | Preserves the full claim covering path and partial pending dispatch path; next candidate for public-path verification |

The conservative last candidate keeps the leading `dispatch_status` key on
the pending dispatch index and leaves the full claim key definition intact.
The pending metric retained an indexed search; terminal range and status
aggregation retained the full claim covering index. The per-task latest index
was never changed, preserving its latest/history/overlap/failure-streak use.
Exact SQL, plans, result hashes, counts and three timing samples summarized
as medians/minima are recorded in
`superpowers/specs/2026-10-15-scheduled-index-copy-probe.json`.

The measured active index-page difference is **10,190,848 bytes**, consisting
of **9,416,704 bytes** from full dispatch to partial dispatch and **774,144
bytes** from rebuilding the unchanged full claim definition. Do not attribute
the latter to the partial predicate. This is not recovered production file
space and does not predict a final VACUUM result.

Three repeated SELECT timings on a shared host are diagnostic only. Large
baseline timing variation between the first and extended experiment prevents
a credible speedup claim. Query plans and exact result identities establish
only the bounded observations above.

## Required before implementation or selection

Exercise the actual public claim, link, release/completion and recovery writes,
full History pagination and dispatcher paths against source and candidate
copies. Preserve original facts, tie ordering, leases, native constraints and
all current-fact inverse/forward behavior. Verify the migration and rollback
against genuine old code, then add focused regression coverage and the
behavior documentation in the same implementation commit. No SELECT-only
result here substitutes for those checks or authorizes production selection.

This index experiment is independent of completing the ten-core-table plus
one-ledger physical schema. It neither waives any remaining table merge nor
introduces a history-retention policy.
