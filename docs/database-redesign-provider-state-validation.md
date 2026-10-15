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

## Unselected pure migration, 2026-10-15

The preceding paragraph records the next task at the storage acceptance
boundary; this appendix records that task's local implementation.

`app/provider_state_schema_release.py` now exposes
`capture_provider_state_source(db)`, `apply_provider_state_migration(db, metadata)`
and `rollback_provider_state_migration(db, metadata)`. Capture returns only
`{"variant": "actual"|"fresh", "tables": {source_name: declaration}}`;
the declarations contain literal table SQL, columns, indexes, foreign keys and
triggers. They contain no source rows, body values, rowid catalog, baseline
snapshot, allocator, counts or persisted ledger. The two source declaration
fingerprints and one exact target fingerprint bind the finite shapes. The
functions do not choose a Store or Email version or begin, commit or roll back
the caller's transaction.

The forward copy reads all observations and folder generations by named fields,
including rows without classifications and old or excluded observations. It
encodes the four payload fields through the accepted tagged SQLite codec. For
each record kind, symmetric source-shaped SQL comparisons include every field
and `typeof`, plus row counts, before the original two tables are dropped.
Every data query and drop names `main`; preflight rejects case-insensitive TEMP
shadows, replacement-name collisions, nonmatching source or target declarations,
inbound FKs and unsupported external SQL dependencies. The inverse reconstructs
the exact captured source table and explicit index declarations, copies the
**current** target facts, verifies both families and only then drops
`provider_state`. Source hidden ROWIDs are not preserved or treated as public
identity; source composite keys are. Reforward from the reconstructed source is
supported.

Test-first record: the initial focused import test failed at collection with
`ModuleNotFoundError: app.provider_state_schema_release`; after implementation,
the empty actual/fresh pair passed. The final focused command
`pytest -q tests/test_provider_state_schema_release.py tests/test_provider_state_source.py tests/test_provider_state_storage.py`
passed **34** tests. Literal actual and fresh source databases independently
exercise the swapped signal/time ordinals, all 12 named fields and storage
classes, BLOB optional fields, numeric strings, an infinity input normalized
to text by native source affinity, JSON scalar `42`, account and
case-sensitive identities, generations, empty tables, post-forward inserts,
updates and deletes, source writes after inverse and reforward. Negative cases
cover missing or tampered metadata, unknown source/target indexes, partial or
already-target state, TEMP shadows, external dependencies, and mid-copy failure
with caller rollback. An unrelated AUTOINCREMENT high-water mark and a
preexisting unrelated FK finding remain equal after the round trip. Ruff for
the two new Python files, compilation and diff check passed.

This is an unselected, synthetic SQLite proof. No current Store caller,
constructor, version marker, release coordinator, real database, provider call,
native Agent run, push, main merge or deployment was changed or exercised.
Copied production data, archived old-code writes, paired Store/Email release
and space savings remain separate evidence gates.


## Root acceptance of pure migration, 2026-10-15

Sequential fresh SPEC then QUALITY accepteda2d10fac. Each inspected the actual
module, tests, literal source declarations and accepted target; each passed
34 focused source/storage/migration cases. Additional independent native
probes covered legal BLOB keys/values, JSON BLOB/scalar values, numeric-string
and nonfinite/negative-zero affinity, and external MAIN trigger/TEMP view
dependency rejection on both source variants. No actionable defect remained.

The finite capture/apply/inverse stores schema metadata only, copies all named
fields and verifies values plus storage classes and counts before dropping a
source or target. The caller owns transaction control. Current-fact inverse
preserves post-forward inserts, updates and deletes. No source hidden-ROWID
catalog, public allocator, old-table view or row snapshot was introduced.

This acceptance is synthetic and unselected. Current callers, fresh schema
factory, exact schema validators and paired release wiring are next; actual
source-copy, genuine old-code cold writes/reforward and publication remain
unverified for this Provider unit. Full10+1 is still incomplete.


## Read-only actual-source preflight, 2026-10-15

Root executed the accepted pure capture/dependency preflight on the immutable
15:35UTC source image, opened mode=ro with query_only and one read transaction.
It accepted the actual variant with72142 observations and55 generations,
Store`2026-10-13.5`/Email51. Production checkout observations before and after
remained7dadc492; these and the older source capture are distinct observations.
Metadata-only proof is in
`superpowers/specs/2026-10-15-provider-state-source-preflight.json`.
This confirms source declaration/dependency compatibility only; no forward,
inverse, real-copy target comparison, cold old-code or current caller proof
was executed by this read-only preflight.
