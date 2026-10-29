# Current12 actual-copy capacity, R3

Derived only from the completed, immutable actual-copy receipt
`superpowers/specs/2026-10-17-email-canonical-current-copy.json`, SHA256
`1d9d2edb43e9d8470bbbdbd37177678c2f72e01a403cfd228223092825493b09`.
No additional production or temporary database was opened for this analysis.
This is a candidate-copy measurement, not a claim about current production size.

The source online backup allocates 503,771,136 bytes
(480.43 MiB), active
503,767,040 bytes. The first target, before synthetic test writes,
allocates 564,404,224 bytes (538.26 MiB),
active 554,467,328 bytes (528.78 MiB).
Allocation increased 12.04%;
active storage increased 10.06%.
The full 116->12->116->12 data/schema proof passed; capacity is NOT accepted.

First target allocations, including each table's indexes:

| Owner | Table and indexes MiB | Index subset MiB | Unused bytes within allocated pages MiB |
|---|---:|---:|---:|
| actions | 202.36 | 32.55 | 33.61 |
| runs | 130.10 | 49.74 | 9.56 |
| tasks | 68.73 | 10.38 | 7.10 |
| emails | 64.14 | 4.21 | 8.17 |
| provider_state | 25.89 | 12.19 | 1.82 |
| business | 11.21 | 1.23 | 1.44 |
| state | 8.84 | 2.58 | 0.59 |
| configs | 8.41 | 1.35 | 0.61 |
| business_source_documents | 7.02 | 0.02 | 0.13 |
| sqlite_schema | 2.04 | 0.00 | 0.03 |
| schedules | 0.02 | 0.01 | 0.01 |
| business_legacy_links | 0.02 | 0.02 | 0.02 |
| sqlite_sequence | 0.00 | 0.00 | 0.00 |
| email_schema_migrations | 0.00 | 0.00 | 0.00 |

The first target has 2426 free pages
(9.48 MiB). These differ from unused bytes
inside occupied pages; do not sum the latter as immediately reclaimable freelist.
No VACUUM or index removal was performed. The final target after synthetic current
and predecessor writes allocates 564,404,224 bytes, active 554,733,568 bytes.
Use the first-target numbers for the same-input comparison.

The new Actions container absorbs formerly separate source families. Comparing
its growth alone with the old Actions table would not establish duplication.
The total active-byte increase is real and needs field/payload/index analysis.
The current implementation uses source-shaped JSON payloads and generated typed
projections for several containers; this is a source-level lead, not a measured
attribution of the entire increase. No compression, new retention policy or
index cancellation is selected by this report.

Next existing approved work is direct WorkTodo->Task ownership (retiring the
empty LegacyLinks container), followed by source-reader/body-free provenance
closure. LegacyLinks currently uses only 20,480 bytes with its indexes, so its
removal will meet a structural goal but will not resolve the capacity increase.
SourceDocuments accounts for 7.02 MiB; even complete retirement of its current
body storage alone cannot explain or eliminate the increase. Keep business
results, necessary state and execution receipts while diagnosing remaining
payload and index costs. The approved 10-core-plus-ledger design, exact migration
and rollback, capacity, real CLI/fake sends, UI and release gates remain open.
