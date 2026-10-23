# Task2D6 Email classification native Task checkpoint

Status: partial implementation checkpoint, not complete. Product code and the
focused native fixture path are locally changed, but current-data offline
Task/Run inverse portability fails and needs the separate converter-only Task3E5
contract before Task2D6 can finish or be called accepted. No constructor or
shared-family activation, release, production operation, provider, model, CLI,
push, or deploy was performed.

## Ownership and runtime path

- `tasks(kind='email_classification')` owns the queue lifecycle. Its public
  runtime key is the canonical decimal Task ID. `parameters_json` is empty;
  status remains `pending/running/done/failed`, with native lease owner,
  attempts, generation, availability and error fields.
- `email_classification_inputs` owns channel, stable message identity and the
  immutable/redactable `input_json`; `task_results` owns only the classification
  runtime result. Email classification rows, ActionPlans and action receipts
  remain their existing independent business owners.
- `EmailClassificationTaskAdapter`, the producer, worker, routed workload
  grammar/parent validation, offline wrapper, storage maintenance, quality
  aggregation, Email queue status/failed Attention readers and deployment-drain
  Email count were moved to native Task ownership. Shared all-family dashboard
  detail/History, constructor/schema activation and deploy preflight remain
  explicitly out of scope.
- Offline open creates a native running Task and retains the source child facts
  (`channel=email`, `stable_message_identity='offline:'+incoming token`,
  `input_json='{}'`, owner, attempt/generation 1 and six-hour lease). Reopening
  the same token replaces the old placeholder as source `INSERT OR REPLACE`
  did. Wrapper delegation uses the canonical ID; finally-close and owner-scoped
  crash purge delete the same empty-input placeholder, including its native
  result row, rather than retaining a second execution alias.

## Required inverse evidence and blocker

Added `test_closed_offline_email_task_attempt_survives_current_task_run_inverse`
using the current source migration fixture, the actual assembled target DDL,
native offline open, an actual terminal `runtime_attempts` row, and actual close.
The sequence fails at `restore_run_entities` after Task inverse:

```text
ValueError: native Task public identity is unresolved:
email_agent_classification_tasks:416922
```

This is the exact current-data risk: source offline close deletes its temporary
Task but its completed RuntimeAttempt survives. The current Task inverse removes
the native-to-source Task identity needed to turn that orphan operation key
back into a source-valid key. The attempt never reached inverse/old-code read or
portable reforward assertions, so the two-cycle/no-replay and numeric collision
proofs are not claimed. The native Task allocation high-water/nonreuse impact
is likewise not yet asserted by this test. Do not solve this by retaining the
closed Task, inventing a Task tombstone, persisting an old source key at runtime,
or adding runtime alias fallback. Parent has bounded the follow-up to dedicated
converter-only Task3E5 review: a portable inverse handoff for the exact orphan
workload edge, allocated source operation key, validated against context/digest
and consumed on reforward. Task2D6 has not edited converter code.

## Verification

Focused green command:

```text
pytest -q tests/test_email_task_adapter.py::test_email_classification_console_queue_reads_native_owner tests/test_email_task_adapter.py::test_email_classification_adapter_uses_native_task_and_typed_result tests/test_email_task_adapter.py::test_email_classification_expired_claim_uses_native_cas_and_canonical_runtime_key tests/test_email_task_adapter.py::test_concurrent_native_classification_enqueue_has_one_stable_owner tests/test_email_training_labeler.py::test_reopening_offline_classification_replaces_the_same_native_owner tests/test_email_worker.py::test_native_classification_worker_keeps_email_result_separate_from_task_result
6 passed in 4.87s
```

The focused inverse test above was run separately and failed at the unresolved
identity shown above; this is a required blocker test, not a green acceptance
result. The entire `tests/test_email_task_adapter.py` module was also run and
reported `34 failed, 41 passed`; those failures exercise the source-era adapter
fixtures and unrelated legacy Email business-task flows against the newly
native-only classifier adapter. They remain outside this focused native
checkpoint and the full module is not reported green. The native fixture uses
the repository's actual `task_run_entity_assembly_objects()` SQL rather than a
fake Store. Ruff passed for all changed Python modules/tests, `compileall`
passed for changed application modules, and `git diff --check` passed.
Architecture/runtime document review and final diff/commit verification remain.

## Remaining work

Resume after the Task3E5 converter-only prerequisite supplies the reviewed
portable orphan-operation handoff. Then assert deleted-Task ID allocation
high-water/nonreuse, inverse can read the exact terminal operation with its
allocated source key without replay, and two JSON-portable inverse/forward
cycles do not collide with another Task. Finish source-migrated pending/running/
completed acceptance and affected reader tests; review final docs/diff; then
commit only the owned hunks. Common family activation and release remain later.
