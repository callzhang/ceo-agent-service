# Confirmation retirement: fresh SPEC review

Status: **APPROVE the unchanged runtime proposal against the clarified approved design; cumulative fixture acceptance remains root-owned**. Reviewed the complete unselected candidate on `7111ea47546ac7cb242ff7267993868eacec6561`, against design cancellation at `docs/superpowers/specs/2026-10-11-database-redesign-design.md:108` and the retirement plan. No selected application, production, provider, network or native CLI result is claimed. This reviewer changed only this review and its claim row.

## Findings

1. **Resolved [P2, test-only]: recovery fixture claim order.** In the executor regression proposal, generated `tests/test_workbench_confirmation_retirement.py:85-89` creates three turns without distinct timestamps, then assumes UUID order matches creation order. Actual `claim_next_turn` orders by `created_at,id`. With equal native timestamps, either first or second assertion can fail before testing recovery. Independent cumulative and isolated executions both reproduced the failure (the isolated run was 5 passed/1 failed in1.13s). The final executor-test proposal now sets three distinct created_at values with the current native SET helper and retains all expired/live/stopped assertions. Final independent current-schema+six-test rerun passed109 in5.75s. Runtime ordering is unchanged. The initial failing copied regression preceded this correction; its exact earlier patch hash was not retained.
2. **Resolved initial404 assumption at the specification boundary; integrated test fixture update remains with its owner.** Removed endpoints are absent from registered Workbench routes and OpenAPI. An isolated FastAPI app returns ordinary404. The real `create_audit_app` retains its existing global GET/HEAD `/{spa_path:path}` route (`app/audit_web.py:11348-11353`), so a POST to the removed path yields ordinary405. The integrated test at generated `tests/test_workbench_api.py:734-744` reproduced405. Root clarified that approved design108 requires deleting the old API, while preserving existing global fallback behavior; The plan and announced behavior-document proposal now describe standalone404/integrated405 accurately. The final integrated assertion is being supplied by the separate API fixture proposal. No compatibility endpoint, shim or general fallback change is justified.

The full cumulative run also found ten stale Task20 physical fixture failures in the preexisting API test module (old attachment/artifact tables, native INSERT helper and direct old Tasks columns). These are acceptance-fixture gaps, not evidence of runtime regressions. The API fixture owner is preparing a separate conversion preserving business assertions. Full cumulative green evidence must follow those fixture changes.

## Source and contract review

The ordered Store/schema, executor, surface and release patches apply cleanly with `--whitespace=error-all` in an owned temporary code copy. Store creation/recognition/manifest no longer accepts the retired table, ordinary queries no longer join it, the timeline is exactly `(task,turns,events,attachments,artifacts,page)`, and only confirmation statistics/cursors/resources disappear. The current Tasks20 CHECK and active-turn index drop only `waiting_confirmation`; current runtime event/model writers drop `confirmation_required`. Historical `task_storage` and `workbench_storage` source factories remain unchanged.

The executor retires the proposer/reviewed-write/confirmation-lease branches and retains ordinary queue claims, runtime start/wait, two-worker reservation, heartbeat, stop, lease-loss, artifact, quiescence and shutdown paths. Native CLI approval configuration and shared service-runtime execution remain unchanged. Remaining `confirmation_required` occurrences elsewhere belong to agent/provider result handling rather than the canceled Workbench feature; removing them would exceed scope.

The pure release binds the original table, all indexes/triggers/FKs through exact declaration hash `e215afb029d2993691a8585b07a614667aca8cddedd8dd21b9b0a3f3335d982d`. Source capture checks table rows, waiting turns and legacy events before Tasks20 DDL. The paired source recheck repeats that check inside `BEGIN IMMEDIATE`. Forward retires the empty table after Tasks20 conversion; inverse recreates the empty source declaration before Tasks20 restoration. Helpers require the caller FK-off transaction and do not commit or persist business rows/counters. The changed target declaration hash is `1afb0380b4280bc061eea303bfbfc43262c0768aad47ead8f5e2d60dbc2da9ff`, matching the candidate's Tasks20 status/index change. No additional runtime policy is introduced.

The announced behavior-doc proposal (`d765761b70bc8782a408c29111eb6fde97b7d88ec921cd1b49fe2be2d690b979`) accurately removes stale confirmation-auxiliary and waiting-cache descriptions, documents the paired empty declaration inverse and unchanged native approval, and describes54 tables as an intermediate selected-development result. It is suitable for the eventual selection commit, not a claim that selection already happened.

## Independent evidence

- Current pure release canonical: **14 passed in0.19s**, including nonempty row/waiting/event refusal, late row recheck, transaction ownership, rollback, exact inverse, ordinary event preservation, TEMP-shadow refusal and metadata rejection.
- Current-schema test proposal plus standalone retirement API and shared service runtime: **111 passed in4.70s** (103+6+2). Two initial collection attempts lacked source metadata in this reviewer's minimal copy; copying the existing referenced fixture files resolved the harness issue without application/test changes.
- Final corrected executor-test proposal plus current-schema proposal: **109 passed in5.75s** (103+6); claim-order finding is resolved.
- Complete initial cumulative test set: **136 passed/12 failed in112.72s**. Failures are exactly the new recovery order fixture, integrated404 assumption and ten old physical API fixtures described above. The runtime/executor/native-event suites otherwise passed in this cumulative set. This was the earlier copied test snapshot, before the six-test correction and clarified integrated response contract; it is not green full acceptance.
- Reviewer's owned tree `/tmp/confirmation-spec-review-228m5wc3` was removed after all bounded review runs; absence verified. No whole repository suite, production database, provider, real native CLI or network call ran.

## Reviewed artifact hashes

| Artifact suffix after `2026-10-16-confirmation-` | SHA256 |
| --- | --- |
| store-schema-proposal.patch | a52dad8ca09acf72dc37c012c2959241d289d799e0d1c4609a5083a3a63f45c4 |
| executor-proposal.patch | 294fc6f7ac837a0d064c3af39e8af7e6ea6e6c3d12c132bf37fce262610cee47 |
| surface-proposal.patch | 0d437d6586e5f35d07cdf3a5c4bd98845885e29c0aaa884987fd4292799d71c6 |
| release-proposal.patch | 25ba3d7e84828be021a498332606d6ed9579ad8116725cf205ba0ba5a9a0047b |
| executor-test-proposal.patch | ad5e2e00f383ce3313e2a2c04e9439f75f7a7678c0d01908c14b90beb5164e7e |
| executor-existing-tests-proposal.patch | 20a0d49353d54180d5ad0387539dde33074fee3259f706a43acabb560d493710 |
| executor-task20-fixture-proposal.patch (native-events file only) | ed9dafe6a0c5594a4e32f0c3b24a8a0653c7bc68bb35075f29a069acc84bb69f |
| surface-proposal-tests.patch | e83cfac91542872f5b6ef2e3be2952fac74594990fea1fa0c7331878e2ee7125 |
| current-schema-tests-proposal.patch | fa58c10dc7b300d72ff01aa13bc26cd8d6e614332106cb54bf46c8fec6f681fb |
| release-tests-canonical.py | 1c7dce5fbc48a69c660e1bfb51c9bf0891df90d37e4f849c454c62ec6c7b21de |

The in-progress API fixture artifact was not accepted or hash-pinned by this review; its final owner-announced version and cumulative rerun remain open. No application defect remains from this SPEC review.

The root cumulative database-copy proof, current-fact inverse through the genuine predecessor, fresh QUALITY review, eventual behavior-document update, installed acceptance and publication remain separate evidence. This SPEC review does not establish those outcomes.


## Final API test delta review

**APPROVE the final post-surface API fixture delta**, SHA256 `a4cd6b3169fbf601a57fb8f2e0a2f47da711f9f4d204f8446788400a7b7cbc61`. This supersedes the earlier API-fixture-review boundary above; it does not extend runtime approval or claim a reviewer-run full API suite.

Independent application of the already reviewed surface test patch followed by this final delta produced API module hash `05fabadc828082bf81ae153e2e8b37d6b52e84dd7c44e397747e44a5677ff64b` from post-surface source hash `ddbb847f4fec262db346747884c59789a314e704ea0b0d49fabd5ed667acb26e`. Both patches applied with whitespace errors treated as failures. All51 top-level function names remain;41 function ASTs are exact. The ten changed bodies replace obsolete physical fixture operations with source-shaped current Tasks8 INSERT/SET parts, typed Task12 attachment/artifact insertion and the current attachment projection. Fixture IDs still use the original physical-parent lookup; all diagnostic strings, file paths, timestamp values and business expectations remain.

The module has207 assertions before and208 after. Only two functions change assertion ASTs: the attachment-count query now uses the current logical projection with the same expected count1; the removed-endpoint test adopts the reviewed integrated405/`Method Not Allowed` response and adds OpenAPI route absence. All other existing assertions are AST-exact. In particular, `test_artifact_download_regression_detects_missing_stream_close` is byte-for-byte source-exact, including its leaking-chunks replacement and required `original artifact descriptor was not closed` failure match. Descriptor lifetime assertions in the path-swap test remain exact; only its artifact seed changes.

Pinned Python3.13 compilation and Ruff passed. No full API rerun was warranted for this bounded source/body audit; the owner's announced52-pass/160.99s execution is owner evidence, not an independent reviewer run. The temporary single-file proof tree `/tmp/confirmation-api-spec-delta-rlu31okr` was removed; absence verified. Application/test working-tree files and index remained untouched.

The final Store test proposal hash `e7bb38f61e7ee0285b256ceb2df44dadf002a249b9727667a87eb43959870dda` and finite boundary were read: cancellation-only cases/helpers retire, ordinary typed fixtures remain, and the owner reports47 passing cases with3 unchanged unsupported-source hybrids. Those3 cases are explicitly unresolved; no full Store green result is claimed here. Root owns final Store acceptance. The copy-checker delta correctly requires the retired table absent and changes the existing ordinary-table reduction from61 to62; this is a declaration assertion, not copied-database execution evidence.
