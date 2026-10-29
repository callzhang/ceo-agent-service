# Final model residue retirement implementation plan

> **For agentic workers:** Use subagent-driven-development, SPEC then QUALITY per unit. Do not repeat completed five-batch work.

**Goal:** Remove the audited unused entrypoints and runtime historical-structure selection while preserving exact historical facts, provenance and receipts.
**Architecture:** Current positive declarations and direct owner reads remain authoritative. Frozen source construction belongs to tests/explicit tools. Historical missing authorization is retained as missing and never converted into permission.
**Tech Stack:** Python/SQLite/pytest/Ruff and existing React/Vitest; standard gated app.deploy.

Baseline: isolated branch codex/retire-final-model-residue-20261029 at4b1ec3e66. Last production read-only834468271 / Store28.5 / Email54,463exact objects/FK[]. Refresh before release.

## Unit1 — no-DDL native execution and current projection cleanup
- [x] Delete execution_owner_storage.update_plan_execution (no callers); move create_plan_execution and its imports into tests/support frozen source fixture; keep native create/update functions unchanged.
- [x] Make current task_delivery_projection and feedback_projection accept only current fields; move variant-dependent source projections into explicit historical modules and update only actual migration/test consumers. Preserve current projection SQL byte-for-byte.
- [x] Remove waiting_confirmation from Workbench current enum/terminal transition map and frontend type/status rendering/API count fixtures. Current production zero rows; retired confirmation workflow remains absent. Do not reinterpret an existing status or add compatibility fallback.
- [x] Run narrow baseline and modified tests: test_action_entity_schema_release.py, test_workbench_confirmation_retirement.py, affected Workbench API tests and frontend API/presentation tests in bounded processes. Verify current full schema hash unchanged. Update exact behavior paragraphs in architecture/runtime.
- [x] SPEC then QUALITY; commit only unit-owned files.

## Unit2 — exact Email field ownership and paired release
- [x] Freeze actual Store28.5/Email54 source SQL and typed field map. Retire classification legacy_processed_without_plan and plan legacy_serialization_pre_v16 only after source assertions prove all values0. Keep all other plan facts, especially null authorization snapshots, unchanged.
- [x] Remove repeated stable_message_identity from completed classification input JSON; scalar identity remains owner. Remove fixed channel column only after all consumers derive existing Email contract without fallback; preserve original input hashes/byte counts, provenance and compact-state fact.
- [x] Add explicit forward/current-data inverse with exact declarations; inverse reconstructs only proven constant/duplicate facts. Unknown/nonneutral source must reject before modification. No historical raw input reconstruction from guesses.
- [x] Current-data copy forward/new native writes/inverse/real old-code cold read/write/reforward; compare typed facts/identity/water/FK. Markers/manifest/SQL artifacts update together.
- [x] SPEC then QUALITY and focused Email storage/reader/compaction tests with fake providers.

## Unit3 — separately scoped retired Email startup lifecycle
- [ ] Audit direct_v1 inventory and skip/fail entrypoints; current producer writes audited_v2. Source preflight must report any direct_v1 residue rather than silently changing it.
- [ ] Remove retired direct_v1 startup scan/authority inventory gate and dedicated DAO/DTOs; remove one-off persistence-error recovery rewriting from normal worker startup. Preserve current audited_v2 queue, recovery, retry, authorization and provider-effect checks. Keep original terminal history.
- [ ] Separate code/tests/docs/commit from unrelated cleanup, with fake inventory/account/model/worker regression covering current consumer start and all preserved recovery outcomes. SPEC then QUALITY.

## Unit4 — historical category, plan and OA history read ownership
- [ ] Locate complete current source/consumer contracts before edits. Keep historical reserved category values and null authorization as facts; never remap category, fabricate snapshots or make old unbound plans executable.
- [ ] Separate current business configuration validation from stored fact reading using existing fields/owner; remove old schema-selector/context compatibility where equivalent current typed read can prove unchanged identity and display.
- [ ] For OA readback formats, retain exact original provider receipts; use authoritative current receipt/result references only when exact matching proves equivalence. Do not invent fields, result classifications or new success/review policy. Any unprovable retirement remains an explicit evidence issue, not silent deletion.
- [ ] Current/old-format history equivalence, identity mismatch and failure assertions run against fakes; SPEC then QUALITY.

## Final integration and publication
- [ ] Check final deleted-symbol closure, direct callers, dynamic imports, fixed schema and preserved source fixtures. No added legacy aliases or runtime fallback.
- [ ] Current-data roundtrip and real old-code cold write proof; focused tests only,2GB/10min; full gate only standard serialized deployment. No real external test sends.
- [ ] Merge latest main normally, deploy exact pushed commit through gate/quiet/verified-backup/rollback. No skip-quiet authorization implied.
- [ ] Read back installed SHA/PID/health/queue/Attention/History/affected Workbench+Email/OA details/schema/FK and exact historical receipts.
- [ ] Verify then clean this workflow's temporary copies and backups with exact ownership/generation guard; update concise evidence/claims and push documentation without another deploy.
