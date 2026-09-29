---
name: ceo-weekly-report
description: Use when preparing, reconciling, reviewing, validating, or publishing Derek's weekly CEO management report or its company and business-line sections from DingTalk evidence.
metadata:
  managed_by: ceo-agent-service
---

# CEO Weekly Report

## Overview

Produce an evidence-backed management mechanism, not an activity summary. Track
results, deviations, recurring causes, decisions, and unresolved issues across
Beijing-calendar weeks.

Read [report-contract.md](references/report-contract.md) and
[dingtalk-runbook.md](references/dingtalk-runbook.md) completely before work.

## Required Inputs

- **Service materials**: run the command given in the task prompt with the
  trigger's `scheduled_task_run_id`. It returns the target Monday, the Beijing
  window and cutoff, the target and previous `管理层周会` documents (title,
  node ID, URL, whether the target exists, its year page), and every meeting
  the service recorded in the window with its participants, its follow-up
  message and the path of its archived transcript. Do not ask anyone to
  confirm the target; it is the upcoming meeting, never one already held.
- Prior report: the previous meeting document from the materials.
- Current company DingTang OKR.
- Business-line material: what the MorningStar, Friday, international and
  world-model marketing owners wrote in section 四 of the target document and
  the reports linked under 六. A line with nothing written yet is recorded as
  `未提交` in the manifest's `business_reports`, gets a coverage warning and
  a native mention in the report, and does not block the run.

Publication is blocked only when the materials command fails, the target's
revision changes under the write, or the write cannot be read back.

## Workflow

1. Create the dated run directory under
   `/Users/derek/Documents/memory/02_管理与组织/CEO周报运行/` and save the
   materials output there as `materials.json`.
2. When the target does not exist, create it in the materials' year page (create
   the year page under the meeting folder first if it is missing) with the
   previous meeting document's section headings and its `重点问题及待办跟踪`
   table, then use it as the target. Fetch the target and previous documents
   in full; save the target revision and JSONML as `target-before.jsonml`.
3. Screen every meeting in the materials from its title, participants and
   follow-up message; read the archived transcript file of each one relevant to
   Derek's management responsibilities or a business line.
4. Search all accessible chats for Derek's messages. Read sufficient context
   for every selected message.
5. Read the business-line material and linked evidence without editing it.
   Read authoritative operating actuals when accessible; otherwise write `无数据`.
   Use CRM as the sole system of record for sales data, including orders,
   Pipeline, Lead/Opportunity Gate, expected signing, and commercial POC
   status. Do not ask finance to supply or confirm sales-funnel data. Finance
   remains the source only for accounting metrics such as recognized revenue,
   cash received, gross margin, and overdue receivables.
   For decision-critical missing actuals, include the responsible native mention
   and a dated next checkpoint; never substitute a target or plan for an actual.
6. Extract prior open issue IDs from the previous document and the target's
   tracking table. Reconcile new evidence against the original question, prior
   diagnosis, deadline, and closure standard.
7. Draft the seven-section `report.json` with `as_of` set to the cutoff's
   Beijing date. Classify every consequential claim and retain evidence IDs.
8. Run `scripts.validate_run`; fix blockers locally. Render with
   `--meeting-document` and `--draft-output draft.jsonml`; the draft keeps the
   complete seven sections in the run directory. The renderer refuses to write
   a malformed node; also run `python3 -m scripts.jsonml_check target-after.jsonml`
   and `dws doc +script --command parse --doc-format jsonml --content @target-after.jsonml`
   (read-only), and do not write until both pass. `dws doc +update --dry-run`
   does not validate the body.
9. The scheduled run is authorized to write the target meeting document
   (Derek 2026-09-24). Save a recoverable document version, re-fetch the
   revision, and stop on any change.
10. Perform one revision-guarded overwrite. Fetch the complete result and pass
    every readback check before reporting completion. Unknown write status
    requires readback, never a blind second write.

## What the report writes into the meeting document

Only three places, and nothing else in the document changes:

- `二、CEO本周判断`: the CEO judgment, followed by the cross-week issues table,
  the cockpit abnormalities, the root-cause guidance and the decisions needed.
- `三、公司级重点指标`: the company metrics table.
- `一、重点问题及待办跟踪`: one new row per issue whose ID is not already in the
  table. Existing rows belong to their owners and are not edited.

The business-line sections, the topic discussion and the report links are the
meeting participants' and stay as they are.

## Management Reasoning

- Ask what result changed, what proves it, what deviated, why it recurred, and
  which management choice is required.
- Separate fact, participant statement, forecast, CEO judgment, current
  judgment, and hypothesis. Treat coordination gaps as coordination gaps rather
  than unsupported blame.
- Friday demand, resources, and timing require Derek's unified confirmation.
  Financial demand requires Shawn and Derek alignment.
- MorningStar's overall plan is decided by Shawn; market direction, product
  path, and technical leadership require Derek's confirmation and feedback.
  Test the plan for new opportunities, customer profile, sales assumptions,
  market validation, leading features, and commercial feedback loops.
- Reconcile Friday product and technical milestones, release Gate, business and
  general Eval ownership, scenario evidence, staffing, and reusable assets.

## Completion Gate

Completion requires `publishable: true`, a saved version, unchanged expected
revision, one guarded write, and a full matching readback in which everything
outside the three written places equals `target-before.jsonml`. A successful
command response alone is insufficient.
