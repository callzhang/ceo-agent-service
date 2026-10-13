# Native schema integration on current main

Draft PR31 (3a4942a8) was initially integrated with main b451cfe7 and has now been refreshed to main fda0bc5d in the isolated codex/native-schema-current-main branch. No main merge, production deployment, prompt reduction or provider change is performed here. Native schema files and canonical business models remain unchanged.

## Confirmed integration repairs

Terminal Settings history now reads native input without RAM format-length metadata. The initial merge duplicated the Codex appendix on Codex previews and retained it on Claude previews: two real-file regressions RED, then GREEN. Native appendix separation uses the exact service-owned terminal boundary only for recorded Codex input (or old RAM native-format receipts without runtime kind). Claude business text quoting that appendix is preserved; its regression failed before this source check. Recorded but unrecognized format boundaries show unavailable in current previews and preserve the original in history. Unredacted original character lengths are never used to slice redacted input. There is only one native format version in this unshipped implementation; a future appendix change requires a separately verified historical-version design.

The runtime and preview share one native format PromptSection. The running input receipt records actual submitted Task and the format source/length. The old PR test read a RAM input after terminal cache release; it now captures the real live receipt during executor invocation rather than restoring duplicate persisted bodies.

Validation: affected Consumer/Audit/codec/Settings suite 215 passed, 4 skipped before final source-boundary additions. Final codec and Settings tests 44 passed after those changes. Ruff and diff checks passed. Independent read-only review found the Claude boundary issue; its correction and current-version scope passed re-review.

## Provider gate remains incomplete

Anonymous source data only; all role callbacks stubbed, no private Skill or business records, no external action dispatch. Complete unchanged schemas and the actual current role catalogs are used.

- codex_api Audit cold: provider HTTP429, zero source calls, no valid final result. Corrected Consumer cold: same HTTP429. Initial Consumer harness duplicated --skip-git-repo-check and exited before model invocation; that result is explicitly excluded from provider conclusions. No resume without valid cold.
- Thin-Developer qwen_gpu4 Consumer cold: native and canonical output valid but no_action instead of the required nonempty candidate; the one source call also alters the start timestamp. Not passed and not resumed.
- Thin-Developer qwen_gpu4 Audit cold: 48 successful repeats of the same exact source call in 180 seconds, no final result. Deadline stopped only the task-owned fixture process. Not passed and not resumed.
- Single-variable Audit source-content control finished: same model/schema/Task/tool declarations/effort/deadline, only source return reduced from nested maps to marker. It repeated successful source reads42 times and timed out without final result; nested return complexity is not a sufficient explanation. This is not yet a confirmed provider or schema defect. Prior no_action/marker compatibility gates do not prove this nonempty/current-input gate.

Evidence: evals/trajectory_optimization/native_schema_current_main.results.v1.json; complete anonymous native fixture files remain at the artifact directories recorded there for ongoing trajectory diagnosis. The subsequent contract-context controls below identify a missing service input in this thin fixture. These earlier results remain diagnostic failures and do not establish a production route gate failure.

The previous passing full-role fixture script is retained at /var/folders/74/yj2lxqs162q7rqzm0mj8nv1c0000gn/T/ceo-qwen-full-roles-ac8ubk_7/run.py: it used reasoning_effort low; current controlled runs use medium. A fresh marker-only Audit low-effort control completed with one exact read, valid native/canonical result, exact nonce/digest/revision and terminal completion, holding current Task/schema/tools/source/deadline unchanged. This is a diagnostic, not a production route/effort change or a confirmed causal conclusion.

Read-only GPU4 code/cache inspection confirms Responses protocol forwards reasoning_effort to chat-template kwargs. The actual cached checkpoint template (SHA256 0a20a4673d45476ed88dcb4a60b6af35ca202ae470fee6fd2bd419758aabd9ab) enables thinking in both modes; low supplies an explicit brief/focused/direct-conclusion instruction while medium leaves reasoning_instructions empty. The low/medium result does not yet prove why the model repeats reads, or justify production parameter/prompt changes. Subsequent controls below hold medium effort fixed and add the service field contract. Keep the original failures and do not promote cold results to continuation evidence.


## Contract-context controls correct the Qwen attribution

The earlier medium-effort failures used a thin anonymous Developer instruction that omitted the service's actual Pydantic wire contract. They do not establish a current service/provider failure. Adding only the actual Audit canonical field contract (Developer 1691 characters) at the same medium effort, native schema, Task, tools, source and deadline produced one exact source read and a completed, bound, native/canonical-valid result. A generated 300-character Audit field guide (Developer 416) also passed this transport fixture. The previous low-effort result therefore does not justify lowering production thinking. Schema-off completed with invalid output, and removing the irrelevant Consumer dynamic-map explanation from the Audit appendix still timed out after 41 successful reads.

Consumer full-contract Developer 9195 preserved the supplied nested target/payload and returned the requested candidate, but repeated the exact source read twice, failing the fixture's one-read criterion. The 1010-character field-guide Developer made one read and valid JSON but replaced the supplied target with read-tool arguments. This toy echo capability is not a registered business action; it demonstrates that this particular reduction is not proven lossless, not that every Consumer needs the entire current contract.

A stronger public-code synthetic document Audit comparison used saved inputs prepared from role composition, an empty Work Profile, a synthetic Skill callback and identical Task/schema/candidate/digest. Full Developer 9553 versus field-guide Developer 8278 both identified source-only capacity/date conflicts and unapproved status, returned grounded revision feedback, and preserved candidate binding. Independent review confirmed those facts. However, the shorter arm omitted the existing required read_system_action_contract call. Its first search hit an unsupported stub; the fixture supplied document_id while treating it as node_id, so search cannot be classified as redundant. The full-document callback returned the same source and does not prove broader material coverage. Neither valid JSON nor this case permits adopting the shorter guide.

No production prompt, thinking, audit policy or provider configuration was changed for these controls. No cold result is reported as continuation success. Keep the field-guide prototype unreleased; repair the source-location fixture and support declared discovery before the next comparison, preserving the original failures and mandatory contract-read requirement. Evidence: evals/trajectory_optimization/native_schema_contract_controls.results.v1.json, with recorded result hashes and original local artifact directories.

Independent evidence review verified all seven recorded result-file hashes against the local originals. The business fixture retains final Developer/Task inputs, but its runner loads those inputs rather than retaining their generation script/configuration hash; reproducibility of input generation is therefore limited. These local hash records are not immutable storage.


## Corrected document locator v2

The v2 anonymous fixture separates document_id from node_id, supplies a MaterialReference with the native read entry, and supports the declared search tool. Both arms use identical Task/candidate/digest/schema. Only their service Developer output contract differs (1575 to 300 characters; total9549 to8274). The input generation script and configuration fingerprint are retained. Native host-skills/AGENTS blocks match, but arm working-directory paths differ in the environment context; this is not a claim that the entire native input has only one differing byte region. v1-to-v2 also changes the Task/source binding/digest, so only within-v2 comparisons isolate the service contract segment.

Both cold arms complete with valid schema/canonical results, exact revision/digest binding, grounded correction of source-only conflicts and explicit preservation of the original reply target. The contract-read result reverses v1: v2 full omits read_system_action_contract; v2 fields calls it successfully. Thus no stable superiority or causal contract-read effect is established. No continuation or production release is inferred. Both directly read the correct node, so the now-supported search path is not exercised. Evidence: evals/trajectory_optimization/native_schema_document_controls.results.v2.json and retained local original trajectories. The shorter wire guide remains a prototype.

The draft was subsequently integrated with main fda0bc5d (including selected action-table return a0be28d6 and independently scoped Audit field-evidence PR49). Earlier controls retain their actual earlier inputs/results; they are not relabeled as tests of the new rules. Canonical models and native schema files remain unchanged.

Latest-main merge verification: 107 codec, native/current Settings preview, action-catalog and context tests passed; independent read-only conflict review confirmed both source-selection and draft native-output behavior. No deployment or native-schema enablement follows from this merge.


## Current-rule field and destination controls

A fresh synthetic OA fixture compares the pre-PR49 application-result rules at a0be28d6 with the current rules, holding each candidate/Task/digest, full Audit schema, model, medium effort, deadline and read-only callbacks fixed. Both candidate variants pass the complete canonical model. Their action task_id is a string; the captured provider taskId is numeric. The wrong-destination variant changes only target_activity_id and its derived binding. No private Skill, Work Profile or business record is sent.

| Route and phase | Rules/case | Format and binding | Source-only judgment |
| --- | --- | --- | --- |
| Qwen cold | Before / correct | Valid | Approve |
| Qwen cold | Current / correct | 180-second deadline; no final | Not passed |
| Qwen cold | Before / wrong destination | Valid | Return for the wrong node |
| Qwen cold | Current / wrong destination | Valid | Return for the wrong node; feedback path omits proposal layer |
| OpenAI API cold | Current / correct | Valid | False task_id type correction |
| OpenAI API same-session resume | Current / correct | Valid | Approve; cold error retained |

OpenAI API capacity is available at this later timepoint; the prior HTTP429 records remain unchanged. API cold and resume use session01a12e61-204f-7363-bb25-6a288e93dd46. Saved inputs, native submitted Tasks, callback arguments and source returns match. The cold native Task contains candidate task_id as a quoted string, yet feedback says its literal is a number and requests the already-present string conversion. Submission/codec conversion is excluded for this case. Source numeric/candidate string confusion is a plausible inference, not a proven internal cause. The next identical same-session turn approves; this does not prove session reuse caused the changed judgment or that the new rules fixed the error.

These are bounded diagnostics, not complete OA acceptance: none reads the synthetic Skill; before/correct also calls an unsupported synthetic artifact-list tool. The synthetic legal return node is not a verification of the real OA Skill's fixed start-event convention. Single samples establish neither a causal improvement nor regression from the new rules. Current Qwen correct repeats contract/task reads and times out. No schema enablement, effort change or additional prompt rule is shipped. An initial fixture with unsupported TO_SPECIFIC_NODE was stopped and excluded; the retained controls use the supported REVERT_FOR_RESUBMIT.

Independent read-only review verified all six native trajectories and their input/source/final boundaries. Evidence: evals/trajectory_optimization/native_schema_audit_field_controls.results.v1.json. Later production observation at2026-10-12T04:25:23Z:947f4f24, PID67717, service healthy, all processing queues0, Attention2, one failed Reply and one failed Email provider action. This observation does not replace the earlier selected-contract deployment timepoint or claim those business failures resolved.


## Source order and prior-rule diagnostic

To test source/candidate confusion, only the source_bindings key was moved to the front of the complete candidate JSON. Its semantic value, digest, Developer, Task length5147, model and full Audit schema remain unchanged within each route. Both orders use the same synthetic callbacks, now supporting an empty native list_task_artifacts; this shared fixture correction prevents single-variable comparisons to the previous fixture. No facts are truncated, copied into new prompt rules or normalized.

OpenAI API again encounters capacity429 on three of four independent cold attempts. One failed attempt has a parseable intermediate typed message but a native turn.failed; it is excluded as a final result. The sole completed source-first attempt still misreports the quoted candidate task_id as a number. The earlier recovered API cold/resume evidence remains valid for that earlier timepoint; capacity is not continuously available.

OAuth completes all four cold attempts with valid full schema/canonical/digest binding. Original order0/2 correct approvals, source-first0/2 correct approvals: all incorrectly return for the already-string task_id. Full native user Tasks match the saved input hashes and contain no numeric candidate task_id. Moving source facts has no observed benefit and is not adopted. One original-order run also confuses the operation's completion state REDIRECT_PROCESS with its payload revert_action. Some cases omit the synthetic Skill or hit unsupported file-reader stubs, so none is a complete OA business gate.

Two additional OAuth cold controls restore the pre-field-evidence application rules, holding baseline Task/digest/source/schema/model/effort fixed. Both complete but make the same false type correction. Thus the added field reminder is not a necessary condition for this error, and no removal is claimed as a repair. Source/action-contract interaction remains a hypothesis to isolate; submission/codec corruption is excluded in the observed native inputs. No audit feedback filter, state override, extra rule, production change or native-schema release follows from these diagnostics.

Independent read-only review verified all ten results, including actual native input/terminal boundaries and exact tool targets. Evidence: evals/trajectory_optimization/native_schema_source_order_controls.results.v1.json.


## Contract prose and source-ID representation controls

Four OAuth cold controls hold complete Developer/Task/candidate/digest fixed; two contract tool replies exclude a single public identifier-format/source paragraph. All terminate and validate. Baselines return/reject with false candidate type corrections; one experimental arm approves and the other asks to change a correct string to the provider number and confuses completion evidence REDIRECT_PROCESS with payload revert_action. Since the removed paragraph also contains source requirements, neither the mixed outcome nor the altered error supports removing policy text. No removal is adopted.

Three further synthetic counterfactuals retain the complete original contract and current instructions, changing only a provider taskId number to the equivalent decimal string in both captured and live source, with complete candidate digest rederived. Two correct candidates approve. A wrong-node case finds the real wrong destination but names an incorrect JSON path and invents changing valid REVERT_FOR_RESUBMIT to completion state REDIRECT_PROCESS. It fails strict business acceptance. Source representation affects the observed judgments, but this does not prove the mechanism or authorize normalization of actual raw provider data.

A source-code parity audit also finds two important fixture limitations. These OA probes submit AuditTurnContext.render and the native appendix but omit the actual AuditAgent task Skill discovery section; AgentTaskContext.skill_names itself is not rendered as that entry. Moreover, synthetic frozen OA sources contain tasks/allowed nodes, whereas reviewed_sources.read_provider_source projects verified native process/form/applicant fields and does not freeze the task list. The source/type observations remain valid for the exact anonymous inputs; they are not full current-runtime Task/Skill business gates and cannot justify changing the production source-binding projection. The next comparison must reuse actual Task assembly and capture_candidate_sources with native-shaped synthetic records.

Independent review checked all seven native trajectories. No production code, source records, audit policies or schema enablement changed. Evidence: evals/trajectory_optimization/native_schema_contract_source_controls.results.v1.json.


## Task/source assembly parity and actual failure cross-check

The corrected anonymous OA input reuses render_task_skill_discovery, compose_plain_task_assembly and capture_candidate_sources. The selected synthetic Skill is read on demand and its complete body/SHA matches; no body is pre-injected. Frozen OA source excludes task transitions. Across three direct-runner and three adapter-built OAuth cold controls, all terminate with valid native/canonical/digest bindings. Both groups' correct candidates still receive false numeric-type feedback. The first group's negative correctly finds the wrong node and gives an exact relative path from ConsumerAgentResult, so its source-only negative judgment passes. The second additionally makes a false type correction and fails. Both groups' correct cases fail; the complete workflow is not accepted.

The role builder discards incoming skills.config and regenerates its existing default. Thus the adapter group did not change final native Skill visibility; it is not evidence that a narrower budget was tried. The duplicate command-build work was separately repaired and deployed on main54e6c209 with265 focused tests and formal release readback. It is unrelated to fixing Audit judgment or enabling PR31.

Local inspection of the actual pre-release failure run25843 reveals candidate.proposal.actions[0].target.task_id and frozen trigger_raw_payload.taskId are both strings. The same ID is numeric in real read_dingtalk_oa result.tasks and read_dingtalk_oa_tasks result.taskIdList. Actual tasks/activities envelopes use result.taskIdList and result.instRevertActivities; the latter supplies revertAction along with activity identity. No private ID values, business text or Skill body are copied into evidence. Changing the frozen source cannot be inferred as the repair. Further fixtures must preserve the real trigger string and observed native query shapes, and distinguish native provider representation from the canonical action.

These six probes still use synthetic common settings/Skill, empty WorkProfile and not the complete AuditAgent adopted-run database lifecycle. Their original trigger task_id number and simplified query envelopes remain explicitly recorded limitations, not retroactively corrected results. Independent review verified exact native inputs, Skill reads, source returns and finals. Evidence: evals/trajectory_optimization/native_schema_task_source_parity.results.v1.json.

Main has subsequently advanced through phase4A schema release7baa1f0e and the independently deployed role-build cleanup54e6c209. This draft still contains its earlier main integration; refresh the draft against current main before claiming current runtime/database integration or conducting lifecycle tests. Historical controlled results retain their original code/input timepoints.

Evaluation correction: the declared contract requires an exact path in the current candidate, not a mandatory candidate. Task-wrapper prefix. Re-review accepts the native-parity negative relative proposal.actions path; this does not excuse missing proposal layers, wrong field names or invented revisions in other results.


## Observed native source-shape correction

Three anonymous OAuth cold controls now preserve the observed historical trigger taskId string, numeric live task IDs, result.taskIdList and result.instRevertActivities including revertAction. All complete, read the full selected synthetic Skill and exact four contract/provider sources, and validate native/canonical/digest output. Both legal candidates approve without feedback; the wrong-node candidate returns only the precise target_activity_id correction and preserves valid ID and revert-action parameters. Independent full-trajectory review passes these three source-only business judgments. Two runs each attempt one unsupported synthetic AGENTS.md read.

This corrects the experiment, not production instructions or provider records. Several fixture details changed together, so no individual causal repair is proven. Empty Profile/common settings, synthetic Skill and direct adapter execution remain limitations; full AuditAgent lifecycle and real business acceptance are incomplete. Runs use81e586e3 before the latest main integration. Receipt: evals/trajectory_optimization/native_schema_real_source_shape.results.v1.json.


Latest-main integration checkpoint: merge dccbb023 incorporates main ad7a6506, including phase4A runs and single-build Skill configuration. Both newly appended Consumer tests are retained. The first five-file focused run reports218 passed,4 skipped and one failing draft-only test SQL reference to the removed agent_runtime_attempts table. Adapting that fixture to runs/record_type/legacy_key yields44 passed across native preview, codec and prompt preview files. Runtime code is unchanged by this fixture repair; no production enablement or deployment follows.


## Actual AuditAgent storage and session diagnostic

On472eed9b, temporary stores and the real AuditAgentRunner/AgentTurnProcess run two candidates through native complete-schema cold and same-revision continuation. Both legal turns approve in one session. Both wrong-node turns return in their own shared session, correctly locating the bad node but falsely claiming an already-string task_id is numeric. Their strict business judgments fail. All four submitted Tasks match saved/native input, Settings historical Task previews and string candidate IDs; actual source IDs remain numeric. The codec/submission layer did not convert candidate values.

A fixture-persisted Consumer R1 is linked to the previous Audit parent and changes only the genuine bad node. Its digest matches the legal candidate; Audit starts the existing fresh-per-revision session and still returns solely for the false numeric type. This is not stale same-session feedback alone and is not a fully model-generated Consumer feedback loop. No production fix, feedback filter or new prompt rule is adopted.

The anonymous setup uses temporary databases, synthetic common configuration/Skill and empty Profile, with all external MCP replaced by read-only callbacks. Setup errors before model execution remain excluded; successful cold is not rerun after the harness parent/revision correction. Receipt: evals/trajectory_optimization/native_schema_audit_lifecycle.results.v1.json.

## Current main and message Wire integration, 2026-10-13

Merge47b84bcb preserves main ea99a352, including the deployed fresh message
identifier validation and phase5A storage. Both independent claim rows were
retained; no authored commit was rewritten. The first affected run reports
357 passed,4 skipped,2 failed. Failures are test-boundary defects: the two native
projection files were not registered in the schema inventory, and the fresh
message lifecycle fixture sent a canonical map instead of the native entries
representation, failing before canonical identifier validation.

Projected files now use the existing non-direct-model inventory category; their
exact generator equality remains checked by native output tests. The lifecycle
fixture uses structural target/payload entries, retaining its numeric identifier,
exact persisted field-error and next Result Correction assertions. Five additional
native decode cases confirm numeric, boolean, object, array and blank identifiers
are rejected by the complete current Wire model after structural restoration;
a valid leading-zero string remains exact. Native grammar acceptance is deliberately
not treated as business acceptance. No runtime validator, business rule or fallback
was added for this test alignment.

Disjoint verification groups: native codec/schema inventory/message lifecycle110
passed; native preview, public preview, Codex adapter, Consumer and Audit257 passed,
4 skipped. Combined affected scope367 passed,4 skipped. Ruff and diff checks pass.

Local CLI is still0.154.0. Both generated native schema file hashes exactly match
the prior approved Qwen full-role compatibility probe (Consumer911ad565…, Audit
d328078c…). Current role declaration counts are42/38 versus the prior41/37, so the
old probe is not fresh acceptance of the current full tool catalog. This equality
proves structural output schema preservation only; it does not prove current
provider behavior or business correctness. Fresh configured-route tool/schema
controls and the unresolved complete business gates remain required. PR31 is
still a draft, unmerged and disabled in production.

Independent readonly review found no test/oracle weakening: exact dual-role native generator pinning remains, lifecycle numeric/field/correction assertions are unchanged, and native shape acceptance stays distinct from canonical business acceptance. Schema hashes and changed tool counts were directly checked; test counts were reviewed from the recorded runs without rerunning models/tests.

## Current configured native tool/schema controls

On a34e967c, native Codex0.154.0 uses actual configured qwen_gpu4 and
codex_oauth, current42/38 registered agent_cli tool declarations and the complete
unchanged native schemas. All business callbacks are substituted. No Work Profile
is supplied. Full-role controls use current production role producers and a fixed
anonymous Audit candidate; a final cohort delegates the actual registered
read_text_file function to an owned JSON file containing a nonce. API relay was
not invoked under the consumed one-off diagnosis permission.

Thin diagnostic cohort: OAuth Consumer/Audit cold+same-SID resume all pass exact
source-read/nonce/canonical/binding checks. Qwen Consumer times out (callback snapshot5 source reads; terminal native12); Audit completes valid output after2 reads, failing the singleton requirement.
Full-role cohort: Qwen Consumer completes after2 reads; Audit reads once but echoes
only the suffix of the prefixed nonce. Those controls remain failures. The actual
registered-reader JSON cohort removes that nonce ambiguity; OAuth again passes
all4 phases. Qwen both cold turns time out. No invalid cold receives a resume.

Full Qwen visible provider reasoning privately confirms it knows the correct
source nonce and complete result fields but repeatedly invokes invalid CodeMode
wait cells. Reasoning text is not copied into repository evidence. This locates
the observed runtime loop, not a proven provider source-code defect. Successful
source returns do not by themselves establish final output or business acceptance.

A diagnostic disabling the native CodeMode host/only/enabled flags allows one
Qwen Consumer cold to pass. A stricter comparison reuses the failed registered
case's exact service Developer/User/schema and unchanged source file: it completes
canonical no_action with correct nonce, but uses builtin exec_command/cat instead
of the mandated MCP reader, so it fails the original gate. CodeMode-off changes
builtin tools and native bootstrap too: actual Developer22003→22927 chars despite
the same service-submitted Developer hash. Do not call this an otherwise identical
model-visible prompt or isolated-function experiment. These are diagnostics,
not an adopted fallback, production mode change or complete provider acceptance.

Evidence/harness:current_native_route_control.v1.json and results. Current Qwen
full-runtime gate remains open; no provider package/reload, runtime source,
route/session policy, business rule or production setting was modified. The next
step is to isolate the CodeMode/native-bootstrap/guidance interaction from the
complete saved trajectory, not repeat these failures or add another prompt rule.

Independent readonly review confirmed all16 case counts, strict paired failure, native bootstrap differences and absence of reasoning originals in committed evidence. Native session originals remain in the CLI home; all16 file hashes and exact Task texts were verified. Owned probe temporary files were removed after process completion and an empty open-handle check.


## CodeMode custom-tool conversion defect

An anonymous non-forwarding local HTTP fixture captures the current native CLI
0.154.0 request using the actual Consumer role builder/catalog: custom `exec`,
function `wait`, function `request_user_input`,42 namespaced agent_cli functions
and web_search. No model is invoked; the fixture deliberately terminates with
`capture_complete`. A first setup failure lacked MCP declarations and is excluded.
No header values or input bodies are saved.

GPU4 installed vLLM0.29 `ResponsesRequest` accepts those exact tool declarations.
CPU execution of its real `construct_tool_dicts` produces44 function declarations:
wait, request_user_input and all42 agent_cli functions; custom exec is absent.
The installed `iter_response_function_tool_dicts` filters this custom exec. `_make_request` passes this conversion to chat rendering; the installed
Qwen structural-tag implementation also uses it for final-schema/tool grammar.
Thus the native execution entry and its description disappear while wait survives.
This confirms a protocol conversion defect, not a CLI serialization defect.
It does not establish that this omission alone caused every prior final-output loop.

Repair scope should be generic Responses custom-tool transport: preserve the
custom declaration through model/grammar rendering, return native custom calls
and stream events, and round-trip custom call/results in continuation input.
Do not rename exec as a service function, modify business prompts or silently
disable CodeMode. The full-role schema, registered-tool and business gates stay
required. No provider source or production configuration changed in this diagnosis.
Evidence and CPU checker:codemode_custom_tool_conversion.v1.json.

Timeout callback snapshots must not be treated as terminal call counts. Preserved
native terminal history contains thin Consumer12 source reads (snapshot5);
registered Consumer12 reads+34 waits (snapshot4), and registered Audit62 reads+2
contract reads (snapshot44). Both layers now appear explicitly in the result
manifest. Original failed snapshots and decisions remain unchanged.

Readonly review matched capture and CPU results, exact harness sources and native hash. The owned serialization fixture was removed after terminal completion and an empty open-handle check; CLI-owned native originals remain.


## Offline custom-tool transport prototype (not release-ready)

The five-file provider prototype preserves CustomTool declarations through
chat rendering, normal/namespace name lookup and native tool grammar; restores
custom calls and typed SSE events; and retains source calls for previous_response_id
continuations. It contains no exec-name special case. Lark/regex input declarations
are retained and postvalidated. Registered function transport and complete current
Consumer output schema remain unchanged. The installed five source files were
read back with exact original hashes after all checks; no provider edit/reload,
model inference, model weights load or business callback occurred.

Final fresh-process normal-import CPU regressions:12 tests pass on the candidate;
original reports12 failures and3 errors across subtests (not15 separate tests).
Ordinary function and function-stream controls pass in both. Readonly review found
a missing stored-response call path and custom lookup gates; both were reproduced
as two candidate failures before correction. The final suite includes stored-response
continuation, required/forced native grammar and multiple streamed call identities.

The complete current Consumer grammar accepts its final object in declared schema
order in both arms, rejecting free text and an invalid enum. Only the candidate
accepts the custom exec XML branch; converter declarations increase44→45 while
all42 registered functions remain. Canonical Wire serialization starts summary,
whereas the native projection intentionally moves summary last/actions after
evidence. Both grammars reject the canonical order at offset2. This is an independent
format-order observation, not invalid fixture data or proof of every Qwen loop.
The historical frozen comparison favored that native order in its bounded sample;
no native field-order change is made without reconciling that evidence. Do not use
xgrammar any_order=True: its installed contract relaxes required-key presence and
key uniqueness, so it would weaken this grammar.

Actual configured Qwen tokenizer/parser checks expose the remaining release
blocker. Two of four raw-source cases round-trip exactly; boundary newlines are
trimmed and literal XML end markers truncate source text. JSON-string framing
does not recover the source. A separately declared JSON-object framing diagnostic
restores whitespace (three of four exact), but still truncates the marker case.
Those observations remain failures. The12 helper passes do not establish end-to-end
freeform fidelity or native Consumer/Audit cold/resume success.

Artifacts:qwen_custom_tool_transport.prototype.patch/v1.json and the CPU checker,
normal-import loader, grammar/parser/object-framing checkers. Status remains
prototype_not_release_ready. Next work must establish an unambiguous text framing
and parser boundary, then repeat complete source/token/stream/role verification.
No prompt rule, model route, CodeMode toggle or fallback was adopted.

The exact owned prototype directory `/private/tmp/ceo-qwen-custom-tools.T8SsIm` was removed after a zero-open-handle check and preservation of the patch, checkers, anonymous results, and hashes. Installed provider source hashes were reread unchanged. Independent read-only review verified the saved artifact hashes and result counts; this review approves the evidence checkpoint, not provider publication.

### Framing diagnostic: generation and parsing disagree

The installed `vllm/parser/qwen3.py` converter trims one wrapping newline and extracts parameter values with markup delimiters; the engine also treats function/tool delimiters as transitions. A CPU-only actual-parser comparison now includes all six parameter/function/tool boundary strings obtained from that module. Ordinary JSON-object framing preserves 3/5 inputs; encoding every UTF-16 code unit inside the JSON string preserves 5/5, including Unicode, boundary newlines, and protocol markers. The actual required-tool xgrammar constraint accepts all ten outputs, including the two ordinary JSON outputs that the parser truncates. See `qwen_custom_tool_transport.framing_diagnostic.cpu.py` and its v1 result.

This establishes a generation/parser contract mismatch. Fully escaped encoding is a diagnostic, not the adopted repair: the present grammar does not require it, and no model behavior or streaming fidelity has been established. A permanent repair must align framing constraints and parsing rather than instructing the Agent to avoid marker text. No installed file, route, prompt or service was changed. Two initial fixture setup failures (absent auto constraint and unconverted Responses declarations) were corrected before the recorded successful run and are excluded from result counts.

### Framing grammar candidate

A private JSON-value envelope grammar now excludes literal `<` only inside the JSON string and permits ordinary JSON unicode escapes, so every input value remains representable without exposing markup as data. The outer Qwen tool/function tags are unchanged. This is protocol grammar, not Agent instruction or a business text filter. Against the same actual installed parser, 15 representations of five anonymous values yield 13 accepted, all exactly restored; the two ambiguous ordinary JSON forms are rejected. All 39 accepted-output stream checks pass: 1-character, 7-character text deltas and one whole-output delta with actual tokenizer token IDs. The previous grammar accepts both ambiguous forms, establishing the regression contrast. The first candidate fixture overescaped quote literals and was corrected before the recorded final run.

Files: `qwen_custom_tool_transport.framing_grammar.cpu.py` and `.v1.json`. This is still an isolated FunctionTool transport experiment. The five-file native custom-tool candidate has not yet been changed to use this envelope and grammar; arbitrary token-boundary streaming, custom format validation, Consumer/Audit model cold/resume and provider publication remain pending. No installed provider edits or reload occurred.

### Integrated native custom framing candidate (offline)

The v2 six-file candidate connects custom declarations to a private `input.value` JSON envelope, maps native stored calls to the same envelope, and decodes original raw input before custom format validation and native custom output events. Qwen custom tag contents use the lossless framing grammar; other tags and the complete final Consumer schema remain unchanged. The actual registered42-tool fixture accepts an exec call and a final Consumer result in declared schema order. This also fixes two exposed protocol gaps: no-final-schema auto originally lacked a custom constraint, and required/forced namespaced grammar used a name inconsistent with the serving conversion.

Final checks: **10 integrated tests pass, 12 transport tests pass**. Integrated checks include top-level/namespaced original input, empty/control/Unicode/markup data, actual incremental tokenizer-ID streams, full schema, validated required/forced request objects, stored history envelope, ordinary Responses and ChatCompletion function controls, optional auto free text, and disabled tools. The original raw-string candidate fails the initial four integrated tests (10 subtest failures,1 error); the no-schema missing-constraint RED is separately retained. The six installed source files were reread byte-identical to their copied originals. Patch, hashes and RED/GREEN outputs are in `qwen_custom_tool_transport.framed.prototype.v2.json` and `.framed.prototype.patch`; test scripts are `.integrated_framing.cpu.py` and `.framed.cpu.py`, using the existing fresh-process in-memory loader and recorded grammar fixture.

No provider file or model process was changed. Custom deltas still wait for a complete envelope. Original custom grammar is checked after decoding, not fully enforced during generation. Model-level Consumer/Audit cold-start and same-session continuation gates remain incomplete, as does a broader arbitrary-string comparison. This is an integrated offline candidate, not a release.

The validated-request fixture initially serialized without Pydantic aliases, dropping the external `schema` field name on reconstruction; this setup error was corrected with `model_dump(by_alias=True)`. The final validated-request10-test run passes. No-schema auto explicitly accepts ordinary final text and ordinary function calls, and `none` returns no tool constraint.

Independent read-only review matched all six source-pair hashes, the patch and checkers, and four preserved logs; it confirmed optional auto behavior and validated forced namespace choices. The exact owned temporary directory was removed after a zero-open-handle check. Only the patch, test code and concise anonymous evidence are retained.

### Additional corpus and provider publication boundary

The same six-file candidate passes129 reproducible seeded anonymous text roundtrips, including all ASCII characters, control characters, Chinese/non-BMP symbols and dense quotes/backslashes. The exact generated case digest is `81ad1a35b1637710523ab025cbf8185148b187dbf753ad918192b8821ae7c603`; see corpus checker/result. This is additional parser/output evidence, not model acceptance or exhaustive Unicode proof.

Current manager/source readback shows both Qwen replicas use the shared vLLM environment. At2026-10-13T16:54:33Z slots0/1 were ready with13 active requests each, ports9000/9010 and PIDs1340918/1142177. Slot1-only reload would leave mixed versions. A separate exact six-file, both-slot proposal is in `docs/qwen-custom-tool-provider-release-plan.md`; it preserves the no-atomic-drain arrival race and mixed-version interval and awaits explicit approval. No source modification or reload occurred.

### Native output contract prompt compaction

Codex runtime and current Settings preview now use the same `native_developer_assembly` to replace the exact service-owned canonical schema section with native structural-channel guidance plus every original unsupported canonical assertion. Consumer retains three complete assertion locations: DecisionOption, ProposedAction and _ConsumerWire; Audit has none. Other instructions, original validators, Claude/Friday inputs and historical native input remain unchanged. Partial receipts preserve unlisted Developer text; unmatched or repeated schema bodies preserve both original text and source receipts, preventing false provenance.

132 focused tests pass (new residual/assertion/partial/collision regressions, native output codec, composition, runner, preview and snapshot checks). Settings endpoint checks confirm the Codex supplemental contract source while Claude retains its full schema. Public core lengths, excluding saved Developer/Profile/runtime and task facts: Consumer21053→14885 (6168 fewer characters), Audit10045→8645 (1400 fewer). These are service-prepared character counts, not tokens or observed model-input reductions. See `native_contract_compaction.v1.json`. Fixed-model trajectory A/B and production acceptance remain pending; this candidate has not been deployed.

### PR78 OAuth native format/session paired control

Actual configured `codex_oauth` with current model and42/38 role declarations passes all8 full/compact Consumer/Audit cold/resume format controls. Each turn invokes the actual registered read_text_file callback exactly once, returns the current exact nonce and a complete Pydantic-valid result, and every resume retains its successful cold session. Native cold records contain the exact submitted Developer hash and each turn contains its exact Task hash; resume Developer evidence is the inherited cold message in the same session, not a newly observed Developer message. The harness passes the same Developer parameter each round. Each baseline/compact pair has identical Task, native schema, model and read arguments/results.

The initial8 native attempts are separately classified setup-invalid: the old reused harness lacked the task-binding database required by current role-server startup. Explicit MCP enabling alone did not repair it; creating a real temporary task/generation and verifying42/38 tool catalogs did. They are preserved as evidence, excluded from acceptance, and no new runtime fallback was added.

Cold cumulative input tokens: Consumer39036→36069; Audit29834→29112. Cache hits and output histories differ, so this is neither an isolated tokenization benchmark nor proof of lower cost. These are anonymous fixed no_action/approve probes, not business-quality acceptance for the four task domains; session handles are harness-supplied, not application binding verification. Qwen/provider publication remains pending. See `native_contract_compaction.oauth_control.v1.json` for all receipts, native original hashes and reproducible harness.
