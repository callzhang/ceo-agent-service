# A2 receipt-owned candidate results

This change selects Store `2026-10-19.2` / Email52 in the isolated
`codex/business-model-receipts-20261019` worktree, based on `c29140f02`.
The implementation and local checks below are separate from root-owned native
acceptance, independent review, integration, commit, and deployment. No provider
send, production write, or primary-checkout edit is part of this implementation.

## Ownership and runtime behavior

The existing `candidate_action_attempts.result_json` slot remains in place. Only
its kind-specific CHECK changes from TEXT to TEXT-or-NULL. A verified NULL result
references `external_action_results.provider_result_json` through the existing
`external_action_key`; no business field, reserved slot, or stored second copy is
added. Failed, uncertain, confirmed-no-effect, and distinct verified results keep
their own text, including a distinct `{}`. Forward conversion compares the raw
strings, not parsed JSON, normalized text, or truthiness.

The runtime projection is exactly:

```sql
CASE WHEN attempt.status = 'verified' AND attempt.result_json IS NULL
     THEN matching_receipt.provider_result_json
     ELSE attempt.result_json END
```

Store readers validate the projected result and raise
`CandidateResultReferenceError` (a `sqlite3.IntegrityError`) for a broken reference.
History propagates that error instead of silently returning an empty receipt
list. SystemExecutor, prior-attempt reconciliation, verified-action listing,
History, and delivery repair consume this projection. The API still returns the
original result string. Empty or distinct text is never replaced by a truthy
fallback.

`record_candidate_external_action` and prior-action reconciliation write the
receipt and the verified reference in their existing transaction.
`record_candidate_action_outcome` stores NULL only when its supplied serialized
body equals the current receipt bytes. Existing-receipt reuse also removes an
equal default `{}`, while retaining that default when the receipt differs.

The source does permit receipt mutation. The existing ambiguous-to-sent correction
in `record_candidate_external_action`, and three existing delivery-repair update
sites, were inspected. Before those existing corrections replace the owner body,
all verified NULL references to it are materialized with the old body in the same
transaction. Attempts actually assigned the corrected body by that existing
operation then store NULL again. Other attempts retain their original meaning.
The existing identity checks, accepted correction conditions, status choices,
replay behavior, and authorization logic are unchanged. Other Store receipt
writers use insert-ignore and do not replace an existing owner body.

## Exact declarations and current-data inverse

`result_action_objects(reply_variant, result_references=True)` selects the new
declaration. The shared factories default `result_references=False`; the old
`NULLABLE_PAYLOAD_FIELDS`, source kind layouts, generated columns, codes, keys,
and historical factory selections remain unchanged. Both Reply FK variants are
supported. The shared physical `result_json` generated column remains available
to other kinds.

Within the candidate-attempt payload CHECK, the only declaration difference is:

```sql
json_type(payload_json,'$[3]') is 'text'
-- becomes
(json_type(payload_json,'$[3]') is 'text' or json_type(payload_json,'$[3]') is 'null')
```

Tests compare the complete target table SQL to the source with only that one
replacement and compare every owned index and trigger unchanged. Fingerprints
use the repository's existing `_fingerprint` over the complete object list:

| Variant | Frozen 19.1 source | Explicit 19.2 target |
|---|---|---|
| actual_no_memory_fk | `317eb42c3c0a24e2f210d5ba52d11888b3c1a1cf74217979e3bce2a240893f04` | `77ac6dff94126976212d1cd7bfda3b36531e4ee8a02839a9a73f88bee85401d4` |
| fresh_memory_fk | `ad99aad17823c90a058947ba61d261e6da6ffbf9c9290a8a67b8aa825d9190ad` | `f9ab43a6874aba1cc9fc8d6c19770a4834c6374850e29630a905dcc91379c3be` |

The pre-A1 compact factories remain pinned to actual
`a72296e1cf7c55dda83b63cff6e1760bfe6ffeb09226450225f100f40501ec66`
and fresh `e57ae120f24d6ab8aa6e8b5d9825101f438222bfbbbf14060a851d2242fe4245`;
the default six-kind factory remains
`21a82e3a36ed0cc4043cb52f91346b9be3aa116d086d8e28b0dcc8b03f98a5ec`.

Both directions require the caller's existing transaction with FKs disabled,
an exact supported source declaration, and known dependent objects. Published
configs and Email triggers are allowed only with their exact SQL. Unknown
dependent views, triggers, incoming FKs, and TEMP-name collisions refuse before
mutation. Unrelated objects survive. The allowed incoming Business FK declaration
is checked explicitly.

The rebuild uses only transaction-local TEMP staging. It copies every writable
column, changes the planned result slots, rebuilds the exact declaration, restores
all current `sqlite_sequence` rows including row IDs, and checks stored values
and SQLite types in both directions. Existing FK findings are compared as a
multiset, preserving duplicates and allowing enumeration order to change. The
inverse metadata is only the Reply declaration variant; release metadata adds
the original Store marker and clock, Email markers, and original FK findings.
It contains no business rows or body backup.

Inverse reconstructs every current verified NULL attempt from its current
matching receipt, including receipts and attempts inserted after publication.
It refuses missing owners or invalid NULL/status bindings. Distinct attempt-local
results remain exact. The current physical/public identities, JSONB bytes of
other rows, scalar types, provenance, selection bindings, and allocator state
survive inverse followed by another forward conversion.

## Publication and historical-test boundary

The existing `prepare_database_schema_release` chooses `ResultSchemaRelease` for
exact Store19.1/Email52 to Store19.2/Email52. Publication converts Actions and the
Store marker atomically, invokes the existing target-root cold opener, and uses
the existing `publish`, `verify_loaded`, `rollback`, and `finalize` protocol.
Normal current constructors refuse Store19.1 and mixed pairs before writes.
Fresh databases select 19.2 directly. Email52 declarations and marker clocks
remain exact. Historical A1 and action_email release constants, factories, and
default declarations are unchanged.

The original A1 integration tests deliberately cold-open their target runtime.
They therefore belong to the frozen A1 runtime, whose constructors accept 19.1.
They were run unchanged from an owned `git archive c29140f02` extraction before
retiring the superseded current-root module. No version bypass, skip, or patched
constructor was used. Immediately before the final historical run, all **2,717
tracked archive files** were compared byte-for-byte. That same Python process
verified `app.store`, `app.selection_schema_release`, and
`app.reviewed_candidate_store` loaded from
`/tmp/ceo-receipt-reference.q0eygc/a1-code/app/`, then ran the five original cases:
**5 passed in 134.44s**. Archive SHA-256:
`e3a1af7f1604e394d42fa4e7fec8e420a32243bf9e141fec62077bcfafc13b72`.

Each old integration case has a current A2 replacement in
`tests/test_result_schema_release.py`:

| Frozen A1 case | Current A2 case |
|---|---|
| `test_registered_release_keeps_email_marker_and_inverts_new_selection` | `test_registered_release_and_current_fact_rollback`: adds a new selection, receipt, and attempt after forward; verifies exact marker/clock, Email52, binding, IDs, current raw rows and allocators through inverse/reforward |
| `test_current_target_factory_is_noop_and_rejects_mixed_pair` | `test_fresh_target_is_noop_and_mixed_marker_refuses` |
| `test_old_pair_requires_explicit_publication_without_constructor_writes[AutoReplyStore]` | `test_source_constructor_refuses_without_writes[AutoReplyStore]` |
| `test_old_pair_requires_explicit_publication_without_constructor_writes[EmailStore]` | `test_source_constructor_refuses_without_writes[EmailStore]` |
| `test_deploy_readers_support_both_exact_sides_and_target_cold_failure_rolls_back` | `test_publication_readers_and_cold_failure_rollback` |

A1 component, dependency, FK-order, and frozen-factory tests remain in the current
suite without a historical-version bypass.

## Test-driven development and focused verification

All commands use `/Users/derek/miniforge3/bin/python3.12` (Python3.12.11,
SQLite3.53.4, pytest9.0.2) and
`CEO_ENV_FILE=/tmp/ceo-receipt-reference.q0eygc/no-env`, an intentionally missing
file. Tests use fixtures and substitute provider results only.

The first A2 run was RED: 16 missing-module failures and one duplicate-body
writer failure. Subsequent failing tests demonstrated the untouched historical
attempt changing when its receipt changed, History swallowing a missing owner,
an unrecognized incoming FK being accepted, and an equal `{}` reuse keeping a
second body. Each was fixed at its shared data-access or schema boundary. A
physical JSONB comparison also caught a different string encoding when a plain
SQL TEXT parameter was used with `jsonb_set`; the migration now supplies the
serialized string through `json(?)`, matching the source representation.

Focused corrections passed: inverse/missing-owner four cases (86.44s), runtime
writer/correction two cases (13.34s), registered-release/writer/correction three
cases (63.50s), atomic rollback/nonverified/prior reconciliation five cases
(24.55s), incoming-dependency two cases (65.66s), and equal/distinct `{}` reuse
two cases (11.96s). A fixture's nonexistent `first_agent_run_id` was corrected to
the already-existing source run; this did not change product behavior.

The seven-file regression (`test_model_result_references`,
`test_result_schema_release`, `test_model_action_schema`,
`test_reviewed_candidate_store`, `test_system_executor`,
`test_dingtalk_delivery_repair`, `test_selection_runtime_readers`) finished
**179 passed, 1 failed in 700.09s** with two workers. Its only failure was the new
post-forward selection fixture supplying no parent for a revised Consumer; the
fixture now supplies its existing prior Audit run ID. This run spans the later
equal-`{}` writer correction and is preliminary regression evidence, not a
frozen-final-code claim.

Runtime code was then frozen as **420 app files**, manifest SHA-256
`68a39cba039e0ba86c0fc24668ea684a09a60ac49714043c8019863655961dd5`.
The final two A2 test modules plus the real Store restart, uncertain-receipt
no-dispatch, and corrected-receipt readback executor cases passed on this freeze:
**34 passed in 201.14s**, exit 0. The exact focused command was:

```sh
CEO_ENV_FILE=/tmp/ceo-receipt-reference.q0eygc/no-env \
/Users/derek/miniforge3/bin/python3.12 -m pytest -q -n 2 \
  tests/test_model_result_references.py \
  tests/test_result_schema_release.py \
  tests/test_system_executor.py::test_sqlite_reviewed_execution_preserves_receipt_across_restart \
  tests/test_dingtalk_delivery_repair.py::test_executor_reconciles_uncertain_historical_receipt_without_dispatch \
  tests/test_dingtalk_delivery_repair.py::test_executor_completes_corrected_uncertain_receipt_from_readback
```

Ruff passed all 26 changed Python files, and `git diff --check` passed. The
strengthened complete-DDL comparison passed separately in 1.28s. The whole
repository suite is not run.

## Owned source-copy proof

The immutable source is opened read-only with `mode=ro&immutable=1` and backed up
to `/tmp/ceo-receipt-reference.q0eygc/result-copy.sqlite3`. Frozen A1 code first
publishes that owned copy from 14.1/Email52 to 19.1/Email52 using the real release
protocol. Source inventory was independently recounted as **274 verified,
37 failed, 5 uncertain** attempts; all 274 verified attempts had matching
receipts, exactly 273 bodies were byte-equal, and one distinct `{}` remained.
The existing FK finding is `[('runs', 190042, 'tasks', 1)]`.

The first A2 copy publication completed in 258.86s and the unchanged-table,
public-result, and receipt-row assertions passed. A verification-script mistake
then recomputed the 23,672-row State hash map once per State row. That harness
process was interrupted through its own process session while outside a write
transaction. It is not counted as a completed inverse proof or a product failure.
Because the original marker/clock metadata was only in that process, a fresh
owned copy was prepared from the immutable source for the corrected one-pass
verification. No original marker was inferred or invented.

The final proof hashes row IDs, all writable values, BLOB bytes, and SQLite scalar
types for all ordinary tables and `sqlite_sequence`; it compares every public
attempt result and every receipt before and after forward. It then adds a new
receipt, a NULL attempt, and an unrelated allocator watermark, rolls back from
current facts, verifies the original Action bytes and State rows are restored,
and publishes again to compare complete current row hashes. The final run also
hashes all app files before and after, refusing a proof if runtime code changed.

## Changed-file boundary

The implementation owns the following 30 paths. `docs/agent-claims.md` is edited
separately by the root coordinator. No staging or commits are made by this
implementer.

```text
app/action_delivery_schema_release.py
app/action_email_schema_release.py
app/action_postfix_schema_release.py
app/action_receipt_schema_release.py
app/action_schema_release.py
app/action_storage.py
app/attempt_what_happened.py
app/configs_schema_release.py
app/current_database_schema.py
app/deploy_maintenance.py
app/dingtalk_delivery_repair.py
app/email_publication_sources.py
app/email_store.py
app/model_result_schema.py                         (new)
app/repository_updater.py
app/result_schema_release.py                       (new)
app/reviewed_candidate_store.py
app/runs11_current_schema.py
app/scheduled_config_storage.py
app/store.py
app/store_schema_release.py
app/system_action_handlers.py
app/system_executor.py
app/task_schema_release.py
docs/architecture.md
docs/business-model-result-reference-validation.md (new)
docs/runtime-mechanism.md
tests/test_model_result_references.py               (new)
tests/test_result_schema_release.py                 (new)
tests/test_selection_schema_release.py              (retired as explained above)
```

The upstream `EmailStore.list_recent...` SELECT addition announced by the root
coordinator is outside this isolated base and must survive integration. This
change touches only EmailStore's current-declaration checks. The validation
copy and archive are owned temporary evidence under
`/tmp/ceo-receipt-reference.q0eygc/`, retained for root review; they are not runtime
metadata or production recovery backups.
