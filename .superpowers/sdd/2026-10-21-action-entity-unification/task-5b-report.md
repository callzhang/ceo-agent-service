# Task 5B: populated forward migration report

**Result:** the populated Store19.12/Email53 forward translator and exact target declaration pass on one owned copy of the authoritative source. This is local forward evidence only. Task 5C still owns current-data inverse, exact old-code cold-open/reforward checks, and the agreed canonical-correction retention behavior; independent final review and release gates remain open.

## Source and owned copy

The read-only source was `/tmp/ceo-action-entity-validation.8rUAvK/source.sqlite3`, 496,410,624 bytes, mode `0444`, SHA-256 `799307664c58aeb2163eb44fa18a3261fadeb36fabf9e8534e905fdfe966f1a1`. Preflight observed marker `2026-10-19.12`, Email schema maximum 53, reply variant `fresh_memory_fk`, source schema SHA-256 `9d97aae7d76124ab74af9091cf82260ee24a30857c54dc7435e80ab1e898e007`, and one existing FK finding.

The forward publish ran once on the owned, initially byte-identical copy `/tmp/ceo-action-entity-validation.8rUAvK/task5b-populated-forward-dz8st8a8/populated-forward.sqlite3`. It called `prepare_action_entity_release(copy)` and `publish_action_entity_release(copy, contract)`. The release transaction’s exact target manifest, FK multiset, and `quick_check` guards all passed before commit. Final marker is `2026-10-21.1`; final copy SHA-256 is `2235fddcca1ec5557edf11c961621dec1f19e46f95e63e0d0e20f5d195f26bdb`. The readonly source SHA-256 was rechecked after publish and remains unchanged.

An extra post-publish verifier initially compared source out-of-scope rows with every target row not in a retired family. That predicate also included newly-created common Action owners, so it stopped on a 15,263-row difference; the release was already committed and its own guards had passed. The verifier was corrected to compare only the exact 39 source-retained record types. That comparison covered 72,478 rows and all shared physical source columns. It found only two changed cells, both `legacy_key` on the two `email_direct_retry_requests` rows; each is the specifically approved composite parent reference rewrite. No other out-of-scope source cell changed. The newly-added `action_data` column is SQL NULL on all 72,478 unchanged non-Action rows.

## Populated conversion evidence

The source contained these covered parent-family counts: `external_action_results` 576; `candidate_action_attempts` 401; `outbound_postfixes` 1,487; `sent_replies` 2,508; `sent_reply_observers` 487; `email_actions` 11,387; and `email_action_attempts` 13,478. Prepared child facts included 868 provider receipts and 207 dispatch observations. `email_historical_operations` contained zero source rows.

The target contains 15,263 common Action owners, 17,833 Action Receipts, 487 Action Observers, and 31,399 typed identity-map rows. All seven covered source families and both prepared child types have zero remaining rows. Identity-map row counts are: candidate attempts 401; email attempts 13,478; email owners 11,387; external results 576; postfix parents 1,487; postfix receipt children 868; dispatch children 207; Sent 2,508; and observers 487. The 253 exact prepared-delivery-key candidate joins share a common Action owner; all candidate attempts, including the 43 without a canonical external-result owner, have their own mapped receipt/owner facts. Of 487 nonempty Sent external keys, 485 join their exact canonical owners and two remain independent orphan Actions. All 2,021 empty Sent keys remain separate owners. No grouping uses body, token, digest, or timestamp equality.

Every mapped source body/receipt comparison was exact by SHA-256, with no body content exported: external-result bodies 576/576; candidate result values and SQL-NULL distinctions 401/401; prepared provider result bodies 868/868; and Sent result values and SQL-NULL distinctions 2,508/2,508. Prepared parent bodies matched their mapped Action body hashes 1,487/1,487; Sent bodies matched their mapped Action body hashes 2,508/2,508. The source has 479 Sent `send_result_json` SQL-NULL references and 2,029 local result bodies; the target retains 479 SQL-NULL receipt bodies with 479 typed canonical receipt references. Candidate result JSON has 357 SQL-NULL rows, each retained as the exact canonical receipt reference. Receipt status, operation/target, result ID and source clocks are mapped in source receipts; observers retain their `(sent_reply_id, agent_run_id)` identity, task and creation time.

The release retained the original one-row FK finding multiset and passed `pragma quick_check=ok`. There are no target FK findings beyond the one inherited finding. The final schema exactly matches the current 507-object Store/Email manifest; its static rendering is `docs/action-entity-current-target.sql` and a focused test compares that file byte-for-byte with the manifest.

## Direct-retry FK exception

Two surviving `email_direct_retry_requests` rows referenced the retired `email_actions` parent through the existing generated composite FK. The target now generates parent type `action`, enforces `email_action_id -> actions.id`, and checks the parent’s `record_type='action'` on insert and composite-key update. The migration resolves each source public parent key through the typed `email_actions` map and rewrites only element 0 of the retry composite `legacy_key`; the remainder of the row, payload, attempt number, status, and clocks remain unchanged. Both actual rows resolve to a common Action. No old public-key alias, new business field, or additional global index was added.

A fresh current `EmailStore` regression exercises the source-verified retry Store API from a real eligible failed trash Action and checks that the new request points to the same common Action ID. The archived predecessor API harness is not used for this assertion: its fixture creates Store13.5/Email51, which this explicit Task5B source release correctly rejects before reaching that API. That older fixture chain is not changed here.

## Verification and remaining gates

Passed focused checks:

- `pytest -q tests/test_action_entity_contract.py tests/test_action_entity_schema_release.py::test_current_release_ddl_matches_target_manifest` — 14 passed.
- Seven populated synthetic/current-API tests covering the converted families, prepared/Sent/candidate proven joins, ordered synthetic history-ID rewriting, conflict abort without mutation, direct-retry parent translation, and current Store API retry — 7 passed.
- `pytest -q tests/test_action_entity_callers.py::test_prepared_message_caller_uses_common_action_and_owned_receipts tests/test_action_entity_callers.py::test_retry_keeps_original_external_identity` — 2 passed. The former query now checks absence of the retired record type without selecting its retired `delivery_key` generated column.
- Scoped Ruff, `compileall`, and `git diff --check` — passed.

A broader `pytest -q tests/test_email_action_storage.py` probe returned 9 passed and one fixture failure: `test_email_generated_fields_are_null_for_older_actions_and_absent_email_fields` constructs the schema through `create_action_schema()` but then calls the compact-source `_kind` writer, which reports `table actions has no column named _kind`. No `action_storage.py` writer or test was changed for that mismatch; it is outside the forward-release/API tests above. The archived paired direct-retry test likewise remains outside this gate because its Store13.5/Email51 source marker is unsupported by this bounded 19.12/53 release.

Task5C remains responsible for actual current-data inverse, old-code cold-open/reforward acceptance, and source-faithful canonical correction/receipt retention. No provider/native CLI, production database, push, deploy, or cleanup of shared source/archive paths was performed. Keep the owned populated copy at the exact path above for Task5C handoff.
