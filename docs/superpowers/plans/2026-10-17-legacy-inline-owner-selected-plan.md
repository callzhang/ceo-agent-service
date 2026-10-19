# Legacy import: selected direct-owner implementation direction

Selected for the next bounded implementation unit, after current Email tests
and their independent reviews finish. This document changes no runtime/schema.
It supersedes the all-12-field preservation recommendation in the earlier
unselected import-result plan. It does not claim final11 or production readiness.

## Authority and evidence

Derek approved the October11 design: verified ownership directly referenced on
its owner, legacy_key for old identity, no generic relations. Derek explicitly
authorized cancelling illogical/redundant/no-value functions or fields. This
scope is already authorized; cancellation does not require another approval
merely because the obsolete DDL offered more combinations than the application.

Current source app/task_semantic_import.py:255-315 always produces history_only
for WorkProject and WorkUpdate. The official branch uses matched=False, an empty
registry key, and cannot be activated through a manually changed manifest:
_check_manifest rebuilds the same plan and compares its items before writes.
The only reachable successful import is source-backed WorkTodo -> formal Task.
The sole located runtime link writer/reader is that importer. Link-local ID and
created_at have no located business or locator consumer. This finding is about
this application's contract, not a claim to have searched arbitrary external SQL
clients. The service has no located public arbitrary-link creation API.

Actual R2 copy failure receipt ../specs/2026-10-17-email-canonical-current-copy-failure-r2.json
SHA8575eb5696395a12e2e7b47dc5ba987996d87bfced1f9baf021ea8ff44bce498:
original business_legacy_links rows=0; source and post-current-write allocator
water=null. Selected12 exact declaration/FKs/indexes captured in
post_target_remaining_business_declarations. Forward preserved these empty
facts and all116 source facts passed inverse. The whole R2 roundtrip failed
later in root script before genuine-old writes; do not cite it as whole-copy
acceptance. Revalidate these facts in the next complete proof and before release.

## Minimal selected representation and cancellation

Keep WorkTodo -> formal Task only, with one nullable structural target FK on the
existing work_todo Business owner. Working SQL name import_task_id points to
business(task_public_id), the same typed public identity already referenced by
the old link's task_id. This is a translated existing relation, not a new
business fact. Use existing owner legacy_key/public_id; do not duplicate
work_todo_id. Restrict this nullable slot to record_type='work_todo'. A target
Task's public ID already has a unique typed alias, so no second copy of target
physical/public identity is required. Confirm exact source-owned physical name
before implementation and never repurpose work_todo.project_id (legacy parent).

Retire link-local id/created_at/allocator and the unused target alternatives.
Retire unreachable official_project_match application branch instead of adding a
second unused WorkProject FK. WorkProject and WorkUpdate remain history_only.
Their native/public identities, existing business results, source references and
history remain unchanged. Do not create an Action solely to preserve obsolete
link metadata or generic endpoint combinations.

The old 7x3 native alternatives remain historical schema/inverse oracles; they
are not the new importer contract. A source row that cannot map to the selected
WorkTodo->Task representation must stop migration with explicit evidence, not be
discarded or silently converted. The explicit field disposition retires link-local
ID, created_at and allocator for every supported WorkTodo->Task relation; no
located business/public locator consumer needs these container metadata. Retain
owner and target identities, not the independent link-row identity. This applies
to supported baseline rows as well as inverse-created rows: distinguishing them
would require an invented marker or caller receipt and would break an ordinary
inverse/old-reader/reforward cycle. Current zero-row/no-sequence evidence means
there are no original metadata values to retire in this measured baseline;
refresh source rows and unsupported alternatives at copy/release.

## Required implementation behavior

Keep exact manifest/source/supporting-evidence checks, business transaction,
formal Task and Signal creation, matching-existing-target checks, idempotent
retry, conflicting-target refusal and created/skipped/formal/history counts.
History-only must not look up an import link: this also removes the source-level
WorkUpdate public-vs-physical-ID mismatch before the history branch.
Do not modify source-shaped WorkTodo projection/digest to include the new
structural FK; otherwise import itself would invalidate its source manifest.

Retain the old target Task public-ID delete/update FK restriction. Explicitly
retire the old mapping-child restriction on deleting/changing the legacy source
owner: that restriction existed because an independent link row referenced it.
There is no independent import receipt identity/time to retain in the selected
owner representation. Deleting the legacy owner removes its own mapping slot,
not the adopted formal Task, Signal or evidence; those retained results remain
independent. The bounded app search finds no delete_business caller or public
WorkTodo/WorkProject delete endpoint; only storage primitive and candidate-only
delete implementations exist. This is an explicit obsolete-container constraint
retirement under Derek's permission to cancel redundant features, not an exact
claim that all formerly legal SQL DML/error codes are preserved. Do not introduce
new trigger machinery to emulate that retired inbound FK. Normal source/evidence
checks, import conflict behavior and target existence remain enforced.
Do not leave compatibility views, generic link actions, legacy tables or dual
writers in the selected current schema.

## Inverse contract and proof

Original old rows currently absent; preserve every retained original fact/type in
full-source proof. For imports represented in the selected schema, inverse must
recreate a valid old WorkTodo/task mapping from owner+typed target and allow
old-code idempotent lookup. Link-local ID/time and allocator are intentionally
retired container metadata under the explicit disposition above, not business
result identity: old SQL defaults may allocate them on inverse. Do not compare
fabricated link IDs/time as retained original business facts. Supported populated
WorkTodo/task links can reforward without an ownership marker; unsupported owner/
target alternatives still stop. Original empty/no-sequence roundtrip is exact.
All unrelated original facts, IDs, objects and counters remain unchanged.

Component tests: target-kind FK, owner-kind check, NULL/one-owner idempotence,
many-owners-one-task, recorded native owner-deletion restriction versus its explicit retirement,
retained target deletion/public-ID restrictions versus native oracle; original empty ID/sequence exact roundtrip; post-forward new import then
inverse old-reader/retry and reforward; no extra source-digest field; history-only
WorkUpdate with colliding public/physical numeric IDs; unreachable official branch
removed without changing planner-produced manifests; malformed source/data refusal.
Preserve source-backed basis tests for explicit assignment/commitment, external
TODO and meeting action item, source-digest and conflicting-target tests.

No second implementation author while Email test closure is active. Next unit:
sole component author -> fresh SPEC -> fresh QUALITY -> caller unit -> fresh SPEC
-> fresh QUALITY -> current consistent-copy full old/new writes and schema inverse.
At that point removing LegacyLinks yields11 ordinary tables only provisionally:
SourceDocuments still must become body-free provenance/inline identity under the
approved actual source-reader and lifecycle requirements. Table count alone is
not final design or capacity acceptance.

### Source-owner restriction disposition rationale

Native owner-delete/public-ID-mutation FK errors must still be captured as source
facts, then classified retired rather than silently claimed equivalent. This
supersedes the conservative preservation suggestion in the earlier source/options
notes. An inline outgoing FK cannot preserve an independent child row's inbound
restriction without extra guards; copying that representation artifact would add
complexity contrary to the selected direct ownership. All adopted formal Task,
Signal/evidence identities and states remain retained, and importing an existing
owner still verifies its exact source digest and current target evidence. Native
SQL mutations of a legacy public ID are not a located service feature. If a real
consumer needing the independent import row is found, report the evidence before
implementation, rather than creating an unowned generic Action relation.

### Minimal native DDL candidate to test first

Prefer SQLite's native ADD COLUMN for the one nullable import_task_id reference
if the exact current Business declaration permits it; it avoids rebuilding
unrelated Business rows/IDs/indexes. Test native ADD->DROP roundtrip for exact
original DDL before selecting that inverse; if it normalizes old declaration
text, use the existing metadata/current-fact rebuild for exact source inverse.
No generic SQL rewriter is needed. Current Source predicate and all retained
Business facts/counters must stay exact in either case.

The only located live Action-side reference to the old link is the entire
trg_sync_legacy_update_public_id trigger produced in
app/action_followup_sync_definitions.py:796-803. Retire that owned trigger when
the WorkUpdate mapping alternative is retired, rather than rewriting its body.
The two legacy-parent kind triggers are attached to the removed table. Preserve
all other Action objects, constraints and water aliases; current factories and
strict validators must agree on this one omitted trigger. Historical source
factories/inverse keep their old exact declaration roles. Source capsule and
fresh code review must verify the full caller/factory closure before selection.

### Populated supported-link disposition clarification

A component author identified the conflict between rejecting every populated old
link and requiring inverse -> old reader/retry -> reforward after a new import.
The selected solution is to retire unconsumed link-local ID/time/allocator for
ALL supported WorkTodo->Task mappings, retain their owner/target facts, and reject
unsupported alternatives. Do not add a caller receipt, compatibility flag or
inverse-generated-row marker to distinguish two semantically identical mappings.
This uses Derek's existing authorization to cancel redundant fields. It changes
the earlier conservative populated-metadata refusal suggestion explicitly; it
does not waive retained owner/target, typed FK, conflict, or unrelated-data checks.

### Caller residue and manifest-format disposition

Caller source inspection found no runtime consumer for the always-zero
ImportApplyResult.official_project_matches, and planner-generated items always
carry an empty official_project_registry_key. The CLI plan branch emits an
always-zero official_project_match count. Cancel those dead fields with the
unreachable execution branch; retain actual created/skipped/formal/history counts.
This is the approved cancellation of unused functionality, not removal of a
working official-project import capability. Because the serialized item and
manifest checksum shape change, advance existing MANIFEST_VERSION from2 to3 and
reject unsupported versions before item parsing. Old manifests must be regenerated
with the normal plan command; do not add a legacy reader or compatibility shim.
The database target marker remains the unpublished Store2026-10-14.1/Email52.
