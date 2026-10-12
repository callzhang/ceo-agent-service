# Business role native memory scope candidate

Current production Consumer/Audit native trajectories include a16,098-character automatically injected CLI-home memory block in addition to service-owned Work Profile, common instructions and explicit task sources. This is a separate context layer containing workspace-level cross-project guidance and profile material; it is not the service's business Memory/MCP context. Removing it requires business validation rather than assuming every remembered fact is irrelevant.

The official [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) documents `memories.use_memories=false` as skipping existing-memory injection into future sessions. `memories.generate_memories` is a different setting controlling whether new threads become memory-generation inputs. The candidate adds only the use=false option to existing Codex Consumer/Audit role command construction and removes conflicting incoming copies of that exact key. It introduces no new setting API, CLI home or global file edit.

Service Work Profile, Developer/Task assembly, source retrieval, business Memory/MCP and durable-memory handling are unchanged. Standard, Task, coding, Claude and Friday command construction is untouched. Role tool lists, approvals, sandbox, schema and routing are unchanged. Existing session binding is reused without service resets or history rewriting. Active historical memory retention during native resume has not been tested; do not promise its effective-context behavior.

The new Consumer/Audit command regression fails against the old implementation. After the candidate,68 affected tests pass with Ruff/diff checks clean, including role tool boundaries, task-frozen Skill discovery, action contract lookup and prompt context tests. It verifies explicit incoming use=true is replaced by one use=false while a supplied generate=true and service instructions survive.

## Native injection control

A same-model, same-home, same-directory, same-input cold comparison uses a synthetic2+3 task. The39-element commands differ only in use_memories true/false. Both probes temporarily set generate_memories=false to avoid generating memory from test data; that option is not included in the production patch. Real MCP servers are disabled and the sandbox is read-only.

The native true session has exactly one16,098-character memory Developer block; false has none. All other Developer blocks have the same canonical hash and both results are sum5. Independent read-only review verified original commands, native records and hashes. This proves injection mechanics only, not business behavior. Local debug prompt-input true/false results omit the Memory block in both modes and are identical; debug rendering is therefore not accepted as verification of actual native turn context.

Evidence: `business_native_memory_scope.native.v1.json`. Full Consumer/Audit business cold/resume comparison, preservation of necessary user preferences through authoritative service sources, and production publication remain open. Do not deploy solely on the arithmetic probe or passing unit tests.

## Fresh production context readback

Read-only complete native slices and durable records are saved as metadata only in `current_business_trajectory_readback.v1.json`:

- Calendar task404297: Consumer runtime348301 and348312 share one session and load the same complete three frozen Skills again, totaling21,566 repeated content characters. Source dictionaries and actual model-visible content match exactly in both turns. This independently supports the reuse mechanism in PR62; it does not authorize cross-role reuse or unconditional skipping after compaction/source changes.
- Calendar error: the first model output shifts a correctly offset-qualified source time by seven hours and wrongly reports an overlap. The entire model-visible calendar result equals the complete MCP result, so missing/truncated time data is not the cause. The model's internal arithmetic is unavailable; repeated conversion is a hypothesis. Audit returns candidate433, Consumer produces replacement434, and Audit approves it. The source case is retained without another timezone prompt rule or source-format patch.
- Execution112/action109 is done/verified and provider success=true, while provider deliveryStatus=unknown and deliveryStatusKnown=false. This proves recorded platform acceptance, not independently observed recipient delivery. No new delivery policy is introduced.
- Triage task404299: candidate435 no_action and review427 approve, with no candidate execution. A preexisting processing reaction is present; do not call this zero external activity.

The inspected recent email actions follow existing deterministic model-classification plans and provider executors rather than separate LLM turns. MOVE/STORE/trash attempt statuses are distinct from live provider readback. Stored personal-category observation after actions12215/12216 supports that category/important state, while several recent actions have no joined observation. Those missing observations do not prove failure or justify replay. No action, email body or private provider locator is copied or resent.
