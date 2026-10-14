# Email Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task. Steps use checkboxes for tracking. This document authorizes no additional agents, runtime release, provider operation or production migration.

**Goal:** Move thirteen existing Email action families and all 144 source fields into finite `actions` records, preserving business results, legal source values, public identities, source row locators, native constraints and current behavior.

**Architecture:** Extend the current sixteen-type actions schema by thirteen explicit Email types. Nine nullable TEXT-primary-key types retain source SQLite ROWID in `legacy_key` and their original public key exactly once in the finite payload; attempts/steps retain their public AUTOINCREMENT namespaces; effects/retry retain original composite identities. Native typed composite foreign keys reference generated original public keys, following the existing postfix pattern, while the classification FK still references the existing Email classification table.

**Tech Stack:** Python, SQLite, finite action projections and writes, paired Store/Email schema publication, focused pytest, Console History/Attention, offline production-copy and native CLI acceptance with provider substitutes.

---

## Status, source and authorization boundary

This is a docs-only implementation plan, not target code or migration acceptance. Its preparation checkout is `6dfb439a` on `codex/database-redesign-email-actions-20261014`. Phase5E PR82 CI/publication is still pending when this plan is written: candidate Store19 does not establish production Store19; production remains Store18/Email51. The full objective remains ten core tables plus the migration ledger, rather than completion at 29 action types. No retention policy, source-body retirement, SMTP activation, audit/authorization addition or new periodic work belongs to this unit.

The approved structure is `docs/superpowers/specs/2026-10-11-database-redesign-design.md`. Source contracts are the two frozen 2026-10-13 Email action JSON/map pairs: nine tables/103 fields and four children/41 fields. Their measured actual source was Store16/Email51 at `d7397203`; those are historical observations, never the publication predecessor. The accompanying `2026-10-14-database-redesign-email-actions-field-map.csv` independently names all 144 actual source columns and their exact target path. No field is retired in this plan.

Implementation starts only after E is normally published and root captures the actual immediate predecessor commit, Store/Email markers, literal actions/Email declarations, generated columns, FKs, indexes, triggers and native sequence metadata. Record those verified values in a new `docs/superpowers/specs/2026-10-14-database-redesign-email-actions-published-source.json` and source map. Refuse a changed declaration outside the enumerated source variants before mutation. Do not assign a target Store/Email version until that binding exists. A numeric release name is deliberately absent here; assigning one now would falsely reserve an unpublished predecessor.

## File responsibilities and ownership

All repository paths below are relative to this plan's worktree root, `/Users/derek/.codex/worktrees/database-redesign-email-actions/ceo-agent-service`.

| File | Responsibility during implementation |
|---|---|
| `app/action_storage.py` | Opt-in thirteen Email types, finite source-value codec reuse, explicit generated fields, native keys/FKs/checks/indexes, source-rowid/AUTOINCREMENT handling, Email projections and confined insert/update/delete helpers; older manifests remain fixed |
| New `app/action_email_storage.py` | Published-source capture, thirteen-family forward/current-fact inverse, original row locator and sequence restoration, external dependency translation; no caller or provider execution |
| New `app/action_email_schema_release.py`; `app/store_schema_release.py`; `app/email_schema_release.py`; `app/deploy_maintenance.py`; exact existing publication selectors | Paired Store/Email source/target/inverse selection and atomic markers; bind actual E successor rather than guessed version numbers |
| New `app/email_classification_schema.py` | Neutral single canonical current classification-parent DDL factory, with no Store/Email imports or constructor calls; used by both fresh constructors |
| `app/store.py` | Fresh/current actions construction, History source detection/query and source ID preservation; no migration of classifications/messages/accounts into another core table in this unit |
| `app/email_store.py` | Fresh schema and old-schema initialization dispatch; finite thirteen-family reads/writes; durable validation and exact external trigger checks; retain public method signatures/returns |
| `app/email_worker.py`, `app/email_action_reconcile.py`, `app/email_unsubscribe_direct.py`, `app/email_unsubscribe.py`, `app/email_unsubscribe_audit.py`, `app/email_reply_delivery.py`, `app/email_task_adapter.py`, `app/email_task_producer.py`, `app/email_pipeline.py`, `app/email_context_source.py` | Only direct SQL/physical-presence assumptions and existing helper consumers; do not activate dormant delivery or change the decision/retry/recovery lifecycle |
| `app/audit_web.py`, `app/web_api/email.py`, `app/web_api/attempts.py`, `app/cli.py`, `app/deploy_maintenance.py`, `app/daily_report_facts.py`, `app/history_types.py` | Existing observability, History/Attention, feedback, recovery/retry CLI and release readiness consumers |
| New `tests/test_email_action_storage.py`, `tests/test_email_action_migration.py`, `tests/test_email_action_schema_release.py`, `tests/test_email_action_callers.py`, `tests/test_email_action_copy_proof.py` | Source-versus-target values/types/constraints, forward/inverse/current facts, publication failure stages, callers and copy harness |
| Existing `tests/test_email_action_source.py`; new `tests/test_email_action_children_source.py` | Independent native source-only oracles. Another agent owns current source-oracle work; do not replace source expectations with target behavior |
| New `scripts/validate_action_email_copy.py`, new `evals/database_redesign/email_actions_copy.v1.json` and `email_actions_native.v1.json` | Reproducible offline latest-copy/old-code/current-fact evidence and actual native Consumer/Audit with in-memory provider substitutes; no raw private rows in reports |
| `docs/architecture.md`, `docs/runtime-mechanism.md`, new `docs/database-redesign-email-actions-validation.md`, redesign program status | Same-commit behavior documentation and precise evidence boundaries |

The plan/map author owns only this plan, the CSV and their claim row. No current runtime/test/validation file is edited by the planning task.

## Concrete finite representation

### Record identities and source row locators

The following nine record types use `legacy_key = canonical signed decimal source ROWID`: `email_action_plans`, `email_actions`, `email_unsubscribe_cancellations`, `email_unsubscribe_claims`, `email_unsubscribe_continuations`, `email_unsubscribe_receipts`, `email_feedback_requests`, `email_reply_dispatch_claims`, `email_reply_receipts`. Zero and negative source ROWIDs are legal; do not add positivity checks. Their original named public PK is retained once at `payload_json.$.<original_column>`, including explicit JSON null. An original missing TEXT PK insert means SQL NULL, not a generated public business identity. `actions.id` remains the separate globally allocated physical integer.

`email_action_attempts.id` and `email_unsubscribe_steps.id` use canonical decimal `legacy_key` and their own existing AUTOINCREMENT watermarks. The effects `legacy_key` is a canonical JSON array of original `(action_identity,effect_digest)`; retry uses `(action_id,attempt_number)`. Their PK components occur only in that array, not again in payload. Components use the confined SQLite scalar codec when required. Do not reuse postfix's strict two-TEXT-component key CHECK: source TEXT affinity permits BLOB PK components; array shape, component decoding, affinity and original component constraints are explicit for each of these two types. No extra source/public ID, status flag or identity table is introduced.

Original source rowid is not a fifteenth business field: it is SQLite's existing physical locator carried by the approved structural legacy key. Source composite tables also have implicit rowid, but no current business/UI reader depends on it; only the historical effects rebuild orders by effects.rowid at `email_store.py:4991`. Composite business identities remain their exact two original components. The inverse acceptance compares these composite identities and facts; it does not claim preservation of their otherwise unobserved implicit rowids or allocator sequence. Before accepting inverse, the genuine predecessor constructor must prove the historical effects rebuild is not rerun for its current schema; if it is rerun, verify its rowid ordering leaves the same declared effect facts and lineage, otherwise stop before publication rather than claim parity.

Source forward mapping keys are `(record_type, source_rowid)` for the nine nullable types, `(record_type, source_integer_id)` for the two integer types, and `(record_type, exact_original_composite_components)` for the other two. Never use a dict keyed only by a nullable public PK: two NULL rows must not collapse. Preserve existing sixteen-type physical IDs and values through the actions rebuild.

### Original values, affinities and generated projection

For each CSV row, create the explicitly named `email_<original_column>` generated column using the original SQL affinity. The sole exception is numeric source `id`, whose source projection is `CAST(legacy_key AS INTEGER)`. Shared generated names may be shared only when all contributing source declarations have the same affinity; finite CASE scopes must return NULL outside their applicable Email record types. Prefixing avoids existing action column collisions. All source fields remain exactly once in payload/key storage. Inner JSON strings such as actions, parameters, account snapshots, operations, decisions and provider evidence stay original strings, not parsed/re-serialized business objects.

Reuse the existing `_encoded_sqlite_value`, `_reply_value_check` and generated decoding expressions as confined scalar primitives for these thirteen types. Rename a primitive only if existing manifests remain byte-identical; a small Email wrapper is sufficient. TEXT affinity legally preserves BLOBs; INTEGER affinity may legally retain nonintegral REAL or nonnumeric TEXT/BLOB when original CHECK/FK permits it. Native source affinity and checks are the authority, not application Pydantic validation. The source oracle and target must agree on value and `typeof`, including embedded NUL, BLOB, SQL NULL and finite/nonfinite numeric outcomes. A strict JSON `text`/`integer` check from `_payload_check` cannot be copied as the Email legal-state contract.

The decoding expression is finite, with the current E representation:

```sql
CASE WHEN json_type(payload_json,'$.action_identity')='object'
 THEN CASE json_extract(payload_json,'$.action_identity.t')
  WHEN 'blob' THEN unhex(json_extract(payload_json,'$.action_identity.v'))
  WHEN 'inf' THEN 9e999 WHEN '-inf' THEN -9e999 END
 ELSE json_extract(payload_json,'$.action_identity') END
```

Declare the generated source column TEXT so native affinity applies. Every named payload key must be present, including explicit NULL where the old column is nullable. Reject extra keys with the finite per-type key-set CHECK, not a generic arbitrary payload. Apply each literal frozen source CHECK/default/NOT NULL to decoded source-affinity values, with SQLite's original three-valued CHECK behavior; do not wrap nullable CHECK results in `coalesce(...,0)`. Required keys and allowed scalar codec shapes are distinct from source column NULL constraints.

### Typed native public-key foreign keys

Choose direct native composite public-key FKs, rather than converting these original opaque relationships to physical integer IDs plus new key-rename triggers. Existing postfix already demonstrates this pattern in `action_storage.py:338`. Keep `email_public_key TEXT GENERATED ALWAYS AS (...)` selecting the original PK generated column for exactly the nine nullable types, NULL elsewhere. Create an unconditional unique index `(record_type,email_public_key)`; NULLs remain distinct, and this exact nonpartial index can serve as an FK parent.

Six explicit child reference families are sufficient:

| Generated source reference | Generated parent record_type | Original callers |
|---|---|---|
| `email_action_plan_id` | `email_action_plans` | operations, unsubscribe claims/receipts, reply claims/receipts |
| `email_expected_current_action_plan_id` | `email_action_plans` | feedback; nullable means initial confirmation |
| `email_resulting_action_plan_id` | `email_action_plans` | feedback result |
| `email_action_id` | `email_actions` | attempts and direct retry |
| `email_action_identity` | `email_unsubscribe_claims` | effects, continuations, steps |
| `email_classification_id` | existing classification INTEGER endpoint | plans, operations, unsubscribe claims/receipts, feedback, reply claims/receipts |

The corresponding generated discriminator names are `email_plan_parent_type`, `email_prior_plan_parent_type`, `email_result_plan_parent_type`, `email_operation_parent_type`, `email_claim_parent_type`. Their CASE expressions are exact finite child-kind membership, not a user-controlled relationship string. The last row remains a simple direct FK to `email_classifications(id)`; it is not an actions.id or future emails.id mapping.

For example, the plan relation is exactly:

```sql
FOREIGN KEY(email_plan_parent_type,email_action_plan_id)
 REFERENCES actions(record_type,email_public_key) ON UPDATE NO ACTION ON DELETE RESTRICT
```

All source FKs are immediate, NOT DEFERRABLE. Compare native source and target extended error codes, not an assumed common subclass: NO ACTION key rename and RESTRICT deletion can report different native constraint codes even with the same human-readable message. Preserve that timing, original source affinity and BINARY collation. Child NULL association skips native FK checking exactly as source; a non-NULL child can never select a NULL parent public key. Keep the child's original scalar once, rather than reconstructing it from a possibly changed physical parent. This also preserves INSERT OR IGNORE/CHECK/UNIQUE-before-FK ordering without Python parent lookup errors. No claim-to-receipt FK, account/message FK, effect/run/thread FK or new composite effect constraint is invented.

### Unique and query index closure

Keep `(record_type,CAST(legacy_key AS INTEGER))` unique for the nine rowid types and two integer types, excluding the two composite types from numeric public-order scope. Older integer/postfix/observer scopes remain byte-identical when Email is not enabled. For the Email-enabled schema, exclude all Email kinds from existing older-type source-PK projections and indexes wherever necessary, rather than letting composite JSON cast to zero and collide.

Translate all 13 frozen table index sets explicitly. Required source uniqueness includes plan `(classification_id,action_plan_version)`, operation `(action_plan_id,action_type)`, attempt `(action_id,attempt_number)`, step `(action_identity,sequence)`, effects `(action_identity,effect_digest)`, retry `(action_id,attempt_number)`, feedback expected/result plan individually, and each reply family's outgoing Message-ID. Preserve all nine nullable original public PK uniqueness rules with `email_public_key`. Scoped source indexes retain classification/plan lookups and `(status,updated_at,action_identity/action_id)` claim/order access. Query predicates explicitly name record_type and the same partial-index scope so SQLite can use it; compare EXPLAIN and bounded VM progress with unrelated action volume as in E.

History requires a separate locator projection: `CAST(legacy_key AS INTEGER) AS rowid` for direct actions; the original opaque `action_id` remains the `email_action_id` projection. Preserve `id='email-action:'||source_rowid`, `source_table='email_actions'` and `source_id=source_rowid` in `store.py:30725`. Attention's compact failed-row membership currently uses rowid (`audit_web.py:3651`) and must use that source locator, not opaque action_id or physical actions.id. Do not claim target API equivalence until the caller tests and copy readback verify it.

### Minimal helper changes

Add an explicit opt-in `include_email` branch in `_action_definitions`/schema factories after the E branch. Export finite `EMAIL_ACTION_PROJECTIONS`, with each projection listing original columns in captured actual/fresh order; optional `email_action_projection(kind, include_rowid=True)` includes the source locator only for the nine supported rowid kinds. Preserve the unsubscribe-claim actual/fresh physical column-order variants explicitly.

Add confined Email helpers next to the E helpers, with these exact interfaces:

```python
_EMAIL_ROWID_UNSET = object()

insert_email_action(db, kind, values, *, source_rowid=None, ignore=False)
update_email_action(db, kind, source_rowid, values, *, new_source_rowid=_EMAIL_ROWID_UNSET)
delete_email_action(db, kind, source_rowid)
```

`source_rowid`/`new_source_rowid` are structural arguments for the nine rowid kinds. The private Python sentinel distinguishes omitted rowid assignment from an explicit SQL NULL update; it is not a stored flag or business field. Integer/composite kinds continue the appropriate existing identity-shaped helper paths, extended only for their two finite new kind sets. Public DAO methods keep their existing input identities; they select the projected source locator before a rowid helper mutation, within the same existing transaction. Public-key assignments update the original named payload field; rowid assignments update only legacy_key. Multi-row source writes select all matching locators under the caller's writer transaction, preserving statement atomicity through a savepoint when multiple target writes are required. Native FK constraints remain the final authority; do not pre-resolve/reject absent parents in Python.

Reuse the ordinary source-rowid allocator logic at `action_storage.py:928-935`: empty namespace starts at 1; otherwise MAX+1, including negative/zero maxima; deleting the largest permits reuse; at signed max, use native in-memory INTEGER PRIMARY KEY allocator populated with only that record type's existing rowids. A failed/ignored ordinary insert advances no logical namespace watermark. No sequence row is created for these nine. Explicit rowid assignment and update use `_native_integer_primary_key`, including update=True semantics; new NULL rowid UPDATE must raise the native datatype mismatch rather than auto-allocate. Include all ordinary auto-rowid allocations in the existing writer-lock acquisition predicate before reading MAX. Apply that lock to autocommit and deferred connections as E does; no new lease or policy layer.

Attempts/steps extend existing native AUTOINCREMENT sequence strategy with two scoped sequence names, including explicit IDs, ignored high IDs, delete highwater, max exhaustion and transaction behavior. Effects/retry extend finite composite identity handling; retain retry's original immutable insert/update/delete trigger behavior. `ActionWriteResult.lastrowid` for successful nine-kind insertion represents original source rowid; native SQLite's ignored-insert `last_insert_rowid()` versus the helper's existing `None` return is not assumed equal. Verify actual callers do not consume a different return before changing their write path; API methods currently do not use these nine cursors' lastrowid.

## Task 1: Bind the actual predecessor and complete the source-only oracle

**Files:** published-source JSON/map; source-only tests owned by the source-oracle agent; `docs/database-redesign-email-actions-source-validation.md`.

- [ ] After E deployment, read actual production commit/markers and schema/aggregate metadata with read-only URI and one transaction. Compare all thirteen declarations, own/external objects, old sixteen actions manifest and both claim physical-order variants to frozen source. No business row body or credentials enter artifacts.
- [ ] Independently validate source-only cases for all nine NULL PKs, source rowid zero/negative/deleted-high/ignored insert/max allocation, public key/ROWID UPDATE, BLOB keys, native FK immediate timing, duplicate and OR REPLACE behavior, 13 external blockers, 2 count triggers and 3 retry immutable triggers. Include a NULL prior feedback plan independently of its non-NULL feedback PK.
- [ ] Run `pytest -q tests/test_email_action_source.py tests/test_email_action_children_source.py`; expected all native source cases pass, with only documented opposite-variant skips. These are source evidence, never target evidence. Commit source evidence independently of target code.

## Task 2: Implement finite storage and native constraint parity

**Files:** `app/action_storage.py`; new `tests/test_email_action_storage.py`; runtime storage documentation only when implemented.

- [ ] Write failing target tests using both frozen variants. Build native source tables directly from captured SQL, with minimal classification/account/message parent stubs for external trigger tests. The target fixture creates the same parent rows and an opt-in Email actions schema. Compare every field in CSV order, value and `typeof`, not selected business samples.
- [ ] Use these complete source examples as mandatory comparisons:

```python
def insert_plan(db, public_key, version):
    db.execute("""INSERT INTO email_action_plans
        (action_plan_id,action_plan_version,classification_id,account_id,category,
         classification_source,confidence,model_id,config_version,actions_json,
         action_parameters_json,created_at)
        VALUES(?,?,11,'account','category','model',0.5,'model','config','[]','{}','time')""",
        (public_key, version))

def test_source_two_null_public_plan_ids(source_db):
    insert_plan(source_db, None, 1)
    insert_plan(source_db, None, 2)
    assert source_db.execute("SELECT count(*) FROM email_action_plans WHERE action_plan_id IS NULL").fetchone()[0] == 2
    assert source_db.execute("SELECT count(*) FROM email_action_plans WHERE action_plan_id=?", (None,)).fetchone()[0] == 0

def test_source_rowid_and_public_key_are_independent(source_db):
    insert_plan(source_db, 'plan', 1)
    source_db.execute("UPDATE email_action_plans SET rowid=-4 WHERE action_plan_id='plan'")
    source_db.execute("UPDATE email_action_plans SET action_plan_id=NULL WHERE rowid=-4")
    assert source_db.execute("SELECT rowid,action_plan_id FROM email_action_plans").fetchone() == (-4, None)
    insert_plan(source_db, 'second', 2)
    assert source_db.execute("SELECT rowid FROM email_action_plans WHERE action_plan_id='second'").fetchone()[0] == -3
```

Target counterparts call the interfaces defined above with the same values and assert projected rowid/public key pairs. Run RED `pytest -q tests/test_email_action_storage.py`; expected missing Email opt-in/helper failure, not changed source oracle output.

- [ ] Implement the finite schema/projections/helpers exactly described above. Encode and decode all 144 source columns through the CSV; preserve original NOT NULL/default/CHECK/UNIQUE behavior. For composite fields test BLOB/text distinctions and source affinity before indexing. Keep six/nine/twelve/fourteen/sixteen schema SQL/manifests byte-identical when Email opt-in is false.
- [ ] Exercise public key NULL→value/value→NULL, duplicate non-NULL keys, NULL equality/IS NULL, signed explicit rowid and rowid UPDATE, referenced public-key UPDATE/DELETE failures, unreferenced rename success, OR IGNORE invalid CHECK versus missing parent, OR REPLACE referenced parent restriction, native immediate transaction boundaries and unrelated records with colliding numeric IDs.
- [ ] Run `pytest -q tests/test_email_action_storage.py tests/test_action_storage.py tests/test_action_receipt_storage.py tests/test_action_postfix_storage.py tests/test_reply_attempt_action_storage.py`; expected all selected cases pass and prior manifests match unchanged. Commit storage and its focused tests/docs only.

## Task 3: Forward/current-fact inverse and exact dependency closure

**Files:** new `app/action_email_storage.py`; new `tests/test_email_action_migration.py`.

- [ ] Write RED tests for exact source capture, declaration mismatch, extra inbound table/view/trigger, variant mismatch, zero-row sources, colliding old/Email IDs and unsupported changed metadata. Follow `action_reply_storage.py` capture/manifest/current-fact pattern rather than a new mapper.
- [ ] Implement forward conversion inside caller-owned FK-off transaction. Capture exact source declarations/sequence metadata and original sixteen action stored rows; read nine rowid kinds with explicit rowid, numeric kinds with explicit id, composite kinds with exact keys. Allocate new physical IDs without modifying old physical IDs. Insert finite facts, replace external triggers with exact generated-column predicates, remove only the thirteen old tables, then check integrity/FK and unchanged unrelated objects before commit.
- [ ] Preserve the full external closure: 3 direct blockers on account update/delete and classification switch; 5 unsubscribe blockers on accounts, message identity/thread update/delete and classification switch; 5 reply blockers on the same endpoints. Preserve each trigger's existing changed-value predicate and processing/dispatching state. Keep the 2 direct count triggers and 3 retry immutable triggers. No new account/message FK or common receipt immutability rule.
- [ ] Implement inverse from current typed records, not original row backups. Recreate exact source declarations/objects, explicitly insert nine original rowids, preserve integer IDs and sequence progress, reconstruct composite source key fields from their finite legacy array, restore source triggers and old sixteen actions. Include facts created or updated after forward conversion; do not resurrect the original state or replay an action.
- [ ] Add a current-fact case with a valid synthetic empty-SMTP-ID reply receipt seeded before forward: forward, insert two NULL-PK plans on distinct rowids/versions, append an attempt/step after raising both sequence watermarks, enrich an existing reply receipt's empty SMTP ID, update an unsubscribe skip projection using its existing permitted behavior, add an explicit retry record, inverse, cold open both genuine old AutoReplyStore and EmailStore, perform fresh read/write, and re-forward. Expect exact source projections/values/types/rowid identities and current receipts at every boundary; old IDs and all unrelated tables unchanged.
- [ ] Run `pytest -q tests/test_email_action_migration.py tests/test_email_action_storage.py`; expected unsupported-source refusals before mutation and all current-fact inverse comparisons pass. Commit migration/inverse separately.

## Task 4: Paired release, constructors, DAO and external triggers

**Files:** new `app/action_email_schema_release.py`; existing Store/Email release selectors; `app/store.py`; `app/email_store.py`; new `tests/test_email_action_schema_release.py`, `tests/test_email_action_callers.py`.

- [ ] First write a regression against `prepare_database_schema_release` at `app/store_schema_release.py:120-129`: it currently rejects simultaneous Store/Email changes. Add the exact approved source-pair to target-pair coordinator path before independently preparing either single release; preserve rejection for every other unsupported simultaneous pair. Do not leave Email51 claiming its old 13-table validator contract after removing those tables. The source capture includes `(Store marker value, updated_at)`, all Email migration ledger `(version,applied_at)` rows, source Email marker and exact declarations. `publish` commits the 29-type schema, thirteen-table/trigger transition and both target markers atomically. `rollback` restores both marker values/timestamps and the exact source Email ledger while reconstructing all current facts, original thirteen declarations/trigger sets and the previous sixteen-type actions with their native codec/allocator state.
- [ ] Replace the release's migration callback with a confined paired initializer rather than reusing `_migrate_store` unchanged: `_migrate_store` currently launches only `AutoReplyStore` (`store_schema_release.py:35`). Add an explicit subprocess path that first runs the exact paired DDL/marker transaction and then cold initializes/verifies both `AutoReplyStore` and `EmailStore`; the two constructors must not independently publish half the pair. The deploy coordinator must select this path before either constructor can recreate source tables. Do not globally weaken the existing simultaneous-change refusal.
- [ ] Treat constructor ordering as its own foundation subtask before target activation. Extract only the full **current** classification DDL into neutral `app/email_classification_schema.py`, with a single `create_current_email_classifications(db)` factory and no Store/Email imports. Current means the exact post-Email51 declaration from Task1; `_create_base_tables` at `email_store.py:3680` still declares retired action_plan_json and is not the canonical current factory. Call the neutral factory in fresh AutoReplyStore before actions creation, so even an older-kind action insert has its declared Email parent table. Do not create a stub, repeat the classification declaration, or call EmailStore from Store.
- [ ] Give fresh EmailStore an explicit current non-action-schema construction path, rather than its historical 0→51 migration chain: that chain may expect action_plan_json later and cannot be combined blindly with a current classification table already present. Reuse the neutral classification factory and factor the remaining exact current non-action Email DDL without old thirteen-table creation. On EmailStore-first construction, call the genuine public AutoReplyStore once **outside** EmailStore's own schema_initialize_lock, then enter the Email initializer; avoid nested schema locks and any Store→EmailStore→Store recursion. On an existing verified predecessor, the paired release coordinator handles both schemas/markers before ordinary target initializers; old-schema source constructors remain genuine predecessor code for inverse acceptance.
- [ ] Write explicit RED tests for both empty constructor orders, AutoReplyStore-only old-kind insert with FK checking on, EmailStore-first classification/action write, published predecessor open in both orders, current reopen and crash between paired transition stages. A shared actions FK parent is required even for generated NULL child keys; the neutral parent factory solves that exact dependency, rather than silently constructing a second full Email service inside Store. Verify actual initializer ordering and historical migration assumptions before implementation; if the fresh/current path cannot be confined to this foundation subtask, stop target activation and report the concrete constructor dependency instead of adding fallback or broad legacy cleanup.
- [ ] Bind the actual source versions from Task1, then allocate the target Store/Email markers in this integration commit. Add exact routing in existing selectors; do not treat a phase constant, candidate commit or constructor as already published. Implement atomic paired marker/DDL transition and current-fact inverse following E's verified publication protocol.
- [ ] RED publication tests inject failure before DDL, after old actions capture, during thirteen-family conversion, after first marker and before final commit. Compare code/schema/markers/facts to original transaction state; no marker claims success alone. Refuse inverse if unsupported current facts/declarations cannot be represented, preserving the current database intact.
- [ ] Convert fresh construction and old migration dispatch so a fresh Store/Email pair creates one current actions schema, and an old published pair migrates exactly once. Remove now-obsolete old-table initialization/ALTER paths for migrated families from current bootstrap; historical source constructors remain genuine code in the old-code acceptance environment. Do not leave two writers or compatibility views.
- [ ] Replace EmailStore SQL with finite projections/writes while retaining current method inputs/outputs. Cover `_persist_action_plan`, `_ensure_direct_action_rows`, `_apply_human_classification`, `append_action_plan_version`, direct claim/complete/recovery/retry, unsubscribe claim/effect/continuation/step/terminal/cancellation and reply claim/receipt functions. Retain original transaction scopes, current-plan CAS, dependency ordering, lease generation, immutable retries and exact receipt backfill. Keep account snapshots including existing credential references wherever recovery reads them; do not copy credential values.
- [ ] Translate durable validators, required table/column/index/trigger metadata checks and startup schema acceptance alongside the actual constructor. A NULL public key remains legal at storage level even when application durable validation rejects it; target storage must not strengthen SQL based on the application validator. Account admin and message hydration preserve their existing trigger behavior, with no bypass of in-flight checks.
- [ ] Run `pytest -q tests/test_email_action_schema_release.py tests/test_email_action_callers.py tests/test_email_store.py tests/test_email_store_startup_validation.py tests/test_email_action_plan_reference.py tests/test_email_pipeline.py tests/test_email_retry_schema_release.py tests/test_store_schema_release.py`; expect source/target/inverse factory and named caller semantics pass. Commit release/constructors/DAO and matching architecture/runtime documentation together.

## Task 5: History, Attention, context and remaining callers

**Files:** `app/store.py`, `app/audit_web.py`, `app/web_api/email.py`, `app/web_api/attempts.py`, bounded consumers listed above; existing History/Email tests.

- [ ] RED History test inserts direct actions with identical nullable public IDs but distinct signed rowids; compare native source and target History IDs/source IDs, ordering, pagination, total counts and detail retrieval. Include mixed old/new action families and a later row update. Ensure no source_id becomes physical actions.id.
- [ ] RED Attention test retains failed nullable-public-key direct rows through compact source-rowid selection; keep existing retry-window/status/current-plan predicates. Verify representative query plans and bounded VM steps with many unrelated action records, plus current-plan poll and due-claim indexes. Do not add speculative indexes after sufficient access is shown.
- [ ] Convert remaining direct SQL/physical table existence checks. Actual families: worker direct claim/complete/recovery; CLI source-verified retry and audit/reconciliation read paths; unsubscribe direct and audited history; classification observability; safe context receipts; deployment readiness; daily-report counters; feedback replay and original result timestamp. Stop if exact-symbol/AST scan finds an active consumer outside the frozen list, add that concrete caller to this task and its tests.
- [ ] Verify the three Console surfaces: History page/detail/native-link availability, Attention failed-action row and existing retry state, Email classification detail/feedback replay/receipt summaries. Existing SMTP-disabled settings and legacy task failure remain unchanged. Native source absence remains honest unavailable state, with no replay.
- [ ] Run `pytest -q tests/test_store_history_pagination.py tests/test_email_action_attention_query_plan.py tests/test_audit_web.py tests/test_email_web_api.py tests/test_email_context_source.py tests/test_email_worker.py tests/test_email_action_reconcile.py tests/test_email_unsubscribe_direct.py tests/test_email_unsubscribe_audit.py tests/test_email_reply_delivery.py tests/test_email_task_adapter.py tests/test_email_task_producer.py`; expect all selected behavior/query checks pass with native/provider fakes and no real sends. Commit callers and same-commit behavior docs.

## Task 6: Latest-copy, genuine old code and native fake-provider acceptance

**Files:** new `scripts/validate_action_email_copy.py`; new `tests/test_email_action_copy_proof.py`; existing `scripts/validate_native_copy_lifecycle.py` only a confined Email case extension; new evidence JSON and validation document.

- [ ] Build the copy harness with the existing reply-copy pattern and these flags: `--source` (owned immutable SQLite backup), `--source-ref` (exact published predecessor), `--evidence` (aggregate-only report). It must inspect source markers/ref, compare all 144 fields and SQL storage classes/keys/sequence watermarks, unrelated-table fingerprints/counts, original sixteen actions, current queue/History/Attention counters and all 13 blocker states. Never emit row bodies/digests, credentials or trajectory content in the evidence artifact.
- [ ] Use SQLite backup API for the latest production copy in a task-owned temporary directory; quick_check/integrity/FK first. Keep only the newest verified backup for this workflow. Run current default constructors on the copy; record actual marker transition and source/table-count change. A 13-table disappearance with actions still present is expected; it does not prove full ten-table completion or a proportional size gain.
- [ ] Run exact old published code in a fresh process on inverse-derived current facts, including fresh source constructor and meaningful read/write: classification feedback replay, direct action attempt completion with a fake result, native receipt enrichment, sequence continuation, History source IDs and failed action visibility. Then current-code re-forward and compare current facts again. Merely importing old classes is insufficient.
- [ ] Run `pytest -q tests/test_email_action_copy_proof.py`; expected all copied synthetic failure/current-fact/inverse/old-process cases pass. Then run the new copy harness against the owned backup and exact source ref selected in Task1. The evidence result must say `passed` only after each read/type/rowid/sequence comparison and cold old-process test succeeds.
- [ ] Add confined native cases to the existing lifecycle harness: actual Consumer/Audit produce a persisted accepted action on the migrated copy, provider writes are replaced in memory, resulting business receipt/context/History readback is verified. Run both approval and revision cases; no live SMTP/IMAP/browser/DingTalk mutation is allowed. The ordinary deterministic Email source/claim/receipt chain is tested with its existing native provider substitutes; this does not activate auto reply.
- [ ] Create the owned directory with `EMAIL_ACTION_NATIVE_ROOT=$(mktemp -d /tmp/ceo-email-native.XXXXXX)`. Run `python scripts/validate_native_copy_lifecycle.py --owned-temp-root "$EMAIL_ACTION_NATIVE_ROOT" --case approve` and the same command with `--case revision` after the harness's explicit Email setup is added. The named variable must resolve to the verified task-owned temporary directory. Expect actual native CLI completion, fake-provider receipts, unchanged original rows and no real external effects. Record scripted tests, actual native model evidence and untested live provider outcomes separately. Clean owned temporary copies/native outputs after concise accepted evidence is saved. Commit harness/evidence independently.

## Task 7: CI, publication and live readback

**Files:** current validation/program status; versioned release evidence only after runtime code has passed all prior gates.

- [ ] Run focused checks after the last code change: `git diff --check`, scoped Ruff on changed Python files and the exact selected pytest files from Tasks2–6. Preserve unrelated dirty changes and stage only owned hunks. Do not run serial full pytest while service is live; if full CI is required, use repository CI or the approved parallel `pytest -q -n 6` outside restart windows.
- [ ] Integrate the latest remote main with safe fast-forward/merge, preserve unrelated increments, rebind the immediate predecessor and changed-module evidence if needed. Re-run only checks justified by the increment. Obtain independent specification/quality review through the existing review mechanism; no extra agents are authorized by this plan alone.
- [ ] Push the complete commits, create and attach the review PR, wait for actual CI completion, merge only within existing authorization, then use `python -m app.deploy` from development. The paired publication path must back up the DB and roll code/schema together before external work resumes; after external work resumes, no stale DB rollback can erase new receipts. Do not hand-kill/kickstart or edit/test production checkout.
- [ ] Read new PID, healthz, queue/claim state, Attention, History and Email API details, plus markers, thirteen-table retirement and current typed counts using read-only schema/aggregate queries. Verify no new unexpected failed/stuck work or duplicate business receipt. No settings-secret fields are printed. Save exact production commit/marker/CI/deploy/readback boundaries in the validation doc before marking publication complete.
- [ ] Do not VACUUM or add retention cleanup in this unit. Report actual DB/WAL/index measurements without claiming table reduction is proportional space recovery. No fixed retention deadline is introduced.

## Optional separately reviewable dormant SMTP retirement

Default implementation includes both reply claim and reply receipt record types and their exact constraints, snapshots and existing disabled behavior. Current static evidence finds no app construction of `EmailReplyDelivery`, no app caller to `accepted_email_reply_effect`, and no app caller to terminated-owner reply recovery; tests exercise them. Configuration, authorization and worker already reject auto reply. That evidence supports a later scoped removal proposal for dormant execution code, not automatic field/table retirement during migration.

Any retirement must preserve real Sent evidence/business receipts and active `email_action_identity` imports used by unsubscribe. Do not delete a whole module because its delivery class is uncalled; do not delete zero-row supported record types by inference. Approval, code/tests/docs and commit for optional removal are separate from storage migration. This plan adds no new rejection, audit, safety gate or activation policy.

## Acceptance and known risks

Success requires source-equivalent legal SQL values/NULLs/affinity/constraints, exact original public identities and nine ROWID namespaces, preserved 2 AUTOINCREMENT sequences, current business receipts and feedback lineage, complete typed FK/13 external trigger closure, unchanged old sixteen action facts, genuine current-fact inverse and old AutoReplyStore and EmailStore cold read/write, bounded list/claim access, actual native fake-provider acceptance, successful CI/normal deployment and honest live readback. Full redesign remains incomplete until the remaining core-table consolidation finishes.

Hard implementation risks are explicit: generated TEXT/BLOB/INTEGER affinity and BINARY uniqueness must match source; composite FK references require the unconditional parent unique index; original immediate/RESTRICT timing must survive FK-off migration and normal FK-on execution; MAX+1 must occur under the writer lock; source ROWID updates must not mutate original opaque PKs; OR IGNORE must not run Python parent validation ahead of native source constraints; old partial index scopes must not accidentally index Email composite keys; constructor/validator and all external triggers must switch together. None is verified by this plan or by the frozen production zero-NULL counts.

The source snapshots contain 382 deliberately NULL feedback prior-plan associations; their NULL allowance and replay semantics are mandatory. The nine public PKs have normal creation validation/generated identities, but SQL-legal nullable states remain preserved by this storage plan. No incidental constraint cleanup is proposed.

## Planning acceptance

Independent sequential SPEC and QUALITY accepted the plan and complete field
map at `6a273df766e900bd7792bfe204e2ec346c7f6cb8`. Both compared the 144 unique
source fields against the frozen inventories and inspected the actual
constructor and paired-publication dependencies. The independent source-only
oracle at `cf47ee0e` also passed SPEC/QUALITY and 36 focused cases. This accepts
the implementation direction within the already approved redesign; no target
schema or migration has been implemented, no version allocated, and no
production transition follows from planning acceptance. Task1 still requires
the actually published Phase5E predecessor.
