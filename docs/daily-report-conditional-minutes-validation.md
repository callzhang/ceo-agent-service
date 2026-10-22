# Daily Report conditional Minutes Skill read

The earlier v4 comparison is historical only: it used the retired
`daily_report_facts` interface and does not validate the current scheduled
context and service activity path. The v5 comparison below uses the current
task prompt and current read sequence.

The seed tells Consumer to read scheduled context, service facts and group
messages first. It reads the task-frozen `dingtalk-minutes` Skill and minutes
summary only when the meeting details needed for the report remain missing.
The five task-bound Skills remain selected; only the unnecessary read is
skipped. Audit, report sections, document readback, message proposal and system
delivery behavior are unchanged.

The native comparison in
`evals/trajectory_optimization/daily_report_conditional_minutes.native.v5.json`
uses Codex OAuth, the current prompt composer, the current
`read_scheduled_context` / `read_service_activity` input path, anonymous
synthetic facts, and fake read-only tools. It does not use Work Profile,
production records, business MCP, or external actions. All four Consumer
outputs are valid typed proposals with one pending direct-message action.

- **No meeting needing more detail:** baseline reads all five Skills before
  facts; candidate reads four, skips `dingtalk-minutes`, and reduces tool calls
  from 17 to 16. Both create and read back the report and prepare a proposal.
- **Meeting needs detail absent from chat:** baseline reads the Minutes Skill
  before facts, then lists and reads the fake minutes source. Candidate reads
  facts and chat first, then reads the frozen Skill before listing and reading
  the fake meeting summary. Both reports and proposals include the synthetic
  small-pilot decision and preserve the follow-up details; the candidate uses
  18 calls versus the baseline's 20.

These fixed pairs support the conditional read order and show one fewer tool
call when Minutes are not needed and two fewer when they are needed; they do
not establish a token-cost reduction because candidate token telemetry was
unavailable. Production
trajectory 15866 independently shows the same unnecessary order: five Skills
were read before facts; `dingtalk-minutes` was read, but no minutes operation
was needed. Its Audit correctly returned the first proposal because it merged
`decision` and `push` items into one intervention count; the Consumer revision
separated them and Audit approved it. That business correction is preserved.

The migration replaces only the exact previous repository-seeded prompt;
operator-customized task prompts remain unchanged. Focused test verification:

- The full seed test file reported 49 passed and one failed because its
  assertion expected the earlier wording. After updating that assertion, the
  migration test passed in isolation.
- No production run, live report publication, or message send was performed.
