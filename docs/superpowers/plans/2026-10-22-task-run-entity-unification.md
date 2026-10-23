# Task / Run Entity Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Derek selected Luna. Execute Tasks sequentially; independently review SPEC and QUALITY per task, then final branch review. Steps use checkbox syntax.

**Goal:** Execute Derek's approved Task/Run entity design through copy/native/current-data inverse validation and production publication.
**Architecture:** Executable Task and logical Run are single owners; input, result, invocation, occurrence and domain facts are explicit children. One atomic runtime/schema cutover; no old-schema runtime fallback.
**Tech Stack:** Python>=3.12, SQLite JSONB, pytest, React/TypeScript, native Codex CLI, app.deploy.
**Spec:** `docs/superpowers/specs/2026-10-22-task-run-entity-unification-design.md` (approved in chat before implementation).

## Global Constraints

- Read canonical AGENTS and claim owned paths. Preserve unrelated work; new worktree based on current origin/main.
- User-selected Luna for implementers/reviewers; bounded briefs, no worker-spawned helpers, no production/provider mutations by workers.
- No fabricated business fields or facts; no audit/retry/authorization/cancellation policy changes.
- Preserve source NULL/types, current-v-claimed inputs, versions, multi-key identity, role/revision/attempt cardinality, business outcomes/provenance/receipts.
- No duplicated trajectories, old runtime type dispatch or compatibility DTO wrappers; explicit migration/inverse metadata only.
- Validate newly written current facts through inverse and genuine old-code cold read/write/reforward before publication; strict fake outbound.
- Empty-scan aggregation is explicitly approved lossy history retirement; inverse retains aggregate range/count, not invented ticks.
- Source-backed complete DDL/field and index contracts must be reviewed before new business writes. Net packed capacity must decline including technical metadata.

## Review Focus

1. Same parent/revision key collisions and Task merging: preserve full source input identity and multiple business keys (Task2).
2. New current input while an execution holds old claimed input: CAS/deferral/cancellation/version contracts remain exact (Task2/5).
3. One Run, several invocations, provider ACK before settlement: no synthetic calls or resend (Task3/5).
4. Aggregated historical instant on restart/config edit/manual trigger: range-aware dedupe, no accidental replay (Task4/5).
5. Fresh source changes, original FK orphan and unavailable trajectory: exact preflight/exception accounting, no fake repair or reconstructed receipts (Task1/5/6).

## Task 1: Freeze field ownership, complete DDL, retirement and indexes

**Files:** Create `app/task_run_entity_definitions.py` (pure declarative target/mapping, not activated), `tools/inspect_task_run_entities.py` (read-only metadata/field/index inspector), `tests/test_task_run_entity_definitions.py`, `docs/task-run-entity-field-map.json`, `docs/task-run-entity-target.sql`, `docs/task-run-entity-retirement.sql`, `docs/task-run-entity-validation.md`. Update dated-state wording in `docs/business-model-execution-progress.md`, `docs/architecture.md`, `docs/runtime-mechanism.md` and own claim. Do not change existing runtime writers or frozen source declarations.
**Consumes:** approved spec; current Task20/lifecycle/logical Run/Action Entity declarations and actual caller identity constraints.
**Produces:** exact `task_run_entity_objects() -> dict[str,str]`, `task_run_field_map() -> tuple[dict,...]` and complete SQL fixtures; every logical/physical retained source field is mapped exactly once or explicitly derived/retired. Map entries include source kind/field, physical or logical owner, target table/field/expression, retirement reason, SQL type/NULL contract and caller evidence. Target names/columns fixed in those artifacts for Tasks2–4. No undefined destinations. No activation.

- [ ] Write tests failing on missing target, duplicate/missing source field, unknown field accepted, missing full input key/multi-key relation, real unique/FK semantics and target fixture drift.
- [ ] Verify RED, implement complete declarations/inspector and schema artifacts with source-backed domain tables and Run result/selection/execution ownership; explicitly decide every target field, primary/public identity and inverse metadata strategy. Decisions refine approved source-backed contract, never add business meaning.
- [ ] Create target in memory and execute all DDL/keys/triggers; run focused baseline and new tests; inspect current production metadata read-only or verified backup without exposing bodies/secrets.
- [ ] Fix documentation timing/backup wording, record exact DDL coverage and baseline/test/capacity boundaries, commit own changes/report.

## Task 2: Task/input/business-result migration and current runtime

**Files:** new `app/task_run_entity_storage.py` and explicit migration module; existing Task/Store/Email/Workbench/dispatcher producers/readers, focused tests and behavior docs. Exact target identifiers come from Task1.
**Consumes:** Task1 field map, target DDL and identity contract.
**Produces:** one current Task create/read/CAS update/claim owner, immutable full-key task_inputs, task_business_keys, Task result and explicit domain owners. Migration/inverse functions operate caller-owned transactions/copies and preserve current facts; old helpers become migration-only, not runtime aliases. Complete code caller list before changing.

- [ ] RED tests for nine work kinds, source-absent NULLs/types, full input key collision, multi-key merging, current/claimed refresh, numeric versions and Meeting ready_to_send exclusion.
- [ ] Implement forward/inverse Task ownership and atomically switch creation/claim/renew/release/refresh/recovery/public readers; remove old runtime type matrix, generated-name helpers and duplicate metadata.
- [ ] Focused relevant tests incl Workbench/Email/feedback/domain workflows; update docs in same commits, report/commit.

## Task 3: Logical Runs, invocations and adopted results

**Files:** current Run/Store/runtime router/reviewed-candidate/History/dispatcher callers, explicit conversion module, focused tests and docs. Use exact Task1 target contract; preserve Task2 interfaces.
**Consumes:** Task1 Run/result/selection/execution/identity map and Task2 new parent identities.
**Produces:** one logical Run owner, runtime_attempts child (including NULL Run operations), Run-owned adopted results/selection/progress, direct Action/receipt references; retired execution aliases and source-only runtime branches. No empty persisted body DTOs, no changed in-flight result validation.

- [ ] RED one Run/multiple attempts, no-invocation histories, exact Consumer/Audit/revision lineage, repeated terminal writes, candidate digest/selection, receipt binding, historical missing Task/source and native-unavailable result retrieval.
- [ ] Implement migration/inverse and all runtime writers/readers; preserve every independent server failure event and business-attempt identity. Update immutable reference edges atomically, no opaque-string remapping.
- [ ] Focused lifecycle/native recovery/History/result tests and docs, report/commit.

## Task 4: Scheduled occurrence owner and empty-scan aggregation

**Files:** schedule occurrence storage/declarations, agent_cron commands/consumer/scheduler, existing scheduled Store/dispatcher/History/frontend readers and focused tests/docs.
**Consumes:** Task1 occurrence DDL/index contract and source-backed command metrics; Task2/3 actual execution link owners.
**Produces:** narrow occurrence owner, pending-only dispatch/full-state claim indexes, typed transient command counts, transactional safe aggregation and covered-instant dedupe. No persisted guessed flag or retry/audit policy change.

- [ ] RED concurrent claims/ties/leases/metrics plans; exact zero-v-positive/multi-counter/unknown/manual/failed/active/referring outcomes; config/command boundary and chronological continuity; restart into aggregated range; inverse aggregate representation.
- [ ] Move all occurrence paths; implement structure-based new empty results and narrowly verified historical contracts; preserve newest identity/first time/count and all protected references. Cutover History/frontend range display with matching tests.
- [ ] Prove public query plans and actual bounded timing with adversarial terminal population, update docs, report/commit.

## Task 5: Full production-copy/current-data inverse/native/UI acceptance

**Files:** new `tools/validate_task_run_entity_copy.py`, native substitute-only integration fixture, focused tests, `docs/task-run-entity-validation.md`, acceptance matrix. Only task-owned temporary copies/code outside durable data.
**Consumes:** Tasks1–4 current implementation, fresh verified production source, pinned actual predecessor code.
**Produces:** forward/new-write/inverse/genuine predecessor cold-read-write/reforward/packed-capacity proof, actual CLI and UI evidence with strict fake effect logs. No backup restore masquerading as inverse; approved aggregation retired events excluded explicitly, not silently normalized.

- [ ] Validate original source/schema/allocator/FK exception contracts and entire necessary fact/reference inventory after forward.
- [ ] Write new Tasks/inputs/multi-key/revisions/attempts/results/receipts/aggregates; inverse; old-code public reads and harmless writes; reforward; compare SQL types, provenance, reference semantics, state and sequences.
- [ ] Native Luna/high Consumer→Audit approve and revision, retry/resume and ACK settlement interruption: fake provider exactly expected count/no duplicates. Browser queues/History/Attention/input/Run/invocation/feedback/receipt flows on copy server with no background workers/provider access.
- [ ] Same-source packing baseline/candidate, gross/index/inverse-map/aggregation decomposition; net decline mandatory. Record missing/failed evidence honestly, complete report/commit.

## Task 6: Integration, review, release and closeout

**Files:** approved runtime changes and release integration; architecture/runtime/progress/acceptance/changelog; explicit migration publication hook; no direct production edits.
**Consumes:** all previous accepted reports and tests, latest origin/main and actual production predecessor.
**Produces:** reviewed/pushed runtime commit, standard app.deploy release and live readback, newest verified backup, accurate final report.

- [ ] Reconcile origin/main safely; recheck source/schema, refreeze/rerun affected copy/native/inverse proof when changed. Final branch SPEC/QUALITY review and one fix wave/scoped review per SDD.
- [ ] Publish through explicit approved schema migration and normal app.deploy quiet window only after backup verified and net-capacity/acceptance gates pass. Preserve unrelated work, never skip quiet or use launchctl manually.
- [ ] Read PID/health/schema/queues/Attention/History/details/receipts; distinguish natural production outcomes from substitute tests. Update total matrix/rulings and release docs, commit/push.
- [ ] Cleanup only exact owned temporary artifacts after evidence durability; policy-blocked deletion reported, never bypassed. Keep newest verified formal backup and native trajectories. No claiming whole program or Phase3 complete.


### Task3C1B Review identity correction (2026-10-22)

C1B preserves source Review `id` as the real native Run-owned Review child key and stores the Audit Run separately as unique `audit_run_id`. `run_adopted_results.predecessor_review_id` references Review `id`; selections/progress continue to reference Audit Run owner IDs. Candidate/review body and digest remain unchanged. Frozen source evidence covers 23 linked predecessor bodies, all matching source Review IDs and differing from Audit Run IDs, with no unresolved references. This is an unactivated schema/Store correction; source-copy conversion and runtime activation remain later gates.

### Task2D2A Meeting delivery identity prerequisite

Before changing the Meeting creator, store the exact pre-existing delivery key in `task_business_keys` against the canonical Task. Add an exact Task-kind-gated reader and the explicit forward/inverse identity handoff; keep the `business_object_tasks` field-map source semantics Reply-only. Verify that key row IDs survive two JSON inverse/reforward cycles and an intervening authentic old-source key insert, and that the shared old Tasks allocator reserves those IDs. The source public ID embedded in the external identity remains provenance, and the sender continues receiving the unchanged key. This task leaves the Meeting writer, Action creation and service constructor unchanged; Task2D2B performs that cutover.
