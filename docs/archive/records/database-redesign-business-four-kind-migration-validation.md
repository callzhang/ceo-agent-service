# Four-kind Business finite migration validation, 2026-10-15

This commit adds an unselected source-bound migration and inverse for Task,
Project, Anchor and Project context revision. It adds three VIRTUAL typed public
aliases and full UNIQUE indexes to the existing Business declaration. The
fifteen retained children keep their public ID columns; nineteen FK endpoints
move to those aliases, while twenty-one other FK groups keep their source
parents. No Store constructor, caller, coordinator, version marker, provider,
production database, push or deploy selects this code.

The fresh fixture in `tests/fixtures/business_four_kind_fresh_declarations.json`
was generated from the current `AutoReplyStore` constructor before any Store
bootstrap change. It contains declarations and auxiliary SQL only, with no
private rows. Actual tests reconstruct the accepted actual parent and Attention
DDL from the source oracle, retain the verified identical auxiliary declarations,
then recheck the full actual fingerprints. The accepted oracle is
`docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json`.

The focused migration tests use native SQLite transactions and both actual and
fresh declarations. They populate all four parent kinds and all fifteen child
families with synthetic facts, including colliding signed public IDs, a two-row
Task merge cycle, Project/Anchor/Context references, BLOB and infinity storage
classes, nullable edges, six hidden child ROWIDs, and a deleted high child
sequence value. They compare every source-shaped value and `typeof`, all 40
child FK groups, 29 child indexes, three date triggers, four parent and nine
child sequence waters, `quick_check`, and native FK findings. Each translated
endpoint rejects a wrong-kind parent; referenced parent deletes and public-ID
updates fail under native FK enforcement. Inverse includes target writes,
deletion and Project rebind made after preparation, then reforward reads them
back. Empty sequence rows stay absent. A preexisting unrelated `runs/tasks`
FK finding remains identical. Malformed source/target declarations, broken moved
references, tampered inverse metadata and injected DDL failure are refused or
rolled back without a committed schema change.

Each of the nineteen endpoint groups also has an isolated native FK control
under both actual and fresh declarations. The test inserts only the intended
child reference to its target parent, counts exactly one incoming reference to
that parent, and verifies both parent DELETE and public-ID UPDATE fail. A
same-kind parent with no child reference accepts both mutations. The Task
relation's two endpoints use different Task parents; the immutable date child
is tested by insertion. A process-local negative control removed only the
`business_task_evidence.task_id` target FK from translated DDL. Its isolated
parent DELETE and public-ID UPDATE then succeeded, and the isolation assertion
went RED with `DID NOT RAISE sqlite3.IntegrityError`. Restoring the unchanged
translation made both source variants GREEN. This closes a test gap; no
application implementation was changed for this review finding.

A later QUALITY regression reproduced a real inverse data-loss path: after a
valid forward copy, a TEMP table named `business` copied from MAIN had only
its Task title changed. The old inverse read the unqualified Business
projection and wrote that TEMP title into `main.business_tasks`; FK and
`quick_check` still passed. Actual and fresh regressions were RED with the
wrong title. Migration projection reads now use the same finite source-field
map against `main.business`, leaving public runtime projection strings alone;
both inverse regressions are GREEN. Native SQLite also resolves unqualified
index DDL `ON business` or `ON` a rebuilt child through a same-named TEMP
relation, causing failure after the first DROP. Forward capture now refuses
TEMP `business` or one of the fifteen child destination names before DDL;
inverse refuses TEMP names of its four parent or fifteen child destinations
before DDL. The two inverse shadow cases and six exact forward/inverse
collision cases are GREEN. A TEMP `business` during inverse remains supported
and cannot change MAIN source facts.

An independent native SQLite control creates `main.business(public_id)` and
`temp.business(shadow_marker)`; unqualified `CREATE INDEX ... ON business(public_id)`
raises `no such column: public_id`, proving why the destination-name guard is
needed even though the row-copy queries are MAIN-qualified.

This is code and local SQLite validation only. Selecting the coupled
Store/Email release, replacing current callers and bootstrap, copied-database
proof, CI and deployment are later tasks under the reviewed integration plan.


## Root acceptance and current candidate reconciliation

The corrected complete integration plan at `6b0fbcfd` passed sequential
independent SPEC and QUALITY review. Its Task4 copy harness now explicitly
partitions moved/rebuilt/unchanged families, compares source-shaped Business
values/types/ROWIDs/thirteen waters, binds code/proof paths and accounts for the
combined net-sixteen table reduction. That review establishes implementability,
not a copied-database acceptance result.

Implementation SPEC first found a missing isolated parent-mutation proof;
`0df6d3c8` supplies all nineteen endpoints in actual/fresh variants. QUALITY then
reproduced silent TEMP-shadow parent data loss. `f677f53f` fixes only migration
fact reads and necessary DDL destination collision handling. Fresh SPEC passed
the nine new cases and all485 focused checks; subsequent QUALITY passed449
migration/storage checks and found no remaining concrete related blocker.
Both reviews kept the module unselected. Public runtime projections remain
unchanged. This closes Task1 local implementation acceptance only.

Root normal merge `fd4b29f9` incorporates reviewed Email head `82795db4` and its
`origin/main` c2aa8082 increments. The only conflict was the claim ledger, where
both sides were retained. Business modules remain byte-for-byte those reviewed
at f677f53f. Root's combined Business migration/storage/source plus meeting
Memory fixture checks passed **529 tests in 20.25 seconds**. Email-only complete
Quality run38748142018 remains separate evidence and cannot establish Business
integration. No main merge or production deployment occurred.

Read-only production measurement at 2026-10-15T14:17:59.205604Z still found121
noninternal physical tables and485036032 main-file bytes; all four old Business
parents remained. No production database copy or migration was performed for
this Task1 local acceptance. The approved10-core-plus-ledger target still needs
Tasks2-4 application/paired-release/copy integration, remaining families and
final retirement/publication.
