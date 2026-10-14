# Audit revision context, Attempt15391

## Evidence and root cause

The production Attempt15391 ended failed/audit_revision_exhausted with four
adopted candidate revisions. The immutable adopted revision2 Audit requested
“职责确认前沿用现有参会要求”, host-confirmed proxies and no final decision when
substitution is unavailable. Consumer revision3 included those changes. Its
Audit then returned those same additions for lacking current source support.
Earlier returns also changed a proposed message to no_action and then back to a
message after a newer request. These are distinct observations, not proof that
every return was wrong.

The native sessions referenced by these runs are unavailable. Historical tool
calls and exact complete native inputs cannot be reconstructed. Diagnosis uses
the persisted adopted candidates/reviews and the verified orchestrator input
path: AuditTurnContext previously contained only the current candidate/context,
with no preceding review, while each Consumer received that review's feedback.
The tool-unavailable and malformed-feedback failures remain separate historical
technical failures; this change does not claim to repair those missing records
or prove their cause.

## Repair

The orchestrator uses the validated current Consumer parent's parent Audit run
to read the adopted review. It passes only a return/reject as revision context.
Rendering includes exact revision/digest/feedback before current evidence and
candidate, explicitly distinguishing historical instruction from facts and
authorization. It omits previous summary and quality scores. No new storage,
retrieval fallback, approval condition, retry budget or lifecycle state is added.
No production message or Attempt is replayed or edited by this repair.

## Verification

- Three new regression cases failed before implementation because revision
  context was absent. Four final focused regression cases pass: return/reject
  lineage, cold reconstruction with technical retry, and exclusion of a prior
  approved stage. The five relevant test files pass108 cases. Tests use the
  canonical current seed User template; an obsolete untracked development
  template initially caused three unrelated failures, which were not patched.
- Changed Python files pass Ruff and the diff passes whitespace validation.
- The versioned three-case synthetic native diagnostic uses gpt-5.6-luna, low
  effort, exact current Developer instructions and wire parser, identical facts
  and action contracts, with all tools disabled. It is not a replay of the lost
  historical native sessions. Baseline omits revision context; candidate carries
  it. All candidate results bind the current digest/revision.
- native.v1 and native.v2 preserve an undesirable approval of the case containing
  an unsupported existing-attendance assertion after complete old review input
  was appended. Merely adding a disclaimer did not resolve it. Native.v1's
  control scoring also initially accepted only return, although reject is a
  valid rejection; v1.json now allows both for unsuitable candidates.
- native.v3 uses instruction-only revision context before current evidence.
  Candidate passes3/3: it returns the unsupported current-rule wording and
  explicitly identifies the preceding instruction as not being source evidence;
  it approves explicitly labeled requested advice; it rejects fabricated enacted
  policy and personnel consequences. This run's baseline passes2/3. These are
  small diagnostic observations with model variability, not a measured general
  production error-rate reduction.

## Release boundary

Implementation and the synthetic diagnostic are validated locally. Production
deployment and operational readback must be recorded separately. Attempt15391
remains historical failed work until a genuinely authorized new business run
finishes; deploying this change does not mean its old reply was sent.
