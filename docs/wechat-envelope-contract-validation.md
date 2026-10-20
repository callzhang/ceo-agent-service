# WeChat first-turn AgentEnvelope contract

## Acceptance boundary

The dedicated WeChat decision runner must return a Pydantic-valid `AgentEnvelope` on the first turn for both a no-reply message and a message that merits a reply. The resulting `CodexDecision` must retain the corresponding `no_reply` or `send_reply` action. WeChat delivery remains a separate persisted service step. The change must not alter the shared Consumer/Audit prompts, native output-schema setting, configured routes, tools, session binding, result validation, or retry budget.

## Complete trajectory and cause

Production WeChat Consumer run `386575` (2026-10-20) has a complete native Codex OAuth session. Its first Developer instruction named `AgentEnvelope` but gave no field shape. The Agent read the current WeChat Skill, then searched local Skill files for `AgentEnvelope` without finding it. Its first result was a top-level `mode/reason` object, so runtime attempt `386576` failed result validation. A same-session correction supplied the full schema and attempt `386577` returned a valid `no_action` envelope. The resulting task completed with no reply; no WeChat send was inferred from process success. Since 2026-10-01, eight result-validation-failed attempts occurred among 20 WeChat Consumer runs. Native files for the seven older failures are unavailable here, so they establish recurrence of the failure class, not this exact cause in every run.

The WeChat path uses `WechatDecisionRunner`, separate from the general Consumer/Audit prompt composer. Its Developer instruction had only “Return ... AgentEnvelope JSON”; Codex native output-schema is disabled on this path, and the initial Task likewise named the envelope without listing its fields. The repair prompt already works but adds about 9,600 characters and a second model turn. The root fix is to give this entry point one compact, valid envelope example and the field changes for a reply. Full Pydantic validation remains authoritative.
The initial WeChat Developer instruction grows from 252 to 624 characters (372 added), only for this task entry point.

## Focused verification

The new first-turn contract tests failed before the edit and pass after it. In an anonymous, read-only Codex OAuth comparison using `gpt-5.6-luna`, low effort, identical synthetic facts and no business MCP, the original Developer instruction produced no parseable `AgentEnvelope` for either no-reply or reply. The candidate produced valid envelopes on the first turn: `no_action/no_reply` with empty reply text, and `reply/send_reply` with nonempty reply text. `parse_codex_json` accepted both. No business tool call occurred. The CLI emitted the same Skill-description budget warning in both arms; it did not change the fixed-case facts. This is a bounded model comparison, not a population reliability estimate or a live WeChat outcome.

Focused pytest: `tests/wechat/test_decision_runner.py tests/wechat/test_consumer.py tests/test_codex_decision.py` yielded 87 passed and one unrelated failure. `test_corrected_generation_replaces_unsent_wechat_delivery` directly executes `UPDATE tasks SET status=...` against the current compact Tasks schema, where `status` is no longer a physical column. It uses `FakeCodexRunner` and does not execute the changed Developer instruction. Ruff on the two changed Python files and `git diff --check` pass. No full suite was run beside the live service.

Release and production readback: pending.
