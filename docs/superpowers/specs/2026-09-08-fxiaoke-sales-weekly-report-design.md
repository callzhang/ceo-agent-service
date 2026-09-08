# Fxiaoke CLI and Sales Weekly Report Design

**Date:** 2026-09-08
**Status:** Approved design, pending written-spec review
**Scope:** Give CEO Agent direct read-only use of the installed `sharecrm` CLI and add an on-demand sales weekly report Skill.

## 1. Objective

CEO Agent shall generate an evidence-backed sales weekly report on demand. It
shall read business targets from the current company OKR or another explicitly
approved sales plan, read actual sales results from Fxiaoke through the locally
installed `sharecrm` CLI, calculate deterministic progress scores for the
company and each business line, and save one final Markdown report under the
configured `CEO_WORKSPACE`.

The first version does not add a Fxiaoke MCP server, a CRM-specific
`agent_cli` wrapper, a Python CRM client, scheduled generation, CRM event
ingestion, database tables, or CRM write operations.

## 2. Confirmed Product Choices

- CEO Agent uses the native `sharecrm` CLI inherited through the existing
  Codex runtime rather than the configured remote `crm_connector` MCP.
- The report is a CEO sales operating overview, not only a pipeline report or
  a cash report.
- Targets come from the current company OKR or an explicitly approved sales
  plan. CRM is authoritative for actual sales results.
- If CRM also contains targets, the report compares them with the authoritative
  OKR or plan and reports any mismatch instead of silently selecting one.
- Scores are produced at company and business-line level. The report does not
  rank individual salespeople or convert operational deviation into a
  personnel judgment.
- Generation is on demand. This design does not introduce a weekly scheduler or
  task producer.
- Only the final Markdown report is saved. Raw CRM responses and intermediate
  calculation files are not persisted as report artifacts.
- CRM read-only behavior is governed by Skill instructions and Codex automatic
  command review. The user explicitly accepts that this is not a code-level
  `sharecrm` command allowlist.

## 3. Current Runtime Basis

The installed CLI is `/opt/homebrew/bin/sharecrm`. The CEO Agent launchd
configuration includes `/opt/homebrew/bin` in `PATH`, and the background Codex
runtime inherits the user's environment and existing CLI authentication. A
live read-only object-description call succeeded during design discovery.

The background role command removes the dangerous approval-bypass flag and
configures Codex with `approval_policy="on-failure"` and
`approvals_reviewer="auto_review"`. Automatic review is a second review layer;
it is not represented by this design as a guaranteed read-only sandbox.

## 4. Skill Architecture

Add one service-managed business Skill named `ceo-sales-weekly-report` and add
it to the managed business Skill catalog. It is discoverable for explicit
sales-weekly-report requests but does not receive a separate task-production
feature toggle because this version has no independent producer.

The new Skill has three dependencies with separate responsibilities:

| Skill or capability | Responsibility |
| --- | --- |
| `ceo-sales-weekly-report` | Select report evidence, reconcile targets and actuals, calculate scores, identify deviations, and render the final report. |
| `ceo-weekly-report` | Supply shared management-report rules: Beijing reporting window, evidence classes, missing-data behavior, cross-week issues, milestones, privacy, and decision framing. |
| `fxiaoke-crm-cli` | Supply current Fxiaoke object discovery, field validation, pagination, metric definitions, command discovery, and troubleshooting rules. |

The new business Skill must instruct the Agent to read both dependency Skills
before querying CRM or drafting the report. It must not copy their full command
references or management rules into a second divergent source of truth.

## 5. Allowed and Forbidden CRM Behavior

### 5.1 Allowed reads

The Agent may use `sharecrm` to:

- inspect authentication status and CLI help;
- identify a business object from a business term;
- describe an object's fields, option values, relationships, and business
  types;
- resolve a record name to an exact object and record;
- read a record by confirmed ID;
- perform structured detail queries with explicit filters, ordering, and
  pagination;
- perform structured count, sum, average, minimum, maximum, and grouped
  aggregate queries;
- read other CRM data needed for the report when the discovered command is
  unambiguously read-only.

The Agent must prefer the current structured semantic query interfaces, such
as `semantic object-data-query` and
`semantic object-aggregate-data-query`, over generated SQL. It must inspect the
live object description before selecting fields or status values. It must
finish pagination before claiming a complete result.

### 5.2 Forbidden CRM effects

The Agent must not:

- pass `--confirm` or `--yes` to `sharecrm`;
- create, update, delete, invalidate, or otherwise mutate a CRM record;
- claim, allocate, assign, transfer, return, reclaim, or move a customer or
  lead;
- create a follow-up, sales activity, task, visit, or interaction record;
- change an opportunity stage, owner, forecast, amount, or expected date;
- send email, IM, feed, notice, or other CRM communication;
- approve, reject, cancel, or otherwise act on a CRM approval;
- start, advance, modify, or cancel BPM, workflow, flow, stage, automation, or
  schedule state;
- log in, log out, import token data, or change CLI configuration;
- execute a command whose read-only nature cannot be established from current
  CLI help and the applicable operation Skill.

When a required read cannot be performed safely, the Agent records the metric
as `无数据` with the precise missing capability or evidence. It must not infer
an actual value or substitute a target.

The CRM read-only restriction does not prohibit writing the explicitly
requested final report to the configured local workspace.

## 6. Reporting Window and Evidence Contract

Unless the request supplies an explicit interval, the report uses
`Asia/Shanghai` and covers the preceding Monday at 00:00 inclusive through the
current Monday at 00:00 exclusive. If the report is run before the end of a
requested period, it uses the actual query time as the cutoff and displays that
cutoff.

Every consequential metric states:

- target period and target source;
- CRM object;
- date field;
- metric field;
- status field and included status values;
- included record count or aggregation coverage;
- formula;
- query cutoff and timezone;
- whether pagination completed;
- material masking, permission limits, or missing data.

The report separates verified facts, participant statements, forecasts,
current judgments, and hypotheses. Plans, forecasts, meeting statements,
opportunity amounts, signed contracts, recognized revenue, delivered value,
and collected cash are not interchangeable.

## 7. Report Contents

The Markdown report contains the following sections:

1. `CEO销售判断`
2. `公司业务目标进度评分`
3. `公司销售经营指标`
4. `MorningStar`
5. `Friday`
6. `国际业务`
7. `世界模型营销`
8. `重点Pipeline与异常`
9. `回款、应收与交付风险`
10. `跨周问题与下周检查点`
11. `需CEO讨论与决策`
12. `数据口径、覆盖率与缺失项`

The report emphasizes abnormalities requiring management attention. Activity
such as communicating, scheduling, visiting, or working does not prove
delivery, acceptance, contract, revenue, or collection.

Top Pipeline items are ranked by amount impact, win likelihood, time urgency,
and need for management intervention. A Lead requires a clear customer need,
concrete functional requirements, and explicit product match. An Opportunity
also requires confirmed budget and decision maker. The report flags a mismatch
without modifying CRM.

## 8. Business-Line Attribution

The report produces one company score and separate scores for MorningStar,
Friday, international business, and world-model marketing.

A CRM record belongs to a business line only when a live CRM field or an
authoritative target source provides an explicit, reproducible mapping. The
Agent must not classify a record by customer-name keywords, salesperson-name
keywords, or informal inference. Records that cannot be attributed are listed
under `待归属`, remain in any valid company-level total, and are excluded from
business-line scores.

The report does not produce individual rankings. A named owner appears only
where required for a business result, next checkpoint, or acceptance standard.

## 9. Deterministic Scoring

### 9.1 Base formulas

For metrics where higher values represent progress:

```text
target_completion_rate = actual / period_target
time_progress_rate = elapsed_time / total_target_period
progress_index = target_completion_rate / time_progress_rate
```

The calculation uses the target's actual start and end dates. A weekly result
may be displayed separately, but a quarterly or annual target is scored using
its quarterly or annual elapsed-time denominator rather than treating the
weekly value as the full-period actual.

For bounded metrics where lower values are better, including overdue
receivables and sales-cycle duration, the Skill applies an explicitly labeled
inverse formula appropriate to the target definition. It must not divide by
zero or treat an undefined target as zero.

### 9.2 Score mapping

The following piecewise-linear mapping produces a stable score without Agent
judgment:

| Progress index | Score range | Display status |
| --- | ---: | --- |
| `>= 1.10` | `100` | `✅ 超前` |
| `0.95 <= index < 1.10` | `90–100` | `✅ 正常` |
| `0.80 <= index < 0.95` | `75–90` | `⌛ 轻度偏离` |
| `0.60 <= index < 0.80` | `50–75` | `⚠️ 明显偏离` |
| `< 0.60` | `0–50` | `❌ 严重偏离` |

Within each interval, interpolate linearly between the stated endpoints and
round the displayed score to the nearest integer. Negative actuals are not
silently clamped; the report must expose the source value and explain whether
the metric definition permits it.

### 9.3 Default weights

| Metric group | Weight |
| --- | ---: |
| New signed contracts or orders | 25% |
| Recognized revenue | 15% |
| Payment collected | 20% |
| Gross profit or gross margin | 10% |
| Weighted Pipeline | 15% |
| Opportunities advancing to the next Gate | 10% |
| Overdue receivables and material sales risk | 5% |

An explicitly approved target document may provide different weights. The
report must disclose which weight set it used; the Agent must not invent a
custom set during a run.

### 9.4 Missing values and coverage

A metric is scored only when it has:

- a valid authoritative target;
- a compatible CRM actual;
- a defined period;
- a disclosed and consistent measurement definition.

Unscored metrics are removed from the score denominator and reduce score
coverage. Remaining eligible weights are normalized only to calculate the
displayed score. The report always shows both values, for example
`82/100，评分覆盖率 75%`. A low-coverage score must not be presented as a
complete view.

Target conflicts, missing permissions, incomplete pagination, undefined
business-line attribution, and incompatible target/actual definitions remain
visible even when another metric can be scored.

## 10. Workspace Output

The configured workspace is resolved from `CEO_WORKSPACE`; the implementation
must not hard-code the current machine's concrete path. The report destination
is:

```text
${CEO_WORKSPACE}/01_业务与客户/销售周报/YYYY/
```

The filename is:

```text
YYYY-MM-DD-HHmm-销售周报.md
```

The timestamp uses `Asia/Shanghai`. A new on-demand run creates a new file and
does not overwrite an earlier report. The implementation creates only the
necessary sales-report year directory, uses an atomic file replacement for the
new unique target, and returns the resolved report path as an artifact.

Only the final Markdown is saved. It excludes OAuth material, raw CLI debug
logs, complete raw CRM responses, customer/contact phone numbers, unrelated
personal data, and intermediate calculations. Necessary business record names
and aggregate evidence may appear when they are relevant to the report and
visible to the authenticated CRM user.

## 11. Failure Behavior

- Missing or expired CLI authentication produces a failed report run with the
  precise login-state reason. The Agent does not initiate login.
- A missing CLI, unsupported command, permission denial, rate limit, network
  failure, invalid field, invalid status, malformed JSON, or incomplete page is
  not reported as zero data.
- A failure in one non-critical metric produces `无数据`, lowers coverage, and
  leaves the failure visible.
- A failure in targets, all CRM actuals, report scoring, or final workspace
  persistence prevents report completion.
- No CRM command is retried as a write. Read retries, if used, must remain
  bounded and must not conceal the original failure.
- The final file is reported complete only after reopening it and verifying its
  identity, report period, score, coverage, required sections, and non-empty
  source definitions.

## 12. Testing and Acceptance

### 12.1 Skill contract tests

Tests verify that `ceo-sales-weekly-report`:

- requires `ceo-weekly-report` and `fxiaoke-crm-cli`;
- declares the target-versus-actual authority split;
- contains the complete CRM read-only and forbidden-effect boundary;
- forbids `--confirm` and `--yes`;
- requires live object and field discovery;
- requires complete pagination and disclosed metric definitions;
- implements company and business-line scoring without individual rankings;
- treats missing values as reduced coverage rather than zero;
- writes only the final Markdown under `CEO_WORKSPACE`;
- creates a unique report rather than overwriting an existing one.

### 12.2 Deterministic calculation tests

Fixtures cover:

- on-plan, ahead, lightly deviated, materially deviated, and severely deviated
  progress indexes;
- interval-boundary values;
- inverse metrics;
- quarterly and annual target periods;
- missing targets, missing actuals, zero targets, negative actuals, and target
  definition conflicts;
- weight renormalization and score coverage;
- unattributed records included at company level but excluded from
  business-line scores.

If scoring remains expressed only as Skill instructions rather than application
code, these cases must be covered by focused live semantic evaluations with
fixed inputs and exact expected output. If repeatability is inadequate, moving
only the arithmetic into deterministic code requires a separate approved
design change.

### 12.3 Runtime acceptance

Acceptance requires one background CEO Agent run, not only an interactive shell
test. The run must demonstrate:

1. selection and loading of the new business Skill and both dependency Skills;
2. successful native `sharecrm` authentication status and read-only CRM query;
3. no completed CRM write command;
4. target and actual reconciliation;
5. company and business-line score calculation with coverage;
6. final Markdown creation at the configured workspace destination;
7. reopening and validating the saved report;
8. persisted task/run and artifact evidence;
9. successful managed Skill load receipt after service restart;
10. a healthy service with no unresolved `failed` or `processing` backlog
    introduced by the change.

No test executes a CRM write in order to prove that the Skill is read-only.
The accepted boundary is verified through Skill contract tests, completed tool
events in the background run, and Codex automatic command review configuration.

## 13. Explicit Non-Goals

- Fxiaoke MCP or remote `crm_connector` authentication.
- CRM-specific `agent_cli` tools or code-level command allowlisting.
- Scheduled weekly generation.
- CRM webhook, polling, or automatic task creation.
- CRM record writes, messages, approvals, workflows, or configuration changes.
- Per-salesperson scoring or ranking.
- Publishing the report to DingTalk, email, IM, or another remote system.
- Persisting raw CRM datasets or creating a sales-report database.
- Changing the existing Consumer/Audit lifecycle or adding a new safety state.
