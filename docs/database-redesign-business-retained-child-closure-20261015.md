# Four-kind Business retained-child and caller closure, 2026-10-15

Research proposal at `016c104100b07244366fa84c1c848134fdc6caa6`, on the isolated
Business source worktree. The accepted, unselected four-kind implementation is
`c9c2c74fda5b95f17400b4c32f5d2b6c6aa7f711`. This document changes no application,
test, constructor, migration, marker, provider or production state. Root must
approve the constraint contract before an executable implementation plan.

## Recommendation and exact boundary

For a partial release of Task, Project, Anchor and Project context revision,
retain the existing public integer values in all retained children. Rebuild
the **15 retained child families / 135 existing fields**, changing exactly
**19 FK endpoints** to three typed public-ID aliases on `business`. Add three
VIRTUAL generated aliases and three full UNIQUE parent indexes. Keep the child
columns and all existing child operations in their original public-ID domain.
Convert every current reader/writer of the four old parents to the accepted
finite Business projections/writers. Remove the four obsolete parent tables
when the eventual coupled release selects this representation.

This is the smallest native constraint option found for the current boundary:
no child data-value remap, no child generated columns, no new tables, no source
compatibility views, no body copying, and no second business writer. It spends
three parent indexes to preserve the original child row shapes. Those indexes
have entries for the other Business kinds' NULL values too; their space/write
cost is real and has not been measured against a copied database.

This partial boundary keeps Signal, source document, adopted evidence, date
facts and Task events in their existing tables. The 158 unproved historical
bodies still block their final retirement. They do not block this four-kind
boundary while the existing exact-source store remains authoritative. This
proposal does not assign a future Store/Email version or a published
predecessor; the Email PR 90 candidate remains unpublished.

The final approved map requires typed **physical** `business.id` endpoints when
the retained children themselves become approved `actions` kinds. The retained
public-ID FK below is explicitly a partial-release contract. It is not evidence
that the final physical remap has happened. Later action consolidation must
resolve the exact public-to-physical parent maps and remove these aliases once
their last retained incoming edge is gone.

## Nineteen groups, fifteen retained families

Source of truth: accepted actual/fresh literal DDL, xinfo and FK records in
`docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json`.
For each variant, union `tables` and `external_endpoint_tables`, exclude the
four moved parents as children, then select FKs whose parent is one of those
four. This yields the following exact set. Every row is one immediate native
NO ACTION/NO ACTION group, MATCH NONE, targeting the old parent's `id`.

| # | Retained child.column | Old parent | Proposed partial endpoint |
| ---: | --- | --- | --- |
| 1 | business_task_evidence.task_id | business_tasks | business.task_public_id |
| 2 | business_task_date_evidence.task_id | business_tasks | business.task_public_id |
| 3 | business_task_events.task_id | business_tasks | business.task_public_id |
| 4 | business_project_evidence.project_id | business_projects | business.project_public_id |
| 5 | business_task_dingtalk_links.business_task_id | business_tasks | business.task_public_id |
| 6 | business_task_follow_ups.business_task_id | business_tasks | business.task_public_id |
| 7 | business_task_todo_sync_outbox.business_task_id | business_tasks | business.task_public_id |
| 8 | business_task_relations.from_task_id | business_tasks | business.task_public_id |
| 9 | business_task_relations.to_task_id | business_tasks | business.task_public_id |
| 10 | business_work_cluster_tasks.task_id | business_tasks | business.task_public_id |
| 11 | business_task_anchor_links.task_id | business_tasks | business.task_public_id |
| 12 | business_task_anchor_links.anchor_id | business_anchors | business.anchor_public_id |
| 13 | business_project_candidates.confirmed_project_id | business_projects | business.project_public_id |
| 14 | business_attention_items.anchor_id | business_anchors | business.anchor_public_id |
| 15 | business_attention_tasks.task_id | business_tasks | business.task_public_id |
| 16 | business_attention_proposal_tasks.task_id | business_tasks | business.task_public_id |
| 17 | business_legacy_links.task_id | business_tasks | business.task_public_id |
| 18 | business_legacy_links.anchor_id | business_anchors | business.anchor_public_id |
| 19 | business_legacy_links.project_id | business_projects | business.project_public_id |

Totals: **13 Task, three Project, three Anchor, zero context-revision incoming
groups**. The four adopted evidence/date/event families contribute groups 1–4.
The other eleven families contribute fifteen groups. This is distinct from the
full map's 23 external groups across twelve external families: its eight Signal
groups remain unchanged, and `business_attention_events` has no four-parent edge.

The fifteen children have **40 total FK groups**. The remaining **21** target
unchanged Signal, cluster, Attention, candidate and legacy work parents. Preserve
all 21 exactly. `business_task_follow_up_send_attempts` retains its draft FK to
`business_task_follow_ups`; its parent table name/public IDs do not change, so it
needs no FK rewrite. Rebuilding a child must not let SQLite rename surviving
grandchildren's FK endpoints to a temporary table name.

## Constraint choice

The proposed structural SQL is finite. For each of `task`, `project`, `anchor`,
the parent alias is the existing public integer only for that actual record
type; every other Business row yields SQL NULL. The indexes must be full UNIQUE
indexes, with BINARY collation. A partial index does not qualify as a native FK
parent key.

```sql
-- Added to the Business declaration; analogous project/anchor aliases follow.
task_public_id integer generated always as
  (case when record_type='task' then public_id end) virtual

create unique index idx_business_task_public_id on business(task_public_id);
create unique index idx_business_project_public_id on business(project_public_id);
create unique index idx_business_anchor_public_id on business(anchor_public_id);

-- In each rebuilt child's otherwise unchanged literal CREATE TABLE:
foreign key(task_id) references business(task_public_id)
  on update no action on delete no action
```

SQLite accepts multiple NULL entries in these parent indexes. A Project with
public ID 100 cannot satisfy a Task FK to 100. Updating a referenced Business
public ID changes its typed alias and reaches the native NO ACTION failure;
deleting its row does likewise. No trigger, application lookup or child cache
is needed. The existing `idx_business_public_id(record_type,public_id)` remains
the four-kind identity constraint. Existing internal physical/public composite
FKs, Project Anchor-type coupling, 42-field projections and four public
allocator streams remain unchanged.

| Alternative | Native closure | Cost and conclusion |
| --- | --- | --- |
| Three parent typed public aliases, recommended | 19 child FKs to three full UNIQUE keys; exact original child columns/values | Three VIRTUAL parent columns and three indexes. All child `SELECT *` shapes stay exact; retained writers keep public IDs. |
| Child constant record discriminators | Each child FK becomes `(generated_kind, old_public_id) → business(record_type,public_id)`, using the existing parent unique index | 18 added child columns across fifteen families: reuse one Task discriminator for the two relation edges; legacy links needs three and anchor links two. Every child's `SELECT *` expands. Existing strict hydration fails until those readers explicitly project their old fields. Native FK behavior works, but the added child declarations and reader rewrites exceed this boundary's three parent aliases. |
| Preserve public values plus coupled physical child refs | 19 stored physical refs, 18 generated type discriminators and 19 `(physical,type,public)` FKs to the existing typed Business index | At least 37 added child columns in that concrete formulation, writes must resolve physical IDs and maintain pairing. Native constraints can be sound; the extra stored values and writers provide no current retained-child benefit. |
| Rewrite the nineteen existing child FK values to typed physical IDs | Three typed physical parent aliases/full UNIQUE keys could keep child column count | Rewrites child public identities, composite PK/unique values, joins, filters and projections. Every affected child writer/reader needs public-to-physical handling. Requires physical maps and source-shaped child projections now; substantially larger than retaining these public columns. |

The constant option is a viable competing design, not a failed FK mechanism.
Its concrete disadvantage is visible in the current consumers: the
`_FrozenBusinessModel` base in `app/task_semantic_models.py:160` uses
`extra="forbid"`, and Store hydrates `dict(row)` from `SELECT *`.
For example `_business_task_evidence_from_row` rejects `_task_parent_type` as
`extra_forbidden`. Parent aliases avoid those additional child API changes.
Neither design is a generic relation layer or a new business field.

## Exact preservation work for the fifteen rebuilds

Change only the nineteen FK clauses above. Preserve all 135 xinfo records
including cid/order, declared affinity, NOT NULL, default, PK position and
hidden flag; all values and their SQLite storage classes; and every other
constraint/endpoint. Nine children have AUTOINCREMENT INTEGER PRIMARY KEYs;
six have composite PKs and ordinary hidden ROWIDs. Preserve explicit public
IDs, the nine independent sequence waters and the six hidden ROWIDs rather
than silently reassigning them during a rebuild. Deletion gaps and allocator
waters are different facts. Preserve UNIQUE/CHECK/IGNORE conflict behavior,
including the relation self-link CHECK and candidate/legacy NULL predicates.

The following is the complete auxiliary inventory from the current literal
Store schema. `auto` identifies SQLite-generated indexes retained by the
original PK/UNIQUE clauses. All named explicit indexes keep their full SQL,
key order, collation and partial predicates. There are **16 explicit indexes,
13 autoindexes and three triggers** across the fifteen children.

| Retained family | Columns | PK/UNIQUE and explicit index preservation |
| --- | ---: | --- |
| business_task_evidence | 4 | PK(task_id,signal_id,evidence_role), one auto; idx_business_task_evidence_task(task_id,created_at,signal_id) |
| business_task_date_evidence | 10 | AUTOINCREMENT id; idx_business_task_date_evidence_task(task_id,id); all three immutable triggers |
| business_task_events | 8 | AUTOINCREMENT id; idx_business_task_events_task(task_id,created_at,id) |
| business_project_evidence | 3 | PK(project_id,signal_id), one auto; no explicit index |
| business_task_dingtalk_links | 17 | AUTOINCREMENT id; idx_business_task_dingtalk_links_task(business_task_id,status,id); UNIQUE idx_business_task_dingtalk_links_one_active(business_task_id) WHERE status IN ('creating','active') |
| business_task_follow_ups | 18 | AUTOINCREMENT id; UNIQUE(dedupe_key), one auto; idx_business_task_follow_ups_due(status,scheduled_at,id) |
| business_task_todo_sync_outbox | 15 | AUTOINCREMENT id; UNIQUE(operation_key), one auto; idx_business_task_todo_sync_outbox_status(status,next_attempt_at,id) |
| business_task_relations | 7 | PK(from_task_id,to_task_id,relation_type), one auto; idx_business_task_relations_from(from_task_id,relation_type,to_task_id); idx_business_task_relations_to(to_task_id,relation_type,from_task_id) |
| business_work_cluster_tasks | 3 | PK(cluster_id,task_id), one auto; idx_business_work_cluster_tasks_task(task_id,cluster_id) |
| business_task_anchor_links | 8 | AUTOINCREMENT id; UNIQUE(task_id,anchor_id), one auto; idx_business_task_anchor_links_task(task_id,status,active,anchor_id) |
| business_project_candidates | 8 | AUTOINCREMENT id; idx_business_project_candidates_cluster(cluster_id,status,id) |
| business_attention_items | 16 | AUTOINCREMENT id; UNIQUE(stable_key), one auto; idx_business_attention_items_list(status,category,updated_at,id) |
| business_attention_tasks | 3 | PK(attention_item_id,task_id), one auto; idx_business_attention_tasks_task(task_id,attention_item_id) |
| business_attention_proposal_tasks | 3 | PK(attention_item_id,task_id), one auto; idx_business_attention_proposal_tasks_task(task_id,attention_item_id) |
| business_legacy_links | 12 | AUTOINCREMENT id; three UNIQUE(work_project_id/work_todo_id/work_update_id) autos; idx_business_legacy_links_task(task_id,id) |

The date triggers are exactly
`trg_business_task_date_evidence_immutable_update`,
`trg_business_task_date_evidence_immutable_delete` and
`trg_business_task_date_evidence_immutable_replace`. Their original predicates
and `business task date evidence is immutable` error stay intact. The six
Signal/document immutable triggers remain owned by unchanged tables.

This inventory did not assume the FK list was the whole dependency graph.
The accepted actual/fresh oracle has `external_schema_references=[]` for all
ten original core parents. Independently, the current Store `_initialize`
literal SQL was evaluated without running the constructor, reconstructed in
SQLite memory, then every `sqlite_schema` trigger, view and virtual-table SQL
was scanned for the four exact old parent names: **zero references**. Source
search across `app/**/*.py` also located all direct SQL references listed below;
the current initializer's three date triggers refer only to their own table.
No nonowned parent-reference trigger, view or FTS object was found in this
bound source. A future release still needs a newly bound actual predecessor's
complete object metadata; this research is not current production inspection.

## Current parent SQL caller closure

Use finite SQL expressions from the accepted `BUSINESS_PROJECTIONS`, as inline
relations under the existing SQL aliases. Preserve old field aliases and
public-ID ordering; `id` in those projections means public ID. Do not replace
an old `SELECT *` with physical `business.*`. Task/Anchor/Project/revision
models must receive their original fields only. Use `insert_business`,
`update_business` and `delete_business` for the four parents' mutations under
the existing caller transaction; return `BusinessWriteResult.lastrowid`, not
physical cursor ROWID. Retained child writes continue using public IDs.

The following function list comes from actual Python AST/string searches,
then inspection of the direct queries. It names the SQL owners to convert;
callers using the public Store methods keep their existing method contracts.

| File | Actual SQL-owning functions and purpose |
| --- | --- |
| app/store.py | `create_business_task_in_transaction`, `get_business_task_in_transaction`, `update_business_task_in_transaction`, `get_business_task`, `list_business_tasks`, `list_business_tasks_for_projection`: Task CRUD/projection |
| app/store.py | `list_unmerged_formal_business_task_ids_for_signal_in_transaction`, `list_unmerged_formal_business_task_ids_for_source_in_transaction`: retained evidence/Signal joins to Task |
| app/store.py | `get_business_anchor_in_transaction`, `get_business_anchor_by_identity_in_transaction`, `create_business_anchor_in_transaction`, `list_business_anchors`: Anchor lookup/create/list |
| app/store.py | `get_business_project_in_transaction`, `create_business_project_in_transaction`, `list_business_projects`, `update_business_project_crm_customer_lookup_in_transaction`, `confirm_business_project_crm_customer`, `clear_business_project_crm_customer`: Project registry/CRM read/write |
| app/store.py | `get_business_project_context_in_transaction`, `list_business_project_context_revisions_in_transaction`: latest context and revision paging |
| app/store.py | `list_business_task_ids_for_project`, `list_business_task_project_links`, `derive_business_task_relevance_in_transaction`, `list_business_task_project_customer_names`: Task/Project/Anchor associations |
| app/store.py | `claim_business_task_follow_up_for_send`: retained follow-up `f.*` joined to Task status; preserve claim predicates/transaction |
| app/project_context_service.py | `ProjectContextService._require_project`, `ProjectContextService.apply`: existence, latest context and revision insert; retained Project evidence insertion remains in the same transaction |
| app/task_business_resolution.py | `BusinessResolutionService.register_official_project`, `.register_source_project`, `.confirm_project_candidate`: official lookup/register and candidate confirmation using shared Task/Anchor/Project transactions |
| app/task_semantic_service.py | `TaskSemanticService.record_suggestion`, `.merge_same_deliverable`: official Project lookup, supporting-link existence and merge-parent lookup |
| app/task_agent.py | `_confirmed_official_project_anchors`, `_official_project`, `_validate_stored_project_assessments`: registered Project/Anchor validation joins |
| app/task_attention_projection.py | `BusinessAttentionProjection._validate_proposal`, `._current_eligible_task_ids`, `.resolve`: Anchor eligibility, registered Project/evidence joins; preserve category/status policy |
| app/minutes_owner_backfill.py | `ownerless_candidate_meetings`: Task/evidence/Signal/document join; source bodies still come from the original document table |
| app/todo_completion.py | `complete_business_task_from_external_todo`: direct Task completion update, alongside unchanged Signal/evidence/event/follow-up writes |
| app/task_semantic_import.py | `_rows`, `_fingerprint`, `_plan_items`, `_existing_link_matches`, `apply_task_semantic_import_manifest`: dynamic old-table existence/`SELECT *`, registry fingerprint, official mapping and existing Task checks. Preserve source-shaped registry values in hashes; convert existence checks to the selected Business representation, with no old-table fallback. |
| app/web_api/tasks.py | `_confirmed_project_ids_by_task`, `_project_summary_context_and_activity`, `_project_summary_payload`, `business_project_detail`: Project association, latest-public-revision CTE, max timestamp and revision count |

At the later release boundary, `app/store.py` also owns the current
`STORE_SCHEMA_REQUIRED_TABLES`, `STORE_SCHEMA_REQUIRED_INDEXES` and
`STORE_SCHEMA_REQUIRED_COLUMNS` parent entries, consumed by
`_schema_manifest_is_current_in_connection`, plus `_initialize`'s old parent
CREATE/ALTER paths. Its `_migrate_project_attention_evidence` currently joins
Project and runs during initialization. Those current bootstrap/metadata/data
paths must select the new representation together; leaving them unchanged
would recreate the obsolete parents. Historical old-source SQL remains bound
to its real historical source rather than being silently rewritten as current
target SQL. No constructor/validator changes are implemented here, and no new
audit, authorization, recovery or repair behavior is proposed.

The disabled Followup9 work must be composed deliberately once. The three
retained follow-up/link/outbox families above still physically exist today.
`app/action_followup_sync_definitions.py` owns `SYNC_SOURCE_FKS`, `SYNC_EXTERNAL_FKS`
and `sync_schema_parts`; `app/action_followup_sync_storage.py` owns the
unselected `capture_followup_sync_source` parent-declaration assumptions.
If that work becomes selected before Business integration, its actual action
FKs replace the corresponding retained-table endpoints in a newly captured
source closure. Do not treat its prepared migration, or Email PR 90, as a
published predecessor or convert the same family twice.

Maintained diagnostic/eval SQL also needs target-aware finite projections
before it can validate the release: `scripts/inspect_task_attention.py`
`inspect_run`; `scripts/replay_task_attention.py` `comparison_capabilities`,
`read_project_centered_expectations`, `read_domain`, `readback`, and its
`domain_snapshot` enumeration (currently `business_*`, which would omit the
new `business` table); `scripts/validate_native_copy_additional.py`
`work_summary`. These were inspected only. The task schema-title eval, daily
report facts, retrieval and registered Web routes call public Store methods;
their old names in Python are not direct SQL endpoints requiring renaming.

## Small native comparison performed

Only synthetic values in closed `:memory:` SQLite 3.53.4 connections were used.
The temporary proof imported the existing target declarations/writers and
evaluated the source initializer's literal DDL expression without constructing
AutoReplyStore. It reconstructed the accepted actual/fresh old tables and
the two proposed child variants separately. Supplemental cluster/legacy work
parents came from literal current source DDL. No service database, copied
database, native session file, provider, Agent CLI or network was used.

For each of the 19 edges in each actual/fresh variant, the source, aliases and
child-constant versions each received a valid synthetic child row. Anchor
edges used a dedicated Anchor with no Project/other child reference so a
failure could be attributed to the tested child. Every rejected parent
operation had an otherwise identical no-child success control. Signed public
IDs were different from target positive physical IDs.

| Check | Original source | Parent aliases | Child constants |
| --- | ---: | ---: | ---: |
| Valid child writes, 19 × 2 | 38 accepted | 38 accepted | 38 accepted |
| Referenced parent DELETE | 38 FK refusals | 38 FK refusals | 38 FK refusals |
| Referenced parent public-ID UPDATE | 38 FK refusals | 38 FK refusals | 38 FK refusals |
| Same DELETE with tested child rolled back | 38 accepted | 38 accepted | 38 accepted |
| Same public-ID UPDATE with tested child rolled back | 38 accepted | 38 accepted | 38 accepted |
| ID existing solely in wrong parent kind | 38 FK refusals | 38 FK refusals | 38 FK refusals |
| Nullable candidate/legacy rows, two per variant | 4 accepted | 4 accepted | 4 accepted |
| Unreferenced negative Task ID changed to positive 10, then referenced | 2 accepted | 2 accepted | 2 accepted |
| Date UPDATE/DELETE/REPLACE immutable errors | 6 refusals | 6 refusals | 6 refusals |
| Existing Store evidence hydration on positive identities | 2 accepted | 2 accepted | 2 extra_forbidden refusals |

All fifteen alias child xinfo lists equaled the captured source exactly in
each variant, and all successful states had an empty native
`PRAGMA foreign_key_check`. All child auxiliary SQL came from the original
schema; the original immutable errors were exercised. This is a small
constraint/shape proof, not a migration, performance, allocator-copy,
whole-caller or production acceptance. No full test suite was run.

## Coherent next unit and remaining decisions

Approve or reject the three-parent-alias contract first. If accepted, the
next executable plan should cover the **complete four-kind partial integration**:
the three aliases/indexes, all nineteen retained FK rewrites, the finite
current SQL caller closure above, exact public-ID/source-shaped API parity and
the existing transaction boundary. It must describe fresh selection and
source-bound forward/current-fact inverse/reopen as one release, preserving
unrelated schema and child facts. Do not split it into one Anchor helper or
another six-field scaffold. No additional full-system redesign is needed.

The migration design must precompute complete physical/public identity maps
for all four kinds before inserting internal physical refs. A merged Task can
reference a higher public ID, and the source permits multirow merge cycles.
Calling `insert_business` in public-ID order is not a valid copy algorithm:
the accepted immediate runtime writer uses physical 0 for a missing parent to
reach native FK checking. Bulk copy must insert actual mapped refs under the
eventual coupled atomic migration discipline and run native FK checking; it
must not preserve runtime missing-parent placeholders or invent a fallback.
This does not require changing the accepted runtime API for unused deferred-FK
modes.

Before actual release, rebind the then-published source and its complete
metadata/allocator state, reconcile any selected Followup9 change, and verify
all public parent/caller projections, child xinfo/40 FK groups/29 indexes/three
triggers, transaction rollback, forward/inverse identity and allocator facts,
fresh/reopen and existing application paths on the exact source. Publication,
CI, provider/native outcomes and production/deploy readback remain separate.
No missing-body recovery is prerequisite to this partial integration.

## Self-review and handoff

Mechanically recomputed actual/fresh incoming groups (19), families (15),
columns (135), affected-parent distribution (13/3/3), total child groups (40),
unchanged groups (21), nullable moved columns (four), AUTOINCREMENT children
(nine) and discriminator alternative columns (18). Inspected the actual
parent SQL owners, strict hydration, bootstrap metadata and original auxiliary
DDL. Native comparison results above are local planning evidence only.
The task-owned temporary proof directory and call-search output were removed;
their absence was verified. Only this new document and the author's own claim
row are in scope.
Root-owned independent SPEC then QUALITY review remains pending.
