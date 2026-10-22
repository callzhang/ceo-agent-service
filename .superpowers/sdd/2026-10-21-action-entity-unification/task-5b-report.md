# Task 5B: populated forward migration report

**Result:** the populated Store19.12/Email53 forward translator and exact target declaration pass on one owned copy of the authoritative source. This is local forward evidence only. Task 5C still owns current-data inverse, exact old-code cold-open/reforward checks, and the agreed canonical-correction retention behavior; independent final review and release gates remain open.

## Source and owned copy

The read-only source was `/tmp/ceo-action-entity-validation.8rUAvK/source.sqlite3`, 496,410,624 bytes, mode `0444`, SHA-256 `799307664c58aeb2163eb44fa18a3261fadeb36fabf9e8534e905fdfe966f1a1`. Preflight observed marker `2026-10-19.12`, Email schema maximum 53, reply variant `fresh_memory_fk`, source schema SHA-256 `9d97aae7d76124ab74af9091cf82260ee24a30857c54dc7435e80ab1e898e007`, and one existing FK finding.

There was one successful forward publish commit on the owned, initially byte-identical copy `/tmp/ceo-action-entity-validation.8rUAvK/task5b-populated-forward-dz8st8a8/populated-forward.sqlite3`; this does not mean there was only one full-source attempt. Earlier diagnostic attempts on owned copies rolled back while resolving the source’s 57-versus-53 reply-layout boundary, a generated-column name mismatch, a dispatch-only prepared-child pointer, and the two real direct-retry parent FK references. Those attempts were not committed. The successful command called `prepare_action_entity_release(copy)` followed by `publish_action_entity_release(copy, contract)`. The release transaction’s exact target manifest, FK multiset, and `quick_check` guards all passed before its commit. Final marker is `2026-10-21.1`; final copy SHA-256 is `2235fddcca1ec5557edf11c961621dec1f19e46f95e63e0d0e20f5d195f26bdb`. The readonly source SHA-256 was rechecked after publish and remains unchanged.

An extra post-publish verifier initially compared source out-of-scope rows with every target row not in a retired family. That predicate also included newly-created common Action owners, so it stopped on a 15,263-row difference; the release was already committed and its own guards had passed. The verifier was corrected to compare only the exact 39 source-retained record types. That comparison covered 72,478 rows and all shared physical source columns. It found only two changed cells, both `legacy_key` on the two `email_direct_retry_requests` rows; each is the specifically approved composite parent reference rewrite. No other out-of-scope source cell changed. The newly-added `action_data` column is SQL NULL on all 72,478 unchanged non-Action rows.

## Populated conversion evidence

The source contained these covered parent-family counts: `external_action_results` 576; `candidate_action_attempts` 401; `outbound_postfixes` 1,487; `sent_replies` 2,508; `sent_reply_observers` 487; `email_actions` 11,387; and `email_action_attempts` 13,478. Prepared child facts included 868 provider receipts and 207 dispatch observations. `email_historical_operations` contained zero source rows.

The target contains 15,263 common Action owners, 17,833 Action Receipts, 487 Action Observers, and 31,399 typed identity-map rows. All seven covered source families and both prepared child types have zero remaining rows. Identity-map row counts are: candidate attempts 401; email attempts 13,478; email owners 11,387; external results 576; postfix parents 1,487; postfix receipt children 868; dispatch children 207; Sent 2,508; and observers 487. The 253 exact prepared-delivery-key candidate joins share a common Action owner; all candidate attempts, including the 43 without a canonical external-result owner, have their own mapped receipt/owner facts. Of 487 nonempty Sent external keys, 485 join their exact canonical owners and two remain independent orphan Actions. All 2,021 empty Sent keys remain separate owners. No grouping uses body, token, digest, or timestamp equality.

Every mapped source body/receipt comparison was exact by SHA-256, with no body content exported: external-result bodies 576/576; candidate result values and SQL-NULL distinctions 401/401; prepared provider result bodies 868/868; and Sent result values and SQL-NULL distinctions 2,508/2,508. Prepared parent bodies matched their mapped Action body hashes 1,487/1,487; Sent bodies matched their mapped Action body hashes 2,508/2,508. The source has 479 Sent `send_result_json` SQL-NULL references and 2,029 local result bodies; the target retains 479 SQL-NULL receipt bodies with 479 typed canonical receipt references. Candidate result JSON has 357 SQL-NULL rows, each retained as the exact canonical receipt reference. Receipt status, operation/target, result ID and source clocks are mapped in source receipts; observers retain their `(sent_reply_id, agent_run_id)` identity, task and creation time.

The release retained the original one-row FK finding multiset and passed `pragma quick_check=ok`. There are no target FK findings beyond the one inherited finding. The final schema exactly matches the current 507-object Store/Email manifest; its static rendering is `docs/action-entity-current-target.sql` and a focused test compares that file byte-for-byte with the manifest.

## Direct-retry FK exception

Two surviving `email_direct_retry_requests` rows referenced the retired `email_actions` parent through the existing generated composite FK. The target now generates parent type `action`, enforces `email_action_id -> actions.id`, and checks the parent’s `record_type='action'` on insert and composite-key update. The source parent `action_id` is not a separate stored control cell: the generated `email_action_id` projection decodes element 0 of each retry row’s composite `legacy_key` (`[source action_id, attempt_number]`). The migration resolves that source public parent key through the typed `email_actions` map and rewrites only composite-key element 0; element 1, payload, status, and clocks remain unchanged. The changed generated projection then resolves to the target Action physical ID. Both actual rows resolve to a common Action. No old public-key alias, new business field, or additional global index was added.

A fresh current `EmailStore` regression exercises the source-verified retry Store API from a real eligible failed trash Action and checks that the new request points to the same common Action ID. The archived predecessor API harness is not used for this assertion: its fixture creates Store13.5/Email51, which this explicit Task5B source release correctly rejects before reaching that API. That older fixture chain is not changed here.

## Verification and remaining gates

Passed focused checks:

- `pytest -q tests/test_action_entity_contract.py tests/test_action_entity_schema_release.py::test_current_release_ddl_matches_target_manifest` — 14 passed.
- Seven populated synthetic/current-API tests covering the converted families, prepared/Sent/candidate proven joins, ordered synthetic history-ID rewriting, conflict abort without mutation, direct-retry parent translation, and current Store API retry — 7 passed.
- `pytest -q tests/test_action_entity_callers.py::test_prepared_message_caller_uses_common_action_and_owned_receipts tests/test_action_entity_callers.py::test_retry_keeps_original_external_identity` — 2 passed. The former query now checks absence of the retired record type without selecting its retired `delivery_key` generated column.
- Scoped Ruff, `compileall`, and `git diff --check` — passed.

A broader `pytest -q tests/test_email_action_storage.py` probe returned 9 passed and one fixture failure: `test_email_generated_fields_are_null_for_older_actions_and_absent_email_fields` constructs the schema through `create_action_schema()` but then calls the compact-source `_kind` writer, which reports `table actions has no column named _kind`. No `action_storage.py` writer or test was changed for that mismatch; it is outside the forward-release/API tests above. The archived paired direct-retry test likewise remains outside this gate because its Store13.5/Email51 source marker is unsupported by this bounded 19.12/53 release.

Task5C remains responsible for actual current-data inverse, old-code cold-open/reforward acceptance, and source-faithful canonical correction/receipt retention. No provider/native CLI, production database, push, deploy, or cleanup of shared source/archive paths was performed. Keep the owned populated copy at the exact path above for Task5C handoff.


## Recorded TDD and reproducibility details

The populated external-result test was first run as a genuine RED before the translator was complete:

```sh
pytest -q tests/test_action_entity_schema_release.py::test_populated_external_result_is_transferred_to_common_action
```

It failed at the expected target `CHECK` constraint because the source row had not yet been transferred into a valid common Action. The exact initial pytest traceback/output was not retained in the final Task5B execution record; this sentence records the observed failure class and intended assertion boundary, not a reconstructed traceback. No RED test was rerun solely to recreate that output.

The exact seven-case final synthetic/current-API command and captured result were:

```sh
pytest -q tests/test_action_entity_schema_release.py::test_forward_preserves_business_facts_and_receipts tests/test_action_entity_schema_release.py::test_populated_external_result_is_transferred_to_common_action tests/test_action_entity_schema_release.py::test_populated_prepared_sent_and_observer_facts_keep_distinct_receipts tests/test_action_entity_schema_release.py::test_direct_email_translation_preserves_attempt_identity_and_rewrites_history_ids tests/test_action_entity_schema_release.py::test_direct_retry_parent_retargets_only_the_covered_action_reference tests/test_action_entity_schema_release.py::test_conflicting_identity_aborts_without_mutation tests/test_action_entity_callers.py::test_retry_keeps_original_external_identity
```

```text
.......                                                                  [100%]
7 passed in 32.13s
```

The exact contract/manifest command and result were:

```sh
pytest -q tests/test_action_entity_contract.py tests/test_action_entity_schema_release.py::test_current_release_ddl_matches_target_manifest
```

```text
..............                                                           [100%]
14 passed in 10.46s
```

The current caller regressions were run with:

```sh
pytest -q tests/test_action_entity_callers.py::test_prepared_message_caller_uses_common_action_and_owned_receipts tests/test_action_entity_callers.py::test_retry_keeps_original_external_identity
```

```text
..                                                                       [100%]
2 passed in 8.40s
```

Scoped static checks were run with these exact commands:

```sh
ruff check app/action_entity_transfer.py app/action_entity_schema_release.py app/action_email_definitions.py app/current_action_schema.py tools/action_entity_contract.py tests/test_action_entity_schema_release.py tests/test_action_entity_callers.py
python -m compileall -q app/action_entity_transfer.py app/action_entity_schema_release.py app/action_email_definitions.py app/current_action_schema.py tools/action_entity_contract.py tests/test_action_entity_schema_release.py tests/test_action_entity_callers.py
git diff --check
```

The final captured Ruff result was `All checks passed!`; `compileall` and `git diff --check` exited 0 with no output. These same scoped checks were repeated after the caller-fixture correction and before commit.

The actual copy runner and aggregate verifier were inline Python invocations and were not saved as a separate script. For Task5C reproduction, the implementation entrypoints are `app.action_entity_schema_release.prepare_action_entity_release`, `publish_action_entity_release`, and `app.action_entity_transfer.capture_source_facts`, `_identity`, and `transfer_source_facts`. The aggregate checks used: `PRAGMA table_xinfo(actions)` columns with `hidden=0`; exact source-retained record-type set from `RETIRED_ACTION_TYPES`; ordered source/target rows filtered to that same retained set; per-row equality for every shared stored source column except `legacy_key` on `email_direct_retry_requests`; and exact count of two such exceptions. For mapped source facts, identity-map joins used `(source_type, _identity(source_key))`, then SHA-256 comparisons to the mapped `action_receipts` values; `capture_source_facts` supplied source projections, including raw SQL NULL result distinctions. Prepared and Sent parent-body checks compared source `final_body`/`reply_text` hashes to the mapped Action `action_prepared_body`. No script path exists for those inline checks, so the counts above are recorded evidence, not a claim of a reusable saved verifier.

## Fix round 1: approved-execution binding and corrected populated copy

The first independent Task 5B review found that prepared-delivery lookup had indexed candidate proposal/options without requiring an approved review and an actual bound execution/selection. It also found that prepared-only Actions inherited `operation` from a plan that had not become a source operation fact. The translator now indexes only steps reached through an exact candidate execution whose review approves the same candidate digest; selected branches must reference that same candidate and review. Unselected options and unapproved proposals cannot supply prepared identity. If the source postfix has no matching candidate attempt, the new prepared-only Action leaves `operation` NULL. These are migration-time source-binding rules; no runtime caller or task/retry policy was changed.

The synthetic regression adds colliding unselected-option and unapproved-plan deliveries, an approved selected execution with an exact prepared delivery, and assertions that unbound prepared rows remain separate and have NULL operation. It also checks the approved candidate/attempt join. The historical-operation regression now creates two genuine active source Email Actions and an `email_historical_operations` row before prepare/publish; it exercises `capture_source_facts` and the real transfer transaction and verifies the ordered old public IDs are rewritten to their mapped common physical IDs. The exact command run for the focused regression bundle was:

```sh
pytest -q tests/test_action_entity_schema_release.py::test_forward_preserves_business_facts_and_receipts tests/test_action_entity_schema_release.py::test_populated_external_result_is_transferred_to_common_action tests/test_action_entity_schema_release.py::test_populated_prepared_sent_and_observer_facts_keep_distinct_receipts tests/test_action_entity_schema_release.py::test_direct_email_translation_preserves_attempt_identity_and_rewrites_history_ids tests/test_action_entity_schema_release.py::test_direct_retry_parent_retargets_only_the_covered_action_reference tests/test_action_entity_schema_release.py::test_conflicting_identity_aborts_without_mutation tests/test_action_entity_callers.py::test_retry_keeps_original_external_identity tests/test_action_entity_contract.py tests/test_action_entity_schema_release.py::test_current_release_ddl_matches_target_manifest tests/test_action_entity_callers.py::test_prepared_message_caller_uses_common_action_and_owned_receipts
```

```text
22 passed in 67.09s
```

The specific genuine populated RED remains the earlier `pytest -q tests/test_action_entity_schema_release.py::test_populated_external_result_is_transferred_to_common_action` run recorded above; it failed at the target Action `CHECK` because that source fact had not yet been transferred. Its exact initial traceback was not retained, so no traceback is reconstructed here. Fix-round scoped checks were `ruff check app/action_entity_transfer.py tests/test_action_entity_schema_release.py` (`All checks passed!`) and `python -m compileall -q app/action_entity_transfer.py` (exit 0). The test command also includes current retry API, history transaction, conflict abort, contract, target manifest, and prepared-message caller coverage.

The corrected translator was run once against this fresh owned copy: `/tmp/ceo-action-entity-validation.8rUAvK/task5b-fix1-n3m25y91/populated-forward.sqlite3`. The readonly source remained `/tmp/ceo-action-entity-validation.8rUAvK/source.sqlite3`, mode `0444`; its SHA-256 before and after was `799307664c58aeb2163eb44fa18a3261fadeb36fabf9e8534e905fdfe966f1a1`. The owned copy was byte-identical before publish, then `prepare_action_entity_release(copy)` and `publish_action_entity_release(copy, contract)` completed with `publish committed`. The transaction took about 2 minutes on a host concurrently running unrelated deploy/test processes. The final copy SHA-256 is `d69d0a27eec9e2bdb55f052de587e17c112e5f702ece5b0a5d2bc106a2321289`. Its preflight was Store `2026-10-19.12`, Email 53, reply variant `fresh_memory_fk`, schema hash `9d97aae7d76124ab74af9091cf82260ee24a30857c54dc7435e80ab1e898e007`, and one inherited FK finding. Publish guards and post-publish checks report exact target schema equality, the same one-row FK multiset, and `quick_check=ok`.

On that corrected copy, source fact counts and identity-map counts matched for every migrated family: external results 576; candidate attempts 401; postfix parents 1,487; prepared receipts 868; dispatch observations 207; Sent 2,508; observers 487; Email owners 11,387; and Email attempts 13,478. Each family had zero missing map rows. Historical operations numbered zero in the authoritative copy; their rewrite path is covered by the genuine non-empty synthetic source test. The target has 15,263 common Actions, 17,833 receipts, 487 observers, and 31,399 typed identity entries. All covered parent families were absent after publish. The corrected actual copy has 253 candidate-attempt owners with a prepared delivery key, retaining the expected exact prepared/candidate joins.

The actual-copy source-fact comparison matched SQL NULL as a distinct value and compared non-NULL JSON/body values by SHA-256: external result bodies/results 576/576; candidate outcomes 401/401; prepared provider results 868/868; Sent provider results 2,508/2,508; prepared parent bodies 1,487/1,487; and Sent reply bodies 2,508/2,508. All 479 source Sent SQL-NULL results remained NULL and carried their exact canonical provider-receipt reference; no body values were exported. A retained-row comparison selected generated `record_type` separately from stored columns and excluded newly-created common `action` owners. It compared 72,478 source-retained rows across all 24 shared stored columns and preserved the out-of-scope discriminator on every row. Only two cells differed: `legacy_key` on the two approved direct-retry controls, where the parent ID in composite element 0 is translated; all other control components and stored cells matched. `action_data` was SQL NULL on all 72,478 retained rows. The first attempt at this post-publish verifier selected generated `record_type` as though it were stored and failed in the verifier with `KeyError: 'record_type'`; it made no database changes. The corrected comparison above selected the generated discriminator separately and completed successfully. A separate initial comparison also included the newly-created `action` owners in its retained-row target predicate; the final comparison excludes those owners and is the evidence reported here.

The inline copy/verifier commands were not saved as standalone scripts. Reproduction entrypoints are `app.action_entity_schema_release.prepare_action_entity_release` and `publish_action_entity_release`, plus `capture_source_facts`; the aggregate method is documented above, including the exact source-retained discriminator/column comparison and the two allowed composite `legacy_key` element-0 translations. The prior owned copy and historical diagnostics remain historical evidence only; this fix-round copy is the current corrected forward result to hand to Task 5C. The inverse, old-code cold-open/reforward, canonical-correction retention review, and independent final release review remain open.
