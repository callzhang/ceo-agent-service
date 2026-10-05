# Consumer / Audit / System Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. The primary agent integrates the coupled runtime changes; bounded implementation and independent review subagents are authorized by Derek's handoff.

**Goal:** Deliver the entire approved spec, including Audit-reviewed current-instance human requests and direct execution of their exact reviewed option plans.

**Architecture:** Keep reply_task / agent_run / reply_attempt lineage. Persist each prepared Consumer candidate and its whole-candidate review before a domain executor runs; conditionally execute only the atomically selected human branch. Reuse external_action_results, prepared outbound messages, native_reply_dispatches and domain receipts, with execution claims associated with reviews rather than a running Audit.

**Tech Stack:** Python 3.12, Pydantic, SQLite, native Codex/Claude CLI + direct MCP, DwsClient, ServiceMessageSender, React/TypeScript, pytest and Vitest.

---

## Source and implementation boundary

- Baseline: `origin/main` at `a7d4738a` (frozen full SHA will be recorded with eval cases before execution).
- Spec: `docs/superpowers/specs/2026-10-04-consumer-audit-system-execution-design.md`, cherry-picked as `4094667b` from `7128b7f3`.
- Worktree: `/Users/derek/.codex/worktrees/consumer-audit-system-execution/ceo-agent-service`; branch `codex/consumer-audit-system-execution`. No reset, rebase or push of the divergent development main.
- Production root verified from launchd: `/Users/derek/Services/ceo-agent-service`, supervisor PID `86943` at initial inspection. Installed shell defaults DB to `/Users/derek/Library/Application Support/ceo-agent-service/auto-reply.sqlite3`; confirm effective configured value before any production readback or backup.
- No production source edits, tests or builds. Only formal `python -m app.deploy` after scoped PR merge.
- Stale task-class option claims are preserved; approved spec expressly supersedes their behavior. Only the spec author's completed documentation claim was released.

## Task correspondence for system reviews

Derek requires every system-review item to map to an actual system task and its current business instance. Use `docs/audit-task-scope.md` as the current14-task inventory and frozen business-case correspondence;do not build review requirements from the MCP/tool catalog. XiaoQing has no background interview task and remains in personal conversations. Existing technical command verification does not gain an Audit turn.

## Independently approved post-publication scopes (2026-10-05)

Consumer ordinary work uses actual tools and receipts; only registered reviewed
system actions require whole-candidate approval before System execution. Remove
the whole-result completion-phrase scan, preserve strict wire and real receipts,
and provide task-generation artifact writing with read-only Audit access. Keep
existing runtime risk/authorization and historical no-replay boundaries. Verify
with fixed native baseline/candidate judgments and separate real tool probes;
contract replay alone does not establish native behavior.

## File responsibilities and interfaces

The separately approved deployment-reachability scope may defer only original
reply tasks 386130/386131 after exact identity, latest failed-run fingerprint,
verified backup and absence of active ownership, candidates or possible effects
are rechecked in one transaction. `app.reply_task_deferral` provides read-only
preview and explicit single atomic apply/resume. It preserves input, generation,
history and receipts, uses a bounded available_at, and restores only its exact
receipt's delay after formal deployment. Active processing ownership blocks the
operation. This scope has its own code, regression evidence, review and commit;
it neither retries other tasks nor replays historical risk refusal.

| Area | Exact files | Responsibility |
| --- | --- | --- |
| Contracts | `app/agent_contracts.py`, `app/agent_wire_contracts.py`, `app/schemas/consumer_agent_result.schema.json`, `app/schemas/audit_agent_result.schema.json` | Current-instance complete branch plans; read-only review decisions; separate system results |
| Candidate persistence | new `app/reviewed_candidate_store.py`, `app/store.py` schema and mixin wiring | Immutable prepared candidate, digest, review, selection, stage lineage and execution claim; reuse existing successful action ledger |
| Candidate hashing | new `app/reviewed_candidates.py` | Canonical JSON binding, action-content comparison, stage facts, whole-candidate validation |
| Execution | new `app/system_executor.py`, `app/agent_cli.py`, existing `app/dws_client.py`, `app/service_message_sender.py` | Typed domain handlers, ordered claims, reconciliation and verified receipts |
| Orchestration | `app/agent_orchestrator.py`, `app/agent_context.py`, `app/consumer_agent.py`, `app/audit_agent.py` | Both candidate kinds reviewed; feedback vs technical retry vs stage counters |
| Capability boundary | `app/agent_turn_runner.py`, `app/wechat/codex_safety.py`, `app/service_codex_config.py`, `app/claude_runtime_adapter.py`, `app/agent_cli.py`, runtime route/probe capabilities | Native tool restrictions + typed reads/document work; no arbitrary shell/argv or controlled Agent write tools |
| Final projection | `app/worker.py`, `app/agent_cron/consumer.py`, `app/decision_quality.py`, `app/audit_web.py`, `app/web_api/attempts.py` | Current reviewed question, selection, execution results; historical facts read-only |
| Console | `frontend/src/api/console.ts`, `frontend/src/pages/AttemptDetailPage.tsx`, its tests and styles | Exact branch actions/context/tradeoffs; selection identity vs new instruction |
| Rules | `docs/architecture.md`, `docs/runtime-mechanism.md`, role prompts, `ci/shared-skills/*/SKILL.md`, applicable `/Users/derek/.agents/skills/*/SKILL.md` | Match implemented responsibility and current-instance decisions, no automatic Skill edits |
| Eval | new `evals/consumer_audit_system_execution/v1.json`, `scripts/eval_consumer_audit_system_execution.py`, validation report | Frozen synthetic cases, identical baseline/candidate runtime settings, measured results |

Use the current Consumer `outcome=proposal|needs_human|no_action|failed` to carry a candidate without inventing an update_daily_report action. `ConsumerProposal` remains the complete action plan. `DecisionOption` contains key/label/instruction/consequence plus either a complete `plan` or `terminal_outcome=skipped` with `reason`; remove `applies_to`. Open-ended human facts use `requested_input` with no executable options. `ConsumerAgentResult` records stage/predecessor and may request continuation after a verified stage; do not mix an immediate action plan with unchosen branches.

`AuditOutcome` live values become `approve`, `return`, `reject`, `failed` (failed is technical, not a business review). `AuditAgentResult` has the existing quality/error fields, summary, revision, exact `candidate_digest`, evidence references and optional `AuditFeedback`; no external_result, rewritten payload, decision options or human question. Historical old JSON stays raw historical evidence; live wire rejects it. `SystemExecutionResult` alone carries external outcome and receipt references.

Persistence APIs on AutoReplyStore (implemented through a focused mixin) are `persist_review_candidate(task, consumer_run, prepared_result)`, `get_review_candidate(candidate_id)`, `record_candidate_review(candidate_id, audit_run_id, result)`, `current_reviewed_candidate(task_id, generation)`, `select_candidate_option(candidate_id, review_id, option_key)`, `record_candidate_supplement(candidate_id, instruction)`, `claim_candidate_execution(candidate_id, review_id, owner, lease_seconds)`, and `finish_candidate_execution(...)`. IDs plus digest are checked under the same SQLite transaction. Duplicate exact selection returns its existing record; a competing selection raises a conflict. Existing external action ledger remains the success truth; execution progress does not create a parallel success ledger.

## Task 1: Freeze the baseline and demonstrate old failures

**Files:** new `tests/test_consumer_audit_system_execution.py`; eval cases and report listed above.

- [ ] Save the full baseline SHA, synthetic case inputs, expected business judgments, model/route settings, timeout and concurrency. No private live message fixtures.
- [ ] Add a focused old-behavior regression using a persisted complete Consumer needs_human result and a spy Audit runner:

```python
def test_consumer_question_waits_for_audit(tmp_path):
    store = AutoReplyStore(tmp_path / 'service.sqlite3')
    task = create_synthetic_reply_task(store)
    run = persist_complete_consumer_question(store, task)
    orchestrator = AgentOrchestrator(store=store, consumer=NeverConsumer(), audit=NeverAudit())
    state = orchestrator._derive_state(task)
    assert isinstance(state, _NextAudit)
    assert state.parent_run_id == run.id
```

The fixture initially uses the old strict task_class shape solely to demonstrate the existing bypass; replace it with the new current-instance fixture when the new contract lands.
- [ ] Run `/Users/derek/miniforge3/bin/python -m pytest -q tests/test_consumer_audit_system_execution.py`. Expected old failure: `_derive_state` returns final needs_human without Audit.
- [ ] Freeze baseline measurements before altering runtime paths. Measure review correctness, unnecessary escalation, incorrect/repeated execution and recovery completeness independently; record unavailable live evidence honestly.
- [ ] Commit only case list, regression and baseline evidence: `test: freeze consumer audit system execution regressions`.

## Task 2: Separate candidate/review/execution contracts

**Files:** contracts/wire/schema files above; `tests/test_agent_contracts.py`, new `tests/test_reviewed_candidates.py`.

- [ ] Add red contract tests for executable option completeness, stop reason, open-ended input, no task_class, no mixed immediate/conditional result, and review approve/return/reject with no external result.

```python
def test_approved_review_cannot_claim_external_execution():
    with pytest.raises(ValidationError):
        AuditAgentResult.model_validate({**valid_review('approve'), 'external_result': {'id': 'invented'}})

def test_stop_branch_requires_reason():
    with pytest.raises(ValidationError):
        DecisionOption(key='stop', label='停止', instruction='停止此事', consequence='不执行', terminal_outcome='skipped', reason='')
```

- [ ] Run focused contract tests; verify expected validation failures or absent new API.
- [ ] Implement typed option invariants, three review decisions, separate execution result, stage/predecessor fields and canonical digest. Reject unchanged unresolved action content after reject even if reasons/metadata changed; return permits the same actions with added evidence. Audit prompt checks substantive resolution, so cosmetic content changes cannot evade the identified issue.

```python
def candidate_digest(result: ConsumerAgentResult) -> str:
    body = json.dumps(result.model_dump(mode='json'), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return sha256(body.encode()).hexdigest()
```

- [ ] Derive and pin schemas from actual models; make parser reject old Audit-executes wire. Preserve old raw stored runs for history.
- [ ] Run contract/wire/schema tests; commit contracts with the architecture/runtime contract change.

## Task 3: Durable reviews, choices, claims and migration

**Files:** new `app/reviewed_candidate_store.py`, `app/store.py` schema hooks, new `tests/test_reviewed_candidate_store.py`, `tests/test_store.py` migration checks.

- [ ] Add red SQLite integration tests covering prepared digest mutation, foreign generation/revision/review, duplicate/conflicting choice, restart before execution, old unbound options, immutable history and schema idempotency.

```python
def test_competing_choice_is_rejected(store, approved_question):
    store.select_candidate_option(approved_question.id, approved_question.review_id, 'execute')
    with pytest.raises(ValueError, match='selection_conflict'):
        store.select_candidate_option(approved_question.id, approved_question.review_id, 'stop')
```

- [ ] Use the existing immediate-write transaction for candidate/review/selection; bind exact completed Consumer/Audit run, task generation, digest and stage. Record choice separately from execution.
- [ ] Claims may be renewed/resumed only for the same persisted approved plan. A new generation/fact invalidates pending execution while preserving the old answer as context. Successful external_action_results survive revision/stage restart.
- [ ] Add schema tables to initialization readiness checks and advance store version; additive migration only, no historical Task import or terminal-run rewriting.
- [ ] Verify online backup integrity on a development DB copy before migration; migrate/reopen twice and compare retained history/receipts.
- [ ] Run the new store tests and affected schema tests; commit persistence with schema documentation.

## Task 4: Domain executor and uncertain outcome recovery

**Files:** new `app/system_executor.py`, `app/agent_cli.py`, existing domain sender/client interfaces; new `tests/test_system_executor.py`, affected sender tests.

- [ ] Add red domain integration regressions: approve alone performs no send; selected exact message executes with completed Audit; OA then notification respects order; timeout after dispatch reconciles first; provider accepted before process termination; partial success resumes only remaining actions; unsupported action fails; historical risk refusal cannot replay.

```python
def test_partial_success_does_not_repeat_oa(executor, approved_oa_and_notice, provider):
    provider.fail_notice_once = True
    executor.execute(approved_oa_and_notice)
    executor.execute(approved_oa_and_notice)
    assert provider.oa_calls == 1
    assert provider.notice_verified == 1
```

- [ ] Inventory and implement exact domain handlers before switching producers: DingTalk message/reply, OA comment/approve/reject/revert/redirect, calendar respond and existing audited emoji, document comment/publication where already a proposal. XiaoQing transcript/result uploads stay in Derek’s personal interview conversations and are outside this background-service rollout; do not add a service-owned upload client or make it a release gate. Use native provider clients/MCP client directly; no shell argv executor or Agent fallback. Consumer report/document preparation remains its own typed operation capability.
- [ ] Message preparation uses existing ServiceMessageSender before review, preserving exact payload and postfix. Refactor the approved send entry to load review/selection instead of a running Audit; do not expose it to either Agent. Bind stable OA applicant target from live originator identity rather than assuming the trigger sender is the applicant.
- [ ] Reuse existing external_action_results, sent_replies, outbound receipts, native_reply_dispatches and WeChat receipts. Persist dispatch identity before side effect; unknown outcome reads original object, never infer absence from a bounded empty read. Technical retries retain plan/digest and skip completed actions.
- [ ] Preserve hard runtime refusals. A provider business-state mismatch invalidates the candidate for Consumer; unchanged technical failure uses supported retry/reconcile and bounded failed/uncertain state.
- [ ] Run executor/sender/native-reply tests and commit with recovery docs.

## Task 5: Real Agent capability boundary

**Files:** role/runtime wiring and agent_cli above; focused native/Claude/router tests.

- [ ] Add red command/tool-list tests and native controlled synthetic probe proving Audit has no exec/code/write or controlled MCP interface, while Consumer can do document preparation. A prompt assertion alone is insufficient.
- [ ] Disable builtin shell/code executors using the native inline config mechanism already exercised by `tests/support/native_codex_read_fixture.py`; restrict MCP tool IDs by actual provider tool declarations/annotations and exact supported operation interfaces. Do not use keyword tool filters or credential proxies. Keep native home and direct OAuth.
- [ ] Extend agent_cli with typed DWS evidence reads and Consumer document operations; do not expose `execute_reviewed_read(argv)` because current code marks arbitrary argv read-only without enforcing it.
- [ ] Use Claude native tool controls and role-specific direct MCP config. Require a role-boundary capability on routes; Friday currently lacks tool-policy fields and cannot serve these turns until it supports that capability. Existing route fallback still handles eligible providers only.
- [ ] Run real read-only synthetic native probes plus adapter/router tests; commit boundary with precise documentation of actual enforcement and eligible routes.

## Task 6: Full orchestration, reviewed human visibility and stages

**Files:** agent_orchestrator/context/Consumer/Audit/worker/scheduled consumer/decision_quality, corresponding tests.

- [ ] Add red tests for spec cases 2–5, 8–12: all human candidates reach Audit, return/reject remain processing, technical errors do not become questions, review plus exactly three resubmissions, stage continuation has its own bound, stop is skipped, selected execution not done until receipt.
- [ ] Remove `_bounded_fact_finding_feedback` keyword heuristic. Audit evaluates the whole candidate and all seven needs_human questions from spec §4.2; it never originates or rewrites the question.
- [ ] Persist the prepared candidate before Audit; Audit context includes complete question/plans, exact digest, predecessor receipts and prior feedback. approve records review then either publishes the question or invokes the system executor. return/reject creates a new Consumer revision; four failed business reviews exhaust as failed. Technical retries reuse candidate/review without spending the content budget.
- [ ] Use distinct explicit stage predecessor plus receipts, require verified completion before next stage, refresh source context before execution, preserve human answer when new facts invalidate the old plan. Old pending text-only requests are non-executable until a new current candidate is reviewed.
- [ ] Worker projection reads SystemExecutionResult instead of Audit external_result; question projection is tied to persisted current approval, not raw Consumer result. Stop/free-input handling and notifications honor the reviewed state.
- [ ] Run orchestrator/worker/scheduled/decision projection suites and commit with prompt/architecture/runtime changes.

## Task 7: Decision API and console

**Files:** `app/audit_web.py`, `app/web_api/attempts.py`, `frontend/src/api/console.ts`, `frontend/src/pages/AttemptDetailPage.tsx`, its tests/styles, Attention/History projections.

- [ ] Add red API/UI tests for exact option+candidate+review submission, forged client payload ignored/rejected, duplicate/conflicting choice, supplements produce Consumer revision, old unbound options denied, no reusable_policy/Skill-update effects, selected progress/receipt/failure display.
- [ ] Decision API accepts distinct selection or supplement requests:

```json
{"kind":"select","candidate_id":14,"candidate_digest":"sha256","review_id":25,"option_key":"execute"}
```

```json
{"kind":"supplement","candidate_id":14,"candidate_digest":"sha256","review_id":25,"instruction":"New source fact"}
```

- [ ] Selection transaction loads server-stored plan and wakes ordinary task execution, without a Consumer/Audit turn. A supplement stores source evidence and enters a new full review. Reject old scope/update fields and remove old HTML inputs/handler side effects.
- [ ] Render ordinary-language question, traceable source context, reason, exact actions, tradeoffs, completed prior stages, selected branch and independent execution receipts. Current actionable list excludes unapproved/history/selected requests; History retains all original facts.
- [ ] Run backend attempt/decision/Attention/history suites and Vitest Attempt/NeedsDecision tests; build frontend; commit API/UI with docs.

## Task 8: Business Skill consistency and fixed eval

**Files:** applicable shared business Skills, pinned CI copies, versioned cases/runner and validation report.

- [ ] Compare installed shared Skill instructions and CI snapshots to new contracts; remove Audit-executes, task_class, reusable_policy and decision-triggered Skill updates. Preserve rule/config editing as explicitly requested work elsewhere. Reports keep their document work and controlled send proposals; unsubscribe retains direct independent workflow.
- [ ] Freeze at least the 15 spec §9 scenarios plus autonomous valid messages, truly ambiguous current-instance branches, OA+notice and false technical human decisions. Baseline/candidate use identical route/model/thinking/timeouts/concurrency and synthetic inputs.
- [ ] Execute both exact commit refs; store case results, settings/digests and metrics. Check review correctness, unnecessary escalation, wrong effects, repeats, complete recovery. Distinguish deterministic contract replay from model/business eval and live verification.
- [ ] Confirm required cases have no semantic regressions; independently inspect failures instead of tuning case strings/keywords. Update validation report and commit reproducible artifacts.

## Task 9: Independent review, Quality CI and scoped release

- [ ] Independent spec review with no implementation-session history checks all spec §9 cases, particularly full §4 release gate. Independent code review then checks concurrency, claims, provider uncertainty and no runtime refusal replay. Resolve all blocking findings and rerun affected checks.
- [ ] Run affected backend/frontend suites. Full backend verification, if required, uses `pytest -q -n 6` outside production and never overlaps restart. Run repository Quality workflow including lint, npm tests/build and browser tests. Record CI completion and actual artifacts, not queue acceptance.
- [ ] Create scoped PR from this branch, attach it using `attach_artifact`, include frozen baseline/candidate comparison and review evidence. Merge only reviewed scoped changes into remote main; do not publish the unrelated divergent local main.
- [ ] Verify production's effective DB/root/PID, current in-flight claims, draft gates, refusal inventory and resumable action state. Verify database backup and additive migration on a copy; no live historical semantics import.
- [ ] Deploy using `python -m app.deploy` from development checkout. It must drain claims and use pushed main. Never manual kill/kickstart or edit production.
- [ ] Read new PID, healthz, queues, Attention meta.total and expanded records, failed History, audit page and controlled external receipt/readback. Select a synthetic designated principal-only receipt test; never replay private historic business effects.
- [ ] Remove only task-created scratch data after verified evidence retained; release own claim, retain review/eval artifacts, report implementation/test/eval/live/business completion separately.

## Coverage and release checklist

| Spec §9 case | Implementation/verification tasks |
| --- | --- |
| 1 tool boundary/system executor | 4, 5, 6 |
| 2 whole review/return/reject | 2, 6, 8 |
| 3 three resubmissions/technical/stage counters | 3, 6 |
| 4 reviewed questions only | 1, 3, 6, 7 |
| 5 valid context/reasons/options/no long-term rules | 2, 6, 7, 8 |
| 6 exact conditional execution | 2, 3, 4, 7 |
| 7 idempotent mutually exclusive choice | 3, 7 |
| 8 supplements/facts/answer preservation | 3, 6, 7 |
| 9 stop/selected != done | 2, 4, 6, 7 |
| 10 OA+notice/stages/partial dependencies | 4, 6 |
| 11 interrupted/unknown/readback | 3, 4 |
| 12 review/question/selection restarts | 3, 6 |
| 13 docs/unsubscribe/no Skill side effect | 5, 7, 8 |
| 14 projections/history | 3, 6, 7 |
| 15 runtime refusals/old options | 3, 4, 6, 7, 9 |

No task is complete merely because it was committed. The release remains blocked until implementation, tests, fixed eval, independent review, CI, formal deployment and external receipt/readback evidence are all present.
