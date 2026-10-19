# Scheduled task migration: native Agent validation

This validation compares decisions made from the original and migrated Skill
sources on seven frozen synthetic cases. It uses the installed native Codex CLI
with the production configuration's selected Codex OAuth model. It performs no
service invocation, business provider read, message send, calendar mutation or
document write.

## Evidence and result

Final results and source hashes are recorded in
[`results.json`](../evals/scheduled_task_generic/results.json). The original
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
No additional harness revision, Skill change, model change or rerun followed
the bounded revision-2 result. This comparison **does not pass the fixed
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
   Exactly one revision-2 pair is authorized; it is not repeated until passing.

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
