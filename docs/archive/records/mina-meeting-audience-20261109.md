# Mina meeting distribution investigation — 2026-11-09

Status: Derek selected retaining business-group distribution and correcting wrong-group
ownership judgment. Focused tests pass; paired native evaluation and release pending.
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
