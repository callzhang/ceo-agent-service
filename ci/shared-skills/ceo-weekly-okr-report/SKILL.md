---
name: ceo-weekly-okr-report
description: Use for scheduled Sunday or explicitly requested CEO-2 manager OKR progress reports, current-quarter evidence scoring, per-person scorecards, and delivery of the management report and group summary.
metadata:
  managed_by: ceo-agent-service
  version: 19
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
Provider diagnostics stay in the source receipts for Audit. Do not copy provider
status codes, machine error labels or raw diagnostics into analyses, report text
or the message proposal. Describe the business impact in plain language; an
unreadable current-quarter record does not establish that the member has no goal.
The scoring criteria in this Skill and `dingtang-okr-review` are sufficient to
complete the report. Do not invent or require a separate local performance-policy
file. If a relevant policy document is found but cannot be read, record that
source limitation and continue with the declared scoring criteria; the missing
document does not block member analysis or publication.

### Correct an Audit return before publication

Audit correction is not a prose-only edit. For each finding, identify the
source field that generated the incorrect report text. Update the affected
`analysis-<userId>.json` or `appendix-links.json`, preserve unrelated reusable
analyses, and rerun `scripts/report.py render` before publishing or proposing a
message. For example, a missing stable member ID or a duplicated member/KR risk
must be corrected in that member's analysis; editing the rendered report does
not correct its source. Never write generated report outputs with
`consumer_artifact_write`. Read the new rendered bytes, update affected provider
documents, and retain their provider write receipts before returning the revised
candidate.

## Collect and prepare

`read_scheduled_context.run_id` is this execution's scheduled run ID. Compare it
with the preceding run IDs in `read_scheduled_context.history`. If it differs
from the preceding scheduled execution, treat this as a new run and call
`read_okr_roster` plus `read_okr_member` once for every current roster member
before running `prepare`, even when earlier roster/member receipt artifacts
exist in this task's artifact directory. Overwrite old receipt filenames with
the current run's tool results; never treat an earlier run's receipt as a
current source snapshot. Reuse an earlier analysis only after the fresh
`prepare` confirms its source digest is unchanged.
`source.json` is an immutable serialization of current source receipts written
by `prepare`; never create, edit or overwrite it with `consumer_artifact_write`.
Translate source outcomes only in analysis and report prose, while keeping the
raw receipt and its diagnostic unchanged for Audit.

A Consumer retry, resumed turn or Audit correction for the same
`read_scheduled_context.run_id` continues that execution: reuse its verified
roster and member source receipts instead of rereading all members. If the first
attempt did not finish collection, read only the missing or failed members.
Never infer that a member source is unchanged from an earlier scheduled
execution or from the presence of a reusable analysis. A reusable analysis is
eligible only after the current scheduled execution's source receipt confirms
the same user ID, period, outcome and source digest.

### Resume after an audit return or an interrupted Consumer turn

Before calling a source reader, read the latest Audit feedback and call
`list_task_artifacts` for this execution generation. If this is a retry or
resumed turn for the same `scheduled_run_id`, check whether the roster and
member receipt artifacts are already present. Reuse all existing receipts;
do not call source readers for them. If collection was interrupted, read the
roster only when its artifact is missing, then read only members whose receipt
artifact is missing or whose prior receipt explicitly failed.
`read_okr_member` is the source freshness check for a new scheduled execution;
there is no separate digest-only source endpoint. Save any missing receipts,
then run `prepare`, which verifies saved identity, period, outcome and digest
and reports missing or invalid source artifacts. Do not load large successful
member receipt JSON into the model just to recheck fields that `prepare`
validates.
Reuse each complete prior analysis when its user ID, period, outcome and source
digest still match the current scheduled execution; reanalyze only members
whose source digest changed or whose analysis is incomplete. Never repeat a
successful member read within the same scheduled execution. After `prepare`,
continue analysis immediately; do not spend the retry recollecting data
already refreshed for this scheduled execution.

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

For a report/readback mismatch caused by a changed member source, update only
the changed member's analysis and regenerate all affected report files and
provider documents from the current analysis artifacts. A request to fix a
shared report, appendix set, notification summary or document revision does
not make other member analyses stale. In particular, never rewrite a file
whose current plan entry says `analysis_reusable=true` unless Audit explicitly
identifies that member's evidence or score as wrong; preserve those bytes
through the correction and verify their digests before finishing.

1. Call `read_scheduled_context` for this run's trigger and artifact directory.
   Resolve the report date in Asia/Shanghai, using the scheduled trigger's date
   for scheduled reports and the explicitly requested date for backfills.
   Call `list_task_artifacts` before deciding whether collection is needed. On
   a retry of the same `scheduled_run_id`, reuse listed roster and member
   receipt artifacts; do not repeat successful source reads.
2. For a new `scheduled_run_id`, call `read_okr_roster` with
   `group_name=CEO-2 管理群`. Derive the quarter label (YYYY Q1..Q4) from the
   report date unless explicitly requested. The result supplies
   `okr-roster.json`; read it with `read_task_artifact`. Use the current group
   roster and contact titles, including same-name members with distinct userId.
   On a retry of the same scheduled execution, read the listed roster with
   `read_task_artifact` and reuse it; do not call `read_okr_roster` again.
3. For a new `scheduled_run_id`, call `read_okr_member` once per returned member
   with that member's exact
   `user_id`, `name`, `title`, `open_dingtalk_id` and the resolved `period_label`.
   Each result saves `okr-member-<userId-sha256>.json` with a verified source outcome. Retry a transient source failure with another member read; each read is one bounded attempt. Complete
   every member read before preparing analysis. Separate calls give source IO
   a bounded operation and expose real completed collection progress. On a
   retry of the same scheduled execution, read only missing/failed member
   receipts; preserve successful receipts already saved for that execution.
   Do not reread successful receipts from this execution; `prepare` validates
   their userId, period, outcome and source digest. Refresh only a missing or
   explicitly failed receipt.
   Before preparing or publishing, inspect each `source_failed` receipt. A
   `scope: member` label or a diagnostic containing “member-bound” is not proof
   that the failure is isolated to that person. Treat it as member-specific
   only when the receipt gives a concrete underlying failure and verifiable
   evidence that roster identity, authorization, and shared source access are
   working. If that cannot be established, stop the run before analysis,
   scoring, rendering, document writes, or notification; report the exact
   available diagnostic and leave the prior publication untouched.
   A concrete current-quarter `member_not_found`/404 receipt is member-specific
   when the roster binds the requested `user_id`, the provider receipt says
   authorization was granted, and same-run reads for other roster members
   succeeded without a shared source/runtime error. Record this person as
   `source_failed` and unscored; do not claim the person has no goals, and do
   not require evidence that the roster entry itself is wrong. Missing or
   ambiguous identity, denied authorization, or same-run failures across
   members are not isolated member failures and stop the report.
4. Run `consumer_run_skill_script` with `name=ceo-weekly-okr-report`,
   `script=scripts/report.py`, and arguments:
   `prepare --roster-file okr-roster.json --members-dir . --period-label <YYYY Qn> --report-date <YYYY-MM-DD> --output-dir .`.
   The directory may contain other files; prepare selects exact userId records.
   After `prepare`, call `list_task_artifacts` and read `plan.json`. Read each
   manager's schema and source only when beginning that manager's analysis; do
   not preload a later manager's files. Use offset pagination until each file
   is fully read. Do not try to open the Python script
   source or task artifacts with `read_text_file`: read frozen Skills with
   `read_task_skill`, task-generation files with `read_task_artifact`, and run
   only reviewed script entrypoints through `consumer_run_skill_script`. A
   `text_material_path_forbidden` response for a local Skill or script path is
   a wrong-tool signal; use the corresponding Skill or artifact tool and
   continue. Shared source or
   identity/authority failures stop the report; report the exact source error.
   Verified missing goals and proven member-specific failures retain unscored sections,
   never zero scores or substituted historical goals.

Keep every file directly in the current run's artifact directory: these
commands run there, and generic artifact tools accept flat file names. The scripts are pure
local transformations and neither call an Agent nor publish externally.

## Analyze

For each manager with live OKRs, search independent DingTalk documents using the
stable userId together with the current display name, then read every relevant
exact `node_id` returned by `search_dingtalk_documents` with
`read_dingtalk_document`. A title match alone is not evidence: confirm the
document body identifies the manager and supports the specific result or
behavior being scored. Keep source node IDs and the supported claims in
`source_coverage`; do not treat the member endpoint's attached evidence as an
independent search. Search and read are required on every new scheduled run,
including a changed report date. On a same-run retry, reuse the exact source
readbacks already recorded for that generation and search only for an
Audit-identified missing or mismatched source.

Process live-OKR managers in roster order. For each manager, read that manager's
source and schema, search and read that manager's independent evidence, then
write and read back the complete schema-valid analysis artifact. Do not read
the next manager's source or schema, or search their evidence, until this
readback succeeds. Never batch searches across managers. An empty search is an evidence
gap, not a reason to stop: record that no matching document was found, do not
invent corroboration, and continue using admissible evidence already available. Score KR progress and outcomes
from the live source and other verified material; score behavioral dimensions
only from evidence that supports the dimension and apply the existing rubric
conservatively when it does not. After all manager analyses are saved, finish
report preparation, rendering, review and delivery through the normal workflow.
A missing independent document must remain visible in `source_coverage`, but it
does not make an otherwise analyzable report incomplete or justify ending the
turn after the first empty search.

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
time. For each KR, choose only an allowed `dingtang-okr-review` evidence band
(0, 20, 40, 60, 80 or 100) for `base_score`; never invent intermediate values
such as 30, 50 or 70. Explain any separate deadline discount applied by the
script. Classify each KR as 业务OKR, 领导力 or 文化价值观. Use current contact title to establish role level;
unresolved roles remain 职级待确认. Use the formal dimensions from
`dingtang-okr-review`: leadership is 战略能力、团队赋能能力、组织协同能力、创新能力;
culture is AI时代原生能力、I Can I Up、简单可信赖. Map observed behaviors to these
dimensions with evidence; observation labels do not replace the policy dimensions.
The discrete bands above apply only to each KR's `base_score`; they do not apply
to the separate leadership or culture dimension scores. For those dimensions,
70–79 is the valid role-standard band, as defined below. Do not round a supported
role-standard score down to 60 or up to 80 to fit the KR bands.
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
Under this rubric's allowed bands, missing positive evidence with no concrete
contrary case is `60`. A score below `50` requires repeated or high-impact
contrary behavior and organizational damage; never use `40` merely because the
source does not mention innovation or AI use.
Do not substitute business outcomes for these behavioral scores.

Before rendering, verify every scored person's four leadership and three
culture dimensions one by one against their named requirement and the exact
source evidence. A summary statement that several behaviors meet standard only
supports dimensions whose named behaviors actually match; do not assume it
covers an unnamed dimension. Innovation requires a specific improvement,
validation and observed effect. AI-era capability requires a specific AI use
and an observed work quality or efficiency effect. Keep each dimension below
70 when its own standard is not established, even if adjacent dimensions are
supported. A missed or incomplete business KR does not lower a leadership or
culture dimension unless the behavior evidence links that result to the named
dimension. When independent behavior evidence explicitly says a dimension meets
the person's role standard and gives matching behaviors, score that dimension
at 70 or above; a below-standard score requires dimension-specific evidence of
an unmet behavior or contrary pattern. Audit must independently check all seven dimension/evidence/score
matches for every scored person and return any unsupported standard score; do
not approve based only on correct dimension labels, arithmetic or business-KR
evidence. Independently inspect every score of 80 or more and require explicit
evidence of a result beyond that person's role standard; do not infer that
repeated work, routine same-team adoption, or a reusable method alone exceeds a
director's standard. Any correction must update affected person totals, report sections,
average and summary before publication.

Before rendering, check `next_week_focus`, risk and improvement text against the
current source evidence. Do not ask a member to produce acceptance, usage or
completion evidence that the current source already verifies. Lack of an
above-standard outcome is not itself a delivery risk; next steps should target
the evidence that is actually missing, such as a quantified effect or broader
reuse.

Zero reported progress is not evidence that no outcome occurred. For every KR,
inspect the actual progress history and search independent evidence using its
distinctive objective/KR terms together with the responsible manager. Read any
matching source before citing it. If targeted searches find no corroboration,
score using the existing rubric and explain the evidence gap and the basis for
the score; do not mark the manager or KR as an analysis failure. Do not infer a
result from a percentage, title, or self-report.

Keep progress, risks, next checkpoints and CEO decisions concise. Explain what
changed this week separately from period-to-date scores. Call a result a
"this-week" change only when a source includes a date that places it in the
report week; otherwise describe it as a verified result without a weekly time
claim. Never calculate final
scores in prose: the script uses O×KR weights, the leadership/culture arithmetic
means and the original culture coefficient formula. Displayed calculated scores
use one decimal place with conventional half-up rounding.
When auditing a displayed final score, substitute its business score,
leadership mean and culture coefficient into
`(business × 0.70 + leadership × 0.30) × coefficient`, then round to one
decimal. A formula finding must state those inputs and the correctly calculated
result; do not return a revise finding for an arithmetic result that does not
follow from the written formula.

When two or more roster members share a name, use each affected member's exact
`subject_label` (name plus stable user ID) in every report-level progress,
risk, decision and summary statement that attributes a fact to a person. Do not
use the bare shared name for those facts; keep the individual scorecards bound
to the same stable user ID throughout.

Report only source categories that this execution actually read; do not list
documents, wiki, minutes, chat or other independent sources as report inputs
unless their contents were retrieved and used. Where the evidence supports a
verified completed outcome, include it as a concise, owner-attributed progress
highlight. In `company_progress`, `manager_reviews[].progress_summary` and
`manager_reviews[].key_progress`, state completed or observed facts only; do not
append an unverified outcome or unresolved limitation to a completed-work
sentence. Put an unresolved manager/KR outcome in that manager's `risks` (or in
`ceo_attention_items` only when it requires a specific CEO decision). Keep each
material risk once in the summary. If a limitation is already stated in
`executive_summary` or `company_progress`, do not repeat it in a risk bullet;
prefer keeping the risk in the risk list and remove it from the progress
summary. Before finishing, compare each summary bullet and risk item by manager
and KR, not only by identical wording, and remove any semantic duplicate.
In the full report, put a manager/KR risk either in `ceo_attention_items` when
it needs a specific CEO decision or in that manager's `risks`, not in both.
Keep the individual appendix detail, but do not repeat the same manager/KR risk
in multiple sections of the main page.
In `数据覆盖与限制`, state each coverage boundary once. When an Audit correction
adds a limitation, merge it into the relevant existing line instead of appending
a second sentence that repeats the same coverage fact. Attribute every missing
evidence limitation to the manager/KR whose source establishes it; do not state
that a customer acceptance, deployment or result is missing without identifying
the affected owner, and check that the statement does not contradict another
manager's independently verified result. Describe `goals_missing` as a verified
empty current-period objective list, and `source_failed` using the exact
member-specific provider outcome; do not expose internal status codes in report
text.

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
When the current manifest title differs from an earlier execution because the
report date changed, resolve the new exact title and create that dated document
if it does not exist. Do not write a new-date body into a previous-date node.
Existing nodes use `node_id` and their read-back `expected_revision`.
For each manifest appendix run the same destination script with --title and
--parent <main-node-id>, then create/update the returned target as a real
child of the main report.
Use this same parent-scoped resolution on recovery, updating the existing child
and its current revision. After any analysis correction, republish changed
appendices as well as the main report. Before proposing the notification, confirm
successful provider write receipts for the current manifest and that each
appendix's parent is the main node. No automatic content readback or full-body
comparison is required. Reuse existing write receipts and unchanged artifacts;
publish changed report or appendix artifacts when the date, title, links or
analysis changes. Call
`read_dingtalk_document_info(node_id)` to obtain each published document's
canonical URL; preserve that provider URL for appendix links and delivery.
The final purpose output must include the required `evidence_refs` array, with
the saved report, appendix and provider write receipt references supporting the
proposed publication. Do not omit required schema fields when returning a
completed plan.

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
After any correction rerenders `summary.md`, read that current artifact and
replace the proposal content with those exact bytes. Never retain an earlier
summary in the proposal after the canonical summary changes.
For every Audit correction, change only the affected member analysis or
`appendix-links.json`, then rerun the appropriate `render` command. Never write
`main.md`, `full.md`, `summary.md` or an appendix file with
`consumer_artifact_write`; read the exact rendered bytes and submit those same
bytes to the document tools. Retain the resulting write receipts after
each correction.
Propose that body and the roster's
conversation ID through the existing `dingtalk-chat`
message action. Publication and sending are normal controlled actions reviewed
by Audit; keep document and message receipts in the task result.

This scheduled report has no triggering message to reply to. Use one pending
`send_group_message` proposal addressed to the verified roster's conversation
ID. Use `reply_to_message` only when the trigger is an actual message and
`read_scheduled_context` contains its non-empty `message_id`. Never infer or
invent a message ID for a scheduled run.

Report the document URL, coverage, unresolved evidence, appendix location and
actual send result. A local rendered file is a draft; provider write receipts
establish publication, while message delivery uses its own execution receipt.
