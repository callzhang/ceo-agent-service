# Meeting planner Skill scope comparison

The Meeting planner receives a frozen managed judgment Skill plus operation Skills used to collect minutes and calendar facts. In the inspected native runs, the service had already assembled the source facts before the decision turn, and the planner did not call business tools. This change keeps the full managed rule in Codex input and omits the two completed operation guides. Other runtime routes retain the original full protocol.

The fixed [anonymous read-only cases](../evals/trajectory_optimization/meeting_skill_scope_native.py) compare the current full protocol against the managed-only input on the same latest-main prompt builder, model (`gpt-5.6-luna`), schema, and fictional facts. The [saved results](../evals/trajectory_optimization/meeting_skill_scope_native.v1.json) contain the Skill hashes, prompt hashes, checks, and structured decisions.

| Case | Full prompt | Managed-only prompt | Full checks | Managed-only checks |
| --- | ---: | ---: | --- | --- |
| Personal cross-time-zone meeting | 40,708 chars | 23,293 chars | Pass | Pass |
| Business rollout agreement | 41,608 chars | 24,193 chars | Pass | Pass |
| Mixed business and sensitive personnel content | 41,547 chars | 24,132 chars | Failed sensitive-hint check | Pass |

Each managed-only input saved 17,415 characters, about 42% of its corresponding full input. All six runs exited successfully, produced valid `MeetingAlignmentDecision` objects, made zero tool calls, and emitted no Skill-budget warning. The full-input sensitive result included “人员事项不纳入本次项目群总结,” which disclosed the existence of a personnel matter to the group; the managed-only result discussed only the business assignments.

This is one model run per case, so it establishes a reproducible comparison rather than a statistical quality improvement. The cases contain no Work Profile, real meeting, business MCP, or external action. Focused tests cover frozen-material selection, Codex route input, full-input fallback to Friday, and same-route correction. Production behavior requires a merged release and live readback; none is claimed here.

A later read-only check of complete production Meeting attempt `374592` confirms the
opportunity on a larger input. Its service User prompt had 337,922 characters,
including 23,639 for the three frozen Skills, 100,731 for 58 distinct group
candidates, and 188,422 for the complete meeting source (1,275 transcript
lines). Native Codex also supplied three separate Skill messages totaling
23,474 characters. The exact 23,130-character bodies of those three installed
Skills appeared in both the service prompt and the native messages. The turn
made no tool calls and returned a valid decision; the run was `ready_to_send`
at inspection, which is not a delivery receipt. These are input and output
facts, not a production test of this PR. The source and group evidence remain
intact in this change; their size needs separate evaluation.
