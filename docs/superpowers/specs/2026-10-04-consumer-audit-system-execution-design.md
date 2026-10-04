# Consumer / Audit / System Execution Design

Date: 2026-10-04 (America/Los_Angeles).
Status: product decisions confirmed by Derek; implementation requested in a new Codex task. This document does not claim that the new runtime is deployed.

## 1. Goal And Confirmed Boundaries

Consumer completes business work and proposes a candidate. Audit reviews that candidate without executing its controlled actions. The system executes an approved, persisted structured action plan and verifies its external outcome. Consumer requests for human judgment are also candidates that must pass Audit before reaching Derek.

The intended outcomes are fewer unnecessary human escalations, a smaller Audit responsibility, direct execution of pre-reviewed human choices, and truthful recovery without repeated external actions.

- Use a uniform contract for structured actions that actually require Audit, including sending messages and OA approval operations. The implementation must inventory existing audited structured actions and map each in-scope action to a domain executor; do not introduce an arbitrary shell-command executor.
- Consumer is not globally read-only. It retains business preparation and work such as producing reports and operating their documents under the applicable workflow. There is no new standardized `update_daily_report` action.
- Consumer does not itself execute the controlled structured actions it submits for review. Audit has read capabilities but no capability to execute those actions.
- Email unsubscribe stays in its independent direct workflow and does not acquire an Audit turn.
- Audit reviews the whole candidate. There is no per-action partial approval.
- The autonomous business boundary comes from approved CEO application code, configuration and applicable business rules. Do not invent a per-instance permission requirement when these already support the operation; a real unresolved current-instance business choice may still require Derek.
- This loop resolves current business instances only. Remove long-term-rule choices and automatic Skill updates from the human-decision flow. Self-evolution is a separate future loop driven by observable execution records, not an option in this loop.

## 2. Existing Paths That Must Change

These are navigation anchors verified during design; the implementer must inspect current main before editing.

- `app/agent_orchestrator.py` currently finishes pure Consumer `needs_human` before launching Audit. A proposal may also execute and then end in `needs_human` through `consumer_state.escalates`.
- `app/agent_contracts.py` currently makes `DecisionOption.applies_to` equal `task_class`, and combines Audit review with external results through outcomes such as `executed` and `feedback_provided`.
- `app/agent_cli.py::send_approved_dingtalk_message` currently requires a running Audit run. Reuse its prepared-message and receipt mechanisms, but bind execution to persisted review approval instead of a running Agent.
- `app/audit_web.py::handle_needs_human_decision_post` accepts free-text instructions, reusable-policy scope and Skill updates, then queues Consumer again. Pre-reviewed option selection must become a distinct direct-execution path.
- The Attempt detail UI and API currently carry text-only options and a Skill-update checkbox. They must expose exact current-instance options and remove long-term-rule behavior.
- Existing architecture, runtime docs, role prompts and business Skill instructions describe the previous responsibility split. Update them with implementation, not in this documentation-only commit.

## 3. Candidate And Audit Contracts

### Candidate

Keep the existing task, run, business-object and revision lineage. Introduce two reviewable candidate kinds: an action plan and a human-decision request. Both identify their business object, stage and candidate revision, and carry sourced facts, reasoning and references.

An action plan contains complete structured actions with stable action identities, exact targets, exact payloads, execution order and verification requirements. Content that the service adds mechanically, such as an outbound signature, must be prepared before approval so the approved and executed artifact are bound consistently.

A human-decision request is specified separately in section 4. Do not overload an action result or a technical failure as a question.

### Audit Output

Audit returns exactly one business decision, bound to the complete candidate revision and its content digest:

| Decision | Meaning | Consumer or system behavior |
| --- | --- | --- |
| `approve` / 通过 | The whole candidate is acceptable. | Execute its action plan, or publish its human-decision request. |
| `return` / 退回 | More facts, explanation, background or evidence are required. | Consumer may retain the same proposed actions and body while supplying the missing support. |
| `reject` / 驳回 | This version's content or action is unsuitable. | Consumer must materially change the rejected proposal or abandon it with a justified `skipped` outcome. |

The review result contains the decision and explanation, evidence references, and specific requested changes when returning or rejecting. It does not contain a rewritten business body, a rewritten option, an external receipt, or an assertion that execution succeeded. Audit cannot originate an alternative human question; it tells Consumer what is wrong with the submitted one.

The initial submission allows at most three resubmissions. Business review feedback consumes this budget. Runtime/provider failures, route unavailability and restarts do not. Budget exhaustion is `failed`, not an artificial human question or `skipped`.

A rejected unchanged action/body cannot execute merely because metadata changed. Cosmetic rewriting is not sufficient for a substantive rejection; Audit evaluates whether the identified problem was actually resolved. A returned action/body may stay identical when the requested evidence has been added.

### Review Versus Execution Responsibilities

Audit judges business appropriateness, relevant facts, audience, timing, reasoning and the need for human involvement. It can read evidence that these judgments require. It does not own provider dispatch, claim handling, receipt storage, exact duplicate detection or retry orchestration.

System code performs deterministic target/version binding, claim ownership, duplicate prevention, relevant provider-state checks and external-result reconciliation. Business changes discovered by these checks invalidate the applicable candidate and return to Consumer; a technical failure with unchanged business content does not trigger another business review.

## 4. needs_human: Reviewed Current-Instance Decisions

This is a first-class part of the design, not a presentation filter or optional validation.

### 4.1 Consumer Submission

Consumer proposes a human-decision request containing:

- The one current-instance decision Derek needs to make, expressed in ordinary language.
- Enough source context to understand who asked, what happened, the current state and why the decision matters, with traceable references rather than an out-of-context task ID.
- A specific explanation of what cannot be resolved by the applicable code, configuration, business Skill, available facts, memory or session context.
- The alternatives and their actual consequences and tradeoffs. Each executable option includes its own complete bound action plan; a stop/no-action option includes the intended terminal outcome and reason.
- Any already completed prior-stage actions and their verified receipts, so the question cannot imply that nothing happened when earlier stages ran.

Do not force a fake multiple-choice question. If the missing input is an open-ended fact that only Derek can provide, say precisely what input is needed; that answer cannot directly execute an unbound action plan.

### 4.2 What Audit Must Review

Audit must check all of the following, not merely whether required JSON fields exist:

1. Does this instance genuinely require Derek's decision, rather than normal autonomous processing under an existing rule?
2. Can code, an installed business Skill, memory, session context or available read tools settle it without asking him?
3. Should missing source material instead be requested from the actual applicant or source owner?
4. Is this a business decision rather than a technical/provider, authentication-route, schema, runtime, model-output or retry failure disguised as a decision?
5. Does the request include enough concrete context and a justified reason?
6. Are the options clear, feasible and meaningfully different, with reasonable stated tradeoffs and no false choices?
7. Is every directly executable option complete, restricted to the current instance and supported by the facts and applicable rules?

`approve` approves the question and its complete option plans for conditional execution of the chosen branch. It does not approve all branches for immediate execution. `return` requests better context, evidence or options. `reject` sends an unnecessary or invalid escalation back to Consumer for autonomous handling or a different justified candidate.

Audit must not use vague missing-authorization language where approved application behavior already supports the operation. A genuine human-only step, login or missing material must name the exact required action and context.

### 4.3 State And Visibility

```text
Consumer decision candidate
  -> processing: waiting for Audit
      -> return/reject: Consumer feedback loop, still processing
      -> approve: persisted needs_human question visible to Derek
      -> technical failure: technical retry/failed, not needs_human
```

Only an Audit-approved current candidate appears as an actionable `needs_human` request or notification. Before review, it is not counted as a question waiting for Derek. Audit approval alone does not make the task `done`.

Historical requests remain readable as historical evidence but cannot supply approval for a new executable branch. A task's current question and its historical questions must not be conflated in Attention, History or the Attempt detail view.

### 4.4 Derek Selects An Option

The decision API submits the option key, candidate version and review identity. The server loads the exact reviewed plan; it does not trust client-provided instruction text, body or recipient as the executable payload.

```text
Audit-approved needs_human request
  -> Derek selects an approved option
      -> system claims and executes that option's persisted plan
          -> verified completion: done
          -> reviewed stop/no-action choice: skipped with reason
          -> technical failure: retry/reconcile, then failed if exhausted
```

No new Consumer or Audit turn is required for a still-valid pre-reviewed option. The human choice selects one branch; it does not grant execution of other branches. No external action occurs before a branch requiring a choice has actually been selected.

Repeated submission of the same selection is idempotent. A competing selection after the first committed selection is rejected rather than starting a second plan. Selecting an option and completing its business actions are separately observable events.

### 4.5 Additional Instructions, New Facts And Missing Information

- A free-text supplement is a new instruction or evidence, not a reviewed executable option. Preserve it and route it to Consumer for a new complete candidate and Audit review.
- A new relevant fact that invalidates a selected plan prevents execution of that old plan. Preserve the human answer and let Consumer incorporate it into the refreshed candidate; do not repeatedly ask the same resolved question.
- Malformed or mismatched selection identities do not bind a choice to another candidate.
- Provider failure, an unreadable source or exhausted technical retries remain technical failures. They do not fabricate a human option. If actual user login/access/material is required, Consumer must formulate the concrete request and Audit must assess it.

### 4.6 No Long-Term Rule Update In This Loop

Remove `applies_to=task_class`, reusable-policy scope, the Skill-update checkbox and the associated decision-handler side effects. Current-instance choices must not edit Skills or automatically create reusable rules. Old stored scope/update facts remain historical facts, not enabled behavior.

Preserve linked proposal, review, selection, execution, receipt and user-correction records so a separate future self-evolution mechanism has observable inputs. Do not implement that mechanism in this change.

## 5. Multi-Action And Staged Work

A candidate may contain several related actions, such as an OA operation followed by a notification to its applicant. Audit accepts or returns/rejects the complete plan. The system executes its declared order and does not issue dependent actions before their prerequisites are verified.

Partial execution is not partial approval. Preserve a successful earlier action and resume only incomplete actions; an unverified outcome is reconciled before replay. Never change an OA result merely to retry its notification.

Derek approved staged processing. Each stage is an independent complete candidate and review. For example, a verified necessary material request can run before a later current-instance judgment; the later question includes what the first stage already did. Do not mix immediate actions and unchosen conditional branches into one implicit execution path.

Stage progression is a business continuation, not content-review disagreement. Keep its counter and receipts distinct from the three-resubmission review budget. A new stage has explicit predecessor evidence; it does not create an unbounded auto-loop.

## 6. Domain System Execution And Recovery

Reuse the service's existing provider clients, message sender, prepared text, external-action identities and receipt mechanisms. Do not create a parallel send ledger or alternate ad hoc delivery path.

- Message execution loads a persisted approved candidate or selected option, rather than requiring a running Audit Agent. The system itself calls the domain sender; neither Audit nor Consumer calls an approved-send tool to perform the controlled action.
- OA execution uses the existing provider client with exact instance/node/action identities and approved parameters, then records and reads back the corresponding result.
- Inventory other already-audited structured operations and supply their domain handlers before switching their producers to the new contract. Consumer document work and Email unsubscribe are not pulled into this registry simply because they write externally.
- Unsupported executable actions fail explicitly; no generic shell fallback or Agent execution fallback is allowed.
- Claims and action-result storage must make process termination resumable. Transient retry uses the same approved plan and external action identity, not a newly authored proposal.
- Unknown external outcome is a reconciliation state. Local receipt absence is not proof of no effect; inspect the original external object before deciding whether dispatch is safe.
- Confirmed success advances the plan. Confirmed no-effect permits the same plan's supported retry. Unresolved ambiguity or an external hard block stays visibly failed/uncertain under the domain recovery contract, not falsely completed or disguised as needs_human.
- A revised business plan is reviewed again; an unchanged technical recovery is not.

Audit's read-only capability boundary must be reflected in actual runtime tool availability and enforced operation interfaces, not only in a prompt or tool-name keyword filter. Consumer retains the capabilities needed for its allowed business work while the controlled proposal actions remain system-owned.

Do not interpret this redesign as permission to replay a historical operation explicitly refused by its execution environment. Keep that refusal and its recovery boundary intact; do not release it by renaming a tool or moving the same blocked operation to another channel.

## 7. Persistence, API And Console

Extend the existing append-only lineage rather than creating a second task system. Persist the candidate revision/content binding, the whole-candidate review decision, the selected option when applicable, stage relations, execution claims and per-action verified results. Human answers and review feedback must remain traceable to the actual candidate they addressed.

Separate typed Audit review results from system execution results. Update result schemas, parsing, orchestrator transitions, API projections and runtime prompts together. The new live runtime must not continue accepting the old Audit-executes contract as a fallback.

The decision endpoint distinguishes reviewed option selection from free-text supplement. The Attempt UI renders context, human reason, alternatives, exact action summary and consequences, and separately displays selection, execution progress, failure and receipts. Remove reusable-policy and Skill-update inputs from the frontend and backend, including old HTML forms.

Attention/History must follow the current approved question and actual execution state. `decision_selected` or equivalent selection evidence is not business completion. A sent receipt cannot be inferred from Audit approval or a human click.

The implementation must update `docs/architecture.md`, `docs/runtime-mechanism.md`, result schemas, default prompts and any copied/shared Skill instructions that enforce the old reusable-rule or Audit-execution behavior in the same behavioral commits.

## 8. Migration And Rollout

- Do not rewrite historical terminal runs or turn historical `executed` results into new `approve` results. Keep old results as historical audit facts.
- Old pending human requests have text-only options without this plan binding. Generate and review new current-instance candidates before making them executable; no implicit conversion of old choices into approved actions.
- Use existing verified receipts to avoid replaying completed stages/actions during recovery. Do not auto-release historical failed/risk-refused operations.
- Before deployment, verify all claimed work and persisted actions can resume idempotently, drain in-flight turns through the official deploy path and verify any still-applicable legacy draft gate against live state.
- Database changes require a verified backup and migration/restart tests. Do not apply a semantic historical Task import.
- Never edit, commit, build or run tests in the production checkout. Deployment uses pushed `origin/main` and `python -m app.deploy`; do not issue manual kill/kickstart commands.
- Read launchd to identify the actual service root, production DB and PID. After deployment verify the new PID, healthz, queues, Attention total/expanded records, failed History, audit page and controlled external receipts.

## 9. Tests And Fixed Evaluations

The implementation begins with failing focused regressions for the current behavior, then follows with integration and fixed baseline-versus-candidate evaluations. Full-suite verification must use the repository's safe parallel/CI workflow and must not overlap a restart or run inside production.

Required cases:

1. Audit has no controlled-write tool; approved actions run through the system executor, with no running-Audit dependency.
2. Whole-candidate approve/return/reject semantics; same body with added evidence is allowed after return, but an unresolved substantive rejection is not bypassed by a cosmetic change.
3. Initial review plus three resubmissions; technical failures and business stage continuation do not consume that feedback budget; exhaustion is failed.
4. Consumer needs_human always reaches Audit; valid questions are visible only after approval. Unnecessary escalation returns to autonomous handling without notifying Derek.
5. Audit rejects or returns missing context, vague human reasons, unreadable-source-as-choice, false alternatives and long-term-rule options.
6. A complete selected option executes directly, without another Consumer/Audit turn; exact target/body binding and mutually exclusive branch selection are enforced.
7. Duplicate selection is idempotent; conflicting selection, mismatched candidate and invalid option key cannot execute another plan.
8. Free-text instructions and truly open-ended facts produce a new candidate for review. New business facts invalidate the old option without losing the person's answer.
9. A stop choice ends skipped with a reason; selecting an execute option is not done until its actions have verified outcomes.
10. OA operation plus applicant notification, separate material-request/decision stages, partial success and dependency order recover without replaying the first successful effect.
11. Dispatch followed by process termination before local receipt, provider timeout, missing receipt and delayed readback reconcile against the original object before resend.
12. Review-complete/execution-not-started restart, pending-question restart and selected-plan restart preserve the correct stage and reviewed binding.
13. Consumer report/document work remains allowed and unsubscribe stays outside Audit. No reusable-policy or Skill-update side effect remains in the decision API/UI.
14. Current Attention/History/SQLite projections distinguish reviewed question, selected answer and verified business completion, with historical facts preserved.
15. Existing hard runtime refusals are not automatically retried through the new executor, and old unbound human choices cannot directly dispatch an action.

Use a frozen versioned case list across old and new paths with the same runtime/model settings. Compare business review correctness, unnecessary human escalation, incorrect execution, repeat execution and recovery completeness. Include valid autonomous messages, genuinely ambiguous current-instance choices, OA plus notification, and technical failures that must not become human decisions. Do not use private live messages as uncontrolled replay fixtures.

Acceptance requires focused regressions, backend/frontend suites, fixed eval artifacts, Quality CI, independent spec/code review and controlled live receipt/readback verification. Report these completion levels separately; a passing review or a successful process exit is not an external business outcome.

## 10. Implementation Handoff

This is a scoped architecture and product-behavior change, not a mechanical bug repair. The new implementation task must first read the canonical shared instructions, repository instructions, architecture/runtime docs and current agent claims, then create a concrete implementation plan using the approved spec. Use TDD and independent code review. Open a PR and attach fixed eval comparison evidence before merging.

Work in a clean development worktree/branch based on current origin/main. Preserve other agents' changes and claims. Do not reset or push an unrelated divergent main. The documentation-only spec commit may be carried into the implementation branch without pulling unrelated local commits.

The handoff must explicitly retain section 4 as a release gate: needs_human submission -> Audit review -> approved question -> selected pre-reviewed plan -> system execution -> verified terminal result. A task that implements only the send/Audit split, but leaves the old needs_human bypass or reusable-rule choices, does not satisfy this spec.

After tests/evals/review and PR merge, push the authorized scoped changes, deploy formally, verify production readback and report remaining business blockers with concrete context. Do not claim the feature complete merely because its code was committed or its task was queued.
