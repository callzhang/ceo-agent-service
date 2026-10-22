# Scheduled task migration: native Agent validation

This validation compares decisions made from the original and migrated Skill
sources on seven frozen synthetic cases. It uses the installed native Codex CLI
with the production configuration's selected Codex OAuth model. It performs no
service invocation, business provider read, message send, calendar mutation or
document write.

## Current result — 2026-10-19, runner revision 3

The seven frozen annotation cases now pass after the existing one-Audit/one-correction
budget in both arms. Candidate first turn also passes all seven common checks and
the migration checks. Baseline first turn passes common checks but incorrectly
ignores its historical publication gate, so that first-turn pair remains `failed`;
`post_feedback_status` is separately `passed`. No failed phase is relabeled.

| Revision-3 phase | Baseline common | Candidate common | Migration |
| --- | --- | --- | --- |
| First Consumer | 7/7 | 7/7 | Baseline fails; candidate passes |
| One independent Audit and one correction per arm | 7/7 | 7/7 | Both pass |

The root cause was the evaluation protocol, confirmed by tracing actual role
inputs and saved output. Revision 2 defined retained identities as sources retained
anywhere, including notes; a correct explanation of an exclusion could therefore
promote that source into `include_ids`. It also supplied evaluation labels to
Consumer but omitted them and its schema from Audit. Audit changed `respond` to
`proposal` and `pending_source_failure` to `failed`, following the production Skill
names rather than the evaluation wire protocol. Its corrections were scored as
failures even when the business judgment in the notes stayed correct.

Revision 3 gives Consumer, Audit and correction one shared transport contract.
Identity lists describe substantive output or action targets, with provenance and
coverage counts separate. Dispositions are constrained to the original nine labels.
Identity schema values come only from explicit input `id`/`*_id` values, without
consulting expected answers. Invalid native output fails schema validation and is
retained; it is never converted into a passing answer. No production Skill or
service runtime code changed in this repair.

Acceptance protection is executable in
[`test_scheduled_task_generic_eval.py`](../tests/test_scheduled_task_generic_eval.py):
the case-file hash and scoring-function AST hash are frozen, and the old outputs
are rescored to exactly the original failures. Three regression checks first
failed against revision 2 and passed after the protocol repair. Focused unit and
companion artifact checks are recorded below.

- Case SHA-256 remains `938dcb51c9207d7d79bdc4240a50d827fbd1b376b606541b62799517fe862868`.
- Scoring AST SHA-256 remains `b016cc78e6254299521296e541eb4307f672d1a8632540bf49b6e8fe2f0133a7`.
- The complete unmodified revision-2 result is [`results.v2.json`](../evals/scheduled_task_generic/results.v2.json), SHA-256 `cd736ad91074de7e77519ec931e53e8d5fef8307a225b06ca121f3f610569103`.
- Revision-3 native evidence is [`results.json`](../evals/scheduled_task_generic/results.json). Candidate source reference is `26e50f8d8b1c091aba46171399bbc85925168dc7`; all eleven Skill hashes are identical to the preceding candidate.
- Both arms used the same installed CLI, production-selected requested model
  `gpt-5.6-luna`, reasoning `high`, timeout and tool restrictions. All six phases
  exited successfully with zero non-message runtime items. Both cleanup receipts
  are true. Native events do not independently report the actual model identity.

## Actual offline drafts and action payloads

The companion [`behavior.py`](../evals/scheduled_task_generic/behavior.py)
generates authored report paragraphs, response bodies and proposed production
payloads from the same frozen inputs. It receives neither the annotation outputs
nor expected answers. The real application purpose resolver and purpose-output
parser convert those payloads to `ConsumerProposal`; the real email classifier
wire parser checks classification. Proposed capability/operation pairs must exist
in the existing system action registry. No new production rule is introduced.

This checks actual draft rows/action targets against the original required,
excluded and unverified source identities; it also checks the actual calendar
response, unknown owner, source-failure action and email classification fields.
Copying an excluded input body into a differently labeled paragraph still fails.
Report-document work and internal candidate records remain offline draft artifacts;
they are not invented registered SystemExecutor operations. Classification is
bound to the case's exact source by the native envelope even though the production
classifier wire does not itself contain an email ID.

The initial companion probe is preserved without regrading in
[`behavior-results.v1.json`](../evals/scheduled_task_generic/behavior-results.v1.json),
SHA-256 `a0fe6bfbe57f31d85c77e74fbadf7fc4b2c1eb5f33afa1b0398a151463e60dd3`.
It recorded 3/7 in each arm. Inspection showed its own input boundary was incomplete:
it requested production operations without supplying the operation contracts that
the Skills require reading, included coverage/history as substantive draft rows,
and did not define whether verification described a quotation or its underlying
claim. The candidate used `decline` where the adapter requires `declined`; both
arms also invented unregistered document/message operation names. These remain
failed probe outputs; they are not evidence that production, with its operation
contract lookup, emits or executes the same invalid actions.

Before changing that companion, two additional regression checks failed: it
accepted an unregistered `dingtalk-chat/reply` operation and its actor input lacked
the platform contracts. The repaired probe supplies the unchanged canonical action
contract and calendar adapter contract to both arms, describes substantive draft
rows separately from coverage/source tracing, and defines underlying-claim
verification. It also refuses unregistered operations. It does not change case
facts, expected answers, the original annotation scoring function or any Skill.
Exactly one new paired companion probe follows that diagnosed interface repair.

The repaired native generation initially scored baseline 7/7 and candidate 6/7.
The candidate weekly report actually contained `issue-1` twice in its authored
body, but cited `meeting-target` as the paragraph's source. The companion reader
looked only at source labels and missed the literal issue in the actual body.
A new regression reproduced this false negative before the deterministic reader
was fixed. Complete input identities in authored text now count alongside source
labels and actual action targets; longer identifier substrings do not count.
Removing the issue from both provenance and body still fails the same check.

The **same native outputs**, without another model call, now score **7/7 in each
arm**. `scoring_revision: 2` and each arm's `score_history` preserve the prior
scores, including the candidate's failed result. Raw deliverables remain unchanged;
regression tests verify rescore immutability and preservation of the failure.
Both native generations produced zero tool/runtime actions and cleaned their
temporary files. The actual drafts were also read directly: candidate output
retains the waiting/decision/watch items and new issue, omits irrelevant material
from substantive content, declines the current event with `declined`, classifies
without mail actions, leaves the meeting owner unknown and the OA claim unverified.

| Actual draft / proposed payload | Baseline | Candidate |
| --- | --- | --- |
| Daily substantive facts and irrelevant-source exclusion | Pass | Pass |
| Weekly target and actual new issue in body | Pass | Pass |
| Reply to current staffing request | Pass | Pass |
| Current invitation decline and superseded-source exclusion | Pass | Pass |
| Notification/unimportant classification, no mail action | Pass | Pass |
| Unassigned source-action candidate | Pass | Pass |
| Unverified customer acceptance, technical failure, no applicant action | Pass | Pass |

Focused regression verification: **13 passed** in
`tests/test_scheduled_task_generic_eval.py`; Ruff and `git diff --check` pass.
The native companion results and exact drafts are stored in
[`behavior-results.json`](../evals/scheduled_task_generic/behavior-results.json).
The companion supplements the original seven-case annotation acceptance; it does
not replace the historical service-gate migration check or demonstrate a provider
receipt. It does not invoke the production orchestrator, AuditAgent or executor.

## Historical revision-2 evidence and result

Historical revision-2 results and source hashes are recorded in
[`results.v2.json`](../evals/scheduled_task_generic/results.v2.json). The original
Consumer annotations, per-check scores, failed attempt history and bounded
feedback phases are separate records. A passing model annotation does not prove
a published report or any other external outcome.

The final harness revision-2 first-turn comparison **failed** in both arms:
baseline 5/7 and candidate 5/7 on the unchanged common checks. Both passed the
weekly migration checks, and both retained the supplied new issue identity.
This result does not establish full seven-case acceptance.

| Revision-2 first turn | Baseline | Candidate |
| --- | --- | --- |
| Daily coverage | Fail: OTP promoted into retained identities rather than excluded | Fail: OTP promoted into retained identities rather than excluded |
| Weekly materials | Pass; historical service flag required | Pass; publish with coverage, no historical service flag required |
| DingTalk relevance | Pass | Fail: old broadcast promoted into retained identities rather than excluded |
| Calendar update | Fail: superseded card promoted into retained identities rather than excluded | Pass |
| Email OTP | Pass | Pass |
| Meeting unknown owner | Pass | Pass |
| OA source failure | Pass | Pass |

The failed cases contain a typed identity selection error: notes identify the
OTP, superseded card or older broadcast as non-management or superseded, but
`include_ids` retains them and the required `exclude_ids` entries are absent.
These remain failures under the frozen rubric. The final annotations are not
rendered report bodies or sent messages, so this evidence cannot determine
whether actual delivered content would contain the same mistakes.

The one bounded native Audit/correction also **failed**: baseline remained
5/7; candidate changed to 4/7. Both continued to pass the weekly migration
checks. Original first-turn scores are preserved separately and are not
replaced by feedback scores.

| Final phase | Baseline common checks | Candidate common checks | Weekly migration |
| --- | --- | --- | --- |
| First Consumer | 5/7 | 5/7 | Both pass |
| After one Audit and at most one correction | 5/7 | 4/7 | Both pass |

Baseline Audit revised weekly source/validator wording but did not catch its
daily and calendar identity exclusions. Candidate Audit also missed its daily
and DingTalk identity exclusions. It additionally instructed the Consumer to
replace the synthetic wrapper dispositions `respond` and
`pending_source_failure` with the actual Skill outcome names `proposal` and
`failed`. The corrected annotations followed that feedback. The unchanged
rubric counts those wrapper dispositions as failures, so OA becomes a third
failed candidate case; DingTalk was already failing its identity checks.
Candidate OA notes still distinguish the connector timeout from applicant
omission, leave the approval pending and prohibit an approval/rejection or
applicant-facing action. These findings expose limitations in annotation
fidelity and the synthetic Audit wrapper; they do not demonstrate a real
provider action or a change in the production OA terminal state.

All six final native phases completed without a non-message/tool runtime item.
Both primary and feedback temporary-artifact cleanup receipts are true.
At the end of that earlier work, no additional harness revision, Skill change,
model change or rerun had followed the bounded revision-2 result. The current
user request explicitly authorizes diagnosing and fixing the remaining failures.  This comparison **does not pass the fixed
seven-case acceptance check**, and the Audit probe does not establish that the
production feedback mechanism resolves these cases.

## Frozen comparison

- Cases and scoring rules:
  [`cases.json`](../evals/scheduled_task_generic/cases.json), version 1.
- Case file SHA-256:
  `938dcb51c9207d7d79bdc4240a50d827fbd1b376b606541b62799517fe862868`.
- Baseline: `be1ffd86fd4d34e814c2436f3f732d1c07d539e1`, explicitly pinned to
  the original deployed version when the migration began. This is not a claim
  that the baseline remains the currently deployed production version.
- Candidate: `e6c2a2ef68dc6a035b36e09f89f8e624728fc7c3`, the merged source
  snapshot identified by `candidate_git_ref`
  and the eleven individual `source_sha256.candidate` entries in the result.
  File hashes identify the exact loaded source even when a Skill edit was
  initially uncommitted.
- Each arm receives the same seven cases without their expected answers, the
  same output schema, native runtime, model selection, reasoning configuration,
  timeout and execution restrictions. Only the supplied Skill source differs.
- Each arm runs one batched seven-case Consumer response. If a final first-turn
  case fails, each arm receives one independent native Audit and at most one
  Consumer correction. Expected answers and scoring rules are never supplied
  to either role.

| Case | Decision checked |
| --- | --- |
| Daily coverage | Missing optional minutes/group inputs do not block a useful report; waiting, decision and watch items remain; OTP is excluded. |
| Weekly materials | Optional business-line submissions are coverage gaps; retain the new issue and target; distinguish historical service publication flags from current Skill/Audit readiness. |
| DingTalk relevance | Respond to the actual staffing decision request; exclude the older cafeteria broadcast. |
| Calendar update | Reassess the current card after an update in place; use the new conflicting time and ignore the superseded card/question. |
| Email OTP | Classify as an unimportant notification; perform no mail or other action. |
| Meeting owner | Preserve the sourced action as a candidate with unknown owner; do not infer an assignee. |
| OA source failure | Distinguish a source-read failure from applicant facts; leave the claim unverified without calling it an applicant omission. |

The weekly migration check intentionally expects different policies: the
baseline requires the historical service `publishable` flag; the candidate
uses Skill judgment and the existing Audit feedback mechanism. Both must
preserve substantive evidence. The check does not introduce a replacement
service business-policy gate. Renderer field validation and artifact storage
are mechanical operations; they do not decide business publication readiness.

## Runtime and isolation

- Installed CLI: `codex-cli 0.154.0`.
- Selected production route: `codex_oauth`, requested model `gpt-5.6-luna`.
  The runner reads production `.env` through the existing `read_env_file` and
  `load_runtime_config` functions to select the model name. It does not copy
  production environment variables into the child process or record secrets.
- Native configured reasoning effort: `high`, unchanged between both arms.
  Native events do not report an independent actual model identity; the
  evidence records the requested/configured model, not an inferred identity.
- Native authentication and home directory remain unchanged. No alternate
  home, proxy, new authentication, or custom provider endpoint is created.
- Ephemeral execution, read-only sandbox, shell/apps/plugins/hooks/multi-agent
  features disabled, web search disabled and project document injection set to
  zero. Every configured MCP server is disabled using its configuration name
  only; configuration values are neither printed nor persisted.
- Native Skill catalog metadata remains visible. The model receives explicit
  source snapshots and is instructed to use only those sources and synthetic
  facts. Native diagnostics report the under-development chronicle feature and
  a Skill-description context budget warning; these are preserved separately
  from tool events.
- Model input/output/event files live in task-owned temporary directories under
  `/tmp`. The runner removes them after recording compact final annotations,
  feedback, scores, source/prompt hashes, usage and sanitized failure causes.

## Earlier attempts and harness correction

Failed attempts are retained in `attempt_history`; they are not relabeled as
passing or omitted from the comparison history.

1. A preliminary configuration launch failed before a model call because
   quoting a dotted MCP override produced a literal quoted server name and an
   `invalid transport` configuration error. The runner now uses the actual
   configured server names without embedded quotes.
2. Native-default model `gpt-6-luna` was rejected with HTTP 400:
   `The 'gpt-6-luna' model is not supported when using Codex with a ChatGPT account.`
   Both arms were blocked without semantic decisions.
3. The process environment/default service route selected `gpt-5.5`, which
   received the equivalent unsupported-model error in both arms. This was not
   the production `.env` route. The authorized production route selected
   `gpt-5.6-luna`, which executed successfully.
4. The first working production-model pair scored baseline 6/7 and candidate
   7/7 on common checks, but the candidate failed the migration check by
   retaining the historical service publication requirement. The weekly Skill
   was clarified to describe the approved retirement of that requirement.
5. The next pair scored baseline 7/7 and candidate 6/7, with both migration
   checks passing. The candidate used a JSON field name in place of the
   supplied issue identity. The weekly renderer field documentation was also
   corrected to include the existing required `issue.id` and top-level `as_of`.
6. The final revision-1 source pair still scored baseline 7/7 and candidate
   6/7, with both migration checks passing. After one independent Audit and
   one correction per arm, scores remained 7/7 and 6/7. The candidate retained
   the issue in its notes but omitted its literal identity from `include_ids`.
   The Audit itself incorrectly narrowed that field to document identities.
   This is an annotation contract failure; the probe generated no report body,
   so it does not prove that a real report would omit the issue.
7. Harness revision 2 defines `include_ids` consistently in the Consumer schema,
   Consumer instruction and Audit instruction: every literal identity of
   substantive retained facts, sources, output targets, issues and work items
   belongs in the typed identity list; JSON property names are not identities,
   and a note does not substitute for that list. This clarification applies to
   both arms and discloses no expected identities or business decisions. Cases,
   rubrics and production Skills were not changed for this harness correction.
   Exactly one revision-2 pair was run. The later revision-3 pair above follows a
   demonstrated protocol defect and the user’s explicit repair request, rather
   than repeating an unchanged experiment until a favorable sample appears.

## Publication boundary for this repair

Only eval code, focused tests and documentation changed. Service runtime, business
Skills, schedules, prompts and production configuration were not modified by this
repair, so it requires no service deploy or restart. No completed job was replayed.
The prior migration's deployment/readback record remains separate in
[the implementation record](scheduled-task-generic-implementation.md).

## Reproduction

From an authorized development checkout with the same installed native runtime
and production model configuration:

```sh
/Users/derek/miniforge3/bin/python evals/scheduled_task_generic/run.py --model-source production-config
```

Only when a case in that new pair fails, and only once for that result:

```sh
/Users/derek/miniforge3/bin/python evals/scheduled_task_generic/run.py --audit-feedback-only
```

The feedback command verifies the cases, Skill hashes and native settings
against the saved pair and refuses an already recorded feedback budget. A new
primary invocation preserves the previous compact result in attempt history.
This documentation records the authorized execution count; the runner is not a
production runtime or an approval mechanism.

The separate draft probe uses the same production-selected model and disabled
tools. A new generation refuses to overwrite an existing result:

```sh
python evals/scheduled_task_generic/behavior.py
```

To reproduce only the fixed deterministic readback of the saved version-2 native
drafts, use `rescore_saved_result(saved, cases)` from `behavior.py`. The recorded
`python evals/scheduled_task_generic/behavior.py --rescore` command made **zero
model calls**, preserved old scores and raw deliverables, and refuses an already
recorded scoring revision. It is not a new native experiment.


## What this establishes

This is real native model behavior on synthetic decision annotations, distinct
from Python unit tests. It checks seven selected business decisions and the
intended weekly policy migration. It does not estimate statistical quality,
exercise every one of the fourteen scheduled tasks, execute renderer scripts,
test real provider permissions or validate live business outcomes.

The optional native Audit probe uses the service's role pattern but does not
invoke the actual application AuditAgent, orchestrator, feedback persistence,
delivery code or provider tools. Full production lifecycle and external
receipt/readback evidence remain separate checks.

## OKR composite workflow acceptance

The seven cases above remain frozen. The separate four-case native OKR workflow
uses real prepare/render/destination scripts and the production purpose parser,
with synthetic provider IO and an independent native audit/correction. Its failed
probes, current status, root causes, and real publication readback are recorded in
[OKR composite acceptance](scheduled-okr-agent-acceptance.md). It does not replace
production orchestrator acceptance or future natural scheduled execution.

Final OKR composite status (2026-10-20 UTC): **4/4 passed**, with real application artifact/document callbacks and simulated provider IO. The final record preserves original first-turn and feedback failures, same-output observer rescoring, the exact source/contract hashes, and cleanup. See the [composite acceptance record](scheduled-okr-agent-acceptance.md); real historical publication and future natural execution remain separate.
