# Task-scoped prompts: implementation and acceptance

## Scope and frozen versions

This change moves task workflows out of common Consumer/Audit instructions and into on-demand Skills. The service keeps current facts, candidates, feedback, and the canonical Pydantic contracts complete. Consumer sessions remain bound to conversation and runtime route; task-specific requirements and Skill entries are submitted on every turn, including resume.

The implementation was reviewed at `adb23e1b74984e5548bfd08c8ea48f5765db073b`. The final test-only correction is `ba3d5415fb8de8b66cefd6be61f614d57f6f6b0a`. Native output-schema PR #31 remains an independent draft and is not part of this release.

Project rules are in this repository's `AGENTS.md`, not the shared machine policy. The existing default calendar/timezone instructions were moved verbatim to the authored `ceo-calendar-invite` Skill. Default Developer publication uses the existing exact-SHA stopped-window publisher; custom templates remain subject to their explicit migration contract.

## Instruction and input receipts

Common Developer contains the role, user work profile, common principles, evidence/result contracts, and capability names. Current Task contains its selected requirements, minimal Skill entry, complete current facts, and continuation or feedback.

The existing read-only `agent_cli.read_skill` supports directory discovery with no non-null selector, exact installed-name reads with `name`, and the original authorized Markdown read with `path`. A directory response contains only name/description/path metadata. A selected frozen Skill is read through `read_task_skill(name)` from the original saved task. Its dependencies can be read by exact name after the Agent inspects that Skill; no dependency closure is preloaded.

Unknown tasks receive a 263-character discovery entry with zero catalog rows. Frozen calendar and document entries are 850 and 714 characters respectively. A rejected dependency-closure experiment produced 41 calendar entries and about 14.5k characters, so it is not used.

Settings explains the purpose and order of Developer, User, Work Profile, role, Skill, and runtime content. Template previews render the corresponding saved template. The separate read-only runtime view displays public, task-bound, and historical sources explicitly. Section receipts contain name/source/placement and redacted character counts, without duplicating section text. Historical inputs and section receipts are read as saved; missing historical metadata is shown as unrecorded. CLI-internal system text, tool schemas, and session history are outside the service-input display.

## Local and independent verification

- Prompt composition, frozen discovery, Settings preview, and runtime context: 70 passed in the final combined run.
- Complete Scheduled Agent consumer module: 36 passed after reproducing and correcting the test's expected owning-parser error.
- Final scoped interface/producer checks: 14 passed; prompt/frozen/receipt group: 39 passed; read_skill checks: 10 passed; catalog checks: 4 passed. These groups overlap and are not an aggregate suite count.
- Frontend focused tests: 85 passed, 2 skipped; production build and typecheck passed.
- Existing prompt-template publication and rollback checks: 29 passed.
- Independent final code review: approved, no open P1/P2. The damaged-material assertion was corrected without changing strict runtime rejection.
- Actual Audit bound-tool discovery returned 337 body-free metadata entries. Calendar/document/chat named reads and their returned-path reads had identical content and SHA. Directory queries exclude authorization escapes; ambiguous names require a path rather than silently selecting a version. Explicit inactive null arguments retain the same read receipt.

These are implementation and interface results. They do not by themselves prove a business action or deployment.

## Fixed scenario comparison

The unchanged anonymized case list and criteria are versioned in `evals/task_scoped_prompts/v1.json` (SHA-256 `9e6bb578a321cee3865c20051f47762de12710c1076b2e4fbf1c333008a192af`). Baseline assembly uses `a2173cd9`; candidate assembly uses the reviewed source above. Both arms use the same frozen configuration and authored Skill bodies, model `gpt-5.6-luna`, medium reasoning, current native role wrapper/home, tool schemas/results, fixed time, and criteria. Native output-schema is off in both arms, matching the independently releasable prompt change.

There are six cold scenarios per arm, plus a same-session calendar continuation per arm: 14 native calls. Cases cover cross-midnight timezone arithmetic, a real calendar conflict, an already-handled mail thread, material document conflicts, an existing work-remediation Task, and Audit feedback. Expected answers are not included in model prompts. Actual Consumer and Audit producer bytes must match frozen Developer/Task inputs before calls begin.

The final 14-call run completed in 298.712 seconds. All 14 outputs passed canonical Pydantic parsing. Independent arm-blind review found six passes and one partial result for the candidate. Calendar, mail, document, and work-tracking judgments were correct. The candidate Audit returned feedback and blocked an invalid plan, but missed an additional `payload.body` versus required `payload.content` contract defect. This original partial result is preserved rather than relabeled as a clean first-pass success. The baseline missed a midnight boundary event by using that instant as an exclusive query end, so its correct calendar action lacked required source evidence.

The required follow-up used the candidate's actual return feedback without adding field-correction instructions. Two serial calls followed the existing protocol: Consumer revision 1 naturally read the action contract and emitted the correct `payload.content`; the original Audit native session reviewed the prepared revision 1 and approved its exact recomputed digest. The reply preserved the document conflicts, unapproved status, exact source target, normal service postfix, and task_context binding. No action was executed. Consumer revision was cold because this constructed original candidate had no Consumer native session. This does not replace the matrix's separate same-session calendar proof.

Independent review approved this complete feedback-loop evidence. The 14-call initial results remain unchanged. Earlier runs with incomplete operation fixtures or mismatched Skill selector schemas are explicitly superseded and not counted. A local closure postprocessing mistake was recovered from the same saved Consumer wire output, without rerunning the model or changing its result.

| Cold case | Baseline characters | Candidate characters | Reduction |
| --- | ---: | ---: | ---: |
| Calendar cross-midnight | 38,112 | 27,639 | 27.48% |
| Calendar real overlap | 38,100 | 27,627 | 27.49% |
| Mail already handled | 37,547 | 26,768 | 28.71% |
| Document conflicts | 37,594 | 26,985 | 28.22% |
| Work existing remediation | 37,660 | 27,120 | 27.99% |
| Audit feedback | 26,689 | 21,879 | 18.02% |
| Total | 215,702 | 158,018 | 26.74% |

Counts are redacted service Developer plus Task characters. Provider usage, cache/history, tool result size, and cost are different measures; some candidate turns consumed more provider input tokens despite shorter submitted instructions. This small controlled matrix does not establish general model-quality or per-call cost improvement.

The work-tracking case explicitly asks Consumer to reply. The Skill's silent envelope instruction belongs to its separate Task Agent extraction workflow; independent review confirmed it does not override this Consumer request. The source requirement to reuse task-auth-1 and avoid duplicate/completed claims was preserved.

Production evidence is narrower than the fixed comparison: input statistics cover 403 saved runtime.prompt snapshots, but complete event/native transcript evidence was established for calendar examples only. Other families use the controlled fixed cases and are not claimed as complete production trajectory samples.

## Native output-schema boundary

The configured OpenAI API control returns HTTP 429; its detailed quota/capacity cause is unconfirmed. Qwen's current vLLM 0.29.0 path drops Codex CodeMode custom/freeform tools. Disabling CodeMode exposes namespace functions but still fails the exact-schema plus tool-use gate: the provider's pure final-JSON grammar prevents Qwen tool-call syntax. A prior strict-function control internally cleared final-schema guidance and therefore did not demonstrate compatibility.

Official upstream commit `2e06395fd384f3bd6755faded96d90a19d87e31e` provides namespace grammar alignment together with prior auto-tool/final-schema composition. An isolated, real ResponsesRequest/Qwen parser/xgrammar gate passed for both unchanged complete Consumer/Audit schemas with the official FUNCTION strict floor: legal namespaced tool calls and exact final JSON are both accepted, while unknown tools, missing required final fields, and plain text are rejected. Client strict=false and tool_choice=auto remain unchanged. This is a parser/grammar result, not a served-model result. The current provider remains vLLM 0.29.0; PR #31 still needs the configured provider runtime/model cold/resume gates and OpenAI API capacity. Private trajectory and source evidence is retained separately.

## Production release

Pre-deployment acceptance is complete for this independent prompt/Settings change. Formal quiet deployment and its readback are recorded as a separate release artifact. Readback must record the deployed commit/PID, health, queues, Attention/History, exact saved template fingerprints, and Settings input sources. Model fixture outcomes, deployment, and live external business outcomes are reported separately; this document does not itself assert a production deployment.
