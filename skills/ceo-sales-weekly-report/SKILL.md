---
name: ceo-sales-weekly-report
description: Use when Derek explicitly asks CEO Agent to generate, refresh, reconcile, or score an on-demand sales weekly report from current targets and Fxiaoke CRM actuals.
metadata:
  managed_by: ceo-agent-service
---

# CEO Sales Weekly Report

## Purpose

Create an evidence-backed sales management report, not an activity summary.
Produce a company score and business-line scores from authoritative targets and
current CRM actuals, explain material deviation, and save one final Markdown
artifact in the configured workspace.

This Skill runs only on an explicit report request. It does not schedule itself,
scan CRM proactively, send the report, or change CRM.

## Required Skills And Sources

Before gathering evidence, read and follow both `ceo-weekly-report` and
`fxiaoke-crm-cli` completely, including the references they require.

Use the current company OKR or an explicitly approved sales plan as the
authoritative target. Use Fxiaoke as the authority for CRM actual results. If CRM
also contains a target, compare it with the authoritative target. Record a
target-definition conflict when value, period, owner, business line, or metric
definition differs; never silently choose the CRM target.

If the authoritative target source cannot be resolved, or all material CRM
actuals are unavailable, return a failed outcome and do not create a report.

## Reporting Window

Use `Asia/Shanghai`. Unless the request supplies an explicit interval, cover the
preceding Monday 00:00 inclusive through the current Monday 00:00 exclusive. If
the requested target period is still open, use the actual query time as cutoff
and display it.

Score a quarterly or annual target against elapsed time in that target's own
period. A weekly actual does not become a full-period actual.

## CRM reads only

Use the installed native `sharecrm` CLI and its current authenticated user. Do
not copy, request, display, or persist token material.

Allowed work is limited to authentication status, CLI help, object
identification, object and field description, record-name resolution, record
reads, structured detail queries, structured aggregate queries, and other
commands whose current help unambiguously identifies them as reads.

Describe the live object before choosing fields or status values. Prefer
structured semantic detail and aggregate queries over generated SQL. Finish all
pages before claiming a complete result. Distinguish an empty result from a
failed or incomplete query.

Never pass `--confirm` or `--yes` to `sharecrm`.

Do not create, update, delete, invalidate, or otherwise mutate CRM records. Do
not assign, transfer, claim, return, or reclaim customers or leads. Do not create
follow-ups or sales activities. Do not change opportunity stages, owners,
forecasts, amounts, or dates. Do not send CRM email, IM, feed, or notice. Do not
act on CRM approvals, BPM, workflows, stages, automations, or schedules. Do not
log in, log out, import token data, or change CLI configuration.

When current help and `fxiaoke-crm-cli` cannot establish that a command is
read-only, do not run it. Mark the affected metric `无数据`, state the exact
missing capability, and reduce score coverage.

Codex automatic command review is a second review layer, not proof that an
unknown command is read-only.

## Metric Evidence

For every consequential value disclose the target source and period, CRM
object, date field, metric field, status field and included values, record count
or aggregation coverage, formula, cutoff, timezone, pagination completion, and
any masking or permission limit.

Do not substitute one business event for another. A plan is not an actual. A
meeting statement or forecast is not a CRM actual. Pipeline is not a signed
contract. A contract is not recognized revenue, delivery, or collected cash.
Activity count does not prove a business result.

## Business-Line Attribution

Produce one company score and separate scores for MorningStar, Friday,
international business, and world-model marketing.

Attribute a CRM record to a business line only through an explicit live CRM
field or an authoritative target mapping. Do not map records through customer,
project, or salesperson name keywords. Put unresolved records in `待归属`.
Include them in a valid company total but exclude them from business-line
scores.

Do not rank individual salespeople. Name an owner only for a business result,
checkpoint, or acceptance standard.

## Deterministic Progress Score

For a metric where higher values represent progress, calculate:

```text
target_completion_rate = actual / period_target
time_progress_rate = elapsed_time / total_target_period
progress_index = target_completion_rate / time_progress_rate
```

Use this piecewise-linear mapping and round the displayed score to the nearest
integer:

| Progress index | Score | Status |
| --- | ---: | --- |
| `>= 1.10` | `100` | `✅ 超前` |
| `0.95` to `< 1.10` | interpolate `90` to `100` | `✅ 正常` |
| `0.80` to `< 0.95` | interpolate `75` to `90` | `⌛ 轻度偏离` |
| `0.60` to `< 0.80` | interpolate `50` to `75` | `⚠️ 明显偏离` |
| `< 0.60` | interpolate `0` to `50` over `0.00` to `0.60` | `❌ 严重偏离` |

For a bounded metric where lower values are better, including overdue
receivables or sales-cycle duration, use an explicitly labeled inverse formula
that matches the target definition. Never divide by zero.

If an actual is negative, disclose the value and its source. Unless the
authoritative target definition explicitly permits negative actuals and defines
their score treatment, leave that metric unscored and reduce score coverage.

Use approved weights when the target source defines them. Otherwise use:

| Metric group | Weight |
| --- | ---: |
| New signed contracts or orders | 25% |
| Recognized revenue | 15% |
| Payment collected | 20% |
| Gross profit or gross margin | 10% |
| Weighted Pipeline | 15% |
| Opportunities advancing to the next Gate | 10% |
| Overdue receivables and material sales risk | 5% |

Score only metrics with a valid target, compatible CRM actual, defined period,
and consistent definition. Missing values are not zero. Remove an ineligible
metric from the score denominator, normalize remaining eligible weights for the
displayed score, and report score coverage as the original eligible weight.
Always show both the score and score coverage.

## Report Structure

Write these sections in order:

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

Show abnormalities that need management attention. Preserve prior open issue
IDs when a prior sales report exists. A missed deadline is red immediately; two
weeks without Gate movement is at least yellow; after three weeks without
evidence recommend exactly one of continue, change owner, downgrade, or stop.

## Workspace Output

Resolve the root from `CEO_WORKSPACE`; do not hard-code a machine-specific
path. If it is absent or not a directory, fail with
`ceo_workspace_unavailable`.

Write under `01_业务与客户/销售周报/YYYY/` using the Beijing timestamp filename
`YYYY-MM-DD-HHmm-销售周报.md`. Never overwrite an existing report. If the target
already exists, fail with `sales_weekly_report_path_exists` and leave it
unchanged.

Save only the final Markdown report. Do not save raw CRM responses, debug logs,
tokens, complete customer/contact records, or intermediate calculations.

Reopen the saved file after writing and verify the reporting period, score,
score coverage, twelve required sections, source definitions, and non-empty
content. Return the resolved file path as the report artifact. A generated
answer without a matching saved file is not complete.
