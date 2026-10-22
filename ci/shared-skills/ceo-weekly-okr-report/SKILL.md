---
name: ceo-weekly-okr-report
description: Use for scheduled Sunday or explicitly requested CEO-2 manager OKR progress reports, current-quarter evidence scoring, per-person scorecards, and delivery of the management report and group summary.
metadata:
  managed_by: ceo-agent-service
  version: 13
---

# CEO-2 Manager OKR Progress Report

Produce a current OKR period-to-date management report for every member of the
current CEO-2 管理群, with a management review page and individual scorecard child
documents. Use the normal Consumer → Audit → revision lifecycle and existing
controlled document/message tools. This Skill owns the business workflow and
local calculations; the service supplies source access and action execution.

Read `dingtang-okr-review` for the full evidence, business/GTM, product,
engineering, leadership, culture and deadline calibration criteria. Use its
0/20/40/60/80/100 evidence bands; system percentages and self-reports are clues,
not scores. A document deliverable requires reading its actual contents. Search
independent evidence through Friday Memory, current DingTalk documents, wiki,
minutes, messages, logs, todos and calendars. Unreadable evidence remains a gap.

## Collect and prepare

### Resume after an audit return or an interrupted Consumer turn

Before calling a source reader, read the latest Audit feedback and list the
current run's artifacts. On each resumed Consumer turn, refresh the roster and
read each current member once: `read_okr_member` is the only freshness check,
and there is no separate digest-only source endpoint. Save these receipts,
then run `prepare`. Reuse each complete prior analysis when its user ID,
period, outcome and source digest still match the refreshed input; reanalyze
only members whose source digest changed or whose analysis is incomplete.
Never repeat a successful member read again within the same Consumer turn.
After refresh and `prepare`, continue analysis immediately; do not spend the
turn recollecting data already refreshed in that turn.

Run `prepare` against the current run's saved member files before analyzing.
Preserve every complete analysis whose `analysis_reusable` is true, and resume
at the first missing, changed or Audit-identified analysis. A file containing
`analysis_failure` is not a complete analysis and must be rewritten. Write each
completed manager analysis artifact immediately before moving to the next
manager. Never replace unfinished analyses with `analysis_failure`
placeholders at the start of a turn. If a turn ends before the report is
complete, retain all valid source and analysis artifacts and return a retryable
`analysis_incomplete` result. Do not mark a still-incomplete report as a
non-retryable terminal failure merely because this turn ended; the next
Consumer turn must continue from the first incomplete manager. The normal
bounded retry lifecycle remains in force.

Treat `analysis_reusable=true` as a preserve instruction: do not rewrite that
member's analysis file. A report-level wording, duplication or layout finding
does not make an unchanged member analysis stale. Correct the changed or newly
collected member analysis that introduced the report issue, then regenerate the
affected report sections. Rewrite an unchanged member only when Audit names that
member's evidence or score as incorrect; preserve all other reusable files byte
for byte.

Treat Audit feedback as a concrete correction checklist. Resolve each finding
against the named manager and KR, then update only the affected analyses and
their report sections. Do not restart collection, rendering or publication
until every required manager analysis is complete.

1. Call `read_scheduled_context` for this run's trigger and artifact directory.
   Resolve the report date in Asia/Shanghai, using the scheduled trigger's date
   for scheduled reports and the explicitly requested date for backfills.
2. Call `read_okr_roster` with `group_name=CEO-2 管理群`. Derive the quarter
   label (YYYY Q1..Q4) from the report date unless explicitly requested. The
   result supplies `okr-roster.json`; read it with `read_task_artifact`. Use the current group
   roster and contact titles, including same-name members with distinct userId.
3. Call `read_okr_member` once per returned member with that member's exact
   `user_id`, `name`, `title`, `open_dingtalk_id` and the resolved `period_label`.
   Each result saves `okr-member-<userId-sha256>.json` with a verified source outcome. Retry a transient source failure with another member read; each read is one bounded attempt. Complete
   every member read before preparing analysis. Separate calls give source IO
   a bounded operation and expose real completed collection progress.
   Before preparing or publishing, inspect each `source_failed` receipt. A
   `scope: member` label or a diagnostic containing “member-bound” is not proof
   that the failure is isolated to that person. Treat it as member-specific
   only when the receipt gives a concrete underlying failure and verifiable
   evidence that roster identity, authorization, and shared source access are
   working. If that cannot be established, stop the run before analysis,
   scoring, rendering, document writes, or notification; report the exact
   available diagnostic and leave the prior publication untouched.
4. Run `consumer_run_skill_script` with `name=ceo-weekly-okr-report`,
   `script=scripts/report.py`, and arguments:
   `prepare --roster-file okr-roster.json --members-dir . --period-label <YYYY Qn> --report-date <YYYY-MM-DD> --output-dir .`.
   The directory may contain other files; prepare selects exact userId records.
   Read `plan.json` and each prepared manager source with `read_task_artifact`; use offset pagination until the full source is read. Shared source or
   identity/authority failures stop the report; report the exact source error.
   Verified missing goals and proven member-specific failures retain unscored sections,
   never zero scores or substituted historical goals.

Keep every file directly in the current run's artifact directory: these
commands run there, and generic artifact tools accept flat file names. The scripts are pure
local transformations and neither call an Agent nor publish externally.

## Analyze

For each collected manager, read the plan's `schema_file` by its file name.
Use `consumer_artifact_write` with `name` equal to the plan's `analysis_file`
and `content` equal to the complete analysis JSON. It has the original
`WeeklyOkrAnalysis` shape: executive_summary, company_progress,
ceo_attention_items, manager_reviews, source_coverage and warnings. The schema
contains every nested field. Each analysis has exactly one manager review and
exactly one KR review per live KR in the original source order. Preserve KR IDs
and objective/KR titles. Private ownership comes from the plan's userId-bound
file, not an identity invented in model output. Do not add `user_id` or member gaps to the model JSON.
Use the plan's `subject_label` for authored ownership references to this person;
the prepared schema states that exact label, including stable identity when names collide.
Keep the manager review's source name unchanged.
Save the plan
entry source_sha256 as `_source_sha256` alongside the analysis JSON. On recovery,
read the existing analysis when the plan marks `analysis_reusable=true` and
preserve that file verbatim. Analyze only missing or changed entries; a changed
source or scoring date requires new analysis. Audit corrections change the
identified analysis, preserving other valid members' files.

Score each commitment using actual effects, independent evidence and completion
time. Classify each KR as 业务OKR, 领导力 or 文化价值观; explain base_score and
deadline-discounted score. Use current contact title to establish role level;
unresolved roles remain 职级待确认. Use the formal dimensions from
`dingtang-okr-review`: leadership is 战略能力、团队赋能能力、组织协同能力、创新能力;
culture is AI时代原生能力、I Can I Up、简单可信赖. Map observed behaviors to these
dimensions with evidence; observation labels do not replace the policy dimensions.
For individual contributors,
leadership candidate scores apply only when explicitly assigned and never
enter their final formula. Business KRs use the completion bands in
`dingtang-okr-review`: 100 means the committed outcome is fully met with evidence;
it does not require a behavioral exemplar. For leadership and culture dimensions,
70–79 means meeting the current role standard. A score of 80 or more requires a
concrete case beyond that standard; 90 or more requires the applicable exemplar
scope; 100 requires a company-level exemplar. Below 70 names the unmet behavior
or contrary case.
Do not treat routine performance of the role standard as an above-standard
result. In particular, a manager's ordinary coaching and a director's reusable
method used within their own team can support 70–79 when performed consistently;
80+ needs a distinct result beyond the role requirement, with its scope and
effect stated.
Score each dimension independently from evidence that demonstrates that
dimension's required behavior. A broad statement that "culture behavior is
supported" does not establish AI-era capability; business delivery, customer
acceptance, feature implementation or testing does not establish innovation
unless the source connects the person's specific improvement to validation and
an observed effect. Do not transfer evidence between dimensions. If the source
does not establish the current standard for a dimension, score below 70 and
state the missing behavior evidence; do not describe missing evidence as proof
of poor conduct.
Do not substitute business outcomes for these behavioral scores.

Before rendering, verify every scored person's four leadership and three
culture dimensions one by one against their named requirement and the exact
source evidence. A summary statement that several behaviors meet standard only
supports dimensions whose named behaviors actually match; do not assume it
covers an unnamed dimension. Innovation requires a specific improvement,
validation and observed effect. AI-era capability requires a specific AI use
and an observed work quality or efficiency effect. Keep each dimension below
70 when its own standard is not established, even if adjacent dimensions are
supported. Audit must independently check all seven dimension/evidence/score
matches for every scored person and return any unsupported standard score; do
not approve based only on correct dimension labels, arithmetic or business-KR
evidence. Independently inspect every score of 80 or more and require explicit
evidence of a result beyond that person's role standard; do not infer that
repeated work, routine same-team adoption, or a reusable method alone exceeds a
director's standard. Any correction must update affected person totals, report sections,
average and summary before full readback.

Zero reported progress is not evidence that no outcome occurred. For every KR,
inspect the actual progress history and search independent evidence using its
distinctive objective/KR terms together with the responsible manager. Read any
matching source before citing it. If targeted searches find no corroboration,
score using the existing rubric and explain the evidence gap and the basis for
the score; do not mark the manager or KR as an analysis failure. Do not infer a
result from a percentage, title, or self-report.

Keep progress, risks, next checkpoints and CEO decisions concise. Explain what
changed this week separately from period-to-date scores. Never calculate final
scores in prose: the script uses O×KR weights, the leadership/culture arithmetic
means and the original culture coefficient formula. Displayed calculated scores
use one decimal place with conventional half-up rounding.

If analysis cannot be completed for a member, create its analysis file as
`{"_source_sha256":"<current-plan-digest>","analysis_failure":{"diagnostic":"the concrete unavailable evidence or analysis failure"}}`.
This preserves the member as an unscored section; it cannot stand for a shared
provider, runtime, or authentication failure. Those failures stop the task and
remain visible in the normal task lifecycle. This field is only for a concrete
member-specific technical failure that prevents reading or interpreting that
member's source. It is not a shortcut for unfinished analysis, a large roster,
zero reported progress, missing comments, or a lack of corroborating business
evidence; those are handled by the rubric and an explained evidence gap.

## Render and deliver

Run the same script with:
`render --source source.json --analysis-dir . --output-dir .`.
The script verifies coverage, binds stable IDs, computes exact scores, and
returns paths for full.md, main.md, appendices and manifest.json. Resolve any
reported error by correcting analysis/source material. There is no service
`publishable` flag; assess the report using the evidence and this Skill.

Run `scripts/destination.py --title <exact-report-title>` through
`consumer_run_skill_script`; it resolves wiki `🎯  目标与执行` and folder
`管理者 OKR 进度周报`, creating the folder if absent. Use the returned exact
target to update an existing report,
or create it under that folder through `consumer_document_write` using
the manifest's exact `artifact_name`, `title`, `doc_format=markdown`, `parent_id` and `workspace_id`.
Existing nodes use `node_id` and their read-back `expected_revision`.
For each manifest appendix run the same destination script with --title and
--parent <main-node-id>, then create/update the returned target as a real
child of the main report.
Use this same parent-scoped resolution on recovery, updating the existing child
and its current revision. After any analysis correction, republish changed
appendices as well as the main report. Before proposing the notification, compare
all final provider readbacks with the current manifest artifacts and confirm
each appendix's parent is the main node; Audit checks these same actual results.
After every render, publish or update and fully read back every appendix in the
current manifest, even when that member's analysis is unchanged. The report date,
title, links or summary can change independently of the member analysis; an old
appendix or its previous readback does not satisfy this run's manifest.
Read back content through the returned readback artifact. Call
`read_dingtalk_document_info(node_id)` to obtain each published document's
canonical URL; preserve that provider URL for appendix links and delivery.

Use `consumer_artifact_write` to save `appendix-links.json`, a JSON object
mapping every userId to its verified appendix URL from document info, then rerun render with
`--appendix-links-file appendix-links.json` to produce the final
main.md with online links. Update the main document with its latest revision.
After its receipt, run render with that same links file and
`--document-url <main-url>` to produce summary.md. This final `summary.md` is the
canonical notification artifact: preserve its text and Markdown verbatim in the
proposal content. Audit compares the proposal directly with that artifact, not
with a separately rewritten message draft. Correct analysis inputs and rerender
to change generated reports; do not hand-edit the generated canonical files.
Propose that body and the roster's
conversation ID through the existing `dingtalk-chat`
message action. Publication and sending are normal controlled actions reviewed
by Audit; keep document and message receipts in the task result.

Report the document URL, coverage, unresolved evidence, appendix location and
actual send result. A local rendered file is a draft; provider receipts and
readback establish publication and delivery.
