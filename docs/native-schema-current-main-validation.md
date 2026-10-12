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
