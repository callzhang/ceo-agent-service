# Canonical Email unselected component plan

> Required workflow: subagent-driven-development. One fresh component author,
> then fresh SPEC, then fresh QUALITY; repairs return to the same author and
> reviewer. Root owns actual-data proof. Do not select this schema in callers
> until the component has passed its separate gates.

Goal: replace current13's two native Email parent tables with one canonical
`emails` row per existing stable identity, yielding an unselected12-table graph.
Preserve current source facts and public identities, not two rows distinguished
by record_type. This component does not retire source bodies or change runtime
callers, provider operations, business policy, review or authorization behavior.

Worktree: `/Users/derek/.codex/worktrees/database-redesign-provider-state/ceo-agent-service`.
Preserve accepted dirty prerequisites and every other author's hunks. Read shared
AGENTS, local claims, architecture/runtime and approved original design first.
No production/source database writes, commit/push/deploy, native Agent CLI,
provider/model calls or real sends. Use temporary synthetic/copy databases only.

## Prerequisites before dispatching implementation

1. Accept the normal uninstrumented full116→13→116→13 copy proof after reviewed
   bounded900s repair. The earlier diagnostic1200s forward is not acceptance.
2. Capture an exact current13 source capsule: both native table declarations,
   all owned indexes/triggers, complete current FK graph, Actions/Business/Legacy
   declaration hashes for actual and fresh variants, source sequence presence
   and waters. Record code/review/copy pins. Do not assume the earlier15-table
   Actions declaration is still the source.
3. Keep recorded9,227 pairs distinct from this new consistent snapshot. Measure
   unmatched IDs and raw shared values/types on the snapshot. Unexpected source
   shapes must be reported, not silently normalized by migration.

## Frozen source capsule after actual13 acceptance

Normal actual13 receipt SHA256
`6b8c310cf83694000ee671400f17136452452f0ae79edf4dbe39c0bd28a428fc`
passes1558.671s116→13→116→13. Exact next source capsule
`docs/superpowers/specs/2026-10-17-email-canonical-current13-source.json`, SHA256
`79f04ccf0dcf7fcc7c6c1b903ce82df03f10f0f9bf19f299bc672d4988560eff`,
records49fields,12ownedtriggers and the one physical Actions FK used by seven
classification-linked kinds. Fresh synthetic capture PASS26.480s; managed temp
removed and371runtime pins stable. No new production read.

The first source capture stopped at an invalid actual=fresh declaration
assumption; no capsule written and managed temp removed. Measured differences:
five classification trigger bodies differ in indentation; actual Message DDL
has in_reply_to/references_json appended by historical ALTER, while fresh DDL
places them after rfc_message_id. Preserve both exact declaration/column-order
profiles, not one whitespace-normalized oracle. Other column metadata, indexes
and FKs compare equal. Capsule explicitly separates actual source and fresh
oracles; this difference does not invalidate the accepted current13 proof.

Default13 Actions object hashes: actual_no_memory_fk
`9b02487d85dab8e2bfef6be1fe61cd1833d7ed73df1e00202fc83b4d09566658`,
fresh_memory_fk
`b4ff6ce904b6c1d3fb4c73774477c9595cfeb4dd7401e25b758610f74aebaa16`.
Preserve these defaults in component code. Capsule fresh sequence metadata is
fresh-only, not the actual snapshot message water; migration captures the exact
live source water at invocation and must restore it plus newer target writes.

## Exact field disposition, no new business facts

Existing DDL has30 classification and19 message fields. Shared source facts for
valid pairs are only `account_id`, `stable_message_identity`, `uidvalidity`, `uid`.
The remaining26 classification-side and15 message-side facts stay independently
projectable: total45 source facts. The exhaustive groups are in the root source
analysis section “49→45 facts”; verify them against actual capsule columns.

Both raw `folder` values remain distinct: durable validation compares trimmed
folders. RFC/thread normalization likewise does not erase raw NULL/empty/string
facts. Four headers and timestamps can legitimately differ. Preserve the two
message timestamps in this unit; cancellation remains a separate disposition.
Keep current normalized_text and model_text readers functional. No text archival,
new training corpus, fallback body copy or assumed source availability.

Only approved structural representation is permitted: physical row identity,
exact source identity aliases, finite payload/projection paths as needed. Prefer
existing native columns for large bodies over repeatedly escaped stored copies;
use virtual generated projections/indexes when needed. Do not add new business
fields, a generic source framework or compatibility views keeping old tables.
Do not choose physical IDs from a single mixed namespace without proving alias
collisions. Exact final SQL must be explicit and tested, not a generic JSON store.

## Task 1 — identity, type and lifecycle RED cases

Claim exact new component/test paths before edits; root captures immutable source
metadata separately. Keep historical source factories and current13 constructors
unchanged. Build source fixtures from exact source DDL, not the new target code.

Retain failures for paired migration, message-only→classification, both alias
spaces, deterministic preference for existing classification IDs during paired
migration, physical-ID collisions, explicit negative/zero keys, ordinary NULL
classification IDs, native INTEGER affinity/binding errors, ordinary INT64MAX
allocation, independent message AUTO stream after deletes and INT64MAX failure.
Source public classification IDs use the existing positive63bit SHA-derived
value; do not replace them or retarget existing action foreign keys to a message
identity. The source message identity also determines thread-order tie breaking.

## Task 2 — finite schema and source-shaped mutation primitives

Implement an opt-in standalone component only. One row owns both projections;
classification is optional while a message is present. Source DDL permits more
raw shapes than durable startup; distinguish source constraint parity from valid
public lifecycle rather than silently adding new global checks. Preserve source
NOT NULL/default/CHECK/unique behavior per projection and exact SQLite storage
classes using existing native scalar encoding/coercion where needed.

The physical ID and the two public IDs have distinct roles. Preserve ordinary
classification allocation and independent message AUTO water including deleted
maxima. Existing Actions allocators query Actions explicitly, so do not call them
unchanged against Emails. Reuse generic SQLite-native coercion and named sequence
mechanics; keep writer locking and caller transaction boundaries. No helper-owned
commit, retries, read-path repair or new retention/status/policy.

Seven existing Actions classification endpoints must reference exact classification
identity. Rebuild only what dependency changes require; preserve retained
Business/Legacy table rows, five existing inbound Actions edges, IDs and waters.
Validate known declarations exactly and reject unknown MAIN/TEMP dependencies
through the existing SQLite parser approach, not a global dependency bypass.

Translate the12 native owned triggers with projection-aware OLD/NEW values:
five status/source/inclusion, three class plan-switch and four message blockers.
A message-only update must not accidentally execute classification-only logic.
Preserve existing exceptions/status behavior, no extra safety gates. Record exact
index/FK/trigger disposition from the source capsule.

## Task 3 — reversible component migration

Metadata contains declarations/counters/identity mapping only where required,
never a source row or body backup. It must survive JSON roundtrip. Forward maps
current source values, inverse reconstructs current target facts including writes
made after conversion. Preserve all source IDs, hidden-rowid semantics where
applicable, sequence waters, original DDL and original FK findings.

Synthetic proof: source13→unselected12→component mutations→inverse13→genuine
source reads/writes→reforward12. Cover message-only and laterclassification,
raw folder/RFC/thread/header distinctions, nullable/real/blob values supported by
source, conflict modes and SQLite defaults, caller rollback/outer transaction,
unknown dependencies, no accidental changes to retained Actions/Business facts.
Add meaningful focused tests; no full serial suite on the live machine.

## Task 4 — review and separate caller unit

Document actual final field paths/constraints/indexes, test RED/GREEN and scope.
Freeze exact component/test/requirements/runtime pins; fresh SPEC then QUALITY.
Do not activate current12 in AutoReplyStore, EmailStore, release selectors or
query callers in this component. The separate caller unit must update actual
EmailStore SQL plus AutoReplyStore reply/history joins, audit_web queue/failure
joins and email_training_labeler stable identity query. It must preserve atomic
persist_scan_result message/classification/plan/cursor writes and public output.

Root actual-copy/genuine-predecessor proof follows that separate selected caller
unit and its reviews. No deployment before final11-table, source retirement,
capacity, rollback, nativeCLI with fake sends and UI gates are fulfilled.


## Exact FK interpretation resolved before component freeze

Original design section3 requires deterministic physical target IDs and direct
FKs, but does not mandate that every FK target the physical PK. The seven CSV
classification relationship mappings retain actions.classification_id, while
the CSV is directional and leaves finalSQL explicit percomponent. Root selects
the minimal direct endpoint: unchanged Actions public classification numbers
reference the canonical table's exact classification alias, backed by a FULL
UNIQUE nullable key (not a partial unique index). Physical emails.id remains
deterministic/classification-preferred during migration. Message-only rows have
no classification alias and cannot satisfy an Actions classification FK.

Here rewriting references means rebinding the parent endpoint to canonical
Emails, preserving the exact source public number. No duplicate Actions email_id
is invented solely to replace that existing relationship. Preserve existing FK
NO ACTION/RESTRICT; prove a message-only physical-ID collision cannot attach an
Actions row to the wrong email, and that source current/inverse IDs remain exact.
This resolves a technical representation choice, not a new business fact/policy.


## Source operation scope, clarified before review

Conflict parity covers real current caller operations: Message UPSERT on stable
identity updates only its existing seven fields, and the implemented source-shaped
IGNORE mode preserves native AUTO water burn. Failed statements/outer rollback
must match native semantics. The component is not a general compatibility layer
for arbitrary raw SQL issued against former physical table names. In particular,
raw REPLACE on a combined canonical row would delete both source projections,
whereas the old Message REPLACE deletes only Message; no current public caller
uses that mode. Do not add an unneeded generic REPLACE API or new policy to
simulate it. Preserve finite source-shaped lifecycle mutation primitives and the
current-fact migration/inverse contract needed by the actual caller unit.
