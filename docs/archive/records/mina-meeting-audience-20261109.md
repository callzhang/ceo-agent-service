# Mina meeting distribution investigation — 2026-11-09

Status: Derek selected retaining business-group distribution and correcting wrong-group
ownership judgment. Focused tests and paired native evaluation complete; release pending.
No historical meeting delivery has been replayed.

## Expected and observed

Mina reported that an HR weekly summary reached a recruiting-only management
group and said it belonged in the HR department group. She proposed requiring
an exact attendee/group roster match, using individual delivery otherwise.
These are distinct requirements when the available meeting roster is incomplete.

The final planner and independent Audit trajectories were read through their
persisted RuntimeAttempt API entries, with `has_more=false`. Both explicitly
recognized that a known meeting participant was absent from the selected group.
Audit still approved because the current meeting-specific rule allows ordinary
business content to a related work-line group despite incomplete attendee
matching. The provider delivery was subsequently recorded as sent.

This was not a missing department-group search result. The original planner
input already contained that department group (rank 11) and the selected
recruiting group (rank 149) among 407 candidates. The input occupied 651,903
UTF-8 bytes, including 430,664 bytes of candidate evidence. The carried-forward
selected-group rationale contained a disproven attendee-match claim; the final
revision corrected the count but retained the target. These observations are
facts; whether input volume materially caused the selection is unproven.

Current read-only DingTalk verification confirmed both distinct groups and
complete human rosters: eight people in the department group, three in the
recruiting group. The recording-derived attendee list contains three known
people and is marked incomplete. Thus exact roster equality cannot currently
be established even for the department group; it would require individual
delivery under Mina's proposed rule. Bot identities must not be conflated with
human attendees.

## Scope decision and implementation

1. Adopt exact human-roster equality for meeting group delivery. Replace the
   existing incomplete-roster exception; preserve the existing related-attendee
   direct-message path and sensitive-content rules. Missing or unequal rosters
   cannot qualify a group.
2. Retain business-group delivery and fix the business-ownership judgment:
   distinguish a department's weekly work from a recruiting-only discussion;
   topic overlap and historical selection do not establish ownership of the
   complete summary. Correct department groups may include non-attendees.

Any change needs a frozen comparison containing the reported wrong-group case,
an exact-match group, an otherwise-correct larger department group, incomplete
attendee identities, and the existing personal/direct/sensitive cases. Validate
planner and independent Audit behavior without external sends. Follow the
standard gate and deployment path, verify runtime readback, then reply to Mina
with the actual adopted rule and verified release status. Do not replay an
already delivered meeting summary or claim a natural post-release result from
an offline evaluation.

Derek explicitly chose option 2 in this chat. The implementation replaces the
loose meeting-specific same-project acceptance sentence with a shared criterion
for the planner and independent Audit: actual group purpose and concrete work
must encompass the whole ordinary summary; compare candidates and recheck prior
target claims. Larger correct department groups and incomplete rosters remain
valid. No runtime gate, keyword heuristic, schema, retry, route, disclosure or
sensitive-message rule is added. Installed Skills are unchanged.

Regression: `python -m pytest -q tests/test_meeting_alignment_agent.py -k
compare_group_purpose` failed before the change (1 failed, 108 deselected).
Focused verification: meeting agent/models/delivery/audience evaluation tests
passed (263 passed in 4.51s). Native frozen comparison is recorded separately
under `evals/meeting_group_scope/`. No post-release natural outcome is claimed.

## Fixed comparison and limitations

`evals/meeting_group_scope/v1.json` freezes six synthetic cases and independent
meeting titles. Baseline and candidate use native Claude `sonnet`, high effort,
tools disabled and the actual Meeting native output schemas. Both achieved
12/12 target/outcome/schema checks. Manual review of all outputs found all
substantive source conclusions preserved, and both Audit returns named the
better department group with actionable revisions. This sample does not
demonstrate superiority; it verifies no selection regression on these cases.
Both versions sometimes make unsupported statements about absent owners,
deadlines or disagreements from a sparse synthetic summary; this is a recorded
content limitation, not repaired or counted as an improvement here.

An early harness run was interrupted for a validator/title defect and is not
acceptance evidence. A second complete-calendar frozen rationale was corrected;
only the two affected native Audit invocations were rerun. Twenty-two unchanged
invocations were reused only by identical prompt SHA-256 and model settings.
The corrected canonical artifact records both prompt and harness hashes.
The optional complete real-source model replay was rejected by automatic
approval review for sensitive-data egress and was not executed. The diagnosis
uses the already recorded original planner/Audit trajectories, not a new replay.

Verification also passed: 249 workflow/source/documentation tests; 114 agent and
audience tests after the evaluator regression; 113 deployment-drain/delivery/
audience tests. Ruff and diff checks pass. GitHub full-suite failures in
`test_action_receipt_callers` and `test_rule_question_retirement` reproduce
locally in files and owning implementation unchanged from baseline; unrelated
frontend CSS tests also fail. No gate or assertions are relaxed for this PR.
