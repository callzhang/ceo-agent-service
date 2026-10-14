# Phase5E source refresh and execution boundary

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
Source `UPDATE` binds all inputs and acquires a writer transaction before
checking row constraints; a missing row or false predicate returns zero even
for an invalid public ID or unknown fresh Memory parent. The E update path now
binds inputs, locks and checks the exact predicate before row-specific key or
parent resolution. Fresh Memory explicit insert likewise acquires its writer
transaction before resolving a typed parent, retaining native failure state
for deferred, autocommit, outer transaction and savepoint callers. Focused
source-versus-target regressions cover those modes and unbindable inputs; the
full local gate passed 397 tests with eight variant-specific skips, with Ruff
and `git diff --check` passing. This remains opt-in foundation evidence only.

## Earlier source preparation

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
