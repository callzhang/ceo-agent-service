# Four-kind Business Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select the accepted 42-field Task, Project, Anchor and Project context revision representation as one complete Business cutover, preserving retained child facts and current application behavior through the existing unpublished paired Store/Email release.

**Architecture:** A finite Business migration module captures the exact source, precomputes all four public-to-physical identity maps, copies parents and fifteen retained children, and supplies a current-fact inverse. Current Business projections and writers replace every direct old-parent SQL owner and bootstrap path. The current Store/Email coordinator publishes Email29 and Business four-kind under its one transaction and two markers; fresh construction and both cold reopen orders use the same target.

**Tech Stack:** Python `sqlite3`, existing tagged SQLite value codec and finite Business projections, native generated columns/FKs/indexes, focused pytest, Ruff, offline SQLite backup copies and provider-substitute harnesses.

---

## Frozen decision and file responsibilities

The target is the approved ten-core-plus-ledger design in `docs/superpowers/specs/2026-10-11-database-redesign-design.md`. Select exactly the four already implemented kinds in `app/business_definitions.py`, `app/business_storage.py` and `tests/test_business_storage.py` at `c9c2c74f`: Task 19, Project 12, Anchor 6 and Project context revision 5 source fields. Their 414 storage and 450 adjacent passes are prior **unselected** evidence. Preserve original signed integer public IDs, four independent public waters and separate positive `business.id` physical IDs. Do not redesign fields, add kinds, copy bodies, enable Followup9, or change source plain INSERT/`SQLITE_FULL` whole-transaction behavior.

Source authority is `docs/superpowers/specs/2026-10-15-database-redesign-business-core-native-source.json` and executable `tests/test_business_core_source.py`. The accepted retained-child closure is `docs/database-redesign-business-retained-child-closure-20261015.md`. The actual published predecessor is Store `2026-10-13.5` / Email `51` at verified production `7dadc492`. Email29 / Store `2026-10-14.1` / Email `52` remains **unpublished**. Extend that same candidate pair, with no intermediate Business version. Before actual acceptance rebind the then-published source and reconcile any selected Followup9 change; the current plan does not assert that an older copy is current.

The child boundary is fifteen existing tables, 135 columns and 19 changed FK endpoints (13 Task, three Project, three Anchor); 21 other FK groups stay exact. Add three VIRTUAL parent aliases and three full BINARY UNIQUE parent indexes, retaining the exact child `SELECT *` shape and public values. Preserve 16 explicit plus 13 autoindexes, three date immutability triggers, nine child AUTOINCREMENT waters and six child hidden ROWID streams. Keep Signal, source documents, adopted evidence, date and Task event tables. The 158 unproved historical bodies block later body retirement, not this partial unit. The new indexes have an unmeasured space/write cost. Four parents become one table, net three fewer; combined 121→105 noninternal tables is an estimate only.

| File | Responsibility |
| --- | --- |
| `app/business_definitions.py` | Add only three typed public VIRTUAL aliases and full UNIQUE indexes to accepted Business DDL. |
| `app/business_schema_release.py` (new) | Finite four-parent/fifteen-child source capture, exact 19 FK translations, forward copy, current-fact inverse and target declaration verification. No connection, marker or transaction ownership. |
| `app/action_email_schema_release.py` | Carry Email and Business captures through existing paired publication/subprocess; forward Email then Business then both markers; inverse Business then Email then original markers. |
| `app/store_schema_release.py` | Select only published Store19/Email51 to candidate Store20/Email52; keep other simultaneous-pair refusals. |
| `app/store.py` | Select Business in current/fresh bootstrap, manifest and Store direct reads/writes; retain public method contracts and transactions. |
| `app/email_store.py` | Keep Email52 constructor/fresh non-action factory; validate both constructor orders against selected Business. |
| `app/project_context_service.py`, `app/task_business_resolution.py`, `app/task_semantic_service.py`, `app/task_agent.py`, `app/task_attention_projection.py`, `app/minutes_owner_backfill.py`, `app/todo_completion.py`, `app/task_semantic_import.py`, `app/web_api/tasks.py` | Direct-SQL caller closure in Task 2. |
| `scripts/inspect_task_attention.py`, `scripts/replay_task_attention.py`, `scripts/validate_native_copy_additional.py` | Target-aware diagnostics and copy-eval projections. The independent current-pair harness repair is a prerequisite; do not duplicate it here. |
| `scripts/validate_action_email_copy.py`, `scripts/validate_native_copy_lifecycle.py` | Extend the existing offline paired-copy proof and native lifecycle publication to the combined target; preserve genuine old-code acceptance and in-memory provider boundary. |
| `tests/test_business_schema_release.py`, `tests/test_business_integration_callers.py`, `tests/test_business_copy_proof.py` (new) | Native constraints, callers, fresh/reopen, rollback and copied-source proof. |
| `docs/architecture.md`, `docs/runtime-mechanism.md`, `docs/database-redesign-business-four-kind-integration-validation.md` (new) | Same-commit current behavior and evidence. |

Before implementing, read `docs/agent-claims.md`, claim only edited files, stage only owned hunks and commit behavior docs with behavior changes. This plan author's claim does not claim implementation files.

## Task 1: Exact Business declaration and finite migration

**Files:** modify `app/business_definitions.py`; create `app/business_schema_release.py` and `tests/test_business_schema_release.py`; update same-commit behavior docs when selected.

- [ ] **Step 1: Write RED structural tests.** Reconstruct actual and fresh native sources with the `CAPTURE` pattern from `tests/test_business_core_source.py`. Assert the target `business` xinfo adds exactly `task_public_id`, `project_public_id` and `anchor_public_id` as VIRTUAL; each new index is full UNIQUE/BINARY. Exercise every 19 child endpoint with valid public parent, wrong-kind-only parent, referenced parent DELETE/public-ID UPDATE, and no-child success control. Require each of 15 child xinfo lists unchanged and all 40 FK groups equal source except those exact 19. Retained `dict(row)`/`extra="forbid"` hydration remains accepted.

```python
@pytest.mark.parametrize("kind,alias", [
    ("task", "task_public_id"), ("project", "project_public_id"),
    ("anchor", "anchor_public_id"),
])
def test_typed_public_parent_key(db, kind, alias):
    xinfo = {row[1]: row for row in db.execute("pragma table_xinfo(business)")}
    assert xinfo[alias][6] == 2
    index = db.execute("select sql from sqlite_schema where name=?",
                       (f"idx_business_{kind}_public_id",)).fetchone()[0]
    assert index.lower() == (
        f"create unique index idx_business_{kind}_public_id on business({alias})"
    )
```

Run `pytest -q tests/test_business_schema_release.py -k typed_public_parent_key`. Expected: three RED cases because aliases/indexes are absent.

- [ ] **Step 2: Add the three aliases/indexes only.** Insert these declarations beside generated `public_id` in `_definitions()`, and append these SQL strings to existing `BUSINESS_INDEX_DDL`. Keep 42 fields, existing ten indexes, four allocator triggers, internal typed physical/public FKs, projections and codec unchanged.

```python
columns.extend((
    "task_public_id integer generated always as "
    "(case when record_type='task' then public_id end) virtual",
    "project_public_id integer generated always as "
    "(case when record_type='project' then public_id end) virtual",
    "anchor_public_id integer generated always as "
    "(case when record_type='anchor' then public_id end) virtual",
))
indexes = indexes + (
    "create unique index idx_business_task_public_id on business(task_public_id)",
    "create unique index idx_business_project_public_id on business(project_public_id)",
    "create unique index idx_business_anchor_public_id on business(anchor_public_id)",
)
```

The `indexes` tuple is constructed inside `_definitions()`; append three tuple entries there (or concatenate after its original assignment). Run `pytest -q tests/test_business_storage.py tests/test_business_schema_release.py -k 'typed_public_parent_key or exact_columns_defaults_and_source_ordinals'`. Expected GREEN and original source projection ordinals unchanged.

- [ ] **Step 3: Freeze the finite endpoint map and RED source capture.** In `app/business_schema_release.py` use `app.action_reply_storage._source_declaration` and `_fingerprint` to capture full four-parent and fifteen-child declarations. The four parent declarations must match the accepted actual/fresh oracle. The external endpoint oracle contains SQL/xinfo/FK, not index/trigger SQL: validate those three parts against it and capture live auxiliary SQL separately. Reject other changed/colliding owners. Capture complete source declarations before any DDL, plus `PRAGMA foreign_key_check` **findings**, which are distinct from schema FK edges. The known unrelated `runs/tasks` finding may remain only as an identical pre/post finding.

```python
SOURCE_PARENTS = {
    "task": "business_tasks",
    "project": "business_projects",
    "anchor": "business_anchors",
    "project_context_revision": "business_project_context_revisions",
}
RETAINED_CHILD_FKS = {
    "business_task_evidence": {"task_id": "task"},
    "business_task_date_evidence": {"task_id": "task"},
    "business_task_events": {"task_id": "task"},
    "business_project_evidence": {"project_id": "project"},
    "business_task_dingtalk_links": {"business_task_id": "task"},
    "business_task_follow_ups": {"business_task_id": "task"},
    "business_task_todo_sync_outbox": {"business_task_id": "task"},
    "business_task_relations": {"from_task_id": "task", "to_task_id": "task"},
    "business_work_cluster_tasks": {"task_id": "task"},
    "business_task_anchor_links": {"task_id": "task", "anchor_id": "anchor"},
    "business_project_candidates": {"confirmed_project_id": "project"},
    "business_attention_items": {"anchor_id": "anchor"},
    "business_attention_tasks": {"task_id": "task"},
    "business_attention_proposal_tasks": {"task_id": "task"},
    "business_legacy_links": {
        "task_id": "task", "anchor_id": "anchor", "project_id": "project",
    },
}
PUBLIC_ALIAS = {
    "task": "task_public_id", "project": "project_public_id",
    "anchor": "anchor_public_id",
}

def translated_child_sql(table: str, sql: str, *, inverse: bool = False) -> str:
    for column, kind in RETAINED_CHILD_FKS[table].items():
        source = f"foreign key({column}) references {SOURCE_PARENTS[kind]}(id)"
        target = f"foreign key({column}) references business({PUBLIC_ALIAS[kind]})"
        old, new = (target, source) if inverse else (source, target)
        if sql.count(old) != 1:
            raise ValueError(f"unsupported {table}.{column} FK declaration")
        sql = sql.replace(old, new, 1)
    return sql
```

The map has exactly 19 entries; the other 21 FK groups are never rewritten. Use finite exact literal replacement only after validating the source declaration; no generic DDL parser, regex rewrite or compatibility view. RED command: `pytest -q tests/test_business_schema_release.py -k 'source_capture or exact_19_child_endpoints'`. Expected missing capture/migration failure, then GREEN after Step 4.

The accepted parent declaration fingerprints, computed with `_source_declaration` and `_fingerprint` from the oracle, are:

| Variant | Task | Project | Anchor | Context revision |
| --- | --- | --- | --- | --- |
| actual | `91b5763374243a76dc446881190d1f8e68ea9526a82ba98c920e0fe935957d07` | `6d73ac34c4cc559f164afbe344d76e913d3709ebfecb76646b2307b37e7f85b4` | `d57eb678de2b9b06728e870ca3340bfa9297f301d6a11cffc3f52a8987a7a36f` | `2b7413b2ceabd2ef752ad218d860615a9e3d0d36d04966ff0ca3226286c98ab8` |
| fresh | `19341a7f00ed976b0fee462b1fcfe1bcadc061ebc555d6757d9e89c1151b81c0` | `4c75ae1efd72dc5b27772a9a69454a2f1bcca619b35376fa087ec6206c70cf98` | `d57eb678de2b9b06728e870ca3340bfa9297f301d6a11cffc3f52a8987a7a36f` | `2b7413b2ceabd2ef752ad218d860615a9e3d0d36d04966ff0ca3226286c98ab8` |

For the 15 children, hash a sorted-key JSON dictionary of each table's `sql`, `columns` and `foreign_keys` in map order. Accepted actual hash is `d59d3ae841b3e410305079267d4c2a12b26103fddc06929cf316ec78ab8e28c1`; fresh is `d4bab7b6cd1e59bbcd2c5154d4b43bfd06196ed5ba15cae5f8149d63aef02719`. These hashes do not replace full live auxiliary capture. Refresh only from a newly verified immediate predecessor.

- [ ] **Step 4: Implement source capture and complete-map forward copy.** `capture_business_source(db)` returns the variant, full 19 declarations, parent/child sequence presence and values, and exact `_foreign_keys(db)` findings; it stores no business rows. Before DDL, reject moved-parent FK findings, mixed existing Business objects or allocator rows, target-name collisions, unsupported external MAIN/TEMP schema references and fingerprint mismatch. Use `app.action_reply_storage._source_declaration` and the finite dependency inspection convention in `app/action_email_storage.py`. The accepted unrelated `runs/tasks` finding may remain only as an identical pre/post finding. The external child oracle has SQL/xinfo/FK but lacks index/trigger SQL; validate the former three, capture the live auxiliary SQL and preserve it.

`migrate_business_schema(db)` requires an existing FK-off transaction and recaptures current rows after the write lock. Read all four parent row sets and 15 child row sets with explicit columns, `typeof` tuples, hidden `rowid` for six composite-PK children, and thirteen independent AUTOINCREMENT waters (four parents, nine children). Before inserting anything, assign a positive physical ID to every source kind/public-ID pair and resolve all three internal reference types from that complete map. Higher-ID merges and multirow merge cycles must have real mapped refs. Missing moved parents fail before DDL; do not invoke the runtime `insert_business` physical-zero path for bulk copy.

Then drop owned child objects/tables and four parents, create Business, insert each parent with explicit `id,record_type,legacy_key,payload_json,title,status,created_at,updated_at,merged_task_ref,canonical_anchor_ref,context_project_ref`, rebuild every child through `translated_child_sql`, insert exact old values and hidden ROWIDs, recreate source indexes/triggers and restore all public/child sequence waters. Use `_encoded_sqlite_value` and `business_payload_fields`; keep SQLite native affinity/default/storage classes. Autoindexes arise from unchanged PK/UNIQUE clauses. Check source-shaped projection values and `typeof`, 15 child rows/ROWIDs, 40 FK groups, 29 child indexes, three date triggers, `quick_check`, and exact equality of native FK **findings** before returning metadata-only inverse declarations. No helper opens a connection or commits.

The complete `migrate_business_schema` body and every called helper are in the normative Task 1 module below.

Install the complete finite module in this step. Reuse existing `app.action_email_storage._quote`/`_set_sequence`, `app.business_storage.create_business_schema` and accepted projections/codec. Only this bulk transaction may disable FK enforcement; ordinary `insert_business` retains immediate native behavior and whole-transaction `SQLITE_FULL` handling. Native `PRAGMA foreign_key_check` must equal the source **findings**; never compare it to a captured schema FK graph.

Tests must include signed public IDs colliding across kinds, higher-ID merge and two-row cycle, Project/Anchor/revision refs, nullable candidate/legacy edges, legal BLOB/infinity/NULL classes, deleted-high waters, all 19 wrong-kind/parent mutation controls, all rows/waters/ROWIDs/indexes/triggers, unrelated FK finding unchanged and failure after one child drop with whole-transaction rollback. Run `pytest -q tests/test_business_schema_release.py tests/test_business_storage.py tests/test_business_core_source.py`; expected GREEN and `quick_check='ok'`.

- [ ] **Step 5: Implement current-fact inverse before selecting the release.** `restore_business_schema(db, captured_declarations)` first validates current Business and translated-child declarations, then reads **current** Business source-shaped projections, children, ROWIDs and waters. It drops owned target objects inside caller's FK-off transaction, recreates exact captured original parent/child DDL and auxiliary SQL, inserts current facts in captured actual/fresh source column order, restores current public waters under original parent names and verifies source declarations/facts/FK findings. Unsupported current declarations/facts fail without changing the target; any later exception rolls back the entire transaction. Restore marker timestamps only in Task 3 after Email inverse also succeeds.

The complete `restore_business_schema` body and every called helper are in the normative Task 1 module below.

Use the complete finite module in the same step. Include target writes made **after prepare**; inverse cannot replay captured old rows over them. Test an unrepresentable current fact, exception after DDL, repeated rollback and target→inverse→reforward. Run `pytest -q tests/test_business_schema_release.py -k 'inverse or cycle or rowid or water or failure'`; expect GREEN for actual/fresh. Commit Task 1 with behavior docs after focused GREEN, scoped Ruff and `git diff --check`.

## Task 2: All current parent callers, bootstrap and diagnostics

**Files:** `app/store.py`, `app/project_context_service.py`, `app/task_business_resolution.py`, `app/task_semantic_service.py`, `app/task_agent.py`, `app/task_attention_projection.py`, `app/minutes_owner_backfill.py`, `app/todo_completion.py`, `app/task_semantic_import.py`, `app/web_api/tasks.py`, `scripts/inspect_task_attention.py`, `scripts/replay_task_attention.py`, `scripts/validate_native_copy_additional.py`, `tests/test_business_integration_callers.py`, relevant focused existing tests and same-commit behavior docs.

- [ ] **Step 1: RED public behavior.** Build a current-target fixture through Task 1's finite Business migration; exercise four public Store CRUD/projection families, Project context revision, Task merge, Project/Anchor registration, retained child evidence/date/event/link/follow-up writes/reads, Attention resolution and Task Web detail. Assert original model field set and **public** `id`, never physical `business.id`. Include strict `dict(row)` retained-child hydration. Add an SQL trace assertion that current app requests contain no `business_tasks`, `business_projects`, `business_anchors` or `business_project_context_revisions` after initialization, except historical source-only migration/oracle code. Expected RED: current callers still query retired parents.

```python
def test_current_task_api_returns_public_id(current_business_store):
    store = current_business_store
    with store._connect() as db:
        db.execute("begin immediate")
        task_id = store.create_business_task_in_transaction(
            title="synthetic", stage="candidate", _db=db
        )
        db.commit()
    row = store.get_business_task(task_id)
    assert row.id == task_id
    with store._connect() as db:
        physical = db.execute(
            "select id from business where record_type='task' and public_id=?",
            (task_id,),
        ).fetchone()[0]
    assert physical > 0
```

The `current_business_store` fixture belongs in the new test file and constructs the real Store on a target schema; use the existing `tests/test_task_semantic_store.py` Store fixture/transaction conventions. The assertion above is intentionally about the public API, not an assumed inequality between public and physical IDs. Run `pytest -q tests/test_business_integration_callers.py -k current_task_api_returns_public_id`; expect RED while old-parent SQL remains.

- [ ] **Step 2: Convert every Store direct parent SQL owner.** In `app/store.py` change: `create_business_task_in_transaction`, `get_business_task_in_transaction`, `update_business_task_in_transaction`, `get_business_task`, `list_business_tasks`, `list_business_tasks_for_projection`; both `list_unmerged_formal_business_task_ids_for_signal_in_transaction` and `list_unmerged_formal_business_task_ids_for_source_in_transaction`; `get_business_anchor_in_transaction`, `get_business_anchor_by_identity_in_transaction`, `create_business_anchor_in_transaction`, `list_business_anchors`; `get_business_project_in_transaction`, `create_business_project_in_transaction`, `list_business_projects` and three CRM mutations (`update_business_project_crm_customer_lookup_in_transaction`, `confirm_business_project_crm_customer`, `clear_business_project_crm_customer`); `get_business_project_context_in_transaction` and `list_business_project_context_revisions_in_transaction`; `list_business_task_ids_for_project`, `list_business_task_project_links`, `derive_business_task_relevance_in_transaction`, `list_business_task_project_customer_names`; `claim_business_task_follow_up_for_send`. Preserve old aliases, filters, public-ID ordering, claim predicates, transaction scopes and return models. Use finite `BUSINESS_PROJECTIONS[kind]` as inline SQL relations, never `business.*` for a source model. Parent mutations use `insert_business`/`update_business`/`delete_business` in existing transactions and return `BusinessWriteResult.lastrowid` on insert. Retained child writes still take public IDs.

```python
from app.business_definitions import BUSINESS_PROJECTIONS
from app.business_storage import insert_business, update_business

# Replace an old business_tasks table under its existing t alias:
task_relation = "(" + BUSINESS_PROJECTIONS["task"] + ")"
row = db.execute(
    "select t.* from " + task_relation + " as t where t.id=?",
    (task_id,),
).fetchone()

# Replace old parent DML inside the existing caller transaction:
created = insert_business(db, "task", source_values)
public_task_id = created.lastrowid
changed = update_business(db, "task", task_id, source_patch)
```

`source_values` and `source_patch` are the current method's exact values/patch dictionaries; preserve their existing signatures. This code is the finite relation/write substitution used at each listed owner, not a new general SQL translator. For `claim_business_task_follow_up_for_send`, keep the existing `BEGIN IMMEDIATE`/claim update and `f.*` row shape. Run `pytest -q tests/test_business_integration_callers.py tests/test_business_storage.py tests/test_task_semantic_store.py tests/test_project_context_service.py tests/test_follow_up.py`; expect GREEN.

- [ ] **Step 3: Convert non-Store direct owners without policy change.** `ProjectContextService._require_project` and `.apply` keep latest revision and Project evidence in one transaction. `BusinessResolutionService.register_official_project`, `.register_source_project` and `.confirm_project_candidate` keep official/candidate semantics. `TaskSemanticService.record_suggestion` and `.merge_same_deliverable` keep source identity/merge checks. `app/task_agent.py` ``_confirmed_official_project_anchors`, ``_official_project` and ``_validate_stored_project_assessments` keep registered Project/Anchor validation. `BusinessAttentionProjection._validate_proposal`, `._current_eligible_task_ids` and `.resolve` retain category/status policy. `minutes_owner_backfill.ownerless_candidate_meetings` still joins Signal/document and reads original body. `todo_completion.complete_business_task_from_external_todo` updates Business Task alongside unchanged Signal/evidence/event/follow-up writes. `task_semantic_import` ``_rows`, ``_fingerprint`, ``_plan_items`, ``_existing_link_matches` and ``apply_task_semantic_import_manifest` use source-shaped registry projections for fingerprints and current Business existence; no old-table fallback. `app/web_api/tasks.py` ``_confirmed_project_ids_by_task`, ``_project_summary_context_and_activity`, ``_project_summary_payload` and ``business_project_detail` keep latest public revision CTE, max timestamp, revision count, ordering and response shape.

```python
# Source-shaped inline relation for a direct SQL caller:
project_relation = "(" + BUSINESS_PROJECTIONS["project"] + ")"
revision_relation = "(" + BUSINESS_PROJECTIONS["project_context_revision"] + ")"
row = db.execute(
    "select p.id,r.id as revision_id,r.context_json from "
    + project_relation + " as p left join " + revision_relation
    + " as r on r.project_id=p.id where p.id=? "
    "order by r.id desc limit 1",
    (project_id,),
).fetchone()
```

This shows the exact public-ID join. Apply each original caller's **existing** SQL selection, predicates and result fields around these two relations; do not replace them with the illustrative three-column read. RED/GREEN cases must cover import fingerprint parity, merged Task readback, CRM confirmation, ownerless source body, Attention eligibility, Task Web detail and Project revision count. Run `pytest -q tests/test_task_business_resolution.py tests/test_task_semantic_service.py tests/test_task_semantic_import.py tests/test_task_attention_projection.py tests/test_task_attention_multisource.py tests/test_minutes_owner_backfill.py tests/test_todo_completion.py tests/test_web_api_task_project_summary.py tests/test_task_agent.py`; expect current app SQL trace free of old-parent access and all assertions GREEN.

- [ ] **Step 4: Replace current bootstrap and metadata together.** Remove four old parents from `STORE_SCHEMA_REQUIRED_TABLES`/`COLUMNS` and their seven parent indexes from `STORE_SCHEMA_REQUIRED_INDEXES`. Require `business`, exact table/alias/index/four-trigger declaration fingerprint and 15 rebuilt child xinfo/FK/index/trigger sets; markers alone are insufficient. Use `PRAGMA table_xinfo` for VIRTUAL aliases, not only `table_info`. In `store.py::_initialize` remove the four old parent CREATE/ALTER current paths at approximately lines 3332, 3555, 3585, 3610, 4014 and 4408; create Business before the retained children and select the exact finite child target SQL. Preserve unchanged Signal/document/other child declarations and non-Business bootstrap. Change `_migrate_project_attention_evidence` to join source-shaped Project during initialization. A fresh Store-first construction must create all target objects without running source migration; EmailStore-first still calls the real Store constructor outside Email's schema lock. Test both fresh orders, both reopen orders and cached-path reopen.

```python
# Current manifest's Business branch, added to its existing conjunction:
business_current = business_schema_is_current(db)
required_columns_present = all(
    set(required).issubset({
        row["name"] for row in db.execute(f"pragma table_xinfo({table_name})")
    })
    for table_name, required in STORE_SCHEMA_REQUIRED_COLUMNS.items()
)
```

Define `business_schema_is_current(db)` in Task 1's module using exact finite target declaration comparison and no read-path repair. RED/GREEN marker-correct malformed alias/index/child FK/date trigger tests must fail schema acceptance; stale bootstrap must never recreate an obsolete parent. Keep historical old-source SQL bound to its historical source module/test, not as a current fallback.

- [ ] **Step 5: Convert maintained diagnostics/evals after the independent current-pair harness repair lands.** Use the same projections in `scripts/inspect_task_attention.py` `inspect_run`; `scripts/replay_task_attention.py` `comparison_capabilities`, `read_project_centered_expectations`, `read_domain`, `readback` and `domain_snapshot`; and `scripts/validate_native_copy_additional.py` `work_summary`. The snapshot enumerator currently uses `business_*` and misses `business`; explicitly include it. Root separately owns the additional harness's current Email pair publication, target baseline/hash, original-row comparison and candidate-only high Task-ID reservation. Reuse that accepted repair, then change only its future Business projection/comparison: migrated four parents are absent, so compare them through source-shaped target projections; leave unchanged tables under literal comparison. Do not edit this script concurrently with that repair. Run the updated additional harness offline with an in-memory provider substitute; record work summary/scheduled/workbench separately only after their own checks pass and clean task-owned temp.

Run the complete selected Task/Project/Anchor/Context focused app and diagnostic files, scoped Ruff and `git diff --check`. Commit Task 2 with behavior docs only after every named caller and bootstrap path is covered; there must be no current direct old-parent SQL reference outside historical source/migration/oracle code.

## Task 3: Extend the same unpublished paired Store/Email release

**Files:** `app/action_email_schema_release.py`, `app/store_schema_release.py`, `app/store.py`, `app/email_store.py` if its current preflight needs exact Business shape, `tests/test_email_action_schema_release.py`, `tests/test_business_schema_release.py`, `tests/test_business_integration_callers.py`, same-commit behavior docs.

- [ ] **Step 1: RED the combined pair, atomicity and cold constructors.** From exact Store19/Email51 actual and fresh source fixtures, prepare the one Store20/Email52 candidate. Assert source preparation captures Email **and** Business declarations before any DDL and leaves file bytes/markers unchanged. Publication must yield Email29 actions, Business four-kind, fifteen rebuilt children and both target markers in a single committed state; no independent half-pair construction. Test Store-first/Email-first fresh, both published-predecessor orders through coordinator, both target cold reopen orders, cached-path reopen and a malformed target Business alias/index/FK with apparently correct markers. Existing Email fingerprints, actions and thirteen source-table behavior remain exact. The first RED must show current release only converts Email and leaves old four Business parents.

```python
def test_paired_publication_contains_email_and_business(source_with_business):
    release = prepare_database_schema_release(ROOT, source_with_business)
    assert release is not None
    release.apply()
    with closing(sqlite3.connect(source_with_business)) as db:
        assert db.execute(
            "select value from service_state where key='store_schema_version'"
        ).fetchone() == ("2026-10-14.1",)
        assert db.execute("select max(version) from email_schema_migrations").fetchone() == (52,)
        assert db.execute(
            "select 1 from sqlite_schema where type='table' and name='business'"
        ).fetchone()
        assert not db.execute(
            "select 1 from sqlite_schema where type='table' and name in "
            "('business_tasks','business_projects','business_anchors',"
            "'business_project_context_revisions')"
        ).fetchone()
```

The `source_with_business` fixture must extend the existing `tests/test_email_action_schema_release.py::source` actual/fresh source with the accepted four-parent/fifteen-child source fixture and realistic rows. Do not synthesize a fake intermediate marker.

- [ ] **Step 2: Carry metadata through the existing paired object/subprocess.** Keep `SOURCE_STORE_VERSION='2026-10-13.5'`, `SOURCE_EMAIL_VERSION=51`, `TARGET_STORE_VERSION='2026-10-14.1'`, `TARGET_EMAIL_VERSION=52`. In `prepare_action_email_schema_release`, under one read snapshot capture Email with existing `capture_email_action_source(db)`, then Business with Task 1's `capture_business_source(db)` before DDL. Extend the dataclass's source metadata with a Business key, and extend `_inverse_metadata`/`_migrate_pair`/`_paired_initializer` to serialize exact declarations and original marker timestamps, not business rows. `_verify_source` recaptures both under the write transaction and compares metadata; `_verify_target` requires Email29 **and** `business_schema_is_current(db)` plus original FK findings. Keep `source_fk` as `PRAGMA foreign_key_check` findings; never reinterpret it as the schema FK graph. The known unrelated `runs/tasks` finding survives exactly; unsupported broken moved-parent findings are rejected.

```python
# Existing release class: exact additions to the ordered protocol.
source = capture_email_action_source(db)
source["business"] = capture_business_source(db)
release = ActionEmailSchemaRelease(
    root, path, version, versions, source, _foreign_keys(db),
    target_store_version, target_email_version,
)

# Existing apply() under pragma foreign_keys=off; begin immediate:
self._verify_source(db)  # captures and verifies both source declarations
email_inverse = migrate_email_action_schema(db)
business_inverse = migrate_business_schema(db)
if email_inverse != email_inverse_metadata(self.source_schema):
    raise ValueError("Email source changed during paired conversion")
if business_inverse != business_inverse_metadata(self.source_schema["business"]):
    raise ValueError("Business source changed during paired conversion")
# Existing update of Store marker, insert Email52 ledger row, _verify_target,
# COMMIT follow these conversions in this same try/rollback.
```

Define `email_inverse_metadata` as the existing five-key `_inverse_metadata` Email selection renamed or scoped to Email keys; define `business_inverse_metadata` in Task 1 and import it. Both named functions must be implemented before use. Do not change the original Email source fingerprints, its migration/inverse or the post-commit cold opener ordering (`AutoReplyStore` then `EmailStore`). `app/store_schema_release.py:prepare_database_schema_release` continues selecting this exact pair once and keeps refusal for every unsupported simultaneous pair. It must not route the source into independent Store/Email migrations.

Use these concrete metadata and verification bodies in `app/action_email_schema_release.py`; the module already imports `capture_email_action_source`, `action_schema_is_current`, `_foreign_keys`, `SOURCE_TABLES` and `EXTERNAL_TRIGGER_NAMES`. Import `business_inverse_metadata`, `business_schema_is_current`, `capture_business_source`, `migrate_business_schema` and `restore_business_schema` from Task 1's module. Keep the existing Email trigger comparison in `_verify_target` immediately before `_verify_integrity`.

```python
def email_inverse_metadata(source):
    return {key: source[key] for key in (
        "action_variant", "email_variant", "source_actions",
        "source_tables", "external_triggers",
    )}

def _inverse_metadata(source):
    return {
        **email_inverse_metadata(source),
        "business": business_inverse_metadata(source["business"]),
    }

# ActionEmailSchemaRelease._verify_source, after marker comparison:
current = capture_email_action_source(db)
current["business"] = capture_business_source(db)
if _inverse_metadata(current) != _inverse_metadata(self.source_schema):
    raise ValueError("paired Email/Business source declaration changed")
self._verify_integrity(db)

# ActionEmailSchemaRelease._verify_target, after existing Email shape check:
if not business_schema_is_current(
    db, self.source_schema["business"]["variant"]
):
    raise ValueError("paired Business target declaration mismatch")
# Then keep existing external Email trigger comparison and _verify_integrity.
```

`capture_business_source` returns `fk_findings` in addition to declarations; `business_inverse_metadata` intentionally retains only variant and declarations. `source_fk` remains the original `_foreign_keys` findings and is checked after conversion/inverse. The original marker/ledger readback remains byte-for-byte exact.

- [ ] **Step 3: Inverse current Business facts before current Email facts.** In the existing `rollback` FK-off `BEGIN IMMEDIATE` block, verify complete combined target, run `restore_business_schema(db, original_business_declarations)` first, then existing `restore_email_action_schema(db, original_email_declarations)`. Restore the original Store value **and updated_at**, remove only inserted Email52 ledger row, require exact original Email ledger versions/**applied_at** values, verify both source declaration sets and original FK findings, then commit. If rollback is called twice, verify unchanged original and return. A failure at any point rolls back the entire inverse, preserving complete target.

```python
# Existing ActionEmailSchemaRelease.rollback() try block after target check:
self._verify_target(db)
restore_business_schema(db, self.source_schema["business"])
restore_email_action_schema(db, email_inverse_metadata(self.source_schema))
db.execute(
    "update main.service_state set value=?,updated_at=? where key=?",
    (*self.original_version, VERSION_KEY),
)
db.execute(
    "delete from main.email_schema_migrations where version=?",
    (self.target_email_version,),
)
self._verify_source(db)
db.commit()
```

Test postpublication Business and Email writes **after preparation** before inverse; inverse and genuine old-code cold constructors must see the new current facts and preserve all public IDs/waters/child ROWIDs. Then reforward those old-code writes into the same target without duplicate/omitted facts. Inject failures before DDL, after Email conversion, during Business child rebuild, after first marker, after target verification and at each inverse stage; fingerprint entire database/markers/facts to prove rollback. Unsupported current Business state must refuse inverse with target intact.

- [ ] **Step 4: Bind current constructors and fingerprints.** `app/store.py` current manifest/initializer from Task 2 must require exact Business target; `app/email_store.py` opens Store before Email lock and must reject a marker-correct malformed Business target through that manifest, including cached-path reopen. `require_current_email_pair` preserves the current fresh Store-first temporary state rule while refusing a source/mixed pair before either constructor caches it. Do not add a second constructor migration, old parent bootstrap fallback or compatibility view. Both fresh orders should make exactly one Business table and one Email29 action schema; old published pair opens only through the deploy coordinator. Keep original `EMAIL_SCHEMA_VERSION=52` and `STORE_SCHEMA_VERSION='2026-10-14.1'` constants.

Run `pytest -q tests/test_business_schema_release.py tests/test_email_action_schema_release.py tests/test_business_integration_callers.py tests/test_email_action_callers.py tests/test_email_current_schema.py tests/test_store_schema_release.py tests/test_email_store_startup_validation.py` after confirming paths exist. Expected GREEN, paired failure injection leaves no half-converted state, and both target constructors are cold-reopen clean. Commit Task 3 with architecture/runtime behavior docs.

## Task 4: Copied-source proof, app paths and publication gate

**Files:** `scripts/validate_action_email_copy.py`, `scripts/validate_native_copy_lifecycle.py`, `scripts/validate_native_copy_additional.py` after its independent repair, `tests/test_business_copy_proof.py`, affected exact app tests, `docs/database-redesign-business-four-kind-integration-validation.md` and same-commit behavior docs.

- [ ] **Step 1: Write RED copy proof tests before using a real copy.** Extend the existing `scripts/validate_action_email_copy.py` protocol to capture Business source facts/schema/waters/ROWIDs alongside Email before the combined pair. Its source oracle must distinguish unchanged tables from four moved parents and 15 rebuilt children: compare unchanged rows literally; compare moved parents through actual/fresh `BUSINESS_PROJECTIONS` with source column order and `typeof`; compare children values and hidden ROWIDs literally despite FK DDL translation. Capture source and candidate `quick_check`, `integrity_check`, FK findings, marker/ledger timestamps and allocator streams separately. Do not compare an old physical parent table against target by name after it has been removed. The RED test inserts a higher-ID Task merge, cyclic merge, public ID collision across kinds, child hidden ROWID gap, independently advanced allocator water and one unrelated `runs/tasks` FK finding, then requires forward→current write→inverse→old-code write→reforward equality. Expected RED until the full paired implementation is selected.

```python
def test_business_copy_is_current_fact_reversible(tmp_path, source_with_business):
    candidate = tmp_path / "candidate.sqlite3"
    with closing(sqlite3.connect(source_with_business)) as old, closing(
        sqlite3.connect(candidate)
    ) as target:
        old.backup(target)
    release = prepare_database_schema_release(ROOT, candidate)
    release.apply()
    with closing(sqlite3.connect(candidate)) as db:
        db.execute("pragma foreign_keys=on")
        db.execute("begin immediate")
        insert_business(db, "task", {
            "title": "post-publication", "stage": "candidate",
        })
        db.commit()
    release.rollback()
    with closing(sqlite3.connect(candidate)) as db:
        assert db.execute(
            "select 1 from business_tasks where title='post-publication'"
        ).fetchone()
    prepare_database_schema_release(ROOT, candidate).apply()
    with closing(sqlite3.connect(candidate)) as db:
        assert db.execute(
            "select 1 from business where record_type='task' "
            "and title='post-publication'"
        ).fetchone()
```

The new test imports `ROOT`, `source_with_business`, `prepare_database_schema_release` and `insert_business` from its own explicit fixture/imports; the source fixture is the one defined in Task 3 tests. Add byte/storage-class/ROWID/sequence assertions and meaningful failure-injection in the same test file. A status or row count alone does not prove current-fact inverse.

- [ ] **Step 2: Run bounded latest-copy proof on root-owned input.** Root obtains a consistent read-only production SQLite backup and an immutable SHA/bytes/source commit/Store marker/Email ledger receipt; this plan execution must not assume the historical `7dad` snapshot is still current. Prepare only if the captured live predecessor is the accepted exact pair and the source declaration fingerprints match. Use `sqlite3.Connection.backup` into a task-specific `mktemp -d /tmp/ceo-business-copy.XXXXXX` directory, never mutate the production checkout/database. On the copy, run the actual `prepare_database_schema_release` → `publish/apply` path and independently read Business/Email target fingerprints, 42-field projections, 15 child rows, 19 translated/21 untouched FK groups, 29 child indexes/three triggers, all thirteen waters/six ROWIDs, full FK findings, `quick_check` and `integrity_check`. Record measured table count and Business index pages/bytes; do not promise space recovery or VACUUM. Inject a copy-only failure into each transaction stage and prove complete rollback.

- [ ] **Step 3: Prove genuine published-old-code cold behavior.** Use the exact published predecessor Git archive in an isolated temp directory and subprocess/PYTHONPATH, as the existing Email copy script does. After inverse, cold-open both `AutoReplyStore` and `EmailStore` from that archive, read Task/Project/Anchor/revision plus representative child and Email facts, make one source-valid Task/Project/context/Email write in an isolated copy, close both, then reforward with current code. Compare all current facts, native storage classes, public IDs, source rows, waters and receipt links. If an arbitrary source SQL value is legal but old application validation rejects it, separate SQL-boundary losslessness from the strict old-app-valid case; never claim a cold app write passed from a direct SQL insert. The old archive must not import current modules.

- [ ] **Step 4: Verify whole app and real native lifecycle on a copied database.** Run selected focused Task/Project/Anchor/context Store APIs; Task semantic import hash; merge/official Project/CRM; ownerless meeting evidence; Attention; Task Web Project summary/detail; retained child follow-up claim; Email action/worker/History; scheduled, work summary and Workbench paths after their own scripts are Business-aware. Update `scripts/validate_native_copy_lifecycle.py::_publish_email_pair` to invoke the same actual combined coordinator, keeping its accepted candidate-only high JavaScript-safe Task-ID reservation and target-baseline hashing. Run one **real Codex** Consumer→Audit approval plus one revision/correction case against the owned copy, with the existing `InMemoryProvider` only and exact native artifact/receipt/readback assertions. Do not contact DingTalk, Email or another real provider. Check source SHA and original source rows unchanged; compare target baseline to postnative copy with the harness's existing table-row proof plus new source-shaped Business projection. Record provider substitute call count, native outcome and cleanup of all task-owned temporary directories. A synthetic model output is not a native acceptance substitute.

- [ ] **Step 5: Complete scoped checks and review before requesting publication.** Run only affected exact tests, not serial whole-tree pytest while service is live. Minimum: `pytest -q tests/test_business_core_source.py tests/test_business_storage.py tests/test_business_schema_release.py tests/test_business_integration_callers.py tests/test_business_copy_proof.py tests/test_email_action_schema_release.py tests/test_email_action_callers.py tests/test_email_current_schema.py tests/test_store_schema_release.py`, then the exact Task/Project/Attention/Web/Email/harness files changed in Task 2. Run `ruff check` on changed Python, `python -m compileall -q` on changed Python, and `git diff --check`. CI needs a **new combined candidate** run and completed result; pending CI `38736466402` for Email09bc cannot count as a pass or as combined Business coverage. Obtain independent spec then quality review of the complete final branch. Record actual source binding, checked paths/results, source/target fingerprints, failure-injection receipts, copied real Codex outcome, measured size and any open risk in the validation doc. Keep report boundaries separate: code, focused tests, copied DB, native substitute, CI, push/merge, deploy and live business result.

- [ ] **Step 6: Final merge/deploy remains a direct Derek decision.** The root must present the complete reviewable combined implementation and evidence to Derek before main merge or production deployment. This plan's execution stops at that final decision unless Derek has directly authorized it in the session. Once authorized, preserve remote increments with a safe normal merge/fast-forward (no reset/rebase/force push), rebind the actual immediate predecessor, push the complete commit, wait for new combined CI, then use `python -m app.deploy` from development only. Formal deploy backs up and rolls code/schema together before work resumes. After deploy, read actual production commit, paired markers, new PID, healthz, queues/claims, Attention, History, Task/Project/Email API details and no unexpected failed/stuck work; never paste secret settings. If the service has resumed external actions, do not roll back to a stale database copy that would erase receipts. No direct production edits, tests in production checkout, provider replay, VACUUM, retention cleanup or invented approval-policy change belongs in this unit.

## Normative Task 1 finite migration module

The complete function bodies below replace the Task 1 ordering sketches. Install this as `app/business_schema_release.py` after Task 1 Step 2 adds the aliases; Task 2 extracts the fifteen retained-child current DDL from the existing Store initializer for fresh construction. The actual/fresh full-child fingerprints include the 29 source indexes and three triggers; the target fingerprints include the translated nineteen FK clauses. The implementation tests must exercise both variants and update these constants only from the newly verified immediate predecessor.

```python
"""Finite Business four-kind source-bound migration reference."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from app.action_reply_storage import _fingerprint, _source_declaration
from app.action_email_storage import _quote, _sequence, _set_sequence
from app.action_storage import _encoded_sqlite_value
from app.business_definitions import (
    BUSINESS_COMMON, BUSINESS_FIELDS, BUSINESS_FRESH_FIELDS,
    BUSINESS_PROJECTIONS, BUSINESS_FRESH_PROJECTIONS, BUSINESS_REFERENCES,
    BUSINESS_TABLE_DDL, BUSINESS_INDEX_DDL, BUSINESS_TRIGGER_DDL,
    business_payload_fields,
)
from app.business_storage import create_business_schema
from app.task_schema_release import _foreign_keys

SOURCE_PARENTS = {
    "task": "business_tasks",
    "project": "business_projects",
    "anchor": "business_anchors",
    "project_context_revision": "business_project_context_revisions",
}
RETAINED_CHILD_FKS = {
    "business_task_evidence": {"task_id": "task"},
    "business_task_date_evidence": {"task_id": "task"},
    "business_task_events": {"task_id": "task"},
    "business_project_evidence": {"project_id": "project"},
    "business_task_dingtalk_links": {"business_task_id": "task"},
    "business_task_follow_ups": {"business_task_id": "task"},
    "business_task_todo_sync_outbox": {"business_task_id": "task"},
    "business_task_relations": {"from_task_id": "task", "to_task_id": "task"},
    "business_work_cluster_tasks": {"task_id": "task"},
    "business_task_anchor_links": {"task_id": "task", "anchor_id": "anchor"},
    "business_project_candidates": {"confirmed_project_id": "project"},
    "business_attention_items": {"anchor_id": "anchor"},
    "business_attention_tasks": {"task_id": "task"},
    "business_attention_proposal_tasks": {"task_id": "task"},
    "business_legacy_links": {
        "task_id": "task", "anchor_id": "anchor", "project_id": "project",
    },
}
PUBLIC_ALIAS = {
    "task": "task_public_id", "project": "project_public_id",
    "anchor": "anchor_public_id",
}
COMPOSITE_ROWID_CHILDREN = (
    "business_task_evidence", "business_project_evidence",
    "business_task_relations", "business_work_cluster_tasks",
    "business_attention_tasks", "business_attention_proposal_tasks",
)
AUTOINCREMENT_CHILDREN = tuple(
    name for name in RETAINED_CHILD_FKS if name not in COMPOSITE_ROWID_CHILDREN
)
PARENT_HASHES = {
    "actual": (
        "91b5763374243a76dc446881190d1f8e68ea9526a82ba98c920e0fe935957d07",
        "6d73ac34c4cc559f164afbe344d76e913d3709ebfecb76646b2307b37e7f85b4",
        "d57eb678de2b9b06728e870ca3340bfa9297f301d6a11cffc3f52a8987a7a36f",
        "2b7413b2ceabd2ef752ad218d860615a9e3d0d36d04966ff0ca3226286c98ab8",
    ),
    "fresh": (
        "19341a7f00ed976b0fee462b1fcfe1bcadc061ebc555d6757d9e89c1151b81c0",
        "4c75ae1efd72dc5b27772a9a69454a2f1bcca619b35376fa087ec6206c70cf98",
        "d57eb678de2b9b06728e870ca3340bfa9297f301d6a11cffc3f52a8987a7a36f",
        "2b7413b2ceabd2ef752ad218d860615a9e3d0d36d04966ff0ca3226286c98ab8",
    ),
}
SOURCE_CHILD_HASHES = {
    "actual": "d59d3ae841b3e410305079267d4c2a12b26103fddc06929cf316ec78ab8e28c1",
    "fresh": "d4bab7b6cd1e59bbcd2c5154d4b43bfd06196ed5ba15cae5f8149d63aef02719",
}
SOURCE_CHILD_FULL_HASHES = {
    "actual": "0b30b896fce6bb46d0cc1a8e5b20041059965e52da6339c91509763fd37a77f4",
    "fresh": "0ab4033f06c805811bf6193fd48e32a66f61a7e1885e238c2bdc9f08c6e6b78d",
}
TARGET_CHILD_HASHES = {
    "actual": "789a625b97df6c223520542904c3bf3a56b8da127800e908d470e1930ad39587",
    "fresh": "bda7bbe8141657cf0e8b9937b1aa9c3eb44d6f9b48c15996720ceff3e69cd8b1",
}


def translated_child_sql(table: str, sql: str, *, inverse: bool = False) -> str:
    for column, kind in RETAINED_CHILD_FKS[table].items():
        source = f"foreign key({column}) references {SOURCE_PARENTS[kind]}(id)"
        target = f"foreign key({column}) references business({PUBLIC_ALIAS[kind]})"
        old, new = (target, source) if inverse else (source, target)
        if sql.count(old) != 1:
            raise ValueError(f"unsupported {table}.{column} FK declaration")
        sql = sql.replace(old, new, 1)
    return sql


def _shape(declarations: dict) -> dict:
    return {
        name: {key: item[key] for key in ("sql", "columns", "foreign_keys")}
        for name, item in declarations.items()
    }


def _shape_hash(declarations: dict) -> str:
    return hashlib.sha256(json.dumps(
        _shape(declarations), sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _objects(db: sqlite3.Connection, names) -> dict:
    return {name: _source_declaration(db, name) for name in names}


def _source_variant(parents: dict, children: dict) -> str:
    parent_hashes = tuple(_fingerprint(parents[name]) for name in SOURCE_PARENTS.values())
    child_hash = _shape_hash(children)
    for variant in PARENT_HASHES:
        if (parent_hashes == PARENT_HASHES[variant]
                and child_hash == SOURCE_CHILD_HASHES[variant]
                and _fingerprint(children) == SOURCE_CHILD_FULL_HASHES[variant]):
            return variant
    raise ValueError("unsupported Business source declarations")


def _snapshot(db: sqlite3.Connection, name: str, *, hidden_rowid: bool = False) -> dict:
    columns = tuple(row[1] for row in db.execute(f"pragma main.table_xinfo({_quote(name)})")
                    if row[6] == 0)
    selected = (("rowid",) if hidden_rowid else ()) + columns
    selected_sql = ",".join(_quote(column) for column in selected)
    values = tuple(tuple(row) for row in db.execute(
        f"select {selected_sql} from main.{_quote(name)} order by rowid"
    ))
    types_sql = ",".join(f"typeof({_quote(column)})" for column in selected)
    types = tuple(tuple(row) for row in db.execute(
        f"select {types_sql} from main.{_quote(name)} order by rowid"
    ))
    return {"columns": columns, "rows": values, "types": types,
            "hidden_rowid": hidden_rowid}


def _restore_rows(db: sqlite3.Connection, name: str, snap: dict) -> None:
    columns = (("rowid",) if snap["hidden_rowid"] else ()) + snap["columns"]
    sql = (f"insert into main.{_quote(name)}("
           + ",".join(_quote(column) for column in columns) + ") values("
           + ",".join("?" for _ in columns) + ")")
    db.executemany(sql, snap["rows"])
    if _snapshot(db, name, hidden_rowid=snap["hidden_rowid"]) != snap:
        raise ValueError(f"{name} copied values or storage classes differ")


def _create_auxiliary(db: sqlite3.Connection, definition: dict) -> None:
    for item in reversed(definition["indexes"]):
        if item["sql"] is not None:
            db.execute(item["sql"])
    for item in definition["triggers"]:
        db.execute(item["sql"])


def _child_waters(db: sqlite3.Connection) -> dict:
    return {name: _sequence(db, name) for name in AUTOINCREMENT_CHILDREN}


def _parent_waters(db: sqlite3.Connection, *, target: bool) -> dict:
    return {kind: _sequence(db, "business:" + kind if target else table)
            for kind, table in SOURCE_PARENTS.items()}


def _source_fk_findings(db: sqlite3.Connection) -> tuple:
    findings = _foreign_keys(db)
    moved = set(SOURCE_PARENTS.values())
    if any(table in moved or parent in moved for table, _rowid, parent, _id in findings):
        raise ValueError("broken moved Business parent source")
    return findings


def capture_business_source(db: sqlite3.Connection) -> dict:
    parents = _objects(db, SOURCE_PARENTS.values())
    children = _objects(db, RETAINED_CHILD_FKS)
    variant = _source_variant(parents, children)
    if db.execute("select 1 from main.sqlite_schema where name='business'").fetchone():
        raise ValueError("mixed Business source")
    if db.execute("select 1 from main.sqlite_sequence where name like 'business:%' limit 1").fetchone():
        raise ValueError("mixed Business allocator source")
    from app.action_email_storage import _external_dependencies
    _external_dependencies(db, targets=(*SOURCE_PARENTS.values(), *RETAINED_CHILD_FKS))
    for sql in (BUSINESS_TABLE_DDL, *BUSINESS_INDEX_DDL, *BUSINESS_TRIGGER_DDL):
        words = sql.split()
        name = words[2 if words[1] in ("table", "index", "trigger") else 3]
        if db.execute(
            "select 1 from main.sqlite_schema where name=? collate nocase", (name,)
        ).fetchone():
            raise ValueError("Business target name collision")
    return {"variant": variant, "parents": parents, "children": children,
            "fk_findings": _source_fk_findings(db)}


def business_inverse_metadata(source: dict) -> dict:
    return {key: source[key] for key in ("variant", "parents", "children")}


def _assert_transaction(db: sqlite3.Connection) -> None:
    if not db.in_transaction or db.execute("pragma foreign_keys").fetchone()[0] != 0:
        raise sqlite3.ProgrammingError("Business migration needs caller FK-off transaction")


def _parent_facts(db: sqlite3.Connection) -> dict:
    return {kind: _snapshot(db, table) for kind, table in SOURCE_PARENTS.items()}


def _child_facts(db: sqlite3.Connection) -> dict:
    return {name: _snapshot(db, name, hidden_rowid=name in COMPOSITE_ROWID_CHILDREN)
            for name in RETAINED_CHILD_FKS}


def _parent_dicts(facts: dict) -> dict:
    return {kind: [dict(zip(snap["columns"], row, strict=True)) for row in snap["rows"]]
            for kind, snap in facts.items()}


def _physical_ids(parents: dict) -> dict:
    pairs = ((kind, row["id"]) for kind in SOURCE_PARENTS for row in parents[kind])
    physical = {pair: position for position, pair in enumerate(pairs, start=1)}
    if len(physical) != sum(len(rows) for rows in parents.values()):
        raise ValueError("duplicate Business public ID within kind")
    for kind, ref in BUSINESS_REFERENCES.items():
        field, _column, parent_kind = ref
        for row in parents[kind]:
            if row[field] is not None and (parent_kind, row[field]) not in physical:
                raise ValueError("broken moved Business parent source")
    return physical


def _drop_source(db: sqlite3.Connection) -> None:
    for name in RETAINED_CHILD_FKS:
        db.execute(f"drop table main.{_quote(name)}")
    for name in SOURCE_PARENTS.values():
        db.execute(f"drop table main.{_quote(name)}")


def _create_children(db: sqlite3.Connection, declarations: dict, *, target: bool) -> None:
    for name, definition in declarations.items():
        sql = translated_child_sql(name, definition["sql"]) if target else definition["sql"]
        db.execute(sql)
    for definition in declarations.values():
        _create_auxiliary(db, definition)


def _insert_business_parents(db: sqlite3.Connection, parents: dict, physical: dict) -> None:
    columns = ("id", "record_type", "legacy_key", "payload_json", "title",
               "status", "created_at", "updated_at", "merged_task_ref",
               "canonical_anchor_ref", "context_project_ref")
    sql = ("insert into main.business(" + ",".join(columns) + ") values("
           + ",".join("?" for _ in columns) + ")")
    for kind in SOURCE_PARENTS:
        for row in parents[kind]:
            refs = {"merged_task_ref": None, "canonical_anchor_ref": None,
                    "context_project_ref": None}
            if kind in BUSINESS_REFERENCES:
                field, column, parent_kind = BUSINESS_REFERENCES[kind]
                if row[field] is not None:
                    refs[column] = physical[(parent_kind, row[field])]
            payload = json.dumps(
                {field: _encoded_sqlite_value(row[field])
                 for field in business_payload_fields(kind)},
                ensure_ascii=False, allow_nan=False, separators=(",", ":"),
            )
            common = {field: row.get(field, "") for field in BUSINESS_COMMON}
            db.execute(sql, (
                physical[(kind, row["id"])], kind, str(row["id"]), payload,
                common["title"], common["status"], common["created_at"],
                common["updated_at"], refs["merged_task_ref"],
                refs["canonical_anchor_ref"], refs["context_project_ref"],
            ))


def _projected_parent_facts(db: sqlite3.Connection, kind: str, variant: str) -> dict:
    fields = (BUSINESS_FRESH_FIELDS if variant == "fresh" else BUSINESS_FIELDS)[kind]
    relation = (BUSINESS_FRESH_PROJECTIONS if variant == "fresh"
                else BUSINESS_PROJECTIONS)[kind]
    selected = ",".join(_quote(field) for field in fields)
    types = ",".join(f"typeof({_quote(field)})" for field in fields)
    rows = tuple(tuple(row) for row in db.execute(
        f"select {selected} from ({relation}) order by id"
    ))
    classes = tuple(tuple(row) for row in db.execute(
        f"select {types} from ({relation}) order by id"
    ))
    return {"columns": fields, "rows": rows, "types": classes,
            "hidden_rowid": False}


def _verify_target_rows(db: sqlite3.Connection, source: dict,
                        parent_facts: dict, child_facts: dict,
                        source_parent_waters: dict, source_child_waters: dict) -> None:
    for kind, snap in parent_facts.items():
        if _projected_parent_facts(db, kind, source["variant"]) != snap:
            raise ValueError(f"{kind} Business projection differs from source")
    for name, snap in child_facts.items():
        if _snapshot(db, name, hidden_rowid=snap["hidden_rowid"]) != snap:
            raise ValueError(f"{name} retained child differs from source")
    if (_parent_waters(db, target=True) != source_parent_waters or
            _child_waters(db) != source_child_waters):
        raise ValueError("Business public allocator waters differ")
    if _foreign_keys(db) != source["fk_findings"]:
        raise ValueError("Business source FK findings changed")
    if db.execute("pragma quick_check").fetchone()[0] != "ok":
        raise ValueError("Business copy quick_check failed")


def migrate_business_schema(db: sqlite3.Connection) -> dict:
    _assert_transaction(db)
    source = capture_business_source(db)
    source_parent_waters = _parent_waters(db, target=False)
    source_child_waters = _child_waters(db)
    parent_facts, child_facts = _parent_facts(db), _child_facts(db)
    parents = _parent_dicts(parent_facts)
    physical = _physical_ids(parents)
    _drop_source(db)
    create_business_schema(db)
    _insert_business_parents(db, parents, physical)
    _create_children(db, source["children"], target=True)
    for name, snap in child_facts.items():
        _restore_rows(db, name, snap)
    for kind, water in source_parent_waters.items():
        _set_sequence(db, "business:" + kind, water)
    for name, water in source_child_waters.items():
        _set_sequence(db, name, water)
    _verify_target_rows(db, source, parent_facts, child_facts,
                        source_parent_waters, source_child_waters)
    return business_inverse_metadata(source)


def _target_children_are_current(db: sqlite3.Connection, variant: str) -> bool:
    return _fingerprint(_objects(db, RETAINED_CHILD_FKS)) == TARGET_CHILD_HASHES[variant]


def _canonical_create(sql: str) -> str:
    for prefix in ("create unique index", "create index", "create table", "create trigger"):
        if sql.startswith(prefix):
            return prefix.upper() + sql[len(prefix):]
    raise ValueError("unsupported Business DDL")


def business_schema_is_current(db: sqlite3.Connection, variant: str | None = None) -> bool:
    if db.execute(
        "select 1 from main.sqlite_schema where type='table' and name in "
        "('business_tasks','business_projects','business_anchors',"
        "'business_project_context_revisions') limit 1"
    ).fetchone():
        return False
    table = db.execute(
        "select sql from main.sqlite_schema where type='table' and name='business'"
    ).fetchone()
    if table is None or table[0] != _canonical_create(BUSINESS_TABLE_DDL):
        return False
    expected = {_canonical_create(sql) for sql in
                (*BUSINESS_INDEX_DDL, *BUSINESS_TRIGGER_DDL)}
    actual = {row[0] for row in db.execute(
        "select sql from main.sqlite_schema where tbl_name='business' "
        "and type in ('index','trigger') and sql is not null"
    )}
    if actual != expected:
        return False
    versions = (variant,) if variant is not None else tuple(TARGET_CHILD_HASHES)
    return any(_target_children_are_current(db, current) for current in versions)


def _target_parent_facts(db: sqlite3.Connection, variant: str) -> dict:
    return {kind: _projected_parent_facts(db, kind, variant)
            for kind in SOURCE_PARENTS}


def _recreate_source(db: sqlite3.Connection, source: dict) -> None:
    for definition in source["parents"].values():
        db.execute(definition["sql"])
    for definition in source["parents"].values():
        _create_auxiliary(db, definition)
    _create_children(db, source["children"], target=False)


def restore_business_schema(db: sqlite3.Connection, source: dict) -> None:
    _assert_transaction(db)
    variant = source["variant"]
    if not business_schema_is_current(db, variant):
        raise ValueError("unsupported current Business declarations")
    before_fk = _foreign_keys(db)
    parents = _target_parent_facts(db, variant)
    children = _child_facts(db)
    parent_waters = _parent_waters(db, target=True)
    child_waters = _child_waters(db)
    for name in RETAINED_CHILD_FKS:
        db.execute(f"drop table main.{_quote(name)}")
    db.execute("drop table main.business")
    _recreate_source(db, source)
    for kind, snap in parents.items():
        _restore_rows(db, SOURCE_PARENTS[kind], snap)
    for name, snap in children.items():
        _restore_rows(db, name, snap)
    for kind, water in parent_waters.items():
        _set_sequence(db, SOURCE_PARENTS[kind], water)
    for name, water in child_waters.items():
        _set_sequence(db, name, water)
    if any(_source_declaration(db, name) != definition
           for name, definition in source["parents"].items()) or any(
               _source_declaration(db, name) != definition
               for name, definition in source["children"].items()
           ):
        raise ValueError("Business original declarations differ after inverse")
    if _foreign_keys(db) != before_fk or db.execute("pragma quick_check").fetchone()[0] != "ok":
        raise ValueError("Business inverse FK or quick_check differs")
```

## Self-review and acceptance map

| Accepted requirement | Task |
| --- | --- |
| Exact 42 existing fields, signed public IDs, four waters, native codec/defaults/checks | Existing `c9c2c74f` storage plus Tasks 1, 4 |
| Three typed VIRTUAL aliases/full UNIQUE indexes and 19/40 child FK boundary | Task 1 |
| 15 child shapes, 135 fields, 29 indexes, three date triggers, nine child waters/six hidden ROWIDs | Tasks 1, 4 |
| Complete physical map before internal refs, higher-ID merge/cycles, moved-parent broken-state refusal | Task 1 |
| Every current SQL owner, bootstrap, manifest, fresh/reopen, diagnostic/eval | Tasks 2, 3 |
| Same unpublished Store20/Email52 pair, Email+Business source capture, atomic forward/inverse and timestamps | Task 3 |
| Current-fact inverse, old-code cold read/write, reforward and source/copy equality | Tasks 1, 3, 4 |
| Real copy and Codex approval/revision with in-memory provider only; release/readback gates | Task 4 |

No step activates the disabled Followup9 extension, retires original body/source/event tables, adds compatibility views/dual writers, or changes audit, authorization, recovery or outbound policy. If the source no longer matches the captured immediate predecessor, pause **this publication** and rebind exact metadata/caller changes before proceeding; do not route an unsupported source through a generic fallback. The plan itself is docs-only and provides no claim that Email52 or Business is published.
