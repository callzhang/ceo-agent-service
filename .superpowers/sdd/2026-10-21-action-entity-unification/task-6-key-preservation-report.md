# Task 6 key preservation

## Finding and source contract

The migration dropped a nonempty `candidate_action_attempts.external_action_key` when the key had no `external_action_results` owner. `_transfer_candidates` cleared the key before creating the common Action, and `_capture_inverse_facts` reads the candidate key from that common Action. The saved Task 6A2 inverse/reforward snapshots differ only in this field for the new uncertain orphan observation.

The observation is a real source-supported identity. `expected_external_action()` derives the key from the business object, action identity, operation, and target. `ReviewedCandidateStore.begin_candidate_action()` persists it per execution/action index, and runtime lookup/reconciliation queries prior attempts by that key. Repeated observations for one key are therefore expected across executions. The source table permits repeated keys and uniquely identifies an observation by `(execution_id, action_index)`. The current common Action key index is unique for Action owners, so repeated observations must share one owner only after checking the full stable tuple.

The fix retains a noncanonical key in the existing common `Action.external_action_key` field. A conversion-local map reuses a retained-key owner only when business object, action identity, operation, and canonical target JSON all match; a mismatch raises `ValueError` inside the release transaction. The candidate observation receipt retains its existing status and local outcome, plus the approved operation and target. The canonical-result owner and receipt path is unchanged. An orphan key without canonical result evidence does not produce an `external_action_results` row or provider receipt.

## Fail-first and focused regression

Before the implementation, this command failed both source-backed cases:

```sh
pytest -q tests/test_action_entity_schema_release.py -k 'orphan_candidate_key'
```

The repeated uncertain observations became an Action with no external key or operation, and the mismatched-target source was accepted instead of aborting. After the implementation the same command passed: `2 passed, 12 deselected`.

The cases build old-schema candidate, review, execution, and attempt rows through existing source factories. They verify same-key/same-tuple observations share one Action with both execution receipts; status, local result, target, typed source identity, and key survive forward/inverse/reforward; no canonical external result appears; and a same-key/different-target conversion aborts with the original snapshot unchanged.

The complete migration-release test file passed:

```text
pytest -q tests/test_action_entity_schema_release.py
14 passed in 34.50s
```

`ruff check app/action_entity_transfer.py tests/test_action_entity_schema_release.py`, `python -m py_compile app/action_entity_transfer.py tests/test_action_entity_schema_release.py`, and `git diff --check` passed.

## Saved 402-attempt owned-copy proof

Input was `/tmp/task6a2.fjw5dC/roundtrip-preflight/fixture/after-old-cold.sqlite3`, SHA-256 `629e010cbefe11d73adb928dbac4080d212e96ff71d5de4b67bd50a0c3d42ed9`. The check used a new SQLite-backup copy at `/tmp/task6-key-preservation.9r618jg1/owned-402b.sqlite3`; it did not write the input copy.

The task-owned temporary backup directory was removed after the evidence readback; only the source copy and the report remain.

Forward translation found 402 attempts, 359 nonempty keys, 358 unique nonempty keys, and the one orphan uncertain observation. That orphan key SHA-256 is `1dd0af9acf47ff2f1ec9b654c9772f9178069cae9bf9a1e766cd9ec268b47a2e`; its status remained `uncertain`, and its local-result JSON SHA-256 remained `cabbc5867911066c46d470115a1d883661f4e94610955c164d7042921d3db43a`. The 43 empty-key observations remained empty.

After inverse, `_compare_source_databases` reported `exact_match=true` across 102,817 typed Action identities, exact schema and SQLite sequences, and the same single pre-existing foreign-key finding. Re-forward then passed exact normalized typed-fact equality. `PRAGMA integrity_check` returned `ok`. A final readback showed the exact same FK row before and after: `('runs', 190042, 'tasks', 1)`.

One early attempt at the owned-copy check stopped on a mistaken assertion that every attempt must have a nonempty key. The report's corrected counts above came from a fresh SQLite backup and the complete forward/inverse/reforward proof; no migration failure or input mutation resulted from that early assertion.

## Scope

Only the candidate-key translation was changed. No fields, aliases, comparator exemptions, canonical-result inference, provider-success claims, runtime retry behavior, or business policy were added. No native run, provider call, push, or deploy was performed.
