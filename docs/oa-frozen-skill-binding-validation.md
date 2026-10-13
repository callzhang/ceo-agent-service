# OA frozen Skill input binding

Actual OA Audit350083 first calls read_task_skill and receives a missing-material error, then reads installed sources. The OA scanner reused _task_scheduled_consumer_payload, a helper intentionally dropping skill_materials for Task extraction. The same scanner passes seven selected names/protocol into Consumer/Audit, so those names were not backed by the scheduled snapshot bodies. Current Task source projection remains intentional and is outside this fix.

Regression changes the existing OA enqueue test to require the complete original scheduled wrapper and invoke actual bound read_task_skill for both Consumer and Audit. Before code change it fails because both frozen bodies are absent. The implementation changes only the OA wrapper expression to context.to_payload(). No new audit/authorization/safety policy, special classification, provider call, session change or old-data migration. The first post-fix test run additionally exposes a fixture typo (store.db_path does not exist); corrected to the exact temporary database path.

Preserving frozen bodies is not preloading them into model Task text: normal composition continues to submit the existing short discovery entries, with full bodies available only through the bound read tool. Native applicability/resource and full business acceptance remain separate gates.

Four affected scanner, frozen-source, composition and role-boundary suites pass102 in7.10s; Ruff/diff checks pass. Independent read-only review confirms the one-line scope and actual both-role bound-reader regression, with no code-level blocker. Architecture/runtime documentation updated in the same change. No native model/business replay was used to establish this deterministic delivery mechanism.

## Production release

PR69 merged as3e0d962487ada4d12c73e2fc43826439ea9ada50. Formal deploy deploy-aa1f5e7c-6e26-452a-b3d1-28f0a65517a9 succeeded2026-10-12 23:36:59 America/Los_Angeles (2026-10-13T06:36:59Z). Production checkout is clean, PID90772 replaces43583, and the persisted backup exists. Installed scanner carries OA complete scheduled wrapper while retaining Task extraction projection. Health, queues, Attention, History and both current role previews returnHTTP200. Pending and failed work remain visible; endpoint availability does not prove their business completion.

This readback invokes no provider/model and does not reconstruct historical inputs. A future natural OA trajectory must independently confirm frozen reads; old tasks that omitted materials remain unchanged. Automatic CI was still running when the PR merged;102 focused local passes and independent review are the completed code gates, not a full CI success claim. Technical receipt: evals/trajectory_optimization/oa_frozen_skill_binding.release.v1.json.

## Natural post-release source reads

Task404387 Consumer run351096 and Audit runs351101/351109, at2026-10-13 01:01–01:04 America/Los_Angeles, successfully read dingtalk-oa-approval and stardust-oa-attendance-travel-review via read_task_skill. Native outputs39633/8685 characters contain no truncation marker or error; all three sessions return matching frozen content hashes. The Consumer continuation uses the same native session and rereads changed live state, without rereading unchanged Skill bodies.

This closes the missing natural source-delivery evidence for the frozen-reader repair, not external dispatch/business completion. The Consumer initially proposes an OA action, Audit approves, and subsequent state reads yield no_action; action success must be established by its separate immutable receipt. Only two bodies are read here versus seven in older project-case Audits, but source/category differ and no causal read-volume reduction is claimed. Installed-applicability PR67 remains a separate draft. Metadata only: oa_frozen_skill_binding.post_release.v1.json.
