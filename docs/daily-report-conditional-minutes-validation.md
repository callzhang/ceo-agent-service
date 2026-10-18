# Daily Report conditional Minutes Skill read

Daily Report uses the five already-selected Skills. The prompt first reads the
service facts and scans the group messages. When those messages contain the
meeting follow-up detail needed for the report, it skips reading the Minutes
Skill and minutes source. When the details are missing, it reads the exact
task-frozen `dingtalk-minutes` Skill before any Minutes operation. This changes
the read sequence only; it preserves report inputs, publication, proposal,
review, and execution behavior.

The native comparison in
`evals/trajectory-optimization/daily_report_conditional_minutes.native.v4.json`
uses synthetic data, Codex OAuth, fake read-only MCP results, cold sessions,
and no Work Profile, production data, business MCP, or external actions. Both
baseline and candidate pass the fixed output checks in the no-meeting and
meeting-needs-detail cases. In the first case, the candidate reads service
facts before other Skills, skips Minutes, uses one fewer tool call, and saves
9,170 combined task-input and Skill-body characters. In the second case, it
reads Minutes Skill before searching or reading minutes and preserves the
meeting/source references, report proposal, and wire validity.

Token totals are mixed, so this is not a general token-cost claim: the
no-meeting candidate has 275,820 more summed input tokens and one extra
provider request; the meeting candidate has 812,117 fewer summed input tokens
and one fewer provider request. Cache and request patterns differ. Both pairs
are fixed synthetic cases, not a production acceptance result.

The migration replaces only the exact previous repository-seeded prompt.
Customized scheduled-task prompts remain unchanged. Focused verification:

- `pytest -q tests/test_agent_cron_seeds.py` — 38 passed.
- Ruff and Python compilation checks cover the changed seed module and test.

No live scheduled run, Audit, same-session continuation, report publication,
or production deployment was performed.
