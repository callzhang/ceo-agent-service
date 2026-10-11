# Generic Message Audience And Split Delivery

Status: approved by Derek with the no-self-delivery constraint below; not
installed or business recovery proof.

## Approved Business Outcome

Message routing considers the actual content, current discussion, complete group
membership, verified identities and current responsibilities/titles. This is a
general messaging policy, not a meeting-only or financing-only exception.
Titles provide responsibility evidence; they do not automatically authorize
disclosure. A direct message is not inherently safe.

Split-message private recipients must be the verified relevant counterpart,
not the principal. Do not send the sensitive section to Derek himself, use a
self-chat as a fallback, or add a self-copy merely to complete a split plan.
Compare stable recipient identity with the configured principal identity,
including verified aliases; matching display names alone are insufficient.
If the appropriate counterpart cannot be established, retain the actual gap
instead of replacing that recipient with the principal. This constraint is for
audience-specific split delivery, not a ban on separately requested reports or
notifications addressed to Derek.

## Options And Decision

1. Extend the common Consumer preparation instructions and Audit publication
   rules, using the existing multi-action proposal. Recommended: preserves one
   authorization/execution path and allows contextual business judgment.
2. Create a separate sensitive-message routing subsystem. Rejected: duplicates
   candidate review, receipts and recovery without a new execution requirement.
3. Use keyword or title-based routing rules. Rejected: content, audience and
   purpose cannot be established from static words or title categories.

## Preparation And Review

Consumer reads the relevant discussion and complete membership, resolves stable
member identities and current responsibilities, and connects each proposed
content section to its intended recipients and business purpose. It records
evidence references and genuine gaps, without copying private source material
into repository fixtures or logs.

If all content fits the same verified audience, propose one message. If content
sections require different audiences, propose exact group and direct-message
actions in the same ConsumerProposal.actions collection. Do not mechanically
split every message. Every direct recipient must be independently verified;
missing evidence cannot justify a guessed recipient or replacement group.

Audit checks the complete plan: exact text, target, conversation context,
responsibility evidence, disclosure scope and remaining gaps. It returns a
candidate needing correction to Consumer; it does not rewrite or execute it.
An unresolved material disclosure boundary uses the existing current-instance
human decision contract, not a blanket trusted-recipient list.

## Execution And Recovery

Use existing ProposedAction identities, approved candidate digests, System
execution records and provider readbacks. Preserve the identity of the same
external result across revision/recovery. A successful group action does not
prove a direct action succeeded. Before recovery, reconcile each exact action;
never resend a completed action to finish another one.

Implementation must first verify the existing executor's multi-action recovery
contract. Any discovered receipt/retry defect requires a failing regression and
a separately scoped root-cause repair; this design does not assume that contract
is already proven merely because the proposal supports multiple actions.

Historical provider_risk_rejected candidates remain non-replayable. Splitting
does not authorize distributing the same refused content through another tool
or recipient. A materially safer new plan follows the formal new submission,
Audit and System path with fresh audience evidence.

## Scope

Update app/consumer_agent.py common preparation instructions and the tracked
app/defaults/audit_rules.md publication rules. Document the common policy in
runtime documentation. Reuse existing agent contracts; do not add a split queue,
change OA policy, weaken risk guards, or manually edit production prompt files.
Meeting-specific discovery still must establish an actual target; this policy
does not turn transcript speakers into a complete meeting/group roster.

## Acceptance

Freeze cases, model and configuration for baseline/candidate native evaluation:

- Same audience and supported content: one appropriate message, no artificial DM.
- Mixed content and verified responsibilities: exact audience-specific actions.
- Relevant title but unsuitable actual discussion: no automatic disclosure.
- Unknown membership or direct identity: no guessed target.
- Private recipient not entitled to content: DM does not bypass review.
- Proposed split DM resolves to the principal or a verified principal alias:
  no self-delivery, no fallback self-copy, no fabricated substitute recipient.
- Historical harness refusal: no replay or indirect circumvention.
- Completed group action and unfinished DM: only the unfinished action resumes.

Contract tests separately cover unique identities, candidate binding, exact
targets, receipts and idempotent recovery. Native judgment is evaluated from
actual evidence and proposed bodies, not prompt-string matching. Publish only
after review, exact CI and formal quiet deployment; read back installed policy
assets and affected runtime behavior. Old business failures are not marked
resolved by publishing this policy.
