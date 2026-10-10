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
Claude fixture verification was outstanding at review time; the bounded native
verification below closes that fixture gate. Production release remains outstanding.

### Native Claude cold and compact resume

Installed Claude Code 2.1.269, configured `claude_oauth` route `sonnet`/medium,
used the same anonymous full source (93,706 characters) and compact correction
(902 characters) from the recorded Codex fixture. Commands and child environment
were built by the existing `ClaudeRuntimeAdapter`, using its `no_tools` policy,
native home/authentication, strict empty MCP config and no provider actions.
Cold and actual `--resume` both completed with `success`, exit zero and native
session `648b549a-4277-4bae-9df2-83a45202d2aa`.

The cold stream has 27 events and the resumed stream 4; neither contains a tool
use. Both were passed through the worktree's `ClaudeEventNormalizer`, `finalize`,
terminal proof and `parse_final_result` using the complete Task parser. Each
produced one valid Task for Alex with the exact `Alex: I will prepare it.`
ownership quotation. The resume normalizer explicitly required the cold session
UUID. Native history remains under the standard Claude projects directory.

An initial offline verification command imported the stale primary checkout
because stdin Python places cwd before PYTHONPATH; that obsolete model rejected
current Project fields. Re-running from this isolated worktree passed both exact
streams without changing their outputs. That diagnostic harness error is not a
native model failure or a reason for a compatibility branch. This fixture proves
Task context survives native Claude resume; it does not validate all configured
API routes, business scenarios or production release.

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

### Native result retention probe

Session `01a127a9-dac8-7453-9d2f-7acddfb42288`, service model
`gpt-5.6-luna`/low, used an anonymous read-only MCP source with 100 full records
and an unpredictable final marker. Native CodeMode called the source exactly
once, stored the complete result with `store` without emitting the large envelope,
then used `load` in a distinct exec cell to return the exact final marker.
The controller verified marker equality and the single backend read. Full native
call code was inspected; no shell/file reads or business effects occurred.
This proves native retention across exec cells in one invocation. It does not
prove retention across CLI restarts, nor improvement of the production daily
report. A task-specific large-source comparison is still required before
changing its Skill; no service caching layer or new global prompt was introduced.

### Daily source retention comparison: candidate rejected

Frozen cases are `evals/trajectory_optimization/daily_source_retention.v1.json`,
`v2.json` and `v3.json`. Each compares the same anonymous 100-record source,
service model `gpt-5.6-luna`/low, original daily-report Skill, and original Skill
plus native result-retention guidance. Two independent decisions occur in the
middle and last records (42 and 99); acceptance requires both original source
IDs/types, responsible business owners, numeric amounts, count 100, and no
business action. This is preparation-only testing, not report publication.

| Fixture | Service facts baseline / candidate | Group window baseline / candidate | Interpretation |
| --- | --- | --- | --- |
| v1 | pass / fail | fail / fail | Initial acceptance semantics were insufficiently explicit; MCP fixture omitted production return annotation. |
| v2 | fail / fail | fail / pass | Owner/count/ID semantics clarified; fixture still returned bare `dict`, without production structuredContent. |
| v3 | fail / pass | fail / fail | Exact production `dict[str, object]` return annotation; sources and gold unchanged from v2. Candidate not accepted. |

Local SDK inspection confirmed a bare `dict` return has no output schema and
returns only text content. `dict[str, object]`, used by the production tools,
also supplies structuredContent. v1/v2 are retained as failed/excluded interface
fixtures, not evidence against the production transport. Their fixes did not
change a production prompt or introduce a fallback.

Authoritative v3 native sessions:

- Service baseline `01a127b7-3bea-7490-bfa6-a1183620acf3`.
- Service candidate `01a127b7-3bdd-7590-93d4-56fe2b6a3171`.
- Group baseline `01a127b7-6b4b-7a02-97ba-a8218b86da03`.
- Group candidate `01a127b7-88f9-7de2-8eb5-07ab03983ad7`.

Source hashes match between arms: service
`711a863ea959e287042dc59114bf9d2a6d89646d0161ae93ec499b1d7d75fc6e`, group
`7f8d05bf83d48ab321e754a09a9612389d99d7037c50a12fe4e84a1d13030ea0`.
All four processes exited zero, called the exact source once and executed no
shell command or business action. Neither exit status nor provider completeness
counts as semantic acceptance. Guidance increased prompt length 5,689 -> 6,557;
backend read counts did not improve.

Complete v3 model-facing call/output inspection locates the failure:

- Service baseline printed text content; output reported 32,831 original tokens
  and truncation. Record 42 was absent; final result contained only 99.
- Group baseline printed the entire MCP transport envelope, duplicating text
  content and structuredContent. Output reported 181,835 original tokens and
  truncation. Record 42 was absent; final result contained only 99.
- Group candidate stored the complete result and inspected top-level keys, then
  printed the entire nested `data` object as a preview. That output reported
  89,260 original tokens and truncation, with record 42 absent. It finalized
  without inspecting the missing range. Native retention worked; the subsequent
  model-visible projection failed.
- Service candidate printed records 0–19, then 20–59 in a cell whose output was
  truncated (12,483 original tokens). It recovered both decisions by filtering
  against a phrase specific to routine fixture records. Its correct final JSON
  does not prove complete source examination or a reusable pagination method.

No daily-report Skill or release manifest change is accepted from this study.
The next mechanism test must demonstrate complete bounded inspection of source
text, preserve original records and metadata, and avoid fixture-specific keyword
selection. Native store/load is available, but prose guidance alone has not
established reliable report completeness. Multi-source retention, real report
coverage, other runtimes and production behavior remain unverified.

### Native source pagination probe

Installed `dws chat +chat-messages --help` exposes `--limit` and a bound
`--page-token`; the service currently fixes `--page-all` in its group-window
tool. Frozen `daily_source_pagination.v1.json` compares the same group source
and original Skill/prompt/model against a fixture returning five messages per
page with `hasMore` and `nextPageToken`. No production tool was changed.

- Whole-window baseline `01a127bc-11d9-7d20-b591-077133ef3c1e`: two backend
  reads, final JSON contains both decisions. The first model-visible output
  truncates; the second filters against a fixture-specific routine-prefix
  regular expression. Correct JSON does not prove complete source inspection.
- Paged candidate `01a127bc-11bf-7e73-bfc9-1754c0aa7df0`: all twenty distinct
  source pages read, no shell/business actions, final JSON contains only 99.
  First page output is not truncated. The next exec cell loops over the
  remaining nineteen pages, accumulates their messages and emits one aggregate:
  original output 84,726 tokens, truncated, with record 42 absent.

Both arms use the v3 group source SHA above and 5,689-character prompt. This
rejects pagination alone as a completeness fix: backend paging does not ensure
separate model-visible page delivery. A controlled native direct-tool transport
probe is recorded separately in `daily_source_direct_pages.v1.json`; its native
feature flags must be verified through actual trajectory, not assumed effective.

The paired native transport probe (`daily_source_direct_pages.v1.json`) is also
rejected. Candidate session `01a127bd-122b-7411-913b-29ca5a64fedb` with
`features.code_mode_host=false`/`features.code_mode=false` still attempted the
`exec` tool; its only result was `code-mode host is disabled`. No source calls
occurred and final source_count was zero. These flags do not expose direct MCP
tools on this configured model/runtime. Default-host control
`01a127bd-122b-79c1-8ed6-41642425fff0` read all twenty pages and returned both
decisions, but again selected them using a fixture-specific regular expression
inside its pagination loop. It is not complete-source acceptance. Both prompts
are 5,689 characters and sources are identical to the pagination probe.

Do not disable native CodeMode or ship tool pagination based on these probes.
The confirmed defect is loss of model-visible source text at aggregate output,
while native data retention and provider paging themselves work. A reliable
fix must address that output delivery boundary; repeated prose refinements or
fixture-specific filters are not accepted substitutes.
