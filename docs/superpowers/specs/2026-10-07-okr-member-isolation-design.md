# Weekly OKR Member Failure Isolation

Status: approved by Derek; not installed or report delivery proof.

## Approved Business Outcome And Confirmed Defects

An individual member's missing goals, source failure or analysis failure must
not withhold the whole report. Every roster member remains visible, without
substituting a previous quarter or treating unavailable data as a zero score.

app/weekly_okr_report.py currently aborts collection when one fetch/validation
raises. Its analysis stage collects individual errors but subsequently raises
instead of rendering those gaps. Both stages need repair; prompt-only changes
cannot resolve this behavior.

## Options And Decision

1. Persist explicit per-member collection and analysis outcomes, render a full
   roster report with honest gaps. Recommended: preserves usable work and
   distinguishes business absence from technical failure.
2. Omit unavailable people and publish the remainder. Rejected: hides coverage.
3. Substitute empty scored reviews or zero values. Rejected: fabricates results.

## Source And Analysis Contract

Each member has a stable user identity and one explicit collection outcome:
available, goals_not_established, or source_failed. Only an authoritative
current-period absence can mean goals_not_established. Authentication,
permissions, transport and malformed payload failures retain their original
technical classification and evidence; they do not mean missing goals.

Continue other member fetches after an individual failure. Keep valid current
period payloads intact. Analyze only available payloads, preserving the
existing scoring and KR evidence rules. A completed member analysis has a
scored review; a terminal analysis failure has a separate unscored gap record.
An analysis still running is not a terminal gap: preserve existing leases and
resume semantics instead of prematurely publishing around live work.

Do not synthesize ManagerReportAnalysis with fake KR/culture/leadership scores
for a failed member. Extend the report-level typed gap contract and source
outcomes; require exact, disjoint coverage of the roster by scored reviews and
unscored gaps. Key coverage by stable user identity, not name alone. Keep
successful member validation strict. Persist outcomes so retry/restart does
not discard completed member work or lose failure provenance.

## Report And Publication

Render a section for every member, showing period, source outcome, analysis
outcome and the exact missing evidence or failed stage. Scores for unavailable
members are absent, not zero. Company statistics explicitly state their
available denominator and exclude unscorable members from score averages.

If nobody has current-quarter goals but live access and the roster are verified,
publish a truthful coverage/gap report with no invented scores. If shared
authentication, roster discovery or a common prerequisite is unavailable,
retain a technical failure; do not label everyone as having no goals. Use
structured provider errors and verified readiness to identify shared failures,
not message keywords or a percentage threshold.

Only verified document and group-summary publication advance publication
success. Published coverage gaps remain visible and must not be described as
all members successfully scored. Preserve existing report identity and
external receipt reconciliation before any recovery/publication attempt.

## Scope And Acceptance

Change weekly collection, aggregate model/schema, coverage validation, rendering
and their tests in app/weekly_okr_report.py. Do not change scoring formulas,
roster membership, OKR goals, unrelated archive behavior or delivery ownership.

Failing regressions must demonstrate:

- First member lacks the current period; later members are still fetched/scored.
- A member source or terminal analysis failure does not erase successful work.
- All missing goals produce an explicit full-roster coverage report, no zeros.
- Shared auth failure stays a technical failure, not missing goals.
- Duplicate/missing identities, overlap and incomplete successful KR reviews fail.
- Active analysis is resumed rather than reclassified as a completed gap.
- Rendered counts/averages use real denominators; all roster sections are present.
- Restart and partial publication reconcile receipts without duplicate delivery.

Run fixed baseline/candidate report cases under identical model/configuration,
independent review, exact CI and formal quiet deployment. For the blocked
production weekly report, refresh the full roster and live period data plus
existing publication receipts before formal recovery. No historical failure is
closed solely by schema tests, deployment or a successful API response.
