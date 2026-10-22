# Task5C2 report — current-data inverse and legacy round trip

Date: 2026-10-22. Scope: local implementation and one isolated copy of the pinned source. This is not native CLI, capacity, UI, release, or production acceptance.

## Implementation

`invert_action_entity_release` now captures current Action, receipt, observer, and typed-map facts inside the inverse transaction, drops the target-only structures, rebuilds the exact Store19.12/Email53 declaration, and reconstructs the covered source families from those facts. The inverse preserves the source family’s typed public IDs independently of common Action physical IDs, reconstructs source-specific parents, translates retry `legacy_key[0]` and ordered History references, and reconstructs prepared receipt/dispatch children from their actual physical source columns. It preserves retained source declaration cells and allocator high-waters. Current canonical binding references remain separate from independent old Sent/attempt bodies; source SQL NULL is retained only for a matching canonical body, otherwise the exact observed body is materialized in the corresponding old local result.

The inverse uses the exact predecessor declaration and raw source-shaped table rebuild; it does not use the obsolete generic predecessor helper for the 57-column `reply_attempts` table. No normal runtime identity alias or additional authorization, audit, retry, or reconciliation policy was added.

## RED and focused GREEN

The first actual new-caller inverse probe hit a real conversion gap: `_action_path` could not represent the prepared receipt’s three ordinary physical TEXT columns. The recorded checkpoint is in `progress.md` (“C2 first actual new caller inverse RED”); the exact shell invocation and stderr were not retained, so I do not reconstruct them. The conversion now reads the source child projection from the actual prepared declaration (`postfix_receipt_provider_result_json`, `postfix_receipt_created_at`, and `postfix_dispatch_created_at`), preserving SQL NULL versus literal JSON text.

### Controlled retrospective exact-base RED/GREEN

To close the review evidence gap without claiming access to the historical run, I performed a new controlled comparison against the exact pre-C2 base `61632a97bd65fb51ea654e009a8276b6b6b1a435`. The archived app/tests/pyproject bundle SHA-256 is `d46d44555b9c12643888328b7ee06fac93e32c3f1f954a750e2a13a29e37d21b`. The shared probe script SHA-256 is `cd06f923fe79ca21d6437d5c9986bf646f67fcdbc5eed339d84ef8c1fbe904ca`; it creates a valid predecessor schema, uses current public `prepare_outbound_action`, `claim_outbound_action`, and `record_outbound_action_receipt` writers once, then invokes the actual public inverse and checks predecessor parent, receipt body, dispatch clock, and integrity. It does not call `_capture_inverse_facts` or another implementation-private helper.

The single forward-seeded fixture SHA-256 is `9d83139bb124ff1952ffe8eb220c7fde4f1d73bd81ee05446a14bb0a89a99835`. I copied those exact bytes to `baseline3.sqlite3` and `current3.sqlite3` before either inverse. Both executions reported that same forward DB hash. The predecessor inverse module was imported from the extracted BASE archive (`action_entity_schema_release.py` SHA-256 `d62c0f60842077c52d65a889735fcc4c2e13e89321932c875920c2010975503c`); the current inverse module was imported from this worktree (SHA-256 `7f26e58ec52cf7aefe72511958b8036e15a5bf758f32e75b400da8fb99f8c9c1`).

The exact BASE RED command was:

```sh
PYTHONPATH=/tmp/ceo-action-entity-validation.8rUAvK/task5c2-red-green-YMhcPV/base \
  python /tmp/ceo-action-entity-validation.8rUAvK/task5c2-red-green-YMhcPV/prepared_inverse_probe.py \
  inverse /tmp/ceo-action-entity-validation.8rUAvK/task5c2-red-green-YMhcPV/baseline3.sqlite3
```

It emitted `inverse_started_after_actual_writer` with the pinned BASE module path and the fixture hash above, then exited `1` in the predecessor `invert_action_entity_release` → `_rebuild(..., inverse=True)` conversion with:

```text
sqlite3.IntegrityError: NOT NULL constraint failed: actions.legacy_key
```

This failure occurs inside the real inverse after valid prepared facts were written; it is not a missing import, test helper, or malformed setup. It demonstrates the pre-C2 inverse cannot round-trip this new normal prepared Action. The historical first RED remains unclaimed and its exact invocation/output remains unavailable.

The exact current GREEN command on the byte-identical pre-inverse fixture was:

```sh
PYTHONPATH=/Users/derek/.codex/worktrees/action-entity-plans/ceo-agent-service \
  python /tmp/ceo-action-entity-validation.8rUAvK/task5c2-red-green-YMhcPV/prepared_inverse_probe.py \
  inverse /tmp/ceo-action-entity-validation.8rUAvK/task5c2-red-green-YMhcPV/current3.sqlite3
```

It exited `0`. The observable parent body hash was `23faa265ea472d043bb36733457ba113468fd2dbd2da258a0308883874d899d8`, provider receipt body hash `e09b99b6d2369530bb339c089c9f788015e8248537ba791cdcf875040da1ab94`, dispatch clock was present, `quick_check=ok`, and the inverse retained the source’s empty FK finding set. This controlled comparison is a newly captured retrospective BASE-vs-current proof, not a reconstruction of the initial C2 RED chronology.

The following focused regression command passed after the conversion and source-ID collision test were added:

```sh
pytest -q \
  tests/test_action_entity_schema_release.py::test_new_action_and_recall_survive_inverse \
  tests/test_action_entity_schema_release.py::test_inverse_keeps_colliding_public_ids_in_their_source_families \
  tests/test_action_entity_schema_release.py::test_direct_retry_parent_retargets_only_the_covered_action_reference
```

Result: `3 passed in 20.53s`. After adding the injected-after-write rollback assertion, this targeted command also passed:

```sh
pytest -q \
  tests/test_action_entity_schema_release.py::test_new_action_and_recall_survive_inverse \
  tests/test_action_entity_schema_release.py::test_inverse_keeps_colliding_public_ids_in_their_source_families \
  tests/test_action_entity_schema_release.py::test_direct_retry_parent_retargets_only_the_covered_action_reference \
  tests/test_action_entity_schema_release.py::test_conflicting_identity_aborts_without_mutation
```

Result: `4 passed in 24.03s`. The inverse rollback test injects failure after `restore_inverse_facts` completes writes and compares the complete `actions` rows (including `action_data`), receipts, observers, typed identity map, History references, and schema marker before and after rollback.

The collision regression uses actual source families and valid parents. Candidate-attempt source IDs 203/204 and Sent source ID 203 coexist; inverse restores those typed public IDs, while their current common Action physical IDs remain distinct and are never substituted for the source IDs.

The pre-existing `test_inverse_does_not_restore_source_snapshot` and `test_reforward_has_no_duplicate_effect` remain empty-schema marker/zero-Action cases. They are not counted as evidence for new-fact inverse or no-duplicate business behavior; the populated writer-family regression and owned actual-copy sequence provide that evidence.

## Isolated current-data round trip

Protected source: `/tmp/ceo-action-entity-validation.8rUAvK/source.sqlite3`, SHA-256 `799307664c58aeb2163eb44fa18a3261fadeb36fabf9e8534e905fdfe966f1a1` (unchanged). Pinned old code: `/tmp/ceo-action-entity-validation.8rUAvK/old-code-897227b`. Owned current copy: `/tmp/ceo-action-entity-validation.8rUAvK/task5c2-own-aGGHBR/current-facts.sqlite3`, final SHA-256 `af19e486cd41bbd3da88966ff498c66f51ec103d1bf941a6ff614871e50a9c85`.

On that one copy, the sequence was source → current forward → real current reviewed/canonical/observation/correction/local-mismatch facts, direct-email attempt/retry/History facts, prepared/provider/dispatch facts, Sent/observer/recall facts → actual current-fact inverse → separate-process old-code Store/EmailStore cold reads and prepared-postfix write → current reforward. No provider was called. The old process imported the pinned `app/store.py` (SHA-256 `0d426abbfe0042247cdbc40f41d559f294b1f7299436724602f0e38155268d4c`) and `app/email_store.py` (SHA-256 `cdd33b7b23367ba1be328307941d85a5003e92710a9366d32a84beade712d147`).

Read-only final verifier command:

```sh
PYTHONPATH=/Users/derek/.codex/worktrees/action-entity-plans/ceo-agent-service \
  python /tmp/ceo-action-entity-validation.8rUAvK/task5c2-own-aGGHBR/verify_reforward.py
```

It passed with `quick_check=ok`, final current Action count 15,268, and the same inherited FK finding multiset as source: `runs(190042) -> tasks(1)`. It confirmed the reviewed canonical body hash `8b72b9f4a03eeee43c2460f3d16134d026ee05b1a947d08c0797d2ec103a42fb`; the prior Sent body hash `a709a62f7f77f1343f5a0a8a5edecab6723f4cd1eac6bd9ac04ec60df7b2ae77` and `recalled` status; prepared body hash `2ed1c12da2ce0d4d9531d21031102e08a15514acd66ac47768f2c11f02fbc02d`, receipt hash `db3181bc4ceafe34ab7dae243b6bc2ae04948d48d2eca5188c5fc6aa82041a76`, and receipt/dispatch clocks; direct-email attempt numbers `[1,2]`, retry `[2]`, and History reference to the current public ID; and the old-code cold-write prepared body hash `2d5c1754832f2526ae8a5b527cc76a9e789adb7a10350ffb188b74d55125c6f8` surviving reforward.

The inverse preserved all original typed public-ID sets and correctly allocated new source IDs. It preserved 72,476 out-of-scope source rows by original physical ID and full projected row tuple (SHA-256 `69a2d14bcab183e45e272a95e5db85dafdd0af902817262d754bcb961e05ebac` at inverse); actual new writer parent rows were four explicitly counted additions. The source Actions declaration has 468 PRAGMA projection columns: 30 stored and 438 virtual. Source and target share 438 column names; 24 are common stored columns. Reforward comparison covers all 438 shared projection values, not 438 stored cells. The two existing retry controls retain their physical IDs, attempt number, payload bytes, state, clocks, and other composite fields; only the documented parent components are translated. Their payloads remain byte-identical.

Saved read-only verifier SHA-256: `3cfeae173ae2d925c184e55b2ef4d0b4b15fad856f1e5156307e6335ee3e5377`. The saved full-sequence scaffold SHA-256 is `e4bde9dd5f1b3cde3134d61707af776d7ed2cf1d3151d2c5b2bfaca16993c8eb`; the inverse/cold-write/reforward continuation SHA-256 is `84101546ed86a8d19fa2635dbec363d81a0a1b14333f48343214d8162015e2bd`. These scripts and the copied DB remain under the owned task directory, outside the repository.

The copy’s verifier is read-only. It does not re-run inverse or reforward. Temporary orchestration and cold-process scripts remain under the owned `task5c2-own-aGGHBR` directory; no source restore was used.

## Verification

```sh
python -m py_compile app/action_entity_schema_release.py app/action_entity_transfer.py tests/test_action_entity_schema_release.py
ruff check app/action_entity_schema_release.py app/action_entity_transfer.py tests/test_action_entity_schema_release.py
git diff --check
```

All three passed. Full-suite tests were not run. No native CLI, production DB, real provider, push, or deploy was used. Task5C independent final review, Task6 native/capacity/UI gates, and Task7 release remain outstanding.
