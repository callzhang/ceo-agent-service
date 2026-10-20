# Runtime Context native tool-name validation

## Scope and evidence

Recent complete Codex Consumer/Audit trajectories include an empty `ALL_TOOLS` lookup for dotted `agent_cli.read_*` names, followed by a broader lookup and a successful native `mcp__agent_cli__read_*` call. The natural OA task 416071 has both role trajectories: Consumer runtime attempt 387146 and Audit runtime attempt 387153. Two other recent fresh Consumer attempts (387112 and 387084) show the same empty dotted lookup. In a bounded sample of 35 recent completed role attempts, four of 32 fresh attempts showed it. These observations identify a service-to-CodeMode naming mismatch, not a failed business decision.

The service's Runtime Context previously listed declared Codex tools as `server.tool`, although the callable CodeMode tool names have the form `mcp__server__tool`. The change only renders the declared Codex list using the native spelling. Claude's existing list spelling, declared tool set, command, sandbox, approval, session, action and business contracts remain unchanged.

Task Skill discovery still uses `agent_cli.read_skill(...)` and `agent_cli.read_task_skill(...)` as service namespace notation. Both dotted notation and the native list occurred in the saved natural inputs, so the natural trajectory alone cannot attribute the empty lookup to one section. This change does not claim to eliminate every dotted reference or guarantee fewer tool calls. Inspect a post-release fresh Consumer/Audit trajectory before considering another prompt change.

## Finite acceptance

1. A focused regression must fail on the old Codex Runtime Context and pass when the declared list contains the exact `mcp__agent_cli__...` and `mcp__exa__...` names. Claude retains its previous representation. Undeclared tools do not appear.
2. An anonymous fixed native case must complete with a valid tool result using the native name, without an empty dotted catalog lookup. Compare the prior and changed labels under the same tool, task and model. A synthetic case cannot prove natural latency or call reduction.
3. The affected local tests, lint and independent review must pass. After normal merge and quiet deploy, read back version, PID, health, queues, Attention, History and Settings preview. Do not replay a business task to create acceptance evidence.

## Verification so far

- The focused Runtime Context test failed on the old renderer and then passed with the implementation. `tests/test_runtime_prompt_context.py`: 20 passed.
- `tests/test_prompt_preview.py`: 14 passed on the first run; one expected-name assertion was updated, and that case passed independently. No prompt assembly or business rules changed.
- In a paired anonymous Codex OAuth case, both the dotted and native label variants completed with one native read call, the expected synthetic marker, zero empty catalog lookups and exit 0. This shows the native label works and shows no measured reduction in that simplified case.

Production acceptance is pending merge and deployment. The first natural post-release cold role trajectory is the measurement for whether the empty dotted lookup recurs.
