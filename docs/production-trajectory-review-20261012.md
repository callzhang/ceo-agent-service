# Fresh production trajectory review — 2026-10-12

Scope:12 complete native Consumer/Audit turn ranges on tasks404297,386399,386402 after the recorded compact-context deployment. Installed production revision4b38ab8b55568666770f32b0cb385eb4d11db512. The versioned metadata artifact is `evals/trajectory_optimization/production_trajectory_review.current.v1.json`. This review makes no runtime changes or external requests. Private sources, prompts, Profile, message contents and model reasoning are excluded from the artifact; native pointers, hashes, lengths and field-visibility booleans permit local rechecking.

## Expected versus actual

A revised candidate should be assessed against the same facts and the correction that caused the revision. Calendar task404297 instead ran proposal→return→no_action→return→proposal→approve (attempts348815/348820/348824/348826/348828/348830). The first Audit considered the existing clarification sufficient; the second required an updated clarification. This is an observed reversal, not evidence that either business judgment was necessarily correct.

Both first Audits received identical calendar MCP structured results and identical message MCP structured results. Their clipped model-visible message bodies, after removing native timing/chunk wrappers, are also byte-identical: SHA25611bba7191ad6b36f7aa6e06788f049f3fca1967c56ef65dfce9bd6c8acdcbea0. The large message list is clipped, but the critical prior reply's366-character body, creation time and670-character quoted content are complete in both model inputs. Therefore differing provider facts or differing projection of this critical reply cannot explain the reversal. General clipping remains an independent observation, not the proven cause of this case.

Each Audit starts a fresh session. `AuditTurnContext` and its normal orchestrator construction contain the task and current candidate/revision/digest, without a separate previous Audit feedback field. The three long textual fields of the first feedback do not appear verbatim in the second Audit service Task. This verifies the input gap; the candidate may still paraphrase earlier reasoning. It does **not** prove that supplying prior feedback would improve the decision, and does not justify adding an instruction to agree with the previous reviewer.

## Repeated Skill reads

Calendar turns repeatedly read four identical frozen Skills totaling29,485 characters per reading turn. Five of the six examined turns do so; the final Consumer reuses its existing context. Existing draft PR62 tests the narrower frozen-content reuse instruction with same-session feedback, source changes and native compaction. Fresh Audit sessions cannot use a same-session reuse benefit; changing Audit session ownership is outside this review.

OA task386399 first Audit349154 reads seven Skills totaling80,792 characters. Its corrected Consumer349160 and closing Audit349162 read two totaling44,931. The generic OA Skill already directs selection by process category, so another prompt rule is not justified. The extra35,861 characters are a measured difference, not proof that every extra Skill was irrelevant or an assembly dependency. The runtime's selection/discovery input and tool order must establish who selected them before changing delivery.

The initial OA candidate concerned a time-sensitive action that the Audit rejected as stale relative to the current local/Shanghai dates. The Consumer then returned no_action and the final Audit approved. Task386402 was no_action→approve. These persisted outcomes do not establish new external effects; this review did not replay any action.

## Next scoped work

1. Validate the feedback-context hypothesis with a fixed anonymous calendar revision case: same facts, model, current candidate and judgment criteria; compare existing Audit input with input that additionally supplies the previous review as evidence. Evaluate correct business judgment and contradictory correction cycles. Do not add a rule requiring reviewer agreement. Any resulting review-behavior change stays separate from prompt-size changes.
2. For OA, inspect the native Skill-selection input and call sequence to distinguish service selection from agent discovery. Improve a confirmed delivery mechanism; do not repeat the category rule already present in the Skill.
3. Continue independent acceptance of the existing frozen-Skill and compact-discovery drafts. Do not merge them on the strength of these production observations alone.
4. Inspect a fresh actual email and document task through full native inputs, tools, result and persisted outcome. This12-turn sample covers calendar and OA; it is not all-domain acceptance.

No audit policy, authorization behavior, fallback, model route, prompt or production deployment changed in this review. API relay diagnosis remains a separate pending destination approval.

Independent read-only review verified all12 ranges contain task_complete, source and projection hashes, critical-field coverage, normal Audit context construction, and existing OA category rules. The candidates differ (proposal versus no_action); this is not a controlled causal comparison. No root-cause claim was promoted beyond the evidence.
