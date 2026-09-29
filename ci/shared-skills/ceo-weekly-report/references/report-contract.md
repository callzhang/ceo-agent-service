# CEO Weekly Report Contract

## Reporting Window

Use `Asia/Shanghai`. For a target dated Monday, collect the preceding Monday
00:00 inclusive through the target Monday 00:00 exclusive. If the run starts
early, use the actual run time as cutoff and display it.

## Source Coverage

1. Resolve the exact target and prior report by title, node ID, URL, and
   revision.
2. Inventory every accessible AI Minutes record in the window and verify
   pagination completion.
3. Read title, summary, action items, participants, and linked documents for
   every record.
4. Read the complete transcript for records relevant to Derek's management
   responsibilities or any of the four business lines.
5. Search every accessible conversation for Derek's messages in the window and
   read sufficient surrounding context for every selected message.
6. Read the four source reports and linked evidence without modifying them.
7. Read current company OKR, CRM, finance, product telemetry, release, quality,
   and delivery evidence when accessible and relevant.

## Evidence Classes

| Class | Publication rule |
| --- | --- |
| `verified_fact` | Direct authoritative evidence; publish with source and date. |
| `participant_statement` | Attribute the status to its speaker. |
| `forecast_or_judgment` | Label it and retain prior/current/actual/reason. |
| `ceo_confirmed_judgment` | Derek explicitly stated it in a verified source. |
| `current_judgment` | At least two independent sources support it and counterevidence was reviewed. |
| `hypothesis_pending_validation` | Publish only as a question or hypothesis. |

Never infer a speaker from an unreliable shared-room label. An activity such as
communicating, scheduling, or working does not prove delivery, acceptance, or
closure.

## Source Priority

For targets and strategy: latest explicit Derek decision, current company OKR,
formally approved plan, business-line plan, department or individual OKR, then
meeting proposal or draft. Show conflicts instead of silently overwriting them.

For sales actuals, CRM is the sole system of record: orders, Pipeline,
Lead/Opportunity Gate, expected signing, and commercial POC status must come
from current CRM records with defined fields. Contracts and customer evidence
support a CRM record but do not replace it. Do not request sales-funnel data
from finance. For accounting actuals, use finance/bank records for recognized
revenue, cash received, gross margin, and overdue receivables. For product and
delivery actuals, use system telemetry, customer written confirmation, or a
linked delivery artifact. Missing actuals remain `无数据`.

## Report Structure

1. `CEO本周判断`: one screen of material changes, strengthened or weakened
   judgments, recurring root causes, and required choices.
2. `公司级重点指标`: 12 fixed metrics plus at most 3 exception metrics, each
   with target, previous, current, change, status, cutoff, definition, and
   evidence.
3. `跨周问题与行动`: stable ID, original question, current cause, prior state,
   new evidence, status, weeks open, next checkpoint, native mention, and
   closure standard.
4. `经营驾驶舱与CRM`: only abnormalities requiring management attention.
5. `四条业务线`: MorningStar, Friday, international, and world-model
   marketing. Each has a one-page conclusion, metrics table, milestone table,
   source link, and folded complete source report.
6. `根因判断与管理指导`: label every conclusion as CEO judgment, current
   judgment, or hypothesis.
7. `需讨论与决策`: only matters requiring an explicit management choice.

## Fixed Company Metrics

1. New signed orders.
2. Recognized revenue.
3. Payment collected.
4. Gross margin.
5. Overdue receivables.
6. Weighted Pipeline.
7. Opportunities advancing to the next Gate.
8. Friday KA paid POC/orders.
9. Friday SMB WAU/paid conversion.
10. MorningStar orders/margin/new-opportunity validation.
11. International Pipeline/paid POC.
12. World-model validated demand/paid POC.

Status icons are `✅` on plan, `⌛` in progress with evidence, `⚠️` deviation or
material evidence gap, `❌` overdue or missed, and `❓` undefined or no data.
Every value requires a definition, data date, and source. Decision-critical
missing data needs both a native mention to the responsible person and a dated
next checkpoint. A target or plan must never be copied into the actual field.

## Business-Line Metrics

- MorningStar: orders, revenue, collection, gross margin, delivery risk,
  qualified Pipeline, validated new opportunities, product-feedback closure,
  and evidence for incremental revenue.
- Friday: KA paid POC and order movement, reusable Domain Pack/Recipe/Memory/
  workbench/benchmark assets, SMB WAU/conversion/retention/churn, release Gate,
  business and general Eval readiness, product effect, and technical leadership.
- International: effective customer meetings, named qualified Pipeline, paid
  POC, channel partners, first Logo/revenue, quarterly investment, approved
  twelve-month recurring-revenue conversion, and Stage evidence.
- World-model marketing: named customers, measurable pain-point validation,
  paid POC, standard capability/reuse, recurring and project revenue, gross
  margin, and delivery-to-product feedback closure.

## Milestone Rules

Use `日期 | 负责人 | 里程碑 | 交付物和验收证据 | 进度 | 状态`. A milestone name
is a short project or result name, not a work category. A deliverable is an
inspectable document, release, customer confirmation, order, revenue result,
dashboard, accepted dataset, or equivalent result. Never write `见本周进展`.
Missing evidence is `无` plus a native mention.

Every business-line report object must therefore contain a `milestones` list.
Each row uses `date`, a resolved `owner` with `name` and `user_id`, `name`,
`deliverable_evidence`, `progress`, and `status`.

## Sales and Gate Rules

Rank Top Pipeline by amount impact, win likelihood, time urgency, and need for
management intervention. A Lead needs a clear customer need, concrete function
requirements, and explicit product match. An Opportunity additionally needs a
confirmed budget and decision maker. Flag evidence mismatch without changing
CRM.

Forecasts may change, but show previous forecast, current forecast, actual
result, and reason. Escalate only material quarterly impact, repeated
stagnation, abnormal POC/quote/contract delay, company-level price/contract/
product/R&D/resource choices, or a blocker the operating owner cannot resolve.

## Issue Rules

Use stable prefixes `COMP`, `SALES`, `FRI`, `MS`, `INTL`, `WM`, `RD`, `DEL`,
and `ORG`. Match meaning and evidence before allocating a new ID. Retain the
original question when the diagnosis changes. An open prior issue cannot
disappear; write `无新增证据` if it was not mentioned.

A missed explicit deadline turns red immediately. Two weeks without Gate
movement is at least yellow. Three weeks without evidence requires one of:
continue, change owner, downgrade, or stop. Closure requires an inspectable
outcome and acceptance evidence.

## Root-Cause Test

For each selected issue record the observed phenomenon, direct evidence,
counterevidence, prior recurrence, tested surface explanation, current cause,
managerial change, and next-week proof. Distinguish market opportunity from
execution, business demand from project requests, a bug from an unmet product
requirement, development completion from release/customer readiness, and a
meeting from received and understood feedback.

## Privacy and Tone

Exclude compensation, health, family, private emotion, and unrelated one-to-one
content. Describe plan, evidence, deviation, risk, behavior, business effect,
and required decision. Do not turn weak evidence into a personnel judgment or
use an accusatory formulation when planning and coordination are the actual
intervention.
