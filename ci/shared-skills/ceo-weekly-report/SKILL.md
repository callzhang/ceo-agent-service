---
name: ceo-weekly-report
description: Use when preparing, reconciling, reviewing, validating, or publishing Derek's weekly CEO management report or its company and business-line sections from DingTalk evidence.
metadata:
  managed_by: ceo-agent-service
---

# CEO Weekly Management Report

Prepare the upcoming management meeting's CEO report from live evidence.
Read `references/report-contract.md` for business requirements and
`references/dingtalk-runbook.md` for document operations. Fixed metrics,
coverage, issue continuity, privacy and source quality are business review
requirements: assess them under this Skill and the normal Audit feedback
cycle. Publication readiness is decided by this business review and Audit.
The former service `publishable` flag and separate publication validator have
been retired; historical validation output is not a current publication
condition. Missing business-line submissions and missing CRM, finance or
telemetry actuals do not block the report, but a metric may be `无数据` only
after its source was actually tried: cite the query it came back empty from, or
the exact error, in the coverage gaps. Return `failed` only when a declared
read or the document write itself fails, never because evidence is incomplete.
Do not ask Derek to supply materials already accessible to the Agent.

## Inputs and dates

1. Call `agent_cli.read_scheduled_context`; save the exact JSON as
   `context.json` with `consumer_artifact_write`. The returned
   `artifact_directory` is the working directory for selected Skill scripts.
2. Call `consumer_run_skill_script` with name `ceo-weekly-report`, script
   `scripts/materials.py`, arguments `--context context.json --output
   materials.json --before before.json --ensure-year-page`. It computes the upcoming Monday,
   resolves target/prior meeting documents and writes complete template JSONML
   locally. It preserves the existing target's revision even when its blank
   paragraph skeleton requires using the prior complete meeting template.
   A prior meeting moved within the previous reporting week may supply the
   template only under a unique canonical title; suffixed drafts are excluded.
3. Call `read_service_activity` with the materials' explicit window and
   sources=["meetings"]. Screen all discovered meetings, then read relevant
   full transcripts using `read_meeting_archive(meeting_id)` or
   `dingtalk-minutes`; missing archives are coverage gaps. Use `dingtalk-chat`
   to read relevant group messages in the same window. Read the previous
   report, the company OKR and the business-line materials as needed.
4. Required, not optional: read CRM actuals (orders, Pipeline, Gate, expected
   signing) with the native `sharecrm` CLI in the Consumer shell, following `fxiaoke-crm-cli` (load it
   with `read_skill`). Read-only: authentication status, help, object
   description, record reads and queries; never pass `--confirm` or `--yes`,
   and never create, update, delete or follow up. `sharecrm` keeps its CRM
   login in the macOS keychain, which the shell sandbox cannot read: run it
   with escalated permissions (Codex `sandbox_permissions: "require_escalated"`,
   Claude `dangerouslyDisableSandbox`) and a one-line justification. A query
   that still fails leaves that metric `无数据` with the exact error in the
   coverage gaps. Finance has no readable source here and stays `无数据`.

## Judgment and report data

Follow the report contract's fixed KPI definitions and evidence requirements.
Keep facts, participant claims and CEO judgments distinct. Include real source
IDs, dates, responsible people and evidence for changes. Trace prior open
issues and explain new risks and decisions. Never infer ownership from
attendance. Report absent or inaccessible materials truthfully. Apply privacy
requirements from the report contract before preparing any public document.

Write structured report JSON as `report.json`. The meeting renderer reads:
`ceo_judgment` (status/text objects), `company_metrics` and `issues` arrays.
Metric fields are name, target, previous, current, change, status, data_date,
definition_evidence. Tracking rows require a top-level as_of date. Every issue
retains its stable id and supplies question, current_root_cause,
prior_state, new_evidence, status_icon, weeks_open, next_checkpoint,
closure_standard and owner={name,user_id}. Use empty arrays only when your
business review supports them. Additional full-draft sections and business-line
shapes are described in the report contract and renderer.

## Render and publish

1. Run `scripts/render_dingtalk_jsonml.py --before before.json --report
   report.json --meeting-document --output target.jsonml` using
   `consumer_run_skill_script`. Add `--draft-output draft.jsonml` for the full
   seven-section draft. The script checks the fields it actually consumes and
   the generated JSONML. It does not decide business readiness.
2. Review the resulting content under this Skill. The published meeting
   document changes only “二、CEO本周判断”, “三、公司级重点指标”, and new rows
   appended to “一、重点问题及待办跟踪”. Preserve previous rows, business-line
   sections, discussion and report links owned by participants.
3. Call `consumer_document_write` using artifact_name="target.jsonml",
   title from materials, doc_format="jsonml" and target node_id plus
   expected_revision for an existing target. For a missing target use the
   resolved year page as parent_id plus workspace_id. The materials script ensures the canonical year page exists under the
   meeting folder when --ensure-year-page is supplied.
4. Read the complete readback artifact, compare its three managed areas and
   verify unchanged participant content. A revision conflict requires a fresh
   target read and a new render. A previously completed write remains a fact
   after interruption; read the target before deciding whether another write
   is needed. Record the document link and coverage in the final result.

Use the ordinary Consumer/Audit feedback lifecycle. The system checks provider
format and document revision; report quality is judged by this Skill and Audit.
