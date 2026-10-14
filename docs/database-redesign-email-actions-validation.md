# Email action storage preparation: Tasks 2A and 2B

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
