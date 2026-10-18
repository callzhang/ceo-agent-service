# Meeting planner Skill scope comparison

The Meeting planner receives a frozen managed judgment Skill plus operation Skills used to collect minutes and calendar facts. In the inspected native runs, the service had already assembled the source facts before the decision turn, and the planner did not call business tools. This change keeps the full managed rule in Codex input and omits the two completed operation guides. Other runtime routes retain the original full protocol.

The fixed [anonymous read-only cases](../evals/trajectory_optimization/meeting_skill_scope_native.py) compare the current full protocol and native Skill catalog against the managed-only input and filtered catalog on the same prompt builder, model (`gpt-5.6-luna`), schema, and fictional facts. The [saved results](../evals/trajectory_optimization/meeting_skill_scope_native.v2.json) contain the Skill hashes, prompt hashes, checks, and structured decisions. The earlier [v1 result](../evals/trajectory_optimization/meeting_skill_scope_native.v1.json) filtered the native catalog in both arms, so it isolated service-text reduction but did not represent the full production baseline.

Acceptance requires a shorter submitted input, a valid complete decision in every case, the same justified recipient and cross-time-zone or business facts, and no personnel hint in the mixed-content group message. The scoped regression tests must keep the full frozen input on non-Codex routes and preserve the decision, audience and delivery contracts. A failed check stops this change rather than prompting a special-case rule.

| Case | Full prompt | Managed-only prompt | Full checks | Managed-only checks |
| --- | ---: | ---: | --- | --- |
| Personal cross-time-zone meeting | 40,708 chars | 23,293 chars | Pass | Pass |
| Business rollout agreement | 41,608 chars | 24,193 chars | Pass | Pass |
| Mixed business and sensitive personnel content | 41,547 chars | 24,132 chars | Failed sensitive-hint check | Pass |

Each managed-only service input saved 17,415 characters, about 42% of its corresponding full input. All six v2 runs exited successfully, produced valid `MeetingAlignmentDecision` objects, and made zero tool calls. The native baseline emitted a Skill-description budget warning in all three cases; the filtered candidate emitted none. The full-input sensitive result included a hint that a personnel topic existed, while the managed-only group message discussed only the business assignments. The checks found no missing cross-time-zone fact, aligned business conclusion, recipient, or assignment in the candidate cases.

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
