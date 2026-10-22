# Provider State: retain four native fields without repeated keyed payload

## Evidence and scope

Approved database redesign maps the existing Provider observation/generation facts to provider_state. The current unpublished31-table candidate passes full-copy schema inverse/reforward, but active object bytes grow500.2→565.0MB. Exact per-object receipt2026-10-17-current31-allocation.json attributes approximately9.1MB net growth to Provider. A controlled RAM experiment on72241 observations/55 generations, identical three indexes, confirms all nine observation values/types and34.2→27.2MB allocation when the four existing payload fields become writable native columns. This is not an implemented or deployed correction.

No new business field, retention rule, provider send, new policy or compatibility layer is required. Table count stays31 at this stage. Final10cores+ledger remains separate work. Do not start implementation before the current Errors public parity verification completes independent SPEC then QUALITY.

## Exact field/behavior boundary

Keep provider_state id/record_type/account_id/stable_message_identity/state/provider_folder_id/observed_at. Replace generated provider_folder_name TEXT, category_key TEXT, important INTEGER and important_signals_json TEXT with writable columns of those same declared affinities; remove redundant payload_json. Keep the same source observation/generation identities and all three index DDL strings. Generation rows must continue to have NULL observation-only values. Observation NULL/type/JSON/important/native state constraints and account/time/identity checks remain equivalent; introduce no additional validation policy.

Keep all existing public DAO signatures, projections, statement-level updates, rowcount/no-op semantics, transactions and errors to the degree required by the native source contract. Replace payload manipulation with direct native field assignments. Maintain actual/fresh source declaration fingerprints, exact captured inverse declarations and current-fact rollback, including new post-migration writes. Replace only the unpublished candidate target fingerprint; do not add a compatibility branch for the obsolete unshipped JSON target. Native input/storage behavior, including BLOB/REAL/NULL and SQLite affinity, must be checked with original source oracles rather than assumed.

## Sequential implementation/verification units

1. Test first: prove current schema still stores the keyed payload and fail desired four-writable-field/no-payload shape. Extend focused original-source/type/constraint/DML/current-write inverse cases. A capacity regression should compare reproducible source-shaped fixtures with the same indexes and real values; do not assert a hard-coded one-off production size as a functional test.
2. Scoped implementation: app/provider_state_definitions.py, app/provider_state_storage.py, app/provider_state_schema_release.py. Adjust only directly affected Provider tests and the describing architecture/runtime paragraphs. Do not modify unrelated app callers if public helper/projection contracts suffice; stop and report any concrete new scope. No legacy fallback, extra table, source body copy or retention deletion.
3. Freeze files. Fresh independent SPEC then fresh QUALITY must inspect real source actual/fresh constraints, methods/query plans/negative controls, current facts inverse/reforward, no-op/bulk timestamp/rowcount, and allocation evidence. Run only Provider-focused test files and necessary selected paired constructor cases in managed temporary roots.
4. Root copy validation: read-only online production copy, current paired forward and full schema inverse/reforward with actual production predecessor cold reads/writes. Include both Provider families' all fields/types alongside already accepted five-family proof where practical. Capture per-object before/after allocation; preserve original FK finding and quick_check, same-index shape and exact source schemas. No real sends, production mutation, vacuum/checkpoint or deployment. Remove managed copies/code archives at scope exit; retain concise metadata only.

## Completion boundary

A tested layout correction closes only Provider representation overhead. It does not resolve all Actions/Tasks growth, remaining22 native names, missing emails/provenance,source retirement proof/read-path gaps (158 lack native-file proof; older Provider130 exact contents still match current rows, with28 lacking either recorded method), final11/full-service/native-CLI/UI acceptance or release. Report measured savings only at the validated level.
