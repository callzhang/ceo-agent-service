# Phase5E source refresh and execution boundary

## Task 3B1 paired release unit acceptance

Independent SPEC review found that rollback read its marker before taking the
writer lock. A deterministic second-connection regression reproduced the
interleaving: the old code overwrote a concurrently changed, unknown marker.
Rollback now begins the FK-off writer transaction before reading or acting on
the marker, including the unchanged-source branch. The regression confirms it
refuses the inverse and preserves the concurrent marker and target facts.
Loaded readback now begins one read transaction so its marker, target objects,
Email version and integrity observations share a snapshot. After this repair,
the new release suite and six migration/preceding-release files passed together:
132 tests, with Ruff and diff checks clean. Independent SPEC and QUALITY then
accepted `529a3479`. QUALITY ran 119 release/migration/preflight/prior-release
tests and verified that unchanged-source rollback writes no rows. Its separate
WAL interleaving probe confirmed coherent loaded readback and rejection of the
changed database on the next read.

The unpublished Store18→Store19 release unit captures the exact validated
published Attempt/Memory source declarations and the original marker value and
raw timestamp in a read transaction. Its injected publisher must produce the
finite sixteen-type target. Loaded verification checks the selected actual or
fresh target declaration, unchanged FK diagnostics and SQLite quick check. On
rollback, the existing current-fact inverse recreates the original declarations
and marker in one transaction; an unchanged original marker is accepted only
after validating the original source declarations and integrity again. The
future-version factory route is literal and inactive while `app/store.py` remains
Store18. The candidate release target is `2026-10-13.5`, but the real Store
initializer still declares `2026-10-13.4`. The release state contains only four inverse metadata fields; it has
no historical row snapshot or stale backup restore path.

Focused unit checks passed for both source variants and tuple/Row connections,
prior FK diagnostics, raw marker timestamp restoration, current results and
Memory receipts, rebind and delete, repeat inverse/reforward, changed source and
target refusal, atomic inverse failure, a publisher failure after target commit,
and dependency/verification/restart/health/loaded deploy failure stages. These
are injected-publisher tests. Actual Store19 constructor publication, converted
writer/readers and their publication selectors, an old-code copy and final
activation remain part of later integration. This checkpoint did not alter
production or call a provider.

## Source primary-key ordered access before Store conversion

Root measured latest-public-ID access in isolated synthetic data. The source
query used 27 VM steps for both 20 and 1,020 records; the target query grew
from 309 to 13,301 steps and sorted the type's whole set. Adding only a
cast-public-ID partial index did not change the chosen plan. A scoped
`(record_type, cast(legacy_key as integer))` partial index selected a covering
ordered plan and reduced the 1,020-row query to 83 steps. This source primary-key
access regression was the reason for the confined target-index correction and
growth regression below. No production index was changed.

The opt-in sixteen-type action definition now adds one partial
`(record_type, cast(legacy_key as integer))` ordering index for each reply
type. Against both captured source variants, the target projection returns the
same ascending, descending, bounded-range and full rows at offsets zero and
three as the source tables. It explicitly reads a deleted-then-reused ID,
checks negative and high public IDs, proves Attempt and Memory can both use
public ID one, and adds unrelated action rows. The focused regression
checks SQLite plans for ordered index use without a temporary sort and counts
VM work at 20 and 1,020 records: every five-row target query remains within
100 VM steps of its small-table cost. Full-row projection has a fixed JSON
extraction cost; the growth comparison, rather than absolute parity with the
source's direct table read, is the relevant ordered-access bound. These
indexes affect only opt-in E schema creation; existing frozen layouts and
production remain unchanged.

## Task 3A independent and online-copy acceptance

The opt-in forward/current-fact inverse at `4f98d82e` passed independent SPEC
and QUALITY review. Seventeen migration checks passed, including source and
post-forward observer rebinds to a later physical parent in both source modes.
The complete E gate passed 453 tests with eight variant-specific skips; six
preceding migration/release files passed 125 tests. Independent probes also
confirmed exact source/target objects and active reference guards after both
directions. Task 3 remains unfinished: this unit does not set a release marker,
activate Store, convert callers or implement paired publication.

The root then made a latest online SQLite backup in an owned temporary directory
and exercised the standalone migration on real current data. The exact source
contained 14,823 Attempts, 476 Memory events and 6,607 existing action facts.
Forward, synthetic copy-only new results/receipts and parent rebind, deletion,
current-fact inverse, and a new-process migration-module re-forward passed.
All 120 unrelated tables retained their value/type hashes, retained action
physical values/types were identical, current public allocator waters survived,
and the one original FK diagnostic was unchanged. Full integrity checks passed
before forward, after inverse and after re-forward. The inverse retained two
new synthetic Attempts and one synthetic Memory receipt, without resurrecting
the deleted Attempt. No provider was called and no production row was changed.

The 64.4-second probe cleaned its temporary database afterward. Its file remained
471,347,200 bytes: this acceptance proves facts and schema transitions, not
physical reclamation. Only concise metadata is retained in
[`phase5e_storage_copy.v1.json`](../evals/database_redesign/phase5e_storage_copy.v1.json).
The synthetic results are not verified external business outcomes. The subprocess
used the migration module; actual old-code and candidate Store constructors,
paired publication failures, native substituted execution, final CI and normal
deployment remain required.

After this probe production was observed at `492259b5`, from a separate audit
feedback-context release. Its storage files and the source declarations are
unchanged from the earlier Store18 binding. The database copy was bound by exact
schema fingerprints; it was not an atomic checkout-and-database capture. Latest
main must be integrated before paired release and caller work.
Root subsequently merged main `1dc841ed` as `549602eb`, retaining the audit
revision context and null DWS response fixes. Only claim-ledger insertions
conflicted; all rows were preserved. The migration/preflight and both newly
changed feature suites passed together: 327 tests. The accepted E storage
and migration implementation files were unchanged by this merge.

## Task 2 opt-in finite storage checkpoint

The isolated E candidate adds two opt-in `actions` record types with separate
canonical declarations for `actual_no_memory_fk` and `fresh_memory_fk`. It has
no Store version, bootstrap choice, migration, caller activation or production
effect. Public Attempt and Memory IDs remain distinct integer namespaces; the
physical `actions.id` remains internal. Actual Memory keeps its scalar
association and four historical Attempt fields; fresh Memory has an immediate
typed parent FK and omits those four fields. Attempt `agent_run_id` remains a
scalar and does not acquire the shared run FK. Only exact queued `audit_review`
markers survive in Attempt event arrays; Memory copied payloads project as
`{}`. All other source fields use the shared finite `payload_json` with SQLite
typed generated projections; a confined storage-class tag preserves legal BLOB
and nonfinite values without parsing raw inner receipt strings. The opt-in
choice leaves the older six/nine/twelve/fourteen-type layouts unchanged.

The fresh Memory parent alone uses a physical typed FK. Index and constraint
fields are generated from the shared type payload with their original affinities;
the actual Memory association stays in that payload. The E payload decoder
currently uses SQLite `unhex` for BLOB projections. It passed on the local
SQLite 3.53.4 runtime; CI/runtime support must be checked before E publication.
This is a target storage checkpoint, not accepted migration or caller behavior.
No source row or provider action was changed here.

The initial local focused gate ran the frozen reply source oracle, new E target
comparisons, and the existing action, receipt, postfix and delivery storage
suites: 295 passed, five variant-specific skips. Its initial RED run failed
because the opt-in schema flag was absent. Independent SPEC review of commit
`1fa4e4b1` then found gaps in ignored fresh Memory constraints, strict payload
keys/tags, BLOB JSON audit markers, and native integer-key assignment. The
repair added source-versus-target RED cases before changing the mechanism:
allocation now precedes fresh parent resolution; ignored public-key conflicts
and source NULL constraints preserve SQLite outcomes; both E payloads reject
unknown keys and malformed encoding tags; direct event arrays accept only
queued audit markers; and a confined in-memory SQLite primary key applies
native key affinity. The second SPEC pass found two further source differences:
an ignored explicit duplicate must advance its type's allocator after a legal
public-ID update, and a nullable payload field's malformed structural object
must fail its finite CHECK conclusively. Source-versus-target regressions now
cover updated IDs of 900 and `MAX_INT` for both types and both variants,
including deletion and the following automatic insert; raw payload regressions
cover missing tags on nullable fields. The second repair advances the ignored
insert's sequence under the existing writer transaction and uses a definite
SQLite CASE result for every finite payload value. The focused gate passed
384 tests with seven variant-specific skips; Ruff and `git diff --check`
passed. Independent SPEC/QUALITY re-review, migration,
publication and production readback remain separate gates.

The subsequent independent QUALITY review found two write-ordering differences.
Source `UPDATE` acquires a writer transaction and binds all inputs before
checking row constraints; a missing row or false predicate returns zero even
for an invalid public ID or unknown fresh Memory parent. That E update path
bound inputs, locked and checked the exact predicate before row-specific key or
parent resolution. Fresh Memory explicit insert likewise acquires its writer
transaction before resolving a typed parent, retaining native failure state
for deferred, autocommit, outer transaction and savepoint callers. Focused
source-versus-target regressions cover those modes and unbindable inputs; the
full local gate passed 397 tests with eight variant-specific skips, with Ruff
and `git diff --check` passing. This remains opt-in foundation evidence only.

A final QUALITY repair closed two update transaction gaps. On an ordinary
deferred connection, native SQLite opens the write transaction before failing
to bind an unbindable SET or WHERE value; the prior finite path bound first
and left no transaction. In autocommit mode, its no-row writer lock ended
before the predicate scan, allowing another writer to change the candidate.
Source-versus-target bad-binding regressions and a two-connection lock probe
failed before the repair. The intermediate finite update acquired the writer
lock before binding and kept it through predicate scan and UPDATE, with a savepoint owned
only for legacy/Python 3.12 autocommit connections. Ten new parameterized
cases pass across deferred, legacy autocommit, caller transaction/savepoint and Python 3.12
`autocommit=True`; the exact six-file reply source, reply target, action,
receipt, postfix and sent-delivery storage gate passed 407 tests with eight
variant-specific skips. The three corresponding schema-release files also
passed in a separate 326-pass, eight-skip gate that included the reply/source
and action storage files. Ruff and
`git diff --check` passed. This remains an isolated, unpublished storage unit.

The independent SPEC review then found the intermediate lock ordering still
differed from native SQLite. A failed SET/WHERE bind opens a deferred transaction
without acquiring the writer lock, so a second connection can begin a write;
the intermediate E update held that lock. E insert had the same gap for automatic
or explicit public IDs, and an explicit actual Attempt insert could return a
binding error without opening the native deferred transaction. Six update and
12 insert source-versus-target cases exposed those differences across deferred,
outer transaction, savepoint and autocommit scopes. The permanent repair binds
all original inputs on the same zero-row SQLite write statement that acquires
the writer lock. SQLite now handles implicit transaction creation, binding and
lock acquisition in its native order; the existing owned savepoint keeps a
successful autocommit update locked through its predicate scan and final write.
The seven-file storage and published-source preflight gate passed 436 tests
with eight variant-specific skips. No E migration or caller activation occurred.

## Independent storage acceptance and interrupted-work recovery

Task 1 at `a01d14e0` passed independent SPEC and QUALITY review. Its eleven
preflight cases still pass at the final Task 2 candidate. Task 2 at
`1f4f28583b4bb3c5c617fafc890e9ca80ac5477d` passed independent SPEC and QUALITY
review: the seven-file storage/source/preflight gate passed 436 tests with
eight variant-specific skips. Independent checks additionally covered 2,232
insert field/value cases, 1,612 update field/value cases, 80 native binding-error
and second-writer comparisons, and 40 valid-update scan-lock cases. The earlier
six/nine/twelve/fourteen-type manifests retain exactly 28/41/42/53 objects.
These are storage acceptance results; migration, caller conversion and release
acceptance have not occurred.

The application removed the old Phase5C checkout during startup cleanup, saving
its four changed files in Git snapshot `3c7534820bd878820942435e90e71127401ed968`.
The root verified its parent and diff, restored it by fast-forward on the same
E branch in the managed `database-redesign-phase5e-resume` worktree, and repaired
the remaining transaction defects there. No source work was reset or discarded.

Production independently advanced to `748519023c4e0d98d802820ed57b3d727a9a5e39`.
A new single read-only transaction confirmed Store `2026-10-13.4`, Email 51,
the same exact Attempt/Memory and fourteen-type action fingerprints, and the
actual-origin variant. At that observation there were 14,822 Attempts with
sequence 15,410 and 476 Memory events with sequence 1,426. Counts are observations,
never constants for migration. No private row was exported or production data
changed. The deployed interpreter's in-memory SQLite BLOB `unhex` probe also
passed; final publication CI and candidate deployment remain separate gates.

After fixed-head storage acceptance, root merged published main `74851902`
into E as `d895df19`, preserving its failed-reply recovery changes. The only
merge conflict was the claim ledger; both ownership records and the newer
completed Status claim were retained. The seven-file gate passed again at
the integrated head: 436 passed, eight skips. Eighteen relevant failed-reply,
OA completion and task-recovery tests also passed (902 unrelated cases were
deselected). The source/storage files reviewed at `1f4f2858` were unchanged
by that merge. This does not activate the E schema.

## Earlier source preparation details

The work below was source preparation for the approved Attempt/Memory action
consolidation before the isolated target storage checkpoint above. It did not
migrate rows, reserve a new Store version or deploy. The full
ten business/runtime tables plus one migration table goal remains incomplete.
The managed Phase5C checkout was clean and has been reused on the independent
`codex/database-redesign-phase5e-20261013` branch at D candidate5221062d.
That earlier refresh left D's release branch unchanged.

## Current declarations and dependencies

`2026-10-13-database-redesign-phase5e-source-refresh.json` records an earlier
readonly snapshot of published7fabea80 Store17 and an owned empty Store18
candidate constructor. The original frozen Store16/candidate17 inventory is
unchanged and is not relabelled. Both new declarations, column/default/PK
metadata, exact indexes, triggers and inbound/outgoing FKs equal their
corresponding frozen source variants.

Actual Attempt has58 fields and14821 rows; actual Memory has10 fields and476
rows. The empty candidate has54/10 fields. Actual Memory still has no parent
FK; fresh Memory retains its immediate Attempt FK. The four populated
historical Attempt fields remain source facts, not new DTO fields or inferred
relations. Counts are observations, not migration constants or retention rules.
Only schema metadata and aggregate counts/byte sizes are exported; no raw
business row, provider body, trajectory or credential is in the artifact.

All14821 current Attempt event arrays equal`[]`, occupying29642 bytes, while
Memory payload copies occupy601922 bytes. This snapshot does not make queued
review markers impossible: `record_reviewed_reply_rerun` still appends the exact
existing `audit_review`/`queued` marker. The approved migration must retain its
order and multiplicity while dropping copied native tool fragments.

The normal metadata writer still returns`[]` and transient original events feed
Memory receipt/error extraction before retirement. Static app call/import
inspection still finds no caller of`run_codex_memory_write`; its implementation
takes explicit data/type/time/source arguments and does not read a stored
Memory payload as retry input. WeChat imports typed Memory helpers, and meeting
code imports the exception; neither is a caller of that wrapper. The wrapper's
eligible completion semantics and the three existing native-range behaviors
remain requirements of the approved E plan; no replay is added here.

## Verified scope

Independent frozen source oracle:54 passed, two opposite-variant skips.
Existing Memory wrapper tests plus the main detail-page and bounded-zero-range
regressions:12 passed. The source declaration equality and current aggregate
checks passed against the actual readonly snapshot and empty constructor.
All source-only temporary databases, synthetic native fixtures and scripts were
removed after retaining the metadata artifact.

Independent readonly review of6e39cf31 reproduced every declaration/hash and
aggregate equality check, confirmed the static wrapper/import and marker
claims, and ran the combined source/dependency suite:66 passed, two opposite
variant skips. It found no blocking gap, removed its owned test directory and
made no production/provider/model call. This is source-preparation acceptance.

At that earlier point, D still needed publication before E implementation.
The Store17 observation and empty Store18 candidate did not establish a
published Store18 source. No E version was reserved, no source row was altered,
and no model, Memory provider, send, recall or deployment was invoked by that
refresh. The separate published binding below closes that source prerequisite.

## Task 1: published Store18 source binding

The separate [published source capture](superpowers/specs/2026-10-13-database-redesign-phase5e-published-source.json)
binds the immediately preceding production checkout `9e0570cb8d132f8e4b0394219b4c63411aa6610c`
to a single read-only SQLite transaction. The observed database marker is
Store `2026-10-13.4`, Email 51, with 124 tables. It contains 14,821 Attempt
rows (sequence 15,409), 476 Memory rows (sequence 1,426), and 6,589 existing
fourteen-type action rows. Counts and allocator waters are observations, not
migration constants. Only schema declarations, aggregate counts, sequences
and dependency names were committed; no private business or native row was
exported.

Both published source tables exactly match the frozen Store16 actual and later
Store17 actual declarations. The current Store18 fresh constructor matches
the frozen fresh Attempt/Memory variant. The published fourteen-type `actions`
declaration is bound by its own fixed SHA-256 and tested separately from any
future E additions; this preflight does not derive the source manifest from an
evolving target type list. The read-only preflight rejects unknown Store/Email
markers, mixed old/target allocator state, changed columns, added indexes or
triggers, changed actions, and external FK/view dependencies before schema
mutation. Existing source-owned object names are included in the exact
declaration comparison. No external declared dependency on the three source
tables was found in this capture.

The frozen oracle passed 54 cases with two variant-specific skips. Eleven
new preflight tests passed, including before/after byte, schema and allocator
equivalence for every rejected synthetic database. This is source binding and
refusal evidence only. It does not implement E target DDL, migrate a row,
reserve a Store version, change a runtime caller or complete the ten-table
redesign.

## Task 3A: opt-in transactional forward and current-fact inverse

The isolated E storage migration now accepts only the exact published Store18
fourteen-type action declaration and the actual or fresh Attempt/Memory source
declarations. The caller owns an FK-off transaction. Forward preflights names,
dependencies and declarations before touching schema, preserves all fourteen
action rows with their physical IDs, raw stored values, SQLite storage classes
and allocator waters, then copies Attempt/Memory through the accepted finite
writer. Public Attempt and Memory IDs keep independent namespaces; fresh
Memory uses its typed physical parent reference and actual Memory retains its
source scalar association. The two intentional retirements are queued audit
markers only, in order and with multiplicity, and a `{}` Memory payload
placeholder. Source table DDL, index/FK/trigger metadata and variant are the
only inverse state; no business row or retired body snapshot is retained there.

The inverse verifies the exact sixteen-type declaration and captured source
metadata, reads current facts after finite writes/updates/deletions, recreates
the original source DDL and current legacy rows, and transfers current public
ID allocator waters back to the original names. It clears the two finite
allocator namespace rows so another exact-source forward migration can run.
Value checks include SQLite `typeof` for every retained field and physical
action column. Forward remains bound to published Store18/Email51 markers;
inverse deliberately has no release marker policy because the paired release
wrapper must verify its eventual target marker and timestamp atomically.

This is a local, opt-in migration unit. It has no Store bootstrap or caller
conversion, no version reservation, no release wrapper, no old-code constructor
copy acceptance, and no production or provider operation. Task 3B must add the
paired publication/rollback wrapper and real caller gates before activation.
The seven existing storage/source/preflight files plus thirteen new forward and
inverse checks passed together: 449 passed, eight source-variant skips. The
new checks include a fresh-process reforward from committed restored source.
Focused prior delivery/postfix/receipt migration and inverse/release files
passed 376 checks with eight variant skips before the final three added E
cases; no full suite, production copy or old Store constructor was run here.

## Task 3A review correction: retained action copy order

An independent review found a valid retained state that the first migration
commit could not rebuild: an observer physically inserted between two sent
replies can later be rebound to the second reply. Copying raw rows in physical
ID order while the new schema's cross-row trigger is active rejects the
observer before its current parent has been copied. The same state can be
created after forward migration, so the inverse had the symmetric failure.

Both directions now create the exact declared action schema, defer its triggers
while copying the complete retained fourteen-type row set, restore current
allocator waters, and reinstall the exact triggers before migrating reply
facts or returning. This preserves physical IDs and raw references without
sorting by record type or treating observers as a special case. Both actual
and fresh source variants have a regression for a source rebind before forward
and a current rebind before inverse. The eight-file E gate passed 453 tests
with eight variant skips; the six focused earlier delivery/postfix/receipt
migration and release files passed 125 tests. Ruff and diff check passed.
No publication, caller, production-copy or old-code constructor gate was
completed by this correction.

## Task 4 candidate Store integration (unpublished)

The isolated branch now declares candidate Store19 `2026-10-13.5` and routes
the real Store initializer through the reviewed exact Store18 to Store19
Attempt/Memory migration inside an FK-off immediate transaction. It checks the
complete core and sixteen-type action manifest before updating the marker.
Fresh construction creates only the fresh Memory-FK variant. A current Store19
restart or missing-auxiliary-object repair first validates the whole actual or
fresh canonical manifest, then repairs that same variant. Unknown, mixed or
malformed shapes are rejected before core mutation. The old physical Attempt
and Memory tables, columns and indexes are removed from the current required
schema. This is a local candidate only; the production Store marker remains
18 and Email marker 51. No push, deployment or provider action occurred.

Store-internal Attempt SELECTs use one explicit 54-field public-ID projection
that is common to both canonical variants. Memory SELECTs use the validated
variant's projection; inserts, updates and deletes use the finite action
writers. Existing eligibility, leases, generations, row counts and update
times are retained under the ordinary writer transaction. Memory extraction
parses only the transient audit events, deletes all prior events for the
selected Attempt even when replacement input is malformed or empty, and
rebuilds typed events with `{}` payload projections. The retired raw tool body
is not serialized into stored Memory. A routed completion updates only the
same eligible event ID to written with the supplied episode and clears the
error; it cannot recreate a deleted event or increment attempts.

Focused local checks passed: 18 new Store integration cases; 10 routed Memory
completion cases; 64 selected Store Attempt/Memory, history, reconciliation and
compaction cases; and 16 current-schema reconciliation/history fixture cases.
The synthesized Store18 source tests use an exact published marker, Email51
metadata, captured old table DDL and old fourteen-type action declarations
constructed from a fresh temporary Store. They do not substitute for an
old-code cold constructor or a production-copy test. One older
`test_source13_workbench_lease_values_survive_before_recovery_index` fixture
advances only to Store17 before invoking the current Store19 constructor. Its
current-auxiliary-table copy also no longer supplies the retired Attempt/Memory
declarations, so it needs those exact frozen source declarations, Email51
metadata and the Store17 to Store18 advancement. The assertion was not
weakened here. The original
removed-universal-runtime repair regression passes unchanged: a current Store
with the two known universal tables still archives unreferenced history before
dropping them. External callers still reading physical Attempt/Memory
tables are assigned to Task 5, including the reviewed-human Attention path.

The preceding public-ID index unit at `0436c399` passed independent SPEC and
QUALITY review; its focused gate was 217 passed, six skipped, with Ruff and
diff checks clean. This Task 4 candidate is awaiting its own independent
reviews and the remaining Task 5/6 publication gates. Retiring these two
tables does not complete the larger ten-core-table-plus-ledger redesign.

## Task 4 independent acceptance at d7c62241

Independent SPEC and then QUALITY reviews accepted the exact Task4 commit
`d7c62241d89c98e62b36d00e10901e16f46d7758` against `0436c399`.
SPEC independently passed 39 integration/migration/completion and 81 selected
Store cases; QUALITY independently passed 35 target/completion/reviewed-human
and 36 selected Store cases. Both used owned temporary pytest directories,
cleaned them, and confirmed a clean worktree and diff check.

The implementer's complete Store-file gate was 436 passed and one historical
fixture failure. Its source13 fixture borrows unrelated current constructor
tables, which no longer include the retired Attempt/Memory source objects;
its explicit chain also stops at Store17. Task5 must reconstruct these old
objects from the independent frozen source, supply Email51 metadata and take
the exact Store17 to Store18 step without weakening the lease-value assertions.
The reviewed-human Attention failure is the known external `audit_web` old
table reader, also Task5 scope. Neither remaining failure is waived for final
integration or publication.

The original known-universal-runtime repair regression passed unchanged after
removing an overbroad startup exclusion discovered during implementation:
only mixed retired E tables are excluded, while the existing conversion of
unreferenced business history remains supported. No new source guard policy
or old-table fallback was added.

This accepts internal Store integration only. The real production-copy,
current-fact inverse, actual old-code cold constructor, current factory,
external callers, native substitute, CI and deployment gates remain outstanding.

After these reviews, merge `57942b54` integrated published main `9cdcddae`,
preserving the read-only original DingTalk mail tools and their release evidence.
Only the append-only claim ledger conflicted; both sets of rows were retained.
There was no Store, action storage or reply migration code increment in that
main delta. The combined current mail-source, new Store integration, Memory
completion and source-preflight gate passed 48 cases after the merge. Its owned
temporary directory was cleaned. This is an integration check, not a repeated
production copy or native business acceptance.

## Task 5 external caller and fixture candidate

The isolated Store19 candidate now routes external Attempt/Memory reads through
finite public-ID projections and mutates Attempt by the typed action writer.
History/Attention, quality coverage, scheduled recovery, rule-question
retirement, CLI channel discovery, email unsubscribe evidence and maintenance
no longer require the two retired physical tables. Logical source labels and
queue/status predicates remain the existing contracts. The main Attempt detail
reader no longer resurrects stored tool-event bodies when a native session is
unavailable; the queued `audit_review` business marker remains stored.

The deployment quiet-wait selectors recognize both complete Store19 action
manifests while retaining their exact older declarations. Candidate, receipt,
postfix, delivery, task, meeting and scheduled selectors reject an unknown
version or an extra action index without modifying the database. The generic
run selector already reads unified runs and required no new version case.

The source13 workbench fixture restores Attempt/Memory from frozen actual-source
declarations, then takes explicit Store17 to Store18 and Store18 to Store19
steps, with exact Email51 metadata for the latter preflight. The original lease
and recovery-index assertions pass. Historical Store17/18 publisher tests keep
their protocol/current-fact assertions and are labeled as injected historical
coverage; a separate current default-factory test proves a full Store18 to
Store19 publication, current-fact inverse and republish. Neither synthetic
fixture is described as an actual published old-code cold constructor.

Focused local evidence on this candidate: 68 quality and scan-cost checks;
164 reviewed-human/recovery/rule/email/scan-cost checks; 166 History, Attempt
detail, OA, WeChat and Attention checks; 338 audit web and native-maintenance
checks; 12 deployment-maintenance checks; 98 historical workbench/task/meeting
checks. These are local implementation gates. Independent SPEC/QUALITY review,
latest production-copy/current-fact proof, original-code cold constructor,
native role paths, complete CI, push, deployment and live readback remain open.

## Task 5 independent acceptance

Commit `8d4b1e6d` passed fresh sequential SPEC and QUALITY reviews. SPEC
independently ran 548 focused publication, quality, recovery, native-boundary,
page, History and historical fixture checks. QUALITY independently ran 398
caller checks and two native checks, and compared Memory queue snapshots and
email public-ID lookups before and after migration on both captured source
variants, including mixed-case statuses, timestamp ties and NUL receipts.
Neither review found a blocking issue. Root separately ran 439 Store/native
boundary checks, built the candidate frontend and passed both existing real
browser regressions with synthetic isolated data and a non-executing executor.
These counts overlap and are not a summed unique-test count. Every owned
temporary test directory was cleaned; all reviews checked the exact clean
commit. Task5 is accepted locally. Actual production-copy current-fact inverse,
separate-process original-code cold read and write, native substituted execution,
final CI, normal deployment and live readback remain required. Production still
declares Store18 / Email51, and the full ten-table-plus-ledger goal is unfinished.

## Task 6 offline paired-constructor copy acceptance

The isolated `scripts/validate_action_reply_copy.py` harness uses a SQLite
online backup of the latest production Store18/Email51 database and a separate
fresh Store18 database created by an archived, explicit production source ref.
Both run the real Store19 release constructor, current-fact inverse, a
separate-process old-code cold read and public Attempt/Memory write, then the
real Store19 release constructor again. It compares all retained rows and SQLite
types, public IDs, allocator waters, business queue status/order, source
declarations, the raw Store marker timestamp, and unchanged unrelated tables.
Synthetic bodies and receipts are copy-only; the old Memory result is parsed
locally from a fake tool result. The harness invokes no native CLI or provider.
All source copies and archived code are owned by one temporary directory and
cleaned after the run. Exact aggregate results and non-atomic production HEAD
binding are recorded in
`evals/database_redesign/phase5e_constructor_copy.v1.json`. The final run
passed on the actual source (14,830 Attempts, 476 Memory events, 123 to 121
noninternal tables, one unchanged source FK finding) and genuinely old fresh
source (zero initial reply facts, 123 to 121 tables, zero FK findings). The
actual-copy SQLite file stayed 471,347,200 bytes after republishing, so the
two-table reduction is logical rather than a measured file-size saving.
The separate focused release, public-ID and source checks passed 86 tests
with two fixture-dependent skips; Ruff and diff checks passed. This proves
offline storage compatibility only; native role, final CI, deployment and live
readback remain separate gates.
