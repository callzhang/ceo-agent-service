# Follow-up and Todo-sync actions implementation plan

> **For agentic workers:** Use `superpowers:executing-plans` task by task. This document authorizes planning only; execution requires the parent task's implementation scope. Each implementation task starts with a focused failing test, makes the smallest change, runs its named checks, updates the describing document and commits only owned changes.

**Goal:** Move the approved nine existing follow-up/Todo-sync families into finite typed `actions` records while retaining all 150 source fields, existing business behavior and a current-fact inverse.

**Architecture:** Add opt-in declarations and pure migration first. Bind the actual published predecessor only when it exists, then publish the nine families together with their one external schema dependency and all live callers. Preserve existing public identities and use physical typed references where the approved global mapping requires them; business/provenance parents remain in their current tables.

**Tech stack:** Existing Python/SQLite storage, Store, Dispatcher adapters, Console and existing deploy tooling; no new dependency.

## 1. Evidence and publication boundary

This plan is based on candidate `2b888442` on `codex/database-redesign-email-actions-20261014`, the approved `docs/superpowers/specs/2026-10-11-database-redesign-design.md` and global field CSV, and the accepted source-only artifacts:

- `docs/superpowers/specs/2026-10-14-database-redesign-followup-sync-source.json` (literal actual and genuine production-code fresh declarations).
- `docs/superpowers/specs/2026-10-14-database-redesign-followup-sync-source-map.md` (finite code/caller closure).
- `tests/test_followup_sync_source.py` and `docs/database-redesign-followup-sync-source-validation.md` (twenty native DML cases; metadata and native SQL semantics, not runtime acceptance).
- Companion `docs/superpowers/specs/2026-10-14-database-redesign-followup-sync-field-map.csv`: exactly one row for every source field, with source affinity/nullability/defaults, target storage/projection, index membership and declared FK.

The frozen source capture binds production commit `9a2a23fcaad6eb73f6131d5b7a70d22ae02418cd`; it is preparation evidence, **not** the future publication predecessor. At planning time the E sixteen-type candidate is not published and the Email twenty-nine-type opt-in storage/pure migration is disabled. Do not assign a Store/Email version, reserve a future number, declare a published manifest or assume the eventual predecessor contains twenty-nine types. Use its actual published declaration and add exactly these nine types after binding. No initializer selects this extension before Tasks 4–7.

| Source record_type (retain exact name) | Fields | AUTOINCREMENT | Indexes | Declared outgoing FKs |
|---|---:|---:|---:|---:|
| work_updates | 9 | 1 | 2 | 0 |
| follow_up_drafts | 31 | 1 | 6 | 0 |
| follow_up_send_attempts | 16 | 1 | 3 | 0 |
| business_task_follow_ups | 18 | 1 | 2 | 2 |
| business_task_follow_up_send_attempts | 12 | 1 | 2 | 1 |
| task_todo_sync_outbox | 15 | 1 | 2 | 0 |
| business_task_todo_sync_outbox | 15 | 1 | 2 | 1 |
| work_todo_dingtalk_links | 17 | 1 | 3 | 0 |
| business_task_dingtalk_links | 17 | 1 | 2 | 1 |
| **Total** | **150** | **9** | **24** | **5** |

There are eight source CHECKs and zero owned source triggers. The separate `business_legacy_links` dependency has its own two CHECKs, four indexes, ten FKs and AUTOINCREMENT state; none are included in the nine-family totals. Actual row counts in the table order above are 5468/2477/65/0/0/242/0/875/0. These are captured observations, not future migration row-count assertions. No history is removed by age or terminal status; no field retirement is justified by this source evidence.

## 2. One atomic data unit, staged code work

All nine can migrate in one transaction. Their only internal declared edge is business send-attempt → business follow-up. Four edges lead to retained `business_tasks`/`business_task_signals`; those parents need no migration. The sole external schema SQL dependency is `business_legacy_links.work_update_id → work_updates.id`. Rebuild that retained table within the same unit, retarget only that endpoint and preserve its other nine FKs, exclusivity CHECKs, keys, rows, IDs and sequence metadata. This is finite dependency conversion, not early business consolidation.

A split into legacy/business publications is technically possible but adds an intermediate actions manifest and release/inverse combinations without removing the shared actions rebuild or finite caller work. A split putting work_updates last would additionally keep a retired physical table through the earlier releases. Neither is forced by the captured dependency graph. Therefore use **one final nine-family publication**, with smaller independently reviewable preparation, storage, migration, release and caller commits. If refreshed published-source closure adds a real dependency, stop at the source-binding task and revise this bounded unit from that evidence.

`work_todos.created_from_update_id` is a scalar, not a declared FK. Keep its source public value, including zero/missing-parent cases. Convert semantic-import and source-update joins to the work_updates projection, never rewrite the scalar to physical actions IDs. Apply the same rule to unconstrained legacy project/Todo/draft/link/outbox joins. No title/name matching, automatic semantic import or invented parent rows.

## 3. Concrete target contract

The CSV refines the approved global mapping into explicit source-shaped projections; it does not add source fields. `actions.id` is internal physical identity; each source `id` is stored once as canonical signed decimal `legacy_key`, unique within `record_type`, and projected as INTEGER. Keep all nine independent native AUTOINCREMENT namespaces and sequence-row absence versus zero. Use the existing allocator/state mechanism already used by AUTOINCREMENT action families; do not create another durable table. Lock before source-default evaluation, source row/allocator read and ID allocation. Test explicit negative/zero/positive IDs, deleted maximum, sequence above max, rollback, failed/conflicting insert and signed-64-bit exhaustion against native SQLite.

Other fields are stored once in finite `payload_json` members and exposed as source-affinity `sync_<field>` generated values, scoped only to owning nine-family types. Preserve SQLite storage class and original JSON text bytes with the existing finite SQLite scalar codec; a `_json` suffix does not authorize parsing/reformatting or an extra JSON-valid CHECK. Shared action task/run/candidate/Email reference slots stay NULL for these types. `sync_` denotes planned target columns, not observed source columns. No persisted copy of generated values or historical payload arrays.

The one exception is the approved physical internal FK: business send-attempt `draft_id` is stored once as `sync_draft_action_id REFERENCES actions(id)` and projected from the exact typed parent `legacy_key`; do not also store draft_id in payload. Preserve immediate NO ACTION on UPDATE/DELETE and require parent `record_type='business_task_follow_ups'`. Parent public-ID mutation while referenced must fail just as source PK mutation does; physical identity alone must not permit it. Keep a normal FK for parent existence and the finite type/public-key checks in existing action storage style, without adding application policy. Unreferenced public-ID mutation remains native-legal.

The four external declared edges remain native integer-affinity fields referencing existing parents: business follow-up business_task_id/source_signal_id, business outbox business_task_id and business link business_task_id. Do not map them to `actions.id` until those parents actually migrate. For every FK, apply source affinity before lookup: preserve legal NULL and exact stored scalar results; zero/negative IDs are valid when the actual parent exists. Do not coerce with unconditional Python int(), reject legal numeric TEXT/REAL inputs prematurely or invent a positive-ID rule. Values that cannot match a native INTEGER PRIMARY KEY remain FK failures, not silently repaired references. Native source-versus-target probes decide the result for unusual SQLite TEXT/BLOB/numeric values.

For `business_legacy_links`, keep its original column names and DTO shape. `work_update_id` now stores the mapped physical action endpoint with a real FK to actions.id and a finite work_updates-parent type constraint. Every public Store/importer lookup converts public update ID to this endpoint; outward reads project the parent's original public ID. Do not keep a second source-ID column. Preserve other endpoint values, NULL patterns, the original one-business/one-legacy CHECKs, all UNIQUE membership, immediate NO ACTION behavior and inbound public-ID update blocking. Inverse converts current endpoints back to public update IDs before restoring literal source FK DDL. Preserve this table's own rowid/ID, row order where observable, sequence absence/high water and all unrelated schema objects. Add no generic link ledger.

Translate all 24 source indexes, including automatic UNIQUE indexes, to type-scoped generated/physical keys. Keep BINARY collation, key order, ASC/DESC and exact partial predicates. Preserve these distinctions:

- Legacy links: global unique **nonempty** provider Task ID and one creating/active link per work Todo. Business links: only one creating/active link per business Task; provider IDs may repeat.
- Business follow-up dedupe is UNIQUE; legacy draft dedupe has no DB UNIQUE constraint.
- Both attempt claim tokens are UNIQUE; UUID and draft/revision pair are not UNIQUE.
- Both outbox operation keys are UNIQUE; source operation/status/state CHECKs remain per family, not a combined lifecycle.

Translate all eight source CHECKs literally over source-affinity fields. Do not introduce immutability or append-only rules: source revision/update/delete legality and the oracle's zero-trigger result remain true. The historical actual draft updated_at default is empty string; fresh is current_timestamp. Retain that distinction on omitted writes. Inverse restores each captured variant's literal column/table/index declarations, including actual draft/link ALTER order and quoted rebuilt legacy outbox. It does not normalize existing blanks or create defaults on migrated rows.

## 4. Implementation files and interfaces

Planned new runtime files (none created by this planning task):

| File | Bounded responsibility |
|---|---|
| app/action_followup_sync_definitions.py | Nine exact field/default/affinity/FK/index/CHECK definitions; source-shaped projections and opt-in schema parts. |
| app/action_followup_sync_storage.py | Read-only source capture, pure forward and current-fact inverse, finite external dependency conversion. |
| app/action_followup_sync_schema_release.py | Exact published predecessor/target pairing and inverse evidence for existing release coordinator. |
| scripts/validate_followup_sync_copy.py | Offline latest-source copy/forward/inverse/new-fact/old-code acceptance; no provider calls. |
| docs/database-redesign-followup-sync-validation.md | Future implementation receipts and evidence limits, separate from accepted source-only validation. |

Modify existing `app/action_storage.py` only for explicit opt-in schema/projection/mutation integration. Planned finite API in the new storage module: `capture_followup_sync_source(db)`, `migrate_followup_sync_schema(db, source)`, `restore_followup_sync_schema(db, inverse_state)`. Capture is read-only; forward/inverse require the caller-owned FK-off transaction, following existing pure migration ownership. `source` carries the captured complete declaration and variant, not an invented marker. Returned inverse evidence contains source schema/markers, native sequence-row absence metadata and finite dependency conversion metadata; no old business-row snapshot or saved source sequence values used to overwrite current allocator waters. Forward transfers the current waters to the target allocator; inverse reads its current values, including post-forward advances and deletes, rather than restoring stale saved values. Use existing `ActionWriteResult`/source-shaped writers for insertion and update, preserving source cursor/rowcount behavior.

Each task below is a separate commit. Update `docs/architecture.md` and `docs/runtime-mechanism.md` in the same commit that changes described runtime behavior. Do not edit accepted source artifacts or unrelated Email/E work during implementation. Claim exact files/hunks before starting.

## Task 0: Recheck source facts and caller decisions (preparation only)

**Read:** source JSON/map/oracle, global CSV; `app/store.py` legacy/current follow-up helpers; `app/todo_completion.py`; `app/todo_sync.py`; `app/task_noise_backfill.py`; `scripts/backfill_follow_up_todo_ids.py`.

- [ ] Run `python -m pytest -q tests/test_followup_sync_source.py`; expect all twenty cases passing with no target import.
- [ ] Enumerate current Python call ASTs for `create_follow_up_draft` and `_follow_up_dedupe_key`, separately counting production and test calls, plus symbol/method-value references. Expected captured finding: zero production callers of create, fifty test calls, one production dedupe use inside create. Repeat before removal, not a text-match count.
- [ ] Retire the unused production create helper and sole dedupe helper in Task 5. Convert fixtures to explicit source-shaped action insertion preserving each fixture's original identity, payload and state. Do not drop their historical records or introduce a new sender.
- [ ] Retain `update_follow_up_draft`: `task_noise_backfill` apply calls it; legacy outbox dispatch → maybe_create → saved failed-link refresh → close_todo_with_completion_evidence → complete_follow_ups_for_todo calls it. Preserve revision increment, claim reset, claimed-attempt invalidation and optional transaction ownership.
- [ ] Convert `scripts/backfill_follow_up_todo_ids.py` explicitly in Task 6. Its `--apply` binds an existing Todo or creates a work_todo and writes draft todo_id without the revision/update side effects of `update_follow_up_draft`. Keep that exact behavior using a finite action update, keep its dry-run output and existing selection algorithm. Do not route it through the revision helper or run it on production.

Proof: retain a finite file/symbol inventory in the new implementation validation doc; no schedule/provider/native CLI invocation. `follow-up-delivery-v1` remains soft-deleted and retired; scheduled_at stays suggestion metadata. Current TaskAgent business follow-up creation and Console button sending remain active.

## Task 1: Opt-in finite storage and writers

**Create:** `app/action_followup_sync_definitions.py`, `tests/test_followup_sync_action_storage.py`. **Modify:** scoped `app/action_storage.py`; scoped architecture/runtime paragraphs; new implementation validation doc.

- [ ] First write failing tests replaying both frozen variants and exercising the CSV's 150 source-shaped projections, independent public IDs, omitted defaults and target type separation. Assert all earlier action declarations byte-identical when opt-in is absent.
- [ ] Add `include_followup_sync=False` opt-in extension only over the existing reviewed Email twenty-nine-type candidate, in its two exact actual/fresh reply variants. This is a disabled scaffold, not support for arbitrary predecessor manifests; reject other option combinations. The later published source may require an evidence-backed adjustment. No constructor/release/version selection. Implement finite insert/update/delete integration using source defaults under the writer lock; enforce payload ownership and NULL unrelated reference slots.
- [ ] Implement exact source constraints/index translation, physical draft FK and external parent FKs. Exercise all twenty accepted native cases on target operations, with special FK/public-key mutation probes, nonunique UUID/revision pairs and source-legal revision rewrite/delete. Add codec/affinity comparisons for NULL, signed IDs, numeric-looking TEXT, REAL, BLOB, Unicode, embedded NUL and raw nested JSON strings; compare result, storage class and constraint failure rather than Python normalization.
- [ ] Run `python -m pytest -q tests/test_followup_sync_source.py tests/test_followup_sync_action_storage.py`; expect green source and target equivalence cases. Run Ruff on changed Python files and `git diff --check`, record evidence, update documents and commit owned changes.

## Task 2: Read-only capture and pure forward/current-fact inverse

**Create:** `app/action_followup_sync_storage.py`, `tests/test_followup_sync_action_migration.py`, `tests/test_followup_sync_action_inverse.py`.

- [ ] Write refusal tests before implementation against the same exact reviewed Email twenty-nine-type candidate scaffold used by Task 1: MAIN-qualified complete action declaration, both source variants, nine source sequences, business_legacy_links declaration/rows/water, four retained external endpoints and every external SQL/FK dependency. Unsupported extra object/column/trigger, mixed/partial source or TEMP binding must fail without changing schema/data/markers/waters.
- [ ] Capture schema via MAIN-qualified metadata/row reads. Use SQLite's native parser and isolated rename/DROP probes for MAIN/TEMP dependency effects, following existing Email source-capture behavior; do not infer SQL references by regex. Do not reuse a prior candidate capture as publication evidence.
- [ ] Implement pure forward: save complete predecessor action identities/physical relationships, copy all nine current rows, resolve the exact internal physical endpoint, rebuild business_legacy_links with finite remapping, restore unrelated objects and all allocator metadata, then remove the nine old physical tables. No views, dual writes, provider calls or Store marker changes. The caller owns one transaction; injection at each copy/rebuild/drop stage rolls everything back.
- [ ] Implement inverse from **current target facts**: restore predecessor actions, all nine literal source declarations, current new/changed/deleted rows and endpoint/public-ID mappings; restore business_legacy_links's original FK declaration using current links. Preserve absent sequence rows separately from zero, migrated explicit maxima and high waters raised after migration, including later deletion of the maximum. Preserve existing predecessor allocator state and unrelated current facts. Restore source schema marker values/timestamps only in paired release, not pure migration.
- [ ] Prove forward → mutate → inverse → old-code reopen → re-forward for actual/fresh. Mutate every family (including originally empty business families), remove existing rows, insert a business legacy link, change permitted refs, advance/delete allocator maxima and preserve a saved provider ID/receipt. Compare all 150 projected facts, exact FK/UNIQUE/CHECK outcomes and source declarations. Never recover by copying old database business rows.
- [ ] Run `python -m pytest -q tests/test_followup_sync_action_migration.py tests/test_followup_sync_action_inverse.py tests/test_followup_sync_action_storage.py tests/test_followup_sync_source.py`; expect focused green. Record synthetic-only boundary and commit. No version/initializer selection.

## Task 3: Bind the actual published predecessor (publication prerequisite)

**Modify:** finite captured-source constants/tests in `app/action_followup_sync_storage.py`; new implementation validation doc. **Create:** a source-binding artifact only after the predecessor is actually published; its filename and version come from that real receipt, not this plan.

- [ ] Observe the exact pushed/published code commit separately from the database snapshot, record observation times, and verify the commit did not change across capture. Read live Store/Email markers, complete action manifest, actual nine-family declaration and external closure in one database read transaction. Code HEAD and the database snapshot are separate observations, not an atomic cross-system capture. Construct fresh source in a new subprocess from that exact archived published code. Recheck call paths against intervening increments.
- [ ] Compare both with Task 2 support: action declarations and typed relationships, source defaults/order, sequence states and dependency objects. Expand only for a demonstrated published declaration difference with focused RED/GREEN evidence; refuse unsupported source before mutation.
- [ ] Record immutable source fingerprints and exact actual/fresh pair supported by the release. If E/Email is still unpublished, this task remains unfulfilled while Tasks 0–2 may proceed disabled. Do not allocate or bind a version until the real predecessor and current coordinator policy are known.

Gate: the actual published-source declaration must exist and match before Tasks 4–7 can publish/select anything. Preparation tests alone never satisfy this gate.

## Task 4: Paired release and exact constructor integration

**Create:** `app/action_followup_sync_schema_release.py`, `tests/test_followup_sync_schema_release.py`. **Modify:** `app/store_schema_release.py`, `app/store.py` initializer only, relevant explicit publication selectors in `app/storage_maintenance.py`, existing release validator scripts; describing docs.

- [ ] Write failing exact-source/target/unsupported-marker and rollback-stage tests. Include nonempty business_legacy_links, actual/fresh defaults, altered source after capture and two-connection publication race cases already covered by the established release pattern.
- [ ] Allocate real marker(s) through the existing coordinator only after Task 3. Publish source-to-target migration and marker update atomically. Remove selected initializer paths that create/alter these nine physical source tables. Default fresh construction builds the verified fresh target contract directly; historical upgrade retains actual behavior. No per-read repair or old-table fallback.
- [ ] Pair inverse with the current-fact inverse evidence. At failure before/after fast-forward, database conversion, console build, restart and health, keep code/schema aligned and retain writes made after forward. Existing release checks remain; add no unrelated authorization/audit policy.
- [ ] Run `python -m pytest -q tests/test_followup_sync_schema_release.py tests/test_followup_sync_action_inverse.py tests/test_run_storage.py`; expect source/target/rollback/reopen cases green. Record candidate-only status and commit. Constructor activation is not releasable alone: Tasks 5–6 must land before copy/CI/publication.

## Task 5: Store, TaskAgent and Dispatcher writers/readers

**Modify:** scoped `app/store.py`, `app/task_agent.py`, `app/follow_up.py`, `app/todo_sync.py`, `app/todo_completion.py`, `app/dispatcher/adapters.py`, `app/task_noise_backfill.py`; relevant describing docs. **Create:** `tests/test_followup_sync_callers.py`.

- [ ] First test current returned DTO/public IDs and transactional behavior on source, then target. Cover work_update + project activity, TaskAgent follow-up cancel/create and outbox intent in its existing domain transaction, Console expected revision and business send claim/recover/finish, and legacy update with claimed-attempt invalidation. Preserve `_db` transaction ownership.
- [ ] Switch finite SQL to typed projections and source-shaped mutations; retire only the verified unused create/dedupe production helpers. Keep business send attempts as separate rows, exact claim UUID/token/revision/lease/CAS conditions and nullable result values. No scheduled sending or legacy attempt insertion route.
- [ ] Convert both adapter direct-SQL metrics/claim/renew/finish/retry/reconcile paths together. Preserve adapter names, public numeric source_id string, generation/lease tables and atomic source receipt + Dispatcher generation completion. A late/stale owner still cannot complete a different claim. Preserve due ordering/limits, attempt_count<3/backoff and recovery predicates.
- [ ] With in-memory provider substitutes prove both outbox consumers, provider-ID persistence **before** readback, unknown-create reconciliation from saved IDs, failed saved legacy link refresh and the retained update helper completion chain. Do not reopen terminal legacy drafts/outboxes or turn unknown receipts into new sends. Prove TaskAgent and Console business flow even though source business tables were empty.
- [ ] Run `python -m pytest -q tests/test_followup_sync_callers.py tests/test_follow_up.py tests/test_todo_sync.py tests/test_todo_completion.py tests/test_consumer_dispatcher.py tests/test_dispatcher_idle_claims.py`; expect original business assertions preserved. Run the specifically affected TaskAgent tests selected from its current test inventory, record names/results and commit.

## Task 6: Remaining readers, maintenance and explicit scripts

**Modify:** `app/audit_web.py`, `app/quality_gate.py`, `app/history_actions.py`, `app/task_retrieval.py`, `app/task_progress.py`, `app/task_owner_backfill.py`, `app/task_project_repair.py`, `app/task_semantic_import.py`, `app/web_api/registration.py`, `app/web_api/tasks.py`, `app/cli.py`, `scripts/backfill_follow_up_todo_ids.py`; all remaining literal/method-value sites in the frozen source navigation plus new sites identified in Task 3. **Create:** `tests/test_followup_sync_readers.py`, `tests/test_followup_sync_index_access.py`.

- [ ] First fail target tests for legacy/formal Tasks details, sent-Todo/progress DTOs, History pagination/detail URLs/search/operation logs, failed-history Attention and quality receipt failures. Preserve legacy detail-only failure action and current scanner exclusion of legacy links. Do not hide historic failures because their old table disappeared.
- [ ] Convert owner/noise/project backfills, semantic-import legacy_kind/source lookup and business_legacy_links insertion/lookups. Keep `history_only` disposition, existing dry-run/apply outputs and no semantic-import execution as migration side effect. Source joins use public IDs; physical FK conversion is confined to typed endpoint access. Explicit todo-id script writes only its former todo_id field and original work_todo side effects, with no draft revision churn.
- [ ] Convert CLI legacy update listing, both Dispatcher registration/consumer paths, dry-run behavior, maintenance/quiet-wait/schema table-presence checks and dynamic table-name specifications. Enumerate expected historical constants separately from runtime SQL; no compatibility view or dynamic fallback to retired tables.
- [ ] Prove source-equivalent results and EXPLAIN/VM bounds for public-ID lookup/order, status/due queries, project/Todo schedule, owner/conversation sent windows, History updated ordering, attempt reconciliation/draft-revision and active/provider links. Grow unrelated action types and same-type history separately; target work must remain bounded by the equivalent indexed source query. Add only evidence-required indexes without changing semantics.
- [ ] Run `python -m pytest -q tests/test_followup_sync_readers.py tests/test_followup_sync_index_access.py tests/test_store_history_pagination.py tests/test_task_owner_backfill.py tests/test_task_project_repair.py tests/test_task_semantic_import.py tests/test_task_retrieval.py tests/test_history_actions.py tests/test_quality_gate.py`; run affected Console/CLI selections and the explicit script synthetic apply cases in test_followup_sync_readers.py. Record focused results and commit.

## Task 7: Offline source copy, genuine old code and native CLI acceptance

**Create:** `scripts/validate_followup_sync_copy.py`, `tests/test_followup_sync_copy.py`. **Modify:** existing `scripts/validate_native_copy_lifecycle.py` only for finite this-unit routing, new validation doc; architecture/runtime candidate status.

- [ ] First test the copy validator on synthetic actual/fresh sources and failure cleanup. Obtain one latest verified SQLite backup using the established backup API; keep it and all archives/copies in one task-specific temporary directory. Verify integrity/FKs/source identity, record aggregate-only acceptance and remove task-owned artifacts after proof. Do not retain historical backup sets or private trajectory copies.
- [ ] On actual copy and genuine archived-published-code fresh DB, run constructor forward, read/modify all nine families and retained link dependency, inverse with current facts, then cold-start the exact old code in a new process, perform native old-code source reads and cold writes (new work update, retained legacy draft update, business follow-up claim/finish, outbox/link mutation through existing APIs using substitutes), and re-forward. Verify the re-forwarded facts include those cold writes and their raised allocator waters. Inspect source values, API results, allocator absence/high waters, leases/receipts and physical table count. Do not call providers.
- [ ] Run actual native Consumer and Audit CLI paths through the established offline acceptance harness with in-memory outbound substitutions and this nine-family persistence. Record native session/range and terminal receipts without copying trajectory bodies. Verify the specific business result against database readback; native exit zero alone is insufficient. Test Console button flow and both adapter consumers with substitutes separately because they do not run through app/worker.py Consumer/Audit.
- [ ] Run `python -m pytest -q tests/test_followup_sync_copy.py` and the validator using its implemented `--help`/documented exact offline arguments. Document those actual arguments and receipts after the file exists; no imagined production command or native acceptance claim in this plan. Re-run a proof only after a source/runtime increment materially invalidates it.

## Task 8: Review, CI and normal publication

**Modify:** new implementation validation doc and candidate-status paragraphs to reflect actual evidence; existing release metadata only from real receipts.

- [ ] Self-review exact CSV coverage (150/150; nine IDs; zero retired fields), source CHECK/index/FK equivalence, finite caller closure, physical/public mappings, current-fact inverse and honest candidate/published wording. Obtain independent spec then quality review under the parent execution workflow and resolve findings with narrow regressions.
- [ ] Run targeted checks plus Ruff/diff; use existing remote CI/full parallel CI policy for complete acceptance. Do not run the live-tree full serial suite or overlap it with restart. Bind CI to the exact final commit/source manifests; passing earlier Email/E CI does not accept this unit.
- [ ] After all gates pass, push/merge under the authorized parent publication workflow, then `python -m app.deploy` from development. Existing deploy must quiet claims, pair code/schema/rollback and preserve current receipts. This plan executes none of those operations.
- [ ] Read back new PID, healthz, Store/Email markers, exact complete actions manifest, absence of retired nine tables, preserved business_legacy_links FK, queues/claim generations, Attention, History/detail and aggregate receipt counts. Provider delivery outcomes require their own real receipts; schema publication must not generate a test send. Record deployment status separately from local/CI/native-copy proof.

## 5. Release proof table

| Boundary | Required evidence before advancing |
|---|---|
| Planning → disabled storage | Exact 150-row mapping, source DML oracle, native caller decisions; no version/source publication claim. |
| Storage → pure migration | Source/target field and SQL legality equivalence; earlier default-disabled manifests unchanged. |
| Migration → release binding | Actual/fresh forward-current-fact-inverse, external FK/row/sequence preservation and nonmodifying unsupported-source refusal. |
| Binding → activation | Actual published predecessor exists; exact source captured; paired coordinator/constructor and every caller landed. |
| Activation → publication | Latest source-copy/old-code cold reopen, native CLI substitute receipts, index/VM comparisons, independent review and final CI. |
| Publication → completion | Normal deploy receipt and PID/health/schema/queues/Attention/History readback. |

No audit, authorization, confirmation, retention, generic relation, credential or provider policy change belongs to this unit. Any later wish to alter a product behavior is a separately scoped decision; the work here preserves the approved behavior and source facts.


## Independent planning acceptance

Plan/map `ad8852d0` passed sequential independent SPEC then QUALITY. SPEC
checked all150 unique field rows against both source variants with zero
affinity/default/nullability/PK/index differences, plus six bounded native
uniqueness/FK/scalar/CAS checks. The unused-create/retained-update distinction
was independently verified from calls and method-value references. Root also
reran the accepted twenty-case native source oracle successfully. QUALITY
found no blocking design defect and accepted the following Task1 split.

Task1A implements only opt-in declarations, constraints, projections and
full-value storage tests over the exact reviewed Email29 actual/fresh scaffold.
Task1B implements source-shaped insert/update/delete, omitted defaults under
the writer lock, allocation, mutation/FK parity and source-shaped results.
Both remain disabled, with no constructor, release or version selection.
Neither Task1 nor source-write/default/locking equivalence is complete until
1B passes. This changes commit/review boundaries only; all nine families still
form one atomic data publication unit with their finite external dependency.


### Task1A acceptance and measured pre-activation index item

Task1A at1cfeb2fe passed sequential independent SPEC then QUALITY after the
legacy draft_id NOT NULL repair; Task1B remains unimplemented. The new native
sync_draft_action_id self-FK scans unrelated action rows on parent DELETE.
Carry its measured source/target VM comparison into Task6 and remove that
growth before activation with an evidence-required supporting index. Include
native FK parent DELETE/public-key mutation, not only application SELECTs, in
the performance matrix. This is a storage access requirement, not a new policy
or permission to change source fields/constraints or unrelated Email behavior.


### Task1B acceptance and Task2 sequencing

Task1B9402291b passed sequential independent SPEC then QUALITY after the native
IGNORE/default-coherence repairs; disabled Task1 storage is now accepted.
Task2 executes as2A readonly source capture/refusal, then2B pure forward and
current-fact inverse, with independent reviews between them. No constructor,
release marker or target version selects this preparation. Both current index
gates (native child-FK scans and physical-parent public-ID lookup) remain
mandatory before activation; the source-shaped writer acceptance does not
claim they are solved. Actual published predecessor binding remains Task3.

### Task2A acceptance

Task2A b6214a28 passed sequential independent SPEC then QUALITY after fixing
caller-specific trigger exemptions and qualifying MAIN/TEMP metadata. The
read-only capture/refusal unit is accepted; Task2B pure forward/current-fact
inverse may now proceed on the same disabled scaffold. This does not bind a
published predecessor, reserve versions, select runtime constructors or resolve
the existing performance gates.

### Task2B acceptance

Pure migration/current-fact inverse c57de7bc passed sequential SPEC then QUALITY
after7022ec1c andb17db9fc. Final70 migration/inverse checks passed; independent
refusal and valid controls preserved serialized database bytes, current BLOB
receipts, legacy ownership and unrelated existing FK diagnostics. This is
disabled scaffold acceptance only. Task3 actual published predecessor binding,
activation, index gates and later real-copy/archived-code/native proof remain
unfulfilled. No constructor, version, caller or production selection was made.
