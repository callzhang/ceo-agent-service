# Runtime Context native tool-name validation

## Scope and evidence

Recent complete Codex Consumer/Audit trajectories include an empty `ALL_TOOLS` lookup for dotted `agent_cli.read_*` names, followed by a broader lookup and a successful native `mcp__agent_cli__read_*` call. The natural OA task 416071 has both role trajectories: Consumer runtime attempt 387146 and Audit runtime attempt 387153. Two other recent fresh Consumer attempts (387112 and 387084) show the same empty dotted lookup. In a bounded sample of 35 recent completed role attempts, four of 32 fresh attempts showed it. These observations identify a service-to-CodeMode naming mismatch, not a failed business decision.

The service's Runtime Context previously listed declared Codex tools as `server.tool`, although the callable CodeMode tool names have the form `mcp__server__tool`. The change only renders the declared Codex list using the native spelling. Claude's existing list spelling, declared tool set, command, sandbox, approval, session, action and business contracts remain unchanged.

Task Skill discovery still uses `agent_cli.read_skill(...)` and `agent_cli.read_task_skill(...)` as service namespace notation. Both dotted notation and the native list occurred in the saved natural inputs, so the natural trajectory alone cannot attribute the empty lookup to one section. This change does not claim to eliminate every dotted reference or guarantee fewer tool calls. Inspect a post-release fresh Consumer/Audit trajectory before considering another prompt change.

## Finite acceptance

1. A focused regression must fail on the old Codex Runtime Context and pass when the declared list contains the exact `mcp__agent_cli__...` and `mcp__exa__...` names. Claude retains its previous representation. Undeclared tools do not appear.
2. An anonymous fixed native case must complete with a valid tool result using the native name, without an empty dotted catalog lookup. Compare the prior and changed labels under the same tool, task and model. A synthetic case cannot prove natural latency or call reduction.
3. The affected local tests, lint and independent review must pass. After merge and repository deployment, read back version, PID, health, queues, Attention, History and Settings preview. Do not replay a business task to create acceptance evidence.

## Verification so far

- The focused Runtime Context test failed on the old renderer and then passed with the implementation. `tests/test_runtime_prompt_context.py`: 20 passed.
- `tests/test_prompt_preview.py`: 14 passed on the first run; one expected-name assertion was updated, and that case passed independently. No prompt assembly or business rules changed.
- In a paired anonymous Codex OAuth case, both the dotted and native label variants completed with one native read call, the expected synthetic marker, zero empty catalog lookups and exit 0. This shows the native label works and shows no measured reduction in that simplified case.

## Production readback, 2026-10-20

PR #177 merged as `30dca89f4`. Derek authorized `--skip-quiet-wait` after repeated natural-idle timeouts. The first code deployment loaded `7021ce675`, then exposed an unrelated startup unsubscribe-receipt SQL ambiguity in the background worker. A separate fail-first repair, `b6f87c14a`, qualified task fields in that joined update; 309 focused tests and Ruff passed. The repository deployment returned `deployed 61370307 -> b6f87c14`.

Production readback on `b6f87c14a`: supervisor PID 11652 and its service child remained running, `/healthz` returned OK, Reply failed count was 6, Work and Meeting had no processing or failed items, Attention had 4 groups and History had 36,534 entries. The nine older Reply failures surfaced during the first restart were eight DingTalk tasks and one scheduled task; after the worker started, they projected `done` with `reconciled_from_failed`. They were not email unsubscribe tasks. No Reply task created after 09:00 UTC became failed, and no processing Reply was older than five minutes at the checked snapshot. Settings Prompt Preview for the Codex OAuth route showed `mcp__agent_cli__...` in both Consumer and Audit Runtime Context and no `agent_cli.` listing in that section. The preview is evidence of rendered service input, not of a natural Agent tool choice.

An independent retrospective review approved both diffs with no blocking findings. For PR #177, eight before/after render comparisons covered Consumer/Audit, OA/general and Codex/Claude; only the Codex callable names changed. For `b6f87c14a`, an isolated probe reproduced the original SQL ambiguity, then verified six terminal outcomes, six nonmatching cases, preserved task and receipt fields, and an idempotent second pass. The probe did not replay provider actions.

At this initial readback, natural post-release trajectories were still needed to measure whether an empty dotted catalog lookup recurred or tool-call count changed. No business task was replayed for the release check.

## Natural post-release trajectory readback

Sixteen complete Codex OAuth Consumer/Audit native turn ranges after the code release were checked against their saved session files. The ranges contained twelve `ALL_TOOLS` catalog queries and **zero searches for dotted `agent_cli.read_*` names**. This is bounded evidence that the original naming mismatch did not recur in these turns; it does not establish a population-wide call reduction.

Consumer run 387735 and Audit run 387740 for the same ordinary message task still made an empty catalog lookup. The Consumer first tried `tools.mcp__agent_cli__read_task_skill` inside `functions.exec` and received `TypeError: ... is not a function`; the Audit queried the catalog and received `[]`. Both then completed the selected Skill and action-contract reads through native MCP calls. Their role command config uses `mcp__agent_cli` as a direct-only namespace, which is callable natively but absent from CodeMode's nested catalog. This is a different transport-selection issue from the dotted-name mismatch.

An extra Runtime Context line explaining direct-only transport passed a fail-first rendering test, but two anonymous fixed Codex OAuth comparisons showed **no behavioral improvement**: both old and candidate prompts performed exactly one native Skill read in the simple case, and exactly two Skill reads plus one action-contract read in the production-shaped case, with no empty catalog lookup in either arm. The candidate prompt change and its test were withdrawn; no code, tool declaration, route, permission or business action changed. Reconsider only with a fixed case that reproduces the wasted catalog path and shows its removal without business regression.
