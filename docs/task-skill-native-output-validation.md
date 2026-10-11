# Native Task Skill source delivery

## Expected and confirmed cause

An immutable read_task_skill result must retain its exact body in model-visible native history. In the saved original OA trajectory the completed MCP structured result held 38,068 characters and its valid SHA256; the following native function_call_output held only 23,741 body characters and a literal truncation marker. Service storage and callback were complete. The loss occurred in native CLI tool-output projection.

## Minimal repair and verification

The existing role command builder passes the native read_task_skill per-tool output_token_limit=30000. No global tool cap, prompt rule, fallback, role table, sandbox, approval policy, provider or session change is included.

Four RED/GREEN command regressions cover Consumer/Audit and cold/resume. The affected role-boundary test file passed 13 tests in the original implementation tree. A fresh 63,055-character anonymous body with different unpredictable middle markers was delivered exactly through the modified builder without a manual budget override. Consumer cold/resume passed; full-schema Audit cold/resume passed in the prospective V3 fixture. Earlier Audit digest and synthetic-summary-format failures remain recorded and were not reclassified.

Read-only independent review accepted the code scope, exact per-turn source equality, cold-before-resume session relationships and validation boundaries. The native probes isolate the fake MCP catalog with ControlledCliConfig; they prove the actual native role-builder path, not the whole production CLI environment. Evidence is retained in the trajectory-optimization branch commits 0461c7a1, 35a9e2cf and e4a80b9b. Full delivery alone did not fix OA business judgment, and the lean OA Skill candidate was not installed.

## Release

Integrated independently on origin/main97ed868b; the same affected role-boundary file passes13 tests on that base and git diff --check passes. Production deployment and readback are pending. PR31 output-schema enablement, OA Skill changes and receipt policy changes are excluded.
