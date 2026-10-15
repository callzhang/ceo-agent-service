# Meeting personnel disclosure and member precision validation

Derek explicitly authorized this isolated policy change on 2026-10-15: layoffs,
workforce reductions and personnel optimization are sensitive even without
individual names; complete group membership and actual disclosure permissions
are required; compare matched participants / full group members (3/N), never
attendee recall.

## Implementation

The shared Consumer/Audit message audience contract, meeting prompt and bundled
meeting/personnel Skills apply the content-level disclosure boundary. Actual
authorization must cover every current member, including nonparticipants;
business relevance, discussion history, roles and matching ratios do not grant
permission. Classification remains Agent judgment over evidence, not a runtime
keyword filter or arbitrary percentage gate.

Discovery removes the old minimum-two/60%-attendee-recall cutoff. It retains
full member IDs/count and emits matched member count, member precision and
reference identity completeness. Precision breaks ties after discussion/topic
scores. Historical sends retain their count without a verified-audience label.
Missing participant IDs keep reference completeness false. The unused legacy
recall implementation was removed.

The Agent-authored message_title is checked for the actual audience and is used
for both rendered heading and both primary/private notification titles. Raw
source.title is no longer inserted automatically. Original titles remain source
metadata. Decisions without a reviewed title use the neutral 会议跟进.

## Deterministic verification

- Existing discovery/prompt/scorer baseline: 116 passed.
- Four initial member-precision regression cases failed on the original code:
  a 3/30 group appeared as attendee 3/3; a 2/2 group was discarded for failing
  attendee recall; empty group and incomplete-reference evidence were incorrect.
- The sensitive source-title and old-decision regressions both failed before
  the rendering change. A separate unresolved-identity completeness case also
  failed before its correction.
- Integrated group/meeting/audit/scorer run: 239 passed.
- After normal merge of concurrent remote main, model, delivery, prompt,
  managed-Skill, discovery, precision, audit and scorer files: 417 passed.
- Additional cross-version and split notification regressions passed. Ruff and
  git diff --check passed for the changed runtime/test files.

Independent read-only review identified the raw public and private notification
title paths and incomplete identity flag; these were corrected and regression
covered. Marker scoring alone cannot exclude paraphrased sensitive disclosure,
so complete native outputs and rendered messages require semantic inspection.

## Frozen synthetic native comparison

Manifest: evals/meeting_audience/v2.json. Baseline: 92420eb0. Candidate: 8d51e3bd.
Both run the same six cases, gpt-5.6-sol/high, timeout and tool-disabled native
CLI harness. The harness renders each decision using that revision's own
meeting_followup_message, so a sanitized model body cannot hide a raw sensitive
title inserted by the sending layer. All facts and identities are synthetic;
there are no provider reads, real sends or replayed meetings.

Native results: baseline 5/6, candidate 6/6. Complete artifact:
`evals/meeting_audience/v2.results.json` (SHA256
7849eff3902fd8e911001dddb4408f604c17d9b9ff30eb7ea2663279f03600ee).
The measured baseline failure is the raw W-20 source title reinserted by the
sending layer. Baseline also kept unnamed-plan details out of the supplied
synthetic public bodies; do not claim a measured body-classification improvement
from this small corpus.

The author and independent reviewer read all six complete candidate rendered
messages and private sections against the case facts, independently of marker
scoring. No unauthorized direct/indirect plan disclosure was found. W-19 has
3/30 precision, a checklist-only public message and authorized Morgan-only
personnel follow-up; W-20 has a neutral title, checklist-only body, no private
fallback and unresolved sensitive follow-up; W-21 remains deliverable to the
explicitly authorized full committee. Ordinary business coordination is retained.
These are synthetic semantic and renderer checks, not live ACL or provider-send
acceptance. The final merged source has byte-identical prompt, schema, group
policy and Skill files to candidate8d51e3bd; the additional private push-title
fix is covered separately by the integrated delivery regressions.

## Release and recovery boundary

The two production Skills are independently bound in runtime config18 and the
meeting schedule: meeting revision142 and personnel revision6. Changing the
repository copies alone does not update them. Exact reviewed revisions165/166
were created and hash-read back through the existing Settings API without
changing active bindings. Publication must update the existing runtime config
and scheduled task2 references with their normal parent/version checks, export
the exact revisions, perform the normal deploy, and read back actual loaded
hashes and the new process.

Old prepared content is immutable. If the neutral title changes an unconfirmed
old prepared body, existing body-consistency validation refuses it without a
send; re-authoring uses the existing decision flow. Exact confirmed receipts
still reconcile without replay. Cross-version tests use literal old-format
bytes, not the current renderer. Pre-release production inventory found zero
processing/ready_to_send meeting jobs; recheck before deployment.

Production deployment and final readback: pending.
