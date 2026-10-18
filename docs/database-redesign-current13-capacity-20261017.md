# Current13 same-snapshot capacity, 2026-10-17

The13-table candidate passed the complete normal116→13→116→13 proof; it has
**not passed capacity acceptance and is not deployed**. This report uses only
that consistent-copy receipt, not a new measurement of the live source.

Source receipt SHA256 `6b8c310cf83694000ee671400f17136452452f0ae79edf4dbe39c0bd28a428fc`.
Source502,931,456 bytes; first target563,466,240 bytes; growth60,534,784 bytes
(12.04%). Final563,642,368 bytes includes synthetic current/old public writes and
is not the no-write comparison. Allthree have zero freelist pages. MB below means
1,000,000 bytes, not MiB. Per-table totals include their owned indexes.

| Owner | Data MB | Index MB | Other MB | Total MB |
|---|---:|---:|---:|---:|
| actions | 177.852 | 34.025 | 0.000 | 211.878 |
| runs | 83.976 | 51.937 | 0.000 | 135.913 |
| tasks | 61.157 | 10.883 | 0.000 | 72.040 |
| email_classifications | 37.335 | 3.408 | 0.000 | 40.743 |
| email_messages | 34.755 | 1.614 | 0.000 | 36.368 |
| provider_state | 14.365 | 12.784 | 0.000 | 27.148 |
| business | 10.469 | 1.290 | 0.000 | 11.760 |
| state | 6.558 | 2.707 | 0.000 | 9.265 |
| configs | 7.385 | 1.409 | 0.000 | 8.794 |
| business_source_documents | 7.340 | 0.025 | 0.000 | 7.365 |
| sqlite_schema | 0.000 | 0.000 | 2.138 | 2.138 |
| schedules | 0.016 | 0.008 | 0.000 | 0.025 |
| business_legacy_links | 0.004 | 0.016 | 0.000 | 0.020 |
| sqlite_sequence | 0.004 | 0.000 | 0.000 | 0.004 |
| email_schema_migrations | 0.004 | 0.000 | 0.000 | 0.004 |

The13 ordinary tables exclude SQLite's own sqlite_schema and sqlite_sequence;
the migration ledger is included. Data pages grew40,574,976 bytes, index pages
18,243,584 bytes, schema/internal pages1,716,224 bytes. This is current migration
representation overhead and remaining data, not proof of new business-event
growth over a time interval. A single snapshot cannot establish a daily rate.

SQLite dbstat also reports unused bytes *inside allocated pages*: source
66,182,664B versus target74,050,446B, a7,867,782B increase. This differs from the
freelist of whole unused pages. The remaining52,667,002B of net allocated growth
is occupied page content, headers and cells rather than this reported internal
slack difference. These counters do not by themselves separate JSON/key encoding
from row/index-cell overhead or prove exact VACUUM savings. Offline compaction
may repack partially occupied pages; measure it, do not assume74MB reclaimable.


Actions211.878MB, Runs135.913MB and Tasks72.040MB together own419.832MB
(74.51% of target). Classifications plus Messages own77.111MB. Consolidating the
two Email parents removes a table boundary, not automatically their live body,
model/training input, labels or action dependencies. SourceDocuments7.365MB still
has the separate verified-source/lifecycle retirement gate. The target currently
preserves those source bodies; the approved project memory-context cache is the
only explicit retirement exception in this full40-family proof.

## Next reductions, with evidence requirements

- Complete canonical Emails13→12 with exact existing fields/IDs/constraints;
  avoid repeated stored large-body projections and unnecessary JSON escaping.
- Complete SourceDocuments/LegacyLinks→inline/provenance12→11 only after exact
  sources and active reader/dependency lifecycle are verified. Do not substitute
  an identity/hash for an actually available original.
- Before final release, measure representation and indexes separately on the
  same snapshot. Indexes currently own120.107MB. Largest are provider identity
 10.334MB and scheduled-run dispatch9.953MB/claim9.626MB/public identity8.331MB/
 event8.217MB/scheduled-instant7.102MB. Existing correctness/query-plan reviews
 required several of these scopes; size alone is not authorization to remove
 uniqueness, lease/claim semantics, FK parent keys or useful access paths.
- Retire completed input/body copies only under the already approved provenance
  and live dependency rules; no unapproved30-day policy or replay behavior.
- Final capacity acceptance requires the resulting file and per-object pages
  after appropriate offline compaction, plus retained business/output/receipt,
  rollback, nativeCLI-with-fake-send and UI proofs. VACUUM cannot by itself
  explain away this growth: measured freelist is zero, and the additional live
  representation/index pages must be addressed explicitly.

Current13 proves whole source DDL/markers restoration,40 distinct retained
families/types/IDs/ROWIDs/waters and current+genuine-old public writes. It does
not prove all116 original rowsets, final11, source availability, production or
capacity acceptance. Original preexisting FK finding remains documented.
