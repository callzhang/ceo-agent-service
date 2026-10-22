# Unselected feedback retirement test proposal

The patch preserves the four setup purposes that used the retired direct resolver, replaces the obsolete direct DAO contract with the current batch projection/status contract, and explicitly retires four recurring startup self-heal tests. It adds one public-API cold-reopen control. No application code or test file in this worktree was changed. The companion patch is a proposal only.

The parent explicitly approved retiring the direct resolver and recurring startup repair, keeping normal create/claim/associate/evidence/resolve/reopen/upsert behavior, and leaving genuine historical source tests for separate source-fixture ownership. The prior evidence is recorded in `2026-10-16-task20-feedback-backfill-disposition-analysis.{md,json}`. This patch adds no application API, compatibility path or runtime gate.

## Exact dispositions

Original line numbers refer to the pinned before-file hashes in the JSON.

| Original owner | Disposition | Behavior retained |
| --- | --- | --- |
| `test_claim_rejects_unknown_or_resolved_keys_atomically`1802 | Replace resolved-key setup with the test-only current batch fixture | Unknown and resolved claim rejection, no invalid batch/unknown item, unaffected pending item, resolved item status. |
| `test_reopen_existing_batch_is_idempotent_after_source_resolution`1879 | Finish the already-created pending batch through its normal public lifecycle | Reopening the same normalized existing batch preserves identity/requested count after source resolution. This is now a fully resolved current batch; the obsolete pending-batch/direct-resolver split is intentionally retired. |
| `test_resolved_event_projection_and_status_transition_are_consistent`1903 | Replace with `test_batch_resolved_event_projection_and_status_transition_are_consistent` | Event/item/round/batch resolved status, exact current-round pointer, matching nonempty resolution timestamps, status-patch rejection, no new batch for resolved feedback. |
| `test_user_feedback_nav_badge_shows_pending_count`3449 | Replace resolution setup; retain both original HTML assertions | Pending badge appears and disappears after resolution. |
| `test_console_feedback_pending_badge_is_global_when_filtered_to_resolved`1173 | Replace resolution setup; retain original request and response assertions | Resolved filter total remains1 while global pending badge remains1. |

The obsolete DAO test's raw deletion of the processing item after direct resolution is explicitly removed. The replacement still verifies rejection of a new batch for resolved feedback, using the complete current projection rather than a deliberately missing projection left by the canceled direct-resolution path. This proposal does not claim new coverage of an event whose processing item was manually deleted. Other corruption/lineage tests remain untouched and require their own typed fixture conversion where they use retired native names.

The four recurring current-schema startup assertions are explicitly removed:

- `test_feedback_round_backfill_self_heals_current_schema_and_interruption`1264.
- `test_feedback_round_reconciliation_requires_pointer_ownership_and_batch`1441.
- `test_feedback_round_reconciliation_clears_ambiguous_matching_history`1630.
- `test_round_reconciliation_clears_stale_non_latest_matching_pointer`3218.

Their synthetic projection/table/pointer corruption and expected automatic reconstruction or clearing describe the behavior the parent explicitly canceled. They are not translated into a hidden repair fixture. The replacement `test_feedback_cold_reopen_preserves_current_batch_rounds_and_sources` creates pending, processing and resolved states through current public methods, validates pending-without-round and exact version2 processing/resolved pointers, then checks event/item/round/transition/batch DTO equality across two uncached constructor opens. This proves normal reopen preserves current facts; it does not claim arbitrary raw corruption is repaired.

The genuine historical tests `test_feedback_round_backfill_preserves_legacy_receipts_and_source`1065 and `test_migrated_v1_processing_round_resolves_idempotently_and_reopens`4847 are AST-identical. Their old native schema deletion/backfill and migrated-version1 lifecycle roles remain for a separate genuine source fixture; they were not run against the selected Tasks20 constructor in this proposal. Historical application/source factories remain unchanged.

## Fixture and verification

The new `tests/feedback_processing_fixtures.py` contains one shared test-only helper. It accepts an existing event, then uses only current public batch creation, claim, association, evidence patch and resolution. It never upserts/replaces the source event. Its typed `ResolutionEvidence` contains explicitly synthetic receipt facts; it executes no Agent, provider, commit lookup, tests, health request, restart or external send. Caller-supplied batch IDs permit the existing-batch idempotency test to use its original batch.

The builder copied application/tests/config into one task-owned temporary directory, sequentially applied the exact nine caller proposals, Store constructor proposal and four-file coupled proposal, then applied this test patch. All eleven prerequisite hashes, all four before/after file hashes, exact changed owner inventory and patch SHA256 are in the JSON. The patch applied, reversed to exact original bytes/absence, reapplied, compiled and passed Ruff. Every other top-level test function/class AST in the three existing test files stayed exact; imports are the only changes outside the listed owners. Both old direct-resolution endpoint409 tests remain AST-identical.

Only these eight named tests ran in the synthetic copied target: the four setup-purpose tests, replacement batch projection test, cold-reopen control, and the two existing409 route refusal tests. Result: **8 passed, 1 existing Starlette deprecation warning, 11.31s**. No whole feedback file/full suite, actual database, production copy, native CLI, provider, main merge, push or deployment was used. The task-owned temporary code/databases were removed and absence verified.

The JSON inventories **61 unchanged native-literal fixture owners /183 literal sites** in `test_feedback_processing.py`, excluding the explicitly replaced/retired owners. This is a lexical inventory of native feedback names, including source schema tests and helper fixtures, not a report that61 tests failed. The proposal does not convert those unrelated fixtures or claim the entire file now passes. Existing source guard-replacement/schema-upgrade tests likewise need their genuine historical/current fixture role resolved separately. Broad conversion would exceed this bounded retirement proposal.

Patch SHA256: `3ffb68481e6bb6a23459e8dc32515bec9058e4f2d85d8b74b1ecbdc742344e6f`.

These four proposal artifacts and this agent's claim row are committed as documentation only under the parent's exclusive index authorization. The parent retains application/test selection ownership.
