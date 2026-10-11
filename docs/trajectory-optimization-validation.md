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
