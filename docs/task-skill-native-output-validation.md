# Native Task Skill source delivery

## Expected and confirmed cause

An immutable read_task_skill result must retain its exact body in model-visible native history. In the saved original OA trajectory the completed MCP structured result held 38,068 characters and its valid SHA256; the following native function_call_output held only 23,741 body characters and a literal truncation marker. Service storage and callback were complete. The loss occurred in native CLI tool-output projection.

## Minimal repair and verification

The existing role command builder passes the native read_task_skill per-tool output_token_limit=30000. No global tool cap, prompt rule, fallback, role table, sandbox, approval policy, provider or session change is included.

Four RED/GREEN command regressions cover Consumer/Audit and cold/resume. The affected role-boundary test file passed 13 tests in the original implementation tree. A fresh 63,055-character anonymous body with different unpredictable middle markers was delivered exactly through the modified builder without a manual budget override. Consumer cold/resume passed; full-schema Audit cold/resume passed in the prospective V3 fixture. Earlier Audit digest and synthetic-summary-format failures remain recorded and were not reclassified.

Read-only independent review accepted the code scope, exact per-turn source equality, cold-before-resume session relationships and validation boundaries. The native probes isolate the fake MCP catalog with ControlledCliConfig; they prove the actual native role-builder path, not the whole production CLI environment. Evidence is retained in the trajectory-optimization branch commits 0461c7a1, 35a9e2cf and e4a80b9b. Full delivery alone did not fix OA business judgment, and the lean OA Skill candidate was not installed.

## Release

Integrated independently on origin/main97ed868b; the same affected role-boundary file passes13 tests on that base and git diff --check passes. The read_task_skill-only repair is present in production7aec844a, PID8951, with its native setting. Health later returned200 in8.05s; History, Attention and public Settings requests timed out20s, so full operational readback is incomplete. The old deployment handle is unavailable; no duplicate deployment was launched. PR31 output-schema enablement, OA Skill changes and receipt policy changes are excluded.

## Installed Skill entry revealed by the new production generation

Production runs25757–25765 used read_skill, not read_task_skill. Root independently decoded the first native turn: complete MCP generic OA content37,887 characters has SHA256 cceeca1bd20857b08be186f57e78e77c96534c3d72d084284460982051bc81b5; model-visible content23,643 characters retains that SHA but no longer matches. Read-only review confirms repeated installed-Skill clipping in the later Consumer and Audit turns. This is a separate entry into the same native output projection, outside the earlier frozen-only setting.

The same native per-tool budget is now passed for read_skill as well. Four role/cold-resume tests failed before this leaf and the complete affected file passes13 after. An anonymous read_skill control uses identical Consumer cold source in both arms. Default budget loses the middle (45,701 of63,055 characters, hash mismatch, marker absent); the modified real role builder passes Consumer and Audit cold/resume, with every63,055-character model output exactly equal to its per-turn source, fresh continuation markers and valid results. No manual candidate budget override, global output setting, prompt/Skill rule or native schema enablement is included. Audit's anonymous probe uses its full native schema to isolate result-format failures; production defaults remain unchanged. Results are in evals/trajectory_optimization/installed_skill_native_budget.*.json. Independent read-only review of all five complete native turns accepts this extension; production publication/readback remain pending.

New production errors are separately preserved:评分完整度、同意人归属、业务阶段与action task_id数值类型问题触发不同Audit返修；25763对象尾随逗号触发非法JSON，25764同session纠正。Source completeness alone does not prove these judgments are fixed. This generation lacks the old fixture's personal subordinate rule; old fixture criteria must not be imposed on it.
