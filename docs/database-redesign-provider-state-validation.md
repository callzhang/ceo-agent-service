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

## Current caller and paired candidate integration, 2026-10-15

This worktree selects the accepted `provider_state` declaration in the same
**unpublished** Store `2026-10-14.1`/Email52 candidate that already selects
Email13 and Business4. It does not add a provider-only marker or intermediate
release. Fresh Email creates the one provider table and its three partial
indexes inside Email's existing transaction. The paired coordinator captures
the frozen actual/fresh provider source declarations together with Email and
Business, converts all three before updating either marker, verifies the exact
target declaration, and uses current target facts in the inverse. The source
Store `2026-10-13.5`/Email51 pair remains the sole predecessor.

Current provider scan publication preserves source input validation, role and
binding classifications, signal JSON, and the original observation →
authoritative absence → inactive accounts → unavailable folders → generation
order. Connection-local membership and optional active-account sets are staged
before the same immediate writer transaction. The accepted storage routines
perform set-based reconciliation; `None` and an empty active-account list retain
their distinct meanings. Signal updates open an explicit caller transaction.
Classification reads use `record_type` predicates on both observation and
generation aliases and retain the existing outcome projection and generation
time overlay. Current shape validation uses the pure migration's exact target
fingerprint, including generated columns, checks, partial-index keys/predicates,
triggers and foreign keys, and refuses either old source name as a table or
view. A matched current pair with malformed provider metadata is refused by
the read-only selector before constructor cache, WAL or repair. The synthetic
matched-current damage matrix covers a missing folder partial index, column
affinity and kind CHECK changes, and mixed old table/view names; both public
constructors are tested against each with cache and initializer sentinels,
DELETE journal, database fingerprint/bytes and `-wal` absence.

Synthetic validation so far: source/storage/pure/current provider and current
Email learning/schema/seeds/index files passed **65** cases in 15.92s. Selected
provider/fresh/current cases across Email callers, action validation and store
passed **43** cases in 23.20s. A paired coordinator subset covering provider
source/target mutation refusal, actual/fresh source declarations, post-forward
provider writes, inverse, old-source write and reforward, and injected failures
just after provider forward/inverse passed **38** cases in 101.78s. The
application source test keeps the genuine published `7dadc492` AST comparison
for the unchanged projection only; four SQL callers were intentionally retired
from byte-identical AST comparison and retain behavioral tests. The generation
rollback injection fires only after the observation has changed to `finance`,
proving the later generation write caused transaction rollback. These results
are local synthetic SQLite evidence, not actual-copy acceptance.

The full paired coordinator and caller files passed **132** cases in 569.65s;
the expanded matched-current constructor damage matrix separately passed five
cases after that long run started. Ruff, Python compilation and `git diff
--check` passed for the edited Python files and worktree diff. Fresh independent SPEC
then QUALITY reviews, actual immutable source-copy conversion, genuine old-code
cold writes/reforward, native/provider doubles, CI and any publication are
separate gates. No production database, provider network, outbound action,
main branch, push or deployment was touched by this integration.

## Root current-integration acceptance, 2026-10-15

Fresh sequential independent SPEC then QUALITY accepted the exact current
integration rangeb92ed762..b13a01d0. SPEC checked the actual implementation and
passed14 focused schema/signal-rollback/constructor cases in2.72s. QUALITY
checked all six changed application modules and passed13 focused cases with
six deselected in2.76s. Root independently passed33 source/current/pure-migration
cases in2.37s. No concrete deviation or regression remained in these reviews;
neither reran the previously recorded132-case pair suite or a full local suite.

This accepts the coherent unpublished current path only. The combined
Business/Email ancestor CI38769762524 at ac60db6d subsequently reported28 failures
and nine errors (14361 passing); its fixtures and portability issues are being
repaired in the separate Business worktree before reconciliation. That result
does not cover the local Provider integration. Actual immutable121-to104
conversion, current-fact inverse, genuine published7dad cold Provider writes
and reforward, real CLI/provider doubles, replacement CI and final production
approval remain separate outstanding gates. No production migration, main
merge, push, restart, deploy or real outbound test action occurred here.

## Copied-database Provider harness unit, 2026-10-15

The unpublished paired proof now carries Provider's two frozen source tables
and exact `provider_state` target beside Email13 and Business4. It compares all
nine observation and three generation fields by named source order, SQLite
`typeof`, binary composite identity ordering, exact actual/fresh source
declarations and the accepted target predicate. It retains original Provider
rows through synthetic public scan, move, authoritative missing, direct signal
and generation writes; inverse checks the source DDL and current facts. A cold
subprocess then runs archived `7dadc492` public Provider scan, flag and read
methods against the restored source, with observation and generation timestamps
checked before and after the direct flag write. Reforward compares the resulting
facts. Receipt summaries contain derived counts and digests, not Provider rows.

The paired ordinary-table comparison excludes exactly the two Provider source
tables and their one target after moving them to the separate image proof. The
three independent SQL-boundary cases remain Email-only. The native lifecycle
fixture now requires the exact Provider target and absence of both old names
before its ordinary-table baseline. Its real CLI cases were not invoked here.
The synthetic paired fixture passed after these changes; a regression test
mutating an original Provider row fails the image proof. Direct actual/fresh
image cases passed six tests, the paired success and negative cases passed two,
the native baseline and current Email public case passed two, and seven focused
report/clean-guard assertions passed. Ruff and `git diff --check` passed.

This unit does not establish the actual 121-to-104 copied-database result,
whole-source Provider row counts, genuine old-code writes on the actual source,
native CLI/provider-double behavior, replacement CI, publication or live
readback. No actual database, real Provider, network, production checkout,
push, PR, main branch or deployment was touched.

### SPEC follow-up on the synthetic Provider proof

A later public scan repeats the same Archive observation with unchanged
source-shaped values. The harness compares all nine values and their SQLite
types before and after that scan: its `observed_at` remains at the prior
observation time while the Archive folder generation advances to the new scan
time. The subsequent direct flag write still leaves both times unchanged.
Direct actual and fresh DDL cases now include whitespace-bearing observation
identity, folder name, category, JSON text and timestamp, plus a matching
folder generation; the full image comparison carries those exact values and
types through forward, current-fact inverse and reforward. Six direct and two
paired focused tests passed after this follow-up. Actual copied-source and
native CLI acceptance remain pending.

### QUALITY follow-up on proof attribution

The copied-database report now includes the literal Provider source fixture
`tests/test_provider_state_source.py` in its clean Git guard and SHA-256 proof
manifest. Before the change, focused regressions showed a 20-entry manifest
and accepted a dirty fixture. After adding the exact path, the report contains
21 proof hashes and rejects that dirty fixture; all nine focused report and
guard cases pass. This changes only proof attribution, not Provider data or
migration behavior.


### Independent copied-source harness acceptance

Fresh SPEC accepted both synthetic proof follow-ups at4d3f6927 (six direct
cases passed in0.64s). QUALITY independently ran68 focused cases in138.57s
and found one proof-attribution gap: the literal Provider fixture imported by
the proof tests was absent from the manifest. Commitfbbb22a2 adds exactly that
fixture path to the existing clean Git guard and SHA256 list. The regressions
were RED before the fix (manifest20 rather than21 entries and dirty fixture
accepted), then nine report/dirty-path cases passed. Fresh SPEC reran nine in
0.24s; fresh QUALITY reran nine in0.17s and accepted the narrow fix. This is
harness acceptance, not the actual-copy result. Root has started the committed
actual four-case source-copy proof atfd3ba699 against the frozen970a909a input;
it remains in progress at this recording. The initial direct-script command
omitted PYTHONPATH and stopped at import before any database operation; the
corrected invocation uses PYTHONPATH=.
