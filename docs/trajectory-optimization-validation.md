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

## Consumer Audit-feedback revision input, 2026-10-17

Production trajectory inspection found a repeated-input case in Consumer
feedback revisions: Task 405336 used one Codex session for three Consumer
revisions and repeated 41,385 of the original 41,394 characters in each of two
revisions. This candidate narrows only the repeated Consumer input. It retains
the original full source in the exact native session and submits the new Audit
feedback, current turn time, any runtime correction and the complete replacement
result requirement. Audit's independent context is unchanged.

Compaction is allowed only when the persisted Audit return/reject points to the
exact preceding completed Consumer run, task and generation, the selected
Codex route/session is unique and unchanged, the latest completed transcript is
still at its persisted line end, and native trajectory inspection proves the
initial full-input fingerprint remains in that session with no intervening
compaction. Missing, malformed, grown or compacted history and all other routes
fall back to the full current Consumer input. The prompt snapshot records the
input actually submitted.

Local verification on the isolated worktree: 203 passed and 4 skipped across
`tests/test_native_trajectory_context_guard.py`, `tests/test_agent_turn_runner.py`
and `tests/test_consumer_agent.py`; Ruff and `git diff --check` pass. Synthetic
integration tests assert that the verified path is shorter, includes the full
Audit feedback and is reflected in the saved prompt snapshot; a failed guard
keeps the original task facts in the full prompt. Native model comparison,
production release and live readback are separate gates.

### Native Codex cold/resume comparison

Two tool-free native Codex sessions were cold-started with the same 628-character
fictional meeting source, then resumed with either the full source plus Audit
feedback or the compact feedback-only prompt. The type-only output schema did
not encode expected values. Both native resumes returned valid structured
results without tool calls. Each kept the prior slot `withdrawn`, retained the
current local time and `America/Los_Angeles`, described the DST occurrence as
unresolved, and reported a draft card with no invitation sent. The full arm said
the new slot was `proposed`; the compact arm explicitly said
`proposed_not_accepted`. The correction prompts were 1,041 and 519 characters,
respectively (50.1% fewer in the compact arm). Session IDs, source digest and
outputs are recorded in `evals/trajectory_optimization/consumer_revision_context.native.v1.json`.

An earlier attempted comparison used output-schema `const` values that exposed
the expected answer. Those two resumes are excluded. Its corrected runs used
independent cold sessions and a schema that constrained field types only. This
small synthetic comparison shows the tested model retained the source facts in
these two sessions; it does not establish a general quality or latency gain,
validate the service's trajectory reader against every native format, or cover
other models, providers, tasks or production behavior.

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

### Native chunked output capability

`native_output_chunks.v1.json`/`v2.json` test a supplied exact CodeMode program,
not autonomous report behavior. One anonymous MCP read returns 100 records with
complete long text and unpredictable per-record markers. Both programs retain
the full source and iterate in five-record pages; the only output difference
is `text(JSON.stringify(page))` versus `notify(JSON.stringify(page))`.
Acceptance inspects actual custom-tool outputs, compares every complete record
and text to the source, checks middle/last markers, and counts source/tool calls.

v1 (`01a127c2-8be0-7421-add0-d94d471cf6a7` text,
`01a127c2-8c2f-7d73-87d3-a0859c24652f` notify) established the output difference,
but both additionally read canonical AGENTS through a read-only shell call.
Thus v1 fails its no-shell criterion and is not silently relabeled a pass.

v2 disables shell tools in both fixture arms, without changing the code or
source construction. Both use configured service model `gpt-5.6-luna`/low,
read-only sandbox, standard native home/authentication and one exact source read.

| Arm | Native session | Model-visible output blocks | Truncated blocks | Exact complete records decoded from model-facing output | Markers | Shell calls |
| --- | --- | --- | --- | --- | --- | --- |
| text | `01a127c3-b1d2-78d0-882c-2f22d0036508` | 1 | 1 | 0 | fail | 0 |
| notify | `01a127c3-b1cd-7842-be7b-b65a4a6cba48` | 21 | 0 | 100 | pass | 0 |

Each v2 arm used one exec cell with the exact supplied code; neither invoked
file/business tools. The text arm's zero decoded records does not mean zero
visible text: its truncated aggregate fails complete page JSON decoding and
loses the middle marker. The notify arm produced twenty individually complete
five-record output blocks and a small count result. This confirms an available
native mechanism at the previously failing output boundary. It introduces no
service cache, native config policy or business rule. General autonomous use,
other runtimes and production quality remain separate gates; the fixed daily
comparison is defined in `daily_source_notifications.v1.json`.

### Autonomous notification comparison and fixture-shape correction

The unchanged v3 source/prompt plus 345 characters of native notify/store access
guidance did not pass autonomous acceptance. Service baseline
`01a127c5-2aa2-7843-b94a-21bf6b624941` read three times, had two truncated
outputs, omitted 42 and added an unrequested result field. Candidate
`01a127c5-2ac2-7d12-979a-c35f86540903` read once and returned both decisions,
but printed the entire content as metadata (truncated) and recovered via a
business keyword regex; it did not emit complete source pages. Both service
results fail complete-source acceptance regardless of final semantic JSON.

Group baseline `01a127c5-622b-77c1-8357-04fc1af94eb6` printed the entire MCP
envelope, truncated, and omitted 42. Candidate
`01a127c5-6f27-7c33-bf34-4288ca72fd9f` retained the full result and emitted four
25-record notify blocks; its final result still omitted 42. The native JSONL
stores all four blocks without a truncation warning, but the last model request
contains 54,193 input tokens versus 19,720 before those outputs, much less than
the full-source original-token measurements. This is evidence to investigate
additional native model-input projection, not proof that every stored tool byte
reached the model. All four runs disabled shell and had no business action.

Further inspection of actual run 25643 revealed a fixture mismatch: the real
`read_dingtalk_messages_in_window` returns the DWS ledger at the root, including
`complete`, `hasMore`, `partial`, `truncated`, `queryRange`, `messages` and other
provider metadata. Actual message fields are `conversationId`, `createTime`,
`messageAiSendFlag`, `messageId`, `sender`, `senderId`, `text`. The earlier
synthetic group fixture instead nests `data.messages` and triplicates text in
`content`/`raw_payload.text`/`raw_payload.content`. That fixture remains a useful
large-output stress case but is not production-shape fidelity or evidence for
shipping a group-reader change. Actual service-facts keys match the synthetic
Task body fields, with additional production metadata (`time_zone`, Task stage,
commitment status and business relevance).

Distinguish complete provider result, retained CodeMode value, persisted native
tool-output bytes, actual model-input projection and final business judgment.
Native notify is experimentally available; no autonomous Skill change has yet
passed the complete-source and semantic gates together. Production-shaped
fixtures must be checked before further business acceptance runs.

### Bounded production-shaped ledger comparison and independent review

`daily_source_notifications.v2.json` preserves the original 100 anonymous
message IDs, timestamps, senders and complete texts, maps them to actual DWS
`messageId`/`sender`/`text` fields and removes invented duplicate raw fields.
The fixed original nested-source hash is `7f8d05bf...`; the delivered anonymous
root-ledger hash is
`cd74f194cfdb459035baf93dc106d969e94e68cbfd44d3a9051907ff6f2fc4d5` in both arms.
The manifest's `source_sha256` is the pre-transform source hash; the second hash
above identifies the actual fixture passed to the MCP tool. Both arms use the
same corrected fixture. No production source projection was changed.

- Baseline `01a127c9-80c2-74b3-a64a-de11075517ab`: one source read, truncated
  transport-envelope output (68,626 original tokens), final only `msg:99`.
- Candidate `01a127c9-80fe-7e31-a2b5-2271cd21646e`: one source read, complete
  result retained, bounded metadata followed by four notify pages. Parsing the
  saved outputs gives all 100 unique record objects exactly equal to the source,
  including complete text. No warning or fixture/business-specific prefilter.
  Final includes `msg:42`/Avery/32000 and `msg:99`/Alex/58000. Prompt length
  5,683 -> 6,028; neither arm executes shell/file/business actions.

An independent read-only auditor verified both full trajectories, source read
counts, all 100 record equalities and final gold. It observed notify offsets
arrive in order [25, 0, 50, 75], so source coverage must be checked by offset and
identity rather than notification arrival order. This is a narrow source/output
and two-decision fixture pass. Native JSONL contains persisted output bytes, not
raw model requests; final input count 46,136 is not proof of complete effective
model input. No generalized Skill change is accepted from that alone.

The review identified remaining fixture limits: timestamps wrap at index 60
while queryRange declares ascending order; optional real quotation/reaction/
resource/forwarded fields are not covered; and one repetitive synthetic group
does not reproduce real multi-group coverage. Those must be corrected/expanded
before broader acceptance. Actual run 25643 has 68 complete group reads and
681 messages, 190,121 text characters and 500,009 ledger JSON characters;
median group size is 3, largest 96, longest message text 4,478 characters.
These aggregate counts were read from native MCP results, with no business
message contents copied to this report.

Official [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
documents `features.code_mode.direct_only_tool_namespaces` and
`features.code_mode.enabled`. This is a possible native way to expose paged
reads directly without disabling the entire CodeMode host. It is not yet
verified on the installed CLI/model, and no production setting is changed.
The reference did not establish an exact notify/model-input truncation limit;
that boundary remains unconfirmed and must not be inferred from saved JSONL.

### Native direct-only namespace capability

`native_direct_namespace.v1.json` tests a tiny anonymous marker read with the
same configured model, prompt, MCP source, read-only sandbox and disabled shell
in both arms. Candidate sets the documented native
`features.code_mode={enabled=true,direct_only_tool_namespaces=["mcp__fixture"]}`
while keeping the host enabled. No production config is changed.

- Baseline `01a127ce-ade9-7dc0-afed-e6c686b8a7c2`: discovers metadata and invokes
  `tools.mcp__fixture__read_source` through two `custom_tool_call exec` cells.
- Candidate `01a127ce-b09c-7b22-89da-931abca08bbb`: directly issues native
  `function_call read_source {}`; no exec aggregation. Both call the source once,
  exit zero, and return the exact unpredictable marker.

Thus installed CLI/model supports the documented direct-only transport. This
corrects the earlier failed attempt to obtain direct tools by disabling the
whole host. It proves transport capability for a single tiny read, not complete
large-source delivery or suitability for all service tools. The next controlled
test can use identical bounded source pages and unchanged business Skill with
default versus direct-only transport; source order and metadata must be valid,
all unique pages/records verified, and global role/tool-schema overhead measured.

### Identical bounded pages, default versus native direct transport

`native_paged_direct.v1.json` corrects timestamps to strictly ascending UTC
minutes 10:00–11:39, retaining the original IDs, full text, business owners and
amounts. Both arms use the identical corrected source hash
`3b236a29ace1b89a2c11b09e50b6c32e11d5b875f0387a589861ab3cc3b4f18d`,
same original 5,683-character business prompt/Skill, same model/effort and
five-record page API. No notify guidance or prescribed extraction code is added.

| Metric | Default transport | Direct-only transport |
| --- | --- | --- |
| Native session | `01a127d2-bf7c-7940-9a4b-8a42f7df843c` | `01a127d2-bf7c-7e71-a6a2-7fcbb5888efe` |
| Unique source pages | 20 | 20 |
| Exact full original records in saved outputs | 100 | 100 |
| Source output truncation warnings | 0 | 0 |
| Native exec cells / direct function calls | 21 / 0 | 0 / 20 |
| Model requests | 22 | 21 |
| Sum of request input tokens | 1,129,678 | 729,974 |
| Cached request input tokens | 1,039,872 | 672,512 |
| Uncached request input tokens | 89,806 | 57,462 |
| Last request input tokens | 86,473 | 50,463 |
| Request output tokens | 2,190 | 1,901 |

Both return exact gold for 42/Avery/32000 and 99/Alex/58000 with source_count
100; no fixture-specific source filtering, shell/file calls or business actions.
Default transport reads each page in a separate exec cell and prints the MCP
envelope, retaining duplicate text/structured representations. Direct outputs
contain only the source ledger, with a `Wall time... Output:` prefix. Parsing
that prefix correctly confirms all twenty pages and all full record equalities.
Independent read-only review recomputed every count, timestamp, full-record
equality, final result and usage total from the two complete native trajectories.

Direct transport reduces cumulative input 35.38% and uncached input about 36.0%
within this fixture. These are per-request sums (including repeated history),
not a prompt-size measurement or dollar-cost claim. Twenty-one model requests
remain substantial; bounded pages should not become an unconditional tiny-page
policy for all source sizes. Neither a global mode switch nor a production
page API is adopted. Mixed message fields, multi-group sources, the actual
role catalog, configured provider routes and production report behavior remain
separate gates. Saved output bytes and aggregate token counts do not expose the
literal model request payload.

The actual service role factory forces `code_mode_only=true`, excludes nested
`functions` for Audit, and resets direct-only namespaces to an empty list.
Standalone fixture success therefore does not establish compatibility with
service invocation profiles. A separate read-only Audit-profile capability
probe preserves those restrictions and tests nested direct-only overrides;
it changes no production role policy or command builder.

### Existing Audit profile and full role-catalog compatibility

`native_audit_transport.v1.json` failed before model execution: combining
`--ignore-user-config` with role-factory inherited-server disabling overrides
created disabled server tables without transports (`brightdata`). v2 uses the
standard native home/configuration expected by production; no placeholder
transport or compatibility shim is added.

v2 baseline `01a127df-2ad0-7480-a94c-48edcb3716c6` uses native exec discovery
and source invocation. Candidate `01a127df-2b04-7343-8438-785d89ca15db` directly
calls the same allowed group reader. Both read once and return the exact random
marker. Effective commands retain `sandbox_mode="read-only"`, CodeModeOnly and
host enabled, excluded namespace `["functions"]`, shell/unified-exec disabled,
and the same Audit allowed-tool list. Candidate adds only nested CodeMode
enabled/direct-only namespace settings in its temporary test command. No
production command builder, read-only policy or tool authorization changes.

`native_role_catalog.v1.json`/v2 expand the fixture to actual role tool
declarations from `build_role_server`: 41 Consumer and 37 Audit tools (input
schemas/descriptions total 15,234/13,530 JSON characters). Before server startup,
all actual callbacks are replaced with fixture stubs: only the existing group
reader returns a marker, every other invocation is recorded and fails without
effects. SDK Tool.run was inspected to confirm it calls the replaced `fn`,
preventing real DWS, task, artifact or delivery operations in this test.

v1 Consumer commands duplicated `--skip-git-repo-check` already owned by the
Consumer factory; CLI rejected both before any model or source work. v1 Audit
completed and is retained. v2 reruns Consumer with that fixture error corrected.

| Role/arm | Native session | Model requests | Initial input | Sum request input | Uncached input |
| --- | --- | --- | --- | --- | --- |
| Audit/default | `01a127e5-a68b-7e33-a958-d778e6d4d94d` | 3 | 9,178 | 33,692 | 12,700 |
| Audit/direct | `01a127e5-a684-7031-a2b6-aadf60ef15c5` | 2 | 10,253 | 20,697 | 10,713 |
| Consumer/default | `01a1282d-cd91-7ad2-b74e-328dde67b174` | 3 | 10,917 | 33,588 | 12,596 |
| Consumer/direct | `01a1282d-cdf9-7dc1-ac04-6d59fd6e2ea3` | 2 | 12,150 | 24,501 | 12,469 |

All four exit zero, read the marker once, match its exact JSON and record zero
unexpected callbacks, shell/file calls or business effects. Default uses two
exec cells; direct uses one native function call. Direct declarations increase
initial input about 1.1–1.2k tokens in these samples but avoid one discovery
request. These are role-catalog and command-profile capability tests on the
configured OAuth model, not Consumer/Audit business judgment, API providers,
session-resume acceptance or generalized cost estimates.

### Existing API routes: exact terminal evidence

The current production configuration declares `codex_api` (gpt-5.6-luna) and
`qwen_gpu4` (qwen3.8-27b) in addition to OAuth routes. Existing credentials were
reused under the approved plan, without printing, creating or changing them.
The API fixture uses native adapter provider settings/environment and the
actual 37-tool Audit declarations with every production callback stubbed.

`native_api_catalog.v1.json` failed before model work because the full service
manifest needed MEMORY_CONNECTOR_URL absent from the test process. v2 uses the
existing native CodexRunner/provider settings plus only the fixture transport;
no placeholder memory server or application fallback was introduced.

| Route/arm | Native session | Source calls | Terminal evidence |
| --- | --- | --- | --- |
| codex_api/default | `01a1283a-6bfa-7443-8e92-758db5243308` | 0 | turn.failed, exceeded retry limit, last HTTP status 429 |
| codex_api/direct | `01a1283a-6bfa-7332-b734-020155b640c5` | 0 | same HTTP 429 failure |
| qwen/default | `01a1283a-7caf-7750-a6f9-29665aad7921` | 0 | turn.completed, assistant reports only wait/request_user_input available; no exact marker |
| qwen/direct | `01a1283a-7d4d-7ac1-8c44-be93170267b2` | 1 | native function call, exact marker JSON, turn.completed |
| qwen/enabled-only control | `01a1283f-55ff-75f3-9931-114f2c1a3506` | 0 | turn.completed, same tool-unavailable response |

The original v2 harness incorrectly failed Qwen/direct by requiring the CLI
last-message output file. That file was absent even though stdout/native JSONL
contains the exact final JSON. Existing service `parse_typed_agent_result` with
a strict one-field marker model validates that same stream against the actual
fixture nonce. The corrected result is a tool-transport pass with zero unexpected
callbacks, not a full business-output/schema pass. The original file-based
false fail is retained rather than hiding this evidence correction.

The harness's failure classifier also returned a transport-disconnected code
after noticing model-list refresh warnings. Full trajectories supersede that
interpretation: OpenAI-compatible route failures are explicit HTTP 429 before
source work; Qwen/default and enabled-only complete with tool-unavailable text;
Qwen/direct completes the requested read and typed JSON despite the same warning.
Thus neither the model-list warning nor process exit/file absence establishes
schema incompatibility or final-result failure.

`native_api_catalog.v3.json` changes only CodeMode enablement while retaining
an empty direct-only namespace list. Its failure to invoke the source, contrasted
with the direct arm, shows that enablement alone does not restore this allowed
MCP read in the tested Qwen role profile. The native direct namespace configuration
restores that read without prompt rules or model-route changes. Literal model
request tool definitions are not exposed by these traces; underlying model
capability attribution remains narrower than this controlled behavioral result.
OpenAI-compatible 429 capacity and full business cold/resume cases remain open;
no production transport or role policy has been changed.

### Narrow native transport implementation

The development command profile now explicitly sets
`features.code_mode.enabled=true` and
`features.code_mode.direct_only_tool_namespaces=["mcp__agent_cli"]` for
Consumer/Audit, removing stale incoming overrides of those settings. This is
the exact combination validated above; other MCP namespaces stay on their
existing path. Role tool lists, sandbox, Audit functions exclusion, sessions,
routes, and authorization options are unchanged. No prompt rule was added.

The updated regression first failed on missing explicit native enablement.
After the implementation, the five affected test files passed: **275 passed,
4 skipped**. The initial sandboxed broader run had 15 write-permission failures
in schema/prompt fixtures; rerunning with write access to this isolated
development worktree resolved every failure. Independent read-only review found
no actionable defect and confirmed the native probe evidence supports this
exact configuration, with the API 429 and business-workflow limits intact.

This implementation remains local and unpushed. It does not establish full
business schema compatibility, real report correctness, or production readback.
Native CLI metadata, stored trajectory and turn-runner verification also passed:
**71 passed**. Combined focused verification: **346 passed, 4 skipped**.

### Real weekly-report input-contract failure, run 25688

Read-only production DB and complete native session
`01a12730-290b-79e1-a7f2-1b6f74248e5c` identify a failed Consumer with eight
weekly validation calls and no rendering/publication call. The actual tool
catalog declared manifest/report as unconstrained objects. Early attempts used
a list for manifest.minutes; later attempts used a nonempty keyed object for
report.company_metrics. The installed validators expect an object and an array
of objects respectively, and raised list/str `.get` errors instead of reporting
field errors. An anonymous complete fixture with only company_metrics changed
to a mapping reproduces the exact str error at metric_by_key construction.

Combined five-Skill output and several source outputs were truncated. No separate
reference read occurs in this trajectory, but the referenced contract also lacks
complete JSON shape definitions. Truncation is a contributing hypothesis, not
a proven sole cause; the generic native tool parameter contract is confirmed.
Independent read-only review checked the whole trajectory and corroborated both
shape failures. The prior native direct transport fix alone does not repair them.

Development now exposes the existing weekly-report structures through typed
native validate/render parameters. Unknown fields are retained, no missing
business sections are filled, and installed business checks are unchanged.
Regression first failed because malformed object/list input entered the old
parameter model. The updated interface rejects it with exact nested field paths.
The complete valid Skill fixture survives argument validation with identical
JSON values and still returns publishable=true with no errors. Focused
Agent CLI, role-boundary and materials tests: **65 passed**. Existing standalone
Skill tests: **49 passed** (run from its own root, as its scripts imports require).
No production run was retried or external document changed.

Independent implementation review found an initial overly strict scalar schema:
optional metric data_date and issue deadline nulls were valid under the installed
Skill but rejected at the new tool boundary. The null regression failed before
correction. Business-valued fields now retain their existing value range;
container types and existing string text requirements remain discoverable.
The full publishable fixture with optional nulls is unchanged after validation
and still publishable. Actual FastMCP Tool.run rejects a wrong metric mapping
before any business callback, with the exact report.company_metrics field path.
The reviewer also identified evidence=null on a hypothesis claim as an accepted
existing value; its regression failed before allowing that null. The automated
complete Skill fixture now includes all three reviewed null cases, preserves
the exact input, and remains publishable. Installed report_contract.py and
validate_run.py hashes match the repository fixture sources used by that test.

### Qwen direct transport with native output-schema control

Versioned cases: `evals/trajectory_optimization/qwen_direct_schema.v1.json`.
Current command profile `19124d8d`, existing qwen_gpu4 model/credentials,
low effort, one cold session per arm, serial concurrency, same prompt and actual
Audit role catalog with every production callback replaced. Only the native
output-schema argument varies. Source marker is random and absent from prompts;
the sole allowed source returns it. Canonical Audit parsing and terminal
completion are necessary but insufficient: the exact read and marker are required.

| Arm | Native session | Source calls | Canonical result | Exact marker |
| --- | --- | ---: | --- | --- |
| Schema off | `01a12856-5d3f-7163-8440-067fe82d3aae` | 1 | valid, completed | matches |
| Minimal schema | `01a12856-8b80-7320-9f6a-40cf92157f90` | 0 | valid, completed | does not match |
| Complete draft Audit schema | `01a12856-a680-7d23-8ce4-12392f701204` | 0 | valid, completed | does not match |

All processes exited zero and no unexpected callbacks occurred. Schema-off
has a native source function call and matching return; both schema arms contain
no function call and fill the summary with literal marker/context text instead
of the hidden source value. Thus those arms fail the requested source-backed
task despite structurally valid output. Optional CLI output files were not used
as a success criterion. Complete native trajectories were read.

This confirms that the direct namespace repair does not resolve the configured
Qwen native tool-plus-output-schema gate. Failure also occurs with the minimal
schema, so changing the full business schema alone is not established as a repair.
Underlying provider grammar causality remains a hypothesis without the actual
request/server parser evidence; the earlier isolated upstream grammar result is
not a served-model acceptance result. No provider configuration or service code
was changed and no real Audit/business action was performed.

Independent read-only review verified all three complete native trajectories,
the 37-tool catalogs, identical hidden-source files, canonical parsing and
callback counts. The output-schema flag follows the saved harness and native
runner code; process argv was not captured by native session JSONL. The full
schema is reproducible from immutable Git commit
`3a4942a8a7c45a0aaccc01b233b499eaf1ae2eb1` at
`app/schemas/audit_agent_native_output.schema.json`, with SHA recorded in the
manifest. It is not claimed to exist in this current checkout. Written prompts
are identical; auto-injected environment text differs slightly with each arm's
temporary cwd. No matching resume gate is counted for the failed schema arms.

### Actual session reuse and repeated Task contract inventory

Production sessions `01a12734-cf45-79b2-96cf-6c5cebde7388` (three turns),
`01a12739-9d46-7e12-b361-ec5517f12421` (two turns) and
`01a1251c-4d03-7160-903b-aae3dd549109` (seven turns) each inject the initial
service Developer once and continue Task messages in the same native session.
Their initial Developer texts are 32,843/32,843/32,940 characters. It is false
to multiply that initial text by the number of turns as duplicate injection.
Consumer context.render does, however, repeat the exact 891-character
Application Result Contract already present in Consumer Developer; Audit uses
the analogous 2,546-character contract. This is a confirmed instruction assembly
duplication, not permission to remove current facts, Skill entries or feedback.
The current runtime explicitly disables native output-schema, so the Developer's
wire schema remains necessary until the independent native schema gate is closed.

### Complete application contract injected once

Actual Consumer/Audit producer regressions, capturing the submitted Developer
and Task rather than a standalone template, first failed with two Consumer
copies and three Audit copies. Audit's Role Boundary was the exact same
_AUDIT_AGENT_RULES string as its named Application Result Contract. Both common
Task copies and that duplicate Audit Developer section are removed; the full
unchanged contract remains once in Developer. Explicit Task audit instructions,
complete candidate and source facts, digest, stage and feedback remain intact.
No matching by keywords, policy rewrite, route change or session reset is added.

Consumer Task removes 893 characters including separators; Audit Task removes
2,548 and Audit Developer removes 2,565. These are exact removed duplicate bytes
as Unicode character counts, not estimated cost savings. Current role Developer
and actual complete producer input each contain the canonical contract once.
Tests cover full candidate/source preservation, distinct boolean/number source
facts, retained custom Audit requirements and Settings previews. Scoped final
run: **235 passed, 4 skipped**. Two context tests and two earlier compaction
assertions that deliberately expected duplicate contract copies were updated
to verify the complete common contract in Developer and facts in Task.

Independent read-only call-site review confirmed production producers always
submit complete Developer with Task, Settings uses the same assembly, Consumer
role-boundary content remains, and session IDs, hash calculation and routes
were unchanged. The review's stale-test findings were addressed in the final
scoped run. This is code/input-assembly evidence; the changed bytes have not
yet been released. The native business matrix below validates this change within
its controlled fixture scope.

### Native business comparison of single contract injection

Manifests: `evals/trajectory_optimization/role_contract_matrix.v1.json` and
`role_contract_matrix.v2.json`; accepted native references and technical result
metadata: `role_contract_matrix.results.v2.json`. Baseline assembly is
`266b8c99`, candidate is `84300f77`. Both use OAuth `gpt-5.6-luna`, medium effort,
native output-schema off, 180-second timeout and at most two parallel invocations.
Prompt configuration, Audit rules, 342 frozen Skill bodies and their referenced
Markdown, source facts, clock and criteria are identical. Actual current role
tool schemas are used, but every production callback is replaced with a fixture.
Assembly uses the frozen helper inputs rather than a task DB producer; actual
producer behavior has separate submitted-input regressions above.

| Business criterion | Baseline | Candidate |
| --- | --- | --- |
| Shanghai invitation crosses the Los Angeles date boundary; midnight Blocked event does not overlap | pass | pass |
| Real 23:00–23:30 calendar overlap; decline the exact event | pass | pass |
| Same-session continuation rereads accepted state; no duplicate response | pass | pass |
| Completed mail conversation; no unnecessary reply | pass | pass |
| Document capacity, date and budget contradictions; request reconciliation | pass | pass |
| Existing unresolved remediation; retain owner/deadline and avoid duplicate task | pass | pass |
| Audit returns incorrect factual conclusion with matching revision and digest | pass | pass |

A fresh read-only reviewer, given anonymous X/Y groups and complete outputs,
judged both groups **7/7** against the fixed criteria. The Audit fixture uses
canonical `payload.content` and exact task-context binding, with an intentionally
incorrect factual conclusion. It does not test the older `payload.body` defect.
The result is no observed business regression in these fixtures, not evidence
of improved quality, provider execution or production acceptance.

Independent technical review verified all 14 accepted invocations: exit zero,
no timeout, matching native session start and terminal `turn.completed`, canonical
result, and both continuations retaining their own valid cold-session ID.
There are 57 successful stubbed MCP callbacks, all read/list, with correct source
IDs and no MCP error. Thirty native direct Skill outputs across 12 distinct
sessions match the frozen Skill strings exactly. This establishes content in
native `function_call_output`, not every provider request byte or freedom from
later context compaction. Consumer Task removes 893 characters per invocation;
Audit removes 2,548 Task and 2,565 Developer characters. No source facts are cut.

Initial v1 fixture failures are retained separately and are not timely passes.
Two resume commands exited 2 before model work because hand-inserted MCP options
split a `-c` option from its value. V2 uses existing `ControlledCliConfig` with
`make_role_agent_command`; the production helper needed no patch. Several v1
network resets caused wrapper timeouts; killing the Node wrapper left Rust
children able to finish late. Original timeout records are preserved, and late
outputs are not counted as accepted runs. V2 starts each invocation in its own
process group and waits for its recorded process to exit. V2 also rejects wrong
fixture source IDs and matches production's empty Task `audit_rules`; neutral
case requirements remain explicit. The four timely v1 calendar cold runs plus
ten completed v2 runs form the accepted set. This is a fixture correction,
not an application retry, fallback or process-policy change.

Some model turns attempt MCP calls inside CodeMode and receive `TypeError`
before recovering through direct tools. Failed attempts contain no Skill text;
all successful Skill reads above are direct. Accordingly architecture/runtime
documentation now distinguishes direct tool declaration from the model's actual
calling behavior. The extra failed attempts remain an observed optimization
candidate; no prompt rule or capability restriction is added to suppress them.
All accepted processes are terminal. No live business action, service setting,
provider setting, merge or deployment was performed for this comparison.

### Current-main integration verification

Merged `origin/main` at `4a84d06b` into this isolated branch as `bd048ec8`.
The two document conflicts retain both claim sets and current main's complete
unsettled/settled input-source semantics, plus the verified Meeting Skill sentence.
A bounded read-only reviewer found no concrete runtime conflict: active source
inputs remain complete during role turns, Task repair uses WorkSummaryInput,
and email projection occurs before classification result adoption. Storage,
lease, delivery, route and business-state policies are unchanged by this branch.

Affected test files for role context/Consumer/Audit assembly, compaction, native
tool boundary, agent CLI, Task processing/repair/routing, email classification
and meeting-work Skill: **624 passed, 4 skipped in 99.37 seconds**. The changed
tool-free Meeting Alignment frozen-Skill regression separately passed
**1 test, 129 deselected**. No full suite was run alongside the service.
Pre-release readback: production checkout clean, PID 72634, health status ok,
Status/Attention/History APIs readable. This remains pre-release evidence;
post-deploy behavior must be recorded separately. Native output-schema PR31
is excluded from this branch and still requires its independent provider gate.

### Production release readback

Formal `python -m app.deploy` operation
`deploy-b19a9fcd-ea1f-4f63-a173-c557a603f496` succeeded, installing
`55cbecef890bac80784bfaf872cf0eb9d01fa8d4` from production's prior `452b4d83`.
Receipt time: `2026-10-11T02:05:01.245495+00:00`. PID changed from 72634 to
56046; production checkout is clean and health reports ok. No manual process
control, production source edit or output-schema publication was performed.

Status, Attention and History readbacks succeed. Dispatcher queues have zero
running/due work and business queues have zero processing. There are five
pending email provider actions, with latest update before release, twelve
failed counts in the queue summary and four Attention records. Current failed
and needs_human reply-task rows were last updated before release; Attention's
latest timestamp is `2026-10-10 09:21:24`. They remain unresolved existing work,
not a claim that the service has no failures. The latest Consumer/Audit pair
25701/25702 completed before the deployment receipt; no post-release role
business result is counted by this readback.

Settings current public previews are available on codex_oauth and contain the
complete canonical role contract exactly once: Consumer Developer 32,729
characters, Audit Developer 29,270, each with the 24-character unbound-task
placeholder. This verifies deployed public assembly, not a submitted task or
CLI-internal context. Full wire schema remains while native output-schema is
disabled, so the Developer is still substantial.

The receipt's pre-upgrade backup path no longer exists after the service's
existing daily backup retention replaces earlier backups. The retained
`auto-reply-2026-10-10.sqlite3` is 411,848,704 bytes, has the completed-backup
application ID 1128615746 and passes read-only `quick_check=ok`. Native references
for all twelve distinct matrix sessions remain present, and neither of the
two task-owned matrix fixture directories has a running test process. Their
intermediate evidence remains temporary while this investigation continues.

### Post-release instruction inventory and native global instructions

Read-only production checks still show PID 56046, and no new role run was
present after the deployment receipt when queried in this turn. Do not substitute
that absence for production business acceptance or launch a real task merely
to create a receipt.

Current Settings source-length receipts identify the remaining size precisely:

| Segment | Consumer characters | Audit characters |
| --- | ---: | ---: |
| Work Profile | 9,097 | 9,097 |
| Wire schema | 9,079 | 1,575 |
| Configured and fixed Audit Rules | not injected | 7,608 |
| Capability instructions | 3,529 | 828 |
| Runtime context | 2,660 | 2,430 |

Thus Audit bulk is primarily Profile and rules, not its wire schema. Profile
has explicit scope/precedence, judgment/evidence, six models/limits,
business/people, scenarios, external decision conditions and expression/privacy
sections. It is not the repository's short seed or the current generated
profile format, and no research appendix is present. Removing a presumed
bibliography would not address this actual profile. A separate read-only
review checks actual clauses against existing Skills and task metadata before
any profile or policy change; no user-authored configuration was overwritten.

Manifest `evals/trajectory_optimization/native_global_instructions.v1.json`
records a local CLI-only reproduction. `codex debug prompt-input` with
`project_doc_max_bytes=0` exits zero and still injects all 4,323 trimmed
characters of the current global AGENTS file verbatim in its initial user
message. The file contains the canonical shared-rules bootstrap. Cold matrix
trajectories also contain the bootstrap and some Consumers read the shared
rules afterwards. This is separate from service-owned Developer/Task assembly
and is not visible in Settings' intentionally bounded service input preview.

Official source at `cc7ba33601286a751477832b01de7b6f8de0dd9c`,
`codex-rs/codex-home/src/instructions/mod.rs`, loads global instructions from
the native home through its own provider. That provider accepts a home path
and has no project-document byte budget. Installed CLI help offers no dedicated
global-instruction switch; `--ignore-user-config` documents config.toml omission,
not global AGENTS omission. The public configuration schema supplies no global
provider toggle. Exact installed-source equality is not claimed, but actual
installed prompt behavior is reproduced. Do not silently switch CLI home,
overwrite a global bootstrap, assume an undocumented override works, or add
another prompt rule as a substitute for a native opt-out.

Independent read-only Profile/Rules review confirmed the actual authored
profile has no evidence-ID appendix. Its scene workflows overlap existing
message, mail, document, calendar, OA and personnel Skills. Generic root-cause
repair/code/persistence directions also appear in the profile used by read-only
Audit. The profile's external-decision section still prescribes numeric outcome
thresholds, whereas current `DECISION_QUALITY_GATE_INSTRUCTIONS` expressly
treats those scores as evidence-quality measures rather than numerical outcome
gates. This is a confirmed instruction inconsistency, not yet a demonstrated
cause of a specific model failure. Do not silently overwrite authored Profile
or delete review policies based on static comparison alone.

Audit Rules combine a 224-character role wrapper, 1,210-character business
context contract, 645-character publication-scope contract, 1,676-character
message-audience contract and 3,844-character authored rules. Their OA-specific
rule is approximately 1,504 characters and overlaps the task's applicable OA
Skills. Actual task `skill_names`, channel and live OA processCode/registry are
available mapping anchors; trigger keywords are not. Before any relocation,
the same complete rules must remain available for applicable tasks and fixed
business comparison must show no loss of required review or audience checks.
No production rule, Profile, tool permission or prompt was changed in this
read-only investigation. The next evidence step is a fixed comparison using
this actual Profile/Rules, rather than the earlier matrix's smaller frozen
configuration, plus complete trajectories for any newly observed failure.

### Actual authored Profile native recheck

Manifest `evals/trajectory_optimization/current_profile_recheck.v1.json` and
`current_profile_recheck.results.v1.json` close the earlier short-Profile fixture
gap. Production prompts are read through the read-only prompts endpoint and
rendered Audit Rules through public prompt-preview. Full current Profile is
9,097 characters including its wrapper, versus the earlier fixture's 591;
authored body is 8,803 characters. Actual Developer and User templates are frozen
too. Personal content stays in temporary fixtures/native context, not the result
artifact. Code assembly and actual role tool schemas remain the released version,
with all production callbacks replaced. Skill bodies, source cases, clock, model,
effort, timeout, concurrency and business criteria retain the frozen controls.

All six cold cases and the valid same-session calendar continuation finish
within their timeout, exit zero and parse canonically. Independent read-only
review reads complete results and actual completed source-tool inputs/outputs
and judges **7/7 phases pass**, including all document contradictions, no duplicate
task or calendar response, and exact Audit revision/digest feedback. The resume
retains `01a128c5-b4ab-7db3-bd30-e7c1165bd25c` and rereads accepted live state.
Thirty-three direct MCP outputs contain no MCP errors; seventeen native Skill
outputs match frozen content exactly. Two CodeMode TypeErrors are recovered.
The calendar directory's callback log is overwritten when the resume fixture
server starts, so cold-call evidence comes from the distinct cold stream/native
turn rather than that current callback log.

The actual Profile did not cause a wrong rejection, escalation or action in
these samples, but their scores also satisfy its older numeric thresholds.
No phase explicitly cited those thresholds. This does not establish that the
thresholds never matter, resolve the static inconsistency, or validate a trimmed
Profile. No profile/rule change or external business action was performed.
Remaining work is a controlled candidate comparison that maps repeated task
flows to existing applicable Skills while preserving preferences and current
business rules, plus the independent native output-schema provider gate.

### Single-contract regression assertions and Task Skill discovery scope

The reported Quality run 38104147210 contained three stale prompt assertions.
Focused reproduction failed those three assertions (33 other tests passed).
The revised tests check the exact complete Consumer/Audit application contract
once in the combined Developer/Task input, preserve Task source/stage assertions,
and retain host-instruction isolation checks. Audit no longer requires a second
identical Role Boundary section. No runtime contract or business criterion changes.
The three files plus prompt compaction passed 42 tests; adding business Skill
coverage passed 80 tests. This does not classify or resolve the other CI failures.

The V2 Consumer project-document fixture requested a source reply but selected
`ceo-work-tracking`, whose unchanged body is explicitly a Task Agent reader with
`project_decisions`, `task_decisions`, `project_assessments` and no source reply.
The candidate followed that instruction and returned `no_action`; preserve the
original failed criterion rather than calling it a successful reply. Correct the
fixture role/Skill binding to document review in a new comparison. Consumer
project review does not validate Task Agent extraction.

The CI Skill description now names the existing Task Agent scope and Consumer
document/message entry points. Only frontmatter description/version changed;
the body SHA-256 remains
`353be24fd6c84df24f55fd8fa709adc737416ab712d8d2e902959d4851067edf`.
The parsed-catalog regression failed with the old description and passed with
the candidate CI asset. Independent read-only review found no relaxed business
or permission oracle. Installed Skill publication and production load remain
separate, unverified steps at this checkpoint.

### Actual production task-specific input asymmetry

Read-only inspection of task 386349 covered the complete native Consumer
session `01a128e7-c971-7f92-abbd-8c42d920b76c` and Audit sessions
`01a128ea-bbf8-7b21-818a-4ea84d1c6703`,
`01a128ed-035c-7331-b050-df36d972797a`,
`01a128ee-4c54-76d3-9b4a-78e9209cab16` (runs 25703–25708).
Consumer received an 844-character saved task prompt including explicitly
applicable individual requirements. All three Audit inputs omitted that text;
the first Audit rejected a candidate's application of a requirement as unsupported
by the generic Skill. The next feedback reversed its earlier recommended action
order without new source facts. All 54 direct calls succeeded; identical reads
returned unchanged source and frozen Skill content. This is input asymmetry,
not proof of a tool-schema defect or model hallucination. The final approve
does not prove complete-rule review or external execution.

The fix passes the saved task instructions to Audit once through existing task
sections/snapshots. It preserves every rule and does not decide a new precedence
or inject an OA exception. The existing exact Skill-appendix separation is shared
with Consumer; custom Skill instructions remain in the separate Skill section.
The selected-Skill regression first failed in both generated/custom cases because
the task requirement was absent. After fixing input assembly and using the
existing snapshot `characters` field, 61 focused tests passed. The parallel larger
run passed 210 other tests with 4 skips but used the earlier incorrect test-only
snapshot field assertion; it is not recorded as an all-green run.

### V3 profile comparison and production publication

V3 fixes the Consumer project-document fixture's Skill binding, keeping all
source facts and reply criteria; original V1/V2 failures remain recorded.
Independent anonymous complete-trajectory review passed both arms 8/8, including
true cross-midnight overlap and valid same-session continuation. The baseline
recovered a macOS date-format failure through Python zoneinfo and an unsupported
fixture global-AGENTS read after reading the required Audit document. These
errors are retained in `profile_flow_index.results.v3.json`; valid JSON alone
was not the business acceptance criterion.

The approved candidate removes only the two message/calendar workflow paragraphs
from Profile E, without a new global Skill index. Raw text is 8,803 -> 8,238;
rendered Profile is 9,097 -> 8,532 characters (565 fewer, about 6.2 percent of
Profile). Every other authored Profile clause, complete Audit Rules, Developer
and User template remains unchanged. Settings save preflight matched the complete
frozen baseline exactly; HTTP 200 plus exact raw/rendered readback confirmed the
candidate. Personal Profile content is not copied into this repository.

Formal deploy `deploy-1838189f-03ac-4d25-8da4-db3f5dbda87c` succeeded at
2026-10-11T03:18:40Z, production 55cbecef -> fa95463f, clean checkout and
healthz ok. A separate existing single-Skill sync published only the reviewed
work-tracking description/version; the entire body was byte-identical. Installed
file SHA is `1cd05b1a3589437d795001723ec5bfb332456db16f21ff79b7f23bce6af5b101`;
actual parsed runtime discovery names the Task Agent scope. Formal restart then
completed, main PID 6031 (first deploy PID 3023), worker load receipts had no
error, and startup captured Skill revision 145. Managed runtime bindings remain
configuration 18; no migration or wholesale replacement of their older revisions
is claimed. The actual runtime discovery reads the installed tree.

Final readback: processing 0, five pending Email provider actions, Attention total
5 and History available. The deploy-specific backup receipt path was removed by
the existing daily backup retention; retained daily backup is 412,696,576 bytes,
`quick_check=ok`. No post-deploy Consumer/Audit role turn existed at this checkpoint,
so deployed code/config readback is not a new-business-turn success claim.

Task 386349 ended failed before this deployment, after an Audit approve and one
System OA-comment dispatch. Readback shows a new remark near the dispatch time,
but no exact comment identity/content receipt. The handler discards the initial
provider response in its missing-comment-ID branch; subsequent reconciliation
also overwrites the stored action outcome. Only one action attempt exists,
and uncertain retries did not dispatch again. This independent external-action
confirmation issue remains unresolved; it is not evidence that the input fix failed,
and uncertain is not proof that the external comment was never written. No replay
or incidental effect-reconciliation policy change was made.

### Weekly Report validator and runtime publication alignment (2026-10-17)

The synthetic new-target native trace exposed a validator/render contract
mismatch: the installed validator returned `publishable=true`, followed by nine
failed render calls for fields the renderer reads. The candidate validator
reports the missing metric, issue, owner and business-line fields before
rendering; after correction, rendering and the in-memory fake-sink readback
passed. A matched prompt-only reuse hint did not remove a second identical
successful render call, so no new Skill instruction was added.

The current `consumer-system-contract-release` manifest also omitted both
validator scripts and had a stale Weekly Report Skill digest. The candidate
release now includes `report_contract.py` and `validate_run.py` with the Skill;
focused publication tests cover exact copy and rollback. Skill tests: 57 passed;
release, materials and CLI tests: 97 passed. At that local checkpoint,
production HEAD `1fcbddda` still had the old `validate_run.py` digest and no
manifest entry. No real report was written in this validation.
Receipt: `evals/trajectory_optimization/weekly_report_missing_target_render.native.v1.json`.

Acceptance for this release is bounded to the missing-target mechanism: a
missing current document must use the complete previous meeting document as
the render base and must not require a nonexistent target ID or revision;
validator success must imply that every field read by the renderer is present.
An existing target must retain its revision-guarded overwrite and readback,
and the daily-report write path must continue to accept its existing content.
The fixed anonymous new-target case and affected local tests verify those
behaviors without writing a real document. Production acceptance additionally
requires the published Skill and both validator scripts, the exact scheduled
Skill binding and worker load receipt, a healthy new process, and queue,
Attention and History readback. A real post-release document publication is
not claimed without its own provider receipt.

After merging `origin/main` at `3039eb159`, the prior PR Quality run's 48
failed test IDs were compared with the same-base main run's 48. One new failure
was the generation-fence test's lightweight database fixture: it stubbed the
writer but not the caller's task-type lookup. The production writer already
opened the full Store. Completing that fixture preserved its old/new generation
assertions; the focused test passed. Before review, the merged branch passed 86 CLI/release
tests, 53 materials/receipt/boundary/artifact tests, and 34 Skill contract tests;
Ruff and diff checks passed. Read-only publication preflight passed all 16
manifest entries and found only the Weekly Report Skill plus its two validator
scripts changing. The People OA Skill was already identical to the current
repository source; only its stale manifest `old_sha256` was refreshed.

PR review found that an exact rendered-content digest check would add a new
publication gate beyond the missing-target repair, so it was removed. The
service binds target existence and revision from rendering; the existing
document content and JSONML validation remains in the writer. Review also
found that business-line metrics were being required to carry `previous` and
`change`, fields read only by the company-metric renderer. A separate field
set now matches the business-line renderer. A valid synthetic Friday metric
without those two fields passes validation and renders 32 managed nodes;
the 58 Skill tests and 97 affected CLI/release tests pass after
these corrections. The publication preflight still changes only the same
three Weekly Report files.

### Production readback (2026-10-18)

PR #130 merged as `f9ea9b0c`. Formal deployment with
`--publish-consumer-system-contracts` advanced the production checkout from
`0376f4d4` to that commit. Release receipt
`deploy-c4bbe4ce-634d-4ed0-8fd2-7293c1aea384` is `verified` and records
only the Weekly Report Skill plus `report_contract.py` and `validate_run.py`
as changed files. The installed SHA-256 values are respectively
`6c510f3af2aa955008bcbbacc076c93956d7f6d5ee641c99fff09800159adb0c`,
`bc90cfe6efa735675fd833003363776aea5463e0cdf1f543357971dfaad8e13a`,
and `4c81e3de54a191655da8c899e2c50464108828a4be769014743f837b976bd6b5`.
Active runtime config 343 binds the Weekly Report Skill to revision 196 and
both new service PID 35611 and email-worker PID 35613 reported that exact
Skill digest without an error. Scheduled task 13 remains enabled at version
23, references revision 196, and has no pending old trigger.

The new supervisor PID is 35601; `healthz` returned OK. Console scheduled
queue had zero pending/running items; Attention remained at seven and History
returned recent items. The existing overall `degraded` indicator is from a
Work item last updated on 2026-10-17 with no terminal handling, not from this
deployment. No post-release Weekly Report turn or real document write occurred
at this checkpoint, so the production readback proves code, Skill and runtime
binding, while actual publication awaits the next natural run and provider
readback.

## Task semantic-context reuse draft: no-go, 2026-10-18

Draft PR #125 combines two changes. Its Consumer feedback-revision mechanism
was delivered separately in PR #134. Its remaining Task mechanism omits
semantic context only when the immediately preceding Task input has an exact
semantic-context fingerprint in the same native session and runner lifetime.
The fingerprint includes `current_work_item`, so a new source or Work Item is
unlikely to match. The anonymous native comparison in #125 proves one
constructed matching case saved 8,974 submitted characters and preserved that
case's Project assessment; it does not establish repeated production benefit.

Read-only production metadata yielded 8,921 completed Task Agent runs with a
session ID and 340 adjacent completed-run pairs sharing a session. Only eight
of those pairs shared both `summary_input_id` and source identity. This was a
metadata proxy, not an exact fingerprint count. The JSONL file for the most
recent shared session was unavailable, but Codex's paginated native history
retains its complete turns. A file-only lookup had missed that source.

The exact native-input comparison is now complete for recent applicable
Codex sessions (2026-10-10 through 2026-10-17): five sessions, 36 completed
Task runs and all 31 adjacent completed-run pairs. Each saved user message was
bound to its run time window, its `Current semantic Project/Task context` JSON
was extracted without re-rendering current database state, and the exact
submitted bytes were hashed. **No adjacent pair had the same full semantic
context fingerprint.** The median extracted context was 337,713 characters
(range 285,561–419,557). Three additional recent completed runs in one shared
Claude CLI session were excluded because the draft applies only to Codex and
that session is not in Codex native history. Older shared sessions were not
used to extrapolate current benefit.

The most recent Codex session does contain seven pairs of identical full
semantic contexts among runtime attempts for the same source. Six pairs have
native compaction between the full submissions; the sole pair without
compaction follows a failed runtime attempt (`codex_oauth_model_unsupported`),
which cannot seed the draft runner's successful prior-context state. These are
not seven eligible, distinct completed Task decisions. In the 31 completed-run
pairs, 13 had no compaction between turns and 18 did; none met the draft's
exact-fingerprint gate. PR #125 therefore remains closed: its Task half has
zero observed eligible production pairs in this bounded recent comparison.

Different current Work Items still carry much overlapping historical data:
across the 31 adjacent pairs, the median byte-identical row payload was
101,574 characters of `source_documents`, 58,538 of `source_signals`, and
11,210 of `project_contexts`. This is an overlap measurement, **not** safe
prompt savings: a changed current source makes the full context differ, and
compaction or missing model-visible history can make old rows unavailable.
It identifies a separate row-level retrieval/projection question, not grounds
to reactivate #125 or suppress prior source facts.
These native turns predate PR #128's within-one-input sharing of exact duplicate
`visible_ranges` and `decoded_excerpts`, which is already deployed. The row
overlap above measures identical rows **between** different inputs; it must
not be counted again as the within-input saving from #128 or presented as a
post-#128 production input length. No natural Task run after that release was
available in this readback.

Acceptance for this specific optimization would require a complete, repeated
production trajectory showing byte-identical context across adjacent Tasks,
measured input savings, and a fixed case preserving Task/Project decisions,
source citations, and no cross-item omission. The current evidence disproves
the first condition in the recent applicable sample. Do not merge or deploy
the Task half of #125, or add fallback/prompt rules to make the draft fire
more often. Any row-level alternative needs its own full-trajectory root
cause, exact current-source and citation-preservation criteria, fixed
business-decision comparison, and native visibility proof.

## Consumer stage-pair correction diagnostic, 2026-10-18

Read-only production records show nine Consumer result failures across three
reply tasks with the same `later stage requires predecessor_review_id` detail.
The latest task had six failed revision attempts after one completed Consumer
candidate and one Audit return. Its complete native trajectory is available in
Codex's paginated thread history even though the JSONL files are absent: the
original Consumer session has 34 projected items across four turns, the Audit
session 16 items, and two fresh retry sessions nine and ten items. The stored
inputs, tool calls and returns, model outputs, and terminal run records were
compared. All 18 business MCP reads in these four sessions report completed.

The service input told the revision to echo
`{"stage_index":0,"predecessor_review_id":null}`. Each failed output instead
returned stage 0 with predecessor ID 1. The result validator rejected this
invalid pair but described it as a *missing* predecessor for a later stage;
the correction prompt repeated that misleading detail. Subsequent same-session
and fresh-session attempts repeated the same invalid pair. The binding itself,
Audit feedback, stage budget, and business decision are not changed here.

Acceptance for this one defect: stage 0 with any predecessor ID must still
fail with a diagnostic saying that stage 0 requires a null predecessor; a later
stage without an ID must still fail with the opposite diagnostic; valid pairs
must still parse; and an anonymous same-session correction must remove only
the extra ID while preserving the other JSON fields. The new wire-boundary
regression failed against the old message and passed after the change. The
affected Agent contract and turn-runner tests pass (144). One read-only native
Codex OAuth fixture with the configured `gpt-5.6-luna` model returned the
deliberately invalid pair on its first turn, then returned a Pydantic-valid
stage-0/null pair on resume with every other JSON field identical and no tool
calls; the receipt is
`evals/trajectory_optimization/consumer_stage_diagnostic_native.v1.json`.
This is controlled correction evidence, not a real post-release business turn.

### Release readback

PR #135 merged as `3ab4f2fe` after independent review found no issues.
The affected contract, turn-runner, Consumer, and orchestrator tests passed
(352 passed, 4 skipped); Ruff and whitespace checks passed. Formal
`python -m app.deploy` waited for the four items that were in flight at the
preflight check and advanced the production checkout from `8b480c30` to
`3ab4f2fe`. The new launchd supervisor PID was 74487 and `/healthz` returned
HTTP 200. The subsequent in-flight count and dispatcher running counts were
zero. Status, Attention, and History APIs returned HTTP 200; Attention still
contained seven existing items. The newest failed History item occurred at
10:19:39 UTC, before the 11:29 UTC deployment, while newer History items were
completed. The existing system-health `degraded` violation remains and is not
evidence of a new stage-binding failure. GitHub Quality was still running at
readback and is supplementary to the local and fixed-case evidence.

This release verifies that the new diagnostic is installed and the service
restarted cleanly. No natural post-release Consumer correction turn has yet
confirmed a business outcome.

## Recent Audit native-input inventory: no change, 2026-10-18

The complete saved native turns for 96 production Audit runs created from
2026-10-16 through the 2026-10-18 07:15 UTC run are available in paginated
Codex history. The run ledger has 95 completed role runs and one failed role
run; completion alone does not prove the business judgment. The failed run
returned extra fields outside the Audit result contract and its same-task
retry completed. These runs contain 470 MCP calls, of which 14 report tool
failure. Seven thread-reply reads report that the message detail has no
conversation/thread identity; four document reads report a spreadsheet or
AI table rather than an online document (three are retries on one task).
The remaining three are a forbidden text path, a Drive byte limit, and a
forbidden Skill path. All runs with those tool failures nevertheless reached a
completed Audit result. This is a tool-selection inventory, not proof that the
result was correct or that treating a tool error as an empty source is safe.

The saved Audit task inputs range from 12,701 to 53,300 characters, median
27,096. All 96 include candidate source bindings. In 93 inputs, the exact
Task source matches the captured candidate binding and the original trigger
uses a source reference instead of copying its text a second time. The three
nonmatching inputs retain the complete original source and candidate binding,
as the released equality gate requires. Candidate source bindings themselves
have median serialized length 8,621 characters; current scalar-field evidence
has median 3,205 characters. Those evidence sections were introduced for
specific Audit correctness failures. Their size and overlap with the full
candidate are not sufficient grounds to remove them or alter an audit rule.

No repeated, high-impact Audit failure mechanism is established by this
bounded inventory. The seven thread-reply errors and four wrong-type reads
are concrete candidates for later trajectory review, but this sample does
not show a wrong decision caused by either tool error. The three scheduled
Audit turns predate PR #133's production release, so they cannot validate its
new Consumer document-return handoff; its fixed anonymous case and deployment
receipt remain the current evidence. No prompt, Skill, tool, result contract,
audit policy or provider behavior changes in this inventory. The next Task
input investigation must start with a natural post-PR #128 native input and
measure row-level overlap without counting PR #128's already-shipped
within-input source sharing a second time. Any removal needs fixed cases
preserving current-source facts, Project/Task decisions and exact citations.

## Next selection gate: Task historical-row scope, 2026-10-18

Read-only current-run lookup still finds no Task Agent run created on
2026-10-18 after PR #128. To separate its shipped within-input sharing from
the remaining inter-turn overlap, 47 complete native Task inputs from six
pre-release sessions were passed through the current pure
`share_source_document_projections` function, without invoking an Agent or
rebuilding a source from today's database. All 47 simulated inputs became
smaller, by a median 20,917 characters. Across 34 adjacent turn pairs with
different current source IDs, the simulated payloads still share median
83,099 characters of exact `source_documents` rows, 59,089 of
`source_signals`, and 11,210 of `project_contexts`. These 47 turns include
runtime attempts and are not 47 independent business decisions. The numbers
are a deterministic counterfactual on old inputs, **not** measured production
input savings or evidence that the shared rows may be omitted.

This candidate meets the repeated-size screen but not the safe-removal gate.
Before changing retrieval, inspect a natural post-#128 Task input and its
complete trajectory to identify which historical rows were needed for its
current-source assessment, prior Task matching, owner evidence and citations.
Then freeze paired cases that preserve those decisions and exact cited source
IDs while testing a narrower row selection under the same model and facts.
The change should be limited to Task retrieval/projection and its necessary
documentation; report submitted-length reduction and any lost or changed
business result. If the fixed cases cannot prove those invariants, keep the
present complete historical rows. Do not reuse an earlier turn's rows merely
because their bytes match: native compaction may have removed them from the
model-visible session.

## DingTalk recent-message raw-payload sharing: isolated candidate, 2026-10-18

**Selected mechanism and finite acceptance.** Complete saved native Consumer
and Audit trajectories show that `agent_cli.read_dingtalk_messages` commonly
returns the same message body in top-level `content` and the same message's
`raw_payload.content` / `raw_payload.text`. Among 120 successful saved MCP
reads from 142 recent role sessions, there were 3,553 messages. Their native
model-visible tool-result text totaled 9,610,958 characters; serialized
`raw_payload` values contributed about 5,176,572 characters and exact repeated
raw string values about 2,708,270. In 105 of 120 calls, exact repeated raw
strings alone exceeded 5,000 characters. This is a repeated tool-result
assembly issue, not evidence that the underlying message content should be
truncated. The fixed gate is to reduce duplicate bytes while preserving exact
reconstruction of every raw payload, all message fields and order, the current
Consumer/Audit judgments and reply targets, and unchanged DWS query, failure,
Prompt, route and audit-policy behavior. A changed business judgment or a
nonreconstructable raw field fails the gate.

**Local checks.** Deterministic read-only replay of all 120 saved successful
tool results reconstructed each of the 3,553 original `raw_payload` objects
from the projected form, including key order, and preserved every original
top-level field. Of those messages, 3,031 had a shorter projected form.
In the initial compact-JSON replay, length fell from 8,317,652 to 5,820,948
characters across the 120 result objects, saving 2,496,704 characters
(median 15,436 per call, maximum 98,908). Independent review found that a
compact-JSON size gate could let short duplicates enlarge FastMCP's actual
indented tool text. The gate now measures the message inside the `messages`
array with FastMCP's real JSON formatting. The pre-correction compact savings
are a size baseline, **not** a final model-visible saving claim; the corrected
gate may retain more short raw values. A regression invokes `FastMCP.call_tool`
at the short-value boundary and confirms the original result is retained;
additional lengths and Unicode confirm non-growth and lossless expansion under
the actual format. The focused tool/projection tests passed (91 tests), as did
Ruff. Four anonymous, no-tool, read-only Codex OAuth
comparisons checked the original and projected forms of two Consumer message
cases and two Audit candidate cases. Both Consumer forms returned `no_action`
for a previously handled confirmation, and targeted the same unanswered
question without inventing a backend action. Both Audit forms approved the
grounded `no_action` candidate and returned a reply containing unsupported
backend claims. These small cases check interpretation of this representation;
they do not prove the full production role workflow or a new business outcome.

**Production readback.** Independent review found no remaining Critical or
Important issue after the FastMCP size-gate correction. PR #136 merged as
`808f5579`; `python -m app.deploy` advanced the production checkout from
`3ab4f2fe` to that commit after its normal quiet wait and backup. At the
2026-10-18 12:22 UTC readback, launchd supervisor PID 819 was running from
the new checkout and `/healthz` returned HTTP 200 with `status=ok`. Console
Status, Attention and History returned HTTP 200. The queue view had no
processing items; its six pending items were Email provider actions. The
scheduled dispatcher showed two running scans, while other dispatchers showed
zero running; Attention totaled seven, and the five latest History items were
`done`. The existing overall health `degraded` indicator remained. GitHub
Quality was still in progress at this checkpoint and is supplementary to the
focused behavior validation. No natural post-release Consumer/Audit message
read has yet established a production business judgment; the local replay and
fixed cases establish the lossless output contract under their tested inputs.

## Email Classifier category-table duplication: no-go, 2026-10-18

**Observed input and proposed boundary.** The latest readable native Email
Classifier turn submitted 15,937 service-prompt characters. Its managed Skill
contained a fixed nine-category reference table, while the same submitted
input also contained current `allowed_category_keys` and descriptions for 11
categories, including `finance` and `invoice`. The current prompt assembly
includes both by construction, so the duplication recurs across classifier
turns. The candidate replaced only the fixed Skill table with a short pointer
to the current category descriptions; no runtime or Skill file was changed.
The finite gate required every fixed case to preserve the category, importance
flag, and unsubscribe candidate index, as well as a valid wire result. A
shorter prompt alone was insufficient.

**Paired fixed cases and decision.** Eleven anonymous messages, one per current
category, were run once through each arm using the same `gpt-5.6-luna` Codex
OAuth model, synthetic category descriptions, message facts, read-only CLI
settings and no business MCP. All 22 results passed `AgentClassificationWireResult`
validation and made no tool calls. The candidate saved exactly 1,649 prompt
characters per case; all categories and unsubscribe indices matched. The
`external_billing` case changed `important` from `false` in the baseline to
`true` in the candidate. This breaches the predeclared preservation gate, so
the candidate is **not approved for implementation or deployment**. A single
paired sample does not establish whether the shorter instruction caused the
change or whether model variance contributed. The sanitized fixture, prompt
hashes, and all 22 parsed outputs are in
`evals/trajectory_optimization/email_classifier_category_table_pair.v1.json`.
The next investigation must resolve the importance behavior with a repeated,
fixed comparison or an explicit product-level importance criterion before
reconsidering a Skill change. No real email classification or provider action
was part of this evaluation.

**Exact-input repeat, 2026-10-18.** The saved anonymous case and the original
evaluation script were used to reconstruct both prompts byte for byte; their
SHA-256 hashes match the v1 receipt. Two new calls per arm, interleaved under
the same `gpt-5.6-luna` read-only invocation, returned valid wire results and
made no tool calls. Together with the original pair, all three baseline calls
classified the subscription receipt as `external_billing` with
`important=false`, while all three shorter-Skill calls classified it as
`external_billing` with `important=true`. A preliminary two-per-arm probe had
used a simplified sender shape and produced a different prompt hash; it is
recorded separately and is not counted as a reproduction of the original
comparison. The exact-input result strengthens the no-go: removing the table
reliably changes an importance decision in this fixed case. No Skill or runtime
change follows. The compact, sanitized repeat receipt is
`evals/trajectory_optimization/email_classifier_category_table_repeat.v2.json`.

### DingTalk Sheet range style sharing, 2026-10-18

**Observed failure boundary.** Four production tasks' native trajectories had
28 `read_dingtalk_sheet_range` calls; ten in one task produced about 3.69 million
characters of tool output. Its `A1:BV66` return reached the native 1 MiB text
boundary twice, leaving saved MCP text unparsable, while a separate read-only
DWS command for the same range returned valid JSON. A 534,380-character saved
partial-range result had 1,970 styled cells but only 28 distinct style objects;
styles occupied 253,873 compact JSON characters. This supports a tool-output
transport/representation issue, not a DWS JSON defect. The saved native result
does not by itself prove exactly what the model retained after truncation.

**Finite gate and edit.** Preserve every row, column, value, style, order,
provider status and failure; a small response must not grow. A large anonymous
range must remain parseable at the native tool boundary, and fixed value/style
judgments must match the original format. Only the range tool's returned
representation changed: exact repeated `cellStyles` objects use
`cellStyleRef` plus a top-level `sharedCellStyles` array when the actual
FastMCP serialization shrinks. No query, budget, prompt, role, route, session
or policy change was made.

The focused regression started with two failing Consumer/Audit tool-boundary
cases, then passed after the edit. The failure-receipt preservation case also
failed before the explicit success check and passed after it. Independent
review found that missing, null and nonboolean success fields also needed to
remain unchanged; four new cases failed before the strict `success is True`
check, then passed. All 111 affected local tests and Ruff pass. A synthetic
66-by-74 range exceeds 1 MiB in the old representation, falls below 1 MiB
after sharing, and reconstructs to identical ordered JSON serialization. In the fixed
anonymous Codex OAuth comparison, original and shared outputs both identified
the same three red-font cells, their 161 sum, and D12's value/color; the MCP
text fell from 22,491 to 7,036 bytes. A separate large candidate run delivered
all 66 rows and 74 columns as valid 346,039-byte JSON; the model read the
bottom-right value 4,884 and style color `#000003`. A 352,640-character native
transport probe also retained its final marker. Compact results and hashes are
in `evals/trajectory_optimization/sheet_style_sharing.native.v1.json`.

A read-only repeat of the real 66-by-74 DWS range through the implemented
helper returned `success=true` and 79 unique styles. Its FastMCP-formatted
result fell from 1,327,319 to 367,854 bytes; expanding every reference
restored identical ordered JSON, and the projected JSON parsed. This probe did
not submit business cells to a model or change the source workbook.

These anonymous runs verify representation and model visibility in the fixed
cases. They do not establish a real post-release business outcome.

**Release readback, 2026-10-18.** Independent review closed its one material
finding before PR #146 merged as `2246d5184de62a4f629967fc0effbdb2b6504d10`.
The official `python -m app.deploy` path waited for a quiet window, backed up
the database and advanced the production checkout from `91da74b8` to that
merge commit. The production checkout was clean; launchd reported a running
new PID, `63135` (previously `77471`), and `/healthz` returned `ok=true` and
`status=ok`. Console readback showed 13 queues, six pending items, zero
processing items, 12 failed items, zero retryable items and seven Attention
items. The failed and pending queue counts and seven Attention items were
already present before deployment; no new failed or stuck work was observed.
History reported 36,080 total records. A real business Sheet run after this
release has not yet been observed, so no production judgment outcome is claimed.
GitHub Quality remained in progress at the time of this readback; it is a
supplemental check, not the basis for the behavior acceptance above.

## Next trajectory selection gate, 2026-10-18 14:46 UTC

The current production database has 91 Consumer and 63 Audit role runs created
since 2026-10-17. Their exact native attempt ranges were checked against the
Codex session files, not rebuilt from today's configuration. Only the three
Consumer and three Audit runs for tasks 405419 and 405421 still have readable
complete native records; their ranges include terminal `task_complete`
markers. Older session files referenced by the run records are currently
absent. The latest Task Agent run remains on 2026-10-17, before PR #128, and
the latest Meeting planner native session is also no longer available. These
are availability facts at this checkpoint, not a conclusion that older runs
failed or that their decisions were wrong.

The six available role trajectories cover a scheduled Daily Report and a
message-triage task. Their service-submitted Developer sections are about
36–37 thousand characters each; Task sections range from 1,196 to 20,930.
No exact paragraph of at least 120 characters was repeated between Developer
and Task in these inputs, and the exact Skill bodies read through
`read_task_skill` were not already embedded in either submitted section. This
does not rule out semantic overlap or CLI-injected material. In the scheduled
Consumer turn, all five selected Skills were read before the 22,288-byte
compact service-facts result, including the optional Minutes Skill; this is
one observed run, not repeated evidence of a generic scheduled-task defect.
The Audit turns reread their selected Skills and facts for separate proposal
revisions; their independent source verification is part of the current
review behavior and cannot be removed based on byte overlap alone.

No new runtime change passes the next selection gate today. The Task
historical-row candidate above has repeated byte overlap but still lacks a
natural post-#128 full trajectory and fixed cases proving that current-source
facts, Project/Task decisions and exact citations survive narrower retrieval.
Draft PR #127's speaker-index arm failed its fixed sensitive-audience case;
the separate native Skill duplication it also addresses has not been shown to
remain after PR #132. PR #129 changes only one Daily Report seed and its two
synthetic comparisons show mixed token totals, so it does not establish a
general scheduled-task improvement. Keep both candidates unreleased; do not
add a prompt rule, remove historical rows, or broaden a task-specific edit to
scheduled tasks without the missing trajectory and behavior evidence.

## Task acceptance-field native-schema no-go, 2026-10-18

**Selection and boundary.** Natural post-#128 Task Agent trajectories are now
available. In run 376298, its rejected decision attempt 376303 populated
`acceptance_polarity` on eight `create_task`/`none` items, then exhausted the
result repair and failed. The first attempt of the successful rerun, 376310,
repeated that exact class of error on four items before later correction. An
independent anonymous native Task probe on 2026-10-10 recorded the same
accepted-polarity/`none` mismatch. The current flat native schema permits
these combinations; `TaskDecision` correctly rejects them. This is an output
representation gap, not permission to relax the business validator. The
adjacent Task inputs of about 350,000–370,000 characters also repeat historical
rows, but their relevance to duplicate suppression and Project matching remains
unproved. This unit did not change retrieval or source projection.

**Finite candidate.** Require native schema rejection of polarity/target ID
outside `apply_acceptance`, require `accepted` and a positive assignment signal
for that transition, and retain all other Task fields and domain checks. No
prompt, Skill, route, session or repair-budget change was proposed. Native
Codex OAuth `gpt-5.6-luna` rejects `allOf`/`if` both at the root and nested
Task object; it accepts nested `anyOf` object variants. A temporary complete
Task schema with two variants was accepted by the provider and produced a
structured result. The generated candidate's compact JSON grew from 26,725
to 34,743 characters; the semantically equal temporary file used by the native
probe was 34,719 characters because integral numeric bounds serialized as
integers rather than floats. The candidate was never committed or deployed.

Six temporary structural regressions failed against the old schema and passed
with the candidate; 179 focused Task model/schema/repair tests then passed.
One wider Work Tracking Skill test failed because the machine's installed Skill
text lacks the phrase expected by that test, while the repository Skill contains
it; the test and both Skill files were unchanged here. The direct prompt-only
model probes without the Work Tracking Skill produced unrelated cross-field
errors in both arms and were not used for acceptance.

**Behavioral no-go.** Identical anonymous service-built Work Tracking prompts,
installed Skill and model were compared under old versus candidate schema with
business-domain validation, not just JSON parsing. In a two-speaker assignment
case, both arms created a formal Task in one of three cold runs; domain-valid
results were one of three baseline and two of three candidate. This case is
variable and does not establish improvement. In the clearer self-commitment
case (fixed prompt SHA-256
`082d08e929038c3383df8095688a17b0466a89556e8e21498ad95fb0160aee12`),
both baseline runs created the formal Task and passed domain validation;
the candidate created it in one of two runs, but returned **zero Tasks** in
the other despite recognizing a self-committed deliverable. A paired existing
Task acceptance case initially lacked `reply_to_source_ref`, so both arms
correctly skipped it. After adding that required binding (fixed prompt SHA-256
`5f3cd22380c75edaaff0d49b6513206c5cce10b4feaccaf20afbd6203ac04091`),
both arms returned the same domain-valid `update_task`/`apply_acceptance` with
accepted polarity and the exact assignment signal ID.

The small native sample does not prove that the schema caused the missed Task,
but it fails the stated no-regression gate and increases schema input. The
candidate code, schema, tests and test-only dependency were removed from the
worktree; no candidate runtime PR merge or production deploy follows. Do not
replace the validator with silent normalization or add a case-specific prompt
rule. A future representation candidate needs a fixed source/model comparison
that preserves formal-task creation as well as acceptance, plus an actual
post-release Task run before any business-outcome claim. Compact anonymous
output digests and per-run judgments are recorded in
`evals/trajectory_optimization/task_acceptance_schema_no_go.v1.json`.

## Post-PR #128 natural Task input: overlap confirmed, removal not yet justified (2026-10-19)

The complete native session `01a14fbe-74e2-7102-bee5-baa8aa7de92f` is now
available. It contains seven full Task Agent inputs after PR #128's within-input
source sharing, rather than a reconstruction from the current database. Their
submitted lengths are 355,260–440,430 characters. In one 371,642-character
input, the rendered semantic-context JSON is 341,308 characters; its 63
`source_documents` occupy 151,400 serialized characters and its 109
`source_signals` occupy 68,238. The source projection reports 1,670,325 body
characters before sharing, 922,850 unique body characters, and 150,371
visible body characters. This confirms that PR #128's within-input sharing is
present; the before-sharing metric is not a second potential saving.

For each of six adjacent inputs whose `current_work_item` differs, exact
serialized historical rows repeated across the pair total 249,908–272,031
characters (median 260,270) across the project/task/source/attention
collections. Exact `source_documents` rows alone account for
110,696–121,145 characters per pair. These are submitted-input bytes, not a
measured model token or latency reduction. The repeated rows can still provide
evidence for cross-source Task identity, Project matching, duplicate
suppression, and exact citations. Session history may also compact, so a hash
pointing back to an earlier turn is not proof that the Agent can still inspect
the source. No row was removed, no source was truncated further, and no
runtime prompt or retrieval code changed in this checkpoint.

Before changing this boundary, use fixed source/model cases to compare the
same current source under full context and a proposed narrower context. Require
formal Task creation, existing-Task updates, Project decisions, duplicate
handling, and exact source citations to remain correct in both cold and
continued sessions. A smaller serialized input alone does not pass that gate.
The separate native Task acceptance-schema candidate remains no-go on its own
behavioral result above.

### Cross-turn omission gate: previous input is not a durable evidence store (2026-10-19)

The candidate considered here was to omit exact historical semantic-context rows
from a later Task input when the same shared native session had received them
earlier. Its acceptance condition is stronger than a shorter input: every later
turn, including cold start and after native compaction, must still be able to
inspect the exact source version and excerpt needed for formal Task creation,
existing-Task updates, Project decisions, duplicate suppression, and citations.
If that evidence path is absent, there is no reason to run a model comparison or
change retrieval.

I re-read the complete saved native session
`01a14fbe-74e2-7102-bee5-baa8aa7de92f` rather than inferring continuity
from its session ID. It has 11 submitted Task inputs for nine distinct Work Item
payloads. Ten of those inputs are followed by a native `compacted` event; nine
of the ten adjacent Task-input pairs have such an event between the two inputs.
The first seven inputs are the post-PR #128 sample quantified above. The
session's compaction record establishes a history replacement, not a promise
that every prior `source_documents` row, version and exact citation span remains
available in the next model window. The shared session therefore cannot serve
as the sole evidence store for a delta-only next input.

This candidate fails the evidence-availability precondition and is **no-go**.
No historical row was removed, no extra prompt rule or fallback was added, and
no runtime code, test or model route changed. Existing fixed business-case
results were not rerun because there is no changed candidate to validate; a
model's success on one continuation would not establish exact access across
these compaction boundaries. A future narrower input requires a durable,
source-versioned on-demand read path available to the Task Agent, then the
fixed cold/continued decision and citation comparison stated above. This
finding does not reject relevance-based retrieval in general; it rejects
reliance on earlier session input as the evidence transport.

## OA process attachment read path: repeated failure root cause (2026-10-19)

Task 405641 produced 217 `dependency_read_unavailable` Consumer runs between
10:01 and 18:08 on 2026-10-19. Complete native first, middle and last failed
trajectories show the same two submitted OA form attachments: generic
`read_dingtalk_drive_info/file` returned `forbidden.accessDenied` or “没有权限访问”,
while the Agent repeatedly re-read the OA and Skill and did not form an
approval decision. The error was treated by the service as an external
dependency wait without a turn limit. This is the observed loop; no generic
retry-policy change is made here.

The DWS OA-specific read-only `attachment download-url` returned a signed URL
for each of the same two exact process/file IDs, and the URL returned the
material bytes. The first Markdown file was 7,300 bytes with 3,414 characters
of extracted text. The second PDF was 1,412,701 bytes, with six pages requiring
OCR. The failure layer was therefore missing OA process attachment access in
the service role tool catalog and stale Skill guidance, not missing applicant
material or a need to add permission-granting behavior.

Acceptance for this round: Consumer and Audit can read a form attachment by
exact process instance and file ID through the configured DWS account, without
generic Drive permission or authorization writes; downloaded material retains
the existing byte, response, temporary-file and OCR-page bounds. The source
must still be read before a business decision, and no approval, Audit or retry
policy changes are included. A fixed test with generic Drive access denied
failed before the new tool existed and passed afterward; the role and scanned
PDF cases also passed. The current real files were read through the new helper
without recording their contents in the repository. Post-deployment tool
visibility was read back after deployment; a future natural OA review still
needs to show the Agent uses this tool and makes a correct business decision.

Separately, the final approved OA comment for this task returned no comment
ID and was marked uncertain. Exact provider readback found one matching
`ADD_REMARK` by process, author, body and post-attempt time. Existing execution
reconciliation recorded its action as verified without resending, and the
current comment task closed as done. The OA approval itself remains pending;
the comment receipt does not prove the underlying project review is complete.

The scoped release was merged into `main` and deployed with the approved Skill
publication: 67 focused tests passed before release; the release preflight
matched all 16 source/installed file digests, six managed bindings and five
scheduled references. A follow-up regression found duplicate registration of
the new read tool in the native role server; it failed before the correction
and 34 focused role/material tests passed after merging the correction with
current `main`. Production commit `223788a2` includes both fixes. Live
Consumer/Audit tool catalogs contain `read_dingtalk_oa_process_attachment`
once; the installed OA Skill SHA-256 is
`4efbf60a185d533293382d4d9e8bbfec26799ce96a6c92bf2d15402b2734236f`.
Service PID 8796 reported healthy with zero system-health violations,
processing queue zero, Attention count four without task 405641, and History
HTTP 200. The exact task and execution read back as `done`, with its action
attempt verified. This proves release and receipt reconciliation, not a
subsequent real project approval.

## Claude Consumer native wire format: repeated malformed results (2026-10-19)

Task 405497 had four consecutive Claude OAuth Consumer failures on the same
business item before a Codex route completed it. I read all four complete
Claude session files, including their tool calls, returns and final messages.
Each Claude turn reached a terminal `end_turn` after reading the relevant OA
source and Skill. The first final message included a fenced JSON object with a
syntax error; the other three final messages contained no JSON object. The
service's `claude_result_validation_failed` reports therefore reflect actual
model output, not a truncated CLI stream or missing tool response. This is a
format transport failure; it does not establish that the proposed OA business
actions were correct.

Acceptance is limited to Claude Consumer: its final result must be valid native
JSON and pass the existing full Pydantic wire and business validation after
read-only source tools, in both a fresh and resumed session. It must preserve
the no-action versus proposal decision, exact target identity and no external
execution in anonymous fixed cases. Audit, Codex, Friday, routing, retries,
permissions, Skill selection and business rules must not change.

The installed Claude CLI 2.1.269 supports inline `--json-schema`. An anonymous
minimal schema probe returned structured output. The complete Consumer wire
schema was rejected because its `oneOf` and `allOf` conditions are outside the
provider's accepted input-schema subset. A projection retaining field types,
references and required fields while removing only those three conditional
branches was accepted. The unchanged Pydantic model remains authoritative for
the omitted cross-field conditions. Focused service tests failed before the
new native command option and passed after it was connected only to Consumer.

Three anonymous read-only Sonnet probes used the existing synthetic MCP server,
with no production record, Work Profile, business MCP or external action: a
fresh already-handled message returned valid `no_action` after one read; a
fresh direct-confirmation request returned a valid `proposal` with the exact
synthetic recipient and `send_direct_message` operation after one read; and a
fresh/continued pair reused one Claude session, read the source on both turns
and returned valid `no_action` twice. These probes establish native schema,
tool and continuation compatibility for these fixtures, not production
business quality. The two affected test files passed with 227 tests and four
skips; a subsequent negative case confirmed that Pydantic still rejects a
`proposal` outcome without a plan despite the projected native schema. Ruff,
diff check and independent review found no blocking issue. Production deploy
and readback are still required.
