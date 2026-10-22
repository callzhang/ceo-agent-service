# Task 5A implementation report

## Result and boundary

Implemented the prepared-to-reviewed binding for the actual selected DingTalk message path. The failing test used `_prepare_outgoing_dingtalk_messages`, persisted Consumer/Audit/review/selection records, `SystemExecutor`, `native_dingtalk_message_handlers`, and a strict fake provider. Before the change, one accepted send created two Action owners: preparation was keyed by `agent_message_delivery_key`, while reviewed execution created a second owner by `external_action_key`. After the change, one actual send has one Action ID across prepared facts, the reviewed execution observation, canonical provider receipt, Sent receipt, and Audit observer.

This is a local, unpublished Task5A candidate. Populated migration, inverse/current-data coverage, old-code cold-open/reforward, release, and deploy remain out of scope. No production data, native CLI, real provider, push, or deploy was used.

## TDD and verification

The genuine RED command was:

```sh
TMPDIR=/tmp/ceo-action-entity-validation.8rUAvK/task-5a-codex python -m pytest -q tests/test_action_entity_callers.py::test_selected_reviewed_message_uses_prepared_action_through_sent
```

RED result: failed at `assert len(owners) == 1`; the query found two Actions for one provider send (`provider.calls == 1`). The test was built from the current preparation producer and native reviewed caller, not from a synthetic bare Action row.

GREEN and focused regression results:

```text
TMPDIR=/tmp/ceo-action-entity-validation.8rUAvK/task-5a-codex python -m pytest -q tests/test_action_entity_callers.py::test_selected_reviewed_message_uses_prepared_action_through_sent
1 passed

TMPDIR=/tmp/ceo-action-entity-validation.8rUAvK/task-5a-codex python -m pytest -q tests/test_shared_action_lifecycle.py::test_prepared_receipt_is_pinned_separately_from_sent_and_canonical_bodies tests/test_shared_action_lifecycle.py::test_reviewed_and_email_callers_write_common_action_without_origin_dispatch
2 passed
```

The actual selected-message test asserts one Action owner and one fake-provider call; selected option `send_notice` appears in the source delivery key along with the task business key, execution generation, and proposal revision. It checks that the prepared receipt retains its `send_message` operation, delivery-key target, and own observation time; the canonical provider receipt retains `send_group_message`, target JSON, and provider body; the Sent receipt points to that canonical receipt; and the execution observation, Sent projection, and Audit observer resolve to the same Action ID.

The same test creates a later persisted candidate/review at proposal revision 1 with a distinct prepared delivery key and the same canonical external effect key, then calls the persisted store claim API. That known-provider branch returns a verified observation on the original canonical Action; the fresh, unused preparation remains a separate owner, and the provider call count stays one. This verifies the source query’s selected `provider_receipt_id` field (not the nonexistent `id` field).

An attempted full-SystemExecutor later-revision retry reached the existing `_prior_action_matches` guard first and returned `completed_action_content_conflict`; the current postfix preparation changes the candidate payload for that delivery identity. The guard remains unchanged. The persisted-store known-provider branch is tested directly with genuine task/candidate/review/selection records; no claim is made that the existing SystemExecutor content-conflict case reaches that branch.

Additional checks:

```text
ruff check app/shared_action_lifecycle.py app/reviewed_candidate_store.py app/system_action_handlers.py app/system_executor.py tests/test_action_entity_callers.py
```

Ruff returned 7 warnings in these already-WIP files: unused locals/imports at `shared_action_lifecycle.py:415,510,637,749,812` and `test_action_entity_callers.py:154`. They are outside the Task5A hunks and were left untouched. A separate `python -m compileall -q app/shared_action_lifecycle.py app/reviewed_candidate_store.py app/system_action_handlers.py app/system_executor.py tests/test_action_entity_callers.py` passed, as did `git diff --check`.

## Source binding and changed files

- `app/system_action_handlers.py`: exposes the exact `(task, delivery_key)` already derived from source `agent_message_delivery_key` inputs; it does not use message-body equality as an identity.
- `app/system_executor.py`: supplies that reference to the reviewed Action claim before dispatch. Generic handlers keep their existing call signature.
- `app/reviewed_candidate_store.py`: accepts the optional preparation owner reference and passes it within the existing transaction.
- `app/shared_action_lifecycle.py`: when no canonical external-key owner exists, validates the prepared owner against the reviewed operation, external key, business object, and action identity, then binds those fields in the same CAS claim. If a canonical owner already exists and the later preparation has a different delivery key, it keeps the canonical owner and leaves the unused preparation separate. The provider-result recovery branch now reads the exact selected `provider_receipt_id` field. Sent receipt lookup distinguishes operation/target so a prepared provider fact cannot masquerade as the canonical reviewed provider body. Sent projection fills a previously-null Action `agent_run_id` from the actual Sent owner.
- `tests/test_action_entity_callers.py`: adds the end-to-end one-send/one-owner regression plus the persisted known-provider reuse case.
- `docs/architecture.md`, `docs/runtime-mechanism.md`: document this as unpublished Task5A candidate behavior and name the remaining migration/cold-open gates.

## Task5B implications and self-review

Task5B must prove that populated-forward migration preserves exact `delivery_key` ↔ `external_action_key` identity where source records refer to one effect, while retaining genuinely distinct preparations and provider/Sent clocks, bodies, receipts, and public identities. It must not merge by body or assume every later preparation for an already-completed canonical key is the same prepared fact. The current implementation does not make old populated rows, inverse data, or cold-open behavior pass.

No new business columns, identity heuristics, review/reconciliation gates, or retry policies were introduced. Existing `_prior_action_matches`, provider verification, claim/CAS, and unknown-result behavior are untouched. The focused end-to-end and shared-lifecycle tests pass; the preexisting broad `tests/test_action_entity_callers.py` file has an unrelated first-test SQL error (`no such column: delivery_key` on its historical `outbound_postfixes` query), and Ruff reports the seven existing warnings listed above. No full suite was run.
