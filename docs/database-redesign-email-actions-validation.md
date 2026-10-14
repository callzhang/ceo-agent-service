# Email action storage preparation: Tasks 2A and 2B

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
