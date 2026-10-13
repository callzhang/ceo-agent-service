# OA frozen Skill input binding

Actual OA Audit350083 first calls read_task_skill and receives a missing-material error, then reads installed sources. The OA scanner reused _task_scheduled_consumer_payload, a helper intentionally dropping skill_materials for Task extraction. The same scanner passes seven selected names/protocol into Consumer/Audit, so those names were not backed by the scheduled snapshot bodies. Current Task source projection remains intentional and is outside this fix.

Regression changes the existing OA enqueue test to require the complete original scheduled wrapper and invoke actual bound read_task_skill for both Consumer and Audit. Before code change it fails because both frozen bodies are absent. The implementation changes only the OA wrapper expression to context.to_payload(). No new audit/authorization/safety policy, special classification, provider call, session change or old-data migration. The first post-fix test run additionally exposes a fixture typo (store.db_path does not exist); corrected to the exact temporary database path.

Preserving frozen bodies is not preloading them into model Task text: normal composition continues to submit the existing short discovery entries, with full bodies available only through the bound read tool. Native applicability/resource and full business acceptance remain separate gates.

Four affected scanner, frozen-source, composition and role-boundary suites pass102 in7.10s; Ruff/diff checks pass. Independent read-only review confirms the one-line scope and actual both-role bound-reader regression, with no code-level blocker. Architecture/runtime documentation updated in the same change. No native model/business replay was used to establish this deterministic delivery mechanism.
