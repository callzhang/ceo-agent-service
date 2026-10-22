# Task 6A4 historical native-readback fixture

**Result:** Replaced the invalid reverse-conversion setup in `_legacy_native_readback_case` with a synthetic Store 19.12 / Email 53 source fixture followed by the real current Action Entity forward release. The existing completed-run/no-candidate settlement case and its permission and receipt-target rejection cases now exercise actual `apply_native_chat_readback` from facts that exist in the declared predecessor schema. No runtime code or policy changed.

## Fail-first evidence

Command:

```sh
python -m pytest -q \
  tests/test_dingtalk_delivery_repair.py::test_native_chat_readback_settles_legacy_completed_reply_without_inventing_audit \
  tests/test_dingtalk_delivery_repair.py::test_legacy_native_readback_requires_unreviewed_exact_historical_facts
```

Before the fixture change, all three nodes failed while `_legacy_native_readback_case` called `restore_shared_actions`; `_require_source` raised `ValueError: unsupported shared lifecycle source declaration`. This confirmed the setup did not represent a valid source schema.

## Fixture and behavior

The fixture builds declarations from `source_objects("fresh_memory_fk")`, inserts the exact 19.12 Store and 53 Email markers, and uses registered Task8 insert/set parts for the reply task, its canonical input, and current/claimed input pointers. It inserts a completed `agent_runs` row with the task's execution generation, a completed ReplyAttempt with no permission/review or Audit evidence, one external result, and one Sent row. Source Action record codes come from that source declaration only during source-shaped inserts. There are no candidate, review, selection, execution, or candidate-attempt facts.

`prepare_action_entity_release` and `publish_action_entity_release` explicitly forward that populated source database. The positive case invokes the real readback application and verifies one successful settlement, no invented approval, one unchanged Action identity, one unchanged external-action identity, one unchanged Sent identity/body, no candidate or candidate-action-attempt facts before or after, unchanged completed AgentRun, unchanged canonical first-run identity, and preservation of the original provider result and ambiguous verification in the correction lineage. There is no dispatch-capable handler connected to this storage settlement call, so the test does not claim to observe or count provider sends.

The permission negative changes the historical ReplyAttempt permission fact. The receipt-target negative changes the exact current canonical target binding through `update_sent_receipt`; the old `external_action_results.target_identifiers_json` write no longer changes that current canonical fact after the forward release. Both cases still assert that readback rejects with `legacy reply lacks`, leaves the task failed, and leaves the precondition receipt, attempt, run, and Sent facts unchanged after rejection.

## Verification

Before the receipt-target fixture adjustment, the exact three nodes reported two passes and one failure: changing the obsolete source-shaped target payload did not alter the current canonical receipt target. After changing the mutation to the canonical target receipt:

```sh
python -m pytest -q \
  tests/test_dingtalk_delivery_repair.py::test_native_chat_readback_settles_legacy_completed_reply_without_inventing_audit \
  tests/test_dingtalk_delivery_repair.py::test_legacy_native_readback_requires_unreviewed_exact_historical_facts
```

Result on the final fixture assertions: **3 passed in 7.98s**.

```sh
python -m pytest -q tests/test_dingtalk_delivery_repair.py
```

Result on the final test file: **31 passed in 90.75s**.

This is synthetic local functional coverage. It does not claim a real provider read, native CLI run, UI acceptance, production effect, or overall Task 6 completion. The source database used by this test is created under pytest's temporary directory; the protected source, accepted A1/C2 copies, and runtime implementation are untouched.

## Review fix round 1

Restored the positive assertion that the persisted `verification_repair.native_chat_readback.quoted_trigger_id` equals the task's trigger message ID. Removed the fixture's unattached `SimpleNamespace(sends=1)` counter. The positive case now snapshots actual storage facts around `apply_native_chat_readback`: exactly one Action identity, one external-action result identity, and one Sent identity/body before and after; zero candidate and candidate-action-attempt facts before and after; and the original AgentRun and canonical first-run identity remain unchanged. The check does not claim to observe provider dispatch: this storage settlement call has no dispatch-capable handler.

Final focused command and output:

```text
$ python -m pytest -q tests/test_dingtalk_delivery_repair.py::test_native_chat_readback_settles_legacy_completed_reply_without_inventing_audit tests/test_dingtalk_delivery_repair.py::test_legacy_native_readback_requires_unreviewed_exact_historical_facts
...                                                                      [100%]
3 passed in 7.29s
```

The full repair file was not rerun for this bounded review correction; the prior final-fixture run remains 31 passed in 90.75s. The three focused nodes cover every changed test path. Ruff result:

```text
$ ruff check tests/test_dingtalk_delivery_repair.py
All checks passed!
```
