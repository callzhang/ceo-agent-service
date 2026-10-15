# Email action storage preparation: Tasks 2A and 2B

## Task5B0 independent acceptance, 2026-10-14

Root accepts bounded correction `4a9de75282745d6abec01d6cf3d493456c9572a0` over `7dc87561536c55ebd1c6f1f9411f6029fe779eb3`. Fresh SPEC passed39 metadata cases in92.68s and10 retained-path cases in52.75s. Fresh QUALITY passed8 focused cases in7.53s plus independent malformed-SQL probes for each32 auxiliaries through warmed-cache fast Email and Store constructors. Facts, DB bytes and DELETE journal remained unchanged on refusal. MAIN ownership, TEMP shadows, unexpected full-manifest objects and preserved shared-index repair were independently checked. All final commands exited0; both reviewers verified unchanged clean HEAD and exact owned temporary-directory removal. These receipts are separate from the implementation batches.

The parent-F defect described below is resolved by this accepted local correction; original Email refusal and original Store/provider repairs remain distinct. Next5B1 is limited to six existing current scan/plan/feedback tests using existing finite projections while preserving their assertions. Historical-source and shared-rebuild fixtures remain separate. No broad gate, latest production-copy/native proof, CI, publication, or full10+1 completion is claimed.

## Task5A acceptance and required5B0 correction, 2026-10-14

Sequential independent SPEC then QUALITY accepted the external-consumer diff
7dcbe2278e615341effc10790d097ef4a22453dc over4957997d. SPEC ran13 focused cases
(14.40s) and independently compared complete OperationLog model dumps from real
0441 source through the real paired child, including detail/timestamps/pages.
QUALITY ran17 focused cases (15.48s) and independent same-Store initialization,
queue/maintenance/quiet, strict-default/current-explicit variant, receipt fence/
settlement/recovery and full genuine-source History probes. All final runs exited0;
both reviewers verified unchanged HEAD/clean status and all owned temps absent.
These are independent of the implementation's355-pass and10-pass rerun receipts.

QUALITY also confirmed a parent-existing F contract defect which must be fixed
before broad old-test adaptation. Genuine0441 Email51 refuses a missing
`idx_email_actions_status` or
`idx_email_unsubscribe_receipts_classification_action` with
EmailPersistenceCorruption and leaves it missing. After real paired publication,
the corresponding `idx_actions_email_email_actions_1` and
`idx_actions_email_email_unsubscribe_receipts_0` are automatically recreated by
the shared Store known-repair path. The same behavior was reproduced with the
archived parent4957997d, so5A did not introduce it. This broadens the original
Email repair contract and qualifies the earlier F review; it is not approval of
a new repair policy. Do not weaken the old refusal assertions to make them pass.

Next is a separately scoped5B0 regression and mechanism repair preserving both
original Store known repairs and Email strict refusal/its one existing provider
column repair. General fixture work waits for that correction and sequential
review. Remaining Task4/5 named gates, latest-copy/native evidence, CI/deployment
and the full10+1 goal remain open. No production or provider mutation occurred.

## Task4F independent acceptance, 2026-10-14

Root accepts only the coupled F unit at20bebe37265c17041732f149d1e4da7da0376ef8
(parent5dd1af7f) after sequential independent SPEC then QUALITY. SPEC independently
ran all48 F cases (48 passed,192.78s),16 selected protocol cases (15.14s),
two existing Store repair cases and one feedback-round repair case. Its additional
genuine2773 Store19-only and empty-Email-ledger-only counterexamples were refused
by both real constructors and the coordinator without changing bytes or making WAL.

QUALITY independently passed four focused crash/unsupported-inverse/concurrent/
existing-provider-repair cases (20.33s). A separate real old-DAO write after
prepare, real child publication, new-DAO write after forward, current-fact inverse
and genuine old strict cold read/write retained both results and the original
BLOB Store timestamp plus complete two-version BLOB Email ledger. Cached-path
missing-index repair and unknown-action-trigger refusal also passed. Reviewers
left HEAD/status unchanged and verified all their owned temporary roots absent.
These independent results remain separate from the implementation's82-pass and
119-pass/1-failure batches and the later single-case corrections described below.

Root's isolated60-open measurement compared published4eb Store19 with F: cached
constructor median3.3378ms versus14.1731ms; QUALITY's separate30-open candidate
median was16.473ms. The additional action-manifest check scans schema metadata,
not action rows. Existing feedback guard reads remain present. QUALITY accepted
the bounded cost and selected29/known-repair consistency without recommending
another cache layer. These empty temporary-DB timings are not production request
latency or throughput evidence. Root's temporary archive/databases were removed
and their absence verified.

This acceptance selects local Store2026-10-14.1/Email52 defaults but does not make
the branch deployable: Task5 external old-table consumers and the exact deferred
current/historical test adaptations below remain open. Task6 must refresh the
actual production copy and immediate published-source binding, then prove native
fake-provider behavior; Task7 CI, normal deployment and live readback remain open.
No production migration, provider call, push or deploy follows from this review.

Before Task5 edits, root normally merged main0441cf15 (PR87) at a clean gate.
The incoming310 Store lines preserve failed-History source-candidate hydration
and unlinked WeChat failure behavior; there were no merge conflicts. All six
History pagination cases plus the real Store-only canonical-parent/29-kind/FK
case passed together (7 passed,4.19s,exit0). The owned temporary test root was
removed and verified absent. Source/inverse/factory/DAO files were not changed
by this increment. Task5 must also translate the newly arrived
`_list_failed_operation_logs_with_count` Email candidate branch while preserving
the PR87 query and status semantics; the new native SQL is not already migrated.

## Task4 current-schema factory preparation (unselected)

The neutral classification factory creates the exact post-Email51 30-column
parent, including its legacy flag CHECK and without the retired
`action_plan_json` column. The retained Email factory creates the 19 current
business tables, 10 independent indexes and 19 independent triggers, while a
separate call creates only the empty migration ledger. It reuses the current
category table/binding/index constants and the current required trigger SQL;
it creates no old thirteen action tables, external action blockers, seed rows
or markers. Both factories run on the caller's connection without an implicit
commit. Archived `2773d6f4` fresh-constructor SQLite metadata supplies the
independent fixture. Six focused tests passed for exact native declarations,
repeat calls, rollback, and opt-in Email29 action writes under foreign keys.
Current Store and Email constructors still use their existing paths; release
coordination, activation, and production acceptance remain open.

## Task3A-2 local forward/current-fact inverse

`app/action_email_storage.py` now has standalone forward and inverse functions
for the two exact frozen synthetic sixteen-action/thirteen-Email pairs. Both
require a caller-owned FK-off transaction and make no schema-version, Store,
Email constructor, provider or runtime-route choice. The forward preflight
validates the entire source declaration/dependency/allocator closure before
mutation. It copies the existing sixteen action types with their physical IDs
and stored SQLite types, then encodes all 144 Email fields through the accepted
finite scalar codec. It retains signed source ROWIDs, two AUTOINCREMENT waters,
two composite keys, and all existing logical action allocator aliases. During
raw copy, row-dependent action triggers are deferred and then restored. The
thirteen captured external blocker triggers are replaced by finite typed-action
equivalents with their original changed-value predicates and abort text.

The inverse validates the canonical metadata-only source state, exact
twenty-nine-type target declaration, translated blocker bodies, dependencies
and source-name availability before mutation. It rebuilds all thirteen source
tables, indexes and five own triggers from current projected facts, including
post-forward inserts, updates and deletes. No original source row backup is
stored or replayed. Canonical SQLite object SQL is installed with an explicit
MAIN schema qualifier on index and trigger names; SQLite stores the same exact
canonical SQL while same-name TEMP tables, indexes and triggers remain intact.

The API-missing RED failed at import. A meaningful RED then exposed unqualified
factory index creation binding a TEMP `actions` shadow and failing after schema
mutation; the local canonical-object installer fixed that without changing the
shared factory or the six default-disabled manifests. Focused cases across
both source variants cover all thirteen populated families, 144-field
value/`typeof` and ROWID checks, NULL public-key duplicates, negative and zero
ROWIDs, deleted-highest ordinary ROWID reuse, BLOB and NUL values, raw
non-JSON text, old action-ID collision,
post-forward current facts, absent/zero/high allocator waters, all thirteen
external blockers before/after both transitions, MAIN/TEMP separation, source
and target refusal, and caller savepoint rollback after late SQL failures in
each direction. An existing source foreign-key finding remains visible in the
target and returns unchanged after inverse; no clean-source assumption was
added. The bounded source/definition/writer/old-reply/inverse set passed
**360 tests with 6 existing skips** before the final two deleted-highest
regressions, which passed separately; scoped Ruff and whitespace checks
passed. These are synthetic tests only. Task3B still must bind the actual
published E predecessor and validate current production-copy/old-code behavior
before any release decision.

Independent Task3A-2 SPEC review found a MAIN/TEMP dependency gap after
`090dc101`: dropping a MAIN source table or `actions` could silently delete a
TEMP trigger attached to it. The previous schema-only rename check inspected
only MAIN declarations; a historically created unqualified TEMP trigger can
keep identical SQL during rename yet disappear on DROP. Sixteen new cases
(four dependency forms in both forward/inverse directions and both frozen
variants) were RED because conversion proceeded. The local preflight now
replays MAIN declarations first and every TEMP table, view, index and trigger
in TEMP schema creation order inside a separate SQLite connection. Native
per-table rename and DROP probes under scratch savepoints compare external
MAIN and TEMP view/trigger declarations, then roll back. They refuse only
dependencies that SQLite would rewrite or remove. The 16 formerly failing
cases pass, while unrelated same-name TEMP tables, indexes and triggers still
survive both transitions unchanged. A TEMP table renamed after a trigger was
created follows SQLite's actual rebound ownership and remains allowed. A
file-backed read-only MAIN with TEMP objects and `query_only=ON` still permits
read-only source capture; scratch writes never touch either live schema.
After the correction, the bounded Email source/definition/writer and older
reply/storage selection passed **382 tests with 6 existing skips**. Scoped
Ruff and diff checks passed. This remains local synthetic evidence; independent
SPEC and then QUALITY review are pending.

## Task 3A-1: unselected source capture

`app/action_email_storage.py` now provides a read-only candidate source capture
for an already open caller transaction. It accepts only the reviewed E
sixteen-type action declaration paired with the matching frozen actual or fresh
thirteen-table Email declaration. The capture returns the literal action and
Email table metadata (including columns, foreign keys, indexes and the five
own triggers), the thirteen exact external blockers, row counts, locator modes,
and each relevant allocator's absent-versus-zero sequence state. The two
source pairs are explicit; crossed variants, changed declarations or trigger
bodies, unexpected inbound FKs/views/triggers, target object-name collisions,
mixed action types and allocator aliases refuse before any mutation. SQLite's
rename parser checks external SQL dependencies on schema-only scratch data.

Independent SPEC review found that the first closure check covered only the
thirteen old Email tables, while the next unit also rebuilds `actions`. Six
deterministic tests (an outside inbound FK, view and trigger referencing
`actions` under each frozen pair) were RED because preflight accepted them.
The closure now checks `actions` and all thirteen Email tables together with
the existing native FK and schema-only rename checks. All six cases are GREEN;
the selected suite reports 269 passed and 6 skipped. This changes only
candidate source refusal; no runtime route or migration DDL was added.

Independent QUALITY review then found that a TEMP table can shadow a MAIN
source name during unqualified PRAGMA, row-count or `sqlite_sequence` reads.
Six tests were RED across the two frozen variants: a TEMP same-name child hid
a MAIN inbound FK, a TEMP Email cancellation row changed the captured MAIN
count, and TEMP sequence metadata hid a MAIN attempt high-water of 57. The
source capture and its two shared metadata helpers now qualify these reads
with `main`. A separate regression confirms changed TEMP declarations and
TEMP `actions` rows leave the MAIN capture and caller transaction intact.
All 40 focused capture cases and the selected Email/reply/action storage set
passed (339 passed, 6 skipped); scoped Ruff and diff checks passed. The six
default-disabled action schemas retain the same factory declarations because
only source-introspection helpers changed. Actual published-source binding,
forward/inverse DDL and release integration remain pending.

The synthetic tests cover empty sources, colliding existing action IDs and
original Email ROWIDs, deleted high ROWIDs, preserved sequence zero/high-water,
and refusal without changing source schema or action rows. The first capture
test was RED because the new module was absent. An unexpected sequence on a
non-AUTOINCREMENT source table was separately observed RED and then refused.
The focused suite passed 26 tests; scoped Ruff and whitespace checks passed.
No forward/inverse DDL, source or target marker, Store/Email constructor route,
provider call, production copy or release claim is in this unit. Actual
published-source binding and current-fact inverse remain required later.

The isolated opt-in storage declaration adds thirteen Email action types to the
existing sixteen-type actions schema. The option defaults to off. No Store or
Email constructor, migration selector, version marker, provider path, or
production checkout selects it.

The finite declaration is derived from the frozen 2026-10-13 nine-table and
four-child source inventories. Its static metadata covers all 144 source
columns, their source affinities, NOT NULL and CHECK rules, declared unique and
query indexes, literal source defaults, and native outgoing foreign keys. The
defaults are frozen metadata for Task 2B's writer; the opt-in physical payload
does not apply a source-table default to an omitted JSON member. The two source column-order
variants of `email_unsubscribe_claims` have explicit projections. The nine
nullable TEXT primary-key families carry their original ROWID as a signed
decimal `legacy_key`, while their public key remains a nullable source scalar.
Two integer IDs and two composite keys use their original public identity
shape. Scalar JSON encoding preserves SQLite BLOB/NULL/numeric storage classes
and raw inner JSON strings; the source fields appear once in the stored key or
payload.

Focused tests create captured native source declarations and an opted-in target
with narrow classification/tasks/runs parent stubs. They compare all 144
projected values and `typeof` results for both captured variants, and exercise
nullable/BLOB public keys, native parent rename and delete restrictions,
CHECK/UNIQUE failures, direct-action attempt-count triggers, and retry
immutability. These tests do not use the full EmailStore constructor.

Task 2B now supplies the finite writer and allocator helpers, including ordinary
signed ROWID and two AUTOINCREMENT namespaces; its separate acceptance is below. The thirteen external source
triggers will be translated with their actual owning tables during integration;
they are outside this actions-table declaration. Migration, current-fact
inverse, exact published-predecessor binding, caller routing and release
validation remain separate work. Passing Task 2A tests does not establish
their behavior or a production Store/Email change.

The first target test was observed RED in both source variants because
`create_action_schema` did not accept `include_email`. After the finite schema
and projection implementation, the focused target tests passed (7 cases).
The selected older storage/reply regression files plus this target file passed
with 351 tests and 6 existing skips. This is local storage evidence only.
The six pre-Email action table/index/trigger manifests (6, 9, 12, 14 and both
16-type variants) were compared byte for byte against this branch's preceding
`app/action_storage.py` definition with `include_email=False` and matched.

Independent specification review found that the first candidate's generated
Email field expression could read a same-named payload member on an older
action type. A valid `external_action_results` row exposed its
`action_identity`, `operation` and `created_at` through Email-prefixed columns.
The regression was observed RED, then fixed by finite per-field record-type
CASE membership. All Email-prefixed fields now return NULL outside the exact
Email kinds that own each original field, including within the thirteen-type
family. The approved operation FK discriminator name is
`email_operation_parent_type`. The focused target suite is now 8 cases; the
larger source/native mutation matrix belongs to the independent review, not
to this local focused validation.
After this correction, the selected target and older storage/reply files passed
with 352 tests and 6 existing skips; scoped Ruff and diff checks passed.
The six default-disabled manifests remained byte-identical to the pre-fix
commit.

Independent quality review found a second opt-in declaration gap: Email rows
could populate physical parent-reference columns reserved for the older action
types, creating unintended foreign-key blockers. A new regression was observed
RED for both reply variants, then GREEN after one Email-only CHECK derived from
the factory's existing reference columns. It covers all twelve shared physical
references and, for the fresh variant, `memory_attempt_ref`. It checks INSERT
and UPDATE rejection with FK enforcement off, then verifies a real task parent
is not held by an invalid Email row with FK enforcement on. The valid Email
cancellation fact still projects unchanged. This remains a local schema test;
the external Email trigger and migration work is pending.
After this correction, the focused and selected older storage/reply files
passed with 354 tests and 6 existing skips. Scoped Ruff and diff checks passed;
all six default-disabled action manifests remained byte-identical.

## Task2A independent acceptance

The finite declaration unit `b38c2458`, corrected by `cac51248` and
`7adf3ea8`, passed sequential independent SPEC and QUALITY. SPEC checked the
144-field map, six unchanged default-disabled manifests, native conflict/FK
timing and 6,912 savepoint-isolated scalar mutations across the two source
variants without acceptance/value/typeof differences. Generated Email columns
now return NULL outside their exact owning kinds; a separate Email-only CHECK
rejects every unrelated older physical reference, including the fresh-only
Memory parent. Regression cases failed before each fix and passed afterward.
Independent final SPEC passed all10focused cases and all-kind ownership probes;
QUALITY passed265selected cases with6variant skips. These overlapping counts
are not a single aggregate suite. All independent probes used in-memory data
and left no disposable artifacts.

Root normally merged E/main candidate `a0b60ba6` as `f249e9dd`, retaining both
claim sets and concurrent receipt behavior. No Email storage code changed in
that merge. This accepts Task2A definitions/projections only; it establishes
no writer, allocator, constructor, migration, version, publication or provider
behavior. Default Email opt-in remains disabled. The complete ten-core-table
plus-ledger redesign remains incomplete.

## Task2B local writer validation

The finite opt-in writer unit adds source-shaped insert, update and delete
paths for all thirteen Email kinds. Nine ordinary ROWID kinds use independent
signed source locators; the two AUTOINCREMENT kinds retain separate
`sqlite_sequence` aliases; effects and direct retry use source-affinity
composite keys. Original omitted fields and defaults are materialized under
SQLite's writer lock. No Store or Email constructor selects this schema.

The writer regression was first RED on the missing ROWID helper, then GREEN
against the frozen actual source DDL. The focused comparison covers all
thirteen native inserts and `typeof` projections, nullable public keys,
negative/zero and reused ROWIDs, MAX fallback, independent deleted-high
AUTOINCREMENT sequence, explicit-ID conflict and UPDATE behavior, composite
affinity and partial key update, immutable retry conflict, CHECK ignore,
no-target binding errors, and two-connection deferred/autocommit contention.
The existing Email source and declaration oracles and selected older storage
tests also passed: 431 focused tests and six existing variant skips. Ruff and
diff checks passed; all six default-disabled table/index/trigger manifests
remained byte-identical to the pre-writer commit. A bounded work regression
checks that the indexed Email MAX lookup does not grow with 1,000 additional
lower IDs. This local writer result passed independent SPEC and QUALITY as detailed below;
migration, constructor activation, version assignment,
publication and provider effects are outside this unit.

## Task2B independent acceptance

Commit `0f1a0b5a` passed fresh sequential SPEC and QUALITY. SPEC independently
ran60writer/declaration/source cases plus344older storage cases with6skips,
and106paired assertions using both captured source variants: all13insert
value/type projections plus all9NULL/BLOB/TEXT public-key transitions at signed
ROWID. Its independent exhaustion probes matched native SQLITE_FULL ordering
and whole-caller-transaction rollback. Six older complete manifests remained
byte-identical. QUALITY independently ran395storage/writer cases with6skips
and36source-oracle cases; four additional paired duplicate-insert/conflicting-
update scenarios matched rows and transaction ownership in deferred and
autocommit modes. These are overlapping gates, not additive test totals.

The established declaration boundary remains native TEXT-primary-key1555
versus the finite unique-index2067 extended code; both raise IntegrityError
with the same facts and transaction state, and no current Email caller uses
that distinction. Native FK timing/codes remain separately preserved. No
columns or triggers were invented merely to imitate a diagnostic subclass.
All probes used closed in-memory databases or cleaned task-owned temporary
directories. This accepts disabled Task2A/2B storage only; actual published E
source binding, forward/current-fact inverse, constructors, caller conversion,
paired publication and production acceptance remain required.


## Task3A-1 independent source-capture acceptance

The source-capture unit `18387703`, finite rebuilt-source closure correction
`2ca854a4`, and MAIN-schema qualification `729aaf45` passed fresh sequential
SPEC then QUALITY review. SPEC independently passed111 selected checks with
two existing skips, the138 exact-metadata/closure cases and34 MAIN/TEMP
shadow cases. QUALITY independently passed61 checks and10 combined scenarios
covering all thirteen temporary source shadows, same-name temporary indexes,
actions shadows, unrelated temporary allocator metadata, query-only and
file-backed read-only connections. MAIN and TEMP serialized facts and caller
transactions remained unchanged throughout the independent probes.

All144 source fields, both canonical sixteen-type declarations, five own
triggers, thirteen external blockers and the9/2/2 locator split remain exact.
The six disabled-Email manifests are byte-equivalent. This accepts only
read-only candidate source capture/refusal, not forward/inverse conversion,
a published predecessor, a release version or production activation. Task3A-2
may now implement pure forward/current-fact inverse; Task3B actual publication
binding and copy/genuine-old-code acceptance remain mandatory before Task4.


## Task3A-2 independent pure migration acceptance

The pure forward/current-fact inverse at `090dc101`, including the TEMP
dependency correction `fedd8ee1`, passed fresh sequential SPEC then QUALITY.
SPEC independently passed96 selected checks,24 dependency/chronology cases,
32 native value/allocator boundaries and populated both sixteen-type source
variants through all thirteen Email families. Current post-forward updates
to every older kind survived inverse with physical IDs, native types and
logical watermarks intact. QUALITY independently passed86 focused checks,
1,538 paired native source/target constraint comparisons and20 dependency/
chronology scenarios. These are overlapping gates, not additive test totals.

Capture and migration preserve unrelated MAIN/TEMP facts, caller transaction
ownership and foreign-key settings. Dependent TEMP triggers attached to a
replaced MAIN table are refused before mutation, including historical owner
bindings hidden by a later TEMP shadow. Unrelated TEMP triggers remain usable.
Current facts, not saved original rows, drive inverse; raw receipt values and
all144 source fields retain their native SQLite types. Older disabled-Email
manifests remain unchanged.

This accepts only unselected synthetic/candidate-prepared Task3A. Task1 actual
published predecessor binding and Task3B latest actual-copy plus genuine old
code cold read/write/re-forward are still required. Full Task3, constructor/
caller integration, paired release/version selection and production acceptance
remain incomplete. No provider, native CLI or production mutation was used in
these independent reviews, and task-owned temporary artifacts were cleaned.

## Task3B offline copy proof (independent review pending)

The proof at `scripts/validate_action_email_copy.py` reads the live SQLite
source through a read-only URI and SQLite online backup into an owned temporary
directory. Its exact published old application is archived from Git. The
historical Task1 schema oracle remains `eb797e6bd35de695c269054a2e697af8fde2baa5`;
the current old-code binding is `2773d6f43518b64c85f1d4bed9d520bd5563df07`.
The intervening Store changes concern startup reply reconciliation batching,
selective candidate rechecks and CAS, not Email/action DDL. The source Git HEAD
and SQLite backup are separately observed, not an atomic pair. The candidate
worktree normal-merged 2773 at `6b9a50a9` so that current Store behavior is
retained. No constructor or release selector activates Email13.

The first offline run at `2b201a0e` was rejected in independent SPEC review:
its blank SMTP receipt was seeded after first forward, required attempts,
steps, unsubscribe outcome and retry were written only after inverse, and
fixture/original-row exclusions could hide corruption. The later `6b9a50a9`
run was also unaccepted because its last re-forward check omitted complete
fixture rows. These are proof-harness gaps; no pure-migration corruption was
demonstrated. Both runs are historical, not the current Task3B result.

QUALITY found one further proof gap in the `5099c2ab` run: a write-only
change to the old attempt allocator water could be accepted after inverse. That
run remains narrow and unaccepted. The corrected, committed harness at
`e9314a50530c7615cdde1ad76e091bc2dd5b22fa` passed all three cases:
actual owned online backup (121 source user tables to 108 target), an archived
old-created SQL-legal fresh case with two distinct NULL ActionPlan IDs, and a
separate archived old-created application-valid fresh case. On an unmigrated
old database, those legal SQL NULL rows cause the genuine old EmailStore cold
constructor to reject `invalid immutable ActionPlan None`. Accordingly only
the SQL case claims lossless row preservation; both application-valid cases
strictly cold-open with genuine archived AutoReplyStore and EmailStore.

Before first forward, the fixture contains a valid blank-SMTP receipt. An
independently seeded old-code donor checks the allowed synthetic rows and
storage types; only old public clock fields are normalized, bounded by the
observed seed interval plus two seconds. After first forward, both Email AUTO
waters are raised and the target receives exact current-fact attempt, step,
receipt-enrichment, unsubscribe-skip and explicit-retry deltas from a genuine
old public API donor through the finite candidate helpers. Their complete
projections, SQLite types, signed ROWIDs, composite keys and waters are checked
before inverse. Inverse restores that current state; in application-valid
cases strict archived constructors/public readers then verify it, and old
public methods apply and replay feedback, replay the receipt and complete the
second TRASH retry attempt using a fake DAO-only result. A separate donor
checks the expected public-write delta and the complete resulting native Email
allocator-water map before that state becomes the re-forward oracle. Re-forward again checks complete
source projections, original sixteen-action facts, native allocators, unrelated
original rows and fixture rows, schema markers and existing FK findings. The
actual source had one pre-existing FK finding, preserved unchanged.

The aggregate-only artifact is
`evals/database_redesign/email_actions_copy.v1.json`; it binds exact executed
commit, source ref, source and candidate module hashes, observed checkout HEAD
before/after, per-case counts and assertions. `source_counts_after_fixture_seed`
are explicitly fixture-inclusive. Twenty-three selected focused checks passed,
including the independent SPEC false-pass regressions, the QUALITY write-only
allocator +999 regression and absent/zero/high,
deleted-highest ordinary ROWID and signed-max AUTO-water cases. Ruff and diff
checks passed. Owned temporary databases, archived code and intermediate
aggregate copy were cleaned. The proof uses no provider, native CLI,
production write, target version allocation, constructor selection, push or
deployment. Task3B implementation and evidence still require independent SPEC
then QUALITY review before downstream integration.


## Task3B independent acceptance, 2026-10-14

Root accepts the offline proof after sequential independent SPEC and QUALITY
rechecks, both PASS. Exact executed code is e9314a50530c7615cdde1ad76e091bc2dd5b22fa;
reviewed final evidence is b0a7e90411490495866d37de0118801afea2df65. The sole
QUALITY finding (unexplained post-old-write sequence advancement) is fixed by
exact entire donor/native Email-water equality before adopting the re-forward
oracle. Its regression rejects a sequence-only +999 change. SPEC independently
ran three water checks (11.56s); QUALITY independently verified22 provenance
and evidence-boundary assertions. Earlier SPEC checks and author23 selected
checks retain their recorded scope; neither reviewer repeated the costly
actual-copy run. All8 reported module hashes and relevant executed/current
file identities were independently checked.

Acceptance covers actual published2773/current-fact inverse/strict genuine
old public read-write/re-forward, plus separate SQL-NULL and application-valid
fresh cases. Actual121→108 tables and the existing FK finding1 are preserved.
No source row was filtered or sanitized. No production change, constructor
activation, target marker, provider call, native CLI, CI or publication is
claimed. The full ten-core-table plus one-ledger redesign remains incomplete.


## Unselected current-schema factory acceptance

Root accepts0a1929a0d14a578d292c299c4afcca6c0bd004ca after sequential
independent SPEC then QUALITY. SPEC reconstructed genuine published2773 fresh
metadata and verified all49 fixture objects and complete retained namespace;
six focused tests passed2.77s. QUALITY independently verified passive imports,
caller-only connection use, both transaction modes/savepoints/outer rollback,
real older-kind and Email29 FK writes, and unchanged active constructors;
six focused tests passed1.72s. Both Ruff/diff checks passed and review-owned
temporary artifacts were cleaned.

The neutral30-column classification parent and19-table/10-index/19-trigger
retained DDL are prepared only. The separate ledger is empty, and no seed
rows or version values are assigned. Existing constructors, source tables,
validators, DAO and releases remain selected exactly as before. This is not
Task4 constructor integration or production acceptance; that work remains open.


## Task4A unselected paired protocol preparation

Following independently accepted sequencing e977fece, the new
`app/action_email_schema_release.py` contains only the unselected pure paired
transaction protocol. Source Store19/Email51 is mandatory. Preparation is
read-only and captures the exact Store value/timestamp, every Email ledger
row/timestamp, the supported actual/fresh declaration variant, source allocator
aliases/waters and the existing FK baseline. Caller-required target pairs use
obvious sentinel versions in tests; no real target version is allocated.

Publication rechecks the source inside its writer transaction, applies the
accepted pure thirteen-family conversion, and commits both supplied markers
together with the twenty-nine-type schema and all thirteen existing translated
external blockers. The inverse reconstructs current facts, receipt updates,
original public IDs/ROWIDs and allocator waters before restoring the exact
source Store timestamp and complete ledger. Source facts written after
preparation remain inputs to conversion; captured metadata is never an old
business-row backup. Existing missing-parent FK findings are compared exactly.

The first test run was RED because the protocol module did not exist. The
initial protocol run passed39 cases after canonicalizing dump statement order
(the inverse recreates objects; sqlite_schema insertion order is not a source
contract). The expanded new file passed53 cases in43.07s. Both frozen variants
exercise failures before DDL, after capture, during conversion, after the first
marker and before commit; the entire logical source fingerprint, sequences and
observed source ROWIDs remain equal. Current-fact inverse tests preserve a
later receipt backfill, new nullable-ID cancellation at a signed ROWID, new
retained reply fact and separate deleted-high attempt/step allocator waters.
Unsupported target metadata/aliases and failures after reconstruction or the
first inverse marker preserve the complete target logical fingerprint.

The active Store19/Email51 constructors and database coordinator are explicitly
verified unchanged on a fresh database. There is no real target-default cold
constructor proof, paired subprocess, active release selector, DAO conversion,
new seed path, CI/native/provider/production action or publication in Task4A.
The final complete constructor/DAO/validator/marker activation remains coupled;
Task4 and the full10+1 redesign remain incomplete. Independent SPEC then QUALITY
review of this preparation remains pending.

Focused regression result:216 passed in73.23s using only the new protocol,
Email migration, finite Email writes, current Email/classification factories,
Store release, Email release and prior Attempt/Memory release test files.
Ruff and `git diff --check` passed. Owned temporary test roots were inspected
and removed after verification. This result is local protocol regression
evidence, not integrated Task4/runtime publication acceptance.


### Independent Task4A acceptance, 2026-10-14

Candidate `2fead6ee8ef2bfa23fd7c295693cf981cca55b5b` passed sequential
independent SPEC then QUALITY for the unselected paired publication protocol.
SPEC independently ran53 protocol cases in25.41s. QUALITY independently ran
the same53 in36.16s plus8 lifecycle/marker probes across both supported source
variants. Ruff and commit whitespace checks passed; the candidate was clean.
The probes verify post-commit readback failure leaves an inverseable valid
target, and split marker clocks, extra Email ledger rows or a missing target
ledger row refuse readback/inverse without mutation. Owned temporary fixtures
were removed. No full suite, production/provider/native CLI, push or deployment
was performed for this unit.

The accepted protocol matches source Store19/Email51 and uses explicitly supplied
test target markers. It is not selected by current constructors, coordinator or
DAOs. All final default-constructor/coordinator/subprocess checks remain Task4F.
A stale already-completed release object can inverse a distinct later publication
using the same target version pair; current checking establishes paired marker
consistency rather than publication-instance ownership. Independent reviewers
found no introduced supported call path and accepted this as a nonblocking
preparation limitation; no new policy field or ownership guard was added.


## Group4B partial plan/direct/feedback/retry DAO preparation

Group4B changes only current five-family SQL in `EmailStore`, the two authorized
shared C/D read guards, and one narrow accepted writer interface. All current
FROM/JOIN references to `email_action_plans`, `email_actions`,
`email_action_attempts`, `email_direct_retry_requests` and
`email_feedback_requests` at/after `_current_action_plan_rows` now use explicit
finite projections. The module-level explicit-retry/configuration SQL is
converted too. An AST scan reports zero remaining physical five-family
FROM/JOIN/INSERT/UPDATE references in that current-method range. Historical
migration/DDL and startup validators remain E/F work.

The fourteen original write sites across ten methods use accepted source-shaped
writers. `_update_direct_action` selects the original structural ROWID with the
original public-ID/status/start/count guards in the existing writer transaction.
It preserves zero-row CAS outcomes, and the original caller still controls
rollback. Attempt helper `lastrowid` remains the independent public attempt ID.
The narrowly named `ignore_plan_action_conflict` option emits the exact native
Email-operation `(plan,type)` partial conflict target. It does not replace it
with broad `OR IGNORE`, preempt CHECK/NOT NULL/FK errors, or alter allocator
semantics. Both original helpers' default behavior and DDL remain unchanged.

`tests/test_email_action_plan_direct_dao.py` first failed at the missing narrow
helper keyword and at the original physical direct-action read on an explicitly
converted copy. The helper and public read then passed. Thirteen focused cases
use genuine archived2773 public methods and the native SQL source oracle, with
explicit supporting target guard/index checks. The genuine fresh source is
created by both real old default constructors. Target setup
explicitly converts an owned copy using the accepted pure helper and creates a
test-only EmailStore object; it makes no claim that the unfinished target
default constructor can open that copy. A fixture source ROWID is-7 and its
real attempt allocator is advanced to700 by native insert/delete before copying.
The first new public attempt is701, independently of physical `actions.id`.

The comparisons cover immutable plan replay/conflicts; NULL-prior feedback,
replay, different-intent and stale-current-plan refusals; append/reapply with
current versus retired plans; direct polling, claim, dependency order, delayed
retry, successful receipts, stale claim rejection, recovery, reconciliation and
concurrent single-winner claims; immutable source-verified explicit retries;
mailbox/progress/filter projections; attempt sequencing; and the unchanged
reply-in-flight refusal. A native after-attempt-insert mutation makes the final
CAS fail in both implementations, proving that the entire transaction, including
the attempt row, rolls back. No provider is called.

The real current/retired-plan polling call path uses existing typed Email
indexes in representative EXPLAIN output. Adding3000 unrelated action records
changes total SQLite VM steps from5751 to5752 and preserves the selected current
public action ID. No index was added. The selected new DAO/storage/writer/
migration/paired-protocol regression passed176 cases in85.06s. A strengthened
native-source versus helper check additionally ignored an explicit signed-MAX
ROWID duplicate and verified that the next ordinary ROWID remains2; that scoped
check passed. Ruff and diff checks passed.

Remaining current physical C/D SQL is explicit, not hidden behind a router:

- Reply: `get_email_reply_receipt`, `claim_email_reply_dispatch`,
  `get_email_reply_dispatch_claim`, `mark_email_reply_dispatch_uncertain`,
  `release_email_reply_dispatch_retryable`,
  `recover_terminated_email_reply_dispatch_claims`, `persist_email_reply_receipt`.
- Unsubscribe state/lifecycle: `claim_email_unsubscribe_write`,
  `get_email_unsubscribe_claim`, `get_email_unsubscribe_state_snapshot`,
  `get_email_unsubscribe_terminal_snapshot`,
  `_validate_email_unsubscribe_terminal_snapshot`, `get_email_unsubscribe_effect`,
  `persist_email_unsubscribe_continuation`, `get_email_unsubscribe_continuation`,
  `append_email_unsubscribe_step`, `list_email_unsubscribe_steps`,
  `list_orphaned_email_unsubscribe_claim_owners`,
  `release_orphaned_email_unsubscribe_claim`,
  `repair_email_unsubscribe_effect_journal`,
  `retire_email_unsubscribe_claim_for_direct_run`,
  `recover_terminated_email_unsubscribe_claims`,
  `mark_email_unsubscribe_uncertain`, `release_email_unsubscribe_failed_operation`,
  `release_failed_email_unsubscribe_for_explicit_retry`,
  `advance_email_unsubscribe_phase`, `persist_email_unsubscribe_terminal`,
  `get_email_unsubscribe_receipt`.
- Mixed views/cancellation/task recovery: `list_unsubscribe_classifications`,
  `list_email_context_receipts`, `backfill_email_unsubscribe_entry_url`,
  `get_email_unsubscribe_entry_url`, `list_email_classification_observability`,
  `list_missing_unsubscribe_action_tasks`, `get_email_unsubscribe_cancellation`,
  `cancel_failed_email_unsubscribe_task`, `_unsubscribe_cancellation_decision`,
  `list_terminal_unsubscribe_tasks_missing_receipts`.

The mixed observability/context APIs above are not claimed fully target-runnable
by B. B-family joins within C/D methods have changed, while their remaining
C/D-specific reads and writes are deliberately deferred. The finite scan finds
38 current methods with these residual physical C/D accesses. E/F still owns
`_REQUIRED_*` metadata, `_validate_schema_shape`, `_validate_durable_state`,
`_validate_durable_rows`, historical initialization/migration dispatch, seed
factoring, both fresh constructor orders, the coordinator/subprocess, real
version allocation and final coupled default activation. External direct
consumers and full copy/native/CI/production acceptance remain Task5/6.

Store/Email default versions, constructors, schema selectors and action schema
flags are untouched. This unpublished B DAO code requires explicit Email29
preparation, while selected default schemas remain Store19/Email51; the partial
branch is therefore not an independently runnable default release. There is no
alternate DAO, compatibility router, view/dual writer, new policy, native CLI,
provider action, push, deployment or production mutation. Full Task4 remains
open. Independent SPEC then QUALITY review of group4B remains pending.

Owned task temporary roots, genuine old-code copies and copied test databases
were inspected and removed after checks; no historical test archive is retained.


### Independent Task4B acceptance, 2026-10-14

Candidate1e54ce2540b8f1f14d383980bc4cdba336bc2ee6 passed sequential independent
SPEC then QUALITY againstf404192c. SPEC independently passed13 new cases
in106.50s plus genuine-old comparisons for moved provider locator completion/
readback and unsubscribe receipt absence/match/wrong-classification outcomes.
It verified unchanged public signatures and no current physical five-family SQL
references. QUALITY independently passed13 cases in122.21s plus7 probes for
moved source ROWID, each CAS guard, caller-owned rollback and unrelated source
ROWID uniqueness errors under targeted conflict handling. The archived2773
EmailStore oracle is byte-identical to the parent implementation. Ruff/diff
checks passed, reviewed HEAD stayed clean and all review-owned temporary code/
DB/pytest roots were cleaned. No provider/native CLI/production/publication
action occurred. This accepts partial4B preparation only, not default bootstrap
or full Task4. The documented38 residual C/D methods and E/F dependencies remain
open; the next implementation unit is C's existing unsubscribe read/write chain.


### Task4C partial unsubscribe DAO preparation, 2026-10-14

This unit follows accepted5230a480 and converts the six unsubscribe families'
current DAO SQL: claims19, effects6, continuations6, steps8, receipts20 and
cancellations2 retain all61 source fields. All34 original mutations across14
methods use the existing finite source-shaped writers. Scalar/multi-row updates
and deletes resolve the original source ROWID, public step AUTO ID or original
two-component effect key inside the original writer transaction, with the same
predicates and rowcount outcomes. Backfill alone previously acquired its writer
implicitly: it now begins the writer transaction after URL validation, exactly
at the original conditional mutation point. No entry-reference predicate or
other policy has been added to that update.

Read conversion also covers current getters, snapshots, terminal validation,
orphans, recovery, cancellation and mixed context/observation/task paths. The
module `_AUDITED_UNSUBSCRIBE_LINEAGE_SQL`, `_audited_unsubscribe_lineage` and
`_current_unsubscribe_task_lineage` are included; their plan, generation,
identity and result predicates are unchanged. The demonstrated C state-display
call path required the accepted reply-receipt projection in exactly
`list_email_classification_observability` and `list_email_context_receipts`.
Those are shared D reads only, matching the earlier B shared-read approach.

The helper gap was reproduced as a missing-keyword RED before implementation.
`insert_email_action(update_continuation_conflict=True)` is confined to
`email_unsubscribe_continuations`, mutually exclusive with existing conflict
options. Native `(record_type,email_public_key)` UPSERT changes exactly four
payload values: effect_digest, observation_reference, controls_json and
updated_at. It retains created_at, action_identity and source ROWID and returns
that existing structural locator. SQLite JSON subtypes preserve the accepted
scalar BLOB/NULL codec without Python decode/re-serialization. Existing broad
OR IGNORE effect inserts and B's targeted plan/type DO NOTHING are unchanged.

The first real claim/snapshot comparison passed the genuine archived source API
and failed against the converted copy at the retired cancellation-table read.
The thirteen cases in `tests/test_email_action_unsubscribe_dao.py` then exercise:

- Native continuation UPSERT versus source SQL: negative/zero locators, chosen
  public-key versus competing ROWID conflict, retained creation time, BLOB
  scalar codec, nullable public key, native CHECK/NOT NULL/FK/other unique
  errors and caller-owned rollback.
- Genuine2773 public-method comparisons for claim acquisition and owner fences,
  phase/status changes, refused release, successful failed-operation release,
  proved-owner recovery, orphan release and explicit failed-Audit retry.
- Continuation prefix acceptance, fresh owner/generation, effect chain,
  contiguous journal and replay/refusal, terminal receipt/idempotence/conflict,
  state/terminal snapshots, current observation and task recovery readbacks.
- Audited receipt lineage, actual Consumer/Audit IDs, earned generation after a
  later task generation, and stale caller refusal. The source claim uses ROWID
  -7; the copied target retains it. Direct retirement, allowed receipt promotion,
  healthy/undercovered journal repair, exact URL backfill and safe context
  projections also match the archived public methods.
- Principal cancellation preview/apply/replay, retained decision, absent provider
  receipt and stopped claim/task recovery; terminal-write failure and two-row
  owner-recovery failure roll back all earlier facts. Deterministic competing
  fill occurs after URL validation and preserves the original false/no-overwrite
  result, proving the mutation lock was not moved before validation.

Every source scenario cold-opens genuine archived2773 constructors, with source
code unchanged. Only the explicitly copied/converted target uses test-only
object setup; this is not target default-constructor proof. Complete native
six-family facts, source ROWIDs, steps' AUTO water and public readbacks are
compared. The source's real deleted step700 makes subsequent public journal IDs
continue at701. Journal repair's native CURRENT_TIMESTAMP is independently
checked for its SQL format and current time bound; only that volatile claim
field is excluded from exact source/target fact equality.

The actual existing-claim public call is also measured with600 other claims and
3000 unrelated cancellation rows. Its typed family unique index serves the
public-identity lookup, with4542 VM steps before and4542 after the added volume;
no full actions scan or speculative index is introduced. The independent small
behavior oracle is the genuine old claim API.

A finite AST SQL scan finds no remaining current physical six-family access.
The seven remaining current D methods are `get_email_reply_receipt`,
`claim_email_reply_dispatch`, `get_email_reply_dispatch_claim`,
`mark_email_reply_dispatch_uncertain`, `release_email_reply_dispatch_retryable`,
`recover_terminated_email_reply_dispatch_claims` and
`persist_email_reply_receipt`. E/F still owns required metadata/manifests,
`_validate_schema_shape`, `_validate_durable_state`, `_validate_durable_rows`,
historical DDL/initialization and migration dispatch, seed factoring, both fresh
constructor orders, coordinator/subprocess, real target version allocation and
final coupled activation. `preview_failed_email_unsubscribe_task` still invokes
that deferred startup shape validation. Task5 external direct consumers and
Task6 full copy/native/CI/production acceptance remain separate gates.

Store/Email default versions, constructors, selectors and action flags are
unchanged. This is unpublished partial DAO work requiring explicit Email29
conversion, not independently runnable default runtime or full Task4 completion.
No alternate DAO, compatibility router, views/dual writes, new policy, provider,
native Agent CLI, production mutation, push or deployment is included.

Focused verification passed189 cases in106.27s: the new C file plus accepted B
DAO, Email storage/writes, migration and paired-release protocol files. This
regression selection checks the new confined conflict option and shared call
paths without running default Email files whose constructors/validators remain
unfinished. Ruff and diff-whitespace checks passed. All343 EmailStore function
signatures and the historical/bootstrap/validator method bodies are unchanged.
The source/default schema declarations remain preserved; Email remains51.
Independent SPEC then QUALITY review of group4C is pending. Owned temporary
archives, copied databases, pytest roots and editing intermediates were inspected
and removed; no historical test archive is retained.


### Independent Task4C acceptance, 2026-10-14

Candidate9ecf1c2542e29ffdd66a3f73a8848215f60cba14 passed sequential independent
SPEC then QUALITY against5230a480. SPEC inspected all34 writes, ordering and
transaction boundaries,61 source fields,343 unchanged signatures, protected
declarations and remaining historical/startup-only physical references. Its
observed2 critical cases passed16.75s, plus3 option-rejection probes. A separate
full13 run finished but its final stdout was not retained, so no13-pass receipt
is claimed from that run.

QUALITY independently captured6 passed/7 deselected in31.08s, exit0: continuation/
effect chain/terminal, cancellation and no-replay decision, journal repair,
direct retirement/receipt promotion/context, competing URL fill, and audited
earned-generation lineage. Both reviewers confirmed12 original writer bodies
match genuine2773 by AST and the remaining2 differ only through accepted B
plan projections. Diff checks passed; HEAD stayed clean and all owned review
roots/code archives/DBs were cleaned. No live database/provider/native CLI or
publication action occurred.

This accepts only scoped partial4C preparation. In particular, the public
preview_failed_email_unsubscribe_task startup shape dependency remains E/F;
underlying cancellation decision/replay comparisons do not establish target
default bootstrap. Seven D methods, metadata/durable validation, seeds, paired
constructors/coordinator/subprocess and integrated/native/publication remain
open. The next implementation is the seven remaining reply claim/receipt DAO
methods, with no dormant SMTP activation or policy change.


### Task4D partial reply DAO preparation, 2026-10-14

Candidate group4D builds on accepted832a2ee3. The exact seven converted methods
are `get_email_reply_receipt`, `claim_email_reply_dispatch`,
`get_email_reply_dispatch_claim`, `mark_email_reply_dispatch_uncertain`,
`release_email_reply_dispatch_retryable`,
`recover_terminated_email_reply_dispatch_claims` and
`persist_email_reply_receipt`. Their eight original mutations (two inserts and
six updates) and ten current read literals now use the accepted finite
projections/source-shaped writers. All five writers already owned BEGIN
IMMEDIATE. Each locator SELECT retains the original mutation predicates inside
that transaction; no implicit-write boundary needed changing and no helper
interface expansion was needed. The two source families retain their exact
seventeen plus thirteen fields and public method signatures.

The first source-oracle claim/owner case passed genuine archived2773 code and
failed against the converted copy at the retired receipt-table read. It passed
after conversion. The target uses real EmailStore methods on explicitly pure-
converted owned copies; test-only object setup does not prove a target default
constructor. Source constructors and implementations are unchanged. Existing
account-clock hooks make the snapshot-refresh comparison deterministic without
altering its monotonic update rule. No SMTP delivery/provider harness or native
Agent CLI is involved.

The ten primary D cases cover:

- Claim acquisition, duplicate/same-owner/other-owner replay, immutable effect/
  outgoing-ID conflicts and exact owner/generation/lease CAS, including actual
  transition to uncertain and refusal to reacquire that state.
- Retryable release/reclaim, unchanged original snapshot until a valid retry,
  refreshed account snapshot/version and fresh owner, stale snapshot refusal,
  and source structural ROWID -7 preservation.
- Proved terminated-owner recovery of two exact-owner claims, mismatched owner
  and repeated-recovery rowcounts, and no automatic replay. Source claim ROWIDs
  -7 and0 survive conversion and recovery.
- Receipt insertion/completion, replay, one-way SMTP-ID enrichment, retained
  creation time, conflicting evidence/ID refusal, standalone receipt ROWID0
  and absence of an invented dispatch claim. Native outgoing Message-ID unique
  errors, typed-parent FK errors and existing in-flight parent blockers match
  the source behavior.
- The original JSON-text receipt CAS, including SQL-valid noncanonical JSON
  that parses identically but does not equal the original canonical comparison
  string. Enrichment refuses with the same rowcount/conflict outcome and keeps
  the original text unchanged.
- Receipt-insert failure after a claim-completion fault, and receipt-enrichment
  rollback when a later owner CAS fails. The latter uses an explicitly labeled
  synthetic SQL-valid uncertain-claim/existing-receipt state, independently
  evaluated by the original public source method. A separate two-row recovery
  fault preserves both original claims and all native values/types.
- Positive reply receipts through the shared observation/context read APIs.
  The actual existing-claim path with600 other reply claims plus3000 unrelated
  rows remains4432 to4432 VM steps. EXPLAIN selects the existing typed reply
  identity index with no actions scan; no speculative index is added.

Complete seventeen/thirteen-field native facts, SQL types and source ROWIDs are
compared after behavior scenarios. A separate eleventh confined case tests only
the native SQL NULL-public-key boundary: after a genuine constructor has opened
its valid source, source SQL and accepted target writers permit the original
nullable text public keys. Equality getters find no row for NULL, and public
claim/receipt input validators reject NULL exactly as the original methods do.
This case does not claim that a source or target strict startup validator accepts
those native SQL facts, and it is separate from the primary application proof.

The complete primary targeted invocation, when the D file contained its ten
primary cases and before the separate NULL probe was added, was:

```text
python -m pytest -q tests/test_email_action_reply_dao.py tests/test_email_action_plan_direct_dao.py::test_shared_reply_dispatch_read_guard_still_refuses_feedback tests/test_email_action_plan_direct_dao.py::test_plan_idempotence_feedback_null_prior_replay_and_cas_match_genuine_old tests/test_email_action_unsubscribe_dao.py::test_continuation_effect_chain_journal_terminal_and_readbacks_match_source tests/test_email_action_unsubscribe_dao.py::test_direct_retirement_receipt_promotion_exact_backfill_and_context_match_source --basetemp=/tmp/email13-dao-4d.Oj1bbj/focused-final -s
```

Captured stdout, joined across tool output chunks:

```text
.........reply claim VM steps 4432 -> 4432; 600 same-family plus 3000 unrelated rows
.....
14 passed in 108.05s (0:01:48)
```

The four accepted B/C cases specifically cover the reply-in-flight feedback
guard, feedback/current-plan CAS and mixed observation/context closure. The
separate NULL-boundary invocation passed1 case in11.14s. No full suite or repeat
of the accepted storage/migration/protocol matrices was needed because this
unit changes neither their interfaces nor declarations. Ruff and diff checks
pass; all343 EmailStore signatures and protected source metadata/default
selection remain unchanged. Only the seven named method bodies and two finite
locator constants change in runtime code.

A whole-module scan finds no current EmailStore physical thirteen-family DAO
SQL left. Residual physical SQL is confined to source metadata, historical
migration/bootstrap and startup validation, including `_initialize`, old reply/
unsubscribe migration finishers, retry detach/restore, cached plan validation,
legacy-plan marking, `_create_indexes_and_triggers`, `_validate_durable_state`
and `_validate_durable_rows`. `preview_failed_email_unsubscribe_task` remains an
explicit API dependency on deferred shape validation. These are E/F work along
with seed factoring, fresh constructor order/locking, coordinator/subprocess,
real target markers and final coupled activation. Whole-repository external
consumers still include Store unsubscribe recovery/receipt paths, deploy
maintenance's direct-action readiness query, and audit/History/web consumers;
they remain Task5 and were not modified here. This branch has not absorbed the
separately published History increment; root will preserve it at a clean gate.

Store19/Email51 constructors, selectors and action flags remain unchanged. This
is unpublished partial preparation, not default runtime, complete Task4 or
Task5/6 acceptance. Dormant SMTP remains disabled. No new policy, alternate DAO,
query router/parser, mixin, views/dual storage, provider, native Agent CLI,
production mutation, push or deployment is included. Independent SPEC then
QUALITY review is pending.

Owned4D temporary archives, source/target databases, pytest roots and editing
intermediate were inspected and removed from the exact task root; absence was
verified. No historical test copy is retained.


### Independent Task4D acceptance, 2026-10-14

Candidate2e38c20dca8eac510d91ab64e5a21e9e69dbf47c passed sequential
independent SPEC then QUALITY against832a2ee3. SPEC independently captured
11 passed in57.33s, exit0, all signatures/constants and source-oracle method
AST parity with genuine2773 (allowing only accepted B plan SQL conversion).
QUALITY independently captured11 passed in32.36s, exit0, inspected the full
writer closure and retained caller-owned writer transactions. Both observed
claim4432→4432 VM behavior without a scan or new index.

QUALITY additionally verified SQL NULL identity recovery at source ROWIDs-7
and0: a fault on the second recovery update rolled back both original claims
and timestamps; wrong lease recovered0, correct owner2, replay0, with complete
source/target native facts equal. This is explicitly a native SQL boundary
following valid source construction, not strict startup acceptance of NULL
facts. Both reviewers verified owned temporary roots were absent after cleanup;
HEAD stayed clean. No production/provider/native CLI/publication action occurred.

This accepts only D's seven-method unpublished DAO conversion. E startup shape
and durable validators, current fresh seeds, F paired constructors/coordinator/
subprocess and target allocation, external Task5 consumers and copy/native/
publication gates remain open. Source Store19/Email51/defaults remain selected;
the isolated partial branch is not default-runtime-ready.


### Clean-gate published-main reconciliation after D

After D acceptance, normal mergeee4a0075 preserves published main4eb7abb8
(including startup receipt-index use834b8348, History666f4078 and its readback).
Only appended architecture/runtime/claim documentation conflicted; both sides
were retained. Incoming code does not change EmailStore, action storage/source/
release declarations or either neutral current Email factory. D runtime and
all accepted Email declaration evidence remain bound unchanged; this merge is
not recast as a new actual-copy or native proof.

Eight focused merged checks passed in1.71s, exit0: History pagination and the
receipt-index, failed-current-run and bounded-batch reconciliation tests. The
owned temporary root was cleaned and absence verified. No source/target markers,
constructor/default activation, providers or production database changed.
E target validators/current seeds is the next implementation; F and later gates
remain open. Final publication still requires immediate-predecessor binding.


### Task4E1 current fresh-seed preparation, 2026-10-14

Parent671de0c95d841beec04ecf7e609b00975980bea3. E is split into E1 seeds and
later E2 target validators/metadata; E1 stops for sequential SPEC then QUALITY
before E2 begins. The only changed existing methods are `_migrate_v19_to_v20`
and `_migrate_v33_to_v34`, extracting the original seed blocks into private
helpers. Expanding those calls reproduces both original method ASTs exactly.
The new `_seed_current_fresh_state` is unselected and caller-transactional;
no constructor/default/marker, coordinator, action schema, validator, source
capture or storage helper changes are part of E1.

Preflight examined v35–51: no later migration rewrites category/threshold/config
or description-revision seed facts. The v38→39 promotion-column rename affects
`macro_f1_min` only when present; genuine fresh v34 already creates
`micro_f1_min`. The main independent oracle still runs genuine archived2773
AutoReplyStore and default EmailStore all the way from fresh0→51. With fixed
clocks and deterministic UUIDs it compares every final category-config,
promotion-config and revision row, including JSON text and ordered UUID calls.
The source's populated Email business tables are exactly these three. Final
seed facts match; this is archived fresh-source proof, not a latest actual-copy
or current target-constructor claim. The target setup explicitly calls the
accepted neutral nonaction factory and the new private helper, with no old
migration chain or action/ledger creation.

Additional helper-boundary cases preserve existing configured sets and promotion
rows, add only missing initial category revisions, preserve existing revision
snapshots, and allocate no IDs/clocks or writes on repeat. These cases describe
the new fresh helper's boundary; they do not claim that an already structured
empty table is the historical v19 migration input. Unpatched UUID4 and original
second-precision category/promotion clocks plus the existing monotonic revision
clock are exercised separately. Caller savepoint success/rollback and a native
trigger failure late in revision insertion demonstrate caller-controlled whole
transaction/savepoint rollback without a new commit or recovery layer.

RED: the genuine fresh51 oracle completed, then the target test failed with the
missing `_seed_current_fresh_state` AttributeError (1 failed in2.46s). GREEN
first passed that comparison (1 passed in2.21s). An expanded test initially
exposed the test's microsecond bound mismatch with the existing second-precision
`_now`; the bound was corrected without changing runtime clock behavior.
Final complete captured run, exit0:

```text
python -m pytest -q tests/test_email_current_seeds.py tests/test_email_current_schema.py::test_current_retained_objects_match_archived_native_metadata tests/test_email_current_schema.py::test_current_retained_schema_is_caller_transactional_and_unseeded tests/test_email_store.py::test_v19_category_config_migration_maps_active_rows_and_preserves_history tests/test_email_store.py::test_fresh_category_configs_seed_structured_approved_boundaries --basetemp=/tmp/email13-seeds-e1.XMngdA/final-green -s
............
12 passed in 3.32s
```

Ruff and diff checks passed. Existing v19 legacy/history and structured fresh
category tests and the retained native49-object/unseeded-factory checks passed
alongside eight new seed cases. No full suite, environment mutation, providers,
native Agent CLI, production, push or deploy was used. F must call the new
seeder only on new-database creation, never current reopen, Email51 migration
or a read path to fill empty configuration. E2/F and external Task5/full-copy/
native/publication gates remain open; no default runtime readiness is claimed.

Protected-surface verification found only the two historical migration bodies
changed among existing methods, with exact AST expansion, unchanged existing
signatures/module-level declarations and zero runtime calls of the new entry
point. The exact owned `/tmp/email13-seeds-e1.XMngdA` root was inspected
(1,683 files under red/green/final/final-green and the factoring intermediate),
removed, and verified absent. No historical archive or test database is retained.


### Independent Task4E1 acceptance, 2026-10-14

Candidate25852120dac0bf328eecc38a0887c9efce4bc181 passed sequential
independent SPEC then QUALITY against671de0c9. SPEC independently passed13
scoped cases in3.63s, exit0, then bound both resolved constructor module paths
to its exact genuine2773 archive and compared all9 category/1 promotion/9
revision rows plus10 ordered UUID calls after default fresh0→51 initialization.
Neutral19-table/10-index/19-trigger objects were empty before seeding and
unchanged afterward. The retained native49-object checks remain separate.

QUALITY independently passed23 cases in4.57s, exit0:8 committed cases plus15
review probes comparing parent/candidate SQL traces, clock/UUID events,
transaction state and database dumps. These cover enabled/disabled accounts,
custom/blank descriptions, billing alias precedence, invalid legacy keys,
replace/append ledger branches, empty/full/partial revision history and a
backward clock behind a future-dated revision. Caller rollback after successful
and partially failed migrations remained identical. Both reviewers independently
expanded the two modified historical method ASTs exactly and found zero app
callers of the fresh seeder, with protected declarations/methods unchanged.
Ruff/diff checks passed, worktree stayed clean, and all owned temporary roots
were cleaned with absence verified. No production/provider/native action occurred.

Only unselected fresh seed factoring is accepted. E2 is the single current
target shape/durable-validator conversion. Preserve the original required-table
coverage (16 retained business tables plus ledger, not three additional optional
historical tables); native49-object factory parity is separate from that policy.
The <=48-only cached-plan helper and historical source DDL stay unchanged. The
new target validator is not a compatibility path for those historical upgrades;
F must reject unsupported old sources before mutation under the approved exact
Store19/Email51 source contract. E2/F, Task5 consumers and later current-copy/
native/publication remain open. Fresh seeding must be selected only in F's new
DB creation branch, never reopen/51 migration/read-path configuration repair.


### Task4E2 current metadata and durable validators, 2026-10-14

Parentd79ee9618a0a50511155928807bc56487b992468. Only the three current EmailStore
validators and their imports change, plus a narrow finite external-blocker
interface shared by pure conversion. All original `_REQUIRED_*` maps/keys,
historical cached-plan helper, source DDL, source capture and current-fact
inverse remain unchanged. The existing13 translated SQL declarations are
AST-identical. The fresh seeder still has zero app callers. No constructors,
defaults, selected flags, versions, coordinator or F activation changed.

The current shape validator filters the original source maps to their existing
16 retained business tables plus ledger, preserving each required column/type/
default/declaration, key, CHECK, FK, index and native trigger check. It separately
requires the exact29-kind action manifest for one of the two accepted reply
variants. It selects19 retained native triggers plus13 exact translated external
blockers (32 total). Existing source maps are not repurposed or deleted, so the
neutral retained factory's dynamic trigger imports remain valid. Its49-object
native metadata proof is separate from original startup coverage. Tests remove
the optional historical traversal/operation/candidate tables and confirm both
source and target shape acceptance, avoiding a silent new startup requirement.

All17 active old-family query sites (one retry/direct join in durable-state and
16 SELECTs in durable-rows) now use accepted finite projections. The policy AST
of durable-state and durable-rows is identical to genuine2773 outside SQL
arguments, including all loops, conditions, errors and helper calls. This does
not add a new rule for receipts, account/message FKs, recovery, authorization,
classification or row validation. Native typed storage and application behavior
remain separate: original durable-row validation accepts a SQL NULL reply
receipt identity, while public receipt writing refuses NULL; original durable
validation refuses a nullable feedback ID. The original retry timestamp
ValueError is preserved, distinct from the excessive-attempt count's
EmailPersistenceCorruption. No blanket NULL-startup-refusal is inferred.

New tests use real EmailStore methods on explicit pure-converted owned copies
and genuine archived2773 validators in a separate process/module path. They
compare acceptance and exact refusal type/message for plan JSON/direct states,
feedback, reply owner/snapshot/receipt, unsubscribe effect/continuation/journal/
terminal facts and retry bounds/timestamps. They execute genuine and target
claim→continuation→next-effect→terminal lifecycles and direct/retry/feedback
cycles, validating intermediate/final state. The retry fault corpus is explicitly
native storage, not an assertion of public source-release authorization.

Metadata cases cover required retained table/index/trigger/declaration refusal,
missing/corrupt target action index/trigger/column/typed-parent declaration and
translated blocker predicates. Both existing reply variants pass neutral shape
checks; finite trigger installation is repeatable/caller-savepoint controlled and
preserves a deliberately malformed existing blocker for strict refusal. Target
validation preserves caller changes, native facts and savepoints without writes.
The real public `preview_failed_email_unsubscribe_task` now closes its shape
validation dependency, matches the genuine-source decision and leaves complete
logical facts and database bytes unchanged, without constructing either store.

RED: the archived validator accepted the source, then the target shape test
failed on missing retired tables (1 failed in3.55s). After finite conversion all
three current validator entry points passed (3 passed in7.95s). Expanded oracle
setup exposed the original NULL-receipt acceptance, source continuation-prefix
rules, source AUTO/composite writer namespaces and timestamp ValueError; tests
were corrected to preserve those measured semantics, with no new runtime rules.
Final complete run, exit0:

```text
python -m pytest -q tests/test_email_action_validation.py tests/test_email_current_schema.py::test_current_retained_objects_match_archived_native_metadata tests/test_email_action_storage.py::test_finite_metadata_matches_frozen_source_objects tests/test_email_action_storage.py::test_every_frozen_field_projects_with_native_value_and_storage_class tests/test_email_action_migration.py::test_all_thirteen_families_preserve_current_facts tests/test_email_action_migration.py::test_thirteen_external_blockers_survive_both_transitions tests/test_email_action_migration.py::test_main_rows_and_temp_shadows_stay_separate_through_inverse tests/test_email_action_migration.py::test_caller_can_rollback_forward_after_late_sqlite_failure tests/test_email_action_schema_release.py::test_inverse_uses_current_facts_receipt_backfill_native_ids_and_allocator_waters --basetemp=/tmp/email13-validation-e2.oa6ROt/final -s
.............................
29 passed in 43.02s
```

This is15 new validator cases plus14 justified regressions: retained native49,
frozen metadata/all144 fields' native values/types, all13 current-fact inverse,
13 blockers both transitions, main/temp separation, late conversion rollback
and paired current-fact inverse for both variants. No full suite, environment
mutation, provider/native Agent CLI, production, push or deploy was used.

Remaining physical old-family SQL is confined to `_initialize`,
`_prepare_v8_reply_claim_migration`, `_finish_v8_reply_claim_migration`,
`_finish_v10_unsubscribe_migration`, `_finish_unsubscribe_schema_migration`,
`_ensure_unsubscribe_receipt_columns`, `_migrate_v16_to_v17`,
`_migrate_v18_to_v19`, `_detach_direct_retry_requests`,
`_restore_direct_retry_requests`, `_migrate_v21_to_v22`, `_migrate_v24_to_v25`,
`_migrate_v34_to_v35`, `_validate_cached_action_plan_snapshots`,
`_migrate_v39_to_v40`, `_ensure_unsubscribe_claim_columns`,
`_mark_legacy_processed_without_plan` and `_create_indexes_and_triggers`, plus
historical module declarations. The cached-plan helper remains the native <=48
migration contract. It is not converted or given a compatibility validator.
Active current durable validators have no physical old13 SQL. All other existing
EmailStore method ASTs/signatures and module assignments are unchanged.

The partial branch now has a target-only current validator while default
Store19/Email51 initialization is still historical. Existing default-constructor
and historical-upgrade testfiles therefore are not a passing target-runtime gate;
their expectations were not bypassed or rewritten. F must explicitly refuse
unsupported older source pairs before mutation and select the supported pair,
fresh constructors/locking, coordinator/subprocess and real markers in one
coupled activation. No F work is included. External direct consumers remain
Task5; latest actual-copy binding, native/full-copy and publication acceptance
remain Task6. This archived2773 test is not a current production-copy proof.
E2 stops for sequential independent SPEC then QUALITY review.

Ruff (both changed runtime modules and new tests), test formatting and diff
checks passed. The exact owned `/tmp/email13-validation-e2.oa6ROt` root was
inspected, removed and verified absent. All temporary archived code, owned
source/target databases, pytest runs and the editing intermediate were removed;
no historical test archive is retained.


### Independent Task4E2 acceptance, 2026-10-14

Candidatef1778a7ef9cec587c8fc5806b13922d41dedf7d6 passed sequential
independent SPEC then QUALITY againstd79ee961. SPEC independently captured21
passed in105.19s, then7 passed in7.59s, both exit0: all15 new cases, both-variant
external blockers, MAIN/TEMP isolation, late forward rollback, finite metadata,
all144 native values/types, all13 current-fact inverse/reforward and paired
inverse. Source-required declarations and durable-policy ASTs match genuine2773
outside SQL arguments; only the three current validator methods changed.
Their15-method helper closure has no old-family physical SQL; the18 residual
historical/bootstrap methods are explicitly inventoried. Source capture,
original translated SQL and current-fact inverse stay unchanged.

QUALITY independently captured15 passed in38.02s, exit0, plus successful
isolated counterexamples. It bound the oracle module path and bytes to exact
genuine2773, ran all three validators under a read-only SQLite authorizer with
no attempted writes or changed facts/total_changes, refused extra action index/
trigger objects, and compared exact source/target errors for retained CHECK,
FK delete action and PK corruption. Changed retained partial-key predicates
were refused. Denial on the second trigger creation kept the first inside the
caller savepoint; savepoint/outer rollback restored the original state.
Both reviewers left HEAD clean and verified all owned temporary roots absent.
No live/provider/native CLI/publication operation occurred.

This accepts E2 target validation preparation, including17 retained required
tables and32 required triggers, both exact action variants, public read-only
cancellation preview, and method-specific NULL/error semantics. It does not
accept the current default constructors: these still produce the old layout,
while validators now require Email29. F must couple constructors, locking,
source-pair release/coordinator, real markers and default29 selection and prove
unsupported-source refusal before mutation. Task5 consumers, fresh current-copy/
genuine-old-code/native and publication remain separate unfinished gates.


### Task4F coupled local candidate, 2026-10-14

Parent: `5dd1af7f627198e5777aaf33498b254f8baf18e3`. The local candidate allocates
Store `2026-10-14.1` (program20)/Email52 and selects29-kind actions together.
Published main was checked as `4eb7abb8`; no main merge occurred during this unit.
This is unpublished implementation evidence, pending independent sequential SPEC
then QUALITY. It does not close Task4's full named legacy-test gate, Task5
consumers, latest-source-copy/native acceptance or Tasks6/7 publication.
**The entire branch is not deployable while external old-table callers remain.**

Implementation closure:

- `prepare_database_schema_release` intercepts exactly19/51→20/52 before either
  standalone preparer, returns one paired release and preserves other-target
  refusal behavior. There is no independent52 publisher or composite publication.
- `require_current_email_pair` performs read-only selection before constructor
  cache/WAL/DDL and is repeated under the original path lock. It permits fresh,
  current20/52 and genuine complete Store-first states; exact19/51 requires the
  coordinator, while unsupported older/newer/missing/mixed and old Email-only
  sources refuse. Existing Store bootstrap collision/known-repair policy remains.
- Store `_ensure_initialized`, cache/manifest checks, `_initialize` final suffix
  and `_require_supported_store_bootstrap_source` select29. The earlier legacy
  commits/executescript model stays; immediately after removed-runtime migration,
  canonical30-column classification,29 actions and Store marker share the final
  transaction. Dynamic SQL trace proves the prefix does not execute an actions
  dependency before creation. Failure leaves the existing unversioned core/retry
  refusal; completed Store-first remains valid, including after later OA backfill.
- Email `__init__` invokes genuine public AutoReplyStore once outside Email's
  non-reentrant flock. `_initialize` has one current validation/fresh current
  factory path. Fresh retained19 tables/10 indexes/19 triggers, empty ledger,
  existing seeds,13 translated blockers and52 share its transaction. No historical
  chain/retired13/action_plan_json creation or retired-family ALTER repair runs
  in this path. The original current provider-observation missing-column helper
  remains before validation, preserving its already supported repair.
  Existing empty configs/revision histories stay empty on reopen and migration.
- `publish` always uses the real paired subprocess. `apply` exposes the same
  accepted atomic primitive for child execution and confined sentinel/fault
  tests. The JSON stdin binds prepared Store marker/value/timestamp, complete
  Email ledger, source declarations and FK aggregate; it carries no business rows,
  temporary config file or recaptured source. Legal BLOB timestamps were accepted
  by both genuine2773 constructors and now round-trip through the actual child
  and inverse with original `typeof`/bytes. No marker/ledger policy was tightened.
- Both default classes cold open after atomic conversion. Existing inverse uses
  post-publication current facts and exact original declarations, public IDs,
  native ROWIDs/allocator waters and original marker/ledger values/types. No old
  snapshot is restored. Source maps, historical cached helper and native source
  DDL are unchanged; no new compatibility validator/router/view/dual writer or
  ownership epoch was added.

The47 new actual-default cases cover full-parent FK-on older-kind insertion,
fresh Store-first/Email-first and strict/non-strict reopen, actual classification
and direct writes, opposite-order concurrent processes, exact source coordinator
interception before standalone preparers, full child metadata binding, source
change refusal,10 DELETE-journal/cache refusal states, genuine old Email-only and
unversioned Email collisions, dynamic bootstrap prefix, Store suffix and Email
fresh rollback at parent/actions/seeds/marker/commit boundaries, known-current
repair selection, and child failure before DDL/after capture/during conversion/
after first marker/before commit/process exit43/after each real cold constructor.

Current-fact tests perform real target direct completion→inverse→both genuine old
constructors/public readback→republication with public attempt allocator701 after
original700. Receipt enrichment survives inverse with native receipt ROWID0.
The fingerprint compares sorted complete SQL/data, explicit source ROWIDs,
SQLite `typeof`, every sequence name/value/type and journal mode. The earlier
post-commit fault diagnostic showed identical sorted dump and source ROWIDs but
different catalog emission order; only this test fingerprint was corrected. No
row/fact/type difference was discarded.

Actual RepositoryUpdater tests use an owned local Git remote, the newly loaded
candidate selector and real paired child. They verify stop→prepare→publish→
verification and failure at verification/restart/health/loaded verification;
current receipt enrichment survives inverse before genuine old restart. An
unsupported inverse preserves the target and leaves replacement stopped with
needs_manual, using existing updater behavior. This does not prove selector
reload in an already-running old UI/updater process. No updater/deploy runtime
code changed.

Test changes outside the new file are confined: two obsolete default staging
assertions now expect local20/52/29; old4A sentinel/minimal/native fault tests call
`apply` to state their true primitive boundary; six pipeline cases change12
readonly SQL literals to existing finite source-shaped projections, keeping
assertions/order/columns; one E2 preview fixture now copies the complete genuine
source and uses actual coordinator+child before target public preview. Its
source oracle still executes genuine51, and byte/fact readonly assertions remain.
No production testmode, weakened version guard or manually altered target marker
was used.

Final captured test commands and results (all owned basetemps):

```text
python -m pytest -q tests/test_email_pipeline.py tests/test_email_action_schema_release.py tests/test_email_current_schema.py --basetemp=/tmp/email13-coupled-f.NUHCny/pipeline-protocol
........................................................................ [ 87%]
..........                                                               [100%]
82 passed in 26.45s
exit 0

python -m pytest -q tests/test_email_action_callers.py tests/test_email_action_plan_direct_dao.py tests/test_email_action_unsubscribe_dao.py tests/test_email_action_reply_dao.py tests/test_email_action_validation.py tests/test_email_current_seeds.py tests/test_email_store_startup_validation.py tests/test_store_schema_release.py tests/test_store.py::test_current_schema_reopens_and_repairs_old_route_session_shape tests/test_store.py::test_store_repairs_missing_required_table_despite_current_schema_version tests/test_store.py::test_current_schema_reopens_and_adds_agent_run_recovery_index tests/test_store.py::test_store_restores_wal_for_current_schema_database tests/test_email_store.py::test_real_store_persists_and_reopens_custom_category tests/test_email_action_plan_reference.py::test_current_read_and_full_validation_reject_invalid_reference --basetemp=/tmp/email13-coupled-f.NUHCny/final-targeted
........................................................................ [ 60%]
........................F.......................                         [100%]
FAILED tests/test_email_action_validation.py::test_public_readonly_cancellation_preview_closes_shape_dependency
1 failed, 119 passed in 249.68s (0:04:09)
exit 1

python -m pytest -q tests/test_email_action_validation.py::test_public_readonly_cancellation_preview_closes_shape_dependency --basetemp=/tmp/email13-coupled-f.NUHCny/preview-real-pair
.                                                                        [100%]
1 passed in 5.53s
exit 0
```

The initial focused failure was the obsolete staging fixture's Email51 marker:
`EmailPersistenceCorruption: cancellation requires deployed email schema 52`.
Root approved exactly this fixture closure. The real coordinator fixture rerun
passed; the119 previously passing cases were not repeated, and the initial batch
is not represented as a single120-pass run. All47 new F cases and the other72
selected DAO/E/known-repair cases passed in that initial batch. The whole B/C/D
matrix covers public identity versus physical ID, rowid/attempt highwater,
plan/feedback CAS and NULL prior, receipt JSON/native scalar and conflict policy,
whole-transaction faults, direct poll/dependencies, unsubscribe/reply lifecycle,
no-replay, leases/retry/recovery, and existing typed-index/VM growth assertions.
Earlier accepted measured probes remain direct5751→5752 with3000 unrelated,
unsubscribe4542→4542 and reply4432→4432 with600 same-family+3000 unrelated; this
unit reran their bounded-growth and EXPLAIN assertions without adding indexes.

The complete final stdout/exit was captured and read before cleanup, including
the original52-line failure trace. Initial constructor RED failed the Store-only
expected target marker (old19 versus20); core GREEN passed5, expanded constructor/
fault refusal passed25, binding/BLOB/no-reseed/concurrency passed4, and the final
child/current-fact/old-only selected batch passed12 in61.97s. These earlier
batches are diagnostic evidence, not additional unique-case counts.

Remaining test and consumer navigation follows. No larger matrix, full suite,
provider/native Agent CLI/send, production mutation, environment creation, push,
deploy or VACUUM was performed.


#### Deferred old-SQL test navigation (no bulk edits or skips)
These are static literal-SQL navigation sites, not a claim that each test was
executed or failed. H means historical-source/migration contract: preserve native
SQL and bind genuine old code rather than route old versions through the new
defaults. T means current API/shape/trace or fixture assertions needing a finite
projection/writer adaptation while retaining their business meaning. S is a
shared native table-rebuild helper whose historical callers must stay genuine
and whose current corruption fixtures need separate target setup.
The original Store inventory counts75 function symbols/123 literal attributions
because the enclosing read-view test and nested `mutate_after_first_read` both
attribute one SQL. Nearest-function attribution gives74 symbols/122 unique SQL
literals. Both navigation names are retained below. Reference is5/5 and retry3/6.
Dynamic `business_rows` in the reference file is also an historical native fixture
helper outside this literal detector. Other old schema/PRAGMA assumptions can
exist without a matched literal; this is not an exhaustive whole-suite readiness
claim.

`tests/test_email_store.py` — 75 symbols, 123 attributed SQL literals.

| Source line | Symbol | Follow-up |
| --- | --- | --- |
| 751 | `test_real_store_human_confirmation_persists_plain_string_category` (1) | T |
| 806 | `_replace_feedback_requests_with_v18_table` (2) | H |
| 892 | `test_reopen_rehydrates_legacy_reserved_classification_plan_and_feedback` (2) | H |
| 1806 | `test_backfill_fills_a_missing_entry_url_only_when_it_hashes_to_the_receipt` (2) | T |
| 1976 | `_downgrade_email_database_to_v16` (1) | H |
| 2084 | `_replace_email_action_attempts` (1) | S |
| 2114 | `_replace_email_actions` (2) | S |
| 2600 | `_create_action_with_attempts` (1) | T |
| 3173 | `test_v10_unsubscribe_claim_migrates_to_v11_effect_prefix_chain` (3) | H |
| 3208 | `test_exact_v14_unsubscribe_schema_migrates_with_nullable_positive_audit_run` (4) | H |
| 3336 | `test_v11_unsubscribe_schema_migrates_missing_phase_and_receipt_evidence` (5) | H |
| 3450 | `test_v9_store_atomically_adds_unsubscribe_durability_without_data_loss` (3) | H |
| 3490 | `test_reopen_rejects_feedback_request_linked_to_another_classification` (1) | T |
| 3787 | `test_email_store_migration_is_idempotent` (2) | T |
| 4533 | `test_current_v17_schema_missing_result_integrity_column_fails_without_repair` (1) | T |
| 4592 | `test_audited_unsubscribe_lineage_query_uses_exact_primary_key_chain` (1) | T |
| 4671 | `test_current_schema_missing_required_foreign_key_is_domain_corruption` (1) | T |
| 4824 | `test_v2_processed_without_plan_upgrades_to_explicit_legacy_once` (4) | H |
| 4885 | `test_exact_v15_legacy_action_plan_upgrades_without_rewriting_history` (3) | H |
| 4978 | `test_upgraded_database_does_not_extend_legacy_permission_to_new_v16_plan` (4) | H |
| 5095 | `test_current_v16_missing_authorization_column_fails_without_schema_repair` (1) | T |
| 5131 | `test_current_v16_missing_legacy_provenance_column_fails_without_schema_repair` (2) | T |
| 5176 | `test_v2_upgrade_does_not_reapply_prototype_classification_backfill` (2) | H |
| 5232 | `test_prototype_plan_migration_backfills_plan_and_direct_actions` (3) | H |
| 5274 | `test_migration_failure_rolls_back_and_can_recover` (2) | H |
| 5375 | `test_versioned_task3_upgrade_does_not_run_prototype_backfill` (2) | H |
| 5709 | `test_startup_rejects_normal_processed_classification_without_plan` (2) | T |
| 5749 | `test_pending_feedback_persists_message_and_cursor_without_plan_or_actions` (2) | T |
| 5771 | `test_processed_scan_persists_plan_and_only_direct_action_rows` (2) | T |
| 5811 | `test_exact_scan_replay_is_idempotent` (2) | T |
| 5827 | `test_processed_model_rescan_preserves_business_snapshot_plan_and_actions` (4) | T |
| 5943 | `test_changed_plan_appends_next_version_and_preserves_history` (2) | T |
| 6236 | `test_rescan_updates_locator_without_replacing_final_decision_or_plan` (2) | T |
| 6282 | `test_rescan_only_updates_mutable_locator_and_preserves_business_snapshot` (4) | T |
| 6702 | `test_action_attempts_append_and_duplicate_or_invalid_values_are_rejected` (3) | T |
| 6782 | `test_direct_action_claim_does_not_materialize_settled_history` (1) | T |
| 6832 | `test_direct_action_probe_does_not_load_retired_pending_plan_bodies` (1) | T |
| 6870 | `test_idle_direct_action_poll_does_not_wait_for_writer` (2) | T |
| 6908 | `test_direct_action_probe_is_not_a_reservation` (1) | T |
| 6930 | `test_claim_direct_action_uses_current_immutable_plan_and_stable_locator` (1) | T |
| 7142 | `test_concurrent_direct_action_claim_has_one_winner` (1) | T |
| 7165 | `test_processing_action_blocks_concurrent_sibling_claims` (1) | T |
| 7207 | `test_processing_action_blocks_only_its_own_classification` (2) | T |
| 7462 | `test_explicit_historical_move_dependencies_gate_exact_and_global_claims` (1) | T |
| 7559 | `test_explicit_historical_trash_dependency_gates_exact_and_global_claims` (1) | T |
| 7711 | `test_claim_rejects_cycle_between_explicit_and_provider_safe_dependencies` (1) | T |
| 7952 | `test_v21_schema_migrates_to_allow_flag_important_actions` (1) | H |
| 7990 | `test_complete_direct_action_appends_attempt_and_updates_current_atomically` (1) | T |
| 8160 | `test_direct_action_completion_rolls_back_attempt_and_current_state_together` (1) | T |
| 8275 | `test_startup_rejects_pending_action_with_historical_terminal_attempt` (1) | T |
| 8303 | `test_startup_rejects_action_attempt_count_mismatch` (1) | T |
| 8325 | `test_startup_rejects_gap_in_action_attempt_numbers` (1) | T |
| 8337 | `test_startup_rejects_latest_attempt_receipt_or_status_mismatch` (1) | T |
| 8350 | `test_startup_rejects_terminal_action_without_attempt` (1) | T |
| 8365 | `test_processing_action_may_follow_terminal_attempts_but_has_no_terminal_receipt` (1) | T |
| 8385 | `test_atomic_scan_persistence_rolls_back_message_plan_actions_and_cursor` (2) | T |
| 8428 | `test_corrupt_historical_action_plan_is_rejected_on_open` (1) | T |
| 8530 | `test_startup_rejects_noncanonical_or_unsafe_unsubscribe_result_text` (1) | T |
| 8557 | `test_startup_rejects_wrong_untruncated_unsubscribe_observation_digest` (1) | T |
| 8586 | `test_startup_rejects_empty_nonempty_unsubscribe_digest_mismatch` (1) | T |
| 8614 | `test_unsubscribe_result_read_fails_closed_after_durable_row_corruption` (1) | T |
| 8672 | `test_exact_16kib_untruncated_result_requires_matching_observation_digest` (1) | T |
| 8693 | `test_stored_bounded_result_digest_is_always_verified` (1) | T |
| 8835 | `test_v16_migration_rejects_corrupt_untruncated_observation_digest` (1) | H |
| 8906 | `test_unsubscribe_terminal_snapshot_rejects_step_effect_digest_tamper` (1) | T |
| 8927 | `test_unsubscribe_terminal_snapshot_uses_one_sqlite_read_view` (1) | T |
| 8938 | `mutate_after_first_read` (1) | T |
| 9031 | `test_unsubscribe_state_snapshot_rejects_row_33_before_effect_decoding` (1) | T |
| 9190 | `test_explicit_retry_releases_failed_unsubscribe_without_browser_state` (2) | T |
| 9312 | `test_startup_rejects_forged_durable_unsubscribe_action_identity` (1) | T |
| 9427 | `test_direct_action_parameters_must_match_immutable_plan` (1) | T |
| 10912 | `test_a_skipped_attempt_is_terminal_and_schedules_no_retry` (1) | T |
| 10963 | `test_exhausted_interrupted_claim_recovers_after_retry_ceiling` (1) | T |
| 11158 | `test_a_training_label_correction_never_moves_or_trashes_the_message` (1) | T |
| 11381 | `test_flagged_important_between_lists_only_done_flags_in_the_window` (1) | T |

`tests/test_email_action_plan_reference.py` — 5 symbols, 5 attributed SQL literals.

| Source line | Symbol | Follow-up |
| --- | --- | --- |
| 21 | `rewind48` (1) | H |
| 29 | `test_stable_classification_existence_does_not_hydrate_corrupt_plan` (1) | T |
| 56 | `test_v48_reference_migration_preserves_business_and_api` (1) | H |
| 108 | `test_list_hydrates_canonical_plans_in_one_query_and_connection` (1) | T |
| 134 | `test_current_reference_survives_correction_feedback_and_scan_retry` (1) | T |

`tests/test_email_retry_schema_release.py` — 3 symbols, 6 attributed SQL literals.

| Source line | Symbol | Follow-up |
| --- | --- | --- |
| 13 | `_schema50` (1) | H |
| 45 | `test_schema51_inverse_refuses_to_discard_request` (2) | H |
| 68 | `test_retry_request_cannot_be_replaced` (3) | T |


No current business assertion was removed to obtain green results. Arbitrary
<=48/50 historical constructor upgrades are deliberately unsupported by the
new defaults; historical contracts remain native/genuine-old evidence. Current
metadata corruption tests must target29 generated/typed constraints rather than
pretend that a retired physical table still exists. This adaptation remains an
open test gate; the82-pass pipeline batch does not imply those files are green.

#### Task5 and later gates still open

A fresh literal-query scan still finds native old-family consumers in:

- Store `_compact_settled_reply_task_input`, `_runtime_operation_parent_exists`,
  `begin_direct_email_unsubscribe_execution`,
  `settle_direct_email_unsubscribe_execution`,
  `retry_failed_effect_free_email_unsubscribe_tasks`,
  `reconcile_failed_email_unsubscribe_tasks_with_terminal_receipts`,
  `reconcile_done_reply_tasks_with_failed_current_run` and
  `_operation_logs_base_query`.
- `app/deploy_maintenance.py::check_maintenance` and
  `app/audit_web.py::_email_action_queue_snapshot`/`_queue_attention_rows`.

Historical `app/email_schema_release.py` native contracts remain available but
are bypassed by this exact paired coordinator transition. In EmailStore itself,
remaining native13 SELECT/DML literals belong only to historical migrations,
the historical cached-plan helper and source DDL/index/trigger construction;
none belongs to the selected current initializer/DAO/validator. Module metadata
maps retain source names intentionally. Task5 still must verify dynamic table
presence/context/web/worker/deployment-readiness callers and the Console surfaces,
including no-replay and truthful native-source availability. The old already-
running updater selector reload boundary is not covered by the new-selector
RepositoryUpdater tests. No compatibility fallback was added for these callers.

Latest production-copy/source-ref binding, all144 original-field/typeof/native
ID/sequence comparisons on that copy, genuine native Consumer/Audit fake-provider
cases and CI/publication/live readback remain separate Tasks6/7. The genuine2773
synthetic/source oracle here is not a claim of a current4eb production-copy proof.


Final hygiene: scoped Ruff on the four changed runtime modules and five changed
Python test files reported `All checks passed!`; `git diff --check` exited0.
The resolved owned temporary root `/private/tmp/email13-coupled-f.NUHCny` (2.4GB,
including test databases/archived old code/local remotes/stdout) was explicitly
removed after readback. Both it and `/tmp/email13-coupled-f.NUHCny` were verified
absent. No historical test backups or copied source archives were retained.
The candidate commit stops for root's sequential independent SPEC then QUALITY;
no Task5 work is started.


#### Final existing Email current-repair correction

Before freezing the commit, root identified that the new current initializer had
omitted the predecessor's `_ensure_provider_observation_signal_column` call.
A narrow genuine2773 counterexample legally drops the existing retained-table
`important_signals_json` column. Genuine Email51 reopens and repairs it; the
initial candidate52 refused with `email_provider_observations is missing required
columns: important_signals_json`. This was a runtime regression, distinct from
the E2 staging fixture failure above. The candidate now retains that exact one
existing helper call before current shape/account/row validation, with no new
repair or stricter gate.

The added48th F test compares complete repaired PRAGMA column metadata to the
actual old default constructor. It publishes the same source through the real
coordinator/child, drops the column again and exercises both `validate_rows=False`
and default strict target reopen, preserving all marker/ledger values and exact29
shape. The initial47 F cases and119 passes were not rerun. Only this regression
and the directly related two startup-validation cases ran after this correction:

```text
python -m pytest -q tests/test_email_action_callers.py::test_current_provider_observation_signal_repair_matches_genuine51 --basetemp=/tmp/email13-f-repair.CbfCsw/red
FAILED tests/test_email_action_callers.py::test_current_provider_observation_signal_repair_matches_genuine51
1 failed in 5.60s
exit 1

python -m pytest -q tests/test_email_action_callers.py::test_current_provider_observation_signal_repair_matches_genuine51 tests/test_email_store_startup_validation.py --basetemp=/tmp/email13-f-repair.CbfCsw/green
...                                                                      [100%]
3 passed in 6.09s
exit 0
```

The final evidence is therefore82 passing pipeline/protocol/current-schema cases,
119 passing focused cases with one obsolete staging fixture initially failing,
that exact fixture's1-pass real-publish rerun, and3 passing final repair/startup
cases. It is not a fictional all-pass monolithic rerun. The new current-repair
case is the only additional implementation/test after the first cleanup receipt.


Cached Store health now includes the selected29 action manifest as well as the
existing feedback guard. The marker-only pair preflight cannot prove this shape,
and a cache hit otherwise skips the Store manifest/initializer entirely. This
implements F's current-reopen29 requirement; a missing allowed metadata object
falls through to the existing known-repair path. It is stricter than the old
feedback-only cache check and adds schema-only checks for up to the two existing
reply variants on each hit. It adds no durable-row scan, but repeated-constructor
latency has not been benchmarked; its exact scope/cost is a QUALITY review point.

After the final narrow repair run, scoped Ruff again reported `All checks passed!`
and diff check exited0. `/private/tmp/email13-f-repair.CbfCsw` and its `/tmp` alias
were removed explicitly and verified absent; the earlier main F root also remains
absent. No test processes, copied archives or historical backups remain owned by
this unit. The frozen handoff contains48 F tests in total: the first47 passed in
the initial focused batch, and the added single current-repair case passed in the
final3-case batch. The two validation modes in that case are not counted as two
parameterized tests.

### Task5A finite external consumers, local candidate

Task5A converts active Store unsubscribe receipt/recovery, History (including
the PR87 failed-candidate fast path), audit queue/Attention, quality memory
queue, deployment maintenance and exact current publication readers. It uses
existing typed Email projections and writes. `validated_reply_variant` and
`reply_schema_is_current` keep their Store19 default; only the two active memory
queue callers and the selected Store20 publication branches explicitly request
Email29 validation. Store20-first remains valid for generic publication readers,
whereas Email runtime availability requires Email52 and the Email-specific quiet
reader accepts only exact 19/51 or 20/52 pairs. Historical
`publication_postfix_sources` has no active `app/` caller and remains unselected;
`publication_run_sources` has no version boundary to extend.

The source/target History oracle archives the actual published-main `0441cf15`
`app/` reader under an owned test temp root, creates a valid Store19/Email51
source, then uses the real paired child migration to compare full rows,
failed-only rows, count, one-row pages, detail and mixed 16/Email ordering against
Store20/Email52. An independent signed-ROWID case inserts failed target actions
at -7 and 42 with NULL public action IDs: History retains
`email-action:-7`/`email-action:42` and matching `source_id`; Attention retains
both rows. That target SQL boundary is separate from genuine old-source strict
constructor/source oracle behavior; no historical markers, validators or source
rows were altered to force a false NULL migration success.

Current-plan direct polling refuses the retry before its due time, claims after
the window, and excludes a retired plan. With 1,200 unrelated sent-reply actions,
the poll plan searches `idx_actions_email_public_key (record_type=?)` then the
classification primary key; Attention uses the same typed index and primary-key
lookup. Neither scans classifications. Measured progress-handler count was 0
1,000-step buckets for each query (fewer than 1,000 VM steps), below the 300,000
limit. This is a bounded synthetic plan check, not a production latency claim.

Focused RED cases initially failed on old physical reads/current validators.
After conversion, the scoped command over `test_email_action_external_consumers.py`,
`test_email_action_attention_query_plan.py`, `test_store_history_pagination.py`,
`test_store.py`'s direct parent and retry cases, and `test_audit_web.py` reported
`355 passed in 98.23s`, exit 0. After a lint-only fixture registration change,
the new external-consumer file reran `10 passed in 24.65s`, exit 0. Each run
owned and removed its `/tmp/email13-task5a-*` root. This is not the full named
Task5 matrix: broader old fixture H/T/S conversion, console/worker/CLI/direct
delivery/reconcile tests and Task4/5 final acceptance remain open. Task6/7
copy/native/CI/publication/live readback also remain open; this branch is not
ready for deployment.


### Task5B0 original Email metadata-refusal restoration, 2026-10-14

Parent: `7dc87561536c55ebd1c6f1f9411f6029fe779eb3`, after accepted Task5A. Genuine
source binding is published `0441cf15fafe800f9ca4d67f667e87062ca62109`, archived
unmodified under an owned temporary root and executed in a separate process.
Its actual default constructors create Store2026-10-13.5/Email51. The source is
synthetic fresh data, not a current production-copy/native acceptance claim.

Root found a parent-F regression: Store's generic29 factory inherited the old
Store missing-auxiliary repair permission and could recreate missing Email
indexes/triggers before the Email validator saw the defect. This broadened the
original Email contract. The preflight probe confirmed all five original required
independent indexes and five action-owned required triggers are refused by genuine
0441 Email51 with EmailPersistenceCorruption; each missing object remained missing,
DB bytes were identical and DELETE journal stayed DELETE. Before the correction,
the target automatically recreated all32 Email-only auxiliary objects one by one
and changed DELETE to WAL. This was runtime behavior, not a stale-test expectation.

The correction is narrowly scoped:

- `email_action_auxiliary_objects_are_current` uses the existing cached canonical
  declarations: same-variant29 objects minus16 object names. It compares complete
  canonical object dictionaries (type/name/table/SQL) against actual actions-owned
  metadata. Both accepted reply variants produce the same32 complete objects.
- Email's constructor makes this read-only check after existing pair selection
  and before the genuine AutoReplyStore call. Missing/malformed Email auxiliaries
  raise the original EmailPersistenceCorruption domain error. Fresh databases
  without actions skip this check; normal/Store-first construction still invokes
  the genuine Store exactly once outside Email's path flock.
- Store's original current-known-repair permission requires this same Email
  subset to be intact before its existing auxiliary repair path. Direct Store
  refusal uses its existing sqlite3.IntegrityError source-refusal boundary. Its
  normal cached path retains the existing full manifest check; there is no new
  cache layer, ownership epoch or broad validation gate.
- Shared names `actions` and `idx_actions_public_id` are outside the difference.
  The public-ID index and prior16 auxiliaries retain Store's existing repair
  treatment. The generic create_action_schema capability, codec, DDL, writers,
  source/inverse and all Task5A explicit include_email consumers are unchanged.

Object classification is exact, not a new business rule inferred from names.
Of the25 indexes,24 translate the finite native indexes/PK/unique constraints;
five are the independently droppable required source indexes. Their mapping is:

| Original required index | Target index |
| --- | --- |
| idx_email_actions_classification_plan | idx_actions_email_email_actions_0 |
| idx_email_actions_status | idx_actions_email_email_actions_1 |
| idx_email_reply_dispatch_claims_status | idx_actions_email_email_reply_dispatch_claims_0 |
| idx_email_unsubscribe_claims_status | idx_actions_email_email_unsubscribe_claims_0 |
| idx_email_unsubscribe_receipts_classification_action | idx_actions_email_email_unsubscribe_receipts_0 |

The other19 native PK/unique indexes cannot be dropped independently in native
SQLite, so no fabricated native counterexample was used. The extra public-key
index enforces the accepted target representation. Five of the7 triggers are the
exact original required attempt-count insert/update and explicit-retry immutable
insert/update/delete translations. Two sequence triggers represent the original
attempt/step AUTO allocator semantics. These extra structural objects are required
by the already accepted E2 canonical target manifest; this correction does not
claim the original source had same-named triggers or invent new business policy.
The13 translated external and19 retained triggers remain under their existing
Email validator and are not widened into a new Store repair/refusal policy.

`tests/test_email_action_metadata_repair.py` binds the genuine source independently,
checks both reply variants' complete delta metadata, and deletes every one of the
32 target objects. For each object it exercises cold/cached Store paths, Email
default/validate_rows=False, Store→Email and Email→Store. Both calls refuse;
complete sorted dump, journal and DB bytes stay identical, covering facts and
markers as well as metadata. Four further same-name corruption cases distinguish
index SQL, index owner, wrong object type and trigger SQL; names alone cannot pass.
The shared Store public-ID index still repairs successfully.

The first RED case stopped because AutoReplyStore incorrectly accepted and repaired
a missing target auxiliary. The full native probe had independently reproduced
the known status/receipt index regressions as well as the entire32-object repair
expansion. Actual captured pytest stdout/exit:

```text
python -m pytest -q tests/test_email_action_metadata_repair.py -k missing_never -x --basetemp=/tmp/email13-5b0.aRQoJq/red
FAILED tests/test_email_action_metadata_repair.py::test_email_auxiliary_missing_never_becomes_store_repair[idx_actions_email_email_action_attempts_0]
1 failed, 2 deselected in 3.94s
exit 1

python -m pytest -q tests/test_email_action_metadata_repair.py --basetemp=/tmp/email13-5b0.aRQoJq/green
..................................                                       [100%]
34 passed in 35.73s
exit 0

python -m pytest -q tests/test_email_action_metadata_repair.py::test_email_auxiliary_type_owner_and_sql_are_compared_before_store tests/test_email_action_metadata_repair.py::test_shared_store_public_index_still_uses_existing_repair tests/test_email_action_callers.py::test_real_fresh_orders_current_reopen_and_classification_action tests/test_email_action_callers.py::test_email_first_calls_real_store_once_outside_email_lock tests/test_email_action_callers.py::test_real_coordinator_selects_one_pair_and_real_subprocess_opens_both tests/test_email_action_callers.py::test_real_target_direct_facts_inverse_genuine_old_reopen_and_republish tests/test_email_action_callers.py::test_current_known_repair_variant_selection_keeps_original_accepted_path tests/test_email_action_callers.py::test_current_provider_observation_signal_repair_matches_genuine51 tests/test_store.py::test_current_schema_reopens_and_repairs_old_route_session_shape tests/test_feedback_processing.py::test_feedback_round_backfill_self_heals_current_schema_and_interruption --basetemp=/tmp/email13-5b0.aRQoJq/positive
.............                                                            [100%]
13 passed in 21.27s
exit 0

python -m pytest -q tests/test_email_action_metadata_repair.py::test_email_auxiliary_type_owner_and_sql_are_compared_before_store[index_type] --basetemp=/tmp/email13-5b0.aRQoJq/type
.                                                                        [100%]
1 passed in 3.91s
exit 0
```

The final wrong-type case was added after the13-case run and tested separately;
these are48 distinct passing selections across three batches, not a claimed
monolithic48-pass run. Positive coverage preserves shared/old reply recovery-index,
route-session and feedback current repair, the original Email provider-column
repair, actual fresh two orders and once-only unlocked Store invocation, actual
paired child/current-fact inverse, genuine old strict reopen and republication.
No full48-F/355-Task5A matrix, H/T/S adaptation,5B1, native Agent CLI, provider,
production, CI, environment changes, push or deployment was performed. The branch
remains unpublished and broader Task5/6/7 acceptance is open. This unit stops for
fresh independent sequential SPEC then QUALITY review.


Final hygiene: scoped Ruff over the three changed runtime modules and new test
reported `All checks passed!`; diff check exited0. Complete RED/GREEN stdout and
preflight metadata probe output were read before cleanup. The explicit resolved
owned root `/private/tmp/email13-5b0.aRQoJq` and its `/tmp/email13-5b0.aRQoJq` alias
were removed and verified absent, including all archived code, DB copies and logs.
No task-owned historical source copy or backup was retained. No other tree or
publication worktree was modified. Commit is frozen for root's fresh SPEC then
fresh QUALITY; no5B1 or broader fixture adaptation follows in this unit.

### Task5B1: six current scan/plan test reads

After accepted 5B0, six adjacent current-behavior tests were selected: five
pending-feedback/processed-scan/replay/rescan/plan-version cases in
`test_email_store.py`, and the current correction/feedback/scan-retry case in
`test_email_action_plan_reference.py`. The original six ran RED with
`6 failed in 3.27s`, exit 1: each reached `sqlite3.OperationalError: no such
table: email_action_plans` after its business operation. Only their direct
plan/action SELECTs now use the existing source-shaped `EMAIL_PLAN_SQL` and
`EMAIL_DIRECT_ACTION_SQL` projections. The selected columns, ordering,
`sqlite3.Row`/full-dictionary comparisons, zero/one-row checks, direct-only
types and statuses, immutable rescan snapshot, plan versions and all public
correction/feedback/replay assertions remain unchanged. `_fetchall` and runtime
code are unchanged.

The same six selected tests then reported `6 passed in 2.61s`, exit 0.
Each test run owned and removed its `/tmp/email13-task5b1-*` root, verified
absent afterward. Historical H, shared native-rebuild S and other current T
fixtures remain deferred; this unit is not a full Email Task5 acceptance or
publication gate.

### Task5B1 independent acceptance, 2026-10-14

Root accepts8989d5d4e0dcd550a96a7b513f52538f0076afdf over63eefeb023ae60706142c691c434908d531595ed for only six current scan/plan/reference tests. Fresh SPEC independently ran the exact six cases:6 passed2.65s, scoped Ruff and commit whitespace exit0, clean frozen HEAD and owned temp absent. Subsequent fresh QUALITY inspected actual projections and used an AST comparison reversing only two imports and13 finite SELECT substitutions: both entire test-file ASTs exactly matched the parent, confirming all assertions/helpers/surrounding tests unchanged. QUALITY did not repeat already sufficient six-case execution and created no temporary files. Both reviewers PASS. No runtime change, historical/shared fixture adaptation, broad gate, latest-copy/native proof, CI or deployment is implied.

### Task5B2: fourteen current direct-read tests

The next bounded batch selected fourteen current tests in `test_email_store.py`:
human-confirmed category, current idempotent reopen, two rescan snapshots,
probe/claim/concurrent sibling behavior, exact historical-action dependencies,
completed and skipped attempts, interrupted-claim retry, and training-label
correction. Although one test retains `migration` in its name, its setup uses
fresh current EmailStore and repeats only current constructor opens; it has no
old marker or historical migration path. The unmodified fourteen ran RED with
`14 failed in 5.14s`, exit 1, at their direct reads of retired plan/action
tables. Their selected reads now use the existing `EMAIL_PLAN_SQL` and
`EMAIL_DIRECT_ACTION_SQL` source-shaped projections. Original columns, filters,
order, counts under the same logical table names, complete row dictionaries,
claim/attempt/dependency/retry behavior and every assertion remain intact.
No helper or runtime source changed; an accidental draft hunk in an unrelated
historical test was removed before GREEN and is absent from the final diff.

The same fourteen then reported `14 passed in 5.07s`, exit 0. Both runs owned
and removed their `/tmp/email13-task5b2-*` root, verified absent afterward.
Historical H, shared rebuild S, current mutation/metadata T and the selected
Task5/6/7 final gates remain open; this local test alignment is not deployable
acceptance.

### Task5B2 independent acceptance, 2026-10-14

Root accepts e9d38852c6a22fdac64bf5afcdf47a50c59baef4 over6dc0b946d7eaba5469950e29d962e8ea13e53d82. Fresh SPEC independently ran exactly14 cases:14 passed11.80s, lint/diff exit0, all nonselected AST nodes unchanged, owned temp removed. Fresh QUALITY inspected all14 bodies and verified reversing only21 finite projection substitutions and the count-map adaptation makes the entire module AST identical to its parent. No assertions/helpers/H/S/otherT changes; source aliases/order/direct record-type filters retain row identity and count semantics. QUALITY did not rerun sufficient existing execution and created no temps. Both reviewers verified clean frozen HEAD and PASS. This only accepts current reads; fixture mutations, corruption/history tests, dynamic SQL sites, broader gates, latest-copy/native/CI/publication and full10+1 remain open.

### Task5B3: six current fixture and poll tests

The selected six test symbols expand to nine cases, including four idle-poll
parameters. Their unchanged form ran RED: seven failed at direct SQL against
retired `email_actions` or `email_unsubscribe_receipts`, two passed, pytest
exit 1. Only those six bodies now obtain all matching source ROWIDs through
the existing finite locator projections, then use `update_email_action` to
set the same status, timestamps or receipt URL. The receipt read uses its
source-shaped projection. The source ROWID is never the public action ID or
physical `actions.id`; each original predicate, parameter, complete selected
row set, poll/claim behavior and business assertion remains. The retired-plan
probe keeps its SQLite authorizer assertion, pointed at the exact generated
`actions.email_actions_json` column for the plan body; no trace substitute was
added. These synthetic done/failed fixtures serve poll/report scenarios and
are not claimed as strict cold-reopen-valid persisted histories.

After adjusting two native SQLite tuple-row locator reads, the final selected
run reported `9 passed in 4.38s`, pytest exit 0. Scoped Ruff and diff-check
passed. Each run removed and verified absence of its own
`/tmp/email13-task5b3-*` temporary root. Historical H, shared rebuild S,
other current T and the two dynamic four-table cursor-count tests remain
deferred; this is not full Task5 or publication acceptance.

### Task5B3 independent acceptance, 2026-10-14

Root accepts53e75043468070ebb374425de44847a56424258b overd369932df7835ad006ad2497320451f43901493f. Fresh SPEC independently ran9 cases:9 passed3.94s, scoped lint/diff exit0; exact six tests/assertions and sourceROWID identity checked, own temp removed. Subsequent fresh QUALITY inspected complete matching row sets, sourceROWID versus public/physical identity, unchanged predicate/value/transaction behavior and existing generated plan-column authorizer; independent AST verifies assertions/decorators and all surrounding nonimport code unchanged. It did not repeat sufficient tests or create temps. Both PASS and frozen cleanHEAD verified. Synthetic poll/report states remain scoped fixtures, not strict cold-valid histories. Remaining corruption/metadata/history/dynamic SQL tests and broader copy/native/CI/publication/full10+1 gates are open.

### Task5B4: ten current corruption and rejection tests

Ten selected symbols expand to thirteen cases. The unmodified cases ran RED:
`13 failed in 4.59s`, pytest exit 1, each at direct SQL against a retired
Email action family. The selected bodies now resolve all rows matching their
original predicates via finite source-shaped projections. Plan, direct-action
and unsubscribe-receipt mutations use the corresponding source ROWID with
`update_email_action`; unsubscribe-step mutation uses its original public
AUTOINCREMENT `id` with `update_action`, never physical `actions.id`. Exact
stored strings, digest bytes, 16 KiB boundary, plan JSON, action parameters
and `action_identity` conditions are unchanged. The invalid `discarded` status
must still raise SQLite `IntegrityError` from the target CHECK, while all
business-corruption read/open errors retain their original types and match
assertions. No target trigger was disabled.

The selected GREEN run reported `13 passed in 4.79s`, pytest exit 0. The
nonselected module AST is identical to its parent after excluding imports and
the ten named functions. Scoped Ruff and diff-check pass; RED and GREEN each
removed and verified absence of their `/tmp/email13-task5b4-*` root. The
shared `_create_action_with_attempts` helper, attempt-count trigger fixtures,
metadata rebuild S, historical H, composite effect/retry fixtures, other
current T and two dynamic cursor-count tests remain deferred. This is not a
full Task5 or publication gate.

### Task5B4 independent acceptance, 2026-10-14

Root accepts0a41ea4b92dbcfcf225cfc21f1bf3fea3bed7037 over96b0faa21ec2933188fc6fd0c65ea17fc7547787. Fresh SPEC independently ran13 cases:13 passed4.58s, lint/diff exit0, exact original predicates/all matchingrows/sourceROWID or publicAUTOINCREMENT id and unchanged assertions/raises/params verified; owned temp absent. Subsequent fresh QUALITY confirmed each mutation reaches the intended source field without normalization/business validation, illegal discarded status still hits SQLite CHECK, and application errors retain their original corruption meanings including16KiB/digest/effect binding. Independent AST verifies only the ten tests/imports changed and all assertions/raises/decorators and surrounding tests preserved. It did not repeat sufficient execution or create temps. Both PASS and clean frozen HEAD. Trigger-count/remaining current/history/metadata/dynamic SQL, broad gate, latest-copy/native/CI/publication and full10+1 remain open.

### Task5B5: eight current action lifecycle corruption tests

The selected eight tests ran unchanged RED: `8 failed in 3.31s`, pytest exit
1, all at direct access to retired Email action tables. The only shared
helper changed here, `_create_action_with_attempts`, has exactly six callers,
all selected current fresh-EmailStore tests; it now reads the same public
action ID through `EMAIL_DIRECT_ACTION_SQL` before using the unchanged attempt
API. Each selected native action or feedback update retains its exact original
predicate and field values, resolving all matching source ROWIDs through a
finite locator before `update_email_action`. Attempt number 2 is selected
through `EMAIL_ATTEMPT_SQL` and updated by its native AUTOINCREMENT public
`id` with `update_action`, never the physical `actions.id`.

Only the two original attempt-count drift fixtures temporarily drop the exact
target `trg_actions_email_attempt_count_update` trigger. Each reads the current
trigger SQL from `sqlite_schema` before dropping it and recreates that same
definition after injecting the original drift. All other target triggers
remain enabled. The source statuses, receipts, retry-in-progress state,
feedback mislink, valid dependency-cycle JSON, strict reopen acceptance or
rejection, and original exception text assertions remain. The final selected
run reported `8 passed in 3.39s`, pytest exit 0; nonselected module AST is
unchanged, scoped Ruff and diff-check pass, and both run-owned
`/tmp/email13-task5b5-*` roots were removed and verified absent.

Transaction fault injection, dynamic cursor counts, historical H, shared
native rebuild S, composite effect/retry and other current T tests remain
deferred; this is not full Task5 or publication acceptance.

### Task5B5 independent acceptance, 2026-10-14

Root accepts2eb474032f4d878427685fae58da010c2b257a6d over492be677d320e3547f2d975b553915721e8bd662. Fresh SPEC independently ran8 cases:8 passed3.89s, lint/diff exit0, unchanged assertions/raises and nonselected AST verified, six helper callers exactly selected current tests; owned temp absent. Subsequent fresh QUALITY checked native identity/all matchingrows, attempt gap[1,3], exact two count-trigger capture/drop/restore, SQL-legal business-corruption values, valid processing retry, feedback mislink and acyclic rejection. It did not repeat sufficient tests or create temps. Both PASS and frozen cleanHEAD confirmed. Remaining dynamic/transaction/query-view, metadata/shared/history/composite fixtures, broader copy/native/CI/publication and full10+1 remain open.

### Task5B6: seven current count, fault, reference and read-view tests

The seven unchanged selected tests ran RED: `7 failed in 7.14s`, pytest
exit 1, at old physical Email action SQL or a retired trace substring. The
two cursor tests keep their four original logical table keys and compare all
four counts before and after stale writes; only plan and direct-action value
sources now use `EMAIL_PLAN_SQL` and `EMAIL_DIRECT_ACTION_SQL`. Their cursor,
stale classification and rollback assertions remain intact. Fault-injection
triggers now attach to `actions` only when `new.record_type` is respectively
`email_actions` or `email_action_attempts`, so the original exception text and
message/classification/plan/action/cursor rollback and claimed-action state
checks cover the same failure point without intercepting other action kinds.

The reference corruption fixture updates all plan source ROWIDs matching its
original classification predicate with the same version increment. The list
trace identifies the actual finite `EMAIL_PLAN_SQL` plus its current-plan
`IN (select value from json_each(...))` query, preserving one connection,
one plan query, twenty rows and their plan IDs. The nested read-view writer
updates every unsubscribe-step public AUTOINCREMENT ID matching the original
action identity via `update_action`; the first snapshot still sees its
consistent prior state and the following read still rejects the changed
effect digest.

The selected GREEN run reported `7 passed in 3.47s`, pytest exit 0. Both
test files have identical nonselected ASTs to their parent after excluding
imports and the seven selected functions. Scoped Ruff and diff-check pass;
RED and GREEN each removed and verified absence of their own
`/tmp/email13-task5b6-*` roots. Historical H, shared metadata rebuild S,
composite effect/retry and remaining current T tests are still deferred;
this is not full Task5 or publication acceptance.

### Task5B6 QUALITY P2 repair: count all finite plan reads

Fresh QUALITY found that the first selector counted only finite plan queries
already containing the batch `json_each` predicate. An additional point read
through the same `EMAIL_PLAN_SQL` projection would have been filtered out and
could falsely satisfy the one-query assertion. A focused regression now
executes the real `_current_action_plan_rows` batch read followed by a point
read on the same SQLite connection and trace. The original batch-only selector
and an intermediate `SELECT *` prefix selector each ran RED with `Failed: DID
NOT RAISE AssertionError`, pytest exit 1. The point read selects only
`action_plan_id`, proving that outer-column selection cannot hide the read.
The final selector counts every trace query containing the full finite
`from ({EMAIL_PLAN_SQL})` source fragment, asserts there is exactly one, and
only then verifies that sole query has the batch `json_each` predicate. The
new regression and original one-query/connection test ran GREEN:
`2 passed in 1.94s`, pytest exit 0. Scoped Ruff and diff-check passed;
all run-owned `/tmp/email13-task5b6-p2-*` roots were removed and
verified absent. This repairs the review finding; independent re-review is
still pending and the broader Task5/publication gates remain open.

### Task5B6 independent acceptance including P2 correction, 2026-10-14

Root accepts b2a8e762 plusf9674b957a3e1c25c9883425e0253b56931a2e1f. Initial SPEC independently7pass2.98s/lint/diff/cleanup. Initial QUALITY found a P2: batch-only trace filtering ignored additional point reads; the batch was not accepted then. Same actor corrected the count to all finite plan projection reads regardless selected columns/predicate, then separately checks sole batch shape. New real SQLite batch plus SELECT action_plan_id point-read regression fails under old batch-only and intermediate SELECT-star-prefix guards; final two tests2pass1.94s. SPEC follow-up independently2pass2.00s/lint/diff/cleanup. QUALITY follow-up PASS: exact-helper independent probe accepts batch/case variant and rejects batch+point/point-only/no-read/two-batches. Original one-connection/20row/current-ID and other six test semantics unchanged. All frozen cleanHEADs verified; no runtime/provider/production changes. Remaining metadata/shared/history/composite fixtures and broader copy/native/CI/publication/full10+1 remain open.

### Task5B7 current metadata fixtures (unpublished)

Nine selected current test symbols, thirteen parameter cases, retain the source
missing-column, required-FK, CHECK-literal/order and column-collation questions
against the Email29 shared `actions` table. The four source receipt/plan columns
map to the matching `email_` generated columns; each fixture removes exactly
that generated declaration and its actual dependent NOT NULL/CHECK clauses.
The attempt parent-FK fixture removes only the typed
`(email_operation_parent_type,email_action_id)` reference. Five CHECK variants
change only the selected Email-family constraint. Both old status-collation
cases remain and test the same target `email_status` generated column, whose
physical scope includes attempts, actions, unsubscribe claims and reply
dispatch claims; action-type collation changes its independent generated column.
Historical v21 still uses the unchanged native `_replace_email_actions` helper.

The unchanged thirteen cases ran RED: `13 failed in 3.10s`, pytest exit 1,
because the native tables are absent. After finite target adaptation, the
selected run passed `13 passed in 4.89s`, exit 0; exact Store diagnostics were
then asserted and the final selected run passed `13 passed in 4.28s`, exit 0. Every
fixture requires a new SQLite connection to load the damaged schema, checks
`integrity_check=ok` and its exact missing/changed target metadata, then checks
that both default and `validate_rows=False` EmailStore construction raise
`sqlite3.IntegrityError` with the exact current Store20 malformed-schema
message. Schema objects, action facts and whole-file SHA-256 remain identical
across both rejected constructors. This is the existing shared-ownership
diagnostic boundary; no runtime exception mapping, repair, retry or policy was
changed. Each run removed and verified absence of its owned `/tmp/email13-5b7-*`
directory. Historical H, shared native-rebuild S and remaining current T tests
are still deferred; this is not full Task5 or publication acceptance.

### Task5B7 independent acceptance, 2026-10-14

Root accepts966c23c215020cab86129d7067115ac06b41bcfb over135b4db33ae2a92060eb3f9d26920d9f92740cf2. Fresh SPEC independently13pass10.33s/lint/diff/otherASTunchanged/ownedtempsabsent. Subsequent fresh QUALITY confirms exact generated-column dependencies, sole attemptFK, finite family-specific CHECKs, real shared-column collation, exact target Store error/defaultFalse and no-repair facts. Independent exact-helper probe verifies only attempt CHECK changes while retry CHECK retains >0, schema-cookie reload on warmed and fresh connections, writable_schema off, integrityok and realmanifest rejection; exit0, own temp removed. It did not repeat sufficient13case execution. Both PASS and frozen cleanHEAD. Target diagnostic difference is explicitly accepted existing shared-ownership behavior, not an added catch or policy. Historical helpers remain unchanged. Remaining composites/history/source gates and broader latest-copy/native/CI/publication/full10+1 remain open.

### Task5B8 current composite fixtures (unpublished)

Five selected current tests, eight parameter cases, preserve the source
unsubscribe snapshot corruption, cardinality gate, explicit retry, bounded
audit lineage and immutable retry-request replacement assertions. Claim and
continuation mutations locate the signed source ROWID through their finite
projections and use `update_email_action`; effect mutations use the complete
public `(action_identity, effect_digest)` tuple with `update_action`. The row-33
fixture inserts the original 32 effect values through `insert_action` after the
existing first effect, confirms 33 rows and preserves the zero-decode bound.
The lineage test retains the 0/2000 unrelated task parameters, both task index
searches, run/input primary-key lookups, all returned IDs and the original
500-step VM ceiling. Its target effect search is the exact
`idx_actions_email_email_unsubscribe_effects_0` two-key lookup.

The retry test uses the existing encoded public composite key and payload in
the physical action row for a genuine `INSERT OR REPLACE`, changing the same
readback digest and request time as the source test. The before-insert immutable
trigger rejects it before replacement; the projected source row, physical row
and count are unchanged. It does not substitute a plain duplicate insertion or
add a runtime writer option. Original tests ran RED `8 failed in 3.27s`, exit 1,
on removed native tables. Adapted tests ran GREEN `8 passed in 3.44s`, exit 0;
the added explicit public-key/error guard ran `1 passed in 1.72s`, exit 0.
Owned `/tmp/email13-5b8-*` roots were deleted and verified absent after each
run. Historical source tests, shared native-rebuild helpers and remaining
current fixtures are still deferred; Task5 and publication are not complete.

### Task5B8 independent acceptance, 2026-10-14

Root accepts11aed8276ef3edb58ea6c5d1adf754364c866e89 over971f6dfd9b2d9555127876826b08243074304dfb. Fresh SPEC independently8pass5.39s/lint/diff/otherASTsame/ownedtempabsent, actuallineage EXPLAIN and160VM at both0and2000unrelated. Subsequent fresh QUALITY confirms complete publiccomposite versus sourceROWID paths, original33effects values/zero decode, explicitretry cleanup, actual query-only VM capture, genuineINSERTORREPLACE with beforeinsertimmutabletrigger/exacterror and unchangedprojected/physicalrow/id/count. It did not repeat sufficient execution or create temps. Both PASS and frozen cleanHEAD. Remaining oldsource/historical/bootstrap and any final current omissions must be inventoried before broad gates; latest-copy/native/CI/publication/full10+1 remain open.

### Task5B9 current structure and durable-row tests (unpublished)

Seven selected current `test_email_store.py` symbols collect ten cases. The fresh
schema test now asserts the exact twenty retained `email_*` tables (nineteen
business tables and the schema ledger), shared `actions`, all twenty-nine
declared Store/Email record types, and absence of the thirteen retired native
tables. It preserves the account, classification, feedback and reply-claim
column assertions through retained tables or finite source projections. The
two former native indexes map to the exact partial indexes
`idx_actions_email_email_actions_1` and
`idx_actions_email_email_unsubscribe_receipts_0`; each test checks its exact
declaration and absence after damage, the existing target Email29 malformed
schema error, and no repair. The other retained-table/trigger cases are intact.

Missing direct-action and processed-without-plan fixtures locate signed source
ROWIDs through finite projections before calling `delete_email_action`;
SQLite's default foreign-key setting is unchanged. The forged unsubscribe
claim uses the original identity and digest values through
`update_email_action`. The atomic-failure trigger fires only for the selected
unsubscribe claim's transition to `done` on shared `actions`, preserving the
real rollback, empty receipt/step, subsequent successful terminal write and
conflict assertions. Original tests: `7 failed, 3 passed` in 2.77s, exit 1,
all failures at retired native table/index access. Adapted tests: `10 passed`
in 3.12s, exit 0. Owned `/tmp/email13-5b9-probe-*` SQLite prototypes were
cleaned and verified absent. Historical source tests, unrelated current tests,
latest-copy/native, CI and publication remain open.

### Task5B9 independent acceptance, 2026-10-14

Root accepts248d0d6bd5a31e14cfb2de05873140832930195a overe06af5b9d0a88bb101da9ba6544fc8cb5a751aaa. Fresh SPEC independently collected10cases and10pass2.70s/lint/diff/nonselectedASTsame/ownedtempabsent. Subsequent fresh QUALITY confirms exact fresh20emailtables/29kinds/retiredabsence, exactpartialindexes and no repair, complete sourceROWIDdeletions and originalpointer/forgedclaim fields, precisefamily/status/identityterminalfault and receipt-step atomicrollback/subsequentsuccess/bindingconflict. No repeated sufficient tests or temps. Both PASS and frozen cleanHEAD. Historical-source fixture closure and explicit CIoldref availability need resolution; remaining current/matrix/latest-copy/native/CI/publication/full10+1 acceptance remains open.

### Task5B11 current identifier and paired-ledger tests (unpublished)

Five current `test_email_store.py` symbols collect six cases after adding a
complete Store20/Email52 pair with a future Email ledger. The original five
cases ran RED `5 failed` (exit 1): two rewrote shared `actions` SQL and broke
its `1e999` numeric literal; the three ledger tests expected the retired
Email-only constructor diagnostics. The added full-pair future case also ran
against its old assertion in owned temporary code and failed with the actual
paired `sqlite3.IntegrityError` (exit 1). The adapted six cases ran GREEN
`6 passed in 2.18s` (exit 0); scoped Ruff/diff checks passed.

The identifier fixture keeps the original 265-name required set, its sorted
case alternation and all three quote assignments, but applies its 190-name
intersection only to the actual 19 retained Email business table owners,
28 indexes and 32 triggers. It checks the 79 selected schema objects (61 SQL
definitions), exact required object-name coverage, SQLite reload/integrity,
and every unselected schema row unchanged. Read-only `EmailStore` reopen leaves
database bytes unchanged and creates no WAL. The genuine `0441cf15` source
ran both original positive tests (`2 passed`, exit 0); its partial unique
index name was outside the original required set and remained unchanged.
An earlier prototype that changed all 199 actual owner identifiers instead
of the original required set exposed an existing case-sensitive lookup for
that index; the bounded test adaptation does not expand that coverage or
change runtime validation.

The malformed, missing-column and partial/full future-ledger cases assert
their exact present SQLite error classes and messages, plus unchanged schema,
ledger and Store marker facts, schema cookie, database bytes and no WAL. No
runtime, source/H wrapper, CI, provider, native CLI or production change is
part of this test unit. Historical-source integration and remaining program
gates remain open.

### Task5B11 independent acceptance, 2026-10-14

Root accepts9ae63315fd5fe3620e646740fd0dbbe9a89fafdf over2a01e77dc7259d71b074bddbce5572496b757245. Fresh SPEC independently6pass1.89s/lint/diff/307otherfunctionsASTsame and protected459metadata rows verified; own temps absent. Subsequent fresh QUALITY independently checks199actual identifiers/190originalrequired intersection, all190appear in61selectedSQL, all191stringliteral tokens preserved, original265assignment/case/threequotestyles and protected459 rows intact. Currentpartialledger exact pairederrors and fullfuturepair refusal match constructor ordering/no mutations. It did not repeat sufficient6case execution; owned probe dirs absent and frozen cleanHEAD. No runtime changes. Currentthree-file residual diagnostic had318cases313pass5fail; this accepted unit fixes those5 plus fullfuturecase, but combined319current regression still needs rerun. Historical47genuine0441cases already proven in tmp, not yet integrated; copy/native/CI/publication/full10+1 remain open.

### Task5B12 genuine historical test integration (unpublished)

The old-source boundary is fixed to published main
`0441cf15fafe800f9ca4d67f667e87062ca62109`. Forty original historical
test functions are removed from current collection only after AST comparison
proves each body and decorator identical to that source commit: 35 Store
functions/40 parameter cases, three reference functions/six cases and two
retry functions/two cases. The original assertions remain in the archived
same-ref `tests` and run against the archived same-ref `app`, `pyproject.toml`
and required phase4 inventory JSON. One module-scoped, task-owned `/tmp`
archive serves three explicit pytest node groups. Each child must exit zero,
produce a JUnit report with exactly 40/6/2 named cases, and report no failure,
error or skip; a missing source commit, report or case fails visibly with the
child stdout/stderr. No old code is kept in the repository and no current
constructor is made to accept retired versions. Historical fixture helpers
and imports remain for their other test-file callers.

The first manifest accidentally omitted
`test_startup_rejects_noncanonical_current_action_plan_snapshot`, whose
unchanged body reconstructs a pre-v49 `action_plan_json` column and selects
retired native `email_action_plans`. Its initial focused run correctly exposed
`1 failed, 321 passed in 71.62s`, exit 1. The fixed source body was AST-identical
and its genuine single node passed (`1 passed in 1.98s`, exit 0); it was added
to the Store historical group without changing its assertion or helper. The
corrected focused collection is 321 parent-visible cases: 318 current cases
across the three existing files plus three historical group entrances.
The corrected focused gate passed `321 passed in 69.65s`, exit 0. After adding
independent group/function/case count guards, the three nested groups passed
again (`3 passed in 12.04s`, exit 0), exercising all 48 source cases. AST
comparison confirms exactly these 40 names were removed and all remaining
top-level test functions are unchanged. CI's existing single checkout step now
fetches full history so this bound source and existing genuine-old-source
tests can resolve their commits; no CI job was triggered. All copied source,
database and report files lived under owned temporary roots and were removed.
Runtime cleanup, production, provider/native calls and broader publication
gates remain separate.

The archive additionally checks that the fixed source includes its original
`tests/conftest.py` and `app/email_store.py`. The three wrappers passed again
after that guard (`3 passed in 10.84s`, exit 0), with the same exact nested
counts and task-owned temporary cleanup.

### Task5B12 independent acceptance, 2026-10-14

Root accepts626ca557b54a01ec02e14b72384ba72dafbcec16 overe282f43d1f015bd091decc8e92b73e72db2acd7f. Fresh SPEC independently3wrappers24.24s with exact40/6/2nestedcases, collect3212.64s/lint/diff, complete40removed AST incldecorators identical to0441 and entirecurrentmodulesminus40 identical; ownedtempsabsent. Subsequent fresh QUALITY independently proves missing/empty/wrongnode/skipped/failed/error/nonzero/extra-child-report rejection with diagnostics, exactsuccess accepted, missingcommit/archive/extract failures propagate and tempcleanup verified. OnlyCIcheckoutfetch-depth0, no runtime. Both PASS and frozen cleanHEAD. Accepted implementation fullfocused321pass69.65s contains318current+3wrappers and48actualnestedsourcecases; initialomittedsnapshot321pass1fail71.62s was corrected explicitly, not hidden. Root additionally ran test_email_context_source.py and test_email_task_producer.py:8passed0.30s exit0 with ownedtemp absent, after reviewfreeze no source edits. NamedTask5collect1099 identifies six remaining currentfixture files and additional genuineoldH in four files; these and latest-copy/native/CI/publication/full10+1 remain open.

### Task5C1 Email Web API current fixtures (local candidate)

Thirteen current Web API functions (19 collected cases) replace retired direct
Email action SQL with finite source-shaped projections and existing source-ROWID,
autoincrement-ID or composite-key writers. The two processing-progress callers
share only `_signal_failed_move_plan`; seven lineage-mismatch parameters,
response codes, queue/list counts, retry windows, private-data and throughput
assertions stay intact. A local `register_email_routes` future-ledger case now
expects the existing paired selector's `sqlite_integrity_error` and verifies
schema, Email ledger, Store marker, schema cookie and database bytes unchanged.
Its original healthy non-Email routes, unavailable Email endpoints and one
factory call are still asserted.

The original whole Audit-app Email51 isolation test is AST-identical to the
fixed `0441cf15` source and passed there in an owned archive (`1 passed in
2.92s`, exit 0). The target Store20/Email52 constructor instead rejects an
advanced Email ledger before `create_audit_app` registers routes. The current
test checks that exact paired `IntegrityError`, with unchanged schema, ledger,
Store marker, cookie, bytes and no WAL. This distinguishes local Email route
isolation from construction of the whole Audit app without changing runtime.

Original targeted 20 cases gave `15 failed, 5 passed` in 8.30s, exit 1, on
retired physical SQL and the prior route diagnostic. Converted cases gave
`19 passed, 1 failed` in 8.93s, exit 1; the one new no-WAL assertion required
the established DELETE-journal fixture setting and then passed alone
(`1 passed in 1.74s`, exit 0). A first full Web API file run exposed the
unselected whole-app test (`180 passed, 1 failed` in 27.96s, exit 1), prompting
its genuine-old-source/current-pair split above. The bounded final gate over
the complete Web API file and four historical group entrances reported
`185 passed in 42.80s`, exit 0: 181 current API cases plus four wrappers,
whose exact nested published-source case counts are 40/6/2/1 with no skip.
Its owned `/tmp/email13-task5c1-focused-*` root was removed and verified
absent. No current runtime, provider, native CLI, production or CI operation
was changed. Other named Task5 files and the additional 11 genuine-old-source
functions remain open.

### Task5C1 independent acceptance, 2026-10-14

Root accepts9887c07662d882842cc21a7aed01bf889ea794b5 over552fa0551ae458e68c4b2f51e193279c9a5448c7. Fresh SPEC independently22pass12.63s/lint/diff/ownedtempabsent, original13businessassertions/decorators/helper2callers/18field6rowthroughput exact; typedidentities/ordereddelete and route/wholeapp split verified. Subsequent fresh QUALITY independently confirms assertionASTs, allmatchingrow identities, SQLNULL encoding, rawvalues/FK0 preservation, privacy/window/lineage7cases, exactnewsource1wrapper count and existingFpairedconstructor behavior. No repeated sufficient tests or temps; both PASS cleanfrozenHEAD. Accepted implementationAPI181+legacy4 parent185pass42.80 contains49nestedoldsourcecases. Formerwholeappindependentfutureisolation remains actual0441source assertion; currentwholepairedfutureunknownschema refusesunchangedDB, no runtimepolicy/catch. AdditionalH11func56cases sourceboundintegration and five remainingfixturefiles plus finalTask5/latest-copy/native/CI/publication/full10+1 remain open.

### Task5C2 remaining genuine historical fixtures (local candidate)

Eleven original source-version tests were removed from four current default
modules: two schema49 direct-unsubscribe cases, two v7/v8 reply-delivery cases,
one v29 worker case and six v25 task-adapter cases. Every removed function,
including decorators, is AST-identical to its fixed published `0441cf15`
version; every remaining top-level current test function is unchanged. The
existing historical archive/runner and its four groups remain unchanged. Four
explicit additional groups run those eleven original nodes in the same
isolated source subprocess with exact function/case expectations 2/2, 2/2,
1/1 and 6/51. No old source code is vendored or rewritten, and no missing
source ref, skipped child, error or nonzero child exit can pass the existing
Junit guard.

The new groups reported `4 passed in 12.76s`, exit 0, executing all 56
genuine old-source cases. The complete eight-group entrance reported
`8 passed in 25.06s`, exit 0, executing 105 source cases with counts
40/6/2/1/2/2/1/51. Both owned `/tmp/email13-task5c2-*` roots were removed
and verified absent. Read-only collection of the four remaining current files
reported worker249, direct47, reply45 and adapter67, totaling408 current
cases; adding the eight historical entrances collects416 parent-visible
cases. Those 408 current cases were not run in this unit and still include
retired-table fixtures to adapt separately. Four source-only imports made
unused by the removal had no remaining local or external references and were
removed after explicit scope approval; retained source helpers and their
still-used imports remain. No runtime, CI, provider, native CLI or production
operation changed.

### Task5C2 independent acceptance, 2026-10-14

Root accepts c40a7efd0536214e24822d359b16f531e6d0863a over65289fa1c8acd376735d3c33b70f892b192f886c. Fresh SPEC independentlynew4wrappers13.91s/nested56/JUnitnames/lint/diff/ownedtempabsent; wholemoduleASTminus11Hfuncsand4approvedimports exact and source0441decorators/body exact. Subsequent fresh QUALITY confirms original4groups/runner/fixture unchanged, no import dependencies, exact46adapterparameterrows and source2/2/1/51 counts, truthful408currentcollectonly. It corrected handoff assert statistic to68(direct8/reply9/worker4/adapter47), not78; candidate had no incorrectcount. No rerun/tempcreated; both PASS frozen cleanHEAD. Accepted implementation8wrappers25.06s invokes105nestedoldsourcecases; currentfourfiles408 onlycollected. Worker23currentfixture scope provenincluding19explicitreceiptfields/defaultentry_url/7outcomes, not yet implemented. Remainingcurrentfixtures/finalTask5/latest-copy/native/CI/publication/full10+1 open.

### Task5C3 current worker fixtures (local candidate)

Only eleven current worker test functions (23 parameter cases) and their current-only `_assert_no_model_accept_rows` helper are adapted. Plan, direct-action and attempt counts use finite projections. Original unfiltered deletion and version update, plan-ID corruption and `mark_read` retry-time updates select exactly the prior matching source ROWIDs before using existing typed writers; public action IDs are not substituted for source ROWIDs. Both synthetic unsubscribe receipts retain all nineteen explicitly supplied source values, including empty result metadata and the original timestamp; the omitted `entry_url` remains the source and target default `''`. The `None` outcome still inserts nothing. The scanner-race fixture still updates only the retained classification or message locator and asserts attempt rollback.

The unchanged 23 selected cases were RED: 21 failed from retired physical-table SQL, two passed, exit 1. The finite conversion was GREEN: 23 passed, 226 deselected, exit 0 in 6.26 seconds. The necessary worker-file integration gate passed 249 cases, exit 0 in 28.31 seconds. Each run used a task-owned temporary pytest directory and verified removal. Historical 0441 source wrappers, runtime code and the other current test files are unchanged. Fresh SPEC and QUALITY review remain pending; Task5's final named matrix, latest database copy, native verification, CI and publication are still open.

### Task5C3 independent acceptance, 2026-10-14

Root accepts d7f6ebc2771b3705e8cf6b319026ffe49ca3f214 over125507870a358e455bae1224aae73cd3fa976a6a. Fresh SPEC independently23pass6.23s, exact67assertions afteronly3countquerynormalization, original19explicitreceiptexpressions/defaultentry_url and parameters/sourceROWID predicates verified, ownedtempabsent. Subsequent fresh QUALITY confirms fullmatchingrow identities, sevenreceiptoutcomes andNone-noinsert branch, exactscanner retainedlocator/raceassertions and unchangedtwohelpercallers/othersAST. No repeated sufficienttests or temps; both PASS frozen cleanHEAD. Accepted implementationworker249pass28.31s; root separatelyran currenttest_email_task_adapter.py67pass8.02s exit0/ownedtempabsent (notcandidatecodechange). Remainingreconcile22 anddirect47/audit81/reply45 fixtures/finalTask5/currentcopy/native/CI/publication/full10+1 open.

### Task5C4 current action reconciliation fixtures (local candidate)

The 22 current reconciliation tests now read the two retired action families through the existing finite direct-action and attempt projections. `_snapshot` still concatenates complete row tuples from exactly four sources in the original order: direct actions, attempts, retained classifications and retained messages. Each source keeps `order by 1`; the two retained-table queries are unchanged. The target projections match the fixed 0441 source column order exactly (18 action columns beginning with `action_id`, ten attempt columns beginning with `id`). `_action_row` still returns a SQLite Row filtered by public `action_id`. The single direct test query still compares pending actions with attempt history, exact attempt-count drift and sorted statuses through the same correlated predicates.

The original file was RED with 14 retired-table failures and eight passes, exit 1. After the finite read conversion, all 22 cases passed, exit 0 in 9.60 seconds. Both runs used owned temporary pytest directories and verified their removal. No writer, provider behavior, runtime, historical test or index changed. Fresh SPEC and QUALITY reviews remain pending; the other current fixture files and final Task5 gates remain open.

### Task5C4 independent acceptance, 2026-10-14

Root accepts3a59cf7e48df2bee1aec24b830557c8d8e028784 overe5837337ac3805068ab3584346c43fdd0a7445ae. Fresh SPEC independently22pass17.96s/lint/diff, originalfour-sourcefulltuples/order1/Rowpublickey and86assertASTs preserved; source0441fieldorder18actions/10attempts exact; ownedtempabsent. Subsequent fresh QUALITY independently executes projectioncolumn-shape and correlated pending/drift queries in memory, confirms18/10aliases and detects onlyviolatingaction while acceptinganother matchinghistory; nestedaliases retaincorrelation. No repeated22 tests or temporaryfiles. Both PASS frozen cleanHEAD. Acceptedimplementation22pass9.60. Direct47/audit81/reply45 current173 adaptation and finalTask5/currentcopy/native/CI/publication/full10+1 remain open.

### Task5C5 current direct unsubscribe, audit and reply fixtures (local candidate)

Exactly sixteen current test functions (twenty parameter cases) and two shared read helpers now use finite Email action projections. Receipt and claim mutations locate every row selected by the original `action_identity` predicate and write through its source ROWID. Effect mutations use the full public `(action_identity, effect_digest)` identity; the orphan branch inserts the original six values. Step tampering selects every matching public step ID. Whole-table effect and ordered step reads retain the journal prefix check, while the other reads retain their original columns, predicates and ordering. The reply failure fixture now triggers before the shared `actions.payload_json` update only for a reply dispatch claim whose new status is `done`; it keeps the original all-claim scope and `simulated_claim_done_crash` error. The receipt/claim rollback and later completion assertions remain intact.

The selected twenty cases were RED with sixteen retired-table failures and four passes, exit 1, then GREEN with twenty passes, exit 0 in 8.13 seconds. The necessary three-file integration run passed all 173 current cases, exit 0 in 37.97 seconds. Each run used a task-owned temporary pytest directory and verified removal. The zero-caller retired receipt metadata helper, historical wrappers, runtime code, provider behavior and CI are unchanged. Fresh SPEC and QUALITY review remain pending; the final Task5 matrix and publication gates remain open.

### Task5C5 independent acceptance, 2026-10-14

Root accepts f14960d0649542aa68a75705f6fca646c6bc7f9e over56d8815ecc5a85ef2a387a07c5cd02fbf3dd2b7b. Fresh SPEC independently24pass17.93s(20changedplus4helpercaller),collect173/lint/diff, exact454assertASTs afteronlyreceiptcountsource normalization, ownedtempsabsent. Subsequent fresh QUALITY independently confirms454asserts(direct127/audit161/reply166), wholematchingROWID/effectcomposite/stepID predicates, exact6fieldorphanvalues, wholejournalcoverage/order and allclaimdonepayloadfault alignment with atomicreceipt/claimtransaction. No repeatedsufficienttests or temps; both PASS frozen cleanHEAD. Acceptedimplementationthreefiles173pass37.97s. Existingunsubclaim/receiptprojectionfieldorder caveat affects no positionalreads in this batch. Remote main moved0441→d06(PR88WeChaterrors/PR89OAlockbackfill, no schemafield/versionchanges); clean normalmerge and justifiedincrementchecks precede finalTask5matrix. Latest-copy/native/CI/publication/full10+1 remain open.

### Clean main d06 integration, 2026-10-14

AfterC5accept875ef943, root claimedc191a280 and normally merged origin/main d06c148b90aaeee74c14bbabf0183ffd65f34048 (PR88 WeChat component-error recovery and PR89 OA audit metadata read-outside-writer/batched update) without conflicts. The increment has no schema fields/version changes; its code/docs/tests are preserved. Focused existingPython checks after the merge: WeChat success/failure ticks, newOA scan/write-boundary, existingOA historical identity backfill, and freshStore/Email paired orders:6passed3.61s exit0. Exactownedtemp directory removed/absence verified. These are increment checks, not finalTask5matrix or production proof. Historicaloracle0441 remains fixed; latestproductioncopy proof must bind actual then-published predecessor separately. No production/provider/nativeCLI/CI/push/deploy occurred.

### Task5 final focused combination after main d06 merge (local candidate)

At clean merge HEAD0da16e9fb652d754b29503e397690b89b80bf50d, the twelve named Task5 files, three current core files (`test_email_store.py`, `test_email_action_plan_reference.py`, `test_email_retry_schema_release.py`), and the historical wrapper file collected exactly 1,369 parent cases across sixteen files, exit0 in2.44s. One combined run with the existing Python and pytest cache provider disabled passed all1,369, exit0 in261.24s (outer elapsed261.94s), with no parent skips. The eight historical wrappers bind the exact published0441 source and require JUnit counts40+6+2+1+2+2+1+51=105 child cases, successful exits and zero failures, errors or skips; their passage in this run confirms those genuine old-source assertions, not a target52 downgrade. Both task-owned collect/run temporary directories were removed and absence checked. No production database, provider, native Agent CLI, CI, push or deploy was used. This is the focused Task5 test gate; independent overall review, latest-copy transition, native verification, CI and publication remain open.

### Task5 overall independent acceptance, 2026-10-14

Root accepts6539cb1c992829c1dea3dd53eee20fd80d8708e4 after fresh sequential overall SPEC then QUALITY. Both independently found no active retired-physical-table consumer beyond the exact19/51 publication quiet source and historical source/migration owners; signed source ROWID History, bounded Attention/current-plan retry, Store-first Email availability, exact25-index/7-trigger B0 metadata ownership, existing provider repair and genuine0441 historical bindings remain consistent. Neither repeated sufficient1369-parent/105-child evidence or prior unit assertion audits. Both read-only final checks retained frozenHEAD and clean worktree, no temporary artifacts or external operations. The accepted combined run is1369parent cases passed261.24s, including8 wrappers enforcing105 genuine old-source cases with zero skips/errors/failures.

Task5 code/test integration is accepted locally. Latest-production-copy coupled publication/default constructors, target public writes/current-fact inverse/genuine published-old cold writes/re-forward, actual native approve/revision with fake providers, actual UI interactions, CI/push/deployment/live readback remain Task6/7. A root read-only production snapshot during review observed Store2026-10-13.5/Email51/121tables, main477696000bytes/WAL4276592bytes/freelist0; this is a point-in-time source measurement, not target size or publication evidence. Full ten core tables plus one migration ledger remains incomplete.

### Task6A paired-copy harness (local candidate, synthetic predecessor only)

The existing three-case SQL boundary proof remains separate and still checks SQL-legal nullable ActionPlan source ROWIDs without claiming old strict constructor acceptance. A new case now archives the exact predecessor app and its real phase4 inventory dependency, seeds an application-valid Store19/Email51 copy through genuine old public methods, and selects `prepare_database_schema_release` for the real paired child publication. It requires Store20/Email52 markers, the exact original ledger plus row52, the 29-kind declaration, all thirteen retired tables absent and both default constructors cold-openable. Current `EmailStore` public methods then enrich a blank reply receipt, complete one direct action, persist a failed action and History source ROWID, write unsubscribe terminal/step facts, and create one source-verified explicit retry. The target rows are generated by those methods, never copied from an old-code donor. Full source-shaped values, SQLite storage types, signed ROWIDs, native public IDs, 144 source fields and both Email allocator waters are checked before and after the captured release's inverse.

The inverse must restore the exact original Store marker timestamp, complete Email51 ledger and declarations, all thirteen tables, current facts, unrelated table fingerprints, all non-version `service_state` rows, the sixteen existing action facts and pre-existing FK findings. Genuine archived predecessor constructors then cold-read the result; genuine old public methods replay feedback and receipt enrichment and finish the pending retry. An independent old-code donor checks only the expected public-write delta, with bounded public clock normalization. A second real coordinator publication requires the complete post-write source projections and waters to reappear under default target constructors. Public Queue, History and failed Attention counts are compared at matching seed, current, inverse, old-write and reforward phases. The frozen `eb797e6b` declaration binding remains the source-schema oracle; execution still requires the exact production checkout HEAD as `--source-ref` before and after the read-only online copy, and the source archive is bound to that exact Git tree. Evidence contains only aggregate flags/counts, code/dependency hashes and refs, not row bodies or provider output.

The added baseline was RED because no paired path existed (one failure, exit1), then passed with three failure-injection regressions: skipping publication fails the default-constructor pair contract; reporting success without target public writes fails durable fact deltas; altering a restored source row fails current-fact equality. The proof file plus three existing paired coordinator/direct/receipt regressions passed 24 cases in117.28s, exit0. After strengthening the skipped-write regression to return success-shaped flags, that case separately passed in6.10s, exit0. Scoped Ruff and diff checks passed, and each task-owned temporary tree was removed and verified absent. No actual production database copy was used in this implementation run, so `run(--source, --source-ref, --evidence)` against the then-current production predecessor, native Agent CLI, UI, CI, push, publication and live readback remain separate Task6 gates. Fresh SPEC and QUALITY review are pending.

Fresh SPEC found two local evidence gaps in that candidate. The paired proof now compares the actual Console Attention rows returned by `_queue_attention_rows` on both the archived predecessor and current app, across every account and category, as category/status counts; fixture-specific failed-action DAO assertions remain separate. After inverse and again after genuine old public writes, it also locates the failed fixture's restored native `email_actions` ROWID and requires the old public History entry to have exactly `id=email-action:<signed ROWID>`, that `source_id`, and `failed` status. This checks the public surface rather than treating a fixture-filtered DAO count or History total as sufficient. The accepted SQL-boundary cases and old `prewrite_read`/`read` modes are unchanged. Narrow regressions first failed 4 cases before these checks existed, then the selected seven wiring/guard/paired cases passed in19.15s, exit0. An initial probe using a raw old-table update hit its existing attempt-count trigger; the probe now uses genuine old public claim/completion and passed without disabling triggers. The original 24-case result remains the baseline; this correction awaits the same fresh SPEC re-review and subsequent QUALITY review. No production copy, provider, native CLI, CI, push or deployment was run.

### Task6A harness independent acceptance, 2026-10-14

Root accepts92c4432b3690841121e9a444abc036d5dbeadc6e over35a9de1eff5de3ade76dc1b753b3419dd7ee8f76 after fresh SPEC, correction and same-SPEC re-review PASS, followed by fresh QUALITY PASS. Initial SPEC found the fixture-only Attention proxy and absent genuine-old History identity guard; same-owner correction now calls the actual all-account Console reader and validates native ROWID/logical ID/failed status after inverse and old public writes. QUALITY independently confirms real coordinator/child/default constructors, actual target public writes, complete current-fact inverse and genuine predecessor public writes/reforward; donor never supplies target facts, bounded timestamp normalization and original SQL proof remain intact. Accepted evidence:24baseline cases117.28s, strengthened no-write fault1case6.10s, correction7cases19.15s and2wiringprobes4.58s, all exit0 and owned temporary roots absent. Reviews made no edits or redundant reruns.

This accepts the committed synthetic predecessor harness, not the latest production-copy result. Root will now bind exact current production HEAD and SQLite backup, execute this accepted harness on owned temporary copies, and retain only aggregate acceptance evidence. Native approve/revision, UI, CI and publication remain open; full10+1 remains incomplete.

### Task6A latest production-copy acceptance, 2026-10-14

Root executed committed9fd4cc8ad3a5ba04098f85974b1f29afa5e8ff46 against an owned immutable SQLite backup observed2026-10-14T19:58:20.229941+00:00, exact published predecessor d06c148b90aaeee74c14bbabf0183ffd65f34048. Backup477753344bytes,121tables, Store2026-10-13.5/Email51, quick_check/integrity_check bothok; one existing FK finding was captured and preserved exactly, not repaired or ignored. Source backup checksum remained unchanged. Checkout/SQLite observations are explicitly non-atomic and production HEAD remainedd06 before/after.

All4cases passed, exit0,396.75seconds: actual-copy real paired coordinator/child/default constructors and target publicwrites/current-fact inverse/genuine predecessor coldread-write/reforward; separate actual-copy SQLboundary; genuineoldfresh SQLlegalNULLplans; genuineoldfresh strict applicationvalid publicflow. Actual pairedtable count121→108; this is thisunit's13table retirement, not full10+1 completion or a physical-space reduction claim. All144fields/storageclasses/publicidentities/ROWIDs/waters/currentfacts plus original16actions/unrelatedfacts/markerledger/nativeoldHistory/actualConsoleAttention and queues were checked by the accepted harness. Aggregate evidence only in docs/superpowers/specs/2026-10-14-database-redesign-email-actions-latest-copy.json (6085bytes); no source rows, native transcripts or credentials retained. Internal copies/archive and outer owned source backup were removed, absence verified. Production mutations/provider/native CLI calls all0. Native approval/revision fake-provider execution, UI interaction, CI and publication remain open.

### Task6B native-copy harness extension (local candidate; CLI execution pending)

The existing five real Consumer/Audit native CLI modes remain the general DingTalk task chain with an in-memory sender and genuine persisted review, action receipt and business result. On an owned Store19/Email51 source copy the harness now selects the official paired release, publishes through its real child, cold-opens both default constructors and checks Store20/Email52, the complete 29-kind Action declaration and thirteen retired tables absent. It then takes an SQLite Backup API snapshot of that **post-publication, pre-native** target as the immutable original-row baseline. The old source checksum is checked independently. This separates the accepted Task6A old-to-new source-fact proof from the native phase's same-schema row/FK preservation check.

Current Email coverage is a distinct deterministic public-API exercise on that migrated candidate: it persists one isolated processed source and ActionPlan, reads the stored message context with zero prior reply/unsubscribe receipts, claims and completes one direct mark-read action using an in-memory provider-result value, and reopens to verify the attempt receipt and signed source-ROWID History entry. It creates no pending Email Agent task, sends no email and makes no claim of native Email-browser approval. The old `original_rows_unchanged` caller contract and additional harness import remain; its row comparison now uses two read-only connections with bounded rowid/primary-key streaming because the canonical Email target trigger references `main` and SQLite refuses to ATTACH another target copy. Original FK finding equality and FTS-shadow exclusions remain. Canonical-target query plans for actions, runs and tasks show one ordered rowid scan on each side, with no per-row lookup or temporary sort; only three small `WITHOUT ROWID` Email metadata tables require a PK-key temporary sort.

Focused regressions include genuine d06 fresh source publication, the canonical ATTACH refusal with successful independent comparison, public Email context/receipt/History cold readback, refusal of an unsupported incomplete pair, changed or lost original rows, and missing public context/attempt/History observations. The first seven-case run reported five pass/two fail at the ATTACH limitation; after the bounded comparison repair and immutable-baseline checksum assertion the focused module passed twelve cases in6.22s, exit0. A final run after switching large-file checksums to streaming passed twelve in5.72s, exit0. Ruff and diff checks passed, and task-owned temporary test roots were removed. The extension has not executed the real native CLI or used a production database copy in this implementation unit; those are subsequent acceptance gates after independent SPEC and QUALITY review. No runtime, provider, credential or native configuration behavior changed.

### Task6B harness independent acceptance, 2026-10-14

Root accepts9254756a0b5b7d5ae89d75370afdc532301f293b over06d97e3a after fresh sequential SPEC and QUALITY PASS. Both confirm official paired publication before native task creation, separate immutable source/postpublication target baselines via SQLite Backup API, correct independently read-only streaming inclusion with original FTS/shadow exclusions/FK findings, and unchanged native commands/roles/provider/CLI modes. QUALITY additionally passed a signed-ROWID/mixed composite PK/Unicode/BLOB/addition/deletion helper probe and cleaned its temp root. Accepted implementer12cases5.72s/Ruff/diff/AST boundaries and cleanup were not needlessly repeated. Native Email projection/direct-attempt/History proof is separately deterministic, with honest zero prior reply/unsubscribe receipts; no Email-browser native or full-task-context claim.

Actual native approve/revision runs on latest production copies, in-memory-provider receipts, isolated Console clicks, CI and publication remain open. No production or real provider mutation has occurred. Root will now run those two real CLI cases; all original trajectories remain solely in native Codex storage.

### Local generic History query candidate, 2026-10-14

The full UNION key stage evaluated reply Attempt status work during a plain
multi-source list, although that list needed only source identity and sort time.
The local candidate projects three compact keys per selected source branch,
applies original filters before combining branches, then hydrates page rows in
the existing read snapshot. Repeated later/superseding terminal-attempt checks
now share one grouped maximum public ID per trigger, with the same eight
terminal statuses and strict newer-ID predicate. The failed-only dispatch and
its candidate path are unchanged. Focused regression-first tests cover filter,
offset, total, page-only hydration and a long same-trigger history; broad text
search, whole-copy performance and live UI acceptance remain open. This unit
has no schema, provider, native CLI, production or deployment change.

Focused pagination module: 14 passed in 8.75 seconds after the final query
structure correction. The original full-projection helper retains its string
return contract; selected source keys wrap its actual returned SQL, so existing
body-read instrumentation observes the key stage. A disposable fault probe
forced summary evaluation on every key and observed eight body evaluations
for a one-row page; the page-only regression would fail under that fault. The
probe's owned `/tmp` directory was removed and absence verified. Scoped Ruff
and whitespace checks passed. Two selected non-Email History-type checks
passed; a third selected test reached its previously identified retired
`email_action_plans` fixture INSERT and failed before the query assertion.
That fixture conversion belongs to Task7 and is outside this query unit.

### Task6 native/UI and History query acceptance, 2026-10-14

Root accepts the read-only History repair `0c210a7d` after fresh sequential SPEC
and QUALITY PASS. SPEC independently compared 258 full projected rows with the
frozen base across all eight terminal statuses, four current statuses, public-ID
ordering, channel/trigger isolation and signed IDs; its owned probe was cleaned.
QUALITY found no actionable issue and explicitly retained the finite aggregate
materialization cost as a boundary requiring whole-copy measurement. The
implementer's 14-case pagination result and fault-sensitive body measurement
remain separate evidence.

On the held revision-case migrated production copy, root then ran the actual
public `list_operation_logs_with_count`, with native connection pragmas and
cache bypass, sequentially under an eight-second/60M-VM interrupt ceiling.
Plain: 1.7845s, 8.541M VM, total35,254/page20. Fixture search: 0.9163s,
12.917M VM, exactly one result. No-match search: 1.3001s, 23.943M VM,
zero results including the existing fallback count. All completed; no timeout,
cache, schema or frontend change was used. These are this copy's observations,
not production latency guarantees.

Actual native execution used committed `07e1d075` and separate then-current
production SQLite backups bound to published d06. Approval completed in90.04s
with two real CLI invocations. Revision completed in113.57s with four real CLI
invocations and persisted review decisions `return`, then `approve`. Each
finished the task with one in-memory provider call, one verification, one
execution receipt, one sent-reply business result and one business attempt.
Each also passed the separately labelled deterministic Email public context,
direct-action attempt/receipt and signed-ROWID History proof. No real outbound
provider was invoked. Original trajectories remain only in native Codex storage.

Read-only browser clicks used `0c210a7d` against the revision copy with application
lifespan off. Production assets were read-only and their frontend source matched
d06. History displayed35,254 records and the fixture search displayed one; its
detail showed four internal runs, one revision and one verified action. Consumer
and Audit process links opened, native timelines rendered21 and13 entries,
and an expanded tool card displayed its actual native input/output. The first
audit's material mismatch and return reason were visible. Attention rendered27
issues and expanded a detail. The mock Email search/detail displayed its current
plan version1, completed mark-read action and in-memory Provider receipt. Existing
unsubscribe list displayed1,087 records; its detail displayed two processing
records and its existing business-detail link opened. That old attempt has no
Agent-run link in either source or target; none was fabricated. One persisted
retired-reply-table error also existed once in both source and target; this was
not a new copy request failure. Browser error/warning observations were empty.
Write/retry/withdraw/send controls were not clicked; this is not native Email
browser approval or exhaustive write-control acceptance.

After closing the owned browser tab and stopping the owned test server normally,
all103 original ordinary target-baseline tables still passed row inclusion,
quick_check/integrity_check were bothok, and the original one FK finding remained
unchanged. Both native working roots were removed and absence verified. Aggregate
evidence is `docs/superpowers/specs/2026-10-14-database-redesign-email-actions-native-copy.json`;
it retains no row bodies, copied trajectories or credentials. The native harness
itself is unchanged since actual execution. Production/provider mutations are0.

At20:58UTC the production checkout still read d06/Store19/Email51:121 non-internal
tables. Counting SQLite's own `sqlite_sequence` yields122; all earlier121→108
comparisons exclude internal tables and include the existing FTS/shadow tables.
This unit is not deployed. Task7 must finish the additional current-versus-old
test-fixture migration, CI, main reconciliation and normal deployment/readback.
The complete ten-core-table plus migration-ledger redesign remains open.

### Task7 first four current tests: independent acceptance, 2026-10-14

Root accepts1be8f7cd over3191e17e after fresh sequential SPEC then QUALITY PASS.
Both inspected the real finite projection, source-ROWID locator, writer and
retry-release implementations: plan history remains unfiltered within its
family, original columns/order/predicates remain, the training check still
counts the entire direct-action family and requires zero, and the changed-error
fixture updates every original matching source ROWID without using physical
Action IDs. SPEC independently confirmed all nonselected function/class and
non-import module ASTs unchanged; all business assertions remain except the
necessary SQL source normalization in the zero-count assertion. QUALITY found
no new risk warranting a repeated four-case run. Both left clean frozen HEAD.

Accepted implementation evidence is RED4 removed-table failures2.89s then
GREEN4pass2.72s, scoped Ruff/diff and verified owned-temp cleanup. This closes
only the four named current functions. Other direct fixtures, shared snapshots,
genuine historical-owner bindings, browser/e2e reads, current marker expectations
and the source13 caller fixture's declaration-pair setup remain required before
CI and publication. No runtime, production or provider state changed.

### Task7 four current Email test storage reads

Four existing current tests now read plan history and zero mailbox actions through
the finite Email action projections. The changed-failure retry test locates every
matching original source ROWID and updates its error through the accepted Email
writer. The tests retain their plan version/history, original authorization
snapshot, no-mailbox-action, queue, training-record, and stale-retry refusal
assertions. Their exact selectors first failed four times on removed physical
tables, then passed four times against Store20/Email52. No runtime, schema,
provider, production, or historical-test behavior changed. This is one bounded
Task7 test-debt unit; remaining Task7/CI/publication gates are open.

### Task7 current fixture writes: independent acceptance, 2026-10-14

Root acceptsb16b4b21 over70292310 after fresh sequential SPEC then QUALITY PASS.
Both confirmed original field values, source predicates and intentionally absent
parents remain with the original FK-off fixture domain. Source ROWIDs are read
from legacy keys, not physical Action IDs. The account-change predicate and
readonly write error remain effective. The attempt ledger correctly uses the
existing automatic-ID `insert_action` dispatch, while the direct action uses its
ROWID writer; the0→1→0 drift check still fails immediately in the translated
SQLite trigger and then rolls back. No application-level drift exception,
invented parent/ledger facts, new helpers or runtime changes were introduced.
SPEC independently checked all unselected function/class ASTs and selected
signatures/decorators/assertions unchanged. QUALITY found no new risk requiring
redundant test execution; both left clean frozen HEAD.

Accepted implementation evidence: RED8 removed-table failures14.79s, intermediate
7pass/1fail identifying the existing automatic-ID insert API, final GREEN8pass
4.45s, scoped Ruff/diff/AST checks and owned-temp removal/absence verification.
This closes only seven named functions/eight cases. Shared snapshot/History/Store
fixtures, genuine historical owners, browser/e2e reads, current markers and the
coherent source13 caller fixture remain Task7 work before CI and publication.

### Task7 current Email action fixture writes

Seven existing functions (eight parametrized behavior cases) now create Email
actions and attempt ledger rows through the finite writers. The queue fixtures
retain every supplied source field, including stale plan references and retry
timestamps. The account-change count retains its retriable predicate over the
finite source projection. The read-only preview still attempts to update every
source action and requires SQLite's `readonly` error. The drift case retains
foreign keys off, its immediate transaction and orphan source keys; its second
source-ROWID counter update still raises `attempt_count_drift` before rollback.

The exact eight selectors first failed on removed tables (8 failed, 14.79s).
After changing the writes, an intermediate run exposed that attempt ledger rows
use `insert_action`, not the rowid-family `insert_email_action` (7 passed, 1
failed). The corrected selectors passed 8/8 in 4.45s. Scoped Ruff and diff
checks passed; an AST comparison found every nonselected function/class and all
selected assertions unchanged. No runtime, schema, provider, production, CI,
push or deployment was part of this unit. The rest of Task7 and full10+1
redesign remain open.

### Task7 shared current Email fixtures

Two History callers now seed the same classification, plan and provider-action
facts through the existing finite Email action writer. The classification SQL,
Store-only empty History check before Email initialization, current/superseded
plan cases, retry window, failed Attention identity and all assertions remain
unchanged. The isolated queue-summary test now gives the current Email
projection its 18 real `email_*` columns and `email_actions` discriminator,
using the current field/type declarations. Its original seven input rows still
include uppercase `FAILED`, an invalid retry date, retired/inactive plans, a
latest-row tie and a Unicode unit separator in the error. This small query
fixture is intentionally permissive; it does not establish canonical storage
or schema-validation behavior. The Store retry fixture no longer creates four
retired, empty unsubscribe tables because the current Store already supplies
the finite projections the method reads.

The focused baseline was 4 failed and 5 passed, with failures at the removed
History plan table and absent `actions` table in the snapshot fixture. The same
nine cases passed after the changes. The complete History and snapshot modules
plus the selected Store retry case passed 27/27. Scoped Ruff, diff and AST
checks passed: unselected functions/classes and selected assertions did not
change. This is local fixture coverage only; no runtime, provider, CI,
production, push or deployment work is claimed.

### Task7 shared current fixtures: independent acceptance, 2026-10-14

Root accepts f6046bf2 after fresh sequential SPEC and QUALITY PASS. Review
confirmed identical original assertions in all three files, unchanged unselected
functions, exact History values/defaults and source ROWID allocation inside the
original transaction. The minimal snapshot exposes the actual 18 fields, keeps
all seven original rows and its one-query assertion, and remains deliberately
permissive query coverage. Store retries use the current finite projections
without artificial legacy tables or extra Email initialization.

The existing baseline4fail/5pass, focused9pass and combined27pass evidence was
sufficient; neither reviewer identified a concrete gap warranting a repeat.
Scoped diff checks passed and the reviewed tree was clean. Root also corrected
stale History evidence sentences in architecture/runtime documentation to reflect
the already completed Task6 whole-copy and isolated UI acceptance. No runtime,
schema, provider or production change occurred. Historical owners, other shared
fixtures, CI/publication and full10+1 remain open.

### Task7 historical Email schema owners, 2026-10-14

Thirteen historical functions whose fixtures construct Email schemas 33, 45,
46, 47, 48, 49 or 50 now run in five explicit groups through the fixed
`0441cf15fafe800f9ca4d67f667e87062ca62109` published-source harness.
The JUnit gate requires the exact named functions and sixteen parameter cases,
with zero failures, errors or skips. The five new wrappers passed (16 nested
cases); the five current test modules passed 117/117 once. AST comparison
confirmed all thirteen removed function definitions match the fixed source
exactly, and all 60 remaining current function definitions (44 tests and 16 helpers) are unchanged.
Only imports made unused by the removals were dropped. Scoped Ruff and
`git diff --check` passed. Both task-owned pytest temporary roots were removed
and their absence verified. This is local test binding only; Task7 CI,
publication and the full10+1 redesign remain open.

### Task7 historical-owner independent acceptance and next fixture evidence

Root accepts34a50f13 plus wording correction36dbd78e after fresh sequential
SPEC and QUALITY PASS. The original13 historical functions remain exact in
fixed0441; five new groups enforce16 children with no failures/errors/skips.
Existing eight groups/105 children and their shared runner/source paths remain
unchanged. All44 current tests and16 helpers keep their original function ASTs;
only orphaned imports were removed. The five current modules passed117 cases
once. No repeated run was needed for the documentation-only count correction.

A separate root baseline of five current bootstrap/reader cases failed only
on the old Store19 marker expectation (5failed2.53s). The actual constructors
selected Store20. Its owned temporary directory was removed and absence checked;
these tests have not yet been corrected or accepted.

A bounded synthetic research proof established a coherent current caller
fixture route. Genuine0441 source13→16→17→18, complete native Email51, then
genuine Store18→19 succeeded. With the empty claims table asserted empty, its
frozen actual declaration/index replaced the fresh declaration without changing
external triggers. A separate old process accepted19/51; current capture
recognized exactly actual_no_memory_fk/actual. Official paired publication and
a separate current cold open accepted20/52, with quick_check ok and no FK
findings. The proof archive contained only app/tests/pyproject and six exact
phase4/5A/5B/5C/5D/5E metadata dependencies. Its owned root was removed and
absence verified. No repository/runtime code changed in this research. This
empty synthetic proof does not verify the populated14 current caller cases.
The runtime accepted-pair set was not expanded; production and providers were
untouched. Task7 fixture implementation, CI/publication and full10+1 remain open.

Task7 current bootstrap marker fixtures: the five selected current tests now
expect Store `2026-10-14.1`; the fresh delivery and postfix action manifest
checks include Email in the candidate 29-type current target. Original source
IDs, mutations, rows, reopen checks, and absence assertions remain in place.
The genuine published-source delivery test retains its historical Store19
binding. Root baseline was five failures out of five, each on the obsolete
Store19 expectation; the exact five selectors now pass (5 in 2.96s). All 41
nonselected function ASTs in the four files match HEAD; scoped Ruff and
`git diff --check` pass. The owned pytest temporary root was removed and
absence verified. Fresh sequential SPEC and QUALITY review remain pending.

### Task7 current marker independent acceptance and browser baseline

Root acceptsb6004e35 plus documentation precisionc7c93669 after fresh sequential
SPEC and QUALITY PASS. Five current expectations select Store20 and the exact
29-type fresh manifest; all41 other functions, historical Store19 ownership and
substantive current business assertions remain unchanged. The five GREEN cases
and scoped checks were sufficient; no redundant rerun followed the wording-only
correction. No production publication is implied.

Separately, root ran the existing local-only audited E2E case and both
remove_accepted_control variants of the dedicated-profile continuation case
with WORKBENCH_BROWSER_TESTS=1. All three reached the original persistence-read
assertions and failed only because those reads still name retired Email tables
(3failed32.96s). This is a baseline, not a completed browser gate. Their owned
pytest root was deleted with absence verified; no task Chromium process remains.
The E2E's existing IMAP/SMTP/network substitutes were retained. No real provider
or production mutation was performed.

Task7 audited browser and E2E current reads: the selected direct-worker E2E
now reads the action plan, effect and step rows through the finite Email
projections; its account/message reads, receipt and claim binding checks,
loopback requests, private URL, browser profile and external-I/O assertions
remain unchanged. The dedicated-profile case reads all five privacy-checked
families through the same finite projections and still excludes only
`entry_url` from its persisted JSON check. The source effect table's `ROWID`
cannot be projected for this composite-key family; the finite read uses
`action_identity,effect_digest` order, and the unchanged assertion requires
exactly one effect. Root's original baseline was 3 failures in 32.96s at the
retired table reads. The first local attempt passed both browser variants but
failed at an invalid `include_rowid=True` request for effects; after using the
declared composite key, the exact three selected cases passed in 14.95s.
The tests used only their existing local loopback and IMAP/SMTP substitutes,
with `WORKBENCH_BROWSER_TESTS=1` for that invocation. The owned pytest temp
roots were cleaned. Fresh sequential SPEC and QUALITY review remain pending;
this is local test evidence, not production or provider verification.

### Task7 browser-read independent acceptance and remaining fixture baselines

Root accepts606d228c after fresh sequential SPEC/QUALITY PASS. All69 selected
assertions and68 unselected functions remain unchanged. SPEC independently
verified six complete source-shaped field sets, row keys, parameter binding and
family isolation in memory. Effects use their declared composite identity; the
original exactly-one-effect assertion makes the revised ordering equivalent.
The existing local-only three-case GREEN evidence was sufficient; no reviewer
repeated browser execution. Provider and production behavior were unchanged.

Root also established two focused remaining baselines in owned temporary roots.
The14 current source13 caller cases yielded8failed/3passed/3setup errors in17.21s;
all three fresh controls passed and the11 migrated cases stopped at the missing
paired publication before current reader/business assertions. The three prior
Store constructor tests yielded five historical failures; one additional current
delivery index-repair test failed because its final exact manifest check still
omits include_email (6failed2.50s total). These are recorded failures, not
accepted target behavior. Both roots were deleted and absence verified. The
next changes must retain current business tests while binding genuine historical
owner tests to fixed source. CI/publication and full10+1 remain open.

### Task7 Store-owned historical source tests

The three remaining Store-owned historical functions in delivery bootstrap,
reply action Store, and source13 caller chain match their original function ASTs
at fixed commit `0441cf15fafe800f9ca4d67f667e87062ca62109`. Their five parameter
children now run under three new groups in the published-source harness, with
the five exact phase5 inventory/source JSON files included in that archive.
The existing thirteen groups and 121 children remain selected unchanged. The
complete harness passed 16 groups and enforced 68 function names/126 child
cases with exact JUnit counts, zero failures, errors, or skips (83.03s).

The only current assertion changed is the delivery auxiliary repair's exact
manifest check, which now includes Email alongside Reply. The two affected
current modules passed 18 cases in 4.70s. AST comparison found only the three
historical removals and that one current assertion change; other retained
functions and helpers were unchanged, apart from now-unused imports. Scoped
Ruff and diff checks passed. Both owned temporary roots were removed and their
absence verified. This is local test evidence; fresh sequential SPEC/QUALITY,
CI/publication, and the full10+1 gate remain open.

### Task7 previous Store owners: independent acceptance

Root accepts49903964 after fresh sequential SPEC/QUALITY PASS. Exactly three
AST-exact published functions are preserved through three explicit groups and
five children; all16 groups require68 functions/126 children without skipped,
failed or errored cases. The five extra archive metadata paths close real
historical helper dependencies; app/tests/conftest bytes stay genuine0441.
No current repair behavior was hidden: its sole manifest-flag correction retains
the index drop, cold reopen and exact remaining assertions, with18 current cases
passed. Shared harness/old groups and all other retained functions remain
unchanged. No redundant rerun followed independent source/AST/archive checks.
The remaining current source13 callers still require complete coherent old-source
assembly and official paired publication. Full Task7, CI/deployment and10+1 stay
open; no runtime, production or provider change is claimed.

### Task7 source13 current-caller fixture preparation (2026-10-14)

The selected source13 current callers now construct Email51 with the published
0441 EmailStore before source13 business rows are inserted, and use the archived
Store for unrelated auxiliary declarations. The only Email declaration adjusted
afterward is the empty unsubscribe-claims table, using its independently frozen
actual SQL and indexes. Archived delivery and Store reply migrations then reach
Store19; a separate archived cold open verifies the complete Store19/Email51
pair before the official paired release publishes current Store20/Email52. The
fixture asserts the actual_no_memory_fk/actual source pair. Current business
assertions, the Meeting missing-parent rows, and malformed-source refusal stay
intact. The source fingerprint now orders genuine WITHOUT ROWID Email seed tables
by their declared primary keys.

The first focused run passed14 and failed2 in archived Email v25 initialization:
both retained intentional Meeting orphans tripped its global foreign-key check.
Moving native Email51 initialization ahead of source13 row insertion resolved
that fixture timing issue. The exact selected16 cases then passed in56.47s;
the final empty-claims precondition was checked by one selected passing case.
Scoped Ruff, diff, and AST comparisons of every selected business assertion
and nonselected test function passed. This is local test-fixture evidence only;
Task7, CI, deployment and full10+1 remain open.

### Task7 coherent current caller fixture: independent acceptance

Root accepts097f12ef after fresh sequential SPEC/QUALITY PASS. All16 selected
current cases passed, including three fresh controls and both malformed-source
variants. Independent AST normalization of479 caller functions confirmed only
authorized fixture plumbing changed; every original business assertion stayed.
The fixed published-source fixture and historical wrappers remain unchanged.
Native Email51 initialization precedes source13 row insertion, archived Store
supplies only auxiliary declarations, genuine historical transitions and a
separate archived cold process establish19/51, then exact actual/actual capture
and official publication establish20/52 before current API observations.
The intentional orphan is preserved. The native WITHOUT ROWID/PK-order helper
was independently probed alongside ordinary rowid ordering. No redundant16-case
rerun was necessary for review. Owned working roots were cleaned.

Root's separate old-owner probe confirmed genuine0441 EmailStore alone creates
Email51 and no tasks/runs/service_state/scheduled_tasks/workbench_tasks; native
pre-seeding therefore avoids copying ledger/seed/index data. Its owned database
root was removed and the archived fixture context closed normally.

At the earlier Task7 checkpoint, origin/main was ff4d36234a05f33bd9f68119f886b68e1b2b84fd,
with59 changed files since d06: newer Console query/cache removal, delivery
repair, input formatting and related evidence. Read-only production checkout
was5e52cafc8f209986868454d25ea5ba77a6be782c, clean. This supersedes the earlier
023188/d06 assumption for future integration. The local normal merge of
ff4d36234 is now being verified; no deployment has occurred. Preserve the new increments, bind the actual immediate predecessor and
revalidate affected code/proofs before CI/publication. Full10+1 remains open.

### Task7 current-main integration (local candidate)

The normal merge from the exact fetched main ff4d36234 retains its incoming
Console, Worker, delivery-repair, formatting, evidence and documentation
changes. The Email queue keeps the target finite `email_actions` projection
and the incoming actions-first `CROSS JOIN` plan. History keeps per-source
compact keys before page hydration and removes the incoming obsolete page
cache and its private test-only switch. Two reply-attempt recovery checks
share the existing grouped terminal public-ID maximum by channel,
conversation and trigger. A scalar lookup from that materialized group is
pruned from key-only pages; `EXPLAIN QUERY PLAN` shows no terminal group in
the key query and one `MATERIALIZE terminal_attempts` in the status query.
The incoming unresolved-problem grouping across channels is preserved.

The first bounded combined run had 79 passes and two failures. One measured
133,000 SQLite VM steps against the existing 100,000 bound because the joined
terminal group was evaluated for key-only pagination. Moving the same grouped
maximum into the prunable status projection passed six selected History
key/status/chart cost checks. The other failure came from comparing target
`limit=1` pages against archived 0441's page cache, which returns all three
rows for its first page. The mixed History oracle now binds to the exact clean
published predecessor 5e52cafc, retaining exact rows, counts, ordering and
page comparison plus a one-row page-size assertion. Historical schema fixture
sources remain 0441. This is local integration evidence; CI, publication,
deployment and the full10+1 gate remain open.

### Task7 current-main independent integration acceptance

Root accepts34ead3a4 after fresh sequential SPEC/QUALITY PASS. Both independently
confirmed normal merge parents,49of59 incoming paths byte-identical and the
remaining expected two runtime/three docs/five test combinations. Seven central
schema/storage/release/field-map files stay byte-identical to the candidate.
No old cache/private-switch/prewarm references remain in the inspected closure.
SPEC additionally verified160 terminal-state cases across all eight terminal
statuses and four subject states, newer/older IDs and mismatched matching keys;
public terminal IDs preceded the subjects physically, so insertion order could
not simulate the result. Its independent query-plan probe confirmed key pruning
and single status-map materialization.

The mixed predecessor oracle proves exact six-field tuples (id, source_table,
source_id, status, context, summary), totals, order and pages; it does not assert
every OperationLog field. The original79pass/2fail integration run was followed
by six corrected cost cases and two final mixed-History/Email-plan cases, all
passing. Existing limits and exact tuple equality were not relaxed. Owned probe
and test roots were cleaned. CI and renewed latest-copy/native evidence for
this merged tree, PR/publication/live readback, and the full10+1 goal remain
open. No production schema or provider action occurred in this acceptance.

### Latest main integration after Task7 (local candidate)

The candidate normally merged exact pushed main `2ae037a16bf660e8b8b2e190fd2c551ebb6057fe`
as `ff5b8f95`, then exact pushed main
`e87fb7d0442c7f71449eb3550a9c9c8d47083cfa` as `f258115a`. The first
merge combined only the overlapping claim log and the opening candidate/main
paragraphs in architecture and runtime documentation; the incoming agent/task
code and tests auto-merged. The second merge auto-combined current
`app/audit_web.py`, its runtime paragraph and Console assertions. Neither
incoming increment changed Email or Store schema declarations, paired release,
source capture, migration, or copy harness code. The candidate Email29/Store20
and History terminal materialization/cache-removal paths remain in place.

The incoming task identity update clarifies native strict-schema field
descriptions without changing validators. Audit wire parsing binds digest and
revision from the already verified candidate context, including completed-turn
recovery. Email finalizer capture doubles now implement the persisted-task read
used by terminal artifact cleanup. The later Attention increment counts and
lists current actionable, Audit-approved human decisions alongside queue
failures in one snapshot. No new audit, authorization, delivery or recovery
policy was added by the integration.

Focused post-merge verification passed: 245 task/Audit/artifact contract tests,
249 full `test_email_worker.py` tests, and 112 Console/Status/Email external
consumer tests. Scoped Ruff and `git diff --check` passed. These are local
integration results; the separate whole-copy/native proof, final Task2–6
matrix, independent review, CI and publication gates are tracked by the root
task and are not asserted here. No push, production write or deploy was made.


### Task7 latest-main integration independent acceptance

Root accepts the two normal merges ff5b8f95 and f258115a and evidence a654b522
after fresh sequential SPEC then QUALITY PASS. Incoming native Audit binding,
Task identity descriptions, terminal artifact cleanup and actionable human
decisions in Attention remain exact main behavior. The finite Email source
projection and current-plan/retry conditions remain; ten inspected central
schema/storage/release/History/field-map files are byte-identical to1921c343.
Both sides of claim/architecture/runtime conflicts were retained. Independent
review was read-only; the245/249/112 passing tests are implementer results,
not independently repeated suites. No new audit, authorization or retention
policy was introduced by these merges.

The exact29-file Task2–6 focused matrix, refreshed7301 source-copy proof and
real native Consumer/Audit approval/revision with in-memory providers are now
running against this unchanged runtime. Their results, CI, publication and
production readback remain open. No production schema or real test delivery
has occurred. The full10+1 target remains incomplete.

### Latest production-main diagnostics integration (fca229bb)

The isolated Email-actions branch normally merged exact production-main
`fca229bb363e30ab20ff5440b59d2c41e5fc4c24` after accepted `e87fb7d0`.
The incoming ten-file delta caches identical quality-scan runtime metadata
predicate results within one scan, logs slow Store contexts that reuse a read
snapshot, updates the matching runtime/health descriptions, and changes prompt
tests to inspect parsed JSON fields rather than whitespace. The claim and
runtime-document conflicts retained both branch and main content. The merge
changed no Email or Store source constructor, migration, release, binding,
provider or native lifecycle module.

Local incremental checks passed: the four incoming focused test files had
33/33 passes; the exact 14 changed worker tests had 14/14 passes. Scoped Ruff
and merge `git diff --check` passed. The first worker-node invocation selected
one truncated test name and collected no tests; the corrected invocation above
is the measured result. The root task owns the broader 29-file matrix,
fresh-source copy/native proof, independent review, CI and publication.


### Task7 diagnostics increment independent acceptance

Root accepts0d703eae and evidence2d984302 after fresh sequential SPEC then
QUALITY PASS. The exact productionfca229bb increment changes only per-scan
runtime metadata predicate reuse and slow-context diagnostics in the existing
read-snapshot connection path. Both changed functions match incoming main;
central Email schema, storage, migration/inverse, release selection, History,
copy and native harness implementations remain unchanged. The33 focused and
14 changed Worker checks are implementer results. The already-imported29-file
schema/lifecycle matrix retains its evidence scope; the bounded new diagnostic
checks cover the new code.

The7301 source-copy/native initialization runs were interrupted by root after
production advanced tofca229bb and machine load exceeded30. Both owned process
groups exited130 before native CLI/model/provider execution; no test failure
or timeout was masked. Both incomplete native working roots were removed and
absence verified. These runs are not accepted results. A refreshed latest-source
copy/native run will execute serially after the matrix. No production mutation
or real test sending occurred. CI/publication/live readback/full10+1 stay open.


### Task7 final named focused matrix

The exact29-file Task2–6 matrix completed with1996 passed and6 pre-existing
actual/fresh reference-variant skips in1490.10s, using three low-priority
workers and no new skip or relaxed assertion. It started on the accepted
f258 runtime; the later bounded diagnostics increment changed no inspected
schema/lifecycle/harness implementation and its33+14 focused cases passed
separately. Scoped Ruff passed all89 changed Python files againstfca229bb,
and the same publication diff check passed. The long elapsed time occurred
under shared-machine load; no timeout was raised to obtain this result.

Draft PR90 is attached to this task at
https://github.com/callzhang/ceo-agent-service/pull/90 (pushed8ac15d2d). Its
Quality CI38692215658 is pending, not accepted here. Latestfca229bb online
backup integrity passed:481296384bytes, Store2026-10-13.5/Email51,121 noninternal
tables, original1 FK diagnostic. The superseded owned7301 backup was removed
after this new backup was verified. Refreshed copy proof is running serially;
native approval/revision, CI and publication are still open. Keep PR draft
and do not merge main pending Derek's direct approval of the final result.

The owned focused-test root was removed after this aggregate receipt was
recorded; no historical test DB/archive is retained. Full10+1 remains open.

### Exact main 633 integration (2026-10-15)

The Email candidate worktree at `2ca1a5ed9ba34adca4a6cc6fe11d63fca8e8ab5f`
merged exact `origin/main` `633f5e59d8235826b747a2afc55b3217dd0d8611`
through a normal merge, recorded at `411d81764d25fa9fdea6719db7d9e0f33d17c6bf`.
The incoming 48-file increment is retained. Three text conflicts were resolved:
`app/audit_web.py` keeps candidate-aware Email availability and adds main's
per-reader Status timing wrapper; `docs/agent-claims.md` and
`docs/runtime-mechanism.md` retain both branches' independent entries. No other
incoming file needed conflict resolution.

Every incoming `app/` and `launchd/` file is byte-equal to `633f5e59`, except
`app/action_storage.py`, `app/quality_gate.py` and `app/audit_web.py`, which
contain pre-existing candidate behavior. The new
`REPLY_ATTEMPT_PROBLEM_TRIGGER_KEYS_SQL` assignment, `_check_reply_attempts`
function and `_read_status_queue` function are AST-equal to main. The first two
coexist with candidate Email29 schema and source variant selection; the last
wraps the candidate Email queue reader without changing its availability rule.

Bounded incoming tests passed: 86 tests across quality scan cost, inline Task
Skill selection, Task schema annotations, launchd classification, Status reader
timing, business Skill composition and Agent role boundaries (31.49 seconds);
11 selected Audit/native and changed prompt tests passed with 98 deselected
(96.94 seconds); 21 candidate Status/Email consumer/current-plan tests passed
(230.54 seconds). The third group ran slowly under shared-machine load and
finished without failure. The exact known CI 38692215658 remaining failures
belong to separate fixture/constructor work and were not broadened into this
integration. No provider, native CLI, production, push, PR or deploy action
occurred here.

The incoming `app/prompt_schema.py` at line 24 has a trailing blank line at
EOF. Default `git diff --check` reports this inherited 633 warning; its bytes
were preserved. `git -c core.whitespace=-blank-at-eof diff --cached --check`
reported no other merge whitespace issue. Local post-merge working tree was
clean before this evidence entry. Root-owned fresh SPEC, then QUALITY review,
copy/native proof, CI and publication remain open.

### Copy report when production HEAD changes after cases complete (2026-10-15)

The copy harness reads the production checkout HEAD before opening the source
and requires it to equal the requested source ref. After its four case
functions completed, a changed final HEAD previously raised before `run()`
returned, so the CLI could not serialize the completed case evidence. The
coordinator now returns the same report fields, including all four case
results, candidate and published source hashes, and both HEAD observations,
with top status `production_changed`. The CLI writes that report and exits 1;
an unchanged final HEAD retains `passed` and exit 0. Entry mismatch and case
assertions still raise. The source ref remains a non-atomic checkout-HEAD and
SQLite-backup observation; a drift report is not latest-source acceptance.

The narrow coordinator regression uses synthetic binding/marker data and
mocked Git, copy, and case functions. It proves report preservation and CLI
serialization/exit behavior only; it is not a production copy, migration,
native CLI, provider, or full Email13 acceptance result.
Before the fix, the selected RED run had 3 expected failures: drift raised at
the final assertion, and both CLI serialization cases lacked a callable entry
point; the other 3 selected controls passed. After the fix and exact four-case
mapping assertion, those 6 selected tests passed in 0.43 seconds. Seven
adjacent current-fact loss, target public write, and old History locator
guards passed in 82.72 seconds. Scoped Ruff and `git diff --check` passed.

### Root acceptance: main633 integration and completed-report preservation

Root accepts the exact main633 integration at411d8176 plus evidence14f53a39
after fresh sequential SPEC and QUALITY PASS. Reviewers independently verified
the merge ancestry, all48 incoming files, exact new SQL/functions and complete
prior candidate behavior outside those changes. The118 focused passes remain
implementation evidence; no schema/source-binding/copy/native behavior was
changed by the integration. The inherited prompt_schema blank EOF warning is
explicitly retained, not reported as a clean default publication diff check.

Root separately accepts06507ce1 after fresh sequential SPEC and QUALITY PASS.
QUALITY independently reran6 coordinator/CLI checks successfully. This accepts
only completed-result preservation and unchanged failure boundaries; the failed
fca copy invocation cannot be retroactively turned into a passing JSON report.
Actual refreshed source-copy/native cases and CI still need acceptance.

Capacity limits are recorded in database-redesign-email-capacity-20261015.md.
CI38692215658 remains failed, with38 source-addressed Audit fixtures plus8
historical/current schema mismatches and1 unresolved Linux trace diagnostic.
The next bounded unit preserves genuine0441 historical tests, corrects only
current refusal expectations, and adds diagnostic evidence without widening
the read-only SQL whitelist. Incoming main131ae6a8 meeting changes also require
integration before final publication. PR90 remains draft and unmerged;
production writes, provider sends and full10+1 acceptance have not occurred.

### CI38692215658 schema fixtures and Linux trace visibility (2026-10-15)

The focused pre-change run reproduced the eight remaining schema-fixture
failures and four passing cases: four historical Store19 constructor assumptions
failed against the current Store20/Email29 factory, and only the four `unknown`
marker variants of the partial Workbench source test failed because the paired
source guard correctly reports `unsupported or mixed Store/Email source schema`.
The four `None` variants already passed with `unsupported or malformed`.

The four factory test functions now run unchanged from exact published source
`0441cf15fafe800f9ca4d67f667e87062ca62109` through the existing strict
JUnit archive harness (one postfix, two reply, one fresh reply). The original
partial Workbench source test also runs there with all eight parameter cases.
All five archived function ASTs match that published ref; the three current
modules retain every other function AST. The first archive run passed 13 cases
and failed one postfix case because source extraction omitted the archived
`2026-10-13-database-redesign-phase5b-fresh-source.json` manifest read by its
helper. That exact file exists at the same published ref and is now in
`SOURCE_PATHS`; no current manifest was substituted. The archive harness still
requires exact identities and counts and rejects failures, errors, and skips.

The current Workbench test retains its eight calls, database fingerprint,
exception type, and no-mutation readback. Its expected diagnostic now follows
the observed marker: `None` requires `unsupported or malformed`; `unknown`
requires `unsupported or mixed Store/Email source schema`. The current fresh
Store20 assertion also checks that retired reply and memory tables are absent.
The Email journal test retains the same allowed-statement predicate and all
other journal/schema/transaction checks; on failure it now prints only rejected
normalized trace statements plus Python and SQLite versions. The old Linux CI
log did not contain the offending SQL, so this change does not establish a
Linux fix or authorize a broader whitelist.

After the manifest extraction correction, the four strict archive groups and
the affected current files passed together: **89 passed in 130.21 seconds**.
Scoped Ruff and `git diff --check` passed; archived function AST and remaining
current function AST preservation checks passed. This is local fixture and
diagnostic verification only. A new Linux CI run must reveal or clear the
journal trace; real-copy, native, publication, and full Email13 acceptance
remain with the root release owner.

### Root acceptance: historical/current CI fixture separation

Root acceptsd91f7c4c after fresh sequential SPEC and QUALITY PASS. Both reviews
independently checked the five original ASTs, strict12-child archive wiring,
unchanged unselected functions, current refusal/fingerprint assertions and
the identical journal permitted-SQL predicate. SPEC separately collected and
passed the same89 cases in151.56 seconds. QUALITY performed source review
without a third duplicate run. This accepts fixture preservation and diagnostic
visibility, not a Linux journal repair or a passing full CI.

SPEC removed its exact owned default pytest root after recording the result;
archived-source TemporaryDirectory roots self-cleaned. Implementer recorded
RED root6044 and the failed archive root are absent. Later compact pytest
output did not establish a separate exact default root for its89-pass run;
no unrelated numeric pytest directory was attributed or deleted by timestamp.
Report-repair roots also have no surviving matching cases. Future test runs
must explicitly use an owned /tmp basetemp rather than rely on default retention.

Next: preserve exact main5aa6909a (meeting runtime131ae6a8 plus publication
evidence), then push the candidate and obtain fresh CI and actual source-copy/
native acceptance. Existing capacity increase and full10+1 remaining scope
remain explicit. No production write, provider operation, main merge or deploy
is accepted by this local test unit.

### Exact main 5aa6909a integration (2026-10-15)

The isolated Email-actions branch at `d5abeaae079f608f9d414b8447c8551b52728c84`
claimed this merge in `14fee9e4` and merged exact main
`5aa6909ac79dec1f51bd5b75d86691b5ed24f13b` with a normal two-parent
merge. The prior main merge base was `633f5e59d8235826b747a2afc55b3217dd0d8611`.
The incoming increment contains 24 paths: 23 meeting/personnel runtime, Skill,
test, eval and documentation paths through `131ae6a8e289cb63638c17e9f345d5c9fc17ad84`,
plus one release-evidence file in `5aa6909a`. Git merged without conflicts.
All incoming paths except shared `docs/agent-claims.md`,
`docs/architecture.md` and `docs/runtime-mechanism.md` are byte-identical to
`5aa6909a`. Those three shared documents retain both the incoming meeting
contract and the candidate's Email redesign notes. The central candidate
`app/action_storage.py`, `app/store.py`, `app/email_store.py`,
`app/action_email_schema_release.py`, `app/audit_web.py` and
`scripts/validate_action_email_copy.py` are byte-identical to their
pre-merge `14fee9e4` versions.

Focused validation used `/Users/derek/miniforge3/bin/python -m pytest -q`
with an explicit owned `--basetemp` under `/tmp/ceo-email-main-5aa.J0NPdD`
and these files: `test_group_discovery.py`, `test_meeting_alignment.py`,
`test_meeting_alignment_agent.py`, `test_meeting_alignment_delivery.py`,
`test_meeting_audience_eval.py`, `test_meeting_member_precision.py`,
`test_meeting_alignment_models.py` and `test_meeting_recovery_safety.py`.
Result: **415 passed in 139.76 seconds**. Scoped Ruff on the seven incoming
Python implementation/script files and six changed test files passed. Merge
index, working-tree and main-diff whitespace checks passed. The earlier
`app/prompt_schema.py` EOF blank-line observation remains inherited from
main633 and was not changed or represented as an integration fix.
The exact owned basetemp directory was removed after the aggregate receipt;
its absence was verified.

This unit is local integration evidence only. The full Email redesign,
fresh Linux CI, actual source-copy/native validation, push and publication
remain with the root owner; this unit made no provider or production write.
