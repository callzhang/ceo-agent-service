# Business core target field map, 2026-10-15

This is a bounded **target proposal**, grounded in the accepted ten-family source
oracle at `09497ef4796af49abcece9a00614b48ff97a038b`. It specifies dispositions for
all 84 source columns and all 23 external incoming FK groups. It is not executable
DDL, a migration, a constructor selection or permission to retire bodies.

The approved structure remains `2026-10-11-database-redesign-design.md`: business
current state, finite adopted action facts, direct typed endpoints, unique source
metadata inline and provenance only for a genuinely shared exact source version.
No new business facts, cache, provider, policy, version or activation flag is
proposed. Followup9 is a separate disabled candidate; Email29/PR90 is unpublished.
Neither is a published predecessor. The captured actual/fresh source is genuine
published `7dadc492c1c62d5b0624f2f97dd0c89f4f358646` Store19/Email51; a later
implementation must bind its actual published predecessor anew.

## 1. Mapping notation and identity

`P.x` means the fixed path `payload_json` → `$.x`, preserving the source SQL value
and meaning, including SQL NULL versus empty text and exact JSON **text** values.
It does not mean recursively parsing existing `*_json` text into a different value.
`Q.x` means that same fixed value must additionally be queryable as a constrained
physical/generated column for a real index or CHECK; it is not a second authoritative
copy. Names for generated columns and final SQL encoding remain the next declaration
unit's work. SQLite native dynamic value/storage-class behavior must be compared
against the source oracle; adding a JSON type rule cannot silently tighten it.
`C.x` means a common physical column from the approved design; final Business DDL does not exist
yet. All mappings are scoped by the indicated `record_type`.

For each single `id` PK, preserve the old signed public integer through
`(record_type, legacy_key)` while allocating a separate deterministic physical target
integer and remapping actual FK endpoints. Source public IDs/URLs/order remain
source-shaped projections, not target physical row order. Negative/zero/explicit
IDs, NULL-as-allocation and deleted high water must remain native-equivalent.
No merge by title, source_ref, body hash or equal integers across families.
Composite evidence identity remains its complete typed endpoint tuple, with
`evidence_role` where applicable; a structural action ID does not replace uniqueness.
Any structural legacy key encoding must be collision-free and round-trip tested.
Source evidence tables also have ordinary hidden ROWID; its permitted behavior must
be checked explicitly rather than treating their composite PK as AUTOINCREMENT.

The tables below are in **actual source ordinal order**, with 0-based `actual/fresh`
ordinals. NN/PK are exact `table_xinfo.notnull/pk`, not inferred SQL guarantees:
INTEGER PK has NN=0 while remaining a rowid alias. `—` means no default, not SQL NULL.

### `business_tasks` → `business` / `task` (19 fields)

| Source column | SQL type | NN | PK position | Default SQL | Ordinal A/F | Proposed destination / disposition |
| --- | --- | ---: | ---: | --- | --- | --- |
| `id` | INTEGER | 0 | 1 | `—` | 0/0 | business.id + legacy_key; source public identity |
| `title` | TEXT | 1 | 0 | `—` | 1/1 | C.title |
| `description` | TEXT | 1 | 0 | `''` | 2/2 | P.description |
| `stage` | TEXT | 1 | 0 | `—` | 3/3 | P.stage; Q.stage |
| `status` | TEXT | 1 | 0 | `'open'` | 4/6 | C.status |
| `commitment_status` | TEXT | 1 | 0 | `'none'` | 5/7 | P.commitment_status; Q.commitment_status |
| `formal_basis` | TEXT | 0 | 0 | `—` | 6/8 | P.formal_basis; Q.formal_basis |
| `business_relevance` | TEXT | 1 | 0 | `'unknown'` | 7/9 | P.business_relevance; Q.business_relevance |
| `merged_into_task_id` | INTEGER | 0 | 0 | `—` | 8/10 | direct typed FK → business(task).id; reads project source public Task ID |
| `owner_user_id` | TEXT | 1 | 0 | `''` | 9/11 | P.owner_user_id |
| `owner_name` | TEXT | 1 | 0 | `''` | 10/12 | P.owner_name |
| `owner_evidence_json` | TEXT | 1 | 0 | `'{}'` | 11/13 | P.owner_evidence_json; Q.owner_evidence_json |
| `deadline_at` | TEXT | 1 | 0 | `''` | 12/14 | P.deadline_at |
| `missing_evidence_json` | TEXT | 1 | 0 | `'[]'` | 13/15 | P.missing_evidence_json; Q.missing_evidence_json |
| `last_activity_at` | TEXT | 1 | 0 | `current_timestamp` | 14/16 | P.last_activity_at |
| `created_at` | TEXT | 1 | 0 | `current_timestamp` | 15/17 | C.created_at |
| `updated_at` | TEXT | 1 | 0 | `current_timestamp` | 16/18 | C.updated_at |
| `origin` | TEXT | 1 | 0 | `'source'` | 17/4 | P.origin; Q.origin |
| `suggestion_json` | TEXT | 1 | 0 | `'{}'` | 18/5 | P.suggestion_json; Q.suggestion_json |

### `business_task_signals` → `actions` / `signal_fact` (13 fields)

| Source column | SQL type | NN | PK position | Default SQL | Ordinal A/F | Proposed destination / disposition |
| --- | --- | ---: | ---: | --- | --- | --- |
| `id` | INTEGER | 0 | 1 | `—` | 0/0 | actions.id + legacy_key; source public identity |
| `source_type` | TEXT | 1 | 0 | `—` | 1/1 | P.source_type; Q.source_type |
| `source_ref` | TEXT | 1 | 0 | `—` | 2/2 | P.source_ref; Q.source_ref |
| `source_time` | TEXT | 1 | 0 | `''` | 3/3 | P.source_time; Q.source_time |
| `conversation_id` | TEXT | 1 | 0 | `''` | 4/4 | P.conversation_id |
| `conversation_title` | TEXT | 1 | 0 | `''` | 5/5 | P.conversation_title |
| `author_user_id` | TEXT | 1 | 0 | `''` | 6/6 | P.author_user_id |
| `author_name` | TEXT | 1 | 0 | `''` | 7/7 | P.author_name |
| `source_document_id` | INTEGER | 1 | 0 | `—` | 8/8 | final: provenance_id for actually shared exact version; unique owner inline identity; blocked (§3) |
| `context_json` | TEXT | 1 | 0 | `'{}'` | 9/9 | P.context_json: preserve necessary adopted/locator members; retire only proved raw duplicates (§3); exact split unproved; Q.context_json |
| `dedupe_key` | TEXT | 1 | 0 | `—` | 10/10 | P.dedupe_key; Q.dedupe_key |
| `created_at` | TEXT | 1 | 0 | `current_timestamp` | 11/11 | C.created_at |
| `author_kind` | TEXT | 1 | 0 | `'unknown'` | 12/12 | P.author_kind; Q.author_kind |

### `business_source_documents` → `provenance` / `conditional shared source version` (4 fields)

| Source column | SQL type | NN | PK position | Default SQL | Ordinal A/F | Proposed destination / disposition |
| --- | --- | ---: | ---: | --- | --- | --- |
| `id` | INTEGER | 0 | 1 | `—` | 0/0 | shared: provenance.id + legacy_key; unique: owner inline source_document_id with identity_key; final encoding unproved |
| `identity_key` | TEXT | 1 | 0 | `—` | 1/1 | shared: provenance identity_key; unique: owner inline identity_key; never infer a retrievable locator from this hash |
| `body` | TEXT | 1 | 0 | `—` | 2/2 | RETIRE only after exact source recovery and caller conversion (§3); currently BLOCKED; no provenance/payload body |
| `created_at` | TEXT | 1 | 0 | `current_timestamp` | 3/3 | shared: provenance.created_at; unique: owner inline source created_at (not owner creation time) |

### `business_task_evidence` → `actions` / `task_evidence` (4 fields)

| Source column | SQL type | NN | PK position | Default SQL | Ordinal A/F | Proposed destination / disposition |
| --- | --- | ---: | ---: | --- | --- | --- |
| `task_id` | INTEGER | 1 | 1 | `—` | 0/0 | direct business_id FK → business(task).id |
| `signal_id` | INTEGER | 1 | 2 | `—` | 1/1 | direct signal_action_id FK → actions(signal_fact).id |
| `evidence_role` | TEXT | 1 | 3 | `—` | 2/2 | P.evidence_role; Q.evidence_role |
| `created_at` | TEXT | 1 | 0 | `current_timestamp` | 3/3 | C.created_at |

### `business_task_date_evidence` → `actions` / `date_fact` (10 fields)

| Source column | SQL type | NN | PK position | Default SQL | Ordinal A/F | Proposed destination / disposition |
| --- | --- | ---: | ---: | --- | --- | --- |
| `id` | INTEGER | 0 | 1 | `—` | 0/0 | actions.id + legacy_key; source public identity |
| `task_id` | INTEGER | 1 | 0 | `—` | 1/1 | direct business_id FK → business(task).id |
| `date_type` | TEXT | 1 | 0 | `—` | 2/2 | P.date_type; Q.date_type |
| `value_at` | TEXT | 1 | 0 | `''` | 3/3 | P.value_at |
| `raw_phrase` | TEXT | 1 | 0 | `—` | 4/4 | final: exact source read via signal, no long-term phrase copy; active comparison/validation blocks retirement (§3) |
| `source_signal_id` | INTEGER | 1 | 0 | `—` | 5/5 | direct signal_action_id FK → actions(signal_fact).id |
| `actor_kind` | TEXT | 1 | 0 | `—` | 6/6 | P.actor_kind; Q.actor_kind |
| `actor_user_id` | TEXT | 1 | 0 | `''` | 7/7 | P.actor_user_id |
| `actor_name` | TEXT | 1 | 0 | `''` | 8/8 | P.actor_name |
| `created_at` | TEXT | 1 | 0 | `current_timestamp` | 9/9 | C.created_at |

### `business_task_events` → `actions` / `task_event` (8 fields)

| Source column | SQL type | NN | PK position | Default SQL | Ordinal A/F | Proposed destination / disposition |
| --- | --- | ---: | ---: | --- | --- | --- |
| `id` | INTEGER | 0 | 1 | `—` | 0/0 | actions.id + legacy_key; source public identity |
| `task_id` | INTEGER | 1 | 0 | `—` | 1/1 | direct business_id FK → business(task).id |
| `event_type` | TEXT | 1 | 0 | `—` | 2/2 | P.event_type; Q.event_type |
| `signal_id` | INTEGER | 0 | 0 | `—` | 3/3 | nullable direct signal_action_id FK → actions(signal_fact).id |
| `before_json` | TEXT | 1 | 0 | `—` | 4/4 | P.before_json; Q.before_json |
| `after_json` | TEXT | 1 | 0 | `—` | 5/5 | P.after_json; Q.after_json |
| `reason` | TEXT | 1 | 0 | `—` | 6/6 | P.reason; Q.reason |
| `created_at` | TEXT | 1 | 0 | `current_timestamp` | 7/7 | C.created_at |

### `business_projects` → `business` / `project` (12 fields)

| Source column | SQL type | NN | PK position | Default SQL | Ordinal A/F | Proposed destination / disposition |
| --- | --- | ---: | ---: | --- | --- | --- |
| `id` | INTEGER | 0 | 1 | `—` | 0/0 | business.id + legacy_key; source public identity |
| `canonical_anchor_id` | INTEGER | 1 | 0 | `—` | 1/1 | direct typed composite FK → business(anchor).(id, anchor_type) |
| `anchor_type` | TEXT | 1 | 0 | `'project'` | 2/2 | P.anchor_type; Q.anchor_type |
| `title` | TEXT | 1 | 0 | `—` | 3/3 | C.title |
| `registry_source` | TEXT | 1 | 0 | `—` | 4/4 | P.registry_source; Q.registry_source |
| `created_at` | TEXT | 1 | 0 | `current_timestamp` | 5/11 | C.created_at |
| `crm_customer_id` | TEXT | 1 | 0 | `''` | 6/5 | P.crm_customer_id |
| `crm_customer_name` | TEXT | 1 | 0 | `''` | 7/6 | P.crm_customer_name |
| `crm_customer_lookup_status` | TEXT | 1 | 0 | `'not_requested'` | 8/7 | P.crm_customer_lookup_status; Q.crm_customer_lookup_status |
| `crm_customer_candidates_json` | TEXT | 1 | 0 | `'[]'` | 9/8 | P.crm_customer_candidates_json; Q.crm_customer_candidates_json |
| `crm_customer_label` | TEXT | 1 | 0 | `''` | 10/9 | P.crm_customer_label |
| `crm_customer_evidence_json` | TEXT | 1 | 0 | `'{}'` | 11/10 | P.crm_customer_evidence_json; Q.crm_customer_evidence_json |

### `business_anchors` → `business` / `anchor` (6 fields)

| Source column | SQL type | NN | PK position | Default SQL | Ordinal A/F | Proposed destination / disposition |
| --- | --- | ---: | ---: | --- | --- | --- |
| `id` | INTEGER | 0 | 1 | `—` | 0/0 | business.id + legacy_key; source public identity |
| `anchor_type` | TEXT | 1 | 0 | `—` | 1/1 | P.anchor_type; Q.anchor_type |
| `anchor_ref` | TEXT | 1 | 0 | `—` | 2/2 | P.anchor_ref; Q.anchor_ref |
| `title` | TEXT | 1 | 0 | `—` | 3/3 | C.title |
| `active` | INTEGER | 1 | 0 | `1` | 4/4 | P.active; Q.active |
| `created_at` | TEXT | 1 | 0 | `current_timestamp` | 5/5 | C.created_at |

### `business_project_evidence` → `actions` / `project_evidence` (3 fields)

| Source column | SQL type | NN | PK position | Default SQL | Ordinal A/F | Proposed destination / disposition |
| --- | --- | ---: | ---: | --- | --- | --- |
| `project_id` | INTEGER | 1 | 1 | `—` | 0/0 | direct business_id FK → business(project).id |
| `signal_id` | INTEGER | 1 | 2 | `—` | 1/1 | direct signal_action_id FK → actions(signal_fact).id |
| `created_at` | TEXT | 1 | 0 | `current_timestamp` | 2/2 | C.created_at |

### `business_project_context_revisions` → `business` / `project_context_revision` (5 fields)

| Source column | SQL type | NN | PK position | Default SQL | Ordinal A/F | Proposed destination / disposition |
| --- | --- | ---: | ---: | --- | --- | --- |
| `id` | INTEGER | 0 | 1 | `—` | 0/0 | business.id + legacy_key; source public identity |
| `project_id` | INTEGER | 1 | 0 | `—` | 1/1 | direct typed FK → business(project).id |
| `context_json` | TEXT | 1 | 0 | `—` | 2/2 | P.context_json |
| `evidence_json` | TEXT | 1 | 0 | `—` | 3/3 | P.evidence_json |
| `created_at` | TEXT | 1 | 0 | `current_timestamp` | 4/4 | C.created_at |

## 2. Native constraints and allocation that the mapping must carry

The accepted JSON artifact is the literal SQL authority, at
`actual.tables.<family>` and `fresh.tables.<family>`; each carries `sql`, `columns`,
`check_expressions`, `indexes`, `foreign_keys`, `triggers`, `identity` and `sequence`.
The following is a complete constraint inventory, not a replacement DDL declaration.
All CHECK literals, including the exact Unicode whitespace trim character set and
SQLite three-valued NULL behavior, must be translated from those entries, rather
than reduced to a Python validator or a new enum guessed from current rows.

| Family suffix (`business_`) | CHECKs | Indexes | Owned triggers | Source behavior to preserve / explicit retirement dependency |
| --- | ---: | ---: | ---: | --- |
| tasks | 13 | 3 | 0 | Nonblank title; stage/status/commitment/formal_basis/relevance/origin domains; three JSON object/array checks; formal stage↔basis; merged status↔merge pointer; no self merge |
| task_signals | 5 | 3 | 3 | Nonblank type/ref/dedupe; context object; actor domain; immutable update/delete and insert collision on id OR dedupe key |
| source_documents | 1 | 1 | 3 | Nonblank body and immutable update/delete/id-or-identity replacement currently required; remove/rewrite only with exact source retirement, retain identity uniqueness |
| task_evidence | 1 | 2 | 0 | All nine existing evidence roles; full (task, signal, role) PK |
| task_date_evidence | 3 | 1 | 3 | Six existing date types, actor domain, nonblank phrase; immutable update/delete/id collision; phrase constraint changes only with caller/source conversion |
| task_events | 4 | 1 | 0 | Eleven event types, before/after JSON objects, nonblank reason; do not add a new immutability trigger |
| projects | 6 | 1 | 0 | Project anchor type, nonblank title/registry_source, CRM lookup domain and candidates/evidence JSON shape |
| anchors | 4 | 2 | 0 | Existing eleven anchor types; nonblank anchor_ref/title; active ∈ (0,1) |
| project_evidence | 0 | 1 | 0 | Full (project, signal) PK; no extra DB JSON/immutability policy |
| project_context_revisions | 0 | 1 | 0 | Native TEXT NOT NULL only; service validates context/citations; DB currently accepts non-JSON text |
| **Total** | **37** | **16** | **9** | No deletion because a family currently has zero rows |

For example, the existing Task coupling expressions are exactly
`(stage = 'formal' and formal_basis is not null) or (stage = 'candidate' and formal_basis is null)`,
`(status = 'merged' and merged_into_task_id is not null) or (status <> 'merged' and merged_into_task_id is null)`,
and `merged_into_task_id is null or merged_into_task_id <> id` (source whitespace
omitted). Translate the final comparison against the same identity space; a
physical target ID cannot be compared with an unremapped public ID.

All 12 internal FK groups / 13 rows are accounted for by the field-map FK rows:
Task self-merge; Signal→document; Task evidence→Task+Signal; date→Task+Signal;
event→Task+nullable Signal; Project→Anchor **composite** `(canonical_anchor_id,
anchor_type) → (id,anchor_type)`; Project evidence→Project+Signal;
context revision→Project. They use immediate NO ACTION update/delete and NONE
match. Typed parent validation must restrict the target record kind. `parent_id`
is insufficient to represent these independent edges; no generic relation table.
The final Signal→source edge is conditional on real sharing and source recovery,
so its exact replacement DDL is deliberately unresolved here.

All source indexes use BINARY collation, no partial predicate; ASC unless noted:

| Family | Exact indexed source key(s); U means unique |
| --- | --- |
| tasks | `(business_relevance,updated_at,id)`; `(stage,status,updated_at,id)`; `(updated_at,id)` |
| task_signals | `(source_document_id)`; `(source_type,source_ref,source_time,id)`; U `(dedupe_key)` |
| source_documents | U `(identity_key)` |
| task_evidence | `(task_id,created_at,signal_id)`; U `(task_id,signal_id,evidence_role)` |
| task_date_evidence | `(task_id,id)` |
| task_events | `(task_id,created_at,id)` |
| projects | U `(canonical_anchor_id)` |
| anchors | U `(id,anchor_type)`; U `(anchor_type,anchor_ref)` |
| project_evidence | U `(project_id,signal_id)` |
| project_context_revisions | `(project_id,id DESC)` |

Rewrite keys with type predicates and exact physical endpoint maps, retaining
source public-ID sort order where the index is used for source ordering. The
Signal document index must become the proved inline/shared source lookup, not
an index on a retired pointer. Do not infer target index names or an index plan
from the old names. The two evidence tables remain separate finite action kinds,
with their entire original composite keys; multiple Signals and roles remain
multiple rows even when today's sample has only one edge.

| Native AUTOINCREMENT family | Actual sqlite_sequence water | Fresh water |
| --- | ---: | --- |
| business_tasks | 594 | absent |
| business_task_signals | 833 | absent |
| business_source_documents | 188 | absent |
| business_task_date_evidence | absent | absent |
| business_task_events | 893 | absent |
| business_projects | 50 | absent |
| business_anchors | 50 | absent |
| business_project_context_revisions | 6 | absent |

Preserve eight independent source allocation streams, including absent waters,
deleted highs, failed/ignored insert effects, rollback and exhaustion, separately
from target physical IDs. Project has 32 rows and water 50. Row count or MAX(id)
cannot reconstruct the water. No new business sequence field is proposed; the
exact reuse of existing action allocator/state storage needs a declaration proof.
Document public identity and allocation cannot silently disappear simply because
body is retired; its unique/shared identity disposition must be proved first.
Task/Project evidence have no sqlite_sequence, ordinary ROWID and composite PKs.

Omitted defaults stay distinct from explicit NULL. Evaluate CURRENT_TIMESTAMP
under the existing caller's writer transaction with coherent times for one row;
do not introduce an independent clock per payload field. The field table records
all defaults and actual/fresh ordinal differences, including Task origin/suggestion
and Project created_at placement. Preserve both source variants by name; do not
compare `SELECT *` tuples across them. Actual Signal SQL is rebuilt/quoted while
fresh SQL is original DDL; its literal binding remains variant-specific. External
Attention assessment_json also differs in ordinal; endpoint rewrites must retain
that child's actual declaration. Native SQLite affinity, SQL NULL/BLOB acceptance,
conflict ordering, REPLACE/IGNORE behavior and all nine trigger errors require
source-versus-target comparison before any declaration is accepted.

## 3. Adopted results and source retirement are different work

| Existing field / path | Actual caller evidence | Required disposition and unresolved evidence |
| --- | --- | --- |
| Task state, owner_evidence_json, suggestion_json, deadline_at | `app/store.py:5800` Task model/writer; semantic mutations | Keep adopted state and its exact evidence metadata in business; a Task deadline is existing, a Project deadline is absent |
| Signal source tuple, author fields and dedupe_key | `app/task_source_documents.py:43`; `app/task_semantic_service.py:201` | Keep identity and author kind. Document identity hashes type/ref/time/conversation/author/body together; not a body-only hash or retrievable locator |
| Signal context_json | `app/task_agent.py:1014`; `app/task_semantic_service.py:344,952`; `app/task_semantic_import.py:420` | Preserve actual locator, owner_identity, assignment_authorized, reply_to_source_ref, external_task_id and provenance-origin facts. Import can embed legacy_row. Exact per-key duplicate retirement is **unproved**; inspect finite producing/consuming shapes and historical value metadata before defining the residual schema |
| Source body / joined evidence_text | `app/store.py:5697-5724`; `app/task_agent.py:1014,1330`; `app/project_context_service.py:41` | Current source is often WorkItem.summary or a cited excerpt; fetching a latest provider document does not reconstruct it. Final no-body destination requires exact historical version readback |
| Date raw_phrase | `app/task_semantic_service.py:375`; `app/store.py:5995`; `app/task_agent.py:2213` | Source membership/actor checks, merge-copy dedupe and replay compare the phrase. assigned_at can use source_time; other phrases need their exact bound source. Rewrite these actual readers before omitting it; zero rows proves no retirement |
| Event before_json/after_json/reason | `app/task_semantic_service.py:195,240,479,1202` | Keep adopted snapshots and original command result, including merge target event ordering. Embedded public IDs remain source public identities; do not recursively replace arbitrary JSON numbers with target IDs |
| Project context_json/evidence_json | `app/project_context_service.py:35-96`; `app/store.py:6406-6449` | Keep goal/scope/owner/responsibilities/facts, citations and revision signal identities. They are adopted business results; context can exist without Tasks. Revision evidence_json is existing text, not a new generic edge container |

Exact historical recovery is proved only for **30/188 bodies, 672,297 UTF-8 bytes**:
7 Codex and 23 Claude. **158 bodies / 5,314,029 bytes remain unproved**, an actual
full-boundary activation blocker. `read_native_source_body` is an accepted but
unselected exact reader; it does not supply missing sessions, raw phrase lookup,
Signal context extraction or a production source repository. The metadata artifact
and native-reader validation contain the finite coordinates and evidence boundary;
no real native file, DB, provider or body was read for this proposal.

Final shared source identity goes to provenance only when multiple owners actually
share the exact version or independently maintain it; a unique owner's identity,
source created_at and locator stay inline. Owner cardinalities, unreferenced
source identities and a total exact-locator binding for all 188 are **unproved**
here. The next evidence is a bounded source ownership/locator inventory and exact
historical readback, not automatic merging by common URL. Body SHA/byte length
check content; they cannot stand in for a real retrievable source. Do not add body,
raw_phrase or context-original text to provenance, another payload cache or a new
permanent table. For an **unselected intermediate scaffold** only, existing native
Signal/source-document tables and endpoints can remain untouched; that is not the
final mapping and does not constitute retirement or release readiness.

## 4. Exact external incoming endpoint remaps (23 groups)

Each row is one captured FK group, seq=0, existing parent `id`; all are immediate
NO ACTION for update/delete, MATCH NONE. Rewrite the integer through that parent's
exact source→target map, and type-restrict the new FK. Children retained at an
intermediate stage keep their column names, nullability and all other constraints;
future child consolidation changes ownership, not the identity of the edge.

| # | Existing child.column | Existing parent | Required target endpoint |
| ---: | --- | --- | --- |
| 1 | business_task_dingtalk_links.business_task_id | business_tasks | business(task).id |
| 2 | business_task_follow_ups.business_task_id | business_tasks | business(task).id |
| 3 | business_task_follow_ups.source_signal_id | business_task_signals | actions(signal_fact).id |
| 4 | business_task_todo_sync_outbox.business_task_id | business_tasks | business(task).id |
| 5 | business_task_relations.from_task_id | business_tasks | business(task).id |
| 6 | business_task_relations.to_task_id | business_tasks | business(task).id |
| 7 | business_task_relations.supporting_signal_id | business_task_signals | actions(signal_fact).id |
| 8 | business_work_cluster_tasks.task_id | business_tasks | business(task).id |
| 9 | business_task_anchor_links.task_id | business_tasks | business(task).id |
| 10 | business_task_anchor_links.anchor_id | business_anchors | business(anchor).id |
| 11 | business_task_anchor_links.evidence_signal_id | business_task_signals | actions(signal_fact).id |
| 12 | business_project_candidates.confirmed_project_id | business_projects | business(project).id |
| 13 | business_project_candidates.confirmation_signal_id | business_task_signals | actions(signal_fact).id |
| 14 | business_attention_items.anchor_id | business_anchors | business(anchor).id |
| 15 | business_attention_items.evidence_signal_id | business_task_signals | actions(signal_fact).id |
| 16 | business_attention_items.resolution_signal_id | business_task_signals | actions(signal_fact).id |
| 17 | business_attention_tasks.task_id | business_tasks | business(task).id |
| 18 | business_attention_proposal_tasks.task_id | business_tasks | business(task).id |
| 19 | business_attention_events.signal_id | business_task_signals | actions(signal_fact).id |
| 20 | business_legacy_links.task_id | business_tasks | business(task).id |
| 21 | business_legacy_links.signal_id | business_task_signals | actions(signal_fact).id |
| 22 | business_legacy_links.anchor_id | business_anchors | business(anchor).id |
| 23 | business_legacy_links.project_id | business_projects | business(project).id |

These are **12 physical child families / 118 fields**. The accepted JSON includes
each child's literal DDL/xinfo/FKs. It has no external-owner trigger/view reference
to this boundary. That absence does not remove child FK checks or allow ignoring
their other parents. Relations and anchor associations become their finite approved
action kinds with these exact endpoints, status/reason/evidence and existing
self-link/unique constraints; cluster/task and Attention/task/proposal membership
retain their distinct kinds/roles. Candidate confirmation remains its two direct
edges. Legacy links carry only actually verified assignments/legacy keys and all
other existing endpoints; no guessing and no generic relations replacement.
Followup9 owns the first four edges' families plus its separately prepared children;
its disabled retained-business FK conversion must later be composed once, not
implemented a second time or described as published. Other child conversions are
separate finite units, with complete current declarations required at release.

## 5. Smallest implementable storage unit and bounded alternatives

Recommend next: **unselected Business anchor declaration and source-shaped read
projection only**, covering six existing columns. It has no outgoing FK, no body
or mixed context, four exact CHECKs, two unique indexes and one independent native
public-ID stream (actual water 50; fresh absent). Define only the Business skeleton
needed by that kind and the six mappings above, compare both captured variants in
native in-memory SQLite, including BINARY uniqueness, all actual omitted/default/
NULL/affinity behavior and explicit negative/zero IDs. Exercise target declaration
with synthetic values; do not create a DB source copy, touch constructors, add a
flag/version, write migration/caller code or claim storage writers are complete.
Allocator representation and deterministic public/physical identity encoding must
be settled in that declaration review; no activation depends on this preparation.

This is implementable without fetching any missing source. It is deliberately a
small structural anchor for the accepted business table, not a six-column runtime
migration: Projects and two external child families already depend on anchors,
so activating it alone would break the existing storage closure. The following
bounded unit can add source-shaped anchor writes/allocator parity, then the Project
kind and exact typed composite FK. Full target SQL for the other 78 columns must
not be declared solved by this document.

| Alternative | Benefit | Concrete cost / blocker |
| --- | --- | --- |
| Anchor declaration/projection first (recommended) | Independent finite native constraints; establishes Business identity/value representation without any source retirement | No runtime/table-count reduction; writes, Projects and incoming endpoints remain future work |
| Four Business kinds together (Task, Project, Anchor, context revision; 42 columns) | More useful shared declaration and internal FK coverage; adopted facts need no body deletion | Larger type/allocator/JSON/typed FK comparison; retains Signal/source endpoints until separate conversion; still cannot activate without children/actions/callers |
| Full ten-family target and release | Closes Task/Project atomic boundary and source retirement in one release | Blocked by 158 unproved bodies, mixed context/phrase readers, final inline/shared identity binding, all child remaps and actual published predecessor; cannot fabricate final DDL or source availability |

Any later writer/migration must preserve the **existing caller-owned transaction**:
Task path writes document, Signal, Task, evidence, date facts and events together;
Project adds Anchor/Project/evidence/context revisions inside the same Task Agent
transaction (`app/task_agent.py:1713,2532-2536`, `app/task_semantic_service.py:445-485`,
`app/project_context_service.py:58`). A late FK failure must roll back all changed
rows and allocators. Replays require the exact persisted Signal payload and latest
original event public ID, even after merge copies evidence. Project context
validation and evidence insertion run in that same transaction. This introduces
no policy/review gate; these are current storage and caller contracts.

Before release: settle the finite target SQL/codec; implement tested source-shaped
writes and exact event ordering; prove source ownership/locator and body/phrase
recovery; remap all 12 internal groups and the 23 incoming groups; convert callers
without a duplicate writer/cache; bind the actual published predecessor and only
then design coupled migration/inverse/constructor selection. No new migration
number or speculative published successor is assigned here.

## 6. Self-review boundary

The 84 map rows were matched mechanically to accepted actual xinfo and approved
CSV by exact `(family,column,type,notnull,pk)`, including each default and both
ordinals. All 23 numbered endpoint rows were matched to captured incoming FK
`(child_table,from,parent,to)` after excluding internal parents' own rows. The
constraint inventory totals 37 CHECKs, 16 indexes, nine triggers, eight independent
AUTOINCREMENT streams, two composite PKs and 12 internal groups / 13 FK rows.
Only this document and the author's claim row changed. No runtime tests were
necessary for a documentation-only proposal; no body/native/DB/provider read,
implementation, migration, version, push or deploy occurred. Independent SPEC
then QUALITY review remains root-owned.
