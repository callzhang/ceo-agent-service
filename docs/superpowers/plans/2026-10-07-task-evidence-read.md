# Task Agent original-evidence read interfaces

Derek asked to continue Task Agent input repair. Current Task Agent on-demand implementation is owned by the active project-design-validation task; this companion handles the missing read interfaces only. It does not duplicate Task Agent/Skill/preload changes.

## Evidence and scope

Production Project detail returns at most 20 evidence rows plus pinned citations and indicates has_more; it has no follow-on evidence page route. Project detail may legitimately have zero evidence/context even when older task sources mention the Project. Exact Signal sources exist but have no individual Console read route. Do not claim this companion fixes source discovery or all model coverage.

## One implementation task

1. Add failing real-Store/TestClient regressions: GET /api/console/tasks/projects/{project_id}/evidence?page=1&page_size=20 follows all persisted BusinessProjectEvidence references, stable pagination (newest window first using existing Store order), no overlap or omission across >20 rows, truthful next_cursor/has_more/total. Items are evidence refs (project_id/signal_id/created_at), not duplicate full bodies. Existing Project detail behavior stays intact. Unknown Project404, existing empty Project200, invalid page/size422.
2. Add failing GET /api/console/tasks/signals/{signal_id} regression. Return the original complete BusinessTaskSignal as item, including immutable source_document_id, source_ref/source_time/source_type/author and exact evidence_text. Long JSON/raw body with decisive uncited middle text is byte-for-byte original, not the2048 projection or normalized display value. Unknown Signal404. Two same-ref versions remain distinct. Read-only calls do not mutate business tables.
3. Implement minimally using existing read_snapshot, get_business_task_signal, list_business_project_evidence, typed ApiListEnvelope/ApiItemEnvelope and ApiListMeta. No new Store/schema/migration, ranking/search, write API, permission/authentication or native transport. No speculative compatibility/fallback branch. Do not change any existing payload/endpoint behavior.
4. Run new focused regressions plus existing Project detail/Console Task API checks and Ruff/diff check. Update architecture/runtime with endpoint behavior in the same commit; no private source names/text in Git.
5. Independent scoped spec/quality review, PR/CI as appropriate, formal deployment and native HTTP readback of exact original stored source plus queue/health status. Source-read API success is not proof of Task Agent tool use or business coverage. The active on-demand task must still evaluate real reads, source/project attribution and domain output under its frozen cases.

## Execution ledger

- 2026-10-07: isolated managed worktree at origin/main2cfd02f5; file claims recorded. Production baseline7e0e9654. No runtime changes yet.
- 2026-10-07: RED `tests/test_task_evidence_read_api.py` = 4 failed on missing routes (expected 404). GREEN = 4 passed; focused compatibility = 31 passed (4 new + 27 existing Project detail/Console Task API checks) in 138.58s; Ruff passed on the three touched Python files and `git diff --check` passed. Added only typed read envelopes/helper and the two GET routes; no Store/schema/domain mutation, push, deploy, or production write.

- 2026-10-07: independent scoped spec/quality review of863de4b7 clean; same deployed-source and domain/read boundaries confirmed. Merged upstream501d7c3c tests-only Skill assertion correction without altering this API implementation. Final CI, merge/deploy and native production source readback remain pending.
- 2026-10-07: PR #22 full CI run37603443031 reached 10,781 passed, 88 skipped and 44 deselected with one failure: the existing conflicting-chat registry test compared the complete Project summary while a legitimate new-evidence write advanced its activity `updated_at` by one second. A deterministic SQLite clock reproduced RED as 1 failed with only `2026-10-07 10:10:30` -> `10:10:31` different. The corrected test compares every summary field except `updated_at` and separately requires that exact controlled advance; its card/source evidence assertion remains. GREEN was 1 passed in1.80s; the complete multisource module was151 passed in18.78s; Ruff and `git diff --check` passed. This is test/evidence correction only, with no runtime, Store, schema, domain or policy change; a new full CI run remains required.
