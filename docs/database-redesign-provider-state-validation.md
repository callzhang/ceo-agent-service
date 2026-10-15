# Provider-state consolidation validation

## Accepted source and unselected storage, 2026-10-15

The accepted source contract is3b5c5939, independently reviewed in SPEC then
QUALITY with11 source cases. The exact single-table plan101c121e was reviewed
in the same order before implementation. No new business facts were added:
the12 original source fields map by name into real source-key/state/time
columns and a finite four-key tagged payload with source-affinity projections.
The target has two independent BINARY composite identities, three partial
indexes, and one internal INTEGER PRIMARY KEY. It adds no legacy-key copy,
public allocator, FK, trigger, or AUTOINCREMENT stream.

Sequential independent SPEC then QUALITY accepted implementation3afb1915.
Both reviewed the actual code and passed the20 focused source/storage cases;
SPEC also compared120 payload edge values/storage classes against both literal
source variants without a mismatch. Ruff, compilation and diff checks passed.
Test-only8ceaf4b5 closes the declaration-test connection; QUALITY rechecked its
unchanged assertions and passed the exact declaration case. No application
code changed after3afb1915.

The new helpers preserve changed-fact timestamps, folder moves, generation
overlay, partial versus authoritative membership, inactive-account semantics,
available-only direct signal updates and caller-owned group rollback. They
operate on owned synthetic SQLite databases in these tests. This evidence
does not cover a production copy, current Store caller conversion, paired
release, live provider response or native Agent execution for this unit.

Current Store and provider paths still use the original two physical tables.
The next approved separate unit is pure forward/current-fact inverse migration
for both actual and fresh declarations, followed by caller/release integration
and real-source copy/old-code read-write/reforward proof. No constructor,
version, migration coordinator or production state was changed. The broader
10-core-table-plus-ledger goal remains incomplete.
