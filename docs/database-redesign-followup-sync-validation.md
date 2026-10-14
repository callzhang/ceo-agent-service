# Follow-up/Todo-sync action storage validation

## Task 2B disabled pure conversion

The isolated candidate now has a pure forward conversion from the two frozen
Email29 plus nine-source variants and a paired inverse. Both require a
caller-owned transaction with foreign keys disabled. Forward recaptures the
complete source before its first write, copies all 150 fields and retained
Email29 actions, maps the business send-attempt to its physical draft, rebuilds
`business_legacy_links` with only its work-update FK pointed to `actions`, and
preserves source allocators including absent and recorded-zero rows. The
retained link has typed-parent insert/update guards and a public-ID update
guard. The inverse reconstructs the literal actual or fresh source DDL from
current action/link rows and current allocator waters, including writes and
deletions after conversion. Neither function changes Store or Email markers.

Synthetic actual/fresh fixture tests exercise forward, current-fact inverse,
old-source writes and re-forward, all nine families, 150-field projections and
SQLite storage classes, TEMP shadows, retained links, raised/deleted maxima,
and caller rollback after injected copy/rebuild/drop errors. This remains an
unselected storage candidate. The eventual release must bind the then-published
predecessor and complete caller conversion, actual-copy/old-code, CI,
performance, and live readback gates before any publication claim.

Task 1A is an unselected, disabled candidate. The explicit `include_followup_sync=True` declaration extends only the reviewed Email29 actual/fresh action scaffold. It defines nine typed record kinds, source-affinity `sync_` projections for the 150 source fields, 24 corresponding index declarations, eight source CHECKs, four retained external business foreign keys and one typed internal draft foreign key. The internal draft public ID is projected through its parent action. Existing shared task, run, candidate and Email reference slots are NULL for the nine kinds. The default flag leaves earlier manifests unchanged.

The source of truth is the accepted actual/fresh capture and 150-row field map in `docs/superpowers/specs/`. Direct target SQL tests cover every field in both layouts, index key shape, unique business dedupe, provider ID uniqueness distinction, the typed draft relationship, source public-ID blocking while referenced, raw JSON bytes, NUL, BLOB and nonfinite scalar values. The focused test started RED because the new definitions module was absent, then passed after implementation. SPEC review found that the legacy send-attempt `draft_id` NOT NULL check had been skipped with the business physical-FK exception. A native source comparison reproduced four RED cases (NULL and SQLite-bound NaN in both variants); the scoped correction and six legal zero/negative/dangling scalar cases passed. The final follow-up storage, source oracle and Email storage command passed 47 selected tests. All eight earlier declaration variants (6/9/12/14/actual and fresh 16/actual and fresh Email29) compared byte-identical with committed HEAD source before this scoped correction, which changes only the opt-in declaration. Ruff and diff checks passed. This is declaration and projection evidence only: source-shaped omitted defaults, writer locking, native allocation, mutation parity, migration and live caller behavior belong to later tasks. No constructor selects this schema, and no Store version or production migration is claimed.


## Task1A independent acceptance

The initial declaration4143fe11 lost the NOT NULL constraint on legacy
follow_up_send_attempts.draft_id by exempting every draft_id field. Independent
SPEC compared3,666 native source/target scalar cases and found exactly four
mismatches: NULL and SQLite-bound NaN in each variant. Repair1cfeb2fe scopes
the exemption only to the business attempt's physical reference. Four RED
comparisons became green; legal zero/negative/dangling legacy values remain
unconstrained scalars. No extra legacy FK was introduced.

Sequential independent SPEC then QUALITY accepted1cfeb2fe. SPEC rechecked
20 native FK-on scalar boundaries,36 all-nine negative/zero-ID projections,
150 exact field declarations/defaults/order,24 index semantics,8 CHECKs,
the2-valid/14-refused option matrix and8 unchanged prior SQL manifests.
The selected implementation/source suites passed47 checks. QUALITY passed
11 focused checks with6 deselections and found no blocking value/constraint
counterexample. Counts overlap; they are not additive coverage totals.

QUALITY did identify a concrete performance gate before activation: the new
sync_draft_action_id native self-FK has no supporting child index. SQLite
3.53.4 scans the shared actions B-tree for native parent deletion. With0/100/
1000 unrelated cancellation rows, Email29 DELETE costs3278/29578/266278 VM
steps and Followup38 costs4387/30987/270387; the added loop is3 VM steps per
unrelated row. Literal source business-draft deletion remains29 steps with
0 or1000 unrelated work_updates. The24 source indexes do not contain a draft
child-key index. A measured supporting target index must remove this unrelated-
type growth before release; existing Email29 cost is a separate baseline, not
evidence that native FK access is already bounded. Task6 must include native
FK parent DELETE/public-key mutation as well as explicit application SELECTs.

Task 1A accepted only disabled declarations/projections. Source-shaped writers,
omitted defaults, lock/allocation semantics and mutation parity were assigned
to Task 1B below.
No constructor/version/migration/caller/provider or publication is accepted.
All review fixtures were in memory or cleaned owned temporary directories.

## Task 1B disabled writer candidate

The nine-family writer API is `insert_sync_action`, `update_sync_action`, and
`delete_sync_action` in `app/action_storage.py`. It accepts the original field
names and returns source public IDs, row counts, or SQLite update/delete cursors.
The actual/fresh source variant is inferred only from the two accepted Email29
action declarations. Omitted defaults are evaluated after the writer lock;
explicit NULL and SQLite-bound NaN remain explicit values. Each source family
has a separate `actions:<kind>` sequence that advances on source-shaped
inserts and does not advance on public-key updates. The business send-attempt
draft ID is resolved against the source-affinity public parent ID and stored as
one physical action reference. Legacy send-attempt draft IDs remain raw source
scalars.

The first focused writer test run was RED with six setup failures: its minimal
task/run FK stubs lacked the `record_type` columns required by the existing
action triggers. After correcting the fixture, the native source comparisons
passed. The writer/source/declaration/Email selection is 95 passed, including
48 writer comparisons across actual and fresh layouts. They cover all nine
families' omitted defaults, source storage classes, update/delete/CAS, raw
BLOB/NUL/nonfinite values, independent IDs, signed/explicit IDs, ignored
unique conflicts and sequence rollback, source-affinity draft references, and
native SQLITE_FULL whole-transaction rollback. Ruff and diff checks passed.
These are synthetic database checks; source migration, caller conversion and
live behavior remain unselected. The measured physical child-FK scan described
above remains a Task 6 performance gate before activation.

SPEC review of `8b49248f` found two additional source counterexamples.
First, the initial BEFORE typed-parent trigger aborted `INSERT OR IGNORE`
before SQLite could skip a business send-attempt row with a NULL/NaN draft,
invalid state or duplicate claim token; native source still raised its
per-family sequence to the explicit attempted ID. A genuinely unresolved
non-NULL draft must still fail even under `OR IGNORE`. The repair moves the
typed-parent check to AFTER row constraints. A missing non-NULL source parent
uses a nonmatching BLOB physical endpoint for that statement: SQLite integer
rowids cannot match it, so it cannot silently identify another draft or be
persisted as a valid parent. Native CHECK/UNIQUE/NOT NULL can skip first;
otherwise the existing typed check and physical FK reject the write. Both
source layouts passed the previously RED missing/NULL/NaN/unique combinations
and preserved sequence presence and value.

Second, one SELECT per omitted default let a boundary between seconds make
`created_at` and `updated_at` disagree within one insert. All omitted default
expressions are now evaluated in one SQLite SELECT under the writer lock,
matching SQLite's one-statement timestamp behavior while preserving the actual
layout's empty `follow_up_drafts.updated_at`. A trace-delayed two-clock-read
regression was RED in both layouts and is GREEN. The final focused selection
is 107 passed across source oracle, target declarations/writers and Email
storage; Ruff and diff checks passed. Task 1B awaits fresh sequential SPEC
then QUALITY acceptance. No migration, activation, release or provider action
was performed.


## Task1B independent acceptance

Writer8b49248f initially failed two native comparisons: BEFORE typed-parent
guards aborted rows that source INSERT OR IGNORE skipped, and separate SQL
default reads split one row's timestamps. Repair9402291b moves child guards
after row constraints, distinguishes real SQL NULL from an unresolved non-NULL
parent through an unmatchable BLOB physical endpoint, and evaluates omitted
defaults in one SQLite SELECT under the writer lock. Under FK-on the invalid
endpoint cannot commit; it is not a fabricated business identity. Native
ignored NOT NULL/CHECK/UNIQUE rows keep their sequence advancement; genuine
parent FK failures remain non-ignorable. Ten RED comparisons became green.

Sequential independent SPEC then QUALITY accepted9402291b. SPEC passed107
selected checks,8,700 literal-source scalar UPDATE comparisons across150
fields/both variants,18 all-nine allocator-order probes and18 all-nine
SQLITE_FULL whole-caller rollback probes. All8 earlier SQL manifests remain
byte-identical. QUALITY passed34 selected checks,62 independent CAS/transaction/
storage assertions and32 signed physical-parent/FK assertions, with no exposed
API success/state/transaction counterexample. These are overlapping evidence
sets, not additive test coverage. Owned review fixtures were in memory or
cleaned temporary directories; no provider/native/production work was used.

The representation preserves error classes and caller behavior; it does not
claim identical extended SQLite error diagnostics for all mixed-invalid inputs.
The parent public-key BEFORE guard may report FK before another failed CHECK/
NOT NULL on a combined invalid UPDATE. Both sides fail with unchanged facts
and transaction state; the API has no UPDATE IGNORE mode and no relevant
current caller depends on that diagnostic priority. No new policy or field
was introduced to imitate a diagnostic subclass.

The known native child-FK scan remains a mandatory Task6 gate. A second measured
plan item is the physical-parent resolver: it filters the kind's public-ID
values after a record_type seek, whereas adding the existing numeric-scope
predicate enables the covering public integer-ID expression seek. Resolve both
with source/target growth evidence before activation. No query budget is
claimed from these writer-semantic checks.

Task1A/1B storage is accepted only as disabled preparation over the reviewed
Email29 actual/fresh scaffold. Source capture/pure forward/current-fact inverse
are next. Actual published predecessor binding, constructors/callers, paired
release, copy/old-code/native acceptance, CI and production remain required.

## Task2A read-only source capture candidate

`capture_followup_sync_source(db)` binds the exact reviewed Email29 action
declaration and the actual/fresh nine-family source pair. It reads MAIN-qualified
table/index/FK metadata, counts, the nine native AUTOINCREMENT waters, retained
`business_legacy_links` metadata and water, action allocator metadata, and
the business Task/Signal parent declarations in the caller's transaction. Counts
and public-ID locator modes are metadata; no business rows or payloads are
copied into the result. Absent and zero sequence rows remain distinct.

Preflight rejects altered declarations, unknown action records or allocator
aliases, extra inbound FKs, target-name collisions and external MAIN/TEMP SQL
dependencies through SQLite's parser on a disposable in-memory schema. TEMP
shadow tables can remain while all actual reads still qualify MAIN. A TEMP
trigger originally attached to MAIN is rejected even if a later TEMP table
shadows its owner name. The parser performs no DDL on the caller connection.

The focused initial test was RED because the capture module did not exist.
After implementation, `tests/test_followup_sync_action_migration.py` plus the
accepted source oracle passed 40 tests on both frozen variants, including
mode=ro/query_only, tuple and Row results, absent/zero/high sequence state,
schema refusal and TEMP binding cases. Ruff and diff checks passed. This is
Task2A metadata capture only: it does not implement forward/inverse migration,
bind a published predecessor, select a version, alter a constructor, or make a
production change. Independent SPEC then QUALITY review remains pending.

### Task2A SPEC dependency repair

SPEC reproduced a falsely accepted external trigger: an unrelated trigger
using a reserved Email trigger name could read `actions`,
`business_legacy_links` or `work_updates`, while the shared Email parser probe
unconditionally exempted that name. The helper now takes its exemption list
from its caller. Email retains its existing finite list; follow-up capture
passes an empty list so SQLite's rename probe sees the dependency. The helper
also enumerates virtual tables through explicit `main.table_list` and
`temp.table_list` PRAGMAs. Six actual/fresh trigger-reference cases were RED
before this change and GREEN afterward. The focused follow-up source and Email
migration selection passed 132 tests. This remains an unselected, read-only
candidate pending fresh SPEC then QUALITY review.

### Root Task2A acceptance

Read-only capture at b6214a28 passed sequential independent SPEC then QUALITY.
SPEC reran132 scoped checks and eight additional actual/fresh inbound-FK and
caller-state probes. QUALITY ran123 follow-up checks plus86 Email migration
checks, and16 independent native parser probes covering quoted/indirect MAIN
and TEMP dependencies, harmless CTE/literal names, local TEMP shadows/FTS and
the historical MAIN-owner TEMP trigger followed by a shadow. No caller
transaction/settings, schema, sequences or total_changes changed in those
probes. The earlier two blockers are resolved. Task2B forward/current-fact
inverse, actual published predecessor binding and runtime activation remain
unimplemented; this accepts only disabled Task2A.
