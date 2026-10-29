# Audit result binding proposal — approval required, not implemented

## Confirmed failure

Production task404975 has six failed Audit attempts across two native sessions, three attempts each. Every service input contains the same single64-character expected candidate_digest and correct revision. Original native model outputs first diverge at character53: lengths63/64/64 then63/64/63. Existing wire shape rejects three malformed63-character values; exact candidate-binding validation rejects the three64-character wrong values. All revisions match. Parsing the saved native ranges reproduces this distinction. The error is already in the model's original JSON; input assembly and parsing do not corrupt the digest. The model's internal character-choice cause is not established.

Metadata-only original ranges, input/output/source hashes, tool call/return hashes and parser outcomes are in evals/trajectory_optimization/audit_digest_failure.live_metadata.v1.json. Private bodies remain in original native records. No retries, provider calls, service edits, historical mutation or replay are performed by this investigation.

## Proposed independent change

Treat candidate_digest and proposal_revision as service-owned execution bindings rather than model-authored review content. The Audit model returns the review judgment/evidence/feedback/error/quality fields. The existing Agent run/context, already verified against the adopted candidate before invocation, supplies the exact digest and revision to the persisted typed result. The public/domain AuditAgentResult remains bound to the same exact adopted candidate. Every subsequent approval/execution check remains. The model-returned wire schema no longer asks the model to reproduce opaque binding bytes. Unexpected old binding fields in the new model output are rejected by the new strict wire contract, without legacy fallback or silently overwriting supplied wrong values.

Scope explicitly excludes any new approval rule, permission, effect reconciliation, route/model/fallback policy, extra retry budget, automatic replay or historical migration. Do not change whether an Audit judgment approves/returns/rejects/fails or which evidence is sufficient. This is an output-binding ownership change, not proof of business correctness.

## Alternatives and tradeoffs

1. Recommended: service-owned bindings as above. Removes redundant model-generated metadata and its copy errors on every route. Requires an explicitly scoped wire/runtime conversion change; stale/wrong-context association must be covered by existing run/candidate linkage tests.
2. Keep model echo and constrain it with per-invocation native schema constants. Preserves the current echo contract, but requires task-specific schema delivery on every configured runtime; static Codex schema files currently cannot encode different per-run values without new file lifecycle plumbing. Existing plan requires native simple plumbing, so do not introduce that mechanism merely for this case.
3. Keep current contract. No implementation; exact mismatches still fail. Do not add repeated hash-copy instructions or character repair rules, or weaken comparisons.

## Concrete implementation and acceptance, if approved

- Change Audit wire model/conversion and its actual callers as one separately scoped feature. Keep domain result and existing adopted-candidate/current-run validation. Generate its native schema from that exact model; Settings shows actual service input/schema scope accurately. Consumer wire stays unchanged.
- Regressions: valid judgment acquires exact current run bindings; mismatched context/adopted candidate remains rejected before native invocation; unexpected model binding fields fail strict schema; stale revision/run association cannot bind a review to another candidate; failed/recovered/cold/resumed results preserve original run identity and do not trigger actions.
- Fixed anonymous before/after trials use the same candidate, facts, model and prior context; inspect all native inputs/tools/returns/output and final persisted binding. Existing live failure remains untouched. Require business judgment/evidence checks as well as structured success.
- Focused caller/schema/preview/recovery tests and independent readonly review. Publish only this independent change through formal deployment/readback after acceptance; no held Task/provider drafts.

Approval boundary: repo AGENTS.md says audit/review/authorization or safety-policy logic must be proposed and confirmed as a separate explicitly scoped change, with separate code/tests/docs/commit. Conservatively treat moving Audit binding ownership as such a scoped change. Nothing in this proposal is implemented yet.
