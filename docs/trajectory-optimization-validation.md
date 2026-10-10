# Trajectory optimization validation

## Task repair input, 2026-10-10

Implementation: full source and semantic context remain available on every repair.
The runtime selects the compact correction only if the actual route resumes the
native session that produced the preceding candidate. A cold start, another
session or failover receives full context. Route selection and validation budgets
are unchanged. Task prompt schema serialization removes only formatting spaces;
JSON-decoded equality against the complete original schema was verified.
Schema text is 49,004 -> 28,393 characters, retaining every field and assertion.

Local evidence: six actual-route input-selection regressions cover cold, matching
and other sessions, each with and without route failover. Together with Task and
router tests, 353 passed before schema whitespace compaction. After compaction,
236 Task tests passed and one obsolete whitespace assertion failed; replacing it
with complete parsed-schema equality passed its focused rerun.

Native model: configured service OAuth model `gpt-5.6-luna`, effort low, native
home/authentication retained, read-only sandbox, no user MCP configuration.
Anonymous source: Avery requests a report; Alex explicitly commits to it. The
simulated rejected candidate quotes Avery as evidence of Alex's ownership.
Acceptance requires a complete Pydantic-valid result creating the standalone
report Task for Alex with the exact `Alex: I will prepare it.` source quotation;
no invented Project and no external business action.

- Valid cold source session: `01a1279b-9afe-7150-9e94-2f62eadc83c7`.
- Actual compact resume of that session: valid result, correct owner and quote.
- Paired full/compact corrections forked from the same native history:
  `01a1279d-2553-7d63-b510-faf8211a24c6` and
  `01a1279d-2553-76b3-a1d2-3ec90ce60d9f`. Both satisfy the acceptance criteria.
  Full submitted correction is 94,393 characters; compact is 902 (99.04% less).
  Native usage is cumulative across history/provider requests and is not treated
  as the submitted prompt size or a general cost claim.
- Independent baseline cold `01a1279c-4456-72e2-9eaa-66431d668edf` initially
  returned `acceptance_polarity=accepted` with `transition=none`, violating the
  existing cross-field contract. The existing, unchanged format-correction
  prompt on that same session produced a valid result. Its initial failure is
  retained and is not counted as a cold pass or concealed by the paired result.

All completed native events were inspected. The valid source cold turn read the
canonical shared instructions once through a read-only shell command; the paired
corrections had no executed commands or MCP calls. Native skill-catalog length
warnings appeared and do not prove task failure. The test uses a supplied rejected
candidate; it proves context retention for this repair fixture, not an observed
natural semantic-error recovery across all business cases.

Excluded attempts: the desktop `gpt-6.1-sol` alias was rejected by the direct OAuth
interface before model work; another early fixture omitted current-source text
from semantic context and correctly returned missing-source evidence. Neither
counts toward acceptance. Native trajectories remain authoritative in Codex home.

These results are local/controlled evidence. No production deploy, production
input reduction or broad trajectory-quality acceptance is claimed.

### Independent read-only review

The independent reviewer traced actual Codex `exec resume` and Claude `--resume`
commands, persisted result recovery, format corrections, capacity waits and route
failover, then directly parsed the documented native fixtures. It found one
verified defect: Friday ignores the supplied conversation reference and creates
a new thread, so equality with a stored Friday thread is insufficient to reuse
input. The selector now accepts compact text only for the Codex/Claude native
transports and the exact preceding session. Friday always receives full source.
The matching-Friday-binding regression failed before the correction; the final
Task/router/execution test run passed 354 tests, including schema equivalence.
The review found no further actionable defect in this bounded scope. Native
Claude fixture verification and production release remain outstanding.

## Consumer model-facing evidence correction

Full CodeMode inspection of daily-report run 25643 supersedes the earlier raw
MCP duplicate-call interpretation. Its three `daily_report_facts` requests had
identical underlying results, but wrapper calls emitted different projections:
full envelope; meetings/handled/coverage with an incorrect `tasks` lookup; then
`tasks_active_today`, attention, emails and other compact records. The first two
model-facing wrapper results explicitly report truncation; the last does not.
The repeated conversation-list request was used to feed the 68-group read batch.

Thus repeated native-result hashes measure repeated backend retrieval/serialization,
not duplicate model-visible evidence. The uncommitted generic Consumer reuse
instruction was withdrawn rather than suppressing necessary recovery reads.
The group batch also used keyword selection and a twelve-message slice before
printing results: complete provider reads do not establish complete model-visible
coverage. Source delivery and output-size behavior require further mechanism
analysis; no business-rule or Audit change is claimed here.
